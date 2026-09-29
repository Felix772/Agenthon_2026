"""Synthetic checks for the daily reference forecast path."""

import unittest

import numpy as np
import pandas as pd

from qfbench2_track_forecasting.grid import GridSpec
from reference_model import reference_samples


class ReferenceModelTests(unittest.TestCase):
    def setUp(self):
        dates = pd.bdate_range('2020-01-01', periods=400)
        self.histories = {
            'A': pd.Series(100. + np.arange(400, dtype=float), index=dates),
            'B': pd.Series(20. + 2. * np.arange(400, dtype=float), index=dates),
        }

    def test_reference_center_uses_cutoff_history(self):
        grid = GridSpec(('A',), (21, 63))
        steps = np.array([[21, 63]])
        baseline, baseline_fit = reference_samples(self.histories, grid, steps,
            target='level', draws=500, unit_id='synthetic', seed=17)
        np.testing.assert_allclose(baseline.mean(axis=0), np.array([[520., 562.]]), atol=.01)
        self.assertEqual(baseline_fit['fit_rows'], 299)
        self.assertEqual(baseline_fit['seed'], 17)

    def test_cell_reordering_and_seed_are_well_defined(self):
        first_grid = GridSpec(('A', 'B'), (21, 63))
        first, _ = reference_samples(self.histories, first_grid, np.array([[21, 63], [21, 63]]),
                                      target='level', draws=500, unit_id='synthetic', seed=17)
        reordered, _ = reference_samples(self.histories, GridSpec(('B', 'A'), (63, 21)),
                                          np.array([[63, 21], [63, 21]]), target='level',
                                          draws=500, unit_id='synthetic', seed=17)
        np.testing.assert_array_equal(first[:, 0, 0], reordered[:, 1, 1])
        np.testing.assert_array_equal(first[:, 1, 1], reordered[:, 0, 0])
        other_seed, _ = reference_samples(self.histories, first_grid,
            np.array([[21, 63], [21, 63]]), target='level', draws=500,
            unit_id='synthetic', seed=18)
        self.assertFalse(np.array_equal(first, other_seed))

    def test_long_gap_does_not_enter_innovation_mean(self):
        dates = pd.bdate_range('2019-01-01', periods=350)
        history = pd.Series(np.arange(350, dtype=float), index=dates)
        history.loc[pd.Timestamp('2024-01-01')] = 1000.
        grid = GridSpec(('A',), (21,))
        _, fit = reference_samples({'A': history}, grid, np.array([[21]]),
                                   target='level', draws=500, unit_id='gap', seed=0)
        self.assertAlmostEqual(fit['empirical_mean'][0], 1.)
        self.assertEqual(fit['fit_rows'], 298)


if __name__ == '__main__':
    unittest.main()
