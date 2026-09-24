#!/usr/bin/env python3
"""lda_pivots.py — first-ever federal lobbying registrations as alpha events.

Signal: a company registering to lobby for the FIRST time (LD-1 / RR filing)
positions itself for a regulatory catalyst before headlines exist. Long 90d.

Stages: pull | resolve | backtest | run
"""

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import ox_lab
from alias_resolve import build_index, resolve, norm

RUN = ROOT / "lab_runs" / "unstructured_proto"
# Canonical host is lda.gov (lda.senate.gov 301-redirects there as of 2026-09).
BASE = "https://lda.gov/api/v1/filings/"
UA = {"User-Agent": "alphaHunt research contact@example.com"}


def cmd_pull(run_dir):
    out_path = run_dir / "lda_registrations.jsonl"
    if out_path.exists():
        print(f"lda already pulled -> {out_path}")
        return
    http = ox_lab.CachedHTTP(run_dir / "cache" / "http", min_interval=0.15)
    url = BASE + "?filing_type=RR&filing_dt_posted_gte=2012-01-01&page_size=100&limit=100"
    n = 0
    pages = 0
    while url and pages < 3000:
        d = None
        for attempt in range(3):
            try:
                d = http.json(url)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"  pull error at page {pages}: {e}", flush=True)
                    url = None
                time.sleep(1.0)
        if d is None:
            break
        for r in d.get("results", []):
            client = (r.get("client") or {}).get("name") or ""
            posted = r.get("filing_dt_posted") or ""
            acts = r.get("lobbying_activities") or []
            bills = []
            issues = []
            for a in acts:
                for b in (a.get("bills") or []):
                    code = b.get("bill_number") or ""
                    if code:
                        bills.append(code)
                iss = a.get("issue_code") or ""
                if iss:
                    issues.append(iss)
            ox_lab.append_jsonl(out_path, {
                "client": client,
                "posted": posted,
                "legacy_dt": r.get("dt_posted"),
                "expenses": r.get("expenses"),
                "income": r.get("income"),
                "bills": bills[:12],
                "issues": issues[:8],
                "uuid": r.get("filing_uuid"),
            })
            n += 1
        url = d.get("next")
        pages += 1
        if pages % 50 == 0:
            print(f"  {n} registrations ({pages} pages)", flush=True)
        time.sleep(0.05)
    print(f"pulled {n} registrations over {pages} pages", flush=True)


def cmd_resolve(run_dir):
    out_path = run_dir / "lda_resolved.jsonl"
    idx = build_index(run_dir)
    rows = ox_lab.load_jsonl(run_dir / "lda_registrations.jsonl")
    # earliest registration per normalized client
    first = {}
    for r in rows:
        key = norm(r.get("client", ""))
        if not key:
            continue
        p = r.get("posted", "")
        if key not in first or p < first[key]["posted"]:
            first[key] = r
    print(f"{len(first)} unique clients; resolving...", flush=True)
    matched = 0
    for key, r in first.items():
        # resolve() takes the raw client name.
        t, c, conf = resolve(r.get("client", ""), idx)
        if t:
            matched += 1
            row = {**r, "ticker": t, "cik": c, "match_confidence": conf}
            ox_lab.append_jsonl(out_path, row)
    print(f"resolved {matched}/{len(first)} clients -> {out_path}", flush=True)


def cmd_backtest(run_dir):
    from backtest_signals import fetch_all_prices, forward_excess, parse_date
    rows = ox_lab.load_jsonl(run_dir / "lda_resolved.jsonl")
    tickers = {r["ticker"] for r in rows if r.get("ticker")}
    prices = fetch_all_prices(tickers, run_dir)
    spy = prices.get("SPY", [])

    events = []
    for r in rows:
        t = r["ticker"]
        d = parse_date(r.get("posted"))
        if not d or t not in prices:
            continue
        ex = forward_excess(prices[t], spy, d, 90)
        if ex is None:
            continue
        events.append({"ticker": t, "date": d.isoformat(), "excess": round(ex * 100, 2),
                       "client": r.get("client"), "expenses": r.get("expenses"),
                       "nbills": len(r.get("bills", []))})

    xs = [e["excess"] for e in events]
    print(f"\nevents: {len(events)}, mean 90d excess {mean(xs):+.2f}%"
          if xs else "no events")

    yrs = defaultdict(list)
    for e in events:
        yrs[e["date"][:4]].append(e["excess"])
    for y in sorted(yrs):
        ys = yrs[y]
        win = sum(1 for x in ys if x > 0) / len(ys) * 100
        print(f"  {y}: n={len(ys):>4} mean={mean(ys):>+7.2f}% win={win:.0f}%")

    out = run_dir / "lda_events.json"
    out.write_text(json.dumps(events, indent=1))
    print(f"saved -> {out}")


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["pull", "resolve", "backtest", "run"])
    args = ap.parse_args()
    run_dir = RUN
    (run_dir / "cache" / "http").mkdir(parents=True, exist_ok=True)
    if args.stage in ("pull", "run"):
        cmd_pull(run_dir)
    if args.stage in ("resolve", "run"):
        cmd_resolve(run_dir)
    if args.stage in ("backtest", "run"):
        cmd_backtest(run_dir)


if __name__ == "__main__":
    main()