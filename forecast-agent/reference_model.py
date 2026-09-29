"""Reconstruct the published M0 daily random walk from the current unit's inputs."""

import zlib

import numpy as np
import pandas as pd


def reference_samples(histories, grid, steps, *, target, draws, unit_id, seed=None):
    """Sample a cutoff-only joint Gaussian over the card's asset/horizon grid.

    This is an intentionally conservative reference profile. It uses the public
    M0 construction, but never reads another card or a realized outcome.
    """
    if target not in ('level', 'log_return') or draws != 500:
        raise ValueError('Reference profile requires a supported target and 500 draws')
    if not isinstance(unit_id, str) or not unit_id:
        raise ValueError('A nonempty unit identifier is required for deterministic draws')
    assets = tuple(sorted(grid.assets))
    if np.asarray(steps).shape != (len(grid.assets), len(grid.horizons)):
        raise ValueError('Step grid shape mismatch')
    changes = {}
    last = {}
    for asset in assets:
        history = histories[asset].sort_index().tail(300)
        if len(history) < 31 or history.index.has_duplicates:
            raise ValueError('Insufficient or ambiguous reference history')
        dates = pd.DatetimeIndex(history.index)
        gap_days = np.diff(dates.values).astype('timedelta64[D]').astype(int)
        if np.any(gap_days <= 0):
            raise ValueError('Reference history must have increasing dates')
        max_gap = max(10 * float(np.median(gap_days)), 5.0)
        values = history.to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError('Non-finite reference history')
        if target == 'log_return' and np.median(np.abs(values)) >= .2:
            raise ValueError('Log-return panel does not resemble per-step returns')
        increments = np.diff(values) if target == 'level' else values[1:]
        changes[asset] = pd.Series(increments, index=dates[1:]).where(gap_days <= max_gap)
        last[asset] = float(values[-1]) if target == 'level' else 0.0
    aligned = pd.DataFrame(changes).dropna()
    if len(aligned) < 30:
        raise ValueError('Insufficient aligned reference innovations')
    observations = aligned.to_numpy(dtype=float)
    mean_step = observations.mean(axis=0)
    sigma = np.atleast_2d(np.cov(observations, rowvar=False, ddof=1))
    if not np.isfinite(sigma).all():
        raise ValueError('Non-finite reference covariance')
    asset_index = {asset: index for index, asset in enumerate(assets)}
    steps_by_cell = {(asset, horizon): int(steps[grid.assets.index(asset), grid.horizons.index(horizon)])
                     for asset in grid.assets for horizon in grid.horizons}
    cells = tuple(sorted(steps_by_cell))
    if any(steps_by_cell[cell] < 1 or steps_by_cell[cell] > 10000 for cell in cells):
        raise ValueError('Reference forecast step outside supported range')
    centers = np.asarray([last[a] + steps_by_cell[(a, h)] * mean_step[asset_index[a]]
                          for a, h in cells])
    covariance = np.asarray([[min(steps_by_cell[i], steps_by_cell[j])
                              * sigma[asset_index[i[0]], asset_index[j[0]]]
                              for j in cells] for i in cells], dtype=float)
    covariance[np.diag_indices_from(covariance)] += 1e-10
    try:
        root = np.linalg.cholesky(covariance + 1e-9 * np.eye(len(cells)))
    except np.linalg.LinAlgError:
        root = np.diag(np.sqrt(np.maximum(np.diag(covariance) + 1e-9, 0)))
    seed = ((zlib.crc32(unit_id.encode('utf-8')) & 0x7FFFFFFF)
            if seed is None else int(seed))
    random = np.random.default_rng(seed).standard_normal((draws, len(cells)))
    flat = centers + random @ root.T
    by_cell = {cell: i for i, cell in enumerate(cells)}
    samples = np.empty((draws, len(grid.assets), len(grid.horizons)), dtype=float)
    for ai, asset in enumerate(grid.assets):
        for hi, horizon in enumerate(grid.horizons):
            samples[:, ai, hi] = flat[:, by_cell[(asset, horizon)]]
    stats = {'method': 'published-reference-walk', 'window_limit': 300,
             'fit_rows': len(aligned), 'fit_first': str(aligned.index[0]),
             'fit_last': str(aligned.index[-1]), 'gap_policy': 'max(10*median_spacing, 5 days)',
             'empirical_mean': mean_step.tolist(), 'covariance': sigma.tolist(),
             'seed': seed, 'cell_order': 'asset then horizon, sorted',
             'note': 'Published reference construction; not a learned model or claim of information uplift'}
    return samples, stats
