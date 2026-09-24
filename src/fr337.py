#!/usr/bin/env python3
"""fr337.py — USITC §337 Federal Register classifier + agency sweep (researcher #6 spec).

Research-only. Keyless federalregister.gov api/v1/documents.json client with
agency filter (international-trade-commission) + term 337, paginated sweep
(not company-seeded), raw snapshot with pull time, title-regex classifier with
escalation ladder, respondent-extraction stub + alias-table hook, JSONL events
+ sweep entrypoint.

PIT-strict: event timestamp is public-inspection filing time when available,
else publication_date. Never infer tradability before the PIT timestamp.

Does NOT write to lab_runs/ or research/. Default outputs go to /tmp or an
explicit --out path.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

API_BASE = "https://www.federalregister.gov/api/v1/documents.json"
AGENCY_SLUG = "international-trade-commission"
TERM_337 = "337"
UA = "alphaHunt/1.0 public research (USITC-337 classifier)"

# ── Title regexes (4 required + ID helper for the ladder) ────────────────────
# 1. Institution of Investigation — must say "of investigation" so formal
#    enforcement / modification / rescission proceedings stay in misc.
RE_INSTITUTION = re.compile(r"institut\w*\s+of\s+investigation", re.I)
# 2. Receipt of Complaint (incl. amended-complaint receipts → same ladder rung)
RE_RECEIPT = re.compile(r"receipt\s+of\s+(amended\s+)?complaint", re.I)
# 3a. Final Initial Determination (ALJ ID at end of evidentiary phase) → ladder id
RE_FINAL_ID = re.compile(r"final\s+initial\s+determination", re.I)
# 3b. Other Initial Determinations (incl. summary-determination IDs) → ladder id
RE_ID = re.compile(r"initial\s+determination", re.I)
# 3c. Commission Final Determination (violation / no-violation) → ladder final
RE_FINAL = re.compile(
    r"final\s+(commission\s+)?determination"
    r"|determination\s+of\s+no\s+violation"
    r"|finding\s+a\s+violation\s+(under|of)\s+section\s+337"
    r"|affirm.*final\s+initial\s+determination.*finding\s+no\s+violation"
    r"|finding\s+of\s+no\s+violation\s+of\s+section\s+337",
    re.I,
)
# 4. Exclusion-or-consent-order / cease-and-desist remedy language → ladder final
RE_REMEDY = re.compile(
    r"(general|limited)\s+exclusion\s+order"
    r"|\bexclusion\s+order\b"
    r"|\bconsent\s+order\b"
    r"|cease\s+and\s+desist",
    re.I,
)

# Misc guards (documentation; classification falls through to misc by default)
RE_MISC_HINT = re.compile(
    r"deadline|extension\s+of\s+(the\s+)?target\s+date"
    r"|public\s+interest"
    r"|enforcement\s+proceeding"
    r"|modification\s+proceeding|rescission\s+proceeding",
    re.I,
)

LADDER_RANK = {"complaint": 0, "institution": 1, "id": 2, "final": 3, "misc": -1}

# ── Investigation / party extraction stub patterns ───────────────────────────
RE_INV_NO = re.compile(r"337-TA-(\d+[A-Z]?)", re.I)
RE_DN = re.compile(r"\bDN\s*(\d{3,4})\b")
RE_COMPLAINANT = re.compile(r"on behalf of\s+(.+?)(?:\.|;|The complaint)", re.I | re.S)
# "(b) The respondent is the following entity ...: Apple Inc., 1 Infinite Loop..."
RE_RESPONDENT_CLAUSE = re.compile(
    r"\(b\)\s*The respondents?\s+(?:is|are)\s+(?:the following[^:]*:\s*)?(.+?)"
    r"(?:\(\s*c\s*\)|; and\n|\n\s*\(c\)|\.\s*\n)",
    re.I | re.S,
)
RE_RESPONDENT_NAMED = re.compile(
    r"named respondents?[^:]*:\s*(.+?)(?:\.|$)", re.I | re.S
)


def build_search_url(per_page: int = 100, page: int = 1,
                     term: str = TERM_337,
                     agency: str = AGENCY_SLUG,
                     order: str = "newest") -> str:
    """Keyless documents.json URL with agency + term filters."""
    q = urllib.parse.urlencode(
        {"per_page": per_page, "order": order, "conditions[term]": term}
    )
    agency_q = urllib.parse.urlencode({"conditions[agencies][]": agency})
    return f"{API_BASE}?{q}&{agency_q}&page={page}"


def fetch_page_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def sweep(max_docs: int = 200, per_page: int = 100,
          term: str = TERM_337, agency: str = AGENCY_SLUG,
          fetcher=None, sleep_s: float = 0.3) -> tuple[list[dict], dict]:
    """Paginated agency sweep. Returns (raw_docs, snapshot_meta).

    fetcher(page, per_page) -> dict with 'results' (+ optional
    'count'/'total_pages'/'next_page_url'). Injectable for tests.
    """
    if fetcher is None:
        def _default(page: int, pp: int) -> dict:
            return fetch_page_json(build_search_url(pp, page, term, agency))
        fetcher = _default
    docs: list[dict] = []
    pages = 0
    total_count = None
    page = 1
    while len(docs) < max_docs:
        payload = fetcher(page, per_page)
        if total_count is None and isinstance(payload.get("count"), int):
            total_count = payload["count"]
        results = payload.get("results") or []
        if not results:
            break
        docs.extend(results)
        pages += 1
        if len(results) < per_page:
            break
        # Stop if API reports no further pages
        tp = payload.get("total_pages")
        if isinstance(tp, int) and page >= tp:
            break
        if not payload.get("next_page_url") and isinstance(tp, int) is False:
            # documents.json always sets next_page_url when more pages exist;
            # absence with a full page is ambiguous → try next page anyway
            pass
        page += 1
        if sleep_s:
            time.sleep(sleep_s)
    docs = docs[:max_docs]
    meta = {
        "pulled_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "agency": agency,
        "term": term,
        "pages_fetched": pages,
        "docs_returned": len(docs),
        "api_total_count": total_count,
        "per_page": per_page,
    }
    return docs, meta


def save_raw_snapshot(docs: list[dict], meta: dict, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"snapshot_meta": meta, "count": len(docs), "results": docs}
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return out_path


def classify_title(title: str) -> tuple[str, str]:
    """Return (event_type, ladder_stage). Clean misc bucket by construction.

    Priority encodes the escalation ladder:
      receipt → complaint; institution-of-investigation → institution;
      initial-determination mentions → id, EXCEPT consent-order
      terminations (remedy) and affirmed final-ID dispositions (final);
      commission-final language → final; exclusion/consent/cease-desist
      without final language → remedy; everything else (deadline extensions,
      public-interest solicitations, enforcement/modification/rescission
      proceedings) → misc.
    """
    t = title or ""
    if RE_RECEIPT.search(t):
        return "receipt", "complaint"
    if RE_INSTITUTION.search(t):
        return "institution", "institution"
    if RE_ID.search(t):
        # Consent-order termination ends the matter as to those respondents
        # even though the title cites the underlying ID → remedy (ladder final).
        if re.search(r"consent\s+order", t, re.I):
            return "remedy", "final"
        # Commission finally affirming a final-ID (violation or no-violation)
        # with termination → commission-final, not interlocutory ID.
        if (RE_FINAL_ID.search(t) and re.search(r"affirm", t, re.I)
                and re.search(r"terminat", t, re.I)):
            return "final_determination", "final"
        return "initial_determination", "id"
    if RE_FINAL.search(t):
        return "final_determination", "final"
    if RE_REMEDY.search(t):
        return "remedy", "final"
    return "misc", "misc"


def pit_timestamp(doc: dict):
    """PIT-strict timestamp: public-inspection filing time else publication_date.

    Handles documents.json shape (publication_date + public_inspection_pdf_url),
    public-inspection-documents.json shape (filed_at + publication_date), and
    detail shape (nested public_inspection dict).
    """
    for key in ("filed_at", "filedAt", "inspection_filed_at", "public_inspection_at"):
        val = doc.get(key)
        if val:
            return str(val)
    pi = doc.get("public_inspection")
    if isinstance(pi, dict):
        for key in ("filed_at", "publication_date", "timestamp"):
            if pi.get(key):
                return str(pi[key])
    elif isinstance(pi, str) and pi:
        return pi
    return doc.get("publication_date")


def extract_investigation_no(*texts: str) -> str | None:
    for text in texts:
        if not text:
            continue
        m = RE_INV_NO.search(text)
        if m:
            return f"337-TA-{m.group(1).upper()}"
    return None


def extract_dn(*texts: str) -> str | None:
    for text in texts:
        if not text:
            continue
        m = RE_DN.search(text)
        if m:
            return m.group(1)
    return None


def extract_complainant(*texts: str) -> str | None:
    for text in texts:
        if not text:
            continue
        m = RE_COMPLAINANT.search(re.sub(r"\s+", " ", text))
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip(" ,.;")
            # Trim trailing "of <place>" is kept (useful); cap length for stub.
            return name[:300] if name else None
    return None


def _split_party_names(blob: str) -> list[str]:
    blob = re.sub(r"\s+", " ", blob).strip(" ,;.")
    # Respondent clauses often append addresses after commas; keep the head
    # entity per semicolon-separated entry as the stub.
    parts = [p.strip(" ,.") for p in re.split(r";", blob) if p.strip(" ,.")]
    out = []
    for p in parts:
        # Cut off street-address tails ("..., 1 Infinite Loop, Cupertino, CA 95014")
        m = re.match(r"^(.+?Inc\.?|.+?LLC|.+?Ltd\.?|.+?Corp\.?|.+?Co\.?|.+?GmbH|.+?B\.V\.?|.+?S\.A\.?|.+?Limited)", p, re.I)
        out.append((m.group(1).strip() if m else p[:200]).strip(" ,."))
    return [o for o in out if o][:10]


def extract_respondents(abstract: str = "", body: str = "") -> list[str]:
    """Stub: respondent names from investigation-notice body, else abstract."""
    for text in (body, abstract):
        if not text:
            continue
        flat = re.sub(r"\s+", " ", text)
        m = RE_RESPONDENT_CLAUSE.search(text) or RE_RESPONDENT_CLAUSE.search(flat)
        if m:
            names = _split_party_names(m.group(1))
            if names:
                return names
        m2 = RE_RESPONDENT_NAMED.search(flat)
        if m2:
            names = _split_party_names(m2.group(1))
            if names:
                return names
    return []


def _local_norm(name: str) -> str:
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    stop = {"inc", "corp", "corporation", "ltd", "limited", "llc", "co",
            "company", "plc", "the", "group", "holdings", "technologies",
            "technology", "systems", "international", "industries"}
    return " ".join(t for t in s.split() if t not in stop)


def resolve_names(names: list[str], alias_index: dict | None = None) -> list[dict]:
    """Alias-table hook: exact normalized match → (ticker, cik).

    alias_index: normalized-name -> (ticker, cik) or dict with those keys.
    Uses alias_resolve.norm when importable, else a local normalizer.
    Unmatched names keep ticker=None for the later respondent-join pass.
    """
    norm_fn = _local_norm
    try:
        from alias_resolve import norm as alias_norm  # type: ignore
        norm_fn = alias_norm
    except Exception:
        pass
    out = []
    for raw in names:
        key = norm_fn(raw)
        ticker = cik = conf = None
        if alias_index and key and key in alias_index:
            val = alias_index[key]
            if isinstance(val, (list, tuple)) and len(val) >= 2:
                ticker, cik = val[0], val[1]
            elif isinstance(val, dict):
                ticker, cik = val.get("ticker"), val.get("cik")
            conf = "exact"
        out.append({"raw": raw, "normalized": key, "ticker": ticker,
                    "cik": cik, "match_confidence": conf})
    return out


def classify_doc(doc: dict, alias_index: dict | None = None) -> dict:
    title = doc.get("title") or ""
    abstract = doc.get("abstract") or ""
    event_type, ladder = classify_title(title)
    inv_no = extract_investigation_no(title, abstract)
    return {
        "document_number": doc.get("document_number"),
        "title": title,
        "event_type": event_type,
        "ladder_stage": ladder,
        "ladder_rank": LADDER_RANK[ladder],
        "investigation_no": inv_no,
        "complaint_dn": extract_dn(title, abstract),
        "complainant_raw": extract_complainant(abstract),
        "respondents_raw": extract_respondents(abstract),
        "respondents_resolved": resolve_names(extract_respondents(abstract), alias_index),
        "pit_timestamp": pit_timestamp(doc),
        "publication_date": doc.get("publication_date"),
        "filed_at": doc.get("filed_at"),
        "html_url": doc.get("html_url"),
        "pdf_url": doc.get("pdf_url"),
        "public_inspection_pdf_url": doc.get("public_inspection_pdf_url"),
        "abstract_head": abstract[:1000],
    }


def sweep_events(max_docs: int = 200, per_page: int = 100,
                 fetcher=None, alias_index: dict | None = None,
                 sleep_s: float = 0.3) -> tuple[list[dict], list[dict], dict]:
    """Full sweep → (raw_docs, events, snapshot_meta)."""
    raw_docs, meta = sweep(max_docs=max_docs, per_page=per_page,
                           fetcher=fetcher, sleep_s=sleep_s)
    events = [classify_doc(d, alias_index) | {"pulled_at": meta["pulled_at"]}
              for d in raw_docs]
    return raw_docs, events, meta


def write_events_jsonl(events: list[dict], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return out_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["sweep", "classify"], nargs="?", default="sweep")
    ap.add_argument("--max-docs", type=int, default=200)
    ap.add_argument("--per-page", type=int, default=100)
    ap.add_argument("--raw-out", default="/tmp/fr337_raw.json")
    ap.add_argument("--events-out", default="/tmp/fr337_events.jsonl")
    ap.add_argument("--in-file", default=None,
                    help="classify: raw snapshot JSON (from --raw-out) instead of live fetch")
    args = ap.parse_args(argv)

    if args.command == "classify" and args.in_file:
        payload = json.loads(Path(args.in_file).read_text(encoding="utf-8"))
        raw_docs = payload.get("results", payload if isinstance(payload, list) else [])
        meta = payload.get("snapshot_meta", {"pulled_at": dt.datetime.now(dt.timezone.utc).isoformat()})
        events = [classify_doc(d) | {"pulled_at": meta.get("pulled_at")} for d in raw_docs]
    else:
        raw_docs, events, meta = sweep_events(max_docs=args.max_docs,
                                              per_page=args.per_page)
        save_raw_snapshot(raw_docs, meta, Path(args.raw_out))
    write_events_jsonl(events, Path(args.events_out))
    by_type: dict[str, int] = {}
    for ev in events:
        by_type[ev["event_type"]] = by_type.get(ev["event_type"], 0) + 1
    print(json.dumps({"events": len(events), "by_event_type": by_type,
                      "events_out": args.events_out,
                      "raw_out": args.raw_out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
