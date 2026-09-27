"""Frozen national CPC gauge controls; default is an integrity-checked offline rebuild.

Explicit --refresh downloads only August/September regional grid data, using
four processes because libnetcdf is not thread safe. No crop labels are read.
"""
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import shutil

import h5py
import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
FREEZE_SHA = "30dea1d528c27adbeee1057a612f364313b3b2ab51a3be46f49fbfad164a30da"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_freeze():
    if digest(OUT / "geography_freeze.json") != FREEZE_SHA:
        raise ValueError("Frozen geography changed")
    freeze = json.loads((OUT / "geography_freeze.json").read_text())
    for country in freeze["countries"].values():
        if digest(OUT / country["mask_file"]) != country["mask_sha256"]:
            raise ValueError("Frozen national mask changed")
    if digest(OUT / "metadata/ZM_ZW_boundaries.geojson") != freeze["geometry_subset_sha256"]:
        raise ValueError("Boundary snapshot changed")
    return freeze


def fetch_year(year):
    import netCDF4

    freeze = load_freeze()
    path = OUT / f"inputs/cpc_augsep_{year}.h5"
    sidecar = path.with_suffix(".source.json")
    if path.exists():
        source = json.loads(sidecar.read_text())
        if digest(path) != source["sha256"]:
            raise ValueError("Cached CPC source checksum mismatch")
        return source
    url = freeze["source_url_pattern"].format(year=year)
    ys = slice(min(c["lat_indices"][0] for c in freeze["countries"].values()),
               max(c["lat_indices"][1] for c in freeze["countries"].values()) + 1)
    xs = slice(min(c["lon_indices"][0] for c in freeze["countries"].values()),
               max(c["lon_indices"][1] for c in freeze["countries"].values()) + 1)
    with netCDF4.Dataset(url) as remote:
        lat, lon = np.asarray(remote.variables["lat"][:]), np.asarray(remote.variables["lon"][:])
        if hashlib.sha256(lat.tobytes()).hexdigest() != freeze["lat_sha256"] or hashlib.sha256(lon.tobytes()).hexdigest() != freeze["lon_sha256"]:
            raise ValueError("CPC source coordinate grid changed")
        times = np.asarray(remote.variables["time"][:])
        time_units = remote.variables["time"].units
        unit, origin = time_units.split(" since ")
        dates = pd.Timestamp(origin) + pd.to_timedelta(times, unit={"days": "D", "hours": "h"}[unit])
        ti = np.flatnonzero((dates >= pd.Timestamp(year, 8, 1)) & (dates <= pd.Timestamp(year, 9, 30)))
        expected = pd.date_range(f"{year}-08-01", f"{year}-09-30")
        if not np.array_equal(dates[ti].to_numpy(), expected.to_numpy()):
            raise ValueError("Missing or duplicated source calendar dates")
        variable = remote.variables["precip"]
        if variable.units != "mm":
            raise ValueError("Unexpected precipitation units")
        values = np.ma.filled(variable[slice(ti[0], ti[-1] + 1), ys, xs], np.nan)
        with h5py.File(path, "w") as local:
            local.create_dataset("lat", data=lat[ys])
            local.create_dataset("lon", data=lon[xs])
            local.create_dataset("time", data=times[ti]).attrs["units"] = time_units
            local.create_dataset("precip", data=values, compression="gzip").attrs["units"] = variable.units
        source = {"path": str(path.relative_to(OUT)), "url": url, "sha256": digest(path),
                  "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                  "variable_units": variable.units, "variable_long_name": variable.long_name,
                  "source_title": getattr(remote, "title", None), "source_history": getattr(remote, "history", None),
                  "indices": {"time": [int(ti[0]), int(ti[-1])], "lat": [ys.start, ys.stop - 1], "lon": [xs.start, xs.stop - 1]},
                  "storage": "Lossless numeric OPeNDAP subset in HDF5, August/September only; national mask applied offline."}
    sidecar.write_text(json.dumps(source, indent=2) + "\n")
    return source


def aggregate(values, lat, mask, dates):
    """A missing/low-coverage day invalidates its month; outside-mask cells never count."""
    values = values.astype(float).copy()
    values[(values < 0) | (values > 10000)] = np.nan
    valid = np.isfinite(values) & mask[None, :, :]
    weights = np.cos(np.deg2rad(lat))[None, :, None]
    denominator = np.sum(weights * valid, axis=(1, 2))
    weighted = np.where(valid, values, 0) * weights
    daily_mean = np.divide(weighted.sum(axis=(1, 2)), denominator,
                           out=np.full(len(values), np.nan), where=denominator > 0)
    counts = valid.sum(axis=(1, 2))
    daily = pd.DataFrame({"date": dates, "precip_mm": daily_mean, "valid_cells": counts})
    ncells = int(mask.sum())
    if ncells == 0:
        raise ValueError("Empty country mask")
    rows = []
    for (year, month), part in daily.groupby([daily.date.dt.year, daily.date.dt.month]):
        expected = pd.date_range(pd.Timestamp(year, month, 1), periods=pd.Timestamp(year, month, 1).days_in_month)
        calendar_complete = np.array_equal(part.date.to_numpy(), expected.to_numpy())
        good = calendar_complete and bool(part.valid_cells.ge(.9 * ncells).all()) and bool(part.precip_mm.notna().all())
        rows.append({"year": year, "month": month, "precip_mm": float(part.precip_mm.sum()) if good else np.nan,
                     "n_days": len(part), "minimum_valid_cells": int(part.valid_cells.min()), "grid_cells": ncells,
                     "eligible": good})
    return rows, daily


def rebuild():
    freeze = load_freeze()
    manifest, rows, daily_rows = [], {c: [] for c in freeze["countries"]}, {c: [] for c in freeze["countries"]}
    for year in range(1982, 2025):
        path = OUT / f"inputs/cpc_augsep_{year}.h5"
        source = json.loads(path.with_suffix(".source.json").read_text())
        if digest(path) != source["sha256"]:
            raise ValueError(f"CPC source checksum mismatch: {path}")
        manifest.append(source)
        with h5py.File(path) as raw:
            raw_lat, raw_lon = raw["lat"][:], raw["lon"][:]
            unit, origin = raw["time"].attrs["units"].split(" since ")
            dates = pd.Timestamp(origin) + pd.to_timedelta(raw["time"][:], unit={"days": "D", "hours": "h"}[unit])
            if not np.array_equal(dates.to_numpy(), pd.date_range(f"{year}-08-01", f"{year}-09-30").to_numpy()):
                raise ValueError("Incomplete snapshot dates")
            for country, config in freeze["countries"].items():
                with np.load(OUT / config["mask_file"]) as saved:
                    lat, lon, mask = saved["lat"], saved["lon"], saved["mask"]
                yi, xi = np.flatnonzero(np.isin(raw_lat, lat)), np.flatnonzero(np.isin(raw_lon, lon))
                if not np.array_equal(raw_lat[yi], lat) or not np.array_equal(raw_lon[xi], lon):
                    raise ValueError("Country-mask coordinates do not match snapshot")
                values = raw["precip"][:][:, yi][:, :, xi]
                monthly, daily = aggregate(values, lat, mask, dates)
                rows[country].extend(monthly)
                daily_rows[country].append(daily)
    quality = {"source": "CPC Unified gauge-based daily precipitation; interpolated field, not station counts", "countries": {}}
    for country in rows:
        frame = pd.DataFrame(rows[country])
        path = OUT / f"{country}_ground_augsep.csv"
        frame.to_csv(path, index=False)
        daily_path = OUT / f"{country}_daily_quality.csv"
        daily = pd.concat(daily_rows[country], ignore_index=True)
        daily.to_csv(daily_path, index=False)
        manifest.extend({"path": p.name, "sha256": digest(p), "method": "Frozen national mask; cosine area mean; complete monthly sum and >=90% per-day mask coverage"} for p in (path, daily_path))
        quality["countries"][country] = {"months": len(frame), "eligible_months": int(frame.eligible.sum()), "mask_cells": freeze["countries"][country]["mask_cells"],
                                          "ineligible_months": frame.loc[~frame.eligible, ["year", "month", "minimum_valid_cells"]].to_dict("records")}
    manifest.append({"path": "geography_freeze.json", "sha256": FREEZE_SHA})
    manifest.append({"path": Path(__file__).name, "sha256": digest(__file__)})
    (OUT / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (OUT / "source_quality.json").write_text(json.dumps(quality, indent=2) + "\n")
    print(json.dumps(quality, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    load_freeze()
    if args.refresh:
        (OUT / "inputs").mkdir(exist_ok=True)
        with ProcessPoolExecutor(max_workers=4) as pool:
            for i, source in enumerate(pool.map(fetch_year, range(1982, 2025)), 1):
                if i % 5 == 0:
                    print(f"Downloaded {i}/43 source years", flush=True)
    rebuild()


if __name__ == "__main__":
    main()
