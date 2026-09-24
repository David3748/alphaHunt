#!/usr/bin/env python3
"""Read-only HTTP monitor for a running century pipeline."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def file_info(path: Path) -> dict:
    if not path.exists():
        return {"rows": 0, "bytes": 0, "modified": None}
    return {"rows": line_count(path), "bytes": path.stat().st_size,
            "modified": dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat()}


def tail(path: Path, size: int = 32_768, lines: int = 24) -> list[str]:
    if not path.exists():
        return []
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - size))
        return handle.read().decode("utf-8", errors="replace").splitlines()[-lines:]


def snapshot(run_dir: Path, source_dir: Path) -> dict:
    state_path = run_dir / "pipeline_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"completed": {}, "attempts": []}
    artifacts = {
        "filings": file_info(run_dir / "filings.jsonl"),
        "symbols": file_info(run_dir / "symbols.jsonl"),
        "preprice": file_info(run_dir / "preprice.jsonl"),
        "cases": file_info(source_dir / "cases.jsonl"),
        "extractions": file_info(run_dir / "extractions.jsonl"),
        "syntheses": file_info(run_dir / "syntheses.jsonl"),
        "outcomes": file_info(run_dir / "outcomes.jsonl"),
    }
    cases = artifacts["cases"]["rows"]
    targets = {"extractions": cases * 5, "syntheses": cases * 2, "outcomes": cases}
    latest = state.get("attempts", [])[-1] if state.get("attempts") else None
    result_path = run_dir / "results.json"
    result = json.loads(result_path.read_text()) if result_path.exists() else None
    hypotheses_path = run_dir / "century_hypotheses.json"
    hypotheses = json.loads(hypotheses_path.read_text()) if hypotheses_path.exists() else None
    miner_dir = run_dir.parent / "obscure_miner"
    miner = {"discoveries": file_info(miner_dir / "discoveries.jsonl"),
             "documents": file_info(miner_dir / "documents.jsonl"),
             "analyses": file_info(miner_dir / "analyses.jsonl"),
             "log_tail": tail(miner_dir / "miner.log", lines=12)}
    return {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "active_stage": latest.get("stage") if latest and not latest.get("finished_at") else None,
            "latest_attempt": latest, "completed": state.get("completed", {}),
            "artifacts": artifacts, "targets": targets,
            "log_tail": tail(run_dir.parent / "century_pipeline.log"),
            "result": ({"validated": result.get("validated"),
                        "scored_cases": result.get("scored_cases"),
                        "causal_selected": result.get("causal_selected"),
                        "conservative": (result.get("bounds") or {}).get("conservative")}
                       if result else None),
            "hypotheses": ({"generated_at": hypotheses.get("generated_at"),
                            "cases": hypotheses.get("cases"),
                            "rules": {name: {"clean_weighted_ir": row.get("clean_weighted_ir"),
                                             "blocked_placebo": row.get("blocked_placebo"),
                                             "passes_secondary_gate": row.get("passes_secondary_gate")}
                                      for name, row in (hypotheses.get("rules") or {}).items()}}
                           if hypotheses else None), "miner": miner}


HTML = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'>
<title>alphaHunt century run</title><style>
body{font:15px system-ui;background:#0b1020;color:#e8edf8;max-width:1000px;margin:32px auto;padding:0 18px}
h1{font-size:28px}.muted{color:#8fa0bd}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.card{background:#141c30;border:1px solid #293552;border-radius:12px;padding:16px}.n{font-size:27px;font-weight:700}
.bar{height:7px;background:#293552;border-radius:5px;overflow:hidden;margin-top:9px}.fill{height:100%;background:#59d2a9}
pre{white-space:pre-wrap;overflow-wrap:anywhere}.ok{color:#59d2a9}.warn{color:#ffc66d}</style></head><body>
<h1>alphaHunt century run</h1><p id=stamp class=muted>Loading…</p><div id=stage class=card></div><h2>Artifacts</h2>
<div id=grid class=grid></div><h2>Live log</h2><pre id=log class=card>Pending</pre><h2>Primary result</h2><pre id=result class=card>Pending</pre><h2>Strategy hypotheses</h2><pre id=hypotheses class=card>Queued after outcomes</pre><h2>Obscure-source miner</h2><pre id=miner class=card>Queued after outcomes</pre><script>
const fmt=n=>new Intl.NumberFormat().format(n||0); const size=n=>n>1e9?(n/1e9).toFixed(1)+' GB':n>1e6?(n/1e6).toFixed(1)+' MB':(n/1e3).toFixed(1)+' KB';
async function load(){let d=await fetch('/api/status',{cache:'no-store'}).then(r=>r.json());
 document.getElementById('stamp').textContent='Updated '+new Date(d.generated_at).toLocaleString();
 document.getElementById('stage').innerHTML='<div class="muted">Active stage</div><div class="n '+(d.active_stage?'warn':'ok')+'">'+(d.active_stage||'Idle / complete')+'</div>';
 document.getElementById('grid').innerHTML=Object.entries(d.artifacts).map(([k,v])=>{let t=d.targets[k]||0,p=t?Math.min(100,100*v.rows/t):0;return `<div class=card><div class=muted>${k}</div><div class=n>${fmt(v.rows)}${t?' / '+fmt(t):''}</div><div class=muted>${size(v.bytes)}</div>${t?`<div class=bar><div class=fill style="width:${p}%"></div></div>`:''}</div>`}).join('');
 document.getElementById('log').textContent=(d.log_tail||[]).join('\\n')||'Pending';
 document.getElementById('result').textContent=d.result?JSON.stringify(d.result,null,2):'Pending';
 document.getElementById('hypotheses').textContent=d.hypotheses?JSON.stringify(d.hypotheses,null,2):'Queued after outcomes';
 document.getElementById('miner').textContent=d.miner?JSON.stringify(d.miner,null,2):'Queued after outcomes';}
 function refresh(){load().catch(e=>{document.getElementById('stamp').textContent='Dashboard error: '+e.message;document.getElementById('stamp').className='warn';});}
 refresh();setInterval(refresh,10000);
</script></body></html>"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=Path("lab_runs/century_safety"))
    parser.add_argument("--source-dir", type=Path, default=Path("lab_runs/century_safety_source"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/status":
                body = json.dumps(snapshot(args.run_dir, args.source_dir), default=str).encode()
                content_type = "application/json"
            elif self.path in ("/", "/index.html"):
                body, content_type = HTML.encode(), "text/html; charset=utf-8"
            else:
                self.send_error(404); return
            self.send_response(200); self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def log_message(self, *_):
            return

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
