from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from satellite_hurricane_validation import MODELS, annual_activity, build_panel, parse_hurdat, predict, score


def sources():
    years = np.arange(1981, 2026)
    rng = np.random.default_rng(5)
    activity = pd.DataFrame({"year": years, "aug_nov_ace": 100 + rng.uniform(-50, 50, len(years)),
                             "june_july_ace": rng.uniform(0, 20, len(years))})
    sst = pd.DataFrame({"year": years[1:], "source_month": [f"{year}-06-01" for year in years[1:]],
                        "mdr_sst_c": rng.normal(27, .5, len(years) - 1),
                        "nino34_sst_c": rng.normal(28, .8, len(years) - 1)})
    return activity, sst


def test_ace_uses_only_synoptic_storm_winds_and_fixed_calendar_target(tmp_path):
    path = tmp_path / "hurdat.txt"
    lines = ["20000731,1800,,TS,20N,60W,40,990,", "20000801,0000,,HU,20N,60W,100,960,",
             "20000801,0300,L,HU,20N,60W,110,950,", "20000901,0600,,EX,20N,60W,120,950,",
             "20001001,1200,,TD,20N,60W,30,1000,", "20001130,1800,,SS,20N,60W,40,990,",
             "20001201,0000,,HU,20N,60W,100,960,"]
    path.write_text(f"AL012000,TEST,{len(lines)},\n" + "\n".join(lines))
    activity = annual_activity(parse_hurdat(path)).iloc[0]
    assert activity.june_july_ace == pytest.approx(.16)
    assert activity.aug_nov_ace == pytest.approx(1.16)


def test_missing_wind_not_silently_zero_and_record_truncation_detected(tmp_path):
    path = tmp_path / "hurdat.txt"
    path.write_text("AL012000,TEST,1,\n20000801,0000,,HU,20N,60W,-999,960,\n")
    with pytest.raises(ValueError, match="Missing"):
        parse_hurdat(path)
    path.write_text("AL012000,TEST,2,\n20000801,0000,,HU,20N,60W,100,960,\n")
    with pytest.raises(ValueError, match="Truncated"):
        parse_hurdat(path)


def test_only_june_sst_and_preissue_source_dates():
    activity, sst = sources()
    panel = build_panel(activity, sst)
    predictions = predict(panel)
    assert len(predictions) == 27 and predictions.eligible.all()
    assert (predictions.latest_training_year == predictions.year - 1).all()
    assert (predictions.latest_training_label_available < predictions.forecast_at).all()
    assert (predictions.sst_assumed_available_at < predictions.forecast_at).all()
    assert (predictions.storm_feature_cutoff < predictions.forecast_at).all()
    sst.loc[sst.year == 2000, "source_month"] = "2000-07-01"
    with pytest.raises(ValueError, match="June"):
        build_panel(activity, sst)


def test_current_and_future_target_labels_cannot_change_issued_forecast():
    activity, sst = sources()
    original = predict(build_panel(activity, sst))
    activity.loc[activity.year >= 2010, "aug_nov_ace"] += 10000
    changed = predict(build_panel(activity, sst))
    mask = original.year <= 2010
    np.testing.assert_array_equal(original.loc[mask, MODELS], changed.loc[mask, MODELS])


def test_missing_future_outcome_does_not_prevent_forecast_but_is_not_scored():
    activity, sst = sources()
    original = predict(build_panel(activity, sst))
    activity.loc[activity.year == 2025, "aug_nov_ace"] = np.nan
    changed = predict(build_panel(activity, sst))
    assert changed.eligible.all()
    np.testing.assert_array_equal(original[list(MODELS)], changed[list(MODELS)])
    result = score(changed, draws=100)
    assert result["n_test_years"] == 26
    assert result["n_pending_outcomes"] == 1
    assert result["n_abstentions"] == 0


def test_missing_training_label_is_removed_without_requiring_current_outcome():
    panel = build_panel(*sources())
    original = predict(panel).set_index("year")
    panel.loc[panel.year == 2000, "actual_ace"] = np.nan
    changed = predict(panel).set_index("year")
    assert changed.loc[2000, "eligible"]
    assert changed.loc[2000, "satellite"] == original.loc[2000, "satellite"]
    assert changed.loc[2001, "n_train"] == original.loc[2001, "n_train"] - 1
    assert np.isfinite(changed.loc[2001, "satellite"])


def test_future_sst_and_storm_activity_cannot_change_issued_forecast():
    activity, sst = sources()
    original = predict(build_panel(activity, sst))
    sst.loc[sst.year > 2010, ["mdr_sst_c", "nino34_sst_c"]] += 100
    activity.loc[activity.year > 2010, "june_july_ace"] += 10000
    changed = predict(build_panel(activity, sst))
    mask = original.year <= 2010
    np.testing.assert_array_equal(original.loc[mask, MODELS], changed.loc[mask, MODELS])


def test_same_current_june_july_activity_is_a_control_in_both_models():
    activity, sst = sources()
    original = predict(build_panel(activity, sst)).set_index("year")
    activity.loc[activity.year == 2010, "june_july_ace"] += 100
    changed = predict(build_panel(activity, sst)).set_index("year")
    assert original.loc[2010, "baseline"] != changed.loc[2010, "baseline"]
    assert original.loc[2010, "satellite"] != changed.loc[2010, "satellite"]
    assert original.loc[2010, "climatology"] == changed.loc[2010, "climatology"]


def test_unavailable_feature_is_rejected():
    panel = build_panel(*sources())
    panel.loc[panel.year == 2000, "sst_assumed_available_at"] = pd.Timestamp("2000-08-02")
    with pytest.raises(ValueError, match="timing"):
        predict(panel)


def test_prior_season_lookup_does_not_shift_across_missing_year():
    activity, sst = sources()
    activity = activity.loc[activity.year != 2009]
    panel = build_panel(activity, sst)
    assert np.isnan(panel.loc[panel.year == 2010, "prior_season_ace"]).all()
    predictions = predict(panel)
    assert not predictions.loc[predictions.year == 2010, "eligible"].iloc[0]


def test_score_does_not_claim_trading_or_isolated_satellite_attribution():
    result = score(predict(build_panel(*sources())), draws=100)
    assert not result["original_vintage_operational_verification"]
    assert not result["satellite_only_incremental_attribution"]
    assert not result["trading_alpha_verified"]
    assert result["n_test_years"] == 27
