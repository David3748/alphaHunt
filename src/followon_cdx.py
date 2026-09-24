#!/usr/bin/env python3
"""followon_cdx.py — Wayback CDX leadership-page diff + 8-K 5.02 cross-check (thin stub).

Research only. PIT-strict. No trading claim.

Working vertical (Theme 3 #1):
  web.archive.org/cdx client returning digest captures
  + body-diff helper flagging person-name removals on company/about/governance
  URLs with (prev_capture, this_capture] interval semantics
  + 8-K Item 5.02 +/-5 session cross-check stub.

Interval semantics: a diff between consecutive collapsed captures is
attributed to (prev_capture, this_capture] — the change happened AFTER
prev_capture and AT OR BEFORE this_capture. An 8-K filed exactly at
prev_capture does NOT explain the change; one at this_capture may.

Network discipline: all HTTP GETs are throttled (default >=2s between
requests), carry a contact User-Agent, and are cached under /tmp only
(tempfile.gettempdir()/alphahunt_cdx). No repo writes. Tests inject a fake
opener — no network in tests.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
CONTACT_UA = "alphaHunt research contact research@example.com"
MIN_INTERVAL_S = 2.0

_LAST_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


def cache_root() -> Path:
    """Network cache lives in /tmp only — never in the repo."""
    root = Path(tempfile.gettempdir()) / "alphahunt_cdx"
    root.mkdir(parents=True, exist_ok=True)
    return root


def cdx_query_url(url: str, start: str | None = None, end: str | None = None,
                  limit: int = 1000) -> str:
    params = [
        ("url", url),
        ("output", "json"),
        ("fl", "timestamp,original,digest,statuscode"),
        ("filter", "statuscode:200"),
        ("collapse", "urlkey"),  # one row per distinct capture moment server-side
        ("limit", str(limit)),
    ]
    if start:
        params.append(("from", start))
    if end:
        params.append(("to", end))
    return CDX_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _throttled_get(url: str, opener=None, min_interval: float = MIN_INTERVAL_S) -> bytes:
    global _LAST_REQUEST
    key = hashlib.sha256(url.encode()).hexdigest() + ".gz"
    cached = cache_root() / key
    if cached.exists():
        return gzip.decompress(cached.read_bytes())
    with _LAST_LOCK:
        wait = min_interval - (time.monotonic() - _LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": CONTACT_UA})
    if opener is None:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    else:
        data = opener(req)
    tmp = cached.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(data, compresslevel=5))
    tmp.replace(cached)
    return data


def parse_cdx_ts(ts: str) -> str:
    """'20240102150405' -> '2024-01-02' (date precision is all the stub claims)."""
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"


def cdx_captures(url: str, start: str | None = None, end: str | None = None,
                 opener=None, min_interval: float = MIN_INTERVAL_S) -> list[dict]:
    """Return digest captures sorted by timestamp: {timestamp, date, original,
    digest, statuscode}. Raises RuntimeError on fetch/parse failure."""
    try:
        raw = _throttled_get(cdx_query_url(url, start, end), opener=opener,
                             min_interval=min_interval)
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        raise RuntimeError(f"CDX fetch failed for {url}: {exc}") from exc
    if not payload or len(payload) < 2:
        return []
    header = payload[0]
    idx = {name: header.index(name) for name in
           ("timestamp", "original", "digest", "statuscode") if name in header}
    out = []
    for row in payload[1:]:
        try:
            ts = row[idx["timestamp"]]
            out.append({
                "timestamp": ts,
                "date": parse_cdx_ts(ts),
                "original": row[idx["original"]],
                "digest": row[idx["digest"]],
                "statuscode": row[idx["statuscode"]],
            })
        except (IndexError, KeyError):
            continue
    return sorted(out, key=lambda r: r["timestamp"])


def collapse_digests(captures: list[dict]) -> list[dict]:
    """Drop consecutive captures with identical digest (reposts, not changes).

    Only *consecutive* duplicates collapse: a digest that reappears after a
    different digest is a genuine revert and is kept.
    """
    collapsed = []
    for cap in captures:
        if collapsed and collapsed[-1]["digest"] == cap["digest"]:
            continue
        collapsed.append(cap)
    return collapsed


# ── Governance-URL gate + person-name diff ────────────────────────────────────

_GOV_PATH_RE = re.compile(
    r"/(about|leadership|management|team|governance|board|directors|executives|officers)\b",
    re.IGNORECASE,
)

_NAME_RE = re.compile(r"\b([A-Z][a-z]{1,30}(?: [A-Z][a-z]{1,30}){1,2})\b")

# First-token stoplist: titles, nav chrome, months/days. Heuristic, not NER.
_STOP_FIRST = {
    "About", "Board", "Contact", "Leadership", "Team", "Investor", "Investors",
    "Chief", "Press", "News", "Careers", "Privacy", "Terms", "Products",
    "Services", "Customer", "Customers", "Annual", "United", "New", "Our",
    "The", "For", "And",
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
}


def is_governance_url(url: str) -> bool:
    return bool(_GOV_PATH_RE.search(urllib.parse.urlsplit(url).path or ""))


def extract_person_names(text: str) -> set[str]:
    names = set()
    for match in _NAME_RE.finditer(text or ""):
        name = match.group(1)
        if name.split()[0] in _STOP_FIRST:
            continue
        names.add(name)
    return names


def flag_person_removals(prev_text: str, curr_text: str, url: str) -> dict:
    """Flag person names present in prev but absent in curr.

    Gated on governance URLs: non-governance pages always return flag=False
    (person churn there is normal content, not a leadership signal).
    """
    if not is_governance_url(url):
        return {"url": url, "flag": False, "removed_names": [],
                "reason": "not a governance URL"}
    prev_names = extract_person_names(prev_text)
    curr_names = extract_person_names(curr_text)
    removed = sorted(prev_names - curr_names)
    return {
        "url": url,
        "flag": bool(removed),
        "removed_names": removed,
        "n_prev": len(prev_names),
        "n_curr": len(curr_names),
    }


# ── (prev, curr] interval semantics + 8-K 5.02 cross-check stub ────────────────


def in_interval(ts: str, start: str, end: str) -> bool:
    """Half-open membership: start < ts <= end on ISO date strings."""
    return start < ts <= end


def change_interval(prev: dict, curr: dict) -> dict:
    """Attribute a collapsed-capture diff to (prev.date, curr.date]."""
    return {
        "start": prev["date"],
        "end": curr["date"],
        "semantics": "(prev_capture, this_capture]",
        "prev_timestamp": prev["timestamp"],
        "this_timestamp": curr["timestamp"],
    }


def crosscheck_802(filing_dates: list[str], interval: dict,
                   window_sessions: int = 5) -> dict:
    """Stub: 8-K Item 5.02 filings within +/-window of the interval end.

    filing_dates: sorted ISO dates of the issuer's 8-K 5.02 filings (caller
    supplies these from SEC submissions — no EDGAR fetch here).
    Session counting is a CALENDAR-DAY proxy; a real trading-session calendar
    is L effort and explicitly out of scope. A match corroborates timing only.
    """
    end = interval["end"]
    lo = _shift_days(end, -window_sessions)
    hi = _shift_days(end, window_sessions)
    matched = sorted(d for d in filing_dates if lo <= d <= hi)
    deltas = [_day_diff(end, d) for d in matched]
    nearest = min(deltas, key=abs) if deltas else None
    return {
        "interval_end": end,
        "window_sessions": window_sessions,
        "window_calendar_proxy": [lo, hi],
        "matched_filings": matched,
        "nearest_delta_days": nearest,
        "corroborated": bool(matched),
        "note": "calendar-day proxy for sessions; timing corroboration only, not validation",
    }


def _shift_days(iso: str, delta: int) -> str:
    import datetime as dt
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=delta)).isoformat()


def _day_diff(a: str, b: str) -> int:
    import datetime as dt
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
