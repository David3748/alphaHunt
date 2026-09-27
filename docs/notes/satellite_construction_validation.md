# Construction stall validation, September 26, 2026

The inherited exterior-stall signal does not establish investable alpha. A
causal replay of the supplied measurement rows produces a Denton warning on
October 8, 2025 only when each composite is treated as available at its endpoint
scene's publication time. That assumption fails: the season-matched baseline
uses asset versions published in August 2026. Delaying composites until all
**known** constituent versions were published removes Denton's warnings. Even
this guarded replay cannot certify historical availability because the CSVs
omit complete scene lineage and some initial scenes.

APLD Ellendale remains a concrete counterexample to treating an exterior pause
as a delayed delivery. Its guarded replay warns on March 22, 2025, but Applied
Digital subsequently reported the first 50 MW ready for service on time on
October 27 and the full 100 MW on time on November 24. These are issuer-reported
operating milestones, not independent inspections. They still disprove a claim
that the measured pause by itself demonstrates a late first building.

## Reproduce

```sh
python3 src/satellite_construction_validation.py --as-of 2026-09-26
python3 -m pytest -q tests/test_satellite_construction_validation.py
```

The replay reads the nine committed construction CSVs, representing eight
independent campuses; Denton's second box is a sensitivity check. It writes
results to `results/satellite_validation/construction/`:

- `summary.json`: rule, input SHA-256 hashes, limitations and per-site statistics.
- `input_timestamp_audit.csv`: one row per legacy composite, endpoint publication,
  latest known constituent publication, and the lower bound on availability.
- `replay_<site>_<mode>.csv`: every decision, freshness/coverage eligibility,
  last material exterior progress, and warning state.
- `alert_episodes.csv`: first warning for each no-progress record, including
  episodes still open at the cutoff.
- `site_summary.csv` and `threshold_sensitivity.csv`: controls and 90/120/150/180
  day thresholds. Threshold variants are diagnostics, not searched investments.
- `verified_event_sources.json`: a primary-source event ledger that separates
  event date, document date, verified claim, and unresolved attribution.

## Rule and timing

The rule reuses the inherited minimum underway area of 1 ha and material-progress
step of max(0.3 ha, 5% of the previous record). An exterior pause requires 120 days
without a new material record, at least three published observation endpoints in
the last 75 days, and a latest image no older than 30 days. Alerts happen only
when a new composite becomes available; there are no alerts invented during
unobserved cloud/snow gaps. A one-day processing lag follows publication, and
site identity cannot be acted on before the day after `public_since`.

For `endpoint_only`, availability is endpoint `created` plus one day. This is an
explicitly optimistic diagnostic, not a historical trading simulation. For
`dependency_guard`, availability is the latest `created` among **exported**
endpoint scenes in the trailing 75-day window and seasonal baseline, plus one
day. Those scenes are known dependencies of the legacy composite. Missing
constituents could push actual availability later. We cannot remove unavailable
pixels from the precomputed rounded CSV measurements. Both modes therefore set
`strict_point_in_time_certified=false` on every decision.

Delayed arrivals are processed in publication order and cannot rewrite an old
warning. At each decision only currently available measurements are examined.
The record state is rebuilt from those measurements in acquisition order, so a
late old scene cannot reset the progress clock to its publication date. Future
input append invariance, missing-timestamp rejection, identity gating, processing
lag, episode deduplication, and stale-data abstention are tested.

## Results

| Campus | Endpoint-only episodes | Dependency-guard episodes | Largest acquisition gap |
|---|---:|---:|---:|
| Denton | 1 | 0 | 33 days |
| Ellendale | 1 | 1 | 168 days |
| Abilene | 2 | 1 | 17 days |
| Project Jupiter | 0 | 0 | 20 days |
| Lake Mariner | 2 | 1 | 185 days |
| Colossus 1 | 1 | 1 | 70 days |
| Helios | 1 | 2 | 40 days |
| Hyperion | 0 | 0 | 55 days |

Denton's two AOIs give the same endpoint-only warning timestamp,
2025-10-08 23:40:46 UTC, based on October 7 imagery and a June 9 progress record.
That is about 33 days before CoreWeave's November 10 earnings call, but only
about 16 days before Core Scientific's October 24 filing discussed existing
weather/construction slippage. The latter filing says these delays had already
been disclosed; this audit does not claim October 24 was the first public
warning. Of Denton's post-baseline composite rows, 51 contain a **known** input
created later than the endpoint. Across sites, endpoint asset-version publication
lags reach about 1,116 days. An earlier original image might have existed; its
version and publication are not evidenced by the committed CSV.

Ellendale's guarded warning comes from March 20 imagery, using a September 21,
2024 progress record. It cannot distinguish indoor fit-out from a construction
problem. Lake Mariner and Ellendale's multi-month acquisition gaps show why
elapsed time without an image must not be treated as observed inactivity.
Season matching reduces predictable vegetation changes but does not remove snow,
cloud selection, roof-material classification errors, or a seasonal maximum in
the record statistic. No-delay-found labels for the other campuses are **unknown
outcomes**, not proved negatives. A precision or false-positive percentage across
all eight sites would be unjustified without a complete milestone ledger.

## Primary-source checks

CoreWeave's [issuer-hosted November 10, 2025 earnings transcript](https://s205.q4cdn.com/133937190/files/doc_events/2025/Nov/10/CORRECTED-TRANSCRIPT-CoreWeave-Inc-CRWV-US-Q3-2025-Earnings-Call-10-November-2025-5-00-PM-ET.pdf)
confirms powered-shell delays and the revised $5.05–5.15 billion revenue outlook
(pages 4 and 8). When asked about Core Scientific, management declined to identify
the developer (page 10). The call does not mention Denton. Thus the November delay
is primary-source verified, while the mapping to this particular campus remains
an inherited retrospective attribution. No stock-return claim is needed to
validate the physical observation.

Core Scientific's [original October 24, 2025 10-Q](https://investors.corescientific.com/sec-filings/all-sec-filings/content/0001628280-25-046272/core-20250930.htm)
reports weather/construction slippage to later in 2025 and completion of Denton
data halls in the second and third quarters. The latter is direct evidence that
the exterior proxy can stay flat while operational progress occurs. This
announcement-only information must be a baseline in a future predictive test.

Applied Digital's [October 27 announcement](https://www.globenewswire.com/news-release/2025/10/27/3174518/0/en/index.html)
reports on-time delivery of the first 50 MW at Polaris Forge 1. Its [November 24
announcement](https://ir.applieddigital.com/news-events/press-releases/detail/137/applied-digital-completes-phase-ii-ready-for-service-at)
reports the second 50 MW on time, bringing Building 1 to 100 MW. The control has
an explicit, dated positive milestone rather than an assumed absence of bad news.

The construction candidate remains research-only. The next useful experiment
needs a frozen building-level guided-service-date ledger and complete scene
manifests, then tests whether roof progress improves an announcement-only model
on future campuses. This audit supplies no verified construction trading edge.
