# Operational satellite irradiance and delayed solar reporting

The earlier CERES experiment improved Topaz production estimates, but its historical irradiance arrived too late for the proposed monthly nowcast. This continuation tested genuine operational GOES observations, and separately investigated whether annual reporters offered a longer information window.

## Operational GOES: fixed test fails

The model was fixed before joining GOES observations to production. It multiplies average daily generation from the preceding two same-calendar-month years by current irradiation divided by irradiation in those identical historical months. No irradiance coefficient is fitted. The issue is month-end plus 14 days; every source object must have been created and archived by then. Training production is embargoed for two months after month-end.

The primary plant is Topaz (EIA 57695), a monthly respondent throughout 2015–2025. The intended evaluation is all 48 months of 2022–2025. Coverage rules and their propagation into calibration exclude six months, leaving 42. The fixed satellite model has RMSE **13,948.64 MWh**, versus **12,909.28** for the calendar/trend/local-weather comparator: **8.05% worse**. Mean absolute error is also worse. The matched two-year seasonal mean is weaker, and the satellite improves it by 6.03%, but that does not meet the stronger baseline gate.

The paired year-block interval against weather is −4,130.22 to +1,945.39 MWh. There are only four year clusters. Per-year scores, the April 2024 product-change split, a same-season resampling sensitivity, and a prior-year-irradiance placebo are retained. No later tuning replaces this failure.

The GOES extraction itself is useful infrastructure, but its successful retrieval and timing audit do not establish prediction utility. Missing daytime values remain missing; averages over complete days may still have nonrandom missingness. The source algorithm/resolution changes in April 2024 and final-vintage EIA/weather revisions remain limitations. See [the extraction notes](satellite_goes_extraction.md).

## Annual reporting: genuine longer window, invalid historical monthly target

The nearby California Valley Solar Ranch (EIA 57439) reported monthly in 2015–2016, annually in 2017–2022, and annually with monthly detail in 2023–2025. The [November 2022 Federal Register proposal](https://www.govinfo.gov/content/pkg/FR-2022-11-21/pdf/2022-25287.pdf) documents EIA's addition of monthly operational detail for annual renewable respondents. Therefore the earlier annual years' monthly allocations are not independent monthly measurements.

A monthly delayed-CERES diagnostic was preserved but discarded as validation: its 72 months include these allocations. It also failed numerically, with RMSE 9,212 MWh versus weather 8,841 MWh. It cannot be promoted as evidence even if an alternative model improved it.

Before any GOES outcome metrics were examined, the confirmation protocol was amended to use the plant's authoritative **annual** totals. A January 14 following-year forecast precedes annual EIA disclosure; November 1 is a conservative embargo for previous years' training labels, not an asserted first-publication date. Official annual workbook fields, respondent frequencies, release notices, and original-file hashes are retained under `goes_solar/reporting_frequency/`.

Only two of the four intended confirmation years have complete eligible satellite features. Their satellite annual RMSE is 152,840 MWh versus the matched mean's 137,240. The confirmation fails both coverage and point-improvement requirements.

## Reproduction

```sh
python3 src/satellite_goes_solar_validation.py
python3 src/satellite_annual_solar_validation.py
python3 -m pytest -q tests/test_satellite_goes_solar_validation.py tests/test_satellite_annual_solar_validation.py
```

Raw operational extraction can be replayed offline with `src/satellite_goes_extract.py --compact`. None of these tests verifies trading alpha.
