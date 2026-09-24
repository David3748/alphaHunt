#!/usr/bin/env python3
"""Render a self-contained HTML research report for the slow crop-alpha strategy."""

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
    "slow_rv": "#3b567a",
    "slow_carry": "#b98233",
    "rv_plus_weather": "#53877d",
    "rv_weather_yield": "#8b6b9e",
    "rv_carry_weather_yield": "#cf5f45",
}
LABELS = {
    "slow_rv": "Slow relative value",
    "slow_carry": "Carry",
    "rv_plus_weather": "RV + weather",
    "rv_weather_yield": "RV + weather + yield",
    "rv_carry_weather_yield": "All four sleeves",
}
SERIES = list(COLORS)
COMMON_START = pd.Timestamp("2011-05-06")
CHART_DIR = Path(".")


def chart_svg(fig: plt.Figure, filename: str) -> str:
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    target = CHART_DIR / filename
    fig.savefig(target, format="png", dpi=145, bbox_inches="tight", transparent=True)
    plt.close(fig)
    return f'<img class="chart" alt="Data chart" src="html-assets/{filename}">'


def style_axis(ax: plt.Axes, percent_y: bool = False) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#c9c3b8")
    ax.grid(axis="y", color="#ded9cf", linewidth=0.7, alpha=0.8)
    ax.tick_params(colors="#565b61", labelsize=8)
    ax.set_axisbelow(True)
    if percent_y:
        ax.yaxis.set_major_formatter(lambda value, _: f"{value:.0%}")
    ax.xaxis.set_major_locator(mdates.YearLocator(3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))


def line_legend(ax: plt.Axes, columns: list[str]) -> None:
    ax.legend(
        [LABELS[column] for column in columns], loc="upper left", ncol=2,
        frameon=False, fontsize=8, handlelength=2.5, columnspacing=1.5,
    )


def cumulative_chart(returns: pd.DataFrame) -> str:
    growth = (1 + returns[SERIES]).cumprod() - 1
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    for column in SERIES:
        ax.plot(growth.index, growth[column], color=COLORS[column], linewidth=1.65)
    style_axis(ax, True)
    ax.axhline(0, color="#8b8b86", linewidth=0.8)
    ax.set_ylabel("Cumulative net excess return", fontsize=8, color="#565b61")
    line_legend(ax, SERIES)
    return chart_svg(fig, "cumulative-excess-return.png")


def incremental_chart(returns: pd.DataFrame) -> str:
    columns = ["slow_carry", "rv_plus_weather", "rv_weather_yield", "rv_carry_weather_yield"]
    active = returns[columns].sub(returns["slow_rv"], axis=0)
    growth = (1 + active).cumprod() - 1
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    for column in columns:
        ax.plot(growth.index, growth[column], color=COLORS[column], linewidth=1.65)
    style_axis(ax, True)
    ax.axhline(0, color="#8b8b86", linewidth=0.8)
    ax.set_ylabel("Cumulative return minus slow RV", fontsize=8, color="#565b61")
    line_legend(ax, columns)
    return chart_svg(fig, "incremental-over-rv.png")


def rolling_sharpe_chart(returns: pd.DataFrame) -> str:
    rolling = returns[SERIES].rolling(52, min_periods=39).mean() / returns[SERIES].rolling(
        52, min_periods=39).std() * np.sqrt(52)
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    for column in SERIES:
        ax.plot(rolling.index, rolling[column], color=COLORS[column], linewidth=1.4)
    style_axis(ax)
    ax.axhline(0, color="#8b8b86", linewidth=0.8)
    ax.set_ylim(max(-3, np.nanpercentile(rolling.values, 1)), min(4, np.nanpercentile(rolling.values, 99)))
    ax.set_ylabel("Trailing 52-week Sharpe", fontsize=8, color="#565b61")
    line_legend(ax, SERIES)
    return chart_svg(fig, "rolling-sharpe.png")


def rolling_return_chart(returns: pd.DataFrame) -> str:
    rolling = returns[SERIES].rolling(52, min_periods=39).mean() * 52
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    for column in SERIES:
        ax.plot(rolling.index, rolling[column], color=COLORS[column], linewidth=1.4)
    style_axis(ax, True)
    ax.axhline(0, color="#8b8b86", linewidth=0.8)
    ax.set_ylabel("Trailing 52-week annualized return", fontsize=8, color="#565b61")
    line_legend(ax, SERIES)
    return chart_svg(fig, "rolling-return.png")


def drawdown_chart(returns: pd.DataFrame) -> str:
    wealth = (1 + returns[SERIES]).cumprod()
    drawdown = wealth.div(wealth.cummax()) - 1
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    for column in SERIES:
        ax.plot(drawdown.index, drawdown[column], color=COLORS[column], linewidth=1.4)
    style_axis(ax, True)
    ax.set_ylabel("Drawdown from prior peak", fontsize=8, color="#565b61")
    line_legend(ax, SERIES)
    return chart_svg(fig, "drawdown.png")


def carry_chart(carry: pd.DataFrame) -> str:
    palette = {"corn": "#d59c42", "soy": "#56876d", "wheat": "#8c7055"}
    smoothed = carry.rolling(13, min_periods=4).mean()
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    for column in ["corn", "soy", "wheat"]:
        ax.plot(smoothed.index, smoothed[column], color=palette[column], linewidth=1.35, label=column.title())
    style_axis(ax, True)
    ax.axhline(0, color="#8b8b86", linewidth=0.8)
    ax.set_ylabel("Annualized front/deferred carry", fontsize=8, color="#565b61")
    ax.legend(loc="upper left", frameon=False, ncol=3, fontsize=8)
    return chart_svg(fig, "carry-history.png")


def sensitivity_chart(sensitivity: dict) -> str:
    labels, common, recent = [], [], []
    for label, stats in sensitivity.items():
        labels.append(label.replace(" expiry buffer / ", "d / ").replace("-week smoothing", "w"))
        common.append(stats["rv_carry_weather_yield"]["common_yield_window"]["sharpe"])
        recent.append(stats["rv_carry_weather_yield"]["holdout_2021"]["sharpe"])
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    ax.bar(x - 0.18, common, 0.36, color="#3b567a", label="2011–present")
    ax.bar(x + 0.18, recent, 0.36, color="#cf5f45", label="2021–present")
    ax.set_xticks(x, labels, rotation=18, ha="right", fontsize=8)
    ax.set_ylabel("All-four-sleeve Sharpe", fontsize=8, color="#565b61")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#ded9cf", linewidth=0.7)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8)
    return chart_svg(fig, "carry-sensitivity.png")


def metric_table(metrics: dict, slice_name: str) -> str:
    rows = []
    for strategy in SERIES:
        value = metrics[strategy][slice_name]
        rows.append(f"""<tr><td><span class="dot" style="background:{COLORS[strategy]}"></span>{LABELS[strategy]}</td>
        <td>{value['annual_return_pct']:.2f}%</td><td>{value['annual_vol_pct']:.2f}%</td>
        <td>{value['sharpe']:.3f}</td><td>{value['max_drawdown_pct']:.2f}%</td>
        <td>{value['avg_gross_exposure']:.3f}</td><td>{value['hac_mean_tstat']:.3f}</td></tr>""")
    return "\n".join(rows)


def main() -> None:
    global CHART_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("research/slow_crop_alpha"))
    parser.add_argument("--output", type=Path, default=Path("research/slow_crop_alpha/slow-alpha-report.html"))
    args = parser.parse_args()
    CHART_DIR = args.output.parent / "html-assets"
    returns = pd.read_parquet(args.input / "strategy_returns.parquet").loc[COMMON_START:]
    carry = pd.read_parquet(args.input / "raw/ers_weekly_carry.parquet").loc[COMMON_START:]
    metrics = json.loads((args.input / "metrics.json").read_text())
    sensitivity = json.loads((args.input / "carry_sensitivity.json").read_text())
    manifest = json.loads((args.input / "manifest.json").read_text())
    final = metrics["rv_carry_weather_yield"]["common_yield_window"]
    recent = metrics["rv_carry_weather_yield"]["holdout_2021"]

    charts = {
        "cumulative": cumulative_chart(returns),
        "incremental": incremental_chart(returns),
        "sharpe": rolling_sharpe_chart(returns),
        "rolling_return": rolling_return_chart(returns),
        "drawdown": drawdown_chart(returns),
        "carry": carry_chart(carry),
        "sensitivity": sensitivity_chart(sensitivity),
    }
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Slow Crop Alpha — Research Report</title>
<style>
:root{{--ink:#17222d;--muted:#687078;--paper:#f4f1ea;--panel:#fffdf8;--line:#d9d3c8;--navy:#3b567a;--red:#cf5f45;--green:#53877d}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}}
.shell{{max-width:1240px;margin:auto;padding:54px 28px 80px}} .eyebrow{{font-size:11px;font-weight:750;letter-spacing:.16em;text-transform:uppercase;color:var(--red)}}
h1{{font-family:Georgia,serif;font-size:clamp(38px,6vw,72px);font-weight:500;letter-spacing:-.035em;line-height:1;margin:12px 0 18px;max-width:900px}}
.dek{{font-size:18px;color:var(--muted);max-width:850px}} .rule{{height:1px;background:var(--line);margin:35px 0}}
.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}} .kpi{{background:var(--panel);padding:18px}}
.kpi span{{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}} .kpi strong{{font:500 28px/1.2 Georgia,serif}}
h2{{font:500 31px/1.2 Georgia,serif;margin:48px 0 9px}} h3{{font-size:15px;margin:0 0 7px}} .section-note{{color:var(--muted);max-width:820px;margin:0 0 22px}}
.thesis{{display:grid;grid-template-columns:1.1fr .9fr;gap:30px}} .callout{{border-left:4px solid var(--red);padding:3px 0 3px 20px;font-size:18px}}
.architecture{{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line)}} .sleeve{{padding:18px;border-right:1px solid var(--line);background:rgba(255,253,248,.6)}} .sleeve:last-child{{border:0}}
.weight{{font:500 27px Georgia,serif;color:var(--navy)}} .sleeve p{{color:var(--muted);font-size:13px;margin:6px 0 0}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:24px}} figure{{margin:0;border-top:1px solid var(--line);padding-top:12px;min-width:0}} figcaption{{font-weight:700;margin-bottom:4px}} .caption{{font-size:12px;color:var(--muted);margin-bottom:8px}}
.chart{{display:block;width:100%;height:auto}} table{{width:100%;border-collapse:collapse;background:var(--panel);font-variant-numeric:tabular-nums}}
th,td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:right;font-size:13px}} th:first-child,td:first-child{{text-align:left}} th{{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}} .fine{{font-size:12px;color:var(--muted)}}
.method{{display:grid;grid-template-columns:repeat(3,1fr);gap:22px}} .method div{{border-top:2px solid var(--navy);padding-top:12px}} ul{{padding-left:18px}}
@media(max-width:850px){{.kpis{{grid-template-columns:repeat(2,1fr)}}.grid,.thesis{{grid-template-columns:1fr}}.architecture,.method{{grid-template-columns:1fr 1fr}}}}
@media(max-width:560px){{.shell{{padding:32px 16px 60px}}.architecture,.method{{grid-template-columns:1fr}}.sleeve{{border-right:0;border-bottom:1px solid var(--line)}}.kpis{{grid-template-columns:1fr 1fr}}.kpi:last-child{{grid-column:1/-1}}th,td{{padding:8px 6px;font-size:11px}}}}
</style></head><body><main class="shell">
<div class="eyebrow">AlphaHunt · systematic agriculture research</div><h1>Slow crop alpha</h1>
<p class="dek">A monthly corn–soy–wheat portfolio built for persistent information: relative value, curve carry, delayed crop weather and stage-aware yield forecasts.</p>
<div class="rule"></div><section class="kpis">
<div class="kpi"><span>Annualized return</span><strong>{final['annual_return_pct']:.2f}%</strong></div>
<div class="kpi"><span>Annualized volatility</span><strong>{final['annual_vol_pct']:.2f}%</strong></div>
<div class="kpi"><span>Sharpe</span><strong>{final['sharpe']:.2f}</strong></div>
<div class="kpi"><span>Maximum drawdown</span><strong>{final['max_drawdown_pct']:.2f}%</strong></div>
<div class="kpi"><span>Average gross</span><strong>{final['avg_gross_exposure']:.2f}×</strong></div></section>

<section><h2>What the result says</h2><div class="thesis"><div class="callout">The four-sleeve mix improves risk-adjusted performance mainly through diversification and disciplined cancellation—not higher leverage. Since 2021 its Sharpe is {recent['sharpe']:.2f}, but that period was not an untouched holdout.</div>
<div><p>On the common 2011–2026 window, the portfolio returned {final['annual_return_pct']:.2f}% annualized at {final['annual_vol_pct']:.2f}% volatility, with a HAC mean t-statistic of {final['hac_mean_tstat']:.2f}. Its average gross exposure was only {final['avg_gross_exposure']:.2f}× because opposing sleeves are allowed to cancel into cash.</p></div></div></section>

<section><h2>Strategy architecture</h2><p class="section-note">Signals are dollar-neutral across corn, soy and wheat. Monthly targets execute on the following weekly observation.</p>
<div class="architecture"><div class="sleeve"><div class="weight">40%</div><h3>Relative value</h3><p>Three-year own-price normalization, cross-grain demeaning and 13-week smoothing.</p></div>
<div class="sleeve"><div class="weight">20%</div><h3>Carry</h3><p>Annualized front/deferred backwardation, excluding contracts within 28 days of expiry.</p></div>
<div class="sleeve"><div class="weight">20%</div><h3>Weather</h3><p>Production-weighted crop stress, delayed four weeks and smoothed over twelve.</p></div>
<div class="sleeve"><div class="weight">20%</div><h3>Yield</h3><p>Expanding-window MODIS/weather yield forecasts, expiring after 26 weeks.</p></div></div></section>

<section><h2>Return path</h2><p class="section-note">Futures P&amp;L is an excess-return stream. Returns hold the selected contract consistently between observations and include signal and scheduled-roll costs.</p>
<div class="grid"><figure><figcaption>Cumulative net excess return</figcaption><div class="caption">Growth above initial capital, common sample</div>{charts['cumulative']}</figure>
<figure><figcaption>Incremental return over slow RV</figcaption><div class="caption">Cumulative weekly strategy return minus the RV baseline</div>{charts['incremental']}</figure>
<figure><figcaption>Rolling 12-month Sharpe</figcaption><div class="caption">Trailing 52-week mean divided by volatility, annualized</div>{charts['sharpe']}</figure>
<figure><figcaption>Rolling 12-month excess return</figcaption><div class="caption">Trailing 52-week arithmetic return, annualized</div>{charts['rolling_return']}</figure>
<figure><figcaption>Drawdown</figcaption><div class="caption">Decline from each strategy's prior equity peak</div>{charts['drawdown']}</figure>
<figure><figcaption>Underlying carry</figcaption><div class="caption">Thirteen-week average; positive values indicate backwardation</div>{charts['carry']}</figure></div></section>

<section><h2>Comparable performance</h2><p class="section-note">All rows below use the common out-of-sample yield window beginning May 2011.</p>
<div style="overflow:auto"><table><thead><tr><th>Strategy</th><th>Return</th><th>Volatility</th><th>Sharpe</th><th>Max drawdown</th><th>Avg gross</th><th>HAC t-stat</th></tr></thead><tbody>{metric_table(metrics,'common_yield_window')}</tbody></table></div></section>

<section><h2>Recent behavior</h2><p class="section-note">The 2021–2026 slice is diagnostic rather than a sealed holdout.</p>
<div style="overflow:auto"><table><thead><tr><th>Strategy</th><th>Return</th><th>Volatility</th><th>Sharpe</th><th>Max drawdown</th><th>Avg gross</th><th>HAC t-stat</th></tr></thead><tbody>{metric_table(metrics,'holdout_2021')}</tbody></table></div></section>

<section><h2>Carry robustness</h2><p class="section-note">The base specification remains a 28-day expiry buffer and 13-week smoothing; alternatives are robustness checks, not selection candidates.</p>
<figure>{charts['sensitivity']}</figure></section>

<section><h2>Method and limits</h2><div class="method"><div><h3>Point-in-time discipline</h3><p>Weather is delayed four weeks; monthly decisions trade the next weekly bar; yield models use only prior crop years.</p></div>
<div><h3>Contract construction</h3><p>USDA ERS Thursday settlements create front/deferred carry and same-contract weekly returns. Approximate expiry is the 15th of contract month.</p></div>
<div><h3>Costs</h3><p>10 bps per unit of signal turnover plus 2 bps for each scheduled contract roll.</p></div></div>
<ul class="fine"><li>NASA and VegScape histories are current reprocessed archives, not preserved release-vintage files.</li><li>ERS observations are weekly and execution is simplified; daily institutional settlement histories remain preferable.</li><li>The recent window influenced the research process and must not be presented as untouched validation.</li><li>Carry source coverage: {manifest['carry_coverage']['start']} to {manifest['carry_coverage']['end']}; {manifest['carry_coverage']['contract_pair_rows']:,} dated crop/curve observations.</li></ul></section>
<div class="rule"></div><p class="fine">Generated from the reproducible artifacts in research/slow_crop_alpha. Research use only; not investment advice.</p>
</main></body></html>"""
    args.output.write_text(document, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
