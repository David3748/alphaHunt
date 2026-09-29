from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from satellite_hydro_validation import MODELS, aggregate_ons, build_panel, parse_altimetry, predict, score


def sources():
    dates = pd.date_range("2008-01-01", "2025-12-01", freq="MS")
    rng = np.random.default_rng(12)
    monthly = pd.DataFrame({"date": dates, "generation_mw": 500 + rng.normal(0, 50, len(dates)),
                            "inflow_m3_s": 1000 + rng.normal(0, 100, len(dates)),
                            "ground_level_m": 390 + rng.normal(0, 1, len(dates))})
    observed = pd.date_range("2008-07-01", "2025-12-31", freq="10D")
    satellite = pd.DataFrame({"observed_at": observed,
                              "assumed_available_at": observed + pd.Timedelta(days=90),
                              "height_m": rng.normal(0, 2, len(observed))})
    return monthly, satellite


def test_parser_excludes_old_mission_defaults_and_bad_error(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("metadata\n"
                    "TOPEX 1 20090801 1 0 2 .1 1 ERA GIM ERA 0 0 0 394 0\n"
                    "JASON2 2 20090802 2 0 999.99 .1 1 ERA GIM ERA 0 0 0 394 0\n"
                    "JASON2 3 20090803 3 0 2 99.999 1 ERA GIM ERA 0 0 0 394 0\n"
                    "JASON2 4 20090804 4 0 2 .1 1 ERA GIM ERA 0 0 1 394 0\n"
                    "JASON2 5 20090805 5 1 2 .1 1 ERA GIM ERA 0 0 0 394 0\n")
    frame = parse_altimetry(path)
    assert len(frame) == 1
    assert frame.iloc[0].observed_at == pd.Timestamp("2009-08-05 05:01")
    assert frame.iloc[0].assumed_available_at == pd.Timestamp("2009-11-03 05:01")


def test_numeric_strings_in_ons_parquet_are_parsed_before_monthly_mean(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    hours = pd.date_range("2020-01-01", "2020-02-01", freq="h", inclusive="left")
    pd.DataFrame({"din_instante": hours, "nom_usina": "UHE Sobradinho", "id_ons": "BAUSB",
                  "val_geracao": "150.50"}).to_parquet(inputs / "generation_2020.parquet")
    days = pd.date_range("2020-01-01", "2020-01-31", freq="D")
    pd.DataFrame({"din_instante": days, "nom_reservatorio": "Sobradinho", "id_reservatorio": "TEST",
                  "val_nivelmontante": "390.4", "val_vazaoafluente": "999.0"}).to_parquet(inputs / "hydrology_2020.parquet")
    row = aggregate_ons(tmp_path).iloc[0]
    assert row.generation_mw == pytest.approx(150.5)
    assert row.ground_level_m == pytest.approx(390.4)
    assert row.inflow_m3_s == pytest.approx(999.0)
    assert row.hour_coverage == 1


def test_satellite_asof_dates_and_staleness():
    panel = build_panel(*sources())
    valid = panel.loc[panel.complete]
    assert (valid.satellite_available_at < valid.forecast_at).all()
    age = (valid.forecast_at - valid.satellite_observed_at).dt.days
    assert age.between(90, 130).all()
    assert ((valid.satellite_observed_at - valid.satellite_previous_observed_at).dt.days >= 30).all()
    assert ((valid.forecast_at - valid.satellite_previous_observed_at).dt.days <= 180).all()


def test_simultaneous_tandem_missions_average_without_outcome_selection(tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("JASN3 1 20210117 2 6 -1.5 .1 1 ERA GIM ERA 0 0 0 390 1\n"
                    "SEN6A 1 20210117 2 6 -1.7 .1 1 ERA GIM ERA 0 0 0 390 0\n")
    frame = parse_altimetry(path)
    assert len(frame) == 1
    assert frame.iloc[0].height_m == pytest.approx(-1.6)
    assert frame.iloc[0].n_simultaneous_measurements == 2


def test_fixed_30_day_ground_lag_does_not_force_march_eligibility():
    panel = build_panel(*sources())
    march = panel.loc[panel.month == 3]
    assert (~march.complete).all()
    assert panel.loc[panel.month == 4, "complete"].all()


def test_current_and_future_outcome_changes_cannot_change_issued_prediction():
    monthly, satellite = sources()
    original = predict(build_panel(monthly, satellite))
    monthly.loc[monthly.date >= "2020-01-01", "generation_mw"] += 10000
    changed = predict(build_panel(monthly, satellite))
    mask = original.date <= "2020-01-01"
    np.testing.assert_array_equal(original.loc[mask, MODELS], changed.loc[mask, MODELS])


def test_unavailable_satellite_observations_cannot_change_issued_prediction():
    monthly, satellite = sources()
    original = predict(build_panel(monthly, satellite))
    satellite.loc[satellite.assumed_available_at >= "2020-01-01", "height_m"] += 10000
    changed = predict(build_panel(monthly, satellite))
    mask = original.date <= "2020-01-01"
    np.testing.assert_array_equal(original.loc[mask, MODELS], changed.loc[mask, MODELS])


def test_missing_ground_month_abstains_without_shift_or_fill():
    monthly, satellite = sources()
    monthly = monthly.loc[monthly.date != "2019-11-01"]
    predictions = predict(build_panel(monthly, satellite))
    assert not predictions.loc[predictions.date == "2020-01-01", "eligible"].iloc[0]
    assert predictions.loc[predictions.date == "2020-02-01", "eligible"].iloc[0]


def test_training_labels_precede_issue_and_preserve_gate_claim_limits():
    predictions = predict(build_panel(*sources()))
    valid = predictions.loc[predictions.eligible]
    assert len(predictions) == 96
    assert (valid.n_train >= 72).all()
    assert (valid.latest_training_target_available < valid.forecast_at).all()
    result = score(predictions, draws=100)
    assert not result["original_vintage_operational_verification"]
    assert not result["trading_alpha_verified"]
    assert not result["prospective_verification"]


def test_no_eligible_forecast_is_not_a_pass():
    predictions = predict(build_panel(*sources()))
    predictions["eligible"] = False
    assert not score(predictions, draws=10)["incremental_gate_passed"]


def test_unknown_target_outcome_does_not_prevent_forecast():
    monthly, satellite = sources()
    expected = predict(build_panel(monthly, satellite)).iloc[-1]
    monthly.loc[monthly.date == pd.Timestamp("2025-12-01"), "generation_mw"] = np.nan
    actual = predict(build_panel(monthly, satellite)).iloc[-1]
    assert actual.eligible == expected.eligible
    for model in MODELS:
        assert actual[model] == pytest.approx(expected[model])
    assert pd.isna(actual.generation_mw)
