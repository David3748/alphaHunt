"""Independent timing, coverage and scoring contract checks for the county study."""
import numpy as np
import pandas as pd
import pytest

from src import satellite_cybench_validation as model


def fold_inputs():
    years = np.arange(2003, 2015)
    features = pd.DataFrame({"adm_id": "US-19-001", "year": years,
                             "forecast_at": pd.to_datetime([f"{y}-08-15 12:00:00" for y in years]),
                             "usable": True, "latitude": 41., "longitude": -94.})
    for column in model.WEATHER + model.NDVI:
        features[column] = .5
    labels = pd.DataFrame({"adm_id": "US-19-001", "year": years,
                           "actual": 5 + .1 * (years - 2003) + .05 * np.sin(years),
                           "available_at": pd.to_datetime([f"{y + 1}-06-30" for y in years])})
    return features, labels


def test_unreleased_label_cannot_enter_training_or_change_test_features():
    features, labels = fold_inputs()
    labels.loc[labels.year.eq(2012), "available_at"] = pd.Timestamp("2014-01-01")
    train, test, _ = model.fold_rows(features, labels, 2013)
    assert not train.year.eq(2012).any()
    assert train.available_at.lt(pd.Timestamp("2013-08-15 12:00:00")).all()
    labels.loc[labels.year.eq(2012), "actual"] = 100000.
    changed_train, changed_test, _ = model.fold_rows(features, labels, 2013)
    pd.testing.assert_frame_equal(train, changed_train)
    pd.testing.assert_frame_equal(test[model.BASE + model.NDVI],
                                  changed_test[model.BASE + model.NDVI])


def test_nonmonotonic_release_dates_exclude_delayed_observation_from_lags():
    features, labels = fold_inputs()
    labels.loc[labels.year.eq(2005), "available_at"] = pd.Timestamp("2010-01-01")
    train, _, _ = model.fold_rows(features, labels, 2013)
    values = labels.set_index("year").actual
    early = train[train.year.eq(2006)].iloc[0]
    later = train[train.year.eq(2008)].iloc[0]
    assert early.persistence == pytest.approx(values.loc[2004])
    assert early.recent_mean == pytest.approx(values.loc[[2003, 2004]].mean())
    assert later.persistence == pytest.approx(values.loc[2007])
    assert later.recent_mean == pytest.approx(values.loc[[2003, 2004, 2006, 2007]].mean())


def test_county_without_any_feature_rows_remains_explicit_abstention():
    features, labels = fold_inputs()
    predictions, coverage = model.predict(features.iloc[:0], labels, years=[2013])
    assert len(predictions) == 1
    assert predictions.adm_id.iloc[0] == "US-19-001"
    assert not predictions.eligible.iloc[0]
    assert predictions.abstention_reason.iloc[0] == "No current feature row"
    assert np.isfinite(predictions.actual.iloc[0])
    assert coverage[0]["forecasts"] == 0
    assert model.score(predictions)[0]["forecast_gate_passed"] is False


def test_county_without_enough_prior_labels_is_counted_as_abstention():
    features, labels = fold_inputs()
    predictions, coverage = model.predict(features, labels[labels.year.ge(2009)], years=[2013])
    assert len(predictions) == 1
    assert not predictions.eligible.iloc[0]
    reason = "Fewer than eight prior released yield labels"
    assert coverage[0]["abstentions"] == {reason: 1}
    assert predictions.abstention_reason.iloc[0] == reason


def test_unknown_outcomes_do_not_suppress_available_forecast_rows():
    features, labels = fold_inputs()
    labels.loc[labels.year.eq(2013), "actual"] = np.nan
    _, test, _ = model.fold_rows(features, labels, 2013)
    assert test.eligible.all()
    assert test.actual.isna().all()
    for name in model.MODELS:
        test[name] = 5.
    assert model.annual_losses(test).empty


def test_annual_metric_weights_years_equally_despite_county_counts():
    rows = pd.DataFrame({"year": [2013, 2014, 2014, 2014], "eligible": True,
                         "actual": 10.})
    for name in model.MODELS:
        rows[name] = [9., 7., 7., 7.]
    summary, annual = model.score(rows)
    assert annual.n_counties.tolist() == [1, 3]
    assert summary["metrics"]["satellite"]["rmse_t_ha"] == pytest.approx(np.sqrt(5))
    assert summary["metrics"]["satellite"]["mae_t_ha"] == pytest.approx(2.)
    assert not summary["data_count_gate_passed"]


def test_calendar_gap_is_retained_in_block_resampling():
    annual = pd.DataFrame({"year": [2000, 2002, 2003, 2004, 2005, 2006],
                           "weather_mse": [4., 1., 8., 2., 3., 7.],
                           "satellite_mse": [2., 2., 5., 1., 4., 3.]})
    expected = []
    rng = np.random.default_rng(20260927)
    starts = rng.integers(0, 7, (200, 2))
    calendar = {int(row.year): row for row in annual.itertuples()}
    for draw in starts:
        sampled = [(int(start) + offset) % 7 + 2000 for start in draw for offset in range(5)][:7]
        kept = [calendar[year] for year in sampled if year in calendar]
        expected.append(1 - np.sqrt(np.mean([x.satellite_mse for x in kept]) /
                                     np.mean([x.weather_mse for x in kept])))
    assert model.block_ci(annual, "weather", draws=200) == pytest.approx(np.quantile(expected, [.025, .975]))
