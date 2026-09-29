"""Check the cutoff-only M0 daily control against all 71 scored public cards."""

from collections import Counter
from contextlib import redirect_stdout
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import tomllib
import zlib

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'forecast-agent'))
from forecast import load_contract, load_histories, main as forecast_main, resolve_steps  # noqa: E402
from qfbench2_track_forecasting.scoring import _main as score_main  # noqa: E402

STAGED = ROOT / 'project-evidence/t2-monthly-trend-dev-roster-20260928'
SOURCE = ROOT / '.validation/t2-current-20260928/units'
ROSTER = ROOT / 'project-evidence/t2-codabench-scored-950513-20260928.csv'
OUT = ROOT / 'project-evidence/t2-m0-control-scored-roster-20260928'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def panel_asset_matches(panels, asset):
    found = []
    for path in sorted(panels.glob('*.parquet')):
        frame = pd.read_parquet(path)
        asset_key = 'asset' if 'asset' in frame else 'asset_id'
        if asset_key in frame and asset in set(frame[asset_key]):
            found.append(path)
    return found


def main():
    with ROSTER.open(newline='') as file:
        units = [row['unit'] for row in csv.DictReader(file)]
    if len(units) != 71 or len(set(units)) != 71 or OUT.exists():
        raise ValueError('Expected a new 71-unit audit directory')
    prior = json.loads((STAGED/'report.json').read_text())
    prior_rows = {r['unit']: r for r in prior['records'] if r['status'] == 'passed'}
    if not set(units) <= set(prior_rows):
        raise ValueError('Scored unit lacks a prior manifest-verified staging')
    OUT.mkdir()
    report = {'m0_spec_ref': '28a6cae9674f69e63a07a19165a9217e85eacfff',
              'scored_roster_sha256': digest(ROSTER),
              'forecast_source_sha256': digest(ROOT/'forecast-agent/forecast.py'),
              'reference_source_sha256': digest(ROOT/'forecast-agent/reference_model.py'),
              'records': []}
    for unit in units:
        start = time.perf_counter()
        retry = STAGED/'retry-inputs'/unit
        staged = retry if retry.is_dir() else STAGED/'inputs'/unit
        for member in prior_rows[unit]['staged_files']:
            path = staged/member['path']
            if path.is_symlink() or not path.is_file() or digest(path) != member['sha256']:
                raise ValueError(f'Staged input changed since manifest validation: {unit} {member["path"]}')
        card_path = SOURCE/unit/'card.toml'
        card = tomllib.loads(card_path.read_text())
        asof = str(card['provenance']['data_cutoff'])
        _, grid, spec = load_contract(staged/'panels', asof)
        histories = load_histories(staged/'panels', grid, asof)
        steps = resolve_steps(card, grid, spec, histories, asof)
        first_panel_matches = {}
        for asset in grid.assets:
            matches = panel_asset_matches(staged/'panels', asset)
            first_panel_matches[asset] = [p.name for p in matches]
            if len(matches) != 1:
                raise ValueError(f'M0 first-panel rule differs from combined loader: {unit} {asset} {matches}')
        if not np.array_equal(steps, np.tile(np.asarray(grid.horizons), (len(grid.assets), 1))):
            raise ValueError(f'Daily M0 must use declared steps: {unit}')
        output = OUT/'outputs'/unit
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = forecast_main(['forecast', '--panels', str(staged/'panels'),
                                  '--text', str(staged/'text'), '--asof', asof,
                                  '--out', str(output/'forecast.parquet'),
                                  '--seed', '20260928', '--method', 'm0-control'])
        if code:
            raise RuntimeError(f'M0 control failed for {unit}')
        verdict_stream = io.StringIO()
        with redirect_stdout(verdict_stream):
            verdict_code = score_main(['score', '--card', str(card_path),
                                       '--forecast', str(output/'forecast.parquet')])
        verdict = json.loads(verdict_stream.getvalue())
        rationale = json.loads((output/'forecast_rationale.md').read_text().split('\n\n', 1)[1])
        crc_seed = zlib.crc32(unit.encode('utf-8')) & 0x7FFFFFFF
        if rationale['seed'] != crc_seed or rationale['fit']['seed'] != crc_seed:
            raise ValueError(f'Expected card-id CRC32 seed: {unit}')
        if not verdict_code == 0 or not verdict['admissible']:
            raise ValueError(f'M0 control failed official public gates: {unit}')
        row = {'unit': unit, 'asof': asof, 'target': card['targets']['target_type'],
               'cells': len(grid.assets)*len(grid.horizons),
               'card_order_equals_sorted_cells': (list(grid.assets) == sorted(grid.assets)
                                                  and list(grid.horizons) == sorted(grid.horizons)),
               'first_panel_matches': first_panel_matches,
               'seed': crc_seed, 'fit_rows': rationale['fit']['fit_rows'],
               'admissible': True, 'output_bytes': sum(p.stat().st_size for p in output.iterdir()),
               'elapsed_sec': time.perf_counter()-start}
        if row['output_bytes'] > 64*1024**2:
            raise ValueError(f'M0 output size exceeded: {unit}')
        report['records'].append(row)
        (OUT/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        print(unit, 'passed', flush=True)
    report['count'] = len(report['records'])
    report['by_target'] = dict(Counter(r['target'] for r in report['records']))
    report['card_order_equals_sorted_cells_count'] = sum(
        r['card_order_equals_sorted_cells'] for r in report['records'])
    report['max_output_bytes'] = max(r['output_bytes'] for r in report['records'])
    report['elapsed_sec'] = sum(r['elapsed_sec'] for r in report['records'])
    (OUT/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'records'}, indent=2))


if __name__ == '__main__':
    main()
