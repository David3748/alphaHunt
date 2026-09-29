"""Prepare fixed-calendar CY-Bench maize features, without fitting or target joins.

Default: rebuild from compact committed, checksum-verified sufficient statistics.
--fetch: refresh selected ZIP members by authenticated-free HTTP byte ranges,
retaining large raw inputs only in --raw-dir. Dataset: CY-Bench v1.10, EUPL-1.2.
"""
from __future__ import annotations

import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import struct
import zlib
import zipfile

import numpy as np
import pandas as pd
import requests

OUT = Path("results/satellite_validation/cybench_maize")
RAW = Path("work/audit_construction/cybench_raw")
URL = "https://zenodo.org/api/records/17279151/files/cybench-data.zip/content"
RECORD = "https://zenodo.org/api/records/17279151"
GAZETTEER = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_Gaz_counties_national.zip"
MONTHS = {4: "apr", 5: "may", 6: "jun", 7: "jul"}
MEANS = ("tmin", "tmax", "tavg", "vpd")
SUMS = ("prec", "rad", "et0", "cwb")
WEATHER = MEANS + SUMS
YEARS = range(2003, 2024)
PROTOCOL = {
    "dataset_record": 17279151,
    "years": [2003, 2023], "crop": "maize", "country": "US",
    "issue": "August 15 12:00 UTC",
    "issue_month": 8,
    "months": [4, 5, 6, 7],
    "ndvi": "Raw 8-day composites: window starts on source date, inclusive end=start+7 days at23:59:59UTC. Require end+14 days strictly before issue. Assign to start month; no interpolation, smoothing, or fill.",
    "ndvi_eligibility": "Every month >=2 finite values in [-1,1] AND >=50% of eligible expected eight-day starts (Jan1+8k).",
    "weather": "All source daily values April1-July31. Require every calendar day finite for each variable in each month. Partial totals are missing; never rescale.",
    "weather_means": list(MEANS), "weather_sums": list(SUMS),
    "geography": "All source locations matching 2020 Census county GEOIDs, excluding state totals/unallocated codes by metadata only.",
    "vintage": "Current-vintage scientific fixed-geography test. Static WorldCereal2021 mask and revised AgERA5/yield archives prevent an original operational historical availability claim.",
    "labels": "Independent NASS county yields retained separately and never used to select or construct features.",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, float_format="%.12g", compression={"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None)


def yield_unit_audit(labels: pd.DataFrame) -> dict:
    """Metadata-only check of official unit consistency; never select outcomes."""
    valid = np.isfinite(labels[["yield", "production", "harvest_area"]]).all(axis=1)
    valid &= (labels[["yield", "production", "harvest_area"]] > 0).all(axis=1)
    data = labels[valid]
    relative = data["yield"] / (data.production / data.harvest_area) - 1
    return {"rows": len(labels), "finite_positive_triplets": int(valid.sum()),
            "duplicate_county_years": int(labels.duplicated(["adm_id", "year"]).sum()),
            "relative_yield_vs_production_per_area": {str(q): float(relative.quantile(q)) for q in [0, .01, .5, .99, 1]},
            "source_units": {"yield": "t/ha", "production": "t", "harvest_area": "ha"},
            "upstream_us_conversion_factors": {"yield": .0628, "harvest_area": .4047, "production": .0254},
            "interpretation": "Rounded source conversion factors and published NASS quantities can cause small differences. This is an audit only; no labels are excluded based on this discrepancy and no prediction is scored."}


def normalize_labels(labels: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Preserve genuine crop failures; unsupported zero/unknown labels stay missing.

    Frozen before any fit after input validation exposed 218 source records having
    zero yield but neither harvested area nor production. The exact upstream
    origin of those zeros is not established; they are not asserted crop failures.
    """
    normalized = labels[["adm_id", "year", "yield"]].copy()
    unsupported = labels["yield"].eq(0) & labels.harvest_area.isna() & labels.production.isna()
    invalid = ~np.isfinite(labels["yield"]) | labels["yield"].lt(0)
    normalized.loc[unsupported | invalid, "yield"] = np.nan
    audit = labels[unsupported | invalid].copy()
    audit["normalization_reason"] = np.where(unsupported.loc[audit.index], "unsupported_zero_with_missing_area_and_production", "negative_or_nonfinite_yield")
    audit["normalized_yield"] = np.nan
    return normalized, audit


def get_range(start: int, end: int) -> bytes:
    response = requests.get(URL, headers={"Range": f"bytes={start}-{end}"}, timeout=180)
    response.raise_for_status()
    if response.status_code != 206 or not response.headers.get("Content-Range", "").startswith(f"bytes {start}-{end}/"):
        raise ValueError("Server did not honor exact byte range")
    if len(response.content) != end - start + 1:
        raise ValueError("Truncated byte range")
    return response.content


def zip_members(total_size: int) -> list[dict]:
    tail = get_range(total_size - 65536, total_size - 1)
    at = tail.rfind(b"PK\x05\x06")
    eocd = struct.unpack("<4s4H2LH", tail[at:at + 22])
    size, offset = eocd[5:7]
    if offset == 0xffffffff:
        at = tail.rfind(b"PK\x06\x07")
        zoffset = struct.unpack("<4sLQL", tail[at:at + 20])[2]
        z64 = struct.unpack("<4sQ2H2L4Q", get_range(zoffset, zoffset + 55))
        size, offset = z64[-2:]
    raw = get_range(offset, offset + size - 1)
    rows, at = [], 0
    while raw[at:at + 4] == b"PK\x01\x02":
        fields = struct.unpack("<4s6H3L5H2L", raw[at:at + 46])
        n, e, c = fields[10:13]
        name = raw[at + 46:at + 46 + n].decode()
        rows.append(dict(name=name, compressed_size=fields[8], raw_size=fields[9], local_offset=fields[-1], crc32=fields[7], method=fields[4]))
        at += 46 + n + e + c
    return rows


def download_member(member: dict, raw_dir: Path) -> dict:
    name = Path(member["name"]).name
    raw_path = raw_dir / name
    compressed_path = raw_dir / (name + ".deflate")
    header = get_range(member["local_offset"], member["local_offset"] + 29)
    h = struct.unpack("<4s5H3L2H", header)
    start = member["local_offset"] + 30 + h[-2] + h[-1]
    stop = start + member["compressed_size"]
    if not compressed_path.exists() or compressed_path.stat().st_size != member["compressed_size"]:
        # Bounded parallel chunks; only a single selected country/crop weather member.
        chunks = [(s, min(s + 16 * 1024 * 1024, stop) - 1) for s in range(start, stop, 16 * 1024 * 1024)]
        with compressed_path.open("wb") as f, ThreadPoolExecutor(max_workers=4) as pool:
            for data in pool.map(lambda pair: get_range(*pair), chunks):
                f.write(data)
    decompressor = zlib.decompressobj(-15)
    crc, size = 0, 0
    with compressed_path.open("rb") as src, raw_path.open("wb") as dst:
        for b in iter(lambda: src.read(1024 * 1024), b""):
            data = decompressor.decompress(b)
            dst.write(data)
            crc, size = zlib.crc32(data, crc), size + len(data)
        data = decompressor.flush()
        dst.write(data)
        crc, size = zlib.crc32(data, crc), size + len(data)
    if size != member["raw_size"] or crc != member["crc32"] or not decompressor.eof:
        raise ValueError(f"ZIP size/CRC mismatch: {name}")
    return {**member, "data_start": start, "data_end_inclusive": stop - 1,
            "raw_sha256": sha256(raw_path), "compressed_sha256": sha256(compressed_path)}


def expected_starts(year: int, month: int, issue_month: int = 8) -> pd.DatetimeIndex:
    issue = pd.Timestamp(year, issue_month, 15, 12)
    dates = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="8D")
    return dates[(dates.month == month) & (dates + pd.Timedelta(days=22) - pd.Timedelta(seconds=1) < issue)]


def prepare_ndvi(raw: pd.DataFrame, months: dict | None = None, issue_month: int = 8) -> pd.DataFrame:
    """Retain actual eligible observations, including invalid values as missing."""
    months = MONTHS if months is None else months
    data = raw.copy()
    data["date"] = pd.to_datetime(data.date.astype(str), format="%Y%m%d")
    if data.duplicated(["adm_id", "date"]).any():
        raise ValueError("Duplicate county-date NDVI observations")
    data["year"], data["month"] = data.date.dt.year, data.date.dt.month
    data["window_end"] = data.date + pd.Timedelta(days=8) - pd.Timedelta(seconds=1)
    data["assumed_available_date"] = data.window_end + pd.Timedelta(days=14)
    issue = pd.to_datetime(data.year.astype(str) + f"-{issue_month:02d}-15 12:00:00")
    data = data[data.year.isin(YEARS) & data.month.isin(months) & (data.assumed_available_date < issue)].copy()
    data.loc[~np.isfinite(data.ndvi) | ~data.ndvi.between(-1, 1), "ndvi"] = np.nan
    return data[["adm_id", "year", "month", "date", "window_end", "assumed_available_date", "ndvi"]].sort_values(["adm_id", "date"])


def aggregate_weather(raw_path: Path, county_ids: set[str], chunksize: int = 750_000, months: dict | None = None) -> pd.DataFrame:
    """Accumulate exact monthly sums and finite counts, without target data."""
    months = MONTHS if months is None else months
    pieces = []
    for chunk in pd.read_csv(raw_path, chunksize=chunksize, usecols=["adm_id", "date", *WEATHER]):
        dates = pd.to_datetime(chunk.date.astype(str), format="%Y%m%d")
        chunk["year"], chunk["month"] = dates.dt.year, dates.dt.month
        chunk = chunk[chunk.adm_id.isin(county_ids) & chunk.year.isin(YEARS) & chunk.month.isin(months)].copy()
        if chunk.empty:
            continue
        # Source rows are globally date-sorted. Duplicates are checked independently
        # through per-month day masks, including duplicates split across chunks.
        days = pd.to_datetime(chunk.date.astype(str), format="%Y%m%d").dt.day
        chunk["day_bits"] = np.left_shift(np.int64(1), days.to_numpy(dtype=np.int64) - 1)
        chunk["rows"] = 1
        for var in WEATHER:
            chunk.loc[~np.isfinite(chunk[var]), var] = np.nan
            chunk[var + "_count"] = chunk[var].notna().astype(int)
        group = chunk.groupby(["adm_id", "year", "month"], sort=False)
        values = group[[*WEATHER, *(v + "_count" for v in WEATHER), "rows"]].sum()
        values["day_bits"] = group.day_bits.agg(lambda a: np.bitwise_or.reduce(a.to_numpy()))
        pieces.append(values)
    if not pieces:
        return pd.DataFrame()
    all_parts = pd.concat(pieces)
    group = all_parts.groupby(level=[0, 1, 2], sort=True)
    summed = group[[*WEATHER, *(v + "_count" for v in WEATHER), "rows"]].sum()
    summed["day_bits"] = group.day_bits.agg(lambda a: np.bitwise_or.reduce(a.to_numpy()))
    unique_days = summed.day_bits.map(lambda value: int(value).bit_count())
    if (summed.rows != unique_days).any():
        raise ValueError("Duplicate county-date weather observations")
    return summed.reset_index().rename(columns={v: v + "_sum" for v in WEATHER})


def build_features(ndvi: pd.DataFrame, weather: pd.DataFrame, locations: pd.DataFrame,
                   months: dict | None = None, issue_month: int = 8) -> pd.DataFrame:
    """Construct forecasts' inputs for every source county/year, even no labels."""
    months = MONTHS if months is None else months
    index = pd.MultiIndex.from_product([sorted(locations.adm_id.unique()), YEARS], names=["adm_id", "year"])
    result = {}
    result["forecast_at"] = index.get_level_values("year").astype(str) + f"-{issue_month:02d}-15T12:00:00"
    result["ndvi_eligible"] = pd.Series(True, index=index)
    result["weather_eligible"] = pd.Series(True, index=index)
    for month, tag in months.items():
        part = ndvi[ndvi.month == month].copy()
        part.loc[~np.isfinite(part.ndvi) | ~part.ndvi.between(-1, 1), "ndvi"] = np.nan
        grouped = part.groupby(["adm_id", "year"])
        value = grouped.ndvi.mean().reindex(index)
        count = grouped.ndvi.count().reindex(index, fill_value=0)
        expected_by_year = {year: len(expected_starts(year, month, issue_month)) for year in YEARS}
        expected = pd.Series([expected_by_year[y] for _, y in index], index=index)
        latest = part[part.ndvi.notna()].groupby(["adm_id", "year"]).window_end.max().reindex(index)
        result["ndvi_" + tag] = value
        result["ndvi_count_" + tag] = count
        result["ndvi_expected_" + tag] = expected
        result["ndvi_coverage_" + tag] = count / expected
        result["ndvi_latest_window_end_" + tag] = latest
        result["ndvi_eligible"] &= (count >= 2) & (count / expected >= .5)
        w = weather[weather.month == month].set_index(["adm_id", "year"]).reindex(index)
        days = pd.Series([calendar.monthrange(y, month)[1] for _, y in index], index=index)
        for var in WEATHER:
            count = w[var + "_count"].fillna(0).astype(int)
            total = w[var + "_sum"]
            complete = count == days
            value = total / days if var in MEANS else total
            result[var + "_" + tag] = value.where(complete)
            result[var + "_count_" + tag] = count
            result[var + "_expected_" + tag] = days
            result[var + "_coverage_" + tag] = count / days
            result["weather_eligible"] &= complete
    result["features_eligible"] = result["ndvi_eligible"] & result["weather_eligible"]
    return pd.DataFrame(result, index=index).reset_index()


def fetch(out: Path, raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    record = requests.get(RECORD, timeout=60).json()
    archive = next(f for f in record["files"] if f["key"] == "cybench-data.zip")
    members = zip_members(archive["size"])
    source = []
    for base in ("location", "crop_mask", "crop_calendar", "ndvi", "yield", "meteo"):
        name = f"cybench-data/maize/US/{base}_maize_US.csv"
        member = next(m for m in members if m["name"] == name)
        print(f"Fetching {name}: {member['compressed_size'] / 1e6:.1f} MB", flush=True)
        source.append(download_member(member, raw_dir))
    response = requests.get(GAZETTEER, timeout=60)
    response.raise_for_status()
    (out / "census_2020_counties.zip").write_bytes(response.content)
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        gaz = pd.read_csv(z.open(z.namelist()[0]), sep="\t", dtype={"GEOID": str})
    fips = set(gaz.GEOID.str.strip())
    locations = pd.read_csv(raw_dir / "location_maize_US.csv")
    locations["fips"] = locations.adm_id.str.replace("US-", "", regex=False).str.replace("-", "", regex=False)
    rejected = locations[~locations.fips.isin(fips)]
    write_csv(rejected, out / "excluded_locations.csv")
    locations = locations[locations.fips.isin(fips)].copy()
    write_csv(locations, out / "locations.csv")
    ids = set(locations.adm_id)
    ndvi = prepare_ndvi(pd.read_csv(raw_dir / "ndvi_maize_US.csv"))
    write_csv(ndvi[ndvi.adm_id.isin(ids)], out / "ndvi_observations.csv.gz")
    weather = aggregate_weather(raw_dir / "meteo_maize_US.csv", ids)
    write_csv(weather, out / "monthly_weather_statistics.csv.gz")
    # Independent official labels stay separate; no outcomes printed or fitted.
    labels = pd.read_csv(raw_dir / "yield_maize_US.csv").rename(columns={"harvest_year": "year"})
    labels = labels[labels.adm_id.isin(ids) & labels.year.isin(YEARS)]
    (out / "yield_unit_audit.json").write_text(json.dumps(yield_unit_audit(labels), indent=2) + "\n")
    normalized, missing_audit = normalize_labels(labels)
    write_csv(labels.sort_values(["adm_id", "year"]), out / "yields_source_snapshot.csv.gz")
    write_csv(missing_audit.sort_values(["adm_id", "year"]), out / "yield_missingness_audit.csv")
    write_csv(normalized.sort_values(["adm_id", "year"]), out / "yields.csv.gz")
    names = ["locations.csv", "excluded_locations.csv", "census_2020_counties.zip", "ndvi_observations.csv.gz", "monthly_weather_statistics.csv.gz", "yields.csv.gz", "yields_source_snapshot.csv.gz", "yield_missingness_audit.csv", "yield_unit_audit.json"]
    manifest = {"url": URL, "record": RECORD, "version": record["metadata"].get("version"), "archive": archive, "license": record["metadata"]["license"], "members": source, "census_url": GAZETTEER,
                "compact_input_sha256": {name: sha256(out / name) for name in names},
                "units": {"yield": "t/ha", "ndvi": "dimensionless", "tmin/tmax/tavg": "C", "vpd": "hPa", "prec/et0/cwb": "mm/day", "rad": "J/m2/day"},
                "independence": "Feature preparation does not join or score yields. Static2021 mask and latest source vintages disclosed.",
                "upstream_code": "https://github.com/WUR-AI/AgML-CY-Bench/tree/main/data_preparation"}
    (out / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "preparation_protocol.json").write_text(json.dumps(PROTOCOL, indent=2) + "\n")


def rebuild(out: Path) -> pd.DataFrame:
    manifest = json.loads((out / "source_manifest.json").read_text())
    for name, expected in manifest["compact_input_sha256"].items():
        if sha256(out / name) != expected:
            raise ValueError(f"Input checksum mismatch: {name}")
    ndvi = pd.read_csv(out / "ndvi_observations.csv.gz", parse_dates=["date", "window_end", "assumed_available_date"])
    weather = pd.read_csv(out / "monthly_weather_statistics.csv.gz")
    locations = pd.read_csv(out / "locations.csv")
    configuration = json.loads((out / "preparation_protocol.json").read_text())
    months = {int(month): calendar.month_abbr[int(month)].lower() for month in configuration["months"]}
    features = build_features(ndvi, weather, locations, months=months, issue_month=configuration.get("issue_month", 8))
    write_csv(features, out / "monthly_features.csv.gz")
    coverage = features.groupby("year")[["ndvi_eligible", "weather_eligible", "features_eligible"]].sum().reset_index()
    coverage["counties_total"] = len(locations)
    write_csv(coverage, out / "feature_coverage.csv")
    summary = {"feature_sha256": sha256(out / "monthly_features.csv.gz"), "feature_rows": len(features), "locations": len(locations), "target_values_scored": 0,
               "feature_columns": list(features.columns), "upstream_rebuild": "Run --fetch to reconstruct sufficient statistics from ZIP byte-range inputs; default offline rebuild verifies compact input hashes and recalculates all monthly means/totals, eligibility, and coverage."}
    (out / "extraction_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return features


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=OUT)
    p.add_argument("--raw-dir", type=Path, default=RAW)
    p.add_argument("--fetch", action="store_true")
    a = p.parse_args()
    if a.fetch:
        fetch(a.output, a.raw_dir)
    data = rebuild(a.output)
    print(f"Prepared {len(data):,} county-year rows; {data.features_eligible.sum():,} feature-eligible; no outcome metrics.")


if __name__ == "__main__":
    main()
