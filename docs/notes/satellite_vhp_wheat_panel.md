# Geographic replication: Kansas, Oklahoma and Texas winter wheat

The fixed Kansas/Oklahoma confirmation fails. Pooled yield RMSE improves only 1.96% versus the ground-weather model (95% paired calendar-block interval −3.79% to +8.52%). Production per planted acre is 0.23% worse. The common three-state exploratory pool also fails. Oklahoma individually improves, but Kansas worsens, so selecting the positive state would contradict the frozen replication criterion.

| State | Eligible years | Yield RMSE: weather → satellite | RMSE improvement | 95% interval |
|---|---:|---:|---:|---:|
| Kansas | 23 | 5.909 → 6.330 bu/acre | −7.14% | −14.32% to −2.10% |
| Oklahoma | 23 | 5.848 → 5.134 | +12.21% | +2.00% to +22.16% |
| Texas, already seen | 23 | 3.265 → 3.052 | +6.54% | −4.57% to +23.41% |
| Kansas/Oklahoma confirmation | 23 calendar years | 5.878 → 5.763 | +1.96% | −3.79% to +8.52% |

USDA's historical June forecast is materially stronger in the new states: pooled June-forecast RMSE is 3.014 bu/acre versus the satellite model's 5.763. Oklahoma's individual weather-model improvement does not show incremental information beyond that public forecast. The secondary production/planted-acre result improves 9.11% in Oklahoma, worsens 5.51% in Kansas, and fails the fixed pooled check. Both states and both targets remain in the outputs, irrespective of sign.

This expansion was frozen after seeing the failed Texas result, before any Kansas/Oklahoma feature-outcome join or metric. The geographic rationale was the other major Southern Great Plains hard red winter wheat states. NOAA returned province 17 explicitly as Kansas and 37 as Oklahoma; NCEI's corresponding ground-climate headers identify states 14 and 34. The Texas formula, March weeks 9–13, 56-day satellite smoothing allowance, June 15 nominal issue, October–May weather, expanding 15-year minimum training sample, ridge alpha 5 and missing-data rules were unchanged. Texas's original outcomes were imported without refitting or selecting its successful years.

Pooled MSE and MAE give each state equal weight; the reported RMSE is the square root of average state MSE. Uncertainty resamples two-calendar-year blocks with the same sampled years across states, preserving cross-state drought correlation and missing calendar cells. State-years are never treated as independent. The primary confirmation requires both new states to have at least 20 held-out years, ≥5% pooled RMSE reduction, lower MAE, and a positive lower 95% bound against weather, plus point improvements against mean, persistence and trend. It fails that unchanged gate.

Sources and vintage limitations are the same as the [Texas experiment](satellite_vhp_wheat_validation.md): official [NOAA crop vegetation interface](https://www.star.nesdis.noaa.gov/smcd/emb/vci/VH/vh_adminMeanByCrop.php?type=Province_Weekly_MeanPlot), NOAA NCEI Climate at a Glance, and [USDA NASS bulk files](https://www.nass.usda.gov/datasets/). Compact source responses, exact official URLs, hashes and the pre-fit protocol are preserved under `results/vhp_wheat_panel/`. The NASS filtered rows retain final annual labels and named seasonal forecasts separately. These are current archives, with historical crop-mask, processing, label and publication-vintage limitations. The forecast is before final annual reporting but partly during harvest; no trading alpha, independent temporal confirmation or family-wise discovery is claimed.

```sh
python3 src/satellite_vhp_wheat_panel.py
python3 -m pytest -q tests/test_satellite_vhp_wheat_validation.py tests/test_satellite_vhp_wheat_panel.py
```

Twenty-two tests pass, including equal-state weights under unequal support, preservation of perfectly correlated states in the bootstrap, source identities, per-state sample requirements, unavailable feature abstention and future-label invariance. The new results do not authorize replacing the failed primary with Oklahoma alone. A USDA-consensus residual model was suggested after the first Texas fit, so it was not retroactively added to this claim.
