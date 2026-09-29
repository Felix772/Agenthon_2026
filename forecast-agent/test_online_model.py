"""Synthetic checks for per-unit forecast selection and metric agreement."""

import unittest

import numpy as np
import pandas as pd
from qfbench2_track_forecasting.grid import GridSpec
from qfbench2_track_forecasting.tail import tail_pinball

from online_model import _folds, _tail_pinball, online_samples


class OnlineModelTests(unittest.TestCase):
    def test_pinball_matches_current_official_helper(self):
        rng = np.random.default_rng(29)
        samples = rng.standard_normal((500, 2, 3))
        realized = rng.standard_normal((2, 3))
        expected = tail_pinball(samples.reshape(500, -1), realized.reshape(-1),
                                (.01, .05, .95, .99))
        self.assertAlmostEqual(_tail_pinball(samples, realized), expected, places=14)

    def test_validation_windows_end_before_task_cutoff(self):
        dates = pd.bdate_range('2019-01-01', periods=800)
        history = {'A': pd.Series(100. + np.arange(800)*.01, index=dates)}
        folds = _folds(history, GridSpec(('A',), (21, 63)), (21, 63))
        self.assertEqual(len(folds), 3)
        for past, future in folds:
            self.assertLess(past.index.max(), future.index.min())
            self.assertLessEqual(future.index.max(), dates[-1])
            self.assertEqual(len(future), 63)

    def test_short_history_falls_back_to_incumbent(self):
        dates = pd.bdate_range('2020-01-01', periods=320)
        values = 100. + np.cumsum(np.random.default_rng(0).normal(0, .1, len(dates)))
        histories = {'A': pd.Series(values, index=dates)}
        grid = GridSpec(('A',), (21,))
        samples, stats = online_samples(histories, grid, np.array([[21]]),
                                        target='level', seed=7, unit_id='synthetic-short')
        self.assertEqual(samples.shape, (500, 1, 1))
        self.assertEqual(stats['selected_arm'], 'incumbent')
        self.assertEqual(stats['validation_windows'], 0)


if __name__ == '__main__':
    unittest.main()
