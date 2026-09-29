"""Audit usable pre-cutoff rolling windows on the 71 scored public T2 cards."""

import csv
import json
from pathlib import Path
import sys
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'forecast-agent'))
from forecast import load_contract, load_histories  # noqa: E402
from online_model import _folds  # noqa: E402

STAGED = ROOT / 'project-evidence/t2-monthly-trend-dev-roster-20260928'
ROSTER = ROOT / 'project-evidence/t2-codabench-scored-950513-20260928.csv'
OUTPUT = ROOT / 'project-evidence/t2-unit-local-folds-20260928.json'


def main():
    with ROSTER.open(newline='') as file:
        units = [row['unit'] for row in csv.DictReader(file)]
    if len(units) != 71 or len(set(units)) != 71:
        raise ValueError('Unexpected scored roster')
    rows = []
    for unit in units:
        retry = STAGED / 'retry-inputs' / unit
        directory = retry if retry.is_dir() else STAGED / 'inputs' / unit
        if not directory.is_dir():
            raise ValueError(f'Missing previously manifest-verified staged input: {unit}')
        import tomllib
        card = tomllib.loads((directory / 'card.toml').read_text())
        asof = str(card['provenance']['data_cutoff'])
        _, grid, _ = load_contract(directory / 'panels', asof)
        try:
            histories = load_histories(directory / 'panels', grid, asof)
        except ValueError as exc:
            raise ValueError(f'{unit}: {exc}') from exc
        folds = _folds(histories, grid, grid.horizons)
        rows.append({'unit': unit, 'asof': asof, 'target': card['targets']['target_type'],
                     'cells': len(grid.assets)*len(grid.horizons),
                     'horizons': list(grid.horizons),
                     'usable_folds': len(folds),
                     'fold_cutoffs': [str(past.index[-1].date()) for past, _ in folds],
                     'all_pre_cutoff': all(past.index[-1].date().isoformat() < asof
                                           and future.index[-1].date().isoformat() <= asof
                                           for past, future in folds)})
        if not rows[-1]['all_pre_cutoff']:
            raise ValueError(f'Fold crossed task cutoff: {unit}')
    counts = dict(Counter(row['usable_folds'] for row in rows))
    report = {'roster': 71, 'fold_counts': counts,
              'eligible_for_selection': sum(row['usable_folds'] >= 2 for row in rows),
              'cutoff_violations': 0, 'rows': rows}
    OUTPUT.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k: report[k] for k in ('roster','fold_counts','eligible_for_selection',
                                            'cutoff_violations')}, indent=2))


if __name__ == '__main__':
    main()
