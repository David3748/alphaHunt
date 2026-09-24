#!/usr/bin/env python3
"""followon_rivals.py — Rival map v1 for Class I recall share-gain lookup (thin stub).

Research only. PIT-strict. No statistical claim.

Working vertical: GICS-6 industry + indication/product-keyword overlap map
(data/rival_map_v1.json, 30 entries, >=5 verified) +
rival_long_candidates(event) -> [tickers] stub.

Mapping only: given a recall event on firm X's product, return X's mapped
rivals as *candidates* for share-gain review. This makes NO claim that rival
longs outperform after Class I recalls — that conditioning is unvalidated
and explicitly out of scope for this stub.

Event schema (dict): {"firm_ticker": str, "firm_name": str,
"classification": "Class I" | ...}. Either firm key may be absent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAP_PATH = ROOT / "data" / "rival_map_v1.json"

GICS6_RE = re.compile(r"^\d{6}$")

REQUIRED_KEYS = ("ticker", "company", "gics_industry", "gics_name",
                 "product_keywords", "indications", "rivals", "verified")


def load_rival_map(path: Path | str = DEFAULT_MAP_PATH) -> list[dict]:
    """Load + schema-validate the rival map fixture."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(rows, dict):  # allow {"_comment": ..., "entries": [...]} envelope
        rows = rows.get("entries", [])
    if not isinstance(rows, list) or not rows:
        raise ValueError("rival map must be a non-empty list")
    seen = set()
    for i, row in enumerate(rows):
        missing = [k for k in REQUIRED_KEYS if k not in row]
        if missing:
            raise ValueError(f"entry {i} missing keys: {missing}")
        if not GICS6_RE.match(str(row["gics_industry"])):
            raise ValueError(f"entry {i} ({row.get('ticker')}): bad GICS-6 {row.get('gics_industry')!r}")
        for key in ("product_keywords", "indications", "rivals"):
            if not isinstance(row[key], list) or any(not isinstance(v, str) for v in row[key]):
                raise ValueError(f"entry {i} ({row.get('ticker')}): {key} must be list[str]")
        ticker = str(row["ticker"]).upper()
        if ticker in seen:
            raise ValueError(f"duplicate ticker {ticker}")
        seen.add(ticker)
    return rows


def _resolve(entry_rows: list[dict], event: dict) -> dict | None:
    ticker = str(event.get("firm_ticker") or "").upper()
    if ticker:
        for row in entry_rows:
            if str(row["ticker"]).upper() == ticker:
                return row
        return None
    name = str(event.get("firm_name") or "").strip().lower()
    if not name:
        return None
    # Conservative substring match on company name (no alias table yet).
    candidates = [r for r in entry_rows if name in str(r["company"]).lower()]
    return candidates[0] if len(candidates) == 1 else None


def _is_class_i(classification: str | None) -> bool:
    return (classification or "").strip().lower() in ("class i", "class 1", "class-i")


def rival_long_candidates(event: dict, rival_map: list[dict] | None = None,
                          map_path: Path | str = DEFAULT_MAP_PATH) -> list[str]:
    """Return rival tickers for a Class I recall event, else [].

    Non-Class-I events, unknown firms, and ambiguous name matches yield [].
    No return prediction is attached — candidates require independent review.
    """
    rows = rival_map if rival_map is not None else load_rival_map(map_path)
    if not _is_class_i(event.get("classification")):
        return []
    entry = _resolve(rows, event)
    if entry is None:
        return []
    return [t for t in entry.get("rivals", []) if isinstance(t, str) and t]
