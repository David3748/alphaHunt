"""Independent semantic audit of the USDA-anchored corn forecast contract."""
import numpy as np
import pandas as pd
import pytest

from src import corn_model as model


def vintage(date, year, value=170., hour=16, **changes):
    row = dict(report_date=date, published_at=f"{date}T{hour:02d}:00:00Z",
               publication_time_status="official_csv_release_date_and_time;Eastern_timezone_documented_by_USDA",
               release_time_eastern="12:00:00", marketing_year=f"{year}/{(year + 1) % 100:02d}",
               marketing_year_start=year, wasde_number=600,
               projection_estimate_flag="Proj.", yield_bu_acre=value,
               harvested_area_m_acres=80., production_m_bu=13601.,
               ending_stocks_m_bu=1500., total_use_m_bu=14000.,
               domestic_use_m_bu=12000., exports_m_bu=2000.,
               beginning_stocks_m_bu=1700., imports_m_bu=30., total_supply_m_bu=15500.,
               source_file="synthetic.csv", source_sha256="0" * 64)
    row.update(changes)
    return row


def proxy(year=2023, month=8, **changes):
    row = dict(year=year, forecast_at=f"{year}-{month:02d}-15T12:00:00Z", eligible=True,
               reason="", weather=10.0628, satellite=10.1884, trend=10.,
               prior_area_coverage=.93, forecast_prior_area_ha=20e6,
               total_reported_prior_area_ha=20e6 / .93, weight_harvest_year=year - 1)
    row.update(changes)
    return row


def calibration_panel():
    rows = []
    for year in range(2013, 2025):
        for month in [8, 9]:
            weather = np.sin(year) * 3
            ndvi = np.cos(year) * .8
            revision = .2 * weather - .3 * ndvi + (month - 8) * .6
            rows.append(dict(year=year, forecast_at=pd.Timestamp(f"{year}-{month:02d}-15T12:00:00Z"),
                eligible=True, reason="", usda_yield_bu_acre=170., harvested_area_m_acres=80.,
                usda_production_m_bu=13601., usda_ending_stocks_m_bu=1500., total_use_m_bu=14000.,
                usda_report_date=f"{year}-{month:02d}-10",
                usda_published_at=pd.Timestamp(f"{year}-{month:02d}-10T16:00:00Z"),
                weather_anomaly_bu_acre=weather, ndvi_increment_bu_acre=ndvi, is_september=int(month == 9),
                target_month=f"{year}-{month + 1:02d}", target_yield_bu_acre=170. + revision,
                target_available_at=pd.Timestamp(f"{year}-{month + 1:02d}-12T16:00:00Z"),
                label_revision_bu_acre=revision, coverage=.93, prior_area_footprint=.62))
    return pd.DataFrame(rows)


def same_predictions(left, right):
    assert left["status"] == right["status"] == "ready"
    assert left["training_years"] == right["training_years"]
    assert left["training_rows"] == right["training_rows"]
    for name in ["usda", "bias", "weather", "satellite"]:
        assert left["models"][name] == right["models"][name]


def test_anchor_is_same_marketing_year_and_strictly_before_issue():
    vintages = pd.DataFrame([
        vintage("2023-08-10", 2023, 170.),
        vintage("2023-08-14", 2022, 999.),
        vintage("2023-08-15", 2023, 180., hour=12),
        vintage("2023-09-12", 2023, 175.),
    ])
    row = model.build_panel(vintages, pd.DataFrame([proxy()])).iloc[0]
    assert row.usda_yield_bu_acre == 170.
    assert row.target_month == "2023-09"
    assert row.target_yield_bu_acre == 175.
    assert row.weather_anomaly_bu_acre == pytest.approx(1.)
    assert row.ndvi_increment_bu_acre == pytest.approx(2.)


def test_cancelled_october_report_is_not_replaced_with_november():
    vintages = pd.DataFrame([vintage("2013-09-12", 2013, 155.),
                             vintage("2013-11-08", 2013, 161.)])
    row = model.build_panel(vintages, pd.DataFrame([proxy(2013, 9)])).iloc[0]
    assert row.target_month == "2013-10"
    assert pd.isna(row.target_yield_bu_acre)
    assert pd.isna(row.target_available_at)
    assert pd.isna(row.label_revision_bu_acre)
    assert row.usda_yield_bu_acre == 155.


def test_forecast_is_invariant_to_current_and_future_labels_and_features():
    panel = calibration_panel()
    row = panel[(panel.year == 2023) & (panel.is_september == 0)].iloc[0].copy()
    first = model.forecast_row(row, panel)
    changed = panel.copy()
    changed.loc[changed.year.ge(2023), ["label_revision_bu_acre", "target_yield_bu_acre"]] = 1e9
    changed.loc[changed.year.ge(2024), ["weather_anomaly_bu_acre", "ndvi_increment_bu_acre"]] = -1e9
    row["target_yield_bu_acre"] = 1e9
    row["label_revision_bu_acre"] = 1e9
    same_predictions(first, model.forecast_row(row, changed))


def test_same_harvest_year_and_unreleased_target_cannot_train():
    panel = calibration_panel()
    row = panel[(panel.year == 2023) & (panel.is_september == 1)].iloc[0]
    # The August2023 forecast's September12 target is known, but the frozen
    # cross-harvest-year calibration deliberately excludes it.
    delayed = (panel.year == 2022) & (panel.is_september == 1)
    panel.loc[delayed, "target_available_at"] = row.forecast_at
    first = model.forecast_row(row, panel)
    assert max(first["training_years"]) == 2022
    assert first["training_rows"] == 19
    panel.loc[delayed | panel.year.eq(2023), "label_revision_bu_acre"] = 1e8
    same_predictions(first, model.forecast_row(row, panel))


def test_unknown_next_month_target_does_not_block_a_current_forecast():
    panel = calibration_panel()
    row = panel[(panel.year == 2023) & (panel.is_september == 0)].iloc[0].copy()
    expected = model.forecast_row(row, panel)
    row["target_yield_bu_acre"] = np.nan
    row["label_revision_bu_acre"] = np.nan
    row["target_available_at"] = pd.NaT
    same_predictions(expected, model.forecast_row(row, panel))


def test_minimum_distinct_years_and_row_count_are_separate_requirements():
    panel = calibration_panel()
    row = panel[(panel.year == 2023) & (panel.is_september == 0)].iloc[0]
    seven_years_seven_rows = panel[(panel.year < 2020) & (panel.is_september == 0)]
    four_years_eight_rows = panel[panel.year.between(2018, 2021)]
    assert model.forecast_row(row, seven_years_seven_rows)["status"] == "abstain"
    assert model.forecast_row(row, four_years_eight_rows)["status"] == "abstain"


def test_bias_uses_prior_revisions_for_the_matching_issue_month():
    panel = calibration_panel()
    row = panel[(panel.year == 2023) & (panel.is_september == 0)].iloc[0]
    panel.loc[(panel.year < 2023) & (panel.is_september == 0), "label_revision_bu_acre"] = 1.
    panel.loc[(panel.year < 2023) & (panel.is_september == 1), "label_revision_bu_acre"] = 7.
    forecast = model.forecast_row(row, panel)
    assert forecast["models"]["bias"]["predicted_revision_bu_acre"] == pytest.approx(1.)


def test_conditional_balance_sheet_preserves_reported_rounding_and_area():
    panel = calibration_panel()
    row = panel[(panel.year == 2023) & (panel.is_september == 0)].iloc[0]
    forecast = model.forecast_row(row, panel)
    assert forecast["models"]["usda"]["conditional_production_m_bu"] == 13601.
    for prediction in forecast["models"].values():
        revision = prediction["predicted_revision_bu_acre"]
        assert prediction["predicted_yield_bu_acre"] == pytest.approx(170. + revision)
        assert prediction["conditional_production_m_bu"] == pytest.approx(13601. + 80. * revision)
        assert prediction["conditional_ending_stocks_m_bu"] == pytest.approx(1500. + 80. * revision)
        assert prediction["conditional_stocks_to_use"] == pytest.approx((1500. + 80. * revision) / 14000.)


def test_fixed_ridge_matches_an_independent_training_only_calculation():
    panel = calibration_panel()
    row = panel[(panel.year == 2023) & (panel.is_september == 0)].iloc[0]
    train = panel[panel.year.lt(2023)]
    forecast = model.forecast_row(row, panel)
    for name, columns in [("weather", ["weather_anomaly_bu_acre", "is_september"]),
                           ("satellite", ["weather_anomaly_bu_acre", "is_september", "ndvi_increment_bu_acre"])]:
        x = train[columns].to_numpy(float)
        center, scale = x.mean(axis=0), x.std(axis=0)
        z = (x - center) / scale
        y = train.label_revision_bu_acre.to_numpy()
        coefficients = np.linalg.solve(z.T @ z + 10 * np.eye(len(columns)), z.T @ (y - y.mean()))
        expected = y.mean() + ((row[columns].to_numpy(float) - center) / scale) @ coefficients
        assert forecast["models"][name]["predicted_revision_bu_acre"] == pytest.approx(expected)


def test_usda_reference_is_not_labeled_consensus_and_threshold_boundary_is_flat():
    panel = calibration_panel()
    row = panel[(panel.year == 2023) & (panel.is_september == 0)].iloc[0]
    forecast = model.forecast_row(row, panel)
    forecast["models"]["satellite"]["predicted_yield_bu_acre"] = 170.5
    signal = model.paper_signal(forecast)
    assert signal["reference_kind"] == "latest_usda"
    assert signal["bias"] == "flat" and signal["direction"] == 0
    assert signal["eligible_for_live_trading"] is False
    forecast["models"]["satellite"]["predicted_yield_bu_acre"] = 170.51
    assert model.paper_signal(forecast)["bias"] == "short"
    forecast["models"]["satellite"]["predicted_yield_bu_acre"] = 169.49
    assert model.paper_signal(forecast)["bias"] == "long"


def test_consensus_must_be_dated_before_issue_and_match_target():
    panel = calibration_panel()
    row = panel[(panel.year == 2023) & (panel.is_september == 0)].iloc[0]
    forecast = model.forecast_row(row, panel)
    consensus = dict(published_at="2023-08-14T12:00:00Z", marketing_year_start=2023,
                     target_month="2023-09", expected_yield_bu_acre=171., source="Synthetic audited survey fixture")
    signal = model.paper_signal(forecast, consensus)
    assert signal["reference_kind"] == "user_supplied_analyst_consensus"
    assert signal["reference_yield_bu_acre"] == 171.
    assert signal["eligible_for_live_trading"] is False
    for changes in [dict(published_at="2023-08-15T12:00:00Z"),
                    dict(published_at="2023-08-14 12:00:00"),
                    dict(target_month="2023-10"), dict(marketing_year_start=2022), dict(source="")]:
        with pytest.raises(ValueError):
            model.paper_signal(forecast, {**consensus, **changes})


def test_new_forecast_date_does_not_reuse_a_stale_county_proxy():
    result = model.forecast_as_of(calibration_panel(), "2026-09-15")
    assert result["status"] == "abstain"
    assert result["models"] == {}
    assert result["eligible_for_live_trading"] is False


@pytest.mark.parametrize("issue", ["2023-08-01T12:00:00Z", "2023-08-15T13:00:00Z", "2023-09-15T12:01:00Z"])
def test_fixed_horizon_rejects_other_days_or_clock_times(issue):
    vintages = pd.DataFrame([vintage("2023-07-12", 2023)])
    with pytest.raises(ValueError):
        model.build_panel(vintages, pd.DataFrame([proxy(forecast_at=issue)]))


def test_duplicate_calibration_issue_cannot_inflate_the_training_sample():
    panel = calibration_panel()
    row = panel[(panel.year == 2023) & (panel.is_september == 0)].iloc[0]
    duplicated = pd.concat([panel, panel.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError):
        model.forecast_row(row, duplicated)


def test_national_footprint_uses_prior_area_available_at_issue_not_coverage_ratio():
    vintages = pd.DataFrame([
        vintage("2023-08-09", 2022, harvested_area_m_acres=70.),
        vintage("2023-08-10", 2023, harvested_area_m_acres=80.),
        vintage("2023-08-20", 2022, harvested_area_m_acres=999.),
    ])
    row = model.build_panel(vintages, pd.DataFrame([proxy()])).iloc[0]
    assert row.coverage == .93
    assert row.prior_area_footprint == pytest.approx(20e6 / .40468564224 / 70e6)
    assert row.prior_area_footprint != row.coverage
    vintages.loc[vintages.report_date.eq("2023-08-20"), "harvested_area_m_acres"] = 1e9
    changed = model.build_panel(vintages, pd.DataFrame([proxy()])).iloc[0]
    assert changed.prior_area_footprint == row.prior_area_footprint
