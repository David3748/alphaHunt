# Basic corn model

The model predicts the next monthly USDA corn-yield estimate using the latest
published USDA estimate, a covered-county weather forecast and the additional
vegetation signal. It converts the predicted yield revision into conditional
production and ending-stock scenarios and a transparent paper position.

The first evaluation is negative: 12 forecasts over six harvest years,
2018–2023. Satellite RMSE is 1.7745 bushels/acre, versus 1.7183 for retaining
USDA's last published yield and 1.7440 for the weather model. Its four nonzero
paper positions compound to −7.37% after costs across the 12 scheduled windows;
the other eight are cash. Parameters were not changed to improve that result.
[Full result and figure](../../results/corn_model/report.md).

## Run offline

From the repository root, after installing `requirements.txt`:

```bash
python3 -m src.corn_model backtest
python3 -m src.corn_model_report
python3 -m src.corn_model forecast --as-of 2023-09-15
python3 -m src.corn_model forecast --as-of 2023-09-15 --output work/corn-card.json
```

The date-only argument means **12:00 UTC**. Full UTC timestamps are also accepted.
The supported issue times are August 15 and September 15 at 12:00 UTC. Output
is strict JSON, with missing information represented by `null`, not a fabricated
zero. `backtest --output work/corn-check` writes a separate output directory.
The report renderer reads the standard results directory.

In the September 15, 2023 example, the latest USDA yield was 173.8 bu/ac and the
model predicted 174.2542 for October. The +0.4542 bu/ac difference falls within
the fixed 0.5 bu/ac band, so the paper decision is flat. This historical example
is not a current recommendation.

## What is fitted

The [fixed protocol](../../results/corn_model/protocol.json) and
[configuration](../../config/corn_model.json) define the model before the first
new forecast/price evaluation. Prior research had already inspected these years;
this is an exploratory forward replay, not an untouched final holdout.

1. For harvest year `y`, select the latest USDA report published **strictly before**
   the issue for corn marketing year `y/(y+1)`. Retain that report's harvested
   area, production, ending stocks and total use.
2. Form the weather proxy `(county weather forecast − county trend) / 0.0628` and
   vegetation addition `(county satellite forecast − county weather forecast) /
   0.0628`, converting the original tonnes/hectare values to bushels/acre. The
   three county forecasts share support and prior-year harvested-area weights.
3. Predict the change in national yield in the **first release in the next
   calendar month**. A cancelled month has no target; October 2013 is not filled
   from November. Unknown future outcomes do not block a forecast.
4. Fit standardized ridge regression with alpha 10 and an unpenalized intercept.
   Training uses only earlier harvest years whose target reports were published
   before issue. Require at least five years and eight outcomes. August and
   September share the fit, with a September indicator. Scaling uses only the
   training rows; no same-harvest-year outcomes train the September model.
5. Compare unchanged USDA, its historical same-horizon mean revision, USDA plus
   weather, and USDA plus weather and satellite. All calibrated models share
   training and evaluation support. No model selection is performed.

County coverage means the share of **reported eligible county area**, not the
share of all US corn acreage. A separate prior-area footprint compares covered
county acres with the latest available USDA prior-year national area. The model
learns a national adjustment; it never equates the county average with a national
yield. The satellite inputs retain the earlier study's revised archives, fixed
2021 crop map and assumed release lags.

For predicted yield change `dY` and current USDA harvested million acres `A`:

```text
conditional production = USDA production + A * dY
conditional ending stocks = USDA ending stocks + A * dY
conditional stocks/use = conditional ending stocks / USDA total use
```

Using the reported production anchor preserves official rounding when `dY=0`.
These scenarios hold harvested area, imports, beginning stocks and demand fixed.
They are not separate forecasts of those quantities. Negative or otherwise
invalid physical scenarios cause abstention.

## Paper signal and analyst consensus

The default reference is **latest USDA**, which is not analyst consensus or a
measure of what futures prices already reflect. Relative to the reference:

| Predicted yield difference | Paper position |
|---|---|
| Below −0.5 bu/ac | Long |
| Between −0.5 and +0.5, inclusive | Flat |
| Above +0.5 bu/ac | Short |

An optional `--consensus path/to/survey.json` accepts a user-supplied survey with
these required fields. Populate them from an actual dated source; no synthetic
survey is included with the historical evaluation.

| JSON field | Required content |
|---|---|
| `published_at` | ISO timestamp with timezone, strictly before the forecast |
| `marketing_year_start` | Integer harvest year matching the forecast |
| `target_month` | `YYYY-MM`, matching the forecast's target report month |
| `expected_yield_bu_acre` | Positive finite expected US national corn yield |
| `source` | Nonempty citation or identifier for the analyst survey |

The code validates metadata and timing, not the truth of a supplied citation.
It labels this reference `user_supplied_analyst_consensus`. Changing the reference
changes the paper decision, never the physical forecast or its calibration.
No historical market-consensus performance is claimed without those data.

The market diagnostic uses CORN, an ETF holding several corn-futures maturities.
It is not a December futures contract or spot corn. An authentic free history of
individual December contracts was not obtained in the bounded source check.
Enter at the close of the first New York trading date after issue; exit 20
sessions after entry. Positions use 1x notional, with 25 bp charged on each side
and 3% annual short borrow, plus a doubled-execution-cost sensitivity. Missing
quotes or incomplete windows abstain. Overlapping full-notional windows and
bankruptcy are never compounded into a misleading result.

Forecast losses average the available issue dates within each harvest year,
then weight the years equally. This national
model has only six evaluation-year clusters. Forecast and mean-return intervals
use paired two-calendar-year blocks and are conditional on this exploratory
study, with no adjustment for the wider research search. Compounded returns are
event-window results, not annualized returns or risk-adjusted alpha.

## Update inputs

USDA vintages extend through September 11, 2026, and the dated price snapshot
through September 25, 2026. County forecasts stop at September 15, 2023.

```bash
python3 -m src.corn_model forecast --as-of 2026-09-15
```

This returns `abstain`; the model never combines an old county forecast with a
new crop year's USDA balance sheet. To extend it, generate genuine forecasts
for the new year from the underlying county pipeline, preserving its window,
availability, coverage and prior-year-area rules. Supply them with
`--satellite-input path/to/new-county-aggregates.csv`. Required columns are:

```text
year,forecast_at,eligible,weather,satellite,trend,prior_area_coverage
```

Forecast values are in tonnes/hectare, `eligible` is boolean, and the issue must
be the matching harvest year's August/September 15 at 12:00 UTC. Optional
`forecast_prior_area_ha` enables the national footprint diagnostic. Extra rows
may extend but cannot silently replace existing issues. Imported forecasts are
user inputs; the model's checks do not certify their upstream provenance.

Explicit source refresh commands:

```bash
python3 -m src.corn_usda_data --fetch
# Optional calendar dependency is needed only for a market refresh:
pip install -r results/corn_model/inputs/market/refresh_requirements.txt
python3 -m src.corn_market_data --refresh
```

The offline loaders verify saved source hashes and reconstruct normalized data
from raw snapshots. Refreshes change input vintages deliberately; inspect the
diff and regenerate dependent results. Source details:
[USDA](corn_usda_sources.md), [market](corn_market_sources.md), and
[county forecast](satellite_cybench_validation.md).

## Files and checks

- `src/corn_model.py`: national calibration, forecast cards, paper decisions and evaluation.
- `src/corn_usda_data.py`, `src/corn_market_data.py`: audited source and execution adapters.
- `results/corn_model/`: fixed protocol, dated sources, all forecasts, trades, results and figure.
- `first_run_summary.json` and `first_run_source.py.txt`: exact first evaluation before subsequent reporting/guard improvements.
- `tests/test_corn_*.py`: timing, units, forward training, consensus, source integrity, market execution and independent arithmetic checks.

This version supplies a repeatable model and visible failure evidence. It has no
broker connection, automatic orders or validated live trading edge.
