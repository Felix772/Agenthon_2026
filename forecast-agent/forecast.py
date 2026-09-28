"""Joint statistical forecasting with explicit input and output contracts."""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import tomllib

import jsonschema
import numpy as np
import pandas as pd
from qfbench2_common.taskcard import load_schema
from qfbench2_track_forecasting.grid import GridSpec
from qfbench2_track_forecasting.horizons import monthly_horizon_steps
from qfbench2_track_forecasting.limits import ParseLimits
from qfbench2_track_forecasting.targets import log_return_steps
from joint_model import joint_samples
from text_events import load_text, retrieve

NAMES = ('forecast.parquet', 'forecast_meta.json', 'forecast_rationale.md')


def load_contract(panels, asof):
    date.fromisoformat(asof)
    card_path = panels.parent / 'card.toml'
    card = tomllib.loads(card_path.read_text(encoding='utf-8'))
    targets = card['targets']
    assets, horizons = targets['asset_ids'], targets['horizons']
    if not isinstance(assets, list) or any(not isinstance(a, str) or not a for a in assets):
        raise ValueError('Asset roster must be a nonempty string list')
    if not isinstance(horizons, list) or any(type(h) is not int for h in horizons):
        raise ValueError('Horizon keys must be integers')
    grid = GridSpec(tuple(assets), tuple(horizons))
    if targets['target_type'] not in ('level', 'log_return'):
        raise ValueError('Unsupported target type')
    cutoff = str(card.get('provenance', {}).get('data_cutoff', asof))
    if cutoff != asof:
        raise ValueError('As-of does not match card cutoff')
    spec_path = panels.parent / 'forecast_spec.json'
    spec = json.loads(spec_path.read_text()) if spec_path.exists() else None
    if spec is not None:
        if not isinstance(spec, dict) or not isinstance(spec.get('targets'), dict):
            raise ValueError('Invalid forecast specification')
        for key in ('asset_ids', 'horizons', 'target_type'):
            if spec['targets'].get(key) != targets[key]:
                raise ValueError('Forecast specification disagrees with card grid or target')
    return card, grid, spec


def load_histories(panels, grid, asof):
    files = sorted(panels.glob('*.parquet'))
    if not files:
        raise ValueError('Staged panels directory has no parquet files')
    frames = []
    for path in files:
        if path.is_symlink():
            raise ValueError('Input symlinks are not supported')
        frame = pd.read_parquet(path)
        if 'asset' not in frame and 'asset_id' in frame:
            frame = frame.rename(columns={'asset_id': 'asset'})
        if not {'date', 'asset', 'value'}.issubset(frame.columns):
            raise ValueError('Panel missing date, asset or value')
        frame['date'] = pd.to_datetime(frame['date'], errors='raise')
        if frame['date'].isna().any() or (frame['date'] > pd.Timestamp(asof)).any():
            raise ValueError('Panel violates as-of cutoff')
        frames.append(frame[['date', 'asset', 'value']])
    combined = pd.concat(frames, ignore_index=True)
    histories = {}
    for asset in grid.assets:
        rows = combined[combined.asset == asset].sort_values('date')
        if rows.empty:
            raise ValueError('Required asset absent')
        if rows.date.duplicated().any():
            raise ValueError('Ambiguous duplicate asset observations')
        values = rows.value.to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError('Non-finite history')
        histories[asset] = pd.Series(values, index=pd.DatetimeIndex(rows.date))
    return histories


def resolve_steps(card, grid, spec, histories, asof):
    frequency = card['targets'].get('target_frequency', card.get('metadata', {}).get('target_frequency', 'daily'))
    if frequency == 'monthly':
        if card['targets']['target_type'] != 'level':
            raise ValueError('Monthly non-level target is unsupported')
        for series in histories.values():
            periods = series.index.to_period('M')
            if len(periods) < 3 or periods.has_duplicates or np.median(np.diff(periods.asi8)) != 1:
                raise ValueError('Monthly target requires unambiguous monthly observations')
        return monthly_horizon_steps(grid.assets, grid.horizons,
            {asset: series.index[-1].date().isoformat() for asset, series in histories.items()},
            asof=asof, card=card, forecast_spec=spec)
    return np.tile(np.asarray(grid.horizons), (len(grid.assets), 1))


def validate_outputs(output, grid, draws, target):
    for name in NAMES:
        path = output / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError('Missing, empty or invalid required deliverable')
    if sum(p.stat().st_size for p in output.rglob('*') if p.is_file()) > 64 * 1024 * 1024:
        raise ValueError('Output exceeds 64 MiB')
    meta = json.loads((output / NAMES[1]).read_text())
    jsonschema.validate(meta, load_schema('forecast.schema.json'))
    if (meta['asset_ids'] != list(grid.assets) or meta['horizons'] != list(grid.horizons)
            or meta['n_draws'] != draws or meta['target'] != target):
        raise ValueError('Metadata grid or target mismatch')
    frame = pd.read_parquet(output / NAMES[0])
    if set(frame.columns) != {'draw', 'asset', 'horizon', 'value'}:
        raise ValueError('Invalid draw columns')
    if not all(pd.api.types.is_integer_dtype(frame[key]) for key in ('draw', 'horizon')):
        raise ValueError('Draw and horizon keys must have integer types')
    expected = pd.MultiIndex.from_product([range(draws), grid.assets, grid.horizons])
    actual = pd.MultiIndex.from_frame(frame[['draw', 'asset', 'horizon']])
    if actual.has_duplicates or len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError('Incomplete or duplicate draw grid')
    if not np.isfinite(frame.value.to_numpy(dtype=float)).all():
        raise ValueError('Non-finite draw value')
    if not (output / NAMES[2]).read_text().strip():
        raise ValueError('Missing rationale')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('verb', choices=['forecast'])
    parser.add_argument('--panels', type=Path, required=True)
    parser.add_argument('--text', type=Path, required=True)
    parser.add_argument('--asof', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=os.environ.get('QFBENCH_SEED', '0'))
    parser.add_argument('--n-draws', type=int, default=500)
    parser.add_argument('--method', choices=['contract-probe', 'gaussian', 'bootstrap'], default='gaussian')
    parser.add_argument('--shrinkage', type=float, default=0.1)
    parser.add_argument('--drift', action='store_true')
    parser.add_argument('--monthly-trend', action='store_true',
                        help='Exploratory damped trend for monthly level targets only')
    parser.add_argument('--window', type=int, default=252)
    args = parser.parse_args(argv)
    card, grid, spec = load_contract(args.panels, args.asof)
    floor = max(200, int(card.get('scoring', {}).get('params', {}).get('n_draws_min', 0)),
                int(spec.get('n_draws_min', 0)) if spec else 0)
    if not floor <= args.n_draws <= ParseLimits().max_draws:
        raise ValueError('Draw count outside declared limits')
    if not args.text.is_dir():
        raise ValueError('Staged text directory missing')
    histories = load_histories(args.panels, grid, args.asof)
    documents, text_audit = load_text(args.text, args.asof)
    text_audit['retrieved'] = [{k: v for k, v in hit.items() if k != 'quote'} for hit in
        retrieve(documents, ' '.join(grid.assets) + ' interest rates inflation growth policy')]
    steps = resolve_steps(card, grid, spec, histories, args.asof)
    target = card['targets']['target_type']
    if target == 'log_return':
        for history in histories.values():
            log_return_steps(history.to_numpy())  # Validate simple-return interpretation.
    anchor = {asset: float(series.iloc[-1]) if target == 'level' else 0.0 for asset, series in histories.items()}
    monthly = card['targets'].get('target_frequency', card.get('metadata', {}).get('target_frequency')) == 'monthly'
    if args.monthly_trend and (not monthly or target != 'level' or args.drift
                               or args.method == 'contract-probe'):
        raise ValueError('Monthly trend requires a monthly level target, Gaussian/bootstrap sampling and no generic drift')
    monthly_trend = args.monthly_trend or (
        os.environ.get('AGENTHON_MONTHLY_TREND_AUTO') == '1'
        and monthly and target == 'level' and not args.drift
        and args.method in ('gaussian', 'bootstrap'))
    if args.method == 'contract-probe':
        samples = np.tile(np.array([anchor[a] for a in grid.assets])[None, :, None], (args.n_draws, 1, len(grid.horizons)))
        stats = {'method': 'contract-probe', 'spread': 'zero; interface testing only'}
    else:
        samples, stats = joint_samples(histories, grid, steps, target=target, monthly=monthly,
            draws=args.n_draws, seed=args.seed, method=args.method, shrinkage=args.shrinkage,
            drift=args.drift, window=args.window, monthly_trend=monthly_trend)
    output = args.out.parent.resolve()
    if args.out.name != NAMES[0] or output.is_relative_to(args.panels.parent.resolve()):
        raise ValueError('Use forecast.parquet outside the input unit')
    if any((output / name).exists() for name in NAMES):
        raise ValueError('Refusing to overwrite prior run artifacts')
    output.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([(draw, asset, horizon, samples[draw, ai, hi])
        for draw in range(args.n_draws) for ai, asset in enumerate(grid.assets) for hi, horizon in enumerate(grid.horizons)],
        columns=['draw', 'asset', 'horizon', 'value'])
    frame.to_parquet(output / NAMES[0], index=False)
    meta = {'unit_id': card['task']['id'], 'asof': args.asof, 'representation': 'samples',
        'asset_ids': list(grid.assets), 'horizons': list(grid.horizons), 'n_draws': args.n_draws,
        'target': target, 'rationale': {'file': NAMES[2], 'method': args.method}}
    (output / NAMES[1]).write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
    rationale = {'method': args.method, 'seed': args.seed,
        'asof': args.asof, 'anchor': anchor, 'sampling_steps': steps.tolist(),
        'fit': stats, 'quality_evidence': 'not measured by this run',
        'text_contribution': 'none; retrieval audit only, no events inferred', 'model_calls': 0,
        'text_retrieval': text_audit}
    (output / NAMES[2]).write_text('# Forecast rationale\n\n' + json.dumps(rationale, indent=2) + '\n', encoding='utf-8')
    validate_outputs(output, grid, args.n_draws, target)
    print(f'{args.method} wrote {len(frame)} rows; no quality score computed.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, jsonschema.ValidationError) as exc:
        raise SystemExit(f'Forecast contract error: {type(exc).__name__}') from None
