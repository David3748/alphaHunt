# alphaHunt

**Can an LLM read SEC filings well enough to forecast which beaten-down stocks will
beat the market, and how would you know if it couldn't?**

alphaHunt is a forecasting pipeline and an evaluation harness. Specialist LLM
agents read a company's filings from primary sources. A synthesis step then turns
their findings into a probability: *P(stock beats SPY by 20+ points over the next
90 days)*. Most of the engineering went into the harness. It enforces point-in-time
data, pre-registered rules, placebo tests, and a live forward cohort, because an
LLM backtest is easy to fool.

The backtest was spectacular, and **the live test failed**. The rest of this README
covers what survived out of sample, what didn't, and why.

**Interactive case study:** [david3748.github.io/alphaHunt](https://david3748.github.io/alphaHunt/)
(built from this repo's results by [`docs/case-study/build.py`](docs/case-study/build.py)).

![tests](https://github.com/David3748/alphaHunt/actions/workflows/tests.yml/badge.svg)

## TL;DR

| | Backtest 2009–2025 | Live 2026 (after the model's training data) |
| --- | --- | --- |
| Cases forecast | 9,807 | 985 |
| Rank IC of P(+20%) vs 90-day excess return | 0.15–0.24 by holdout | **0.28** [0.15, 0.44] |
| AUC for the +20 pp event | 0.61–0.63 | **0.51** (chance) |
| Locked P(+20%) rule, per trade | +24.7% mean excess, 70% win (270 trades) | **−21.9%**, 17% win (12 trades) |
| Locked rule, portfolio | 710× vs SPY 10×; information ratio 1.4; blocked-placebo p = 0.002 | worse than any 12-trade stretch in the backtest |

- **The ranking signal is real and survives out of sample.** It beats seven
  mechanical price features in walk-forward tests (monthly IC 0.30 vs 0.13), and
  its rank correlation *held* in 2026. A pre-registered blind re-score with names
  and tickers scrubbed did not weaken it.
- **The part that made money did not survive.** The backtest's returns came from
  the extreme top of the ranking. In 2026 that tail edge vanished, and the locked
  rule lost 22% vs SPY per trade.
- **The model could often tell which company it was reading.** 91% of the
  "anonymized" evidence packs still carried a direct identifier. Even after a strict
  scrub, Claude Haiku 4.5 named 53% of companies from MD&A prose alone. Leakage
  through the model's own weights can't be ruled out for any year before its
  training cutoff.
- **The probabilities are poorly calibrated.** Forecasts average 11–13%, while the
  event happens 19–21% of the time. Brier skill over a base-rate forecast is about
  +0.02, and walk-forward Platt scaling only brings it to about +0.05.

Full numbers: [`results/forecast_audit/report.md`](results/forecast_audit/report.md).

## The forecasting setup

```mermaid
flowchart LR
    A["SEC EDGAR<br/>350,456 filings<br/>15,387 issuers<br/>2009–2025"] --> B["Point-in-time screen<br/>40%+ drawdown, liquidity,<br/>filing-native tickers,<br/>no future prices needed"]
    B --> C["9,885 events<br/>anchor filing + prior 180 days,<br/>frozen at SEC acceptance time"]
    C --> D1[Liquidity lens]
    C --> D2[Operations lens]
    C --> D3[Catalysts lens]
    C --> D4[Accounting lens]
    C --> D5[Governance lens]
    D1 & D2 & D3 & D4 & D5 --> E["2 independent syntheses<br/>P(+20% vs SPY, 90d),<br/>downside tail, expected excess"]
    E --> F["Bitemporal store<br/>content-addressed manifests"]
    F --> G["Locked causal rules<br/>next close after acceptance,<br/>10 slots, 25 bp/side"]
    G --> H["Holdouts + 500 blocked placebos<br/>+ live 2026 cohort"]
```

- **Population.** A case is every 10-K, 10-Q, 20-F, or 40-F filed while the stock
  sat 40%+ below its one-year high, with liquidity filters known at the time. At
  most one event per issuer per 180 days. Tickers come from the filing's own XBRL,
  not today's ticker map. Delisted names stay in and are marked to zero at the
  first gap (conservative bound).
- **Agents.** Five specialist lenses extract exact-quote-grounded claims. Claims
  whose quote isn't in the filing are rejected (about two-thirds survive). Two independent
  syntheses then produce the probabilities. The model was a stealth model served through OpenRouter as "Ox Alpha";
  its identity and training cutoff are undisclosed. About 125,000 LLM calls in total.
- **Trading rule.** Buy when a new score reaches the 90th percentile of *strictly
  earlier* scores (after a 50-case warmup). Enter at the first close after SEC
  acceptance and hold 90 days, with ten 10%-NAV slots, 25 bp per side, T-bills on
  idle cash, and an exposure-matched SPY benchmark.

## Guardrails against fooling yourself

| Failure mode | Guardrail | Where |
| --- | --- | --- |
| Look-ahead in inputs | Bitemporal store (effective / available / system time). Every forecast references a content-addressed manifest that rejects any document available after the knowledge time. Exact SEC acceptance timestamps, not filing dates. | [`temporal_store.py`](src/temporal_store.py), [`sec_enrich.py`](src/sec_enrich.py) |
| Survivorship bias | Filing-native historical tickers; no future price needed for eligibility; delistings bounded conservatively and optimistically | [`sealed_safety.py`](src/sealed_safety.py), [`protocols/sealed_safety.md`](protocols/sealed_safety.md) |
| Garden of forking paths | Rules, cohorts, and pass/fail gates frozen in writing before outcomes were opened; exploratory findings labeled as such | [`protocols/`](protocols/) |
| Luck and regime clustering | 500 placebos shuffling scores within calendar-month blocks; separate 2009–18 and 2021–25 holdouts; 2019–20 kept as discovery only | [`century_hypotheses.py`](src/century_hypotheses.py), [`sealed_safety_eval.py`](src/sealed_safety_eval.py) |
| "An LLM is an expensive momentum screen" | Mechanical baselines (drawdown, momentum, volatility, size) as first-class comparators, walk-forward | [`llm_incremental_value.py`](src/llm_incremental_value.py) |
| The model already knows the answer | Blind re-score, redaction audit, re-identification probe, live post-cutoff cohort | [`blind_rescore.py`](src/blind_rescore.py), [`redaction_audit.py`](src/redaction_audit.py), [`memorization_probe.py`](src/memorization_probe.py) |
| Hallucinated evidence | Exact-quote grounding against the source filing; ungrounded claims are kept for audit but excluded from forecast inputs | [`comprehensive_lab.py`](src/comprehensive_lab.py) |

## Results

### 1. The backtest (pre-registered secondary rules)

These rules were frozen in [`protocols/century_hypotheses.md`](protocols/century_hypotheses.md)
before any century outcomes were opened.

| Rule | 2009–18 IR | 2021–25 IR | Full-period IR | Blocked-placebo p | Gate |
| --- | ---: | ---: | ---: | ---: | --- |
| `p_plus20`: mean P(+20%) | 1.07 | 1.89 | 1.39 | 0.002 | pass |
| `causal_blend`: z-scored blend of four outputs | 0.97 | 1.97 | 1.32 | 0.002 | pass |
| `upside_x_drawdown` | 1.18 | 1.22 | 1.31 | 0.002 | pass |
| `deep_drawdown` (mechanical comparator) | 0.22 | −0.38 | −0.04 | 0.112 | — |

The LLM adds value over price features in walk-forward tests (ridge model, trained
on earlier years only). Monthly IC was 0.13 for mechanical features alone, 0.30 for
LLM outputs, and 0.29 combined, for 2021–2025. The causal top-decile portfolio IR
was −0.13 mechanical, 0.59 LLM-only, and 0.94 combined.

### 2. The live test

The same locked rules ran on every eligible 2026 filing (996 cases, scored on
2026-08-24) and were graded once each 90-day window closed.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/figures/backtest_vs_live-dark.svg">
  <img alt="Per-trade 90-day excess return: backtest 270 trades averaging +25%, live 2026 12 trades averaging -22%" src="docs/figures/backtest_vs_live-light.svg">
</picture>

Twelve trades is a small sample. Still, the backtest's worst run of 12 consecutive
trades averaged −16.9% (Q4 2018), and the live run averaged −21.9%. The two other
locked rules also lost live: `upside_x_drawdown` −13.4% over 34 trades, and the
blend −2.0% over 28.

### 3. What survived and what didn't

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/figures/signal_by_year-dark.svg">
  <img alt="Rank IC by year stays near 0.2-0.3 including 2026; the top-minus-bottom quintile hit-rate edge falls to near zero in 2026" src="docs/figures/signal_by_year-light.svg">
</picture>

The cross-sectional rank correlation is as strong in 2026 as in any recent year.
The LLM is reading something real in filings. What collapsed is its ability to
single out the stocks that jump 20+ points. That was the only part of the ranking
the trading rule used.

### 4. Calibration

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/figures/calibration-dark.svg">
  <img alt="Reliability diagram: the model under-forecasts at every level in 2009-2025; the 2026 curve is flat" src="docs/figures/calibration-light.svg">
</picture>

The model is a timid forecaster: every decile sits above the diagonal. Rescaling
helps historically, with walk-forward Platt Brier skill rising from +0.02 to
+0.05. In 2026 the curve goes flat and skill turns negative (−0.06 raw, −0.02
recalibrated). The ordering is somewhat informative, but the probabilities can't
be taken at face value.

### 5. Could the model tell which company it was reading?

| Test | Result |
| --- | --- |
| Pre-registered blind re-score ([protocol](protocols/blind_rescore.md)): 110 cases re-run with names, tickers, EINs, and file numbers scrubbed | Signal unchanged (Spearman 0.39 → 0.39, AUC 0.70 → 0.71). Verdict *GENUINE* for the name/ticker channel. |
| Redaction audit of all 10,875 evidence packs ([`redaction_audit.py`](src/redaction_audit.py)) | 91% still contain a direct identifier. The distinctive name word survives in 61% of packs that have one (for example, "Nabors" 126 times after "NABORS INDUSTRIES LTD" was redacted). The cover-page address survives in 85%. |
| Re-identification probe, Claude Haiku 4.5 from memory ([`results/memorization_probe`](results/memorization_probe/table.md)): 48 fully scrubbed 4.5k-char MD&A excerpts | Named 53% of 2011–2024 companies (AMD, Best Buy, Wynn, Amarin…). Outcome recall was at chance: 3 of 11 directional calls right, P(+20%) AUC 0.45. |

The blind test rules out leakage through explicit identifiers. It cannot rule out
recognition through business descriptions or addresses. The probe shows that
channel is wide open even for a small model. A small model's outcome recall was
at chance, but that doesn't bound what a frontier model remembers. So any
pre-cutoff backtest of an LLM forecaster is an upper bound on its real skill.

## Next steps

1. **Evaluate only after the cutoff.** Use models with published training
   cutoffs, score only filings after that date, and keep a rolling live cohort.
   Treat everything earlier as development data.
2. **Make re-identification a gate.** Before a case enters a backtest, an
   adversarial "name this company" model must fail on the evidence pack.
   Neutralize addresses, segment names, and product names, not just the
   registrant name.
3. **Separate leakage from regime.** Seven of the 12 live losers came in
   February–March 2026, several of them software stocks. Use sector-neutral
   evaluation and more live months to tell a regime break from memorized history.
4. **Forecast the whole distribution, not just the tail.** The signal lives in
   the middle of the ranking. A long/short or quintile-spread construction uses
   it. A top-decile long-only rule depends on the tail.
5. **Add a calibration layer and a second model family.** Walk-forward
   recalibration, plus ensembles across model families (the TypeSafe/Jev rerun in
   [`protocols/century_typesafe.md`](protocols/century_typesafe.md) is
   pre-registered and paused at 100 of about 49k judgments).

## Repository map

```
src/
  sealed_safety.py, century_pipeline.py   SEC enumeration, point-in-time symbols and prices, case builder
  ox_lab.py, comprehensive_lab.py         evidence packs, redaction, 5-lens extraction, synthesis, grounding
  temporal_store.py, sec_enrich.py        bitemporal DuckDB store, manifests, acceptance-time enrichment
  sealed_safety_eval.py, century_hypotheses.py, safety_backtest.py
                                          causal portfolio simulation, blocked placebos, locked rules
  live_predictions.py, live_outcomes.py   live 2026 cohort: board and grading
  llm_incremental_value.py                LLM vs mechanical features, walk-forward
  forecast_audit.py, redaction_audit.py, memorization_probe.py, blind_rescore.py
                                          leakage and calibration audits
  pipeline_monitor.py                     read-only HTTP monitor for cloud runs
  ...                                     side experiments (below)
protocols/      frozen pre-registrations, copied verbatim from each run
results/        small committed outputs (audit reports, probe answers)
data/           reference data and the audit input snapshot
docs/           case study (index.html), figures/, notes/ research log, week-one/ first write-up
reports/        standalone HTML research reports
sites/          Next.js/vinext front-ends for the results
cloud/, scripts/  Azure VM, Docker, and supervisor scripts for the century run
tests/          289 offline tests with committed fixtures
```

Run data (about 11 GB of filing packs, LLM outputs, and caches under `lab_runs/`
and `research/`) is not committed.

## Reproduce

```bash
pip install -r requirements.txt
python -m pytest -q                         # 289 offline tests
python src/forecast_audit.py                # rebuilds the audit from data/audit_inputs/
python src/memorization_probe.py            # rescores the Haiku probe answers
```

The full pipeline needs an OpenRouter key (`OPENROUTER_API_KEY`) and a
[SEC-compliant user agent](https://www.sec.gov/os/accessing-edgar-data). The
century run took about 70k calls on a 4-vCPU VM. See
[`docs/notes/CENTURY_RUNBOOK.md`](docs/notes/CENTURY_RUNBOOK.md).

## Side experiments

The same harness was used to try other unstructured-data ideas. Most failed or
remain exploratory; details are in [`docs/notes/`](docs/notes/) and
[`reports/`](reports/).

- **Avoid-lists:** CPSC and FDA Class I recalls, USITC §337 cases, 8-K executive departures.
- **Event sources:** Form 4 insider buying since 2003, first-time lobbying
  registrations, Exhibit 21 subsidiary resolution.
- **Commodities:** NASA weather and USDA yields for grains and softs, with
  staged, ablation-first backtests.
- **Real estate:** point-in-time REIT property exposure from filings, and
  satellite built-up area (GHSL) as a supply signal.
- **Macro reports:** UK and global short rates (market vs model), shorting
  bonds during capex booms.

---

*Research code, not investment advice. Every result above that was not
pre-registered is labeled exploratory.*
