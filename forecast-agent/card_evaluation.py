"""Inventory public T2 card shapes and compare monthly forecasts on labeled proxy folds.

The public cards contain inputs but no official outcomes or normalization scales.
Historical folds made from their panel snapshots are exploratory: their old input
vintages and first-release labels are not certified. This module records that
limit rather than presenting a retrospective proxy as an official score.
"""

import argparse
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tomllib
import zlib

import numpy as np
import pandas as pd
from qfbench2_track_forecasting.grid import GridSpec
from qfbench2_track_forecasting.horizons import monthly_horizon_steps
from qfbench2_track_forecasting.scoring import _composite

from joint_model import joint_samples


YEARS = tuple(range(2014, 2024))
SEEDS = (1701, 1702, 1703)
DRAWS = 500
MONTHLY_TREND = {'recent_transitions': 12, 'minimum_transitions': 6,
                 'median_shrink': 0.5, 'damping': 0.8}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_bytes(repo, commit, path):
    """Read immutable committed bytes, including revised public Parquet inputs."""
    command = ['git', '-c', f'safe.directory={repo.resolve().as_posix()}',
               '-C', str(repo), 'show', f'{commit}:{path}']
    return subprocess.check_output(command, stderr=subprocess.PIPE)


def git_paths(repo, commit):
    command = ['git', '-c', f'safe.directory={repo.resolve().as_posix()}',
               '-C', str(repo), 'ls-tree', '-r', '--name-only', commit, 'units']
    return subprocess.check_output(command, text=True, stderr=subprocess.PIPE).splitlines()


def exact_commit(repo, commit):
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Source commit must be a full 40-character SHA')
    command = ['git', '-c', f'safe.directory={repo.resolve().as_posix()}',
               '-C', str(repo), 'rev-parse', '--verify', f'{commit}^{{commit}}']
    resolved = subprocess.check_output(command, text=True, stderr=subprocess.PIPE).strip()
    if resolved != commit:
        raise ValueError('Source commit did not resolve exactly')


def effective_weights(card):
    params = card['scoring']['params']
    weight = params.get('weights', {'marginal': 0.5, 'joint': 0.3, 'tail': 0.2})
    values = (float(weight['marginal']), float(weight['joint']), float(weight['tail']))
    cells = len(card['targets']['asset_ids']) * len(card['targets']['horizons'])
    joint = params.get('joint', 'variogram')
    if cells == 1 and joint == 'variogram':
        live = values[0] + values[2]
        if live <= 0:
            raise ValueError('Single-cell marginal and tail weights must be positive')
        return (values[0] / live, 0.0, values[2] / live)
    return values


def card_inventory(repo, commit):
    """Freeze the source, authored grids, scoring shape, and known label gaps."""
    exact_commit(repo, commit)
    paths = set(git_paths(repo, commit))
    records = []
    for card_path in sorted(p for p in paths if p.endswith('/card.toml')):
        unit_dir = card_path.rsplit('/', 1)[0]
        card_bytes = git_bytes(repo, commit, card_path)
        card = tomllib.loads(card_bytes.decode('utf-8'))
        spec_path = f'{unit_dir}/forecast_spec.json'
        spec_bytes = git_bytes(repo, commit, spec_path) if spec_path in paths else None
        spec = json.loads(spec_bytes) if spec_bytes is not None else None
        manifest_path = f'{unit_dir}/manifest.json'
        manifest_bytes = git_bytes(repo, commit, manifest_path)
        manifest = json.loads(manifest_bytes)
        targets = card['targets']
        if spec is not None:
            for field in ('asset_ids', 'horizons', 'target_type'):
                if spec['targets'].get(field) != targets[field]:
                    raise ValueError(f'{unit_dir}: card/spec {field} mismatch')
        panel_entries = [entry for entry in manifest['files']
                         if entry['path'].endswith('.parquet')]
        if not panel_entries:
            raise ValueError(f'{unit_dir}: no manifest-declared panel')
        for entry in panel_entries:
            panel = git_bytes(repo, commit, f"{unit_dir}/{entry['path']}")
            if sha256(panel) != entry['sha256']:
                raise ValueError(f"{unit_dir}: manifest mismatch for {entry['path']}")
        frequency = targets.get('target_frequency', card.get('metadata', {}).get('target_frequency', 'daily'))
        periods = spec['targets'].get('observation_periods') if spec is not None else None
        if frequency == 'monthly' and not periods:
            raise ValueError(f'{unit_dir}: unresolved monthly observation periods')
        record = {
            'unit_id': card['task']['id'], 'unit_dir': unit_dir,
            'split': card['task'].get('split'), 'family': card['metadata']['category'],
            'asof': card['provenance']['data_cutoff'],
            'target': targets['target_type'], 'frequency': frequency,
            'asset_ids': targets['asset_ids'], 'horizons': targets['horizons'],
            'value_unit': targets.get('value_unit'),
            'cell_count': len(targets['asset_ids']) * len(targets['horizons']),
            'observation_periods': periods,
            'tail_levels': card['scoring']['params'].get('tail_levels', [0.01, 0.05, 0.95, 0.99]),
            'tail_metric': card['scoring']['params'].get('tail_metric', 'pinball'),
            'joint': card['scoring']['params'].get('joint', 'variogram'),
            'weights_effective': effective_weights(card),
            'card_sha256': sha256(card_bytes),
            'spec_sha256': sha256(spec_bytes) if spec_bytes is not None else None,
            'manifest_sha256': sha256(manifest_bytes),
            'panels': [{'path': entry['path'], 'sha256': entry['sha256']}
                       for entry in panel_entries],
            'panel_manifest_bytes_verified': True,
            'official_outcome': 'sealed', 'official_m0_scale': 'sealed',
        }
        records.append(record)
    ids = [record['unit_id'] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate card ids')
    return records


def score_case(samples, truth, m0_samples, card):
    """Use the installed track scorer with an approximate local M0 scale."""
    weights = tuple(card['weights_effective'])
    kwargs = {'weights': weights, 'tail_levels': tuple(card['tail_levels']),
              'joint': card['joint'], 'tail_metric': card['tail_metric']}
    truth = np.asarray(truth, dtype=float).reshape(-1)
    if samples.shape[1] != card['cell_count'] or m0_samples.shape[1] != card['cell_count']:
        raise ValueError('Sample matrix disagrees with authored card grid')
    reference = _composite(m0_samples, truth, ref_scale=None, **kwargs)
    scales = {key: float(reference[key]) for key in ('marginal', 'joint', 'tail')}
    if weights[1] == 0 and card['joint'] == 'variogram':
        scales['joint'] = 1.0
    for key, weight in zip(('marginal', 'joint', 'tail'), weights):
        if weight and (not np.isfinite(scales[key]) or scales[key] <= 0):
            raise ValueError(f'Invalid M0-like {key} normalization')
    candidate = _composite(samples, truth, ref_scale=scales, **kwargs)
    return {'raw': {key: candidate[key] for key in ('marginal', 'joint', 'tail')},
            'm0_like_raw': {key: reference[key] for key in ('marginal', 'joint', 'tail')},
            'local_normalized_proxy': candidate['composite'],
            'weights_effective': weights}


def read_monthly_panel(repo, commit, card):
    """Verify the current public panel bytes and select the authored target asset."""
    asset = card['asset_ids'][0]
    for entry in sorted(card['panels'], key=lambda row: row['path']):
        data = git_bytes(repo, commit, f"{card['unit_dir']}/{entry['path']}")
        if sha256(data) != entry['sha256']:
            raise ValueError(f"{card['unit_id']}: panel manifest checksum mismatch")
        frame = pd.read_parquet(io.BytesIO(data))
        column = 'asset' if 'asset' in frame else 'asset_id'
        if asset not in set(frame[column]):
            continue
        rows = frame.loc[frame[column] == asset, ['date', 'value']].copy()
        rows['date'] = pd.to_datetime(rows['date'])
        rows = rows.sort_values('date')
        periods = rows['date'].dt.to_period('M')
        if periods.duplicated().any() or not np.isfinite(rows['value'].to_numpy(dtype=float)).all():
            raise ValueError(f"{card['unit_id']}: duplicate month or nonfinite target history")
        series = pd.Series(rows['value'].to_numpy(dtype=float), index=pd.DatetimeIndex(rows['date']))
        if series.index.max() > pd.Timestamp(card['asof']):
            raise ValueError(f"{card['unit_id']}: panel exceeds card cutoff")
        return series, entry['sha256']
    raise ValueError(f"{card['unit_id']}: target asset missing from declared panels")


def monthly_steps(repo, commit, card, series):
    card_doc = tomllib.loads(git_bytes(repo, commit, f"{card['unit_dir']}/card.toml").decode())
    spec = json.loads(git_bytes(repo, commit, f"{card['unit_dir']}/forecast_spec.json"))
    steps = monthly_horizon_steps(tuple(card['asset_ids']), tuple(card['horizons']),
        {card['asset_ids'][0]: series.index[-1].date().isoformat()},
        asof=card['asof'], card=card_doc, forecast_spec=spec)
    return tuple(int(step) for step in np.asarray(steps).reshape(-1))


def monthly_pseudo_folds(series, steps, years=YEARS):
    """Yield fixed annual card-shaped proxy folds, retaining unresolved cases."""
    by_month = pd.Series(series.to_numpy(dtype=float), index=series.index.to_period('M'))
    if by_month.index.has_duplicates:
        raise ValueError('Ambiguous monthly panel')
    for year in years:
        cutoff = pd.Timestamp(f'{year}-12-31')
        history_end = cutoff.to_period('M') - 2  # explicit 45-day-lag proxy
        history = by_month.loc[:history_end]
        record = {'origin_year': year, 'cutoff': cutoff.date().isoformat(),
                  'history_end': str(history.index[-1]) if len(history) else None,
                  'steps': list(steps), 'status': 'unresolved'}
        if len(history) < 31:
            record['reason'] = 'fewer than 31 historical monthly levels'
            yield record, None, None
            continue
        targets = [history.index[-1] + step for step in steps]
        record['target_periods'] = [str(period) for period in targets]
        if any(period not in by_month.index for period in targets):
            record['reason'] = 'target observation absent from frozen panel'
            yield record, None, None
            continue
        record['status'] = 'exploratory_snapshot_proxy'
        input_series = pd.Series(history.to_numpy(),
                                 index=history.index.to_timestamp())
        truth = by_month.loc[targets].to_numpy(dtype=float)
        yield record, input_series, truth


def freeze_plan(repo, commit):
    cards = card_inventory(repo, commit)
    monthly = [card for card in cards if card['frequency'] == 'monthly']
    if len(monthly) != 4 or any(card['target'] != 'level' or len(card['asset_ids']) != 1 for card in monthly):
        raise ValueError('Current four-card monthly shape changed; revise the frozen experiment')
    for card in monthly:
        series, _ = read_monthly_panel(repo, commit, card)
        card['last_observation_period'] = str(series.index[-1].to_period('M'))
        card['monthly_steps'] = monthly_steps(repo, commit, card, series)
    shape = Counter((card['frequency'], card['target'], card['cell_count']) for card in cards)
    return {'source_commit': commit, 'source_repo': str(repo.resolve()),
            'model_sha256': sha256(Path(__file__).with_name('joint_model.py').read_bytes()),
            'evaluator_sha256': sha256(Path(__file__).read_bytes()),
            'cards': cards, 'card_count': len(cards),
            'shape_counts': [{'frequency': key[0], 'target': key[1], 'cells': key[2], 'count': value}
                             for key, value in sorted(shape.items())],
            'monthly_card_ids': [card['unit_id'] for card in monthly],
            'panel_manifest_verification': 'all manifest-declared Parquet bytes checked against the pinned commit',
            'monthly_step_source': 'official monthly_horizon_steps helper on pinned card, spec and panel',
            'candidate': {'monthly_trend': MONTHLY_TREND},
            'incumbent': {'method': 'gaussian', 'window': 252, 'shrinkage': 0.1,
                          'drift': False, 'monthly_trend': False},
            'years': YEARS, 'seeds': SEEDS, 'draws': DRAWS,
            'monthly_publication_lag_proxy': 'history through October at each December 31 origin',
            'evaluation_status': 'exploratory_only',
            'm0_proxy_method': (
                'Approximate local M0: joint_samples Gaussian, 300 aligned innovations, '
                'zero covariance shrinkage, empirical drift and 500 draws; pseudo-case '
                'CRC32 seed. Published M0 instead begins with 300 asset observations, '
                'has a different gap rule, card-id seed, cell-level draw construction '
                'and jitter. Neither official outcome nor official per-card scale is available.'),
            'limits': ['historical input vintages at pseudo origins are uncertified',
                       'first-release/fixed-vintage outcomes are not available for all cards',
                       'official outcome and M0 normalization are sealed',
                       'four monthly panel snapshots contain overlapping history']}


def run_monthly(plan):
    repo, commit = Path(plan['source_repo']), plan['source_commit']
    exact_commit(repo, commit)
    if sha256(Path(__file__).read_bytes()) != plan['evaluator_sha256']:
        raise ValueError('Evaluator changed after plan freeze')
    if sha256(Path(__file__).with_name('joint_model.py').read_bytes()) != plan['model_sha256']:
        raise ValueError('Model changed after plan freeze')
    if (tuple(plan['years']) != YEARS or tuple(plan['seeds']) != SEEDS or plan['draws'] != DRAWS
            or plan['candidate']['monthly_trend'] != MONTHLY_TREND):
        raise ValueError('Evaluation settings changed after plan freeze')
    rows = []
    for card in plan['cards']:
        if card['unit_id'] not in plan['monthly_card_ids']:
            continue
        series, panel_sha = read_monthly_panel(repo, commit, card)
        steps = monthly_steps(repo, commit, card, series)
        if tuple(card['monthly_steps']) != steps:
            raise ValueError(f"{card['unit_id']}: monthly steps changed after plan freeze")
        for fold, history, truth in monthly_pseudo_folds(series, steps, YEARS):
            record = {'card_id': card['unit_id'], 'family': card['family'],
                      'authored_horizons': card['horizons'], 'panel_sha256': panel_sha,
                      'monthly_steps': steps, **fold,
                      'value_unit': card['value_unit'],
                      'input_vintage': 'later card-date snapshot; not verified at pseudo cutoff',
                      'label_vintage': 'later card-date snapshot; not certified to task target convention',
                      'eligible_for_promotion': False}
            if history is None:
                rows.append(record)
                continue
            grid = GridSpec(tuple(card['asset_ids']), tuple(card['horizons']))
            histories = {card['asset_ids'][0]: history}
            step_matrix = np.asarray([steps], dtype=int)
            m0_seed = zlib.crc32(f"{card['unit_id']}:{fold['origin_year']}".encode()) & 0x7fffffff
            m0, _ = joint_samples(histories, grid, step_matrix, target='level', monthly=True,
                                  draws=DRAWS, seed=m0_seed, window=300, shrinkage=0,
                                  drift=True)
            m0 = m0.reshape(DRAWS, -1)
            for seed in SEEDS:
                base, _ = joint_samples(histories, grid, step_matrix, target='level', monthly=True,
                    draws=DRAWS, seed=seed)
                candidate, fit = joint_samples(histories, grid, step_matrix,
                    target='level', monthly=True, draws=DRAWS, seed=seed,
                    monthly_trend=True)
                base_score = score_case(base.reshape(DRAWS, -1), truth, m0, card)
                candidate_score = score_case(candidate.reshape(DRAWS, -1), truth, m0, card)
                rows.append({**record, 'seed': seed, 'status': 'scored_exploratory_proxy',
                             'incumbent': base_score, 'candidate': candidate_score,
                             'fitted_monthly_trend': fit['monthly_trend'],
                             'sample_sha256': {'incumbent': sha256(base.astype('<f8').tobytes()),
                                               'candidate': sha256(candidate.astype('<f8').tobytes())},
                             'truth_sha256': sha256(truth.astype('<f8').tobytes())})
    scored = [row for row in rows if row['status'] == 'scored_exploratory_proxy']
    by_card = {}
    for card_id in plan['monthly_card_ids']:
        subset = [row for row in scored if row['card_id'] == card_id]
        if not subset:
            continue
        a = float(np.mean([row['incumbent']['local_normalized_proxy'] for row in subset]))
        b = float(np.mean([row['candidate']['local_normalized_proxy'] for row in subset]))
        by_card[card_id] = {'scored_rows': len(subset), 'incumbent_proxy': a,
                            'candidate_proxy': b, 'relative_improvement': 1 - b / a}
    return {'source_commit': commit, 'evaluation_status': 'exploratory_only',
            'production_promotion': False, 'rows': rows, 'scored_rows': len(scored),
            'm0_proxy_method': plan['m0_proxy_method'],
            'unresolved_folds': sum(row['status'] == 'unresolved' for row in rows),
            'by_card': by_card,
            'equal_card_proxy': ({'incumbent': float(np.mean([v['incumbent_proxy'] for v in by_card.values()])),
                                  'candidate': float(np.mean([v['candidate_proxy'] for v in by_card.values()]))}
                                 if len(by_card) == len(plan['monthly_card_ids']) else None)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('plan')
    make.add_argument('--repo', type=Path, required=True)
    make.add_argument('--commit', required=True)
    make.add_argument('--out', type=Path, required=True)
    run = sub.add_parser('run')
    run.add_argument('--plan', type=Path, required=True)
    run.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'plan':
        payload = freeze_plan(args.repo, args.commit)
    else:
        payload = run_monthly(json.loads(args.plan.read_text(encoding='utf-8')))
    with args.out.open('x', encoding='utf-8') as target:
        json.dump(payload, target, indent=2)
        target.write('\n')
    print(f'{args.command}: {args.out}')


if __name__ == '__main__':
    main()
