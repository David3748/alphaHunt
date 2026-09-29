# Crop-masked NOAA vegetation and Texas wheat

The frozen June 15 nowcast fails its verification gate. On 23 eligible held-out harvest years between 2000 and 2025, March satellite vegetation/temperature improves yield RMSE by 6.54% against weather, trend and prior yield, but its paired two-calendar-year bootstrap interval is −4.57% to +23.41%. It essentially ties the existing USDA June forecast. Production per planted acre, a separately prespecified way to capture drought abandonment, also fails uncertainty validation. This is an exploratory candidate after other satellite candidates had failed; neither result establishes profitable trading or a family-wise discovery.

| Target/comparator | Baseline RMSE | Satellite RMSE | RMSE reduction | 95% paired interval |
|---|---:|---:|---:|---:|
| Yield vs weather | 3.265 bu/harvested acre | 3.052 | 6.54% | −4.57% to +23.41% |
| Yield vs official USDA June forecast | 3.057 | 3.052 | 0.17% | −32.84% to +23.54% |
| Production/planted acre vs weather | 3.248 bu/planted acre | 3.045 | 6.25% | −6.54% to +17.39% |

The primary model uses fixed ridge alpha 5, with all predictors standardized only on earlier training observations. Its inputs are calendar year, previous yield, October–May Texas rainfall and mean temperature. Satellite adds only mean SMN and SMT in weeks 9–13, over NOAA's WHEA crop mask for Texas. Training expands from at least 15 common complete prior years; all models use the same rows. Other comparators are past mean, previous yield, unpenalized trend, and previous-year satellite placebo. No windows, states, penalties or model variants were searched in the Texas experiment. Source missing values require abstention in 2003/2004; 2005 also abstains because the prior-year placebo is missing. All 26 intended years remain in predictions.

[NOAA's definitions](https://www.star.nesdis.noaa.gov/smcd/emb/vci/VH/VH-Syst_10ap30.php) identify SMN as smoothed NDVI and SMT as smoothed brightness temperature. The [processing documentation](https://www.star.nesdis.noaa.gov/jpss/documents/AMM/NPP/VIIRS-VH_ARR_Prov.pdf), slide 74, describes a 15-week smoothing window and revision of the latest seven weeks. Consequently the protocol delays the last selected week by 56 days. It avoids VCI/TCI/VHI, whose climatological normalization uses later history. This buffer addresses the documented smoothing window; it does not certify every historical algorithm or crop-mask version.

The [NOAA crop interface](https://www.star.nesdis.noaa.gov/smcd/emb/vci/VH/vh_adminMeanByCrop.php?type=Province_Weekly_MeanPlot) says GC_Current uses an older pre-2010 GlobalCover land/water mask and a 1981–2017 climatology. The exact wheat crop-mask vintage was not independently established. The experiment uses the returned GC_current version, without switching to the newer WF2025 option. The [direct ASCII history](https://www.star.nesdis.noaa.gov/smcd/emb/vci/VH/get_TS_admin.php?TagCropland=WHEA&country=USA&provinceID=44&type=Mean&year1=1982&year2=2025&yearlyTag=Weekly) explicitly identifies Texas and WHEA.

Ground controls use NOAA NCEI statewide Texas monthly precipitation and temperature from Climate at a Glance. May weather receives a 14-day availability allowance, which caused the nominal issue to move from June 1 to June 15 **before fitting**. It is a pre-final-report nowcast, partly during harvest. Its monthly source archive is revised, not a reconstructed first-release collection. The [Texas annual wheat review](https://www.nass.usda.gov/Statistics_by_State/Texas/Publications/Current_News_Release/2024_Rls/tx-wheat-review-2024.pdf) explains that NASS surveys final yields in September and publishes production forecasts during the season. Historical final labels and named June forecasts come from the [official NASS bulk data](https://www.nass.usda.gov/datasets/); the retained source rows preserve their exact reference periods, units and load times. A conservative November 1 final-label training embargo is an assumption, not a proof of original publication. No current-year final planted acreage is an input: it only defines the secondary outcome. A total-production forecast using contemporaneous acreage was not claimed.

Reproduce from the repository root:

```sh
python3 src/satellite_vhp_wheat_validation.py
python3 -m pytest -q tests/test_satellite_vhp_wheat_validation.py
```

The compact raw rows, full NOAA time series, weather, documents and hashes are in `results/vhp_wheat/raw/`. `fetch_sources.py` can refresh the named official endpoints, including streaming the large NASS bulk file; offline validation checks saved source hashes and is preferred for exact reproduction. `protocol.json` and its pre-fit addendum preserve the original gate and the direct June-forecast comparator. Sixteen tests cover time windows, smoothing latency, source/label definitions, unavailable inputs, training-only scaling, missing years, future-label invariance, and no accidental use of the official forecast or final planted area as model inputs.
