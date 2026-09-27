"""Render the fixed corn-model research results; no fetching, fitting, or tuning.

Run ``python -m src.corn_model_report`` after ``python -m src.corn_model backtest``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / 'results/corn_model'
MODELS = ('usda', 'bias', 'weather', 'satellite')
STRATEGIES = (*MODELS, 'always_long', 'always_short')
LABELS = {'usda': 'USDA unchanged', 'bias': 'Past mean revision', 'weather': 'Weather',
          'satellite': 'Weather + satellite', 'always_long': 'Always long', 'always_short': 'Always short'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def percent(value, signed=False):
    if value is None or not np.isfinite(value):
        return 'Unavailable'
    return f'{value:+.2%}' if signed else f'{value:.2%}'


def interval(value):
    return 'Unavailable' if value is None else f'[{percent(value[0], True)}, {percent(value[1], True)}]'


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |',
                      *['| ' + ' | '.join(map(str, row)) + ' |' for row in rows]])


def load_outputs(directory):
    names = ('summary.json', 'predictions.csv', 'paper_trades.csv', 'historical_example.json', 'protocol.json')
    contents = {name: (directory / name).read_bytes() for name in names}
    summary = json.loads(contents['summary.json'])
    example = json.loads(contents['historical_example.json'])
    protocol = json.loads(contents['protocol.json'])
    predictions = pd.read_csv(directory / 'predictions.csv')
    trades = pd.read_csv(directory / 'paper_trades.csv')
    # Detect mixed runs rather than publishing a stale summary next to new CSVs.
    scored = predictions[predictions.status.eq('ready') & np.isfinite(predictions.target_yield_bu_acre)]
    if len(scored) != summary['forecast']['n_events'] or scored.year.nunique() != summary['forecast']['n_years']:
        raise ValueError('Forecast CSV and summary support disagree; finish the backtest before reporting')
    for name in MODELS:
        squared = (scored[f'{name}_yield_bu_acre'] - scored.target_yield_bu_acre) ** 2
        rmse = float(np.sqrt(squared.groupby(scored.year).mean().mean()))
        if not np.isclose(rmse, summary['forecast']['models'][name]['rmse_bu_acre'], atol=1e-8, rtol=1e-8):
            raise ValueError('Forecast CSV and summary metric disagree: ' + name)
    wide = trades.pivot(index='forecast_at', columns='strategy', values='net_return')
    support = wide.dropna().index
    for name in STRATEGIES:
        rows = trades[trades.strategy.eq(name) & trades.forecast_at.isin(support)]
        metric = summary['trading']['strategies'][name]
        if len(rows) != metric['n_events'] or int(rows.direction.ne(0).sum()) != metric['n_positions']:
            raise ValueError('Trade CSV and summary support disagree: ' + name)
        if metric['compound_net_event_return'] is not None:
            actual = float(np.prod(1 + rows.net_return) - 1)
            if not np.isclose(actual, metric['compound_net_event_return'], atol=1e-10, rtol=1e-9):
                raise ValueError('Trade CSV and summary metric disagree: ' + name)
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()}
    return summary, predictions, trades, example, protocol, hashes


def figure(directory, summary):
    forecast, market = summary['forecast'], summary['trading']
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'pdf.fonttype': 42, 'ps.fonttype': 42})
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 4.7), gridspec_kw={'width_ratios': [1, 1.3]})
    fig.subplots_adjust(left=.065, right=.985, bottom=.23, top=.72, wspace=.25)
    fig.suptitle('Basic corn model: forecast improvement did not carry through', x=.065, ha='left',
                 y=.965, fontsize=16, fontweight='bold', color='#172b3a')
    years = forecast['evaluation_years']
    fig.text(.065, .865, f"{min(years)}–{max(years)}  ·  {forecast['n_events']} issue dates  ·  {forecast['n_years']} calendar years  ·  exploratory research",
             color='#4b5961', fontsize=10.5)
    colors = {'usda': '#52616b', 'bias': '#bec5c9', 'weather': '#8198a6', 'satellite': '#bd632d',
              'always_long': '#8198a6', 'always_short': '#bec5c9'}
    left, right = axes
    values = [forecast['models'][name]['rmse_bu_acre'] for name in MODELS]
    left.bar(np.arange(4), values, width=.65, color=[colors[k] for k in MODELS])
    left.set_xticks(np.arange(4), ['USDA\nunchanged', 'Past mean\nrevision', 'Weather', 'Weather +\nsatellite'])
    left.set_ylabel('Yield RMSE (bushels/acre)')
    left.set_title('Next calendar-month USDA yield\nLower is better', loc='left', fontsize=11, pad=12)
    left.set_ylim(0, max(values)*1.24)
    left.grid(axis='y', alpha=.18)
    left.set_axisbelow(True)
    for x, value in enumerate(values):
        left.text(x, value+.05, f'{value:.3f}', ha='center', va='bottom', fontsize=10)
    comp = [market['strategies'][name]['compound_net_event_return'] for name in STRATEGIES]
    valid = [np.nan if x is None else x for x in comp]
    right.bar(np.arange(6), valid, width=.64, color=[colors[k] for k in STRATEGIES])
    right.axhline(0, color='#52616b', linewidth=.8)
    right.set_xticks(np.arange(6), ['USDA\n(cash)', 'Past mean\nrevision', 'Weather', 'Weather +\nsatellite', 'Always\nlong', 'Always\nshort'])
    right.set_ylabel('Compounded net event return')
    right.yaxis.set_major_formatter(PercentFormatter(1))
    right.set_title('CORN ETF paper positions after costs\nEvent windows only; not annualized', loc='left', fontsize=11, pad=12)
    right.grid(axis='y', alpha=.18)
    right.set_axisbelow(True)
    finite = np.asarray(valid, float)
    finite = finite[np.isfinite(finite)]
    if len(finite):
        span = max(float(np.max(finite)-np.min(finite)), .1)
        right.set_ylim(min(0., float(np.min(finite)))-span*.18, max(0., float(np.max(finite)))+span*.22)
        for x, value in enumerate(comp):
            if value is not None:
                right.text(x, value+(span*.025 if value >= 0 else -span*.025), f'{value:+.1%}',
                           ha='center', va='bottom' if value >= 0 else 'top', fontsize=9.5)
            else:
                right.text(x, 0, 'N/A', ha='center', va='bottom', fontsize=9.5)
    c = summary['configuration']
    n = market['strategies']['satellite']
    fig.text(.065, .075, f"Satellite strategy: {n['n_positions']} positions, {n['n_events']-n['n_positions']} cash decisions. "
             f"{c['entry_cost_bps']:g} bp per side; {c['annual_short_borrow_rate']:.0%} annual short borrow. "
             'USDA is the reference, not market consensus.', color='#4b5961', fontsize=9)
    fig.savefig(directory / 'corn_model_results.png', dpi=200, metadata={'Software': 'Matplotlib'})
    fig.savefig(directory / 'corn_model_results.pdf', metadata={'Title': 'Basic corn model: fixed historical diagnostic',
                                                               'Author': 'alphaHunt', 'CreationDate': None, 'ModDate': None})
    plt.close(fig)


def render_report(directory, summary, predictions, trades, example, protocol, hashes):
    f, t, c = summary['forecast'], summary['trading'], summary['configuration']
    sat, weather = t['strategies']['satellite'], t['strategies']['weather']
    years = f['evaluation_years']
    frows = [[LABELS[k], f"{f['models'][k]['rmse_bu_acre']:.4f}", f"{f['models'][k]['mae_bu_acre']:.4f}"] for k in MODELS]
    crows = [[LABELS[k], percent(v['rmse_reduction'], True), interval(v['conditional_ci95'])]
             for k, v in f['satellite_comparisons'].items()]
    trows = [[LABELS[k], v['n_events'], v['n_positions'], v['n_events']-v['n_positions'],
              percent(v['mean_net_event_return'], True), percent(v['compound_net_event_return'], True),
              percent(v['double_cost_compound_return'], True)] for k in STRATEGIES for v in [t['strategies'][k]]]
    available = [(k, v) for k, v in t['strategies'].items() if v.get('mean_annual_event_return_ci95') is not None]
    uncertainty = ''
    if available:
        uncertainty = '\n\n' + table(['Strategy', '95% interval: mean event return', '95% interval: advantage over weather'],
                       [[LABELS[k], interval(v['mean_annual_event_return_ci95']), interval(v.get('paired_mean_advantage_vs_weather_ci95'))]
                        for k, v in available])
    else:
        uncertainty = '\n\nThe saved trading summary does not yet contain market uncertainty intervals; no interval is inferred from the plotted compound return.'
    usda = example['models']['usda']
    example_sat = example['models']['satellite']
    issue = pd.Timestamp(example['forecast_at']).date().isoformat()
    ready = predictions[predictions.status.eq('ready')]
    coverage = f"{ready.coverage.min():.1%}–{ready.coverage.max():.1%}"
    footprint = f"{ready.prior_area_footprint.min():.1%}–{ready.prior_area_footprint.max():.1%}"
    training_abstentions = int(predictions.status.ne('ready').sum())
    text = f'''# Basic corn model: usable research pipeline, negative first result

The fixed model forecasts the next calendar-month USDA national corn-yield revision, then translates the forecast into a costed CORN ETF paper position. **This first test did not establish a forecast or trading advantage.** Satellite RMSE was {f['models']['satellite']['rmse_bu_acre']:.4f} bushels/acre, versus {f['models']['usda']['rmse_bu_acre']:.4f} for leaving USDA unchanged and {f['models']['weather']['rmse_bu_acre']:.4f} for weather. The satellite rule took {sat['n_positions']} positions across {sat['n_events']} issue dates and compounded to {percent(sat['compound_net_event_return'], True)} after costs; weather returned {percent(weather['compound_net_event_return'], True)} and always-long {percent(t['strategies']['always_long']['compound_net_event_return'], True)}. No parameters were tuned to these results.

![Forecast errors and net paper returns](corn_model_results.png)

The figure shows point estimates. The intervals below measure conditional sampling uncertainty; they do not remove the effects of prior research selection or revised source data.

## Run the model

From the repository root:

```sh
python -m src.corn_model backtest
python -m src.corn_model forecast --as-of 2023-09-15
python -m src.corn_model forecast --as-of 2026-09-15
python -m src.corn_model_report
```

The backtest reproduces the frozen historical test offline. The 2023 command returns a historical research card. **The 2026 command must abstain:** county inputs stop at {pd.Timestamp(summary['latest_county_forecast_at']).date().isoformat()}. Fresh USDA and market prices cannot replace missing current-year satellite/weather observations. No forecast is silently carried forward, no order is submitted, and these commands create no recurring automation.

## What the four forecasts mean

The anchor is the latest same-crop-year USDA WASDE report published before August 15 or September 15 at noon UTC. The target is the first report in the **next calendar month**, not the next arbitrary later report, end-of-season yield, a county yield, or a futures price. A cancelled report stays unlabeled. USDA is a public reference forecast, **not analyst consensus**.

| Model | Definition |
| --- | --- |
| USDA unchanged | Carry forward the latest public national yield estimate; predict a zero revision. |
| Past mean revision | Add the same-horizon average revision from released earlier years. |
| Weather | Calibrate the covered-county weather-minus-trend anomaly plus an issue-month indicator to national revisions. |
| Weather + satellite | Add the covered-county satellite-minus-weather increment to the same weather calibration. |

Both fitted models use training-only standardization and ridge alpha {c['ridge_alpha']:g} with an unpenalized intercept. Calibration uses strictly earlier harvest years with released target reports, at least {c['min_training_years']} distinct years and {c['min_training_rows']} outcomes. The first five source years warm up the model; {training_abstentions} early issue rows abstain rather than report fitted predictions. Evaluation is chronological {min(years)}–{max(years)} on {f['n_events']} matched issue dates across {f['n_years']} years. August and September observations within a year are related, so they are not twelve independent seasons.

The county inputs previously improved county-yield forecasts. That result does **not** imply that their aggregate will predict a national USDA report revision or a price surprise. The covered-county proxy is calibrated rather than treated as a direct national-yield estimate. In this evaluation, it covers {coverage} of reported prior-year area in the eligible county universe, corresponding to {footprint} of USDA prior-year national harvested area. Those are different denominators; coverage is not all-US crop coverage.

## Forecast scores

RMSE and MAE use equal calendar-year weighting, in bushels/acre. Lower is better.

{table(['Model', 'RMSE', 'MAE'], frows)}

A positive reduction below favors satellite; a negative number means satellite is worse.

{table(['Compared with', 'Satellite RMSE reduction', 'Conditional 95% interval'], crows)}

Satellite beats the weaker historical-mean-revision control but does not beat unchanged USDA or weather in this sample. The paired uncertainty uses {c['bootstrap_draws']:,} circular bootstrap draws of {c['bootstrap_year_block']}-calendar-year blocks, seed {c['random_seed']}. With only {f['n_years']} year clusters, these intervals are descriptive and fragile. Earlier research already examined related 2013–2023 inputs and returns; this is not an untouched final holdout and no correction for that research search is claimed.

## Fixed paper-trading rule

A predicted yield more than {c['yield_signal_threshold_bu_acre']:g} bushels/acre below the latest USDA estimate produces a long paper position; more than that amount above produces a short; otherwise the model holds cash. This is a supply hypothesis relative to USDA, not an estimated return probability or surprise relative to market expectations. Unchanged USDA is always cash by construction, which is why its zero return is not a successful active strategy.

The instrument is **CORN, a multi-maturity corn-futures ETF proxy**. Authentic individual December-contract history was not available from the checked free endpoints; no continuous-contract price series is mislabeled as December corn. Entry is the first New York trading session on a strictly later date than the issue, at the daily close; exit is {c['holding_sessions']} sessions after entry. Exposure is one initial notional, with {c['entry_cost_bps']:g} bp entry cost, {c['exit_cost_bps']:g} bp exit cost and {c['annual_short_borrow_rate']:.0%} annualized calendar-day borrow for shorts. Missing required quotes or unfinished windows abstain. The saved event windows do not overlap; no overlapping full-notional returns are compounded.

{table(['Strategy', 'Events', 'Positions', 'Cash', 'Mean net/event', 'Compounded event net', 'Double-cost compound'], trows)}

Cash events remain in the event mean and compound path; they are not omitted to inflate average returns. Double cost doubles execution fees only and preserves the borrow rate. These are event-window returns, not annualized performance, a risk-adjusted alpha estimate, or continuous investment returns. Fund expenses and rolling effects are already in ETF prices. Borrow availability, actual bid/ask spreads, taxes and capacity are not independently verified.{uncertainty}

Market intervals, when present, bootstrap the paired annual means of event returns using the same calendar-year block scheme. They describe **mean event returns**, not confidence bands around compounded returns. No profitable strategy or trading alpha is verified.

## Historical example: {issue}

The latest available USDA estimate was {example['usda_yield_bu_acre']:.1f} bushels/acre. The satellite model projected {example_sat['predicted_yield_bu_acre']:.3f} for the {example['target_month']} report, a {example_sat['predicted_revision_bu_acre']:+.3f} revision. That difference was below the {c['yield_signal_threshold_bu_acre']:g} threshold, so the paper signal was **{example['paper_signal']['bias']}**, with no position.

Holding USDA harvested area and all other balance-sheet items fixed, the revision implies {example_sat['conditional_production_change_m_bu']:+.1f} million bushels of production and ending stocks, and a conditional stocks-to-use ratio of {example_sat['conditional_stocks_to_use']:.2%}. This is a conditional arithmetic scenario, not an independently validated production, demand or stocks forecast. The model preserves the published USDA production rounding and changes it by yield revision times stated harvested area.

## Sources, timing and reproduction

USDA observations preserve the published WASDE vintage and publication time; county features come from current revised archives with a static 2021 crop map. A 2021 map reference year does not establish that it was publicly available in 2021. The weather baseline is a reanalysis-based proxy and is not exclusively ground observations. These source limitations prevent an original-vintage operational backtest claim, even though calibration uses prior labels and issue-time information checks. Market adjusted closes are also a currently retrieved historical snapshot, not verified executable historical adjusted prices.

The frozen specification is in [protocol.json](protocol.json), and the exact numerical configuration is in [../../config/corn_model.json](../../config/corn_model.json). Source snapshots and manifests are under [inputs](inputs); market execution assumptions and provenance are described in [../../docs/notes/corn_market_sources.md](../../docs/notes/corn_market_sources.md). Raw output files are [predictions.csv](predictions.csv), [paper_trades.csv](paper_trades.csv), [historical_example.json](historical_example.json) and [summary.json](summary.json). A vector version of the figure is [corn_model_results.pdf](corn_model_results.pdf).

The renderer only reads saved output files and checks that forecast/trade metrics match their CSV rows. It does not fetch data, refit models or choose parameters. `report_manifest.json` records the input and output hashes for this rendering. Refreshing inputs requires explicitly rebuilding and reviewing dependent results; it cannot make the county data extend beyond their actual last year.
'''
    (directory / 'report.md').write_text(text)


def run(directory=DEFAULT):
    directory = Path(directory)
    summary, predictions, trades, example, protocol, hashes = load_outputs(directory)
    figure(directory, summary)
    render_report(directory, summary, predictions, trades, example, protocol, hashes)
    output_names = ('report.md', 'corn_model_results.png', 'corn_model_results.pdf')
    manifest = {'renderer_sha256': digest(__file__), 'source_hashes': hashes,
                'output_hashes': {name: digest(directory / name) for name in output_names},
                'forecast_events': summary['forecast']['n_events'],
                'evaluation_years': summary['forecast']['evaluation_years'],
                'trading_alpha_verified': False, 'no_new_fit_or_parameter_selection': True}
    (directory / 'report_manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results-dir', type=Path, default=DEFAULT)
    args = parser.parse_args()
    print(json.dumps(run(args.results_dir), indent=2))
