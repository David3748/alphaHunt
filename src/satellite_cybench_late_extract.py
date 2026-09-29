"""Prepare only the prespecified secondary September15 CY-Bench horizon.

Default rebuild is offline. --prepare requires primary frozen source inputs and
the previously fetched raw members; no new network fetch or model fitting.
"""
from __future__ import annotations

import argparse
import calendar
import json
from pathlib import Path
import shutil

import pandas as pd

import satellite_cybench_extract as core

OUT = Path("results/satellite_validation/cybench_maize_late")
MONTHS = {month: calendar.month_abbr[month].lower() for month in range(4, 9)}


def prepare(out: Path, primary: Path, raw_dir: Path) -> None:
    if not (out / "preparation_addendum.json").exists():
        raise ValueError("Secondary horizon must be frozen before preparation")
    source = json.loads((primary / "source_manifest.json").read_text())
    for name, expected in source["compact_input_sha256"].items():
        if core.sha256(primary / name) != expected:
            raise ValueError(f"Primary compact input changed: {name}")
        if name not in ("ndvi_observations.csv.gz", "monthly_weather_statistics.csv.gz"):
            shutil.copyfile(primary / name, out / name)
    for base in ("ndvi", "meteo"):
        name = f"{base}_maize_US.csv"
        original = next(member for member in source["members"] if member["name"].endswith("/" + name))
        if core.sha256(raw_dir / name) != original["raw_sha256"]:
            raise ValueError(f"Original raw source changed: {name}")
    for name in ("DATA_NOTICE.txt", "EUPL-1.2.txt"):
        shutil.copyfile(primary / name, out / name)
    with (out / "DATA_NOTICE.txt").open("a") as f:
        f.write("\nSecondary horizon modification, frozen before either model fit: add August observations and issue September15 noon under the same complete-composite14-day buffer and coverage rules.\n")
    locations = pd.read_csv(primary / "locations.csv")
    ids = set(locations.adm_id)
    ndvi = core.prepare_ndvi(pd.read_csv(raw_dir / "ndvi_maize_US.csv"), months=MONTHS, issue_month=9)
    core.write_csv(ndvi[ndvi.adm_id.isin(ids)], out / "ndvi_observations.csv.gz")
    print("Aggregating fixed April-August weather from verified cached raw source", flush=True)
    weather = core.aggregate_weather(raw_dir / "meteo_maize_US.csv", ids, months=MONTHS)
    core.write_csv(weather, out / "monthly_weather_statistics.csv.gz")
    source["primary_source_manifest_sha256"] = core.sha256(primary / "source_manifest.json")
    source["secondary_horizon"] = "September15 noon; same fixed rules, includes April-August."
    for name in source["compact_input_sha256"]:
        source["compact_input_sha256"][name] = core.sha256(out / name)
    (out / "source_manifest.json").write_text(json.dumps(source, indent=2) + "\n")
    protocol = {**core.PROTOCOL, "issue": "September15 12:00UTC", "issue_month": 9,
                "months": list(MONTHS), "role": "Prespecified secondary horizon; always reported with primary.",
                "weather": core.PROTOCOL["weather"].replace("July31", "August31"),
                "frozen_addendum_sha256": core.sha256(out / "preparation_addendum.json")}
    (out / "preparation_protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=OUT)
    p.add_argument("--primary", type=Path, default=core.OUT)
    p.add_argument("--raw-dir", type=Path, default=core.RAW)
    p.add_argument("--prepare", action="store_true")
    a = p.parse_args()
    if a.prepare:
        prepare(a.output, a.primary, a.raw_dir)
    data = core.rebuild(a.output)
    print(f"Secondary horizon: {len(data):,} rows, {data.features_eligible.sum():,} eligible; no outcomes scored.")


if __name__ == "__main__":
    main()
