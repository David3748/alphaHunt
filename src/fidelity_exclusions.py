#!/usr/bin/env python3
"""fidelity_exclusions.py — dated long-exclusion list for manual brokerage use.

Research only, no trading advice. PIT-strict throughout: an exclusion applies
to an entry date only when event_date <= entry_date <= window_end, where
event_date is the first excludable day (T+1 after the PIT timestamp). Future
events never affect past entries.

Integrator over the four lane modules (all imports lazy/optional; the merger
works standalone on resolved JSONL too):

  FDA      src/fda_avoid.py — Class I only, PIT anchor = report_date (NEVER
           recall_initiation_date: median initiation->report lag ~139d, so
           initiation-anchoring injects ~4.5 months of lookahead), T+1 entry,
           20d drug / 40d device holds (config/fda_avoid.json).
  CPSC     config/cpsc_avoid.json — fire/burn/electrocution/shock-hazard
           recalls with >=10k units, 40d hold, T+1 entry.
  5.02-gap src/stewardship_gap.py — decision=="exclude" rows only (CEO/CFO,
           no successor in filing, no same-role CDX-PIT posting in 30d),
           90TD ~= 130 calendar days. Placebo rows never merge.
  337      src/fr337.py — ladder stages institution/id/final only
           (receipt/complaint and misc never exclude), PIT timestamp =
           public-inspection filing time else publication_date, 180d hold.

Merged rows are {ticker, source, event_date, window_end, reason} (+ "firm"
only for alias-unresolved rows kept for manual review; NULL tickers never
match in filter_signals). Overlap rule (extend, no re-entry): same-ticker
windows that overlap merge into one window ending at the max window_end;
sources/reasons union. While excluded, no new entry is allowed.

Hooks (caller injects point-in-time values, never future data):

- Liquidity: passes_liquidity / filter_liquidity, defaults price >= $3 and
  ADV >= $2M (config/fda_avoid.json). Missing PIT values fail closed per
  row; when NO PIT getters are supplied at all the gate is visibly skipped
  (kept rows flagged _liquidity_checked=False, same convention as
  fda_avoid.apply_liquidity).
- Sector caps: enforce_sector_caps(candidates, sector_of, cap=0.25),
  greedy equal-weight, unknown sectors never capped. (The CPSC lane uses a
  stricter 5% single-industry cap in its own context; pass cap=0.05 there.)

Usage:
  python3 src/fidelity_exclusions.py --fda fda_avoid_exclusion.jsonl \\
      --cpsc cpsc_resolved.jsonl --gap502 stewardship_gap_events.jsonl \\
      --s337 fr337_events.jsonl --out exclusions.json --csv exclusions.csv
"""

import argparse
import csv
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

try:
    import fda_avoid  # type: ignore
except Exception:
    fda_avoid = None
try:
    import stewardship_gap  # type: ignore
except Exception:
    stewardship_gap = None
try:
    import fr337  # type: ignore
except Exception:
    fr337 = None

# ── Windows (lane-owned where a lane spec exists; integrator defaults else) ──
FDA_HOLD = {"drug": 20, "device": 40}  # config/fda_avoid.json (T+1 entry)
FDA_UNKNOWN_CATEGORY_HOLD = 40         # fail-safe: longer avoid on ambiguity
CPSC_HOLD_DAYS = 40                    # config/cpsc_avoid.json
CPSC_MIN_UNITS = 10_000                # config/cpsc_avoid.json
CPSC_HAZARD_RE = re.compile(r"fire|burn|electrocut|electric\s*shock|shock\s*hazard", re.I)
GAP502_CAL_DAYS = 130                  # stewardship_gap: round(90TD * 365.25/252)
ITC337_HOLD_DAYS = 180                 # integrator default (no lane window spec)
ITC337_EXCLUDE_LADDERS = {"institution", "id", "final"}

DEFAULT_MIN_PRICE = 3.0
DEFAULT_MIN_ADV_DOLLARS = 2_000_000.0

SOURCES = ("FDA", "CPSC", "5.02-gap", "337")


# ── Dates (PIT-strict: date-only, no forward fill) ───────────────────────────
def parse_date(s):
    """Parse YYYY-MM-DD, YYYYMMDD, full ISO datetime, or year-only. Else None."""
    if s is None:
        return None
    if isinstance(s, dt.datetime):
        return s.date()
    if isinstance(s, dt.date):
        return s
    s = str(s).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{8}", s):
        try:
            return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    if re.fullmatch(r"\d{4}", s):
        return dt.date(int(s), 1, 1)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return dt.datetime.strptime(s[:19] if "T" in s else s, fmt).date()
        except ValueError:
            continue
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def add_days(day, n):
    return day + dt.timedelta(days=int(n))


def _norm_ticker(t):
    t = str(t or "").strip().upper()
    return t or None


def _record(ticker, source, event_date, window_end, reason, firm=None):
    r = {"ticker": _norm_ticker(ticker), "source": source,
         "event_date": event_date.isoformat(), "window_end": window_end.isoformat(),
         "reason": str(reason)[:300]}
    if r["ticker"] is None and firm:
        r["firm"] = str(firm)[:160]  # manual-review key until alias resolves
    return r


def _pick(row, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return None


# ── FDA: consume fda_avoid lane output (report-anchored, never initiation) ───
def from_fda_avoid_rows(rows, alias_index=None):
    """Map fda_avoid.build_exclusion_rows() output -> merged-schema rows.

    Lane rows already carry entry_date (T+1 after report_date) + window_end.
    Tickers resolve via the lane hook only when alias_index is supplied;
    otherwise unresolved rows are kept with firm for manual review.
    """
    out = []
    for r in rows or []:
        d0, d1 = parse_date(r.get("entry_date")), parse_date(r.get("window_end"))
        if not d0 or not d1 or d1 < d0:
            continue
        t = _norm_ticker(r.get("ticker"))
        firm = r.get("recalling_firm", "")
        if t is None and alias_index is not None and fda_avoid is not None and firm:
            t, _, _ = fda_avoid.resolve_ticker(firm, alias_index)
            t = _norm_ticker(t)
        reason = r.get("reason") or (
            f"Class I {r.get('category', '')} recall {r.get('recall_number', '')}".strip())
        out.append(_record(t, "FDA", d0, d1, reason, firm=firm))
    return out


def load_fda_raw(records, category="device", alias_index=None, today=None):
    """Fallback: raw openFDA enforcement records -> exclusion rows.

    Delegates to fda_avoid.filter_records + build_exclusion_rows when the
    lane module is importable (Class I + report_date floor + sane dates);
    otherwise a local report-anchored fallback. Initiation dates are NEVER
    the anchor (median ~139d lookahead).
    """
    if fda_avoid is not None:
        kept, _ = fda_avoid.filter_records(
            list(records or []), category,
            today=today or dt.datetime.now(dt.timezone.utc).date())
        return from_fda_avoid_rows(
            fda_avoid.build_exclusion_rows(kept, category), alias_index)
    out = []
    for r in records or []:
        if str(r.get("classification", "")).strip().lower() != "class i":
            continue
        rep = parse_date(r.get("report_date"))
        if not rep:
            continue  # fail closed: no observable timestamp, no exclusion
        start = add_days(rep, 1)
        hold = FDA_HOLD.get(category, FDA_UNKNOWN_CATEGORY_HOLD)
        firm = _pick(r, "recalling_firm", "firm", "company") or ""
        desc = _pick(r, "reason_for_recall", "product_description") or ""
        out.append(_record(_pick(r, "ticker", "symbol"), "FDA", start,
                           add_days(start, hold - 1),
                           f"FDA Class I {category}: {str(desc)[:160]}", firm=firm))
    return out


# ── CPSC: config/cpsc_avoid.json gate (fire/burn pattern + >=10k units) ───────
def _cpsc_units(row):
    for k in ("number_of_units", "units", "units_affected",
              "estimated_units_affected", "units_recalled"):
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            return int(float(str(v).replace(",", "")))
        except (TypeError, ValueError):
            continue
    return None


def load_cpsc_rows(rows, hold_days=CPSC_HOLD_DAYS, min_units=CPSC_MIN_UNITS):
    """CPSC recalls -> exclusion rows. Only hazard-pattern recalls qualify;
    event = recall_date + 1 (T+1 entry). Units below min_units are dropped;
    unknown units are KEPT with an '(units unverified)' flag for manual
    review (an avoid list must not silently drop a fire recall on a parse
    miss — the Fidelity check confirms units before acting)."""
    out = []
    for r in rows or []:
        t = _norm_ticker(_pick(r, "ticker", "symbol"))
        d = parse_date(_pick(r, "recall_date", "event_date", "date"))
        if not d:
            continue
        haz = str(_pick(r, "severity", "hazard", "hazard_type", "hazard_name") or "")
        if not CPSC_HAZARD_RE.search(haz):
            continue
        units = _cpsc_units(r)
        if units is not None and units < min_units:
            continue
        start = add_days(d, 1)
        mfr = _pick(r, "manufacturer", "firm", "company") or ""
        title = _pick(r, "title", "description", "product") or ""
        reason = f"CPSC {haz.strip()}: {str(title)[:160].strip()}"
        if units is None:
            reason += " (units unverified)"
        out.append(_record(t, "CPSC", start, add_days(start, hold_days - 1),
                           reason or "CPSC recall", firm=mfr or None))
    return out


# ── 5.02-gap: consume stewardship_gap decisions (exclude only, never placebo)
def from_stewardship_decisions(rows):
    """stewardship_gap run_pipeline decisions -> exclusion rows.

    Only decision=="exclude" rows merge. Placebo rows (is_placebo) and
    no_exclude/no_data rows never merge. Window: exclude_start..exclude_end
    (90TD ~= 130 calendar days, computed by the lane).
    """
    out = []
    for r in rows or []:
        if r.get("is_placebo") or r.get("decision") != "exclude":
            continue
        t = _norm_ticker(r.get("ticker"))
        d0 = parse_date(r.get("exclude_start") or r.get("filed_date"))
        d1 = parse_date(r.get("exclude_end_cal_approx"))
        if not t or not d0 or not d1 or d1 < d0:
            continue
        roles = ", ".join(r.get("trigger_roles", []) or [])
        out.append(_record(t, "5.02-gap", d0, d1,
                           f"5.02 stewardship gap {roles}: no successor, "
                           f"no CDX-PIT posting 30d ({r.get('reason', '')})".strip()))
    return out


def load_gap502_rows(rows, window_cal_days=GAP502_CAL_DAYS):
    """Fallback: raw departures shape (LLM-overlay path) -> exclusion rows.

    Gap = unexpected AND (no successor named OR disagreement flag).
    Event = filed_date (SEC filing = PIT timestamp), T+1 entry.
    """
    out = []
    for r in rows or []:
        if r.get("decision") is not None:
            continue  # lane decisions go through from_stewardship_decisions
        if not r.get("is_unexpected"):
            continue
        if r.get("successor_named") and not r.get("has_disagreement_flag"):
            continue
        t = _norm_ticker(_pick(r, "ticker", "symbol"))
        d = parse_date(_pick(r, "filed_date", "file_date", "event_date", "date"))
        if not t or not d:
            continue
        start = add_days(d, 1)
        role = _pick(r, "officer_role", "primary_role", "role", "departure_type") or "officer"
        why = _pick(r, "stated_reason", "reason") or ""
        flag = "disagreement" if r.get("has_disagreement_flag") else "no-successor"
        out.append(_record(t, "5.02-gap", start, add_days(start, window_cal_days - 1),
                           f"5.02-gap {role} unexpected ({flag}): {str(why)[:160]}".strip()))
    return out


# ── 337: consume fr337 events (institution/id/final ladders only) ─────────────
def from_fr337_events(events, alias_index=None, hold_days=ITC337_HOLD_DAYS):
    """fr337.classify_doc() events -> exclusion rows.

    Only ladder stages institution/id/final (a filed complaint alone or misc
    traffic never excludes). Event = PIT date (public-inspection filing time
    else publication_date) + 1. Respondent tickers resolve via the lane hook;
    unresolved respondents are kept with firm/investigation for manual review.
    """
    out = []
    for ev in events or []:
        if (ev.get("ladder_stage") or "") not in ITC337_EXCLUDE_LADDERS:
            continue
        pit = parse_date((fr337.pit_timestamp(ev) if fr337 is not None
                          else ev.get("pit_timestamp")) or ev.get("publication_date"))
        if not pit:
            continue
        start = add_days(pit, 1)
        inv = ev.get("investigation_no") or ""
        made = False
        for resp in (ev.get("respondents_resolved") or []):
            t = _norm_ticker(resp.get("ticker"))
            if t is None and alias_index is not None and fr337 is not None:
                got = fr337.resolve_names([resp.get("raw", "")], alias_index)
                t = _norm_ticker(got[0].get("ticker")) if got else None
            out.append(_record(t, "337", start, add_days(start, hold_days - 1),
                               f"337 {ev.get('ladder_stage')} {inv}: "
                               f"{str(resp.get('raw', ''))[:120]}".strip(),
                               firm=resp.get("raw")))
            made = True
        if not made:
            out.append(_record(None, "337", start, add_days(start, hold_days - 1),
                               f"337 {ev.get('ladder_stage')} {inv}: "
                               f"{str(ev.get('title', ''))[:160]}".strip()))
    return out


def load_337_rows(rows, hold_days=ITC337_HOLD_DAYS):
    """Fallback: generic 337 JSONL ({ticker, investigation_date, ...})."""
    out = []
    for r in rows or []:
        if r.get("ladder_stage") is not None:
            continue  # lane events go through from_fr337_events
        t = _norm_ticker(_pick(r, "ticker", "symbol", "respondent_ticker"))
        d = parse_date(_pick(r, "investigation_date", "file_date", "filed_date",
                             "event_date", "date", "institution_date"))
        if not d:
            continue
        start = add_days(d, 1)
        inv = _pick(r, "investigation_no", "matter", "docket", "title") or ""
        out.append(_record(t, "337", start, add_days(start, hold_days - 1),
                           f"337 ITC investigation {inv}: import-exclusion risk".strip()))
    return out


# ── Merge: de-dupe overlapping windows (extend, no re-entry) ──────────────────
def _group_key(r):
    t = _norm_ticker(r.get("ticker"))
    if t:
        return t
    return "unresolved:" + str(r.get("firm", "?")).lower()


def merge_exclusions(records):
    """Merge overlapping windows per ticker (unresolved rows group by firm).

    Rows sorted by event_date; a row whose event_date falls on or before the
    running window_end extends it (max) instead of opening a new window.
    Sources/reasons union. NULL-ticker rows never merge across firms.
    Returns event-sorted merged list.
    """
    by_key = defaultdict(list)
    for r in records or []:
        d0, d1 = parse_date(r.get("event_date")), parse_date(r.get("window_end"))
        if not d0 or not d1 or d1 < d0:
            continue
        by_key[_group_key(r)].append({"ticker": _norm_ticker(r.get("ticker")),
                                      "firm": r.get("firm"),
                                      "source": str(r.get("source", "?")),
                                      "event_date": d0, "window_end": d1,
                                      "reason": str(r.get("reason", ""))})

    merged = []
    for rows in by_key.values():
        rows.sort(key=lambda r: (r["event_date"], r["window_end"]))
        cur = dict(rows[0])
        cur_sources = [cur["source"]]
        for nxt in rows[1:]:
            if nxt["event_date"] <= cur["window_end"]:
                cur["window_end"] = max(cur["window_end"], nxt["window_end"])
                if nxt["source"] not in cur_sources:
                    cur_sources.append(nxt["source"])
                if nxt["reason"] and nxt["reason"] not in cur["reason"]:
                    cur["reason"] = (cur["reason"] + " | " + nxt["reason"])[:300]
            else:
                merged.append(_emit(cur, sorted(cur_sources)))
                cur = dict(nxt)
                cur_sources = [cur["source"]]
        merged.append(_emit(cur, sorted(cur_sources)))
    merged.sort(key=lambda r: (r["event_date"], r["ticker"] or "~"))
    return merged


def _emit(cur, sources):
    r = {"ticker": cur["ticker"], "source": "+".join(sources),
         "event_date": cur["event_date"].isoformat(),
         "window_end": cur["window_end"].isoformat(), "reason": cur["reason"]}
    if cur["ticker"] is None and cur.get("firm"):
        r["firm"] = cur["firm"]
    return r


def build_index(merged):
    """Ticker -> sorted [(event_date, window_end, record)] for PIT lookup.

    NULL-ticker rows are unmatchable by ticker and excluded from the index
    (they are manual-review items, listed in the CSV/JSON with firm)."""
    idx = defaultdict(list)
    for r in merged or []:
        t = _norm_ticker(r.get("ticker"))
        d0, d1 = parse_date(r.get("event_date")), parse_date(r.get("window_end"))
        if t and d0 and d1:
            idx[t].append((d0, d1, r))
    for v in idx.values():
        v.sort()
    return dict(idx)


def is_excluded(ticker, asof, exclusions):
    """Return the covering exclusion record, or None. PIT-strict."""
    d = asof if isinstance(asof, dt.date) else parse_date(asof)
    t = _norm_ticker(ticker)
    if d is None or not t:
        return None
    if isinstance(exclusions, dict):
        windows = exclusions.get(t, [])
    else:
        windows = [((parse_date(r.get("event_date"))),
                    (parse_date(r.get("window_end"))), r)
                   for r in (exclusions or []) if _norm_ticker(r.get("ticker")) == t]
    for d0, d1, rec in windows:
        if d0 is None or d1 is None:
            continue
        if d0 <= d <= d1:
            return rec
    return None


def filter_signals(signals, exclusions, date_key="date"):
    """Split entry signals into (kept, skipped) on the exclusion list.

    Default-off at callers: pass exclusions=None to keep everything.
    """
    if not exclusions:
        return list(signals or []), []
    idx = exclusions if isinstance(exclusions, dict) else build_index(exclusions)
    kept, skipped = [], []
    for s in signals or []:
        d = s.get(date_key)
        d = d if isinstance(d, (dt.date, dt.datetime)) else parse_date(d)
        if d is None:
            kept.append(s)  # undated signal: cannot judge, keep for review
        elif is_excluded(s.get("ticker"), d, idx) is not None:
            rec = is_excluded(s.get("ticker"), d, idx)
            skipped.append({**s, "_excluded_by": rec})
        else:
            kept.append(s)
    return kept, skipped


# ── Hooks: sector caps + liquidity (caller injects PIT values) ───────────────
def enforce_sector_caps(candidates, sector_of, cap=0.25):
    """Greedy equal-weight sector-cap filter (input order kept).

    sector_of: dict {ticker: sector} or callable (ticker, asof)->sector|None.
    cap: max fraction of the kept book any one sector may occupy.
    Unknown-sector names are never capped. Returns (kept, dropped).
    """
    def sector_of_fn(t, asof=None):
        if callable(sector_of):
            try:
                return sector_of(t, asof)
            except TypeError:
                return sector_of(t)
        return (sector_of or {}).get(_norm_ticker(t) or "")

    kept, dropped = [], []
    sector_n = defaultdict(int)
    for c in candidates or []:
        t = c.get("ticker")
        sec = sector_of_fn(t, c.get("date"))
        if sec is None:
            kept.append(c)
            continue
        trial = sector_n[sec] + 1
        total = len(kept) + 1
        if trial / total <= cap + 1e-9:
            sector_n[sec] = trial
            kept.append(c)
        else:
            dropped.append({**c, "_drop_reason": f"sector-cap {sec} > {cap:.0%}"})
    return kept, dropped


def passes_liquidity(price, adv_dollars,
                     min_price=DEFAULT_MIN_PRICE,
                     min_adv=DEFAULT_MIN_ADV_DOLLARS):
    """True only when both PIT values clear. None fails closed."""
    if price is None or adv_dollars is None:
        return False
    try:
        return float(price) >= float(min_price) and float(adv_dollars) >= float(min_adv)
    except (TypeError, ValueError):
        return False


def filter_liquidity(signals, price_at, adv_at,
                     min_price=DEFAULT_MIN_PRICE,
                     min_adv=DEFAULT_MIN_ADV_DOLLARS,
                     date_key="date", on_missing="exclude"):
    """Split signals on injectable PIT price/ADV getters.

    price_at(ticker, date)->float|None, adv_at(ticker, date)->float|None.
    When NO getters are supplied at all the gate is visibly skipped (all
    kept, flagged _liquidity_checked=False — fda_avoid.apply_liquidity
    convention). Per-row missing data fails closed (exclude) unless
    on_missing='keep'.
    """
    if price_at is None and adv_at is None:
        return [{**s, "_liquidity_checked": False} for s in (signals or [])], []
    kept, dropped = [], []
    for s in signals or []:
        d = s.get(date_key)
        d = d if isinstance(d, (dt.date, dt.datetime)) else parse_date(d)
        try:
            px = price_at(s.get("ticker"), d) if price_at else None
        except Exception:
            px = None
        try:
            adv = adv_at(s.get("ticker"), d) if adv_at else None
        except Exception:
            adv = None
        if px is None or adv is None:
            if on_missing == "keep":
                kept.append({**s, "_liquidity_checked": False})
            else:
                dropped.append({**s, "_drop_reason": "liquidity-missing-PIT"})
            continue
        if passes_liquidity(px, adv, min_price, min_adv):
            kept.append({**s, "_liquidity_checked": True})
        else:
            dropped.append({**s, "_drop_reason":
                            f"illiquid px={px} adv={adv} < {min_price}/{min_adv:g}"})
    return kept, dropped


# ── Tiny paper portfolio helper (long-only, fixed holds, no PnL claim) ───────
def paper_long_only(signals, exclusions=None, hold_days=90, max_positions=20,
                    date_key="date"):
    """Paper-only cohort tracker: equal-weight, FIFO cap, fixed holds.

    Applies the exclusion list at entry when provided. Price-free: reports
    counts, capacity drops, and holding windows — not returns. Research only.
    """
    kept, skipped_ex = filter_signals(signals, exclusions, date_key) if exclusions else (
        list(signals or []), [])
    dated = [s for s in kept if parse_date(s.get(date_key)) is not None]
    undated = [s for s in kept if parse_date(s.get(date_key)) is None]
    dated.sort(key=lambda s: (parse_date(s.get(date_key)), _norm_ticker(s.get("ticker")) or ""))

    holdings, active_end = [], []
    skipped_cap = []
    for s in dated:
        entry = parse_date(s.get(date_key))
        active_end = [e for e in active_end if e > entry]
        if len(active_end) >= max_positions:
            skipped_cap.append(s)
            continue
        exit_d = add_days(entry, hold_days)
        active_end.append(exit_d)
        holdings.append({"ticker": _norm_ticker(s.get("ticker")),
                         "entry": entry.isoformat(), "exit": exit_d.isoformat()})
    concurrent = []
    for h in holdings:
        d0 = parse_date(h["entry"])
        concurrent.append(sum(1 for o in holdings
                              if parse_date(o["entry"]) <= d0 <= parse_date(o["exit"])))
    return {"n_signals": len(signals or []), "n_entered": len(holdings),
            "n_skipped_excluded": len(skipped_ex),
            "n_skipped_capacity": len(skipped_cap),
            "n_undated_kept_unscored": len(undated),
            "avg_concurrent": round(sum(concurrent) / len(concurrent), 2) if concurrent else 0.0,
            "max_concurrent": max(concurrent) if concurrent else 0,
            "hold_days": hold_days, "max_positions": max_positions,
            "holdings": holdings}


# ── I/O ──────────────────────────────────────────────────────────────────────
def load_jsonl_rows(path):
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_lane_rows(path):
    """Load a JSONL lane file, or an openFDA-style JSON ({results:[...]})."""
    text = Path(path).read_text()
    try:
        doc = json.loads(text)
        if isinstance(doc, dict) and isinstance(doc.get("results"), list):
            return doc["results"]
        if isinstance(doc, list):
            return doc
    except ValueError:
        pass
    return load_jsonl_rows(path)


def _looks_fda_avoid(rows):
    return bool(rows) and isinstance(rows[0], dict) and "entry_date" in rows[0] \
        and "report_date" in rows[0]


def _looks_stewardship(rows):
    return bool(rows) and isinstance(rows[0], dict) and "decision" in rows[0] \
        and ("exclude_start" in rows[0] or "event_id" in rows[0])


def _looks_fr337(rows):
    return bool(rows) and isinstance(rows[0], dict) and "ladder_stage" in rows[0]


def load_exclusions_json(path):
    """Load a merged exclusion file (bare list or {exclusions:[...]} envelope)."""
    d = json.loads(Path(path).read_text())
    rows = d.get("exclusions") if isinstance(d, dict) else d
    out = []
    for r in rows or []:
        if parse_date(r.get("event_date")) and parse_date(r.get("window_end")):
            out.append({"ticker": _norm_ticker(r.get("ticker")),
                        "source": str(r.get("source", "?")),
                        "event_date": parse_date(r.get("event_date")).isoformat(),
                        "window_end": parse_date(r.get("window_end")).isoformat(),
                        "reason": str(r.get("reason", ""))[:300]})
    return out


def save_exclusions_json(records, path, meta=None):
    doc = {"exclusions": records}
    if meta:
        doc = {**meta, **doc}
    Path(path).write_text(json.dumps(doc, indent=2))
    return path


def save_exclusions_csv(records, path):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["ticker", "source", "event_date",
                                           "window_end", "reason", "firm"])
        w.writeheader()
        for r in records or []:
            w.writerow({k: (r.get(k, "") or "") for k in
                        ("ticker", "source", "event_date", "window_end", "reason", "firm")})
    return path


def build_from_files(fda=None, cpsc=None, gap502=None, s337=None,
                     fda_category="device", alias_index=None):
    """Merge lane JSONLs (auto-detecting lane-output vs raw shapes)."""
    recs = []
    if fda and Path(fda).exists():
        rows = load_lane_rows(fda)
        recs += from_fda_avoid_rows(rows, alias_index) if _looks_fda_avoid(rows) \
            else load_fda_raw(rows, fda_category, alias_index)
    if cpsc and Path(cpsc).exists():
        recs += load_cpsc_rows(load_lane_rows(cpsc))
    if gap502 and Path(gap502).exists():
        rows = load_lane_rows(gap502)
        recs += from_stewardship_decisions(rows) if _looks_stewardship(rows) \
            else load_gap502_rows(rows)
    if s337 and Path(s337).exists():
        rows = load_lane_rows(s337)
        recs += from_fr337_events(rows, alias_index) if _looks_fr337(rows) \
            else load_337_rows(rows)
    return merge_exclusions(recs)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fda", default=None, help="fda_avoid exclusion JSONL or raw openFDA JSON/JSONL")
    ap.add_argument("--fda-category", default="device", help="raw-FDA fallback category (drug|device)")
    ap.add_argument("--cpsc", default=None)
    ap.add_argument("--gap502", default=None, help="stewardship decisions JSONL or raw departures JSONL")
    ap.add_argument("--s337", default=None, help="fr337 events JSONL or generic 337 JSONL")
    ap.add_argument("--out", default=None, help="merged exclusions JSON")
    ap.add_argument("--csv", default=None, help="Fidelity CSV export")
    ap.add_argument("--asof", default=None, help="print exclusions active on date")
    args = ap.parse_args(argv)

    merged = build_from_files(args.fda, args.cpsc, args.gap502, args.s337,
                              args.fda_category)
    by_src = defaultdict(int)
    for r in merged:
        by_src[r["source"]] += 1
    n_unresolved = sum(1 for r in merged if not r["ticker"])
    print(f"merged {len(merged)} exclusion windows {dict(by_src)} "
          f"({n_unresolved} unresolved-manual-review)")

    if args.asof:
        d = parse_date(args.asof)
        active = [r for r in merged
                  if parse_date(r["event_date"]) <= d <= parse_date(r["window_end"])]
        print(f"active on {d}: {len(active)}")
        for r in active[:20]:
            print(json.dumps(r))

    if args.out:
        save_exclusions_json(merged, args.out, meta={
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "sources": {k: v for k, v in
                        (("fda", args.fda), ("cpsc", args.cpsc),
                         ("gap502", args.gap502), ("s337", args.s337)) if v},
            "holds": {"FDA_drug": FDA_HOLD["drug"], "FDA_device": FDA_HOLD["device"],
                      "CPSC": CPSC_HOLD_DAYS, "GAP502_cal_days": GAP502_CAL_DAYS,
                      "ITC337": ITC337_HOLD_DAYS},
            "note": "Research only, not trading advice. PIT-strict entry filter."})
        print(f"wrote {args.out}")
    if args.csv:
        save_exclusions_csv(merged, args.csv)
        print(f"wrote {args.csv}")
    if not args.out and not args.csv and not args.asof:
        print(json.dumps(merged[:20], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
