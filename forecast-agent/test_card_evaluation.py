"""Card-shape scoring and cutoff-bounded monthly trend regression checks."""

import numpy as np
import pandas as pd
import pytest
import json
from pathlib import Path
from qfbench2_track_forecasting.grid import GridSpec

from card_evaluation import effective_weights, git_bytes, monthly_pseudo_folds, score_case
from forecast import main as forecast_main
from joint_model import joint_samples
from test_forecast import fixture


def monthly_history():
    dates = pd.date_range('2000-01-01', periods=240, freq='MS')
    changes = np.r_[0.0, np.linspace(0.2, 0.8, len(dates) - 1)]
    return pd.Series(100 + np.cumsum(changes), index=dates)


def test_monthly_damped_trend_preserves_paired_shocks_and_authored_horizons():
    series = monthly_history()
    grid = GridSpec(('CPI_ALL',), (21, 140, 160))
    steps = np.array([[2, 8, 9]])
    common = dict(target='level', monthly=True, draws=200, seed=23)
    baseline, baseline_fit = joint_samples({'CPI_ALL': series}, grid, steps, **common)
    default, default_fit = joint_samples({'CPI_ALL': series}, grid, steps,
                               monthly_trend=False, **common)
    candidate, fit = joint_samples({'CPI_ALL': series}, grid, steps,
                                   monthly_trend=True, **common)
    np.testing.assert_array_equal(default, baseline)
    assert default_fit == baseline_fit and 'monthly_trend' not in baseline_fit
    initial = 0.5 * np.median(np.diff(series.to_numpy())[-12:])
    expected = np.array([initial * sum(0.8 ** i for i in range(step))
                         for step in steps[0]])
    np.testing.assert_allclose(candidate - baseline,
                               np.broadcast_to(expected[None, None, :], candidate.shape),
                               atol=1e-10)
    assert fit['monthly_trend']['initial_step'] == pytest.approx([initial])
    assert fit['monthly_trend']['damping'] == 0.8


def test_monthly_trend_cannot_change_daily_or_return_forecasts():
    series = monthly_history()
    grid = GridSpec(('A',), (21,))
    for monthly, target, drift in [(False, 'level', False), (True, 'log_return', False),
                                    (True, 'level', True)]:
        with pytest.raises(ValueError, match='Monthly trend requires'):
            joint_samples({'A': series}, grid, np.array([[2]]), target=target,
                          monthly=monthly, draws=200, seed=1, drift=drift,
                          monthly_trend=True)


def test_public_single_cell_weights_and_same_m0_proxy_is_one():
    card_doc = {'scoring': {'params': {'joint': 'variogram',
        'weights': {'marginal': .5, 'joint': .3, 'tail': .2}}},
        'targets': {'asset_ids': ['A'], 'horizons': [21]}}
    weights = effective_weights(card_doc)
    np.testing.assert_allclose(weights, [5/7, 0, 2/7])
    card = {'cell_count': 1, 'joint': 'variogram', 'tail_metric': 'pinball',
            'tail_levels': [.01, .05, .95, .99], 'weights_effective': weights}
    m0 = np.random.default_rng(4).normal(size=(500, 1))
    result = score_case(m0, np.array([0.2]), m0, card)
    assert result['local_normalized_proxy'] == pytest.approx(1.0)
    card_doc['targets']['horizons'] = [21, 63]
    assert effective_weights(card_doc) == (.5, .3, .2)


def test_pseudo_fold_retains_horizon_steps_and_ignores_future_values_when_fitting():
    series = monthly_history()
    fold, history, truth = next(monthly_pseudo_folds(series, (2, 8, 9), [2014]))
    assert fold['cutoff'] == '2014-12-31'
    assert fold['history_end'] == '2014-10'
    assert fold['target_periods'] == ['2014-12', '2015-06', '2015-07']
    assert fold['status'] == 'exploratory_snapshot_proxy'
    altered = series.copy()
    altered.loc['2014-11-01':] += 1000
    _, altered_history, altered_truth = next(monthly_pseudo_folds(altered, (2, 8, 9), [2014]))
    pd.testing.assert_series_equal(history, altered_history)
    assert not np.array_equal(truth, altered_truth)


def test_unresolved_pseudo_fold_stays_in_fixed_case_inventory():
    series = monthly_history().loc[:'2014-11-01']
    fold, history, truth = next(monthly_pseudo_folds(series, (2, 8), [2014]))
    assert fold['status'] == 'unresolved'
    assert fold['reason'] == 'target observation absent from frozen panel'
    assert history is None and truth is None


def test_cli_opt_in_on_current_public_monthly_card(tmp_path):
    repo = Path(__file__).resolve().parents[1] / 'track2-forecasting-public'
    commit = '28a6cae9674f69e63a07a19165a9217e85eacfff'
    card_id = 't2-F1-cpi-glidepath-2023'
    unit = tmp_path / card_id
    (unit / 'panels').mkdir(parents=True)
    (unit / 'text').mkdir()
    for name, target in [('card.toml', unit / 'card.toml'),
                         ('forecast_spec.json', unit / 'forecast_spec.json'),
                         ('macro_monthly.parquet', unit / 'panels/macro_monthly.parquet')]:
        target.write_bytes(git_bytes(repo, commit, f'units/{card_id}/{name}'))
    def invoke(name, extra=()):
        out = tmp_path / name
        assert forecast_main(['forecast', '--panels', str(unit / 'panels'),
            '--text', str(unit / 'text'), '--asof', '2023-07-12',
            '--out', str(out / 'forecast.parquet'), '--n-draws', '200',
            '--seed', '17', *extra]) == 0
        frame = pd.read_parquet(out / 'forecast.parquet')
        fit = json.loads((out / 'forecast_rationale.md').read_text().split('\n\n', 1)[1])
        return frame, fit
    incumbent, old_rationale = invoke('incumbent')
    candidate, new_rationale = invoke('candidate', ['--monthly-trend'])
    assert sorted(candidate.horizon.unique().tolist()) == [140, 160]
    assert candidate.shape == incumbent.shape == (400, 4)
    assert old_rationale['sampling_steps'] == new_rationale['sampling_steps'] == [[8, 9]]
    assert 'monthly_trend' not in old_rationale['fit']
    trend = new_rationale['fit']['monthly_trend']['initial_step'][0]
    assert trend > 0
    paired = incumbent.merge(candidate, on=['draw', 'asset', 'horizon'], suffixes=('_old', '_new'))
    assert len(paired) == len(candidate)
    for horizon, step in [(140, 8), (160, 9)]:
        subset = paired.loc[paired.horizon == horizon]
        expected = trend * sum(0.8 ** i for i in range(step))
        np.testing.assert_allclose(subset.value_new - subset.value_old,
                                   np.full(len(subset), expected), atol=1e-10)


def test_cli_monthly_trend_rejects_unsupported_modes(tmp_path):
    daily, daily_asof = fixture(tmp_path / 'daily')
    with pytest.raises(ValueError, match='Monthly trend requires'):
        forecast_main(['forecast', '--panels', str(daily / 'panels'),
            '--text', str(daily / 'text'), '--asof', daily_asof,
            '--out', str(tmp_path / 'daily-out/forecast.parquet'), '--monthly-trend'])
    monthly, monthly_asof = fixture(tmp_path / 'monthly', monthly=True)
    with pytest.raises(ValueError, match='Monthly trend requires'):
        forecast_main(['forecast', '--panels', str(monthly / 'panels'),
            '--text', str(monthly / 'text'), '--asof', monthly_asof,
            '--out', str(tmp_path / 'monthly-out/forecast.parquet'),
            '--monthly-trend', '--drift'])
