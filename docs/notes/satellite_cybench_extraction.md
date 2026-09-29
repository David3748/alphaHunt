# CY-Bench U.S. maize: source preparation

The bounded replication uses the independently published CY-Bench county dataset, not the earlier VegScape state-yield experiment. The original experiment combined representative-county VegScape means, NASA POWER weather and NASS state labels. CY-Bench supplies county-specific, crop-masked MODIS NDVI and AgERA5 weather with independent NASS county yield labels. No yield model or outcome comparison is performed by the extraction module.

The source is [CY-Bench v1.10, fixed Zenodo record 17279151](https://zenodo.org/records/17279151), with [author preparation code](https://github.com/WUR-AI/AgML-CY-Bench/tree/main/data_preparation) and a [published dataset paper](https://doi.org/10.5194/essd-18-3997-2026). The paper's benchmark uses leave-one-year-out evaluation; the separate frozen validation in this repository instead requires forward years. Dataset attribution and EUPL-1.2 terms are retained in `results/satellite_validation/cybench_maize/DATA_NOTICE.txt` and `EUPL-1.2.txt`.

`src/satellite_cybench_extract.py --fetch` retrieves only the selected U.S. maize ZIP members using validated HTTP206 ranges. It checks each member's ZIP size and CRC and records compressed and decompressed SHA256 hashes. The719MB compressed daily weather member and 2.04 GB decoded CSV stay in scratch. The offline layer retains actual eligible NDVI observations and monthly weather sufficient statistics (sums, finite counts, row counts, observed-day bit masks), plus the separate labels and location metadata. Default execution verifies every compact input hash before rebuilding monthly features and coverage.

The prepared panel covers 2003–2023. Each source location must match an official 2020 Census county GEOID, so state totals and unallocated codes cannot enter through target selection. Feature rows exist independently of whether the year has a yield label. Weather, satellite, and final feature eligibility are separate columns.

The forecast issue is August 15 at 12:00 UTC, matching the model protocol. An NDVI source date denotes the beginning of an eight-day composite. The extractor treats the final day as complete through 23:59:59 UTC and applies a further 14-day buffer. Only windows available strictly before issue are retained; their start month assigns them to April, May, June or July. Means use valid values in [-1,1]. Every month requires at least two valid composites and at least 50% of the eligible expected January 1 + 8k starts. No temporal interpolation or future fill is applied.

Weather includes all days April 1–July 31. Monthly minima-temperature, maxima-temperature, average-temperature and vapor-pressure-deficit features are means of daily source values. Precipitation, radiation, evapotranspiration and climatic water balance are sums. Every variable must have finite data for every calendar day; incomplete totals become missing, never rescaled. Duplicate county-date rows, including duplicates split across CSV chunks, raise an error. Coverage/count columns remain visible.

This is a **current-vintage scientific forecast using fixed geography**. The static WorldCereal map describes 2021 and was publicly released in 2023; it was unavailable for earlier historical forecasts. AgERA5 is reanalysis using ground observations and satellite assimilation, not a ground-only archive. Weather, NDVI and yields are current archive values, and original releases are uncertified. The fixed buffer establishes nominal observation age, not proof that the exact archived values were historically published then. The separate 2022–2023 model check is after the map's reference year; it is not proof of availability in both years. These limitations prohibit calling the historical result an executable operational backtest or proven market alpha.

Offline reproduction:

```sh
PYTHONPATH=src python3 src/satellite_cybench_extract.py
PYTHONPATH=src python3 -m pytest -q tests/test_satellite_cybench_extract.py
```

Online reconstruction of the compact inputs:

```sh
PYTHONPATH=src python3 src/satellite_cybench_extract.py --fetch
```

The extraction tests cover inclusive composite timing, missing and negative NDVI, minimum coverage, complete weather totals, duplicate days across chunk boundaries, infinite observations, exclusion of August weather, target-independent feature rows, and rejection of changed compact inputs.

Before the first model fit, input validation found 218 zero-yield records whose harvested area and production were both missing. All occur in 2003–2009, with none in the held-out years. They lack affirmative evidence of total crop failure. The exact upstream origin remains unresolved; the archive no longer contains original NASS disclosure codes, so we do not claim that every row is a known suppression artifact. Current author preparation drops missing quantities, and the common author filter rejects nonpositive yields, but the fixed v1.10 archive still contains these records.

The pre-fit quality repair maps only unsupported zeros with both companion quantities missing to missing yield, retaining every row, the raw source snapshot, and a 218-row audit. Genuine zero output with positive harvested area remains valid. No geography or feature value changed. Negative/nonfinite yields would also remain explicitly missing. This adds a tenth semantic test and is recorded in `prefit_label_quality_repair.json`; it was not a response to model performance.

Before either horizon was fitted, a single secondary horizon was frozen at 2026-09-27T03:41:48.223838Z: September 15 noon using April–August inputs, with every other data rule unchanged. `src/satellite_cybench_late_extract.py --prepare` reconstructs its compact inputs from the checksum-verified raw cache; default execution is offline. This later horizon also admits the final July composite that was not yet available for the August issue. It always remains a secondary result, and both horizons must be retained regardless of score. Two additional timing tests bring extraction validation to 12 tests. The primary has 62,070 feature-eligible county-years and the secondary has 61,703; these counts do not require yield labels and are not fitted-sample counts.

An independent source review found no cross-window smoothing in the raw NDVI preparation. An independent arithmetic check reproduced all 36 primary monthly NDVI/weather values for county US-01-001 in 2003. The count of valid county composites does not establish the fraction of valid source pixels, which the prepared table does not expose. The source audit and arithmetic-check JSON files retain this limitation.
