#!/usr/bin/env python3
"""Score the LLM re-identification / outcome-recall probe.

Probe design (inputs built by `redaction_audit.py --sample`):
- 48 MD&A excerpts, strictly scrubbed of every registrant-name token and ticker.
- 36 century cases (2011-2024) with extreme 90-day outcomes: half beat SPY by
  >= 30 pp, half trailed by >= 30 pp. 12 live-2026 cases (+/- 20 pp) are the
  control: their outcomes postdate the probe model's training data.
- Claude Haiku 4.5, answering from memory only (no tools besides reading the
  batch and writing its answer), guessed the company, recalled the post-filing
  performance, and gave P(beat SPY by >= 20 pp).

    python3 src/memorization_probe.py --probe-dir results/memorization_probe
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERIC = {"inc", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "holdings",
           "holding", "group", "the", "and", "of", "lp", "llc", "international", "technologies",
           "industries", "systems", "energy", "resources", "global", "financial", "fund", "trust"}
UP = {"strong_outperform", "outperform"}
DOWN = {"strong_underperform", "underperform"}


def name_tokens(name: str | None) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if t not in GENERIC and len(t) >= 3}


def identified(answer: dict, truth: dict) -> bool:
    ticker = (answer.get("ticker_guess") or "").upper().split(".")[0]
    return ticker == truth["ticker"].upper().split(".")[0] or bool(
        name_tokens(answer.get("company_guess")) & name_tokens(truth["company"]))


def auc(pos: list[float], neg: list[float]) -> float | None:
    """Probability a random winner outranks a random loser (ties count half)."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def score(key: list[dict], answers: dict[str, dict]) -> dict:
    rows = []
    for truth in key:
        a = answers.get(truth["item"], {})
        recall = a.get("recall") or "no_memory"
        call = 1 if recall in UP else -1 if recall in DOWN else 0
        rows.append({**truth, "company_guess": a.get("company_guess"), "ticker_guess": a.get("ticker_guess"),
                     "id_confidence": a.get("id_confidence"), "identified": identified(a, truth),
                     "recall": recall, "recall_correct": None if call == 0 else call == (1 if truth["excess"] > 0 else -1),
                     "p_beat20": a.get("p_beat20")})

    def block(sub: list[dict]) -> dict:
        ided = [r for r in sub if r["identified"]]
        calls = [r for r in ided if r["recall_correct"] is not None]
        conf_wrong = [r for r in sub if not r["identified"] and (r["id_confidence"] or 0) >= 0.5]
        p = lambda rs: [float(r["p_beat20"]) for r in rs if r["p_beat20"] is not None]
        return {
            "n": len(sub),
            "identified": len(ided),
            "identification_rate": round(len(ided) / len(sub), 3) if sub else None,
            "confident_misidentifications": len(conf_wrong),
            "identified_with_directional_recall": len(calls),
            "recall_direction_accuracy": round(sum(r["recall_correct"] for r in calls) / len(calls), 3) if calls else None,
            "p_beat20_auc_winners_vs_losers": auc(p([r for r in sub if r["excess"] > 0]),
                                                  p([r for r in sub if r["excess"] <= 0])),
        }

    return {"century_2011_2024": block([r for r in rows if r["cohort"] == "century"]),
            "live_2026_control": block([r for r in rows if r["cohort"] == "live_2026"]),
            "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe-dir", type=Path, default=ROOT / "results/memorization_probe")
    args = ap.parse_args()
    key = json.loads((args.probe_dir / "answer_key.json").read_text())
    answers = {}
    for path in sorted(args.probe_dir.glob("answers_*.json")):
        for a in json.loads(path.read_text()):
            answers[a["item"]] = a
    result = score(key, answers)
    (args.probe_dir / "summary.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    lines = ["| item | cohort | filed | company | 90d excess | Haiku guess | identified | recall |",
             "| --- | --- | --- | --- | ---: | --- | :---: | --- |"]
    for r in result["rows"]:
        lines.append(f"| {r['item']} | {r['cohort']} | {r['cutoff']} | {r['company']} | {r['excess']:+.0%} | "
                     f"{r['company_guess'] or '—'} | {'yes' if r['identified'] else 'no'} | {r['recall']} |")
    (args.probe_dir / "table.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
