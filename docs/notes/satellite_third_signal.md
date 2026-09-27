# MODIS vegetation as a crop-supply signal

**Decision: forecast utility is not verified.** Corrected corn-flowering results
improve anomaly RMSE by 2.24% over weather alone, below the 5% verification gate,
with a year-cluster confidence interval spanning zero. A selected 2025 wheat
confirmation also fails. All positive and negative diagnostics remain available.

This adds a third distinct satellite hypothesis: crop vegetation stress can improve
harvest-yield forecasts, creating a possible information advantage for grain
research. Forecast utility and profitable trading are separate claims. This
experiment measures the former; it does not establish the latter.

The measurements are genuine optical observations. [USDA VegScape metadata](https://www.nass.usda.gov/Research_and_Science/Cropland/metadata/metadata_VegScape.htm)
identifies NASA MODIS imagery at 250-meter resolution and weekly vegetation
products. The [published GetFile API](https://nassgeo.csiss.gmu.edu/VegScape/devhelp/vegservice.html)
returns county GeoTIFFs. NASA POWER weather covariates are only the comparison
model and are not counted as a separate satellite signal.

## Corrected 2018–2024 results

The primary corn-flowering model has **7.142%** yield-anomaly RMSE, against
**7.306%** for weather and **7.673%** for trend, over 70 state-years clustered
into seven years. The year-cluster 95% interval for MSE improvement over weather
is **[−0.000111, 0.000598]**. The improvement against trend is 6.92%, but this
does not isolate satellite value: the appropriate optical ablation is weather
versus weather plus NDVI.

| Crop | Stage | NDVI + weather RMSE | Improvement over weather | Years improved / 7 |
|---|---|---:|---:|---:|
| Corn | Vegetative | 7.711% | 1.87% | 5 |
| Corn | Flowering (primary) | 7.142% | 2.24% | 5 |
| Corn | Grain fill | 7.305% | 0.77% | 4 |
| Soy | Vegetative | 9.269% | 4.49% | 4 |
| Soy | Flowering/pod set | 8.571% | 4.38% | 5 |
| Soy | Seed fill | 8.418% | 3.62% | 5 |
| Wheat | Vegetative | 15.721% | −1.67% | 1 |
| Wheat | Heading | 15.148% | 2.34% | 6 |
| Wheat | Grain fill | 14.722% | 3.88% | 7 |

Wheat grain fill improves all seven yearly MSEs, with a one-sided exact
year-sign-flip p-value of 0.0078125. Across the nine disclosed crop/stage cells,
the Bonferroni-adjusted value is 0.0703125. This is an exploratory signal worth
following, not confirmation after selection. The test has only seven yearly
clusters and assumes symmetric paired errors; neither the bootstrap nor this
diagnostic accounts perfectly for serial dependence. See
`paired_year_diagnostics.csv` for every cell.

Nine original GeoTIFF samples—three counties in 2012, 2018 and 2024—were freshly
downloaded. Their extracted means match the inherited NDVI cache to within
1.12e-16; raw TIFFs, URLs and SHA-256 hashes are retained. This supports input
authenticity and extraction correctness, not predictive or trading skill.

## Experiment and reproducibility

Run the committed compact inputs offline:

```bash
python3 src/satellite_crop_validation.py
python3 -m unittest tests.test_satellite_crop_validation -v
```

To independently recheck the fixed nine imagery samples, add `--verify-remote`.
For first-time ingestion from the existing crop experiment, add
`--source /path/to/research/yield_model`. The inherited data cache can be rebuilt
using `src/nass_yield_ingest.py` and `src/crop_yield_model.py`; source URLs, original
input hashes, extraction policy and API documentation are retained in
`results/satellite_validation/third_signal/input_provenance.json`.

The inherited random-forest parameters are retained: 400 trees, maximum depth 5,
minimum leaf size 5, feature fraction 0.75, random seed 17. The evaluation is
2018–2024, with each year trained on earlier years. Five historical target-anomaly
years are required. State yield trends use only earlier years, and feature
imputation and state encodings are fitted only on the training sample. The
primary cell is corn at flowering; all other crop/stage cells are retained as
secondary diagnostics, including failures. This is a chronological evaluation,
not a blind new holdout: prior repository research had already inspected these
years, and preliminary ridge and 150-tree diagnostic runs were inspected during
this audit. Parameters in the final model are inherited rather than selected
from a parameter search.

Five models are compared on the same rows: trend, NDVI alone, weather alone,
weather plus current-year NDVI, and weather plus prior-year NDVI. The last model
tests whether the contemporaneous optical measurements add information beyond
stable location effects and lagged vegetation. Primary verification requires at
least 5% RMSE improvement over both trend and weather, a positive lower bound on
the year-cluster bootstrap interval for MSE improvement over weather, and lower
RMSE than the prior-year NDVI placebo. These are conservative research gates,
not preregistered evidence or a formal proof of economic value. Resampling whole
years avoids treating correlated states as independent. Seven evaluation years
remain a small sample.

## Timing and inherited implementation corrections

The prior crop model dated some predictions before the end of the weekly NDVI
composite. This validator assigns availability to **composite end plus 14 days**.
A July 31, 2024 stage endpoint therefore uses the July 29–August 4 composite and
is not considered available until August 18. The 14-day processing lag is an
assumption, not a recovered historical release timestamp. The NDVI snapshot may
contain vegetation after the nominal crop-stage endpoint; the reported
availability date, not the nominal endpoint, is the valid prediction date.

Earlier-stage NDVI was also absent from later-stage rows in the inherited panel.
This validator rebuilds the optical columns from the underlying measurement
cache and carries earlier observations forward. It never fills later-stage
observations into earlier forecasts. Trend and anomaly targets are recomputed,
and 2025 is excluded to avoid mixing an additional recent outcome year into this
fixed window.

## Limits on interpretation

The existing caches are current reprocessed satellite archives and revised USDA
outcomes. File-by-file historical release vintages were not preserved. Training
on earlier crop years assumes their outcomes are available by the next growing
season; later revisions cannot be removed here. The nine refreshed GeoTIFFs
verify sampled extraction values, not every archived observation or historical
publication date.

Each state is represented by one county's full valid-pixel mean. There is no
crop mask or complete state coverage. Static production weights come from the
existing configuration and were not optimized. A better operational system
would aggregate crop-masked pixels with historical acreage weights, keep raw
files and actual first-seen timestamps, and run a prospective season before
calling the advantage deployable. Forecast improvement alone says nothing about
incremental futures returns after information competition, roll costs and fees.

`summary.json`, `metrics.csv`, `yearly_errors.csv`, and `predictions.csv` contain
the complete numerical result, including the primary verification decision.

## Disclosed 2025 confirmation and invalid initial run

A selected wheat-heading hypothesis was frozen for a separate 2025 check. It is
**not an untouched holdout**: the inherited full-period summaries included 2025,
and an initially invalid run exposed a stage-order bug. Saving the configuration
with sorted JSON keys had reordered stage names alphabetically. The validator
now sorts stages explicitly by calendar endpoint; a regression test verifies
that wheat heading uses the April and June observations and is available on
June 29, 2025. The initial invalid results are retained under
`superseded_stage_order_bug/` and must not be used as evidence.

After that correctness fix, the frozen 400-tree specification still fails the
2025 check: current NDVI plus weather has **10.589%** anomaly RMSE, versus
**9.788%** for weather, **9.953%** for the prior-year NDVI placebo, and **8.242%**
for trend. This is **8.19% worse than weather**, across 12 states in one year.
The experiment retains the failure; it does not pool this one year into a new
significance calculation or select another hypothesis. Reproduce the corrected
confirmation offline with:

```bash
python3 src/satellite_crop_confirmation.py
```

The confirmation runner verifies the frozen core-validator SHA and model
parameters. `confirmation_2025_protocol.json` records the exact disclosure and
criteria before the corrected rerun; `confirmation_2025.json` records its result.
