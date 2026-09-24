# Century strategy hypothesis amendment

Frozen 2026-08-22 before century outcomes were opened. These hypotheses were
chosen after inspecting the separate 2019–2020 sealed run. Consequently,
2019–2020 is the discovery cohort and is excluded from confirmatory claims.

## Unchanged primary endpoint

The original safety-first strategy and verdict in `lab_runs/century_safety/PROTOCOL.md`
remain unchanged. This amendment does not replace or relax that protocol.

## Locked secondary scores

Each synthesis field is averaged across the two independent replicates.

1. `p_plus20`: mean `probability_plus20_excess_90d_pct`.
2. `causal_blend`: equal mean of expanding z-scores for `p_plus20`,
   `expected_excess_return_90d_pct`, `probability_positive_excess_90d_pct`, and
   negative `downside_tail_probability_pct`. At each timestamp, normalization
   uses only strictly earlier cases; the first 50 cases are warmup.
3. `upside_x_drawdown`: `p_plus20 * abs(drawdown_from_1y_high)`.

Locked mechanical comparator: `deep_drawdown = abs(drawdown_from_1y_high)`.

All rules use the original causal expanding top-decile threshold, next eligible
close, 90-calendar-day hold, ten 10%-NAV slots, 25 bp per side, T-bill cash, and
exposure-matched SPY benchmark.

## Cohorts and inference

- Discovery only: 2019–2020.
- Confirmatory historical holdout: 2009–2018.
- Confirmatory forward holdout: 2021–2025.
- A secondary rule must have positive matched excess in both confirmatory
  cohorts and a one-sided blocked-placebo p-value below 0.0167 (Bonferroni for
  three LLM hypotheses). The mechanical comparator is reported, not included
  in the three-hypothesis family.
- Placebos shuffle scores within calendar-month blocks, preserving opportunity
  timing and regime clustering. Five hundred draws are requested per rule. This
  was reduced from 5,000 on 2026-08-23 before any secondary result was produced
  or inspected; hypotheses, gates, and statistics were unchanged.
- Full-period and discovery-period results are descriptive only.
