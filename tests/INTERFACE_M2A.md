# fallsoil Milestone 2a (response metrics) — public interface (defined by the tests)

The tests `tests/test_m2a_*.py` (with the builders in `tests/m2_builders.py`) are the locked specification;
this file is the unambiguous target for the implementer. Package: `src/fallsoil/responses/` (a package or a
module; every public name below must be importable from `fallsoil.responses`). Python 3.12, pandas/numpy only.
Docs: `docs/M2_PLAN.md` section M2a. Errors come from `fallsoil.errors`. Water years come from
`fallsoil.water_year_calculator` ONLY (Oct 1 - Sep 30, labelled by the ending year; never `.year`).

## Conventions
- Daily series: `pd.Series` named `flow_mm_d`, `DatetimeIndex` of naive calendar days. Gaps may be absent rows or NaN.
- **Valid day** = finite and `>= 0`. NaN, +-inf and negative values are INVALID: they are excluded from every
  calculation, counted as missing for the completeness gates, and NEVER clipped to zero or replaced. Zero flow is valid.
  (`qc.run_qc` flags negatives upstream; the metrics layer only excludes them.) No gap filling anywhere.
- Day-of-water-year values (`com_wy_day`, `spring_pulse_day`, `peak_wy_day`) are integers stored as float (NaN-able)
  with Oct 1 = 1 and Feb 29 counted (leap WY: 1..366), identical to `water_year_calculator.wy_day`.
- A **window** is a calendar-date range inside calendar year `wy` (Jan-Sep dates belong to WY `wy`).
  Windows that run into October (below) use October of calendar year `wy`, which belongs to WY `wy`+1; they read
  the series beyond the WY and are NaN with a reason when that data is absent.
- **Completeness gates** (both are `ResponseParams`, defaults 350 and 0.95):
  - WY gate: `n_valid_days_wy >= min_wy_valid_days` (350). Failing it makes EVERY metric NaN, `status="nan"`.
  - Window gate: `n_valid_in_window >= min_window_fraction * n_calendar_days_in_window` (e.g. 116 of 122 days for
    Apr 1-Jul 31 passes, 115 fails). A failing window makes the metrics that use it NaN (the others are unaffected).
- Sums run over valid days only (nothing is scaled up for missing days).

## Unit conversion
```
M3_PER_FT3 = 0.0283168
cfs_to_mm_per_day(flow_cfs, area_sqkm)   # scalar | ndarray | Series (NaN-propagating, Series keeps index) 
mm_per_day_to_cfs(flow_mm_d, area_sqkm)  # exact inverse
```
`mm/d = cfs * M3_PER_FT3 * 86400 / (area_sqkm * 1e6) * 1000`. `area_sqkm` must be a finite number `> 0`, else `ValueError`
(zero, negative, NaN, inf). 1 cfs over 1 km2 = 2.446572 mm/d.

## fallsoil.basins addition
`Basin.drain_sqkm -> float` (property; GAGES-II DRAIN_SQKM, no file access): 14161500 Lookout 62.4241;
14159200 SF McKenzie 414.2997; 12452800 Entiat 526.3533; 12457000 Wenatchee 1545.47; 13336500 Selway 4959.467;
13331500 Minam 618.9291. Must agree with the polygon area (equal-area projection) within 1%.

## ResponseParams
Frozen dataclass; every field has the default shown; all thresholds are parameters, never hard-coded.
```
min_wy_valid_days: int = 350
min_window_fraction: float = 0.95
spring_pulse_start: tuple[int,int] = (2, 1)      # (month, day) window start; end is fixed Jul 31
eckhardt_alpha: float = 0.98
eckhardt_bfi_max: float = 0.80
lh_alpha: float = 0.925
lh_passes: int = 3
recession_min_days: int = 7                      # strictly decreasing steps for a falling limb to qualify
recession_skip_days: int = 2
bn_min_points: int = 10
bn_min_r2: float = 0.8
as_dict() -> dict    # JSON-serialisable (tuples become lists); written to metadata
```
Validation at construction (`ValueError`): `min_wy_valid_days` not in 1..366, `min_window_fraction` not in (0, 1],
any alpha not in (0, 1), `eckhardt_bfi_max` not in (0, 1], `lh_passes < 1`, `spring_pulse_start` not a valid month/day.

## Baseflow filters (pure; 1-D array-like of floats in, `np.ndarray` float64 of the same length out)
```
eckhardt_baseflow(flow, alpha=0.98, bfi_max=0.80)
lyne_hollick_baseflow(flow, alpha=0.925, passes=3)
```
Input must be non-empty, finite, `>= 0` (else `ValueError`; a NaN is never skipped). `alpha` outside (0, 1), `bfi_max` outside (0, 1] or `passes < 1` also raise `ValueError`. Output satisfies
`0 <= baseflow <= flow` elementwise and is positively homogeneous (`f(c*q) = c*f(q)`).
- Eckhardt: `b_t = ((1-bfi_max)*alpha*b_{t-1} + (1-alpha)*bfi_max*q_t) / (1 - alpha*bfi_max)`, `b_t = min(b_t, q_t)`,
  `b_0 = bfi_max*q_0`. For constant flow the steady state is `bfi_max*q` (BFI -> 0.80, NOT 1).
- Lyne-Hollick: quickflow `f_t = alpha*f_{t-1} + (1+alpha)/2*(q_t - q_{t-1})`, `f_t >= 0` (and `<= q_t`), baseflow `q - f`;
  passes alternate forward, backward, forward (`passes` total), each pass filtering the previous pass's baseflow;
  `f_0 = 0`. For constant flow baseflow equals flow (BFI = 1).

## Per-water-year metrics
```
wy_metrics(flow_mm_d: pd.Series, wy: int, params: ResponseParams | None = None) -> dict
```
`flow_mm_d` may span many years; only the rows named by the windows are used (data of other years is ignored, apart
from October of year `wy` where stated). Raises `DataValidationError` for a non-`DatetimeIndex`, an empty series, a
non-numeric dtype, or duplicate index values. A `wy` with no rows in a non-empty series gives the all-NaN result.
Returned dict keys = `TABLE_COLUMNS` minus `frac_provisional` (module constant, see below).

| key | definition (windows are calendar-date ranges of year `wy` unless noted) | gate |
|---|---|---|
| `wy` | the argument | |
| `n_days_wy`, `n_valid_days_wy`, `n_negative_days_wy` | calendar days in the WY (365/366); valid days; finite days `< 0` | |
| `vol_wy_mm` | sum of valid `flow_mm_d` over the WY | WY |
| `vol_aprjul_mm` / `vol_aprsep_mm` | sums over Apr 1-Jul 31 / Apr 1-Sep 30 | WY + window |
| `frac_aprjul` | `vol_aprjul_mm / vol_wy_mm`; NaN if `vol_wy_mm == 0` (reason recorded) | WY + Apr-Jul |
| `com_wy_day` | first WY day on which cumulative flow `>= 50%` of `vol_wy_mm`; NaN if total is 0 | WY |
| `spring_pulse_day` | window = `spring_pulse_start` .. Jul 31; `D_t = sum_{i<=t}(Q_i - mean)` inclusive, mean over the window's valid days, missing days add 0; result = WY day of the first valid day at which `D` is minimal. A step up from low to high flow on day S gives S-1 (last low day) | WY + window |
| `peak_mm_d`, `peak_wy_day` | maximum valid daily flow in Mar 1-Jul 31 and its WY day (first on ties) | WY + window |
| `rb_flashiness` | Richards-Baker: `sum|Q_t - Q_{t-1}| / sum Q_t` over days `t` whose predecessor is the previous calendar day and valid; whole WY; NaN if the denominator is 0 | WY |
| `recession_k_days`, `recession_n_segments` | see Recession below | WY |
| `bn_exponent_b`, `bn_a`, `bn_n_points`, `bn_r2`, `bn_fit_ok` | see Recession below | WY |
| `baseflow_eck_wy_mm`, `baseflow_lh_wy_mm` | sum over the WY's valid days of the Eckhardt / Lyne-Hollick baseflow; each maximal run of consecutive valid days is filtered separately (initialised per run); only days of the WY are used | WY |
| `bfi_wy_eck`, `bfi_wy_lh` | corresponding baseflow volume / `vol_wy_mm`, in [0, 1]; NaN if `vol_wy_mm == 0` | WY |
| `q7min_mm_d` | minimum 7-day mean over runs of 7 consecutive valid days wholly inside **Aug 1-Oct 31 of year `wy`** | WY + that 92-day window |
| `augsep_mean_mm_d` | mean of valid flow over Aug 1-Sep 30 | WY + that 61-day window |
| `next_oct_mean_mm_d` | mean of valid flow over Oct 1-31 of year `wy` (the October FOLLOWING the water year) | WY + that 31-day window |
| `days_peak_to_half` | DIAGNOSTIC ONLY: calendar days from `peak_wy_day` to the first later day inside the WY with a valid `Q <= 0.5*peak_mm_d`; NaN (reason) if none | as peak |
| `status` | `"ok"` (no NaN anywhere), `"partial"` (WY gate passed, at least one float metric NaN), `"nan"` (WY gate failed) | |
| `reasons` | `""` when ok; else `"; "`-joined entries, each naming the affected column(s) and the cause. For `partial`, EVERY NaN float column name appears in `reasons`. For `nan`, `reasons` mentions "valid days" and the counts | |

When the WY gate fails: all float metrics are NaN, `recession_n_segments = 0`, `bn_n_points = 0`, `bn_fit_ok = False`.
`n_*` columns are always filled.

### Recession (`recession_*`, `bn_*`)
Season window: Jun 1-Oct 31 of year `wy` (use whatever data exists; no window gate, only the WY gate).
A **falling limb** is a maximal run of calendar-consecutive valid days with `Q > 0`, `Q_t < Q_{t-1}` for each step, all inside
the season window. It is a **qualifying segment** if it has `>= recession_min_days` (7) strictly decreasing steps.
Fit points of a segment: its days after dropping the peak day and the first `recession_skip_days` (2) decreasing days.
(The tests only use limbs of 20 or more steps, or 4 or fewer, so the 7-vs-9 step ambiguity is unobserved.)
- `recession_n_segments`: number of qualifying segments (int, 0 if none); `recession_k_days`: median over segments of
  `-1/slope` of the least-squares line of `ln Q` against day (any combiner must return the common k when all segments
  share one k). NaN + reason if no segment.
- Brutsaert-Nieber `-dQ/dt = a Q^b`: points `(Q_mid, -dQ)` from consecutive fit-point pairs, `-dQ = Q_t - Q_{t+1}`
  (per day), `Q_mid = (Q_t + Q_{t+1})/2`, pooled over qualifying segments; `b` and `a` are fitted to the **lower envelope** of
  the cloud in log-log space (method free, e.g. log-binned lower quantile; document it in the docstring). Noise-free data from
  a single `(a, b)` lies on its own envelope, so the fit must return that `b`. `bn_n_points` = pooled pairs used,
  `bn_r2` = R2 of the log-log envelope fit, `bn_fit_ok = bn_n_points >= bn_min_points and bn_r2 >= bn_min_r2`.
  No segment: `bn_exponent_b`, `bn_a`, `bn_r2` NaN, `bn_n_points = 0`, `bn_fit_ok = False`, reasons recorded.
  Units: `a` in mm^(1-b) d^(b-2), reported with the literature caveat (scale non-invariant; Dralle et al.).

## Constants
`METRIC_COLUMNS` (tuple of the float metric columns above, i.e. every float column except `frac_provisional`) and
`TABLE_COLUMNS`, in this exact order:
```
wy, n_days_wy, n_valid_days_wy, n_negative_days_wy,
vol_wy_mm, vol_aprjul_mm, vol_aprsep_mm, frac_aprjul, com_wy_day, spring_pulse_day, peak_mm_d, peak_wy_day,
rb_flashiness, recession_k_days, recession_n_segments, bn_exponent_b, bn_a, bn_n_points, bn_r2, bn_fit_ok,
baseflow_eck_wy_mm, baseflow_lh_wy_mm, bfi_wy_eck, bfi_wy_lh, q7min_mm_d, augsep_mean_mm_d, next_oct_mean_mm_d,
days_peak_to_half, frac_provisional, status, reasons
```
dtypes: `wy`, `n_*`, `recession_n_segments`, `bn_n_points` int64; `bn_fit_ok` bool; `status`, `reasons` str; all others float64.

## Drivers
```
compute_responses(flow: pd.DataFrame, area_sqkm: float, params: ResponseParams | None = None) -> pd.DataFrame
compute_basin_responses(flow: pd.DataFrame | str | Path, basin: Basin | str, params=None) -> pd.DataFrame
write_responses_parquet(table: pd.DataFrame, path) -> Path
```
- `flow` columns: `date` (datetime64), `flow_cfs` (numeric), optional `qualifier` (str/None; the `fetch.usgs_flow` schema).
  A `str`/`Path` is a parquet file in that schema. `compute_basin_responses` takes the area from `Basin.drain_sqkm`
  (`str` resolved with `basins.get_basin`, so an unknown id raises `UnknownBasinError`).
- Validation (before any computation): bad `area_sqkm` -> `ValueError`; missing `date`/`flow_cfs`, non-datetime `date`,
  non-numeric `flow_cfs`, empty frame, or DUPLICATE dates -> `DataValidationError`; the message names the offending column (`date`, `flow_cfs`) or contains the word "duplicate".
  Row order is irrelevant: unsorted input gives the same table as sorted input. Input is never mutated.
- Output: one row per water year from `water_year(min date)` through `water_year(max date)` inclusive (years with no
  data, and the partial first/last years, appear as `status="nan"` rows), ascending `wy`, `RangeIndex`, columns
  `TABLE_COLUMNS`. Flow is converted with `cfs_to_mm_per_day` once, then `wy_metrics` is applied per WY.
- `frac_provisional` = (valid days of the WY whose qualifier tokens include `"P"`) / (valid days of the WY), computed
  whenever the WY has at least one valid day (also when the WY gate fails); `"A e"`, `"e"`, `"Ice"`, `""`, None are not
  provisional; provisional and estimated values are KEPT and enter every metric unchanged. No `qualifier` column ->
  `frac_provisional` NaN with a reason naming `frac_provisional` (status `partial` if the gate passed).
- `table.attrs["fallsoil"]` (dict, also the schema metadata of the parquet, JSON under key `b"fallsoil"`, written by
  `write_responses_parquet`, which creates parent dirs and returns the path) holds: `source="fallsoil.responses"`,
  `area_sqkm`, `params` (= `ResponseParams.as_dict()`), `units` ({column: unit} for every column except `status`/`reasons`;
  `*_mm` -> `"mm"`, `*_mm_d` -> `"mm/d"`, `recession_k_days`/`days_peak_to_half` -> `"d"`, `*_day` -> `"day of water year"`,
  dimensionless ratios/counts -> `"1"`), `time_convention` (contains "local standard time" and "calendar day"),
  `negative_flow_policy="excluded_not_clipped"`, `gap_policy="flagged_not_filled"`, `diagnostic_columns=["days_peak_to_half"]`
  and, for `compute_basin_responses`, `basin` (the gauge id).
