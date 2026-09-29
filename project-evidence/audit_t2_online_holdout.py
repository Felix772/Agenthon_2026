"""Audit one pre-cutoff held-out fold and same-process incumbent parity on scored cards.

This is read-only analysis of the already frozen 71-card online roster run.  The
first two nonoverlapping historical folds propose an arm; the third fold is
scored without using its outcome to choose that proposed arm.  The production
rule also uses the third fold as a veto, so that later veto is reported
separately and is not counted as independent improvement evidence.
"""

from collections import Counter
import csv
import json
from pathlib import Path
import sys
import tomllib

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'forecast-agent'))
from forecast import load_contract, load_histories, resolve_steps  # noqa: E402
from joint_model import joint_samples  # noqa: E402
from online_model import ARMS  # noqa: E402

ROSTER = ROOT / 'project-evidence/t2-codabench-scored-950513-20260928.csv'
STAGED = ROOT / 'project-evidence/t2-monthly-trend-dev-roster-20260928'
SOURCE = ROOT / '.validation/t2-current-20260928/units'
ONLINE = ROOT / 'project-evidence/t2-online-scored-roster-20260928'
OUT = ONLINE / 'holdout-and-incumbent-parity.json'


def keyed_values(path, grid):
    frame = pd.read_parquet(path).set_index(['draw', 'asset', 'horizon'])
    index = pd.MultiIndex.from_product([range(500), grid.assets, grid.horizons],
                                       names=['draw', 'asset', 'horizon'])
    if len(frame) != len(index) or not frame.index.is_unique or set(frame.index) != set(index):
        raise ValueError(f'Unexpected forecast grid: {path}')
    return frame.reindex(index)['value'].to_numpy(dtype=float).reshape(
        500, len(grid.assets), len(grid.horizons))


def summarize(rows):
    delta = np.array([r['holdout_delta'] for r in rows], dtype=float)
    rng = np.random.default_rng(20260928)
    draws = rng.integers(0, len(delta), size=(20000, len(delta)))
    means = delta[draws].mean(axis=1)
    return {'cards': len(rows), 'proposed_arms': dict(Counter(r['proposed_arm'] for r in rows)),
            'mean_candidate_minus_incumbent': float(delta.mean()),
            'median_candidate_minus_incumbent': float(np.median(delta)),
            'mean_delta_bootstrap_95pct': [float(v) for v in np.quantile(means, [.025, .975])],
            'holdout_wins': int((delta < 0).sum()),
            'holdout_losses': int((delta > 0).sum()),
            'holdout_ties': int((delta == 0).sum())}


def main():
    report = json.loads((ONLINE / 'report.json').read_text())
    by_unit = {r['unit']: r for r in report['records']}
    with ROSTER.open(newline='') as file:
        units = [row['unit'] for row in csv.DictReader(file)]
    if len(units) != 71 or len(by_unit) != 71 or set(units) != set(by_unit):
        raise ValueError('Audit requires exact scored roster')
    rows = []
    for unit in units:
        retry = STAGED / 'retry-inputs' / unit
        staged = retry if retry.is_dir() else STAGED / 'inputs' / unit
        card = tomllib.loads((SOURCE / unit / 'card.toml').read_text())
        asof = str(card['provenance']['data_cutoff'])
        _, grid, spec = load_contract(staged/'panels', asof)
        histories = load_histories(staged/'panels', grid, asof)
        steps = resolve_steps(card, grid, spec, histories, asof)
        incumbent, _ = joint_samples(histories, grid, steps,
                                     target=card['targets']['target_type'], monthly=False,
                                     draws=500, seed=20260928, method='gaussian',
                                     shrinkage=.1, drift=False, window=252)
        online_dir = ONLINE/'outputs'/unit
        online_values = keyed_values(online_dir/'forecast.parquet', grid)
        old_retry = STAGED/'retry-outputs'/unit/'forecast.parquet'
        old_path = old_retry if old_retry.is_file() else STAGED/'outputs'/unit/'forecast.parquet'
        old_values = keyed_values(old_path, grid)
        fit = json.loads((online_dir/'forecast_rationale.md').read_text().split('\n\n', 1)[1])['fit']
        validation = fit['validation']
        if len(validation) != 3 or len({v['cutoff'] for v in validation}) != 3:
            raise ValueError(f'Card has fewer than three unique scored folds: {unit}')
        selection, holdout = validation[:2], validation[2]
        means = {arm: float(np.mean([fold['scores'][arm] for fold in selection])) for arm in ARMS}
        candidate = min(ARMS, key=lambda arm: (means[arm], ARMS.index(arm)))
        wins = sum(fold['scores'][candidate] < fold['scores']['incumbent'] for fold in selection)
        proposed = candidate if (candidate != 'incumbent'
                                 and means[candidate] <= .98*means['incumbent']
                                 and wins == len(selection)) else 'incumbent'
        delta = float(holdout['scores'][proposed] - holdout['scores']['incumbent'])
        chosen = fit['selected_arm']
        expected_choice = proposed if delta <= 0 else 'incumbent'
        if chosen != expected_choice:
            raise ValueError(f'Online selection differs from audited rule: {unit}')
        row = {'unit': unit, 'target': card['targets']['target_type'],
               'shape': 'single' if len(grid.assets)*len(grid.horizons) == 1 else 'multi',
               'proposed_arm': proposed, 'production_arm': chosen,
               'holdout_delta': delta,
               'online_equals_fresh_incumbent': bool(np.array_equal(online_values, incumbent)),
               'prior_equals_fresh_incumbent': bool(np.array_equal(old_values, incumbent)),
               'online_max_abs_diff_from_fresh_incumbent': float(np.max(np.abs(online_values-incumbent))),
               'prior_max_abs_diff_from_fresh_incumbent': float(np.max(np.abs(old_values-incumbent)))}
        if chosen == 'incumbent' and not row['online_equals_fresh_incumbent']:
            raise ValueError(f'Online incumbent parity failed: {unit}')
        rows.append(row)
    groups = {'all': summarize(rows)}
    for key in ('shape', 'target'):
        for value in sorted(set(r[key] for r in rows)):
            groups[f'{key}:{value}'] = summarize([r for r in rows if r[key] == value])
    result = {'cards': 71, 'seed': 20260928,
              'holdout_design': 'first two folds propose; third fold evaluated before veto',
              'prior_equals_fresh_incumbent_count': sum(r['prior_equals_fresh_incumbent'] for r in rows),
              'production_incumbent_parity_count': sum(r['online_equals_fresh_incumbent']
                                                       for r in rows if r['production_arm'] == 'incumbent'),
              'groups': groups, 'records': rows}
    OUT.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'records'}, indent=2))


if __name__ == '__main__':
    main()
