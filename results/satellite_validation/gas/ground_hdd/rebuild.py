"""Rebuild preselected CPC utility-gas-weighted monthly HDD, offline by default."""
from __future__ import annotations

import argparse
import calendar
import concurrent.futures
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent
BASE = "https://ftp.cpc.ncep.noaa.gov/htdocs/degree_days/weighted/daily_data"
YEARS = range(1990, 2026)
DOCUMENTS = {
    "cpc_degree_days.html": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/cdus/degree_days/",
    "cpc_explanation.html": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/cdus/degree_days/ddayexp.shtml",
    "latest_directory.html": BASE + "/latest/",
    "latest_utility_gas.txt": BASE + "/latest/UtilityGas.Heating.txt",
    "archive_directory.html": BASE + "/",
    "cpc_temperature_method.html": "https://www.cpc.ncep.noaa.gov/soilmst/t.shtml",
}


def fetch_one(item: tuple[str, str]) -> dict:
    relative, url = item
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read()
        modified = response.headers.get("Last-Modified")
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": relative, "url": url, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "http_last_modified": modified,
            "retrieved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}


def parse_conus(data: str) -> list[tuple[dt.date, int]]:
    lines = data.splitlines()
    if "Weights: Utility Gas" not in lines[:3]:
        raise ValueError("Unexpected weighting")
    reader = csv.reader(lines[3:], delimiter="|")
    header = next(reader)
    if header[0] != "Region":
        raise ValueError("Unexpected header")
    selected = [row for row in reader if row and row[0] == "CONUS"]
    if len(selected) != 1 or len(selected[0]) != len(header):
        raise ValueError("Missing or malformed CONUS row")
    result = [(dt.datetime.strptime(day, "%Y%m%d").date(), int(value))
              for day, value in zip(header[1:], selected[0][1:])]
    if len({day for day, _ in result}) != len(result) or any(value < 0 for _, value in result):
        raise ValueError("Duplicate day or invalid HDD")
    return result


def rebuild() -> dict:
    monthly = []
    daily_count = 0
    for year in YEARS:
        data = parse_conus((ROOT / f"raw/{year}_UtilityGas.Heating.txt").read_text())
        expected = {dt.date(year, month, day) for month in range(1, 13)
                    for day in range(1, calendar.monthrange(year, month)[1] + 1)}
        if {day for day, _ in data} != expected:
            raise ValueError(f"Incomplete daily coverage for {year}")
        daily_count += len(data)
        for month in range(1, 13):
            hdd = sum(value for day, value in data if day.month == month)
            monthly.append({"date": f"{year}-{month:02d}-01", "hdd": hdd})
    with (ROOT / "monthly_hdd.csv").open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=["date", "hdd"])
        writer.writeheader()
        writer.writerows(monthly)
    latest = parse_conus((ROOT / "metadata/latest_utility_gas.txt").read_text())
    return {"monthly_rows": len(monthly), "daily_observations": daily_count,
            "start": monthly[0]["date"], "end": monthly[-1]["date"],
            "latest_feed_last_observation": max(day for day, _ in latest).isoformat(),
            "monthly_sha256": hashlib.sha256((ROOT / "monthly_hdd.csv").read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="Explicitly refresh official source snapshots")
    args = parser.parse_args()
    if args.fetch:
        items = [(f"raw/{year}_UtilityGas.Heating.txt", f"{BASE}/{year}/UtilityGas.Heating.txt") for year in YEARS]
        items += [(f"metadata/{name}", url) for name, url in DOCUMENTS.items()]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            sources = list(pool.map(fetch_one, items))
        (ROOT / "source_manifest.json").write_text(json.dumps(sources, indent=2) + "\n")
    else:
        for source in json.loads((ROOT / "source_manifest.json").read_text()):
            digest = hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest()
            if digest != source["sha256"]:
                raise ValueError(f"Snapshot checksum mismatch: {source['path']}")
    result = rebuild()
    (ROOT / "coverage.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
