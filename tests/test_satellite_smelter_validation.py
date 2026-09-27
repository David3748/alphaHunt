"""Availability and economic-target guards for the smelter experiment."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.satellite_smelter_validation import (
    event_validation, expanding_nowcasts, load_heat, paired_block_interval,
    quarter_features, score_forecasts,
)


def heat(rows):
    result = pd.DataFrame(rows, columns=["date", "created", "cloud_frac", "hot_px20"])
    for col in ("date", "created"):
        result[col] = pd.to_datetime(result[col], utc=True)
    return result


def test_quarter_availability_excludes_backfills_missing_metadata_and_clouds():
    data = heat([
        ("2024-01-01", "2024-01-02", 0.0, 2),
        ("2024-02-01", "2026-01-01", 0.0, 0),
        ("2024-02-02", None, 0.0, 0),
        ("2024-02-03", "2024-02-04", 0.3, 0),
    ])
    frame = quarter_features(data, ["2024Q1", "2024Q2"])
    assert frame.iloc[0]["available_scenes"] == 1
    assert frame.iloc[0]["hot_fraction"] == 1
    assert frame.iloc[0]["late_or_missing_scenes"] == 2
    assert frame.iloc[1]["available_scenes"] == 0
    assert np.isnan(frame.iloc[1]["hot_fraction"])


def synthetic_forecasts():
    quarters = pd.period_range("2022Q1", "2024Q4", freq="Q")
    features = pd.DataFrame({
        "quarter": quarters.astype(str), "forecast_at": quarters.end_time.tz_localize("UTC"),
        "hot_fraction": np.arange(12) / 12, "available_scenes": 10,
    })
    labels = pd.DataFrame({
        "quarter": quarters.astype(str), "available_at": quarters.end_time.tz_localize("UTC") + pd.Timedelta(days=20),
        "refined_copper_kt": np.arange(12) * 3 + 10,
    })
    return features, labels


def test_new_forecasts_do_not_change_past_predictions():
    features, labels = synthetic_forecasts()
    first = expanding_nowcasts(features, labels, "refined_copper_kt")
    labels.loc[labels.index[-1], "refined_copper_kt"] = 99999
    features.loc[features.index[-1], "hot_fraction"] = 0
    second = expanding_nowcasts(features, labels, "refined_copper_kt")
    pd.testing.assert_series_equal(first.iloc[:-1]["satellite_ols"], second.iloc[:-1]["satellite_ols"])


def test_unpublished_past_labels_do_not_enter_model():
    features, labels = synthetic_forecasts()
    labels.loc[0, "available_at"] = pd.Timestamp("2026-01-01", tz="UTC")
    first = expanding_nowcasts(features, labels, "refined_copper_kt")
    labels.loc[0, "refined_copper_kt"] = 99999
    second = expanding_nowcasts(features, labels, "refined_copper_kt")
    pd.testing.assert_series_equal(first["satellite_ols"], second["satellite_ols"])


def test_target_already_public_is_rejected():
    features, labels = synthetic_forecasts()
    labels.loc[11, "available_at"] = pd.Timestamp("2025-01-01", tz="UTC") - pd.Timedelta(days=3)
    with pytest.raises(ValueError, match="already public"):
        expanding_nowcasts(features, labels, "refined_copper_kt")


def test_block_interval_keeps_constant_paired_improvement():
    assert paired_block_interval([2.0] * 8) == [2.0, 2.0]


def test_actual_data_does_not_verify_output_forecast():
    root = Path(__file__).resolve().parents[1]
    labels = pd.read_csv(root / "results/satellite_validation/smelters/production_labels.csv")
    data = load_heat(root / "results/satellite_sites/heat_rio_kennecott.csv")
    features = quarter_features(data, labels["quarter"])
    predictions = expanding_nowcasts(features, labels, "refined_copper_kt")
    assert len(predictions) == 8
    assert (pd.to_datetime(predictions["label_available_at"], utc=True) > predictions["forecast_at"]).all()
    score = score_forecasts(predictions)
    assert not score["verified_forecast_usefulness"]
    assert score["comparisons"]["persistence"]["paired_block_bootstrap_95pct_kt"][0] < 0
    no_heat = features.set_index("quarter").loc["2025Q1"]
    assert no_heat["hot_fraction"] == 0
    assert labels.set_index("quarter").loc["2025Q1", "refined_copper_kt"] > 40


def test_closed_smelter_is_not_verified_zero_refinery_production():
    data = heat([
        ("2023-01-01", "2023-01-02", 0, 10),
        ("2024-01-01", "2024-01-02", 0, 0),
    ])
    result = event_validation(data)
    assert result["before"]["hot_scenes"] == 1
    assert result["after"]["hot_scenes"] == 0
    assert not result["forecast_usefulness_verified"]
    assert "refinery continued" in result["critical_target_caveat"]
