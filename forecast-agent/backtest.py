"""Frozen-model retrospective backtest on supplied public panels, not official ranking."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from qfbench2_track_forecasting.grid import GridSpec
from qfbench2_track_forecasting.scoring import _composite
from joint_model import joint_samples


def load_panel(path):
    frame = pd.read_parquet(path)
    frame['date'] = pd.to_datetime(frame['date'])
    if frame.duplicated(['date', 'asset']).any():
        raise ValueError('Duplicate timestamp/asset')
    if not np.isfinite(frame.value.to_numpy(dtype=float)).all() or frame.date.isna().any():
        raise ValueError('Invalid historical panel values or timestamps')
    return frame.pivot(index='date', columns='asset', values='value').sort_index()


def validate_folds(folds):
    previous_end = None
    previous_cutoff = None
    for fold in folds:
        cutoff = pd.Timestamp(fold['cutoff'])
        targets = [pd.Timestamp(value) for value in fold['target_dates']]
        if not targets or min(targets) <= cutoff:
            raise ValueError('Target overlaps training cutoff')
        if previous_cutoff is not None and cutoff <= previous_cutoff:
            raise ValueError('Folds must be chronological')
        if previous_end is not None and cutoff <= previous_end + pd.offsets.BDay(5):
            raise ValueError('Target-window overlap or insufficient embargo')
        previous_cutoff, previous_end = cutoff, max(targets)


def training_only(wide, cutoff, assets):
    history = wide.loc[wide.index <= pd.Timestamp(cutoff), list(assets)]
    if len(history) < 31:
        raise ValueError('Insufficient historical observations')
    return {asset: history[asset].dropna().copy() for asset in assets}


def target_values(wide, assets, target_dates):
    targets = pd.to_datetime(target_dates)
    if not all(day in wide.index for day in targets):
        raise ValueError('Missing target observation')
    values = wide.loc[targets, list(assets)].to_numpy(dtype=float).T
    if not np.isfinite(values).all():
        raise ValueError('Missing or non-finite target cell')
    return values.reshape(-1)


def run_backtest(source_dir, destination):
    destination.mkdir(parents=True, exist_ok=False)
    files = ['rates_daily.parquet', 'g10_fx_daily.parquet']
    # Fixed cutoffs and configurations are written before any outcome is evaluated.
    folds = []
    for year in range(2014, 2024):
        cutoff = pd.offsets.BDay().rollback(pd.Timestamp(f'{year}-12-31'))
        folds.append({'fold_id': f'yearend-{year}', 'cutoff': cutoff.date().isoformat(),
            'target_dates': [(cutoff + pd.offsets.BDay(h)).date().isoformat() for h in (5, 21)],
            'horizons': [5, 21], 'phase': 'development' if year <= 2017 else 'validation' if year <= 2020 else 'holdout',
            'purge_rule': 'Nonoverlapping target windows, plus 5 business-day embargo before next cutoff'})
    validate_folds(folds)
    source_manifest = json.loads((source_dir / 'manifest.json').read_text())
    provenance = [row for row in source_manifest['files'] if row['path'] in files]
    for entry in provenance:
        if hashlib.sha256((source_dir / entry['path']).read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Historical source hash does not match published manifest')
    model_hash = hashlib.sha256(Path(__file__).with_name('joint_model.py').read_bytes()).hexdigest()
    plan = {'source_ref': '4a14af5c48500091de1db54d972b3f6c7bd3ad18', 'source_files': provenance,
        'frozen_model_sha256': model_hash, 'method': 'gaussian', 'window': 252, 'shrinkage': 0.1,
        'drift': False, 'draws': 1000, 'seed': 20260921, 'folds': folds, 'denominator': len(files) * len(folds),
        'selection': 'No tuning or model selection; all phases evaluate the same frozen method',
        'limitations': ['Retrospective row-date cutoff test using supplied public snapshot; point-in-time revision vintage not certified',
            'Only daily level rates and FX panels; no monthly/log-return historical performance coverage',
            'Raw official metric components; no organizer normalization or aggregate ranking']}
    (destination / 'fold-manifest.json').write_text(json.dumps(plan, indent=2) + '\n')
    records = []
    for filename in files:
        wide = load_panel(source_dir / filename)
        assets = tuple(sorted(wide.columns))
        grid = GridSpec(assets, (5, 21))
        for fold in folds:
            record = {'panel': filename, **fold, 'status': 'unscorable', 'metrics': None}
            records.append(record)
            try:
                histories = training_only(wide, fold['cutoff'], assets)
                assert all(series.index.max() <= pd.Timestamp(fold['cutoff']) for series in histories.values())
                samples, fit = joint_samples(histories, grid, np.tile([5, 21], (len(assets), 1)),
                    target='level', monthly=False, draws=1000, seed=20260921)
                # Outcomes are accessed only after predictions and fitted parameters exist.
                y = target_values(wide, assets, fold['target_dates'])
                metrics = _composite(samples.reshape(1000, -1), y, weights=(0.5, 0.3, 0.2),
                    tail_levels=(0.01, 0.05, 0.95, 0.99), joint='variogram', tail_metric='pinball', ref_scale=None)
                name = filename.removesuffix('.parquet') + '-' + fold['fold_id'] + '.parquet'
                pd.DataFrame([(draw, asset, horizon, samples[draw, ai, hi])
                    for draw in range(1000) for ai, asset in enumerate(assets) for hi, horizon in enumerate(grid.horizons)],
                    columns=['draw', 'asset', 'horizon', 'value']).to_parquet(destination / name, index=False)
                record.update(status='scored_raw_official_metrics', metrics=metrics, fit=fit,
                    assets=list(assets), training_rows={a: len(s) for a, s in histories.items()},
                    forecast_sha256=hashlib.sha256((destination / name).read_bytes()).hexdigest(),
                    target_sha256=hashlib.sha256(y.astype('<f8').tobytes()).hexdigest())
            except (ValueError, KeyError) as exc:
                record['reason'] = str(exc)
    result = {'denominator': plan['denominator'], 'records': records,
        'scored_count': sum(record['status'] == 'scored_raw_official_metrics' for record in records),
        'aggregate_official_score': None, 'aggregate_reason': 'No official aggregate contract for these custom historical folds',
        'model_calls': 0, 'credentials_used': False}
    (destination / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f"Historical folds: {result['scored_count']}/{result['denominator']} scored with raw official metrics; no official aggregate.")
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run_backtest(args.source_dir, args.out)
