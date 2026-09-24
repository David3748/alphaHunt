#!/usr/bin/env python3
"""Does a backtest of an LLM forecaster overstate its skill? Same model, same prompt, same
evidence; only the outcome's position relative to the model's training data changes.

Design (seeded; one case per fresh Claude Haiku 4.5 context):
- "In training": 50 filings dated 2023-01-01 to 2024-09-30, so every 90-day outcome was
  public before Haiku 4.5's training data ends (reliable knowledge through early 2025).
- "After training": 50 filings dated 2025-08-01 or later, whose outcomes postdate it.
- Each arm: 25 stocks that beat SPY by >= 25 points over the next 90 days and 25 that
  trailed by >= 25, spread across filing months.
- Evidence: 10,000 characters of the filing's MD&A, strictly scrubbed of name, ticker,
  URLs and tax id (redaction_audit.strict_scrub), plus the filing date.
- Instructions mirror the backtest forecaster's system prompt verbatim on leakage: use
  ONLY the excerpt; no outside knowledge, remembered issuer identity, prices, or later
  events. No question about the company's identity is asked.
- Cases from earlier probes and mis-mapped tickers are excluded.

The same cases are also scored by the backtest's own forecaster (Ox P(+20%)) and by price
features, so a drop that is specific to what the model could have memorised can be told
apart from a regime that is simply harder to forecast.

    python3 src/cutoff_probe.py build --out-dir <dir>
    python3 src/cutoff_probe.py score --probe-dir results/cutoff_probe
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redaction_audit as ra
import recall_probe as rp

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data/audit_inputs/dataset.csv"
WALK_FORWARD = ROOT / "lab_runs/llm_incremental/walk_forward_ridge.csv"
USED = [ROOT / "results/recall_probe/answer_key.json", ROOT / "results/memorization_probe/answer_key.json"]
ARMS = {"in_training": ("2023-01-01", "2024-09-30"), "after_training": ("2025-08-01", "2026-12-31")}
THRESHOLD = 0.25
EXCERPT_CHARS = 10_000
MIN_EXCERPT = 8_000
MECH = ["drawdown", "mom_21", "mom_126", "vol_63", "log_dollar_vol"]

PROMPT = """# Forecasting study — item {item}

You are a calibrated long-only portfolio researcher in a historical, leakage-controlled
experiment. Use ONLY the supplied filing excerpt available at the stated cutoff. Do not use
outside knowledge, remembered issuer identity, prices, or later events. Apply skeptical base
rates; cheapness alone is insufficient.

Filing: {form} filed with the U.S. SEC on {filed} (the cutoff). The company's name, ticker, web
addresses and tax ID were removed. At the cutoff the stock had fallen at least 40% from its
one-year high.

Forecast the stock's total return versus the S&P 500 (SPY) over the 90 calendar days after the
cutoff. Do not search the web or use any tool except writing your answer file.

Write a JSON object with exactly these keys to {answer_path}:
- "item": "{item}"
- "p_outperform": probability 0-100 that it beats SPY over the window
- "p_beat20": probability 0-100 that it beats SPY by 20 or more percentage points
- "expected_excess_pct": expected return minus SPY's, in percentage points
- "rationale": at most 30 words

Then reply with just the word DONE.

---

{excerpt}
"""


def company_names() -> dict[str, tuple[str, str]]:
    names = {}
    if ra.CENTURY_PACKS.parent.joinpath("recovered.jsonl").exists():
        for line in ra.CENTURY_PACKS.parent.joinpath("recovered.jsonl").open():
            r = json.loads(line)
            names[r["comprehensive_case_id"]] = (r["company"], r["form"])
    for line in ra.LIVE_PACKS.open():
        r = json.loads(line)
        names[r["case_id"]] = (r["company"], r.get("anchor_form") or "10-Q")
    return names


def build(out_dir: Path, seed: int = 20260926, per_arm: int = 50) -> list[dict]:
    used = set()
    for path in USED:
        if path.exists():
            used |= {k["case_id"] for k in json.loads(path.read_text())}
    names = company_names()
    walk = {}
    if WALK_FORWARD.exists():
        for r in csv.DictReader(WALK_FORWARD.open()):
            walk[r["case_id"]] = float(r["M0_mech"]) if r.get("M0_mech") else None
    rows = [r for r in csv.DictReader(DATASET.open())
            if r["case_id"] in names and r["case_id"] not in used and r["ticker"]
            and not (r["ticker"] == "ARCT" and "arcturus" not in names[r["case_id"]][0].lower())]
    rng = random.Random(seed)

    def pick(cands: list[dict], n: int) -> list[dict]:
        by_month: dict[str, list[dict]] = {}
        for r in sorted(cands, key=lambda r: r["case_id"]):
            by_month.setdefault(r["cutoff"][:7], []).append(r)
        chosen = []
        while len(chosen) < n and any(by_month.values()):
            for month in sorted(by_month):
                if by_month[month] and len(chosen) < n:
                    chosen.append(by_month[month].pop(rng.randrange(len(by_month[month]))))
        return chosen

    # draw 3x candidates per stratum in seeded order, then keep the first ones whose scrubbed
    # excerpt is long enough to forecast from (short excerpts in one arm would fake a skill gap)
    strata = []
    for arm, (lo, hi) in ARMS.items():
        pool = [r for r in rows if lo <= r["cutoff"] <= hi]
        strata.append((arm, pick([r for r in pool if float(r["excess"]) >= THRESHOLD], 3 * (per_arm // 2))))
        strata.append((arm, pick([r for r in pool if float(r["excess"]) <= -THRESHOLD], 3 * (per_arm - per_arm // 2))))
    want = {r["case_id"] for _, cands in strata for r in cands}
    texts = {}
    for path, key in ((ra.CENTURY_PACKS, "comprehensive_case_id"), (ra.LIVE_PACKS, "case_id")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                if row.get(key) in want:
                    texts[row[key]] = row.get("snapshot_text") or ""
    excerpts, sample = {}, []
    for arm, cands in strata:
        kept = []
        for r in cands:
            ex = ra.strict_scrub(ra.mda_excerpt(texts.get(r["case_id"], ""), EXCERPT_CHARS),
                                 names[r["case_id"]][0], r["ticker"])
            if len(ex) >= MIN_EXCERPT and len(kept) < per_arm // 2:
                excerpts[r["case_id"]] = ex
                kept.append(r)
        sample += [(arm, r) for r in kept]
    rng.shuffle(sample)

    (out_dir / "items").mkdir(parents=True, exist_ok=True)
    (out_dir / "answers").mkdir(parents=True, exist_ok=True)
    key = []
    for i, (arm, r) in enumerate(sample, 1):
        item = f"C{i:03d}"
        company, form = names[r["case_id"]]
        excerpt = excerpts[r["case_id"]]
        (out_dir / "items" / f"{item}.md").write_text(
            PROMPT.format(item=item, form=form, filed=r["cutoff"], excerpt=excerpt,
                          answer_path=out_dir / "answers" / f"{item}.json"), encoding="utf-8")
        key.append({"item": item, "arm": arm, "case_id": r["case_id"], "company": company, "ticker": r["ticker"],
                    "form": form, "filed": r["cutoff"], "excess": float(r["excess"]), "winner": float(r["excess"]) > 0,
                    "ox_p20": float(r["p20"]), "mech_model": walk.get(r["case_id"]),
                    **{m: float(r[m]) if r.get(m) not in ("", None) else None for m in MECH},
                    "excerpt_chars": len(excerpt)})
    (out_dir / "answer_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    return key


# ---------------------------------------------------------------- score

def auc_block(rows: list[dict], score_key: str, seed: int, sign: float = 1.0) -> dict:
    ok = [r for r in rows if r.get(score_key) is not None]
    if len({r["winner"] for r in ok}) < 2:
        return {"n": len(ok)}
    scores = [sign * float(r[score_key]) for r in ok]
    labels = [r["winner"] for r in ok]
    return {"n": len(ok), "auc": rp.auc(scores, labels), "ci": rp.auc_ci(scores, labels, seed=seed),
            "p_value": rp.auc_p_value(scores, labels, draws=5000, seed=seed)}


def gap(pre: list[dict], post: list[dict], score_key: str, sign: float = 1.0, draws: int = 4000,
        seed: int = 0) -> dict:
    """AUC(in training) - AUC(after training), with an independent bootstrap of each arm."""
    def auc_of(rows):
        ok = [r for r in rows if r.get(score_key) is not None]
        return rp.auc([sign * float(r[score_key]) for r in ok], [r["winner"] for r in ok])
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(draws):
        a = auc_of([pre[i] for i in rng.integers(0, len(pre), len(pre))])
        b = auc_of([post[i] for i in rng.integers(0, len(post), len(post))])
        if not (np.isnan(a) or np.isnan(b)):
            diffs.append(a - b)
    diffs = np.array(diffs)
    return {"point": auc_of(pre) - auc_of(post), "ci": [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))],
            "share_of_draws_above_zero": float((diffs > 0).mean())}


def did(pre: list[dict], post: list[dict], key_a: str, key_b: str, sign_a: float = 1.0, sign_b: float = 1.0,
        draws: int = 4000, seed: int = 0) -> dict:
    """Difference-in-differences: scorer a's AUC gap minus scorer b's, resampling the same cases for both,
    so a period that is simply harder to forecast cancels out."""
    def auc_of(rows, key, sign):
        ok = [r for r in rows if r.get(key) is not None]
        return rp.auc([sign * float(r[key]) for r in ok], [r["winner"] for r in ok])

    def stat(a_rows, b_rows):
        return ((auc_of(a_rows, key_a, sign_a) - auc_of(b_rows, key_a, sign_a))
                - (auc_of(a_rows, key_b, sign_b) - auc_of(b_rows, key_b, sign_b)))
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(draws):
        d = stat([pre[i] for i in rng.integers(0, len(pre), len(pre))],
                 [post[i] for i in rng.integers(0, len(post), len(post))])
        if not np.isnan(d):
            diffs.append(d)
    diffs = np.array(diffs)
    return {"point": stat(pre, post), "ci": [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))],
            "share_of_draws_above_zero": float((diffs > 0).mean())}


def score(probe_dir: Path, results: Path) -> dict:
    key = json.loads((probe_dir / "answer_key.json").read_text())
    answers = {}
    if (probe_dir / "answers.jsonl").exists():
        for line in (probe_dir / "answers.jsonl").open():
            a = json.loads(line)
            answers[a["item"]] = a
    if (probe_dir / "answers").is_dir():
        for path in sorted((probe_dir / "answers").glob("*.json")):
            try:
                answers[path.stem] = json.loads(path.read_text())
            except json.JSONDecodeError:
                pass
    rows, missing = [], []
    for k in key:
        a = answers.get(k["item"])
        if a is None:
            missing.append(k["item"])
            continue
        rows.append({**k, **{f: a.get(f) for f in ("p_outperform", "p_beat20", "expected_excess_pct", "rationale")}})
    pre = [r for r in rows if r["arm"] == "in_training"]
    post = [r for r in rows if r["arm"] == "after_training"]
    scorers = {"haiku_p_outperform": ("p_outperform", 1.0), "haiku_p_beat20": ("p_beat20", 1.0),
               "haiku_expected_excess": ("expected_excess_pct", 1.0), "ox_p20_backtest_forecaster": ("ox_p20", 1.0),
               "mechanical_walk_forward_model": ("mech_model", 1.0), "deeper_drawdown": ("drawdown", -1.0),
               "momentum_6m": ("mom_126", 1.0)}
    out = {"answered": len(rows), "missing": missing, "threshold": THRESHOLD, "arms": ARMS, "by_scorer": {}}
    for i, (name, (field, sign)) in enumerate(scorers.items()):
        out["by_scorer"][name] = {"in_training": auc_block(pre, field, seed=10 + i, sign=sign),
                                  "after_training": auc_block(post, field, seed=40 + i, sign=sign),
                                  "gap": gap(pre, post, field, sign=sign, seed=70 + i)}
    out["difference_in_differences"] = {
        "haiku_vs_mechanical": did(pre, post, "p_outperform", "mech_model", seed=101),
        "haiku_vs_ox": did(pre, post, "p_outperform", "ox_p20", seed=102)}
    from scipy.stats import spearmanr
    out["spearman_haiku_p_outperform_vs"] = {
        arm: {"ox_p20": float(spearmanr([float(r["p_outperform"]) for r in sub], [r["ox_p20"] for r in sub]).statistic),
              "mech_model": float(spearmanr([float(r["p_outperform"]) for r in sub if r["mech_model"] is not None],
                                            [r["mech_model"] for r in sub if r["mech_model"] is not None]).statistic)}
        for arm, sub in (("in_training", pre), ("after_training", post))}
    # drop cases priced on a ticker another filer owns (ticker_audit.py writes "ticker_flag" into the key)
    pre_c, post_c = ([r for r in sub if not r.get("ticker_flag")] for sub in (pre, post))
    out["excluding_flagged_tickers"] = {
        "dropped": [r["item"] for r in rows if r.get("ticker_flag")],
        **{name: {"in_training": rp.auc([sign * float(r[f]) for r in pre_c if r.get(f) is not None],
                                         [r["winner"] for r in pre_c if r.get(f) is not None]),
                  "after_training": rp.auc([sign * float(r[f]) for r in post_c if r.get(f) is not None],
                                           [r["winner"] for r in post_c if r.get(f) is not None])}
           for name, (f, sign) in scorers.items() if name in (
               "haiku_p_outperform", "ox_p20_backtest_forecaster", "mechanical_walk_forward_model")},
        "did_haiku_vs_mechanical": did(pre_c, post_c, "p_outperform", "mech_model", seed=103)}
    for arm, sub in (("in_training", pre), ("after_training", post)):
        out[f"mean_p_outperform_{arm}"] = {
            "winners": float(np.mean([float(r["p_outperform"]) for r in sub if r["winner"]])),
            "losers": float(np.mean([float(r["p_outperform"]) for r in sub if not r["winner"]]))}
    results.mkdir(parents=True, exist_ok=True)
    (results / "summary.json").write_text(json.dumps({**out, "rows": rows}, indent=1), encoding="utf-8")
    def fmt(b):
        return f"{b['auc']:.2f} [{b['ci'][0]:.2f}, {b['ci'][1]:.2f}]" if "auc" in b else "n/a"
    lines = [f"# Before/after-cutoff forecasting probe ({len(rows)} cases)", "",
             "AUC for beating SPY over 90 days (winners beat SPY by 25+ points, losers trailed by 25+).", "",
             "| scorer | in training (2023-01 to 2024-09) | after training (2025-08 on) | gap [95% CI] |",
             "| --- | ---: | ---: | ---: |"]
    for name, b in out["by_scorer"].items():
        g = b["gap"]
        lines.append(f"| {name} | {fmt(b['in_training'])} | {fmt(b['after_training'])} | "
                     f"{g['point']:+.2f} [{g['ci'][0]:+.2f}, {g['ci'][1]:+.2f}] |")
    for name, d in out["difference_in_differences"].items():
        lines.append(f"\nDifference-in-differences, {name.replace('_', ' ')}: {d['point']:+.2f} "
                     f"[{d['ci'][0]:+.2f}, {d['ci'][1]:+.2f}] (paired bootstrap)")
    lines += ["", "| item | arm | filed | company | 90d excess | Haiku P(outperform) | P(+20) | Ox P(+20) | rationale |",
              "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |"]
    for r in sorted(rows, key=lambda r: (r["arm"], r["filed"])):
        lines.append(f"| {r['item']} | {r['arm']} | {r['filed']} | {r['company']} ({r['ticker']}) | {r['excess']:+.0%} | "
                     f"{r['p_outperform']} | {r['p_beat20']} | {r['ox_p20']:.0f} | {str(r['rationale']).replace('|', '/')} |")
    (results / "table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out-dir", type=Path, required=True)
    s = sub.add_parser("score")
    s.add_argument("--probe-dir", type=Path, required=True)
    s.add_argument("--results", type=Path, default=None)
    args = ap.parse_args()
    if args.cmd == "build":
        print(f"built {len(build(args.out_dir))} items in {args.out_dir}")
    else:
        out = score(args.probe_dir, args.results or args.probe_dir)
        print(json.dumps({k: v for k, v in out.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
