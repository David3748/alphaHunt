# Separate insurance-sector trading diagnostic

The storm forecasting model and its trading value are separate tests. The Atlantic forecast failed its strongest baseline and uncertainty gate. Its financial diagnostic is retained regardless of that failure.

Before inspecting the forecast/price join, the trading protocol fixed 2006–2025 annual windows, the KIE insurance ETF against SPY, and a direction rule. Predicted August–November storm energy above the expanding prior-year mean shorts KIE and buys SPY; below it reverses both legs. Each leg is 50% of capital, giving 100% gross exposure. The incremental overlay instead compares the satellite forecast with the otherwise identical nonsatellite forecast.

Entry is the first common trading close on or after August 1, following the midnight forecast issue. Exit is the last common trading close on or before November 30. There is no holding-period search. Adjusted returns reflect distributions and splits; negative adjusted returns on the short leg include distributions owed. Costs are 10 basis points per leg per transaction (20 basis points total on capital per round trip), plus assumed annual 3% borrow on the 50% short leg. A sensitivity doubles execution costs.

Across 20 seasons:

| Strategy | Compounded net event return | Mean net season return |
|---|---:|---:|
| Satellite forecast | −18.37% | −0.966% |
| Nonsatellite forecast | −19.87% | −1.058% |
| Incremental satellite overlay | −18.32% | −0.963% |
| Always long KIE / short SPY | −7.18% | −0.329% |
| Cash | 0.00% | 0.000% |

The satellite mean-return 95% season-bootstrap interval is **−2.266% to +0.269%**. Its paired advantage over the nonsatellite strategy also has a lower bound of zero. No net trading advantage is verified. These are compounded event-window returns, not annualized or risk-adjusted alpha. No trades were placed.

This is a coarse economic screen. KIE includes insurers whose main exposure is not Atlantic catastrophe losses; its benchmark changed in 2011. Basin-wide storm energy is not insured landfall damage, and public agencies already issue seasonal forecasts using ocean temperatures. Historical borrow availability and original forecast vintages were not reconstructed.

Sources: [State Street's KIE fund description](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-insurance-etf-kie), [NOAA seasonal outlook scope](https://www.cpc.ncep.noaa.gov/products/outlooks/hurricane.shtml), and saved Yahoo Finance chart responses with URLs and hashes under `results/satellite_validation/hurricane_trading/inputs/`.

```sh
python3 src/satellite_hurricane_trading.py
python3 -m pytest -q tests/test_satellite_hurricane_trading.py
```
