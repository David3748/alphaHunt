import unittest

import numpy as np
import pandas as pd

from src.nasa_crop_weather_backtest import (
    expanding_seasonal_z,
    normalize_gross,
    rv_signal,
    strategy_returns,
)


class NasaCropWeatherBacktestTests(unittest.TestCase):
    def test_expanding_seasonal_z_uses_prior_years_only(self):
        idx = pd.DatetimeIndex([pd.Timestamp.fromisocalendar(year, 1, 5)
                                for year in range(2001, 2008)])
        values = pd.Series([1, 2, 3, 4, 5, 100, -100], index=idx, dtype=float)
        z = expanding_seasonal_z(values, min_years=5)
        self.assertTrue(z.iloc[:5].isna().all())
        expected = (100 - 3) / np.std([1, 2, 3, 4, 5], ddof=1)
        self.assertAlmostEqual(z.iloc[5], min(expected, 4.0))
        # Changing the seventh observation cannot alter the sixth signal.
        changed = values.copy()
        changed.iloc[6] = 10_000
        self.assertEqual(z.iloc[5], expanding_seasonal_z(changed, min_years=5).iloc[5])

    def test_relative_value_is_market_neutral_and_gross_one(self):
        idx = pd.date_range("2020-01-03", periods=12, freq="W-FRI")
        prices = pd.DataFrame({
            "corn": np.linspace(100, 150, 12),
            "soy": np.linspace(100, 105, 12),
            "wheat": np.linspace(100, 80, 12),
        }, index=idx)
        signal = rv_signal(prices, lookback=8, minimum=4)
        live = signal[signal.abs().sum(axis=1) > 0]
        np.testing.assert_allclose(live.sum(axis=1), 0, atol=1e-12)
        np.testing.assert_allclose(live.abs().sum(axis=1), 1, atol=1e-12)

    def test_cost_is_charged_on_position_change(self):
        idx = pd.date_range("2020-01-03", periods=2, freq="W-FRI")
        returns = pd.DataFrame({"corn": [0.01, 0.01]}, index=idx)
        positions = pd.DataFrame({"corn": [1.0, -1.0]}, index=idx)
        net, turnover = strategy_returns(returns, positions, cost_bps=10)
        self.assertAlmostEqual(turnover.iloc[0], 1.0)
        self.assertAlmostEqual(turnover.iloc[1], 2.0)
        self.assertAlmostEqual(net.iloc[0], 0.009)
        self.assertAlmostEqual(net.iloc[1], -0.012)

    def test_normalize_gross_leaves_zero_rows_zero(self):
        frame = pd.DataFrame([[0.0, 0.0], [2.0, -1.0]])
        normalized = normalize_gross(frame)
        self.assertEqual(normalized.iloc[0].sum(), 0.0)
        self.assertAlmostEqual(normalized.iloc[1].abs().sum(), 1.0)


if __name__ == "__main__":
    unittest.main()
