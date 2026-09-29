#!/usr/bin/env python3
"""Build reports/satellite_sites.html from results/satellite_sites (committed outputs only).

The page is self-contained: data inline as JSON, stills inline as WebP data URIs,
charts drawn client-side so they follow the viewer's light/dark theme.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
from pathlib import Path

REPO = "https://github.com/David3748/alphaHunt/blob/claude/cool-bohr-oco47t"


def read_csv(path: Path) -> list[dict]:
    with path.open() as handle:
        return list(csv.DictReader(handle))


def data_uri(path: Path) -> str:
    return "data:image/webp;base64," + base64.b64encode(path.read_bytes()).decode()


def build_data(results: Path) -> dict:
    summary = json.loads((results / "summary.json").read_text())
    sites = {}
    for site_id, entry in summary["sites"].items():
        out = {key: entry.get(key) for key in ("name", "kind", "tickers", "aoi_drawn", "events",
                                                 "windows", "delivery", "stalls", "window_stats",
                                                 "zones", "clear_days", "scenes_listed")}
        if entry["kind"] == "data_center":
            rows = read_csv(results / f"construction_{site_id}.csv")
            out["series"] = [[r["date"], float(r["new_built_ha"]), float(r["new_roof_ha"]),
                              float(r["new_cleared_ha"]), r["in_baseline"] == "True"] for r in rows]
        else:
            rows = read_csv(results / f"heat_{site_id}.csv")
            out["series"] = [[r["date"], int(float(r["hot_px20"])),
                              int(float(r["furnace block"])) if "furnace block" in r else None,
                              float(r["cloud_frac"])] for r in rows]
        out["stills"] = [{"date": s["date"], "src": data_uri(results / s["file"])} for s in entry["stills"]]
        sites[site_id] = out
    return {"generated": summary["generated"], "sites": sites}


PAGE = r"""<meta charset="utf-8">
<title>Smelters and Server Halls</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&family=IBM+Plex+Serif:wght@500;600&display=swap">
<style>
:root {
  --ground: #f2f4f6; --paper: #ffffff; --ink: #12151a; --ink-2: #434b57; --muted: #6f7988;
  --rule: #d6dbe2; --grid: #e7eaee; --axis: #bcc3cc;
  --blue: #2a78d6; --blue-wash: rgba(42, 120, 214, 0.10); --orange: #eb6834;
  --band: rgba(18, 21, 26, 0.07); --band-edge: rgba(18, 21, 26, 0.18);
  --tip: #12151a; --tip-ink: #ffffff; --flag: #d03b3b;
  --serif: "IBM Plex Serif", Georgia, "Times New Roman", serif;
  --sans: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --ground: #0d1014; --paper: #151a21; --ink: #e8ecf1; --ink-2: #b2bbc7; --muted: #818b9a;
    --rule: #2a313b; --grid: #20262e; --axis: #3a424d;
    --blue: #3987e5; --blue-wash: rgba(57, 135, 229, 0.14); --orange: #d95926;
    --band: rgba(232, 236, 241, 0.07); --band-edge: rgba(232, 236, 241, 0.2);
    --tip: #e8ecf1; --tip-ink: #12151a; --flag: #e66767;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --ground: #0d1014; --paper: #151a21; --ink: #e8ecf1; --ink-2: #b2bbc7; --muted: #818b9a;
  --rule: #2a313b; --grid: #20262e; --axis: #3a424d;
  --blue: #3987e5; --blue-wash: rgba(57, 135, 229, 0.14); --orange: #d95926;
  --band: rgba(232, 236, 241, 0.07); --band-edge: rgba(232, 236, 241, 0.2);
  --tip: #e8ecf1; --tip-ink: #12151a; --flag: #e66767;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--ground); color: var(--ink); font: 400 16.5px/1.62 var(--sans); -webkit-font-smoothing: antialiased; }
.page { max-width: 1060px; margin: 0 auto; padding-inline: 20px; padding-block: 32px 72px; }
.col { max-width: 690px; }
a { color: var(--blue); text-underline-offset: 2px; }
a:focus-visible, summary:focus-visible, svg:focus-visible { outline: 2px solid var(--blue); outline-offset: 2px; border-radius: 2px; }
p { margin: 0; }
.stack { display: flex; flex-direction: column; gap: 14px; }
.num { font-variant-numeric: tabular-nums; }
.mast { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 24px; padding-bottom: 14px; border-bottom: 1px solid var(--rule);
  font: 500 12px/1.4 var(--mono); letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); }
.hero { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 36px; align-items: start; padding-block: 36px 26px; }
h1 { font: 600 clamp(29px, 4.3vw, 43px)/1.13 var(--serif); letter-spacing: -0.012em; margin: 0 0 18px; text-wrap: balance; }
.lede { font-size: 18px; line-height: 1.58; color: var(--ink-2); }
.byline { margin-top: 16px; font: 400 13px/1.5 var(--mono); color: var(--muted); }
.exhibit { background: var(--paper); border: 1px solid var(--rule); border-radius: 3px; min-width: 0; }
.exhibit-head { display: flex; justify-content: space-between; gap: 6px 12px; flex-wrap: wrap; padding: 10px 16px; border-bottom: 1px solid var(--rule);
  font: 500 11.5px/1.4 var(--mono); letter-spacing: 0.07em; text-transform: uppercase; color: var(--muted); }
.exhibit-body { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
.exhibit-note { font-size: 14px; line-height: 1.55; color: var(--ink-2); }
.ledger { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-top: 1px solid var(--ink); border-bottom: 1px solid var(--rule); }
.ledger div { padding: 14px 16px 14px 0; display: flex; flex-direction: column; gap: 4px; }
.ledger div + div { padding-left: 16px; border-left: 1px solid var(--rule); }
.ledger b { font: 500 26px/1.1 var(--serif); }
.ledger span { font-size: 13.5px; line-height: 1.4; color: var(--ink-2); }
section.item { padding-top: 54px; display: flex; flex-direction: column; gap: 18px; }
.eyebrow { font: 500 12px/1 var(--mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); display: flex; align-items: center; gap: 12px; }
.eyebrow::after { content: ""; flex: 1; height: 1px; background: var(--rule); }
h2 { font: 600 clamp(23px, 3vw, 30px)/1.2 var(--serif); margin: 0; letter-spacing: -0.005em; text-wrap: balance; }
h3 { font: 600 16.5px/1.35 var(--sans); margin: 0; }
.kicker { font-size: 17.5px; line-height: 1.55; color: var(--ink-2); }
.small { font-size: 14px; color: var(--ink-2); line-height: 1.55; }
.table-wrap { overflow-x: auto; border: 1px solid var(--rule); border-radius: 3px; background: var(--paper); }
table { border-collapse: collapse; width: 100%; font-size: 14px; line-height: 1.45; }
th, td { text-align: left; vertical-align: top; padding: 9px 12px; border-bottom: 1px solid var(--rule); }
th { font: 500 11px/1.3 var(--mono); letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); background: var(--paper); }
tr:last-child td { border-bottom: 0; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.verdict td:first-child { font-weight: 600; min-width: 150px; }
.pill { display: inline-block; font: 500 11px/1 var(--mono); letter-spacing: 0.04em; padding: 4px 6px; border-radius: 2px; border: 1px solid var(--rule); color: var(--ink-2); white-space: nowrap; }
.pill.no { border-color: var(--band-edge); }
.pill.weak { border-color: var(--orange); }
.chart { position: relative; width: 100%; }
.chart svg { display: block; width: 100%; height: auto; overflow: visible; }
.chart text { font: 400 10.5px var(--mono); fill: var(--muted); }
.chart .lbl { font: 500 10.5px var(--mono); fill: var(--ink-2); }
.grid-line { stroke: var(--grid); stroke-width: 1; }
.base-line { stroke: var(--axis); stroke-width: 1; }
.band { fill: var(--band); }
.evt { stroke: var(--ink-2); stroke-width: 1; opacity: 0.55; }
.evt-dot { fill: var(--paper); stroke: var(--ink-2); stroke-width: 1; }
.flag-line { stroke: var(--flag); stroke-width: 1.5; }
.line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.area { stroke: none; }
.dot { stroke: var(--paper); stroke-width: 1.5; }
.cross { stroke: var(--ink); stroke-width: 1; opacity: 0.35; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 16px; font: 400 12.5px/1.4 var(--sans); color: var(--ink-2); }
.legend i { display: inline-block; vertical-align: middle; margin-right: 6px; }
.key-line { width: 16px; height: 2px; border-radius: 1px; }
.key-dot { width: 9px; height: 9px; border-radius: 50%; }
.key-band { width: 14px; height: 10px; background: var(--band); border: 1px solid var(--band-edge); }
.key-flag { width: 2px; height: 12px; background: var(--flag); }
ol.events { margin: 0; padding-left: 22px; font-size: 13.5px; line-height: 1.5; color: var(--ink-2); display: grid; gap: 3px; }
ol.events a { color: inherit; }
.tip { position: fixed; z-index: 20; pointer-events: none; background: var(--tip); color: var(--tip-ink); border-radius: 3px; padding: 7px 9px; font: 400 12.5px/1.4 var(--sans); max-width: 260px; box-shadow: 0 2px 10px rgba(0,0,0,0.18); }
.tip b { font: 600 14px/1.3 var(--sans); display: block; }
.tip .d { font: 400 11px/1.3 var(--mono); opacity: 0.75; }
.tip .row { display: flex; align-items: center; gap: 6px; }
.tip .row i { width: 12px; height: 2px; border-radius: 1px; flex: none; }
.strip { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
.strip figure { margin: 0; display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.strip img { width: 100%; height: auto; image-rendering: pixelated; border-radius: 2px; background: var(--grid); }
.strip figcaption { font: 400 11.5px/1.35 var(--mono); color: var(--muted); }
.multiples { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
.panel { background: var(--paper); border: 1px solid var(--rule); border-radius: 3px; padding: 12px 12px 8px; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.panel h3 { font-size: 14.5px; }
.panel .sub { font: 400 11.5px/1.35 var(--mono); color: var(--muted); }
details { font-size: 14px; color: var(--ink-2); }
details summary { cursor: pointer; font: 500 12px/1.6 var(--mono); letter-spacing: 0.04em; color: var(--muted); }
details[open] summary { margin-bottom: 8px; }
.callout { border-left: 3px solid var(--orange); background: var(--paper); padding: 12px 16px; border-radius: 0 3px 3px 0; font-size: 15px; line-height: 1.55; }
.callout.blue { border-left-color: var(--blue); }
ul.plain { margin: 0; padding-left: 20px; display: grid; gap: 8px; }
ol.plan { margin: 0; padding-left: 22px; display: grid; gap: 12px; }
ol.plan li::marker { font: 600 15px var(--serif); color: var(--muted); }
code { font: 400 13px/1.5 var(--mono); background: var(--grid); padding: 1px 4px; border-radius: 2px; }
pre { margin: 0; overflow-x: auto; background: var(--paper); border: 1px solid var(--rule); border-radius: 3px; padding: 12px 14px; font: 400 12.5px/1.6 var(--mono); }
.sources { font-size: 13px; line-height: 1.55; color: var(--ink-2); columns: 2 320px; column-gap: 32px; }
.sources a { color: var(--ink-2); word-break: break-word; }
.sources div { break-inside: avoid; margin-bottom: 6px; }
@media (max-width: 860px) {
  .hero { grid-template-columns: minmax(0, 1fr); gap: 24px; }
  .ledger { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .ledger div:nth-child(3) { padding-left: 0; border-left: 0; }
  .ledger div:nth-child(n+3) { border-top: 1px solid var(--rule); }
}
@media (max-width: 460px) { .multiples { grid-template-columns: minmax(0, 1fr); } }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>

<div class="page">
  <div class="mast"><span>alphaHunt &middot; satellite pilot</span><span>Sentinel-2 L2A, 10 m &middot; data through __LASTDATE__</span></div>
  <p class="small" style="margin-top:16px;padding:14px;border:1px solid var(--rule)"><b>Historical pilot; timing audit added.</b> These charts retain the original retrospective measurements. Some inputs were republished years after acquisition, and the exported composites lack complete scene lineage. They are not certified historical trading signals. The <a href="__REPO__/results/satellite_validation/report.md">follow-up validation</a> adds publication-aware tests, a third satellite source, and comparisons against independent outcomes. Core Scientific had already disclosed construction delays on <a href="https://investors.corescientific.com/sec-filings/all-sec-filings/content/0001628280-25-046272/core-20250930.htm">October 24, 2025</a>, before CoreWeave's November revenue cut.</p>

  <div class="hero">
    <div>
      <h1>Orbit sees the furnaces and the foundations. It does not see the trade.</h1>
      <p class="lede">I pointed free Sentinel-2 imagery at eight AI data-center campuses and three copper smelters, read __SCENES__ scene windows straight from the public archive, and checked every signal against dated company disclosures. Smelter outages show up as cold furnaces. New data halls show up as new roof. Nothing here produced an edge you could trade, and the parts that come closest are already sold to funds.</p>
      <p class="byline">Research note &middot; exploratory, nothing pre-registered &middot; no price data joined (see Method)</p>
    </div>
    <div class="exhibit">
      <div class="exhibit-head"><span>Project Jupiter, Santa Teresa NM</span><span>Oracle / OpenAI Stargate</span></div>
      <div class="exhibit-body">
        <div class="strip" id="hero-strip"></div>
        <p class="exhibit-note">Desert in June 2025; slabs, roofs and equipment yards by August 2026, with new built surface still rising through September. On Sept 24, 2026 Oracle sent the developer a force-majeure notice over gas supply, because the pipeline that feeds the site is now due in February 2027. The problem Oracle cited is off-site, where this box cannot see it.</p>
      </div>
    </div>
  </div>

  <div class="ledger">
    <div><b class="num">__SCENES__</b><span>scene windows read for 11 sites, 1,820 clear enough to use, $0 of data</span></div>
    <div><b>4 of 5</b><span>disclosed smelter outages visible as a cold furnace, plus 2 false alarms</span></div>
    <div><b>1 of 10</b><span>data-center construction pauses of 120+ days that came before a disclosed delay</span></div>
    <div><b>0 of 2</b><span>apparent leads that survived checking: one was the wrong plant, one looks like nine false alarms</span></div>
  </div>

  <section class="item">
    <div class="eyebrow">The verdict</div>
    <h2>What free 10 m imagery can and cannot do for an equity book</h2>
    <div class="table-wrap">
      <table class="verdict">
        <thead><tr><th>Use case</th><th>What orbit shows</th><th>Lead over the company</th><th>Tradeable</th><th>Already sold by</th></tr></thead>
        <tbody>
          <tr><td>Copper smelter on/off</td><td>Furnace heat at 2.2 &micro;m. Cold furnace in 4 of 5 disclosed outages.</td><td>None in the cases tested. The outages were planned or announced.</td><td><span class="pill weak">weak</span> One smelter rarely moves a diversified miner.</td><td>Earth-i SAVANT via Marex: daily status for 90%+ of copper smelters since 2019.</td></tr>
          <tr><td>Hyperscale AI campuses</td><td>Clearing, slabs, roofs and equipment yards, months before &ldquo;ready for service.&rdquo;</td><td>Little. Groundbreaking is announced.</td><td><span class="pill no">no</span> One campus does not move META, MSFT, AMZN or ORCL.</td><td>SemiAnalysis (vision model on satellite imagery); Epoch AI (free, hand-labelled).</td></tr>
          <tr><td>Neocloud and bitcoin-miner conversions</td><td>The same, at sites where one building is a big share of revenue.</td><td>Denton paused for 4.7 months before CoreWeave's Nov 2025 cut. Nine other long pauses led nowhere.</td><td><span class="pill weak">untested</span> Only with each building's guided date attached.</td><td>SemiAnalysis covers the large sites.</td></tr>
          <tr><td>Mine accidents</td><td>Nothing beforehand. Grasberg, Kamoa-Kakula and El Teniente failed underground.</td><td>None.</td><td><span class="pill no">no</span></td><td>InSAR firms for tailings dams and pit walls.</td></tr>
          <tr><td>Power and fuel bottlenecks</td><td>The campus keeps building while the pipeline or substation runs late elsewhere.</td><td>None from the site itself.</td><td><span class="pill no">no</span> You would have to watch the pipeline route.</td><td>Interconnection-queue trackers.</td></tr>
        </tbody>
      </table>
    </div>
    <p class="small col">If &ldquo;mining&rdquo; meant bitcoin miners: Core Scientific, Applied Digital, TeraWulf, Galaxy, IREN, Cipher and Hut 8 are data-center developers now. They are the only tickers in this note where one building's timing moves the stock, and they are covered in the data-center section.</p>
  </section>

  <section class="item" id="smelters">
    <div class="eyebrow">Smelters</div>
    <h2>Furnace heat is a clean on/off switch at two of the three smelters</h2>
    <div class="col stack">
      <p>Sunlit ground reflects less light at 2.2 &micro;m than at 0.86 &micro;m. Molten slag and matte at several hundred degrees emit far more. I flagged a 20 m pixel as hot using the published Landsat 8 active-fire thresholds (Schroeder et al., 2016) mapped to Sentinel-2's bands, plus one guard added mid-pilot after sun glint off a new roof in Gresik passed as heat. Each dot below is one clear scene.</p>
    </div>
    <div class="exhibit">
      <div class="exhibit-head"><span>Hot pixels per clear scene</span><span>Shaded: this smelter's disclosed outages</span></div>
      <div class="exhibit-body" id="heat-charts"></div>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Smelter and outage</th><th class="num">Clear scenes in window</th><th class="num">With heat</th><th class="num">Hot rate, prior year</th><th class="num">Chance if nothing changed</th></tr></thead>
        <tbody id="window-rows"></tbody>
      </table>
    </div>
    <div class="col stack">
      <p><b>Ventanas</b> (Codelco, Chile) shut for good on May 31, 2023. Heat showed in 13 of 28 clear scenes the year before and in 0 of 52 afterwards. The last hot scene was May 18, and the first clear scene after closure, June 17, was cold.</p>
      <p><b>Kennecott</b> (Rio Tinto, Utah) is the harder case: heat shows in only about a quarter to two-fifths of clear scenes when it runs. It still called both outages. The 2023 rebuild produced 22 straight cold scenes from June to August, and heat returned on Sept 9. The 45-day outage in 2025 gave 1 hot scene in 11, and a 23-pixel flare on Nov 2 marked the restart. It also went cold in 11 straight clear scenes from December 2024 to late March 2025, and Rio reported no outage. That is either an undisclosed slowdown or a false alarm; I cannot tell which.</p>
      <p><b>Manyar</b> (Freeport, East Java) is where this almost fooled me. The spot that glowed in 22 of 26 clear scenes during the 2024 commissioning run, and in 8 of 8 during the 2025 restart, went dark in mid-July 2025. The press reported a &ldquo;prolonged Freeport smelter shutdown&rdquo; on Aug 21. That looked like a five-week lead until I checked which smelter the story meant: it was PT Smelting, Freeport's older plant elsewhere in Gresik. Freeport says Manyar started producing cathode that month, and the rest of the complex still showed heat. My box had also clipped the south end of the plant. Without a site plan, a hot-pixel count tells you something changed, not what.</p>
    </div>
    <div class="exhibit">
      <div class="exhibit-head"><span>Manyar, true colour, hot pixels in magenta</span><span>AOI 2.0 &times; 1.65 km</span></div>
      <div class="exhibit-body">
        <div class="strip" id="manyar-strip"></div>
        <p class="exhibit-note">July 2024 commissioning run; two weeks after the October 2024 fire; June 2025 restart; October 2025 after the Grasberg mud rush cut concentrate supply; July 2026 during the halt. The heat moves between units from one regime to the next.</p>
      </div>
    </div>
    <div class="callout">
      <b>Forward check.</b> Freeport says Manyar restarted cathode production in September 2026 at about 5 million pounds and will reach 57 million pounds a month by December. Through Sept 19 the 2024 hot spot has been cold in 40 straight clear scenes, and the whole complex shows a median of 5.5 hot pixels against 18 to 24 in earlier runs. A real ramp should bring it back toward run levels. The monsoon usually blinds this view from December to March (no clear scene between Dec 23, 2024 and Apr 2, 2025), so the first clean read may not come until April 2027.
    </div>
  </section>

  <section class="item" id="datacenters">
    <div class="eyebrow">Data centers</div>
    <h2>You can watch them rise. You cannot see them slip.</h2>
    <div class="col stack">
      <p>For each campus I measured new built surface: bright, spectrally flat, unvegetated pixels that were not built in the same season of the year before construction. Matching the season matters. Texas pasture reads as pavement every winter. Each value uses only scenes up to its own date.</p>
      <p>Denton is the one campus here where a delay moved stocks. Core Scientific is converting it for CoreWeave. On Nov 10, 2025 CoreWeave cut its revenue guide, blaming one developer's late powered shell, and the stock fell 16% the next day. The Wall Street Journal later reported that concrete pours slipped about 60 days over the summer.</p>
    </div>
    <div class="exhibit">
      <div class="exhibit-head"><span>Core Scientific Denton &middot; CORZ, CRWV</span><span>New built surface vs same-season 2023&ndash;24 baseline</span></div>
      <div class="exhibit-body">
        <div id="denton-chart"></div>
        <div class="strip" id="denton-strip"></div>
        <p class="exhibit-note">The retrospective exterior-area index has no material new high from June 9 to Oct 30, 2025. This does not establish that construction stopped: the October 24 company filing reports completed data halls during Q2/Q3 and separately discloses delays. A fixed box around the public address gives a similar series (correlation 0.997); that sensitivity check does not remove hindsight in site selection or certify input publication times.</p>
      </div>
    </div>
    <div class="col stack">
      <p>That looks like a signal until you run the same measurement on the other campuses. Long pauses are routine: shells go up, then work moves indoors for months. Ellendale's new built surface sat flat for 400 days while Building 1 was fitted out, and Building 1 still opened on time. Abilene, Lake Mariner, Hyperion and Colossus all had pauses of 120 days or more with no delay disclosed. Counting only pauses that ended with visible progress, there were 10 of at least 120 days across eight campuses. One came before a disclosed delay.</p>
    </div>
    <div class="multiples" id="multiples"></div>
    <div class="legend" aria-hidden="true"><span><i class="key-line" style="background: var(--blue)"></i>New built surface, ha</span><span><i class="key-band"></i>Pause of 120+ days that ended with new progress</span><span><i class="key-flag"></i>Delay or force majeure disclosed</span></div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Campus</th><th>Pauses of 120+ days (closed)</th><th>Delay disclosed</th></tr></thead>
        <tbody id="stall-rows"></tbody>
      </table>
    </div>
    <div class="col stack">
      <p>Two measurement failures show in those panels. Abilene's later halls look rust-coloured from orbit, much like the red caliche pads they sit on, so the spectral rule counts them as dirt and the series falls after February 2026. Colossus 1 went into an existing Electrolux plant, so its series tracks roof and yard changes rather than construction. A production version needs a trained segmentation model or sharper imagery, which is what SemiAnalysis built.</p>
    </div>
  </section>

  <section class="item" id="broke">
    <div class="eyebrow">What broke</div>
    <h2>Six ways this fools you, found the hard way</h2>
    <ul class="plain col">
      <li><b>Wrong plant.</b> The Manyar &ldquo;lead&rdquo; was a different Freeport smelter. Heat counts need a site plan or sharper imagery before you attribute anything.</li>
      <li><b>Wrong box.</b> The Manyar box, drawn on one image, cut off the south end of the complex. The Denton blind box was fine. Test both on every site.</li>
      <li><b>Glint.</b> Sun reflecting off a new metal roof reads above 100% in the infrared and passed the fire tests until I added a red-band guard. It was a third of Manyar's &ldquo;heat&rdquo;.</li>
      <li><b>Weather.</b> East Java is blind from December to March and North Dakota and New York lose winters to snow. Stalls and outages hide in those gaps.</li>
      <li><b>Hindsight sites.</b> Every campus here was picked because it is famous now. A real test needs sites chosen from permits or filings on the date they became public.</li>
      <li><b>The archive's units.</b> Earth Search's COGs already have ESA's 2022 offset removed but still advertise it in their metadata. Applying it twice made vegetation reflect negative light.</li>
    </ul>
  </section>

  <section class="item" id="next">
    <div class="eyebrow">If you keep going</div>
    <h2>One test worth running, and what not to build</h2>
    <ol class="plan col">
      <li><b>Guided date vs. roof date, for the neocloud names only.</b> Use the existing LLM filing pipeline to extract every building's guided ready-for-service date from 8-Ks, 10-Qs and press releases, with the date each guidance became public. Label the roof-close date for each building from imagery. That is about 25 campuses and 60 buildings across CORZ, APLD, WULF, GLXY, IREN, CIFR, HUT and CRWV. Flag any building whose roof is not closed a typical shell-to-service lag before its guided date, then score flags against later delay disclosures and 20-day returns. Pre-register it and run it forward. Kill it if flags are not clearly more delay-prone than unflagged buildings after two quarters.</li>
      <li><b>Smelter watch as a monitoring tool, not a strategy.</b> Keep the heat detector for names where one smelter matters or disclosure is slow, and add Landsat 8/9 to cut the revisit gap. Earth-i already sells the global index, so the edge is only in specific names and questions.</li>
      <li><b>Do not build</b> a generic capacity tracker (SemiAnalysis sells it), parking-lot or oil-tank counts (sold to funds for a decade), tailings-dam InSAR (specialist work, no track record of beating disclosures) or stockpile volumes (needs stereo imagery).</li>
    </ol>
  </section>

  <section class="item" id="method">
    <div class="eyebrow">Method</div>
    <h2>How the numbers were made</h2>
    <div class="col stack small">
      <p>Level-2A scenes come from the public bucket behind Element 84's Earth Search (<code>s3://sentinel-cogs</code>). The bucket is listed directly by MGRS tile, with no STAC API or account, and each scene reads only a window around the site. A scene counts for construction if at least 90% of the box is clear of cloud, shadow and snow, and for heat if at most 20% is clouded. For construction, a pixel counts if it was flagged in at least 60% of clear scenes in the trailing 75 days, compared with a baseline from the first 12 months matched within 45 days of season. Every observation keeps the scene's publication time for point-in-time use.</p>
      <p>Built surface is visible mean &ge; 0.20, SWIR-1 &le; 1.35 &times; visible and NDVI &lt; 0.20. Roof is built with visible &ge; 0.35. Heat uses the Schroeder et al. OLI fire tests on B8A, B11 and B12, or B11/B12 above 1.0 with B12/B8A above 1.5, and always B12 &ge; 2.5 &times; red. Thresholds were set from spectra, not from event dates. The glint guard was added after seeing false heat and before the final numbers.</p>
      <p>No stock prices are joined. This cloud session could reach AWS open data but not Yahoo, Stooq, SEC or the STAC APIs, so tradeability rests on disclosure dates and reported moves.</p>
    </div>
<pre>pip install rasterio
python src/sentinel2_sites.py fetch       # ~6,400 windowed scene reads, about 30 min
python src/sentinel2_sites.py summarize   # results/satellite_sites/
python src/render_sentinel2_sites_html.py # this page</pre>
    <p class="small">Code: <a href="__REPO__/src/sentinel2_sites.py">src/sentinel2_sites.py</a> &middot; sites and events: <a href="__REPO__/config/satellite_sites.json">config/satellite_sites.json</a> &middot; series: <a href="__REPO__/results/satellite_sites">results/satellite_sites/</a> &middot; earlier scope note: <a href="__REPO__/docs/notes/data_center_satellite_scope.md">data_center_satellite_scope.md</a></p>
  </section>

  <section class="item" id="sources">
    <div class="eyebrow">Sources</div>
    <div class="sources" id="source-list"></div>
  </section>
</div>
<div class="tip" id="tip" hidden></div>

<script>
const DATA = __DATA__;
const EXTRA_SOURCES = [
  ["Schroeder et al. 2016, Landsat-8 active fire algorithm", "https://doi.org/10.1016/j.rse.2015.08.032"],
  ["Earth-i SAVANT Global Copper Smelting Index", "https://earthi.space/press/earth-i-launch-savant-global-copper-smelting-index/"],
  ["SemiAnalysis on 2026 US datacenter capacity", "https://newsletter.semianalysis.com/p/stop-saying-half-of-2026-us-datacenter"],
  ["Epoch AI Frontier Data Centers (CC BY 4.0)", "https://epoch.ai/data/data-centers"],
  ["S&P Global: Gresik outage leaves PT Smelting with unprocessed concentrate", "https://www.spglobal.com/energy/en/news-research/latest-news/metals/082225-gresik-outage-leaves-pt-smelting-with-100000-mt-of-unprocessed-copper-concs"],
  ["Jakarta Post: prolonged Freeport smelter shutdown (Aug 21, 2025)", "https://www.thejakartapost.com/business/2025/08/21/prolonged-freeport-smelter-shutdown-frees-up-scarce-ore-for-copper-market.html"],
  ["Rio Tinto 2025 results: Kennecott smelter outage", "https://www.sec.gov/Archives/edgar/data/863064/000086306426000006/ex1_2025-q4results.htm"],
  ["Element 84 Earth Search", "https://element84.com/earth-search/"]
];

const DAY = 86400000;
const t = s => { const [y, m, d] = s.split("-").map(Number); return Date.UTC(y, m - 1, d); };
const fmt = ms => new Date(ms).toISOString().slice(0, 10);
const svgNS = "http://www.w3.org/2000/svg";
function el(tag, attrs, parent) {
  const node = document.createElementNS(svgNS, tag);
  for (const k in attrs) node.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(node);
  return node;
}
function niceStep(raw) {
  if (!(raw > 0)) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / e;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e;
}
const tip = document.getElementById("tip");
function showTip(x, y, date, rows) {
  tip.replaceChildren();
  rows.forEach((r, i) => {
    if (i === 0) {
      const b = document.createElement("b"); b.textContent = r.value; tip.appendChild(b);
      const row = document.createElement("div"); row.className = "row";
      const key = document.createElement("i"); key.style.background = r.color; row.appendChild(key);
      const s = document.createElement("span"); s.textContent = r.name; row.appendChild(s); tip.appendChild(row);
    } else {
      const row = document.createElement("div"); row.className = "row";
      const key = document.createElement("i"); key.style.background = r.color; row.appendChild(key);
      const s = document.createElement("span"); s.textContent = r.value + " " + r.name; row.appendChild(s); tip.appendChild(row);
    }
  });
  const d = document.createElement("div"); d.className = "d"; d.textContent = date; tip.appendChild(d);
  tip.hidden = false;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let left = x + 14, top = y - h - 10;
  if (left + w > window.innerWidth - 8) left = x - w - 14;
  if (top < 8) top = y + 16;
  tip.style.left = left + "px"; tip.style.top = top + "px";
}
function hideTip() { tip.hidden = true; }

/* One time-series chart: lines or dots, shaded bands, numbered event markers, red flags. */
function chart(container, cfg) {
  const holder = document.createElement("div");
  holder.className = "chart";
  container.appendChild(holder);
  const draw = () => {
    holder.replaceChildren();
    const W = Math.max(260, holder.clientWidth || container.clientWidth || 600);
    const H = cfg.height || 190;
    const m = { l: 34, r: 10, t: cfg.events && cfg.events.length ? 18 : 8, b: 22 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const x = v => m.l + (v - cfg.t0) / (cfg.t1 - cfg.t0) * iw;
    let maxV = 0;
    cfg.series.forEach(s => s.pts.forEach(p => { if (p[1] > maxV) maxV = p[1]; }));
    const step = niceStep((maxV * 1.05 || 1) / 4);
    const yMax = Math.max(step, Math.ceil((maxV * 1.05 || 1) / step) * step);
    const decimals = Math.max(0, -Math.floor(Math.log10(step)));
    const y = v => m.t + ih - (v / yMax) * ih;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": cfg.aria || "", tabindex: "0" }, holder);
    (cfg.bands || []).filter(b => b.outage !== false).forEach(b => {
      const a = Math.max(t(b.from), cfg.t0), z = Math.min(t(b.to), cfg.t1);
      if (z > a) el("rect", { x: x(a), y: m.t, width: Math.max(1, x(z) - x(a)), height: ih, class: "band" }, svg);
    });
    const nTicks = Math.round(yMax / step);
    for (let i = 0; i <= nTicks; i++) {
      const v = step * i, yy = y(v);
      el("line", { x1: m.l, x2: W - m.r, y1: yy, y2: yy, class: i === 0 ? "base-line" : "grid-line" }, svg);
      const label = el("text", { x: m.l - 6, y: yy + 3.5, "text-anchor": "end" }, svg);
      label.textContent = v.toFixed(decimals);
    }
    const y0 = new Date(cfg.t0).getUTCFullYear(), y1 = new Date(cfg.t1).getUTCFullYear();
    for (let yr = y0; yr <= y1 + 1; yr++) {
      [0, 6].forEach(mo => {
        const tt = Date.UTC(yr, mo, 1);
        if (tt < cfg.t0 || tt > cfg.t1) return;
        const xx = x(tt);
        el("line", { x1: xx, x2: xx, y1: m.t + ih, y2: m.t + ih + 4, class: "base-line" }, svg);
        if (mo === 0 || iw > 520) {
          const lab = el("text", { x: xx + 2, y: H - 6 }, svg);
          lab.textContent = mo === 0 ? String(yr) : "Jul";
        }
      });
    }
    (cfg.flags || []).forEach(f => {
      const tt = t(f.date);
      if (tt < cfg.t0 || tt > cfg.t1) return;
      el("line", { x1: x(tt), x2: x(tt), y1: m.t, y2: m.t + ih, class: "flag-line" }, svg);
    });
    (cfg.events || []).forEach((e, i) => {
      const tt = t(e.date);
      if (tt < cfg.t0 || tt > cfg.t1) return;
      el("line", { x1: x(tt), x2: x(tt), y1: m.t, y2: m.t + ih, class: "evt" }, svg);
      el("circle", { cx: x(tt), cy: m.t - 8, r: 7, class: "evt-dot" }, svg);
      const n = el("text", { x: x(tt), y: m.t - 4.5, "text-anchor": "middle", class: "lbl" }, svg);
      n.textContent = String(i + 1);
    });
    cfg.series.forEach(s => {
      if (s.kind === "line") {
        const d = s.pts.map((p, i) => (i ? "L" : "M") + x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1)).join("");
        if (s.area) {
          const a = d + `L${x(s.pts[s.pts.length - 1][0]).toFixed(1)},${y(0)}L${x(s.pts[0][0]).toFixed(1)},${y(0)}Z`;
          el("path", { d: a, class: "area", fill: s.wash || "none" }, svg);
        }
        el("path", { d, class: "line", stroke: s.color }, svg);
        const last = s.pts[s.pts.length - 1];
        if (last) el("circle", { cx: x(last[0]), cy: y(last[1]), r: 4, class: "dot", fill: s.color }, svg);
      } else {
        s.pts.forEach(p => el("circle", { cx: x(p[0]), cy: y(Math.min(p[1], yMax)), r: 4, class: "dot", fill: s.color, "fill-opacity": s.opacity || 1 }, svg));
      }
    });
    const cross = el("line", { x1: 0, x2: 0, y1: m.t, y2: m.t + ih, class: "cross", visibility: "hidden" }, svg);
    const ring = el("circle", { r: 6, fill: "none", stroke: "currentColor", "stroke-width": 2, visibility: "hidden" }, svg);
    ring.style.color = "var(--ink)";
    const primary = cfg.series[0].pts;
    let idx = -1;
    const pick = i => {
      idx = Math.max(0, Math.min(primary.length - 1, i));
      const p = primary[idx];
      const xx = x(p[0]);
      cross.setAttribute("x1", xx); cross.setAttribute("x2", xx); cross.setAttribute("visibility", "visible");
      ring.setAttribute("cx", xx); ring.setAttribute("cy", y(Math.min(p[1], yMax))); ring.setAttribute("visibility", "visible");
      const rows = cfg.series.map(s => {
        const q = s.pts.find(v => v[0] === p[0]);
        return { name: s.name, value: q ? s.fmt(q[1]) : "n/a", color: s.color };
      });
      const box = svg.getBoundingClientRect();
      const note = cfg.note ? cfg.note(p) : "";
      showTip(box.left + xx * box.width / W, box.top + y(Math.min(p[1], yMax)) * box.height / H, fmt(p[0]) + note, rows);
    };
    const nearest = cx => {
      const box = svg.getBoundingClientRect();
      const vx = (cx - box.left) * W / box.width;
      const tt = cfg.t0 + (vx - m.l) / iw * (cfg.t1 - cfg.t0);
      let best = 0, bd = Infinity;
      primary.forEach((p, i) => { const d = Math.abs(p[0] - tt); if (d < bd) { bd = d; best = i; } });
      return best;
    };
    svg.addEventListener("pointermove", ev => pick(nearest(ev.clientX)));
    svg.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); ring.setAttribute("visibility", "hidden"); hideTip(); });
    svg.addEventListener("focus", () => pick(primary.length - 1));
    svg.addEventListener("blur", () => { cross.setAttribute("visibility", "hidden"); ring.setAttribute("visibility", "hidden"); hideTip(); });
    svg.addEventListener("keydown", ev => {
      if (ev.key === "ArrowLeft") { pick(idx - 1); ev.preventDefault(); }
      if (ev.key === "ArrowRight") { pick(idx + 1); ev.preventDefault(); }
    });
  };
  draw();
  if (window.ResizeObserver) {
    let last = holder.clientWidth;
    new ResizeObserver(() => { if (Math.abs(holder.clientWidth - last) > 2) { last = holder.clientWidth; draw(); } }).observe(holder);
  }
}

function eventList(container, events) {
  const ol = document.createElement("ol"); ol.className = "events";
  events.forEach(e => {
    const li = document.createElement("li");
    li.appendChild(document.createTextNode(e.date + ": "));
    const a = document.createElement("a"); a.href = e.source; a.target = "_blank"; a.rel = "noopener"; a.textContent = e.label;
    li.appendChild(a); ol.appendChild(li);
  });
  container.appendChild(ol);
}
function dataTable(container, head, rows) {
  const det = document.createElement("details");
  const sum = document.createElement("summary"); sum.textContent = "Show data (" + rows.length + " rows)"; det.appendChild(sum);
  const wrap = document.createElement("div"); wrap.className = "table-wrap"; wrap.style.maxHeight = "260px"; wrap.style.overflowY = "auto";
  const table = document.createElement("table");
  const tr = document.createElement("tr");
  head.forEach((h, i) => { const th = document.createElement("th"); th.textContent = h; if (i) th.className = "num"; tr.appendChild(th); });
  const thead = document.createElement("thead"); thead.appendChild(tr); table.appendChild(thead);
  const tb = document.createElement("tbody");
  rows.forEach(r => { const row = document.createElement("tr"); r.forEach((c, i) => { const td = document.createElement("td"); td.textContent = c; if (i) td.className = "num"; row.appendChild(td); }); tb.appendChild(row); });
  table.appendChild(tb); wrap.appendChild(table); det.appendChild(wrap); container.appendChild(det);
}
function strip(container, stills, captions) {
  stills.forEach((s, i) => {
    const fig = document.createElement("figure");
    const img = document.createElement("img"); img.src = s.src; img.alt = (captions && captions[i]) || ("Sentinel-2 true colour, " + s.date);
    const cap = document.createElement("figcaption"); cap.textContent = s.date + (captions && captions[i] ? " · " + captions[i] : "");
    fig.appendChild(img); fig.appendChild(cap); container.appendChild(fig);
  });
}

const S = DATA.sites;
const BLUE = "var(--blue)", ORANGE = "var(--orange)";
const ha = v => v.toFixed(1) + " ha";
const px = v => String(v);

/* hero */
strip(document.getElementById("hero-strip"), [S.orcl_jupiter.stills[0], S.orcl_jupiter.stills[S.orcl_jupiter.stills.length - 1]], ["before", "latest clear"]);

/* smelter charts */
const heatHost = document.getElementById("heat-charts");
const heatSpecs = [
  { id: "codelco_ventanas", title: "Codelco Ventanas, Chile", t0: "2022-01-01", t1: "2024-12-31", col: 1 },
  { id: "rio_kennecott", title: "Rio Tinto Kennecott, Utah", t0: "2022-06-01", t1: "2026-09-30", col: 1 },
  { id: "fcx_manyar", title: "Freeport Manyar, East Java: whole complex", t0: "2024-01-01", t1: "2026-09-30", col: 1 },
  { id: "fcx_manyar", title: "Freeport Manyar: the spot that glowed in the 2024 run", t0: "2024-01-01", t1: "2026-09-30", col: 2 }
];
heatSpecs.forEach(spec => {
  const site = S[spec.id];
  const box = document.createElement("div"); box.className = "stack"; box.style.gap = "6px";
  const h = document.createElement("h3"); h.textContent = spec.title; box.appendChild(h);
  heatHost.appendChild(box);
  const pts = site.series.filter(r => r[spec.col] !== null).map(r => [t(r[0]), r[spec.col]]);
  const events = spec.col === 2 ? [] : site.events;
  chart(box, {
    t0: t(spec.t0), t1: t(spec.t1), height: 150,
    series: [{ name: "hot pixels (20 m)", kind: "dots", color: ORANGE, pts, fmt: px }],
    bands: site.windows || [], events,
    aria: spec.title + ": hot pixels per clear scene with outage windows shaded"
  });
  if (events.length) eventList(box, events);
  if (spec.col === 1) dataTable(box, ["date", "hot px", "cloud"], site.series.map(r => [r[0], String(r[1]), r[3].toFixed(2)]));
});

/* window table */
const wrows = document.getElementById("window-rows");
const winList = [
  ["codelco_ventanas", "Ventanas: closed May 31, 2023", S.codelco_ventanas.window_stats[0]],
  ["rio_kennecott", "Kennecott: 2023 rebuild", S.rio_kennecott.window_stats[0]],
  ["rio_kennecott", "Kennecott: 45-day outage, 2025", S.rio_kennecott.window_stats[1]],
  ["fcx_manyar", "Manyar: fire repairs, Oct 2024 to Mar 2025 (2024 hot spot)", S.fcx_manyar.zones["furnace block"].window_stats[0]],
  ["fcx_manyar", "Manyar: halt after Grasberg (2024 hot spot)", S.fcx_manyar.zones["furnace block"].window_stats[2]],
  ["fcx_manyar", "Manyar: mid-July to August 2025 dark spell (the outage was at PT Smelting)", S.fcx_manyar.zones["furnace block"].window_stats[1]]
];
winList.forEach(([id, label, w]) => {
  const tr = document.createElement("tr");
  const p = w.p_value < 0.001 ? "< 0.001" : w.p_value.toFixed(3);
  [label, String(w.clear_scenes), String(w.hot_scenes), Math.round(w.before_hot_rate * 100) + "%", p].forEach((c, i) => {
    const td = document.createElement("td"); td.textContent = c; if (i) td.className = "num"; tr.appendChild(td);
  });
  wrows.appendChild(tr);
});

strip(document.getElementById("manyar-strip"), S.fcx_manyar.stills, ["2024 run", "after the fire", "2025 restart", "after Grasberg", "halt"]);

/* Denton */
const den = S.corz_denton;
const denHost = document.getElementById("denton-chart");
const denStall = (den.stalls.closed_spells_120d || []).map(s => ({ from: s.from, to: s.to }));
chart(denHost, {
  t0: t("2023-06-01"), t1: t("2026-09-30"), height: 210,
  series: [
    { name: "new built surface", kind: "line", color: BLUE, area: true, wash: "var(--blue-wash)", pts: den.series.map(r => [t(r[0]), r[1]]), fmt: ha },
    { name: "of which roof", kind: "line", color: "var(--muted)", pts: den.series.map(r => [t(r[0]), r[2]]), fmt: ha }
  ],
  bands: denStall.slice(0, 1), events: den.events, flags: [],
  aria: "Denton new built surface in hectares, 2023 to 2026, flat from June to October 2025"
});
const denLegend = document.createElement("div"); denLegend.className = "legend";
[["key-line", BLUE, "New built surface, ha"], ["key-line", "var(--muted)", "Of which bright roof, ha"], ["key-band", "", "June 9 to Oct 30, 2025: no new high"]].forEach(([cls, color, text]) => {
  const s = document.createElement("span"); const i = document.createElement("i"); i.className = cls; if (color) i.style.background = color;
  s.appendChild(i); s.appendChild(document.createTextNode(text)); denLegend.appendChild(s);
});
denHost.appendChild(denLegend);
eventList(denHost, den.events);
dataTable(denHost, ["date", "new built ha", "new roof ha", "newly cleared ha"], den.series.filter(r => !r[4]).map(r => [r[0], r[1].toFixed(2), r[2].toFixed(2), r[3].toFixed(2)]));
strip(document.getElementById("denton-strip"), den.stills);

/* small multiples */
const multi = document.getElementById("multiples");
const order = ["corz_denton", "apld_ellendale", "orcl_abilene", "wulf_lake_mariner", "glxy_helios", "meta_hyperion", "xai_colossus1", "orcl_jupiter"];
order.forEach(id => {
  const site = S[id];
  const panel = document.createElement("div"); panel.className = "panel";
  const h = document.createElement("h3"); h.textContent = site.name.replace(/ \(.*\)$/, "");
  const sub = document.createElement("div"); sub.className = "sub";
  sub.textContent = (site.tickers.length ? site.tickers.join(", ") + " · " : "") + "peak " + (site.stalls.peak_ha || 0).toFixed(1) + " ha";
  panel.appendChild(h); panel.appendChild(sub); multi.appendChild(panel);
  const flags = site.delivery && site.delivery.delay_disclosed ? [{ date: site.delivery.delay_disclosed }] : [];
  chart(panel, {
    t0: t("2023-06-01"), t1: t("2026-09-30"), height: 120,
    series: [{ name: "new built surface", kind: "line", color: BLUE, area: true, wash: "var(--blue-wash)", pts: site.series.map(r => [t(r[0]), r[1]]), fmt: ha }],
    bands: (site.stalls.closed_spells_120d || []).map(s => ({ from: s.from, to: s.to })), flags,
    aria: site.name + ": new built surface in hectares"
  });
});

/* stall table */
const srows = document.getElementById("stall-rows");
order.forEach(id => {
  const site = S[id];
  const tr = document.createElement("tr");
  const spells = (site.stalls.closed_spells_120d || []).map(s => `${s.from} to ${s.to} (${s.days} d)`).join("; ") || "none";
  const delay = site.delivery.delay_disclosed ? site.delivery.delay_disclosed + ": " + site.delivery.note : site.delivery.note;
  [site.name.replace(/ \(.*\)$/, ""), spells, delay].forEach(c => { const td = document.createElement("td"); td.textContent = c; tr.appendChild(td); });
  srows.appendChild(tr);
});

/* sources */
const seen = new Set(), list = document.getElementById("source-list");
const addSource = (label, href) => {
  if (!href || seen.has(href)) return; seen.add(href);
  const d = document.createElement("div"); const a = document.createElement("a");
  a.href = href; a.target = "_blank"; a.rel = "noopener"; a.textContent = label; d.appendChild(a); list.appendChild(d);
};
Object.values(S).forEach(site => (site.events || []).forEach(e => addSource(site.name.replace(/ \(.*\)$/, "") + ": " + e.label, e.source)));
EXTRA_SOURCES.forEach(([l, h]) => addSource(l, h));
</script>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=Path("results/satellite_sites"))
    parser.add_argument("--output", type=Path, default=Path("reports/satellite_sites.html"))
    args = parser.parse_args()
    data = build_data(args.results)
    scenes = sum(site["scenes_listed"] for site in data["sites"].values())
    last = max(row[0] for site in data["sites"].values() for row in site["series"])
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = (PAGE.replace("__DATA__", payload).replace("__SCENES__", f"{scenes:,}")
            .replace("__LASTDATE__", last).replace("__REPO__", REPO))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html)
    print(args.output, f"{len(html) / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
