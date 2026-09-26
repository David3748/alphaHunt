#!/usr/bin/env python3
"""Sentinel-2 construction and furnace-activity series for fixed sites.

Reads Level-2A cloud-optimized GeoTIFFs straight from the public AWS bucket that
backs Element 84's Earth Search (s3://sentinel-cogs, us-west-2). Scenes are found
by listing the bucket under each MGRS tile prefix, so no STAC API, account or key
is needed; only plain HTTPS to *.amazonaws.com.

Two per-scene measurements, both over a small fixed area of interest (AOI):

- construction: area of bright, spectrally flat, unvegetated surface (roofs,
  slabs, gravel pads) and of newly unvegetated ground, each compared with the
  same season of a pre-construction baseline year.
- furnace activity: count of pixels whose short-wave infrared is emitted heat
  rather than reflected sunlight (published Landsat fire tests, plus a guard
  against sun glint off metal roofs).

Point-in-time notes:

- Pixels do not change after acquisition, but a scene reaches the bucket hours to
  days after it is acquired. Every observation keeps the acquisition time and the
  item's 'created' time; a live rule must key on 'created'.
- The larger leak is the site list. A campus that is famous today was picked with
  hindsight, and an AOI drawn on today's imagery fits the buildings that ended up
  there. The config records when each site became public and whether its AOI was
  drawn blind (address plus a fixed radius) or on later imagery.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

BUCKET = "https://sentinel-cogs.s3.us-west-2.amazonaws.com"
PREFIX = "sentinel-s2-l2a-cogs"

# Scene classification (SCL) codes that hide the ground.
SCL_NODATA, SCL_DEFECTIVE, SCL_SHADOW = 0, 1, 3
SCL_CLOUD = (8, 9, 10)  # cloud medium probability, high probability, thin cirrus
SCL_SNOW = 11
OBSCURED = (SCL_NODATA, SCL_DEFECTIVE, SCL_SHADOW, *SCL_CLOUD, SCL_SNOW)

BANDS_10M = ("B02", "B03", "B04", "B08")
BANDS_20M = ("B8A", "B11", "B12", "SCL")
ASSET_KEY = {"B02": "blue", "B03": "green", "B04": "red", "B08": "nir", "B8A": "nir08",
             "B11": "swir16", "B12": "swir22", "SCL": "scl"}

# Fixed thresholds (surface reflectance). Chosen from the spectra of roofs, soil and
# dry pasture at one site before any series was inspected against event dates.
VEG_NDVI = 0.30
BARE_NDVI = 0.20
BUILT_VIS = 0.20          # mean of blue, green, red
BUILT_SWIR_RATIO = 1.35   # B11 / visible: soils run 1.6-2.2, roofs and concrete ~1.0
ROOF_VIS = 0.35
# Hot-pixel tests: the Landsat-8 OLI active-fire thresholds of Schroeder et al.
# (2016, Remote Sensing of Environment 185:210-220) mapped to Sentinel-2 bands
# (OLI 5/6/7 -> B8A/B11/B12), used as published rather than tuned on any smelter.
# A plain "B12 > B11" rule was tried first and flagged blue and grey roofing.
FIRE_R75_SURE, FIRE_D75_SURE, FIRE_B12_SURE = 2.5, 0.30, 0.50
FIRE_R75_MAYBE, FIRE_D75_MAYBE, FIRE_R76_MAYBE = 1.8, 0.17, 1.6
SATURATED = 1.0  # apparent reflectance above 1 cannot be diffuse reflected sunlight
# Sun glint off a metal roof also tops 1.0 in SWIR, but it is just as bright in the
# visible; furnace heat barely touches the red band. Added after a new JIIPE roof
# was flagged as heat in a third of Manyar's hot pixels.
HEAT_OVER_RED = 2.5  # B12 / B04


# --------------------------------------------------------------------------- geometry

_BAND_LETTERS = "CDEFGHJKLMNPQRSTUVWX"
_COL_SETS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")
_ROW_LETTERS = "ABCDEFGHJKLMNPQRSTUV"


def utm_zone(lat: float, lon: float) -> int:
    if 56 <= lat < 64 and 3 <= lon < 12:
        return 32
    if 72 <= lat < 84 and lon >= 0:
        for bound, zone in ((9, 31), (21, 33), (33, 35), (42, 37)):
            if lon < bound:
                return zone
    return int((lon + 180) // 6) % 60 + 1


def latlon_to_utm(lat: float, lon: float, zone: int | None = None) -> tuple[int, float, float]:
    """WGS84 -> UTM (zone, easting, northing) with the Kruger series (sub-mm in zone).

    Southern-hemisphere northings carry the 10,000 km false northing, matching
    EPSG:327xx and Sentinel-2's own grids.
    """
    zone = zone or utm_zone(lat, lon)
    a, f = 6378137.0, 1 / 298.257223563
    n = f / (2 - f)
    big_a = a / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
    alpha = (n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16,
             13 * n ** 2 / 48 - 3 * n ** 3 / 5,
             61 * n ** 3 / 240)
    phi = math.radians(lat)
    dlam = math.radians(lon - (zone * 6 - 183))
    c = 2 * math.sqrt(n) / (1 + n)
    t = math.sinh(math.atanh(math.sin(phi)) - c * math.atanh(c * math.sin(phi)))
    xi = math.atan2(t, math.cos(dlam))
    eta = math.atanh(math.sin(dlam) / math.sqrt(1 + t * t))
    easting = eta + sum(alpha[j] * math.cos(2 * (j + 1) * xi) * math.sinh(2 * (j + 1) * eta) for j in range(3))
    northing = xi + sum(alpha[j] * math.sin(2 * (j + 1) * xi) * math.cosh(2 * (j + 1) * eta) for j in range(3))
    easting = 500_000 + 0.9996 * big_a * easting
    northing = 0.9996 * big_a * northing + (10_000_000 if lat < 0 else 0)
    return zone, easting, northing


def mgrs_square(zone: int, lat: float, easting: float, northing: float) -> str:
    """Grid-zone designation plus 100 km square, e.g. '14SPB' (MGRS 'AA' lettering)."""
    band = _BAND_LETTERS[min(int((lat + 80) // 8), len(_BAND_LETTERS) - 1)]
    column = _COL_SETS[(zone - 1) % 3][int(easting // 100_000) - 1]
    row_offset = 5 if zone % 2 == 0 else 0
    row = _ROW_LETTERS[(int(northing // 100_000) + row_offset) % 20]
    return f"{zone:02d}{band}{column}{row}"


def site_geometry(site: dict) -> dict:
    """UTM zone, AOI bounds snapped to the 20 m grid, and the tile that holds them.

    A tile covers its own 100 km square plus 9.8 km beyond its east and south
    edges, so an AOI that pokes past the square's west or north edge is read from
    the neighbouring square's tile instead.
    """
    lat, lon = float(site["lat"]), float(site["lon"])
    zone, x, y = latlon_to_utm(lat, lon)
    aoi = site.get("aoi_m", {})
    west, east = float(aoi.get("west", 1000)), float(aoi.get("east", 1000))
    south, north = float(aoi.get("south", 1000)), float(aoi.get("north", 1000))
    snap = lambda v, fn: fn(v / 20.0) * 20.0  # noqa: E731
    bounds = (snap(x - west, math.floor), snap(y - south, math.floor),
              snap(x + east, math.ceil), snap(y + north, math.ceil))
    square_x0 = (x // 100_000) * 100_000
    square_y1 = (y // 100_000 + 1) * 100_000
    tx, ty = x, y
    if bounds[0] < square_x0:
        tx = x - 100_000
    if bounds[3] > square_y1 + 20:
        ty = y + 100_000
    tile = mgrs_square(zone, lat + (0.9 if ty > y else 0), tx, ty)
    if "tile" in site:
        tile = site["tile"]
    epsg = (32700 if lat < 0 else 32600) + zone
    return {"zone": zone, "epsg": epsg, "center": (x, y), "bounds": bounds, "tile": tile}


# --------------------------------------------------------------------------- bucket access

def _get(url: str, retries: int = 4, timeout: int = 60) -> bytes:
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code in (403, 404):
                raise
            if attempt == retries:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def tile_prefix(tile: str) -> str:
    return f"{PREFIX}/{int(tile[:2])}/{tile[2]}/{tile[3:5]}"


_TILE_CACHE: dict[str, str] = {}


def resolve_tile(tile: str) -> str:
    """The archived tile name for a 100 km square.

    Squares that straddle a latitude-band edge are archived under one band only
    (31.8N in zone 13 lives under 13SCR, not 13RCR), so try the neighbouring
    band letters when the computed name holds nothing.
    """
    if tile in _TILE_CACHE:
        return _TILE_CACHE[tile]
    index = _BAND_LETTERS.index(tile[2])
    for band_index in (index, index + 1, index - 1):
        if not 0 <= band_index < len(_BAND_LETTERS):
            continue
        candidate = f"{tile[:2]}{_BAND_LETTERS[band_index]}{tile[3:5]}"
        page = _get(f"{BUCKET}/?list-type=2&delimiter=/&max-keys=5&prefix={tile_prefix(candidate)}/").decode()
        if re.search(r"<Prefix>[^<]+/\d{4}/</Prefix>", page):
            _TILE_CACHE[tile] = candidate
            return candidate
    raise LookupError(f"no archived Sentinel-2 tile for {tile}")


def list_scenes(tile: str, start: dt.date, end: dt.date) -> list[str]:
    """Scene folders ('.../S2B_14SPB_20250709_0_L2A/') acquired in [start, end]."""
    scenes = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        url = f"{BUCKET}/?list-type=2&delimiter=/&prefix={tile_prefix(tile)}/{year}/{month}/"
        token = None
        while True:
            page = _get(url + (f"&continuation-token={urllib.parse.quote(token, safe='')}" if token else "")).decode()
            scenes += re.findall(r"<Prefix>([^<]+_L2A/)</Prefix>", page)
            match = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", page)
            if not match:
                break
            token = match.group(1)
        month += 1
        if month == 13:
            year, month = year + 1, 1
    keep = []
    for scene in scenes:
        date = scene_date(scene)
        if start <= date <= end:
            keep.append(scene)
    return sorted(set(keep))


def scene_name(scene: str) -> str:
    return scene.rstrip("/").split("/")[-1]


def scene_date(scene: str) -> dt.date:
    stamp = re.search(r"_(\d{8})_\d+_L2A", scene_name(scene)).group(1)
    return dt.datetime.strptime(stamp, "%Y%m%d").date()


def load_item(scene: str, cache_dir: Path) -> dict:
    path = cache_dir / "items" / f"{scene_name(scene)}.json"
    if path.exists():
        return json.loads(path.read_text())
    item = json.loads(_get(f"{BUCKET}/{scene}{scene_name(scene)}.json"))
    keep = {"id": item["id"], "properties": item["properties"],
            "assets": {k: {"href": v["href"], "raster:bands": v.get("raster:bands", [])}
                       for k, v in item["assets"].items() if k in ASSET_KEY.values()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keep))
    return keep


def _configure_gdal() -> None:
    for key, value in {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "GDAL_HTTP_MAX_RETRY": "5",
        "GDAL_HTTP_RETRY_DELAY": "2",
        "VSI_CACHE": "TRUE",
    }.items():
        os.environ.setdefault(key, value)


def read_band(href: str, bounds: tuple[float, float, float, float]) -> np.ndarray:
    import rasterio
    from rasterio.windows import from_bounds

    with rasterio.open(href) as dataset:
        window = from_bounds(*bounds, transform=dataset.transform)
        return dataset.read(1, window=window, boundless=True, fill_value=0)


def band_scale(item: dict, band: str) -> tuple[float, float]:
    """(scale, offset) that turn this bucket's digital numbers into reflectance.

    ESA baseline 04.00+ adds 1000 to every L2A value. Earth Search strips it
    before writing the COGs ('earthsearch:boa_offset_applied': true) yet still
    advertises offset -0.1 in raster:bands; applying it again drives vegetation
    red reflectance negative. Only unharmonised 04.00+ items need the offset.
    """
    raster = item["assets"][ASSET_KEY[band]].get("raster:bands") or [{}]
    scale = float(raster[0].get("scale", 1e-4))
    properties = item["properties"]
    if properties.get("earthsearch:boa_offset_applied", False):
        return scale, 0.0
    baseline = str(properties.get("s2:processing_baseline", "00.00"))
    return scale, (-0.1 if baseline >= "04.00" else 0.0)


def fetch_chip(site: dict, scene: str, cache_dir: Path, max_obscured: float = 0.6) -> dict:
    """Read SCL first; fetch the remaining bands only if the AOI is mostly visible."""
    geometry = site_geometry(site)
    chip_path = cache_dir / "chips" / site["id"] / f"{scene_name(scene)}.npz"
    if chip_path.exists():
        with np.load(chip_path) as cached:
            return {"status": str(cached["status"]), "path": chip_path}
    item = load_item(scene, cache_dir)
    scl = read_band(item["assets"]["scl"]["href"], geometry["bounds"])
    obscured = float(np.isin(scl, OBSCURED).mean())
    arrays = {"SCL": scl.astype(np.uint8)}
    status = "skipped_obscured"
    if obscured <= max_obscured:
        for band in BANDS_10M + ("B8A", "B11", "B12"):
            arrays[band] = read_band(item["assets"][ASSET_KEY[band]]["href"], geometry["bounds"]).astype(np.uint16)
        status = "ok"
    scales = {band: band_scale(item, band) for band in BANDS_10M + ("B8A", "B11", "B12")}
    chip_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        chip_path, status=status, scales=json.dumps(scales),
        acquired=item["properties"]["datetime"], created=item["properties"].get("created", ""),
        baseline=item["properties"].get("s2:processing_baseline", ""), **arrays,
    )
    return {"status": status, "path": chip_path}


# --------------------------------------------------------------------------- per-scene measures

def reflectance(chip: dict) -> dict[str, np.ndarray]:
    """Surface reflectance on the 10 m grid (20 m bands repeated 2x2)."""
    scales = json.loads(str(chip["scales"])) if isinstance(chip["scales"], (str, np.ndarray)) else chip["scales"]
    shape = chip["B04"].shape
    out = {}
    for band in BANDS_10M + ("B8A", "B11", "B12"):
        raw = chip[band].astype(np.float32)
        if raw.shape != shape:
            raw = np.repeat(np.repeat(raw, 2, axis=0), 2, axis=1)[: shape[0], : shape[1]]
        scale, offset = scales[band]
        values = raw * scale + offset
        values[raw == 0] = np.nan
        out[band] = values
    scl = chip["SCL"]
    if scl.shape != shape:
        scl = np.repeat(np.repeat(scl, 2, axis=0), 2, axis=1)[: shape[0], : shape[1]]
    out["SCL"] = scl
    return out


def construction_measures(bands: dict[str, np.ndarray], pixel_area_m2: float = 100.0) -> dict:
    visible_ground = ~np.isin(bands["SCL"], OBSCURED) & np.isfinite(bands["B04"])
    total = visible_ground.size
    n_valid = int(visible_ground.sum())
    result = {"visible_frac": n_valid / total if total else 0.0}
    if n_valid == 0:
        return result | {"built_km2": np.nan, "roof_km2": np.nan, "bare_km2": np.nan, "veg_frac": np.nan}
    vis = (bands["B02"] + bands["B03"] + bands["B04"]) / 3
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = (bands["B08"] - bands["B04"]) / (bands["B08"] + bands["B04"])
        flat = bands["B11"] <= BUILT_SWIR_RATIO * vis
    built = visible_ground & (vis >= BUILT_VIS) & flat & (ndvi < BARE_NDVI)
    roof = built & (vis >= ROOF_VIS)
    bare = visible_ground & (ndvi < BARE_NDVI)
    veg = visible_ground & (ndvi >= VEG_NDVI)
    # Scale visible-pixel fractions up to the whole AOI; partial scenes stay unbiased
    # as long as clouds fall at random over the site.
    area_km2 = total * pixel_area_m2 / 1e6
    return result | {
        "built_km2": float(built.sum() / n_valid * area_km2),
        "roof_km2": float(roof.sum() / n_valid * area_km2),
        "bare_km2": float(bare.sum() / n_valid * area_km2),
        "veg_frac": float(veg.sum() / n_valid),
    }


def _block_mean_2x2(values: np.ndarray) -> np.ndarray:
    """Average a 10 m band over each 20 m cell, back on the 10 m grid."""
    rows, cols = values.shape
    if rows % 2 or cols % 2:
        return values
    blocks = values.reshape(rows // 2, 2, cols // 2, 2).mean(axis=(1, 3))
    return np.repeat(np.repeat(blocks, 2, axis=0), 2, axis=1)


def hot_mask(bands: dict[str, np.ndarray]) -> np.ndarray:
    """Pixels whose SWIR signal is emitted heat, not reflected sunlight.

    Sunlit surfaces keep B12 at or below B8A; clouds and snow push B12 lower
    still. A pixel at several hundred degrees C lifts B12 (2.19 um) far above
    B8A (0.86 um), and above B11 (1.61 um) unless it is hot enough to lift both.
    The tests reject clouds on their own, so SCL is not applied here.
    """
    b04 = _block_mean_2x2(bands["B04"])  # match the 20 m SWIR cell
    b8a, b11, b12 = bands["B8A"], bands["B11"], bands["B12"]
    finite = np.isfinite(b04) & np.isfinite(b8a) & np.isfinite(b11) & np.isfinite(b12)
    with np.errstate(divide="ignore", invalid="ignore"):
        r75, r76, d75 = b12 / b8a, b12 / b11, b12 - b8a
        sure = (r75 > FIRE_R75_SURE) & (d75 > FIRE_D75_SURE) & (b12 > FIRE_B12_SURE)
        maybe = (r75 > FIRE_R75_MAYBE) & (d75 > FIRE_D75_MAYBE) & (r76 > FIRE_R76_MAYBE)
        glowing = ((b12 > SATURATED) | (b11 > SATURATED)) & (r75 > 1.5)
        not_glint = b12 >= HEAT_OVER_RED * np.maximum(b04, 0.01)
    return finite & (sure | maybe | glowing) & not_glint


def hotspot_measures(bands: dict[str, np.ndarray]) -> dict:
    """Hot-pixel count and summed SWIR excess, plus how much of the AOI was clouded.

    A cloudy scene with no hot pixels says nothing about the furnace, so the
    cloud fraction travels with every count.
    """
    clouded = np.isin(bands["SCL"], (SCL_SHADOW, *SCL_CLOUD))
    hot = hot_mask(bands)
    excess = np.where(hot, bands["B12"] - bands["B8A"], 0.0)
    finite = np.isfinite(bands["B12"]) & ~clouded
    # Pixels are on the 10 m grid, repeated from 20 m: divide by 4 for native counts.
    return {
        "cloud_frac": float(clouded.mean()),
        "hot_px20": float(hot.sum() / 4.0),
        "hot_excess": float(np.nansum(excess) / 4.0),
        "b12_max": float(np.nanmax(np.where(finite, bands["B12"], np.nan))) if finite.any() else np.nan,
    }


def surface_flags(bands: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Per-pixel classes behind construction_measures, for time stacking."""
    visible_ground = ~np.isin(bands["SCL"], OBSCURED) & np.isfinite(bands["B04"])
    vis = (bands["B02"] + bands["B03"] + bands["B04"]) / 3
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = (bands["B08"] - bands["B04"]) / (bands["B08"] + bands["B04"])
        flat = bands["B11"] <= BUILT_SWIR_RATIO * vis
    built = visible_ground & (vis >= BUILT_VIS) & flat & (ndvi < BARE_NDVI)
    return {
        "visible": visible_ground,
        "built": built,
        "roof": built & (vis >= ROOF_VIS),
        "bare": visible_ground & (ndvi < BARE_NDVI),
        "veg": visible_ground & (ndvi >= VEG_NDVI),
        "hot": hot_mask(bands),
    }


def load_stack(site: dict, cache_dir: Path, min_visible: float = 0.9):
    """Clear scenes for a site as (dates, {class: T x H x W bool}, created times).

    One scene per acquisition day, keeping the most visible; scenes under
    min_visible are dropped rather than gap-filled.
    """
    import pandas as pd

    best: dict[str, tuple[float, dict, str]] = {}
    for path in sorted((cache_dir / "chips" / site["id"]).glob("*.npz")):
        with np.load(path) as data:
            if str(data["status"]) != "ok":
                continue
            chip = {key: data[key] for key in data.files}
        flags = surface_flags(reflectance(chip))
        visible = float(flags["visible"].mean())
        day = str(chip["acquired"])[:10]
        if visible >= min_visible and (day not in best or visible > best[day][0]):
            best[day] = (visible, flags, str(chip["created"]))
    days = sorted(best)
    stacks = {name: np.stack([best[day][1][name] for day in days]) for name in
              ("visible", "built", "roof", "bare", "veg", "hot")} if days else {}
    return pd.DatetimeIndex(days), stacks, [best[day][2] for day in days]


def _day_distance(a: np.ndarray, b: int) -> np.ndarray:
    d = np.abs(a - b)
    return np.minimum(d, 365 - d)


def construction_index(site: dict, cache_dir: Path, baseline_months: int = 12,
                       window_days: int = 75, share: float = 0.6, min_obs: int = 3,
                       season_days: int = 45, pixel_area_m2: float = 100.0):
    """Causal, season-matched construction series for one site.

    For each clear scene date t:
      current map  = pixels flagged in >= `share` of clear scenes in (t - window, t]
      baseline map = the same, over baseline-year scenes within +/- season_days of
                     t's day of year (the first `baseline_months` of the series)
      new_built    = current built and not baseline built (km2); likewise roofs,
                     and new_cleared = currently bare where the baseline was vegetated.
    Matching the season cancels dormant grass and ploughed fields that pass for
    pavement in one season only. Nothing after t enters the value at t.
    """
    import pandas as pd

    dates, stacks, created = load_stack(site, cache_dir)
    if len(dates) == 0:
        return pd.DataFrame()
    base_end = dates[0] + pd.DateOffset(months=baseline_months)
    in_base = np.asarray(dates < base_end)
    doy = np.asarray(dates.dayofyear)
    km2 = pixel_area_m2 / 1e6
    rows = []
    for i, t in enumerate(dates):
        window = np.asarray((dates > t - pd.Timedelta(days=window_days)) & (dates <= t))
        season = in_base & (_day_distance(doy, int(doy[i])) <= season_days)
        if window.sum() < min_obs or season.sum() < 2:
            continue
        now = {k: stacks[k][window].mean(0) >= share for k in ("built", "roof", "bare")}
        base = {k: stacks[k][season].mean(0) >= 0.5 for k in ("built", "roof", "veg")}
        rows.append({
            "date": t, "created": created[i], "window_scenes": int(window.sum()),
            "baseline_scenes": int(season.sum()), "in_baseline": bool(in_base[i]),
            "built_km2": float(now["built"].sum() * km2),
            "roof_km2": float(now["roof"].sum() * km2),
            "new_built_km2": float((now["built"] & ~base["built"]).sum() * km2),
            "new_roof_km2": float((now["roof"] & ~base["roof"]).sum() * km2),
            "new_cleared_km2": float((now["bare"] & base["veg"]).sum() * km2),
        })
    return pd.DataFrame(rows)


def zone_slices(site: dict, zone: dict) -> tuple[slice, slice]:
    """Rows/columns of a named sub-box on the AOI's 20 m grid.

    zone["x_m"] and zone["y_m"] are [low, high] metres east and north of the
    site's lat/lon anchor.
    """
    geometry = site_geometry(site)
    (x0, _, _, y1), (cx, cy) = geometry["bounds"], geometry["center"]
    cols = [int(round((cx + v - x0) / 20)) for v in zone["x_m"]]
    rows = [int(round((y1 - (cy + v)) / 20)) for v in zone["y_m"][::-1]]
    return slice(max(rows[0], 0), rows[1]), slice(max(cols[0], 0), cols[1])


def heat_table(site: dict, cache_dir: Path, max_cloud: float = 0.2):
    """Hot pixels per clear-enough day, split by the site's named hot zones."""
    import pandas as pd

    zones = {z["name"]: zone_slices(site, z) for z in site.get("hot_zones", [])}
    rows = []
    for path in sorted((cache_dir / "chips" / site["id"]).glob("*.npz")):
        with np.load(path) as data:
            if str(data["status"]) != "ok":
                continue
            chip = {key: data[key] for key in data.files}
        bands = reflectance(chip)
        clouded = np.isin(bands["SCL"], (SCL_SHADOW, *SCL_CLOUD)) | ~np.isfinite(bands["B12"])
        if clouded.mean() > max_cloud:
            continue
        hot = hot_mask(bands)[::2, ::2]
        row = {"date": str(chip["acquired"])[:10], "created": str(chip["created"]),
               "cloud_frac": round(float(clouded.mean()), 3), "hot_px20": int(hot.sum())}
        for name, (r, c) in zones.items():
            row[name] = int(hot[r, c].sum())
        rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame.sort_values(["date", "cloud_frac"]).drop_duplicates("date").reset_index(drop=True)
    if zones:
        frame["elsewhere"] = frame["hot_px20"] - frame[list(zones)].sum(axis=1)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def window_stats(heat, windows: list[dict], column: str = "hot_px20", lookback_days: int = 365) -> list[dict]:
    """Detection rate inside each outage window against the months before it.

    p_value: chance of seeing this few hot scenes inside the window if the
    pre-window detection rate had held (one-sided binomial).
    """
    import pandas as pd

    out = []
    for window in windows:
        start, end = pd.Timestamp(window["from"]), pd.Timestamp(window["to"])
        inside = heat[(heat.date >= start) & (heat.date <= end)]
        before = heat[(heat.date < start) & (heat.date >= start - pd.Timedelta(days=lookback_days))]
        k, n = int((inside[column] >= 1).sum()), len(inside)
        rate = float((before[column] >= 1).mean()) if len(before) else float("nan")
        p_value = float(sum(math.comb(n, i) * rate ** i * (1 - rate) ** (n - i) for i in range(k + 1))) if n and rate == rate else float("nan")
        out.append({**window, "clear_scenes": n, "hot_scenes": k, "before_scenes": len(before),
                    "before_hot_rate": round(rate, 3), "p_value": p_value})
    return out


def stall_table(index, min_step_ha: float = 0.3, rel_step: float = 0.05, under_way_ha: float = 1.0) -> dict:
    """Longest spells without a new high in new built surface, once building is under way.

    Building is 'under way' from the first date new built surface reaches
    under_way_ha. A new high must beat the old one by max(min_step_ha,
    rel_step x old). A spell is closed when a new high ends it; the last spell
    is open and may be completion, winter or a measurement failure rather than
    a pause. Describes the past; it is not a trading rule.
    """
    series = index.loc[~index["in_baseline"], ["date", "new_built_km2"]].copy()
    series["ha"] = series["new_built_km2"] * 100
    if series.empty:
        return {}
    peak = float(series["ha"].max())
    active = series[series["ha"] >= under_way_ha]
    if active.empty:
        return {}
    series = series[series["date"] >= active["date"].iloc[0]]
    record_date, record = series["date"].iloc[0], float(series["ha"].iloc[0])
    spells = []
    for date, value in zip(series["date"], series["ha"]):
        if value >= record + max(min_step_ha, rel_step * record):
            spells.append({"from": record_date, "to": date, "days": (date - record_date).days,
                           "level_ha": record, "open": False})
            record_date, record = date, float(value)
    spells.append({"from": record_date, "to": series["date"].iloc[-1],
                   "days": (series["date"].iloc[-1] - record_date).days, "level_ha": record, "open": True})
    day = lambda t: t.strftime("%Y-%m-%d")  # noqa: E731
    for spell in spells:
        spell.update({"from": day(spell["from"]), "to": day(spell["to"]), "level_ha": round(spell["level_ha"], 2)})
    long_closed = [s for s in spells if not s["open"] and s["days"] >= 120]
    spells.sort(key=lambda s: -s["days"])
    return {"under_way": day(series["date"].iloc[0]), "latest": day(series["date"].iloc[-1]),
            "latest_ha": round(float(series["ha"].iloc[-1]), 2), "peak_ha": round(peak, 2),
            "closed_spells_120d": long_closed, "spells": spells[:3]}


def still_image(site: dict, cache_dir: Path, day: str, overlay_hot: bool = False,
                min_visible: float = 0.97, max_days: int = 45):
    """True-colour chip from the clearest scene near `day` (hot pixels in magenta)."""
    import pandas as pd

    target = pd.Timestamp(day)
    best = None
    for path in sorted((cache_dir / "chips" / site["id"]).glob("*.npz")):
        when = pd.Timestamp(scene_date(path.stem))
        gap = abs((when - target).days)
        if gap > max_days or (best and gap >= best[0]):
            continue
        with np.load(path) as data:
            if str(data["status"]) != "ok":
                continue
            chip = {key: data[key] for key in data.files}
        bands = reflectance(chip)
        if surface_flags(bands)["visible"].mean() < min_visible:
            continue
        best = (gap, path, bands, when)
    if best is None:
        return None, None
    _, path, bands, when = best
    rgb = true_color(path, gain=3.0).copy()
    if overlay_hot:
        rgb[hot_mask(bands)] = (255, 40, 200)
    return rgb, when.strftime("%Y-%m-%d")


def summarize(config: dict, cache_dir: Path, out_dir: Path) -> dict:
    """Small, committable outputs: per-site series, window and stall stats, stills."""
    import pandas as pd
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "img").mkdir(exist_ok=True)
    summary = {"generated": dt.date.today().isoformat(), "thresholds": {
        "built": f"visible mean >= {BUILT_VIS}, B11 <= {BUILT_SWIR_RATIO} x visible, NDVI < {BARE_NDVI}",
        "roof": f"built and visible mean >= {ROOF_VIS}",
        "hot": (f"Schroeder et al. 2016 OLI fire tests on B8A/B11/B12, or B11/B12 > {SATURATED} with "
                f"B12/B8A > 1.5; always B12 >= {HEAT_OVER_RED} x B04 (glint guard)")},
        "sites": {}}
    for site in config["sites"]:
        entry = {key: site.get(key) for key in ("name", "kind", "tickers", "lat", "lon", "aoi_m",
                                                  "aoi_drawn", "public_since", "events", "delivery", "windows")}
        chips = list((cache_dir / "chips" / site["id"]).glob("*.npz"))
        entry["scenes_listed"] = len(chips)
        if site["kind"] == "data_center":
            index = construction_index(site, cache_dir)
            keep = index[["date", "created", "window_scenes", "in_baseline", "built_km2", "roof_km2",
                          "new_built_km2", "new_roof_km2", "new_cleared_km2"]].copy()
            for col in ("built_km2", "roof_km2", "new_built_km2", "new_roof_km2", "new_cleared_km2"):
                keep[col.replace("_km2", "_ha")] = (keep.pop(col) * 100).round(2)
            keep.to_csv(out_dir / f"construction_{site['id']}.csv", index=False, date_format="%Y-%m-%d")
            entry["clear_days"] = int(len(index))
            stalls = stall_table(index)
            entry["stalls"] = stalls
        else:
            heat = heat_table(site, cache_dir)
            heat.to_csv(out_dir / f"heat_{site['id']}.csv", index=False, date_format="%Y-%m-%d")
            entry["clear_days"] = int(len(heat))
            entry["window_stats"] = window_stats(heat, site.get("windows", []))
            for zone in site.get("hot_zones", []):
                hot_days = heat[heat[zone["name"]] >= 1]["date"]
                last = hot_days.max()
                entry.setdefault("zones", {})[zone["name"]] = {
                    **zone, "last_hot": last.strftime("%Y-%m-%d") if len(hot_days) else None,
                    "clear_scenes_since": int((heat["date"] > last).sum()) if len(hot_days) else None,
                    "window_stats": window_stats(heat, site.get("windows", []), column=zone["name"])}
        stills = []
        for day in site.get("stills", []):
            rgb, used = still_image(site, cache_dir, day, overlay_hot=site["kind"] == "smelter")
            if rgb is None:
                continue
            name = f"{site['id']}_{used}.webp"
            Image.fromarray(rgb).save(out_dir / "img" / name, quality=88, method=6)
            stills.append({"requested": day, "date": used, "file": f"img/{name}"})
        entry["stills"] = stills
        summary["sites"][site["id"]] = entry
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    return summary


def measure_chip(path: Path) -> dict | None:
    with np.load(path) as data:
        if str(data["status"]) != "ok":
            return None
        chip = {key: data[key] for key in data.files}
    bands = reflectance(chip)
    acquired = str(chip["acquired"])
    row = {"scene": path.stem, "acquired": acquired, "created": str(chip["created"]),
           "date": acquired[:10], "baseline": str(chip["baseline"])}
    return row | construction_measures(bands) | hotspot_measures(bands)


def true_color(path: Path, gain: float = 3.2) -> np.ndarray | None:
    with np.load(path) as data:
        if str(data["status"]) != "ok":
            return None
        chip = {key: data[key] for key in data.files}
    bands = reflectance(chip)
    rgb = np.stack([bands["B04"], bands["B03"], bands["B02"]], axis=-1)
    return (np.clip(np.nan_to_num(rgb) * gain, 0, 1) ** (1 / 1.4) * 255).astype(np.uint8)


def swir_composite(path: Path) -> np.ndarray | None:
    """B12/B11/B8A false colour: hot pixels glow orange-white, vegetation is dark."""
    with np.load(path) as data:
        if str(data["status"]) != "ok":
            return None
        chip = {key: data[key] for key in data.files}
    bands = reflectance(chip)
    rgb = np.stack([bands["B12"], bands["B11"], bands["B8A"]], axis=-1)
    return (np.clip(np.nan_to_num(rgb) * 2.2, 0, 1) ** (1 / 1.3) * 255).astype(np.uint8)


# --------------------------------------------------------------------------- pipeline

def fetch_site(site: dict, cache_dir: Path, workers: int = 16, log=print) -> list[dict]:
    _configure_gdal()
    geometry = site_geometry(site)
    start = dt.date.fromisoformat(site["start"])
    end = dt.date.fromisoformat(site.get("end") or dt.date.today().isoformat())
    geometry["tile"] = resolve_tile(geometry["tile"])
    scenes = list_scenes(geometry["tile"], start, end)
    log(f"{site['id']}: tile {geometry['tile']}, {len(scenes)} scenes {start}..{end}")
    results = []
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_chip, site, scene, cache_dir): scene for scene in scenes}
        for done, future in enumerate(cf.as_completed(futures), 1):
            scene = futures[future]
            try:
                results.append({"scene": scene_name(scene)} | future.result())
            except Exception as error:  # a bad scene should not sink the site
                results.append({"scene": scene_name(scene), "status": f"error: {error}"})
            if done % 100 == 0:
                log(f"  {site['id']}: {done}/{len(scenes)}")
    return results


def site_series(site: dict, cache_dir: Path):
    import pandas as pd

    rows = [measure_chip(path) for path in sorted((cache_dir / "chips" / site["id"]).glob("*.npz"))]
    frame = pd.DataFrame([row for row in rows if row])
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values(["date", "visible_frac"], ascending=[True, False])
    # One observation per acquisition day: overlapping datastrips repeat the same pass.
    return frame.drop_duplicates("date", keep="first").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["fetch", "series", "summarize", "where"])
    parser.add_argument("--config", type=Path, default=Path("config/satellite_sites.json"))
    parser.add_argument("--cache", type=Path, default=Path("research/satellite_sites"))
    parser.add_argument("--results", type=Path, default=Path("results/satellite_sites"))
    parser.add_argument("--sites", nargs="*", help="site ids (default: all)")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    sites = [s for s in config["sites"] if not args.sites or s["id"] in args.sites]
    if args.command == "where":
        for site in sites:
            print(site["id"], site_geometry(site))
    elif args.command == "summarize":
        summary = summarize({"sites": sites}, args.cache, args.results)
        for site_id, entry in summary["sites"].items():
            print(site_id, entry["clear_days"], "clear days,", len(entry["stills"]), "stills")
    elif args.command == "fetch":
        for site in sites:
            results = fetch_site(site, args.cache, args.workers)
            counts: dict[str, int] = {}
            for row in results:
                key = row["status"] if not str(row["status"]).startswith("error") else "error"
                counts[key] = counts.get(key, 0) + 1
            print(site["id"], counts)
    else:
        out = args.cache / "series"
        out.mkdir(parents=True, exist_ok=True)
        for site in sites:
            frame = site_series(site, args.cache)
            frame.to_csv(out / f"{site['id']}.csv", index=False)
            print(site["id"], len(frame), "clear days")


if __name__ == "__main__":
    main()
