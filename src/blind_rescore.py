#!/usr/bin/env python3
"""Blind re-score test for parametric (training-data) foreknowledge.

Re-runs extraction + synthesis on a stratified sample of the sealed 2019-2020
cases with every issuer identifier scrubbed from the filing text. If the blind
scores keep their outcome-predictiveness, the signal reads filings; if it
collapses, the identified scores were leaning on memorized identity knowledge.

Verdict rule (frozen before any blind result is inspected):
- GENUINE: blind-vs-identified Spearman/AUC gap CI includes 0 AND blind AUC
  CI excludes 0.5 on the same sample.
- LEAKAGE-DOMINANT: identified clearly predictive on the sample while blind
  AUC CI includes 0.5 and the gap CI excludes 0.
- MIXED: anything else; report retention share.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import math
import random
import re
import statistics
import sys
import threading
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import comprehensive_lab as cl
import ox_lab as ox
import subagents as sa
import temporal_store as ts


EXPERIMENT = "blind_rescore_v1"
NAME_SUFFIXES = (
    "INCORPORATED", "INC", "CORPORATION", "CORP", "COMPANY", "CO", "LIMITED",
    "LTD", "PLC", "N V", "NV", "S A", "SA", "A G", "AG", "HOLDINGS", "HOLDING",
    "GROUP", "INTERNATIONAL", "TECHNOLOGIES", "TECHNOLOGY", "SYSTEMS",
    "SOLUTIONS", "THERAPEUTICS", "PHARMACEUTICALS", "PHARMA", "LABORATORIES",
    "LABS", "BIOSCIENCES", "BIOTECH", "SCIENCES", "INDUSTRIES", "ENTERPRISES",
    "PARTNERS", "TRUST", "BRANDS", "RESOURCES", "ENERGY", "MINING", "MOTORS",
    "AUTOMOTIVE", "COMMUNICATIONS", "COMMUNICATION", "NETWORKS", "DIGITAL",
    "HEALTH", "CARE", "MEDICAL", "DEVICES", "ROBOTICS", "AEROSPACE", "DEFENSE",
    "ACQUISITION", "ACQUISITIONS", "CAPITAL", "VENTURES", "PROPERTIES",
    "REALTY", "RETAIL", "FOODS", "BANCORP", "BANCPORP", "FINANCIAL", "FINANCE",
)
WRITE_LOCK = threading.Lock()


def name_variants(company: str, ticker: str) -> list[str]:
    """Identifier strings to scrub, longest first."""
    variants = set()
    if ticker:
        variants.add(ticker.upper())
    name = re.sub(r"\s+", " ", (company or "")).strip()
    if name:
        variants.add(name.upper())
        variants.add(name.title())
        words = [w.strip(".,") for w in name.upper().replace(",", " ").replace(".", " ").split()]
        core = [w for w in words if w not in NAME_SUFFIXES and len(w) >= 4]
        if core:
            variants.add(" ".join(core))
            variants.add(" ".join(core).title())
            # Bare distinctive tokens: "Ashford" survives when only the full
            # phrase is replaced.
            variants.update(w for w in core)
            variants.update(w.title() for w in core)
    return sorted({v for v in variants if len(v) >= 3}, key=len, reverse=True)


def blind_text(text: str, company: str, ticker: str) -> tuple[str, list[str]]:
    residuals = []
    for variant in name_variants(company, ticker):
        escaped = re.escape(variant)
        pattern = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
        replacement = "TICKER" if variant == ticker.upper() and ticker else "ISSUER"
        text = re.sub(pattern, replacement, text)
        loose = rf"(?<![A-Za-z0-9]){escaped}s?(?![A-Za-z0-9])"
        text = re.sub(loose, replacement, text, flags=re.IGNORECASE)
        if re.search(pattern, text) or re.search(loose, text, re.IGNORECASE):
            residuals.append(variant)
    # Mechanical identifiers resolvable by a model with memorized EDGAR data.
    text = re.sub(r"(?i)(commission\s+file\s+number[:\s]*)[0-9][0-9-]*",
                  r"\g<1>XXX-XXXXX", text)
    text = re.sub(r"\b[0-9]{2}-[0-9]{7}\b", "XX-XXXXXXX", text)
    return text, residuals


def identified_scores(sealed_dir: Path) -> dict[str, dict]:
    """comprehensive_case_id -> mean identified synthesis scores."""
    by_case = defaultdict(list)
    for row in ox.load_jsonl(sealed_dir / "syntheses.jsonl"):
        if isinstance(row.get("result"), dict):
            by_case[row["case_id"]].append(row["result"])
    scored = {}
    for case_id, results in by_case.items():
        def mean(field):
            values = [r[field] for r in results if isinstance(r.get(field), (int, float))]
            return statistics.fmean(values) if values else None
        scored[case_id] = {
            "p_plus20": mean("probability_plus20_excess_90d_pct"),
            "downside": mean("downside_tail_probability_pct"),
            "expected": mean("expected_excess_return_90d_pct"),
            "p_positive": mean("probability_positive_excess_90d_pct"),
            "n_replicates": len(results),
        }
    return scored


def outcomes_by_case(sealed_dir: Path) -> dict[str, dict]:
    rows = {}
    for row in ox.load_jsonl(sealed_dir / "outcomes.jsonl"):
        outcome = row.get("outcome") or {}
        if outcome.get("relative_return_90d") is not None:
            rows[row["case_id"]] = outcome
    return rows


def select_sample(sealed_dir: Path, per_extreme: int, per_middle: int,
                  seed: int) -> list[dict]:
    """Stratify source cases by identified p_plus20 quintile; require outcome."""
    scores = identified_scores(sealed_dir)
    outcomes = outcomes_by_case(sealed_dir)
    link = {}
    for row in ox.load_jsonl(sealed_dir / "outcomes.jsonl"):
        link[row["comprehensive_case_id"]] = row["case_id"]
    eligible = []
    for comp_id, score in scores.items():
        source_id = link.get(comp_id)
        outcome = outcomes.get(source_id)
        if source_id and outcome and score["p_plus20"] is not None:
            eligible.append({"source_case_id": source_id, "comp_id": comp_id,
                             "p_plus20": score["p_plus20"],
                             "excess": outcome["relative_return_90d"]})
    eligible.sort(key=lambda row: row["p_plus20"])
    n = len(eligible)
    quintiles = [eligible[i * n // 5:(i + 1) * n // 5] for i in range(5)]
    rng = random.Random(seed)
    sample = []
    for index, stratum in enumerate(quintiles):
        k = per_extreme if index in (0, 4) else per_middle
        stratum = sorted(stratum, key=lambda row: row["source_case_id"])
        for row in rng.sample(stratum, min(k, len(stratum))):
            sample.append({**row, "quintile": index + 1})
    return sorted(sample, key=lambda row: row["source_case_id"])


def load_source_cases(sealed_dir: Path, wanted: set[str]) -> dict[str, dict]:
    """Wrapper rows in the run dir hold the exact packs the identified run saw."""
    found = {}
    with (sealed_dir / "cases.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("case_id") in wanted:
                found[row["case_id"]] = row
                if len(found) == len(wanted):
                    break
    missing = wanted - set(found)
    if missing:
        raise RuntimeError(f"wrapper cases not found: {sorted(missing)[:5]}")
    return found


def build_blind_cases(sample: list[dict], sources: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Blind packs; drop any case whose identifiers survive scrubbing."""
    cases, rejected = [], []
    for entry in sample:
        wrapper = sources[entry["comp_id"]]
        ticker = entry.get("ticker") or ""
        company = entry.get("company") or ""
        text, residuals = blind_text(wrapper.get("snapshot_text") or "", company, ticker)
        if residuals:
            rejected.append({"source_case_id": entry["source_case_id"],
                             "residual_identifiers": residuals})
            continue
        cases.append({
            "experiment": EXPERIMENT,
            "case_id": ts.digest(EXPERIMENT, entry["source_case_id"])[:24],
            "source_case_id": entry["source_case_id"],
            "ticker": "BLIND", "company": "BLIND",
            "cik": wrapper.get("cik"), "cutoff": wrapper["cutoff"],
            "accepted": wrapper.get("accepted"),
            "anchor_form": wrapper.get("anchor_form"),
            "snapshot_text": text,
            "market_at_cutoff": wrapper.get("market_at_cutoff") or {},
            "identified_p_plus20": entry["p_plus20"],
            "identified_excess": entry["excess"],
            "identified_quintile": entry.get("quintile"),
        })
    return cases, rejected


def run_blind(cases: list[dict], out_dir: Path, client: sa.OpenRouter,
              synth_replicates: int, concurrency: int) -> None:
    cl.WRITE_LOCK = WRITE_LOCK
    cl.run_extractions(cases, out_dir, client, replicates=1, concurrency=concurrency)
    cl.run_syntheses(cases, out_dir, client, replicates=synth_replicates,
                     concurrency=concurrency)


def auc(pairs: list[tuple[float, float]]) -> float:
    """Mann-Whitney AUC for score -> binary outcome."""
    positives = [s for s, y in pairs if y > 0]
    negatives = [s for s, y in pairs if y <= 0]
    if not positives or not negatives:
        return float("nan")
    wins = ties = 0
    for p in positives:
        for n in negatives:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    return (wins + 0.5 * ties) / (len(positives) * len(negatives))


def spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        result = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                result[order[k]] = rank
            i = j + 1
        return result
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def evaluate(sample: list[dict], out_dir: Path, synth_replicates: int) -> dict:
    blind = defaultdict(list)
    for row in ox.load_jsonl(out_dir / "syntheses.jsonl"):
        if isinstance(row.get("result"), dict):
            blind[row["case_id"]].append(row["result"])
    rows = []
    for entry in sample:
        case_id = ts.digest(EXPERIMENT, entry["source_case_id"])[:24]
        results = blind.get(case_id, [])
        if len(results) < synth_replicates:
            continue
        def mean(field):
            values = [r[field] for r in results if isinstance(r.get(field), (int, float))]
            return statistics.fmean(values) if values else None
        rows.append({
            "source_case_id": entry["source_case_id"],
            "blind_p_plus20": mean("probability_plus20_excess_90d_pct"),
            "blind_downside": mean("downside_tail_probability_pct"),
            "blind_expected": mean("expected_excess_return_90d_pct"),
            "blind_p_positive": mean("probability_positive_excess_90d_pct"),
            "identified_p_plus20": entry["p_plus20"],
            "excess": entry["excess"],
        })
    complete = [r for r in rows if r["blind_p_plus20"] is not None]
    paired = [(r["blind_p_plus20"], r["identified_p_plus20"], r["excess"]) for r in complete]
    blind_rho = spearman([p[0] for p in paired], [p[2] for p in paired])
    ident_rho = spearman([p[1] for p in paired], [p[2] for p in paired])
    blind_auc = auc([(p[0], p[2]) for p in paired])
    ident_auc = auc([(p[1], p[2]) for p in paired])
    score_agreement = spearman([p[0] for p in paired], [p[1] for p in paired])
    rng = random.Random(4242)
    deltas, blind_aucs = [], []
    for _ in range(5000):
        pick = [paired[rng.randrange(len(paired))] for _ in range(len(paired))]
        deltas.append(spearman([p[1] for p in pick], [p[2] for p in pick])
                      - spearman([p[0] for p in pick], [p[2] for p in pick]))
        blind_aucs.append(auc([(p[0], p[2]) for p in pick]))
    deltas.sort()
    blind_aucs.sort()
    gap_ci = [deltas[int(0.025 * len(deltas))], deltas[int(0.975 * len(deltas))]]
    blind_auc_ci = [blind_aucs[int(0.025 * len(blind_aucs))],
                    blind_aucs[int(0.975 * len(blind_aucs))]]
    if ident_rho > 0 and blind_auc_ci[1] < 0.5 and gap_ci[0] > 0:
        verdict = "LEAKAGE-DOMINANT"
    elif gap_ci[0] <= 0 <= gap_ci[1] and blind_auc_ci[0] > 0.5:
        verdict = "GENUINE"
    else:
        verdict = "MIXED"
    return {"n": len(paired), "blind_spearman": blind_rho, "identified_spearman": ident_rho,
            "blind_auc": blind_auc, "identified_auc": ident_auc,
            "score_agreement_spearman": score_agreement,
            "gap_ci95": gap_ci, "blind_auc_ci95": blind_auc_ci, "verdict": verdict,
            "rows": rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sealed-dir", type=Path, default=Path("lab_runs/sealed_safety"))
    parser.add_argument("--out-dir", type=Path, default=Path("lab_runs/blind_rescore"))
    parser.add_argument("--model", default="stealth/ox-alpha")
    parser.add_argument("--per-extreme", type=int, default=40)
    parser.add_argument("--per-middle", type=int, default=10)
    parser.add_argument("--synth-replicates", type=int, default=2)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--seed", type=int, default=4242)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args(argv)

    sample = select_sample(args.sealed_dir, args.per_extreme, args.per_middle, args.seed)
    print(f"sample={len(sample)}", file=sys.stderr)
    wanted = {entry["comp_id"] for entry in sample}
    sources = load_source_cases(args.sealed_dir, wanted)
    for entry in sample:
        wrapper = sources[entry["comp_id"]]
        entry["ticker"] = wrapper.get("ticker") or ""
        entry["company"] = wrapper.get("company") or ""
    cases, rejected = build_blind_cases(sample, sources)
    print(f"blinded={len(cases)} rejected={len(rejected)}", file=sys.stderr)
    if rejected:
        (args.out_dir / "blind_rejections.json").write_text(
            json.dumps(rejected, indent=2), encoding="utf-8")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "sample.json").write_text(
        json.dumps([{k: v for k, v in case.items() if k != "snapshot_text"}
                    for case in cases], indent=2), encoding="utf-8")
    if args.dry_run:
        print(json.dumps({"sample": len(cases), "rejected": len(rejected),
                          "median_pack_chars": statistics.median(len(c["snapshot_text"]) for c in cases)},
                         indent=2))
        return 0

    client = sa.OpenRouter(args.api_key or sa.get_api_key(), model=args.model)
    run_blind(cases, args.out_dir, client, args.synth_replicates, args.concurrency)
    result = evaluate(sample, args.out_dir, args.synth_replicates)
    usage = {"model": client.model, "calls": client.calls,
             "prompt_tokens": client.total_prompt_tokens,
             "completion_tokens": client.total_completion_tokens}
    (args.out_dir / "usage.json").write_text(json.dumps(usage, indent=2), encoding="utf-8")
    (args.out_dir / "results.json").write_text(
        json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2),
        encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
