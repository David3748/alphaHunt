import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import satellite_solar_validation as solar


def synthetic_panel():
    dates = pd.date_range("2015-01-31", "2025-12-31", freq="ME")
    rng = np.random.default_rng(724)
    ghi = 5 + 2 * np.sin((dates.month - 3) / 12 * 2 * np.pi) + rng.normal(0, .4, len(dates))
    daily = 2000 * ghi * np.exp(-np.arange(len(dates)) * .0003)
    panel = pd.DataFrame({"ghi_kwh_m2_day": ghi, "daily_mwh": daily,
                          "generation_mwh": daily * dates.days_in_month,
                          "month": dates.month, "year": dates.year,
                          "time": np.arange(len(dates)) / 12}, index=dates)
    panel["prior_year_ghi"] = panel.ghi_kwh_m2_day.shift(12)
    return panel


def test_current_or_future_generation_never_enters_its_own_prediction():
    panel = synthetic_panel()
    base = solar.predict(panel)
    date = pd.Timestamp("2022-06-30")
    mutated = panel.copy()
    mutated.loc[date:, ["daily_mwh", "generation_mwh"]] *= 100
    changed = solar.predict(mutated)
    for model in ("season_trend", "satellite", "prior_year_placebo"):
        np.testing.assert_allclose(base.loc[base.date <= date, model], changed.loc[changed.date <= date, model])
    assert (base.training_last_month <= base.date - pd.offsets.MonthEnd(2)).all()


def test_ablation_identifies_satellite_information_beyond_seasons():
    pred = solar.predict(synthetic_panel())
    satellite_error = np.mean((pred.satellite - pred.actual_mwh) ** 2)
    baseline_error = np.mean((pred.season_trend - pred.actual_mwh) ** 2)
    assert satellite_error < 1e-8
    assert baseline_error > 100


def test_parser_does_not_triple_count_aggregate_rows_or_annual_power(tmp_path):
    common = {"period": "2020-01", "plantCode": "57695", "generation": "100"}
    eia = {"response": {"total": "3", "data": [common | {"fuel2002": "ALL", "primeMover": "ALL"},
        common | {"fuel2002": "SUN", "primeMover": "ALL"},
        common | {"fuel2002": "SUN", "primeMover": "PV"}]}}
    power = {"properties": {"parameter": {"ALLSKY_SFC_SW_DWN": {"202001": 3.0, "202013": 5.0}}}}
    (tmp_path / "eia.json").write_text(json.dumps(eia))
    (tmp_path / "power.json").write_text(json.dumps(power))
    panel = solar.load_panel(tmp_path)
    assert len(panel) == 1
    assert panel.generation_mwh.iloc[0] == 100
    eia["response"]["total"] = "4"
    (tmp_path / "eia.json").write_text(json.dumps(eia))
    with pytest.raises(ValueError, match="Truncated"):
        solar.load_panel(tmp_path)
