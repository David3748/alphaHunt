# Frozen ground-weather comparator

The comparator is NOAA CPC **CONUS utility-gas-customer-weighted heating degree days**, selected before this data retriever examined gas-demand outcomes. The model implementer requested January 1990–December 1999 training history in addition to the initially requested January 2000–December 2025 history; the source, weights, and aggregation did not change. Model evaluation dates belong to the independently frozen gas protocol.

`monthly_hdd.csv` has `date` (ISO first day of the observation month) and `hdd` (sum of the published daily CONUS integers, Fahrenheit degree days). It is an observation-month label, **not a release date**. The daily base is 65°F. The included raw annual files permit exact offline reproduction. Daily rounding is preserved; monthly totals can differ from products calculated from unrounded daily values. No missing observations are filled.

The [CPC statistics page](https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/cdus/degree_days/) identifies current daily data, CONUS coverage, and 2010 ACS heating-fuel-customer weights. The [official annual-file directory](https://ftp.cpc.ncep.noaa.gov/htdocs/degree_days/weighted/daily_data/) provides `YEAR/UtilityGas.Heating.txt`. We select its `CONUS` row, not the nine individual census divisions, and do not recompute weights from hindsight gas consumption. These are conventional near-surface weather observations, not a new satellite predictor: CPC's [temperature-method page](https://www.cpc.ncep.noaa.gov/soilmst/t.shtml) describes daily station-based climate-division estimation and its use for heating degree days. The [older detailed degree-day explanation](https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/cdus/degree_days/ddayexp.shtml) supports the temperature/base/aggregation concepts but describes legacy 2000 Census weights; it must not be used to assert the current product still has exactly 198 source stations or unchanged processing.

## Availability and vintage limits

- A retrieved current feed, saved under `metadata/latest_utility_gas.txt`, includes observations through **September 24, 2026**. Its HTTP last-modified time is **September 26, 2026, 08:07:01 UTC**, also visible in the saved directory index. This is direct evidence of an approximately two-day lag for this snapshot, not a historical service guarantee.
- A forecast issue on the 15th of the following month comfortably follows that observed lag. Treat historical availability at that issue as an assumption. The original release time of every daily value is not preserved here.
- Annual files are mutable current archives. Many files before 2013 were last modified October 29, 2013; their fixed 2010 weights could not have been known in earlier forecast years. CPC also documents corrected erroneous real-time 2008 and 2009 degree-day values. Thus historical performance is a **current-archive hindcast**, not a fully certified point-in-time backtest.
- Each source snapshot, URL, SHA-256, HTTP modification time, and retrieval time appears in `source_manifest.json`. A year-end file modification time is not each included day's initial publication time. No gas-demand series is retrieved or inspected by this script.

Offline rebuild and integrity/complete-day checks:

```sh
python3 results/satellite_validation/gas/ground_hdd/rebuild.py
```

Explicit network refresh (changes snapshots and hashes):

```sh
python3 results/satellite_validation/gas/ground_hdd/rebuild.py --fetch
```

`coverage.json` records date range, day and month counts, latest-feed observation date, and the CSV SHA-256. This folder validates the weather comparator's construction; it does not establish any satellite forecast improvement or tradable alpha.
