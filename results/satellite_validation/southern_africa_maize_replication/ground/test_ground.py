import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SPEC = importlib.util.spec_from_file_location("southern_ground", Path(__file__).with_name("rebuild_ground.py"))
ground = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ground)


def test_only_national_cells_count_and_cosine_weights_apply():
    dates = pd.date_range("2000-08-01", "2000-08-31")
    values = np.zeros((31, 2, 2))
    values[:, 0, 0] = 2
    values[:, 1, 0] = 4
    values[:, :, 1] = 9999
    mask = np.array([[True, False], [True, False]])
    rows, _ = ground.aggregate(values, np.array([0., 60.]), mask, dates)
    assert rows[0]["eligible"]
    assert rows[0]["grid_cells"] == 2
    assert rows[0]["precip_mm"] == pytest.approx(31 * (2 + .5 * 4) / 1.5)


def test_one_missing_day_abstains_instead_of_partial_sum():
    dates = pd.date_range("2000-08-01", "2000-08-31")
    values = np.ones((31, 1, 10))
    values[10, :, :] = np.nan
    rows, _ = ground.aggregate(values, np.array([-15.]), np.ones((1, 10), bool), dates)
    assert not rows[0]["eligible"]
    assert np.isnan(rows[0]["precip_mm"])
    assert rows[0]["minimum_valid_cells"] == 0


def test_daily_coverage_threshold_is_not_monthly_average():
    dates = pd.date_range("2000-09-01", "2000-09-30")
    values = np.ones((30, 1, 10))
    values[0, 0, :2] = np.nan
    rows, _ = ground.aggregate(values, np.array([-15.]), np.ones((1, 10), bool), dates)
    assert not rows[0]["eligible"]
    values[0, 0, 1] = 1
    rows, _ = ground.aggregate(values, np.array([-15.]), np.ones((1, 10), bool), dates)
    assert rows[0]["eligible"]
    assert rows[0]["precip_mm"] == pytest.approx(30)


def test_calendar_gaps_abstain_even_if_all_observed_values_are_finite():
    dates = pd.date_range("2000-08-01", "2000-08-30")
    rows, _ = ground.aggregate(np.ones((30, 1, 1)), np.array([-15.]), np.ones((1, 1), bool), dates)
    assert not rows[0]["eligible"]


def test_mask_freeze_is_intact():
    freeze = ground.load_freeze()
    assert freeze["countries"]["ZM"]["mask_cells"] == 249
    assert freeze["countries"]["ZW"]["mask_cells"] == 135


def test_tampered_geometry_freeze_fails(tmp_path, monkeypatch):
    (tmp_path / "geography_freeze.json").write_text("{}")
    monkeypatch.setattr(ground, "OUT", tmp_path)
    with pytest.raises(ValueError, match="geography changed"):
        ground.load_freeze()
