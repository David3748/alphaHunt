#!/usr/bin/env python3
"""combine_p20_screen.py — apply the forensic avoidance screen to the P(+20%) book.

Joins the P(+20%) trade ledger to per-case forensic red flags, then compares
the original book against screened variants (exclude flagged entries).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import ox_lab

RUN_DIR = ROOT / "lab_runs" / "unstructured_proto"
LEDGER = ROOT / "lab_runs" / "century_safety" / "site_export" / "p_plus20_trades.json"

LENSES = [
    "going_concern", "loss_contingency", "related_party", "goodwill_impairment",
    "debt_covenant", "revenue_recognition", "customer_concentration",
    "supplier_concentration", "pension_assumptions", "subsequent_events",
    "mda_consistency",
]


def load_flags():
    cases = {}
    for lens in LENSES:
        p = RUN_DIR / "forensic" / f"{lens}.jsonl"
        if not p.exists():
            continue
        seen = set()
        for r in ox_lab.load_jsonl(p):
            cid = r.get("case_id")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            c = cases.setdefault(cid, {"flags": 0, "sev": 0.0, "gc": False, "lenses_red": []})
            red = bool(r.get("is_red_flag"))
            try:
                sev = float(r.get("severity") or 0)
            except (TypeError, ValueError):
                sev = 0.0
            c["sev"] += sev
            if red:
                c["flags"] += 1
                c["lenses_red"].append(lens)
                if lens == "going_concern":
                    c["gc"] = True
    return cases


def main():
    ledger = json.load(open(LEDGER))
    trades = ledger["trades"]
    print(f"P(+20%) trades: {len(trades)}")
    flags = load_flags()
    print(f"forensic-flagged cases: {len(flags)}")

    joined = []
    misses = 0
    for t in trades:
        f = flags.get(t.get("case_id", ""))
        if f is None:
            # try comprehensive id fallback
            f = flags.get(t.get("comprehensive_case_id", ""))
        if f is None:
            misses += 1
            f = {"flags": None, "sev": None, "gc": False}
        t2 = {**t, "n_flags": f["flags"], "sev_sum": f["sev"], "gc_flag": f["gc"]}
        joined.append(t2)
    print(f"joined: {len(joined)-misses}/{len(joined)} ({misses} missing forensic data)\n")

    def stats(rows, label):
        if not rows:
            print(f"{label:<28} n=0")
            return
        rets = [r.get("net_trade_return") if r.get("net_trade_return") is not None
                else r.get("stock_return", 0) for r in rows]
        wins = sum(1 for x in rets if x > 0)
        mult = 1.0
        for r in sorted(rows, key=lambda x: x.get("exit_date", "")):
            mult *= (1.0 + (r.get("net_trade_return") if r.get("net_trade_return") is not None else r.get("stock_return", 0)))
        mean_r = sum(rets) / len(rets) * 100
        print(f"{label:<28} n={len(rows):>3}  mean/trade={mean_r:>6.2f}%  win={wins/len(rows)*100:.0f}%  compounded=x{mult:,.1f}")

    stats(joined, "original book (all)")
    stats([t for t in joined if t["n_flags"] is not None and t["n_flags"] == 0], "screened: 0 flags only")
    stats([t for t in joined if t["n_flags"] is None or t["n_flags"] < 1], "exclude 1+ flags")
    stats([t for t in joined if t["n_flags"] is None or t["n_flags"] < 2], "exclude 2+ flags")
    stats([t for t in joined if not t["gc_flag"]], "exclude going-concern")

    # bucket means
    print("\n=== mean trade return by flag count ===")
    buckets = {}
    for t in joined:
        b = t["n_flags"]
        if b is None:
            continue
        buckets.setdefault(min(b, 4), []).append(
            t.get("net_trade_return") if t.get("net_trade_return") is not None else t.get("stock_return", 0))
    for b in sorted(buckets):
        xs = buckets[b]
        print(f"  {b}+ flags: n={len(xs):>3}  mean={sum(xs)/len(xs)*100:>6.2f}%  win={sum(1 for x in xs if x>0)/len(xs)*100:.0f}%")

    # which lenses fire most among P(+20%) names
    from collections import Counter
    lens_hits = Counter()
    for t in joined:
        f = flags.get(t.get("case_id", "")) or {}
        for l in f.get("lenses_red", []):
            lens_hits[l] += 1
    print("\n=== red-flag lenses firing on P(+20%) names ===")
    for l, n in lens_hits.most_common():
        print(f"  {l}: {n}")

    out = RUN_DIR / "p20_screen_join.json"
    out.write_text(json.dumps(joined, indent=1, default=str))
    print(f"\nsaved → {out}")


if __name__ == "__main__":
    main()