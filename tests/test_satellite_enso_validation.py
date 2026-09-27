"""Season alignment and causal training boundaries of the fixed ENSO test."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from satellite_enso_validation import DEFAULT, load_panel, predict, score


def test_september_is_before_entire_target_winter():
    panel = load_panel(DEFAULT / "inputs")
    assert panel.winter_year.iloc[0] == 1983
    assert (panel.predictor_end.dt.year + 1 == panel.winter_year).all()
    assert (panel.target_start.dt.year + 1 == panel.winter_year).all()
    assert (panel.target_end.dt.year == panel.winter_year).all()
    assert (panel.assumed_predictor_available < panel.forecast_at).all()
    assert (panel.forecast_at < panel.target_start).all()


def test_target_and_future_labels_cannot_change_current_forecast():
    panel = load_panel(DEFAULT / "inputs")
    before = predict(panel)
    changed = panel.copy()
    changed.loc[changed.winter_year >= 2010, "rain_inches"] += 100
    after = predict(changed)
    cols = ["satellite", "climatology", "persistence"]
    np.testing.assert_array_equal(before.loc[before.winter_year <= 2010, cols],
                                  after.loc[after.winter_year <= 2010, cols])


def test_future_features_cannot_change_prior_predictions():
    panel = load_panel(DEFAULT / "inputs")
    before = predict(panel)
    panel.loc[panel.winter_year > 2010, "september_sst_c"] += 5
    after = predict(panel)
    np.testing.assert_array_equal(before.loc[before.winter_year <= 2010, "satellite"],
                                  after.loc[after.winter_year <= 2010, "satellite"])


def test_fixed_normal_cannot_manufacture_skill():
    panel = load_panel(DEFAULT / "inputs")
    before = predict(panel)
    panel.september_sst_c -= 26.73
    np.testing.assert_allclose(before.satellite, predict(panel).satellite, atol=1e-11)


def test_unavailable_current_feature_rejected():
    panel = load_panel(DEFAULT / "inputs")
    panel.loc[20, "assumed_predictor_available"] = pd.Timestamp("2099-01-01")
    with pytest.raises(ValueError, match="availability"):
        predict(panel)


def test_preserved_result_is_provisional():
    predictions = predict(load_panel(DEFAULT / "inputs"))
    result = score(predictions)
    assert result["n_test_winters"] == 29
    assert result["comparisons"]["climatology"]["rmse_reduction_fraction"] == pytest.approx(.097371, abs=1e-5)
    assert result["comparisons"]["climatology"]["rmse_reduction_ci95_inches"][0] < 0
    assert not result["predefined_forecast_gate_passed"]
    assert not result["original_vintage_operational_verification"]
