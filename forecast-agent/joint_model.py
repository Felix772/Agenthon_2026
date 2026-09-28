"""Joint paths from cutoff-bounded historical innovations; no persisted fitted model."""
import numpy as np
import pandas as pd
from qfbench2_track_forecasting.targets import log_return_steps


def joint_samples(histories, grid, steps, *, target, monthly, draws, seed,
                  method='gaussian', shrinkage=0.1, drift=False, window=252,
                  monthly_trend=False):
    if method not in ('gaussian', 'bootstrap') or not 0 <= shrinkage <= 1 or window < 30:
        raise ValueError('Invalid statistical method configuration')
    if monthly_trend and (not monthly or target != 'level' or drift):
        raise ValueError('Monthly trend requires a monthly level target without generic drift')
    columns = {}
    for asset in grid.assets:
        series = histories[asset].copy()
        if monthly:
            series.index = series.index.to_period('M')
        if target == 'log_return':
            changes = pd.Series(log_return_steps(series), index=series.index)
        else:
            changes = series.diff()
            gaps = np.diff(series.index.asi8) if monthly else np.diff(series.index.values).astype('timedelta64[D]').astype(int)
            changes = changes.where(np.r_[False, gaps == 1 if monthly else gaps <= 7])
        columns[asset] = changes
    aligned = pd.DataFrame(columns).dropna().tail(window)
    if len(aligned) < 30 or not np.isfinite(aligned.to_numpy()).all():
        raise ValueError('At least 30 aligned finite historical innovations required')
    innovations = aligned.to_numpy(dtype=float)
    empirical_mean = innovations.mean(axis=0)
    applied_mean = empirical_mean if drift or target == 'log_return' else np.zeros(len(grid.assets))
    # One fixed, cutoff-only exploratory specification. The recent median limits
    # the effect of a single macro release; shrinkage and damping bound long paths.
    trend = None
    if monthly_trend:
        trend = (0.5 * np.median(innovations[-12:], axis=0) if len(innovations) >= 6
                 else np.zeros(len(grid.assets)))
    residuals = innovations - empirical_mean
    empirical_cov = np.atleast_2d(np.cov(innovations, rowvar=False, ddof=1))
    covariance = (1 - shrinkage) * empirical_cov + shrinkage * np.diag(np.diag(empirical_cov))
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    floor = max(float(np.trace(covariance)) / len(grid.assets) * 1e-10, 1e-12)
    covariance = eigenvectors @ np.diag(np.maximum(eigenvalues, floor)) @ eigenvectors.T
    root = np.linalg.cholesky(covariance)
    rng = np.random.default_rng(seed)
    anchor = np.array([histories[a].iloc[-1] if target == 'level' else 0 for a in grid.assets], dtype=float)
    if monthly:
        anchor_periods = np.array([histories[a].index[-1].to_period('M').ordinal for a in grid.assets])
        offsets = anchor_periods - anchor_periods.min()
    else:
        offsets = np.zeros(len(grid.assets), dtype=int)
    target_steps = np.asarray(steps, dtype=int) + offsets[:, None]
    if target_steps.max() > 10000:
        raise ValueError('Forecast path exceeds supported 10000-step bound')
    state = np.tile(anchor, (draws, 1))
    samples = np.empty((draws, len(grid.assets), len(grid.horizons)))
    for step in range(1, int(target_steps.max()) + 1):
        shock = (rng.standard_normal((draws, len(grid.assets))) @ root.T if method == 'gaussian'
                 else residuals[rng.integers(len(residuals), size=draws)])
        increment = shock + applied_mean
        if monthly_trend:
            elapsed = step - offsets
            increment = increment + trend * np.power(0.8, np.maximum(elapsed - 1, 0))
        state += increment * (step > offsets)
        for ai, hi in np.argwhere(target_steps == step):
            samples[:, ai, hi] = state[:, ai]
    stats = {'method': method, 'window_limit': window, 'fit_rows': len(aligned),
        'fit_first': str(aligned.index[0]), 'fit_last': str(aligned.index[-1]),
        'transformation': 'log1p(simple_return)' if target == 'log_return' else 'monthly level difference' if monthly else 'daily level difference',
        'missing_policy': 'complete aligned innovations; monthly gaps !=1 or daily gaps >7 days excluded',
        'empirical_mean': empirical_mean.tolist(), 'applied_drift': applied_mean.tolist(),
        'covariance': (covariance if method == 'gaussian' else residuals.T @ residuals / len(residuals)).tolist(),
        'shrinkage': shrinkage if method == 'gaussian' else None,
        'eigenvalue_floor': floor if method == 'gaussian' else None,
        'sampling': 'shared vector innovations across assets and nested future horizons',
        'bootstrap': 'centered historical innovation vectors with replacement' if method == 'bootstrap' else None}
    if monthly_trend:
        stats['monthly_trend'] = {'recent_transitions': min(12, len(innovations)),
            'estimator': 'half of recent monthly change median',
            'initial_step': trend.tolist(), 'damping': 0.8,
            'minimum_transitions': 6}
    return samples, stats
