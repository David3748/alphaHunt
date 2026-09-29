#!/usr/bin/env python3
"""Extract a fixed two-hour GOES DSR sample for two California solar plants.

Public object creation and S3 modification must both precede month-end+14 days.
Old small CONUS files are read in full; Enterprise full-disk files use HTTP ranges.
No economic outcome is loaded here. Replay of saved point values is offline.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import gzip
import io
import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "results/satellite_validation/goes_solar"
SAMPLE_HOURS = (0, 2, 12, 14, 16, 18, 20, 22)
PLANTS = {"57695": {"lat": 35.383, "lon": -120.067, "lat_min": 35.25, "lat_max": 35.50,
                     "lon_min": -120.25, "lon_max": -120.00},
          "57439": {"lat": 35.323739, "lon": -119.9166, "lat_min": 35.25, "lat_max": 35.50,
                     "lon_min": -120.00, "lon_max": -119.75}}
TRANSITION = pd.Timestamp("2023-01-04T18:00:00Z")
ENTERPRISE = pd.Timestamp("2024-04-17T16:40:00Z")
NAMESPACE = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
_SESSION = None


def session():
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
    return _SESSION


def request(url: str, **kwargs):
    error = None
    for attempt in range(4):
        try:
            response = session().get(url, timeout=60, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            error = exc
            time.sleep(.5 * (attempt + 1))
    raise error


def stamp(field: str) -> pd.Timestamp:
    if not re.fullmatch(r"[sec]\d{14}", field):
        raise ValueError(f"Unexpected GOES timestamp {field}")
    return pd.Timestamp(datetime.strptime(field[1:-1], "%Y%j%H%M%S"), tz="UTC") + pd.Timedelta(milliseconds=100 * int(field[-1]))


def filename_dates(key: str) -> dict:
    fields = Path(key).stem.split("_")
    return {"scan_start": stamp(next(x for x in fields if re.fullmatch(r"s\d{14}", x))),
            "scan_end": stamp(next(x for x in fields if re.fullmatch(r"e\d{14}", x))),
            "created_at": stamp(next(x for x in fields if re.fullmatch(r"c\d{14}", x)))}


def source_for(date: pd.Timestamp) -> tuple[str, str]:
    return ("17" if date < TRANSITION else "18", "ABI-L2-DSRC" if date < ENTERPRISE else "ABI-L2-DSRF")


def cosine_solar_zenith(date: pd.Timestamp, lat: float, lon: float) -> float:
    """NOAA fractional-year solar geometry, sufficient for fixed-night zero flags."""
    hour = date.hour + date.minute / 60 + date.second / 3600
    gamma = 2 * np.pi / (366 if date.is_leap_year else 365) * (date.dayofyear - 1 + (hour - 12) / 24)
    eq = 229.18 * (.000075 + .001868 * np.cos(gamma) - .032077 * np.sin(gamma)
                  - .014615 * np.cos(2 * gamma) - .040849 * np.sin(2 * gamma))
    decl = (.006918 - .399912 * np.cos(gamma) + .070257 * np.sin(gamma)
            - .006758 * np.cos(2 * gamma) + .000907 * np.sin(2 * gamma)
            - .002697 * np.cos(3 * gamma) + .00148 * np.sin(3 * gamma))
    hour_angle = np.deg2rad((hour * 60 + eq + 4 * lon) / 4 - 180)
    latitude = np.deg2rad(lat)
    return float(np.sin(latitude) * np.sin(decl) + np.cos(latitude) * np.cos(decl) * np.cos(hour_angle))


def expected_slots(start: str, end: str) -> pd.DatetimeIndex:
    # Extra next-month 00 endpoint is needed for the final daily trapezoid.
    dates = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC") + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1), freq="2h")
    return dates[dates.hour.isin(SAMPLE_HOURS)]


def list_prefix(task: tuple[str, str, int]) -> list[dict]:
    satellite, product, year = task
    url = f"https://noaa-goes{satellite}.s3.amazonaws.com/"
    args = {"list-type": "2", "prefix": f"{product}/{year}/", "max-keys": "1000"}
    rows = []
    while True:
        response = request(url, params=args)
        root = ET.fromstring(response.content)
        for entry in root.findall("s:Contents", NAMESPACE):
            data = {child.tag.split("}")[-1]: child.text for child in entry}
            if not data["Key"].endswith(".nc"):
                continue
            dates = filename_dates(data["Key"])
            if dates["scan_start"].hour not in SAMPLE_HOURS:
                continue
            rows.append({"satellite": satellite, "product": product, "key": data["Key"],
                         "url": url + data["Key"], "size_bytes": int(data["Size"]),
                         "etag": data["ETag"].strip('"'), "s3_last_modified": pd.Timestamp(data["LastModified"]), **dates})
        token = root.findtext("s:NextContinuationToken", namespaces=NAMESPACE)
        if not token:
            break
        args["continuation-token"] = token
    return rows


def build_manifest(out: Path, start: str, end: str, workers: int = 8) -> pd.DataFrame:
    slots = expected_slots(start, end)
    needed = {(*source_for(date), date.year) for date in slots}
    gathered = []
    with ThreadPoolExecutor(max_workers=min(workers, 8)) as pool:
        for rows in pool.map(list_prefix, sorted(needed)):
            gathered.extend(rows)
            print(f"Listed archive prefix: {len(rows)} sampled-hour objects", flush=True)
    frame = pd.DataFrame(gathered)
    frame["slot"] = frame.scan_start.dt.floor("h")
    frame = frame.loc[frame.slot.isin(slots)].copy()
    frame = frame.loc[[source_for(row.slot) == (row.satellite, row.product) for row in frame.itertuples()]]
    # Availability applies to each source object's own observation month; the
    # next-month 00 endpoint also has to fit the preceding month issue below.
    frame["forecast_issue"] = frame.slot.dt.tz_localize(None) + pd.offsets.MonthEnd(0) + pd.Timedelta(days=14)
    frame["forecast_issue"] = frame.forecast_issue.dt.normalize().dt.tz_localize("UTC")
    final_endpoint = slots.max()
    frame.loc[frame.slot == final_endpoint, "forecast_issue"] = final_endpoint - pd.Timedelta(days=1) + pd.Timedelta(days=14)
    frame["available_at"] = frame[["created_at", "s3_last_modified"]].max(axis=1)
    frame["available_by_issue"] = frame.available_at < frame.forecast_issue
    frame = frame.sort_values(["slot", "scan_start", "created_at", "s3_last_modified"])
    # Earliest scan among files that actually existed before the decision.
    chosen = frame.loc[frame.available_by_issue].drop_duplicates("slot", keep="first")
    unavailable = frame.loc[~frame.slot.isin(chosen.slot)].drop_duplicates("slot", keep="first")
    chosen = pd.concat([chosen, unavailable]).sort_values("slot")
    chosen["daylight_any_plant"] = [any(cosine_solar_zenith(date, p["lat"], p["lon"]) > 0 for p in PLANTS.values()) for date in chosen.scan_start]
    out.mkdir(parents=True, exist_ok=True)
    chosen.to_csv(out / "object_manifest.csv", index=False)
    print(f"Selected {len(chosen)} objects; {int(chosen.available_by_issue.sum())} timely, {int(chosen.daylight_any_plant.sum())} daylight", flush=True)
    return chosen


def number(value) -> float:
    return float(np.asarray(value).ravel()[0])


def snapshot_path(out: Path, name: str) -> Path:
    path = out / name
    return path if path.exists() else out / (name + ".gz")


def read_snapshot_lines(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as source:
        yield from source


def compact_snapshots(out: Path) -> None:
    for name in ("object_manifest.csv", "point_values.jsonl", "point_values.csv"):
        path = out / name
        if path.exists():
            # Deterministic gzip bytes support stable offline snapshot hashes.
            (out / (name + ".gz")).write_bytes(gzip.compress(path.read_bytes(), mtime=0))
            path.unlink()


def decode_numeric(dataset, selection) -> tuple[np.ndarray, np.ndarray]:
    raw = np.asarray(dataset[selection])
    fill = np.asarray(dataset.attrs.get("_FillValue", [np.nan])).ravel()[0]
    valid = raw != fill
    if dataset.attrs.get("_Unsigned", b"") in (b"true", "true") and raw.dtype.kind == "i":
        raw = raw.view(np.dtype(f"u{raw.dtype.itemsize}"))
    values = raw.astype(float) * number(dataset.attrs.get("scale_factor", [1])) + number(dataset.attrs.get("add_offset", [0]))
    return values, valid


def extract_hdf(file) -> list[dict]:
    import pyproj
    if "lat" in file:
        lat, _ = decode_numeric(file["lat"], slice(None))
        lon, _ = decode_numeric(file["lon"], slice(None))
        selections = []
        for code, site in PLANTS.items():
            iy = int(np.argmin(abs(lat - (site["lat_min"] + site["lat_max"]) / 2)))
            ix = int(np.argmin(abs(lon - (site["lon_min"] + site["lon_max"]) / 2)))
            if abs(lat[iy] - (site["lat_min"] + site["lat_max"]) / 2) > .126 or abs(lon[ix] - (site["lon_min"] + site["lon_max"]) / 2) > .126:
                raise ValueError("Site outside archived CONUS grid")
            selections.append((code, (slice(iy, iy + 1), slice(ix, ix + 1)), np.ones((1, 1), dtype=bool), float(lat[iy]), float(lon[ix])))
        grid = "equal_angle_0.25deg"
    else:
        attrs = file["goes_imager_projection"].attrs
        h = number(attrs["perspective_point_height"])
        projection = pyproj.Proj(proj="geos", h=h, lon_0=number(attrs["longitude_of_projection_origin"]),
                                 a=number(attrs["semi_major_axis"]), b=number(attrs["semi_minor_axis"]), sweep="x")
        x, _ = decode_numeric(file["x"], slice(None))
        y, _ = decode_numeric(file["y"], slice(None))
        selections = []
        for code, site in PLANTS.items():
            cx, cy = projection([site["lon_min"], site["lon_max"], site["lon_min"], site["lon_max"]],
                                [site["lat_min"], site["lat_min"], site["lat_max"], site["lat_max"]])
            ix = np.flatnonzero((x >= min(cx) / h) & (x <= max(cx) / h))
            iy = np.flatnonzero((y >= min(cy) / h) & (y <= max(cy) / h))
            if not len(ix) or not len(iy):
                raise ValueError("Site outside geostationary fixed grid")
            xs, ys = slice(ix.min(), ix.max() + 1), slice(iy.min(), iy.max() + 1)
            xx, yy = np.meshgrid(x[xs] * h, y[ys] * h)
            lon, lat = projection(xx, yy, inverse=True)
            within = ((lat >= site["lat_min"]) & (lat < site["lat_max"]) &
                      (lon >= site["lon_min"]) & (lon < site["lon_max"]))
            selections.append((code, (ys, xs), within, float(np.mean(lat[within])), float(np.mean(lon[within]))))
        grid = "enterprise_2km_averaged_to_fixed_0.25deg_cell"
    rows = []
    for code, selection, within, latitude, longitude in selections:
        dsr, valid = decode_numeric(file["DSR"], selection)
        dqf, qvalid = decode_numeric(file["DQF"], selection)
        good = within & valid & qvalid & (dqf <= 1) & (dsr >= 0) & (dsr <= 1500)
        coverage = float(good.sum() / within.sum()) if within.any() else 0.
        rows.append({"plant_code": code, "dsr_w_m2": float(dsr[good].mean()) if good.any() and coverage >= .9 else None,
                     "n_cell_pixels": int(within.sum()), "n_valid_pixels": int(good.sum()),
                     "good_quality_fraction": float((good & (dqf == 0)).sum() / good.sum()) if good.any() else None,
                     "pixel_coverage": coverage, "grid": grid,
                     "pixel_mean_lat": latitude, "pixel_mean_lon": longitude})
    return rows


def extract_object(row: dict) -> dict:
    import h5py
    import fsspec
    result = {"key": row["key"], "url": row["url"], "slot": row["slot"],
              "scan_start": row["scan_start"], "created_at": row["created_at"],
              "s3_last_modified": row["s3_last_modified"], "available_at": row["available_at"],
              "etag": row["etag"], "size_bytes": int(row["size_bytes"])}
    for attempt in range(3):
        try:
            if int(row["size_bytes"]) < 1_000_000:
                data = request(row["url"]).content
                with h5py.File(io.BytesIO(data)) as file:
                    result["values"] = extract_hdf(file)
                    result["netcdf_created_at"] = file.attrs["date_created"].decode()
                    result["production_data_source"] = file.attrs["production_data_source"].decode()
                result["sha256_full_small_file"] = hashlib.sha256(data).hexdigest()
                result["bytes_read"] = len(data)
            else:
                with fsspec.open(row["url"], mode="rb", block_size=16384, cache_type="bytes") as obj:
                    with h5py.File(obj) as file:
                        result["values"] = extract_hdf(file)
                        result["netcdf_created_at"] = file.attrs["date_created"].decode()
                        result["production_data_source"] = file.attrs["production_data_source"].decode()
                    result["bytes_read"] = obj.cache.total_requested_bytes
                result["sha256_full_small_file"] = None
            if abs((pd.Timestamp(result["netcdf_created_at"]) - pd.Timestamp(row["created_at"])).total_seconds()) > 1:
                raise ValueError("Filename and netCDF creation timestamps disagree")
            if result["production_data_source"].lower() != "realtime":
                raise ValueError("Expected operational real-time production")
            result["error"] = None
            return result
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            if attempt < 2:
                time.sleep(attempt + 1)
    return result


def retrieve_points(out: Path, manifest: pd.DataFrame, workers: int = 12) -> None:
    path = out / "point_values.jsonl"
    completed = set()
    existing = snapshot_path(out, "point_values.jsonl")
    if existing.exists():
        for line in read_snapshot_lines(existing):
            row = json.loads(line)
            if not row.get("error"):
                completed.add(row["key"])
        if existing.suffix == ".gz":
            path.write_bytes(gzip.decompress(existing.read_bytes()))
    eligible = manifest.loc[manifest.available_by_issue & manifest.daylight_any_plant]
    jobs = []
    for row in eligible.to_dict("records"):
        if row["key"] in completed:
            continue
        jobs.append({key: value.isoformat() if isinstance(value, (pd.Timestamp, datetime)) else value for key, value in row.items()})
    print(f"Point extraction: {len(jobs)} remaining of {len(eligible)} daylight objects", flush=True)
    started = time.time()
    with path.open("a") as dest, ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(extract_object, row) for row in jobs]
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            dest.write(json.dumps(result) + "\n")
            if i % 100 == 0 or i == len(jobs):
                dest.flush()
                print(f"Extracted {i}/{len(jobs)} in {time.time()-started:.1f}s; latest_error={result.get('error')}", flush=True)


def aggregate(out: Path, start: str, end: str) -> pd.DataFrame:
    by_key = {}
    for line in read_snapshot_lines(snapshot_path(out, "point_values.jsonl")):
        row = json.loads(line)
        if row["key"] not in by_key or not row.get("error"):
            by_key[row["key"]] = row
    rows = []
    for record in by_key.values():
        if record.get("error"):
            continue
        for item in record["values"]:
            rows.append({**{key: record[key] for key in ("key", "slot", "scan_start", "created_at", "s3_last_modified", "available_at")}, **item})
    points = pd.DataFrame(rows)
    for col in ("slot", "scan_start", "created_at", "s3_last_modified", "available_at"):
        points[col] = pd.to_datetime(points[col], utc=True, format="mixed")
    points.to_csv(out / "point_values.csv", index=False)
    first = pd.Timestamp(start, tz="UTC").normalize()
    last = (pd.Timestamp(end, tz="UTC") + pd.offsets.MonthEnd(0)).normalize()
    slots = pd.date_range(first, last + pd.Timedelta(days=1), freq="2h")
    daily_rows, monthly_rows = [], []
    for code, site in PLANTS.items():
        plant = points.loc[points.plant_code == code].sort_values(["slot", "available_at"]).drop_duplicates("slot").set_index("slot")
        for month_end in pd.date_range(first, last, freq="ME"):
            month_start = month_end.replace(day=1)
            issue = month_end + pd.Timedelta(days=14)
            month_slots = pd.date_range(month_start, month_end + pd.Timedelta(days=1), freq="2h")
            values, times, expected, valid_slots, avail = {}, {}, 0, 0, []
            for slot in month_slots:
                daylight = cosine_solar_zenith(slot, site["lat"], site["lon"]) > 0
                if not daylight:
                    values[slot] = 0.
                    times[slot] = slot
                    continue
                expected += 1
                values[slot] = np.nan
                times[slot] = slot
                if slot in plant.index:
                    observation = plant.loc[slot]
                    if observation.available_at < issue and pd.notna(observation.dsr_w_m2):
                        values[slot] = observation.dsr_w_m2
                        times[slot] = observation.scan_start
                        valid_slots += 1
                        avail.append(observation.available_at)
            complete_days = []
            for day in pd.date_range(month_start, month_end, freq="D"):
                grid = pd.date_range(day, day + pd.Timedelta(days=1), freq="2h")
                irradiance = np.array([values[t] for t in grid])
                hours = np.array([(times[t] - day).total_seconds() / 3600 for t in grid])
                value = float(np.trapezoid(irradiance, hours) / 1000) if np.isfinite(irradiance).all() else np.nan
                daily_rows.append({"plant_code": code, "date": day.date().isoformat(), "ghi_kwh_m2_day": value,
                                   "complete_day": bool(np.isfinite(value))})
                if np.isfinite(value):
                    complete_days.append(value)
            coverage = valid_slots / expected if expected else 0
            monthly_rows.append({"plant_code": code, "date": month_end.date().isoformat(),
                                 "ghi_kwh_m2_day": float(np.mean(complete_days)) if complete_days else np.nan,
                                 "eligible": coverage >= .9 and bool(complete_days), "daylight_coverage": coverage,
                                 "expected_daylight_slots": expected, "valid_daylight_slots": valid_slots,
                                 "complete_days": len(complete_days), "calendar_days": month_end.days_in_month,
                                 "complete_day_fraction": len(complete_days) / month_end.days_in_month,
                                 "forecast_at": issue.isoformat(), "last_source_available_at": max(avail).isoformat() if avail else None})
    pd.DataFrame(daily_rows).to_csv(out / "daily_irradiance.csv", index=False)
    monthly = pd.DataFrame(monthly_rows)
    monthly.to_csv(out / "monthly_irradiance.csv", index=False)
    report = {"start": start, "end": end, "plants": PLANTS, "sample_hours_utc": SAMPLE_HOURS,
              "n_source_objects": len(by_key), "n_errors": sum(bool(r.get("error")) for r in by_key.values()),
              "estimated_downloaded_bytes": sum(r.get("bytes_read", 0) for r in by_key.values()),
              "n_plant_months": len(monthly), "n_eligible_plant_months": int(monthly.eligible.sum()),
              "creation_and_s3_modification_gated": True,
              "s3_modification_is_current_object_timestamp_not_complete_historical_version_log": True,
              "monthly_estimator": "Mean of complete-day two-hour trapezoidal integrals, using actual scan timestamps and physical nighttime zeros; no imputation of missing daylight; require at least90%expecteddaylight slots.",
              "source_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "snapshot_sha256": {snapshot_path(out, name).name: hashlib.sha256(snapshot_path(out, name).read_bytes()).hexdigest()
                                   for name in ("object_manifest.csv", "point_values.jsonl", "monthly_irradiance.csv")}}
    (out / "extraction_summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return monthly


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--reuse-manifest", action="store_true")
    parser.add_argument("--compact", action="store_true", help="Compress bulky source snapshots after extraction")
    args = parser.parse_args()
    if args.fetch:
        if args.reuse_manifest:
            manifest = pd.read_csv(snapshot_path(args.out, "object_manifest.csv"))
        else:
            manifest = build_manifest(args.out, args.start, args.end, args.workers)
        retrieve_points(args.out, manifest, args.workers)
    monthly = aggregate(args.out, args.start, args.end)
    if args.compact:
        compact_snapshots(args.out)
        report = json.loads((args.out / "extraction_summary.json").read_text())
        report["snapshot_sha256"] = {snapshot_path(args.out, name).name: hashlib.sha256(snapshot_path(args.out, name).read_bytes()).hexdigest()
                                     for name in ("object_manifest.csv", "point_values.jsonl", "monthly_irradiance.csv")}
        (args.out / "extraction_summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(monthly.to_string(index=False))
