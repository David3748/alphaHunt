# alphaHunt progress and handoff

Last updated: 2026-08-23 19:05 America/New_York

## Research site (`strategy-site/`, localhost:3000)

- [x] Export P(+20%) artifacts from the VM evaluation (`/tmp/export_site_charts.py`,
  run in tmux `siteexport`; outputs in `lab_runs/century_safety/site_export/` and
  copied to `strategy-site/public/data/`): weekly NAV-vs-SPY series, 90-day-span
  stats, full P(+20%) ledger, and the latest-cohort current book.
- [x] Add "Vs SPY" equity-curve section (log-scale SVG, shaded excess band,
  stat cards) to `app/page.tsx`.
- [x] Add per-entry-year "share of 90-day spans beating SPY" bars.
- [x] Add "P(+20%) book" section. As of the sealed corpus horizon (2026-06-22)
  no positions are open; the newest cohort (entries through 2025-11-14) is shown
  with realized excess vs SPY over identical windows.
- [x] Add calendar-year P(+20%)-vs-SPY bar chart with clickable year columns
  (drill-down lists that entry cohort's trades with thesis/catalyst/invalidation;
  the 2026 column shows positions held during 2026) and a "2026 in review"
  strip in the book section: 6 positions live in 2026, 4/6 beat SPY, +8.8%
  mean excess, flat since 2026-02-12. Ledger in `public/data/p_plus20_trades.json`
  now carries per-trade SPY benchmark and excess returns.
- Key numbers: P(+20%) compounded to ~×710 (47.5% CAGR, Sharpe 1.49, matched
  IR 1.42, 270 trades) vs SPY ×10.2; spans beating SPY: 69.6% of P(+20%)
  trades (188/270) vs 44.8% of all 9,885 eligible 90-day spans.
- `npm run test` covers the new artifacts (3/3 passing); lint clean.

## Active century-scale cloud run

- [x] Generalize the sealed source builder to configurable years.
- [x] Add resumable one-command orchestration, frozen protocol, count gates,
  Zstandard archives, SHA-256 manifest, and Google Drive upload.
- [x] Connect `gdrive:alphaHunt/century_safety` and verify cloud access.
- [x] Provision Azure for Students VM `alphahunt-century` in West US 2:
  `Standard_B4as_v2`, 4 vCPU, 16 GB RAM, 256 GB standard SSD.
- [x] Restrict SSH and monitoring ingress to the launch IP.
- [x] Deploy and start the read-only live monitor on port 8080.
- [x] Start the persistent century pipeline in tmux with the API key held only
  in process environment.
- [x] Finish structured SEC enumeration for 2009–2025: 350,456 filings from
  15,387 issuers.
- [x] Resolve 25,343 historical symbol regimes and pre-signal eligibility:
  350,456 filing rows screened; 9,885 eligible events.
- [ ] Build all eligible filing packs and run 5 extractors + 2 syntheses/case.
- [ ] Run market-relative/Sharpe/HAC/bootstrap/250-placebo evaluation.
- [x] Freeze `CENTURY_HYPOTHESES.md` before century outcomes, implement the
  reproducible secondary evaluator, and queue it in tmux session `hypotheses`.
  It tests safety, p(+20%), causal blend, upside×drawdown, and deep-drawdown;
  excludes discovery years 2019–2020 from confirmation; runs 500 blocked
  placebos per rule; writes JSON/Markdown to Drive and the dashboard.
- [x] Queue the obscure-source miner behind century outcomes in tmux session
  `obscure-miner`. It seeds up to 1,500 issuers and routes discovery through
  GDELT, Federal Register, ClinicalTrials.gov, openFDA drug/device enforcement,
  broad news RSS, six expandable query families, and discovered source pages.
  It respects provider throttles and site robots rules, deduplicates content hashes,
  then runs independent Ox Alpha
  analyst, skeptic, and consensus passes on up to 12,000 documents (up to
  36,000 calls).
  Outputs are resumable, dashboard-visible, compressed, and copied to
  `gdrive:alphaHunt/obscure_miner`.
- [ ] Archive verified durable artifacts to Drive and stop/delete the VM.

Monitor at launch: `http://<vm-ip>:8080/` (source-IP restricted; VM since deleted).
Requested 2001–2008 filings remain an explicit pre-XBRL historical-security-
master gap; the automated structured pipeline starts in 2009.

## Active sealed survivorship-reduced validation

Protocol frozen at `lab_runs/sealed_safety/PROTOCOL.md` before universe build,
model calls, or post-signal outcome queries.

- [x] Define the SEC-filing population, causal score rule, execution policy,
  missing-history bounds, and pass/fail thresholds.
- [ ] Enumerate every detailed 2019–2020 10-K/10-Q/20-F/40-F filing.
- [ ] Resolve filing-native historical symbols/exchanges and pre-signal prices.
- [ ] Build every eligible severe-drawdown event without future-price filtering.
- [ ] Run grounded extraction and two-replicate synthesis for all cases.
- [ ] Open outcomes and run conservative/optimistic portfolio evaluations.
- [ ] Ingest, validate, export, and issue the sealed verdict.

## Safety-first trading-strategy evaluation

User requested a real evaluation with many robustness tests. Completed in
`src/safety_backtest.py`; full report is `lab_runs/safety_backtest/report.md`.

- [x] Implement next-session daily mark-to-market execution.
- [x] Size each new position at 10% of current NAV, cap at ten positions, and
  return proceeds to cash at exit.
- [x] Apply 10 bp per side and accrue idle cash at the historical 13-week
  Treasury yield (`^IRX`).
- [x] Calculate T-bill Sharpe, drawdown, beta/alpha, exposure-matched SPY/cash
  information ratio, HAC uncertainty, and daily/monthly/yearly returns.
- [x] Test causal expanding thresholds, fixed thresholds, all-event and
  worst-decile controls, five score buckets, 30/60/90/120/180-day holds,
  0/1/4-session delays, 0/10/25/50 bp costs, position sizing, calendar cohorts,
  leave-one-trade/month-out, block/trade bootstrap, and 2,000 random baskets.
- [x] Record the evaluation as `backtest_runs` ID
  `60b49c8104d86860ece9d6881f4c1a9798efffc657b1ae92bc1d11af7f1954b4`.
- [x] Run 56/56 unit tests, full temporal/blob validation, and Parquet export.

Headline results:

| Rule | Trades | CAGR | T-bill Sharpe | Max DD | Matched IR |
| --- | ---: | ---: | ---: | ---: | ---: |
| Global top decile (non-causal) | 8 | +13.3% | 2.07 | -2.1% | 2.15 |
| Expanding top decile (prior scores only) | 10 | +14.5% | 1.53 | -6.6% | 1.66 |
| Expanding top quintile | 20 | +22.4% | 1.61 | -9.1% | 1.80 |
| All events | 68 executed | +5.7% | 0.24 | -39.9% | -0.07 |
| Worst safety decile | 8 | -1.9% | -0.30 | -15.3% | -0.38 |

Verdict: promising signal, not yet a valid deployable strategy. The global rank
uses future score-distribution information. The causal version was introduced
after outcomes were visible and has only ten trades. More importantly, the
universe contains current listings only and case eligibility required a future
90-day price observation, excluding delisted/data-ending failures. The next
test must freeze the expanding rule and evaluate it once on an exhaustive,
survivorship-free, point-in-time universe with corporate actions and delistings.

## Active fresh confirmation

User approved advancing the two surviving strategies. Protocol frozen at
`lab_runs/fresh_confirmation/PROTOCOL.md` before cases/outcomes were inspected.

- [x] Build 80 non-overlapping 2021–2023 safety-first cases (seed 811).
- [x] Build 80 non-overlapping 2021–2023 dilution-event cases (seed 812).
- [x] Run 800 one-per-lens grounded extractions.
- [x] Run 320 two-replicate syntheses.
- [x] Evaluate locked top-decile rules without tuning.
- [x] Ingest the fresh experiment, validate, export, and report.

Fresh confirmation results and ingestion:

- 1,140 successful provider requests including semantic retries: 812 extraction
  requests and 328 synthesis requests; 14,245,768 prompt tokens and 1,804,214
  completion tokens.
- 7,711 proposed claims; 4,964 exact-quote-grounded (64.4%).
- Exact SEC acceptance-time enrichment: 251 packs considered, 237 non-empty
  packs revised, 14 empty packs skipped, zero unmatched accessions.
- Raw SEC expansion: 1,058 new primary filings, zero failures, 790 MB HTML and
  96 MB cleaned text.
- Fresh temporal experiment: 160 cases, 4,964 claims, 800 analyses, 320
  predictions, 160 outcomes, and a locked metric set.
- Safety-first confirmed: top 8 mean +29.1% excess, median +31.8%, 75% +20%
  success, +33.8 percentage-point spread versus the rest, one-sided permutation
  p=0.0029. It passes both p<0.05 and the two-test Bonferroni p<0.025 threshold.
- Dilution-event-conditioned did not confirm: top 8 mean +5.5%, +9.3-point
  spread, p=0.1714.
- Safety robustness: AUC 0.743; Spearman score/return 0.484; positive mean
  returns for basket sizes 4 through 20; replicate score correlation 0.713.
- Important limitation: the 2021–2023 samples come from a current-listing
  universe. Survivorship bias can materially inflate the historical long result.
  This confirms a research signal in the sampled data, not a production strategy.
- Final verification: 53/53 unit tests passed; full temporal and SHA-256 blob
  validation passed with zero issues; zero case/prediction manifest lookahead or
  orphan rows; secret scan clean; all 13 Parquet tables refreshed.

Final store state after fresh confirmation:

| Table | Rows |
| --- | ---: |
| `content_objects` | 5,661 |
| `experiment_versions` | 4 |
| `security_versions` | 374 |
| `document_versions` | 3,694 |
| `claim_versions` | 19,001 |
| `input_manifests` | 1,340 |
| `manifest_items` | 20,341 |
| `experiment_cases` | 760 |
| `analysis_runs` | 3,540 |
| `prediction_runs` | 1,320 |
| `outcome_versions` | 760 |
| `experiment_metrics` | 7 |
| `backtest_runs` | 1 |

Storage: DuckDB 96 MB, immutable blob corpus 2.1 GB, Parquet export 13 MB.

## Active five-strategy experiment batch

Requested after the comprehensive analysis. Pre-specified formulations:

1. Baseline comprehensive +20% probability on the false-distress long universe.
2. Safety-first ranking using lower forecast downside-tail probability.
3. Catalyst ranking with liquidity, accounting, and governance vetoes.
4. Consensus-adjusted probability penalizing disagreement across synthesis replicates.
5. Event-conditioned comprehensive probability on the dilution cohort.

Evaluation rules: preserve the legacy 62/38 long development/validation split;
use a chronological 70/30 split for the dilution cohort; rank the top decile;
report mean/median excess return, hit rate, top-versus-rest spread, and one-sided
permutation uncertainty. These are signal experiments without transaction costs,
portfolio overlap rules, or capacity assumptions.

- [x] Implement deterministic experiment runner and tests (`src/strategy_experiments.py`).
- [x] Run five experiments and write `lab_runs/strategy_experiments/report.md`.
- [x] Record experiment metrics in the temporal store (`strategy_batch_5_v1`).
- [x] Re-run full tests and integrity validation: 51/51 tests passed; zero integrity issues; Parquet refreshed.

Results:

| Experiment | Development top basket | Validation top basket | Validation spread | p |
| --- | ---: | ---: | ---: | ---: |
| Baseline probability | -10.3% | -2.6% | -0.3% | 0.402 |
| Safety first | +1.6% | +9.0% | +12.7% | 0.168 |
| Catalyst with vetoes | -0.2% | -3.8% | -1.6% | 0.439 |
| Consensus adjusted | -18.2% | -2.6% | -0.3% | 0.402 |
| Dilution event conditioned | +19.6% | +15.1% | +17.1% | 0.180 |

Decision: advance safety-first and dilution-event-conditioned only to a new,
untouched sample. Neither is statistically significant; the other three failed.

## Active expansion (started after initial store completion)

User authorized heavy use of the temporary free Ox Alpha allocation. The new
objective is to turn that allocation into a broad, reusable claim corpus and a
better long-research experiment, with redundant extraction rather than merely
adding repetitions of the failed false-distress prompt.

Planned call budget:

- 220 historical cases × 5 specialist lenses × 2 independent replicates = 2,200 extraction calls.
- 220 cases × 3 independent synthesis replicates = 660 long-forecast calls.
- Target total: 2,860 new calls, resumable and cached.

Active tasks:

- [x] Add the comprehensive extraction/synthesis runner (`src/comprehensive_lab.py`).
- [x] Unit-test grounding, deterministic case IDs, role-aware excerpting, and idempotent temporal ingestion.
- [x] Run 2,200 specialist extractions at 48-way concurrency; zero failed jobs.
- [x] Run 660 independent long syntheses; zero failed jobs.
- [x] Ground quotes and ingest 14,037 accepted facts into `claim_versions`.
- [x] Ingest 2,200 analyses, 660 forecasts, and 220 outcomes into 440 new frozen manifests.
- [x] Score by source experiment and sealed long validation split.
- [x] Re-run temporal/blob integrity validation and Parquet export.
- [x] Record final counts, usage, results, and limitations here and in `lab_runs/comprehensive_long/report.md`.

Comprehensive run totals:

- 2,860 new model calls (2,200 extraction + 660 synthesis); zero terminal call failures.
- Main extraction process: 32,947,195 prompt and 3,680,395 completion tokens, plus a five-call smoke test.
- Synthesis: 7,961,898 prompt and 884,695 completion tokens including the smoke test.
- 20,429 proposed claims; 14,037 exact-quote-grounded (68.7%).
- 1,490 raw SEC primary filings fetched with zero failures: 994 MB HTML and 139 MB clean text.
- Sealed long validation still failed: AUC 0.566; top 4 had 0 successes and -2.6% mean excess return.
- Dilution-derived cohort top decile returned +28.4% excess with 41.7% hit rate, but this is event-conditioned exploratory evidence, not clean validation.

SEC availability enrichment completed during this expansion:

- Added `src/sec_enrich.py` and retained optional SEC submission fields in `ox_lab.column_rows`.
- Queried 219 issuers with zero issuer-level failures.
- Appended exact-acceptance-time revisions for 329 legacy evidence packs.
- Matched every accession present in those packs; zero unmatched accessions.
- Eleven empty future-evidence packs had no source accession and were intentionally left unchanged.
- Enrichment system time: `2026-08-22T19:51:46.458442+00:00`.

## Final store state after comprehensive expansion

- `content_objects`: 3,310
- `document_versions`: 2,159
- `claim_versions`: 14,037
- `experiment_versions`: 3
- `experiment_cases`: 440
- `analysis_runs`: 2,740
- `prediction_runs`: 1,000
- `outcome_versions`: 440
- `input_manifests`: 780
- `manifest_items`: 14,817
- Full temporal and SHA-256 blob validation: zero issues.
- Full unit suite after all changes: 48/48 passed.
- Direct consistency checks: zero manifest lookahead, orphan cases, or orphan predictions.
- Workspace secret scan: clean; the supplied API key was never persisted.
- Parquet export refreshed: 13 tables, 9.3 MB.
- DuckDB: 59 MB; immutable blob corpus: 1.2 GB.
- Full post-build analysis: `ANALYSIS.md` (component signals, replicate efficiency,
  statistical uncertainty, supported strategy hypotheses, and next protocol).
- User-requested final rerun: 48/48 tests passed and full temporal/blob validation passed.
- Live artifact audit found provider-fallback omissions: all 660 ranking probabilities
  are present and valid, but 62 synthesis narratives lack `catalyst`, one lacks downside
  probability, two extractions lack confidence, one lacks its redundant result-level role,
  and eight claims lack effective dates (conservatively defaulted during ingestion).

## Current objective

Build a fully point-in-time, bitemporal research dataset for stock predictions based on LLM analysis of SEC filings and other time-stamped unstructured data. It must support reproducible walk-forward backtests without lookahead.

## Temporal contract

Every knowledge-bearing record distinguishes:

- `effective_at` / `effective_from` / `effective_to`: when the fact applies economically.
- `available_at`: earliest time a market participant could know it.
- `system_from`: when this database recorded that version.
- `system_to`: derived in history views with `LEAD(system_from)`; base version tables remain append-only.

Every prediction must reference an immutable, content-addressed input manifest. A manifest rejects documents whose `available_at` is later than the prediction knowledge time or whose version was not visible at the selected system time.

## Architecture decisions

- DuckDB is the local query/catalog layer; `duckdb==1.4.4` is installed.
- Large payloads are immutable SHA-256-addressed blobs under `research/blobs/`.
- Tables can be exported as compressed Parquet under `research/parquet/`.
- No existing JSONL artifacts are deleted. They are migration inputs and remain the original record.
- Legacy filing packs only have filing dates, not exact SEC acceptance timestamps. Migrated documents are marked `availability_precision=date_only` and `needs_acceptance_time_enrichment=true`.
- The current ticker universe has survivorship bias. Migrated security records explicitly retain that warning.
- API keys must never be written to this file, source code, commands, or database.

## Completed experiments

### Dilution forecast

- Directory: `lab_runs/dilution90/`
- 120 cases; 240 forecast rows; 240 outcome-analysis rows.
- AUC 0.907, but exact high-confidence event precision was 55.6%.
- High-risk basket strongly underperformed, but the result is not production proof.

### False-distress long forecast

- Directory: `lab_runs/long_dev/`
- 100 cases; 300 analyst rows; 100 synthesis predictions.
- Development: AUC 0.646; top-decile mean excess return -6.5%.
- Sealed validation: AUC 0.614; Brier skill -2.4%; top decile 0/4 successes and -3.7% excess return.
- Verdict: failed validation; retain as a reproducible negative experiment.

## Temporal-store implementation status

### Added

- `src/temporal_store.py`
  - DuckDB schema for content, experiments, security/document/claim versions, manifests, cases, analyses, predictions, outcomes, metrics, and backtests.
  - Append-only version records with derived history views.
  - Content-addressed blob writer.
  - Point-in-time document and claim queries.
  - Manifest lookahead and system-version guards.
  - Prediction/case manifest invariants.
  - Equivalent lookahead/system-version guards for normalized claim inputs.
  - Point-in-time backtest input snapshots and deterministic backtest input hashes.
  - Integrity validator and Parquet exporter.
  - Migration functions for both completed experiments.
- `tests/test_temporal_store.py`
  - Document revision/system-time test.
  - Future-document manifest rejection test.
  - Effective/available/system-time claim query test.
  - Frozen prediction-manifest test.
  - Blob/integrity validation test.

### Built dataset

- DuckDB: `research/alphahunt.duckdb` (17 MB)
- Content-addressed blobs: `research/blobs/` (143 MB)
- Parquet export: `research/parquet/` (13 tables, 1.7 MB)
- User-facing guide: `TEMPORAL_DATASET.md`

Current row counts:

| Table | Rows |
| --- | ---: |
| `content_objects` | 330 |
| `experiment_versions` | 2 |
| `security_versions` | 219 |
| `document_versions` | 340 |
| `claim_versions` | 0 |
| `input_manifests` / `manifest_items` | 340 / 340 |
| `experiment_cases` | 220 |
| `analysis_runs` | 540 |
| `prediction_runs` | 340 |
| `outcome_versions` | 220 |
| `experiment_metrics` | 4 |
| `backtest_runs` | 0 |

Experiment reconciliation:

- `dilution_90d`: 120 cases, 240 analyses, 240 predictions, 120 outcomes.
- `false_distress_long`: 100 cases, 300 analyses, 100 predictions, 100 outcomes.

### Next work, in priority order

1. Ingest exact SEC `acceptanceDateTime` values and replace date-only availability assumptions with appended document versions.
2. Add a historically complete security master: listings, delistings, ticker/CIK mappings, and corporate actions.
3. Ingest raw price/volume and benchmark bars with vendor release/system timestamps instead of storing only legacy derived returns.
4. Convert evidence into versioned `claim_versions` with quote/span lineage, extractor version, and confidence.
5. Define an explicit long strategy policy (next-session entry, ranking cutoff, sizing, overlap, costs) and record new results in `backtest_runs`.
6. Run a fresh walk-forward long experiment. Do not reuse the failed false-distress formulation as a production signal.

## Rebuild and verification commands

Run from `/Users/davidl/Documents/alphaHunt`:

```bash
python3 -m py_compile src/temporal_store.py
python3 -m unittest discover -s tests -v
python3 src/temporal_store.py --db research/alphahunt.duckdb --blobs research/blobs init
python3 src/temporal_store.py --db research/alphahunt.duckdb --blobs research/blobs migrate
python3 src/temporal_store.py --db research/alphahunt.duckdb --blobs research/blobs validate
python3 src/temporal_store.py --db research/alphahunt.duckdb --blobs research/blobs export-parquet --output research/parquet
```

Do not run migration until the tests pass. Migration is designed to be idempotent by preserving one stable legacy migration system timestamp in `store_metadata`.

## Verification log

- `python3 -m py_compile src/temporal_store.py`: passed.
- `python3 -m unittest discover -s tests -v`: 42/42 passed.
- Fixed SQL parameter counts for document, claim, analysis, and prediction inserts.
- Supporting outcome analyses may use their own horizon-time frozen manifest; final predictions remain locked to the case's original prediction-time manifest.
- `migrate_all` now runs in one DuckDB transaction. Content blobs are immutable and content-addressed, so an interrupted migration can be retried safely.
- Migration was run twice and retained identical counts.
- Full integrity validation, including SHA-256 re-hashing of every blob: passed with zero issues.
- Representative NFLX point-in-time query returned only the prediction-time evidence pack.
- Direct SQL audit found zero manifest availability/system-time violations.

## Likely review points

- Verify INSERT value counts against each DuckDB table definition.
- `analysis_runs` and `prediction_runs` intentionally separate supporting LLM analyses from final forecasts.
- Legacy dilution outcome labelers use a separate future-evidence manifest with a knowledge time at the horizon end.
- Existing historical packs remain imperfect until exact SEC `acceptanceDateTime` values and a survivorship-free security master are ingested.
- Before trusting a future backtest, add delisted securities, historical listings, corporate actions, and next-session execution rules.
