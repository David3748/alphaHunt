# Corrected trend model: South Africa development and Zambia/Zimbabwe confirmation

**Result: failed independent confirmation.** Removing the ridge penalty from the
time trend improved South Africa point forecasts, but its uncertainty intervals
still included zero improvement. Both independently reserved countries lost to
their own best simple comparator. No result here verifies economic usefulness.

| Target | Information assumption | Zambia/Zimbabwe pooled RMSE gain vs best comparator | 95% paired interval | Gate |
|---|---|---:|---:|---|
| Yield | Conservative FAO release | −26.75% | −49.12% to −9.09% | Fail |
| Yield | Latest completed harvest known | −21.45% | −54.52% to +2.22% | Fail |
| Total production, prespecified secondary | Conservative FAO release | −8.47% | −27.81% to +10.58% | Fail |
| Total production, prespecified secondary | Latest completed harvest known | −3.37% | −32.54% to +13.86% | Fail |

The strongest pooled comparator was persistence in all four cases. There were
22 heldout harvest years per country with conservative releases and 24 with the
optimistic completed-harvest information assumption. All country and comparator
results, including unsuccessful ones, are in `metrics.csv`, `summary.json` and
the forecast-level files under `results/satellite_validation/southern_africa_maize_replication`.

## Why this separate study exists

The earlier South Africa study penalized every predictor, including calendar
time. Its technology trend was smaller and forecasts systematically low. That
observation motivated a correction, so **South Africa is development evidence**.
Its original negative files are unchanged. The new protocol froze both Zambia
and Zimbabwe together before their labels were extracted, and required both to
improve. Neither country could be removed after seeing outcomes.

An unpenalized intercept and linear time term form U. Historical prior outcome,
August/September gauge precipitation and, for the satellite model, September
Niño3.4 SST form Z. Z is standardized only on training rows. The model residualizes
y and Z on U, fits ridge with alpha 5 to the residuals, and estimates U conditional
on those fitted Z coefficients. This leaves an exact linear trend unshrunk.
No horizon, country, crop, regularization or coverage search followed the freeze.

Linear detrending is an established way to separate technology and climate effects
in crop studies; see [this South African maize drought study, section 2.5](https://www.mdpi.com/2071-1050/16/11/4703).
The [Martin, Washington and Downing seasonal maize study](https://ora.ox.ac.uk/objects/uuid%3A16431f86-2f45-4e67-9564-22ca96af32e7)
supports the ENSO/maize water-stress rationale. Neither paper validates this exact
partial-ridge implementation or substitutes for its failed empirical test.

## Timing, data and interpretation

September SST and August–September rainfall precede an October 20 issue for the
following calendar year's entire FAOSTAT maize harvest. SST uses a 20-day nominal
delay and gauge data a 10-day delay. No subsequent growing-season weather or
current target-year area enters a predictor. The primary conservative release
assumption uses the latest label three harvest years behind the target. The
stronger information stress allows the latest completed harvest's final value;
this is deliberately optimistic about revisions, and prevents a slow FAO release
from artificially weakening the baseline.

FAOSTAT calendar years follow the bulk harvest, as explained in its
[crop methodology](https://files-faostat.fao.org/production/QCL/QCL_methodology_e.pdf).
The labels came from the [official crop-production bulk archive](https://bulks-faostat.fao.org/production/Production_Crops_Livestock_E_All_Data_(Normalized).zip).
Published yield units and production/harvested-area ratios are cross-checked.
Original flags are retained, including non-A observations. The model's May–August
target dates are inherited descriptive bulk-harvest metadata from South Africa;
they do not define a monthly target or assert an identical national harvest
calendar. The forecast target is each country's full calendar harvest-year label.

Ground controls use [NOAA CPC gauge-only precipitation](https://psl.noaa.gov/thredds/catalog/Datasets/cpc_global_precip/catalog.html)
with national masks from [World Bank official boundaries](https://datacatalog.worldbank.org/search/dataset/0038272/world-bank-official-boundaries).
The masks were frozen before weather reads: 249 Zambia cells and 135 Zimbabwe
cells. Every calendar day and at least 90% valid cells each day are required.
The same three unavailable source months are retained as missing in both
countries. Grid coverage does not establish dense gauge coverage; the CPC
documentation warns of limited tropical African analysis quality.

The pooled metric gives each country equal weight after scaling errors by that
country's fixed first-15-training-label mean. Ten thousand paired circular
five-calendar-year bootstrap draws are shared across countries. The frozen gate
requires both countries to improve, at least 20 heldout years each, pooled gain
of at least 5%, lower MAE and positive 95% gain intervals against every main
comparator, in both release regimes. Yield and production remain separate tests.

This remains an exploratory sequence after other candidates were tried. The
archives are current revised versions, the boundary is contemporary, OISST
blends satellite and in-situ observations, and original data vintages were not
reconstructed. There is no family-wide discovery guarantee, official-forecast
superiority test, satellite-only causal attribution or trading-profit result.

## Reproduction

```sh
python3 results/satellite_validation/southern_africa_maize_replication/ground/rebuild_ground.py
python3 src/satellite_southern_africa_maize.py
python3 -m pytest -q tests/test_satellite_southern_africa_maize.py tests/test_satellite_south_africa_maize.py results/satellite_validation/southern_africa_maize_replication/ground/test_ground.py
```

The validator checks inherited SST/weather hashes, FAO filtered labels, all new
ground-manifest entries and frozen national-mask hashes before fitting. The
main summary records protocol, source and implementation hashes. The geographic
confirmation protocol SHA256 is
`95fb9b7f3011c4e27655159b5a98d8577f311605497f9f27e4e1a0e6b38389ef`.
