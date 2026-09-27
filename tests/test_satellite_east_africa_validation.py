from pathlib import Path
import importlib.util
import sys
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from satellite_east_africa_validation import MODELS, build_panel, predict, score


def sources():
    rng = np.random.default_rng(120)
    years = np.arange(1982, 2026)
    dates = pd.date_range('1982-01-01', '2025-12-01', freq='MS')
    rain = pd.DataFrame({'date': dates, 'precip_mm': rng.uniform(5, 180, len(dates))})
    dates = pd.to_datetime([f'{year}-{month:02d}-01' for year in years for month in (8, 9, 10)])
    ground = pd.DataFrame({'date': dates, 'precip_mm': rng.uniform(5, 100, len(dates)),
                           'n_days': [31, 30, 9] * len(years)})
    sst = pd.DataFrame({'date': pd.to_datetime([f'{year}-09-01' for year in years]),
                        'iod_sst_difference_c': rng.normal(1, .5, len(years)),
                        'nino34_sst_c': rng.normal(27, .7, len(years))})
    return rain, ground, sst


def test_fixed_future_target_prior_lookup_and_training_release():
    panel = build_panel(*sources())
    forecasts = predict(panel)
    assert len(forecasts) == 27 and forecasts.eligible.sum() == 26
    assert not forecasts.iloc[0].eligible and forecasts.iloc[0].year == 1999
    valid = forecasts.loc[forecasts.eligible]
    assert (valid.latest_training_year == valid.year - 1).all()
    assert (valid.latest_training_label_available < valid.forecast_at).all()
    assert (valid.sst_assumed_available_at < valid.forecast_at).all()
    assert (valid.ground_assumed_available_at < valid.forecast_at).all()
    assert (valid.forecast_at < valid.target_start).all()


def test_current_and_future_outcomes_cannot_change_issued_forecast():
    rain, ground, sst = sources()
    original = predict(build_panel(rain, ground, sst))
    rain.loc[rain.date >= '2010-11-01', 'precip_mm'] += 10000
    changed = predict(build_panel(rain, ground, sst))
    mask = original.year <= 2010
    np.testing.assert_array_equal(original.loc[mask, list(MODELS)], changed.loc[mask, list(MODELS)])


def test_missing_current_outcome_retains_forecast_and_is_not_scored():
    rain, ground, sst = sources()
    original = predict(build_panel(rain, ground, sst))
    rain.loc[rain.date >= '2025-11-01', 'precip_mm'] = np.nan
    changed = predict(build_panel(rain, ground, sst))
    np.testing.assert_array_equal(original[list(MODELS)], changed[list(MODELS)])
    result = score(changed, draws=100)
    assert result['n_pending_outcomes'] == 1 and result['n_test_years'] == 25
    assert result['n_abstentions'] == 1


def test_future_predictors_do_not_change_issued_forecast():
    rain, ground, sst = sources()
    original = predict(build_panel(rain, ground, sst))
    ground.loc[ground.date > '2010-12-31', 'precip_mm'] += 10000
    sst.loc[sst.date > '2010-12-31', ['iod_sst_difference_c', 'nino34_sst_c']] += 100
    changed = predict(build_panel(rain, ground, sst))
    mask = original.year <= 2010
    np.testing.assert_array_equal(original.loc[mask, list(MODELS)], changed.loc[mask, list(MODELS)])


def test_identical_current_ground_controls_in_both_models():
    rain, ground, sst = sources()
    original = predict(build_panel(rain, ground, sst)).set_index('year')
    ground.loc[ground.date == '2010-09-01', 'precip_mm'] += 1000
    changed = predict(build_panel(rain, ground, sst)).set_index('year')
    for model in ('baseline', 'satellite'):
        assert original.loc[2010, model] != changed.loc[2010, model]
    assert original.loc[2010, 'climatology'] == changed.loc[2010, 'climatology']


def test_early_october_control_is_in_primary_both_models_only():
    rain, ground, sst = sources()
    original = predict(build_panel(rain, ground, sst)).set_index('year')
    secondary = predict(build_panel(rain, ground, sst), include_early_october=False).set_index('year')
    ground.loc[ground.date == '2010-10-01', 'precip_mm'] += 100
    changed = predict(build_panel(rain, ground, sst)).set_index('year')
    changed_secondary = predict(build_panel(rain, ground, sst), include_early_october=False).set_index('year')
    for model in ('baseline', 'satellite'):
        assert original.loc[2010, model] != changed.loc[2010, model]
        assert secondary.loc[2010, model] == changed_secondary.loc[2010, model]
    ground.loc[ground.date == '2010-10-01', 'n_days'] = 31
    with pytest.raises(ValueError, match='October1-9'):
        build_panel(rain, ground, sst)


def test_source_month_and_late_source_dates_rejected():
    rain, ground, sst = sources()
    sst.loc[0, 'date'] = pd.Timestamp('1982-10-01')
    with pytest.raises(ValueError, match='September'):
        build_panel(rain, ground, sst)
    panel = build_panel(*sources())
    panel.loc[panel.year == 2010, 'sst_assumed_available_at'] = pd.Timestamp('2010-10-21')
    with pytest.raises(ValueError, match='timing'):
        predict(panel)


def test_missing_season_month_does_not_silently_become_zero_or_shift_prior():
    rain, ground, sst = sources()
    rain = rain.loc[rain.date != '2009-12-01']
    panel = build_panel(rain, ground, sst).set_index('year')
    assert np.isnan(panel.loc[2009, 'actual_mm'])
    assert np.isnan(panel.loc[2010, 'prior_season_mm'])
    forecasts = predict(panel.reset_index()).set_index('year')
    assert forecasts.loc[2009, 'eligible'] and not forecasts.loc[2010, 'eligible']
    result = score(forecasts.reset_index(), draws=100)
    assert np.isfinite(result['comparisons']['baseline']['rmse_gain_ci95_mm']).all()


def test_rainfall_claim_does_not_become_production_or_trading_claim():
    result = score(predict(build_panel(*sources())), draws=100)
    assert not result['economic_output_forecast_verified']
    assert not result['trading_alpha_verified']
    assert not result['satellite_only_incremental_attribution']
    assert not result['cross_candidate_multiple_testing_adjusted']
