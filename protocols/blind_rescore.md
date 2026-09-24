# Blind re-score protocol — parametric foreknowledge test

Frozen 2026-08-24 before any blind model call was made or any blind result inspected.

## Question

Do the identified-run synthesis scores depend on memorized issuer identity
(training-data foreknowledge), or on reading the filing text?

## Design

- Population: the 1,713 scored sealed 2019–2020 cases with outcomes.
- Sampling (seed 4242): stratified by identified mean `p_plus20` quintile —
  40 from Q1, 10 each from Q2–Q4, 40 from Q5 → 110 cases. Oversampling the
  extremes maximizes power where leakage would matter most for trading.
- Blinding, applied to the exact wrapper packs the identified run consumed:
  company-name variants (full legal, suffix-stripped core, bare distinctive
  tokens, case variants) and ticker replaced with ISSUER/TICKER;
  commission file numbers and IRS EINs masked. Cutoff dates, anchor form,
  market state, prompts, schemas, effort levels, and model are unchanged.
  Cases with surviving identifier residuals are rejected (0 of 110 were).
- Calls per case: 5 lens extractions ×1 replicate + 2 independent syntheses,
  identical to the century configuration. ≈770 calls, ≈11M prompt tokens.

## Metrics (computed after all blind syntheses complete)

On the same 110 cases, blind vs identified mean `p_plus20`:

- Spearman vs realized 90-day excess; Mann-Whitney AUC for positive excess.
- Paired bootstrap (5,000 draws, seed 4242): ΔSpearman 95% CI, blind AUC 95% CI.
- Blind-vs-identified score agreement.

## Verdict rule (frozen)

- **LEAKAGE-DOMINANT**: identified predictive on-sample while blind AUC CI
  includes 0.5 and Δ CI excludes 0.
- **GENUINE**: Δ CI includes 0 and blind AUC CI excludes 0.5.
- **MIXED**: otherwise; report retention share (blind ρ / identified ρ).

## Known limitations

- HQ addresses, cities, and distinctive business descriptions are not scrubbed;
  identity recognition through those channels remains possible but requires far
  more memorization than names/tickers.
- Perfect blinding of filing *content* is impossible; this test bounds leakage
  through explicit identifiers, the dominant channel.
- n=110 gives ±~0.09 SE on Spearman; a MIXED verdict may need a larger sample.
