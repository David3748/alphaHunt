#!/usr/bin/env python3
"""followon_tariff.py — Tariff/AD-CVD shock x 10-K sourcing-country join (thin stub).

Research only. PIT-strict. No trading claim.

Working vertical (researcher #10 pilot):
  Federal Register AD/CVD initiation client (reuses obscure_miner.get_json)
  + deterministic 10-K sourcing-country extractor
  (12 sourcing-cue regexes x 45-country lexicon) -> exposure JSONL.

Explicitly OUT (pilot result 0/20 hit): HTS extraction from 10-K text.
HTS scope comes ONLY from the FR petition-scope stub
(extract_hts_scope). Do not re-add 10-K HTS mining without a new pilot.

PIT discipline:
  - Issuer exposure is timestamped by 10-K filing_date (never period-end).
  - FR event timestamp is publication_date.
  - join_exposures emits a pair only when event_date >= filing_date.

Network: live FR pulls only; no disk cache here (reuse caller's /tmp cache
if batching). Tests inject a fake getter — no network in tests.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

# ── 45-country sourcing lexicon: canonical -> aliases ─────────────────────────
# Foreign sourcing origins only. The US is deliberately absent (domestic
# mentions are not sourcing exposure); regions ("Europe", "Asia") are absent
# (not joinable to an FR country cell).

COUNTRY_LEXICON: dict[str, list[str]] = {
    "China": ["People's Republic of China", "Mainland China", "PRC", "China"],
    "Mexico": ["United Mexican States", "Mexico"],
    "Canada": ["Canada"],
    "Vietnam": ["Socialist Republic of Vietnam", "Viet Nam", "Vietnam"],
    "Taiwan": ["Republic of China", "Taiwan"],
    "South Korea": ["Republic of Korea", "South Korea", "Korea"],
    "Japan": ["Japan"],
    "India": ["India"],
    "Thailand": ["Thailand"],
    "Malaysia": ["Malaysia"],
    "Indonesia": ["Indonesia"],
    "Philippines": ["Philippine", "Philippines"],
    "Bangladesh": ["Bangladesh"],
    "Cambodia": ["Kingdom of Cambodia", "Cambodia"],
    "Pakistan": ["Pakistan"],
    "Sri Lanka": ["Sri Lanka"],
    "Germany": ["Federal Republic of Germany", "Germany"],
    "France": ["France"],
    "Italy": ["Italy"],
    "Spain": ["Spain"],
    "Ireland": ["Republic of Ireland", "Ireland"],
    "Netherlands": ["The Netherlands", "Netherlands", "Holland"],
    "Belgium": ["Belgium"],
    "Poland": ["Poland"],
    "Czechia": ["Czech Republic", "Czechia"],
    "Hungary": ["Hungary"],
    "Romania": ["Romania"],
    "Turkey": ["Türkiye", "Turkiye", "Turkey"],
    "United Kingdom": ["United Kingdom", "Great Britain", "Britain", "U.K.", "UK", "England"],
    "Israel": ["Israel"],
    "Brazil": ["Brasil", "Brazil"],
    "Colombia": ["Colombia"],
    "Chile": ["Chile"],
    "Peru": ["Peru"],
    "Costa Rica": ["Costa Rica"],
    "Dominican Republic": ["Dominican Republic"],
    "Honduras": ["Honduras"],
    "Guatemala": ["Guatemala"],
    "El Salvador": ["El Salvador"],
    "Nicaragua": ["Nicaragua"],
    "Ecuador": ["Ecuador"],
    "Singapore": ["Singapore"],
    "Australia": ["Australia"],
    "South Africa": ["South Africa"],
    "Switzerland": ["Switzerland"],
}

assert len(COUNTRY_LEXICON) == 45, f"lexicon must hold 45 countries, has {len(COUNTRY_LEXICON)}"

_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canon, _aliases in COUNTRY_LEXICON.items():
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL.setdefault(_alias.lower(), _canon)

# Longest alias first so "People's Republic of China" wins over "China" and
# "Republic of China" (Taiwan) wins over "China".
_COUNTRY_RE = re.compile(
    r"(?<!\w)(" + "|".join(
        sorted((re.escape(a) for a in _ALIAS_TO_CANONICAL), key=len, reverse=True)
    ) + r")(?!\w)",
    re.IGNORECASE,
)

# ── 12 sourcing-cue regexes ───────────────────────────────────────────────────
# Generic sourcing language. A country counts as extracted exposure only when
# one of these fires within WINDOW_CHARS of the country mention.

SOURCING_CUES: list[str] = [
    r"sourc(?:e|ed|ing)\s+(?:from|in|within)",
    r"manufactur(?:e|ed|ing)\s+(?:in|by|through)",
    r"produc(?:e|ed|tion)\s+(?:in|by)",
    r"suppl(?:y|ier|iers|ied)\b",
    r"import(?:s|ed|ing)?\s+(?:from|in)",
    r"contract\s+manufactur",
    r"raw\s+materials?",
    r"facilit(?:y|ies)\s+(?:in|located)",
    r"depend(?:s|ence|ent)?\s+(?:on|upon)",
    r"concentrat(?:ed|ion)",
    r"assembl(?:ed|y|ies)\s+(?:in|by)",
    r"procure(?:d|ment)?\s+(?:from|in)",
]

assert len(SOURCING_CUES) == 12, f"must hold 12 cues, has {len(SOURCING_CUES)}"

_CUE_RES = [re.compile(p, re.IGNORECASE) for p in SOURCING_CUES]

# Concentration language driving the single-country heuristic (the >500bps/90d
# hypothesis is conditioned on single-country-sourced small caps).
_CONCENTRATION_RES = [
    re.compile(p, re.IGNORECASE) for p in [
        r"substantially\s+all",
        r"(?:sole|single)\s+sourc",
        r"majority\s+of",
        r"primar(?:y|ily)",
        r"depend(?:s|ence|ent)?\s+(?:on|upon)",
        r"concentrat(?:ed|ion)",
    ]
]

WINDOW_CHARS = 120

# ── 10-K country extractor ────────────────────────────────────────────────────


def extract_10k_countries(text: str) -> dict:
    """Return {"countries": [...], "single_country_sourced": bool, "cue_hits": n}.

    Deterministic regex pass. countries is sorted canonical names.
    single_country_sourced is True only when exactly one country is extracted
    AND concentration language is present (true/false heuristic).
    """
    found: set[str] = set()
    cue_hits = 0
    for match in _COUNTRY_RE.finditer(text or ""):
        canon = _ALIAS_TO_CANONICAL[match.group(1).lower()]
        window = text[max(0, match.start() - WINDOW_CHARS): match.end() + WINDOW_CHARS]
        if any(rx.search(window) for rx in _CUE_RES):
            found.add(canon)
            cue_hits += 1
    concentrated = any(rx.search(text or "") for rx in _CONCENTRATION_RES)
    countries = sorted(found)
    return {
        "countries": countries,
        "single_country_sourced": len(countries) == 1 and concentrated,
        "cue_hits": cue_hits,
    }


def extract_exposure(ticker: str, cik: str, filing_date: str, text: str) -> dict:
    """One PIT-stamped exposure record. filing_date (ISO) is the PIT timestamp."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", filing_date or ""):
        raise ValueError(f"filing_date must be ISO YYYY-MM-DD, got {filing_date!r}")
    result = extract_10k_countries(text)
    return {
        "ticker": ticker,
        "cik": cik,
        "filing_date": filing_date,  # PIT: knowable only on/after this date
        **result,
    }


# ── FR AD/CVD initiation client (reuses obscure_miner.get_json) ───────────────

FR_API = "https://www.federalregister.gov/api/v1/documents.json"
FR_AD_CVD_TERMS = [
    "antidumping duty initiation",
    "countervailing duty initiation",
]


def fr_adcvd_query_url(term: str, per_page: int = 20) -> str:
    params = urllib.parse.urlencode({
        "per_page": per_page,
        "order": "newest",
        "conditions[term]": term,
    })
    return f"{FR_API}?{params}"


def fetch_fr_initiations(terms: list[str] | None = None, per_page: int = 20,
                         getter=None) -> list[dict]:
    """Fetch FR AD/CVD initiation notices. Event timestamp = publication_date.

    getter defaults to obscure_miner.get_json (deferred import so this module
    stays import-light). Tests inject a fake. Returns newest-first, deduped by
    document_number: {document_number, title, publication_date, html_url, raw}.
    """
    if getter is None:
        from obscure_miner import get_json as getter  # deferred reuse
    seen: dict[str, dict] = {}
    for term in terms or FR_AD_CVD_TERMS:
        try:
            payload = getter(fr_adcvd_query_url(term, per_page)) or {}
        except Exception:
            continue
        for item in payload.get("results") or []:
            doc_no = item.get("document_number") or item.get("html_url") or ""
            if not doc_no or doc_no in seen:
                continue
            seen[doc_no] = {
                "document_number": item.get("document_number", ""),
                "title": item.get("title", ""),
                "publication_date": (item.get("publication_date") or "")[:10],
                "html_url": item.get("html_url", ""),
                "raw": item,
            }
    return sorted(seen.values(), key=lambda r: r["publication_date"], reverse=True)


# ── FR petition-scope HTS stub (ONLY sanctioned HTS source) ───────────────────

_HTS_RE = re.compile(r"\b(\d{4})\.(\d{2})(?:\.(\d{2}))?\b")
_HTS_CTX_RE = re.compile(r"hts|subheading|tariff\s+(?:schedule|item)", re.IGNORECASE)


def extract_hts_scope(fr_text: str) -> list[str]:
    """Stub: HTS subheadings from FR petition scope text.

    Keeps dotted codes (1234.56 / 1234.56.78) only when HTS/subheading context
    appears within 60 chars. 10-K HTS mining is OUT (pilot 0/20) — do not
    call this on 10-K text and expect signal.
    """
    hits: set[str] = set()
    for match in _HTS_RE.finditer(fr_text or ""):
        ctx = fr_text[max(0, match.start() - 60): match.end() + 60]
        if _HTS_CTX_RE.search(ctx):
            g1, g2, g3 = match.groups()
            hits.add(f"{g1}.{g2}.{g3}" if g3 else f"{g1}.{g2}")
    return sorted(hits)


# ── PIT join + JSONL output ───────────────────────────────────────────────────


def pit_ok(filing_date: str, event_date: str) -> bool:
    """True when the exposure was knowable at the event (event on/after filing)."""
    return bool(event_date) and bool(filing_date) and event_date >= filing_date


def join_exposures(exposures: list[dict], events: list[dict]) -> list[dict]:
    """Join exposures to FR events on country, PIT-gated on filing_date.

    Event dicts carry "countries" (from the FR title/scope side) and
    "publication_date". Pairs violating PIT are dropped, never backfilled.
    """
    rows = []
    for expo in exposures:
        for event in events:
            for country in set(expo.get("countries", [])) & set(event.get("countries", [])):
                if not pit_ok(expo.get("filing_date", ""), event.get("publication_date", "")):
                    continue
                rows.append({
                    "ticker": expo.get("ticker"),
                    "cik": expo.get("cik"),
                    "country": country,
                    "filing_date": expo.get("filing_date"),
                    "event_date": event.get("publication_date"),
                    "event_document": event.get("document_number", ""),
                    "event_url": event.get("html_url", ""),
                    "event_hts_scope": event.get("hts_scope", []),
                    "single_country_sourced": expo.get("single_country_sourced", False),
                })
    return sorted(rows, key=lambda r: (r["event_date"], r["ticker"], r["country"]))


def write_exposure_jsonl(path: Path, rows: list[dict]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")
    return path
