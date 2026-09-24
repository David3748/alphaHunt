#!/usr/bin/env python3
"""Render the GHSL cross-country REIT experiment as a standalone HTML report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


COLORS = {
    "major_city_reit_specific": "#c65745",
    "major_city_demand_minus_supply_reit_specific": "#476b86",
    "major_city_growth_demand_blend_reit_specific": "#755a7d",
    "major_city_local_reit": "#69796a",
    "major_city_local_country_equity": "#b88b43",
    "major_city_usd_reit": "#7b688b",
    "satellite_etf_core": "#476b86",
    "satellite_urban_growth": "#d48a78",
    "satellite_supply_constraint": "#476b86",
    "satellite_country_equity": "#b88b43",
    "satellite_reit_minus_equity": "#77678b",
    "equal_weight_reits": "#69796a",
}
LABELS = {
    "major_city_reit_specific": "Major-city REIT residual",
    "major_city_demand_minus_supply_reit_specific": "Demand minus supply residual",
    "major_city_growth_demand_blend_reit_specific": "Growth + demand/supply blend",
    "major_city_local_reit": "Currency-hedged REIT tilt",
    "major_city_local_country_equity": "Local country-equity control",
    "major_city_usd_reit": "USD-unhedged major-city tilt",
    "satellite_etf_core": "ETF-only satellite core",
    "satellite_urban_growth": "Urban-growth tilt",
    "satellite_supply_constraint": "Constrained-supply thesis",
    "satellite_country_equity": "Same signal on country equities",
    "satellite_reit_minus_equity": "REIT-specific residual",
    "equal_weight_reits": "Equal-weight REIT basket",
}
CHART_DIR = Path(".")


def style_axis(ax, percent=False):
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#cbc5b9")
    ax.grid(axis="y", color="#ddd8cd", linewidth=.7, alpha=.85)
    ax.tick_params(colors="#596068", labelsize=8)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    if percent:
        ax.yaxis.set_major_formatter(lambda value, _: f"{value:.0%}")


def save(fig, name, alt):
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHART_DIR / name, dpi=150, bbox_inches="tight", transparent=True)
    plt.close(fig)
    return f'<img class="chart" src="html-assets/{name}" alt="{alt}">'


def cumulative_chart(returns):
    columns = ["major_city_growth_demand_blend_reit_specific",
               "major_city_reit_specific",
               "major_city_demand_minus_supply_reit_specific",
               "major_city_local_country_equity"]
    growth = (1 + returns[columns]).cumprod() - 1
    fig, ax = plt.subplots(figsize=(8.6, 3.8))
    for column in columns:
        ax.plot(growth.index, growth[column], color=COLORS[column], linewidth=1.55, label=LABELS[column])
    style_axis(ax, True); ax.axhline(0, color="#84847f", linewidth=.8)
    ax.set_ylabel("Cumulative return", fontsize=8); ax.legend(frameon=False, fontsize=8, ncol=2)
    return save(fig, "cumulative-return.png", "Cumulative returns of satellite REIT strategies and equal weight REIT basket")


def rolling_sharpe_chart(returns):
    columns = ["major_city_growth_demand_blend_reit_specific",
               "major_city_reit_specific",
               "major_city_demand_minus_supply_reit_specific",
               "major_city_local_country_equity"]
    rolling = returns[columns].rolling(12, min_periods=12).mean() / returns[columns].rolling(12, min_periods=12).std() * np.sqrt(12)
    fig, ax = plt.subplots(figsize=(8.6, 3.8))
    for column in columns:
        ax.plot(rolling.index, rolling[column], color=COLORS[column], linewidth=1.35, label=LABELS[column])
    style_axis(ax); ax.axhline(0, color="#84847f", linewidth=.8)
    finite = rolling.to_numpy()[np.isfinite(rolling.to_numpy())]
    if finite.size:
        ax.set_ylim(max(-4, np.percentile(finite, 2)), min(4, np.percentile(finite, 98)))
    ax.set_ylabel("Trailing 12-month Sharpe", fontsize=8); ax.legend(frameon=False, fontsize=8, ncol=2)
    return save(fig, "rolling-sharpe.png", "Rolling twelve month Sharpe ratios")


def drawdown_chart(returns):
    columns = ["major_city_growth_demand_blend_reit_specific",
               "major_city_reit_specific", "major_city_demand_minus_supply_reit_specific"]
    wealth = (1 + returns[columns]).cumprod()
    drawdown = wealth / wealth.cummax() - 1
    fig, ax = plt.subplots(figsize=(8.6, 3.8))
    for column in columns:
        ax.plot(drawdown.index, drawdown[column], color=COLORS[column], linewidth=1.35, label=LABELS[column])
    style_axis(ax, True); ax.set_ylabel("Drawdown", fontsize=8); ax.legend(frameon=False, fontsize=8)
    return save(fig, "drawdown.png", "Drawdowns for the satellite REIT strategies")


def annual_chart(returns):
    columns = ["major_city_growth_demand_blend_reit_specific", "major_city_reit_specific",
               "major_city_demand_minus_supply_reit_specific"]
    annual = returns[columns].groupby(returns.index.year).sum()
    x = np.arange(len(annual)); fig, ax = plt.subplots(figsize=(8.6, 3.8))
    ax.bar(x - .25, annual[columns[0]], .25, color=COLORS[columns[0]], label=LABELS[columns[0]])
    ax.bar(x, annual[columns[1]], .25, color=COLORS[columns[1]], label=LABELS[columns[1]])
    ax.bar(x + .25, annual[columns[2]], .25, color=COLORS[columns[2]], label=LABELS[columns[2]])
    ax.set_xticks(x, annual.index.astype(str), rotation=45, ha="right", fontsize=8)
    ax.yaxis.set_major_formatter(lambda value, _: f"{value:.0%}")
    ax.spines[["top", "right"]].set_visible(False); ax.grid(axis="y", color="#ddd8cd", linewidth=.7)
    ax.set_axisbelow(True); ax.set_ylabel("Calendar-year return", fontsize=8); ax.legend(frameon=False, fontsize=8)
    return save(fig, "calendar-return.png", "Calendar year long short returns")


def supply_chart(supply, universe):
    supply = supply[supply["Year"].between(2000, 2020)].copy()
    fig, ax = plt.subplots(figsize=(8.6, 3.8))
    palette = plt.cm.tab10(np.linspace(0, .8, len(universe)))
    for (ticker, meta), color in zip(universe.items(), palette):
        rows = supply[supply["ticker"].eq(ticker)]
        ax.plot(pd.to_datetime(rows["Year"].astype(str) + "-12-31"), rows["built_up_5y_cagr"],
                marker="o", markersize=3, linewidth=1.2, color=color, label=meta["label"])
    style_axis(ax, True); ax.set_ylabel("Five-year built-up CAGR", fontsize=8)
    ax.legend(frameon=False, fontsize=7.5, ncol=4)
    return save(fig, "satellite-built-growth.png", "GHSL built-up surface growth by country")


def position_heatmap(positions, universe):
    p = positions["major_city_growth_demand_blend_reit_specific"].copy()
    p = p.resample("YE").last().loc["2011":]
    fig, ax = plt.subplots(figsize=(8.6, 3.4))
    image = ax.imshow(p.T, aspect="auto", cmap="RdBu_r", vmin=-p.abs().max().max(), vmax=p.abs().max().max())
    ax.set_yticks(range(len(p.columns)), [universe[t]["label"] for t in p.columns], fontsize=8)
    ax.set_xticks(range(len(p.index)), p.index.year, rotation=45, ha="right", fontsize=8)
    ax.set_xlabel("Position held at year-end", fontsize=8); ax.tick_params(length=0)
    bar = fig.colorbar(image, ax=ax, fraction=.025, pad=.02); bar.set_label("Portfolio weight", fontsize=8)
    return save(fig, "position-heatmap.png", "Annual positions in the urban growth REIT strategy")


def robustness_chart(robustness, universe):
    values = robustness["leave_one_market_out"]
    blend_values = robustness["demand_blend_leave_one_market_out"]
    labels = [universe[ticker]["label"] for ticker in values]
    sharpes = [item["sharpe"] for item in values.values()]
    blend_sharpes = [blend_values[ticker]["sharpe"] for ticker in values]
    fig, ax = plt.subplots(figsize=(8.6, 3.4))
    y = np.arange(len(labels)); ax.barh(y - .18, sharpes, height=.34, color="#c65745", label="Construction growth")
    ax.barh(y + .18, blend_sharpes, height=.34, color="#755a7d", label="Growth + demand/supply")
    ax.set_yticks(y, labels, fontsize=8); ax.invert_yaxis(); ax.set_xlabel("Full-period Sharpe after omission", fontsize=8)
    ax.axvline(0, color="#84847f", linewidth=.8); ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", color="#ddd8cd", linewidth=.7); ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8, loc="lower center", bbox_to_anchor=(.5, 1.01), ncol=2)
    return save(fig, "leave-one-out.png", "Leave one market out Sharpe ratios")


def epoch_chart(robustness):
    values = robustness["satellite_epoch_windows"]
    labels = ["2005 epoch\n2011", "2010 epoch\n2012–16",
              "2015 epoch\n2017–21", "2020 epoch\n2022–26"]
    sharpes = [item["sharpe"] for item in values.values()]
    fig, ax = plt.subplots(figsize=(8.6, 3.4))
    x = np.arange(len(sharpes)); ax.bar(x, sharpes, color="#c65745", width=.58)
    ax.set_xticks(x, labels, fontsize=8); ax.set_ylabel("Sharpe within holding regime", fontsize=8)
    ax.spines[["top", "right"]].set_visible(False); ax.grid(axis="y", color="#ddd8cd", linewidth=.7); ax.set_axisbelow(True)
    for row, value in enumerate(sharpes): ax.text(row, value + .025, f"{value:.2f}", ha="center", fontsize=8)
    return save(fig, "epoch-stability.png", "Sharpe ratios within each satellite holding regime")


def metric_rows(metrics, sample):
    order = ["major_city_growth_demand_blend_reit_specific",
             "major_city_demand_minus_supply_reit_specific",
             "major_city_reit_specific", "major_city_local_reit",
             "major_city_local_country_equity", "major_city_usd_reit",
             "major_city_equal_tail_reit_specific", "major_city_winner_loser_reit_specific",
             "satellite_etf_core", "satellite_urban_growth", "satellite_supply_constraint",
             "satellite_country_equity", "satellite_reit_minus_equity", "relative_value", "momentum",
             "urban_growth_plus_value", "three_sleeve_experiment", "equal_weight_reits"]
    names = {**LABELS, "relative_value": "Relative-price mean reversion", "momentum": "12-month momentum",
             "major_city_equal_tail_reit_specific": "Major-city residual: equal tails",
             "major_city_winner_loser_reit_specific": "Major-city residual: winner/loser",
             "urban_growth_plus_value": "Growth + relative value",
             "three_sleeve_experiment": "Three-sleeve experiment"}
    rows = []
    for strategy in order:
        value = metrics[strategy][sample]
        rows.append(f'<tr><td>{names[strategy]}</td><td>{value["start"][:4]}</td><td>{value["annual_return_pct"]:.2f}%</td>'
                    f'<td>{value["annual_vol_pct"]:.2f}%</td><td>{value["sharpe"]:.3f}</td>'
                    f'<td>{value["max_drawdown_pct"]:.2f}%</td><td>{value["avg_monthly_turnover"]:.3f}</td>'
                    f'<td>{value["hac_mean_tstat"]:.3f}</td></tr>')
    return "\n".join(rows)


def main():
    global CHART_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("research/satellite_reit_alpha"))
    parser.add_argument("--output", type=Path, default=Path("research/satellite_reit_alpha/satellite-reit-report.html"))
    args = parser.parse_args(); CHART_DIR = args.output.parent / "html-assets"
    metrics = json.loads((args.input / "metrics.json").read_text())
    manifest = json.loads((args.input / "manifest.json").read_text())
    robustness = json.loads((args.input / "robustness.json").read_text())
    returns = pd.read_parquet(args.input / "strategy_returns.parquet").loc[manifest["settings"]["satellite_evaluation_start"]:]
    positions = pd.read_parquet(args.input / "positions.parquet")
    supply = pd.read_csv(args.input / "major_city_satellite_supply.csv")
    universe = manifest["universe"]
    core = metrics["major_city_reit_specific"]["full"]
    demand_supply = metrics["major_city_demand_minus_supply_reit_specific"]["full"]
    candidate = metrics["major_city_growth_demand_blend_reit_specific"]["full"]
    local_reit = metrics["major_city_local_reit"]["full"]
    local_equity = metrics["major_city_local_country_equity"]["full"]
    broad = metrics["satellite_urban_growth"]["full"]
    supply_thesis = metrics["satellite_supply_constraint"]["full"]
    recent = metrics["major_city_growth_demand_blend_reit_specific"]["validation"]
    equity = metrics["satellite_country_equity"]["full"]
    residual = metrics["satellite_reit_minus_equity"]["full"]
    charts = {
        "cum": cumulative_chart(returns), "sharpe": rolling_sharpe_chart(returns),
        "dd": drawdown_chart(returns), "annual": annual_chart(returns),
        "supply": supply_chart(supply, {ticker: universe[ticker] for ticker in manifest["settings"]["core_universe"]}),
        "positions": position_heatmap(positions, universe),
        "loo": robustness_chart(robustness, universe), "epoch": epoch_chart(robustness),
    }
    instrument_rows = "".join(
        f'<tr><td>{meta["label"]}</td><td>{ticker}</td><td>{meta["kind"]}</td><td>{meta["country"]}</td></tr>'
        for ticker, meta in universe.items()
    )
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Satellite REIT Model — Backtest Report</title><style>
:root{{--ink:#17232c;--muted:#687078;--paper:#f4f1ea;--panel:#fffdf8;--line:#d8d2c7;--red:#c65745;--blue:#476b86;--green:#69796a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}}a{{color:#365e78}}
.shell{{max-width:1240px;margin:auto;padding:54px 28px 80px}}.eyebrow{{font-size:11px;font-weight:750;letter-spacing:.16em;text-transform:uppercase;color:var(--red)}}
h1{{font:500 clamp(38px,6vw,72px)/1 Georgia,serif;letter-spacing:-.035em;margin:12px 0 18px}}.dek{{font-size:18px;color:var(--muted);max-width:900px}}.rule{{height:1px;background:var(--line);margin:35px 0}}
.warning{{background:#fff3dc;border:1px solid #dfc695;padding:16px 18px;margin:28px 0}}.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}}.kpi{{background:var(--panel);padding:18px}}.kpi span{{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.08em}}.kpi strong{{font:500 28px/1.2 Georgia,serif}}
h2{{font:500 31px/1.2 Georgia,serif;margin:48px 0 9px}}h3{{font-size:15px;margin:0 0 7px}}.note,.fine{{color:var(--muted)}}.note{{max-width:900px;margin:0 0 22px}}.fine{{font-size:12px}}.verdict{{display:grid;grid-template-columns:1fr 1fr;gap:28px}}.callout{{border-left:4px solid var(--red);padding-left:20px;font-size:18px}}
.architecture{{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line)}}.step{{padding:18px;border-right:1px solid var(--line)}}.step:last-child{{border:0}}.num{{font:500 26px Georgia,serif;color:var(--blue)}}.step p{{font-size:13px;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:25px}}figure{{margin:0;border-top:1px solid var(--line);padding-top:12px;min-width:0}}figcaption{{font-weight:700}}.caption{{font-size:12px;color:var(--muted);margin:3px 0 8px}}.chart{{display:block;width:100%;height:auto}}
table{{width:100%;border-collapse:collapse;background:var(--panel);font-variant-numeric:tabular-nums}}th,td{{padding:10px 11px;border-bottom:1px solid var(--line);text-align:right;font-size:12px}}th:first-child,td:first-child{{text-align:left}}th{{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em}}
@media(max-width:880px){{.kpis{{grid-template-columns:repeat(2,1fr)}}.grid,.verdict{{grid-template-columns:1fr}}.architecture{{grid-template-columns:1fr 1fr}}.step:nth-child(2){{border-right:0}}}}@media(max-width:560px){{.shell{{padding:32px 16px 60px}}.kpis,.architecture{{grid-template-columns:1fr}}.step{{border-right:0;border-bottom:1px solid var(--line)}}th,td{{padding:8px 6px;font-size:10px}}}}
</style></head><body><main class="shell"><div class="eyebrow">AlphaHunt · slow alternative data</div><h1>Satellite REIT model</h1>
<p class="dek">A low-turnover, cross-country REIT experiment combining five-year satellite-derived construction growth with population growth for the same fixed major-city cohorts.</p>
<div class="warning"><strong>Research verdict, not a deployable backtest:</strong> adding demand relative to construction modestly improves risk-adjusted performance: the unscaled 50/50 blend reaches a {candidate['sharpe']:.2f} Sharpe versus {core['sharpe']:.2f} for construction growth alone. The archive remains retrospectively reprocessed, and the result contains only a handful of effective satellite regimes.</div>
<section class="kpis"><div class="kpi"><span>Blended return</span><strong>{candidate['annual_return_pct']:.2f}%</strong></div><div class="kpi"><span>Volatility</span><strong>{candidate['annual_vol_pct']:.2f}%</strong></div><div class="kpi"><span>Sharpe</span><strong>{candidate['sharpe']:.2f}</strong></div><div class="kpi"><span>Max drawdown</span><strong>{candidate['max_drawdown_pct']:.2f}%</strong></div><div class="kpi"><span>2020+ Sharpe</span><strong>{recent['sharpe']:.2f}</strong></div></section>
<section><h2>What the fourth iteration says</h2><div class="verdict"><div class="callout">From {candidate['start']} through {candidate['end']}, the currency- and country-equity-neutral blend returned {candidate['annual_return_pct']:.2f}% a year at {candidate['annual_vol_pct']:.2f}% volatility after two-leg costs, for a {candidate['sharpe']:.2f} Sharpe, {candidate['max_drawdown_pct']:.2f}% maximum drawdown and {candidate['hac_mean_tstat']:.2f} HAC mean t-statistic.</div><p>Demand minus supply alone returned {demand_supply['annual_return_pct']:.2f}% with a {demand_supply['sharpe']:.2f} Sharpe. The original construction-growth residual remains slightly higher-returning at {core['annual_return_pct']:.2f}% but has a deeper {core['max_drawdown_pct']:.2f}% drawdown. The blend's lower average gross exposure reflects genuine signal disagreement and is not relevered away.</p></div></section>
<section><h2>Model mechanics</h2><p class="note">Everything moves slowly by design. Five-year observations are lagged 12 months, held between epochs and executed one month after signal formation.</p><div class="architecture"><div class="step"><div class="num">01</div><h3>Focus</h3><p>Select each country's ten largest cities using population at the start of the five-year measurement interval.</p></div><div class="step"><div class="num">02</div><h3>Measure</h3><p>Annualize built-up and population growth; rank both urban growth and population growth minus construction growth.</p></div><div class="step"><div class="num">03</div><h3>Neutralize</h3><p>Remove currency returns and subtract the correspondingly ranked local country-equity return.</p></div><div class="step"><div class="num">04</div><h3>Blend</h3><p>Average the two rank portfolios without relevering disagreement; charge 20 bps per traded leg.</p></div></div></section>
<section><h2>Return path and signal comparison</h2><p class="note">The demand adjustment tests whether population growth outruns construction for the same fixed cohort. It diversifies the urban-growth rank modestly, but does not create a new high-frequency observation stream.</p><div class="grid"><figure><figcaption>Cumulative return</figcaption><div class="caption">Blend versus construction growth, demand minus supply and the equity control</div>{charts['cum']}</figure><figure><figcaption>Rolling 12-month Sharpe</figcaption><div class="caption">All variants remain regime-dependent over short windows</div>{charts['sharpe']}</figure><figure><figcaption>Calendar-year return</figcaption><div class="caption">Blended and standalone REIT-specific residual signals</div>{charts['annual']}</figure><figure><figcaption>Drawdown</figcaption><div class="caption">The blend reduces the historical drawdown without increasing gross exposure</div>{charts['dd']}</figure></div></section>
<section><h2>What the satellite actually sees</h2><p class="note">GHSL built-up surface is broad urban fabric, not leased commercial square footage. Restricting the measure to ten major cities improves exposure relevance but still misses redevelopment and vertical intensification.</p><div class="grid"><figure><figcaption>Major-city built-up growth</figcaption><div class="caption">Annualized change for fixed top-ten city cohorts</div>{charts['supply']}</figure><figure><figcaption>Resulting blended position map</figcaption><div class="caption">The blend can move toward cash when growth and demand/supply ranks disagree</div>{charts['positions']}</figure></div></section>
<section><h2>Robustness checks</h2><p class="note">All five blend leave-one-market-out variants remain positive, with Sharpes from 0.19 to 0.58. Raising costs from 20 to 50 bps per traded leg reduces the blend Sharpe only from 0.51 to 0.50 because turnover is extremely low. Three of four original construction-growth regimes are positive; the 0.3125 sign-test probability remains weak statistical evidence.</p><div class="grid"><figure><figcaption>Leave one market out</figcaption><div class="caption">Australia remains important; its omission leaves a positive but weak blend</div>{charts['loo']}</figure><figure><figcaption>Performance by satellite epoch</figcaption><div class="caption">Four original signal settings—not 188 independent monthly bets</div>{charts['epoch']}</figure></div></section>
<section><h2>Full-period ablations</h2><div style="overflow:auto"><table><thead><tr><th>Strategy</th><th>Start</th><th>Return</th><th>Volatility</th><th>Sharpe</th><th>Max DD</th><th>Monthly turnover</th><th>HAC t</th></tr></thead><tbody>{metric_rows(metrics,'full')}</tbody></table></div></section>
<section><h2>Time split</h2><p class="note">The satellite-only strategies use the full common history beginning in 2011; price-lookback ablations begin in 2014. The 2020 onward view is labelled validation, but neither period is sealed because the model has already been inspected.</p><div class="grid"><div style="overflow:auto"><h3>Development: through 2019</h3><table><thead><tr><th>Strategy</th><th>Start</th><th>Return</th><th>Vol</th><th>Sharpe</th><th>Max DD</th><th>Turnover</th><th>HAC t</th></tr></thead><tbody>{metric_rows(metrics,'development')}</tbody></table></div><div style="overflow:auto"><h3>Validation view: 2020–present</h3><table><thead><tr><th>Strategy</th><th>Start</th><th>Return</th><th>Vol</th><th>Sharpe</th><th>Max DD</th><th>Turnover</th><th>HAC t</th></tr></thead><tbody>{metric_rows(metrics,'validation')}</tbody></table></div></div></section>
<section><h2>Universe and return proxies</h2><div style="overflow:auto"><table><thead><tr><th>Market</th><th>Ticker</th><th>Instrument</th><th>GHSL geography</th></tr></thead><tbody>{instrument_rows}</tbody></table></div><p class="fine">The production-candidate core uses VNQ, XRE.TO, IUKP.L, VAP.AX and 1343.T. C38U.SI and 0823.HK remain only in the broad robustness series because they are single-company REITs. Adjusted closes are converted to USD with contemporaneous FX.</p></section>
<section><h2>Evidence and next research gate</h2><ul class="fine"><li><a href="https://human-settlement.emergency.copernicus.eu/datasets.php">GHSL GHS-BUILT-S</a> derives built-up surface from Sentinel-2 and Landsat and provides five-year epochs. The WUP urban-centre table is open and harmonized globally.</li><li><a href="https://www.usgs.gov/landsat-missions/landsat-surface-reflectance">USGS Landsat surface reflectance</a> is globally available at 30 m and supports consistent land-change measurement.</li><li><a href="https://doi.org/10.1016/j.rse.2024.114207">Suh, Zhu & Zhao (2024)</a> show that dense satellite time series and deep learning can monitor construction change, including small isolated targets.</li><li><a href="https://doi.org/10.1016/j.srs.2024.100138">Tang et al. (2024)</a> report a broad-area Landsat construction screener, while emphasizing the difficulty of detecting redevelopment and individual sites.</li><li><a href="https://doi.org/10.1109/JSTARS.2024.3409157">Li et al. (2024)</a> use monthly Landsat time series to estimate building construction timing, pointing to a route from five-year stock changes to annual flow signals.</li></ul><p class="note"><strong>Next gate:</strong> rebuild the feature annually from historical Landsat/Sentinel vintages; detect construction starts rather than completed built area; separate residential, office, industrial and data-centre footprints; map them to point-in-time REIT property exposures; and freeze the sign before a forward test.</p></section>
<section><h2>Known limitations</h2><ul class="fine"><li>GHSL R2025A is a current, consistently reprocessed reconstruction—not a preserved release-vintage archive. The 12-month lag prevents mechanical endpoint leakage but cannot remove revision look-ahead.</li><li>Only the 2005, 2010, 2015 and 2020 epochs influence the reported 2011–2026 core window. Monthly observations greatly overstate the independent satellite sample size.</li><li>The demand/supply rank is unchanged from the 2010 through 2020 GHSL epochs, so its apparent 2020+ validation result is one persistent cross-sectional bet, not repeated confirmation.</li><li>Population is only a broad demand proxy. The model has no point-in-time employment, rents, vacancy, sector-level construction or measured REIT property weights.</li><li>The core universe has only five diversified property funds. Yahoo adjusted closes are research proxies rather than institutionally verified total-return indices.</li><li>Annual construction stages, property-type classifications and SEC exposure mentions remain excluded from P&amp;L because their real historical values have not passed validation.</li></ul></section>
<div class="rule"></div><p class="fine">Research use only; not investment advice. Data through {core['end']}.</p></main></body></html>'''
    args.output.write_text(html, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
