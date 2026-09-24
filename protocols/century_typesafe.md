# Century P(+20%) with TypeSafe as the document evaluator

Frozen 2026-09-16, before any TypeSafe judgments were produced. Question wording,
aggregation, and rules below are locked; changes require a new `PROMPT_VERSION`.

## Population and evidence

- The same 9,885 eligible century cases (2009–2025) as `lab_runs/century_safety`.
  Case IDs are mapped back to SEC `(cik, accession)` through the original hashing
  scheme (9,880 recovered; 5 unmatched cases are excluded and reported).
- Evidence is the same complete anchor-filing pack, rebuilt from SEC with the
  original builder (`ox.make_pack`, issuer redaction, 220k/700k character caps).
- No Ox extraction or synthesis output reaches TypeSafe.

## Jev pass 1: anonymizer (final policy locked 2026-09-16 after a 20-case redaction pilot; no analyzer outputs or outcomes were used)

Code mechanically removes URLs, emails, and phone numbers. It then proposes up to 240
candidate identifiers per filing: capitalised phrases containing a word never seen in
lowercase (ignoring words capitalised only at sentence starts), and uncommon acronyms.
Each candidate comes with a 320-character context. Jev answers one **Choice** per
candidate (60 per request, with the filing's first 3,000 characters). The options are
issuer_specific_name, person_name, named_counterparty, distinctive_location,
generic_term, public_institution, and broad_geography.
P(identifying) is the sum of the first four options. Code redacts to `[REDACTED]`:

- any phrase with P >= 0.50, except a lone dictionary word, which needs P >= 0.70;
- any non-dictionary word inside a redacted phrase with its own P >= 0.30.

The pilot rejected two earlier designs. A single identify Noul had a threshold that was
too loose, redacting "Depreciation" and "Exchange Act". An added generic-term Noul
leaked names such as "Genworth" and "Deepwater Champion". The final policy leans
toward over-redacting headings and labels, which leaves figures intact. Every
probability is stored in `redactions.jsonl`, and the analyzer sees only redacted text.

## Jev pass 2: analyzer (`jev-latest`)

For each case, five requests with a role-focused deterministic excerpt
of the REDACTED pack (`role_excerpt`, liquidity/operations/catalysts/accounting/governance, 40k chars,
halved only if the API rejects the size). Each request asks:

- Outcome Nouls (all five views, averaged): `plus20` (beats SPY by 20+ pp over 90
  days), `positive_excess`, `downside_tail` (trails SPY by 25+ pp).
- Role-specific: liquidity runway Score + dilution Noul; operating trajectory
  Score + drawdown-nature Choice; rerating catalyst Score; accounting red-flag
  Noul; governance/legal risk Noul.

## Locked rules

All rules use the original causal expanding top-decile selection (50-case warmup),
next eligible close, 90-day hold, ten 10%-NAV slots, 25 bp per side, T-bill cash,
exposure-matched SPY benchmark, conservative terminal bound.

1. **Primary: `ts_plus20`**: mean `plus20` across five views. This mirrors the
   main strategy rule (`p_plus20`).
2. `ts_composite`: equal mean of expanding z-scores (strictly earlier cases) of
   plus20, catalyst, trajectory, runway, minus downside_tail, dilution, accounting
   red flag, and P(financial_distress).
3. `ts_upside_x_drawdown`: `plus20 × |drawdown|`.
4. `ts_safety`: `-downside_tail`.
5. Comparator `ox_p_plus20`: the original Ox rule re-evaluated on the identical case set.

## Cohorts and inference

As in `CENTURY_HYPOTHESES.md`: 2009–2018 historical and 2021–2025 forward holdouts,
with 2019–2020 descriptive. Primary passes if matched excess is positive in both
holdouts and the calendar-month blocked placebo (500 draws) gives one-sided p < 0.05.
Secondaries use Bonferroni p < 0.0167. We also report a calibration table of `plus20`
against realized 90-day ≥20 pp excess.

Caveat: Jev may have memorised historical market outcomes. The anonymizer removes names,
but exact figures, dates, and business descriptions can still identify a company. Treat
any edge as an upper bound.
