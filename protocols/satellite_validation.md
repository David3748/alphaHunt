# Satellite continuation: evaluation contract

This is exploratory research on the existing Claude pilot, not a preregistered
discovery or a live track record. The original imagery series and some outcomes
were already visible when this continuation began. The user explicitly accepts
independent production/yield forecast improvement as usefulness; profitable
trading returns must be tested separately.

## Five distinct mechanisms

1. Sentinel-2 furnace heat -> smelter throughput and refined copper production.
2. Sentinel-2 exterior construction -> delivery risk at data-center campuses.
3. MODIS crop vegetation -> corn/soybean yield anomalies beyond weather alone.
4. CERES solar irradiance -> solar generation (historical product latency must
   permit the proposed nowcast).
5. NOAA OISST satellite/in-situ ocean temperatures -> following-season rainfall
   relevant to crop/hydropower supply; comparison with an in-situ-only SST model
   would be needed to isolate the satellite component.

See `config/satellite_alpha_candidates.json` for targets, exposures, comparators,
and each candidate's limitations. These are hypotheses, not recommendations.

## Evidence ladder

- **Physical association:** imagery agrees with an independently documented
  physical state. This alone does not satisfy the forecast-improvement gate.
- **Forecast improvement:** a chronological, previously untrained prediction
  beats a stated non-satellite baseline on independently reported outcomes.
  Report MAE/RMSE, individual errors, sample size, uncertainty, and whether it
  adds value to known announcements or merely reproduces them.
- **Robust forecasting evidence:** the improvement survives multiple periods,
  reasonable timing choices, a satellite ablation, and relevant placebos.
  An exploratory result with few observations is labelled provisional even if
  its point estimate improves.
- **Trading alpha:** returns after costs, with original-vintage features,
  known-at-the-time site identity, contemporaneous expectations, appropriate
  risk/market controls, and a new holdout or forward period. No automatic upgrade
  from the preceding levels.

## Timing and validity

- Acquisition dates are not release dates. Use scene creation metadata as a
  conservative archive proxy and disclose that it is not a first-seen log.
- Reprocessed imagery cannot be backdated. A construction composite depends on
  all its current-window and baseline scenes, not just the endpoint scene.
- Missing publication metadata or missing cloudy observations must not become
  zero activity. Keep gaps explicit and include abstention/coverage counts.
- Never include future acquisitions in a baseline. Label baseline-year rows as
  diagnostic, and do not trade before a site's role becomes public.
- Crop composites cannot be used before their last contributing day. State
  release-lag assumptions and current-vintage label limitations explicitly.
- Chronological training uses earlier outcomes only. Preserve every evaluated
  target and specification, including failures. Selecting the best target after
  observing its result does not make it confirmatory evidence.
- Construction controls distinguish independently verified on-time deliveries
  from sites for which no delay disclosure was found. Silence is not a negative
  label. Known public disclosures are first-class comparators.

## Reproduction

Small input snapshots, source URLs, source release dates where available,
prediction/error tables, code, tests and machine-readable summaries are committed.
Reproduction commands are in `results/satellite_validation/report.md`.
The original imagery CSVs remain historical pilot artifacts; code timing fixes
do not retroactively certify those CSVs without refetching their constituent
scenes. A late input may make a historical forecast unreconstructible.
