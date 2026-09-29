# Smelter heat: physical validation, output forecast not verified

The existing Sentinel-2 heat feature does **not** pass a useful-output-forecast
test. Its modest improvement over a previous-quarter forecast for Kennecott
refined copper disappears against the historical mean and with tighter cloud
screening. The more direct target, concentrate smelting throughput, performs
worse than both persistence and the historical mean. Retain this signal as a
research candidate and plant-monitoring diagnostic, not verified financial alpha.

## Reproduce

```sh
python3 src/satellite_smelter_validation.py
python3 -m pytest tests/test_satellite_smelter_validation.py -q
```

The calculation requires only the already committed heat CSVs and
`results/satellite_validation/smelters/production_labels.csv`; it does not need
network access. `label_sources.json` preserves each original report URL, PDF SHA256,
page number, reported numeric rows and publication date. All 13 primary source
PDFs were downloaded and their operating tables checked for this evaluation.
The 2025 Q1 production-table supplement has no release date on its first page;
its date is independently sourced to Rio's dated release, linked in the manifest.
The 2022 quarters use the January 2023 Q4 report vintage, before any evaluated
forecast. The other labels come from their original quarterly release, avoiding
the casual assignment of year-end revised values to earlier release dates.

## Forecast experiment

- Target: Kennecott quarterly refined copper, plus the separately reported
  concentrate tonnage smelted. These are different quantities, both in kt.
- Forecast time: quarter end, before the formal production report by at least
  14 calendar days. This is an output **nowcast**, not next-quarter prediction.
- Input: fraction of clear scenes with at least one hot pixel in the existing AOI.
  Use only assets whose `created` time is known and no later than quarter end.
  A quarter with no available imagery stays missing. The existing 20% AOI cloud
  cutoff is retained. A quarter requires at least three observations.
- Model: expanding OLS with an intercept and that one feature, after five prior
  labeled quarters. Historical feature vectors retain their original quarter-end
  availability; late backfills never improve a past training feature.
- Comparators: last quarter, same quarter last year, and the expanding training
  sample mean. Labels with later publication dates cannot enter any forecast.
- Evaluation: eight chronological forecasts, 2024 Q1 through 2025 Q4. Report all
  outcomes and cloud sensitivities; no feature, lag, window or threshold search.
- Uncertainty: paired absolute-error differences resampled in circular blocks
  of two quarters, 10,000 draws with fixed seed. Eight quarters still provide
  weak inference; these intervals are stability diagnostics.

This is retrospective chronological validation, not a pristine or prospectively
reserved holdout: the existing AOIs and heat definitions came from exploratory
work, and the underlying time series was already visible. The explicit promotion
heuristic is at least 10% MAE reduction against **all** comparators plus a positive
paired interval. It is a research standard for this report, not a preregistered
test or a claim that 10% is a universal economic threshold.

| Target | Satellite MAE | Previous-quarter MAE | Seasonal MAE | Historical-mean MAE |
|---|---:|---:|---:|---:|
| Refined copper | 12.21 kt | 12.73 kt | 18.05 kt | **12.09 kt** |
| Concentrates smelted | 49.45 kt | 38.63 kt | 57.00 kt | **32.48 kt** |

Refined copper improves on persistence by only **4.0%**, wins in 2 of 8 quarters,
and is 1.1% worse than the mean. The paired improvement over persistence is
0.51 kt, with a block interval of **−3.95 to +8.27 kt**. Concentrate throughput is
28.0% worse than persistence and 52.3% worse than the mean. Tighter AOI cloud
cutoffs of 5% and 10% each leave seven evaluated quarters; the refined-copper
satellite MAEs are 13.65 and 14.30 kt, versus 12.29 kt for persistence.

This result gives a concrete reason not to promote the present signal:
**Kennecott has zero hot detections in all 10 available clear scenes in 2025 Q1,
yet reports 42.3 kt refined copper and 163 kt concentrates smelted.** A no-heat
observation is not a reliable zero-output label. The broad AOI may contain hot
slag rather than only the furnace, the furnace can be obscured despite a mostly
clear AOI, and a short daytime overpass samples intermittent processes. In
addition, cathode output has inventory/refinery delays and excludes purchased
and tolled concentrate, unlike the total physical process seen from space.

## Independent company-event checks

Codelco confirms smelting ceased at Ventanas on May 30–31, 2023 and that the
electrolytic **refinery continued operating**. Using assets created by December
31, 2024, the existing AOI has heat in 24/42 pre-closure clear scenes and 0/51
post-closure scenes, covering 14 and 17 observed months respectively. All five
January–May comparisons show heat before closure in 2023 and none in 2024.
This verifies a narrow physical interpretation, but does not forecast refined
copper or establish a tradable information lead. It is one selected episode,
and adjacent scenes are not independent trials; no per-scene binomial p-value
is used. [Codelco's primary closure account](https://www.codelco.com/tras-90-dias-concluyeron-trabajos-de-detencion-total-de-fundicion-ventanas)
explicitly distinguishes the closed smelter from the operating refinery.

Freeport's October 22, 2024 report confirms the October 14 fire and suspension of
smelter operations. Of four available clear scenes in the existing repair window
through March 2025, three still contain furnace-zone heat; the average is 1.25
furnace pixels versus 16.75 elsewhere in the AOI. There are **no** clear scenes
in January–March 2025. This warns against treating any heat as output, broad AOI
heat as furnace-specific, or an unobserved quarter as zero activity.
The November 8, 2024 asset was created in August 2026 and is excluded.
[Freeport Q3 2024 report, page 7](https://s22.q4cdn.com/529358580/files/doc_news/2024/FCX_241022_3Q_2024_Earnings_Release-docx.pdf)
provides the company label; the existing furnace sub-box was itself selected
from commissioning imagery and is not an independent blind AOI.
The [January 2025 report, page 7](https://s22.q4cdn.com/529358580/files/doc_news/2025/FCX_250123_4Q_2024_Earnings_Release.pdf)
confirms that operations remained suspended for repairs, and the
[April 2025 report, page 8](https://s22.q4cdn.com/529358580/files/doc_news/2025/FCX_250424_1Q_2025_Earnings_Release.pdf)
corroborates the retrospective window with startup still expected in Q2 2025.

## Availability and practical limits

The Kennecott file contains 47 assets created over seven days after acquisition,
including seven over a year later; the longest delay is 1,144 days. Ventanas has
25 and two, respectively, with a 1,159-day maximum. In particular, all Q2/Q3
2022 Kennecott acquisitions arrive too late for their original quarter-end
forecasts. The archived `created` field may represent a replacement or reprocessed
asset, not first availability from every possible provider, so this evaluation
uses the conservative, reproducible availability of the actual asset in hand.

An analyst-consensus comparator and point-in-time public maintenance schedules
would be additional required controls before claiming new market information.
Local furnace cloud masks, blind furnace geometry, distinction between emitted
heat and residual slag, and a larger independently chosen plant panel are the
next measurement improvements. A genuine prospective output test should precede
any trading test; no trading returns or alpha claims are made here.

Primary operating tables and economic definitions are in [Rio Q4 2023](https://www.riotinto.com/-/media/content/documents/invest/financial-news-and-performance/production/2023/rt-2023-4qor.pdf),
[Rio Q4 2024](https://www.riotinto.com/-/media/content/documents/invest/financial-news-and-performance/production/2024/2024-4qor-pdf.pdf?rev=a7d510be7ed9429bb7d564c565e5f748),
and [Rio's Q4 2025 SEC filing](https://www.sec.gov/Archives/edgar/data/863064/000086306426000006/ex1_2025-q4results.htm).
Each original quarterly source and the exact raw numeric rows are preserved in
the label manifest, rather than relying on those later summaries for training.
