#!/usr/bin/env python3
"""dossier_board - live HTML board of forensic dossiers.

Parses dossiers/*.md, ranks by interestingness, writes board.html.
Run once, or with --watch to regenerate every N seconds.

Usage:
  python3 src/dossier_board.py [--dir dossiers] [--out board.html] [--watch [SECS]]
"""

import argparse
import glob
import html
import json
import os
import re
import time

CSS = """
body{font-family:-apple-system,'Segoe UI',Roboto,sans-serif;max-width:1060px;margin:0 auto;padding:1.5rem 1.2rem;line-height:1.45;background:#0e1116;color:#d8dee6}
h1{font-size:1.35rem}h1 .muted{font-size:.8rem;color:#7a8595;font-weight:400}
.stats{display:flex;gap:.6rem;flex-wrap:wrap;margin:.8rem 0 1.4rem}
.stat{border:1px solid #2a3240;border-radius:8px;padding:.5rem .9rem;background:#161c26}
.stat b{font-size:1.25rem;display:block}
.card{border:1px solid #2a3240;border-radius:10px;padding:.85rem 1rem;margin:.7rem 0;background:#141a23}
.card h3{margin:.1rem 0 .35rem;font-size:1rem;display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.card .headline{color:#c3ccd8;font-size:.92rem}
.chip{padding:.1em .55em;border-radius:99px;font-size:.72rem;font-weight:700}
.chip.TROUBLE{background:#5b1d22;color:#ff8f8f}.chip.NEUTRAL{background:#4d3d12;color:#ffd479}
.chip.QUALITY{background:#123f22;color:#7fe3a3}.chip.ERROR{background:#333;color:#999}
.chip.flag{background:#1d2735;color:#9fb2c8;font-weight:600}
.findings{margin:.45rem 0 0;padding-left:1.1rem;font-size:.85rem;color:#9fb2c8}
.findings li{margin:.2rem 0}
a{color:#6cb2ff;text-decoration:none}a:hover{text-decoration:underline}
.section{margin-top:1.6rem}
.section h2{font-size:1.05rem;border-bottom:1px solid #2a3240;padding-bottom:.3rem}
.muted{color:#7a8595;font-size:.82rem}
"""


def parse_dossier(path: str) -> dict:
    text = open(path, encoding="utf-8", errors="replace").read()
    d = {"file": os.path.basename(path), "ticker": os.path.basename(path)[:-3],
         "verdict": "ERROR", "headline": "", "red_flags": None, "implication": "",
         "price": None, "score": None, "flags": "", "findings": []}
    m = re.match(r"#\s*(.+?)\s*-\s*\$([\d.]+)\s*\|\s*score=(\d+)", text)
    if m:
        d["company"], d["price"], d["score"] = m.group(1), float(m.group(2)), int(m.group(3))
    fm = re.search(r"swept flags:\s*(.+)", text)
    if fm:
        d["flags"] = fm.group(1).strip()
    vm = re.search(r"## Verdict:\s*(\w+)", text)
    if vm:
        d["verdict"] = vm.group(1)
    hm = re.search(r"## Verdict:\s*\w+\s*\n\*\*(.+?)\*\*", text, re.S)
    if hm:
        d["headline"] = hm.group(1).strip()
    rm = re.search(r"Red flags:\s*(\d+)", text)
    if rm:
        d["red_flags"] = int(rm.group(1))
    im = re.search(r"Trade implication:\s*(.+)", text)
    if im:
        d["implication"] = im.group(1).strip()[:400]
    for fm2 in re.finditer(r"- \*\*\[(\w+)\]\*\*\s*(.+)", text):
        d["findings"].append((fm2.group(1), fm2.group(2)[:230]))
    d["high_sev"] = sum(1 for s, _ in d["findings"] if s.upper() == "HIGH")
    return d


def interestingness(d: dict) -> float:
    """Rank: rare verdicts first, then red flags, then high-severity findings."""
    s = 0.0
    if d["verdict"] == "NEUTRAL":
        s += 100
    elif d["verdict"] == "QUALITY":
        s += 120
    if d["red_flags"]:
        s += min(d["red_flags"], 12) * 2
    s += d.get("high_sev", 0) * 3
    if d["price"] is not None and d["price"] > 5:
        s += 5  # non-shell names with findings are rarer here
    return s


def card(d: dict, max_findings: int = 3) -> str:
    chips = [f'<span class="chip {d["verdict"]}">{d["verdict"]}</span>']
    if d["price"] is not None:
        chips.append(f'<span class="chip flag">${d["price"]:g}</span>')
    if d["red_flags"] is not None:
        chips.append(f'<span class="chip flag">{d["red_flags"]} flags</span>')
    if d.get("high_sev"):
        chips.append(f'<span class="chip flag">{d["high_sev"]} high-sev</span>')
    findings = "".join(
        f"<li><b>[{html.escape(sev)}]</b> {html.escape(txt)}...</li>"
        for sev, txt in d["findings"][:max_findings])
    imp = html.escape(d["implication"]) if d["implication"] else ""
    return (f'<div class="card"><h3><a href="../dossiers/{d["file"]}">{html.escape(d["ticker"])}</a> '
            f'{"".join(chips)}</h3>'
            f'<div class="headline">{html.escape(d["headline"])}</div>'
            + (f'<ul class="findings">{findings}</ul>' if findings else "")
            + (f'<div class="muted" style="margin-top:.4rem">{imp}</div>' if imp else "")
            + "</div>")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", default="dossiers")
    p.add_argument("--out", default="reports/board.html")
    p.add_argument("--watch", nargs="?", const=60.0, default=None, type=float, metavar="SECS")
    a = p.parse_args(argv)

    while True:
        files = sorted(glob.glob(os.path.join(a.dir, "*.md")))
        dossiers = [parse_dossier(f) for f in files]
        dossiers = [d for d in dossiers if d["verdict"] != "ERROR" or d["headline"]]
        for d in dossiers:
            d["rank"] = interestingness(d)
        ranked = sorted(dossiers, key=lambda d: -d["rank"])

        counts = {}
        for d in dossiers:
            counts[d["verdict"]] = counts.get(d["verdict"], 0) + 1
        stats = "".join(f'<div class="stat"><b>{n}</b><span class="muted">{v}</span></div>'
                        for v, n in [("Dossiers", len(dossiers))] +
                        sorted(counts.items(), key=lambda kv: -kv[1]))

        interesting = [d for d in ranked if d["rank"] >= 90][:24]
        rest = [d for d in ranked if d not in interesting]
        top_html = "".join(card(d, max_findings=4) for d in interesting)
        rest_html = "".join(
            f'<div class="card"><h3><a href="../dossiers/{d["file"]}">{html.escape(d["ticker"])}</a> '
            f'<span class="chip {d["verdict"]}">{d["verdict"]}</span>'
            f'<span class="chip flag">{d["red_flags"] if d["red_flags"] is not None else "?"} flags</span>'
            f'<span class="chip flag">${d["price"]:g}</span></div>' for d in rest)

        page = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="90">
<title>alphaHunt forensic board</title><style>{CSS}</style></head><body>
<h1>alphaHunt Forensic Board <span class="muted">auto-refreshes every 90s · {time.strftime('%H:%M:%S')}</span></h1>
<div class="stats">{stats}</div>
<div class="section"><h2>Most interesting ({len(interesting)})</h2>{top_html or '<p class="muted">waiting for dossiers...</p>'}</div>
<div class="section"><h2>Everything else ({len(rest)})</h2>{rest_html}</div>
</body></html>"""
        with open(a.out, "w") as fh:
            fh.write(page)
        if not a.watch:
            print(f"wrote {a.out}: {len(dossiers)} dossiers "
                  f"({counts.get('TROUBLE', 0)} trouble / {counts.get('NEUTRAL', 0)} neutral)")
            return 0
        time.sleep(a.watch)


if __name__ == "__main__":
    raise SystemExit(main())
