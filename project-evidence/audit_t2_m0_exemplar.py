"""Independently reproduce the public M0 worked example's daily draws."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sys
import tomllib
import zlib

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'forecast-agent'))
from forecast import main as forecast_main  # noqa: E402

UNIT = ROOT/'.validation/t2-current-20260928/units/t2-EXAMPLE-ust-curve-1m'
OUT = ROOT/'project-evidence/t2-m0-exemplar-20260928'


def guide_draws(card, asof):
    """Calculate the published M0 recipe directly from the first matching panel."""
    assets = sorted(card['targets']['asset_ids'])
    if card['targets']['target_type'] != 'level' or card['targets']['horizons'] != [21]:
        raise ValueError('Worked example changed')
    steps = {}
    anchors = {}
    for asset in assets:
        for path in sorted(UNIT.glob('*.parquet')):
            frame = pd.read_parquet(path)
            key = 'asset' if 'asset' in frame else 'asset_id'
            matched = frame[frame[key] == asset]
            if not matched.empty:
                break
        else:
            raise ValueError(f'Example panel lacks {asset}')
        matched = matched.loc[pd.to_datetime(matched.date) <= pd.Timestamp(asof)].copy()
        matched['date'] = pd.to_datetime(matched.date)
        matched = matched.sort_values('date').tail(300)
        if len(matched) != 129:
            raise ValueError('Expected 129 dated rows per example asset')
        values = matched.value.to_numpy(dtype=float)
        dates = pd.DatetimeIndex(matched.date)
        gaps = np.diff(dates.values).astype('timedelta64[D]').astype(int)
        max_gap = max(10*float(np.median(gaps)), 5.)
        steps[asset] = pd.Series(np.diff(values), index=dates[1:]).where(gaps <= max_gap)
        anchors[asset] = values[-1]
    aligned = pd.DataFrame(steps).dropna()
    mu = aligned.mean().to_numpy(dtype=float)
    sigma = np.atleast_2d(np.cov(aligned.to_numpy(dtype=float), rowvar=False))
    cells = [(asset, 21) for asset in assets]
    center = np.array([anchors[asset] + 21*mu[assets.index(asset)] for asset, _ in cells])
    covariance = 21*sigma
    covariance[np.diag_indices_from(covariance)] += 1e-10
    root = np.linalg.cholesky(covariance + 1e-9*np.eye(len(cells)))
    seed = zlib.crc32(card['task']['id'].encode()) & 0x7FFFFFFF
    draws = center + np.random.default_rng(seed).standard_normal((500, len(cells))) @ root.T
    return draws, seed, len(aligned), cells


def main():
    if OUT.exists():
        raise ValueError('Expected a new exemplar audit directory')
    card = tomllib.loads((UNIT/'card.toml').read_text())
    asof = str(card['provenance']['data_cutoff'])
    staged = OUT/'input'
    (staged/'panels').mkdir(parents=True)
    for path in UNIT.glob('*.parquet'):
        shutil.copy2(path, staged/'panels'/path.name)
    shutil.copy2(UNIT/'card.toml', staged/'card.toml')
    shutil.copytree(UNIT/'text', staged/'text')
    output_paths = []
    for supplied_seed in (42, 987654):
        output = OUT/str(supplied_seed)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            forecast_main(['forecast', '--panels', str(staged/'panels'),
                           '--text', str(staged/'text'), '--asof', asof,
                           '--out', str(output/'forecast.parquet'),
                           '--seed', str(supplied_seed), '--method', 'm0-control'])
        output_paths.append(output)
    expected, seed, fit_rows, cells = guide_draws(card, asof)
    if seed != 795546941 or fit_rows != 128:
        raise ValueError('Published exemplar seed or fit count differs')
    observed = []
    for output in output_paths:
        frame = pd.read_parquet(output/'forecast.parquet')
        frame = frame.set_index(['draw', 'asset', 'horizon'])
        index = pd.MultiIndex.from_product([range(500), [a for a, _ in cells], [21]],
                                           names=['draw', 'asset', 'horizon'])
        observed.append(frame.reindex(index).value.to_numpy(dtype=float).reshape(500, len(cells)))
        rationale = json.loads((output/'forecast_rationale.md').read_text().split('\n\n', 1)[1])
        if rationale['seed'] != seed or rationale['fit']['seed'] != seed:
            raise ValueError('Control did not use public M0 unit seed')
    max_error = float(np.max(np.abs(observed[0]-expected)))
    supplied_seed_difference = float(np.max(np.abs(observed[0]-observed[1])))
    if max_error > 1e-11 or supplied_seed_difference != 0:
        raise ValueError('M0 worked-exemplar draw parity failed')
    result = {'public_example': card['task']['id'], 'seed': seed,
              'fit_rows': fit_rows, 'cells': cells,
              'max_abs_draw_error_vs_independent_guide': max_error,
              'max_abs_draw_difference_across_injected_seeds': supplied_seed_difference}
    (OUT/'report.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
