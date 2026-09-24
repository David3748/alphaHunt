#!/usr/bin/env python3
"""Render the slow-softs economic backtest as a responsive HTML report."""

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
    "slow_relative_value": "#49647e", "stage_weather": "#78966a",
    "stage_weather_reversion": "#896b96", "slow_positioning": "#c2873e",
    "slow_softs_core": "#c85e4d",
}
LABELS = {
    "slow_relative_value": "Relative value", "stage_weather": "Directional weather",
    "stage_weather_reversion": "Delayed weather reversal", "slow_positioning": "Hedging pressure",
    "slow_softs_core": "Slow softs core",
}
SERIES = list(COLORS)
CHART_DIR = Path(".")


def style_axis(ax, percent=False):
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#c9c3b8")
    ax.grid(axis="y", color="#ded9cf", linewidth=.7, alpha=.8)
    ax.tick_params(colors="#565b61", labelsize=8)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_locator(mdates.YearLocator(3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    if percent:
        ax.yaxis.set_major_formatter(lambda value, _: f"{value:.0%}")


def save(fig, name, alt):
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(CHART_DIR / name, dpi=145, bbox_inches="tight", transparent=True)
    plt.close(fig)
    return f'<img class="chart" src="html-assets/{name}" alt="{alt}">'


def legend(ax, columns):
    ax.legend([LABELS[c] for c in columns], loc="upper left", ncol=2,
              frameon=False, fontsize=8, handlelength=2.5)


def cumulative_chart(returns):
    growth = (1 + returns[SERIES]).cumprod() - 1
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    for column in SERIES:
        ax.plot(growth.index, growth[column], color=COLORS[column], linewidth=1.55)
    style_axis(ax, True); ax.axhline(0, color="#85857f", linewidth=.8)
    ax.set_ylabel("Cumulative benchmark return", fontsize=8); legend(ax, SERIES)
    return save(fig, "cumulative-return.png", "Cumulative modeled benchmark returns")


def rolling_sharpe_chart(returns):
    rolling = returns[SERIES].rolling(12, min_periods=9).mean() / returns[SERIES].rolling(12, min_periods=9).std() * np.sqrt(12)
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    for column in SERIES:
        ax.plot(rolling.index, rolling[column], color=COLORS[column], linewidth=1.35)
    style_axis(ax); ax.axhline(0, color="#85857f", linewidth=.8)
    finite = rolling.to_numpy()[np.isfinite(rolling.to_numpy())]
    if finite.size:
        ax.set_ylim(max(-4, np.percentile(finite, 1)), min(4, np.percentile(finite, 99)))
    ax.set_ylabel("Trailing 12-month Sharpe", fontsize=8); legend(ax, SERIES)
    return save(fig, "rolling-sharpe.png", "Rolling twelve month Sharpe ratios")


def rolling_return_chart(returns):
    rolling = returns[SERIES].rolling(12, min_periods=9).mean() * 12
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    for column in SERIES:
        ax.plot(rolling.index, rolling[column], color=COLORS[column], linewidth=1.35)
    style_axis(ax, True); ax.axhline(0, color="#85857f", linewidth=.8)
    ax.set_ylabel("Trailing annualized return", fontsize=8); legend(ax, SERIES)
    return save(fig, "rolling-return.png", "Rolling twelve month annualized returns")


def drawdown_chart(returns):
    wealth = (1 + returns[SERIES]).cumprod()
    drawdown = wealth / wealth.cummax() - 1
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    for column in SERIES:
        ax.plot(drawdown.index, drawdown[column], color=COLORS[column], linewidth=1.35)
    style_axis(ax, True); ax.set_ylabel("Drawdown", fontsize=8); legend(ax, SERIES)
    return save(fig, "drawdown.png", "Drawdowns from prior peaks")


def overlay_chart(returns):
    columns = ["positioning_plus_weather_reversion", "slow_softs_core"]
    active = returns[columns].sub(returns["slow_positioning"], axis=0)
    growth = (1 + active).cumprod() - 1
    names = {"positioning_plus_weather_reversion": "Weather overlay vs positioning",
             "slow_softs_core": "Core vs positioning"}
    colors = {"positioning_plus_weather_reversion": COLORS["stage_weather_reversion"],
              "slow_softs_core": COLORS["slow_softs_core"]}
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    for column in columns:
        ax.plot(growth.index, growth[column], color=colors[column], linewidth=1.5, label=names[column])
    style_axis(ax, True); ax.axhline(0, color="#85857f", linewidth=.8)
    ax.set_ylabel("Cumulative active return", fontsize=8); ax.legend(frameon=False, fontsize=8)
    return save(fig, "weather-overlay-active.png", "Weather overlay active return versus positioning")


def component_prices_chart(prices):
    normalized = prices / prices.iloc[0]
    palette = {"coffee":"#73533c", "sugar":"#b58a3b", "cocoa":"#76523c", "cotton":"#6d8190"}
    fig, ax = plt.subplots(figsize=(8.5, 3.8))
    for column in prices:
        ax.plot(normalized.index, normalized[column], color=palette[column], linewidth=1.35, label=column.title())
    style_axis(ax); ax.set_yscale("log"); ax.set_ylabel("Price index, Jan 2001 = 1 (log)", fontsize=8)
    ax.legend(loc="upper left", ncol=4, frameon=False, fontsize=8)
    return save(fig, "component-prices.png", "World Bank soft commodity price indices")


def sensitivity_chart(sensitivity):
    labels, overlay, combined = [], [], []
    for label, values in sensitivity.items():
        labels.append(label)
        overlay.append(values["positioning_plus_weather_reversion"]["common_cot_window"]["sharpe"])
        combined.append(values["slow_softs_core"]["common_cot_window"]["sharpe"])
    x = np.arange(len(labels)); fig, ax = plt.subplots(figsize=(8.5, 3.8))
    ax.bar(x-.18, overlay, .36, color=COLORS["stage_weather_reversion"], label="Positioning + weather")
    ax.bar(x+.18, combined, .36, color=COLORS["slow_softs_core"], label="Slow softs core")
    ax.set_xticks(x, labels, rotation=18, ha="right", fontsize=8); ax.set_ylabel("Full-period Sharpe", fontsize=8)
    ax.spines[["top", "right"]].set_visible(False); ax.grid(axis="y", color="#ded9cf", linewidth=.7); ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=8)
    return save(fig, "sensitivity.png", "Sensitivity of strategy Sharpe ratios")


def metric_rows(metrics, sample):
    rows = []
    for strategy in SERIES:
        value = metrics[strategy][sample]
        rows.append(f'<tr><td><span class="dot" style="background:{COLORS[strategy]}"></span>{LABELS[strategy]}</td>'
                    f'<td>{value["annual_return_pct"]:.2f}%</td><td>{value["annual_vol_pct"]:.2f}%</td>'
                    f'<td>{value["sharpe"]:.3f}</td><td>{value["max_drawdown_pct"]:.2f}%</td>'
                    f'<td>{value["avg_gross_exposure"]:.3f}</td><td>{value["hac_mean_tstat"]:.3f}</td></tr>')
    return "\n".join(rows)


def main():
    global CHART_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("research/slow_softs_alpha"))
    parser.add_argument("--output", type=Path, default=Path("research/slow_softs_alpha/slow-softs-report.html"))
    args = parser.parse_args(); CHART_DIR = args.output.parent / "html-assets"
    returns = pd.read_parquet(args.input / "strategy_returns.parquet").loc["2004-01-31":]
    prices = pd.read_parquet(args.input / "raw/world_bank_softs_prices.parquet").loc["2001-01-31":]
    metrics = json.loads((args.input / "metrics.json").read_text())
    sensitivity = json.loads((args.input / "weather_overlay_sensitivity.json").read_text())
    manifest = json.loads((args.input / "manifest.json").read_text())
    best = metrics["slow_softs_core"]["common_cot_window"]
    positioning = metrics["slow_positioning"]["common_cot_window"]
    directional = metrics["stage_weather"]["common_cot_window"]
    reversal = metrics["stage_weather_reversion"]["common_cot_window"]
    recent = metrics["slow_softs_core"]["holdout_2021"]
    charts = {"cum": cumulative_chart(returns), "sharpe": rolling_sharpe_chart(returns),
              "roll": rolling_return_chart(returns), "dd": drawdown_chart(returns),
              "overlay": overlay_chart(returns), "sens": sensitivity_chart(sensitivity)}
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Slow Softs Alpha — Research Report</title><style>
:root{{--ink:#17222d;--muted:#687078;--paper:#f4f1ea;--panel:#fffdf8;--line:#d9d3c8;--navy:#49647e;--red:#c85e4d;--amber:#c2873e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}}
.shell{{max-width:1240px;margin:auto;padding:54px 28px 80px}}.eyebrow{{font-size:11px;font-weight:750;letter-spacing:.16em;text-transform:uppercase;color:var(--red)}}
h1{{font:500 clamp(38px,6vw,72px)/1 Georgia,serif;letter-spacing:-.035em;margin:12px 0 18px}}.dek{{font-size:18px;color:var(--muted);max-width:880px}}.rule{{height:1px;background:var(--line);margin:35px 0}}
.warning{{background:#fff4df;border:1px solid #e0c99f;padding:16px 18px;margin:28px 0}}.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}}.kpi{{background:var(--panel);padding:18px}}.kpi span{{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}}.kpi strong{{font:500 28px/1.2 Georgia,serif}}
h2{{font:500 31px/1.2 Georgia,serif;margin:48px 0 9px}}h3{{font-size:15px;margin:0 0 7px}}.note,.fine{{color:var(--muted)}}.note{{max-width:850px;margin:0 0 22px}}.fine{{font-size:12px}}
.architecture{{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid var(--line)}}.sleeve{{padding:18px;border-right:1px solid var(--line)}}.sleeve:last-child{{border:0}}.weight{{font:500 27px Georgia,serif;color:var(--navy)}}.sleeve p{{font-size:13px;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}figure{{margin:0;border-top:1px solid var(--line);padding-top:12px;min-width:0}}figcaption{{font-weight:700}}.caption{{font-size:12px;color:var(--muted);margin:3px 0 8px}}.chart{{display:block;width:100%;height:auto}}
table{{width:100%;border-collapse:collapse;background:var(--panel);font-variant-numeric:tabular-nums}}th,td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:right;font-size:13px}}th:first-child,td:first-child{{text-align:left}}th{{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em}}.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}}
.verdict{{display:grid;grid-template-columns:1fr 1fr;gap:28px}}.callout{{border-left:4px solid var(--red);padding-left:20px;font-size:18px}}
@media(max-width:850px){{.kpis{{grid-template-columns:repeat(2,1fr)}}.grid,.verdict{{grid-template-columns:1fr}}.architecture{{grid-template-columns:1fr}}.sleeve{{border-right:0;border-bottom:1px solid var(--line)}}}}@media(max-width:560px){{.shell{{padding:32px 16px 60px}}.kpi:last-child{{grid-column:1/-1}}th,td{{padding:8px 6px;font-size:11px}}}}
</style></head><body><main class="shell"><div class="eyebrow">AlphaHunt · systematic agriculture research</div><h1>Slow softs alpha</h1>
<p class="dek">A monthly coffee–sugar–cocoa–cotton study combining persistent weather, multi-year relative value and CFTC producer hedging pressure.</p>
<div class="warning"><strong>Important:</strong> the return leg uses World Bank monthly benchmark prices, not contract-consistent ICE futures. Treat this as an economic screen, not an executable excess-return backtest.</div>
<section class="kpis"><div class="kpi"><span>Core return</span><strong>{best['annual_return_pct']:.2f}%</strong></div><div class="kpi"><span>Core volatility</span><strong>{best['annual_vol_pct']:.2f}%</strong></div><div class="kpi"><span>Core Sharpe</span><strong>{best['sharpe']:.2f}</strong></div><div class="kpi"><span>Core max DD</span><strong>{best['max_drawdown_pct']:.2f}%</strong></div><div class="kpi"><span>2021+ Sharpe</span><strong>{recent['sharpe']:.2f}</strong></div></section>
<section><h2>Bottom line</h2><div class="verdict"><div class="callout">The better weather overlay is a delayed reversal signal, not a race to trade the forecast first. Directional stage-aware weather lost money; fading its already-public stress signal raised the core Sharpe to {best['sharpe']:.2f} and cut maximum drawdown to {best['max_drawdown_pct']:.2f}%.</div><p>The stage model identifies biologically damaging drought, heat, frost and harvest rain. With monthly execution, buying the damaged crop was too late: its Sharpe was {directional['sharpe']:.2f}. Reversing that exposure after the information delay produced a {reversal['sharpe']:.2f} standalone Sharpe. Combined with hedging pressure and relative value, the core earned {best['annual_return_pct']:.2f}% at {best['annual_vol_pct']:.2f}% volatility, with a HAC mean t-statistic of {best['hac_mean_tstat']:.2f}.</p></div></section>
<section><h2>Architecture selected</h2><p class="note">Signals are cross-sectionally dollar-neutral and observed monthly; positions begin with the next month's return.</p><div class="architecture"><div class="sleeve"><div class="weight">25%</div><h3>Relative value</h3><p>Three-year own-price z-score, cross-soft mean reversion and three-month smoothing.</p></div><div class="sleeve"><div class="weight">25%</div><h3>Weather reversal</h3><p>Crop-stage and region-specific nonlinear stress, delayed two weeks, smoothed eight weeks, then faded at the next monthly return.</p></div><div class="sleeve"><div class="weight">50%</div><h3>Hedging pressure</h3><p>Commercial net positioning versus open interest, three-year normalization and thirteen-week smoothing.</p></div></div></section>
<section><h2>Return path</h2><p class="note">Charts are net of 10 bps per unit of monthly turnover, but remain benchmark-price proxies.</p><div class="grid"><figure><figcaption>Cumulative modeled return</figcaption><div class="caption">Growth relative to initial capital</div>{charts['cum']}</figure><figure><figcaption>Weather overlay contribution</figcaption><div class="caption">Active return versus hedging pressure alone</div>{charts['overlay']}</figure><figure><figcaption>Rolling 12-month Sharpe</figcaption><div class="caption">Trailing monthly mean divided by volatility</div>{charts['sharpe']}</figure><figure><figcaption>Rolling 12-month return</figcaption><div class="caption">Trailing arithmetic return, annualized</div>{charts['roll']}</figure><figure><figcaption>Drawdown</figcaption><div class="caption">Decline from prior modeled equity peak</div>{charts['dd']}</figure><figure><figcaption>Weather timing sensitivity</figcaption><div class="caption">Lag and persistence alternatives</div>{charts['sens']}</figure></div></section>
<section><h2>Comparable performance, 2004–2026</h2><div style="overflow:auto"><table><thead><tr><th>Strategy</th><th>Return</th><th>Volatility</th><th>Sharpe</th><th>Max drawdown</th><th>Avg gross</th><th>HAC t-stat</th></tr></thead><tbody>{metric_rows(metrics,'common_cot_window')}</tbody></table></div></section>
<section><h2>Recent behavior, 2021–2026</h2><div style="overflow:auto"><table><thead><tr><th>Strategy</th><th>Return</th><th>Volatility</th><th>Sharpe</th><th>Max drawdown</th><th>Avg gross</th><th>HAC t-stat</th></tr></thead><tbody>{metric_rows(metrics,'holdout_2021')}</tbody></table></div></section>
<section><h2>Time-split check</h2><p class="note">The model was not genuinely sealed: the recent period had already been inspected. These splits show regime dependence rather than an untouched test.</p><div class="grid"><div style="overflow:auto"><h3>Development, 2004–2015</h3><table><thead><tr><th>Strategy</th><th>Return</th><th>Volatility</th><th>Sharpe</th><th>Max DD</th><th>Gross</th><th>HAC t</th></tr></thead><tbody>{metric_rows(metrics,'development_2004_2015')}</tbody></table></div><div style="overflow:auto"><h3>Validation, 2016–2020</h3><table><thead><tr><th>Strategy</th><th>Return</th><th>Volatility</th><th>Sharpe</th><th>Max DD</th><th>Gross</th><th>HAC t</th></tr></thead><tbody>{metric_rows(metrics,'validation_2016_2020')}</tbody></table></div></div></section>
<section><h2>Limits and next gate</h2><ul class="fine"><li>ICE carry is excluded: {manifest['ice_carry_status']}.</li><li>World Bank series include physical/benchmark definitions and may not match Coffee C, Sugar No. 11, Cocoa and Cotton No. 2 futures basis or roll returns.</li><li>NASA history is a current reprocessed archive rather than a preserved release-vintage dataset.</li><li>Before capital allocation, rerun signals on licensed daily ICE individual-contract settlements, add curve carry, and freeze an untouched forward period.</li><li>Price coverage: {manifest['price_coverage'][0]} to {manifest['price_coverage'][1]}; CFTC coverage: {manifest['cot_coverage'][0]} to {manifest['cot_coverage'][1]}.</li></ul></section>
<section><h2>Research translated into the overlay</h2><p class="note">The revision follows the papers' common structure: phenological windows, multi-week trajectories, nonlinear extreme interactions and region-specific aggregation.</p><ul class="fine"><li><a href="https://doi.org/10.1016/j.srs.2024.100153">Kalecinski et al., 2024:</a> the paper you supplied finds that combining SAR and optical data at different growth stages improves yield estimation; it motivates stage alignment and makes satellite condition the next data upgrade.</li><li><a href="https://doi.org/10.1016/j.rsase.2023.101092">Coffee:</a> imagery and crop status can forecast yield months before harvest; the model now separates flowering, bean-fill and Brazilian frost windows.</li><li><a href="https://doi.org/10.1016/j.eja.2023.126889">Sugarcane:</a> Sentinel-1 VOD plus Sentinel-2 greenness supports forecasts up to two months before harvest; growth drought and harvest rain are now separate risks.</li><li><a href="https://doi.org/10.1016/j.indcrop.2024.119540">Cotton:</a> combined heat and drought during flowering and boll development is more damaging than either alone; the overlay includes an explicit interaction.</li><li><a href="https://doi.org/10.1016/j.agwat.2024.108995">Cocoa:</a> seasonal rainfall reduction materially lowered yield, supporting nonlinear dry-season stress concentrated in West African pod-development months.</li></ul></section>
<section><h2>Why reverse a valid damage signal?</h2><p class="note">This is a horizon distinction, not a claim that bad weather raises yields. The biological score points toward supply damage, but a monthly trader observes it after faster participants have already reacted. Agricultural-price research documents reversals following unusually large information-driven moves, while futures research finds weather-return effects vary across market tails. The directional and reversal versions remain side by side in every chart.</p><p class="fine"><a href="https://ideas.repec.org/a/bla/jageco/v45y1994i2p240-251.html">Agricultural commodity overreaction</a> · <a href="https://doi.org/10.1016/j.eneco.2021.105377">Temperature anomalies and futures-return quantiles</a> · <a href="literature-review.md">Full research translation</a></p></section>
<section><h2>Data sources</h2><p class="fine"><a href="https://www.worldbank.org/en/research/commodity-markets">World Bank Pink Sheet</a> · <a href="https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm">CFTC historical COT files</a> · <a href="https://power.larc.nasa.gov/">NASA POWER agroclimatology</a></p></section>
<div class="rule"></div><p class="fine">Research use only; not investment advice.</p></main></body></html>'''
    args.output.write_text(html, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
