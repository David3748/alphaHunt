# Sealed survivorship-reduced safety validation protocol

Frozen: 2026-08-22, before the event universe was built, model calls were made,
or any post-signal outcomes were queried.

## Objective

Test whether the safety-first LLM score supports a causal, executable long
strategy in a new historical sample that does not start from today's listings
and does not require future price availability for case eligibility.

## Sealed period and population

- Filing dates: 2019-01-01 through 2020-12-31.
- Population: every SEC Financial Statement Data Set submission with form 10-K,
  10-Q, 20-F, or 40-F and a detailed XBRL filing in the eight quarterly files.
- Historical identifiers come first from the filing's own DEI `TradingSymbol`
  and `SecurityExchangeName` facts. Early filings that predate mandatory
  cover-page ticker tagging use the filing-native XBRL instance stem as a
  fallback, validated by pre-signal price coverage. Current SEC ticker maps are
  not an eligibility source. Every unresolved mapping is counted and retained
  in the coverage audit.
- Repetitive filings with the same CIK and normalized XBRL-instance filename
  stem share one filing-native DEI resolution. A changed stem forces a new fact
  fetch, preserving likely ticker-change regimes without retaining duplicate
  multi-megabyte instances.
- Amendments are excluded. Duplicate CIK/accession records are deduplicated.
- At most one eligible event per CIK in a rolling 180-calendar-day interval.

## Eligibility known at the signal

- At least 120 daily closes in the preceding 370 calendar days.
- Last adjusted close at least $1.
- Mean 30-session dollar volume at least $1 million.
- Drawdown from the preceding 370-day adjusted-close high is at least 40%.
- Price/exchange lookup failure is recorded, never silently treated as an
  ineligible surviving company.
- No post-filing close or 90-day outcome is required to enter the case set.

## Model procedure

- Model: `stealth/ox-alpha`.
- Evidence: the anchor filing plus up to four prior SEC filings available in the
  preceding 180 days, frozen at exact SEC acceptance time.
- Five one-replicate grounded extractors: liquidity, operations, catalysts,
  accounting, and governance.
- Two independent syntheses per case.
- Safety score: negative mean predicted downside-tail probability.
- Exact-quote grounding is required for normalized claims.

## Causal trading rule

- Sort events by exact SEC acceptance time.
- Warm up on the first 50 scored events without trading.
- For each later event, calculate the 90th percentile of safety scores from
  strictly earlier acceptance dates only. Buy when the new score is at or above
  that threshold. Same-timestamp events cannot inform one another.
- Enter at the first available adjusted close after SEC acceptance.
- Target 10% of current NAV per new position; maximum ten open positions.
- Hold 90 calendar days.
- Charge 25 basis points per side.
- Idle cash earns the historical 13-week Treasury yield.
- Benchmark is an SPY/cash blend matched to daily equity exposure.

## Missing terminal histories and delistings

Run two immutable bounds for a price series ending before the planned exit:

1. Conservative: mark the position to zero at the first confirmed terminal gap.
2. Optimistic: carry the last observable adjusted close through planned exit.

The primary verdict uses the conservative bound. Both counts and outcomes are
reported. Ticker changes or acquisitions not resolved by the free source remain
inside this bracket rather than being dropped.

## Locked evaluation and decision rule

Report trade count, CAGR, T-bill Sharpe, maximum drawdown, matched information
ratio, beta/alpha, win rate, year/regime results, score buckets, holding-period,
delay, cost and sizing sensitivity, leave-cluster-out results, HAC uncertainty,
block/trade bootstrap, and random-score placebo portfolios.

Call the strategy validated only if all of the following hold under the
conservative terminal-history rule:

- At least 30 executed trades after warm-up.
- T-bill Sharpe greater than 1.0.
- Exposure-matched information ratio greater than 0.5.
- Maximum drawdown no worse than -25%.
- Positive exposure-matched excess return in both 2019 and 2020 trading cohorts.
- One-sided random-score placebo p < 0.05.
- No single signal month accounts for more than half of total excess profit.

This protocol will not be changed after outcomes or model scores are opened.
