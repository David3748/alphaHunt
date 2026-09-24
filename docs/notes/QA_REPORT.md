# QA Report — numeric audit of `docs/index.html`

Auditor: quantitative fact audit, 2026-08-24.
Method: chapter-by-chapter extraction of every numeric claim from `index.html`, compared against primary artifacts (`lab_runs/live_2026/predictions.json`, `lab_runs/live_2026/outcomes_forward.json`, `lab_runs/blind_rescore/results.json`, `lab_runs/sealed_safety/results.json`, `lab_runs/fresh_confirmation/results.json`, `lab_runs/strategy_experiments/report.md`, `lab_runs/live_2026/enumeration.json`, `lab_runs/obscure_miner/analyses.jsonl`, `docs/data/*.json`, `PROGRESS.md`, `STRATEGY_REVIEW.md`, `CENTURY_HYPOTHESES.md`, `dossiers/PRCT.md`, plus VM-reported century results supplied with the audit brief). Embedded `DATA` payload parsed and diffed against `docs/data/*.json`.

**Verdict legend:** EXACT · ROUNDING (rounding-consistent) · DERIVED (exact arithmetic from two artifacts) · **MISMATCH** · UNVERIFIABLE

## Claim-by-claim audit

| # | Claim (index.html) | Location | Artifact value | Verdict |
|---|---|---|---|---|
| 1 | ~125,000 LLM calls | meta description; kicker | Component sum of listed runs ≈ 119k–130k (century model layer 69,195; sealed 8,565+3,426; live 4,980+1,992; blind 1,100; fresh 1,140; comprehensive 2,860; legacy ≈880; miner ≤3×11,873=35,619). No single artifact states a total | UNVERIFIABLE (plausible "~") |
| 2 | 350,456 SEC filings | meta; §①; Fig3; funnel row; pipeline fig; Ch4 | PROGRESS.md: "350,456 filings"; funnel row 350,456 | EXACT |
| 3 | 15,387 issuers | §① | PROGRESS.md: "from 15,387 issuers" | EXACT |
| 4 | 25,343 symbol regimes | §②; pipeline fig | PROGRESS.md: "Resolve 25,343 historical symbol regimes" (duplicate statements agree) | EXACT |
| 5 | 10,881 events scored | meta; statrow | 9,885 (century eligible) + 996 (live scored) = 10,881 | DERIVED |
| 6 | 1/4 original gates passed | statrow | Sharpe and maxDD gates demonstrably failed (see #36–37); full four-gate list not recorded locally | UNVERIFIABLE (directionally consistent) |
| 7 | 3/3 pre-registered secondaries confirmed | statrow; Ch4 table | Exactly three LLM hypotheses in CENTURY_HYPOTHESES.md; three PASS rows | CONSISTENT |
| 8 | Live cohort negative, n=40 | statrow; Ch7 | outcomes_forward.json: 40 status="ok" trades; all three rule means negative | EXACT |
| 9 | Placebo reruns whole simulation 500 times | Ch1; §⑦; Fig4 | CENTURY_HYPOTHESES.md: "Five hundred draws… per rule" (duplicates agree) | EXACT |
| 10 | ~7 calls per event | Fig2 caption | 5 extractors + 2 syntheses = 7 | EXACT |
| 11 | Stage⑤ 49,425 calls | pipeline fig | 9,885 × 5 = 49,425 | DERIVED |
| 12 | Stage⑥ 19,770 calls | pipeline fig | 9,885 × 2 = 19,770 | DERIVED |
| 13 | ~163k chars per case | pipeline fig | No artifact; in tension with §④ "up to ~220k characters" cap | UNVERIFIABLE |
| 14 | Evidence pack up to ~220k chars | §④ | No artifact | UNVERIFIABLE |
| 15 | Grounding 65–75%; lens rates 70/75/68/~72/65% | §⑤; lens cards | Only global rates found: 64.4% (fresh_confirmation), 68.7% (comprehensive_long). Lens-level values absent | UNVERIFIABLE |
| 16 | Gates: ≥120 closes, ≥$1, ADV≥$1M, ≤−40% DD, 180d cooldown, 370d history | §③ | Matches funnel rejection rows and protocol conventions | CONSISTENT |
| 17 | Costs 25bp/side; T-bill cash; exposure-matched SPY; expanding top-decile | §⑦; Ch4 | CENTURY_HYPOTHESES.md identical conventions | EXACT |
| 18 | 4 GB raw → 804 MB zstd archives | §⑧ | Local `.zst` files total 46.5 MB (subset); archive manifest lives on VM/Drive | UNVERIFIABLE |
| 19 | Dilution forecast: 120 events, development-only | Ch3 table | PROGRESS.md: "120 cases… not production proof"; dilution90 | EXACT |
| 20 | False-distress long: 100 events; failed sealed validation; **AUC .57**; top decile −3.7% | Ch3 table | 100 ✓; sealed-validation AUC **0.614** (PROGRESS.md long_dev); top decile −3.7% ✓. 0.566 belongs to the separate comprehensive_long run | **MISMATCH (AUC)**; rest EXACT |
| 21 | Catalyst + vetoes: 80 events, not significant | Ch3 table | report.md: p=0.439 (NS ✓) on the 100-case long cohort; no 80-event artifact | significance CONSISTENT; **count UNVERIFIABLE** |
| 22 | Consensus-adjusted probability: 80 events, not significant | Ch3 table | report.md: p=0.402 (NS ✓) on same 100-case cohort | significance CONSISTENT; **count UNVERIFIABLE** |
| 23 | Safety-first fresh 2021–23: 160 events, confirmed, p=.003 | Ch3 table | fresh_confirmation/results.json: n=160, p=0.0029 | ROUNDING |
| 24 | Sealed safety 2019–20: 1,713 events, validated, p≈.003 | Ch3 table | sealed_safety/results.json: scored_cases 1713, validated=true, placebo p_one_sided=0.002997 | EXACT |
| 25 | Half of sealed signals in Mar–May 2020; 55 in May 2020 alone | Ch3; Fig1 caption+label | STRATEGY_REVIEW.md: "98 of 188 signals fired in March–May 2020 (55 in May alone)" — 52% ≈ half | ROUNDING / EXACT |
| 26 | Eligibility = ≥40% off 52-week high | Ch3 | Protocols use 370-day/1-year high, −40% threshold | CONSISTENT |
| 27 | 350,456 → 9,885 eligible events, 2009–2025 | Ch4; Fig3 | PROGRESS.md + funnel row | EXACT |
| 28 | Roughly 69,000 model-layer calls | Ch4 | 49,425 + 19,770 = 69,195 | ROUNDING |
| 29 | Deployed rule Sharpe 0.74 vs required 1.0 | Ch4 | VM-reported primary gates (audit brief): Sharpe 0.74 vs >1. Note: perf.json stores a differently-based Sharpe 0.81 ("vs zero") | EXACT vs brief |
| 30 | Max drawdown −31.8% vs −25% required | Ch4 | perf.json stats.deployed.maxdd = −31.8; brief: fails >−25% gate | EXACT |
| 31 | 319 trades (deployed) | perf table | Brief (VM-reported): 319 trades | EXACT vs brief |
| 32 | Secondary IRs: p(+20) +1.53 PASS; causal_blend +1.52 PASS; upside×drawdown +1.21 PASS; deep_drawdown −0.09 fail | Ch4 table | VM-reported century results: +1.53 / +1.52 / +1.21 / −0.09 | EXACT |
| 33 | Placebo p: ≲.002 (three rules), .11 (mechanical) | Ch4 table | Century hypothesis results written to Drive/dashboard; no local artifact | UNVERIFIABLE |
| 34 | Bonferroni across exactly three hypotheses; discovery years excluded | Ch4 footnote | CENTURY_HYPOTHESES.md: α=0.0167, 2019–20 excluded from confirmation | EXACT |
| 35 | Equity curves rebased to $1 on July 1, 2009 | Ch4 | perf.json dates[0] = 2009-07-01 | EXACT |
| 36 | CAGR 47.0 / 14.9 / 15.1 / 2.6 % | Fig5 table | perf.json stats.cagr 47 / 14.9 / 15.1 / 2.6 (embedded copy byte-identical to data/perf.json) | EXACT (47 rendered as 47.0) |
| 37 | Volatility 27.6 / 19.6 / 17.1 / 6.6 % | Fig5 table | perf.json vol — identical | EXACT |
| 38 | Sharpe (vs zero) 1.53 / 0.81 / 0.91 / 0.43 | Fig5 table | perf.json sharpe0 — identical | EXACT |
| 39 | Max drawdown −26.9 / −31.8 / −33.7 / −23.9 % | Fig5 table | perf.json maxdd — identical | EXACT |
| 40 | Trades taken 269 / 319 / 1 / 1 | Fig5 table | Not present in data/perf.json. 319 matches brief; 269 conflicts with PROGRESS.md site-export ("270 trades") | UNVERIFIABLE (+minor discrepancy) |
| 41 | Fig5 caption: 47%/yr; −27% worst DD; SPY 15.1%/−34%; Treasuries 2.6%/yr | Fig5 caption | 47.0; −26.9→−27; 15.1; −33.7→−34; 2.6 | ROUNDING |
| 42 | "Deployed… still beat nothing-to-do on return but with worse risk than the index" | Fig5 caption | Deployed CAGR 14.9% < SPY 15.1% (beats only IEF 2.6%); vol 19.6>17.1 worse, but maxDD −31.8% is shallower (better) than SPY's −33.7% | **MISMATCH (imprecise)** |
| 43 | Ten 10% slots | Ch4; §⑦ | CENTURY_HYPOTHESES.md: "ten 10%-NAV slots" | EXACT |
| 44 | Blind test: 110 cases re-scored | Ch5 | blind_rescore/results.json: n=110 | EXACT |
| 45 | Blind Spearman .393 vs identified .391 | Ch5 table | 0.39313 / 0.39073 | ROUNDING |
| 46 | Blind AUC .715 [.62–.81]; identified .701 | Ch5 table | 0.71495 [0.61632, 0.80787]; 0.70090 | ROUNDING |
| 47 | Blind-vs-identified agreement ρ = .836 | Ch5 | score_agreement_spearman 0.83584 | ROUNDING |
| 48 | Verdict: genuine | Ch5 | results.json verdict "GENUINE" | EXACT |
| 49 | DT: Feb 9 2026; −46% from high; score 26.5 vs threshold 21.5; long_candidate | Ch6 №1 | predictions.json: cutoff 2026-02-09, drawdown_pct −46.0, p20 26.5, thresholds.p20 21.5, decision long_candidate | EXACT |
| 50 | DT outcome: +11.2% stock vs +6.8% SPY, +4.4 excess | Ch6 №1 | outcomes_forward.json (DT/p20): 11.2 / 6.8 / 4.4 | EXACT |
| 51 | DT "only graded winner in the lead rule's book" | Ch6 №1 | p20 trades: DT sole positive of 7; win_rate 0.14 | EXACT |
| 52 | WIX: Mar 5 2026; −58% from high; score 27.0; long_candidate; third-highest | Ch6 №2 | predictions.json: drawdown −58.3, p20 27.0, decision long_candidate; rank 3 of board | ROUNDING (−58.3→−58) / EXACT |
| 53 | WIX outcome: −42.8% vs +12.9%, −55.7 excess | Ch6 №2 | outcomes_forward.json (WIX): −42.8 / 12.9 / −55.7 | EXACT |
| 54 | WIX context: seven graded trades, one winner, mean −35% | Ch6 №2 | p20: n=7, win_rate .14, mean −35.0 | EXACT |
| 55 | Miner: 11,873 documents analyzed | Ch6 №3 | obscure_miner/analyses.jsonl: 11,873 rows | EXACT |
| 56 | Gate "rejected 88%" | Ch6 №3 | 10,415 reject decisions (10,414 "reject" + 1 "REJECT") / 11,873 = 87.7% | ROUNDING |
| 57 | Kept 1,438 watches | Ch6 №3 | watch decisions = 1,438 (plus 19 records with no parseable consensus decision — unmentioned) | EXACT |
| 58 | Passed exactly one long candidate: PRCT | Ch6 №3 | long_candidate decisions = 1 (PRCT) | EXACT |
| 59 | Nov 2019 Federal Register rule; FR 2019-24138 | Ch6 №3 | Dossier + miner record: federalregister.gov/documents/2019/11/12/2019-24138 | EXACT |
| 60 | CMS quote verbatim ("After consideration… CY 2020.") | Ch6 blockquote | dossiers/PRCT.md verified quote — word-for-word match | EXACT |
| 61 | Stock carried to $99 peak | Ch6 №3; chart label | prices.json prct peak 99.45 (2024-12-04) | ROUNDING |
| 62 | Now trades at $21 | Ch6 №3 | last close 21.68 (2026-08-21); dossier 21.57 | ROUNDING |
| 63 | Down **76%** from peak | Ch6 №3; dossier header flag | 1 − 21.68/99.45 = **−78.2%**; dossier itself says −78% | **MISMATCH** |
| 64 | PRCT "since IPO (Oct 2020)"; chart label "IPO $41.94" | Fig caption; chart | Series starts 2021-09-15 at 41.94 (first plotted close, not an IPO print); no Oct 2020 data | **MISMATCH (date)** |
| 65 | HYDROS ramp / per-account utilization, strengthened AUA guidelines, WATER IV enrollment complete, Q4 2026 positive adj-EBITDA guide | Ch6 №3; figcaption | dossiers/PRCT.md confirms each item | CONSISTENT |
| 66 | Live: 12,135 filings, January–June 2026 | Ch7 | enumeration.json: filings 12135, 20260102–20260630 | EXACT |
| 67 | Filtered to 996 eligible events, scored before outcomes | Ch7 | predictions.json live_cases_scored = 996 | EXACT |
| 68 | p(+20%): 7 graded, −35.0% mean, 14% win | Ch7 table | outcomes_forward summary p20: n 7, mean −35.0, win 0.14 | EXACT |
| 69 | Upside×drawdown: 20 graded, −9.0%, 25% win | Ch7 table | uxd: n 20, mean −9.0, win 0.25 | EXACT |
| 70 | Causal z-blend: 13 graded, −13.1%, 31% win | Ch7 table | blend: n 13, mean −13.1, win 0.31 | EXACT |
| 71 | Forty trades across three rules; TGEN +152 | Ch7 figcaption | 7+20+13 = 40; TGEN excess 152.5 | EXACT / ROUNDING |
| 72 | Standard error of lead-rule mean "±18 points" | Ch7 figcaption | From the 7 p20 excesses: SD 23.62 → **SE = ±8.9 pts** (±18 ≈ 2SE) | **MISMATCH** |
| 73 | Standing signals from June 30 cutoff | Ch8 | enumeration end 20260630; board dates ≤ 2026-06-15 | EXACT |
| 74 | Board rows (12 tickers, dates, scores) | Ch8 table | DATA.board.p20 === predictions.json board.p20 (scores 36→21, dates identical) | EXACT |
| 75 | Column header "Score (thr 21)" | Ch8 table | Thresholds are 21.0 for 10 rows, 21.5 for DT and REPL | ROUNDING (minor) |
| 76 | All three rules converge on RCKT, VRDN, WIX, DT | Ch8 | Present in p20, uxd, and blend boards | EXACT |
| 77 | Decision field flags WIX, ZG, INTU, GWRE as long_candidate | Ch8 | Artifact flags **six** names in the lead rule alone: WIX, DT, ZG, IMRX, INTU, GWRE (DT in all three rules; SFM in uxd; OLLI/ESTC/BBW/NG/ROP/NTNX/FICO in blend) | **MISMATCH (incomplete list)** |
| 78 | Remaining windows mature through November | Ch8 pull-quote | Latest cutoff 2026-06-15 + 90d ⇒ mid-September (June 30 + 90d ⇒ Sep 28). No artifact reaches November | UNVERIFIABLE / unsupported |
| 79 | Drawdown column (−42..−72% expected) | Ch8 table | predictions.json drawdown_pct spans −42.3..−72.3, but build drops `ddp`; column renders empty | Build defect (data dropped) |
| 80 | Data cutoff June 30, 2026; written August 24, 2026 | disclaimer | enumeration end + predictions generated_at 2026-08-24 | EXACT |

### Duplicate-statement consistency
350,456 (×6 locations), 9,885 (×4), 25,343 (×2), 92,331 (×2), 55-May-2020 (×2), 500 shuffles (×3), ~125k calls (×2), 47% (×2), −31.8% (×2), DT/WIX scores (Ch6/Ch8/outcomes), TGEN +152/+152.5 (×2), 40 trades / n=40 (×2) — **all duplicate statements agree**.

### Funnel sum check
Rejections: 148,890 + 92,331 + 40,839 + 35,411 + 9,913 + 6,113 + 5,435 + 1,639 = **340,571**.
350,456 − 340,571 = **9,885** = stated eligible events. **Exact equality holds.**

### Perf-table vs `data/perf.json`
All 16 statistic cells (4 metrics × 4 series) match exactly; embedded `DATA.perf` is byte-identical to `docs/data/perf.json`. Trade counts are not stored in `perf.json` (see #40).

---

## MISMATCHES

1. **False-distress AUC ".57" (Ch3 table).** The 100-case sealed validation in `lab_runs/long_dev` (per PROGRESS.md) had **AUC 0.614** with top-decile −3.7%. 0.57 conflates either the separate comprehensive_long rerun (AUC 0.566, top-4 −2.6%) or the sealed-*safety* deployed ranker (AUC 0.574).
   *Suggested wording:* "failed sealed validation — AUC .61, top decile −3.7%".
2. **PRCT "down 76% from peak" (Ch6 №3).** Charted series gives 1 − 21.68/99.45 = **−78.2%**; the dossier itself says −78%.
   *Suggested wording:* "down 78% from peak".
3. **PRCT caption "(Oct 2020)" and chart label "IPO $41.94".** The plotted series starts 2021-09-15; $41.94 is the first plotted close, not an October-2020 IPO print.
   *Suggested wording:* "PRCT since its Sep 2021 listing (first close $41.94)".
4. **Forward-chart standard error "±18 points" (Ch7 figcaption).** Sample SE of the seven p(+20%) excesses is **±8.9 points**; ±18 is two standard errors.
   *Suggested wording:* "…the standard error on the lead rule's mean alone is ±9 points".
5. **Ch8 long_candidate list omits DT and IMRX.** The model's decision field marks six names `long_candidate` on the lead rule's own board (WIX, DT, ZG, IMRX, INTU, GWRE); DT carries the label in all three rules.
   *Suggested wording:* "The model's own decision field flags WIX, DT, ZG, IMRX, INTU, and GWRE as `long_candidate`."
6. **Fig3 caption "The two largest cuts — no usable price history and non-US/unresolvable listings."** By magnitude the second-largest cut is the drawdown gate (92,331 > 40,839 non-US).
   *Suggested wording:* "The largest cuts — no usable price history, the drawdown gate, and non-US listings — are where dead and foreign companies live…"
7. **Fig5 caption "deployed… still beat nothing-to-do on return but with worse risk than the index."** Deployed CAGR 14.9% trails SPY's 15.1%, and its −31.8% maxDD is shallower than SPY's −33.7%; only volatility (19.6 vs 17.1) and Sharpe (0.81 vs 0.91) are worse.
   *Suggested wording:* "…earned bond-like-plus returns with equity-like volatility — more risk per unit of return than the index (vol 19.6% vs 17.1%, Sharpe 0.81 vs 0.91)".

---

## UNVERIFIABLE

1. **~125,000 LLM calls** (#1) — no aggregate counter exists; component sums span ≈119k–130k depending on retry/miner accounting.
2. **Placebo p-values ≲.002 / .11** (#33) — century hypothesis results were written to Drive/dashboard; no local artifact.
3. **Trades-taken row 269 / 319 / 1 / 1** (#40) — absent from `data/perf.json`; 319 matches the VM report, but 269 conflicts with PROGRESS.md's site-export figure of 270 P(+20%) trades.
4. **"1/4 original gates passed"** (#6) — the four primary gates and their pass/fail bits are not recorded locally (only Sharpe and maxDD failures are documented).
5. **Evidence-pack character counts** "~163k chars each" (fig) vs "up to ~220k characters" (§④) (#13–14) — no artifact; the two figures are unreconciled (mean vs cap is plausible but unstated).
6. **Per-lens grounding percentages** 70/75/68/~72/65 within "65–75%" (#15) — only corpus-global rates (64.4%, 68.7%) exist locally.
7. **Archive sizes 4 GB → 804 MB** (#18) — local `.zst` total is 46.5 MB (partial mirror); authoritative manifest is on VM/Drive.
8. **Event counts "80" for catalyst+vetoes and consensus-adjusted rows** (#21–22) — both experiments ran on the documented 100-case long cohort; no 80-event artifact found.
9. **"Windows mature through November"** (#78) — latest observable window ends late September 2026; nothing in the artifacts reaches November.
10. **Miner tally residue** — 19 of 11,873 analyzed documents have no parseable consensus decision; the page's reject/watch/long partition silently omits them (10,415 + 1,438 + 1 = 11,854 ≠ 11,873).

---

## Chart render status: **FAIL — 0 of 9 render**

Headless execution of the page's inline script (document stub per audit spec):

```
SCRIPT ERROR: Cannot read properties of undefined (reading 'length')
chart-crash      MISSING
chart-pipeline   MISSING
chart-funnel     MISSING
chart-clock      MISSING
chart-dt         MISSING
chart-wix        MISSING
chart-prct       MISSING
chart-perf       MISSING
chart-forward    MISSING
```

Root cause — `assets/charts.js` (embedded verbatim in `index.html`) expects flat data keys, but `build.py` nests payloads by filename:

| charts.js expects | build.py injects | Result |
|---|---|---|
| `DATA.spy_weekly` | `DATA.prices.spy_weekly` | `undefined` → TypeError at `SPY.length` (chart-crash guard) kills the entire script |
| `DATA.crash` | *(no `crash.json`; monthly counts sit in `cohorts.json`)* | `undefined` |
| `DATA.prct` | `DATA.prices.prct` | guarded `typeof` check → chart silently skipped |
| `BOARD.forEach(...)` with `r.ddp` | `DATA.board` is `{p20,uxd,blend}` (no `forEach`), rows lack `ddp` | board table would break again even if the script survived; drawdown column has no data source |

Consequences in any browser: all nine figures blank, Chapter 8 board `<tbody id="board-body">` permanently empty, and the "Model verdict" column header has no corresponding cell writer. Fix: reshape injection in `build.py` (hoist `spy_weekly`, add `crash` from `cohorts.json`, hoist `prct`, flatten the lead-rule board rows with `ddp` and `decision`) or update `assets/charts.js` to the nested shape, then rebuild. Cosmetic: leftover `<!--CHARTS-DATA-->` placeholder comment at line 213.

---

### Audit summary
- Claims checked: **80** numbered rows above (plus duplicate-agreement sweep).
- Mismatches: 7 (items 1–7 in MISMATCHES).
- Unverifiable: 10 groups (items 1–10 in UNVERIFIABLE).
- Charts: **FAIL — 9/9 MISSING** (script throws before first render; board table also dead).
