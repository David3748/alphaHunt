"""As-published USDA WASDE corn vintages; no revised PSD backfill.

Default is fully offline and verifies frozen source hashes. ``--fetch`` explicitly
refreshes official snapshots. The CSV release time belongs to the report, not the
later machine-readable archive posting. See docs/notes/corn_usda_sources.md.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit
import zipfile

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "results/corn_model/inputs/usda"
LANDING = "https://www.usda.gov/historical-wasde-report-data-3"
MIRROR = "https://usda.azureedge.us"
TITLE = "U.S. Feed Grain and Corn Supply and Use"
FIELDS = {
    "yield per harvested acre": ("yield_bu_acre", "Bushels"),
    "area harvested": ("harvested_area_m_acres", "Million Acres"),
    "production": ("production_m_bu", "Million Bushels"),
    "ending stocks": ("ending_stocks_m_bu", "Million Bushels"),
    "domestic, total": ("domestic_use_m_bu", "Million Bushels"),
    "use, total": ("total_use_m_bu", "Million Bushels"),
    "exports": ("exports_m_bu", "Million Bushels"),
    "beginning stocks": ("beginning_stocks_m_bu", "Million Bushels"),
    "imports": ("imports_m_bu", "Million Bushels"),
    "supply, total": ("total_supply_m_bu", "Million Bushels"),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(v for k, v in attrs if k == "href" and v)


def discover_sources(html: str) -> list[str]:
    parser = _Links()
    parser.feed(html)
    links = {urljoin(LANDING, x) for x in parser.links
             if re.search(r"/oce-wasde-report-data-.*\.(?:csv|zip)$", x)}
    if not links:
        raise ValueError("No official vintage archives found; refuse revised-data fallback")
    return sorted(links)


def _get(url: str) -> tuple[bytes, str]:
    # USDA's official public CDN serves the same paths when its front end gives
    # this client a 403. Record both the canonical and actually fetched URLs.
    actual = MIRROR + urlsplit(url).path
    response = requests.get(actual, timeout=120)
    response.raise_for_status()
    if response.content.lstrip().lower().startswith((b"<html", b"<!doctype")):
        raise ValueError(f"Expected data, received HTML at {actual}")
    return response.content, response.url


def _csv_bytes(payload: bytes, suffix: str) -> bytes:
    if suffix == ".zip":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            files = [x for x in archive.namelist() if x.lower().endswith(".csv")]
            if len(files) != 1:
                raise ValueError("Expected exactly one CSV in official archive")
            return archive.read(files[0])  # zipfile verifies member CRC.
    return payload


def parse_vintage_csv(csv_bytes: bytes, source_file: str, source_sha256: str) -> pd.DataFrame:
    """Parse only the US annual corn table, preserving report × marketing year.

    Duplicate field keys and unexpected units fail closed. Missing numeric values
    remain NaN; no prior value, revised series, or neighboring vintage is copied.
    """
    frame = pd.read_csv(io.BytesIO(csv_bytes), dtype=str, keep_default_na=False)
    required = {"Commodity", "Region", "ReportTitle", "AnnualQuarterFlag", "Attribute",
                "Unit", "MarketYear", "Value", "WasdeNumber", "ReleaseDate", "ReleaseTime",
                "ForecastYear", "ForecastMonth", "ProjEstFlag"}
    if not required.issubset(frame):
        raise ValueError(f"Missing columns: {sorted(required - set(frame))}")
    frame = frame.apply(lambda col: col.str.strip())
    frame = frame.loc[(frame.Commodity == "Corn") & (frame.Region == "United States")
                      & (frame.ReportTitle == TITLE) & (frame.AnnualQuarterFlag == "Annual")].copy()
    frame["attribute_key"] = frame.Attribute.str.casefold()
    frame = frame.loc[frame.attribute_key.isin(FIELDS)]
    if frame.empty:
        raise ValueError(f"No US corn balance-sheet rows: {source_file}")
    rows = []
    for (release, market_year), group in frame.groupby(["ReleaseDate", "MarketYear"], sort=True):
        if not re.fullmatch(r"\d{4}/\d{2}", market_year):
            raise ValueError(f"Unknown marketing year: {market_year}")
        crop_year = int(market_year[:4])
        if int(market_year[-2:]) != (crop_year + 1) % 100:
            raise ValueError("Nonconsecutive corn marketing year")
        if group.attribute_key.duplicated().any():
            raise ValueError(f"Duplicate corn field at {release}, {market_year}")
        metadata = ["ReleaseTime", "WasdeNumber", "ForecastYear", "ForecastMonth", "ProjEstFlag"]
        if any(group[x].nunique(dropna=False) != 1 for x in metadata):
            raise ValueError(f"Inconsistent report metadata: {release}, {market_year}")
        first = group.iloc[0]
        report_date = pd.Timestamp(release)
        if (report_date.year != int(first.ForecastYear)
                or report_date.month != int(first.ForecastMonth)):
            raise ValueError("Report month/year disagrees with release date")
        clock = first.ReleaseTime
        if not re.fullmatch(r"\d\d:\d\d:\d\d(?:\.\d+)?", clock):
            raise ValueError(f"Missing or malformed official release time: {clock!r}")
        published = pd.Timestamp(f"{release} {clock}").tz_localize("America/New_York").tz_convert("UTC")
        result = {
            "report_date": report_date.date().isoformat(),
            "published_at": published.isoformat(),
            "publication_time_status": "official_csv_release_date_and_time;Eastern_timezone_documented_by_USDA",
            "release_time_eastern": clock.split(".")[0],
            "marketing_year": market_year,
            "marketing_year_start": crop_year,
            "wasde_number": int(first.WasdeNumber),
            "projection_estimate_flag": first.ProjEstFlag,
            "source_file": source_file,
            "source_sha256": source_sha256,
        }
        for column, _ in FIELDS.values():
            result[column] = np.nan
        for row in group.itertuples():
            column, unit = FIELDS[row.attribute_key]
            if row.Unit != unit:
                raise ValueError(f"Unexpected unit for {row.Attribute}: {row.Unit}")
            value = row.Value
            if value in ("", "NA", "N/A", "--", "-", "(NA)"):
                continue
            number = float(value.replace(",", ""))
            if not np.isfinite(number) or number < 0:
                raise ValueError(f"Invalid {column}: {value}")
            result[column] = number
        # Independent published fields can differ by one million bushels due to
        # rounding. These checks catch wrong units/table joins, not revisions.
        if all(np.isfinite(result[k]) for k in ("domestic_use_m_bu", "exports_m_bu", "total_use_m_bu")):
            if abs(result["domestic_use_m_bu"] + result["exports_m_bu"] - result["total_use_m_bu"]) > 2:
                raise ValueError(f"Corn use accounting mismatch: {release}, {market_year}")
        rows.append(result)
    return pd.DataFrame(rows)


def fetch_sources(output_dir: Path = DEFAULT_DIR) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = output_dir / "raw"
    raw.mkdir(exist_ok=True)
    response = requests.get(MIRROR + urlsplit(LANDING).path, timeout=120)
    response.raise_for_status()
    landing_bytes = response.content
    (raw / "historical_wasde_landing.html.gz").write_bytes(gzip.compress(landing_bytes, mtime=0))
    links = discover_sources(response.text)

    def download(url):
        payload, actual = _get(url)
        name = Path(urlsplit(url).path).name
        stored_name = name if name.endswith(".zip") else name + ".gz"
        stored = payload if name.endswith(".zip") else gzip.compress(payload, mtime=0)
        (raw / stored_name).write_bytes(stored)
        # Validate content before accepting it into the manifest.
        selected = parse_vintage_csv(_csv_bytes(payload, Path(name).suffix), name, sha256(payload))
        return {"canonical_url": url, "fetched_url": actual, "original_name": name,
                "stored_path": "raw/" + stored_name, "source_bytes": len(payload),
                "source_sha256": sha256(payload), "stored_sha256": sha256(stored),
                "first_report_date": selected.report_date.min(),
                "last_report_date": selected.report_date.max(), "corn_rows": len(selected)}

    with ThreadPoolExecutor(max_workers=5) as pool:
        sources = list(pool.map(download, links))
    manifest = {
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "canonical_landing_url": LANDING, "fetched_landing_url": response.url,
        "landing_stored_path": "raw/historical_wasde_landing.html.gz",
        "landing_stored_sha256": sha256((raw / "historical_wasde_landing.html.gz").read_bytes()),
        "landing_source_sha256": sha256(landing_bytes),
        "vintage_basis": "USDA states these data reproduce each published WASDE, excluding subsequent revisions. Individual reports remain official records.",
        "publication_semantics": "ReleaseDate/ReleaseTime are report release, not later CSV archive posting. Times localized to America/New_York (USDA Eastern-time convention).",
        "missing_period_policy": "No revised PSD backfill or synthetic releases. Official machine-readable vintage coverage begins April2010.",
        "sources": sources,
    }
    (output_dir / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def rebuild(output_dir: Path = DEFAULT_DIR) -> pd.DataFrame:
    """Verify the frozen snapshots and reconstruct normalized vintages offline."""
    manifest = json.loads((output_dir / "source_manifest.json").read_text())
    landing = output_dir / manifest["landing_stored_path"]
    if sha256(landing.read_bytes()) != manifest["landing_stored_sha256"]:
        raise ValueError("Landing snapshot hash mismatch")
    frames = []
    for source in manifest["sources"]:
        stored = (output_dir / source["stored_path"]).read_bytes()
        if sha256(stored) != source["stored_sha256"]:
            raise ValueError(f"Snapshot hash mismatch: {source['stored_path']}")
        name = source["original_name"]
        payload = stored if name.endswith(".zip") else gzip.decompress(stored)
        if sha256(payload) != source["source_sha256"]:
            raise ValueError(f"Source hash mismatch: {name}")
        frames.append(parse_vintage_csv(_csv_bytes(payload, Path(name).suffix), name, source["source_sha256"]))
    data = pd.concat(frames, ignore_index=True).sort_values(["report_date", "marketing_year_start"])
    if data.duplicated(["report_date", "marketing_year"]).any():
        raise ValueError("Overlapping source vintages; do not silently deduplicate")
    output = output_dir / "wasde_corn_vintages.csv"
    data.to_csv(output, index=False, float_format="%.10g", lineterminator="\n")
    present = set(pd.to_datetime(data.report_date).dt.to_period("M").astype(str))
    months = pd.period_range(pd.to_datetime(data.report_date).min(), pd.to_datetime(data.report_date).max(), freq="M")
    coverage = {
        "first_report_date": data.report_date.min(), "last_report_date": data.report_date.max(),
        "reports": int(data.report_date.nunique()), "report_marketing_year_rows": len(data),
        "missing_months_within_archive": [str(x) for x in months if str(x) not in present],
        "missing_before_archive": "January2000–March2010: not supplied in official bulk vintage archive; no revised-history substitute",
        "null_counts": {c: int(data[c].isna().sum()) for c, _ in FIELDS.values()},
        "normalized_sha256": sha256(output.read_bytes()),
        "source_manifest_sha256": sha256((output_dir / "source_manifest.json").read_bytes()),
    }
    (output_dir / "coverage.json").write_text(json.dumps(coverage, indent=2) + "\n")
    return data


def load_vintages(path: Path | str | None = None) -> pd.DataFrame:
    """Load the normalized table; no network access and no back/forward filling."""
    path = Path(path) if path is not None else DEFAULT_DIR / "wasde_corn_vintages.csv"
    data = pd.read_csv(path)
    data["report_date"] = pd.to_datetime(data.report_date)
    data["published_at"] = pd.to_datetime(data.published_at, utc=True)
    if data.duplicated(["report_date", "marketing_year"]).any():
        raise ValueError("Duplicate normalized report/marketing-year key")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--fetch", action="store_true", help="Explicitly refresh official raw archive snapshots")
    args = parser.parse_args()
    if args.fetch:
        fetch_sources(args.output_dir)
    data = rebuild(args.output_dir)
    print(f"Verified {data.report_date.nunique()} report vintages / {len(data)} report-marketing-year rows: {args.output_dir}")


if __name__ == "__main__":
    main()
