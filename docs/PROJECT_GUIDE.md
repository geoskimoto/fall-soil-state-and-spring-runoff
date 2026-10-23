# Fall Soil State and Spring Runoff — Project Guide

Status: DRAFT for review. No implementation or tests exist yet.
Repo: https://github.com/geoskimoto/fall-soil-state-and-spring-runoff

## 1. Question and hypothesis

**Question.** How does the soil / shallow-groundwater state at the start of the snow season
affect (a) spring-summer runoff volume, (b) runoff timing, (c) recession behavior, and
(d) late-season baseflow in Columbia River Basin headwaters?

**Hypothesis.** Dry soils in fall are a storage reservoir that must be refilled before
snowmelt generates runoff. If a persistent snowpack forms before soils rewet, a larger share
of melt goes to recharge, so runoff efficiency drops, timing shifts, and recessions steepen
or flatten depending on where the storage sits.

**Fill-and-spill prediction.** The effect should be nonlinear: strong below a deficit
threshold, weak above it. Expected strongest in mid-elevation, thin-soil, continental basins;
weakest in maritime basins where soils rewet quickly.

**Causal variable is the storage deficit at snow-on, not fall precipitation itself.**
Decompose it:

| Component | Metric |
|---|---|
| End-of-summer state | Soil moisture ~Sep 1; Sep-Oct baseflow (groundwater proxy) |
| Fall recharge | Rain (not snow) Sep 1 to snow-on; SPI-2/3 for Sep-Nov |
| State at snow-on | Soil moisture on the snow-on date (key predictor) |

## 2. Scope and phasing

- **Phase 1 (now):** natural-flow headwater sub-basins, pilot set of 5. Aggregate-only data.
- **Phase 2:** more headwater basins; regional synthesis across the basin.
- **Phase 3:** mainstem using BPA modified flows (or another validated unregulated product);
  quantify how much dam operations mask the signal.

Deliverables: (1) research paper, (2) Plotly Dash explorer application.

**Scale / users (per global guideline; confirm):** paper readers and collaborators, low
concurrency. Analysis package is framework-independent so the UI can be swapped cheaply.

## 3. Pilot basins (candidates, to be verified in Phase 0)

Gauge IDs below are from memory and are NOT yet verified. Phase 0 must confirm each against
GAGES-II / HCDN-2009 (reference status), period of record, regulation/diversions, and
SNOTEL coverage (SWE and soil sensors) before approval.

| Setting | Basin | Candidate USGS gauge | Why |
|---|---|---|---|
| Maritime | Cowlitz R. at Packwood (WA Cascades, west slope) | 14226500 | Fast soil rewetting; expected weak effect (negative control). Replaces Cispus R. (14232500), whose daily record ends 1997 |
| Maritime (alt) | Lookout Cr. near Blue River (OR, H.J. Andrews) | 14161500 | Small (~61 km2), well studied; fallback for Packwood |
| Transition | Wenatchee R. at Plain (WA east Cascades) | 12457000 | Dry summers, rain-snow transition |
| Transition | Entiat R. near Ardenvoir (WA east Cascades) | 12452800 | Steeper, thinner soils. The earlier ID 12452500 was the regulated Chelan R. |
| Continental | Selway R. near Lowell (ID) | 13336500 | Wilderness, largely unregulated |
| Continental | Middle Fork Salmon R. at MF Lodge (ID) | 13309220 | Batholith geology contrast; only 35 complete WYs, so marginal |
| Alternate | Minam R. at Minam (OR, Blue Mtns) | 13331500 | Fallback if a slot above fails screening |

### Phase 0 screening results (2026-10-23, `scripts/phase0_screen_basins.py`)

Complete water year = >= 350 daily-mean values. Basin area from NLDI polygons.

| Gauge | Complete WYs | Range | WYs since 1982 | SNOTEL inside basin | SNOTEL within ~10 km |
|---|---|---|---|---|---|
| 14226500 Cowlitz, Packwood | 104 | 1912-2026 | 45 | 0 | 6 |
| 14161500 Lookout Cr. | 69 | 1950-2026 | 45 | 0 | 2 |
| 12457000 Wenatchee, Plain | 103 | 1912-2026 | 37 | 1 | 4 |
| 12452800 Entiat | 69 | 1958-2026 | 45 | 1 | 1 |
| 13336500 Selway | 97 | 1930-2026 | 45 | 1 | 4 |
| 13309220 MF Salmon | 35 | 1974-2026 | 27 | basin polygon unavailable | |
| 13331500 Minam | 62 | 1913-2026 | 45 | basin polygon unavailable | |

Findings that change the plan:
1. **Observed soil moisture is too short to carry the analysis.** No SNOTEL station inside any
   candidate basin has a soil-moisture (SMS) sensor. The few nearby sensors started 2010-2016
   (<= ~15 years). Reanalysis/model soil moisture (ERA5-Land, NLDAS-2) must be the primary
   predictor, with the short SNOTEL SMS records used only to validate its bias and dynamics.
2. **SNOTEL SWE coverage is thin inside basins** (0-1 stations, mostly from 1979+).
   Basin-scale SWE should come from gridded products (UA/SWANN from 1982), with SNOTEL as a check.
3. **Effective sample size is ~45 water years per basin** (common period 1982+), which supports
   the hierarchical, partial-pooling design.
4. **MF Salmon (35 complete WYs, 27 since 1982) is marginal**; Minam is the likely replacement.

Still open from Phase 0 (not yet verified):
- Regulation / reference status (GAGES-II class, HCDN-2009 membership) for every gauge.
  Until verified, "unregulated" is an assumption.
- Basin polygons for MF Salmon and Minam (NLDI timed out; use another delineation service).
- Cowlitz at Packwood: check for upstream hydropower diversions (e.g. Packwood Lake).

Selection criteria: >= 40 water years, unregulated or minimally diverted, >= 1 SNOTEL site
with SWE (soil sensors preferred), contrasting geology and climate.

## 4. Variables

**Predictors (per basin x water year).** Snow-on date; soil moisture at Sep 1 and at snow-on
(percentile-standardized per basin); rain Sep 1 to snow-on; Sep-Oct baseflow; Apr 1 SWE and
peak SWE; Nov-Mar precipitation; winter and spring temperature; ONI/PDO.

**Responses.**
- Volume: Apr-Jul and Apr-Sep runoff; runoff efficiency (Q / effective water input).
- Timing: center-of-mass date, spring pulse onset, peak date and magnitude.
- Recession: recession constant k, Brutsaert-Nieber slope, days peak-to-50%, Richards-Baker.
- Baseflow: Aug-Sep mean and 7-day minimum, Eckhardt BFI, next-October flow.

**Conventions (from global rules).** Water year Oct 1 - Sep 30 via the single
`water_year_calculator.py` source of truth. Units in variable names (`flow_cfs`, `precip_mm`).
Distinguish instantaneous, daily mean, and peak flow. Verify time zones (USGS local vs UTC).
Gap-fill is explicit and logged, never automatic.

## 5. Data sources

| Need | Source |
|---|---|
| Streamflow | USGS NWIS, HCDN-2009, BPA modified flows (Phase 3), HYDAT (Canada) |
| Forcing | gridMET (primary), PRISM and Daymet as sensitivity checks |
| Snow | SNOTEL SWE/depth; UA/SWANN SWE; SNODAS (2003-) |
| Soil moisture, observed | SNOTEL soil sensors, SCAN, USCRN, AgriMet (mostly 2000s onward) |
| Soil moisture, long record | ERA5-Land (1950-), NLDAS-2 (1979-) |
| Cross-checks | SMAP (2015-), GRACE (2002-) |
| Groundwater | USGS NWIS well levels |
| Basin attributes | GAGES-II, gSSURGO (AWC, depth), geology |

**Storage strategy (machine has ~17 GB free).** Aggregate-only: remote subset via
zarr/THREDDS/OPeNDAP, write basin-level parquet only. No local gridded rasters, never
download the NWM retrospective. Budget ~5 GB for the project. Raw pulls are cached and never
overwritten; transforms write to new files. Data directories are git-ignored and rebuilt from
a manifest + fetch code on any machine.

## 6. Statistical plan

1. **Exploratory:** scatter, Spearman, partial correlations controlling for winter precip/SWE.
2. **Core model:** `log(Q_AprJul) ~ log(Apr1_SWE) + log(NovMar_P) + winter_T + SM_snowon
   (+ spring covariates)`; test the SM_snowon term; also analyze residuals of standard
   SWE-based forecast regressions against fall state.
3. **Hierarchical model** across basins: random intercepts/slopes by basin; cross-level
   interactions with soil depth, AWC, aridity, elevation (statsmodels MixedLM / PyMC / bambi).
4. **Threshold test:** GAM and piecewise (hinge) models compared by AIC and CV; quantile
   regression for the low-efficiency tail.
5. **Storage-deficit estimate:** cumulative water input vs cumulative runoff per year;
   x-intercept = retention deficit (mm); regress on SM_snowon.
6. **Matched analog years:** similar Apr 1 SWE and winter precip, contrasting fall state.
7. **Model-based attribution (later, likely on a larger machine):** Snow-17/SAC-SMA or VIC
   with swapped initial soil moisture and identical forcing; compare to empirical estimates.
8. **Validation:** chronological splits only, train/val/test ranges recorded; full metric
   suite (NSE, KGE, PBIAS, RMSE, MAE, R^2) for models with vs without fall predictors;
   interpolation and extrapolation reported separately; block bootstrap by year; BH-FDR across
   basins. Random seeds set and recorded.

**Known pitfalls to control or report:** collinearity (VIF; soil vs groundwater split);
warming-driven rain/snow phase shift (stratify by elevation band, include trend); frozen
ground; reanalysis soil-moisture bias (use percentiles); basal melt wetting soils under early
snow; point vs basin-scale SNOTEL representativeness; small n per basin.

## 7. Architecture (proposed)

```
src/fallsoil/
  config.py            # env/.env settings, paths
  water_year.py        # water_year_calculator (single source of truth)
  fetch/               # one module per source -> basin-level parquet + manifest
  qc/                  # validation, range checks, gap logging
  covariates/          # snow-on date, SM_snowon, deficit metrics
  responses/           # volume, timing, recession, baseflow metrics
  models/              # core regression, hierarchical, GAM, deficit method
  eval/                # metric suite, chronological CV, bootstrap
  app/                 # Plotly Dash UI; thin callbacks over the packages above
tests/                 # unit, integration, property, mutation, UI
docs/
data/                  # git-ignored: raw/, interim/, results/
```

Rules: callbacks stay lean; the app reads precomputed results (parquet), not live model fits;
config via env/.env; no secrets in the repo; reuse existing utilities rather than
reimplementing.

## 8. Testing plan

- Tests are written **before** implementation, by a separate agent invocation or by a human.
- Test files are a locked spec: implementation work never edits them; if that is needed it is
  flagged to the user.
- Suite: unit, integration, property-based (hypothesis), mutation (run by an independent
  evaluator), and UI tests for the Dash app. Synthetic-data tests with known injected effects
  verify the statistical methods recover the truth (and recover ~zero when none is injected).
- A failing test is reported with its cause; application code is not changed to satisfy it
  without user confirmation.
- Report "plausible" (passes tests) and "correct" (matches the spec) separately.

## 9. Milestones

0. Environment (Python 3.12 venv, pinned deps), basin screening and approval, data inventory.
1. Fetch layer + QC + manifest for streamflow, SNOTEL, gridMET (tests first).
2. Covariates and response metrics (tests first, synthetic fixtures).
3. Core regression, hierarchical, threshold, deficit methods on pilot basins.
4. Dash explorer over cached results.
5. Paper draft; Phase 2 expansion; Phase 3 mainstem with BPA modified flows.

## 10. Open decisions

- Approve or swap the pilot basins after Phase 0 screening.
- Public vs private repo, and when to push (`gh` is not authenticated on this machine).
- Whether and when to move attribution modeling to a larger machine.
