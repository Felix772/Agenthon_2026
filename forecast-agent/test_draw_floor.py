"""Verify draw-count contracts on synthetic inputs, with no future outcomes or scores."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import numpy as np
import pandas as pd
from qfbench2_common.taskcard import load_and_validate
from qfbench2_track_forecasting.scoring import _main as score_main

from forecast import NAMES, main
from test_forecast import fixture, run


def set_floor(unit, card_floor=None, spec_floor=None):
    """Add independent card/spec minimums to the existing synthetic fixture."""
    import tomllib
    card_path = unit / 'card.toml'
    if card_floor is not None:
        with card_path.open('a', encoding='utf-8') as stream:
            stream.write(f'\n[scoring.params]\nn_draws_min = {card_floor}\n')
    if spec_floor is not None:
        targets = tomllib.loads(card_path.read_text())['targets']
        (unit / 'forecast_spec.json').write_text(json.dumps({
            'targets': targets, 'n_draws_min': spec_floor}), encoding='utf-8')


def future_fixture(root, family, universe, floor=200):
    """Make a future-dated UST or G10 card using only deterministic synthetic history."""
    unit = root / 'unit'
    (unit / 'panels').mkdir(parents=True)
    (unit / 'text').mkdir()
    asof = '2026-10-23'
    assets = ['UST_2Y', 'UST_10Y'] if universe == 'UST' else ['EURUSD', 'USDJPY']
    target = 'level' if universe == 'UST' else 'log_return'
    dates = pd.bdate_range(end=asof, periods=90)
    # These values are generated here; they are not observations of future markets.
    x = np.arange(len(dates))
    rows = []
    for ai, asset in enumerate(assets):
        values = (3 + ai + 0.08 * np.sin(x / (3 + ai)) + 0.001 * x
                  if target == 'level' else 0.001 * np.sin(x / (3 + ai)))
        rows.extend((day, asset, float(value)) for day, value in zip(dates, values))
    pd.DataFrame(rows, columns=['date', 'asset', 'value']).to_parquet(
        unit / 'panels/synthetic.parquet', index=False)
    (unit / 'text/synthetic-note.txt').write_text(
        'Synthetic contract fixture. This note contains no market observations, '
        'event outcomes, or forecasts.\n', encoding='utf-8')
    (unit / 'text/corpus_index.json').write_text(json.dumps({'documents': [{
        'doc_id': 'synthetic-note', 'file': 'synthetic-note.txt',
        'timestamp': '2026-10-11'}]}) + '\n', encoding='utf-8')
    (unit / 'card.toml').write_text(f'''# Synthetic contract fixture; no market outcomes.
schema_version = "2.0"
[task]
id = "t2-synthetic-{family}-{universe}"
track = "forecasting"
title = "Synthetic future {universe} contract for {family}"
split = "validation"
[metadata]
author_name = "Participant contract tests"
category = "{family}"
tags = ["synthetic", "contract-only"]
[provenance]
license = "CC0-1.0"
data_source = "Deterministic synthetic inputs; no market observations"
data_cutoff = "{asof}"
public_release_date = "2026-10-11"
redistributable = true
manifest = "manifest.json"
[contamination]
canary_guid = "{uuid.uuid4()}"
[scoring]
verifier = "t2.crps_composite"
metric = "crps_composite"
admissibility_gates = ["g0_integrity", "g1_schema", "g2_cutoff_resource", "g3_domain_semantics"]
[scoring.params]
representation = "samples"
n_draws_min = {floor}
joint = "variogram"
tail_levels = [0.01, 0.05, 0.95, 0.99]
[scoring.params.weights]
marginal = 0.5
joint = 0.3
tail = 0.2
[environment]
cpus = 16
memory = "128G"
gpu = true
network = "restricted"
[text]
source = "Synthetic contract note; no market information"
path = "text/"
cutoff = "{asof}"
cutoff_checked = true
n_documents = 1
[targets]
asset_ids = {json.dumps(assets)}
horizons = [2, 7, 12]
target_type = "{target}"
target_frequency = "daily"
leakage_checked = true
''', encoding='utf-8')
    (unit / 'forecast_spec.json').write_text(json.dumps({
        'targets': {'asset_ids': assets, 'horizons': [2, 7, 12], 'target_type': target},
        'n_draws_min': floor}), encoding='utf-8')
    return unit, asof


def invoke_default(unit, asof, out, extra=()):
    return main(['forecast', '--panels', str(unit / 'panels'), '--text', str(unit / 'text'),
                 '--asof', asof, '--out', str(out / NAMES[0]), '--seed', '17', *extra])


class DrawFloorTests(unittest.TestCase):
    def test_default_respects_card_and_spec_floor(self):
        for card_floor, spec_floor, count in ((1000, None, 1000), (None, 2000, 2000),
                                             (1000, 2000, 2000), (2000, 1000, 2000),
                                             (20000, None, 20000)):
            with self.subTest(card=card_floor, spec=spec_floor), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                unit, asof = fixture(root)
                set_floor(unit, card_floor, spec_floor)
                run(unit, asof, root / 'out', ['--method', 'gaussian'])
                meta = json.loads((root / 'out/forecast_meta.json').read_text())
                self.assertEqual(meta['n_draws'], count)
                self.assertEqual(len(pd.read_parquet(root / 'out/forecast.parquet')), count * 4)

    def test_explicit_draw_counts_stay_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unit, asof = fixture(root)
            set_floor(unit, card_floor=1000, spec_floor=2000)
            for count in (-1, 0, 199, 500, 1000, 1999, 20001):
                with self.subTest(draws=count), self.assertRaisesRegex(ValueError, 'Draw count'):
                    run(unit, asof, root / f'bad-{count}', ['--n-draws', str(count)])
            run(unit, asof, root / 'valid', ['--n-draws', '2500'])
            self.assertEqual(json.loads((root / 'valid/forecast_meta.json').read_text())['n_draws'], 2500)

    def test_unfulfillable_floor_is_rejected(self):
        for card_floor, spec_floor in ((20001, None), (None, 20001)):
            with self.subTest(card=card_floor, spec=spec_floor), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                unit, asof = fixture(root)
                set_floor(unit, card_floor, spec_floor)
                with self.assertRaisesRegex(ValueError, 'Draw count'):
                    run(unit, asof, root / 'out')
                self.assertFalse((root / 'out').exists())

    def test_500_only_profiles_reject_higher_floor_without_switching(self):
        profiles = [('reference-walk', 'AGENTHON_DAILY_REFERENCE_WALK'),
                    ('online-ensemble', 'AGENTHON_DAILY_ONLINE_SELECT'),
                    ('m0-control', 'AGENTHON_DAILY_M0_CONTROL')]
        for method, variable in profiles:
            with self.subTest(method=method), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                unit, asof = fixture(root)
                set_floor(unit, card_floor=1000)
                with self.assertRaisesRegex(ValueError, '500 draws'):
                    invoke_default(unit, asof, root / 'explicit-profile', ['--method', method])
                with patch.dict('os.environ', {variable: '1'}):
                    with self.assertRaisesRegex(ValueError, '500 draws'):
                        invoke_default(unit, asof, root / 'environment-profile')
                self.assertFalse((root / 'explicit-profile').exists())
                self.assertFalse((root / 'environment-profile').exists())

    def test_default_500_matches_explicit_bytes_for_existing_targets(self):
        for target, monthly in (('level', False), ('log_return', False), ('level', True)):
            with self.subTest(target=target, monthly=monthly), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                unit, asof = fixture(root, target=target, monthly=monthly)
                with patch.dict('os.environ', {'AGENTHON_MONTHLY_TREND_AUTO': '1'}):
                    invoke_default(unit, asof, root / 'default')
                    invoke_default(unit, asof, root / 'explicit', ['--n-draws', '500'])
                for name in NAMES:
                    self.assertEqual((root / 'default' / name).read_bytes(),
                                     (root / 'explicit' / name).read_bytes())

    def test_future_ust_and_g10_all_families_pass_official_gates(self):
        for family in ('T2-F1', 'T2-F2', 'T2-F3', 'T2-F4'):
            for universe in ('UST', 'G10'):
                with self.subTest(family=family, universe=universe), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    floor = 1000 if family == 'T2-F4' else 200
                    unit, asof = future_fixture(root, family, universe, floor)
                    _, errors = load_and_validate(unit)
                    self.assertEqual(errors, [])
                    invoke_default(unit, asof, root / 'out')
                    captured = io.StringIO()
                    with contextlib.redirect_stdout(captured):
                        code = score_main(['score', '--card', str(unit / 'card.toml'),
                                           '--forecast', str(root / 'out/forecast.parquet')])
                    payload = json.loads(captured.getvalue())
                    self.assertEqual(code, 0, payload)
                    self.assertEqual(payload['gates'], {f'g{i}_{name}': 'pass' for i, name in
                        enumerate(('integrity', 'schema', 'cutoff_resource', 'domain_semantics'))})
                    self.assertFalse(payload['scored'])
                    frame = pd.read_parquet(root / 'out/forecast.parquet')
                    self.assertEqual(sorted(frame.horizon.unique()), [2, 7, 12])
                    self.assertEqual(len(frame), max(500, floor) * 6)
                    self.assertTrue(np.isfinite(frame.value).all())


if __name__ == '__main__':
    unittest.main()
