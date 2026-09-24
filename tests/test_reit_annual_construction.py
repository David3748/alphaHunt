import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reit_annual_construction import (
    annual_flow,
    features_from_bands,
    label_construction_stages,
    search_stac,
    select_annual_items,
)


def test_feature_fractions_and_shape_validation():
    red = np.array([[0.30, 0.30], [0.10, 0.10]])
    nir = np.array([[0.80, 0.80], [0.12, 0.12]])
    swir = np.array([[0.20, 0.20], [0.40, 0.40]])
    result = features_from_bands(red, nir, swir, aoi_area_km2=4.0, aoi="x", year=2020)
    assert result["valid_pixel_fraction"] == 1.0
    assert 0.0 < result["vegetation_fraction"] < 1.0
    assert result["built_up_area_km2"] == result["built_up_fraction"] * 4.0


def test_stage_fixture_covers_all_four_stages():
    root = Path(__file__).resolve().parents[1]
    frame = pd.read_csv(root / "tests/fixtures/reit_annual_construction/stage_fixture_features.csv")
    labelled = label_construction_stages(frame)
    assert labelled["stage"].tolist() == ["monitoring", "clearing", "foundations", "shell", "completion"]
    flow = annual_flow(labelled)
    assert flow["stage_mix"].tolist() == ["clearing", "foundations", "shell", "completion"]
    assert (flow["construction_flow_km2"] > 0).all()


def test_low_coverage_fails_closed_and_does_not_advance_baseline():
    frame = pd.DataFrame([
        {"aoi": "x", "date": "2020-12-31", "valid_pixel_fraction": 1.0, "vegetation_fraction": .7, "bare_soil_fraction": .05, "impervious_fraction": .1, "roof_fraction": .05, "built_up_fraction": .1},
        {"aoi": "x", "date": "2021-12-31", "valid_pixel_fraction": .2, "vegetation_fraction": .1, "bare_soil_fraction": .5, "impervious_fraction": .2, "roof_fraction": .1, "built_up_fraction": .2},
        {"aoi": "x", "date": "2022-12-31", "valid_pixel_fraction": 1.0, "vegetation_fraction": .45, "bare_soil_fraction": .24, "impervious_fraction": .13, "roof_fraction": .08, "built_up_fraction": .13},
    ])
    labelled = label_construction_stages(frame)
    assert labelled["stage"].tolist() == ["monitoring", "monitoring", "clearing"]


def test_select_annual_items_preserves_acquisition_and_publication_fields():
    rows = pd.DataFrame([
        {"aoi": "x", "item_id": "cloudy", "acquired_at": "2020-06-01T00:00:00Z", "cloud_cover_pct": 30, "published_at": None},
        {"aoi": "x", "item_id": "clear", "acquired_at": "2020-07-01T00:00:00Z", "cloud_cover_pct": 5, "published_at": "2020-07-02T00:00:00Z"},
    ])
    chosen = select_annual_items(rows)
    assert chosen.iloc[0]["item_id"] == "clear"
    assert chosen.iloc[0]["published_at"] == "2020-07-02T00:00:00Z"


def test_search_stac_records_null_publication_without_substitution(monkeypatch):
    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"features": [{"id": "x", "collection": "landsat-c2-l2", "properties": {"datetime": "2020-01-02T00:00:00Z", "eo:cloud_cover": 2}, "assets": {}}], "links": []}

    class Session:
        def post(self, *args, **kwargs):
            return Response()

    records, manifest = search_stac("x", [0, 0, 1, 1], "2020-01-01", "2020-12-31", session=Session())
    assert records[0]["acquired_at"] == "2020-01-02T00:00:00Z"
    assert records[0]["published_at"] is None
    assert records[0]["publication_time_field"] is None
    assert manifest["returned_items"] == 1


def test_config_is_json_and_declares_public_sources():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "config/reit_annual_construction.json").read_text())
    assert config["collections"] == ["landsat-c2-l2"]
    assert len(config["aois"]) == 3
