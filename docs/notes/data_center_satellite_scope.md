# Data-center buildout from satellite data: scoped research plan

## Decision

This is feasible, but the investable wedge is narrower than reproducing the
SemiAnalysis product. SemiAnalysis already combines more than 5,000 site records,
permits, property records, FOIA power data, accelerator demand and frequent
satellite imagery. A public-data project should initially target **construction
delivery and slippage at the largest 50–100 campuses**, not claim a complete
global megawatt ledger.

The slow signal is attractive because it is physical and difficult to reverse:
grading, foundations, roof completion, substations and cooling yards unfold over
quarters. Public Sentinel imagery can screen campuses; high-resolution commercial
imagery should be purchased only for ambiguous or high-value sites.

## What can and cannot be observed

| Question | Public Sentinel/Landsat | Commercial VHR | Non-imagery confirmation |
|---|---|---|---|
| Land clearing and grading | Good for large campuses | Excellent | Parcel and grading permits |
| Foundation and shell progress | Directional | Good | Building permits and contractor updates |
| Roof area and building count | Good on hyperscale sites | Excellent | Site plans |
| Cooling/generator-yard configuration | Usually too coarse | Good | Air permits and equipment filings |
| Substation/transmission progress | Directional | Good | Utility and interconnection records |
| Energization | Not directly observable | Not directly observable | Utility filings, meters, air permits |
| GPU type, count, utilization | Not observable | Not observable | Supply-chain and company disclosures |

The Federation of American Scientists' 2026 case studies reach the same boundary:
public imagery can independently verify large construction milestones, but it
cannot directly observe chips or live power consumption.

## Minimum viable data stack

1. **Site seed and identity**
   - Epoch AI Frontier Data Centers Hub for a labelled initial set.
   - PNNL Data Center Atlas and ComputeCompute for US facilities and approvals.
   - OpenStreetMap `telecom=data_center`, parcel records and company disclosures
     for expansion.
   - Preserve every discovery and label with an `observed_at` timestamp.

2. **Open imagery screen**
   - Sentinel-2 Level-2A optical imagery at 10 m for clearing, exposed soil,
     roof area and paved-surface change.
   - Sentinel-1 SAR for cloud-independent structural change and construction
     activity. Use ascending and descending tracks separately.
   - Landsat Collection 2 for the pre-2015 historical baseline.
   - Monthly cloud-free composites plus change features; keep original scene
     publication times for a genuine point-in-time backtest.

3. **Selective commercial verification**
   - Buy 0.3–3 m archive/tasking only after the open-data detector flags a
     meaningful change or a milestone is disputed.
   - Measure roof outlines, cooling equipment, generator yards and substations.
   - Store provider, acquisition timestamp, publication timestamp, resolution,
     license and cost for every image.

4. **Permits and power ledger**
   - County planning/building permits, property transfers and tax parcels.
   - EPA air permits for emergency generators; water agreements where relevant.
   - Utility/RTO interconnection and large-load filings, substations and
     transmission construction.
   - Company/operator announcements kept as claims, not ground truth.

## Site state model

Each campus should advance through an auditable state machine:

`identified → land assembled → permitted → clearing → foundations → shell → fit-out → energized → live`

Required outputs per observation:

- probability of each state;
- disturbed area, foundation area and completed roof area;
- number and dimensions of completed shells;
- visible substation, cooling and generator-yard progress;
- estimated critical-IT MW range, never a false-precision point estimate;
- days ahead/behind the public schedule;
- source links and image timestamps.

## First investable hypotheses

The first tests should be quarterly or slower and pre-registered before return
inspection:

1. **Delivery surprise:** long operators/suppliers whose observed construction
   progresses faster than disclosed schedules; short repeated slippage.
2. **Equipment demand lead:** translate foundations and shell progress into a
   6–18 month demand window for switchgear, transformers, cooling and backup power.
3. **Power bottleneck:** compare visible campus progress with substation and
   interconnection progress; flag shells likely to wait for energization.
4. **REIT local supply:** aggregate new data-center shell/MW estimates by market
   and compare with point-in-time EQIX/DLR/IRM exposure and lease-up commentary.
5. **Capex nowcast:** reconcile operator-level observed construction with
   announced capital expenditure and consensus revisions.

These must be benchmarked against simpler permit-only and announcement-only
models. Satellite data earns its keep only if it improves timing, false-positive
rejection or capacity estimates.

## Pilot design

### Phase 1 — 15 labelled campuses

- Use Epoch AI's open 13-site hub plus two well-documented international sites.
- Download 2017-present Sentinel-1/2 and Landsat scenes around fixed AOIs.
- Hand-label quarterly state, disturbed area, roof area and major milestones.
- Establish whether open imagery catches changes within 30–60 days.

**Pass gate:** at least 80% recall for major stage changes and median timing error
below 60 days versus independently dated milestones.

### Phase 2 — 50–100 sites

- Train a change detector using spectral indices, SAR backscatter and segmentation.
- Add permits, utility filings and owner/operator resolution.
- Run blind reviews on campuses excluded from training.

**Pass gate:** materially better delivery-date and capacity-range accuracy than
the permits/news baseline, with an explainable false-positive log.

### Phase 3 — Point-in-time market test

- Freeze features and labels as they would have been known on each date.
- Test quarterly signals across data-center REITs, operators, utilities and
  equipment suppliers.
- Charge realistic delay, imagery and trading costs; report event count rather
  than treating daily returns as independent observations.

## Build versus buy

- **Buy SemiAnalysis** if the objective is an immediately usable 5,000-site,
  operator/MW/power ledger. Their institutional model is far ahead of a new build.
- **Build the pilot** if the objective is proprietary announcement-versus-ground-
  truth signals, transparent point-in-time histories, or a focused subset of
  companies and utility regions.
- **Hybrid is best:** use commercial/reference data for labels and identity while
  owning the image-change pipeline, timestamps and investable feature history.

## Principal risks

- Data centers resemble warehouses at medium resolution; site identity cannot be
  inferred safely from imagery alone.
- Redevelopment and vertical fit-out may show little external change.
- Roof completion precedes energization and live compute by an uncertain interval.
- Public imagery is cloudy and coarse; commercial archives have license and
  survivorship issues.
- Current databases can leak future identity and ownership backward. Every site,
  operator and capacity label needs vintage control.
- A small number of hyperscale projects can dominate apparent results.

## Sources

- [SemiAnalysis Datacenter Industry Model](https://semianalysis.com/datacenter-industry-model/)
- [FAS: Tracking Hyperscale AI Data Center Growth with Satellite Imagery](https://fas.org/publication/tracking-hyperscale/)
- [Epoch AI Frontier Data Centers Hub](https://epoch.ai/latest/introducing-the-frontier-data-centers-hub)
- [PNNL Data Center Atlas](https://www.pnnl.gov/publications/mapping-future-data-centers-new-public-tool-illuminates-whats-next)
- [ComputeCompute public-record tracker](https://computecompute.org/)
- [Copernicus Sentinel-1](https://dataspace.copernicus.eu/data-collections/copernicus-sentinel-missions/sentinel-1)
- [USGS Landsat surface reflectance](https://www.usgs.gov/landsat-missions/landsat-surface-reflectance)
- [Tang et al. 2024, broad-area construction detection](https://doi.org/10.1016/j.srs.2024.100138)
- [Suh, Zhu & Zhao 2024, dense satellite construction monitoring](https://doi.org/10.1016/j.rse.2024.114207)
