# Fidelity exclusion list — manual-use runbook

Research only, no trading advice. PIT-strict throughout: an entry is skipped
only when `event_date <= entry_date <= window_end`, where `event_date` is the
first excludable day (T+1 after the PIT timestamp). Future events never affect
past entries. This list is a research hygiene filter for manual brokerage use;
it does not change the century or sealed evaluation defaults.

## What it is

`src/fidelity_exclusions.py` is the integrator over the four lane modules. It
merges their JSONL outputs into one dated list of
`{ticker, source, event_date, window_end, reason}`:

| Source | Lane input (auto-detected) | Window |
| --- | --- | --- |
| FDA | `fda_avoid` exclusion JSONL: Class I only, PIT anchor = **report_date** (never `recall_initiation_date` — median initiation→report lag ~139d ≈ 4.5 months of lookahead), T+1 entry | 20d drug / 40d device (`config/fda_avoid.json`) |
| CPSC | `cpsc_resolved.jsonl`: fire/burn/electrocution/shock-hazard recalls with ≥10k units (`config/cpsc_avoid.json`), T+1 entry | 40d |
| 5.02-gap | `stewardship_gap` decisions with `decision=="exclude"` only — CEO/CFO, no successor in filing, no same-role CDX-PIT posting in 30d. Placebo / no_data / no_exclude rows never merge | 90TD ≈ 130 cal days |
| 337 | `fr337` events at ladder `institution`/`id`/`final` only — receipt/complaint and misc never exclude. PIT = public-inspection filing time else publication_date, T+1 entry | 180d (integrator default; no lane window spec) |

Overlap rule (extend, no re-entry): same-ticker windows that overlap merge
into one window ending at the max `window_end`; sources/reasons union. While
excluded, no new entry is allowed.

Alias-unresolved rows (FDA firm with no ticker yet, 337 respondent with no
ticker) are kept with a `firm` field for **manual review** — they never match
in `filter_signals`. Resolve via the alias table (`data/alias_golden.json`,
`src/alias_resolve.py`) before Fidelity use; never substring-match (Endo vs
Ethicon trap — see `src/fda_avoid.py`).

Hooks (caller injects point-in-time values, never future data):

- Liquidity: `passes_liquidity` / `filter_liquidity`, defaults price ≥ $3 and
  ADV ≥ $2M (`config/fda_avoid.json`). Per-row missing PIT data fails closed;
  with no PIT getters at all the gate is visibly skipped
  (`_liquidity_checked=False`).
- Sector caps: `enforce_sector_caps(candidates, sector_of, cap=0.25)`,
  greedy equal-weight, unknown sectors never capped. (The CPSC lane uses a
  stricter 5% single-industry cap in its own context — pass `cap=0.05`.)

Backtest wiring (`src/backtest_signals.py`): `--exclude PATH` is **default
off**; when on, entry signals inside an exclusion window are skipped at entry
and counted (`n_excluded_at_entry` / `skipped_excluded`). `--paper-long
--signals sigs.json [--holds 90]` runs the tiny paper cohort helper
(long-only, fixed holds, price-free counts — not returns). Century and sealed
evaluators are untouched.

## Weekly refresh (Wednesday+1, ~10 min)

1. **Pull Wednesday.** After the FDA Enforcement Report posts (Wednesday),
   re-run the lane pulls: `fda_avoid` drug+device snapshots, CPSC recall
   dump, 8-K Item 5.02 sweep + stewardship rule, ITC Section 337 FR sweep.
2. **Rebuild Thursday (+1).** Run `python3 src/fidelity_exclusions.py --fda
   … --cpsc … --gap502 … --s337 … --out exclusions.json --csv
   exclusions.csv`; confirm merge counts, overlap-extension (not duplication),
   and the unresolved-manual-review count.
3. **Export CSV.** `--csv` writes
   `ticker,source,event_date,window_end,reason,firm` for manual import; keep
   the JSON as the audit copy (regenerate, never hand-edit).
4. **Apply in Fidelity manually.** Before entering any long, skip names where
   `event_date <= today <= window_end`; resolve `firm`-only rows first; then
   apply the liquidity screen (price ≥ $3, ADV ≥ $2M on point-in-time quotes)
   and the sector cap (≤ 25% per sector).
5. **Log and close.** Save the dated JSON+CSV pair with the week; expired
   windows drop off automatically next refresh — no re-entry while active.

## Paper-only status

`rival-long` (`src/followon_rivals.py`), `tariff-long`
(`src/followon_tariff.py`), and `recovery-long` (CDX leadership-diff lane,
`src/followon_cdx.py`) remain **paper-only** research stubs: mapping-only
outputs with no return claim, tracked via `paper_long_only` cohorts (counts
and holding windows, no PnL), never entered in brokerage, pending
out-of-sample evidence and the same exclusion/liquidity/sector gates above.

## Validation

```bash
python3 -m py_compile src/fidelity_exclusions.py src/backtest_signals.py
python3 -m unittest discover -s tests -v
```

Sample schema: `strategy-site/public/data/exclusions.json` (`sample: true`,
illustrative tickers — not a live list).
