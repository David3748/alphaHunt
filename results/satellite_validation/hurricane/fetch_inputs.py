"""Explicit refresh of frozen OISST boxes and independent NHC hurricane records."""
from __future__ import annotations
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import h5py
import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
NCSS = "https://psl.noaa.gov/thredds/ncss/grid/Datasets/noaa.oisst.v2.highres/sst.mon.mean.nc"
BOXES = {"mdr": {"north": 20, "south": 10, "west": 280, "east": 340},
         "nino34": {"north": 5, "south": -5, "west": 190, "east": 240}}
HURDAT = "https://www.nhc.noaa.gov/data/hurdat/hurdat2-1851-2025-091226.txt"


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_box(path, bounds):
    with h5py.File(path) as source:
        lat, lon = source["lat"][:], source["lon"][:]
        lat_keep = (lat >= bounds["south"]) & (lat <= bounds["north"])
        lon_keep = (lon >= bounds["west"]) & (lon <= bounds["east"])
        values = source["sst"][:].astype(float)[:, lat_keep][:, :, lon_keep]
        values[(values < -3) | (values > 45)] = np.nan
        units = str(source["time"].attrs["units"][0])
        if not units.startswith("days since "):
            raise ValueError("Unexpected time units")
        dates = pd.Timestamp(units.removeprefix("days since ")) + pd.to_timedelta(source["time"][:], unit="D")
        if len(dates) != 44 or set(dates.year) != set(range(1982, 2026)) or not (dates.month == 6).all():
            raise ValueError("Unexpected June-only time coverage")
        weights = np.cos(np.deg2rad(lat[lat_keep]))[None, :, None]
        valid = np.isfinite(values)
        denominator = np.sum(valid * weights, axis=(1, 2))
        if (denominator <= 0).any():
            raise ValueError("No valid ocean cells")
        mean = np.nansum(values * weights, axis=(1, 2)) / denominator
        return pd.DataFrame({"year": dates.year, "sst_c": mean, "ocean_cells": valid.sum(axis=(1, 2)),
                             "source_month": dates.strftime("%Y-%m-%d")})


def main():
    assert (ROOT / "protocol.json").exists(), "Freeze protocol first"
    manifest = []
    output = None
    for name, bounds in BOXES.items():
        params = {"var": "sst", **bounds, "horizStride": 1, "time_start": "1982-06-01T00:00:00Z",
                  "time_end": "2025-06-01T00:00:00Z", "timeStride": 12, "accept": "netcdf4"}
        response = requests.get(NCSS, params=params, timeout=120)
        response.raise_for_status()
        path = ROOT / f"inputs/oisst_june_{name}.nc"
        path.write_bytes(response.content)
        manifest.append({"name": name, "url": response.url, "path": str(path.relative_to(ROOT)),
                         "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "sha256": file_hash(path),
                         "http_last_modified": response.headers.get("Last-Modified"), "bounds": bounds})
        frame = read_box(path, bounds).rename(columns={"sst_c": f"{name}_sst_c", "ocean_cells": f"{name}_ocean_cells"})
        output = frame if output is None else output.merge(frame.drop(columns="source_month"), on="year", validate="one_to_one")
    path = ROOT / "inputs/june_sst.csv"
    output.to_csv(path, index=False, float_format="%.12g")
    manifest.append({"name": "derived_june_sst", "path": str(path.relative_to(ROOT)), "sha256": file_hash(path),
                     "method": "Equal-longitude cell area weighted bycos(latitude); exclude cellcentersoutsidefixedboxes; exclude oceanmissingvalues; Juneonly."})
    urls = {
        "hurdat2_1851_2025.txt.gz": HURDAT,
        "oisst_metadata.html": "https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.ncdc:C01606",
        "oisst_product.html": "https://www.ncei.noaa.gov/products/optimum-interpolation-sst",
        "nhc_data_page.html": "https://www.nhc.noaa.gov/data/",
        "ace_definition.html": "https://www.cpc.ncep.noaa.gov/products/outlooks/hurricane2020/August/Background.html",
        "mechanism.html": "https://www.aoml.noaa.gov/general/project/hrdls3.html",
    }
    for name, url in urls.items():
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        path = ROOT / ("inputs" if name.endswith("gz") else "metadata") / name
        data = gzip.compress(response.content, mtime=0) if name.endswith("gz") else response.content
        path.write_bytes(data)
        manifest.append({"name": name, "url": response.url, "path": str(path.relative_to(ROOT)),
                         "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "sha256": file_hash(path),
                         "uncompressed_download_sha256": hashlib.sha256(response.content).hexdigest(),
                         "http_last_modified": response.headers.get("Last-Modified")})
    (ROOT / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Retrieved44JuneSSTyears andNHCfullrecords; derivedcolumns: {list(output.columns)}")


if __name__ == "__main__":
    main()
