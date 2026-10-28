# Findings: fall soil state and spring runoff

Last updated 2026-10-28. Everything here is **exploratory**. The analysis has no snow or soil-moisture data yet, no out-of-sample
test and no correction for autocorrelation. Nothing below is a result; it is a record of what was measured, how, and what is
still unknown. Plans and methods are in `docs/`; project rules and how to run things are in `CLAUDE.md`.

## 1. Status at a glance

| Item | State |
|---|---|
| Question | Does the fall (Sep to Nov) soil / shallow-groundwater state change April to July runoff volume, timing and recession in Columbia basin headwaters? |
| Analysis set | **42 basins**: 6 hand-picked pilots + 36 USGS reference gauges that passed a flow screen |
| Period | Water years 1982 to 2025 (Oct 1 to Sep 30) |
| Response metrics (M2a) | Done for all 42 (`data/interim/responses_all.parquet`) |
| Forcing (gridMET precipitation and temperature) | Done for all 42 |
| First-look regression | Done for all 42 (`scripts/first_look_all.py`) |
| Snow and soil moisture (ERA5-Land) | **Not available.** Pull started 2026-10-24 on the old machine and produced no files in 4 days (see section 8). The deploy script restarts it on a new machine. |
| Climate setting for the 36 new basins | `unclassified`. A snow-share rule is proposed, not adopted (section 6). |
| Dashboard | Private artifact covering all 42 basins; not shared. |
| Tests | 891 pass (`pytest -m "not live"`); locked suites unchanged. Pass rate shows plausible, not correct (see `CLAUDE.md`). |

## 2. Headline observations (all exploratory)

1. **A wetter fall goes with more April to July runoff in snow-dominated basins, and not elsewhere.** Across the 42 basins the
   standardized coefficient for previous Sep to Nov precipitation is positive in 25. Splitting by the share of Nov to Mar
   precipitation that falls as snow (section 6): in the 20 basins above 0.6, **all 20 coefficients are positive** (median +0.080,
   13 nominal and 11 Benjamini-Hochberg hits); in the 22 basins at or below 0.6, 5 are positive (median -0.022) with 1 nominal hit,
   which is a basin in the rain-dominated group with a negative sign.
2. **Basin-level snow share and the coefficient track each other** (Pearson 0.82, Spearman 0.82 across 42 basins). This agrees with the
   hypothesis that the effect is weakest where soils rewet before snow arrives, but the bins and the descriptor were chosen after
   seeing the first-look table, and neighbouring basins share weather, so the correlation overstates independent evidence.
3. **Nominal hits are far above chance, BH hits are real in number but not independent.** 14 of 42 basins have p below 0.05 (about
   2.1 expected by chance); 11 have BH q below 0.05, all positive. Basins in the same range see the same storms, so the effective
   number of independent tests is smaller than 42.
4. **The effect is small in explained variance.** Median R² gain from adding fall precipitation is +0.013 across all basins and
   +0.048 in the snow-dominated group (+0.005 elsewhere). In the Entiat the R² goes from 0.66 to 0.79.
5. **The earlier pilot-only reading is superseded.** With six basins only the Entiat stood out (+0.107 per SD, 95% CI 0.057 to 0.158).
   The 36 added basins show it is a pattern, not a single-basin accident.

## 3. How the sample was built

Funnel from `scripts/phase1_count_candidates.py` (GAGES-II reference gauges, HUC02 17):

| Step | Basins |
|---|---|
| GAGES-II Ref gauges in HUC02 17 | 276 |
| Drainage area 50 to 2000 km² | 199 |
| With a USGS daily discharge series | 198 |
| In the Columbia basin (HUC4 1701 to 1709) | 113 |
| At least 40 years of flow, by proxy (days with data / 365.25) | **66** (4 are pilots: Entiat, Minam, SF McKenzie, Lookout; 62 new) |
| Polygons extracted, area equal to GAGES-II DRAIN_SQKM for all | 62 |
| Flow pulled to 2025-10-31, no failures | 62 of 62 |
| At least 30 usable water years since 1982 | **36 pass** (at least 20: 41; 25: 38; 35: 32; 40: 28) |
| Analysis set | 6 pilots + 36 = **42** |

- A water year is **usable** when its response status is `ok` for WY1982 to 2025. This is the pilot definition and also the screen
  definition. The proxy overstated the set because it counted pre-1982 years.
- Passers by state: OR 13, WA 9, ID 6, WY 4, MT 3, NV 1. HUC4 1709 (Willamette) contributes 8. Area bands: 6 at 50 to 200 km², 18 at
  200 to 500, 9 at 500 to 1000, 3 at 1000 to 2000.
- **Selection issues, not resolved:**
  - Wenatchee and Selway fail the screen as written. Selway is 4959 km², over the area cap. The Wenatchee failure was never
    diagnosed (its record has a 10-year gap, 1979-10-11 to 1989-09-30). Both stay in as pilots.
  - The 36 passers are weighted toward Oregon and the Cascades, so rain-dominated and transition basins are over-represented
    against the continental pilots. Pooled models need a setting term.
  - GAGES-II reference status only covers data through 2009. Later regulation or diversions are unchecked.
  - SNOTEL was demoted from a filter to a flag (user decision 2026-10-27): SWE and soil moisture will come from gridded products;
    SNOTEL only validates them. 24 of the 66 candidates have a SNOTEL station inside.
- Reaching about 100 basins would need a wider region (for example the whole Pacific Northwest or the Rockies) or non-reference gauges.

## 4. Data and provenance

| Source | What | Period | Where (gitignored) |
|---|---|---|---|
| USGS NWIS daily values | Discharge (cfs, daily mean) for 6 pilots + 62 expansion gauges | full record to 2025-10-31 (pilots to 2026-10-23) | `data/interim/usgs/` |
| gridMET (THREDDS, area-weighted basin means) | Precipitation (mm), tmmx and tmmn (°C) | 1981-10-01 to 2025-09-30 | `data/interim/gridmet/` |
| GAGES-II | Basin polygons, attributes, reference status | static | `data/raw/gagesii/`, `data/raw/basins/*.gpkg` |
| ERA5-Land daily statistics (CDS) | Soil moisture layers 1 to 3, snow water equivalent | not yet available | `data/raw/era5land/` |

- Every fetch is recorded in `data/manifest.jsonl` (source, URL, parameters, retrieval time, SHA-256, rows, code version). Raw files
  are never overwritten; a re-pull writes a new file and the scripts take the latest-sorting one.
- Time conventions: USGS days are local standard time, SNOTEL PST, gridMET midnight MST. All are treated as the same calendar day
  (an offset of at most 1 to 2 hours for Pacific-time basins; recorded in metadata, not corrected).
- Units: discharge converted to mm/day over the GAGES-II drainage area; volumes in mm; precipitation sums in mm; temperatures in °C.

## 5. Response metrics (M2a)

1,846 basin-years (42 basins × WY1982 to 2025): **1,735 ok, 19 partial, 92 nan**. Usable years: Entiat 44, Selway 44, Minam 44,
Lookout 43, Wenatchee 36, SF McKenzie 30 (29 in the regression after dropping a year without fall precipitation); the 36 passers
have a median of 44 and a minimum of 30. The 6 remaining `q7min` / `next_oct_mean` flags are real record gaps.

- **Spring-pulse day** (Cayan et al. 2001 concept; our window, not a confirmed replication) fails in the two maritime pilots at every
  start tested (Jan 1, Feb 1, Mar 1, Apr 1): the minimum accumulated departure sits at the window end because there is no
  snowmelt step. Stuck-at-window-end years across the six pilots: 25 of 75 (Jan 1), 23 (Feb 1), 31 (Mar 1), 61 (Apr 1). In the
  four snow-dominated pilots it is robust (median shifts about 12 days between Jan 1 and Mar 1, less than the 12 to 25 day
  year-to-year spread) and correlates 0.5 to 0.8 with centre of mass, so it is partly redundant. Interpret it only for transition
  and continental basins; use `com_wy_day` and `frac_aprjul` for maritime ones. (Proposed, not formally approved.)
- **Centre of mass and Apr to Jul fraction** correlate 0.85 in Lookout, so they carry similar information.
- **Baseflow:** Eckhardt and Lyne-Hollick differ by about 0.2 in BFI; the pair is reported and the spread is the method uncertainty.
- **SF McKenzie recession** is slow (k about 105 days, b about 3.9): consistent with lava-fed groundwater, but possibly
  method-driven. The Brutsaert-Nieber envelope (10 log bins, per-bin minimum) is untested on noisy recessions.
- **No provisional flow** in WY1982 to 2025 for the pilots; provisional values are all in WY2026.
- **Fall low-flow windows:** for WY X, `q7min_mm_d` is Aug 1 to Oct 31 of calendar year X, `next_oct_mean_mm_d` is October of year X
  (it belongs to WY X+1). Antecedent fall dryness for WY X is the previous row's value; the lag is applied at assembly.

## 6. Fall-precipitation signal and snow share (exploratory)

**Model**, fitted per basin (`scripts/first_look_all.py`): `ln(vol_aprjul_mm)` on `ln(Dec to Mar precipitation)`, Dec to Feb mean
temperature, Apr to Jun mean maximum temperature, Apr to Jun precipitation and the **previous Sep to Nov precipitation**
(requires at least 85 of 91 days of data). Predictors standardized (coefficient per 1 SD); HC3 robust errors; Benjamini-Hochberg q
across the 42 basins. A fit needs at least 15 usable years; all 42 have 29 to 43. The "base" model omits the fall term (R² base).

**Snow share** (`scripts/snow_share.py`, `data/interim/snow_share.csv`): the share of Nov to Mar precipitation that falls on days with basin
mean temperature below 0 °C, from gridMET. It uses no runoff, so it does not leak into the test. Basin mean temperature ignores the
elevation range, so the share is coarse.

| Snow share bin | Basins | Positive coefficients | BH q<0.05 | Median coefficient |
|---|---|---|---|---|
| 0.0 to 0.25 (rain-dominated) | 11 | 1 | 0 | -0.027 |
| 0.25 to 0.6 (mixed) | 11 | 4 | 0 | -0.008 |
| above 0.6 (snow-dominated) | 20 | 20 | 11 | +0.080 |

Per-basin results, sorted by coefficient (flag = BH q<0.05, or nominal p<0.05 only):

| Gauge | USGS ID | Setting | Area km² | n | Snow share Nov–Mar | Coef (per SD) | 95% CI | p | BH q | Flag |
|---|---|---|---|---|---|---|---|---|---|---|
| Bruneau River At Rowland Nv | 13161500 | unclassified | 986 | 43 | 0.66 | +0.144 | +0.016 to +0.272 | 0.0276 | 0.0892 | nominal |
| Andrews Creek Near Mazama, Wa | 12447390 | unclassified | 58 | 43 | 0.90 | +0.112 | +0.047 to +0.176 | 0.0007 | 0.0042 | BH |
| Entiat River near Ardenvoir, WA | 12452800 | transition | 526 | 43 | 0.75 | +0.107 | +0.057 to +0.158 | 0.0000 | 0.0000 | BH |
| Pacific Creek At Moran Wy | 13011500 | unclassified | 404 | 43 | 0.93 | +0.104 | +0.056 to +0.152 | 0.0000 | 0.0000 | BH |
| Johnson Creek At Yellow Pine Id | 13313000 | unclassified | 562 | 43 | 0.85 | +0.093 | +0.033 to +0.152 | 0.0022 | 0.0103 | BH |
| Sf Payette River At Lowman Id | 13235000 | unclassified | 1163 | 43 | 0.75 | +0.093 | +0.059 to +0.128 | 0.0000 | 0.0000 | BH |
| Greys River Ab Reservoir Nr Alpine Wy | 13023000 | unclassified | 1162 | 43 | 0.91 | +0.087 | +0.041 to +0.132 | 0.0002 | 0.0017 | BH |
| Middle Fork Rock Cr nr Philipsburg MT | 12332000 | unclassified | 315 | 42 | 0.86 | +0.084 | +0.032 to +0.137 | 0.0017 | 0.0089 | BH |
| Buffalo Fork Ab Lava Creek Nr Moran Wy | 13011900 | unclassified | 852 | 41 | 0.96 | +0.083 | +0.058 to +0.109 | 0.0000 | 0.0000 | BH |
| Sf Salmon River Nr Krassel Ranger Station Id | 13310700 | unclassified | 853 | 36 | 0.79 | +0.080 | +0.023 to +0.136 | 0.0054 | 0.0221 | BH |
| Snake River Ab Jackson Lake At Flagg Ranch Wy | 13010065 | unclassified | 1222 | 42 | 0.94 | +0.080 | +0.023 to +0.138 | 0.0058 | 0.0221 | BH |
| Stehekin River At Stehekin, Wa | 12451000 | unclassified | 831 | 41 | 0.73 | +0.062 | +0.029 to +0.095 | 0.0003 | 0.0021 | BH |
| Prospect Creek at Thompson Falls MT | 12390700 | unclassified | 470 | 43 | 0.53 | +0.055 | -0.021 to +0.132 | 0.1553 | 0.3261 |  |
| Klickitat River Above West Fork Near Glenwood, Wa | 14107000 | unclassified | 394 | 34 | 0.59 | +0.053 | -0.016 to +0.121 | 0.1306 | 0.3227 |  |
| South Fork Jocko River near Arlee MT | 12381400 | unclassified | 151 | 43 | 0.77 | +0.043 | -0.020 to +0.105 | 0.1812 | 0.3624 |  |
| Lake Fork Payette River Ab Jumbo Cr Nr Mccall Id | 13240000 | unclassified | 126 | 34 | 0.87 | +0.042 | +0.006 to +0.078 | 0.0221 | 0.0774 | nominal |
| Mckenzie River At Outlet Of Clear Lake, Or | 14158500 | unclassified | 237 | 43 | 0.35 | +0.040 | -0.010 to +0.090 | 0.1129 | 0.2964 |  |
| Chiwawa River Near Plain, Wa | 12456500 | unclassified | 446 | 34 | 0.66 | +0.036 | -0.059 to +0.130 | 0.4577 | 0.5900 |  |
| Long Tom River Near Noti, Oreg. | 14166500 | unclassified | 226 | 42 | 0.01 | +0.030 | -0.042 to +0.103 | 0.4107 | 0.5842 |  |
| Minam River at Minam, OR | 13331500 | continental | 619 | 43 | 0.70 | +0.029 | -0.010 to +0.068 | 0.1498 | 0.3261 |  |
| Wenatchee River at Plain, WA | 12457000 | transition | 1546 | 36 | 0.61 | +0.029 | -0.028 to +0.085 | 0.3201 | 0.5378 |  |
| American River Near Nile, Wa | 12488500 | unclassified | 205 | 43 | 0.64 | +0.028 | -0.044 to +0.100 | 0.4538 | 0.5900 |  |
| Selway River near Lowell, ID | 13336500 | continental | 4960 | 43 | 0.70 | +0.023 | -0.016 to +0.062 | 0.2405 | 0.4392 |  |
| Icicle Creek Abv Snow Cr Nr Leavenworth, Wash. | 12458000 | unclassified | 499 | 32 | 0.73 | +0.020 | -0.084 to +0.124 | 0.7019 | 0.7559 |  |
| Nf Coeur D Alene R Ab Shoshone Ck Nr Prichard Id | 12411000 | unclassified | 868 | 43 | 0.46 | +0.006 | -0.088 to +0.100 | 0.8975 | 0.8975 |  |
| Shitike Cr, At Peters Pasture, Nr Warm Springs, Or | 14092750 | unclassified | 58 | 36 | 0.51 | -0.006 | -0.093 to +0.080 | 0.8886 | 0.8975 |  |
| South Fork McKenzie River above Cougar Lake, OR | 14159200 | maritime | 414 | 29 | 0.30 | -0.008 | -0.080 to +0.065 | 0.8390 | 0.8810 |  |
| Lookout Creek near Blue River, OR | 14161500 | maritime | 62 | 42 | 0.12 | -0.014 | -0.086 to +0.058 | 0.7010 | 0.7559 |  |
| Muddy Creek Below Clear Creek Near Cougar, Wa | 14216500 | unclassified | 350 | 42 | 0.23 | -0.015 | -0.090 to +0.060 | 0.6914 | 0.7559 |  |
| Sandy River Near Marmot, Or | 14137000 | unclassified | 674 | 43 | 0.26 | -0.020 | -0.074 to +0.034 | 0.4636 | 0.5900 |  |
| Breitenbush R Abv French Cr Nr Detroit, Or. | 14179000 | unclassified | 272 | 31 | 0.28 | -0.021 | -0.118 to +0.077 | 0.6808 | 0.7559 |  |
| Row River Above Pitcher Creek Near, Dorena, Oreg | 14154500 | unclassified | 547 | 43 | 0.07 | -0.022 | -0.103 to +0.058 | 0.5850 | 0.7020 |  |
| Quartzville Creek Near Cascadia, Oreg. | 14185900 | unclassified | 258 | 43 | 0.11 | -0.023 | -0.095 to +0.049 | 0.5305 | 0.6553 |  |
| South Santiam River Below Cascadia, Or | 14185000 | unclassified | 458 | 43 | 0.10 | -0.027 | -0.085 to +0.031 | 0.3630 | 0.5445 |  |
| Wiley Creek Near Foster, Or | 14187000 | unclassified | 135 | 37 | 0.03 | -0.032 | -0.108 to +0.045 | 0.4173 | 0.5842 |  |
| Little North Santiam River Near Mehama, Or | 14182500 | unclassified | 287 | 43 | 0.09 | -0.038 | -0.099 to +0.024 | 0.2299 | 0.4389 |  |
| Umatilla River Above Meacham Creek, Nr Gibbon, Or | 14020000 | unclassified | 341 | 42 | 0.41 | -0.038 | -0.116 to +0.041 | 0.3470 | 0.5398 |  |
| Lolo Creek Nr Greer Id | 13339500 | unclassified | 626 | 43 | 0.39 | -0.041 | -0.117 to +0.035 | 0.2869 | 0.5021 |  |
| East Fork Lewis River Near Heisson, Wa | 14222500 | unclassified | 324 | 43 | 0.06 | -0.048 | -0.113 to +0.017 | 0.1486 | 0.3261 |  |
| Little Sandy River Near Bull Run, Oreg. | 14141500 | unclassified | 60 | 43 | 0.08 | -0.049 | -0.097 to -0.002 | 0.0419 | 0.1257 | nominal |
| Meacham Creek At Gibbon, Or | 14020300 | unclassified | 456 | 41 | 0.40 | -0.061 | -0.187 to +0.065 | 0.3405 | 0.5398 |  |
| Tilton River Ab Bear Canyon Creek Near Cinebar, Wa | 14236200 | unclassified | 361 | 43 | 0.07 | -0.069 | -0.146 to +0.009 | 0.0819 | 0.2293 |  |

Reading the table: among the 22 basins with snow share at or below 0.6 there is one nominal hit, Little Sandy River (share 0.08),
with a **negative** sign and q above 0.05, which is what one chance hit among 22 basins looks like. Two further nominal-only hits
(Bruneau River NV and Lake Fork Payette ID) sit in the snow-dominated group with q above 0.05. The other 11 nominal hits there
are the BH hits.

**Pilot labels do not follow snow share.** The six pilots were labelled by region, not by a rule:

| Pilot | Label | Snow share | Coefficient | BH q |
|---|---|---|---|---|
| Lookout Creek | maritime | 0.12 | -0.014 | no (q 0.76) |
| SF McKenzie | maritime | 0.30 | -0.008 | no (q 0.88) |
| Wenatchee | transition | 0.61 | +0.029 | no (q 0.54) |
| Entiat | transition | 0.75 | +0.107 | yes (q<0.0001) |
| Minam | continental | 0.70 | +0.029 | no (q 0.33) |
| Selway | continental | 0.70 | +0.023 | no (q 0.44) |

Entiat (transition) has a higher snow share than Minam and Selway (continental), so a snow-share rule cannot reproduce the pilot
labels. **Decision pending (user):** (1) label the 36 new basins by snow-share thresholds and leave pilot labels as they are
(my recommendation; for example below 0.25 maritime, 0.25 to 0.6 transition, above 0.6 continental); (2) relabel the pilots with
the same rule (changes existing metadata; needs approval); or (3) skip bins and report against the continuous snow share.

**Interpretation limits:**
- The model has no snow or soil moisture. The storage-deficit variable the hypothesis names is not measured, only its precipitation
  proxy, and fall precipitation also correlates with the wetness of the whole year.
- Neighbouring basins are spatially correlated and share storms. The snow-share correlation and the number of BH hits therefore
  overstate independent evidence. A cluster-aware or hierarchical model is an M3 task.
- Residuals are autocorrelated in some basins; HC3 does not correct for that.
- The three bins and the 0.6 threshold were chosen after looking at the table. They are descriptive, not a pre-registered test.
- Strongest basins (coefficient, p): Entiat +0.107 (p<0.0001), Pacific Creek WY, Buffalo Fork WY, SF Payette ID, Greys River WY, Stehekin WA,
  Andrews Creek WA, Middle Fork Rock Creek MT; the table lists all 42.

## 7. Data-quality log

| Date | Issue | Effect | Handling |
|---|---|---|---|
| 2026-10-24 | Wenatchee: no USGS data 1979-10-11 to 1989-09-30 | 36 usable WYs | real record gap; flagged, not filled |
| 2026-10-24 | SF McKenzie: no USGS data 1987-09-30 to 2000-09-30 | 30 usable WYs | real record gap; flagged, not filled |
| 2026-10-27 | First expansion flow pull ended 2025-09-30 | every expansion WY2025 row partial (no October for q7min, next_oct_mean) | fixed `END` to 2025-10-31, all 62 re-pulled and metrics re-run; old files kept |
| 2026-10-27 | 9 of 62 expansion gauges have zero usable years (records end 1972 to 1978, or gap and resume in 2026) | excluded by the screen | recorded in `expansion_flow_screen.csv` |
| 2026-10-26/27 | gridMET THREDDS server timed out on most requests (76 failures per pass, then 0 files added) | pull stalled for about a day | script is resumable; server recovered 2026-10-28 and one pass finished; cause not determined (outage or throttling) |
| 2026-10-28 | `pr` has 43 files for 42 basins | pilot files also exist with later end dates (to 2026-10-22/23); the loader takes the latest-sorting file and the analysis filters to WY1982 to 2025 | no effect on results |
| 2026-10-28 | First ERA5-Land pull on the new machine: 0 of 180 quarters. 177 refused (CDS Terms of use rev. 13 not accepted); 3 (1981 Q2 to Q4) rejected because CDS now zips one NetCDF per variable and the fetcher expected one file | no ERA5 data yet | owner accepted the terms; fetcher now caches the zip byte-identical (`.zip`) and merges members when reading; pull restarted. Probe download kept in `data/raw/era5land/probe/` |
| ongoing | Record length in the candidate screen is a proxy (days / 365.25) | overstated usable years | re-screened on real usable WYs |

## 8. Operational findings

- **ERA5-Land pull (started 2026-10-24, old machine, PID 3999426):** after about 4 days it had no completed `.nc` file, about 3 minutes of
  CPU time, and most of its sockets to the CDS server in `CLOSE-WAIT`. It was not failing on disk space (a write error would have
  stopped it); it was hung waiting on CDS. It was never logged, so job states could not be read. Likely causes: a CDS queue that
  never started the requests, plus the per-account queued-request limit (the script waits 120 s up to 60 times on "Maximum number
  queued"). Not confirmed.
- **Request unit is a quarter, not a year.** `docs/M2_PLAN.md` said one request per year; the fetcher requests one calendar quarter
  because the CDS cost limit (about 400 fields = variables × days) allows 4 variables × 3 months per request. 45 years × 4 = 180
  requests for WY1981 to 2025. Layer 4 (100 to 289 cm) is omitted for the same reason; the study uses the 0 to 100 cm column.
- **Disk:** the old machine's root volume is 193 GB, 99% full (2.9 GB free). The project is 2.2 GB (`data/raw` 1.4 GB, of which
  GAGES-II is 721 MB and gridMET 548 MB; `data/interim` 71 MB). `/home/geoskimoto` is 55 GB; readable directories account for about
  84 GB of the 190 GB used, so about 106 GB sits in places that need root to read (suspected: the Streamflow DataOps PostgreSQL database).
  The project is being moved to a machine with more space; `new_deploy.sh` does that setup.
- The ERA5 area is the union box of the six pilot basins only (`union_area()`). The 36 expansion basins would need one box per HUC4
  group; deferred until the set is final (it now is) and the six-basin pull has worked.

## 9. Open questions and next steps

1. **Classify the 36 unclassified basins** (section 6 options). Needs a decision before any setting-stratified analysis.
2. **ERA5-Land soil moisture and SWE:** restart on the new machine (`./new_deploy.sh`); then compute basin means, snow-on date,
   soil moisture at snow-on and the deficit proxy (M2c), and add them to the regression. This is the real test of the hypothesis.
3. **Extend ERA5 to the 36 expansion basins** after the six-basin pull works (one box per HUC4 group).
4. **Repeat the regression with the snow-share interaction** (`coef ~ snow_share`) in a pooled, cluster-aware model instead of 42
   independent fits (M3).
5. Diagnose why Wenatchee fails the screen; decide whether Selway (4959 km²) stays as a larger-basin pilot.
6. Decide whether to widen the region to reach about 100 basins.
7. Confirm or reject the proposals marked "proposed, not approved" in `docs/M2_PLAN.md` (spring-pulse "no pulse" NaN would change a
   locked spec and needs approval before the lock is touched).
8. Update the dashboard when settings or ERA5 results change.

## 10. Reproduce

With the repo, `.venv` and the data directory in place (the order is the dependency order):

```
python scripts/pull_expansion_flow.py            # USGS flow, 62 expansion gauges, to 2025-10-31 (resumable)
python scripts/screen_expansion_flow.py          # >= 30 usable WYs -> data/interim/expansion_flow_screen.csv
python scripts/pull_expansion_forcing.py 2.0     # gridMET pr/tmmx/tmmn for the 36 passers (resumable)
python scripts/compute_responses_all.py          # response metrics -> responses_all.parquet
python scripts/first_look_all.py                 # 42-basin regression -> fall_signal_all.csv, first_look_all.parquet
python scripts/snow_share.py                     # snow-share descriptor -> snow_share.csv
python scripts/pull_era5land.py 1981 2025 2      # ERA5-Land (CDS key in ~/.cdsapirc); resumable, 180 requests
```

The pilot versions (`pull_pilot_data.py`, `first_look.py`) and the screening scripts (`phase0_*`, `phase1_count_candidates.py`,
`extract_*_basins.py`) are documented in `CLAUDE.md`. Candidate screening needs `data/raw/gagesii/`.
