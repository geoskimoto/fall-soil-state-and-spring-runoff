# fallsoil Milestone 1 — public interface (defined by the tests)

The tests in this directory are the locked specification. This file is the unambiguous target
for the implementer. Package: `src/fallsoil/`. Python 3.12. HTTP is done with `requests`
(tests mock it with `responses`); NetCDF is read with `xarray`; geometry with `shapely`.

## Conventions shared by all modules
- Parquet files carry file-level schema metadata under key `b"fallsoil"` = UTF-8 JSON object with at least:
  `source` (str), `time_convention` (str), `units` ({column: unit string}), plus module-specific keys below.
- Every fetcher, on success, (1) caches the raw response bytes under `<data>/raw/<source dir>/` byte-identical
  (file names free; NEVER overwrite an existing file; changed upstream content becomes an additional file),
  (2) writes parquet under `<data>/interim/<source dir>/` and returns its `pathlib.Path`,
  (3) calls `manifest.record_fetch(...)`. gridMET raw NetCDF is cached like the others.
- On any failure: raise a `FallsoilError` subclass; write NO parquet and NO manifest record.
- Input arguments (site/triplet/variable/dates/basin) are validated BEFORE any network call.
- Dates accepted as `datetime.date` or ISO `YYYY-MM-DD` str. Parquet `date` column is `datetime64` (naive, calendar day).
- No gap filling, clipping, or dropping of rows anywhere. Bad values are kept and flagged by `qc`.
- Env `FALLSOIL_HTTP_BACKOFF_S` (float seconds) overrides the default backoff base in `fetch.http` (tests set 0).

## fallsoil.errors
```
class FallsoilError(Exception):   # .user_message: str (friendly, no traceback jargon); str(e) == user_message
class ConfigError(FallsoilError)
class UnknownBasinError(FallsoilError, LookupError)
class DataValidationError(FallsoilError)          # qc structural problems (types, missing columns, empty)
class ManifestError(FallsoilError)                # corrupt manifest line (message names the line number)
class FetchError(FallsoilError)
class HttpError(FetchError):                      # .status_code: int|None (None for timeout/connection), .attempts: int
class MalformedPayloadError(FetchError)
class EmptyResultError(FetchError)
class ZeroOverlapError(FetchError, ValueError)    # polygon has zero overlap with the grid
```
All constructed with a friendly message. Messages: HttpError includes the status code (if any) or the words
"timed out"/"timeout" for timeouts; EmptyResultError names the site/triplet; MalformedPayloadError names the
element/variable and says "unit" for unit problems.

## fallsoil.config
- `get_data_dir() -> Path`: from env `FALLSOIL_DATA_DIR` (read at call time), else `FALLSOIL_DATA_DIR=` in `./.env`
  (real env wins). Creates the directory if missing. `ConfigError` (message names the variable and/or the path) if unset/blank,
  not a directory, or not writable.
- `raw_dir(source: str) -> Path` = `<data>/raw/<source>`; `interim_dir(source) -> Path` = `<data>/interim/<source>`; both created.
  `ValueError` if `source` is empty or contains a path separator / is `.`/`..`.
- `manifest_path() -> Path` = `<data>/manifest.jsonl`.
- Source dir names: `usgs`, `snotel`, `gridmet`.

## fallsoil.water_year_calculator
- `water_year(d) -> int` (Series/DatetimeIndex in -> same-shaped Series/Index out; NaT -> `<NA>` in vectors).
  `d`: `date`, `datetime`, `pd.Timestamp`, `pd.Series`/`pd.DatetimeIndex` of datetimes. Uses the wall-clock date as given
  (no tz conversion). Anything else (str, int, None, float) -> `TypeError` or `ValueError`; scalar NaT -> `ValueError`.
- `wy_start(wy: int) -> date` (Oct 1 of wy-1); `wy_end(wy: int) -> date` (Sep 30 of wy);
- `wy_day(d) -> int` 1-based day of water year (Oct 1 = 1). Same input types as `water_year`.

## fallsoil.basins
- `@dataclass(frozen=True) Basin(id: str, name: str, setting: str, gauge: str)` with `id == gauge` (USGS site no.),
  `setting in {"maritime","transition","continental"}`; property `polygon_path -> Path`
  (`<data>/raw/basins/pilot_basins_gagesii.gpkg`), method `polygon() -> shapely Polygon|MultiPolygon` (EPSG:4326, valid;
  selects rows by `site_no`). Missing file -> `FallsoilError`.
- `list_basins() -> list[Basin]` — exactly: 14161500 (Lookout, maritime), 14159200 (SF McKenzie, maritime),
  12452800 (Entiat, transition), 12457000 (Wenatchee, transition), 13336500 (Selway, continental), 13331500 (Minam, continental).
  MF Salmon 13309220 is NOT in the registry.
- `Basin.polygon_sha256() -> str`: sha256 hex of `shapely.to_wkb(polygon(), output_dimension=2, byte_order=1)` of the geopackage polygon.
- `record_polygon_provenance() -> int`: appends one manifest record per pilot basin (`source="gagesii_polygon"`, `params={"site_no": id}`,
  `content` = that WKB, `url` = the geopackage path/URI, `rows`=1); returns number newly appended (0 on repeat).
- `get_basin(basin_id) -> Basin`; anything unknown (incl. None/int) -> `UnknownBasinError` whose message lists valid ids.

## fallsoil.fetch.http
`get(url, params=None, *, timeout_s=30.0, max_retries=3, backoff_base_s=None, sleep=time.sleep) -> bytes`
- 200 -> body bytes. Timeouts, connection errors and 5xx retried up to `max_retries` times (total attempts = max_retries+1),
  exponential non-decreasing sleeps (`backoff_base_s * 2**k`, jitter allowed if non-decreasing), no sleep after the last attempt.
  4xx (incl. 429) never retried. Finite `timeout_s` always passed to requests. `backoff_base_s=None` -> env/default 0.5.
- Failure -> `HttpError` (see above).

## fallsoil.manifest
- `sha256_bytes(data: bytes) -> str` (hex).
- `record_fetch(*, source: str, url: str, params: Mapping, content: bytes, rows: int, path: Path|None=None) -> bool`
  Appends one JSON line `{source,url,params,retrieved_utc (tz-aware UTC ISO-8601),sha256,rows,code_version}`.
  Returns False and writes nothing if a record with same (source,url,params (order-insensitive),sha256) already exists.
  Append-only (existing bytes never rewritten). Creates parent dirs. Corrupt existing file -> `ManifestError`, file untouched.
- `read_manifest(path: Path|None=None) -> list[dict]` (missing file -> `[]`). Default path = `config.manifest_path()`.
- Source names used by fetchers: `"usgs_dv"`, `"snotel_awdb"`, `"gridmet"`.

## fallsoil.fetch.usgs_flow
- `parse_qualifier(code: str|None) -> frozenset/set/list of str tokens` (`"A e"` -> {"A","e"}; ""/None -> empty).
- `fetch_daily_flow(site: str, start=None, end=None) -> Path` — NWIS daily-value mean discharge (param 00060, stat 00003)
  from `https://waterservices.usgs.gov/nwis/dv/` (RDB). `site`: 8-15 digits else `ValueError`/`TypeError`.
  Parquet columns (at least) `date, flow_cfs (float64), qualifier (str, verbatim e.g. "A", "A e", "P", "P e", "Ice", ""/None)`.
  Every RDB row kept in file order sorted by date (stable); blank flow -> NaN; negative kept; duplicates kept.
  Metadata: `source="usgs_dv"`, `site`, `time_convention` containing "local standard time" and "daily mean",
  `units={"flow_cfs":"ft3/s"|"cfs"}`.
  Errors: header-only -> `EmptyResultError` (names site); non-RDB/garbage/non-numeric flow/bad date/wrong parameter -> `MalformedPayloadError`.

## fallsoil.fetch.snotel
- Conversions (scalar, ndarray or Series; NaN-propagating): `inches_to_mm` (x25.4), `mm_to_inches`,
  `fahrenheit_to_celsius`, `celsius_to_fahrenheit`.
- `fetch_snotel_daily(triplet: str, elements=("WTEQ","PREC","TAVG","SMS"), start=None, end=None) -> Path`
  `triplet` like `"908:WA:SNTL"` else `ValueError`; unknown element code -> `ValueError`.
  AWDB `GET https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data` (DAILY) JSON: list of
  `{"stationTriplet", "data":[{"stationElement":{elementCode, heightDepth:{value:-2,unitCode:"in"}|null, storedUnitCode,...},
  "values":[{"date","value"?}]}]}`.
  Columns: `date`; `WTEQ`->`swe_mm` (in*25.4); `PREC`->`precip_accum_mm` (in*25.4); `TAVG`->`temp_avg_c` (degF);
  `SMS`->`soil_moisture_pct_<depth>in` per depth (|heightDepth.value| inches; percent passed through unchanged).
  Only requested elements. Requested element absent from payload -> WARNING log naming the code, no column invented.
  `storedUnitCode` must be `in` (WTEQ, PREC), `degF` (TAVG), `pct` (SMS) else `MalformedPayloadError` naming the element.
  Missing `value` -> NaN row kept. Metadata: `source="snotel_awdb"`, `station=<triplet>`, `time_convention` contains "PST",
  units mm / degC / percent per column.
  Errors: not-JSON/wrong shape/non-numeric/bad date -> `MalformedPayloadError`; `[]`, no series, or series with zero values -> `EmptyResultError` (names triplet).

## fallsoil.fetch.gridmet
- `kelvin_to_celsius(x)`.
- `cell_weights(lat, lon, polygon) -> xr.DataArray` dims `(lat, lon)`, non-negative, sums to 1: each cell's fractional
  overlap area with `polygon`, normalised. `lat`/`lon` = 1-D cell centres (either order); cell size 1/24 degree.
  Zero overlap (outside, edge-touching, empty polygon) -> `ZeroOverlapError`. Tolerance vs exact planar overlap: 5e-3 absolute.
- `area_weighted_mean(da: xr.DataArray, polygon) -> xr.DataArray`: dims `lat`,`lon` reduced; other dims kept.
- `fetch_basin_mean(basin: Basin|str, variable: str, start, end) -> Path`
  variable in `pr` (file var `precipitation_amount`, mm -> `precip_mm`), `tmmx` (`daily_maximum_temperature`, K -> `temp_max_c`),
  `tmmn` (`daily_minimum_temperature`, K -> `temp_min_c`); else `ValueError`; `end < start` -> `ValueError`.
  GET `http://thredds.northwestknowledge.net:8080/thredds/ncss/agg_met_<var>_1979_CurrentYear_CONUS.nc` with query
  `north,south,east,west` (covering the polygon bounds), `time_start`/`time_end` incl. the dates. Body: NetCDF with dims
  `day, lat, lon`, variable `units` attr ("mm" or "K"); anything else -> `MalformedPayloadError` (mentions "unit").
  Output columns `date, <precip_mm|temp_max_c|temp_min_c>, provisional (bool)`.
  Signature adds keyword `now: date|datetime|None = None` (injectable clock; None -> current UTC date; a datetime uses its UTC date).
  Provisional rule (no marker is read from the file): with R = retrieval date, a day d is provisional iff `(R - d).days <= 60`
  (60 days old: provisional; 61 days old: permanent); older days permanent.
  Metadata: `source="gridmet"`, `basin`, `retrieval_date` (ISO), `provisional_window_days=60`, `n_provisional_days`,
  `provisional_start`/`provisional_end` (ISO min/max date of provisional rows in the output, null if none),
  `aggregation="area_weighted_fractional_overlap"`, units, and `time_convention` stating days are approximately calendar days
  ending at midnight Mountain Standard Time (07:00 UTC the next day) — must contain "midnight" and "07:00 UTC" and
  "Mountain Standard Time" (or "MST"), and must NOT describe days as UTC days.
  Raw NetCDF response is cached byte-identical in `<data>/raw/gridmet/` (never overwritten; changed content -> additional file),
  and its sha256 recorded in the manifest (source `"gridmet"`).
  Errors: non-NetCDF/missing variable/bad units -> `MalformedPayloadError`; zero `day` steps -> `EmptyResultError`;
  no overlap -> `ZeroOverlapError`.

## fallsoil.qc
`run_qc(df: pd.DataFrame, dataset: "flow"|"snotel"|"gridmet") -> QCResult` (other `dataset` -> `ValueError`).
Never mutates `df`; never drops/clips rows.
- Requires datetime64 `date` column, non-empty frame, numeric value column(s): flow: `flow_cfs`; snotel: any present of
  `swe_mm, precip_accum_mm, temp_avg_c, soil_moisture_pct_*`; gridmet: any present of `precip_mm, temp_max_c, temp_min_c`.
  Violations (string-typed values, non-datetime date, missing columns, empty) -> `DataValidationError` naming the column.
- `QCResult.flags: DataFrame` (same index as input, all bool): per value column `c`: `<c>_missing` (NaN) and
  `<c>_out_of_range`; plus `duplicate_date` (every row whose date occurs >1x) and `non_monotonic_date` (row whose date < previous row's).
  Ranges: flow_cfs >= 0 and finite; swe_mm >= 0; precip_accum_mm >= 0; precip_mm >= 0; temp_*_c within [-60, 50];
  soil_moisture_pct_* within [0, 100]. Infinite values are out of range; NaN is "missing" only.
- `QCResult.gaps: DataFrame` columns `start, end, n_days` — maximal runs of calendar days between min and max date that have
  no row, or whose row has all value columns NaN. Gaps are logged at WARNING on a logger named `fallsoil.*`.
- `QCResult.ok: bool` (no flag set anywhere and no gaps), `QCResult.n_flagged: int` (rows with any flag).
- netCDF note: build bytes in tests via bytes(ds.to_netcdf()) (HDF5-format NetCDF4); impl may read with netCDF4.Dataset(memory=...) or a temp file (deleted afterwards).
