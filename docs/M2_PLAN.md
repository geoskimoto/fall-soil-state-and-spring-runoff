# Milestone 2 Plan: first data pull, response metrics, covariates, basin-year table

Status: DRAFT for review. Same process as Milestone 1: spec first, tests written by a separate agent from the spec,
tests locked, implementation, independent mutation evaluation. Statistical modelling is Milestone 3 and is not planned here.

## Goal
Produce one auditable table per pilot basin and water year (WY1982 onward, about 45 years) holding:
(a) response metrics from observed unregulated flow, (b) fall soil-state and snow-timing covariates, and (c) the control
covariates the literature review says we need (April weather, winter precipitation, snowpack). Every number traces to
manifest records; gaps are flagged, never filled.

## Split into three sub-milestones (so progress does not wait on accounts)
| Sub-milestone | Needs | Can start now? |
|---|---|---|
| M2a Response metrics from USGS flow | existing fetchers only | yes |
| M2b Forcing covariates from gridMET (+ PRISM monthly sensitivity) | small extension of the gridMET fetcher, new PRISM monthly fetcher | yes |
| M2c Snow and soil state (snow-on date, SWE, soil moisture, deficit) | a gridded soil-moisture/SWE source; see Decision 1 | after Decision 1 |
Then M2d: bulk pull for the six basins and assembly of the basin-year table.

## Conventions carried over
Water year via `water_year_calculator` only. Units in names (`vol_aprjul_mm`, `flow_mm_d`, `tmean_c`). USGS days are local
standard time, SNOTEL PST, gridMET midnight MST: all treated as the same calendar day (at most a 1-2 hour offset for
Pacific-time basins; stated in metadata, not corrected). A basin-year is NaN with a recorded reason when its inputs fail
completeness gates; thresholds below are parameters, not hard-coded, and are recorded in the output metadata.

## M2a. Response metrics (module `fallsoil.responses`)
Input: daily `flow_cfs` (from the USGS parquet), basin drainage area (new `Basin.drain_sqkm`, from GAGES-II DRAIN_SQKM,
tested to agree with the polygon area within 1%). Pure functions on a daily series; no I/O.

| Metric | Definition (default parameters) |
|---|---|
| `flow_mm_d` | cfs x 0.0283168 m3/ft3 x 86400 s / (area km2 x 1e6 m2) x 1000 mm/m |
| `vol_wy_mm`, `vol_aprjul_mm`, `vol_aprsep_mm`, `frac_aprjul` | sums of `flow_mm_d` over the window; `frac_aprjul = vol_aprjul / vol_wy` |
| `com_wy_day` | centre of mass of annual flow: day of WY (Oct 1 = 1) at which cumulative flow reaches 50% |
| `spring_pulse_day` | onset of the spring snowmelt pulse after Cayan et al. 2001: the day on which the accumulated departure of daily flow from the window's mean flow reaches its minimum (concept confirmed 2026-10-23 from a USGS-related NRCS report and a USGS fact sheet; the primary paper's exact window could not be opened, AMS returned 403). Our default window is Feb 1 - Jul 31 with the mean taken over that window; start dates Jan 1 and Mar 1 are sensitivity runs. This default is OURS, not a confirmed replication of the paper |
| `peak_mm_d`, `peak_wy_day` | maximum daily mean in Mar 1 - Jul 31 and its day of WY |
| `rb_flashiness` | Richards-Baker index, sum of absolute daily changes / sum of flow, over the WY |
| `recession_k_days` | linear-reservoir constant from ln(Q) vs time on qualifying falling-limb segments (>= 7 days of strictly decreasing flow after the peak, excluding the first 2 days, Jun 1 - Oct 31) |
| `bn_exponent_b`, `bn_a` | Brutsaert-Nieber fit of -dQ/dt = a Q^b to the lower envelope of the same segments (method documented; known to be sensitive per the literature review, so reported with a segment count and a fit-quality flag) |
| `baseflow_*` | Eckhardt filter (alpha 0.98, BFImax 0.80) AND Lyne-Hollick (alpha 0.925, 3 passes); both reported, plus `bfi_wy` for each; the spread between filters is the uncertainty |
| `q7min_mm_d`, `augsep_mean_mm_d`, `next_oct_mean_mm_d` | 7-day minimum flow in Aug 1 - Oct 31, Aug-Sep mean, and the following October mean |
| `days_peak_to_half` | days from the peak to 50% of the peak: kept as a labelled diagnostic only (the review found no literature support for it as a metric) |

Gates: a WY needs >= 350 valid daily values; a window metric needs >= 95% of that window's days present. Provisional
("P") values are kept; the output carries `frac_provisional` per WY so Milestone 3 can test sensitivity. No gap filling.

## M2b. Forcing covariates (module `fallsoil.covariates.forcing`)
Fetchers: extend `fetch.gridmet` with `srad` (W/m2) and `pet` (reference ET, mm) in the same area-weighted framework,
tests first; new `fetch.prism` for PRISM **monthly** 4 km precipitation and mean temperature, area-weighted the same way
(monthly is enough for a Sep-Nov totals sensitivity check and keeps downloads to about 540 small files; daily PRISM would be
about 16,000 files and is not needed). PRISM terms of use, rate limits and provisional-vs-stable status are recorded in the
module and metadata. The gridMET-vs-PRISM comparison is its own report, not a modelling step.

Covariates per basin-year (parameters in brackets, all sensitivity-tested later):
- `tmean_c` = (tmax + tmin) / 2 from the basin mean; rain/snow partition by `tmean_c` > [1.0 C]. Caveat recorded: a basin-mean
  temperature ignores the elevation range, so partition is coarse; elevation-banded versions are a Milestone 3 option.
- `rain_sep1_to_snowon_mm` (needs snow-on date, M2c), `precip_sepnov_mm`, `precip_novmar_mm`, `tmean_djf_c`.
- April weather controls (the confounder the review highlights for 2021): `tmax_aprjun_c`, `srad_aprjun_wm2`,
  `precip_aprjun_mm`, `pet_aprjul_mm`, and an April-heat index (number of days in April with tmax above the basin's
  1991-2020 April 90th percentile).

## M2c. Snow and soil state (module `fallsoil.covariates.state`): needs Decision 1
- Basin daily SWE and soil moisture (layers 0-7, 7-28, 28-100 cm and a 0-100 cm column) from the chosen source.
- `snow_on_date`: first day of the first run of >= [14] days with basin SWE >= [25] mm in the WY. Reported with
  alternative definitions ([7 d / 10 mm], [14 d / 25 mm], [21 d / 50 mm]) so Milestone 3 can test robustness. Basins where
  snow-on varies strongly with elevation are flagged.
- `sm_sep1_pct`, `sm_snowon_pct`: soil moisture expressed as a within-basin percentile of the same calendar date over
  1982-2020 (reanalysis soil moisture has known level biases; percentiles cancel them), and in mm of water with the
  absolute-bias caveat.
- `swe_apr1_mm`, `swe_peak_mm`, `swe_peak_date`.
- `deficit_snowon_mm`: field-capacity proxy (basin 95th percentile of the 0-100 cm column) minus the value at snow-on.
- Storage-deficit double-mass estimate: cumulative water input (melt + rain) vs cumulative runoff from snow-on; the
  x-intercept of a Theil-Sen line over a stated window is `retention_deficit_mm`, with R2 and a poor-linearity flag.
  (The method is our own reasoning: the literature review found no sourced precedent.)
- Validation: compare modelled soil-moisture percentiles with the short in-situ SNOTEL record where it exists
  (Moss Springs for the Minam since 2003; others 2010+); report agreement, do not tune to it.

## M2d. Bulk pull and assembly (module `fallsoil.assemble`, script `scripts/pull_pilot_data.py`)
- Idempotent, resumable bulk pull: USGS flow per basin; gridMET per basin, variable and water year (cached raw per chunk);
  PRISM monthly; M2c source. Re-pulling never overwrites raw; the last 60 days of gridMET are re-fetched on request.
- `basin_year.parquet` (one row per basin and WY) with a column dictionary, unit metadata, the parameters used, the
  manifest hashes of every input, and a coverage report (which basin-years are NaN and why).
- Acceptance: every row reproducible from raw files and the manifest alone; row counts and spot values checked against
  independent hand calculations for one basin and two water years.

## M2a results and pilot decisions (2026-10-24)
Implemented in `src/fallsoil/responses/` (commit 8ce097e); spec in `tests/INTERFACE_M2A.md`. Run on the cached USGS flow for the
six pilot basins, WY1982-2025. Testing policy for this project is relaxed for now (user decision, 2026-10-24): no new locked
suites or mutation evaluation while the research design iterates; existing locked tests stay green.
- **Usable years.** Lookout 44, Minam 44, Selway 44, Entiat 44, Wenatchee 36, SF McKenzie 31. The Wenatchee (no data 1979-10-11 to
  1989-09-30) and SF McKenzie (no data 1987-09-30 to 2000-09-30) gaps are in the USGS record itself and cannot be repaired.
- **Fall low-flow windows (kept).** For WY X: `q7min_mm_d` = Aug 1-Oct 31 of calendar year X, `augsep_mean_mm_d` = Aug-Sep,
  `next_oct_mean_mm_d` = October of year X (belongs to WY X+1). They describe persistence after the spring response. Antecedent
  fall dryness for WY X is the previous row's values (one-year lag, applied at assembly in M2d). The latest WY is NaN with a reason
  until its October exists.
- **Spring-pulse day.** Default Feb 1 start; Jan 1 and Mar 1 are sensitivity runs. Tested starts Jan 1 to Apr 1: it fails in the two
  maritime basins at every start (many years stuck at the Jul 31 window end; remaining values not tied to snowmelt), and is robust in
  the four snow-dominated basins (median shifts about 12 days between Jan 1 and Mar 1, less than the 12-25 day year-to-year
  spread). Decision: interpret `spring_pulse_day` only for transition and continental basins; use `com_wy_day` and `frac_aprjul`
  for maritime ones. It correlates 0.5-0.8 with `com_wy_day` where it works, so it is partly redundant.
- **Baseflow and recession.** Eckhardt and Lyne-Hollick are reported as a pair; their spread (about 0.2 in BFI) is the uncertainty.
  SF McKenzie's slow recession (k about 105 d, b about 3.9) is plausibly lava-fed groundwater but may be method-driven; the
  Brutsaert-Nieber envelope (10 log bins, per-bin minimum) is untested on noisy recession data.
- **Expansion beyond the pilot** (decision 2026-10-27: user chose option 1, SNOTEL is a flag, not a filter). Screen, counted by
  `scripts/phase1_count_candidates.py`: GAGES-II Ref, HUC02 17, area 50-2000 km2, Columbia basin (HUC4 1701-1709), >= 40 years
  of USGS daily flow. Funnel: 276 -> 199 -> 198 with a discharge series -> 113 in the Columbia basin -> **66 selected** (4 are
  existing pilots: Entiat, Minam, SF McKenzie, Lookout; 62 new). Wenatchee and Selway fail the screen as written (Selway is
  4959 km2). With the plan's original SNOTEL-inside rule only 24 would remain; 24 of the 66 have a SNOTEL station inside. Why
  dropped: SWE and soil moisture come from gridded products, SNOTEL only validates them.
  - Mix of the 66: OR 32, WA 15, ID 10, MT 4, WY 4, NV 1; HUC4 1709 (Willamette) alone has 19, so maritime and Cascade settings
    are over-represented against the continental pilot basins. Pooled models should carry a setting term.
  - **Record length is a proxy** (days with data / 365.25 from the USGS series catalog). Usable water years since 1982 will be
    lower (SF McKenzie: 56 by proxy, 30 usable). The real count needs the flow pull, after which the selected set is re-screened.
  - 49 of the 66 still report in 2025. GAGES-II reference status only covers data to 2009, so later regulation is unchecked.
  - Defaults chosen by the user (2026-10-27, "use your recommendations"): re-screen at >= 30 usable WYs since 1982; new basins
    labelled `unclassified` until a data rule assigns maritime / transition / continental; ERA5-Land for the new basins deferred
    until the set is final, then one request box per HUC4 group; the current ERA5 pull stays six-basin.
  - **Step 1 done** (`fallsoil.expansion`, `scripts/extract_expansion_basins.py`): 62 polygons extracted; polygon area equals
    GAGES-II DRAIN_SQKM for every gauge.
  - **Step 2 result** (`scripts/pull_expansion_flow.py`, `scripts/screen_expansion_flow.py`; USGS flow to 2025-09-30, 62 of 62
    pulled, no failures). Usable WY = response status "ok", WY1982-2025 (the pilot definition). **36 of 62 pass at >= 30**
    (>= 20: 41, >= 25: 37, >= 35: 32, >= 40: 28). Median usable years of the passers is 43. The proxy overstated the set because
    it counted pre-1982 years: 9 basins have zero usable years (records ended 1972-1978, or are gapped and resume only in 2026),
    and others stop in the 1990s-2000s. Passers by state: OR 13, WA 9, ID 6, WY 4, MT 3, NV 1; HUC4 1709 has 8. Area bands: 6
    at 50-200 km2, 18 at 200-500, 9 at 500-1000, 3 at 1000-2000. Analysis set if all pass on to forcing: 6 pilots + 36 = **42
    basins**, not 100+. Output: `data/interim/expansion_flow_screen.csv` (gitignored).
  - Not yet done: gridMET for the 36 passers (step 3), responses and first-look rerun (step 4). Reaching a sample near 100
    would need a wider region or non-reference gauges.

## M2c bulk-pull design: ERA5-Land soil moisture and SWE (2026-10-24; relaxed-testing policy applies)
Probe finding: CDS requests queue for many minutes even when tiny, so the pull is a resumable background job, not interactive.
- **Dataset:** `derived-era5-land-daily-statistics` (daily mean, 1-hourly frequency, time zone UTC-08:00 to match SNOTEL PST),
  variables `volumetric_soil_water_layer_1..4` and `snow_depth_water_equivalent`.
- **Request unit:** one request per calendar year (1981-2026) over ONE union bounding box of the six pilot basins (basin bounds
  plus one 0.1-degree cell, snapped to 0.1). About 46 requests instead of about 270, and the cached grids serve any basin inside
  the box. A later Columbia-wide expansion would add boxes, not redo these.
- **Cache and provenance:** raw NetCDF cached byte-identical under `data/raw/era5land/` (never overwritten), one manifest record
  per file. Resume = skip years whose cached file for the same box already exists.
- **Basin means:** computed offline from the cached grids by fractional-overlap area weighting on the 0.1-degree grid (Lookout,
  62 km2, falls on about one cell: treated as a negative control, per the risks above). Output `data/interim/era5land/`
  parquet per basin with `sm_l1..l4_m3m3`, `sm_0_100cm_m3m3` (ERA5-Land layers are 0-7, 7-28, 28-100 and 100-289 cm;
  the 0-100 cm column weights the first three) and `swe_mm`.
- **Deferred:** within-basin percentile transform, snow-on date, deficit proxy and the double-mass estimate stay as specified in
  M2c above, built on these parquet files after the pull.

## Test plan
- Written first by a separate agent from this document; locked with hashes; mutation-evaluated afterwards by an independent agent.
- Synthetic ground truth: hydrographs built from known parameters (a known peak day, a known CT, a linear reservoir with
  known k, a power-law recession with known b, a sine-plus-noise series with known flashiness) so each metric must recover
  the planted truth within a stated tolerance.
- Property tests: volume conservation across windows; CT within 1..366; metric invariance to a change of units when
  reported in the same units; snow-on monotone in the SWE threshold; area-weighting of PRISM equals the gridMET weights on
  identical grids.
- Failure handling for every new fetcher as in M1; no live network in the default run; opt-in live smoke tests.
- Regression: all existing 659 tests stay green; locked files are not edited without your approval.

## Decisions (resolved 2026-10-23: the user approved the recommendations for 1, 2 and 3)
Resolved as: ERA5-Land for soil moisture (primary), University of Arizona 4 km SWE for snow-on and April 1 SWE, NLDAS-2 later
as a second model; defaults as listed in item 2; bulk pull for all six basins, WY1982-2026. User action outstanding: create a
Copernicus CDS account (API key to `~/.cdsapirc`, mode 600) and a NASA Earthdata login; neither credential is ever stored in
the repo. M2a and M2b do not wait on this. The original options text follows for the record.

1. **Soil-moisture and gridded-SWE source (blocks M2c).** Options:
   - **A. ERA5-Land** via the Copernicus Climate Data Store: 1950 onward, hourly, 9 km, four soil layers plus snow water
     equivalent. Needs a free CDS account and API key in `~/.cdsapirc` (mode 600), created by you. Known wet bias
     (reviewed in the literature notes); 9 km is only about one cell for Lookout Creek.
   - **B. NLDAS-2 (Noah/VIC)** via NASA GES DISC: 1979 onward, 1/8 degree, soil layers and SWE. Needs a free Earthdata login.
   - **C. University of Arizona 4 km SWE** (NSIDC): best mountain SWE for snow-on and April 1 SWE; also needs Earthdata.
   - **D. Livneh-VIC** (NOAA PSL OPeNDAP, no login): 1/16 degree soil moisture and SWE, but the record ends before the
     present, so it can only cover part of the period.
   My recommendation: A for soil moisture (primary) with C for SWE and snow-on, B later as a second model for robustness
   and the initial-condition-swap experiments in Milestone 3. That means creating both a CDS and an Earthdata account.
   Without them, M2a, M2b and the observed-flow parts of M2d proceed anyway.
2. **Defaults** for snow-on (14 days at 25 mm), rain/snow threshold (1 C) and completeness gates (350 days; 95% of window).
   They are parameters with sensitivity runs, so the defaults only set the headline case.
3. **Bulk pull scope:** all six pilot basins and WY1982-2026 (about 44 years). Data stay small (aggregate-only), but the pull
   makes several hundred remote requests; I will pace them and cache every response.

## Risks
- Basin-mean temperature and 9 km soil moisture are coarse for the smallest basin (Lookout Creek, 62 km2): results there are
  treated as a negative control, not a precise estimate.
- ERA5-Land and gridMET are not independent of the same station networks; PRISM and NWRFC (deferred) checks help but do
  not make them independent.
- The Cayan spring-pulse window and the Brutsaert-Nieber segment rules are sensitive choices; both are parameterized and
  reported with diagnostics.
- A new defect found by the independent tests is reported to you before any code change, as in Milestone 1.

## First-look findings (2026-10-24, exploratory; scripts/pull_pilot_data.py, scripts/first_look.py)
Facts (measured on the real pilot data, WY1982-2025):
- **Usable water years per basin:** Entiat 44, Selway 44, Minam 44, Lookout 43, Wenatchee 36 (no gauge data 1979-10-11 to
  1989-09-30), SF McKenzie 30-31 (no gauge data 1987-09-30 to 2000-09-30). These are real USGS record gaps, not pipeline errors,
  and define the sample.
- **Spring-pulse day** fails in the two maritime basins at every window start tested (Jan 1, Feb 1, Mar 1, Apr 1): the
  minimum accumulated departure sits at the window end because there is no snowmelt step. Combined stuck-at-window-end
  years across all six basins: 25 of 75 (Jan 1), 23 (Feb 1), 31 (Mar 1), 61 (Apr 1). In the four snow-dominated basins it is
  robust (median shifts about 12 days from Jan 1 to Mar 1, less than the 12-25 day year-to-year spread) and correlates with
  centre of mass at about 0.5-0.8, so it is partly redundant. Centre of mass and Apr-Jul fraction are correlated at 0.85
  (Lookout), so they carry similar information. The two baseflow filters differ by about 0.2 in BFI.
- **SF McKenzie recession** is slow (k about 105 days, b about 3.9): consistent with lava-fed groundwater storage, but it may be
  method-driven; the locked tests do not cover noisy real recessions.
- **No provisional flow** in any WY1982-2025 year; provisional values are all in WY2026.
- **Fall precipitation signal (orientation only):** after controlling for Dec-Mar precipitation, Dec-Feb temperature and
  Apr-Jun weather, Sep-Nov precipitation has a clear positive effect on ln(Apr-Jul volume) in Entiat only (+0.107 per SD, 95% CI
  0.06-0.16, R2 0.66 to 0.79) and none in the maritime basins; Wenatchee, Selway and Minam lean positive but not significant.
  No snow or soil-moisture data yet, no out-of-sample test, no autocorrelation correction. NOT a result.

PROPOSED, NOT YET APPROVED by the user: keep Feb 1 as the default spring-pulse window with Jan 1 and Mar 1 as sensitivity runs;
treat spring-pulse day as interpretable only for the transition and continental basins and use centre of mass and Apr-Jul
fraction for the maritime ones; add a "no pulse detected" NaN when the minimum sits at a window edge (this changes a locked
spec and needs the user's approval before the lock is touched); keep both baseflow filters as a pair; flag SF McKenzie's
recession parameters as possibly method-driven.
