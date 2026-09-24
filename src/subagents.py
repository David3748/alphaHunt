#!/usr/bin/env python3
"""subagents - OpenRouter-backed LLM subagent swarm for alphahunt deep hunts.

Each candidate event gets fanned out across specialized reasoning roles.
All HTTP is POST to the OpenRouter chat-completions API; the API key is read
from OPENROUTER_API_KEY or passed explicitly and never logged.
"""

import concurrent.futures
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "stealth/ox-alpha"  # free on OpenRouter ($0/$0)

MAX_FILING_CHARS = 12000


class SubagentError(RuntimeError):
    pass


class RateLimited(RuntimeError):
    """Raised when all retries hit 429 - caller should slow down."""
    pass


def get_api_key(explicit: str | None = None) -> str:
    key = explicit or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise SubagentError(
            "no API key: set OPENROUTER_API_KEY env var or pass --api-key"
        )
    return key


class OpenRouter:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 timeout: int = 90, max_retries: int = 2):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.calls = 0
        self._usage_lock = threading.Lock()

    def chat(self, messages: list[dict], temperature: float = 0.2,
             response_format: dict | None = None,
             reasoning_effort: str | None = None,
             max_tokens: int | None = None,
             tools: list[dict] | None = None,
             tool_choice: dict | str | None = None,
             extra_headers: dict | None = None) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        body = json.dumps(payload).encode()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # Optional attribution headers; harmless if unregistered
            "HTTP-Referer": "https://github.com/alphahunt",
            "X-Title": "alphahunt",
        }
        if extra_headers:
            headers.update(extra_headers)
        last_err = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(OPENROUTER_URL, data=body, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode())
                usage = data.get("usage", {})
                with self._usage_lock:
                    self.calls += 1
                    self.total_prompt_tokens += usage.get("prompt_tokens", 0)
                    self.total_completion_tokens += usage.get("completion_tokens", 0)
                message = data["choices"][0]["message"]
                tool_calls = message.get("tool_calls") or []
                if tool_calls:
                    content = tool_calls[0].get("function", {}).get("arguments")
                else:
                    content = message.get("content")
                # Providers occasionally return HTTP 200 with a null/empty
                # message during long generations. Treat that as a retryable
                # transport failure instead of leaking None into json.loads().
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("OpenRouter returned no usable message content")
                return content
            except urllib.error.HTTPError as exc:
                last_err = exc
                if exc.code == 429 and attempt < self.max_retries:
                    time.sleep(2.0 * (2 ** attempt))  # exponential backoff
                    continue
                if exc.code == 429:
                    raise RateLimited("persistent 429 - reduce concurrency") from exc
            except (urllib.error.URLError, KeyError, TypeError, ValueError,
                    json.JSONDecodeError, OSError) as exc:
                last_err = exc
                if attempt < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
        raise SubagentError(f"openrouter call failed after retries: {last_err}")


# ---------------------------------------------------------------- roles

EXTRACTOR_SYSTEM = """You are a deal-terms extraction agent for an event-driven \
equity research system. You will receive raw SEC filing text about a corporate \
event. Extract structured facts ONLY from the text - never invent numbers. \
Respond with STRICT JSON matching this schema, no prose:
{"ticker_or_cik": "...", "event_type": "merger|reverse_merger|financing|other",
 "acquirer_or_counterparty": "...", "consideration": {"cash_per_share": float|null,
 "stock_exchange_ratio": float|null, "cvr_terms": "..."}, 
 "aggregate_values": {"cash_dividend_usd": float|null, "pipe_usd": float|null,
 "stub_valuation_usd": float|null},
 "expected_close": "YYYY-MM or unknown", "closing_conditions": ["..."],
 "termination_fees": "...", "notable_risks_in_text": ["..."]}"""

SKEPTIC_SYSTEM = """You are an adversarial reviewer for event-driven trade ideas. \
Given extracted deal terms and market data, your job is to KILL bad trades: find \
the reason the spread is fair, the condition most likely to fail, and the worst \
realistic outcome. Be specific and quantitative where possible. Respond with \
STRICT JSON only:
{"fair_value_estimate_per_share": float|null, "probability_deal_completes_pct": int|null,
 "biggest_kill_factor": "...", "worst_case_price": float|null,
 "hidden_costs": ["taxes|borrow|fees..."], "verdict": "avoid|watch|actionable"}"""

THESIS_SYSTEM = """You are a portfolio strategist. Given deal terms, a skeptic's \
attack, and market data, produce a concise actionable thesis. Only propose trades \
where expected value is clearly positive after costs. Respond with STRICT JSON only:
{"direction": "long|short|none", "structure": "shares|debit_spread|calendar",
 "entry_zone": [float|null, float|null], "expected_value_note": "...",
 "catalyst_date": "...", "position_sizing_hint": "small|medium|large|skip"}"""

ANOMALY_SYSTEM = """You are a forensic analyst reading ONE section of an SEC filing \
for a company under deep investigation. Hunt for what does not fit: contradictions, \
vague or evasive language, unusual related-party arrangements, aggressive revenue \
recognition, going-concern signals, auditor changes, customer/supplier concentration, \
numbers that do not match the narrative, sudden executive departures, litigation \
time bombs. Only report what is actually supported by the text - quote it. \
Respond with STRICT JSON only:
{"findings": [{"severity": "high|medium|low", "quote": "...", "why_it_matters": "..."}],
 "nothing_unusual": bool}"""

TRIALS_SYSTEM = """You are a clinical-trials auditor. You get company claims from \
SEC filings and the company's actual registered trials from ClinicalTrials.gov. \
Find gaps: claims of pivotal/pivotal-ready programs with no registered trial, \
endpoint downgrades between registration and filings, silently terminated or \
withdrawn trials, timeline claims inconsistent with registration dates. \
Respond with STRICT JSON only:
{"findings": [{"severity": "high|medium|low", "claim": "...", "registry_reality": "...", "gap": "..."}],
 "nothing_unusual": bool}"""

SYNTH_SYSTEM = """You are the lead investigator merging findings from a swarm of \
forensic analysts who each read part of one company's filings (and possibly its \
clinical trial registry data). Synthesize into an investment-relevant verdict: \
what is the single most important thing this company does not want investors \
thinking about, and does the aggregate evidence suggest quality, neutral, or \
trouble? Be decisive but only from the evidence given. Respond with STRICT JSON only:
{"headline": "...", "verdict": "quality|neutral|trouble", "top_findings": ["..."],
 "red_flag_count": int, "what_to_verify_next": ["..."],
 "trade_implication": "..."}"""


ROLES = {
    "extractor": {"system": EXTRACTOR_SYSTEM, "needs": ["filing_text"]},
    "skeptic": {"system": SKEPTIC_SYSTEM, "needs": ["terms_json", "market_data"]},
    "thesis": {"system": THESIS_SYSTEM, "needs": ["terms_json", "skeptic_json", "market_data"]},
    "anomaly": {"system": ANOMALY_SYSTEM, "needs": ["doc_label", "doc_text"]},
    "trials": {"system": TRIALS_SYSTEM, "needs": ["claims_text", "registry_json"]},
    "synth": {"system": SYNTH_SYSTEM, "needs": ["findings_json"]},
}


def build_user_message(role: str, ctx: dict) -> str:
    parts = []
    if role == "extractor":
        parts.append(f"TICKER/CIK: {ctx.get('ticker') or ctx.get('cik', '?')}")
        filing = (ctx.get("filing_text") or "")[:MAX_FILING_CHARS]
        parts.append(f"RAW FILING TEXT:\n{filing}")
    elif role == "skeptic":
        parts.append(f"MARKET DATA:\n{json.dumps(ctx.get('market_data', {}))}")
        parts.append(f"EXTRACTED TERMS:\n{ctx.get('terms_json')}")
    elif role == "thesis":
        parts.append(f"MARKET DATA:\n{json.dumps(ctx.get('market_data', {}))}")
        parts.append(f"EXTRACTED TERMS:\n{ctx.get('terms_json')}")
        parts.append(f"SKEPTIC REVIEW:\n{ctx.get('skeptic_json')}")
    elif role == "anomaly":
        parts.append(f"COMPANY: {ctx.get('company', '?')}  DOCUMENT: {ctx.get('doc_label', '?')}")
        parts.append(f"TEXT:\n{(ctx.get('doc_text') or '')[:MAX_FILING_CHARS]}")
    elif role == "trials":
        parts.append(f"COMPANY CLAIMS (from SEC filings):\n{(ctx.get('claims_text') or '')[:6000]}")
        parts.append(f"REGISTERED TRIALS (ClinicalTrials.gov):\n{ctx.get('registry_json')}")
    elif role == "synth":
        parts.append(f"COMPANY: {ctx.get('company', '?')}")
        parts.append(f"ALL ANALYST FINDINGS:\n{ctx.get('findings_json')}")
    return "\n\n".join(parts)


def parse_llm_json(text: str) -> dict:
    """Defensively pull the first JSON object out of an LLM response."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise SubagentError("no JSON object in response")
    return json.loads(m.group(0))


def run_role(role: str, ctx: dict, client: OpenRouter) -> dict:
    spec = ROLES[role]
    content = client.chat(
        [{"role": "system", "content": spec["system"]},
         {"role": "user", "content": build_user_message(role, ctx)}],
    )
    try:
        return parse_llm_json(content)
    except (SubagentError, json.JSONDecodeError):
        return {"_unparsed": content[:2000]}


# ---------------------------------------------------------------- orchestration

def deep_candidate(candidate: dict, client: OpenRouter,
                   fetch_filing_text, get_market_data) -> dict:
    """Run the extractor -> skeptic -> thesis chain for one candidate."""
    ctx = dict(candidate)
    try:
        ctx["filing_text"] = fetch_filing_text(ctx)
    except Exception as exc:
        return {**candidate, "error": f"filing fetch failed: {exc}"}
    ctx["market_data"] = get_market_data(ctx)

    terms = run_role("extractor", ctx, client)
    ctx["terms_json"] = json.dumps(terms)
    skeptic = run_role("skeptic", ctx, client)
    ctx["skeptic_json"] = json.dumps(skeptic)
    thesis = run_role("thesis", ctx, client)

    return {**candidate,
            "filing_chars": len(ctx.get("filing_text") or ""),
            "market_data": ctx.get("market_data"),
            "terms": terms, "skeptic": skeptic, "thesis": thesis}


def deep_hunt(candidates: list[dict], client: OpenRouter,
              fetch_filing_text, get_market_data,
              concurrency: int = 16) -> list[dict]:
    """Fan out all candidates in parallel; each candidate chains its roles."""
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(deep_candidate, c, client, fetch_filing_text, get_market_data): c
            for c in candidates
        }
        for fut in concurrent.futures.as_completed(futures):
            cand = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:
                results.append({**cand, "error": str(exc)})
    return sorted(results, key=lambda r: r.get("ticker") or r.get("cik", ""))


def rank_results(results: list[dict]) -> list[dict]:
    """Sort by skeptic verdict then thesis direction."""
    def score(r):
        verdict = (r.get("skeptic") or {}).get("verdict", "")
        direction = (r.get("thesis") or {}).get("direction", "none")
        s = {"actionable": 2, "watch": 1, "avoid": 0}.get(verdict, 0)
        if direction in ("long", "short"):
            s += 1
        return s
    return sorted(results, key=lambda r: (-score(r), r.get("error") is not None))


# ---------------------------------------------------------------- investigation

def investigate(docs: list[dict], client: OpenRouter,
                registry_json: str | None = None,
                claims_text: str = "", concurrency: int = 16) -> dict:
    """Swarm forensic read of one company's documents.

    docs: [{"label": "10-K FY2025", "text": "..."}, ...]
    Returns {"doc_findings": [...], "trials": {...}|None, "synthesis": {...}}
    """
    company = docs[0]["company"] if docs and "company" in docs[0] else "?"
    doc_results = []

    def scan_doc(doc):
        ctx = {"company": company, "doc_label": doc["label"],
               "doc_text": doc["text"], "ticker": doc.get("ticker")}
        try:
            return {"label": doc["label"], "findings": run_role("anomaly", ctx, client)}
        except Exception as exc:
            return {"label": doc["label"], "error": str(exc)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(scan_doc, d) for d in docs]
        for fut in concurrent.futures.as_completed(futures):
            doc_results.append(fut.result())
    doc_results.sort(key=lambda r: r["label"])

    trials_result = None
    if registry_json:
        try:
            trials_result = run_role("trials", {
                "company": company, "claims_text": claims_text,
                "registry_json": registry_json}, client)
        except Exception as exc:
            trials_result = {"error": str(exc)}

    all_findings = []
    for dr in doc_results:
        for f in (dr.get("findings") or {}).get("findings", []):
            all_findings.append({"doc": dr["label"], **f})
    if trials_result and isinstance(trials_result, dict):
        for f in trials_result.get("findings", []):
            all_findings.append({"doc": "ClinicalTrials.gov", **f})

    synthesis = run_role("synth", {
        "company": company,
        "findings_json": json.dumps(all_findings)[:40000]}, client)

    return {"company": company, "docs_scanned": len(doc_results),
            "doc_findings": doc_results, "trials": trials_result,
            "all_findings": all_findings, "synthesis": synthesis}
