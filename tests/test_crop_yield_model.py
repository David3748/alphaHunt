import unittest

import numpy as np
import pandas as pd

from src.crop_yield_model import add_point_in_time_trend, saturation_vapor_pressure, vegscape_date_id
from src.nass_yield_ingest import keep


class CropYieldModelTests(unittest.TestCase):
    def test_trend_uses_only_prior_yields(self):
        panel = pd.DataFrame({
            "crop": ["corn"] * 7,
            "state": ["IA"] * 7,
            "year": list(range(2001, 2008)),
            "yield": [100, 102, 104, 106, 108, 110, 999],
        })
        result = add_point_in_time_trend(panel, minimum=5)
        self.assertAlmostEqual(result.loc[result.year == 2006, "trend_yield"].iloc[0], 110)
        changed = panel.copy()
        changed.loc[changed.year == 2007, "yield"] = -999
        result_changed = add_point_in_time_trend(changed, minimum=5)
        self.assertEqual(
            result.loc[result.year == 2006, "trend_yield"].iloc[0],
            result_changed.loc[result_changed.year == 2006, "trend_yield"].iloc[0])

    def test_vegscape_week_is_monday_through_sunday(self):
        self.assertEqual(
            vegscape_date_id(pd.Timestamp("2012-07-15")),
            "weekly_ndvi_28_2012.07.09_2012.07.15")

    def test_vapor_pressure_is_monotonic(self):
        values = saturation_vapor_pressure(pd.Series([0.0, 10.0, 20.0]))
        self.assertTrue(np.all(np.diff(values) > 0))

    def test_nass_filter_keeps_only_state_annual_yield(self):
        row = {
            "SOURCE_DESC": "SURVEY", "COMMODITY_DESC": "CORN",
            "STATISTICCAT_DESC": "YIELD", "UNIT_DESC": "BU / ACRE",
            "DOMAIN_DESC": "TOTAL", "AGG_LEVEL_DESC": "STATE",
            "FREQ_DESC": "ANNUAL", "YEAR": "2020",
        }
        self.assertTrue(keep(row))
        self.assertFalse(keep({**row, "AGG_LEVEL_DESC": "COUNTY"}))


if __name__ == "__main__":
    unittest.main()
