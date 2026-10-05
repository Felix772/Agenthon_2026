"""Check T2-D1 metrics, chronological exclusion and paired covariance changes."""

import unittest

import numpy as np
import pandas as pd
from qfbench2_track_forecasting.grid import GridSpec

from t2_d1_daily_20261001 import (
    ARMS, TAILS, adjust_volatility, chronological_folds, components,
    metric_slices, proxy, tail_pinball, vol_samples,
)


class DailyExperimentTests(unittest.TestCase):
    def histories(self, target="level", n=2200):
        rng = np.random.default_rng(101)
        index = pd.bdate_range("2000-01-03", periods=n)
        increments = rng.normal(0, .01, (n, 2))
        increments[-80:] *= 1.4
        values = increments if target == "log_return" else 1 + increments.cumsum(axis=0)
        return {a: pd.Series(values[:, i], index=index) for i, a in enumerate(("A", "B"))}

    def test_components_match_analytic_crps_and_variogram(self):
        samples = np.array([-1., 0., 1.])[:, None, None]
        result = components(samples, np.array([[2.]]))
        self.assertAlmostEqual(result["marginal"], 4 / 3, places=14)
        self.assertEqual(result["joint"], 0.)
        joint = components(np.zeros((500, 2, 1)), np.array([[0.], [4.]]))
        self.assertEqual(joint["joint"], 8.)

    def test_tail_components_average_to_official_pinball(self):
        rng = np.random.default_rng(3)
        for cells in (1, 6):
            samples = rng.normal(size=(500, cells, 1))
            y = rng.normal(size=(cells, 1))
            parts = components(samples, y)
            expected = tail_pinball(samples.reshape(500, -1), y.reshape(-1), TAILS)
            self.assertAlmostEqual(parts["tail"], expected, places=14)
            self.assertAlmostEqual(np.mean(list(parts["tail_by_level"].values())), expected, places=14)

    def test_class_and_horizon_reports_use_exact_cells(self):
        grid = GridSpec(("UST_2Y", "EUR"), (21, 63))
        samples = np.random.default_rng(4).normal(size=(500, 2, 2))
        y = np.array([[2., 3.], [4., 5.]])
        slices = metric_slices(samples, y, grid, "level")
        for got, expected in ((slices["class:rates_level"]["parts"], components(samples[:, :1], y[:1])),
                              (slices["horizon:63"]["parts"], components(samples[:, :, 1:], y[:, 1:]))):
            for key in ("marginal", "joint", "tail", "composite"):
                self.assertAlmostEqual(got[key], expected[key], places=13)
            self.assertEqual(got["tail_by_level"], expected["tail_by_level"])
        self.assertEqual(slices["horizon:63"]["cells"], 2)

    def test_single_cell_weights_redistribute_and_ignore_zero_joint(self):
        self.assertAlmostEqual(proxy({"marginal": 2., "joint": 0., "tail": 1.},
                                     {"marginal": 2., "joint": 0., "tail": 1.}, 1), 1.)

    def test_folds_exclude_prior_exposed_region_and_have_embargo(self):
        hist = self.histories()
        grid = GridSpec(("A", "B"), (21, 126))
        folds, prior = chronological_folds(hist, grid)
        self.assertEqual(len(folds), 5)
        self.assertEqual([f["phase"] for f in folds], ["inner", "inner", "outer", "outer", "outer"])
        for left, right in zip(folds, folds[1:]):
            self.assertLess(pd.Timestamp(left["target_end"]) + pd.offsets.BDay(5), pd.Timestamp(right["cutoff"]))
        self.assertLess(pd.Timestamp(folds[-1]["target_end"]) + pd.offsets.BDay(5), pd.Timestamp(prior[0]))
        altered = {a: s.copy() for a, s in hist.items()}
        for s in altered.values():
            s[:] = 10000.
        self.assertEqual(chronological_folds(altered, grid), (folds, prior))

    def test_future_values_do_not_change_forecasts(self):
        grid = GridSpec(("A", "B"), (21, 63))
        for target in ("level", "log_return"):
            full = self.histories(target)
            changed = {a: s.copy() for a, s in full.items()}
            for s in changed.values():
                s.iloc[1800:] = 1e5
            for arm in ARMS:
                a, fa = vol_samples({k: s.iloc[:1800] for k, s in full.items()}, grid,
                                   np.tile([21, 63], (2, 1)), arm=arm, target=target, seed=19)
                b, fb = vol_samples({k: s.iloc[:1800] for k, s in changed.items()}, grid,
                                   np.tile([21, 63], (2, 1)), arm=arm, target=target, seed=19)
                np.testing.assert_array_equal(a, b)
                self.assertEqual(fa, fb)

    def test_candidates_preserve_correlation_and_incumbent_identity(self):
        hist = self.histories()
        grid = GridSpec(("A", "B"), (21, 63))
        steps = np.tile([21, 63], (2, 1))
        base, fit = vol_samples(hist, grid, steps, arm="incumbent", target="level", seed=11)
        identity, _ = adjust_volatility(base, fit, hist, grid, steps, "incumbent", "level")
        self.assertIs(base, identity)
        for arm in ARMS[1:]:
            candidate, stats = adjust_volatility(base, fit, hist, grid, steps, arm, "level")
            np.testing.assert_allclose(np.corrcoef(base.reshape(500, -1).T),
                                       np.corrcoef(candidate.reshape(500, -1).T), atol=1e-13)
            self.assertTrue(all(.75 <= x <= 1.25 for x in stats["marginal_scales"]))
            self.assertFalse(np.array_equal(candidate, base))


if __name__ == "__main__":
    unittest.main(verbosity=2)
