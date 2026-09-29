"""Run the unit-local T2 candidate on all 71 scored public card inputs."""

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

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'forecast-agent'))
from forecast import main as forecast_main  # noqa: E402
from qfbench2_track_forecasting.scoring import _main as score_main  # noqa: E402

STAGED = ROOT / 'project-evidence/t2-monthly-trend-dev-roster-20260928'
SOURCE = ROOT / '.validation/t2-current-20260928/units'
ROSTER = ROOT / 'project-evidence/t2-codabench-scored-950513-20260928.csv'
OUT = ROOT / 'project-evidence/t2-online-scored-roster-20260928'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    with ROSTER.open(newline='') as file:
        units = [row['unit'] for row in csv.DictReader(file)]
    if len(units) != 71 or len(set(units)) != 71 or OUT.exists():
        raise ValueError('Expected a new 71-unit audit directory')
    prior_report = json.loads((STAGED / 'report.json').read_text())
    previously_passed = {row['unit']: row for row in prior_report['records']
                         if row['status'] == 'passed'}
    if not set(units) <= set(previously_passed):
        raise ValueError('Scored unit lacks prior manifest-verified staging')
    OUT.mkdir()
    report = {'source_ref': '28a6cae9674f69e63a07a19165a9217e85eacfff',
              'scored_roster_sha256': digest(ROSTER), 'seed': 20260928,
              'forecast_source_sha256': digest(ROOT/'forecast-agent/forecast.py'),
              'online_source_sha256': digest(ROOT/'forecast-agent/online_model.py'),
              'reference_source_sha256': digest(ROOT/'forecast-agent/reference_model.py'),
              'records': []}
    for unit in units:
        start = time.perf_counter()
        retry = STAGED / 'retry-inputs' / unit
        staged = retry if retry.is_dir() else STAGED / 'inputs' / unit
        for member in previously_passed[unit]['staged_files']:
            path = staged / member['path']
            if (path.is_symlink() or not path.is_file()
                    or digest(path) != member['sha256']):
                raise ValueError(f'Staged input changed since manifest validation: {unit} {member["path"]}')
        old_retry = STAGED / 'retry-outputs' / unit / 'forecast.parquet'
        old_forecast = old_retry if old_retry.is_file() else STAGED / 'outputs' / unit / 'forecast.parquet'
        card_path = SOURCE / unit / 'card.toml'
        card = tomllib.loads(card_path.read_text())
        asof = str(card['provenance']['data_cutoff'])
        output = OUT / 'outputs' / unit
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = forecast_main(['forecast', '--panels', str(staged/'panels'),
                                  '--text', str(staged/'text'), '--asof', asof,
                                  '--out', str(output/'forecast.parquet'),
                                  '--seed', '20260928', '--method', 'online-ensemble'])
        if code:
            raise RuntimeError(f'Forecast failed for {unit}')
        score_output = io.StringIO()
        with redirect_stdout(score_output):
            verdict_code = score_main(['score', '--card', str(card_path),
                                       '--forecast', str(output/'forecast.parquet')])
        verdict = json.loads(score_output.getvalue())
        rationale = json.loads((output/'forecast_rationale.md').read_text().split('\n\n',1)[1])
        fit = rationale['fit']
        new_values = pd.read_parquet(output/'forecast.parquet').value.to_numpy(dtype=float)
        old_values = pd.read_parquet(old_forecast).value.to_numpy(dtype=float)
        row = {'unit': unit, 'asof': asof, 'target': card['targets']['target_type'],
               'cells': len(card['targets']['asset_ids'])*len(card['targets']['horizons']),
               'selected_arm': fit['selected_arm'],
               'validation_windows': fit['validation_windows'],
               'validation_cutoffs': [r['cutoff'] for r in fit['validation']],
               'all_validation_pre_cutoff': all(r['cutoff'] < asof for r in fit['validation']),
               'changed_forecast_values': not np.array_equal(new_values, old_values),
               'forecast_sha256': digest(output/'forecast.parquet'),
               'admissible': bool(verdict_code == 0 and verdict['admissible']),
               'output_bytes': sum(p.stat().st_size for p in output.iterdir()),
               'elapsed_sec': time.perf_counter()-start}
        if (not row['admissible'] or not row['all_validation_pre_cutoff']
                or row['output_bytes'] > 64*1024**2):
            raise RuntimeError(f'Candidate failed structural or cutoff check: {unit}')
        report['records'].append(row)
        (OUT/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        print(unit, row['selected_arm'], 'changed' if row['changed_forecast_values'] else 'same', flush=True)
    report['selected_arms'] = dict(Counter(r['selected_arm'] for r in report['records']))
    report['changed_count'] = sum(r['changed_forecast_values'] for r in report['records'])
    report['admissible_count'] = sum(r['admissible'] for r in report['records'])
    report['all_pre_cutoff_count'] = sum(r['all_validation_pre_cutoff'] for r in report['records'])
    (OUT/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k: report[k] for k in ('selected_arms','changed_count',
                                            'admissible_count','all_pre_cutoff_count')}, indent=2))


if __name__ == '__main__':
    main()
