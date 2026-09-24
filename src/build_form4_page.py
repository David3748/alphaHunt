#!/usr/bin/env python3
"""build_form4_page.py (v2) — Form 4 insider conviction page.
NAV-vs-SPY equity curve (log scale, shaded excess), stat cards, clickable year
drill-downs, expandable trade ledger with LLM-extracted evidence."""

import datetime as dt
import json
import math
import statistics
import sys
import bisect
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import ox_lab
import long_lab
from backtest_signals import parse_date, forward_excess, fetch_all_prices, START, END

RUN = ROOT / "lab_runs" / "unstructured_proto"
HORIZON = 90
MAX_POS = 20


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def sim_nav(events, prices, spy_rows):
    """Long-only overlapping-cohort book of `events`, equal weight, MAX_POS slots."""
    cal = [r["date"] for r in spy_rows]
    spy_close = {r["date"]: r["close"] for r in spy_rows}
    closes = {t: sorted(rows, key=lambda r: r["date"]) for t, rows in prices.items()}

    def close_ob(t, day):
        import bisect
        rows = closes[t]
        ds = [r["date"] for r in rows]
        i = bisect.bisect_right(ds, day)
        return rows[i - 1]["close"] if i else None

    entries = []
    for e in events:
        i = bisect.bisect_left(cal, e["d"])
        if i < len(cal) - HORIZON - 1 and e["ticker"] in closes:
            entries.append((i, e))
    entries.sort(key=lambda x: x[0])

    active = []
    nav = 1.0
    series = []
    ei = 0
    for day_i, day in enumerate(cal):
        while ei < len(entries) and entries[ei][0] == day_i:
            if len(active) < MAX_POS:
                _, e = entries[ei]
                c0 = close_ob(e["ticker"], day)
                if c0 and c0 > 0:
                    active.append({"e": e, "last": c0, "exit": day_i + HORIZON})
            ei += 1
        rets = []
        expired = []
        for pos in active:
            if day_i >= pos["exit"]:
                expired.append(pos)
                continue
            c = close_ob(pos["e"]["ticker"], day)
            if not c or c <= 0 or pos["last"] <= 0:
                continue
            rets.append(c / pos["last"] - 1.0)
            pos["last"] = c
        for p in expired:
            active.remove(p)
        if day not in spy_close:
            continue
        w = 1.0 / len(active) if active else 0.0
        nav *= (1.0 + sum(w * r for r in rets))
        series.append({"date": day.isoformat(), "nav": round(nav, 5),
                       "spy": round(spy_close[day] / spy_close[cal[0]], 5),
                       "pos": len(active)})
    return series


def main():
    rows = ox_lab.load_jsonl(RUN / "form4_extracted.jsonl")
    tickers = {r.get("ticker") for r in rows if r.get("ticker")}
    prices = fetch_all_prices(tickers, RUN)
    spy = prices.get("SPY", [])

    events = []
    for r in rows:
        t = r.get("ticker")
        d = parse_date(r.get("file_date"))
        if not t or not d or t not in prices or not prices[t]:
            continue
        ex = forward_excess(prices[t], spy, d, HORIZON)
        if ex is None:
            continue
        events.append({
            "ticker": t, "date": d.isoformat(), "excess": round(ex * 100, 2),
            "dir": r.get("dominant_direction") or "none",
            "disc": bool(r.get("is_discretionary")),
            "b51": bool(r.get("is_10b5_1_scheduled")),
            "role": r.get("officer_role") or "unknown",
            "evidence": (r.get("key_evidence") or "")[:400],
            "value": r.get("aggregate_value") or 0,
        })

    buys = [e for e in events if e["dir"] == "open_market_buy"]
    disc_buys = [e for e in buys if e["disc"]]
    disc_sells = [e for e in events if e["disc"] and e["dir"] == "open_market_sell"]

    def mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    # ---- NAV simulation over discretionary buys ----
    ev_objs = [{"ticker": e["ticker"], "d": parse_date(e["date"])} for e in disc_buys]
    series = sim_nav(ev_objs, prices, spy)

    navs = [s["nav"] for s in series]
    spys = [s["spy"] for s in series]
    years = len(navs) / 252.0
    total = navs[-1] if navs else 1.0
    cagr = total ** (1 / years) - 1 if years > 0 and total > 0 else 0
    vol = statistics.stdev(navs_diff := [b / a - 1 for a, b in zip(navs, navs[1:])]) * math.sqrt(252) if len(navs) > 2 else 0
    sharpe = (cagr) / vol if vol > 0 else 0
    peak, maxdd = 1.0, 0.0
    for v in navs:
        peak = max(peak, v)
        maxdd = min(maxdd, v / peak - 1)
    spy_cagr = (spys[-1]) ** (1 / years) - 1 if years > 0 else 0

    # ---- SVG equity curve, log scale ----
    W, H = 960, 380
    pad_l, pad_r, pad_t, pad_b = 46, 10, 12, 26
    n = len(series)
    lo = min(min(navs), min(spys)) * 0.97
    hi = max(max(navs), max(spys)) * 1.03
    llo, lhi = math.log(lo), math.log(hi)

    def xy(i, v):
        x = pad_l + i / max(1, n - 1) * (W - pad_l - pad_r)
        y = pad_t + (1 - (math.log(v) - llo) / (lhi - llo)) * (H - pad_t - pad_b)
        return f"{x:.1f},{y:.1f}"

    nav_pts = " ".join(xy(i, s["nav"]) for i, s in enumerate(series))
    spy_pts = " ".join(xy(i, s["spy"]) for i, s in enumerate(series))
    # shaded excess band between curves
    band = []
    for i, s in enumerate(series):
        band.append(xy(i, max(s["nav"], s["spy"])))
    for i in range(n - 1, -1, -1):
        band.append(xy(i, min(series[i]["nav"], series[i]["spy"])))
    band_pts = " ".join(band)
    grid = []
    ticks = [0.25, 0.5, 1.0, 2.0, 4.0]
    for tv in ticks:
        if lo <= tv <= hi:
            y = pad_t + (1 - (math.log(tv) - llo) / (lhi - llo)) * (H - pad_t - pad_b)
            grid.append(f'<line x1="{pad_l}" y1="{y:.0f}" x2="{W-pad_r}" y2="{y:.0f}" stroke="#232b3d"/>')
            grid.append(f'<text x="{pad_l-6}" y="{y+4:.0f}" text-anchor="end" fill="#5c6878" font-size="10">{tv}x</text>')
    for i in range(0, n, max(1, n // 8)):
        grid.append(f'<text x="{pad_l + i/(n-1)*(W-pad_l-pad_r):.0f}" y="{H-6}" fill="#5c6878" font-size="10">{series[i]["date"][:7]}</text>')
    curve_svg = (f'<svg viewBox="0 0 {W} {H}" style="width:100%">' + "".join(grid)
                 + f'<polygon points="{band_pts}" fill="#4a9eff" opacity="0.12"/>'
                 + f'<polyline points="{spy_pts}" fill="none" stroke="#7c8899" stroke-width="1.4" stroke-dasharray="4 3"/>'
                 + f'<polyline points="{nav_pts}" fill="none" stroke="#3fb96f" stroke-width="2"/>'
                 + "</svg>")

    # yearly entry bars (clickable)
    yr_counts = Counter(e["date"][:4] for e in disc_buys)
    yr_perf = {}
    for y in sorted(yr_counts):
        xs = [e["excess"] for e in disc_buys if e["date"].startswith(y)]
        yr_perf[y] = {"n": len(xs), "mean": round(mean(xs), 1)}
    yr_html = ""
    for y in sorted(yr_counts):
        c = yr_counts[y]
        active_attr = 'class="yrbtn active"' if y == sorted(yr_counts)[0] else 'class="yrbtn"'
        yr_html += (f'<button {active_attr} data-year="{y}" onclick="pickYear(\'{y}\')" '
                    f'style="background:#141926;border:1px solid #232b3d;border-radius:8px;color:#dbe4f0;'
                    f'padding:8px 14px;cursor:pointer;font-size:13px"><b>{y}</b><br>'
                    f'<span style="color:#7c8899;font-size:11px">n={c} · avg {yr_perf[y]["mean"]:+.1f}%</span></button>')
    yr_html += '<button class="yrbtn" data-year="all" onclick="pickYear(\'all\')" style="background:#141926;border:1px solid #232b3d;border-radius:8px;color:#dbe4f0;padding:8px 14px;cursor:pointer;font-size:13px"><b>all</b></button>'

    # ledger rows
    ledger_rows = ""
    for e in sorted(disc_buys, key=lambda x: (-x["excess"])):
        col = "#3fb96f" if e["excess"] >= 0 else "#e05561"
        ledger_rows += f"""<tr class="lrow" onclick="toggleRow(this)" style="cursor:pointer">
<td><b>{esc(e['ticker'])}</b></td><td>{e['date']}</td><td>{esc(e['role'])}</td>
<td class="num" style="color:{col}">{e['excess']:+.1f}%</td></tr>
<tr class="detail" style="display:none"><td colspan="4" style="background:#0f1420;color:#9fb0c5;font-size:12px;padding:10px 18px">
<b>Evidence:</b> {esc(e['evidence']) or '—'}</td></tr>"""

    sells_rows = "".join(
        f"<tr><td>{esc(e['ticker'])}</td><td>{e['date']}</td><td>{esc(e['role'])}</td>"
        f"<td class='num' style='color:{'#3fb96f' if e['excess']>=0 else '#e05561'}'>{e['excess']:+.1f}%</td></tr>"
        for e in sorted(disc_sells, key=lambda x: -x["excess"])[:20])

    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>Form 4 Insider Conviction</title>
<style>
:root{{--bg:#0b0e14;--card:#141926;--line:#232b3d;--fg:#dbe4f0;--dim:#7c8899}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,'Segoe UI',Roboto,sans-serif}}
.wrap{{max-width:1060px;margin:0 auto;padding:26px}}
h1{{font-size:20px;margin:0 0 2px}} h2{{font-size:13px;color:var(--dim);text-transform:uppercase;letter-spacing:.08em;margin:28px 0 10px}}
.sub{{color:var(--dim);font-size:12px;margin-bottom:20px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}}
.card{{flex:1 1 140px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 15px}}
.card .n{{font-size:24px;font-weight:700}} .card .t{{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.06em}}
.box{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:10px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th,td{{padding:7px 12px;border-bottom:1px solid var(--line);font-size:13px;text-align:left}}
th{{color:var(--dim);font-size:11px;text-transform:uppercase}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.years{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}}
.note{{color:var(--dim);font-size:12px;margin-top:6px}}
.legend span{{margin-right:16px;font-size:12px;color:var(--dim)}}
.dot{{display:inline-block;width:10px;height:3px;vertical-align:middle;margin-right:5px}}
</style></head><body><div class="wrap">
<h1>Form 4 — insider conviction book</h1>
<div class="sub">Discretionary open-market BUY filings (LLM-classified as non-10b5-1) · overlapping-cohort portfolio · 90-trading-day holds · 20 slots equal-weight · long-only · vs SPY</div>

<div class="cards">
<div class="card"><div class="n">{(total-1)*100:+.0f}%</div><div class="t">total return</div></div>
<div class="card"><div class="n">{cagr*100:+.1f}%</div><div class="t">CAGR (vs SPY {spy_cagr*100:+.1f}%)</div></div>
<div class="card"><div class="n">{sharpe:.2f}</div><div class="t">Sharpe (rf=0)</div></div>
<div class="card"><div class="n">{maxdd*100:.0f}%</div><div class="t">max drawdown</div></div>
<div class="card"><div class="n">{len(disc_buys)}</div><div class="t">events</div></div>
</div>

<h2>Growth of $1 — vs SPY (log scale)</h2>
<div class="box">{curve_svg}
<div class="legend"><span><span class="dot" style="background:#3fb96f"></span>insider conviction book</span><span><span class="dot" style="background:#7c8899"></span>SPY</span><span>shaded = active excess</span></div></div>

<h2>Entries by year — click to filter ledger</h2>
<div class="years">{yr_html}</div>

<h2>Ledger — click a row for evidence</h2>
<table id="ledger"><thead><tr><th>ticker</th><th>filed</th><th>role</th><th class="num">90d excess vs SPY</th></tr></thead>
<tbody>{ledger_rows}</tbody></table>

<h2>For contrast — discretionary SELLs do not predict declines</h2>
<div class="box"><table style="border:none"><thead><tr><th>ticker</th><th>filed</th><th>role</th><th class="num">90d excess</th></tr></thead>
<tbody>{sells_rows}</tbody></table>
<div class="note">Insiders selling into strength were followed by continued drift ({mean([e['excess'] for e in disc_sells]):+.1f}% mean) — momentum, not contrarian signal.</div></div>

<h2>Caveats</h2>
<div class="box"><p style="margin:0">2025–26 sample only ({len(disc_buys)} events) — magnitudes are directional until scaled to full Form 4 history. Yahoo pricing (survivorship). Long-only so no borrow effects. rf=0 Sharpe. Sparse positions early in the window make NAV jumpy.</p></div>
</div>
<script>
function toggleRow(tr){{
  const d = tr.nextElementSibling;
  d.style.display = d.style.display === 'none' ? '' : 'none';
}}
function pickYear(y){{ {{
  const rows = document.querySelectorAll('#ledger .lrow');
  let shown = 0;
  rows.forEach(r => {{
    const dateCell = r.cells[1].textContent;
    const show = (y === 'all') || dateCell.startsWith(y);
    r.style.display = show ? '' : 'none';
    if(show) shown++;
    const det = r.nextElementSibling;
    if(det) {{ det.style.display = 'none'; }}
  }});
}} }}
</script>
</body></html>"""

    out = RUN / "form4.html"
    out.write_text(html)
    print(f"wrote {out} | events={len(disc_buys)} nav_points={len(series)}")


if __name__ == "__main__":
    main()