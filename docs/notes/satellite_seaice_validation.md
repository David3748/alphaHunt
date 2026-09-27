# July satellite sea ice predicts September extent

The fixed test finds **conditional historical forecast improvement**. July Arctic sea-ice extent reduces September RMSE by 24.73% against a matched trend-plus-prior-year model in 26 chronological held-out years, 2000–2025. Its predefined gate passes on the current archive. However, the improvement interval against the better, simpler trend-only baseline crosses zero. Substituting available contemporary July reports preserves point improvement but also produces intervals crossing zero. These distinctions prevent a broad claim of robust or original-vintage operational verification.

This is the sixth candidate examined after five failed or conditional candidates. Its own protocol was frozen before downloading the two outcome/predictor files and there was no local horizon, region, model or parameter search. The broader candidate search remains exploratory, without a family-wise statistical discovery claim.

## Fixed experiment and result

The forecast issues August 15 using July mean Arctic extent and predicts **same-year September monthly mean extent**, not the daily annual minimum. The target begins 17 days later and ends 46 days later. Expanding OLS uses an intercept, calendar-year linear trend, previous September extent and current July extent. Its initial 20 complete training rows are 1980–1999; every complete target year 2000–2025 is tested. All target labels used in fitting come from earlier years and are assigned an October 15 availability embargo.

All values below are million square kilometers; positive gain means the July model has lower error.

| Model | RMSE | MAE | July-model RMSE gain | Paired 95% gain interval |
|---|---:|---:|---:|---:|
| Trend + prior September + current July | 0.4738 | 0.3903 | — | — |
| Trend + prior September | 0.6295 | 0.4906 | 24.73% | +0.0118 to +0.2756 |
| Trend only | 0.6034 | 0.4626 | 21.48% | −0.0132 to +0.2504 |
| Prior September | 0.6377 | 0.4788 | 25.70% | +0.0096 to +0.3085 |
| Expanding September mean | 1.4894 | 1.3810 | 68.19% | +0.7848 to +1.2375 |

The frozen gate requires at least 5% lower RMSE and lower MAE versus every baseline, plus a positive 95% paired interval against the matched trend-plus-prior baseline. It does not require positive intervals against every baseline; that stronger claim is false. Intervals resample paired adjacent two-year circular blocks, with 10,000 draws and a fixed seed. Small samples, longer dependence, revised inputs and cross-candidate selection remain limitations.

Full predictions include the sizable misses in 2007, 2008, 2012 and 2021. At the reviewer's request, period diagnostics were added after seeing the primary result:

| Held-out period | July-model RMSE | Matched-baseline RMSE | Trend-only RMSE | Years better than matched baseline |
|---|---:|---:|---:|---:|
| 2000–2009 | 0.5582 | 0.7229 | 0.6579 | 4/10 |
| 2010–2019 | 0.4042 | 0.5831 | 0.5771 | 8/10 |
| 2020–2025 | 0.4258 | 0.5286 | 0.5490 | 5/6 |

The first period's July-model MAE is slightly worse than trend-only, despite lower RMSE. A separate post-result negative-control test replaces current July with previous-year July while retaining the same model and dates. That placebo has RMSE **0.6533**, worse than either trend baseline. It supports the contribution of current-season observations but is not an untouched confirmation sample.

## Satellite source and publication audit

The [official NOAA/NSIDC Sea Ice Index archive](https://nsidc.org/data/seaice_index/data-and-image-archive) supplies [July](https://noaadata.apps.nsidc.org/NOAA/G02135/north/monthly/data/N_07_extent_v4.0.csv) and [September](https://noaadata.apps.nsidc.org/NOAA/G02135/north/monthly/data/N_09_extent_v4.0.csv) data. The [version 4 user guide](https://nsidc.org/sites/default/files/documents/user-guide/g02135-v004-userguide.pdf) identifies satellite passive microwave measurements from SMMR/SSM/I/SSMIS and, for 2025 onward, AMSR2. Extent counts grid cells with at least 15% ice concentration; the current monthly statistic averages daily extents. Raw snapshots, retrieval metadata and SHA-256 hashes are preserved.

Eighteen dated NSIDC reports establish that a July monthly value was publicly discussed before August 15 in 2007 and every year 2009–2025. The full audit table is `revision_audit/original_july_values.csv`; the source discovery list also retains the excluded 2008 report, which gives July 31 daily extent rather than the required monthly value. Examples:

| July year | Report date | Published value | Current archive | Source |
|---|---|---:|---:|---|
| 2007 | August 10 | 8.10 | 7.94 | [NSIDC 2007 archive, August 10 section](https://nsidc.org/ru/node/366796) |
| 2012 | August 6 | 7.94 | 7.67 | [A most interesting Arctic summer](https://nsidc.org/sea-ice-today/analyses/most-interesting-arctic-summer) |
| 2020 | August 4 | 7.28 | 7.29 | [Steep decline sputters out](https://nsidc.org/sea-ice-today/analyses/steep-decline-sputters-out) |
| 2025 | August 11 | 7.66 | 7.66 | [The peak of summer, the depths of winter](https://nsidc.org/sea-ice-today/analyses/peak-summer-depths-winter) |

These are currently hosted historical reports, not immutable contemporaneously captured pages. They substantiate the availability of July information; they do not prove that every current CSV value or historical training vintage was available then. The [October 2017 version 3 announcement](https://nsidc.org/ru/node/48420) explains the material change from extent computed from monthly-average concentration to average daily extent. In the audit, pre-change differences reach **0.39 million km²**. After 2017, observed differences in the audited years are at most 0.05. The August 2025 report explicitly documents reprocessing with AMSR2 before the fixed forecast date.

A post-result stress test substitutes all 18 audited original July values throughout the chronological panel. Other years, prior September values and September target labels remain current archive. This mixes aggregation definitions, so it is **not an original-vintage reconstruction**. Its RMSE is **0.5160**, still 18.03% below the matched baseline and 14.50% below trend-only; the corresponding improvement intervals are **−0.0380 to +0.2342** and **−0.0589 to +0.2080**. The stress test therefore does not establish robust operational skill.

## Scope of usefulness

The empirical target is future satellite-observed ice in the same measurement family. This can inform a broad physical view of seasonal Arctic conditions. It is not an independent measurement of shipping activity, navigability, transport capacity, prices or profits. The [2020 NSIDC report](https://nsidc.org/sea-ice-today/analyses/steep-decline-sputters-out) illustrates why local interpretation matters: passive microwave and a multisensor analysis disagreed about residual ice on the Northern Sea Route. Route-specific ice, vessel capabilities, escorts and economics require a separate evaluation. No such downstream claim is made.

The primary gate, every prediction, all baseline intervals, original-report audit, placebo and mixed-vintage stress test remain reviewable in `results/satellite_validation/seaice/`. Flags for original-vintage operational verification, prospective verification, shipping capacity, trading alpha and cross-candidate multiple-testing adjustment remain false.

## Reproduce

```sh
python3 src/satellite_seaice_validation.py
python3 -m pytest -q tests/test_satellite_seaice_validation.py
```

Seven tests check season/prior-year alignment, future-target and future-feature isolation, availability rejection, wrong-month rejection, exact claim boundaries, and publication-date evidence. `--fetch` refreshes raw provider data and records new provenance. Post-result diagnostics are separate from `protocol.json` and do not alter the frozen primary model or gate.
