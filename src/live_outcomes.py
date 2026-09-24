#!/usr/bin/env python3
"""Materialize outcomes for matured live-cohort signals (forward test check).

Selection is identical to the board (locked rules, causal thresholds); trades
follow the frozen protocol conventions: next eligible close after acceptance,
90-calendar-day hold, conservative terminal bound, excess vs SPY over the same
window. Only signals whose 90-day window has fully elapsed are graded.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sealed_safety_eval as ev
from live_predictions import ROOT, causal_scores, load_pool

RUN_DIR = ROOT / "lab_runs/live_2026"
RULES = ("p20", "uxd", "blend")
TODAY = dt.date.today()


def main() -> int:
    pool = load_pool()
    causal_scores(pool)
    live = [row for row in pool if row["cohort"] == "live_2026"]
    by_case = {}
    for line in (RUN_DIR / "cases.jsonl").open(encoding="utf-8"):
        row = json.loads(line)
        by_case[row["case_id"]] = row

    graded = []
    for rule in RULES:
        picked = [row for row in live
                  if row.get(f"score_{rule}") is not None and row.get(f"thr_{rule}") is not None
                  and row[f"score_{rule}"] >= row[f"thr_{rule}"]]
        for row in picked:
            cutoff = dt.date.fromisoformat(row["cutoff"])
            if (TODAY - cutoff).days < 92:
                continue  # window not fully elapsed
            wrapper = by_case.get(row["case_id"])
            if not wrapper:
                continue
            graded.append({"rule": rule, "case": wrapper, "row": row})
    tickers = sorted({g["case"]["ticker"] for g in graded} | {"SPY"})
    print(f"grading {len(graded)} matured signals across {len(tickers)-1} tickers", file=sys.stderr)
    # fetch_prices spans [cutoff - 400d, cutoff + 200d]; anchor it so the window ends
    # today (a fixed anchor silently truncated prices and left later exits ungraded)
    anchor = (TODAY - dt.timedelta(days=200)).isoformat()
    fake_rows = [{"ticker": t, "cutoff": anchor} for t in tickers]
    prices = ev.fetch_prices(fake_rows, RUN_DIR, concurrency=12)
    spy = {r["date"]: r["close"] for r in prices.get("SPY", [])}

    results = []
    for g in graded:
        case = dict(g["case"])
        trade, status = ev.make_trade(case, prices, "conservative")
        if not trade or not trade.get("entry_date"):
            results.append({"rule": g["rule"], "ticker": case["ticker"],
                            "cutoff": case["cutoff"], "status": status or "no_entry"})
            continue
        e_spy, x_spy = spy.get(trade["entry_date"]), spy.get(trade["exit_date"])
        bench = x_spy / e_spy - 1 if e_spy and x_spy else None
        rel = trade["stock_return"] - bench if bench is not None else None
        results.append({
            "rule": g["rule"], "ticker": case["ticker"], "cutoff": case["cutoff"],
            "score": round(g["row"][f"score_{g['rule']}"], 2),
            "threshold": round(g["row"][f"thr_{g['rule']}"], 2),
            "entry_date": trade["entry_date"], "exit_date": trade["exit_date"],
            "stock_return_pct": round(trade["stock_return"] * 100, 1),
            "spy_return_pct": round(bench * 100, 1) if bench is not None else None,
            "excess_pct": round(rel * 100, 1) if rel is not None else None,
            "status": status,
        })
    ok = [r for r in results if r.get("excess_pct") is not None]
    summary = {}
    for rule in RULES:
        rows = [r for r in ok if r["rule"] == rule]
        if rows:
            excesses = [r["excess_pct"] for r in rows]
            summary[rule] = {
                "n": len(rows),
                "mean_excess_pct": round(statistics.fmean(excesses), 1),
                "median_excess_pct": round(statistics.median(excesses), 1),
                "win_rate": round(sum(e > 0 for e in excesses) / len(excesses), 2)}
    out = {"generated_at": TODAY.isoformat(), "summary": summary, "trades": results}
    (RUN_DIR / "outcomes_forward.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    for rule in RULES:
        rows = sorted([r for r in ok if r["rule"] == rule], key=lambda r: -r["score"])
        print(f"\n== {rule}: {summary.get(rule)}")
        for r in rows:
            print(f"  {r['ticker']:5} {r['entry_date']} -> {r['exit_date']}  "
                  f"stock {r['stock_return_pct']:+.1f}% vs SPY {r['spy_return_pct']:+.1f}%  "
                  f"excess {r['excess_pct']:+.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
