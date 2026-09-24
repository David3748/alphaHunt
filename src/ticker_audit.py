#!/usr/bin/env python3
"""Which cases were priced on another company's stock?

The symbol resolver takes a filing's ticker from its XBRL dei:TradingSymbol fact and, when a
filing has none, falls back to the stem of the XBRL file name (``gogo-20251231.xml`` -> GOGO).
The stem is usually the issuer's own ticker. For non-traded funds, shells and subsidiaries it can
be someone else's: Go Go Buyers, Inc. (a shell) was priced as Gogo Inc., and the Ridgewood Energy
S, U and W funds as SentinelOne, Unity and Wayfair.

Two checks, both from data already on disk:
- Live 2026 cohort (exact): the resolver recorded where each ticker came from. A file-name
  ticker that another filer reports as its own dei:TradingSymbol is a collision.
- Every case (screen): the 2019-20 and 2025-26 runs recorded each filer's own dei:TradingSymbol,
  which names the CIK that owns each ticker. A case whose CIK is not its ticker's owner is flagged
  "probable" if the owner also filed in the dataset that year (two issuers, one ticker, one year)
  and "possible" otherwise: a ticker reassigned after the case, a holding-company reorganisation,
  or a subsidiary priced on its parent can also land there.
Then the backtest ledger and the live trades are checked, and the headline metrics are recomputed
without the flagged cases.

    python3 src/ticker_audit.py [--results results/ticker_audit]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redaction_audit as ra

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data/audit_inputs/dataset.csv"
LIVE_PREPRICE = ROOT / "lab_runs/live_2026/preprice.jsonl"
LIVE_OUTCOMES = ROOT / "data/audit_inputs/live_outcomes_forward.json"
LEDGER = ROOT / "sites/strategy-site/public/data/p_plus20_trades.json"
SYMBOL_FILES = [ROOT / "lab_runs/sealed_safety/symbols.jsonl", ROOT / "lab_runs/live_2026/symbols.jsonl"]


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def iso(filed: str) -> str:
    return f"{filed[:4]}-{filed[4:6]}-{filed[6:8]}" if len(filed) == 8 else filed


def live_stem_collisions(preprice: list[dict]) -> list[dict]:
    """Eligible live filings whose file-name ticker is another filer's dei:TradingSymbol."""
    owners = defaultdict(dict)
    for r in preprice:
        if r.get("symbol_source") == "dei_fact" and r.get("symbol"):
            owners[r["symbol"]][r["cik"]] = r["company"]
    out = []
    for r in preprice:
        if r.get("status") == "eligible" and r.get("symbol_source") == "instance_stem":
            other = {cik: name for cik, name in owners.get(r["symbol"], {}).items() if cik != r["cik"]}
            out.append({"cik": r["cik"], "company": r["company"], "ticker": r["symbol"], "filed": iso(r["filed"]),
                        "priced_as": sorted(other.values()), "collision": bool(other)})
    return out


def dei_owners(paths: list[Path] = SYMBOL_FILES) -> dict[str, set[str]]:
    """ticker -> CIKs that report it as their own dei:TradingSymbol."""
    owners: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        if path.exists():
            for r in load_jsonl(path):
                for sym in r.get("symbols") or []:
                    owners[str(sym).strip().upper().replace(".", "-")].add(r["cik"])
    return owners


def owner_flags(cases: list[dict], owners: dict[str, set[str]]) -> dict[str, dict]:
    """case_id -> details for cases whose CIK is not the owner of the ticker they were priced on."""
    filed = defaultdict(set)
    for c in cases:
        filed[(c["ticker"], c["cutoff"][:4])].add(c["cik"])
    flags = {}
    for c in cases:
        own = owners.get(c["ticker"])
        if own and c["cik"] not in own:
            tier = "probable" if filed[(c["ticker"], c["cutoff"][:4])] & own else "possible"
            flags[c["case_id"]] = {"ticker": c["ticker"], "company": c["company"], "cik": c["cik"],
                                   "cutoff": c["cutoff"], "tier": tier}
    return flags


def load_cases() -> list[dict]:
    """Every case with its CIK and company name, historical and live."""
    meta = {}
    recovered = ra.CENTURY_PACKS.parent / "recovered.jsonl"
    for r in load_jsonl(recovered):
        meta[r["comprehensive_case_id"]] = (r["cik"], r["company"])
    for r in load_jsonl(ra.LIVE_PACKS):
        meta[r["case_id"]] = (str(r["cik"]), r["company"])
    cases = []
    with DATASET.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["case_id"] in meta and r["ticker"]:
                cik, company = meta[r["case_id"]]
                cases.append({"case_id": r["case_id"], "ticker": r["ticker"], "cutoff": r["cutoff"],
                              "cik": cik, "company": company, "excess": float(r["excess"])})
    return cases


def suspect_case_ids(tiers: tuple[str, ...] = ("probable", "possible")) -> set[str]:
    """Cases whose outcome is probably another security's."""
    cases = load_cases()
    flagged = {cid for cid, f in owner_flags(cases, dei_owners()).items() if f["tier"] in tiers}
    if LIVE_PREPRICE.exists():
        bad = {(r["cik"], r["filed"]) for r in live_stem_collisions(load_jsonl(LIVE_PREPRICE)) if r["collision"]}
        flagged |= {c["case_id"] for c in cases if (c["cik"], c["cutoff"]) in bad}
    return flagged


def metric_sensitivity(flagged: dict[str, dict]) -> dict:
    """Headline ranking metrics with and without the flagged cases."""
    import forecast_audit as fa
    df = fa.load()
    out = {}
    for name, mask in fa.PERIODS.items():
        sub = df[mask(df)]
        for label, frame in (("all", sub), ("without_probable", sub[~sub.case_id.isin(
                {k for k, f in flagged.items() if f["tier"] == "probable"})]),
                             ("without_any_flag", sub[~sub.case_id.isin(set(flagged))])):
            ics = fa.monthly_ics(frame)
            out.setdefault(name, {})[label] = {"n": int(len(frame)), "monthly_ic": float(ics.mean()),
                                               "auc_hit": fa.auc(frame.p20, frame.hit)}
    return out


def audit(results: Path) -> dict:
    cases = load_cases()
    flags = owner_flags(cases, dei_owners())
    live = live_stem_collisions(load_jsonl(LIVE_PREPRICE)) if LIVE_PREPRICE.exists() else []
    collisions = [r for r in live if r["collision"]]
    by_key = {(c["ticker"], c["cutoff"], c["company"].upper().strip()): c for c in cases}
    flagged_keys = {(c["ticker"], c["cutoff"]) for c in cases if c["case_id"] in flags}
    live_bad = {(r["ticker"], r["filed"]) for r in collisions}

    ledger = json.loads(LEDGER.read_text())["trades"]
    backtest_hits = []
    for t in ledger:
        c = by_key.get((t["ticker"], t["cutoff"], t["company"].upper().strip()))
        if c and c["case_id"] in flags:
            backtest_hits.append({"ticker": t["ticker"], "cutoff": t["cutoff"], "company": c["company"],
                                  "excess": t["excess_return"], "tier": flags[c["case_id"]]["tier"]})
    live_trades = json.loads(LIVE_OUTCOMES.read_text())["trades"]
    live_hits = [{"rule": t["rule"], "ticker": t["ticker"], "cutoff": t["cutoff"], "excess_pct": t["excess_pct"]}
                 for t in live_trades
                 if (t["ticker"], t["cutoff"]) in live_bad | flagged_keys]

    out = {"cases": len(cases),
           "live_file_name_tickers": len(live),
           "live_file_name_collisions": collisions,
           "owner_flags": {tier: sum(f["tier"] == tier for f in flags.values()) for tier in ("probable", "possible")},
           "owner_flags_by_ticker": dict(sorted(_count(f["ticker"] for f in flags.values()).items(),
                                                key=lambda kv: -kv[1])),
           "owner_flags_by_year": dict(sorted(_count(f["cutoff"][:4] for f in flags.values()).items())),
           "ford_example": sorted({f["company"] for f in flags.values() if f["ticker"] == "F"})[:12],
           "backtest_trades": len(ledger), "backtest_trades_flagged": backtest_hits,
           "live_trades": len(live_trades), "live_trades_flagged": live_hits,
           "metric_sensitivity": metric_sensitivity(flags)}
    results.mkdir(parents=True, exist_ok=True)
    (results / "summary.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    (results / "report.md").write_text(report(out), encoding="utf-8")
    return out


def _count(values) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for v in values:
        counts[v] += 1
    return counts


def report(out: dict) -> str:
    L = ["# Ticker audit", "",
         "Which cases were priced on another company's stock? See `src/ticker_audit.py` for the method.", "",
         f"## Live 2026 cohort: tickers taken from the XBRL file name", "",
         f"{out['live_file_name_tickers']} eligible filings had no dei:TradingSymbol, so the resolver used the file "
         f"name. {len(out['live_file_name_collisions'])} of them collide with another filer's ticker:", "",
         "| filed | filer | ticker | priced as |", "| --- | --- | --- | --- |"]
    for r in out["live_file_name_collisions"]:
        L.append(f"| {r['filed']} | {r['company']} | {r['ticker']} | {'; '.join(r['priced_as'])} |")
    n_flag = sum(out["owner_flags"].values())
    L += ["", "## All cases: priced on a ticker another CIK reports as its own", "",
          f"{n_flag} of {out['cases']} cases ({n_flag / out['cases']:.1%}): {out['owner_flags']['probable']} probable "
          f"(the ticker's owner also filed that year) and {out['owner_flags']['possible']} possible (may be a ticker "
          "reassigned later, a reorganisation, or a subsidiary priced on its parent).", "",
          "Most affected tickers: " + ", ".join(f"{t} ({n})" for t, n in list(out["owner_flags_by_ticker"].items())[:12])
          + ". Filers priced as Ford (F) include " + ", ".join(out["ford_example"][:8]) + ".", "",
          "By year: " + ", ".join(f"{y} {n}" for y, n in out["owner_flags_by_year"].items()) + ".", "",
          "## Effect on the headline metrics", "",
          "| period | cases | monthly IC | AUC (+20 pp) | without probable | without any flag |",
          "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for period, m in out["metric_sensitivity"].items():
        a, b, c = m["all"], m["without_probable"], m["without_any_flag"]
        L.append(f"| {period} | {a['n']:,} | {a['monthly_ic']:.3f} | {a['auc_hit']:.3f} | "
                 f"IC {b['monthly_ic']:.3f}, AUC {b['auc_hit']:.3f} (n={b['n']:,}) | "
                 f"IC {c['monthly_ic']:.3f}, AUC {c['auc_hit']:.3f} (n={c['n']:,}) |")
    L += ["", "## Trades", "",
          f"- Backtest P(+20%) ledger: {len(out['backtest_trades_flagged'])} of {out['backtest_trades']} trades flagged"
          + (": " + "; ".join(f"{h['ticker']} {h['cutoff']} ({h['company']}, {h['excess']:+.1%}, {h['tier']})"
                               for h in out["backtest_trades_flagged"]) if out["backtest_trades_flagged"] else "") + ".",
          f"- Live 2026 locked rules: {len(out['live_trades_flagged'])} of {out['live_trades']} trades flagged"
          + (": " + "; ".join(f"{h['rule']} {h['ticker']} {h['cutoff']}" for h in out["live_trades_flagged"])
             if out["live_trades_flagged"] else "") + "."]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", type=Path, default=ROOT / "results/ticker_audit")
    args = ap.parse_args()
    out = audit(args.results)
    print(report(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
