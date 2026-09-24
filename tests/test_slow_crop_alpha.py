import unittest

import numpy as np
import pandas as pd

from src.slow_crop_alpha_backtest import (
    monthly_positions,
    blend,
    slow_weather_signal,
    weather_conditioned_rv,
    yield_outlook_signal,
)


class SlowCropAlphaTests(unittest.TestCase):
    def test_fixed_weight_blend_does_not_relever_disagreement(self):
        idx = pd.DatetimeIndex(["2020-01-03"])
        first = pd.DataFrame({"corn": [0.5], "soy": [-0.5], "wheat": [0.0]}, index=idx)
        second = pd.DataFrame({"corn": [-0.4], "soy": [0.5], "wheat": [-0.1]}, index=idx)
        mixed = blend((0.5, first), (0.5, second))
        self.assertAlmostEqual(mixed.abs().sum(axis=1).iloc[0], 0.1)

    def test_monthly_signal_executes_next_week_and_only_changes_monthly(self):
        idx = pd.date_range("2020-01-03", periods=14, freq="W-FRI")
        signal = pd.DataFrame({"corn": np.arange(len(idx), dtype=float)}, index=idx)
        pos = monthly_positions(signal, idx)
        january_end = idx[idx.to_period("M") == pd.Period("2020-01")][-1]
        next_week = idx[idx.get_loc(january_end) + 1]
        self.assertEqual(pos.loc[next_week, "corn"], signal.loc[january_end, "corn"])
        self.assertLessEqual(pos.diff().abs().gt(0).sum().iloc[0], 3)

    def test_weather_conditioning_haircuts_conflicting_leg(self):
        idx = pd.DatetimeIndex(["2020-01-03"])
        rv = pd.DataFrame({"corn": [-0.5], "soy": [0.4], "wheat": [0.1]}, index=idx)
        stress = pd.DataFrame({"corn": [2.0], "soy": [0.0], "wheat": [0.0]}, index=idx)
        cfg = {"stress_scale": 1.5, "weather_conflict_haircut": 0.75}
        adjusted = weather_conditioned_rv(rv, stress, cfg)
        self.assertGreater(adjusted.loc[idx[0], "corn"], rv.loc[idx[0], "corn"])
        self.assertAlmostEqual(adjusted.sum(axis=1).iloc[0], 0.0)
        self.assertAlmostEqual(adjusted.abs().sum(axis=1).iloc[0], 1.0)

    def test_yield_vintage_expires(self):
        idx = pd.date_range("2020-01-03", periods=12, freq="W-FRI")
        vintages = pd.DataFrame({
            "crop": ["corn", "soy", "wheat"],
            "forecast_date": pd.to_datetime(["2020-01-03"] * 3),
            "predicted_yield_anomaly": [-0.1, 0.0, 0.1],
        })
        signal = yield_outlook_signal(vintages, idx, max_age_weeks=4)
        self.assertGreater(signal.iloc[0].abs().sum(), 0)
        self.assertEqual(signal.iloc[-1].abs().sum(), 0)

    def test_weather_uses_four_week_information_lag(self):
        idx = pd.date_range("2020-01-03", periods=20, freq="W-FRI")
        columns = pd.MultiIndex.from_product([["corn", "soy", "wheat"], ["stress"]])
        values = np.tile(np.arange(20, dtype=float)[:, None], (1, 3))
        features = pd.DataFrame(values, index=idx, columns=columns)
        cfg = {"weather_level_weeks": 8, "weather_trajectory_weeks": 4, "information_lag_weeks": 4}
        original = slow_weather_signal(features, cfg)[0]
        changed = features.copy()
        changed.iloc[-4:, :] = 10_000
        revised = slow_weather_signal(changed, cfg)[0]
        pd.testing.assert_series_equal(original.iloc[-1], revised.iloc[-1])


if __name__ == "__main__":
    unittest.main()
