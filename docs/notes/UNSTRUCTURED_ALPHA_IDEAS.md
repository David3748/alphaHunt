# Unstructured Alpha Source Ideas — 10-Agent Research Synthesis

Date: 2026-08-24
Method: 10 parallel research agents, each probing sources live (webfetch/curl) to verify availability, then ranked by (a) alpha plausibility, (b) point-in-time backfillability, (c) join-to-structured-data difficulty, (d) engineering effort, (e) uncorrelation with existing GDELT/FedReg/ClinicalTrials/openFDA/news mining.

## Theme 1: Event-conditioned distress & recovery (fits your proven AUC-0.63 dilution-conditioned playbook)

1. **Chapter 11 first-day declarations (CourtListener/RECAP)** — sworn CEO testimony filed within days of petition: cash runway in days, DIP financing, vendor credit terminations, unencumbered assets. LLM extracts distress-severity + recovery-asset vector. Join: 8-K Item 1.03 = debtor→CIK bridge. Hypothesis: filers with ≥60d runway + material unencumbered assets outperform filers with <30d runway over 90d. RECAP free (PACER $0.10/page, waived ≤$30/qtr). Backfill ~2013+. Effort M. *Highest edge density per document.*

2. **openFDA recall severity conditioning** — Class I recalls on a firm's top-3 revenue product → short 20d. `recall_initiation_date` + `report_date` both in-record; backfill to ~2004. Cleanest join (NDC/ANDA → ANDA holder → CIK). Effort S-M. Uncorrelated: most recalls get zero news coverage until weeks later.

3. **CPSC recalls (fire/burn/child-injury → short, labeling-only → long)** — SaferProducts REST API, 25y backfill, ~300-400/yr low volume, S effort. Consumer small caps named directly.

4. **OSHA willful/repeat citations >$100k → short 40d** — repeat-offender conditioning. M-L effort, no clean API.

5. **EPA ECHO formal enforcement cases (2+ facilities → short 60d)** — L effort, plant-name→parent resolution is the project. Industrial SIC 12-49 small caps.

6. **PHMSA pipeline incidents ≥$10M damage or fatality → short 45d** — operator-caused vs third-party conditioning. M effort.

7. **DOJ DPAs / FCPA Clearinghouse / EEOC suits** — monitor-imposition vs self-report conditioning. S-M effort, exact datelines. Mega-cap skew — filter to cap band.

## Theme 2: Patents & IP (biotech/deeptech skew)

1. **PTAB IPR institution decisions → short target's key-patent stock** — competitor-petition = stronger negative readthrough than NPE; claim-cancellation fraction scales the move. Full history since AIA 2012, date-stamped. Join via assignment chain (ODP). M effort. *Biotech small caps move -10% on these.*
   - Watch: USPTO Open Data Portal (ODP) is the new home — legacy APIs retired Aug 2025/26.

2. **USITC §337 investigations via your existing Federal Register pipe** — S effort (classifier + entity extractor on a feed you already ingest). Named respondents are operating companies. Escalation ladder: institution → ID → final.

3. **USPTO assignment/license recordation stream** — exclusive license by top-20 pharma with no matching PR yet = positive drift; assignment to NPE = negative. Recordation lags execution by weeks → structurally ahead of wires. M-L effort (name-matching pipeline dominates).

4. **Claim-scope narrowing during prosecution** — families with ≥50% claim-count reduction publication→grant underperform ≥200bp/90d. L effort, near-zero crowding.

5. **TTAB oppositions by mega-caps against small-cap brands** — S-M effort, condition hard on opposer size + brand centrality.

## Theme 3: Corporate communications beyond filings

1. **Wayback CDX diffing of leadership/pricing/product pages** — silent exec removals, product-page deletions, pricing-tier edits *before* the 8-K. Snapshot density sufficient for microcaps post-2021 (verified latch.com 1,100-3,700/yr). M effort. *The gold-standard PIT source: content-addressed, timestamped.*
2. **StockAnalysis.com conference-transcript appearances** — first-ever flagship conference appearance (JPM/GS) = informed third-party vote → positive 90d. S effort, 2021+.
3. **Earnings-call language deltas** (API Ninjas back to 2005 incl. delisted; Motley Fool free) — hedging-rate increase >1σ vs own 8-quarter baseline → negative 90d after controlling day-1 reaction. Your AUC-0.51 lesson: levels fail, deltas + conditioning work.
4. **GitHub engineering velocity** (commit-cadence collapse pre-earnings) — dev-focused small-cap overlay only, ~5-15% of universe. S-M.
5. **Unscheduled IR-deck replacements mid-quarter** (CDX PDF discovery) — telegraphs capital raises. M-L, post-2021.

## Theme 4: Trade & physical supply chain (all verified)

1. **Tariff/AD-CVD shock × 10-K-disclosed sourcing country** — LLM extracts supplier-country + HTS exposure from 10-Ks; condition on new tariff lines/petitions landing on that HTS×country cell (FR = event timestamp). >500bps/90d hypothesis for single-country-sourced small caps. M effort. *The structured↔unstructured bridge you asked about.*
2. **USDA export-sales surprises → ag/food/fertilizer micro caps** — weekly Thursday 8:30am prints; join via LLM-extracted commodity revenue mix. S-M. PIT gold (`load_time` columns).
3. **openFDA import-refusal spikes → sourcing-country risk** — free, keyless, dated records ~2012+. S effort. Refusal spike → short; rival-country refusals → long (share-gain).
4. **IMF PortWatch port disruptions → logistics small caps** — daily hours-at-anchor, disruptions, free ArcGIS Hub, ~2019+. M effort.
5. **EIA PAD-district stock/import anomalies → Gulf Coast refiners** — weekly, decades. S effort. Weakest edge (partially crowded).

**Trap:** no free company-level bills-of-lading exists (CBP sells nothing; ImportGenius/Panjiva/Datamyne all paywalled; vendor histories are retro-added = not PIT). AIS is forward-only. Don't build on these.

## Theme 5: Consumer/community (verified live)

1. **Steam review-corpus event study** — FULL PIT backfill to 2013 via cursor-walk (`timestamp_created` on every review). Review-bomb onset (7d negative share >60% + 3× volume) → 90d underperformance for gaming publishers (SNAL, MSGM, Embracer, TTWO). S-M. *Only consumer source with true historical reconstruction.*
2. **App Store review RSS (iTunes)** — forward-only (~500 recent), daily polling reconstructs rating history from now. Payout-friction complaints for SKLZ-tier apps precede guidance misses. S.
3. **Steam CCU decay** — flagship launch: peak days 8-30 < 40% of days 1-7 → underperform 90d. Backfillable via steamcharts. M.
4. **Niche product subreddits** — forward-only, datacenter-IP blocked, fragile. Passive only.
5. **Yelp Fusion daily delta tracker** — 500 calls/day, rating-drop ≥0.2★ + LLM illness themes → 90d underperformance for restaurants (TXRH, CAKE, DIN, BJRI). Forward-only.

**Traps:** Trustpilot (robots `Disallow: /`), Amazon reviews (bot-walled + ToS), Google Play (no API), Discord (no metrics), Sensor Tower (paid).

## Theme 6: Government money & political access

1. **USAspending first-ever prime contract ≥10% market cap** — keyless API, backfill to FY2000. Must gate on `last_modified_date` (modifications retro-tag original action dates — the classic leak). M effort.
2. **SBIR Phase II→III commercialization ladder** — bulk CSV from sbir.gov back to 1983. First non-SBIR obligation after Phase II = inflection. S-M.
3. **Senate LDA lobbying filings** — 1.98M filings back to 1999, `dt_posted` datetimes, nested specific bills (H.R./S.#). First-ever registration + bill-specific pivot = regulatory catalyst positioning. M effort. Lobbying never generates headlines → orthogonal to your news miner.
4. **STOCK Act congressional trades** — 30-45d disclosure lag (condition on filing date, never trade date). Capitol Trades unofficial API is fragile (429). S if tolerated.
5. **FEC employee donation surges** — employer strings are garbage; weakest of the five, ensemble feature only.

**Trap:** SAM.gov debarment = rare-event imbalance, risk-screen only.

## Theme 7: Labor & human capital

1. **ATS job-board APIs (Greenhouse/Ashby) + Wayback reconstruction** — posting-freeze after surge, no layoff 8-K = guidance cut ~1 quarter later. Native APIs forward-only; CDX backfill verified (per-job-id pages since 2019). M effort.
2. **DOL H-1B LCA disclosures** — new worksite cities/titles = expansion intent (R&D center openings months early); volume cliff = visa-dependent hiring freeze. Fully backfillable quarterly, 3-6mo lag. M. (dol.gov is bot-walled — use h1bdata.info/USCIS Hub.)
3. **WARN notices** — 60-day early warning vs press; CA/IL/TX histories; layoffs.fyi tech-only & retroactively backfilled (attention bias — not true PIT). M effort.
4. **8-K Item 5.02 exec departures × ATS re-posting check** — CFO exit + role not re-posted in 30d = stewardship gap. S effort, fully backfillable, zero matching cost.
5. **BLS JOLTS quits as regime conditioner** — labor-intensive names underperform in tight-labor regimes. S. Weak alone.

**Trap:** Glassdoor confirmed dead (403 bot wall + ToS).

## Theme 8: Attention & physical world

1. **NOAA Storm Events narrative severity → P&C loss-reserve conditioning** — 60K events/yr with free-text narratives, county FIPS, minute timestamps, back to 1996, versioned cut-dates = perfect PIT. LLM scores severity (structure damage vs tree-down) beyond the coarse dollar field. Geo-join (deterministic — no name matching). S-M. *Underpriced mid-size cat accumulation until reserve charges are disclosed.*
2. **Wikipedia pageview z-scores + edit-war anomalies** — daily history to 2015, free, keyless; Wikidata P249 = ticker join. z>3 on <$500M caps with no same-day SEC/news flow → abnormal drift. S (pageviews) / M (edit analysis).
3. **USDA Quick Stats + Drought Monitor → crop insurers/grain handlers** — weekly, `load_time` = exact PIT timestamps; USDM archives every weekly map since 2000. S-M.
4. **GDELT Events DB deep cuts** (CAMEO codes, Goldstein scores, per-actor tone) — structured layer, not doc discovery; treat as enrichment of your existing GDELT hits. M-L (actor→ticker join quality is the work).
5. **Internet Archive TV News closed-caption search** — potentially the highest novelty (micro-cap TV mentions are unmined) but API unverified from this environment; confirm before building. M-L.

**Traps:** Google Trends (pytrends archived, dead), satellite imagery (free data but per-site pipelines = effort trap), Chronicling America (historic-only), FAA (weak).

## Theme 9: Courts, class actions, derivative suits

1. **Securities class actions — fraud-theory conditioning** (SCAC + RECAP): accounting-fraud complaints → deeper drift than disclosure-only; original-vs-already-priced distinction. SCAC bot-gated (polite scraper/Wayback). Backfill to ~1996. M.
2. **Derivative suits vs already-crashed stocks** — derivative suit on a stable stock (governance surprise) → -8-15% 90d; on crashed stocks → no added drift. CourtListener webhooks (free alerts) = forward PIT. S-M.
3. **WDTX/EDTX patent-docket radar** — NPE suits fully priced at open; operating-company willful suits → -5% 90d drift. M.

**Cross-cutting traps:** RECAP archive is user-driven (attention bias); two clocks everywhere (`dateFiled` vs ingest time — use dateFiled + 1 day); Delaware Chancery is outside RECAP/PACER (paywalled, trap); FINRA BrokerCheck API auth regime changed (403 — skip).

## The Cross-Cutting Insight

**Entity resolution is the moat.** Nearly every agent independently concluded: the highest-alpha sources fail on name→CIK/ticker joining, not on data access. All 10 recommend building ONE shared subsidiary→CIK alias table (from EDGAR 10-K Exhibit 21 trees + `company_tickers.json` + OpenCorporates + human-reviewed golden set), amortized across every source above. Budget more effort there than on ingestion.

## Recommended Build Order (highest edge-per-effort, respecting your sealed-validation discipline)

| Tier | Idea | Effort | Why |
|---|---|---|---|
| 1 | CPSC recalls (S) + openFDA recalls (S-M) | S-M | Clean joins, full PIT, zero news overlap |
| 2 | PTAB IPR institution events (M) | M | Violent biotech moves, date-stamped history |
| 3 | USITC §337 via existing Federal Register feed (S) | S | Classifier on a feed you already ingest |
| 4 | 8-K 5.02 × ATS re-posting check (S) | S | Zero matching cost, fully backfillable |
| 5 | NOAA storm narratives (S-M) | S-M | Deterministic geo-join, no name matching |
| 6 | First-day declarations (M) | M | Highest edge per document in litigation space |
| 7 | Wayback leadership-page diffs (M) | M | The PIT gold standard; microcap coverage verified |
| 8 | USDA export sales (S-M) | S-M | PIT timestamps built in |
| 9 | Steam review backfill (S-M) | S-M | Only true-PIT consumer source |
| 10 | SBIR Phase II→III (S-M) | S-M | Pipeline logic like ClinicalTrials, disjoint catalyst class |

**Regime layers (S effort, weak alone, multiply everything):** BLS JOLTS quits, EIA district data, GDELT Events deep cuts.

**Build the alias table first, then anything above becomes cheap.**