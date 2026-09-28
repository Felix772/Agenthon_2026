"""Synthetic contract tests; never use hidden outcomes or a quality score."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from qfbench2_track_forecasting.grid import GridSpec
from forecast import load_contract, main, resolve_steps, load_histories, validate_outputs


def fixture(root, target='level', monthly=False):
    unit = root / 'unit'
    (unit / 'panels').mkdir(parents=True)
    (unit / 'text').mkdir()
    asof = '2031-02-14' if monthly else '2024-06-28'
    horizons = [42, 65] if monthly else [1, 5]
    frequency = 'monthly' if monthly else 'daily'
    (unit / 'card.toml').write_text(f'''[task]
id = "synthetic-contract"
[provenance]
data_cutoff = "{asof}"
[targets]
asset_ids = ["A", "B"]
horizons = {horizons}
target_type = "{target}"
target_frequency = "{frequency}"
''')
    dates = pd.date_range('2027-01-31', periods=48, freq='ME') if monthly else pd.bdate_range('2024-01-01', periods=40)
    pd.DataFrame([(day, asset, 0.01 if target == 'log_return' else float(100 + i))
        for i, day in enumerate(dates) for asset in ('A', 'B')],
        columns=['date', 'asset', 'value']).to_parquet(unit / 'panels/data.parquet', index=False)
    if monthly:
        (unit / 'forecast_spec.json').write_text(json.dumps({'targets': {
            'asset_ids': ['A', 'B'], 'horizons': horizons, 'target_type': target,
            'observation_periods': ['2031-03', '2031-04']}}))
    return unit, asof


def run(unit, asof, out, extra=()):
    return main(['forecast', '--panels', str(unit / 'panels'), '--text', str(unit / 'text'),
                 '--asof', asof, '--out', str(out / 'forecast.parquet'), '--seed', '17', '--method', 'contract-probe', *extra])


class ContractTests(unittest.TestCase):
    def test_harness_seed_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit, asof = fixture(root)
            def invoke(name, extra=()):
                out = root / name
                main(['forecast', '--panels', str(unit / 'panels'), '--text', str(unit / 'text'),
                      '--asof', asof, '--out', str(out / 'forecast.parquet'), *extra])
                rationale = json.loads((out / 'forecast_rationale.md').read_text().split('\n\n', 1)[1])
                return rationale['seed'], (out / 'forecast.parquet').read_bytes()
            with patch.dict('os.environ', {'QFBENCH_SEED': '21'}):
                first = invoke('harness')
                repeated = invoke('repeat')
                explicit = invoke('explicit', ['--seed', '17'])
            self.assertEqual(first[0], 21)
            self.assertEqual(first, repeated)
            self.assertEqual(explicit[0], 17)
            self.assertNotEqual(first[1], explicit[1])
            with patch.dict('os.environ', {'QFBENCH_SEED': 'invalid'}):
                self.assertEqual(invoke('override-invalid-env', ['--seed', '17']), explicit)

    def test_level_log_return_and_fixed_seed_reproduction(self):
        for target in ('level', 'log_return'):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                unit, asof = fixture(root, target)
                run(unit, asof, root / 'one')
                run(unit, asof, root / 'two')
                for name in ('forecast.parquet', 'forecast_meta.json', 'forecast_rationale.md'):
                    self.assertEqual((root / 'one' / name).read_bytes(), (root / 'two' / name).read_bytes())
                frame = pd.read_parquet(root / 'one/forecast.parquet')
                self.assertEqual(len(frame), 2000)
                self.assertEqual(frame.value.unique().tolist(), [139.0] if target == 'level' else [0.0])
                with self.assertRaisesRegex(ValueError, 'overwrite'):
                    run(unit, asof, root / 'one')

    def test_explicit_monthly_periods_keep_keys_and_publication_lag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit, asof = fixture(root, monthly=True)
            card, grid, spec = load_contract(unit / 'panels', asof)
            histories = load_histories(unit / 'panels', grid, asof)
            self.assertEqual(resolve_steps(card, grid, spec, histories, asof).tolist(), [[3, 4], [3, 4]])
            run(unit, asof, root / 'out')
            self.assertEqual(sorted(pd.read_parquet(root / 'out/forecast.parquet').horizon.unique()), [42, 65])
            (unit / 'forecast_spec.json').unlink()
            with self.assertRaises(ValueError):
                run(unit, asof, root / 'missing-map')

    def test_missing_asset_duplicate_grid_and_invalid_draw_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit, asof = fixture(root)
            for count in ('199', '0', '-1'):
                with self.assertRaises(ValueError):
                    run(unit, asof, root / 'out', ['--n-draws', count])
            card = unit / 'card.toml'
            original = card.read_text()
            for before, after in (('["A", "B"]', '["A", "A"]'), ('[1, 5]', '[1, 1]'), ('[1, 5]', '[true, 5]')):
                card.write_text(original.replace(before, after))
                with self.assertRaises(ValueError):
                    run(unit, asof, root / 'out')
            card.write_text(original)
            panel = unit / 'panels/data.parquet'
            frame = pd.read_parquet(panel)
            frame[frame.asset == 'A'].to_parquet(panel, index=False)
            with self.assertRaisesRegex(ValueError, 'asset absent'):
                run(unit, asof, root / 'out')

    def test_corrupted_outputs_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit, asof = fixture(root)
            run(unit, asof, root / 'good')
            for case in ('missing_asset', 'duplicate', 'nan', 'wrong_horizon', 'wrong_draw', 'float_draw', 'missing_rationale', 'missing_forecast'):
                out = root / case
                shutil.copytree(root / 'good', out)
                path = out / 'forecast.parquet'
                frame = pd.read_parquet(path)
                if case == 'missing_asset': frame = frame[frame.asset != 'B']
                elif case == 'duplicate': frame = pd.concat([frame, frame.iloc[[0]]])
                elif case == 'nan': frame.loc[0, 'value'] = np.nan
                elif case == 'wrong_horizon': frame.loc[0, 'horizon'] = 99
                elif case == 'wrong_draw': frame.loc[0, 'draw'] = 500
                elif case == 'float_draw': frame['draw'] = frame['draw'].astype(float)
                frame.to_parquet(path, index=False)
                if case == 'missing_rationale': (out / 'forecast_rationale.md').unlink()
                if case == 'missing_forecast': path.unlink()
                with self.subTest(case=case), self.assertRaises(ValueError):
                    validate_outputs(out, GridSpec(('A', 'B'), (1, 5)), 500, 'level')

    def test_malformed_spec_and_future_or_nonfinite_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit, asof = fixture(root)
            spec = unit / 'forecast_spec.json'
            spec.write_text('{"targets":{"asset_ids":["wrong"]}}')
            with self.assertRaises(ValueError): run(unit, asof, root / 'out')
            spec.unlink()
            path = unit / 'panels/data.parquet'
            original = pd.read_parquet(path)
            for column, value in [('date', pd.Timestamp('2025-01-01')), ('value', np.inf)]:
                frame = original.copy()
                frame.loc[0, column] = value
                frame.to_parquet(path, index=False)
                with self.assertRaises(ValueError): run(unit, asof, root / 'out')


if __name__ == '__main__':
    unittest.main()
