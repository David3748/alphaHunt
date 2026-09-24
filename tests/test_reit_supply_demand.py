import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reit_supply_demand import (  # noqa: E402
    classify_property_types,
    fit_property_type_model,
    supply_demand_features,
    supply_demand_signal,
    download_world_bank_population,
    validate_property_type_model,
)


ROOT = Path(__file__).resolve().parents[1]


def test_supply_demand_is_lagged_and_direction_is_auditable():
    panel = pd.read_csv(ROOT / "tests/fixtures/reit_supply_demand/supply_demand_fixture.csv")
    features = supply_demand_features(panel, availability_lag_months=6)
    row = features[(features.geo == "Metro_B") & (features.period == 2021)].iloc[0]
    assert row.source_period_end == pd.Timestamp("2021-12-31")
    assert row.available_date == pd.Timestamp("2022-06-30")
    assert row.excess_supply > 0  # built-up growth outruns both demand proxies
    assert np.isclose(row.demand_minus_supply, -row.excess_supply)

    signal = supply_demand_signal(features)
    assert set(signal.columns) == {"geo", "available_date", "demand_minus_supply", "signal"}
    assert np.isclose(signal.groupby("available_date").signal.sum(), 0.0).all()
    assert (signal.available_date > features.source_period_end).all()


def test_missing_night_lights_renormalizes_demand_weights():
    panel = pd.DataFrame([
        {"period": 2020, "geo": "A", "built_up_km2": 100, "population": 100},
        {"period": 2021, "geo": "A", "built_up_km2": 110, "population": 105},
    ])
    features = supply_demand_features(panel, demand_weights={"population_growth": 0.6, "night_lights_growth": 0.4})
    row = features.iloc[-1]
    expected_population_growth = 1.05 - 1.0
    assert np.isclose(row.demand_growth, expected_population_growth)
    assert row.proxy_count == 1


def test_classifier_is_transparent_and_marks_low_evidence_unknown():
    buildings = pd.read_csv(ROOT / "tests/fixtures/reit_supply_demand/property_type_labeled_fixture.csv")
    classified = classify_property_types(buildings)
    assert set(buildings.property_type) == set(classified.property_type)
    assert (classified.confidence > 0).all()
    assert classified.filter(like="score_").shape[1] == 6
    sparse = pd.DataFrame([{"building_id": "unknown-1"}])
    assert classify_property_types(sparse).property_type.iloc[0] == "unknown"


def test_supervised_baseline_is_reproducible_on_fixture():
    buildings = pd.read_csv(ROOT / "tests/fixtures/reit_supply_demand/property_type_labeled_fixture.csv")
    model = fit_property_type_model(buildings)
    diagnostics = validate_property_type_model(model, buildings)
    assert diagnostics["rows"] == len(buildings)
    assert diagnostics["accuracy"] >= 0.9
    assert diagnostics["macro_f1"] >= 0.9


def test_invalid_panel_is_rejected():
    with pytest.raises(ValueError, match="missing supply/demand"):
        supply_demand_features(pd.DataFrame({"period": [2020]}))


def test_world_bank_adapter_is_tidy_without_network():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"page": 1}, [{"date": "2020", "value": 10, "countryiso3code": "AAA"}]]

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    result = download_world_bank_population(["AAA"], start_year=2020, end_year=2020, session=Session())
    assert result.loc[0, "geo"] == "AAA"
    assert result.loc[0, "population"] == 10
