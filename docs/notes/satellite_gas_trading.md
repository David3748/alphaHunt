# Satellite gas-demand forecasts: separate market test

The gas-demand forecast evaluation fails against ground heating-degree days.
This separate test asks whether its fixed forecast differences nevertheless
translate into returns in UNG, a traded natural-gas futures fund. They do not
establish an edge: all three forecast strategies lose after the assumed costs.

The protocol was recorded before inspecting gas forecast results or trading
returns. It is exploratory, added after earlier satellite candidates failed.
No position threshold, horizon, instrument, or cost was optimized on the result.

## Fixed rules

- Target months: January–March and October–December, 2008–2025; 108 complete
  events. The final event ends in February 2026 because economic observations
  precede forecast issuance and trading.
- Forecast issue: the 15th of the month after the demand month. Enter at the
  first market close strictly after issue; exit at the first close strictly
  after issue plus one calendar month. The entry day's return is excluded.
- Long if the demand forecast exceeds its comparator, short if below, flat if
  equal. Compare satellite versus temporal baseline, ground weather versus
  temporal baseline, and combined satellite/weather versus weather alone.
- One initial dollar of exposure per dollar of event capital. Each event earns
  its signed adjusted-price holding return, less 25 basis points per side and
  an assumed 3% annual short borrow charge for actual calendar days held.
- Events do not overlap. Compound successive net event returns, with zero cash
  interest between them. Results are not annualized. Always-long and
  always-short controls use identical windows and costs.

## Results

| Strategy | Net compound event return | Mean net event return |
|---|---:|---:|
| Satellite versus temporal baseline | −94.29% | −1.51% |
| Ground weather versus temporal baseline | −94.39% | −1.33% |
| Incremental satellite over weather | −89.57% | −0.79% |
| Always long | −98.22% | −2.59% |
| Always short | +12.48% | +1.35% |

Winter-cluster bootstrap intervals for every mean include zero. The mean
incremental-satellite advantage over always-short also includes zero. No
risk-adjusted alpha, historical short availability, or live performance is
established. Demand for the target month has already occurred when trades begin;
market prices may already reflect the weather even before EIA publishes demand.

## Provenance and reproduction

The committed Yahoo chart response supplies actual adjusted UNG closes, rather
than a synthetic continuous-futures series whose contract switches can create
false returns. UNG's expenses and roll effects are already embedded in prices;
they are not deducted again. The fund's
[official methodology](https://www.uscfinvestments.com/ung) explains its futures
exposure and tracking objective. Prices and predictors are current archives;
borrow costs are a stated assumption, not a reconstructed historical series.

`results/satellite_validation/gas_trading/` contains the frozen protocol, source
URL and checksum, raw prices, complete events, excluded-window log, and metrics.
The summary hashes the exact gas forecast file. Independent review checked all
108 dates, overlap rules, directions, costs, and that changing actual demand
labels cannot change trades. Seven focused tests cover execution and costs.

```bash
python3 src/satellite_gas_validation.py
python3 src/satellite_gas_trading.py
```
