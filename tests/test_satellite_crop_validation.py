import unittest

import numpy as np
import pandas as pd

from src.satellite_crop_validation import (
    feature_columns, point_in_time_trends, predict, rebuild_optical, weekly_composite,
)


class SatelliteCropValidationTests(unittest.TestCase):
    def test_weekly_measurement_not_available_before_end_plus_lag(self):
        date_id, end = weekly_composite(pd.Timestamp("2024-07-31"))
        self.assertEqual(date_id, "weekly_ndvi_31_2024.07.29_2024.08.04")
        self.assertEqual(end, pd.Timestamp("2024-08-04"))

    def test_cumulative_optical_has_no_future_stage_and_preserves_missing(self):
        config = {"states": {"IA": {"ndvi_county_fips": "19169"}},
                  "crops": {"corn": {"stages": {"first": ["05-01", "06-14"],
                                                "second": ["06-15", "07-31"]}}}}
        raw = pd.DataFrame([dict(crop="corn", state="IA", year=2024, stage_number=s,
                                 forecast_date="2024-06-14" if s == 1 else "2024-07-31")
                            for s in (1, 2)])
        first, _ = weekly_composite(pd.Timestamp("2024-06-14"))
        obs = pd.DataFrame([dict(key="19169:" + first, mean_ndvi=0.7)])
        out = rebuild_optical(raw, obs, config)
        self.assertEqual(out.s1_ndvi.tolist(), [0.7, 0.7])
        self.assertTrue(out.s2_ndvi.isna().all())
        self.assertEqual(out.iloc[1].available_date, pd.Timestamp("2024-08-18"))
        self.assertNotIn("s2_ndvi", feature_columns(out, "weather_plus_ndvi", 1))

    def test_future_yields_do_not_change_earlier_trend(self):
        raw = pd.DataFrame([dict(crop="corn", state="IA", year=y, **{"yield": 100 + 2 * (y - 2001)})
                            for y in range(2001, 2021)])
        base = point_in_time_trends(raw)
        changed = raw.copy()
        changed.loc[changed.year >= 2018, "yield"] = 10000
        other = point_in_time_trends(changed)
        pd.testing.assert_series_equal(base[base.year <= 2018].trend_yield,
                                       other[other.year <= 2018].trend_yield)

    def test_serialized_stage_key_order_does_not_change_acquisition_dates(self):
        config = {"states": {"KS": {"ndvi_county_fips": "20191"}},
                  "crops": {"wheat": {"stages": {
                      "grain_fill": ["06-16", "07-31"],
                      "heading": ["05-01", "06-15"],
                      "vegetative": ["03-01", "04-30"]}}}}
        raw = pd.DataFrame([dict(crop="wheat", state="KS", year=2025,
                                 stage_number=2, forecast_date="2025-06-15")])
        first, _ = weekly_composite(pd.Timestamp("2025-04-30"))
        second, _ = weekly_composite(pd.Timestamp("2025-06-15"))
        obs = pd.DataFrame([dict(key="20191:" + first, mean_ndvi=0.6),
                            dict(key="20191:" + second, mean_ndvi=0.7)])
        out = rebuild_optical(raw, obs, config)
        self.assertEqual(out.s1_ndvi.iloc[0], 0.6)
        self.assertEqual(out.s2_ndvi.iloc[0], 0.7)
        self.assertEqual(out.available_date.iloc[0], pd.Timestamp("2025-06-29"))

    def test_current_year_labels_do_not_enter_fitted_predictions(self):
        raw = pd.DataFrame([dict(crop="corn", state=s, year=y, stage="first", stage_number=1,
                                 forecast_date=pd.Timestamp(f"{y}-07-31"),
                                 composite_end=pd.Timestamp(f"{y}-08-04"),
                                 available_date=pd.Timestamp(f"{y}-08-18"),
                                 production_weight=0.5, s1_precip=y % 4,
                                 s1_ndvi=0.6 + 0.01 * (y % 3),
                                 s1_prior_year_ndvi=0.6 + 0.01 * ((y - 1) % 3),
                                 **{"yield": 100 + 2 * (y - 2001) + (y % 4)})
                            for s in ["IA", "IL"] for y in range(2001, 2019)])
        tiny_model = dict(n_estimators=5, random_state=17, max_depth=2)
        base = predict(point_in_time_trends(raw), 2018, 2018, tiny_model)
        raw.loc[raw.year.eq(2018), "yield"] = 9999
        changed = predict(point_in_time_trends(raw), 2018, 2018, tiny_model)
        np.testing.assert_array_equal(base.prediction_anomaly, changed.prediction_anomaly)
        self.assertTrue((base.training_last_year < base.year).all())


if __name__ == "__main__":
    unittest.main()
