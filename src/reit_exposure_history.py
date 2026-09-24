#!/usr/bin/env python3
"""Point-in-time REIT property exposure from SEC filings.

This module is deliberately independent of the satellite backtest.  It turns
SEC filing metadata and property tables into an exposure panel whose effective
``available_at`` timestamp is the SEC acceptance timestamp (not the fiscal
period end).  ``asof_exposure`` only permits filings accepted on or before the
requested date, which makes the output safe to join to a historical signal.

The extractor is intentionally conservative: it records the source table and
raw location text and leaves unresolved locations visible rather than silently
geocoding them.  A small city-to-metro mapping can be supplied in config.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from io import StringIO
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup


SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"


@dataclass(frozen=True)
class FilingRecord:
    issuer: str
    cik: str
    form: str
    accession_number: str
    filing_date: str
    report_date: str | None
    accepted_at: str
    primary_document: str
    source_url: str


@dataclass(frozen=True)
class PropertyExposure:
    issuer: str
    cik: str
    accession_number: str
    form: str
    filing_date: str
    report_date: str | None
    accepted_at: str
    source_url: str
    table_index: int
    row_index: int
    property_name: str
    location_text: str
    metro: str | None
    segment: str | None
    area_value: float | None
    area_units: str | None
    source_excerpt: str
    extraction_confidence: str


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        return result.tz_localize("UTC")
    return result.tz_convert("UTC")


def _clean(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _number(value: str) -> float | None:
    value = value.replace(",", "").replace("$", "").strip()
    match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else None


class SecClient:
    """Small SEC client with explicit User-Agent, throttling and cache."""

    def __init__(self, user_agent: str | None = None, min_interval: float = 0.2,
                 cache_dir: str | Path | None = None, session: requests.Session | None = None):
        email_match = re.search(r"[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})", user_agent or "", re.I)
        reserved_domains = {"example.com", "example.org", "example.net"}
        if not email_match or email_match.group(1).casefold() in reserved_domains:
            raise ValueError(
                "SEC live requests require a descriptive User-Agent with a real contact email; "
                "set ALPHAHUNT_SEC_USER_AGENT"
            )
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.min_interval = max(0.0, float(min_interval))
        self._last_request = 0.0
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get(self, url: str) -> requests.Response:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        response = self.session.get(url, timeout=60)
        self._last_request = time.monotonic()
        response.raise_for_status()
        return response

    def get_text(self, url: str, refresh: bool = False) -> str:
        cache_path = None
        if self.cache_dir:
            suffix = Path(urlparse(url).path).suffix or ".html"
            cache_path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + suffix)
            if cache_path.exists() and not refresh:
                return cache_path.read_text(encoding="utf-8")
        text = self._get(url).text
        if cache_path:
            cache_path.write_text(text, encoding="utf-8")
        return text

    def get_json(self, url: str, refresh: bool = False) -> dict:
        cache_path = None
        if self.cache_dir:
            cache_path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest() + ".json")
            if cache_path.exists() and not refresh:
                return json.loads(cache_path.read_text(encoding="utf-8"))
        data = self._get(url).json()
        if cache_path:
            cache_path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def filings(self, issuer: str, cik: str | int, forms: Iterable[str] = ("10-K", "10-Q"),
                start_date: str | None = None, end_date: str | None = None,
                refresh: bool = False) -> list[FilingRecord]:
        cik_int = int(str(cik).lstrip("0") or "0")
        data = self.get_json(SEC_SUBMISSIONS.format(cik=cik_int), refresh=refresh)
        recent = data.get("filings", {}).get("recent", {})
        wanted = set(forms)
        records: list[FilingRecord] = []
        for i, form in enumerate(recent.get("form", [])):
            if form not in wanted:
                continue
            accepted = recent.get("acceptanceDateTime", [""])[i]
            accession = recent["accessionNumber"][i]
            document = recent["primaryDocument"][i]
            filing_date = recent["filingDate"][i]
            if not accepted:
                # Fail closed. Filing date is not a defensible substitute for
                # the SEC acceptance timestamp in a point-in-time backtest.
                continue
            record = FilingRecord(
                issuer=issuer, cik=f"{cik_int:010d}", form=form,
                accession_number=accession, filing_date=filing_date,
                report_date=recent.get("reportDate", [None] * len(recent["form"]))[i],
                accepted_at=_utc(accepted).isoformat(), primary_document=document,
                source_url=SEC_ARCHIVE.format(cik=cik_int, accession=accession.replace("-", ""), document=document),
            )
            if start_date and _utc(record.accepted_at) < _utc(start_date):
                continue
            if end_date and _utc(record.accepted_at) > _utc(end_date + "T23:59:59Z"):
                continue
            records.append(record)
        return sorted(records, key=lambda x: x.accepted_at)


LOCATION_COLUMNS = ("location", "market", "city", "state", "country", "address", "geography", "region")
PROPERTY_COLUMNS = ("property", "asset", "community", "building", "project", "portfolio")
SEGMENT_COLUMNS = ("segment", "type", "sector", "property type", "classification")
AREA_COLUMNS = ("rentable", "square feet", "sq. ft", "sq ft", "area", "units", "rooms", "beds")


def _matching_column(columns: Iterable[object], needles: Iterable[str]) -> str | None:
    normalized = {str(c): re.sub(r"[^a-z0-9]+", " ", str(c).lower()).strip() for c in columns}
    for original, value in normalized.items():
        if any(needle in value for needle in needles):
            return original
    return None


def map_metro(location: str, city_to_metro: Mapping[str, str] | None = None) -> str | None:
    """Map a filing's location text using an auditable exact city dictionary."""
    value = _clean(location)
    if not value:
        return None
    mapping = {str(k).casefold(): str(v) for k, v in (city_to_metro or {}).items()}
    for city, metro in mapping.items():
        if re.search(rf"\b{re.escape(city)}\b", value.casefold()):
            return metro
    # Preserve an unresolved city/location as an explicit value only when the
    # caller requests identity mapping; default None prevents false precision.
    return None


def extract_property_tables(html: str, filing: FilingRecord,
                            city_to_metro: Mapping[str, str] | None = None,
                            min_confidence: str = "low") -> list[PropertyExposure]:
    """Extract likely property/portfolio tables from filing HTML.

    SEC filings vary widely.  Every extracted row includes table/row indexes,
    a compact source excerpt, and confidence.  Rows are never deduplicated so
    reviewers can reconcile them back to the filing.
    """
    try:
        # Bytes allow lxml to handle SEC's XML encoding declarations.
        tables = pd.read_html(html.encode("utf-8"), flavor="lxml")
    except (ValueError, ImportError):
        tables = []
    soup = BeautifulSoup(html, "html.parser")
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    filing_fields = asdict(filing)
    # primary_document is filing metadata, while PropertyExposure stores the
    # canonical source URL; avoid leaking an unexpected constructor field.
    filing_fields.pop("primary_document", None)
    filing_fields["accepted_at"] = _utc(filing_fields["accepted_at"]).isoformat()
    # Keep a document-level fallback visible if no HTML table was recognized.
    if not tables and not city_to_metro and re.search(r"properties|portfolio|real estate", text, re.I):
        excerpt = text[:500]
        return [PropertyExposure(**filing_fields, table_index=-1, row_index=-1,
                                 property_name="UNPARSED_DOCUMENT", location_text="",
                                 metro=None, segment=None, area_value=None, area_units=None,
                                 source_excerpt=excerpt, extraction_confidence="document")]

    confidence_order = {"low": 0, "medium": 1, "high": 2, "document": -1, "mention": -1}
    result: list[PropertyExposure] = []
    for table_index, table in enumerate(tables):
        table = table.copy()
        table.columns = [_clean(c) for c in table.columns]
        blob = " ".join([_clean(c) for c in table.columns] +
                         [_clean(v) for v in table.astype(object).to_numpy().ravel()])
        if not re.search(r"property|portfolio|location|market|rentable|square|segment|community", blob, re.I):
            continue
        prop_col = _matching_column(table.columns, PROPERTY_COLUMNS)
        loc_col = _matching_column(table.columns, LOCATION_COLUMNS)
        seg_col = _matching_column(table.columns, SEGMENT_COLUMNS)
        area_col = _matching_column(table.columns, AREA_COLUMNS)
        # A table with a location column is medium confidence; named property
        # plus location is high.  Area alone is not sufficient.
        # Numeric-column tables containing the word "property" in a footnote
        # are common in SEC HTML.  Do not turn their table-of-contents rows
        # into fake assets: a structured row must have a property or location
        # header.
        if not prop_col and not loc_col:
            continue
        confidence = "high" if prop_col and loc_col else "medium"
        if confidence_order[confidence] < confidence_order.get(min_confidence, 0):
            continue
        for row_index, row in table.iterrows():
            values = [_clean(v) for v in row.tolist()]
            if not any(values):
                continue
            property_name = _clean(row.get(prop_col, "")) if prop_col else values[0]
            location = _clean(row.get(loc_col, "")) if loc_col else ""
            segment = _clean(row.get(seg_col, "")) if seg_col else None
            area_value = _number(_clean(row.get(area_col, ""))) if area_col else None
            if not property_name and not location:
                continue
            result.append(PropertyExposure(
                **filing_fields, table_index=table_index, row_index=int(row_index),
                property_name=property_name, location_text=location,
                metro=map_metro(location, city_to_metro), segment=segment,
                area_value=area_value, area_units=area_col,
                source_excerpt=" | ".join(values)[:500], extraction_confidence=confidence,
            ))
    if result or not city_to_metro:
        return result

    # Many older SEC filings use deeply nested colspan tables that pandas
    # cannot represent as columns.  Preserve a reviewable, low-precision
    # fallback: one location mention per city per relevant HTML table.  These
    # are explicitly marked ``mention`` and carry no fabricated area weight.
    seen_cities: set[str] = set()
    for table_index, node in enumerate(soup.find_all("table")):
        table_text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        if not re.search(r"property|portfolio|geograph|market|rentable|square", table_text, re.I):
            continue
        for city, metro in city_to_metro.items():
            if str(city).casefold() in seen_cities:
                continue
            if not re.search(rf"\b{re.escape(str(city))}\b", table_text, re.I):
                continue
            seen_cities.add(str(city).casefold())
            result.append(PropertyExposure(
                **filing_fields, table_index=table_index, row_index=-1,
                property_name=f"LOCATION_MENTION:{city}", location_text=str(city), metro=str(metro),
                segment=None, area_value=None, area_units=None,
                source_excerpt=table_text[:500], extraction_confidence="mention",
            ))
    if not result and re.search(r"properties|portfolio|real estate", text, re.I):
        result.append(PropertyExposure(
            **filing_fields, table_index=-1, row_index=-1,
            property_name="UNPARSED_DOCUMENT", location_text="", metro=None,
            segment=None, area_value=None, area_units=None,
            source_excerpt=text[:500], extraction_confidence="document",
        ))
    return result


def asof_exposure(exposures: pd.DataFrame | Iterable[PropertyExposure], as_of: str | pd.Timestamp) -> pd.DataFrame:
    """Return the latest accepted filing per issuer available at ``as_of``.

    ``as_of`` is interpreted at UTC midnight.  For an end-of-day backtest,
    pass an explicit timestamp such as ``2024-02-14T23:59:59Z``.
    """
    if not isinstance(exposures, pd.DataFrame):
        exposures = pd.DataFrame([asdict(x) for x in exposures])
    if exposures.empty:
        return exposures.copy()
    frame = exposures.copy()
    frame["accepted_at"] = pd.to_datetime(frame["accepted_at"], utc=True)
    cutoff = _utc(as_of)
    eligible = frame[frame["accepted_at"] <= cutoff].copy()
    if eligible.empty:
        return eligible
    latest = eligible.groupby("issuer")["accepted_at"].transform("max")
    return eligible[eligible["accepted_at"].eq(latest)].sort_values(["issuer", "property_name"]).reset_index(drop=True)


def exposure_weights(exposures: pd.DataFrame, weight_column: str = "area_value") -> pd.DataFrame:
    """Aggregate measured rows to metro weights, retaining unresolved exposure.

    Location mentions and unparsed documents have no defensible portfolio
    weight.  Missing measurements therefore receive zero weight, and a filing
    with no measured rows fails closed instead of becoming an equal-weighted
    location list.
    """
    if exposures.empty:
        return pd.DataFrame(columns=["issuer", "metro", "weight", "unresolved"])
    frame = exposures.copy()
    frame["metro"] = frame["metro"].fillna("UNRESOLVED")
    if weight_column not in frame:
        raise ValueError(f"Missing exposure weight column: {weight_column}")
    values = pd.to_numeric(frame[weight_column], errors="coerce")
    if not values.notna().any():
        raise ValueError("No measured exposure weights; mention-only rows cannot be portfolio weighted")
    frame["_weight"] = values.fillna(0.0).clip(lower=0)
    if frame["_weight"].sum() <= 0:
        raise ValueError("Measured exposure weights must contain a positive value")
    grouped = frame.groupby(["issuer", "metro"], as_index=False)['_weight'].sum()
    grouped = grouped.rename(columns={"_weight": "raw_weight"})
    grouped["weight"] = grouped["raw_weight"] / grouped.groupby("issuer")["raw_weight"].transform("sum").replace(0, pd.NA)
    grouped["unresolved"] = grouped["metro"].eq("UNRESOLVED")
    return grouped


def records_to_frame(records: Iterable[FilingRecord]) -> pd.DataFrame:
    return pd.DataFrame([asdict(r) for r in records])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/reit_exposure_history.json")
    parser.add_argument("--output", default="research/reit_exposure_history/pilot_exposures.csv")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    sec_config = config["sec"]
    env_name = sec_config.get("user_agent_env", "ALPHAHUNT_SEC_USER_AGENT")
    user_agent = os.environ.get(env_name) or sec_config.get("user_agent")
    client = SecClient(user_agent, sec_config["min_interval_seconds"], sec_config.get("cache_dir"))
    all_rows: list[PropertyExposure] = []
    for issuer, meta in config["issuers"].items():
        records = client.filings(issuer, meta["cik"], forms=meta.get("forms", ["10-K"]),
                                 start_date=meta.get("start_date"), end_date=meta.get("end_date"), refresh=args.refresh)
        for record in records:
            html = client.get_text(record.source_url, refresh=args.refresh)
            all_rows.extend(extract_property_tables(html, record, config.get("city_to_metro", {})))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([asdict(row) for row in all_rows]).to_csv(output, index=False)
    print(json.dumps({"filings": len({row.accession_number for row in all_rows}), "rows": len(all_rows), "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
