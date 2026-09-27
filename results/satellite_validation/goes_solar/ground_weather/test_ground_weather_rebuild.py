"""Semantic checks for offline weather extraction, with no generation outcomes."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

spec = importlib.util.spec_from_file_location("weather_rebuild", Path(__file__).with_name("rebuild.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def dly_line(element, fields):
    return "USW00093206" + "201501" + element + "".join(
        f"{value:5d}{measurement}{quality}{source}"
        for value, measurement, quality, source in fields
    )


def test_missing_and_rejected_values_remain_missing(tmp_path):
    fields = [(100, " ", " ", "W")] * 31
    fields[0] = (-9999, " ", " ", "W")
    fields[1] = (777, " ", "X", "W")
    fields[2] = (0, "T", " ", "W")
    path = tmp_path / "raw.dly"
    path.write_text(dly_line("PRCP", fields) + "\n")
    result = mod.parse_daily(path)
    assert np.isnan(result.iloc[0].value)
    assert np.isnan(result.iloc[1].value)
    assert result.iloc[2].value == 0
    assert result.iloc[2].measurement_flag == "T"
    assert result.iloc[3].value == 10


def make_daily():
    return pd.DataFrame([
        {"date": date, "station_id": "temperature" if element != "PRCP" else "rain",
         "element": element, "value": value}
        for date in pd.date_range("2015-01-01", "2015-01-31")
        for element, value in [("TMAX", 20.), ("TMIN", 10.), ("PRCP", 1.)]
    ])


def test_monthly_units_pairing_coverage_and_availability():
    result = mod.monthly(make_daily()).iloc[0]
    assert result.tmean_c == 15
    assert result.dtr_c == 10
    assert result.prcp_mm == 31
    assert result.prcp_station_id == "rain"
    assert result.tmax_station_id == "temperature"
    assert result.assumed_available_date == pd.Timestamp("2015-02-14")
    assert result.coverage == 1
    assert result.complete


def test_sparse_precipitation_is_not_zero_filled():
    daily = make_daily()
    daily.loc[daily.element.eq("PRCP") & daily.date.le("2015-01-05"), "value"] = np.nan
    result = mod.monthly(daily).iloc[0]
    assert np.isnan(result.prcp_mm)
    assert not result.complete
    assert result.prcp_days == 26


def test_separate_temperature_completeness_does_not_imply_paired_coverage():
    daily = make_daily()
    daily.loc[daily.element.eq("TMAX") & daily.date.le("2015-01-03"), "value"] = np.nan
    daily.loc[daily.element.eq("TMIN") & daily.date.ge("2015-01-29"), "value"] = np.nan
    result = mod.monthly(daily).iloc[0]
    assert result.tmax_days == result.tmin_days == 28
    assert np.isnan(result.tmean_c)
    assert not result.complete


def test_duplicate_station_element_rejected():
    daily = make_daily()
    daily.loc[0, "station_id"] = "unselected station"
    with pytest.raises(ValueError, match="exactly one selected station"):
        mod.monthly(daily)
