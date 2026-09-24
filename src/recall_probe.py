#!/usr/bin/env python3
"""Named-company outcome-recall probe: does an LLM remember what the stock did next?

The re-identification probe (memorization_probe.py) showed identities leak. This
probe isolates the second step: given the company, ticker and filing date outright,
can the model recall the stock's 90-day move against SPY?

Design (seeded, stratified, one case per fresh model context):
- 80 historical cases filed 2011-2024 (before the probe model's training cutoff):
  40 that beat SPY by >= 30 points over the following 90 days, 40 that trailed by
  >= 30 points, drawn round-robin across filing years.
- 20 control cases filed in 2026 (after the training cutoff): 10 winners and 10
  losers at +/- 20 points. Anything above chance here is reasoning from the name,
  not memory of the outcome.
- Cases from the first probe are excluded. No drawdown, price or filing text is
  shown, so the only route to the answer is memory of the company.

    python3 src/recall_probe.py build --out-dir <dir>      # items/, answer_key.json
    python3 src/recall_probe.py score --probe-dir results/recall_probe --results results/recall_probe
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data/audit_inputs/dataset.csv"
CENTURY_META = ROOT / "lab_runs/century_typesafe/recovered.jsonl"
LIVE_CASES = ROOT / "lab_runs/live_2026/cases.jsonl"
FIRST_PROBE = ROOT / "results/memorization_probe/answer_key.json"

PROMPT = """# Memorization study — item {item}

You are a subject in a study of what language models remember. Answer ONLY from
your own background knowledge. Do not search the web or use any tool except
writing your answer file. "I don't remember" is a useful answer; please do not
invent specifics. Judge this company on its own, as if it were the only question.

Company: {company}
Ticker at the time: {ticker}
Event: filed its {form} with the U.S. SEC on {filed}.

Question: over the 90 calendar days after {filed}, did this stock outperform or
underperform the S&P 500 (SPY), in total return, and by roughly how much?

Write a JSON object with exactly these keys to {answer_path}:
- "item": "{item}"
- "recognize_company": true or false
- "memory": "specific" (you recall this stock's move in roughly this window),
  "general" (you recall the company's broader situation or trajectory then),
  or "none"
- "direction": "outperform" or "underperform" (your best call, even if unsure)
- "p_outperform": probability 0-100 that it beat SPY over the window
- "p_beat20": probability 0-100 that it beat SPY by 20 or more percentage points
- "recalled": at most 25 words on what you remember about the company around
  then, or "nothing"

Then reply with just the word DONE.
"""


def build(out_dir: Path, seed: int = 20260924, n_hist: int = 80, n_live: int = 20) -> list[dict]:
    rows = list(csv.DictReader(DATASET.open()))
    used = {k["case_id"] for k in json.loads(FIRST_PROBE.read_text())} if FIRST_PROBE.exists() else set()
    meta = {}
    for line in CENTURY_META.open():
        r = json.loads(line)
        meta[r["comprehensive_case_id"]] = {"company": r["company"], "form": r["form"]}
    for line in LIVE_CASES.open():
        r = json.loads(line)
        meta[r["case_id"]] = {"company": r["company"], "form": r.get("anchor_form") or "10-Q"}
    pool = [r for r in rows if r["case_id"] in meta and r["case_id"] not in used and r["ticker"]]
    rng = random.Random(seed)

    def pick(cands: list[dict], n: int) -> list[dict]:
        by_year: dict[str, list[dict]] = {}
        for r in sorted(cands, key=lambda r: r["case_id"]):
            by_year.setdefault(r["cutoff"][:4], []).append(r)
        chosen = []
        while len(chosen) < n and any(by_year.values()):
            for year in sorted(by_year):
                if by_year[year] and len(chosen) < n:
                    chosen.append(by_year[year].pop(rng.randrange(len(by_year[year]))))
        return chosen

    hist = [r for r in pool if r["cohort"] == "century" and "2011" <= r["cutoff"][:4] <= "2024"]
    live = [r for r in pool if r["cohort"] == "live_2026"]
    sample = (pick([r for r in hist if float(r["excess"]) >= 0.30], n_hist // 2)
              + pick([r for r in hist if float(r["excess"]) <= -0.30], n_hist - n_hist // 2)
              + pick([r for r in live if float(r["excess"]) >= 0.20], n_live // 2)
              + pick([r for r in live if float(r["excess"]) <= -0.20], n_live - n_live // 2))
    rng.shuffle(sample)  # item ids carry no information about cohort or outcome

    (out_dir / "items").mkdir(parents=True, exist_ok=True)
    (out_dir / "answers").mkdir(parents=True, exist_ok=True)
    key = []
    for i, r in enumerate(sample, 1):
        item = f"R{i:03d}"
        m = meta[r["case_id"]]
        prompt = PROMPT.format(item=item, company=m["company"], ticker=r["ticker"], form=m["form"],
                               filed=r["cutoff"], answer_path=out_dir / "answers" / f"{item}.json")
        (out_dir / "items" / f"{item}.md").write_text(prompt, encoding="utf-8")
        key.append({"item": item, "case_id": r["case_id"], "company": m["company"], "ticker": r["ticker"],
                    "form": m["form"], "filed": r["cutoff"],
                    "cohort": "historical" if r["cohort"] == "century" else "control_2026",
                    "excess": float(r["excess"]), "winner": float(r["excess"]) > 0, "ox_p20": float(r["p20"])})
    (out_dir / "answer_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    return key


# ---------------------------------------------------------------- scoring

def auc(scores: list[float], labels: list[bool]) -> float:
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    if not pos or not neg:
        return float("nan")
    return sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg))


def auc_ci(scores, labels, draws: int = 4000, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    s, l = np.asarray(scores, float), np.asarray(labels, bool)
    pos, neg = s[l], s[~l]
    boots = []
    for _ in range(draws):
        bp, bn = rng.choice(pos, len(pos)), rng.choice(neg, len(neg))
        boots.append(auc(list(bp) + list(bn), [True] * len(bp) + [False] * len(bn)))
    return [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))]


def auc_p_value(scores, labels, draws: int = 20000, seed: int = 0) -> float:
    """One-sided permutation p-value for AUC > 0.5."""
    rng = np.random.default_rng(seed)
    observed = auc(scores, labels)
    lab = np.asarray(labels, bool)
    hits = sum(auc(scores, list(rng.permutation(lab))) >= observed for _ in range(draws))
    return (hits + 1) / (draws + 1)


def wilson(k: int, n: int) -> list[float]:
    if not n:
        return [float("nan"), float("nan")]
    z, p = 1.96, k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [max(0.0, c - h), min(1.0, c + h)]


def block(rows: list[dict], seed: int = 0) -> dict:
    ok = [r for r in rows if r.get("p_outperform") is not None]
    if not ok:
        return {"n": 0}
    labels = [r["winner"] for r in ok]
    p_out = [float(r["p_outperform"]) for r in ok]
    p20 = [float(r["p_beat20"]) for r in ok if r.get("p_beat20") is not None]
    right = sum((r["direction"] == "outperform") == r["winner"] for r in ok)
    by_memory = {}
    for level in ("specific", "general", "none"):
        sub = [r for r in ok if r.get("memory") == level]
        k = sum((r["direction"] == "outperform") == r["winner"] for r in sub)
        by_memory[level] = {"n": len(sub), "direction_accuracy": k / len(sub) if sub else None,
                            "ci": wilson(k, len(sub))}
    return {
        "n": len(ok), "winners": sum(labels),
        "recognized": sum(bool(r.get("recognize_company")) for r in ok),
        "claims_memory": sum(r.get("memory") in ("specific", "general") for r in ok),
        "auc_p_outperform": auc(p_out, labels), "auc_ci": auc_ci(p_out, labels, seed=seed),
        "auc_p_value": auc_p_value(p_out, labels, seed=seed),
        "auc_p_beat20": auc(p20, labels) if len(p20) == len(ok) else None,
        "direction_accuracy": right / len(ok), "direction_ci": wilson(right, len(ok)),
        "called_outperform": sum(r["direction"] == "outperform" for r in ok),
        "mean_p_outperform_winners": float(np.mean([p for p, l in zip(p_out, labels) if l])),
        "mean_p_outperform_losers": float(np.mean([p for p, l in zip(p_out, labels) if not l])),
        "by_memory": by_memory,
    }


def score(probe_dir: Path, results: Path) -> dict:
    key = json.loads((probe_dir / "answer_key.json").read_text())
    answers = {}
    if (probe_dir / "answers.jsonl").exists():
        for line in (probe_dir / "answers.jsonl").open():
            a = json.loads(line)
            answers[a["item"]] = a
    for path in sorted((probe_dir / "answers").glob("*.json")) if (probe_dir / "answers").is_dir() else []:
        try:
            answers[path.stem] = json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    rows, missing = [], []
    for truth in key:
        ans = answers.get(truth["item"])
        if ans is None:
            missing.append(truth["item"])
            continue
        rows.append({**truth, **{k: ans.get(k) for k in ("recognize_company", "memory", "direction",
                                                          "p_outperform", "p_beat20", "recalled")}})
    hist = [r for r in rows if r["cohort"] == "historical"]
    ctrl = [r for r in rows if r["cohort"] == "control_2026"]
    out = {"answered": len(rows), "missing": missing,
           "historical_2011_2024": block(hist, seed=1), "control_2026": block(ctrl, seed=2),
           "historical_by_era": {"2011-2017": block([r for r in hist if r["filed"] < "2018"], seed=3),
                                 "2018-2024": block([r for r in hist if r["filed"] >= "2018"], seed=4)}}
    h = out["historical_2011_2024"]
    diff = []
    rng = np.random.default_rng(7)
    for _ in range(4000):  # bootstrap the historical-minus-control AUC gap
        hs = [hist[i] for i in rng.integers(0, len(hist), len(hist))]
        cs = [ctrl[i] for i in rng.integers(0, len(ctrl), len(ctrl))]
        a = auc([float(r["p_outperform"]) for r in hs], [r["winner"] for r in hs])
        b = auc([float(r["p_outperform"]) for r in cs], [r["winner"] for r in cs])
        if not (math.isnan(a) or math.isnan(b)):
            diff.append(a - b)
    out["auc_gap_historical_minus_control"] = {
        "point": h["auc_p_outperform"] - out["control_2026"]["auc_p_outperform"],
        "ci": [float(np.percentile(diff, 2.5)), float(np.percentile(diff, 97.5))]}
    from scipy.stats import spearmanr
    liquidity = {}
    if DATASET.exists():
        liquidity = {r["case_id"]: float(r["log_dollar_vol"]) for r in csv.DictReader(DATASET.open())
                     if r["log_dollar_vol"]}
    diag = {}
    for name, sub in (("historical", hist), ("control_2026", ctrl)):
        haiku = [float(r["p_outperform"]) for r in sub]
        ox = [r["ox_p20"] for r in sub]
        diag[name] = {"ox_p20_auc_same_cases": auc(ox, [r["winner"] for r in sub]),
                      "spearman_haiku_name_only_vs_ox_filing": float(spearmanr(haiku, ox).statistic)}
    sized = [r for r in hist if r["case_id"] in liquidity]
    if sized:
        cut = float(np.median([liquidity[r["case_id"]] for r in sized]))
        big = [r for r in sized if liquidity[r["case_id"]] >= cut]
        small = [r for r in sized if liquidity[r["case_id"]] < cut]
        diag["historical_auc_by_liquidity"] = {
            "large": auc([float(r["p_outperform"]) for r in big], [r["winner"] for r in big]),
            "small": auc([float(r["p_outperform"]) for r in small], [r["winner"] for r in small])}
    # AR Capital-family filings mapped to ticker ARCT by the symbol resolver carry another security's prices
    clean = [r for r in hist if not (r["ticker"] == "ARCT" and "arcturus" not in r["company"].lower())]
    diag["historical_excluding_mismapped_tickers"] = {
        "n": len(clean), "auc_p_outperform": auc([float(r["p_outperform"]) for r in clean], [r["winner"] for r in clean]),
        "ox_p20_auc": auc([r["ox_p20"] for r in clean], [r["winner"] for r in clean])}
    out["diagnostics"] = diag
    results.mkdir(parents=True, exist_ok=True)
    (results / "summary.json").write_text(json.dumps({**out, "rows": rows}, indent=1), encoding="utf-8")
    lines = ["| item | cohort | company | filed | 90d excess | memory | call | P(outperform) | recalled |",
             "| --- | --- | --- | --- | ---: | --- | --- | ---: | --- |"]
    for r in sorted(rows, key=lambda r: (r["cohort"], r["filed"])):
        lines.append(f"| {r['item']} | {r['cohort']} | {r['company']} ({r['ticker']}) | {r['filed']} | "
                     f"{r['excess']:+.0%} | {r['memory']} | {r['direction']} | {r['p_outperform']} | "
                     f"{str(r['recalled']).replace('|', '/')} |")
    (results / "table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out-dir", type=Path, required=True)
    s = sub.add_parser("score")
    s.add_argument("--probe-dir", type=Path, required=True)
    s.add_argument("--results", type=Path, default=ROOT / "results/recall_probe")
    args = ap.parse_args()
    if args.cmd == "build":
        key = build(args.out_dir)
        print(f"built {len(key)} items in {args.out_dir}")
    else:
        out = score(args.probe_dir, args.results)
        print(json.dumps({k: v for k, v in out.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
