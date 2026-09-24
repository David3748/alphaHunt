import numpy as np
import pandas as pd

from src.softs_weather_overlay import expanding_seasonal_z, rule_score


def test_rule_score_is_stage_specific_and_nonlinear():
    index = pd.to_datetime(["2020-07-03", "2020-12-04"])
    anomalies = pd.DataFrame({"precip_z": [-2.0, -2.0], "tmax_z": [1.5, 1.5], "tmin_z": [0, 0]}, index=index)
    score = rule_score(anomalies, {"months": [7], "dry": .5, "heat": .3, "combo": .5})
    assert np.isclose(score.iloc[0], .5*2 + .3*1.5 + .5*1.5)
    assert score.iloc[1] == 0


def test_expanding_seasonal_z_does_not_use_future_years():
    index = pd.date_range("2001-01-05", periods=8, freq="52W-FRI")
    series = pd.Series(np.arange(8, dtype=float), index=index)
    z = expanding_seasonal_z(series, 3)
    changed = series.copy(); changed.iloc[-1] = 1000
    z_changed = expanding_seasonal_z(changed, 3)
    pd.testing.assert_series_equal(z.iloc[:-1], z_changed.iloc[:-1])
