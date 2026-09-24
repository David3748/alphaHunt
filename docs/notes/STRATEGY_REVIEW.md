# Strategy review and signal screen — sealed 2019–2020 run

Analysis date: 2026-08-22. Source: `lab_runs/sealed_safety/` (`results.json`,
`syntheses.jsonl`, `extractions.jsonl`, `outcomes.jsonl`, `preprice.jsonl`).

## Status: EXPLORATORY, NOT VALIDATED

Everything below was computed **after** `lab_runs/sealed_safety/` outcomes were
opened. Roughly 60 hypotheses were tested against known results. This is exactly
what the sealed protocol exists to prevent, so nothing here is a finding — it is
hypothesis generation for the century run. Do not deploy anything in this
document without pre-registering it first.

Two methodological differences from the official evaluation, so numbers here are
**not** directly comparable to `results.json`:

- Entry is modeled at the cutoff-date close, not the acceptance-time next close.
- The headline metric is mean per-trade excess vs SPY, not exposure-matched
  information ratio, and it ignores costs, cash yield, and compounding.

They are consistent across rules, so relative comparisons hold; absolute levels
do not match the official run.

## Headline finding: the deployed ranker is the weakest of the four available

The strategy ranks on `downside_tail_probability_pct`. Every other synthesis
output already stored in `syntheses.jsonl` is a stronger ranker. Screened across
all 1,713 cases against 90-day excess return (49 features, Benjamini-Hochberg FDR):

| ranker | Spearman | AUC | FDR q |
| --- | ---: | ---: | ---: |
| `probability_plus20_excess_90d_pct` | **+0.271** | **0.634** | <0.001 |
| `expected_excess_return_90d_pct` | +0.253 | 0.629 | <0.001 |
| `probability_positive_excess_90d_pct` | +0.225 | 0.616 | <0.001 |
| `decision` (reject/watch/long_candidate) | +0.210 | 0.604 | <0.001 |
| `downside_tail_probability_pct` **(deployed)** | +0.144 | 0.574 | <0.001 |

Outcome distribution for reference: n=1,713, mean excess +13.3%, median +2.5%,
sd 66.6%, base rate of positive excess 53.9%.

### The deployed ranker is not monotone; `p_plus20` is

Quintiles of the whole eligible universe, low to high:

| quintile | `p_plus20` mean / win% | deployed safety score mean / win% |
| --- | ---: | ---: |
| Q1 (low) | −3.3% / 34.5% | +7.2% / 39.5% |
| Q2 | +9.8% / 48.8% | +10.8% / 53.2% |
| Q3 | +15.9% / 56.1% | **+20.3%** / 55.3% |
| Q4 | +14.7% / 61.1% | +15.8% / 61.7% |
| Q5 (high) | **+29.1%** / **69.0%** | +12.2% / 60.0% |

`p_plus20` is monotone on median and win rate across all five buckets (median
−10.6% → +13.3%, win rate 34.5% → 69.0%). The deployed score peaks in Q3 and
falls back.

### The deployed ranker had negative selection value in 2020

Top-quintile minus rest, by signal year:

| ranker | 2019 spread | 2020 spread |
| --- | ---: | ---: |
| `p_plus20` | +7.6% | **+14.0%** |
| deployed safety score | +5.5% | **−7.9%** |

2020 produced all of the reported alpha. In that year the deployed ranking was
worse than not ranking at all — the +19.7% mean excess of the *non-selected*
names beat the +16.3% of the selected top quintile. The reported 2020 result came
from the universe and the crash timing, not from the ranking.

### Portfolio simulation under the existing rule

Causal expanding 90th percentile, 50-case warmup, 10 slots at 10% NAV, median of
80 tie-break orderings:

| ranking rule | signals | trades | mean excess | win% | exposure | portfolio excess | p vs random |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| deployed (−downside prob) | 188 | 60 | 11.1% | 61.7% | 85% | 9.4% | 0.100 |
| **prob(+20% excess)** | 242 | 54 | **23.6%** | 63.0% | 87% | **20.5%** | 0.013 |
| expected excess return | 230 | 54 | 23.0% | **64.8%** | 85% | 19.5% | 0.013 |
| blend of all 4 (equal z) | 226 | 53 | 22.1% | 64.2% | 85% | 18.7% | 0.013 |
| safety × upside | 236 | 54 | 18.8% | 63.0% | 85% | 15.9% | 0.023 |
| **upside × deep drawdown** | 227 | 56 | **35.8%** | 51.8% | 87% | **31.2%** | 0.007 |
| deep drawdown only | 189 | 59 | 24.4% | 51.7% | 86% | 21.0% | 0.013 |
| prob(positive excess) | 236 | 55 | 15.5% | 63.6% | 86% | 13.4% | 0.053 |
| decision score | 304 | 57 | 14.1% | 61.4% | 89% | 12.5% | 0.066 |
| *random ranking* | — | — | *4.0%* | — | — | — | — |
| *own the entire eligible universe* | — | — | *13.3%* | — | — | — | — |

The deployed rule (11.1%) underperforms simply owning every eligible name (13.3%).
Random ranking's 5–95% band is −3.1% to +15.1%, 99th percentile +21.3%.

There is a clean risk axis: upside-based rules give ~23% excess at a ~64% hit
rate; adding drawdown depth gives 35.8% at a coin-flip hit rate.

## Mechanical baselines the placebo does not cover

The random-score placebo answers "beats random." It does not answer "beats one
line of SQL." Same universe, same selection rule:

| ranking | mean excess | win rate |
| --- | ---: | ---: |
| LLM safety score | +11.4% | **61.7%** |
| **deeper drawdown** | **+24.2%** | 51.7% |
| higher share price | +11.6% | 41.9% |
| random | +4.6% | ~50% |
| log dollar volume | −4.1% | 39.7% |

Ranking by *deepest* drawdown more than doubled the LLM's excess. What the LLM
uniquely delivers is hit rate — 61.7% against 40–52% for every mechanical rule.
It does what it was asked (avoid losers); avoiding losers was not the winning
trade in a V-shaped recovery. **Recommend promoting mechanical baselines to
first-class gates.**

## Weaknesses in the sealed 2019–2020 result

1. **All alpha is 2020.** 2019: exposure-matched IR **0.0125**, matched excess
   **+0.13% annualized**, HAC t=0.03, p=0.98 — indistinguishable from zero.
   2020: IR 1.55, excess +22.8%, t=2.58.
2. **The `positive_year_coverage` gate tests a sign, not a magnitude.** It passed
   on 13 basis points. This is the weakest link in the frozen protocol.
3. **Eligibility acts as a crash detector.** 98 of 188 signals fired in
   March–May 2020 (55 in May alone). "−40% off the 370-day high, liquid, >$1"
   describes most of the market at once during a crash, so slots fill at the
   bottom by construction. Pays in V-shaped recoveries; bleeds in grinding bears.
   The sample contains one crash and it was the best-case one.
4. **90 days sits on the peak of the hold curve.** Matched IR 0.95 at 60d → 1.27
   at 90d → 0.82 at 120d. Pre-committed and baked into the synthesis prompt, so
   not a protocol violation, but the effect is not horizon-robust. At 180d max
   drawdown blows out to −39%/−43%, so the tidy −14.3% partly reflects 2019
   positions exiting before COVID and 2020 positions being bought after it.
5. **The score barely resolves.** 64 distinct values across 1,713 cases, clustered
   on round numbers (99 cases at exactly 18%, 85 at 16.5%, 63 at 15%). The decile
   threshold lands at −0.155 inside a 31-case tie block. Mean replicate
   disagreement is 4.2 points against a useful spread of ~10 points; only 23% of
   replicate pairs agree exactly. Inside the selected set the ranking is one
   bucket with hash tie-breaks.
6. **Short-term tax is unmodeled.** Every trade is a 90-day hold, so all gains are
   short-term. At a 24–32% marginal rate the +22.1% mean trade is ~+15–17% after
   tax, every year. Larger drag than the 25 bp cost assumption that *was* modeled.

## Tested and found NOT to be problems — do not re-litigate

- **Tie-break / queue path variance.** 128 of 188 selections were skipped by the
  10-slot cap in SHA-256 `case_id` order. Across 400 random orderings, mean trade
  return spans only 19.8%–22.8% (p5–p95) and **zero** orderings produce negative
  excess. The signal pool is large and homogeneous; which 60 you get does not
  matter. Robust.
- **Relaxing the position cap makes it worse.** Per-trade excess holds up as slots
  are added, but 1/N sizing means more slots equals more idle cash:

  | slots | size | signals used | mean excess | avg exposure | portfolio excess |
  | ---: | ---: | ---: | ---: | ---: | ---: |
  | 10 | 10% | 32% | 11.4% | 85% | **≈9.7%** |
  | 20 | 5% | 49% | 10.0% | 69% | ≈6.9% |
  | 30 | 3.3% | 60% | 13.8% | 56% | ≈7.8% |
  | 50 | 2% | 74% | 14.5% | 42% | ≈6.1% |
  | uncapped | 0.5% | 100% | 12.0% | 15% | ≈1.8% |

  Signals arrive in bursts, so the cap is what keeps capital deployed. 10 slots is
  near-optimal. The cap is load-bearing, not a leak.
- **Capacity / liquidity.** Not a constraint at personal-account size. The $1M ADV
  floor is a *choice*, not a limit — see below.

## Recommended next actions, ranked

1. **Pre-register secondary rankers into the century `PROTOCOL.md` before stage 7
   opens outcomes.** Proposed set: `p_plus20`, the equal-weight 4-way z-blend, and
   `upside × drawdown`. Three rules, Bonferroni α=0.0167. **This costs zero
   additional model calls** — all four synthesis fields are already written to
   `syntheses.jsonl` for every case the century run produces. It converts
   2009–2025 into a genuine out-of-sample test of all three, for free.
2. **Get a bear regime.** 2009–2012 is the whole question. The century run already
   covers it. If the signal survives a grinding bear it is a strategy; if not, it
   is a dip-buying rule fit to the best dip in history.
3. **Promote mechanical baselines to gates.** The LLM must beat drawdown-rank,
   share price, and a cheap quality screen — not just random.
4. **Fix the year gate** to require economically meaningful excess, not a positive
   sign.
5. **Force score resolution** — more synthesis replicates, or rank-based selection
   instead of a percentile threshold on a lumpy discrete variable.
6. **Consider lowering the $1M ADV floor.** It rejected 5,055 filings, and since
   ADV is screened *before* drawdown, roughly 900 would likely have been eligible
   — a ~50% larger universe, spread across quiet periods, which attacks the
   cash-drag problem at its source. Requires a re-run (no cases were ever built
   for those names). Caution: micro-caps concentrate delisting, fraud, and bad
   price data.

## Untested ideas worth a look

- **Breadth overlay.** Count eligible filings in the trailing 30 days as a
  causally-available market-stress gauge and scale exposure to it. Turns the
  accidental crash-detector property into a deliberate one.
- **Bottom decile as a short or avoid leg.** Q1 at 34.5% win rate is arguably a
  stronger signal than Q5.
- **Sector-neutral ranking.** SIC codes are in `preprice.jsonl`. 2020 was a large
  sector-rotation year and that is likely a meaningful share of the measured edge.
- **Multi-horizon.** 30/60/90/120/180d outcomes are already computed — horizon is
  free to test.
- **The extraction layer is nearly dead weight as a ranker.** Best single feature
  was bearish operating-trend claim count at ρ=+0.107 — and note the sign, *worse*
  operations predicted *higher* returns. If that holds, four of the five lenses
  are not earning their calls. Worth a dedicated ablation before the century run
  spends ~5 calls per case on them.
- **Control for verbosity.** `n_uncert` (+0.155), `catalyst_len` (+0.105) and
  `thesis_len` (+0.055) all correlate positively with returns, which smells like a
  length artifact rather than signal. Worth a control test before trusting any of
  them.

## Reproduction

All analysis was ad-hoc against the artifacts listed at the top; no scripts were
committed. To rebuild: join `outcomes.jsonl` (`comprehensive_case_id` → outcome)
to `syntheses.jsonl` and `extractions.jsonl` on `case_id`, and to
`preprice.jsonl` on `(symbol, filed-date)`. Selection logic mirrors
`sealed_safety_eval.causal_select`; the portfolio walk mirrors
`safety_backtest.simulate` with a 10-slot cap.
