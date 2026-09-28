"""Confirm the frozen monthly trend on two dated, public T2 target vintages.

This study uses the current committed card inputs, official ALFRED release-page
labels, the installed track scorer, and the same approximate local M0 recipe as
card_evaluation.py. It cannot reproduce sealed official scales or outcomes.
"""

import argparse
import json
from pathlib import Path
import zlib

import numpy as np
from qfbench2_track_forecasting.grid import GridSpec

from card_evaluation import (DRAWS, MONTHLY_TREND, SEEDS, exact_commit,
                             git_bytes, monthly_steps, read_monthly_panel,
                             score_case, sha256)
from joint_model import joint_samples


SOURCE_COMMIT = '28a6cae9674f69e63a07a19165a9217e85eacfff'
UPSTREAM_REFS = {
    'Agenthon2026-public': '95a0de3d9a814f3883c151b7efdbbcf579139244',
    'track1-coding-public': '1a60fc48024c4f0e9978416d84e8370dbb0236cd',
    'track2-forecasting-public': SOURCE_COMMIT,
    'track3-simulation-public': 'f910de231209ebbca060efee47fe2c41aabe4bce',
    'track4-analysis-public': '7b2bce1d80d96f5d5667d7f67bfaa945fa5d1491',
}
LABELS = {
    't2-F4-covid-nfp-2020': {
        'asset': 'NFP', 'series_id': 'PAYEMS', 'observation_period': '2020-04',
        'vintage_date': '2020-05-08', 'value': 131072.0,
        'source_url': 'https://alfred.stlouisfed.org/release?rid=50&rd=2020-05-08',
        'source_release': 'Employment Situation',
        'source_unit': 'thousands of persons, seasonally adjusted',
        'crosscheck': 'ALFRED 2020-05-11 revision is 131045; the card requires first release',
    },
    't2-F4-cpi-vintage-2022': {
        'asset': 'CPI_ALL', 'series_id': 'CPIAUCSL',
        'observation_period': '2022-06', 'vintage_date': '2022-07-13',
        'value': 295.328,
        'source_url': 'https://alfred.stlouisfed.org/release?rid=10&rd=2022-07-13',
        'source_release': 'Consumer Price Index',
        'source_unit': 'index 1982-1984=100, seasonally adjusted',
        'crosscheck': 'CPIAUCNS is 296.311 on the same page and is not the target',
    },
}
UNRESOLVED = {
    't2-F1-cpi-glidepath-2023':
        'current-vintage target has no organizer scoring snapshot date',
    't2-F1-sahm-watch-2024':
        'current-vintage target has no organizer scoring snapshot date',
}
CONFIRMATION_IDS = tuple(sorted(LABELS))
MONTHLY_IDS = tuple(sorted((*LABELS, *UNRESOLVED)))
BASE_PLAN_SHA256 = 'ac8cddca1860ef3ae50781565845b09778de4183748c15a04e789f29af40102a'


def _expected_artifacts():
    return {
        'model_sha256': sha256(Path(__file__).with_name('joint_model.py').read_bytes()),
        'card_evaluator_sha256': sha256(Path(__file__).with_name('card_evaluation.py').read_bytes()),
        'confirmation_script_sha256': sha256(Path(__file__).read_bytes()),
    }


def _card_cases(repo, base_plan):
    if (base_plan['source_commit'] != SOURCE_COMMIT or
            tuple(sorted(base_plan['monthly_card_ids'])) != MONTHLY_IDS):
        raise ValueError('The frozen v5 source or four-card inventory changed')
    cards = {c['unit_id']: c for c in base_plan['cards'] if c['unit_id'] in MONTHLY_IDS}
    if tuple(sorted(cards)) != MONTHLY_IDS:
        raise ValueError('The frozen v5 monthly cards changed')
    cases = []
    for card_id in MONTHLY_IDS:
        card = cards[card_id]
        if (card['target'] != 'level' or card['frequency'] != 'monthly' or
                card['cell_count'] != len(card['horizons']) or
                len(card['asset_ids']) != 1 or
                not card['panel_manifest_bytes_verified'] or
                card['official_outcome'] != 'sealed'):
            raise ValueError(f'{card_id}: unsupported or unverified card shape')
        # The v5 inventory already checked every manifest entry. Re-read the two
        # confirmation panels from the pinned Git objects and verify them again.
        case = {key: card[key] for key in (
            'unit_id', 'unit_dir', 'split', 'family', 'asof', 'target',
            'frequency', 'asset_ids', 'horizons', 'value_unit', 'cell_count',
            'observation_periods', 'tail_levels', 'tail_metric', 'joint',
            'weights_effective', 'card_sha256', 'spec_sha256',
            'manifest_sha256', 'panels', 'last_observation_period',
            'monthly_steps')}
        if card_id in LABELS:
            label = LABELS[card_id]
            if (case['asset_ids'] != [label['asset']] or
                    case['observation_periods'] != [label['observation_period']] or
                    case['horizons'] != [21] or case['monthly_steps'] != [2] or
                    case['cell_count'] != 1):
                raise ValueError(f'{card_id}: authored target no longer matches label')
            series, panel_sha = read_monthly_panel(repo, SOURCE_COMMIT, card)
            steps = monthly_steps(repo, SOURCE_COMMIT, card, series)
            if panel_sha not in {p['sha256'] for p in card['panels']} or steps != (2,):
                raise ValueError(f'{card_id}: pinned input panel or step mismatch')
            if (str(series.index[-1].to_period('M')) != card['last_observation_period']
                    or len(series) < 31):
                raise ValueError(f'{card_id}: unexpected target history')
            case['input_history_rows'] = len(series)
            case['input_first_observation'] = series.index[0].date().isoformat()
            case['input_last_observation'] = series.index[-1].date().isoformat()
            case['input_last_value'] = float(series.iloc[-1])
            case['panel_sha256'] = panel_sha
            case['label'] = label
            case['status'] = 'fixed_label_confirmation'
        else:
            case['status'] = 'unresolved'
            case['reason'] = UNRESOLVED[card_id]
        cases.append(case)
    return cases


def freeze_plan(repo, base_plan_path):
    repo = repo.resolve()
    base_plan_path = base_plan_path.resolve()
    exact_commit(repo, SOURCE_COMMIT)
    base_bytes = base_plan_path.read_bytes()
    if sha256(base_bytes) != BASE_PLAN_SHA256:
        raise ValueError('The prior v5 plan bytes changed')
    base_plan = json.loads(base_bytes)
    artifacts = _expected_artifacts()
    if (base_plan['model_sha256'] != artifacts['model_sha256'] or
            base_plan['evaluator_sha256'] != artifacts['card_evaluator_sha256'] or
            tuple(base_plan['seeds']) != SEEDS or base_plan['draws'] != DRAWS or
            base_plan['candidate']['monthly_trend'] != MONTHLY_TREND):
        raise ValueError('The candidate or prior evaluation recipe changed')
    return {
        'status': 'predeclared_before_scoring', 'study': 'two fixed-vintage public monthly cards',
        'upstream_refs': UPSTREAM_REFS, 'source_repo': str(repo),
        'base_plan_path': str(base_plan_path),
        'source_commit': SOURCE_COMMIT, 'base_plan_sha256': BASE_PLAN_SHA256,
        'artifacts': artifacts, 'cases': _card_cases(repo, base_plan),
        'seeds': list(SEEDS), 'draws': DRAWS,
        'incumbent': base_plan['incumbent'],
        'candidate': base_plan['candidate'],
        'm0_like_method': (
            'Same v5 approximation: joint_samples Gaussian, 300 aligned innovations, '
            'zero covariance shrinkage, empirical drift, 500 draws, and CRC32 '
            'pseudo-case seed. For these current-card cases the seed text is '
            'card_id:asof_year. The published official M0 differs in its initial '
            'observation window, gap rule, seed, cell draw construction and floor.'),
        'score_method': (
            'Installed qfbench2_track_forecasting.scoring._composite raw components; '
            'single-cell variogram omitted and effective weights 5/7 marginal, '
            '2/7 tail; same per-card M0-like component scales for paired arms'),
        'promotion_eligible': False,
        'limits': [
            'Only two single-cell public labels with fixed vintages are available',
            'The committed organizer input panels are trusted but ALFRED input histories were not independently reconstructed',
            'Official outcome and M0 normalization scales remain sealed',
            'The two current-vintage monthly cards remain unresolved',
            'This confirmation cannot alone justify production model promotion',
        ],
    }


def validate_plan(plan):
    if (plan['status'] != 'predeclared_before_scoring' or
            plan['source_commit'] != SOURCE_COMMIT or
            plan['base_plan_sha256'] != BASE_PLAN_SHA256 or
            plan['upstream_refs'] != UPSTREAM_REFS or
            plan['artifacts'] != _expected_artifacts() or
            tuple(plan['seeds']) != SEEDS or plan['draws'] != DRAWS or
            plan['candidate']['monthly_trend'] != MONTHLY_TREND or
            tuple(sorted(c['unit_id'] for c in plan['cases'])) != MONTHLY_IDS):
        raise ValueError('Confirmation plan is not the frozen specification')
    for case in plan['cases']:
        card_id = case['unit_id']
        if card_id in LABELS:
            if (case['status'] != 'fixed_label_confirmation' or
                    case['label'] != LABELS[card_id] or
                    case['asset_ids'] != [LABELS[card_id]['asset']] or
                    case['observation_periods'] != [LABELS[card_id]['observation_period']] or
                    case['monthly_steps'] != [2] or case['horizons'] != [21]):
                raise ValueError(f'{card_id}: label or card changed after freezing')
        elif case['status'] != 'unresolved' or case['reason'] != UNRESOLVED[card_id]:
            raise ValueError(f'{card_id}: unresolved card changed after freezing')


def run_confirmation(plan):
    validate_plan(plan)
    repo = Path(plan['source_repo'])
    exact_commit(repo, SOURCE_COMMIT)
    if plan != freeze_plan(repo, Path(plan['base_plan_path'])):
        raise ValueError('Confirmation plan contents changed after freezing')
    rows = []
    for case in plan['cases']:
        card_id = case['unit_id']
        if case['status'] == 'unresolved':
            rows.append({'card_id': card_id, 'status': 'unresolved',
                         'reason': case['reason'], 'scored': False})
            continue
        for name, suffix in [('card_sha256', 'card.toml'),
                             ('spec_sha256', 'forecast_spec.json'),
                             ('manifest_sha256', 'manifest.json')]:
            if sha256(git_bytes(repo, SOURCE_COMMIT,
                                f"{case['unit_dir']}/{suffix}")) != case[name]:
                raise ValueError(f'{card_id}: frozen source file changed')
        series, panel_sha = read_monthly_panel(repo, SOURCE_COMMIT, case)
        steps = monthly_steps(repo, SOURCE_COMMIT, case, series)
        if (panel_sha != case['panel_sha256'] or steps != (2,) or
                len(series) != case['input_history_rows'] or
                series.index[-1].date().isoformat() != case['input_last_observation'] or
                float(series.iloc[-1]) != case['input_last_value']):
            raise ValueError(f'{card_id}: frozen panel history changed')
        label = case['label']
        truth = np.asarray([label['value']], dtype=float)
        grid = GridSpec(tuple(case['asset_ids']), tuple(case['horizons']))
        histories = {case['asset_ids'][0]: series}
        step_matrix = np.asarray([steps], dtype=int)
        m0_seed_text = f"{card_id}:{case['asof'][:4]}"
        m0_seed = zlib.crc32(m0_seed_text.encode()) & 0x7fffffff
        m0, _ = joint_samples(histories, grid, step_matrix, target='level',
                              monthly=True, draws=DRAWS, seed=m0_seed,
                              window=300, shrinkage=0, drift=True)
        m0_cells = m0.reshape(DRAWS, -1)
        for seed in SEEDS:
            common = dict(target='level', monthly=True, draws=DRAWS, seed=seed)
            incumbent, _ = joint_samples(histories, grid, step_matrix, **common)
            candidate, fit = joint_samples(histories, grid, step_matrix,
                                           monthly_trend=True, **common)
            rows.append({
                'card_id': card_id, 'status': 'scored_local_fixed_label',
                'scored': True, 'asof': case['asof'],
                'asset': label['asset'], 'series_id': label['series_id'],
                'authored_horizon': case['horizons'][0], 'monthly_steps': 2,
                'observation_period': label['observation_period'],
                'label_value': label['value'], 'label_unit': label['source_unit'],
                'label_vintage_date': label['vintage_date'],
                'label_source_url': label['source_url'],
                'input_panel_sha256': panel_sha,
                'input_vintage': f"committed as-of {case['asof']} ALFRED panel",
                'seed': seed, 'm0_like_seed_text': m0_seed_text,
                'm0_like_seed': m0_seed,
                'incumbent': score_case(incumbent.reshape(DRAWS, -1), truth,
                                        m0_cells, case),
                'candidate': score_case(candidate.reshape(DRAWS, -1), truth,
                                        m0_cells, case),
                'fitted_monthly_trend': fit['monthly_trend'],
                'sample_sha256': {
                    'incumbent': sha256(incumbent.astype('<f8').tobytes()),
                    'candidate': sha256(candidate.astype('<f8').tobytes()),
                    'm0_like': sha256(m0.astype('<f8').tobytes()),
                },
                'truth_sha256': sha256(truth.astype('<f8').tobytes()),
            })
    summaries = {}
    for card_id in CONFIRMATION_IDS:
        card_rows = [r for r in rows if r['card_id'] == card_id and r['scored']]
        base = float(np.mean([r['incumbent']['local_normalized_proxy'] for r in card_rows]))
        new = float(np.mean([r['candidate']['local_normalized_proxy'] for r in card_rows]))
        summaries[card_id] = {
            'seed_count': len(card_rows), 'incumbent_proxy': base,
            'candidate_proxy': new, 'relative_improvement': 1 - new / base,
        }
    return {
        'study': plan['study'], 'source_commit': SOURCE_COMMIT,
        'evaluation_status': 'local_fixed_label_confirmation_only',
        'official_score': None, 'production_promotion': False,
        'scored_card_count': len(summaries), 'unresolved_card_count': len(UNRESOLVED),
        'scored_seed_rows': sum(r['scored'] for r in rows),
        'rows': rows, 'by_card': summaries,
        'equal_card_incumbent_proxy': float(np.mean([x['incumbent_proxy'] for x in summaries.values()])),
        'equal_card_candidate_proxy': float(np.mean([x['candidate_proxy'] for x in summaries.values()])),
        'limits': plan['limits'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('plan')
    make.add_argument('--repo', type=Path, required=True)
    make.add_argument('--base-plan', type=Path, required=True)
    make.add_argument('--out', type=Path, required=True)
    run = sub.add_parser('run')
    run.add_argument('--plan', type=Path, required=True)
    run.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'plan':
        payload = freeze_plan(args.repo, args.base_plan)
    else:
        plan_bytes = args.plan.read_bytes()
        payload = run_confirmation(json.loads(plan_bytes))
        payload['plan_sha256'] = sha256(plan_bytes)
    with args.out.open('x', encoding='utf-8') as target:
        json.dump(payload, target, indent=2)
        target.write('\n')
    print(f'{args.command}: {args.out}')


if __name__ == '__main__':
    main()
