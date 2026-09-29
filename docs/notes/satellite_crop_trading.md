# Wheat satellite forecasts: fixed-rule market check

Satellite forecast accuracy did not establish a profitable trading strategy in
this test. The corrected 2018–2024 weather and weather-plus-NDVI models choose the
same direction in every year; both lose 19.22% compounded across seven annual
holding windows after costs. Trading only the direction of the satellite
revision loses 0.28% compounded. In the separate corrected 2025 exploratory
check, satellite inclusion changes a profitable short into a losing long.

This test was specified before inspecting these market outcomes. Wheat heading
was selected during the preceding forecast research, and an invalid-stage 2025
forecast had already been inspected before the stage-ordering bug was fixed.
Neither sample is presented as a pristine strategy holdout. No parameter was
tuned to improve these market results.

## Fixed rule

Use the production-weighted average predicted wheat yield anomaly at heading,
across the 12 configured states. The required universe is frozen as CO, ID, KS,
MN, MT, ND, NE, OK, OR, SD, TX and WA; omitting a state from both models also
causes abstention. Trade short for a positive anomaly and long for
a negative anomaly. Compare weather-only with weather-plus-NDVI on a common
availability date equal to the latest input forecast availability across states
and models. The separate incremental strategy takes the negative sign of the
satellite forecast minus the weather forecast. An always-long control uses the
same dates and costs. Realized yield outcomes are never used to set positions.

Enter at the first Friday strictly after the forecast's `available_date`. Exclude
the return labelled with that entry Friday: it describes the preceding period.
Hold the following 12 weekly observations, using contract-consistent CBOT wheat
returns from the existing USDA ERS settlement cache. These returns hold the
selected contract through each interval, so changing contract prices does not
produce a spurious roll gain. Friday is the existing data's weekly boundary;
actual holiday execution and daily margin paths are not reconstructed.

Exposure is 1x collateral, rebalanced weekly; cash earns zero. Deduct 25 basis
points on entry and exit and 5 basis points for each flagged roll during the
hold. Deduct the cost from the affected weekly return before compounding.
Missing state forecasts, required returns, or roll flags cause abstention rather
than gap filling. The seven research windows do not overlap.

## Results after costs

| Strategy | 2018–2024 mean per event | Compounded across seven events | Positive events | 2025 exploratory |
|---|---:|---:|---:|---:|
| Weather-only | −2.58% | −19.22% | 2 / 7 | +9.32% |
| Weather + NDVI | −2.58% | −19.22% | 2 / 7 | −9.91% |
| Incremental satellite revision | +0.33% | −0.28% | 4 / 7 | −9.91% |
| Always-long control | −0.75% | −7.97% | 3 / 7 | −9.91% |

The paired satellite-minus-weather return difference is exactly zero for every
2018–2024 event, because the predicted anomaly signs agree. The exact paired
sign-flip diagnostic consequently has p=1. The satellite model's 2025 direction
loses 19.23 percentage points relative to weather-only over the same interval.
That year is reported separately, not pooled into a new optimized rule.

Seven annual crop episodes are seven independent research units at most, not
84 independent weekly observations. We report no annualized Sharpe from these
sparse event windows. Compounded return assumes zero-return cash between annual
trades; it is neither annualized nor a continuously invested benchmark.

## Reproduce offline

The corrected main prediction file used here has SHA-256
`451246f1faa4fcdfbb83b57ae4a43c2504b452f3460849c81be2be23e9733fe5`.
The market snapshot contains the exact wheat return and roll-flag series used;
`summary.json` records source parquet hashes and the inherited ERS manifest,
including its raw settlement-file hash and source URL.

```sh
python3 src/satellite_crop_trading.py \
  --predictions results/satellite_validation/third_signal/predictions.csv \
  --market-snapshot results/satellite_validation/trading/weekly_wheat_inputs.csv \
  --confirmation results/satellite_validation/third_signal/confirmation_predictions_2025.csv \
  --output-dir work/satellite_crop_trading_replay
python3 -m pytest -q tests/test_satellite_crop_trading.py
```

`signals.csv` records predictions and directions; `trades.csv` records every
research trade and charged roll; `confirmation_signals.csv` and
`confirmation_trades.csv` keep 2025 separate. Nine tests check timing, return
exclusion, horizon, cost accounting, weighting, outcome independence, missing
observations and all-abstention handling.

Historical availability remains the upstream forecast's assumption. The input
data lack original-vintage satellite publication proof and use revised public
weather/yield inputs and retrospective state weights. This check therefore sets
`strict_point_in_time_certified=false`. It establishes the negative result for
these fixed rules on the supplied data; it does not prove every possible
satellite crop strategy fails, nor does the forecast's modest accuracy gain
demonstrate investment alpha.
