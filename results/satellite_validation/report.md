# Satellite alpha: continuation and independent validation

This continues `claude/cool-bohr-oco47t` in `David3748/alphaHunt`. The original
Sentinel-2 construction and smelter experiments are preserved and audited.
The continuation now covers crop vegetation, solar irradiance, ocean temperatures,
sea ice, atmospheric temperatures, reservoir altimetry and raw daily snow imagery,
with independent ground or economic targets and stronger-baseline checks.

**A small historical forecast improvement is independently verified.** Adding
raw MODIS vegetation measurements to a county-level weather/yield model reduces
US maize-yield RMSE by **1.83% for August 15 forecasts** (95% paired calendar-block
interval **1.27–2.57%**) and **3.12% for the predeclared September 15 secondary**
(**2.87–3.42%**). The satellite model improves 10 of 11 years at the earlier date
and all 11 at the later date. It beats every stated comparator on the original
statistical and coverage checks.

**Both original 5% materiality gates remain failed. No trading alpha is verified.**
The user accepted forecast improvement without a minimum effect size. The narrow
historical forecasting result meets that definition; it does not meet the larger
practical gain targeted by our original gate. This interpretation is explicitly
recorded after the first result, without changing models, metrics or original
gate outcomes. The earlier failures remain below.

This uses current revised data and a fixed 2021 crop map. It is not a reconstruction
of what was publicly available at every historical issue date. There are 11 year
clusters, not thousands of independent seasons; uncertainty is conditional on the
study and is not adjusted for the wider exploratory search. Prospective operational
and economic usefulness remain unverified.

## County forecast result

![County forecast comparison with uncertainty](cybench_maize/county_forecast_comparison.png)

| Fixed forecast | Scored county-years, 2013–2023 | Weather RMSE | Weather + NDVI RMSE | Relative gain, 95% interval | Years improved |
|---|---:|---:|---:|---:|---:|
| August 15 primary | 15,026 | 1.5354 t/ha | 1.5073 t/ha | 1.83%, 1.27–2.57% | 10/11 |
| September 15 prespecified secondary | 14,907 | 1.5322 t/ha | 1.4844 t/ha | 3.12%, 2.87–3.42% | 11/11 |

Each forecast trains only on earlier-year county yields under the stated release-lag
assumption. Raw eight-day
vegetation windows receive a fourteen-day buffer; future interpolation and
smoothed FAPAR are excluded. The weather comparator uses AgERA5 reanalysis,
which incorporates both ground and satellite information. Satellite here means
**incremental vegetation information beyond that weather comparator**. Trend,
latest-yield and five-year-mean comparators are also beaten. Both 2022 and 2023
show lower error than weather and trend, after the crop-map reference year;
this is not a claim that the map was publicly available in 2022.

The 218 unsupported zero-yield training entries were audited before any model
fitted and retained as missing, with their raw records preserved. Independent
review reproduced source aggregations, every comparator, scores, uncertainty
and abstention counts. Unknown current outcomes do not prevent forecasts.

The [fixed CY-Bench source record](https://zenodo.org/records/17279151),
`cybench_maize/`, `cybench_maize_late/`, and the county forecast notes contain
inputs, frozen protocols, all annual forecasts and audits. A September forecast
has less lead time; both operating dates were fixed before the first fitted
scores and are reported independently.

The separate CORN ETF test uses prior-year harvested area, entry after the issue
date, October-end exit, 25 basis points each side and 3% annual borrow for shorts.
Across 11 annual event windows, the August satellite strategy compounds to
**+6.69%**, but its mean-return interval includes losses; the incremental overlay
loses **30.79%**. September satellite and weather directions are identical and
compound to **−18.88%**; the overlay loses **25.38%**. These are event-window
returns, not annualized alpha. Neither forecast becomes a verified trading edge.

## Earlier mechanisms and retained failures

| Satellite source and possible information advantage | Independent test | Result | Decision |
|---|---|---|---|
| Sentinel-2 furnace heat: copper supply / producer output surprises | Kennecott quarterly production, 8 chronological predictions during 2024–2025; scene and label publication gates | Refined-copper MAE 12.21 kt versus persistence 12.72, but historical mean 12.09; throughput MAE 49.45 kt versus persistence 38.63 | Not verified; physical outage detection alone is insufficient |
| Sentinel-2 exterior construction: delivery / revenue-recognition risk at data-center operators | Eight campuses, publication-aware warning replay, independently sourced delay and on-time delivery controls | Denton's October 8, 2025 endpoint-only warning disappears when known input publication dates are enforced; APLD still false-alerts before on-time delivery | Not verified |
| Original county-to-state MODIS proxy: crop supply surprises | Prior-year-only training, 2018–2024 evaluation, trend/weather/prior-year-NDVI comparisons; all nine crop-stage cells retained | Corn flowering RMSE 7.142% versus weather 7.306%: 2.24% improvement, uncertainty spans zero. Selected wheat-heading 2025 check is 8.19% worse than weather | Not verified |
| CERES solar irradiance: solar generation / revenue nowcasting | Topaz output reported independently to EIA; 72 monthly test estimates, 2020–2025 | RMSE 10,925 MWh versus 14,567: 25.00% lower. Year-block interval for reduction 14.31%–35.77%; worse in 2022 | Retrospective estimation works, but source latency defeats the proposed historical nowcast |
| NOAA OISST ocean temperatures: seasonal crop / hydropower supply risk | September Niño3.4 → ensuing Texas winter rain; 29 test winters, 1998–2026, initial 15 winters training | RMSE 1.604 inches versus historical mean 1.777: 9.74% lower. Paired two-winter-block gain interval −0.150 to +0.457 inches | Promising point improvement; uncertainty gate fails |
| NOAA/NSIDC passive-microwave ice: Arctic shipping conditions | July → September mean extent, 26 chronological test years, 2000–2025 | RMSE 0.474 versus 0.629 million km² for trend + prior September: 24.73% lower; gain interval +0.012 to +0.276. Versus stronger trend-only model, gain interval crosses zero | Fixed matched-model gate passes; robust and original-vintage economic usefulness remain unverified |
| UAH microwave atmospheric temperatures: gas-demand surprises | 156 winter months, 2000–2025; independent EIA residential gas and official ground heating-degree-day comparator | Satellite lowers basic-model RMSE 12.40%, but adding it to ground weather raises RMSE from 1.112 to 1.175 Bcf/day: 5.61% worse | Incremental forecast gate fails |

![Error improvements and failures](evidence.svg)

Full inputs, per-observation forecasts, failures, provenance, and uncertainty are
in the neighboring `construction/`, `smelters/`, `third_signal/`, `solar/`, `enso/`,
`seaice/`, and `gas/`
directories. `summary.json` collects machine-readable decisions;
`input_manifest.json` records hashes. These are exploratory analyses, not a
preregistered discovery or an original-vintage live record.


## Further forecast and replication tests

Original failures remain in the record. Subsequent tests were specified before their own feature/outcome joins, not before the entire research project. Confidence intervals are not adjusted for the full search across candidates; a positive individual result therefore needs replication before deployment.

| Additional test | Measured result | Decision |
|---|---|---|
| Timely operational GOES irradiance → Topaz monthly generation | 42 eligible 2022–2025 months; RMSE 13,949 MWh versus local weather 12,909, 8.05% worse | Fixed physical model fails; annual second-plant confirmation also fails |
| Delayed CERES → annually reported solar generation | Historical annual respondents' monthly fields were allocated, not independent measured monthly targets; numerical forecast also worse than weather | Discarded diagnostic, never counted as validation |
| NASA radar reservoir altimetry → Sobradinho hydropower | 88 future-month predictions; 19.25% RMSE improvement over generation/inflow baseline, but adding it to public ground storage improves only 0.17% with uncertainty spanning zero | Demonstrates a useful physical proxy; incremental information gate fails where ground storage is available |
| June satellite-enhanced SST → August–November Atlantic storm energy | 27 held-out years; 5.16% RMSE improvement over matched temporal/storm-activity controls, but 3.01% worse than recent climatology | Forecast uncertainty/strong-baseline gate fails |
| June satellite-enhanced SST → Pacific storm energy | 27 held-out years; 7.53% RMSE gain over matched controls, but only 2.55% over stronger recent climatology with uncertainty spanning zero | Strong-baseline gate fails |
| Raw March MODIS snow → April–July San Joaquin runoff | 15 held-out years; satellite raises RMSE 1.32% relative to rainfall/flow controls | Forecast gate fails; future-dependent SPIRES features were never used |
| Raw May MODIS snow → July–September San Joaquin runoff | 14 held-out years; 10.11% RMSE gain over rainfall/April-flow controls, 95% interval +2.42% to +32.66%; adding available May flow reduces gain to 4.86%, interval −0.58% to +20.88% | Primary test passes; stronger-information gate fails |
| Same May-snow model transferred to Kings River | 14 held-out years; 1.72% gain over fresh-flow controls. Pooled two-basin gain 2.77%, interval −1.06% to +12.99%, using shared year blocks | Frozen geographic confirmation fails |
| NOAA crop-masked vegetation → Texas winter wheat | 23 held-out years; 6.54% RMSE gain versus weather, interval −4.57% to +23.41%; essentially tied with USDA June forecast | Uncertainty gate fails |
| Same crop model transferred to Kansas and Oklahoma | Kansas −7.14%, Oklahoma +12.21%; combined confirmation +1.96%, interval −3.79% to +8.52%. Official June forecasts substantially stronger | Geographic confirmation fails; Oklahoma alone is not promoted |
| September Indian/Pacific SST → East African November–December rain | 23 held-out years; 13.21% RMSE gain versus ground/history model, 8.69% versus strongest historical mean. Both gain intervals include zero | Rainfall forecast is promising but unverified; no production claim |
| September Pacific SST → next-harvest South African maize | Original fixed-ridge yield model: 3.80% gain over ground controls under conservative label lag; completed-harvest stress gains 10.61%, interval +3.72% to +25.75%, but remains 1.15% worse than simpler trend. Prespecified total-production secondary also fails its strongest comparator | Original yield and production gates fail; trend treatment investigated separately |
| Corrected trend treatment, independently transferred to Zambia and Zimbabwe | Joint yield forecasts are 26.75% / 21.45% worse than strongest persistence under the two release assumptions; total production also worse | Geographic confirmation fails; South Africa development gains are not promoted |


Operational source retrieval, forecast timing, missing-data treatment and stronger available comparators are audited. Detailed protocols, inputs, predictions and notes accompany each test. The annual solar reporting audit found an important target-quality problem: estimated monthly allocations cannot validate a satellite production forecast against independent meters.

The separate Atlantic-storm insurance-sector test also fails. Across 20 annual 2006–2025 windows, the satellite KIE/SPY spread strategy compounds to −18.37% after two-leg execution costs and short borrow, versus −19.87% for its nonsatellite comparator; the incremental overlay returns −18.32%. Mean-return intervals include zero. These are event-window returns, not annualized alpha. See `hurricane_trading/` and the associated notes.

## Separate market test

The wheat test uses the production-weighted heading-stage forecasts, buys when
predicted supply is below trend and shorts when it is above. The satellite
overlay instead trades the difference between satellite-plus-weather and
weather-only forecasts. Entry is the first Friday strictly after the assumed
feature availability date; the return ending on that Friday is excluded. The
holding period is fixed at 12 weeks. Costs are 25 basis points per entry and exit
plus 5 basis points per scheduled contract roll. The underlying ERS futures
returns follow actual contracts rather than interpreting front-contract roll
price gaps as returns. Collateral cash interest is excluded.

| Strategy | 2018–2024, 7 event years: compounded net return | 2025 diagnostic year |
|---|---:|---:|
| Weather-only forecast | −19.22% | +9.32% |
| Weather plus MODIS forecast | −19.22% | −9.91% |
| Incremental satellite overlay | −0.28% | −9.91% |

These are returns across annual event windows, not annualized returns. The two
full-forecast strategies choose identical directions in all seven original
years. No trading parameter was optimized. The 2025 check is **not untouched**:
aggregate prior research included that year, and an invalid intermediate crop
run had already exposed its outcomes. Small samples, static crop weights,
current-vintage inputs and unmodeled information competition prevent a claim of
market alpha. See `trading/` for each event, exact rules and source hashes.

A separate gas test trades **UNG**, an actual futures-based fund, from the first
session strictly after the economic forecast date until the first session after
the next month's forecast date. It uses adjusted close-to-close prices, fixed
directions, 25 basis points each side and an assumed 3% annual borrow cost for
shorts. Fund expenses and futures rolls are already reflected in prices. The
108 non-overlapping event windows correspond to October–March target months in
2008–2025. Cash interest is excluded; these returns are not annualized.

| Gas-demand strategy | Compounded net event return | Mean net return per event |
|---|---:|---:|
| Satellite forecast versus basic temporal model | −94.29% | −1.51% |
| Ground-weather forecast versus basic temporal model | −94.39% | −1.33% |
| Incremental satellite over ground weather | −89.57% | −0.79% |
| Always short, same windows and costs | +12.48% | +1.35% |

Every mean-return uncertainty interval spans zero. These strategies do not
establish trading alpha; actual historical borrow availability and first-release
predictors were not reconstructed. The overlay loses despite a much less
negative mean than its compounded return because large volatile losses reduce
the capital available for subsequent gains. Exact events, price snapshots and
the protocol fixed before inspecting these returns are in `gas_trading/`.

## What the sea-ice result does and does not establish

The July observation adds information to a model containing time trend and last
September's extent. A prior-year-July placebo fails, and the point improvement
appears in all three reported subperiods. But the simpler trend-only baseline is
better than the matched baseline: its RMSE is 0.603 million km², and the satellite
gain interval against it is **−0.013 to +0.250**, including zero.

Eighteen original July reports appeared before the assumed August 15 forecast
date. They confirm that contemporaneous measurements existed, but older reports
do not always match today's archive. NSIDC changed its monthly calculation in
2017; July 2012 was originally reported as 7.94 million km² versus today's 7.67.
Replacing available July inputs with those original values preserves an 18.03%
point gain over the matched baseline, but its uncertainty interval includes zero.
This mixed-vintage stress test is not a complete reconstruction of original
training and target vintages.

This was the sixth candidate, specified before its own results but after earlier
failures. The confidence interval is not adjusted for the wider research search.
The target is future satellite-observed ice, **not measured shipping capacity or
profits**. See `seaice/sensitivity.json` and its original-report audit. Primary
documentation: [NSIDC Sea Ice Index](https://nsidc.org/data/g02135/versions/4).

## Why the timing audit changes the original pilot

Across eight independent campuses, **437 of 837 post-baseline composite rows**
contain at least one known input published after the endpoint timestamp.
Some scenes were republished more than three years after acquisition. The old
CSV recorded the final scene's creation timestamp, not all composite inputs.
The conservative replay identifies known late dependencies; missing constituent
lineage prevents full historical certification even after that guard.

The construction code now excludes future baseline acquisitions and inputs not
published by its decision time, omits missing publication timestamps, and
exports `available_at`, maximum input publication time and input dates. It also
handles empty usable samples. The inherited CSVs remain explicitly historical
pilot artifacts, because changing code cannot repair already aggregated pixels.
Global daily scene selection and hindsight site/AOI selection remain disclosed.

[Core Scientific's October 24, 2025 filing](https://investors.corescientific.com/sec-filings/all-sec-filings/content/0001628280-25-046272/core-20250930.htm)
already discussed construction delays and completed data halls. Thus exterior
area stagnation did not establish stopped construction, and November 10 was not
the first public warning. CoreWeave's later transcript did not name the delayed
provider; assigning it specifically to Denton is retrospective attribution.

## Data checks and limits

- **Smelters:** 13 original Rio production PDFs checked; source URLs, release
  dates, page references and hashes retained with quarterly labels. Missing
  cloudy scenes stay missing. A Codelco closure is physically visible, but its
  refinery continued after the smelter closed—those are different targets.
- **Vegetation:** nine freshly downloaded USDA county GeoTIFFs reproduce cached
  NDVI means to floating-point precision. Weekly composite dates receive an
  explicit 14-day assumed processing lag. Corrected stage mapping excludes later
  crop stages. The invalid intermediate mapping and failed confirmation are
  archived under `third_signal/superseded_stage_order_bug/` and never counted as
  valid evidence. Final annual yields and weather are current-vintage archives.
- **Solar:** EIA's fuel/prime-mover/plant-total duplicates are filtered to one
  solar-PV series. The baseline controls calendar month and time trend; a
  prior-year-irradiance placebo does not match the contemporaneous improvement.
  However, NASA identifies the downloaded source as SYN1DEG, which arrives
  roughly three to four months after observation. Today's historical values
  cannot be backdated to the much faster FLASHFlux release. Historical FLASHFlux
  downloads require Earthdata access, and a first-release archive was not
  recovered. This result does not satisfy an operational forecasting gate.
- **Ocean temperatures:** NOAA explicitly identifies the index as OISST, which
  blends satellite and in-situ measurements. The September-to-winter test gives
  at least 47 days before the target season begins, using an assumed two-week
  product delay. It does not isolate satellites' incremental contribution versus
  in-situ SST, recover historical revisions, or establish an advantage over
  existing public ENSO forecasts. State, season and model were fixed before
  examining this candidate's outcomes.
- **Atmospheric temperature and gas:** UAH USA48 satellite temperature is a
  lower-atmosphere measurement, not a surface heating-degree-day estimate. The
  independent CPC comparator uses national gas-customer-weighted ground weather.
  All 156 winter months share the same ten-prior-year, same-calendar-month
  training design. The ground-weather model wins; its advantage also survives
  the later-period diagnostic. Inputs and EIA labels are revised archives, and
  the satellite and EIA geographic coverage differ slightly.
- **Snow/runoff feasibility:** authentic MODIS scenes and 312 independent monthly
  California runoff observations were retrieved. The accessible historical snow
  product uses later August–September background observations and retrospective
  smoothing; sampled old MODIS scenes were also reprocessed years later. No
  April forecast was fitted from these future-dependent features. The evidence
  and stronger operational runoff baseline are preserved under `snow/`.

Primary source documentation:
[USDA MODIS/VegScape](https://www.nass.usda.gov/Research_and_Science/Cropland/metadata/metadata_VegScape.htm),
[NASA POWER source and latency table](https://power.larc.nasa.gov/docs/methodology/data/sources/),
[Copernicus reprocessing history](https://documentation.dataspace.copernicus.eu/Data/Others/Sentinel2_L2A_baseline.html),
[EIA Topaz generation](https://www.eia.gov/electricity/data/browser/#/plant/57695).
Additional primary sources:
[UAH satellite temperature](https://www.nsstc.uah.edu/climate/),
[EIA residential gas](https://www.eia.gov/dnav/ng/hist/n3010us2m.htm),
[CPC degree days](https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/cdus/degree_days/),
[NSIDC historical snow product](https://nsidc.org/data/spires_hist/versions/1),
[UNG fund methodology](https://www.uscfinvestments.com/ung).

## External evidence and the next data hurdle

The original published [JFQA parking-lot study](https://doi.org/10.1017/S0022109023001448)
reports that satellite utilization helps predict same-store sales and a
historical earnings-event trading spread. This is useful evidence for choosing a
future commercial-data pilot, not a local replication. Its sales table does not
report held-out forecast RMSE or a satellite-free ablation, and its return figure
does not subtract every implementation cost. The source data are proprietary.
The table-level audit is in `literature/evidence.json` and
`docs/notes/satellite_external_evidence.md`; it does not change our gate result.

The timely GOES follow-up resolves the original solar access question but fails
the independent production test. Snow and crop candidates retain promising local
results alongside failed stronger-comparator and geographic checks. No purchase,
trading order or scheduled task was created.

## Reproduce from a fresh checkout

```bash
pip install -r requirements.txt
python3 src/satellite_construction_validation.py
python3 src/satellite_smelter_validation.py
python3 src/satellite_crop_validation.py
python3 src/satellite_crop_confirmation.py
python3 src/satellite_crop_trading.py --confirmation results/satellite_validation/third_signal/confirmation_predictions_2025.csv
python3 src/satellite_solar_validation.py
python3 src/satellite_enso_validation.py
python3 src/satellite_seaice_validation.py
python3 src/satellite_gas_validation.py
python3 src/satellite_gas_trading.py
python3 src/satellite_goes_solar_validation.py
python3 src/satellite_hydro_validation.py
python3 src/satellite_hurricane_validation.py
python3 src/satellite_pacific_hurricane_validation.py
python3 src/satellite_hurricane_trading.py
python3 src/satellite_snow_daily_validation.py
python3 src/satellite_snow_summer_validation.py
python3 src/satellite_snow_kings_validation.py
python3 src/satellite_vhp_wheat_validation.py
python3 src/satellite_vhp_wheat_panel.py
python3 src/satellite_east_africa_validation.py
python3 src/satellite_south_africa_maize.py
python3 src/satellite_southern_africa_maize.py
python3 src/satellite_cybench_validation.py
python3 src/satellite_cybench_validation.py --late-season
python3 src/satellite_cybench_trading.py
python3 src/satellite_cybench_trading.py --issue-month 9 --output-dir results/satellite_validation/cybench_trading/late
python3 src/satellite_validation_report.py
python3 -m pytest -q
```

The validation commands use committed compact inputs and do not need cloud
credentials. Remote source refreshes are explicit options; re-extracting all
original Sentinel pixels is a separate, larger operation. Source-specific
methods are documented under `docs/notes/satellite_*validation.md` and
`docs/notes/satellite_crop_trading.md`. The shared evaluation contract is
`protocols/satellite_validation.md`.
