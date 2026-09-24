# alphaHunt dataset and strategy analysis

Date: 2026-08-22

## Executive conclusion

The data infrastructure is working; the generic LLM long signal is not.

The completed store is internally consistent, point-in-time guarded, and rich enough to support narrower filing strategies. However, neither the original false-distress formulation nor the new comprehensive filing formulation passed the sealed long validation test. The model's outputs are consistent across replicates, but consistency did not translate into useful stock selection.

The most promising next hypothesis is not “buy distressed stocks the LLM likes.” It is an event-conditioned strategy: identify issuers around financing or dilution events, then buy only after filing evidence shows improved funding, a credible catalyst, and acceptable accounting/governance risk. That hypothesis still requires a new pre-registered, walk-forward test.

## Verification results

- Full unit suite: 48/48 passed.
- Full bitemporal integrity audit: passed with zero issues.
- SHA-256 verification: all 3,310 content objects passed.
- Manifest lookahead violations: zero.
- Orphan cases and predictions: zero.

Current store:

| Entity | Rows |
| --- | ---: |
| Content objects | 3,310 |
| Document versions | 2,159 |
| Grounded claim versions | 14,037 |
| Experiments | 3 |
| Experiment cases | 440 |
| LLM analyses | 2,740 |
| Predictions | 1,000 |
| Outcomes | 440 |
| Input manifests | 780 |
| Manifest items | 14,817 |

The raw corpus contains 1,490 primary SEC filings, 994 MB of original HTML, and 139 MB of derived clean text. Every listed accession was matched to SEC metadata. Exact acceptance-time enrichment exposed two cases where the old date-based knowledge cutoff was too early; the corrected comprehensive cases moved their cutoffs later by as much as 13.2 hours.

## Comprehensive experiment

The experiment used 220 historical cases, five specialist lenses, two extraction replicates per lens, and three synthesis replicates per case.

- Extraction calls: 2,200.
- Synthesis calls: 660.
- Total new calls: 2,860, with zero terminal failures.
- Proposed claims: 20,429.
- Exact-quote-grounded claims: 14,037 (68.7%).

Grounding was strongest for operations (75.3%) and liquidity (70.2%), and weakest for governance (65.1%). Rejected claims remain in the analysis audit trail but are excluded from prediction manifests.

### Live-artifact schema audit

The HTTP client has a free-form JSON fallback when the provider fails to return a
valid strict tool call. A post-run audit found that this fallback occasionally
omitted required narrative fields:

- All 660 forecasts contain a valid 0–100 `probability_plus20_excess_90d_pct`, so the ranked performance analysis is complete.
- 62 forecasts omit the narrative `catalyst` field.
- One forecast omits `downside_tail_probability_pct`; downside analysis uses the other two replicates for that case.
- Two extraction outputs omit their top-level confidence and one omits its redundant result-level role. The authoritative role remains present on every analysis row.
- Eight proposed claims omit `effective_date`; temporal ingestion conservatively assigns the case knowledge cutoff.

These gaps do not change the reported ranking metrics, but the runner should add
post-response schema validation before caching any future large run. “Strict” API
configuration should not be treated as proof of strict artifacts.

## Forecast performance

| Group | N | AUC | Rank correlation with return | Top-decile mean excess return | Top-decile success rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| All scored cases | 217 | 0.582 | 0.211 | +12.1% | 22.7% |
| Dilution-conditioned cohort | 117 | 0.629 | 0.265 | +28.4% | 41.7% |
| Generic false-distress cohort | 100 | 0.513 | 0.116 | -7.4% | 0.0% |
| False-distress development | 62 | 0.497 | 0.125 | -10.3% | 0.0% |
| Sealed false-distress validation | 38 | 0.566 | 0.142 | -2.6% | 0.0% |

“Success” is the case's defined +20% excess-return event; the false-distress cases also include their original drawdown constraint. Three cases lacked usable 90-day relative returns.

The sealed validation result is decisive for the current strategy: its four highest-ranked names were RKLB, VSTM, CHWY, and UI. Their excess returns were +16.9%, -26.3%, +11.4%, and -12.4%; none met the success definition.

## Why the combined headline is misleading

The apparently attractive all-case top decile comes from mixing two different selection mechanisms. The false-distress cohort was selected after severe drawdowns. The dilution cohort was selected around financing-risk filings. Their base rates and failure modes differ, so their rows should not be treated as one homogeneous stock universe.

Within the dilution cohort, the top 12 had:

- Mean excess return: +28.4%.
- Median excess return: +13.6%.
- Mean excess return for the remaining cohort: +1.3%.
- Top-versus-rest spread: +27.0 percentage points.
- One-sided permutation p-value: approximately 0.106.
- Standard error of the top-basket mean: 14.9 percentage points.

The basket included large winners—LEU +150.4%, MESO +71.4%, CMPX +66.0%, and RYES +50.9%—as well as FIG at -53.1%. The positive median means the result is not caused by one winner alone, but the uncertainty is too large to call it validated alpha.

## What the component signals say

### Specialist scores

No specialist lens was a robust standalone long signal.

- On all cases, accounting had the best specialist AUC at 0.545; catalysts followed at 0.540.
- On false-distress development, accounting reached AUC 0.624, but fell to 0.548 on validation.
- Operations was actively weak on validation, with AUC 0.379.
- A simple bullish-minus-bearish grounded-claim ratio was unhelpful: validation AUC 0.338.

Generic claim sentiment therefore throws away too much context. Future strategies should use claim changes and event-specific facts rather than counts of bullish language.

### Downside assessment

The model's lower downside-tail probability was the most interesting post-hoc validation feature:

- Validation AUC: 0.596.
- Top four mean excess return: +9.0%.
- Top four success rate: 25%.
- One-sided permutation p-value: approximately 0.168.

This is not confirmation because it was identified after inspecting validation. It is a reasonable feature to pre-register in the next experiment, particularly as a veto or safety screen rather than a return forecast.

### Decisions and calibration

The model emitted only 21 `long_candidate` decisions across 660 forecasts. Candidate votes did not identify successful longs in the generic long cohort. The probabilities were compressed:

- Minimum: 2%.
- Median: 10%.
- 90th percentile: 16%.
- Maximum: 33%.

Expected-return forecasts had almost no linear relationship with realized return. The model can produce a rough ordering in the event-conditioned cohort, but its probability and return magnitudes should not be interpreted literally.

## Replication efficiency

Repeated calls were highly similar:

| Output | Replicate correlation / agreement |
| --- | ---: |
| Liquidity score | 0.925 correlation; 80.0% exact |
| Operations score | 0.949; 87.7% exact |
| Catalyst score | 0.910; 81.8% exact |
| Accounting score | 0.931; 86.8% exact |
| Governance score | 0.910; 85.9% exact |
| Synthesis probabilities | 0.78–0.82 correlation |

The mean range among three synthesis probabilities was only 3.25 percentage points. Same-prompt replication is therefore a poor use of future call budget. One extraction per lens and one or two syntheses should usually be enough. Additional calls should instead buy:

- more issuers and historical dates;
- genuinely different prompts or model families;
- claim-change extraction across consecutive filings; or
- adversarial verification of the few highest-ranked candidates.

## Strategies the dataset can test

### 1. Filing underreaction

At each exact SEC acceptance timestamp, measure newly disclosed operating, financing, accounting, or catalyst claims versus the prior filing. Enter no earlier than the next tradable session and rank by the size and credibility of the change.

This is now technically feasible because raw filings, clean text, exact availability, and versioned claims are present. It still needs raw point-in-time OHLCV and an exchange-calendar execution layer.

### 2. Dilution-resolution longs

Restrict the universe to firms with a recent financing or elevated dilution risk. Look for evidence that:

- funding runway is now adequate;
- near-term issuance pressure has fallen;
- an operating or regulatory catalyst remains intact; and
- accounting/governance checks do not veto the trade.

This is the strategy most directly suggested by the positive dilution-conditioned ranking. It must be tested on newly sampled events rather than optimized on these 117 scored cases.

### 3. Safety-first catalyst longs

Use downside risk as a hard filter, then rank the survivors by dated catalyst evidence. The next protocol should pre-register the safety threshold and catalyst score before opening a new validation period.

### 4. Claim-delta strategies

Compare normalized claims across consecutive filings for the same issuer:

- cash runway increasing or decreasing;
- debt/covenant pressure appearing or disappearing;
- backlog and margin language strengthening or weakening;
- a vague catalyst becoming funded and dated;
- material-weakness or listing risk being cured.

Changes should be more informative than absolute sentiment because many risk disclosures are persistent boilerplate.

### 5. Adversarial confirmation

Use the cheap model broadly for extraction, then spend deeper analysis only on the top-ranked 5–10% of cases. A candidate should survive explicit liquidity, accounting, governance, and dilution vetoes. This is preferable to averaging five generic scores.

## Recommended next experiment

Pre-register a new event-conditioned walk-forward test:

1. Build a historical, survivorship-free universe of financing events.
2. Use exact acceptance time and next-session entry.
3. Extract one set of five specialist outputs per filing.
4. Compute changes relative to the issuer's previous filing.
5. Require low predicted downside and no accounting/governance veto.
6. Rank the remainder by catalyst specificity and improvement in funding runway.
7. Freeze thresholds using development years only.
8. Evaluate once on a later untouched period, including costs, delistings, overlapping positions, and equal-weight portfolio returns.

The current results justify this narrower experiment. They do not justify trading the present model.
