#!/usr/bin/env python3
"""Can an LLM name the company from its scrubbed filing text?

The first leg of the leakage chain (filing text -> identity), measured on the same
100 cases as the named-recall probe (identity -> outcome), so the two legs can be
joined case by case.

Design:
- The 100 recall-probe cases (80 extreme 2011-2024 moves, 20 from 2026), re-shuffled
  under new item ids.
- 10,000 characters of the filing's MD&A, strictly scrubbed (redaction_audit.strict_scrub):
  every registrant-name word, the ticker, URLs and tax ids removed. The filing date is
  kept, as the forecaster saw it.
- One case per fresh Claude Haiku 4.5 context; best guess, up to two alternates, a
  confidence, and the clue that gave it away.

    python3 src/identification_probe.py build --out-dir <dir>
    python3 src/identification_probe.py score --probe-dir results/identification_probe
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import redaction_audit as ra
import recall_probe as rp

ROOT = Path(__file__).resolve().parent.parent
RECALL_KEY = ROOT / "results/recall_probe/answer_key.json"
RECALL_SUMMARY = ROOT / "results/recall_probe/summary.json"
EXCERPT_CHARS = 10_000

SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
            "holdings", "holding", "group", "the", "lp", "llc", "sa", "nv", "ag", "se", "de", "trust",
            "new", "and", "of", "ltda", "bhd", "spa", "asa", "ab", "oyj", "a", "an"}
# generic business words that never identify a company by themselves (fallback when
# no system dictionary is available, e.g. in CI)
GENERIC = {"american", "international", "global", "national", "united", "general", "first", "energy",
           "resources", "technologies", "technology", "systems", "financial", "capital", "pharmaceuticals",
           "therapeutics", "bancorp", "industries", "services", "solutions", "networks", "entertainment",
           "communications", "realty", "properties", "health", "healthcare", "medical", "oil", "gas",
           "mining", "mines", "motors", "foods", "brands", "partners", "investors", "bank", "biosciences",
           "biotechnology", "electric", "power", "software", "semiconductor", "devices", "micro", "labs",
           "laboratories", "pharma", "bio", "digital", "data", "media", "north", "south", "east", "west",
           "pacific", "atlantic", "offshore", "marine", "air", "airlines", "retail", "stores", "products"}
# Manual review of every automatic match (renames and sibling funds the string matcher can't know).
OVERRIDES = {
    "I023": (True, "Organovo Holdings renamed itself VivoSim Labs in 2025; the excerpt says 'formerly known as'"),
    "I030": (True, "Isis Pharmaceuticals renamed itself Ionis in December 2015"),
    "I040": (True, "D-Wave Systems is D-Wave Quantum's operating company and former name"),
    "I065": (True, "Dronedek Corporation is Arrive AI's former name, stated in the excerpt"),
    "I077": (False, "Healthcare Trust, Inc. is the renamed ARC Healthcare Trust II, a sibling of Trust III"),
}


def mismapped(row: dict) -> bool:
    """AR Capital-family filings that the symbol resolver gave the ticker ARCT (Arcturus Therapeutics
    today): their filing text and identity are real, but their price outcomes are not."""
    return row.get("ticker") == "ARCT" and "arcturus" not in (row.get("company") or "").lower()


PROMPT = """# Identification study — item {item}

Below is an excerpt from a U.S. SEC filing ({form}, filed {filed}), mostly from the
Management's Discussion and Analysis. The company's name, ticker, web addresses and
tax ID were replaced with [ISSUER] or removed; ordinary words that happened to share
a word with the company's name may also have been replaced.

Using ONLY your own background knowledge, identify the company. Do not search the web
or use any tool except writing your answer file. If you have no real idea, say so
with a low confidence rather than guessing a famous name.

Write a JSON object with exactly these keys to {answer_path}:
- "item": "{item}"
- "company_guess": your best guess of the company's name, or null
- "ticker_guess": its ticker at the time, or null
- "alternates": a list of up to 2 other candidate company names (may be empty)
- "confidence": probability 0-1 that company_guess is correct
- "industry": the company's industry in at most 6 words
- "clue": at most 20 words on what identified it, or "nothing specific"

Then reply with just the word DONE.

---

{excerpt}
"""


# ---------------------------------------------------------------- matching

def load_words() -> set[str]:
    words = ra.load_dictionary()
    return words | GENERIC if words else set(GENERIC)


def core_tokens(name: str | None) -> list[str]:
    text = (name or "").lower().replace("&", " and ")
    text = re.sub(r"\\[a-z]{2}\b", " ", text)  # SEC state tags such as "AMARIN CORP PLC\UK"
    return [t for t in re.findall(r"[a-z0-9]+", text) if t not in SUFFIXES]


def name_match(guess: str | None, truth: str, words: set[str]) -> bool:
    """True if the guess names the same company (exact core name, containment, or a shared distinctive word)."""
    g, t = core_tokens(guess), core_tokens(truth)
    if not g or not t:
        return False
    gs, ts = " ".join(g), " ".join(t)
    if gs == ts or (len(gs) >= 4 and gs in ts) or (len(ts) >= 4 and ts in gs):
        return True
    distinctive = {w for w in set(g) & set(t) if len(w) >= 4 and w not in words and w not in GENERIC}
    return bool(distinctive)


def ticker_match(guess: str | None, truth: str) -> bool:
    base = lambda s: re.split(r"[.\-/ ]", (s or "").strip().upper())[0]
    return bool(base(guess)) and base(guess) == base(truth)


# ---------------------------------------------------------------- build

def iter_texts(case_ids: set[str]):
    for path, key in ((ra.CENTURY_PACKS, "comprehensive_case_id"), (ra.LIVE_PACKS, "case_id")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                if row.get(key) in case_ids:
                    yield row[key], row.get("snapshot_text") or ""


def build(out_dir: Path, seed: int = 20260925) -> list[dict]:
    key = json.loads(RECALL_KEY.read_text())
    texts = dict(iter_texts({k["case_id"] for k in key}))
    order = list(key)
    random.Random(seed).shuffle(order)
    (out_dir / "items").mkdir(parents=True, exist_ok=True)
    (out_dir / "answers").mkdir(parents=True, exist_ok=True)
    out = []
    for i, k in enumerate(order, 1):
        item = f"I{i:03d}"
        excerpt = ra.strict_scrub(ra.mda_excerpt(texts.get(k["case_id"], ""), EXCERPT_CHARS), k["company"], k["ticker"])
        (out_dir / "items" / f"{item}.md").write_text(
            PROMPT.format(item=item, form=k["form"], filed=k["filed"], excerpt=excerpt,
                          answer_path=out_dir / "answers" / f"{item}.json"), encoding="utf-8")
        out.append({"item": item, "recall_item": k["item"], "case_id": k["case_id"], "company": k["company"],
                    "ticker": k["ticker"], "form": k["form"], "filed": k["filed"], "cohort": k["cohort"],
                    "excess": k["excess"], "excerpt_chars": len(excerpt)})
    (out_dir / "answer_key.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


# ---------------------------------------------------------------- score

def load_answers(probe_dir: Path) -> dict[str, dict]:
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
    return answers


def rate_block(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    top1 = sum(r["top1"] for r in rows)
    top3 = sum(r["top3"] for r in rows)
    conf = np.array([float(r["confidence"] or 0) for r in rows])
    hit = np.array([r["top1"] for r in rows], dtype=float)
    return {"n": len(rows), "top1": top1, "top1_rate": top1 / len(rows), "top1_ci": rp.wilson(top1, len(rows)),
            "top3": top3, "top3_rate": top3 / len(rows), "top3_ci": rp.wilson(top3, len(rows)),
            "mean_confidence": float(conf.mean()),
            "confidence_auc": rp.auc(list(conf), list(hit.astype(bool))),
            "confidence_brier": float(np.mean((conf - hit) ** 2)),
            "confident_wrong": int(sum((c >= 0.7) and not h for c, h in zip(conf, hit)))}


def score(probe_dir: Path, results: Path) -> dict:
    key = json.loads((probe_dir / "answer_key.json").read_text())
    answers = load_answers(probe_dir)
    words = load_words()
    rows, missing = [], []
    for k in key:
        a = answers.get(k["item"])
        if a is None:
            missing.append(k["item"])
            continue
        top1 = ticker_match(a.get("ticker_guess"), k["ticker"]) or name_match(a.get("company_guess"), k["company"], words)
        alts = [x for x in (a.get("alternates") or []) if isinstance(x, str)][:2]
        top3 = top1 or any(name_match(x, k["company"], words) for x in alts)
        override = OVERRIDES.get(k["item"])
        if override:
            top1 = override[0]
            top3 = top1 or (top3 and override[0])
        rows.append({**k, "company_guess": a.get("company_guess"), "ticker_guess": a.get("ticker_guess"),
                     "alternates": alts, "confidence": a.get("confidence"), "industry": a.get("industry"),
                     "clue": a.get("clue"), "top1": bool(top1), "top3": bool(top3),
                     "override": override[1] if override else None, "mismapped_ticker": mismapped(k)})
    hist = [r for r in rows if r["cohort"] == "historical"]
    ctrl = [r for r in rows if r["cohort"] == "control_2026"]
    out = {"answered": len(rows), "missing": missing, "excerpt_chars": EXCERPT_CHARS,
           "manual_overrides": {i: why for i, (_, why) in OVERRIDES.items()},
           "all": rate_block(rows), "historical_2011_2024": rate_block(hist), "control_2026": rate_block(ctrl),
           "historical_by_era": {"2011-2017": rate_block([r for r in hist if r["filed"] < "2018"]),
                                 "2018-2024": rate_block([r for r in hist if r["filed"] >= "2018"])}}

    # join with the recall probe: does name-only outcome recall concentrate where the text gives the name away?
    if RECALL_SUMMARY.exists():
        recall = {r["case_id"]: r for r in json.loads(RECALL_SUMMARY.read_text())["rows"]}
        chain = {}
        valid = [r for r in hist if not r["mismapped_ticker"]]  # outcomes of mis-mapped tickers are not real
        for label, sub in (("identified_from_text", [r for r in valid if r["top1"]]),
                           ("not_identified", [r for r in valid if not r["top1"]])):
            joined = [recall[r["case_id"]] for r in sub if r["case_id"] in recall]
            scores = [float(j["p_outperform"]) for j in joined]
            labels = [j["winner"] for j in joined]
            chain[label] = {"n": len(joined), "winners": sum(labels),
                            "recall_auc": rp.auc(scores, labels) if joined else None,
                            "ox_p20_auc": rp.auc([j["ox_p20"] for j in joined], labels) if joined else None}
        out["chain_historical"] = chain
    results.mkdir(parents=True, exist_ok=True)
    (results / "summary.json").write_text(json.dumps({**out, "rows": rows}, indent=1), encoding="utf-8")
    lines = ["| item | cohort | filed | company | Haiku's guess | conf. | top-1 | top-3 | clue |",
             "| --- | --- | --- | --- | --- | ---: | :---: | :---: | --- |"]
    for r in sorted(rows, key=lambda r: (r["cohort"], r["filed"])):
        lines.append(f"| {r['item']} | {r['cohort']} | {r['filed']} | {r['company']} ({r['ticker']}) | "
                     f"{r['company_guess'] or '—'} | {r['confidence']} | {'✓' if r['top1'] else ''} | "
                     f"{'✓' if r['top3'] else ''} | {str(r['clue'] or '').replace('|', '/')} |")
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
        key = build(args.out_dir)
        print(f"built {len(key)} items in {args.out_dir}")
    else:
        out = score(args.probe_dir, args.results or args.probe_dir)
        print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
