"""Explicit retrospective experiments; never changes production forecast defaults."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd
from qfbench2_track_forecasting.grid import GridSpec
from qfbench2_track_forecasting.scoring import _composite
from backtest import load_panel, validate_folds
from joint_model import joint_samples

ROOT = Path(__file__).resolve().parents[1]
SEEDS = [1701, 1702, 1703, 1704, 1705]
SOURCES = {
    'rates_level': ('t2-F3-election-2024-joint', 'rates_daily.parquet', 'level', False),
    'fx_level': ('t2-F3-election-2024-joint', 'g10_fx_daily.parquet', 'level', False),
    'factor_log_return': ('t2-F1-ai-mom-2024', 'factors_daily.parquet', 'log_return', False),
    'macro_monthly_level': ('t2-F1-sahm-watch-2024', 'macro_monthly.parquet', 'level', True),
}
BASE = {'method': 'gaussian', 'window': 252, 'shrinkage': .1, 'drift_mode': 'incumbent'}
COMPONENTS = ['marginal', 'joint', 'tail']
WEIGHTS = np.array([.5, .3, .2])


def fingerprint(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configurations():
    return [dict(method=method, window=window, shrinkage=shrink, drift_mode='incumbent')
            for method in ['gaussian', 'bootstrap'] for window in [126, 252, 504]
            for shrink in ([0., .1, .25] if method == 'gaussian' else [.1])]


def key(config):
    return '-'.join(str(config[k]) for k in ['method', 'window', 'shrinkage', 'drift_mode'])


def freeze_fold(wide, year, monthly):
    cutoff = pd.Timestamp(f'{year}-12-31')
    if monthly:
        # Observation months are not release dates: this lag is an experiment
        # convention only. Vintages and true availability remain uncertified.
        history_end = (cutoff.to_period('M') - 2).to_timestamp()
        targets = [(cutoff.to_period('M') + h).to_timestamp() for h in [1, 3]]
        history = wide.loc[:history_end].dropna()
        if history.empty: raise ValueError('missing monthly history')
        steps = [(d.to_period('M') - history.index[-1].to_period('M')).n for d in targets]
    else:
        history = wide.loc[:cutoff].dropna()
        future = wide.loc[wide.index > cutoff].dropna()
        if len(future) < 21: raise ValueError('missing complete future observations')
        steps = [5, 21]
        targets = [future.index[h-1] for h in steps]
    return {'year': year, 'phase': 'development' if year <= 2017 else 'selection' if year <= 2020 else 'holdout',
            'cutoff': cutoff.date().isoformat(), 'history_end': history.index[-1].date().isoformat(),
            'target_dates': [d.date().isoformat() for d in targets], 'steps': steps,
            'label_available_at': None,
            'assumed_label_available_by': (max(targets)+pd.Timedelta(days=90)).date().isoformat(),
            'regime': 'stress_calendar_proxy' if year+1 in [2020, 2022] else 'other_calendar'}


def sample_fold(wide, fold, config, target, monthly, seed, draws):
    # Only history is provided to fitting/sampling. No future value is used here.
    past = wide.loc[:pd.Timestamp(fold['history_end'])].dropna()
    assets = tuple(past.columns)
    histories = {a: past[a].copy() for a in assets}
    steps = np.tile(fold['steps'], (len(assets), 1))
    samples, fit = joint_samples(histories, GridSpec(assets, (5, 21)), steps,
        target=target, monthly=monthly, draws=draws, seed=seed,
        method=config['method'], window=config['window'], shrinkage=config['shrinkage'], drift=False)
    if config['drift_mode'] != 'incumbent':
        fraction = {'zero': 0., 'empirical': 1., 'shrunk': .25}[config['drift_mode']]
        desired = np.asarray(fit['empirical_mean']) * fraction
        samples += (desired - np.asarray(fit['applied_drift']))[None, :, None] * steps[None, :, :]
        fit['applied_drift'] = desired.tolist()
    return samples, fit


def actuals(wide, fold, target):
    targets = pd.to_datetime(fold['target_dates'])
    if target == 'log_return':
        future = wide.loc[wide.index > pd.Timestamp(fold['cutoff'])].dropna()
        if (future <= -1).any().any(): raise ValueError('invalid simple returns')
        y = np.stack([np.log1p(future.loc[:d]).sum().to_numpy() for d in targets], axis=1)
    else:
        y = wide.loc[targets].to_numpy().T
    if not np.isfinite(y).all(): raise ValueError('nonfinite target')
    return y.reshape(-1)


def score(samples, y):
    flat = samples.reshape(len(samples), -1)
    raw = _composite(flat, y, weights=(.5, .3, .2), tail_levels=(.01, .05, .95, .99),
                     joint='variogram', tail_metric='pinball', ref_scale=None)
    lo, hi = np.quantile(flat, [.05, .95], axis=0)
    raw.update(coverage90=float(np.mean((y >= lo) & (y <= hi))),
               width90=float(np.mean(hi-lo)),
               tail_below05=float(np.mean(y < lo)), tail_above95=float(np.mean(y > hi)))
    return raw


def proxy(row, scales):
    return float(WEIGHTS @ (np.array([row['metrics'][k] for k in COMPONENTS]) /
                            np.array(scales[row['group']])))


def aggregate(rows, scales):
    groups = {}
    for group in SOURCES:
        selected = [proxy(r, scales) for r in rows if r['group'] == group]
        if not selected: raise ValueError('missing required group; cannot silently drop denominator')
        groups[group] = float(np.mean(selected))
    return {'local_proxy': float(np.mean(list(groups.values()))), 'groups': groups}


def block_comparison(base_rows, candidate_rows, scales):
    base = {(r['group'], r['year'], r['seed']): proxy(r, scales) for r in base_rows}
    cand = {(r['group'], r['year'], r['seed']): proxy(r, scales) for r in candidate_rows}
    if base.keys() != cand.keys(): raise ValueError('unpaired comparison')
    years = sorted({k[1] for k in base})
    blocks = [np.mean([base[k] - cand[k] for k in base if k[1] == year]) for year in years]
    rng = np.random.default_rng(20260927)
    ci = np.quantile(np.mean(rng.choice(blocks, (10000, len(blocks))), axis=1), [.025, .975])
    a, b = aggregate(base_rows, scales), aggregate(candidate_rows, scales)
    relative = 1-b['local_proxy']/a['local_proxy']
    family_change = {g: b['groups'][g]/a['groups'][g]-1 for g in a['groups']}
    return {'baseline': a, 'candidate': b, 'relative_improvement': relative,
            'paired_year_improvement': dict(zip(years, blocks)), 'paired_block_95ci': ci.tolist(),
            'group_relative_change': family_change,
            'numerical_gate': relative >= .02 and ci[0] > 0 and max(family_change.values()) <= .02,
            'uncertainty_limit': 'Only three holdout year blocks; seed repetitions are not independent temporal evidence.'}


def run(destination):
    destination.mkdir(parents=True, exist_ok=False)
    datasets, inventory = {}, {}
    for group, (unit, filename, target, monthly) in SOURCES.items():
        source = ROOT/'track2-forecasting-public/units'/unit
        manifest = json.loads((source/'manifest.json').read_text(encoding='utf-8'))
        entry = next(row for row in manifest['files'] if row['path'] == filename)
        if fingerprint(source/filename) != entry['sha256']: raise ValueError('source manifest mismatch')
        wide = load_panel(source/filename)
        datasets[group] = wide
        folds = [freeze_fold(wide, y, monthly) for y in range(2014, 2024)]
        validate_folds(folds)
        for a, b in zip(folds, folds[1:]):
            if pd.Timestamp(a['assumed_label_available_by']) >= pd.Timestamp(b['cutoff']):
                raise ValueError('label availability embargo overlaps next fold')
        inventory[group] = {'source': str((source/filename).relative_to(ROOT)), 'manifest_sha256': fingerprint(source/'manifest.json'),
            'provenance': entry, 'assets': list(wide.columns), 'rows': len(wide), 'folds': folds,
            'first_observation': str(wide.index.min()), 'last_observation': str(wide.index.max()),
            'vintage_certified': False, 'actual_label_availability_known': False}
    plan = {'inventory': inventory, 'configs': configurations(), 'incumbent': BASE, 'seeds': SEEDS,
        'screen_draws': 1000, 'confirmation_draws': [500,1000],
        'drift_ablation': ['zero','empirical','shrunk'],
        'local_proxy': 'Equal weight across four target groups, then equal folds/seeds; .5 marginal + .3 joint + .2 tail, each divided by its group incumbent development mean (floor1e-12). Not official normalization.',
        'selection': 'Choose distribution on selection only, then drift on selection only; one fixed winner evaluated once on holdout at each draw count.',
        'daily_horizons': '5 and21 complete observed trading rows after cutoff, a local experiment convention, not a reproduction of every card horizon.',
        'monthly_horizons': 'Explicit Jan/Mar observation targets; history ends October (two-month reporting-lag assumption), sampling counts from actual last history month.',
        'embargo': 'Nonoverlapping windows; assumed label availability target+90 calendar days before next cutoff. This is not verified release history.',
        'missing_coverage': ['official F1-F4 text/regime tasks not represented by statistical panel groups', 'monthly log-return targets unavailable', 'first-release vintages unavailable', 'production normalization unavailable', 'long-horizon and transfer coverage absent'],
        'promotion_threshold': '>=2% heldout proxy improvement, positive paired block95CI, no group degradation>2%; data-eligibility/coverage gates still required.',
        'prior_exposure': 'Earlier incumbent-only evaluation reported 2021-2023 daily rates/FX folds. Holdout is excluded from this search, not historically unseen by the project.',
        'source_hashes': {p.name:fingerprint(p) for p in [Path(__file__),Path(__file__).with_name('joint_model.py'),Path(__file__).with_name('forecast.py')]},
        'production_changed': False, 'point_in_time_certified': False}
    (destination/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    records = []
    def evaluate(config, phase, draws=1000):
        result=[]
        for group, (_, _, target, monthly) in SOURCES.items():
            for fold in inventory[group]['folds']:
                if fold['phase'] != phase: continue
                for seed in SEEDS:
                    started=time.perf_counter()
                    samples,fit=sample_fold(datasets[group],fold,config,target,monthly,seed,draws)
                    y=actuals(datasets[group],fold,target)
                    row={'group':group,**fold,'seed':seed,'draws':draws,'config':key(config),
                         'metrics':score(samples,y),'fit':fit,
                         'sample_sha256':hashlib.sha256(samples.astype('<f8').tobytes()).hexdigest(),
                         'target_sha256':hashlib.sha256(y.astype('<f8').tobytes()).hexdigest(),
                         'elapsed_sec':time.perf_counter()-started}
                    result.append(row)
        records.extend(result)
        print(phase,key(config),draws,len(result),flush=True)
        return result
    development=evaluate(BASE,'development')
    scales={g:[max(1e-12,float(np.mean([r['metrics'][k] for r in development if r['group']==g])))
               for k in COMPONENTS] for g in SOURCES}
    (destination/'frozen-scales.json').write_text(json.dumps(scales,indent=2)+'\n')
    screening=[]
    for config in configurations():
        rows=evaluate(config,'selection'); screening.append((aggregate(rows,scales)['local_proxy'],config))
    distribution=min(screening,key=lambda x:(x[0],key(x[1])))[1]
    drift_scores=[next(x for x in screening if x[1]==distribution)]
    for drift in plan['drift_ablation']:
        config=dict(distribution,drift_mode=drift)
        drift_scores.append((aggregate(evaluate(config,'selection'),scales)['local_proxy'],config))
    winner=min(drift_scores,key=lambda x:(x[0],key(x[1])))[1]
    selection={'distribution':distribution,'winner':winner,'grid_scores':screening,'drift_scores':drift_scores,
               'holdout_not_used':True}
    (destination/'selection.json').write_text(json.dumps(selection,indent=2)+'\n')
    comparisons={}
    for draws in [500,1000]:
        b=evaluate(BASE,'holdout',draws)
        c=evaluate(winner,'holdout',draws) if winner!=BASE else b
        comparisons[str(draws)]=block_comparison(b,c,scales)
    diagnostics={}
    for config in [key(BASE),key(winner)]:
        rows=[r for r in records if r['config']==config and r['phase']=='holdout' and r['draws']==1000]
        if not rows: continue
        diagnostics[config]={'by_group':{},'by_regime':{}}
        for group in SOURCES:
            rs=[r for r in rows if r['group']==group]
            seed_means=[np.mean([proxy(r,scales) for r in rs if r['seed']==s]) for s in SEEDS]
            diagnostics[config]['by_group'][group]={
                'coverage90':float(np.mean([r['metrics']['coverage90'] for r in rs])),
                'width90':float(np.mean([r['metrics']['width90'] for r in rs])),
                'seed_mean_proxy_std':float(np.std(seed_means,ddof=1)),
                'dependence_error':None,
                'dependence_limit':'One realized vector per fold; three blocks cannot estimate a stable realized dependence matrix. Raw joint variogram retained.'}
        for regime in ['stress_calendar_proxy','other_calendar']:
            diagnostics[config]['by_regime'][regime]=aggregate([r for r in rows if r['regime']==regime],scales)
    result={'selection':selection,'comparisons':comparisons,'diagnostics':diagnostics,
        'record_count':len(records),'unscorable_count':0,'production_promotion':False,
        'promotion_blockers':['uncertified vintage/first availability','only three holdout blocks','F1-F4 task-family/long-horizon coverage missing'],
        'production_source_unchanged':fingerprint(Path(__file__).with_name('forecast.py'))==plan['source_hashes']['forecast.py'] and fingerprint(Path(__file__).with_name('joint_model.py'))==plan['source_hashes']['joint_model.py']}
    (destination/'records.json').write_text(json.dumps(records,indent=2)+'\n')
    (destination/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    (destination/'result-hashes.json').write_text(json.dumps({p.name:fingerprint(p) for p in destination.glob('*.json')},indent=2)+'\n')
    print(json.dumps(comparisons,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    run(parser.parse_args().out)
