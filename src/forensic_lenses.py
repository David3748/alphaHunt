#!/usr/bin/env python3
"""forensic_lenses.py — re-mine the existing century corpus with accounting-red-flag lenses.

Runs alongside unstructured_proto.py. Zero new data pulls: reads
lab_runs/century_safety_source/cases.jsonl snapshot_text and applies N forensic
extraction lenses, each a strict JSON tool call. Writes run_dir/forensic/<lens>.jsonl.

Subcommands: list | run
"""

import argparse
import concurrent.futures as cf
import json
import re
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import subagents as sa
import ox_lab

WRITE_LOCK = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
LENSES = {
    "going_concern": {
        "keywords": ["going concern", "substantial doubt", "ability to continue",
                     "as a going concern", "liquidity and capital resources"],
        "system": (
            "You are a forensic accountant. From the filing excerpt, assess going-concern risk. "
            "Distinguish explicit 'substantial doubt' language (severe) from mere liquidity "
            "discussion (normal). A genuine red flag is when management states substantial doubt "
            "or when runway math contradicts a going-concern disclaimer."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "going_concern_mentioned": {"type": "boolean"},
                "substantial_doubt_explicit": {"type": "boolean"},
                "severity": {"type": "integer", "minimum": 1, "maximum": 5,
                             "description": "1=none, 3=qualified/going-concern-with-plan, 5=substantial doubt admitted"},
                "estimated_cash_runway_months": {"type": "number", "description": "If determinable from the text, else 0"},
                "management_plan_credible": {"type": "boolean"},
                "key_evidence": {"type": "string", "maxLength": 300, "description": "Exact quoted language"},
                "is_red_flag": {"type": "boolean"},
            },
            "required": ["going_concern_mentioned", "severity", "is_red_flag"],
            "additionalProperties": False,
        },
    },
    "loss_contingency": {
        "keywords": ["contingenc", "legal proceeding", "litigation", "reserve for",
                     "commitments and contingencies", "class action"],
        "system": (
            "You are a forensic accountant. From the excerpt, identify loss contingencies "
            "(litigation, environmental, tax, warranty) and rank severity by likely probability "
            "times magnitude. Flag cases where the company admits possible material loss but "
            "the language is vague about amounts — that vagueness is itself a risk signal."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "contingencies_found": {"type": "boolean"},
                "severity": {"type": "integer", "minimum": 1, "maximum": 5,
                             "description": "1=none/immaterial, 5=probable & material or multiple material"},
                "vague_on_amounts": {"type": "boolean"},
                "possible_material_loss_admitted": {"type": "boolean"},
                "primary_theme": {"type": "string", "enum": ["litigation", "environmental", "tax", "warranty", "product_liability", "regulatory", "other", "none"]},
                "key_evidence": {"type": "string", "maxLength": 300},
                "is_red_flag": {"type": "boolean"},
            },
            "required": ["contingencies_found", "severity", "is_red_flag"],
            "additionalProperties": False,
        },
    },
    "related_party": {
        "keywords": ["related party", "related-party", "affiliate", "transactions with",
                     "certain relationships and related"],
        "system": (
            "You are a forensic accountant. Detect related-party transactions and rate materiality. "
            "Flags: loans to executives, purchases/sales with entities controlled by officers, "
            "management fees to affiliated entities, unusually favorable terms."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "related_party_present": {"type": "boolean"},
                "severity": {"type": "integer", "minimum": 1, "maximum": 5,
                             "description": "1=none, 3=immaterial/small, 5=material or self-dealing"},
                "transaction_type": {"type": "string", "enum": ["loan_to_executive", "affiliate_sale", "management_fee", "lease_with_officer", "consulting_to_related", "other", "none"]},
                "materiality_assessment": {"type": "string", "maxLength": 200},
                "key_evidence": {"type": "string", "maxLength": 300},
                "is_red_flag": {"type": "boolean"},
            },
            "required": ["related_party_present", "severity", "is_red_flag"],
            "additionalProperties": False,
        },
    },
    "goodwill_impairment": {
        "keywords": ["goodwill", "impairment", "intangible asset", "annual impairment test",
                     "recoverability", "fair value of reporting unit"],
        "system": (
            "You are a forensic accountant. From the excerpt, assess goodwill/intangible "
            "impairment risk. Flag triggering events (declining market cap, failed product, "
            "lost customer, regulatory change) that precede a write-down. High risk when a "
            "reporting unit's fair value is 'not substantially in excess' of carrying value."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "goodwill_material": {"type": "boolean"},
                "impairment_taken": {"type": "boolean"},
                "triggering_events_present": {"type": "boolean"},
                "fair_value_headroom": {"type": "string", "enum": ["substantial_excess", "not_substantially_in_excess", "unknown", "n_a"]},
                "severity": {"type": "integer", "minimum": 1, "maximum": 5},
                "key_evidence": {"type": "string", "maxLength": 300},
                "is_red_flag": {"type": "boolean"},
            },
            "required": ["goodwill_material", "severity", "is_red_flag"],
            "additionalProperties": False,
        },
    },
    "debt_covenant": {
        "keywords": ["covenant", "covenants", "in compliance", "default", "debt covenant",
                     "financial covenant", "waiver", "amendment to credit"],
        "system": (
            "You are a forensic accountant. From the excerpt, assess debt-covenant headroom. "
            "Flag when compliance is asserted without margin detail, when a waiver or amendment "
            "was obtained, or when covenant language suggests tight headroom. Silence on "
            "covenant metrics despite material debt is itself a risk signal."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "covenant_language_present": {"type": "boolean"},
                "waiver_or_amendment": {"type": "boolean"},
                "compliance_asserted_without_margin": {"type": "boolean"},
                "severity": {"type": "integer", "minimum": 1, "maximum": 5,
                             "description": "1=healthy/immaterial debt, 5=waiver obtained or likely breach"},
                "key_evidence": {"type": "string", "maxLength": 300},
                "is_red_flag": {"type": "boolean"},
            },
            "required": ["covenant_language_present", "severity", "is_red_flag"],
            "additionalProperties": False,
        },
    },
    "revenue_recognition": {
        "keywords": ["revenue recognition", "ASC 606", "ASC 842", "contract assets",
                     "deferred revenue", "bill-and-hold", "channel stuffing", "gross versus net"],
        "system": (
            "You are a forensic accountant. From the excerpt, flag aggressive revenue-recognition "
            "practices: bill-and-hold, channel stuffing, gross-vs-net overstatement, capitalized "
            "contract costs, unusual deferred-revenue movements. Changes in accounting estimates "
            "that flatter revenue are red flags."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "aggressive_practices_found": {"type": "boolean"},
                "practice_type": {"type": "string", "enum": ["bill_and_hold", "channel_stuffing", "gross_vs_net", "contract_cost_capitalization", "estimate_change", "other", "none"]},
                "severity": {"type": "integer", "minimum": 1, "maximum": 5},
                "key_evidence": {"type": "string", "maxLength": 300},
                "is_red_flag": {"type": "boolean"},
            },
            "required": ["aggressive_practices_found", "severity", "is_red_flag"],
            "additionalProperties": False,
        },
    },
    "customer_concentration": {
        "keywords": ["customer concentration", "largest customer", "significant customer",
                     "single customer", "major customer", "concentration of credit risk"],
        "system": (
            "You are a forensic accountant. Assess customer-concentration risk from the excerpt. "
            "Flag when a material share of revenue (or receivables) depends on one or few named "
            "or unnamed customers, especially if the customer is a distressed or related entity."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "concentration_present": {"type": "boolean"},
                "top_customer_share_pct": {"type": "number", "description": "If stated, else 0"},
                "top_10_share_pct": {"type": "number", "description": "If stated, else 0"},
                "severity": {"type": "integer", "minimum": 1, "maximum": 5,
                             "description": "1=dispersed, 5=dominant single customer"},
                "key_evidence": {"type": "string", "maxLength": 300},
                "is_red_flag": {"type": "boolean"},
            },
            "required": ["concentration_present", "severity", "is_red_flag"],
            "additionalProperties": False,
        },
    },
    "supplier_concentration": {
        "keywords": ["supplier concentration", "single source", "sole source", "single supplier",
                     "key supplier", "dependence on supplier", "raw material supply"],
        "system": (
            "You are a forensic accountant. Assess supplier-concentration risk. Flag single-source "
            "or sole-source dependence on critical inputs, especially from politically or "
            "operationally unstable origins (e.g., single foreign supplier, tariff exposure)."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "concentration_present": {"type": "boolean"},
                "sole_source_any_input": {"type": "boolean"},
                "foreign_dependency": {"type": "boolean"},
                "severity": {"type": "integer", "minimum": 1, "maximum": 5},
                "key_evidence": {"type": "string", "maxLength": 300},
                "is_red_flag": {"type": "boolean"},
            },
            "required": ["concentration_present", "severity", "is_red_flag"],
            "additionalProperties": False,
        },
    },
    "pension_assumptions": {
        "keywords": ["pension", "discount rate", "expected long-term return", "actuarial",
                     "defined benefit", "OPEB", "postretirement benefit"],
        "system": (
            "You are a forensic accountant. From the excerpt, assess whether pension/OPEB "
            "actuarial assumptions (discount rate, expected return, mortality) are being used to "
            "manage earnings. Flag high expected-return assumptions or rising discount rates that "
            "reduce reported expense, and any underfunded status."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "defined_benefit_present": {"type": "boolean"},
                "underfunded": {"type": "boolean"},
                "assumption_change_flatters_earnings": {"type": "boolean"},
                "severity": {"type": "integer", "minimum": 1, "maximum": 5},
                "key_evidence": {"type": "string", "maxLength": 300},
                "is_red_flag": {"type": "boolean"},
            },
            "required": ["defined_benefit_present", "severity", "is_red_flag"],
            "additionalProperties": False,
        },
    },
    "subsequent_events": {
        "keywords": ["subsequent event", "subsequent events", "events subsequent",
                     "note __ - subsequent", "occurred after"],
        "system": (
            "You are a forensic accountant. From the excerpt, extract subsequent events (events "
            "after period-end but before filing). Flag events that materially change the picture: "
            "new financing, acquisitions, divestitures, impairments, defaults, loss of a major "
            "customer, going-concern developments."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "subsequent_events_found": {"type": "boolean"},
                "event_type": {"type": "string", "enum": ["financing", "acquisition", "divestiture", "impairment", "default", "loss_of_customer", "litigation", "other", "none"]},
                "materiality": {"type": "string", "enum": ["immaterial", "material", "potentially_material", "unknown"]},
                "severity": {"type": "integer", "minimum": 1, "maximum": 5},
                "key_evidence": {"type": "string", "maxLength": 300},
                "is_red_flag": {"type": "boolean"},
            },
            "required": ["subsequent_events_found", "severity", "is_red_flag"],
            "additionalProperties": False,
        },
    },
    "mda_consistency": {
        "keywords": ["management", "discussion", "overview", "results of operations",
                     "liquidity and capital", "outlook", "business"],
        "system": (
            "You are a forensic auditor performing a cross-document consistency check. "
            "Compare the MD&A narrative against the financial figures and notes in this filing. "
            "Flag contradictions: narrative claims of growth/strength that conflict with the "
            "numbers, guidance that contradicts the balance sheet, or qualitative claims that "
            "the footnotes undercut. Internal inconsistency is a credibility red flag."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "inconsistency_found": {"type": "boolean"},
                "inconsistency_type": {"type": "string", "enum": ["narrative_vs_numbers", "guidance_vs_balance_sheet", "optimism_vs_trend", "boilerplate_conflict", "none"]},
                "severity": {"type": "integer", "minimum": 1, "maximum": 5,
                             "description": "1=fully consistent, 5=material contradiction"},
                "description": {"type": "string", "maxLength": 300},
                "key_evidence": {"type": "string", "maxLength": 300},
                "is_red_flag": {"type": "boolean"},
            },
            "required": ["inconsistency_found", "severity", "is_red_flag"],
            "additionalProperties": False,
        },
    },
}


def excerpt_around(text: str, keywords: list, window: int = 1600, max_total: int = 5000) -> str:
    """Return a concatenation of windows around keyword matches (case-insensitive)."""
    low = text.lower()
    positions = []
    for kw in keywords:
        kwl = kw.lower()
        start = 0
        while True:
            idx = low.find(kwl, start)
            if idx == -1:
                break
            positions.append(idx)
            start = idx + len(kwl)
    if not positions:
        return ""
    positions = sorted(set(positions))
    # dedupe nearby
    merged = []
    last = -10_000
    for p in positions:
        if p - last > window // 2:
            merged.append(p)
            last = p
    chunks = []
    for p in merged[:4]:
        chunks.append(text[max(0, p - window // 2):p + window].strip())
    result = "\n\n[...]\n\n".join(chunks)
    return result[:max_total]


def load_cases(cases_path: Path):
    seen = set()
    for row in ox_lab.load_jsonl(cases_path):
        cid = row.get("case_id", "")
        if cid and cid not in seen and row.get("snapshot_text"):
            seen.add(cid)
            yield row


def run_lens(client, lens_name, cases_path, out_path, replicates, concurrency):
    spec = LENSES[lens_name]
    tool = {"type": "function", "function": {
        "name": lens_name, "description": f"Forensic {lens_name} extraction.",
        "parameters": spec["schema"], "strict": True,
    }}

    done = set()
    if out_path.exists():
        for rec in ox_lab.load_jsonl(out_path):
            done.add(f"{rec.get('case_id', '')}#{rec.get('replicate', 0)}")

    jobs = []
    for row in load_cases(cases_path):
        excerpt = excerpt_around(row.get("snapshot_text", ""), spec["keywords"])
        if not excerpt:
            continue
        for rep in range(replicates):
            key = f"{row['case_id']}#{rep}"
            if key in done:
                continue
            jobs.append((row, rep, excerpt))

    print(f"[{lens_name}] {len(jobs)} jobs queued (replicates={replicates})", flush=True)

    def classify_one(job):
        row, rep, excerpt = job
        user = (f"Company: {row.get('company','')} ({row.get('ticker','')})\n"
                f"Filed/period cutoff: {row.get('cutoff','')}\n"
                f"Filing excerpt:\n{excerpt}")
        try:
            raw = client.chat(
                [{"role": "system", "content": spec["system"]},
                 {"role": "user", "content": user}],
                temperature=0.05, max_tokens=1200,
                tools=[tool], tool_choice={"type": "function", "function": {"name": lens_name}},
            )
            result = json.loads(raw)
        except Exception:
            result = {"is_red_flag": False, "severity": 0, "error": "extraction_failed"}
        result["case_id"] = row.get("case_id", "")
        result["ticker"] = row.get("ticker", "")
        result["cik"] = row.get("cik", "")
        result["cutoff"] = row.get("cutoff", "")
        result["replicate"] = rep
        return result

    count = 0
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(classify_one, j): j for j in jobs}
        for fut in cf.as_completed(futures):
            result = fut.result()
            ox_lab.append_jsonl(out_path, result)
            count += 1
            if count % 200 == 0:
                print(f"[{lens_name}] {count}/{len(jobs)}", flush=True)
    print(f"[{lens_name}] done: {count}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["list", "run"])
    ap.add_argument("--cases", default=str(ROOT / "lab_runs" / "century_safety_source" / "cases.jsonl"))
    ap.add_argument("--run-dir", default=str(ROOT / "lab_runs" / "unstructured_proto"))
    ap.add_argument("--lens", default=None, help="comma-separated lens names, default all")
    ap.add_argument("--replicates", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=256)
    ap.add_argument("--model", default=None)
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()

    if args.command == "list":
        for name in LENSES:
            print(name)
        return

    api_key = sa.get_api_key(args.api_key)
    if not api_key:
        print("NO OPENROUTER_API_KEY - refusing to run")
        return
    client = sa.OpenRouter(api_key, model=args.model or sa.DEFAULT_MODEL,
                           timeout=120, max_retries=3)

    cases_path = Path(args.cases)
    run_dir = Path(args.run_dir)
    out_dir = run_dir / "forensic"
    out_dir.mkdir(parents=True, exist_ok=True)

    lenses = args.lens.split(",") if args.lens else list(LENSES.keys())
    for name in lenses:
        if name not in LENSES:
            print(f"unknown lens {name}")
            continue
        out_path = out_dir / f"{name}.jsonl"
        t0 = time.monotonic()
        run_lens(client, name, cases_path, out_path, args.replicates, args.concurrency)
        print(f"[{name}] wall {time.monotonic()-t0:.0f}s, total calls {client.calls}", flush=True)


if __name__ == "__main__":
    main()