#!/usr/bin/env python3
"""Assemble docs/index.html from editable pieces.

Edit these, then run `python3 build.py`:
  assets/style.css      — all styling; design tokens in :root
  assets/charts.js      — every figure renderer (pure SVG, no deps)
  content/*.html        — chapter prose, concatenated in filename order
  data/*.json           — chart data (perf, cohorts, forward, board, cases, prices)

Outputs:
  index.html            — self-contained page ready for GitHub Pages
  qa/figures.html       — all figures stacked large, for screenshot QA
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # docs/
OUT = ROOT / "index.html"

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>&amp;Goliath | the hunt for uncorrelated alpha, week one</title>
<meta name="description" content="One week, 125,000 LLM calls, and a live forward cohort. A story about testing whether AI can read SEC filings well enough to beat the market — including the parts that went badly.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Merriweather:wght@400;700&display=swap" rel="stylesheet">
<style>
{{CSS}}
</style>
</head>
<body>

<header class="site"><div class="hwrap">
<a class="mark" href="/"><span style="color:#B39DFF">&amp;</span> Goliath</a>
<nav class="top"><a href="/writing">Writing</a><a href="/projects">Projects</a><a href="/resume">Resume</a></nav>
</div></header>

<main>
{{CONTENT}}
</main>

<footer class="site">
<div class="mark"><span style="color:#B39DFF">&amp;</span> Goliath</div>
<div>© 2026 David Lieman · <a href="https://github.com/David3748">GitHub</a> · <a href="https://www.linkedin.com/in/david-lieman/">LinkedIn</a></div>
</footer>

<script>
const DATA = {{DATA}};
{{CHARTJS}}
</script>
</body>
</html>
"""

QA_TEMPLATE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>figure QA</title><link rel="stylesheet" href="../assets/style.css"></head>
<body style="background:var(--bg);padding:2rem">
{{FIGURES}}
<script>
const DATA = {{DATA}};
{{CHARTJS}}
</script></body></html>
"""


def main() -> int:
    css = (ROOT / "assets/style.css").read_text()
    js = (ROOT / "assets/charts.js").read_text()

    content = []
    for fragment in sorted((ROOT / "content").glob("*.html")):
        content.append(fragment.read_text().strip())
    content_html = "\n\n".join(content)

    data = {}
    for jf in sorted((ROOT / "data").glob("*.json")):
        data[jf.stem] = json.loads(jf.read_text())
    # Flatten to the key layout charts.js expects:
    # prices.json holds nested benchmark/price series; board.json holds per-rule objects.
    prices, board = data.pop("prices"), data.pop("board")
    payload = json.dumps({
        "spy_weekly": prices["spy_weekly"],
        "prct": prices["prct"],
        "board": board["p20"],          # the page's prediction table is the lead rule
        "crash": data.pop("cohorts"),   # monthly eligible-event counts (Figure 1)
        **data,
    }, separators=(",", ":"))

    page = (TEMPLATE
            .replace("{{CSS}}", css)
            .replace("{{CHARTJS}}", js)
            .replace("{{DATA}}", payload)
            .replace("{{CONTENT}}", content_html))
    OUT.write_text(page)

    figure_ids = ["chart-crash", "chart-pipeline", "chart-funnel", "chart-clock",
                  "chart-dt", "chart-wix", "chart-prct", "chart-perf", "chart-forward"]
    figs = "\n".join(
        f'<h3 style="color:#f3f4f6;font-family:Merriweather">{fid}</h3>'
        f'<svg id="{fid}" class="chart" style="background:var(--panel);border-radius:.5rem"'
        f' width="1100" height="420"></svg><hr>'
        for fid in figure_ids)
    qa = (QA_TEMPLATE.replace("{{FIGURES}}", figs)
          .replace("{{CHARTJS}}", js).replace("{{DATA}}", payload))
    (ROOT / "qa/figures.html").write_text(qa)

    print(f"built {OUT} ({OUT.stat().st_size//1024} KB) + qa/figures.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
