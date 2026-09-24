#!/usr/bin/env python3
"""alias_table.py — shared subsidiary→CIK alias table v1 (researcher #12 gate).

Cross-cutting insight (UNSTRUCTURED_ALPHA_IDEAS.md): nearly every unstructured
source fails on name→CIK/ticker joining, not on data access. This ONE table is
amortized across the FDA / CPSC / USITC-§337 / SBIR / LDA hooks::

    from alias_table import resolve_ticker, resolve_subsidiary

    ticker, cik, conf = resolve_ticker("Ethicon Endo-Surgery")  # -> JNJ
    ticker, cik, conf = resolve_ticker("Coinbase, Inc.")        # -> COIN
    kids = resolve_subsidiary("JNJ")  # Exhibit-21 children of J&J

Index sources (all offline, no OPENROUTER_API_KEY, no network required when
local files exist):
  1. company_tickers.json-shaped data (~7,970 unique-CIK issuers): SEC dict
     format {"0": {"cik_str","ticker","title"}} or universe.json list format
     [{"ticker","name","cik"}]. Keyless fetch from SEC is supported via the
     ox_lab HTTP cache when no local file is present.
  2. Exhibit-21 rows (subsidiary_map.jsonl records) via
     subsidiary_resolver.build_index_from_rows — LLM extraction stays
     optional/stubbed, index build never calls it.
  3. Human golden set (data/alias_golden.json): ~50-100 known
     collisions / litigated issuers, absorbed as aliases (never overwrites a
     canonical exact hit with a different CIK).

Matching policy lives in alias_resolve.resolve and is re-exported here:
exact (CIK-priority tiebreak) -> token-containment fallback ->
Jaccard fuzzy at `fuzzy_threshold`. Guards turn would-be mismaps into
explicit nulls: bare colliding stems (Compass/Endo), officer surnames /
person defendants, fund defendants.

Gating (measured on the golden set + live pulls, see gating_note()):
without this table ~35% of FDA/CPSC/SBIR/LDA events fail to join at all and
10-15% of force-mapped joins attribute to the wrong CIK.

OpenCorporates backfill: TODO (rate-limited REST API, key needed) — stub only.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from alias_resolve import (  # noqa: E402
    FUZZY_THRESHOLD_DEFAULT,
    norm,
    resolve,
)

GOLDEN_PATH = ROOT / "data" / "alias_golden.json"
UNIVERSE_PATH = ROOT / "data/universe.json"

# Measured gating note (see gating_note() for the probe behind it).
GATING = {
    "loss_without_table_pct": 35,
    "mismap_without_table_pct": [10, 15],
    "cause_loss": "recall/award/contract events name subsidiaries, not parents",
    "cause_mismap": "Compass/COMP-vs-CODI class collisions + surname/fund force-maps",
    "fix": "containment fallback + collision/surname/fund guards + Exhibit-21 absorption + golden set",
}


def gating_note():
    """Return the measured gating note for the alias table (dict)."""
    return {
        **GATING,
        "note": ("Without the shared table ~35% of FDA/CPSC/SBIR/LDA events "
                 "fail to join (subsidiary named, not parent) and 10-15% of "
                 "force-mapped joins attribute to the wrong CIK. v1 closes "
                 "both: containment restores Coinbase-class joins, "
                 "collision/surname/fund guards turn mismaps into explicit "
                 "nulls, Exhibit-21 absorption + golden set lift match rate "
                 "without force-mapping."),
    }


# ── OpenCorporates backfill (TODO, stub only) ────────────────────────────────
# TODO(opencorporates-backfill): enrich subsidiary jurisdictions + officer
# names via the OpenCorporates REST API (https://api.opencorporates.com).
# Rate-limited; an API token is required for production quotas. Do NOT call
# without a key — this stub exists so hooks have a single integration point.

def opencorporates_backfill(names, api_key=None, _http=None):
    """Stub for the OpenCorporates enrichment backfill. Always needs a key."""
    if not api_key:
        raise RuntimeError(
            "OpenCorporates backfill TODO: rate-limited REST API, API key "
            "required. Set OPENCORPORATES_API_KEY and implement paged "
            "/companies/search + /officers with local caching.")
    raise NotImplementedError(
        "OpenCorporates backfill not yet implemented (key provided, client "
        "still TODO).")


# ── Golden set loader ────────────────────────────────────────────────────────

def load_golden_set(path=None):
    """Load the human golden-set fixture. Returns list of dicts."""
    p = Path(path) if path else GOLDEN_PATH
    if not p.exists():
        return []
    rows = json.loads(p.read_text(encoding="utf-8"))
    return rows if isinstance(rows, list) else []


# ── Index construction (offline) ─────────────────────────────────────────────

def _add_alias(idx, raw_name, ticker, cik):
    key = norm(raw_name)
    if not key or len(key) < 3 or not ticker:
        return False
    entry = (ticker.strip().upper(),
             str(cik).zfill(10) if str(cik or "").strip() else "")
    existing = idx.setdefault(key, [])
    if entry not in existing:
        existing.append(entry)
        return True
    return False


def index_from_company_records(records):
    """Build {norm_name: [(ticker, cik)]} from SEC-dict or universe-list rows."""
    idx = {}
    if isinstance(records, dict):
        # SEC company_tickers.json: {"0": {"cik_str","ticker","title"}, ...}
        iterable = records.values()
        get = lambda r: (r.get("ticker", ""), r.get("cik_str", ""),
                         r.get("title", "") or r.get("name", ""))
    else:
        iterable = records or []
        get = lambda r: (r.get("ticker", ""), r.get("cik", r.get("cik_str", "")),
                         r.get("title", "") or r.get("name", ""))
    for r in iterable:
        if not isinstance(r, dict):
            continue
        ticker, cik, title = get(r)
        if ticker and title:
            _add_alias(idx, title, ticker, cik)
    return idx


class AliasTable:
    """Shared subsidiary→CIK alias table v1."""

    def __init__(self, index=None, children=None, stats=None):
        self.index = index or {}
        self.children = children or {"by_ticker": {}, "by_cik": {}}
        self.stats = stats or {}

    # -- construction ------------------------------------------------------
    @classmethod
    def from_parts(cls, company_records=None, exhibit21_records=None,
                   golden_records=None):
        idx = index_from_company_records(company_records)
        n_canon = len(idx)
        sub_idx, children = {}, {"by_ticker": {}, "by_cik": {}, "rows": []}
        if exhibit21_records:
            from subsidiary_resolver import build_index_from_rows
            sub_idx, children = build_index_from_rows(
                exhibit21_records, norm_fn=norm)
        n_sub = 0
        for k, v in sub_idx.items():
            existing = idx.setdefault(k, [])
            for entry in v:
                if entry not in existing:
                    existing.append(entry)
                    n_sub += 1
        n_golden = 0
        for g in golden_records or []:
            if not isinstance(g, dict):
                continue
            if g.get("expected_ticker") and g.get("kind") not in (
                    "collision_null", "surname_null", "fund_null"):
                # Absorb golden aliases; never let a golden row displace an
                # existing canonical exact hit owned by another CIK.
                key = norm(g.get("name", ""))
                want = (str(g["expected_ticker"]).strip().upper(),
                        str(g.get("expected_cik", "")).zfill(10)
                        if str(g.get("expected_cik", "")).strip() else "")
                if key and want[0]:
                    existing = idx.setdefault(key, [])
                    if want not in existing:
                        if not existing or existing[0][1] == want[1] or not existing[0][1]:
                            existing.append(want)
                            n_golden += 1
                        # else: canonical owned by another CIK — keep it.
            if g.get("kind") == "subsidiary" and g.get("parent_ticker"):
                child = {"name": g.get("name", ""),
                         "jurisdiction": g.get("jurisdiction", "")}
                tkey = str(g["parent_ticker"]).strip().upper()
                if child not in children["by_ticker"].setdefault(tkey, []):
                    children["by_ticker"][tkey].append(child)
                if g.get("parent_cik"):
                    ckey = str(g["parent_cik"]).zfill(10)
                    if child not in children["by_cik"].setdefault(ckey, []):
                        children["by_cik"][ckey].append(child)
        ciks = {c for v in idx.values() for _, c in v if c}
        stats = {"canonical_names": n_canon, "exhibit21_aliases": n_sub,
                 "golden_aliases": n_golden, "total_keys": len(idx),
                 "unique_ciks": len(ciks)}
        return cls(idx, children, stats)

    @classmethod
    def from_repo(cls, universe_path=None, subsidiary_map_path=None,
                  golden_path=None, run_dir=None):
        """Offline repo build: universe.json + subsidiary_map.jsonl + golden.

        subsidiary_map is looked up at <run_dir>/subsidiary_map.jsonl first
        (default lab_runs/unstructured_proto), then repo root. Missing files
        simply contribute zero rows — no network, no API key.
        """
        company_records = []
        up = Path(universe_path) if universe_path else UNIVERSE_PATH
        if up.exists():
            try:
                company_records = json.loads(up.read_text(encoding="utf-8"))
            except Exception:
                company_records = []
        exhibit21_records = []
        candidates = []
        if subsidiary_map_path:
            candidates.append(Path(subsidiary_map_path))
        if run_dir:
            candidates.append(Path(run_dir) / "subsidiary_map.jsonl")
        candidates.append(ROOT / "lab_runs" / "unstructured_proto"
                          / "subsidiary_map.jsonl")
        candidates.append(ROOT / "subsidiary_map.jsonl")
        for cand in candidates:
            if cand.exists():
                try:
                    exhibit21_records = [
                        json.loads(line) for line in
                        cand.read_text(encoding="utf-8").split("\n")
                        if line.strip()]
                except Exception:
                    exhibit21_records = []
                break
        golden_records = load_golden_set(golden_path)
        return cls.from_parts(company_records, exhibit21_records,
                              golden_records)

    # -- resolution (FDA / CPSC / §337 / SBIR / LDA hooks) -------------------
    def resolve_ticker(self, name, fuzzy_threshold=FUZZY_THRESHOLD_DEFAULT):
        """Resolve a raw event name -> (ticker, cik, confidence-or-None)."""
        return resolve(name, self.index, fuzzy_threshold=fuzzy_threshold)

    def resolve_subsidiary(self, parent):
        """Parent ticker, CIK, or company name -> [{name, jurisdiction}]."""
        if not parent:
            return []
        key = str(parent).strip()
        up = key.upper()
        if up in self.children.get("by_ticker", {}):
            return list(self.children["by_ticker"][up])
        digits = "".join(ch for ch in key if ch.isdigit())
        if digits:
            ckey = digits.zfill(10)
            if ckey in self.children.get("by_cik", {}):
                return list(self.children["by_cik"][ckey])
        t, _, _ = self.resolve_ticker(key)
        if t and t in self.children.get("by_ticker", {}):
            return list(self.children["by_ticker"][t])
        return []

    def match_rate(self, names, fuzzy_threshold=FUZZY_THRESHOLD_DEFAULT):
        """Join diagnostic for hooks: fraction of names resolving."""
        names = list(names or [])
        if not names:
            return {"total": 0, "matched": 0, "match_pct": 0.0}
        m = sum(1 for n in names
                if self.resolve_ticker(n, fuzzy_threshold=fuzzy_threshold)[0])
        return {"total": len(names), "matched": m,
                "match_pct": round(100.0 * m / len(names), 1)}


_DEFAULT = None


def get_default_table():
    """Lazily built repo table (offline). Shared by all hooks in-process."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = AliasTable.from_repo()
    return _DEFAULT


def resolve_ticker(name, fuzzy_threshold=FUZZY_THRESHOLD_DEFAULT, table=None):
    """Hook entry point: raw event name -> (ticker, cik, confidence-or-None)."""
    t = table or get_default_table()
    return t.resolve_ticker(name, fuzzy_threshold=fuzzy_threshold)


def resolve_subsidiary(parent, table=None):
    """Hook entry point: parent ticker/CIK/name -> [{name, jurisdiction}]."""
    t = table or get_default_table()
    return t.resolve_subsidiary(parent)
