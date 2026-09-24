#!/usr/bin/env python3
"""proto_monitor.py — live dashboard for lab_runs/unstructured_proto.

Tracks three pipelines: unstructured (8 sources), forensic (11 lenses over the
century corpus), wave2 (5 sources). Serves / (HTML) and /api/stats (JSON).
Run: python3 src/proto_monitor.py --port 8081
"""

import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GROUPS = [
    ("unstructured", "Wave 1 — Unstructured sources", [
        ("noaa_storms", "NOAA Storm Narratives", "noaa_storm_events.jsonl", "noaa_extracted.jsonl"),
        ("departures_8k", "8-K Exec Departures", "departures.jsonl", "departures_extracted.jsonl"),
        ("usda_segments", "Commodity Exposure", "usda_segments.jsonl", "usda_extracted.jsonl"),
        ("wikipedia", "Wikipedia Anomalies", "wikipedia_anomalies.jsonl", "wikipedia_extracted.jsonl"),
        ("steam", "Steam Review Bombs", "steam_reviews.jsonl", "steam_extracted.jsonl"),
        ("cpsc", "CPSC Recalls", "cpsc_recalls.jsonl", "cpsc_extracted.jsonl"),
        ("fda_recalls", "FDA Recalls", "fda_enforcement.jsonl", "fda_extracted.jsonl"),
        ("sbir", "SBIR Phase II", "sbir_awards.jsonl", "sbir_extracted.jsonl"),
    ]),
    ("forensic", "Forensic lenses (corpus re-mine)", [
        ("going_concern", "Going concern", None, "forensic/going_concern.jsonl"),
        ("loss_contingency", "Loss contingencies", None, "forensic/loss_contingency.jsonl"),
        ("related_party", "Related party", None, "forensic/related_party.jsonl"),
        ("goodwill_impairment", "Goodwill impairment", None, "forensic/goodwill_impairment.jsonl"),
        ("debt_covenant", "Debt covenants", None, "forensic/debt_covenant.jsonl"),
        ("revenue_recognition", "Revenue recognition", None, "forensic/revenue_recognition.jsonl"),
        ("customer_concentration", "Customer concentration", None, "forensic/customer_concentration.jsonl"),
        ("supplier_concentration", "Supplier concentration", None, "forensic/supplier_concentration.jsonl"),
        ("pension_assumptions", "Pension assumptions", None, "forensic/pension_assumptions.jsonl"),
        ("subsequent_events", "Subsequent events", None, "forensic/subsequent_events.jsonl"),
        ("mda_consistency", "MD&A consistency", None, "forensic/mda_consistency.jsonl"),
    ]),
    ("wave2", "Wave 2 — comment letters / insider / crt", [
        ("comment_letters", "SEC Comment Letters", "comment_letters.jsonl", "comment_letters_extracted.jsonl"),
        ("form4", "Form 4 Insider (10b5-1)", "form4.jsonl", "form4_extracted.jsonl"),
        ("formd", "Form D Raises", "formd.jsonl", "formd_extracted.jsonl"),
        ("fda_orphan", "FDA Orphan Designations", "fda_orphan.jsonl", "fda_orphan_extracted.jsonl"),
        ("crt", "crt.sh Subdomains", "crt_subdomains.jsonl", "crt_extracted.jsonl"),
    ]),
]

LOGS = {
    "wave1": "pipeline.log",
    "forensic": "forensic.log",
    "wave2": "wave2.log",
}


def count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def parse_calls(log_text):
    """Sum LLM call counts reported across any pipeline log."""
    total = 0
    for m in re.finditer(r"LLM calls:\s*(\d+)", log_text):
        total += int(m.group(1))
    for m in re.finditer(r"total calls\s+(\d+)", log_text):
        total += int(m.group(1))
    for m in re.finditer(r"calls\s+(\d+)$", log_text, re.M):
        total += int(m.group(1))
    return total


def build_stats(run_dir: Path) -> dict:
    groups = []
    total_pulled = 0
    total_extracted = 0
    corpus_cases = count_lines(ROOT / "lab_runs" / "century_safety_source" / "cases.jsonl")
    for gkey, glabel, sources in GROUPS:
        gsrc = []
        g_pulled = 0
        g_extracted = 0
        for key, label, pulled_name, extracted_name in sources:
            pulled = count_lines(run_dir / pulled_name) if pulled_name else 0
            extracted = count_lines(run_dir / extracted_name) if extracted_name else 0
            g_pulled += pulled
            g_extracted += extracted
            age = None
            ref = None
            if extracted_name:
                p = run_dir / extracted_name
                if p.exists():
                    ref = p
            if ref is None and pulled_name:
                p = run_dir / pulled_name
                if p.exists():
                    ref = p
            if ref:
                age = round(time.time() - ref.stat().st_mtime)
            gsrc.append({"key": key, "label": label, "pulled": pulled, "extracted": extracted,
                         "last_update_age_sec": age})
        total_pulled += g_pulled
        total_extracted += g_extracted
        groups.append({"key": gkey, "label": glabel, "sources": gsrc,
                       "pulled": g_pulled, "extracted": g_extracted})

    # phase detection from wave1 log
    phase = "waiting"
    log_path = run_dir / "pipeline.log"
    if log_path.exists():
        txt = log_path.read_text(errors="replace")
        tail = "\n".join(txt.splitlines()[-20:])
        if "EXTRACT" in tail and "DONE" not in tail:
            phase = "extracting"
        elif "--- PULL ---" in tail:
            phase = "pulling"
        elif "DONE" in tail:
            phase = "done"

    # active process detection
    running = {}
    for key, logfile in LOGS.items():
        p = run_dir / logfile
        if p.exists():
            mtime = p.stat().st_mtime
            running[key] = (time.time() - mtime) < 300
        else:
            running[key] = False

    # combined log calls + tails
    log_tails = {}
    llm_calls = 0
    for key, logfile in LOGS.items():
        p = run_dir / logfile
        if p.exists():
            txt = p.read_text(errors="replace")
            llm_calls += parse_calls(txt)
            log_tails[key] = txt.splitlines()[-25:]
        else:
            log_tails[key] = []

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": phase,
        "corpus_cases": corpus_cases,
        "totals": {"pulled": total_pulled, "extracted": total_extracted, "llm_calls_logged": llm_calls},
        "groups": groups,
        "running": running,
        "log_tails": log_tails,
    }


HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>alphaHunt · alpha miner</title>
<style>
:root{--bg:#0b0e14;--card:#141926;--line:#232b3d;--fg:#dbe4f0;--dim:#7c8899;--ok:#3fb96f;--warn:#d9a13b;--run:#4a9eff;--err:#e05561}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:24px}
h1{font-size:18px;margin:0 0 2px}.sub{color:var(--dim);font-size:12px;margin-bottom:18px}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600}
.badge.pulling{background:#12314e;color:var(--run)}.badge.extracting{background:#173625;color:var(--ok)}
.badge.done{background:#1d2536;color:var(--fg)}.badge.waiting{background:#332712;color:var(--warn)}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
.card{flex:1 1 150px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .n{font-size:26px;font-weight:700}.card .t{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.grp{margin-bottom:22px}
.grp h2{font-size:13px;color:var(--dim);text-transform:uppercase;letter-spacing:.08em;margin:0 0 8px;display:flex;align-items:center;gap:8px}
.pip-dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.on{background:var(--ok);box-shadow:0 0 8px var(--ok)}.off{background:#39435a}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 12px;border-bottom:1px solid var(--line);font-size:13px}
th{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.07em}
tr:last-child td{border-bottom:none}.num{text-align:right;font-variant-numeric:tabular-nums}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;vertical-align:1px}
.fresh{background:var(--ok)}.stale{background:var(--warn)}.dead{background:#39435a}
.logs{display:flex;gap:12px;flex-wrap:wrap}
.logbox{flex:1 1 340px;min-width:320px}
.logbox h2{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.07em;margin:0 0 8px}
pre{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;font-size:11px;line-height:1.5;max-height:300px;overflow:auto;color:#9fb0c5;white-space:pre-wrap;margin:0}
.updated{color:var(--dim);font-size:11px;text-align:right;margin-top:10px}
</style></head><body><div class="wrap">
<h1>alpha miner <span id="phase" class="badge waiting">…</span></h1>
<div class="sub" id="sub">40.125.84.81:8081 · lab_runs/unstructured_proto</div>
<div class="cards">
<div class="card"><div class="n" id="extracted">–</div><div class="t">LLM extractions</div></div>
<div class="card"><div class="n" id="pulled">–</div><div class="t">records pulled</div></div>
<div class="card"><div class="n" id="calls">–</div><div class="t">llm calls logged</div></div>
<div class="card"><div class="n" id="corpus">–</div><div class="t">corpus cases</div></div>
</div>
<div id="groups"></div>
<div class="logs" id="logs"></div>
<div class="updated" id="upd"></div>
</div>
<script>
function fmtAgo(s){if(s==null)return"–";if(s<60)return s+"s";if(s<3600)return Math.floor(s/60)+"m";return Math.floor(s/3600)+"h"+Math.floor((s%3600)/60)+"m"}
async function tick(){
 try{
  const r=await fetch('/api/stats');const d=await r.json();
  document.getElementById('phase').textContent=d.phase;
  document.getElementById('phase').className='badge '+d.phase;
  document.getElementById('extracted').textContent=d.totals.extracted.toLocaleString();
  document.getElementById('pulled').textContent=d.totals.pulled.toLocaleString();
  document.getElementById('calls').textContent=d.totals.llm_calls_logged.toLocaleString();
  document.getElementById('corpus').textContent=d.corpus_cases.toLocaleString();
  const pipDot=k=>`<span class="pip-dot ${d.running[k]?'on':'off'}"></span>`;
  document.getElementById('groups').innerHTML=d.groups.map(g=>{
   const rows=g.sources.map(s=>{
    const fresh=s.last_update_age_sec!=null&&s.last_update_age_sec<180;
    const cls=s.extracted==0&&s.pulled==0?'dead':(fresh?'fresh':'stale');
    return `<tr><td><span class="dot ${cls}"></span>${s.label}</td>
     <td class="num">${s.pulled? s.pulled.toLocaleString():'—'}</td>
     <td class="num">${s.extracted.toLocaleString()}</td>
     <td class="num">${fmtAgo(s.last_update_age_sec)} ago</td></tr>`}).join('');
   return `<div class="grp"><h2>${g.label} <span class="num" style="margin-left:auto;color:var(--dim)">${g.extracted.toLocaleString()} done</span></h2>
     <table><thead><tr><th>source</th><th class="num">pulled</th><th class="num">extracted</th><th class="num">updated</th></tr></thead>
     <tbody>${rows}</tbody></table></div>`}).join('');
  const keys=['wave1','forensic','wave2'];
  document.getElementById('logs').innerHTML=keys.map(k=>`<div class="logbox"><h2>${pipDot(k)} ${k}.log</h2><pre>${(d.log_tails[k]||[]).join('\\n')||'…'}</pre></div>`).join('');
  document.getElementById('upd').textContent='refreshed '+new Date().toLocaleTimeString();
 }catch(e){document.getElementById('upd').textContent='fetch failed: '+e}
}
tick();setInterval(tick,4000);
</script></body></html>"""


_CACHE = {"ts": 0.0, "data": None, "lock": None}
import threading as _threading
_CACHE["lock"] = _threading.Lock()


def get_stats(run_dir, ttl=3.0):
    with _CACHE["lock"]:
        now = time.time()
        if _CACHE["data"] is None or now - _CACHE["ts"] > ttl:
            _CACHE["data"] = build_stats(run_dir)
            _CACHE["ts"] = now
        return _CACHE["data"]


class Handler(BaseHTTPRequestHandler):
    run_dir: Path

    def do_GET(self):
        if self.path.startswith("/api/stats"):
            body = json.dumps(get_stats(self.run_dir)).encode()
            ctype = "application/json"
        else:
            body = HTML.encode()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--run-dir", default=str(ROOT / "lab_runs" / "unstructured_proto"))
    args = ap.parse_args()
    Handler.run_dir = Path(args.run_dir)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"proto_monitor serving {Handler.run_dir} on :{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()