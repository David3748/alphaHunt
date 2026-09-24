# Fresh confirmation protocol

Frozen before building cases or inspecting outcomes: 2026-08-22.

## Objective

Confirm or reject the two surviving hypotheses from the five-strategy batch on
historical dates and cases not used by any prior alphaHunt experiment.

## Samples

- Safety-first: 80 severe-drawdown common stocks with cutoffs from 2021-01-01
  through 2023-12-31, seed 811, built by `long_lab.py`.
- Dilution-event-conditioned: 80 financing-risk filing cases over the same date
  range, seed 812, built by `ox_lab.py`.
- Prior experiments begin in 2024, so the time window cannot overlap them.
- Current-listing survivorship bias remains and will be reported as a limitation.

## Locked model procedure

- Model: `stealth/ox-alpha`.
- Five specialist lenses per case: liquidity, operations, catalysts, accounting,
  and governance.
- One extraction call per lens.
- Two independent synthesis calls per case.
- Only exact-quote-grounded claims enter synthesis.
- No prompt or score changes after outcomes are inspected.

Target new calls for 160 completed cases: 800 extraction calls and 320 synthesis
calls, or 1,120 calls total.

## Locked strategies

1. Safety-first: within the severe-drawdown cohort, rank by the negative mean
   predicted downside-tail probability. Buy the top 10%.
2. Dilution-event-conditioned: within the financing-event cohort, rank by mean
   probability of +20% 90-day excess return. Buy the top 10%.

## Locked evaluation

- Primary metric: equal-weight top-decile 90-day excess return versus SPY.
- Secondary metrics: median excess return, +20% success rate, top-versus-rest
  spread, and 20,000-draw one-sided permutation p-value.
- No costs, position overlap, capacity, or next-session execution adjustment is
  available in the legacy outcomes; this is a signal confirmation, not a tradable
  portfolio backtest.
- Decision rule: do not call a strategy confirmed unless direction is positive
  and permutation p < 0.05. With two primary hypotheses, also report the stricter
  Bonferroni threshold of 0.025.

