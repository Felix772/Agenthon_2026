"""Choose a daily forecast using only one card's cutoff-bounded panel history."""

import numpy as np
import pandas as pd
from qfbench2_common.scoring import crps

from joint_model import joint_samples
from reference_model import reference_samples


ARMS = ('incumbent', 'reference', 'half_pool')


def _tail_pinball(samples, realized):
    """Published T2 pinball formula; see official qfbench2_track_forecasting.tail."""
    flat = samples.reshape(len(samples), -1)
    y = realized.reshape(-1)
    total = 0.
    for level in (.01, .05, .95, .99):
        quantile = np.quantile(flat, level, axis=0)
        total += float(np.mean(np.where(y >= quantile, level*(y-quantile),
                                        (1.-level)*(quantile-y))))
    return total/4


def _half_pool(incumbent, reference):
    if incumbent.shape != reference.shape:
        raise ValueError('Forecast grids cannot be pooled')
    pooled = incumbent.copy()
    pooled[::2] = reference[::2]
    return pooled


def _components(samples, realized):
    flat = samples.reshape(len(samples), -1)
    y = realized.reshape(-1)
    return {'marginal': crps.crps_marginal(flat, y),
            'joint': crps.variogram_score(flat, y, p=.5),
            'tail': _tail_pinball(samples, realized)}


def _normalized_score(components, reference, cell_count):
    weights = (5/7, 0., 2/7) if cell_count == 1 else (.5, .3, .2)
    keys = ('marginal', 'joint', 'tail')
    return float(min(4., sum(weight * components[key] / max(reference[key], 1e-12)
                             for weight, key in zip(weights, keys) if weight)))


def _folds(histories, grid, horizons):
    """Up to three nonoverlapping, complete, pre-cutoff validation windows."""
    wide = pd.DataFrame({asset: histories[asset] for asset in grid.assets}).dropna()
    if not isinstance(wide.index, pd.DatetimeIndex) or wide.index.has_duplicates:
        return []
    long_horizon = int(max(horizons))
    stride = max(long_horizon, 63)
    folds = []
    for origin in range(len(wide)-long_horizon-1, 299, -stride):
        dates = wide.index[origin:origin+long_horizon+1]
        gap_days = np.diff(dates.values).astype('timedelta64[D]').astype(int)
        if len(dates) != long_horizon+1 or np.any(gap_days > 7):
            continue
        folds.append((wide.iloc[:origin+1], wide.iloc[origin+1:origin+long_horizon+1]))
        if len(folds) == 3:
            break
    return list(reversed(folds))


def _realized(future, grid, target):
    horizons = np.asarray(grid.horizons, dtype=int)
    if target == 'level':
        return future.iloc[horizons-1][list(grid.assets)].to_numpy(dtype=float).T
    returns = future[list(grid.assets)].to_numpy(dtype=float)
    if not np.isfinite(returns).all() or np.any(returns <= -1):
        raise ValueError('Invalid historical simple return')
    return np.log1p(returns).cumsum(axis=0)[horizons-1].T


def _draw_arms(histories, grid, steps, target, seed, unit_id):
    draws = 500
    incumbent, _ = joint_samples(histories, grid, steps, target=target,
                                  monthly=False, draws=draws, seed=seed,
                                  method='gaussian', shrinkage=.1,
                                  drift=False, window=252)
    reference, _ = reference_samples(histories, grid, steps, target=target,
                                      draws=draws, unit_id=unit_id, seed=seed)
    return {'incumbent': incumbent, 'reference': reference,
            'half_pool': _half_pool(incumbent, reference)}


def online_samples(histories, grid, steps, *, target, seed, unit_id):
    """Validate forecast arms inside the supplied panel, then fit on all rows.

    No source outside the unit is consulted. Two older windows select one arm;
    the newest window can veto it. With fewer than three valid windows or a
    small/inconsistent gain, the previously submitted method wins.
    """
    if np.asarray(steps).shape != (len(grid.assets), len(grid.horizons)):
        raise ValueError('Step grid shape mismatch')
    if target not in ('level', 'log_return'):
        raise ValueError('Unsupported daily target')
    if any(int(h) < 1 for h in grid.horizons):
        raise ValueError('Invalid daily horizon')
    folds = _folds(histories, grid, grid.horizons)
    score_rows = []
    for fold_index, (past, future) in enumerate(folds):
        fold_histories = {asset: past[asset] for asset in grid.assets}
        fold_seed = (int(seed) + 1009 * (fold_index + 1)) & 0x7FFFFFFF
        try:
            forecasts = _draw_arms(fold_histories, grid, steps, target, fold_seed,
                                   f'{unit_id}:historical-fold-{fold_index}')
            observed = _realized(future, grid, target)
            reference_score = _components(forecasts['reference'], observed)
            scores = {arm: _normalized_score(_components(samples, observed), reference_score,
                                              len(grid.assets)*len(grid.horizons))
                      for arm, samples in forecasts.items()}
        except (ValueError, np.linalg.LinAlgError):
            continue
        score_rows.append({'cutoff': str(past.index[-1].date()), 'scores': scores})
    choice = 'incumbent'
    confirmation_passed = False
    if len(score_rows) >= 3:
        selection_rows = score_rows[:-1]
        confirmation = score_rows[-1]
        means = {arm: float(np.mean([row['scores'][arm] for row in selection_rows]))
                 for arm in ARMS}
        candidate = min(ARMS, key=lambda arm: (means[arm], ARMS.index(arm)))
        wins = sum(row['scores'][candidate] < row['scores']['incumbent']
                   for row in selection_rows)
        if (candidate != 'incumbent' and means[candidate] <= .98*means['incumbent']
                and wins == len(selection_rows)
                and confirmation['scores'][candidate] <= confirmation['scores']['incumbent']):
            choice = candidate
            confirmation_passed = True
    forecasts = _draw_arms(histories, grid, steps, target, int(seed), unit_id)
    stats = {'method': 'unit-local-rolling-selection', 'selected_arm': choice,
             'candidate_arms': ARMS, 'validation_windows': len(score_rows),
             'validation': score_rows, 'minimum_relative_gain': .02,
             'selection_windows': max(0, len(score_rows)-1),
             'confirmation_windows': 1 if len(score_rows) >= 3 else 0,
             'confirmation_passed': confirmation_passed,
             'fallback': 'incumbent when fewer than three folds or insufficient evidence',
             'data_scope': 'current unit panel at or before its as-of date only',
             'seed': int(seed)}
    return forecasts[choice], stats
