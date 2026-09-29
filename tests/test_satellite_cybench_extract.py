import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import satellite_cybench_extract as module


def inputs():
    locations = pd.DataFrame({"adm_id": ["US-01-001"]})
    rows = []
    for month in module.MONTHS:
        for date in module.expected_starts(2003, month):
            rows.append({"adm_id": "US-01-001", "date": date.strftime("%Y%m%d"), "ndvi": .5})
    ndvi = module.prepare_ndvi(pd.DataFrame(rows))
    weather = []
    for month in module.MONTHS:
        days = module.calendar.monthrange(2003, month)[1]
        row = {"adm_id": "US-01-001", "year": 2003, "month": month}
        for var in module.WEATHER:
            row[var + "_sum"] = days * 2.
            row[var + "_count"] = days
        weather.append(row)
    return ndvi, pd.DataFrame(weather), locations


def test_completed_composite_and_publication_buffer():
    raw = pd.DataFrame({"adm_id": ["US-01-001"] * 3, "date": ["20030724", "20030725", "20030726"], "ndvi": [.4, .5, .6]})
    result = module.prepare_ndvi(raw)
    # July25+7 inclusive full days+14 reaches Aug15 evening, after noon issue.
    assert list(result.date.dt.strftime("%Y%m%d")) == ["20030724"]
    assert result.iloc[0].window_end == pd.Timestamp("2003-07-31 23:59:59")


def test_no_future_fill_and_negative_ndvi_is_valid(monkeypatch):
    monkeypatch.setattr(module, "YEARS", [2003])
    ndvi, weather, locations = inputs()
    ndvi.loc[ndvi.month == 4, "ndvi"] = -.2
    ndvi.loc[ndvi.month == 5, "ndvi"] = np.nan
    result = module.build_features(ndvi, weather, locations).iloc[0]
    assert result.ndvi_apr == pytest.approx(-.2)
    assert result.ndvi_count_apr >= 2
    assert np.isnan(result.ndvi_may)
    assert not result.features_eligible


def test_monthly_weather_totals_require_complete_days(monkeypatch):
    monkeypatch.setattr(module, "YEARS", [2003])
    ndvi, weather, locations = inputs()
    result = module.build_features(ndvi, weather, locations).iloc[0]
    assert result.prec_apr == 60.
    assert result.tmax_apr == 2.
    assert result.features_eligible
    weather.loc[weather.month == 4, "prec_count"] = 29
    result = module.build_features(ndvi, weather, locations).iloc[0]
    assert np.isnan(result.prec_apr)
    assert result.prec_coverage_apr == pytest.approx(29 / 30)
    assert not result.weather_eligible


def test_satellite_minimum_count_and_fraction(monkeypatch):
    monkeypatch.setattr(module, "YEARS", [2003])
    ndvi, weather, locations = inputs()
    april = ndvi.index[ndvi.month == 4]
    ndvi.loc[april[1:], "ndvi"] = np.nan
    result = module.build_features(ndvi, weather, locations).iloc[0]
    assert result.ndvi_count_apr == 1
    assert not result.ndvi_eligible


def test_missing_all_inputs_retains_forecast_feature_row(monkeypatch):
    monkeypatch.setattr(module, "YEARS", [2003])
    ndvi, weather, locations = inputs()
    result = module.build_features(ndvi.iloc[:0], weather.iloc[:0], locations)
    assert len(result) == 1
    assert result.iloc[0].forecast_at == "2003-08-15T12:00:00"
    assert not result.iloc[0].features_eligible
    assert "yield" not in result


def test_duplicate_weather_across_chunks_rejected(tmp_path):
    row = {"adm_id": "US-01-001", "date": 20030401, **{v: 1. for v in module.WEATHER}}
    path = tmp_path / "raw.csv"
    pd.DataFrame([row, row]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="Duplicate"):
        module.aggregate_weather(path, {"US-01-001"}, chunksize=1)


def test_weather_infinite_values_missing_and_august_excluded(tmp_path):
    rows = [{"adm_id": "US-01-001", "date": date, **{v: 1. for v in module.WEATHER}} for date in [20030401, 20030402, 20030801]]
    rows[0]["tmax"] = np.inf
    path = tmp_path / "raw.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    result = module.aggregate_weather(path, {"US-01-001"}, chunksize=1)
    assert len(result) == 1
    assert result.iloc[0].month == 4
    assert result.iloc[0].tmax_count == 1
    assert result.iloc[0].prec_sum == 2.


def test_changed_compact_input_rejected_before_rebuild(tmp_path):
    (tmp_path / "input.csv").write_text("changed\n")
    (tmp_path / "source_manifest.json").write_text(json.dumps({"compact_input_sha256": {"input.csv": "0" * 64}}))
    with pytest.raises(ValueError, match="checksum"):
        module.rebuild(tmp_path)


def test_expected_composites_never_reach_issue():
    for year in module.YEARS:
        for month in module.MONTHS:
            dates = module.expected_starts(year, month)
            assert ((dates.dayofyear - 1) % 8 == 0).all()
            assert (dates + pd.Timedelta(days=22) - pd.Timedelta(seconds=1) < pd.Timestamp(year, 8, 15, 12)).all()


def test_unsupported_zero_missing_but_genuine_crop_failure_retained():
    labels = pd.DataFrame({"adm_id": ["a", "b", "c", "d"], "year": [2003] * 4,
                           "yield": [0., 0., 4., -1.], "harvest_area": [np.nan, 10., 10., 10.],
                           "production": [np.nan, 0., 40., -10.]})
    normalized, audit = module.normalize_labels(labels)
    assert np.isnan(normalized.iloc[0]["yield"])
    assert normalized.iloc[1]["yield"] == 0.
    assert normalized.iloc[2]["yield"] == 4.
    assert np.isnan(normalized.iloc[3]["yield"])
    assert list(audit.adm_id) == ["a", "d"]
    assert list(normalized.adm_id) == list(labels.adm_id)


def test_only_prespecified_secondary_horizon_adds_august():
    raw = pd.DataFrame({"adm_id": ["US-01-001"] * 3,
                        "date": ["20030728", "20030821", "20030829"], "ndvi": [.4, .5, .6]})
    assert module.prepare_ndvi(raw).empty
    late = module.prepare_ndvi(raw, months={4: "apr", 5: "may", 6: "jun", 7: "jul", 8: "aug"}, issue_month=9)
    assert list(late.date.dt.strftime("%Y%m%d")) == ["20030728", "20030821"]
    assert (late.assumed_available_date < pd.Timestamp("2003-09-15 12:00:00")).all()


def test_secondary_weather_stops_august31(tmp_path):
    rows = [{"adm_id": "US-01-001", "date": date, **{v: 1. for v in module.WEATHER}} for date in [20030830, 20030831, 20030901]]
    path = tmp_path / "raw.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    result = module.aggregate_weather(path, {"US-01-001"}, chunksize=1, months={8: "aug"})
    assert len(result) == 1
    assert result.iloc[0].prec_sum == 2.
