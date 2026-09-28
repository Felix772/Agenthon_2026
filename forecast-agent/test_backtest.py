"""Leakage guards for frozen retrospective evaluation."""
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pandas as pd
from qfbench2_track_forecasting.grid import GridSpec
from backtest import load_panel, target_values, training_only, validate_folds
from joint_model import joint_samples


class BacktestTests(unittest.TestCase):
    def panel(self):
        dates = pd.bdate_range('2020-01-01', periods=400)
        return pd.DataFrame({'A': np.arange(400.0), 'B': np.arange(400.0) * 2}, index=dates)

    def test_future_perturbation_cannot_change_fit_or_predictions(self):
        wide = self.panel()
        cutoff = wide.index[300]
        changed = wide.copy()
        changed.loc[changed.index > cutoff] = 1e12
        outputs = []
        for data in (wide, changed):
            histories = training_only(data, cutoff, ['A', 'B'])
            self.assertTrue(all(series.index.max() <= cutoff for series in histories.values()))
            outputs.append(joint_samples(histories, GridSpec(('A', 'B'), (5,)), np.array([[5], [5]]),
                target='level', monthly=False, draws=500, seed=1))
        np.testing.assert_array_equal(outputs[0][0], outputs[1][0])
        self.assertEqual(outputs[0][1], outputs[1][1])

    def test_overlap_shuffled_and_training_target_overlap_rejected(self):
        a = {'cutoff': '2020-01-01', 'target_dates': ['2020-01-08', '2020-01-30']}
        b = {'cutoff': '2021-01-01', 'target_dates': ['2021-01-08', '2021-02-01']}
        validate_folds([a, b])
        for folds in ([b, a], [a, {'cutoff': '2020-01-15', 'target_dates': ['2020-02-01']}],
                      [{'cutoff': '2020-01-01', 'target_dates': ['2019-12-31']}]):
            with self.assertRaises(ValueError): validate_folds(folds)

    def test_insufficient_history_and_missing_targets(self):
        wide = self.panel()
        with self.assertRaises(ValueError): training_only(wide, wide.index[10], ['A', 'B'])
        with self.assertRaises(ValueError): target_values(wide, ['A', 'B'], ['2030-01-01'])
        wide.iloc[-1, 0] = np.nan
        with self.assertRaises(ValueError): target_values(wide, ['A', 'B'], [wide.index[-1]])

    def test_duplicate_and_nonfinite_source_rows(self):
        frame = self.panel().rename_axis('date').reset_index().melt('date', var_name='asset', value_name='value')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'panel.parquet'
            for corrupt in (pd.concat([frame, frame.iloc[[0]]]), frame.assign(value=np.inf)):
                corrupt.to_parquet(path, index=False)
                with self.assertRaises(ValueError): load_panel(path)


if __name__ == '__main__':
    unittest.main()
