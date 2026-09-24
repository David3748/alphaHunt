#!/usr/bin/env python3
"""Render the obscure-source miner results as a static HTML page."""

from __future__ import annotations

import datetime as dt
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports/miner.html"

CSS = """/* shared with pipeline.html */
:root { color-scheme: light;
  --surface-1:#fcfcfb; --plane:#f9f9f7; --text-primary:#0b0b0b; --text-secondary:#52514e;
  --text-muted:#898781; --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,.10);
  --series-1:#2a78d6; --status-good:#0ca30c; --status-warning:#fab219; --status-critical:#d03b3b; }
@media (prefers-color-scheme: dark) {
  :root { color-scheme: dark;
    --surface-1:#1a1a19; --plane:#0d0d0d; --text-primary:#fff; --text-secondary:#c3c2b7;
    --text-muted:#898781; --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,.10);
    --series-1:#3987e5; --track:#184f95; } }
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--plane);
  color:var(--text-primary);margin:0;padding:2.5rem 1.25rem 5rem;line-height:1.6}
main{max-width:62rem;margin:0 auto}
h1{font-size:1.9rem;line-height:1.2;margin:0 0 .4rem;letter-spacing:-.02em}
h2{font-size:1.15rem;margin:3rem 0 .75rem;padding-bottom:.45rem;border-bottom:1px solid var(--grid)}
p{margin:.7rem 0;color:var(--text-secondary);max-width:60ch}
p.lead{font-size:1.02rem;color:var(--text-primary);max-width:62ch}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.86em}
table{width:100%;border-collapse:collapse;margin:1rem 0;font-size:.9rem}
th,td{text-align:left;padding:.5rem .65rem;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--text-muted);font-weight:600;font-size:.78rem;text-transform:uppercase;letter-spacing:.04em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:.12rem .6rem;border-radius:99px;font-size:.74rem;font-weight:600;
  border:1px solid var(--border);background:var(--surface-1)}
.pill.good{color:var(--status-good)} .pill.warn{color:var(--status-warning)} .pill.bad{color:var(--status-critical)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:.8rem;margin:1.4rem 0}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:.6rem;padding:1rem .9rem}
.card .n{font-size:1.55rem;font-weight:700;font-variant-numeric:tabular-nums}
.card .l{color:var(--text-muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.05em;margin-top:.15rem}
footer{margin-top:4rem;color:var(--text-muted);font-size:.82rem;border-top:1px solid var(--grid);padding-top:1rem}
tr:hover td{background:color-mix(in srgb,var(--series-1) 5%,transparent)}
"""


def esc(v) -> str:
    return str(v if v is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main() -> int:
    rows = []
    with (ROOT / "lab_runs/obscure_miner/analyses.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    decisions = {}
    for row in rows:
        d = ((row.get("consensus") or {}).get("decision") or "?").lower()
        decisions[d] = decisions.get(d, 0) + 1
    scores = [row.get("underreaction_score") or 0 for row in rows]
    keep = [row for row in rows
            if ((row.get("consensus") or {}).get("decision") or "").lower() != "reject"]
    keep.sort(key=lambda r: -(r.get("underreaction_score") or 0))
    generated = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

    parts = ["""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>alphaHunt — obscure-source miner</title>
<style>""" + CSS + """</style></head><body><main>
<h1>alphaHunt — obscure-source miner</h1>
<p class="lead">Non-filing public evidence mined behind the century safety corpus: GDELT,
Federal Register, ClinicalTrials.gov, openFDA, and broad news RSS over issuers drawn from the
scored universe. Every document passes an analyst → skeptic → consensus LLM gate before it can
become a candidate.</p>

<h2>Funnel</h2>"""]
    n = len(rows)
    cards = [(f"{n:,}", "documents analyzed"), (f"{decisions.get('reject', 0):,}", "rejected"),
             (f"{decisions.get('watch', 0):,}", "watch"), (f"{decisions.get('long_candidate', 0):,}", "long candidates"),
             (f"{statistics.median(scores):.2f}", "median underreaction score"),
             (f"{max(scores):.1f}", "max score")]
    parts.append('<div class="cards">' + "".join(
        f'<div class="card"><div class="n">{v}</div><div class="l">{l}</div></div>' for v, l in cards) + "</div>")

    parts.append("""<h2>Survivors, ranked by underreaction score</h2>
<p>The score is the geometric mean of novelty, materiality, source credibility, and entity-link
confidence, discounted by the skeptic's already-known and priced-in estimates. Rejects are
hidden; this is everything the consensus did not kill.</p>
<table><thead><tr><th>Score</th><th>Ticker</th><th>Company</th><th>Published</th><th>Source family</th>
<th>Consensus</th><th>Direction</th><th>Catalyst window</th><th>Fact</th></tr></thead><tbody>""")
    for row in keep[:40]:
        c = row.get("consensus") or {}
        a = row.get("analysis") or {}
        s = row.get("skeptic") or {}
        window = a.get("catalyst_window_days")
        pill = {"long_candidate": "good", "watch": "warn", "reject": "bad"}.get(
            (c.get("decision") or "").lower(), "")
        parts.append(
            f"<tr><td class='num'><strong>{(row.get('underreaction_score') or 0):.1f}</strong></td>"
            f"<td class='mono'>{esc(row.get('ticker'))}</td><td>{esc((row.get('company') or '')[:40])}</td>"
            f"<td>{esc(str(row.get('published_at') or '')[:10])}</td>"
            f"<td>{esc(row.get('discovery_provider'))}</td>"
            f"<td><span class='pill {pill}'>{esc(c.get('decision'))}</span></td>"
            f"<td>{esc(a.get('direction'))}</td>"
            f"<td class='num'>{'' if window is None else f'{window}d'}</td>"
            f"<td>{esc((a.get('fact') or '')[:150])}</td></tr>")
    parts.append("</tbody></table>")
    parts.append(f"""<h2>Notes</h2>
<p>Analysis cap is 12,000 documents per the miner config; {n:,} were analyzed. The single
<code>long_candidate</code> and the top watches are research leads requiring the verification
steps embedded in their consensus records — they are not trades.</p>
<footer>Generated {generated} from <code>lab_runs/obscure_miner/analyses.jsonl</code>
(synced from <code>gdrive:alphaHunt/obscure_miner</code>). Prompts and schemas:
<code>src/obscure_miner.py</code>.</footer></main></body></html>""")
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "rows": n, "survivors": len(keep),
                      "decisions": decisions}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
