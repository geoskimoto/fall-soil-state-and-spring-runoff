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
