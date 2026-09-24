#!/usr/bin/env python3
"""dashboard - static HTML/SVG visualization of alphahunt hunt results.

Stdlib only. Reads optional deep-report.md (LLM swarm output) plus a
findings JSON snapshot; emits a self-contained dashboard.html with
inline SVG charts. No JS dependencies.

Usage:
  python3 src/dashboard.py [--findings findings.json] [--deep deep-report.md] [--out dashboard.html]
"""

import argparse
import datetime as dt
import html
import json
import re

# Verified session findings (2026-08-21) used when no findings.json exists
DEFAULT_FINDINGS = [
    {"ticker": "TECH", "company": "Bio-Techne / Merck KGaA",
     "price": 72.32, "implied": 73.00, "days_to_close": 147,
     "event": "cash merger $73.00/sh", "verdict": "efficient - thin arb"},
    {"ticker": "TBPH", "company": "Theravance / Zymeworks",
     "price": 17.01, "implied": 17.00, "days_to_close": 90,
     "event": "$17 cash + failed-drug CVR (Ph3 CYPRESS fail)", "verdict": "rational - CVR ~ $0"},
    {"ticker": "FULC", "company": "Fulcrum / Slate reverse merger",
     "price": 3.83, "implied": 3.95, "days_to_close": 120,
     "event": "$31.3M stub + $270M dividend / 76.3M sh", "verdict": "fairly priced"},
    {"ticker": "TALK", "company": "Talkspace / UHS",
     "price": 5.25, "implied": 5.25, "days_to_close": 0,
     "event": "$5.25 cash - closed 8/17", "verdict": "closed"},
]

CSS = """
body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;max-width:960px;margin:0 auto;padding:2rem 1.25rem;line-height:1.5}
h1{font-size:1.4rem;border-bottom:2px solid #8884;padding-bottom:.4rem}
h2{font-size:1.1rem;margin-top:2rem}
.card{border:1px solid #8884;border-radius:10px;padding:1rem;margin:.8rem 0;background:#88881a08}
.chip{display:inline-block;padding:.15em .6em;border-radius:99px;font-size:.78rem;font-weight:600;margin-left:.4em}
.chip.green{background:#2a72233;color:#1a7a3a}.chip.amber{background:#c882233;color:#b07818}.chip.red{background:#c442233;color:#c04a3a}.chip.gray{background:#88822244;color:#777}
table{border-collapse:collapse;width:100%;font-size:.9rem;margin:1rem 0}
th,td{border:1px solid #8884;padding:.45rem .6rem;text-align:left}
th{background:#88842218}
.muted{color:#888;font-size:.85rem}
.flow{display:flex;gap:.5rem;flex-wrap:wrap;margin:.8rem 0}
.stage{border:2px solid #678;border-radius:8px;padding:.5rem .8rem;font-size:.85rem;background:#67821122}
.arrow{align-self:center;color:#888}
svg text{font-family:inherit}
"""


def chip_for(verdict: str) -> str:
    v = verdict.lower()
    cls = "gray"
    if "actionable" in v or "long" in v:
        cls = "green"
    elif any(w in v for w in ("fair", "rational", "watch", "thin")):
        cls = "amber"
    elif "avoid" in v or "closed" in v:
        cls = "red"
    return f'<span class="chip {cls}">{html.escape(verdict)}</span>'


def annualized(price: float, implied: float, days: int) -> float | None:
    if days <= 0 or price <= 0 or implied <= price:
        return None
    sp = implied / price - 1
    return (1 + sp) ** (365 / days) - 1


def svg_price_vs_implied(rows: list[dict]) -> str:
    """Grouped horizontal bars: market price vs implied deal value."""
    W, LH, PAD = 760, 64, 130
    H = PAD + len(rows) * LH + 20
    mx = max(max(r["price"], r["implied"]) for r in rows) * 1.12
    def x(v): return 60 + (v / mx) * (W - 100)
    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="price vs implied">']
    parts.append(f'<text x="{W/2}" y="24" text-anchor="middle" font-weight="600">Market price vs implied deal value ($/share)</text>')
    colors = {"price": "#4878a8", "implied": "#d0862c"}
    for i, r in enumerate(rows):
        y = PAD + i * LH
        parts.append(f'<text x="55" y="{y+18}" text-anchor="end" font-weight="600">{html.escape(r["ticker"])}</text>')
        for j, key in enumerate(("price", "implied")):
            yy = y + j * 18
            w = max(2, x(r[key]) - 60)
            label = f'${r[key]:.2f}'
            parts.append(f'<rect x="60" y="{yy}" width="{w}" height="13" rx="3" fill="{colors[key]}" opacity=".85"/>')
            parts.append(f'<text x="{62+w}" y="{yy+11}" font-size="11">{label}</text>')
    parts.append(f'<rect x="60" width="11" height="11" fill="{colors["price"]}"/><text x="76" y="{H-16}" font-size="11">market</text>'
                 f'<rect x="150" width="11" height="11" fill="{colors["implied"]}"/><text x="166" y="{H-16}" font-size="11">implied by terms</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_spread_bars(rows: list[dict]) -> str:
    """Annualized spread %, live trades only."""
    data = []
    for r in rows:
        ann = annualized(r["price"], r["implied"], r["days_to_close"])
        if ann is not None:
            data.append((r["ticker"], ann))
    if not data:
        return '<p class="muted">No live positive-spread trades.</p>'
    W, BH, PAD = 760, 34, 40
    H = PAD + len(data) * BH + 16
    mx = max(v for _, v in data) * 1.25
    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="annualized spread">',
             f'<text x="{W/2}" y="24" text-anchor="middle" font-weight="600">Annualized gross spread (before costs)</text>']
    for i, (t, v) in enumerate(data):
        y = PAD + i * BH
        w = max(3, (v / mx) * (W - 200))
        parts.append(f'<text x="55" y="{y+16}" text-anchor="end" font-weight="600">{t}</text>')
        parts.append(f'<rect x="60" y="{y}" width="{w:.0f}" height="20" rx="4" fill="#7a4ea8" opacity=".85"/>')
        parts.append(f'<text x="{66+w:.0f}" y="{y+15}" font-size="12">{v*100:.1f}%</text>')
    parts.append("</svg>")
    return "".join(parts)


def parse_deep_report(path: str) -> list[dict]:
    try:
        text = open(path).read()
    except OSError:
        return []
    out = []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        lines = block.strip().splitlines()
        name = lines[0].strip() if lines else "?"
        d = {"name": name}
        for ln in lines:
            for key, tag in (("skeptic", "- skeptic:"), ("thesis", "- thesis:"), ("ev", "- EV note:")):
                if ln.startswith(tag):
                    d[key] = ln[len(tag):].strip()
        out.append(d)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="dashboard")
    p.add_argument("--findings", default=None, help="JSON file with hunt findings")
    p.add_argument("--deep", default="reports/deep-report.md")
    p.add_argument("--out", default="reports/dashboard.html")
    a = p.parse_args(argv)

    rows = DEFAULT_FINDINGS
    if a.findings:
        rows = json.load(open(a.findings))

    deep = parse_deep_report(a.deep)

    table_rows = "".join(
        f"<tr><td><strong>{r['ticker']}</strong></td><td>{html.escape(r['company'])}</td>"
        f"<td>${r['price']:.2f}</td><td>${r['implied']:.2f}</td><td>{r['days_to_close']}d</td>"
        f"<td>{html.escape(r['event'])}</td><td>{chip_for(r['verdict'])}</td></tr>"
        for r in rows
    )
    deep_html = ""
    if deep:
        items = "".join(
            f"<div class='card'><strong>{html.escape(d['name'])}</strong><br>"
            f"<span class='muted'>skeptic:</span> {html.escape(d.get('skeptic', 'n/a'))}<br>"
            f"<span class='muted'>thesis:</span> {html.escape(d.get('thesis', 'n/a'))}</div>"
            for d in deep
        )
        deep_html = f"<h2>LLM subagent swarm verdicts ({len(deep)} candidates)</h2>{items}"

    page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>alphaHunt dashboard</title><style>{CSS}</style></head><body>
<h1>alphaHunt Dashboard <span class="muted">{dt.date.today().isoformat()}</span></h1>
<div class="flow">
<span class="stage">EDGAR sweep</span><span class="arrow">&#8594;</span>
<span class="stage">deal-term math</span><span class="arrow">&#8594;</span>
<span class="stage">live prices/XBRL</span><span class="arrow">&#8594;</span>
<span class="stage">LLM skeptic swarm</span><span class="arrow">&#8594;</span>
<span class="stage">ranked report</span>
</div>
<h2>Hunt results</h2>
<table><tr><th>Ticker</th><th>Event</th><th>Mkt</th><th>Implied</th><th>Close</th><th>Terms</th><th>Verdict</th></tr>
{table_rows}</table>
<h2>Charts</h2>
<div class="card">{svg_price_vs_implied([r for r in rows if r['days_to_close'] > 0])}</div>
<div class="card">{svg_spread_bars(rows)}</div>
{deep_html}
<p class="muted">Generated by alphahunt dashboard.py - static, self-contained, no external assets. Not investment advice.</p>
</body></html>"""
    with open(a.out, "w") as fh:
        fh.write(page)
    print(f"wrote {a.out} ({len(rows)} findings, {len(deep)} deep-report entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
