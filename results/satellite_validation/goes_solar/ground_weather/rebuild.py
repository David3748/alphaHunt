#!/usr/bin/env python3
"""Offline NOAA GHCN-Daily weather extraction; never reads generation labels.

The primary comparator chooses the closest qualifying station per element.
The closest ASOS airport and all-network station are retained as sensitivities.
"""
from __future__ import annotations

import calendar
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ELEMENTS = ("TMAX", "TMIN", "PRCP")


def parse_daily(path: Path) -> pd.DataFrame:
    rows = []
    content = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    for line in content.decode().splitlines():
        if len(line) != 269:
            raise ValueError("GHCND line must contain 269 fixed-width characters")
        year, month, element = int(line[11:15]), int(line[15:17]), line[17:21]
        if year < 2015 or element not in ELEMENTS:
            continue
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            field = line[21 + 8 * (day - 1):21 + 8 * day]
            raw, measurement, quality, source = int(field[:5]), field[5], field[6], field[7]
            valid = raw != -9999 and quality == " "
            rows.append({"date": pd.Timestamp(year, month, day), "station_id": line[:11],
                         "element": element, "value": raw / 10 if valid else np.nan,
                         "raw_value": raw, "measurement_flag": measurement.strip(),
                         "quality_flag": quality.strip(), "source_flag": source.strip(), "valid": valid})
    frame = pd.DataFrame(rows)
    if frame.duplicated(["date", "element"]).any():
        raise ValueError("Duplicate date/element observation")
    if frame.loc[frame.element.eq("PRCP"), "value"].dropna().lt(0).any():
        raise ValueError("Negative precipitation")
    return frame.sort_values(["date", "element"])


def monthly(daily: pd.DataFrame) -> pd.DataFrame:
    station_by_element = {}
    for element in ELEMENTS:
        stations = daily.loc[daily.element.eq(element), "station_id"].unique()
        if len(stations) != 1:
            raise ValueError("Each element must have exactly one selected station")
        station_by_element[element] = stations[0]
    wide = daily.pivot(index="date", columns="element", values="value")
    # Full calendar denominator prevents missing observations becoming zero rain.
    end = wide.index.max() + pd.offsets.MonthEnd(0)
    wide = wide.reindex(pd.date_range("2015-01-01", end, freq="D"))
    for element in ELEMENTS:
        if element not in wide:
            wide[element] = np.nan
    wide["TMEAN"] = (wide.TMAX + wide.TMIN) / 2
    wide["DTR"] = wide.TMAX - wide.TMIN
    rows = []
    for month, frame in wide.groupby(wide.index.to_period("M")):
        days = month.days_in_month
        counts = frame[list(ELEMENTS)].count()
        pair_complete = frame.TMEAN.count() / days >= .9
        complete = bool((counts / days >= .9).all() and pair_complete)
        values = {"date": month.to_timestamp(), "station_id": ";".join(sorted(daily.station_id.unique())),
                  "tmax_station_id": station_by_element["TMAX"],
                  "tmin_station_id": station_by_element["TMIN"],
                  "prcp_station_id": station_by_element["PRCP"],
                  "tmax_c": frame.TMAX.mean() if counts.TMAX / days >= .9 else np.nan,
                  "tmin_c": frame.TMIN.mean() if counts.TMIN / days >= .9 else np.nan,
                  "prcp_mm": frame.PRCP.sum(min_count=1) if counts.PRCP / days >= .9 else np.nan,
                  "tmean_c": frame.TMEAN.mean() if pair_complete else np.nan,
                  "dtr_c": frame.DTR.mean() if pair_complete else np.nan,
                  "wet_days": int(frame.PRCP.ge(1).sum()) if counts.PRCP / days >= .9 else np.nan,
                  "tmax_days": int(counts.TMAX), "tmin_days": int(counts.TMIN),
                  "prcp_days": int(counts.PRCP), "paired_temp_days": int(frame.TMEAN.count()),
                  "days_in_month": days, "coverage": float(min(counts.min(), frame.TMEAN.count()) / days),
                  "complete": complete, "assumed_available_date": month.end_time.normalize() + pd.Timedelta(days=14)}
        rows.append(values)
    return pd.DataFrame(rows)


def run() -> dict:
    manifest = json.loads((HERE / "manifest.json").read_text())
    output = {}
    cached_daily = {}
    for station_id in set(manifest["station_roles"].values()) | set(manifest["primary_element_stations"].values()):
        path = HERE / "raw" / f"{station_id}.dly.gz"
        expected = next(r["sha256"] for r in manifest["sources"] if r.get("station_id") == station_id)
        if hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest() != expected:
            raise ValueError("Raw source hash differs from manifest")
        cached_daily[station_id] = parse_daily(path)
    roles = {role: cached_daily[station_id] for role, station_id in manifest["station_roles"].items()}
    roles["closest_per_variable"] = pd.concat([
        cached_daily[station_id].loc[lambda d: d.element.eq(element)]
        for element, station_id in manifest["primary_element_stations"].items()
    ]).sort_values(["date", "element"])
    for role, daily in roles.items():
        table = monthly(daily)
        daily.to_csv(HERE / f"{role}_daily.csv", index=False)
        filename = "monthly_weather.csv" if role == "closest_per_variable" else f"{role}_monthly_weather.csv"
        table.to_csv(HERE / filename, index=False)
        selection_period = table.date.between("2015-01-01", "2025-12-01")
        output[role] = {"station_ids": sorted(daily.station_id.unique().tolist()), "monthly_file": filename,
                        "n_months_2015_2025": int(selection_period.sum()),
                        "n_complete_months_2015_2025": int(table.loc[selection_period, "complete"].sum()),
                        "missing_months_2015_2025": table.loc[selection_period & ~table.complete, "date"].dt.strftime("%Y-%m").tolist(),
                        "source_flags": {f"{e}:{s}": int(n) for (e, s), n in
                            daily.loc[daily.valid].groupby(["element", "source_flag"]).size().items()}}
    (HERE / "coverage_summary.json").write_text(json.dumps(output, indent=2) + "\n")
    return output


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
