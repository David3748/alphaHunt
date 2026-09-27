# Eastern Pacific seasonal hurricane-risk forecast

**The frozen forecast gate fails.** The June Niño3.4 predictor improves the regression controls, but its gain against the stronger recent climatology is too small and uncertain. All 27 planned forecast years, 1999–2025, are eligible.

| Model | RMSE, ACE | MAE, ACE |
|---|---:|---:|
| Trend, prior season, current June–July activity | 37.630 | 27.337 |
| Same controls plus June Niño3.4 SST | **34.797** | **25.091** |
| Expanding historical mean | 37.178 | 30.878 |
| Previous ten years’ mean | 35.708 | 28.465 |
| Prior-season persistence | 49.601 | 36.128 |

The satellite-enhanced model reduces RMSE 7.53% and MAE 8.22% against the regression controls. The paired circular five-year-block-bootstrap 95% RMSE-gain interval is 0.374–6.342 ACE. Against the strongest baseline, the recent ten-year mean, the RMSE gain is only **2.55%**, with interval **−3.738 to 8.280 ACE**. This fails the predeclared requirement of at least 5% RMSE improvement and positive lower confidence bounds against every baseline. No specifications were changed after observing these results.

The eastern Pacific was selected mechanistically after the Atlantic attempt failed: ENSO influences Pacific storm conditions. This basin choice is part of a broader search across candidate mechanisms, not a preregistered familywise-confirmatory result. The protocol was frozen before fetching Pacific storm outcomes. The model reuses the identical fixed ridge penalty of 5, expanding 1982 onward training, minimum 17 complete years, training-only standardization, nonnegative forecasts and four baselines used for the Atlantic test. The only satellite predictor is June Niño3.4 SST, 5°S–5°N and 170–120°W; no Atlantic SST enters.

The independent [NHC HURDAT2 archive](https://www.nhc.noaa.gov/data/) is `hurdat2-nepac-1949-2025-091426.txt`. The target includes EP-origin storm IDs only and observations east of 140°W, August 1–November 30. CP-origin storms and dateline crossings are excluded. ACE sums wind knots squared /10,000 at 00/06/12/18 UTC for TS/HU/SS states with winds at least 34 knots. The same rule produces the current June–July control. It is a basin storm-energy risk index, not insured losses, landfalls or production losses.

[NOAA OISST v2.1](https://www.ncei.noaa.gov/products/optimum-interpolation-sst) combines satellite and in-situ observations; it does not isolate a satellite-only contribution. The exact June regional grid and derived values are reused, without alteration, from the Atlantic attempt. The source manifest includes their hashes and lineage. June SST receives a 31-day assumed availability lag before the August 1 issue. Current June–July storm activity stops July 31 at 18 UTC. Prior-season labels are assumed available by May 1. OISST reprocessing and revised HURDAT best tracks mean this is a current-vintage scientific hindcast, not certification that archived values were available in their present form historically. Existing official forecast superiority and trading usefulness were not tested.

Reproduce offline using NumPy and pandas:

```sh
python3 src/satellite_pacific_hurricane_validation.py
python3 -m pytest -q tests/test_satellite_pacific_hurricane_validation.py
```

Seven semantic tests cover basin and dateline boundaries, sole Niño3.4 addition, known current activity controls, pre-issue timing, future perturbations, and issuing forecasts before target outcomes exist. After first scoring, an implementation audit separated target-label availability from feature eligibility in both basin modules. All original historical forecasts, scores and comparisons remained exactly identical; the original summary, pre-fit code freeze and post-fit maintenance record are retained.

The source snapshot, manifest, frozen protocol, annual targets, panel, predictions and summary are in `results/satellite_validation/pacific_hurricane/`. The shared OISST raw grid and full refresh helper are in the adjacent `hurricane/` directory. Explicit Pacific refresh uses its `fetch_inputs.py`, which requires requests. This negative result is preserved without tuning.
