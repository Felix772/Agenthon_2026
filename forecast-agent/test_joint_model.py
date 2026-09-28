"""Statistical invariants, not exact random realizations or hidden task scores."""
import unittest
import numpy as np
import pandas as pd
from qfbench2_track_forecasting.grid import GridSpec
from joint_model import joint_samples


def history(singular=False):
    rng = np.random.default_rng(16)
    x = rng.normal(size=400)
    y = x.copy() if singular else 0.8 * x + 0.6 * rng.normal(size=400)
    dates = pd.bdate_range('2020-01-01', periods=400)
    return {a: pd.Series(100 + np.cumsum(v), index=dates) for a, v in [('A', x), ('B', y)]}


class JointTests(unittest.TestCase):
    def draw(self, histories, **kwargs):
        return joint_samples(histories, GridSpec(('A', 'B'), (1, 5)), np.array([[1, 5], [1, 5]]),
            target='level', monthly=False, draws=5000, seed=kwargs.pop('seed', 1), **kwargs)

    def test_cross_asset_and_nested_horizon_dependence(self):
        samples, stats = self.draw(history())
        self.assertGreater(np.corrcoef(samples[:, 0, 0], samples[:, 1, 0])[0, 1], 0.5)
        self.assertGreater(np.corrcoef(samples[:, 0, 0], samples[:, 0, 1])[0, 1], 0.3)
        ratio = samples[:, 0, 1].var() / samples[:, 0, 0].var()
        self.assertTrue(4 < ratio < 6)
        self.assertEqual(stats['fit_rows'], 252)

    def test_seeds_and_singular_covariance(self):
        histories = history(singular=True)
        first, _ = self.draw(histories, shrinkage=0)
        second, _ = self.draw(histories, shrinkage=0)
        third, _ = self.draw(histories, shrinkage=0, seed=2)
        np.testing.assert_array_equal(first, second)
        self.assertFalse(np.array_equal(first, third))
        self.assertTrue(np.isfinite(first).all())

    def test_bootstrap_retains_vector_dependence(self):
        samples, _ = self.draw(history(singular=True), method='bootstrap')
        a = samples[:, 0, :] - samples[:, 0, :].mean(axis=0)
        b = samples[:, 1, :] - samples[:, 1, :].mean(axis=0)
        np.testing.assert_allclose(a, b, atol=1e-10)

    def test_insufficient_history_fails(self):
        short = {a: values.iloc[:20] for a, values in history().items()}
        with self.assertRaisesRegex(ValueError, '30 aligned'):
            self.draw(short)

    def test_log_return_mean_is_sum_of_log_steps(self):
        dates = pd.bdate_range('2020-01-01', periods=50)
        histories = {'A': pd.Series(0.01, index=dates)}
        samples, _ = joint_samples(histories, GridSpec(('A',), (5,)), np.array([[5]]),
            target='log_return', monthly=False, draws=1000, seed=1)
        self.assertAlmostEqual(samples.mean(), 5 * np.log1p(0.01), places=5)

    def test_monthly_staggered_anchors_keep_common_calendar(self):
        dates = pd.date_range('2020-01-31', periods=50, freq='ME')
        levels = np.arange(50.0)
        histories = {'A': pd.Series(levels, index=dates), 'B': pd.Series(levels[:-1], index=dates[:-1])}
        samples, stats = joint_samples(histories, GridSpec(('A', 'B'), (42,)), np.array([[1], [2]]),
            target='level', monthly=True, draws=1000, seed=1, drift=True)
        np.testing.assert_allclose(samples.mean(axis=0).ravel(), [50, 50], atol=1e-5)
        self.assertEqual(stats['transformation'], 'monthly level difference')


if __name__ == '__main__':
    unittest.main()
