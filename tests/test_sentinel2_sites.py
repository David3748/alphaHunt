import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import sentinel2_sites as s2


@pytest.mark.parametrize("lat, lon, square", [
    (33.2149, -97.2097, "14SPB"),     # Denton, TX
    (43.357, -78.605, "17TPJ"),       # Barker, NY
    (-7.10, 112.60, "49MFN"),         # Gresik, East Java
    (-32.75, -71.48, "19HBD"),        # Ventanas, Chile
    (31.8, -106.7, "13RCR"),          # Santa Teresa, NM: archived as 13SCR (see resolve_tile)
])
def test_mgrs_square_matches_reference_grid(lat, lon, square):
    zone, easting, northing = s2.latlon_to_utm(lat, lon)
    assert s2.mgrs_square(zone, lat, easting, northing) == square


def test_utm_forward_matches_known_point():
    # Denton reference from PROJ (EPSG:32614)
    zone, easting, northing = s2.latlon_to_utm(33.2149, -97.2097)
    assert zone == 14
    assert easting == pytest.approx(666846.70, abs=0.01)
    assert northing == pytest.approx(3676539.37, abs=0.01)


def test_southern_hemisphere_uses_false_northing():
    _, _, northing = s2.latlon_to_utm(-7.1, 112.6)
    assert 9_000_000 < northing < 10_000_000


def test_geometry_snaps_to_20m_grid_and_keeps_extent():
    site = {"lat": 33.2147, "lon": -97.20836,
            "aoi_m": {"west": 625, "east": 625, "south": 825, "north": 825}}
    geometry = s2.site_geometry(site)
    x0, y0, x1, y1 = geometry["bounds"]
    assert all(v % 20 == 0 for v in geometry["bounds"])
    assert 1250 <= x1 - x0 <= 1290 and 1650 <= y1 - y0 <= 1690
    assert geometry["epsg"] == 32614 and geometry["tile"] == "14SPB"


def test_aoi_past_the_square_west_edge_reads_the_western_tile():
    # 14SPB's square starts at easting 600,000; a point 300 m east of it with a
    # 1 km AOI spills west, which only 14SNB's tile (+9.8 km overlap) covers.
    lat, lon = 33.2, -97.9235
    zone, easting, _ = s2.latlon_to_utm(lat, lon)
    assert 600_000 < easting < 601_000
    geometry = s2.site_geometry({"lat": lat, "lon": lon, "aoi_m": {}})
    assert geometry["tile"] == "14SNB"


def test_resolve_tile_falls_back_to_neighbouring_band(monkeypatch):
    listings = {
        "sentinel-s2-l2a-cogs/13/R/CR/": "<ListBucketResult></ListBucketResult>",
        "sentinel-s2-l2a-cogs/13/S/CR/": "<ListBucketResult><CommonPrefixes><Prefix>"
                                         "sentinel-s2-l2a-cogs/13/S/CR/2026/</Prefix>"
                                         "</CommonPrefixes></ListBucketResult>",
    }

    def fake_get(url, *args, **kwargs):
        prefix = url.split("prefix=")[1]
        return listings.get(prefix, "<ListBucketResult/>").encode()

    monkeypatch.setattr(s2, "_get", fake_get)
    monkeypatch.setattr(s2, "_TILE_CACHE", {})
    assert s2.resolve_tile("13RCR") == "13SCR"


@pytest.mark.parametrize("applied, baseline, expected", [
    (True, "05.11", 0.0),     # Earth Search harmonised COGs: never subtract again
    (False, "05.00", -0.1),   # raw 04.00+ data carries the +1000 offset
    (False, "02.14", 0.0),    # pre-2022 baselines had no offset
])
def test_band_scale_offset_rules(applied, baseline, expected):
    item = {"properties": {"earthsearch:boa_offset_applied": applied, "s2:processing_baseline": baseline},
            "assets": {"red": {"raster:bands": [{"scale": 0.0001, "offset": -0.1}]}}}
    scale, offset = s2.band_scale(item, "B04")
    assert scale == 0.0001 and offset == expected


def _bands(**pixels):
    """One-row band dict from {name: (B02,B03,B04,B08,B8A,B11,B12)} reflectances."""
    names = ("B02", "B03", "B04", "B08", "B8A", "B11", "B12")
    values = np.array(list(pixels.values()), dtype=np.float32)
    out = {name: values[:, i][None, :] for i, name in enumerate(names)}
    out["SCL"] = np.full((1, len(pixels)), 5, dtype=np.uint8)
    return out, list(pixels)


def test_hot_mask_separates_heat_from_bright_or_odd_surfaces():
    bands, names = _bands(
        soil=(0.10, 0.14, 0.20, 0.26, 0.27, 0.35, 0.30),
        white_roof=(0.60, 0.62, 0.62, 0.57, 0.56, 0.60, 0.55),
        blue_roof=(0.14, 0.15, 0.17, 0.18, 0.20, 0.28, 0.34),   # B12 > B11 but not hot
        cloud=(0.55, 0.56, 0.57, 0.60, 0.60, 0.40, 0.30),
        warm_slag=(0.05, 0.06, 0.07, 0.10, 0.12, 0.20, 0.40),
        molten=(0.10, 0.10, 0.12, 0.25, 0.30, 1.20, 1.10),
        roof_glint=(0.62, 0.70, 0.77, 0.70, 0.66, 1.04, 1.09),  # seen at JIIPE, Sept 2024
    )
    hot = dict(zip(names, s2.hot_mask(bands)[0]))
    assert not hot["soil"] and not hot["white_roof"] and not hot["blue_roof"] and not hot["cloud"]
    assert not hot["roof_glint"]
    assert hot["warm_slag"] and hot["molten"]


def test_surface_flags_classify_roof_soil_and_vegetation():
    bands, names = _bands(
        white_roof=(0.60, 0.62, 0.62, 0.57, 0.56, 0.60, 0.55),
        concrete=(0.24, 0.26, 0.27, 0.29, 0.30, 0.33, 0.30),
        dry_soil=(0.13, 0.17, 0.22, 0.26, 0.27, 0.40, 0.33),
        grass=(0.03, 0.06, 0.04, 0.35, 0.36, 0.20, 0.10),
    )
    flags = {k: dict(zip(names, v[0])) for k, v in s2.surface_flags(bands).items()}
    assert flags["roof"]["white_roof"] and flags["built"]["concrete"] and not flags["roof"]["concrete"]
    assert not flags["built"]["dry_soil"] and flags["bare"]["dry_soil"]
    assert flags["veg"]["grass"] and not flags["built"]["grass"]


def test_day_distance_wraps_the_year():
    assert s2._day_distance(np.array([360]), 5).tolist() == [10]


def _write_chip(root: Path, site_id: str, day: str, bands: dict[str, np.ndarray]) -> None:
    folder = root / "chips" / site_id
    folder.mkdir(parents=True, exist_ok=True)
    raw = {k: np.round(v * 10_000).astype(np.uint16) for k, v in bands.items() if k != "SCL"}
    # 20 m bands are stored at half resolution, as the bucket serves them
    for k in ("B8A", "B11", "B12"):
        raw[k] = raw[k][::2, ::2]
    scales = {k: [0.0001, 0.0] for k in raw}
    np.savez_compressed(folder / f"S2X_00XXX_{day.replace('-', '')}_0_L2A.npz", status="ok",
                        scales=json.dumps(scales), acquired=f"{day}T10:00:00Z",
                        created=f"{day}T14:00:00Z", baseline="05.11",
                        SCL=np.full(bands["B04"].shape, 5, np.uint8)[::2, ::2], **raw)


def _scene(month: int, built_cells: slice | None):
    """8x8 pasture scene; the 'field' block reads as pavement only in winter,
    and built_cells marks real construction (white roof)."""
    shape = (8, 8)
    grass = (0.03, 0.06, 0.04, 0.35, 0.36, 0.20, 0.10)
    field_winter = (0.22, 0.24, 0.25, 0.27, 0.28, 0.30, 0.28)   # passes the built test
    roof = (0.60, 0.62, 0.62, 0.57, 0.56, 0.60, 0.55)
    names = ("B02", "B03", "B04", "B08", "B8A", "B11", "B12")
    bands = {n: np.full(shape, grass[i], np.float32) for i, n in enumerate(names)}
    if month in (12, 1, 2):
        for i, n in enumerate(names):
            bands[n][0:2, 0:4] = field_winter[i]
    if built_cells is not None:
        for i, n in enumerate(names):
            bands[n][4:8, built_cells] = roof[i]
    return bands


def test_construction_index_cancels_seasonal_false_positives(tmp_path):
    site = {"id": "synthetic"}
    days = pd.date_range("2023-06-05", "2025-05-30", freq="10D")
    for day in days:
        # construction: from 2024-09 a 4x4 roof appears (16 px = 0.0016 km2)
        cells = slice(4, 8) if day >= pd.Timestamp("2024-09-01") else None
        _write_chip(tmp_path, site["id"], day.strftime("%Y-%m-%d"), _scene(day.month, cells))
    index = s2.construction_index(site, tmp_path, window_days=40, min_obs=3)
    winter_before = index[(index.date >= "2024-01-10") & (index.date <= "2024-02-20")]
    winter_after = index[(index.date >= "2025-01-10") & (index.date <= "2025-02-20")]
    # raw built area swings with the season (the 8-cell field), ...
    assert winter_before["built_km2"].max() == pytest.approx(0.0008)
    # ... but the season-matched measure ignores it before construction ...
    assert winter_before["new_built_km2"].max() == 0
    # ... and counts exactly the 16 new roof pixels after it.
    assert winter_after["new_roof_km2"].iloc[-1] == pytest.approx(0.0016)
    assert winter_after["new_built_km2"].iloc[-1] == pytest.approx(0.0016)


def test_construction_baseline_never_reads_future_acquisitions(monkeypatch):
    dates = pd.DatetimeIndex(["2023-01-01", "2023-01-11", "2023-01-21", "2023-01-31"])
    # A future roof must not erase the newly visible roof at the third scene.
    flags = np.array([False, False, True, True])[:, None, None]
    stacks = {k: flags.copy() for k in ("built", "roof", "bare", "veg")}
    created = [(d + pd.Timedelta(hours=14)).isoformat() + "Z" for d in dates]
    monkeypatch.setattr(s2, "load_stack", lambda *args: (dates, stacks, created))
    result = s2.construction_index({}, Path("unused"), share=0.3)
    row = result[result.date == "2023-01-21"].iloc[0]
    assert row.new_roof_km2 == pytest.approx(0.0001)
    assert "2023-01-31" not in row.input_dates


def test_construction_excludes_late_and_missing_publication_dependencies(monkeypatch):
    dates = pd.DatetimeIndex(["2023-01-01", "2023-01-11", "2023-01-21", "2023-01-31",
                              "2023-02-10", "2024-01-21"])
    flags = np.array([False, False, True, True, True, True])[:, None, None]
    stacks = {k: flags.copy() for k in ("built", "roof", "bare", "veg")}
    created = ["2023-01-02T00:00:00Z", "2023-01-12T00:00:00Z", "2026-08-01T00:00:00Z",
               "", "2023-02-11T00:00:00Z", "2024-01-22T00:00:00Z"]
    monkeypatch.setattr(s2, "load_stack", lambda *args: (dates, stacks, created))
    result = s2.construction_index({}, Path("unused"), min_obs=1)
    row = result[result.date == "2024-01-21"].iloc[0]
    assert row.new_roof_km2 == pytest.approx(0.0001)
    assert "2023-01-21" not in row.input_dates
    assert "2023-01-31" not in row.input_dates
    assert pd.Timestamp(row.input_created_max) <= pd.Timestamp(row.available_at)
    assert not (result.date == "2023-01-31").any()


def test_summary_handles_all_construction_scenes_missing_publication(monkeypatch, tmp_path):
    dates = pd.date_range("2023-01-01", periods=3, freq="10D")
    stacks = {k: np.ones((3, 1, 1), dtype=bool) for k in ("built", "roof", "bare", "veg")}
    monkeypatch.setattr(s2, "load_stack", lambda *args: (dates, stacks, ["", "", ""]))
    result = s2.summarize({"sites": [{"id": "missing", "kind": "data_center"}]},
                          tmp_path / "cache", tmp_path / "output")
    assert result["sites"]["missing"]["clear_days"] == 0
    assert result["sites"]["missing"]["stalls"] == {}
    frame = pd.read_csv(tmp_path / "output/construction_missing.csv")
    assert frame.empty and "available_at" in frame.columns
