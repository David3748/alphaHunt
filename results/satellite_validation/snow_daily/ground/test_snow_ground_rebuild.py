import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd

spec = importlib.util.spec_from_file_location("snow_ground", Path(__file__).with_name("rebuild.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fixtures():
    months = pd.date_range("1999-10-01", "2000-04-01", freq="MS")
    regional = pd.DataFrame({"value_mm": [1., 2., 3., 4., 5., 6., 999999.], "flag": ""}, index=months)
    days = pd.date_range("1999-10-01", "2000-03-31")
    daily = pd.DataFrame({"value_mm": 1., "flag": ""}, index=days)
    snow = pd.DataFrame({"value_mm": [10., 20., np.nan, 99999.], "flag": "",
                         "observed_at_local": pd.to_datetime(["2000-03-29 04:00", "2000-03-30 04:00", "2000-03-31 04:00", "2000-04-01 04:00"])},
                        index=pd.to_datetime(["2000-03-29", "2000-03-30", "2000-03-31", "2000-04-01"]))
    return regional, daily, snow


def test_winter_crosses_year_and_excludes_future_april():
    row = mod.extract(*fixtures()).iloc[0]
    assert row.year == 2000
    assert row.winter_precip_mm == 21.
    assert row.winter_precip_oct_feb_mm == 15.
    assert row.local_hnt_calendar_days == 183
    assert row.ground_swe_mm == 20.
    assert row.ground_swe_date == pd.Timestamp("2000-03-30")


def test_missing_regional_month_and_sparse_local_rain_remain_missing():
    regional, daily, snow = fixtures()
    regional.loc["2000-03-01", "value_mm"] = np.nan
    daily.iloc[:30, daily.columns.get_loc("value_mm")] = np.nan
    row = mod.extract(regional, daily, snow).iloc[0]
    assert np.isnan(row.winter_precip_mm)
    assert np.isnan(row.local_hnt_precip_mm)


def test_cdec_units_negative_swe_and_missing_sentinel(tmp_path):
    path = tmp_path / "snow.csv"
    path.write_text("STATION_ID,DURATION,SENSOR_NUMBER,SENSOR_TYPE,DATE TIME,OBS DATE,VALUE,DATA_FLAG,UNITS\n"
                    "HNT,D,3,SNOW WC,20000329 0000,20000329 0500,2.0, ,INCHES\n"
                    "HNT,D,3,SNOW WC,20000330 0000,20000330 0500,-1.0, ,INCHES\n"
                    "HNT,D,3,SNOW WC,20000331 0000,20000331 0500,---, ,INCHES\n")
    result = mod.read_cdec(path, "HNT", 3, "D")
    assert result.iloc[0].value_mm == 50.8
    assert result.iloc[1:].value_mm.isna().all()


def test_revised_regional_readings_are_counted_not_hidden():
    regional, daily, snow = fixtures()
    regional.loc["2000-03-01", "flag"] = "r"
    row = mod.extract(regional, daily, snow).iloc[0]
    assert row.precipitation_revised_months == 1
    assert row.winter_precip_mm == 21.
    assert not row.original_release_verified
