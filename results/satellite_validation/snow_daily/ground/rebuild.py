#!/usr/bin/env python3
"""Offline precipitation / ground snow features; never reads runoff outcomes."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def read_cdec(path: Path, station: str, sensor: int, duration: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if not (frame.STATION_ID.eq(station).all() and frame.SENSOR_NUMBER.eq(sensor).all()
            and frame.DURATION.eq(duration).all() and frame.UNITS.eq("INCHES").all()):
        raise ValueError("Unexpected station, sensor, duration, or units")
    frame["date"] = pd.to_datetime(frame["DATE TIME"], format="%Y%m%d %H%M")
    frame["observed_at_local"] = pd.to_datetime(frame["OBS DATE"], format="%Y%m%d %H%M")
    frame["flag"] = frame.DATA_FLAG.fillna("").str.strip()
    frame["value_mm"] = pd.to_numeric(frame.VALUE, errors="coerce") * 25.4
    frame.loc[~np.isfinite(frame.value_mm) | frame.value_mm.lt(0), "value_mm"] = np.nan
    if frame.date.duplicated().any():
        raise ValueError("Duplicate ground observation date")
    return frame.set_index("date").sort_index()


def extract(index_monthly: pd.DataFrame, local_daily: pd.DataFrame, snow_daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for year in range(2000, 2026):
        months = pd.date_range(f"{year-1}-10-01", f"{year}-03-01", freq="MS")
        regional = index_monthly.reindex(months)
        # 'r' monthly readings are official revised values, retained explicitly.
        regional_values = regional.value_mm.where(regional.flag.isin(["", "r"]))
        winter = local_daily.reindex(pd.date_range(f"{year-1}-10-01", f"{year}-03-31"))
        local_values = winter.value_mm.where(winter.flag.eq(""))
        coverage = local_values.count() / len(winter)
        # A negative SWE sensor value is an invalid reading, not snow-free ground.
        snow = snow_daily.loc[f"{year}-03-29":f"{year}-03-31"]
        snow = snow.loc[snow.value_mm.notna() & snow.flag.eq("")]
        last_snow = snow.iloc[-1] if len(snow) else None
        rows.append({
            "year": year,
            "winter_precip_mm": regional_values.sum() if regional_values.count() == 6 else np.nan,
            "precipitation_source": "CDEC 5SI monthly sensor2; San Joaquin regional 5-station index",
            "precipitation_months": int(regional_values.count()),
            "precipitation_revised_months": int(regional.flag.eq("r").sum()),
            "precipitation_source_period_end": pd.Timestamp(year=year, month=3, day=31),
            "winter_precip_oct_feb_mm": regional_values.iloc[:5].sum() if regional_values.iloc[:5].count() == 5 else np.nan,
            "local_hnt_precip_mm": local_values.sum() if coverage >= .9 else np.nan,
            "local_hnt_valid_days": int(local_values.count()),
            "local_hnt_calendar_days": len(winter),
            "local_hnt_coverage": coverage,
            "ground_swe_mm": last_snow.value_mm if last_snow is not None else np.nan,
            "ground_swe_date": snow.index[-1] if last_snow is not None else pd.NaT,
            "ground_swe_observed_at_local": last_snow.observed_at_local if last_snow is not None else pd.NaT,
            "assumed_forecast_at": pd.Timestamp(year=year, month=4, day=1),
            "original_release_verified": False,
        })
    return pd.DataFrame(rows)


def run():
    for source in json.loads((HERE / "retrieval_manifest.json").read_text()):
        if source.get("status") == 200 and hashlib.sha256((HERE / source["path"]).read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError(f"Source changed: {source['path']}")
    regional = read_cdec(HERE / "five_station_monthly.csv", "5SI", 2, "M")
    rain = read_cdec(HERE / "huntington_precip.csv", "HNT", 45, "D")
    snow = read_cdec(HERE / "huntington_swe.csv", "HNT", 3, "D")
    frame = extract(regional, rain, snow)
    frame.to_csv(HERE / "winter_ground.csv", index=False)
    summary = {"n_years": len(frame), "regional_precip_complete_years": int(frame.winter_precip_mm.notna().sum()),
               "local_precip_complete_years": int(frame.local_hnt_precip_mm.notna().sum()),
               "local_precip_incomplete_years": frame.loc[frame.local_hnt_precip_mm.isna(), "year"].tolist(),
               "ground_swe_complete_years": int(frame.ground_swe_mm.notna().sum()),
               "ground_swe_missing_years": frame.loc[frame.ground_swe_mm.isna(), "year"].tolist(),
               "source_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "feature_file_sha256": hashlib.sha256((HERE / "winter_ground.csv").read_bytes()).hexdigest(),
               "original_vintage_operational_verification": False,
               "outcomes_read": False}
    (HERE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    run()
