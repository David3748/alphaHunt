import numpy as np
import pandas as pd
import pytest

from src import satellite_cybench_validation as model


def inputs():
    rows = []
    for year in range(2003, 2015):
        row = dict(adm_id="US19001", year=year,
                   forecast_at=f"{year}-08-15 12:00:00")
        for month, number in zip(model.MONTHS, (4, 5, 6, 7)):
            days = pd.Timestamp(year, number, 1).days_in_month
            row.update({f"ndvi_{month}": .6, f"ndvi_count_{month}": 3,
                        f"ndvi_expected_{month}": 4,
                        f"ndvi_latest_window_end_{month}": f"{year}-{number:02d}-28"})
            for variable in ("tmin", "tmax", "tavg", "vpd", "prec", "rad", "et0", "cwb"):
                row[f"{variable}_{month}"] = 20.
                row[f"{variable}_count_{month}"] = days
                row[f"{variable}_expected_{month}"] = days
        rows.append(row)
    labels = pd.DataFrame(dict(adm_id="US19001", year=range(2003, 2015),
                               **{"yield": np.arange(12) * .1 + 5}))
    locations = pd.DataFrame([dict(adm_id="US19001", latitude=41., longitude=-94.)])
    return pd.DataFrame(rows), labels, locations


def test_future_and_current_outcomes_cannot_change_forecast_inputs():
    features, labels, locations = inputs()
    x, y = model.validate_inputs(features, labels, locations)
    train, test, _ = model.fold_rows(x, y, 2013)
    y.loc[y.year.ge(2013), "actual"] = 900
    altered_train, altered_test, _ = model.fold_rows(x, y, 2013)
    pd.testing.assert_frame_equal(train, altered_train)
    pd.testing.assert_frame_equal(test[model.BASE + model.NDVI],
                                  altered_test[model.BASE + model.NDVI])
    assert test.latest_training_year.eq(2012).all()


def test_unknown_current_outcome_still_gets_forecast_inputs():
    features, labels, locations = inputs()
    labels.loc[labels.year.eq(2013), "yield"] = np.nan
    x, y = model.validate_inputs(features, labels, locations)
    _, test, _ = model.fold_rows(x, y, 2013)
    assert test.eligible.all()
    assert test.actual.isna().all()


def test_training_lag_is_restricted_to_each_rows_own_issue():
    features, labels, locations = inputs()
    x, y = model.validate_inputs(features, labels, locations)
    train, _, _ = model.fold_rows(x, y, 2013)
    row = train[train.year.eq(2005)].iloc[0]
    assert row.persistence == pytest.approx(5.1)
    assert row.recent_mean == pytest.approx(5.05)
    assert not train.year.eq(2003).any()  # No fabricated lag for the first row.


def test_complete_window_and_publication_lag_are_both_required():
    features, labels, locations = inputs()
    features.loc[0, "ndvi_latest_window_end_jul"] = "2003-08-02"
    x, _ = model.validate_inputs(features, labels, locations)
    assert not x.iloc[0].usable
    assert x.iloc[1:].usable.all()


def test_partial_weather_month_and_low_ndvi_coverage_abstain():
    features, labels, locations = inputs()
    features.loc[0, "prec_count_may"] = 30
    features.loc[1, "ndvi_count_jul"] = 1
    x, _ = model.validate_inputs(features, labels, locations)
    assert not x.iloc[:2].usable.any()
    assert x.iloc[2:].usable.all()


def predictions():
    return pd.DataFrame([dict(year=year, eligible=True, actual=10., weather=9.,
                              satellite=9.8, trend=9., persistence=8., recent_mean=8.5)
                         for year in range(2013, 2024) for _ in range(500)])


def test_more_correlated_counties_do_not_narrow_year_block_uncertainty():
    rows = predictions()
    a = model.annual_losses(rows)
    b = model.annual_losses(pd.concat([rows, rows], ignore_index=True))
    assert model.block_ci(a, "weather") == pytest.approx(model.block_ci(b, "weather"))


def test_post_reference_year_failure_cannot_be_hidden_in_pooled_success():
    rows = predictions()
    rows.loc[rows.year.eq(2022), "satellite"] = 8.9
    summary, _ = model.score(rows)
    assert summary["comparisons"]["weather"]["rmse_reduction"] > .05
    assert not summary["post_reference_year_sign_check_passed"]
    assert not summary["forecast_gate_passed"]


def test_delayed_past_label_is_excluded_from_regression_and_trend():
    features, labels, locations = inputs()
    x, y = model.validate_inputs(features, labels, locations)
    y.loc[y.year.eq(2012), "available_at"] = pd.Timestamp("2014-01-01")
    train, test, _ = model.fold_rows(x, y, 2013)
    assert not train.year.eq(2012).any()
    assert test.latest_training_year.eq(2011).all()
    assert test.persistence.eq(5.8).all()
