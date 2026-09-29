from pathlib import Path
import sys
import gzip
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from satellite_pacific_hurricane_validation import MODELS, build_panel, features, parse_pacific, predict
from satellite_hurricane_validation import annual_activity


def sources():
    years = np.arange(1981, 2026)
    rng = np.random.default_rng(5)
    activity = pd.DataFrame({"year": years, "aug_nov_ace": 100 + rng.uniform(-50, 50, len(years)),
                             "june_july_ace": rng.uniform(0, 20, len(years))})
    sst = pd.DataFrame({"year": years[1:], "source_month": [f"{year}-06-01" for year in years[1:]],
                        "nino34_sst_c": rng.normal(28, .8, len(years) - 1)})
    return activity, sst


def test_ep_origin_and_east140w_rules_exclude_cp_and_dateline(tmp_path):
    path = tmp_path / "hurdat.txt.gz"
    text = ("EP012000,TEST,4,\n20000801,0000,,HU,20N,120W,100,960,\n"
            "20000802,0000,,HU,20N,145W,100,960,\n20000803,0000,,HU,20N,170E,100,960,\n"
            "20000731,1800,,TS,20N,120W,40,990,\n"
            "CP012000,TEST,1,\n20000901,0000,,HU,20N,120W,100,960,\n")
    path.write_bytes(gzip.compress(text.encode()))
    records = parse_pacific(path)
    assert records.storm_id.eq("EP012000").all()
    assert records.loc[records.longitude < -140, "ace"].eq(0).all()
    annual = annual_activity(records).iloc[0]
    assert annual.aug_nov_ace == pytest.approx(1)
    assert annual.june_july_ace == pytest.approx(.16)


def test_only_single_nino_predictor_added_no_atlantic_mdr():
    activity, sst = sources()
    panel = build_panel(activity, sst)
    assert "mdr_sst_c" not in panel
    assert features(panel, "baseline").shape[1] == 3
    assert features(panel, "satellite").shape[1] == 4
    original = predict(panel)
    sst["mdr_sst_c"] = 999999
    changed = predict(build_panel(activity, sst))
    np.testing.assert_array_equal(original[list(MODELS)], changed[list(MODELS)])


def test_current_and_future_labels_cannot_affect_issued_forecast():
    activity, sst = sources()
    original = predict(build_panel(activity, sst))
    activity.loc[activity.year >= 2010, "aug_nov_ace"] += 10000
    changed = predict(build_panel(activity, sst))
    mask = original.year <= 2010
    np.testing.assert_array_equal(original.loc[mask, MODELS], changed.loc[mask, MODELS])


def test_forecast_does_not_require_not_yet_observed_target():
    activity, sst = sources()
    original = predict(build_panel(activity, sst))
    activity.loc[activity.year == 2025, "aug_nov_ace"] = np.nan
    changed = predict(build_panel(activity, sst))
    assert changed.eligible.all()
    np.testing.assert_array_equal(original[list(MODELS)], changed[list(MODELS)])


def test_future_nino_and_activity_cannot_affect_issued_forecast():
    activity, sst = sources()
    original = predict(build_panel(activity, sst))
    sst.loc[sst.year > 2010, "nino34_sst_c"] += 100
    activity.loc[activity.year > 2010, "june_july_ace"] += 10000
    changed = predict(build_panel(activity, sst))
    mask = original.year <= 2010
    np.testing.assert_array_equal(original.loc[mask, MODELS], changed.loc[mask, MODELS])


def test_all27years_have_preissue_sources_and_labels():
    predictions = predict(build_panel(*sources()))
    assert len(predictions) == 27 and predictions.eligible.all()
    assert (predictions.latest_training_label_available < predictions.forecast_at).all()
    assert (predictions.sst_assumed_available_at < predictions.forecast_at).all()
    assert (predictions.storm_feature_cutoff < predictions.forecast_at).all()
    assert (predictions.latest_training_year == predictions.year - 1).all()


def test_current_june_july_ace_control_changes_both_ridge_models():
    activity, sst = sources()
    original = predict(build_panel(activity, sst)).set_index("year")
    activity.loc[activity.year == 2010, "june_july_ace"] += 100
    changed = predict(build_panel(activity, sst)).set_index("year")
    assert original.loc[2010, "baseline"] != changed.loc[2010, "baseline"]
    assert original.loc[2010, "satellite"] != changed.loc[2010, "satellite"]
