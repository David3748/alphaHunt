#!/usr/bin/env python3
"""Live predictions board: locked century rules applied to the 2026 cohort.

Calibration is strictly causal: every threshold and z-score normalization uses
only pooled scores from strictly earlier cutoff dates (century 2009-2025 plus
earlier live cases), warmup 50 prior cases. No outcome data is touched.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("p20", "exp", "ppos", "down")


def mean_scores(path: Path) -> dict:
    by_case = defaultdict(list)
    for line in path.open(encoding="utf-8"):
        row = json.loads(line)
        if isinstance(row.get("result"), dict):
            by_case[row["case_id"]].append(row["result"])
    out = {}
    for case_id, results in by_case.items():
        def mean(field):
            values = [r[field] for r in results if isinstance(r.get(field), (int, float))]
            return statistics.fmean(values) if values else None
        out[case_id] = {"p20": mean("probability_plus20_excess_90d_pct"),
                        "exp": mean("expected_excess_return_90d_pct"),
                        "ppos": mean("probability_positive_excess_90d_pct"),
                        "down": mean("downside_tail_probability_pct"),
                        "texts": results}
    return out


def load_pool() -> list[dict]:
    index = {}
    with (ROOT / "lab_runs/century_safety/case_index.csv").open() as handle:
        for row in csv.DictReader(handle):
            index[row["case_id"]] = row
    pool = []
    for case_id, scores in mean_scores(ROOT / "lab_runs/century_safety/syntheses.jsonl").items():
        meta = index.get(case_id)
        if not meta or any(scores[f] is None for f in FIELDS) or not meta["drawdown"]:
            continue
        pool.append({"case_id": case_id, "cutoff": meta["cutoff"], "ticker": meta["ticker"],
                     "company": "", "drawdown": float(meta["drawdown"]),
                     "cohort": "century", **{f: scores[f] for f in FIELDS},
                     "texts": scores["texts"]})
    live_cases = {}
    for line in (ROOT / "lab_runs/live_2026/cases.jsonl").open(encoding="utf-8"):
        row = json.loads(line)
        market = row.get("market_at_cutoff") or {}
        live_cases[row["case_id"]] = {
            "cutoff": row["cutoff"], "ticker": row.get("ticker") or "",
            "company": row.get("company") or "",
            "drawdown": market.get("drawdown_from_1y_high")}
    for case_id, scores in mean_scores(ROOT / "lab_runs/live_2026/syntheses.jsonl").items():
        meta = live_cases.get(case_id)
        if not meta or any(scores[f] is None for f in FIELDS) or meta["drawdown"] is None:
            continue
        pool.append({"case_id": case_id, **meta, "cohort": "live_2026",
                     **{f: scores[f] for f in FIELDS}, "texts": scores["texts"]})
    return sorted(pool, key=lambda r: r["cutoff"])


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low, high = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


WARMUP = 50


def causal_scores(pool: list[dict]) -> None:
    """Attach rule scores + thresholds from strictly-earlier priors only."""
    prior_fields = defaultdict(list)
    prior_rules = defaultdict(list)
    by_date = defaultdict(list)
    for row in pool:
        by_date[row["cutoff"]].append(row)
    for cutoff in sorted(by_date):
        enough = len(prior_fields["p20"]) >= WARMUP
        stats = {f: (statistics.fmean(prior_fields[f]),
                     statistics.pstdev(prior_fields[f]) or 1.0) for f in FIELDS} if enough else None
        for row in by_date[cutoff]:
            row["score_p20"] = row["p20"]
            row["score_uxd"] = row["p20"] * abs(row["drawdown"])
            if enough:
                z = statistics.fmean(
                    ((row[f] - stats[f][0]) / stats[f][1]) * sign
                    for f, sign in (("p20", 1), ("exp", 1), ("ppos", 1), ("down", -1)))
                row["score_blend"] = z * 100
                row["thr_p20"] = quantile(prior_rules["p20"], 0.9)
                row["thr_uxd"] = quantile(prior_rules["uxd"], 0.9)
            else:
                row["score_blend"] = None
            if row.get("score_blend") is not None and len(prior_rules["blend"]) >= WARMUP:
                row["thr_blend"] = quantile(prior_rules["blend"], 0.9)
            prior_fields["p20"].append(row["p20"])
            prior_fields["exp"].append(row["exp"])
            prior_fields["ppos"].append(row["ppos"])
            prior_fields["down"].append(row["down"])
            prior_rules["p20"].append(row["score_p20"])
            prior_rules["uxd"].append(row["score_uxd"])
            if row.get("score_blend") is not None:
                prior_rules["blend"].append(row["score_blend"])


def selected_text(row: dict) -> dict:
    texts = row["texts"]
    best = max(texts, key=lambda r: len(r.get("thesis") or ""))
    return {"ticker": row["ticker"], "company": row["company"], "cutoff": row["cutoff"],
            "thesis": best.get("thesis"), "catalyst": best.get("catalyst"),
            "invalidation": best.get("invalidation"),
            "decision": best.get("decision"),
            "p20": round(row["p20"], 1), "drawdown_pct": round(row["drawdown"] * 100, 1),
            "scores": {k: (None if row.get(f"score_{k}") is None else round(row[f"score_{k}"], 2))
                       for k in ("p20", "uxd", "blend")},
            "thresholds": {k: (None if row.get(f"thr_{k}") is None else round(row[f"thr_{k}"], 2))
                           for k in ("p20", "uxd", "blend")}}


def main() -> int:
    pool = load_pool()
    causal_scores(pool)
    live = [row for row in pool if row["cohort"] == "live_2026"]
    rules = ("p20", "uxd", "blend")
    board, summary = {}, {}
    for rule in rules:
        picked = [row for row in live
                  if row.get(f"score_{rule}") is not None and row.get(f"thr_{rule}") is not None
                  and row[f"score_{rule}"] >= row[f"thr_{rule}"]]
        picked.sort(key=lambda r: (-r[f"score_{rule}"], r["cutoff"]))
        board[rule] = [selected_text(row) for row in picked]
        months = defaultdict(int)
        for row in picked:
            months[row["cutoff"][:7]] += 1
        summary[rule] = {"signals": len(picked), "by_month": dict(sorted(months.items())),
                         "median_score": statistics.median([r[f"score_{rule}"] for r in picked])
                         if picked else None}
    payload = {"generated_at": __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat(),
        "live_cases_scored": len(live), "summary": summary, "board": board}
    out_dir = ROOT / "lab_runs/live_2026"
    (out_dir / "predictions.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = ["# Live predictions — 2026 forward cohort", "",
             f"Generated: {payload['generated_at']} | scored cases: {len(live)}",
             "Rules are the frozen century secondaries; thresholds are expanding top-decile",
             "quantiles over all strictly-earlier scores (2009–2025 + earlier 2026 cases).", ""]
    for rule in rules:
        lines += [f"## Rule: {rule} — {summary[rule]['signals']} signals", "",
                  "| ticker | cutoff | score (thr) | p+20 | drawdown% | decision |", "| --- | --- | --- | --- | --- | --- |"]
        for row in board[rule][:40]:
            s = row["scores"][rule]
            t = row["thresholds"][rule]
            lines.append(f"| {row['ticker']} | {row['cutoff']} | {s:.1f} ({t:.1f}) | "
                         f"{row['p20']:.0f} | {row['drawdown_pct']:+.0f} | {row['decision']} |")
        lines.append("")
    (out_dir / "predictions_board.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
