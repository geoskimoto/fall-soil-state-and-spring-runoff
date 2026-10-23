# Milestone 1 Plan — Fetch layer, QC, manifest

Status: DRAFT for review. Tests are written from this spec by a separate agent before any
implementation exists.

## Goal
Reproducibly pull streamflow, SNOTEL, and gridded forcing for the six pilot basins, store
**basin-level parquet only**, validate at entry, and record provenance. Raw responses are
cached and never overwritten.

## Decisions
- **Basin polygons:** GAGES-II boundaries (offline, versioned) replace the flaky NLDI service.
  Only the `WestMnts` boundary shapefile is needed (all six pilot basins are in it). Polygons
  are stored in `data/raw/basins/` with source and checksum in the manifest. Caveat: 2009-era
  delineations.
- **Package:** `src/fallsoil/`, Python 3.12, pinned deps. Config via env/`.env`
  (`FALLSOIL_DATA_DIR`), never hardcoded paths.
- **Water year:** `water_year_calculator.py` is the only place water-year logic lives
  (Oct 1 - Sep 30; WY labelled by the calendar year in which it ends).
- **Units and time zones in names/docs:** `flow_cfs`, `precip_mm`, `swe_mm`, `temp_c`,
  `soil_moisture_pct`. USGS daily values are daily means in local standard time; AWDB daily
  values are in the station's fixed standard time (PST for WA/OR/ID); gridMET days are UTC-day
  aggregates. Each fetcher states its convention in its output metadata.
- **Gaps:** never filled or dropped silently. QC emits a flag column and a log record.

## Components and contracts (what the tests will pin down)

| Module | Contract |
|---|---|
| `config` | Resolves data dir from env; raises a clear error if unset/unwritable |
| `water_year_calculator` | `water_year(date)`, `wy_start(wy)`, `wy_end(wy)`, `wy_day(date)`; leap years; Oct 1 and Sep 30 boundaries; accepts date/datetime/pandas Series |
| `basins` | Registry of the 6 pilot basins (id, name, setting, gauge, polygon path); lookup by id; unknown id raises |
| `fetch.usgs_flow` | `fetch_daily_flow(site)` -> parquet with `date, flow_cfs, qualifier`; raw rdb cached; provisional/estimated qualifiers kept, not dropped |
| `fetch.snotel` | `fetch_snotel_daily(triplet, elements)` -> `date, swe_mm, precip_accum_mm, temp_avg_c, soil_moisture_pct_{depth}`; unit conversion from inches/F explicit and tested |
| `fetch.gridmet` | `fetch_basin_mean(basin, variable, start, end)` -> area-aware mean over cells whose centers fall inside the polygon; fails loudly if zero cells |
| `fetch.http` | Retry with backoff on timeout/5xx; never retries 4xx; friendly error message type for callers |
| `manifest` | Append-only JSONL: source, URL, params, retrieved_utc, sha256, rows, code version; re-fetch of identical content does not duplicate |
| `qc` | Range/type validation at entry (e.g. flow >= 0, SWE >= 0, temps within plausible bounds, monotonic dates, no duplicate dates); returns flags + gap report; never mutates source values |

## Test plan (written first, by a separate agent)
- **Unit:** water-year boundaries; unit conversions; qualifier parsing; polygon cell masking
  on synthetic grids; manifest idempotence.
- **Property (hypothesis):** water_year is monotonic non-decreasing in date and each WY has
  365/366 days; conversion round-trips; QC never changes input values.
- **Integration:** fetchers run against recorded HTTP fixtures (no live network in CI);
  separate opt-in `-m live` tests hit the real APIs for one small basin.
- **Failure handling:** timeout, 500, malformed payload, empty result - each yields a clear,
  friendly error, no partial parquet written.
- **Mutation:** run by an independent evaluator after implementation, not by the implementer.

## Steps
1. Review and approve this plan.
2. Fetch GAGES-II `WestMnts` boundaries; extract the six polygons; record in manifest.
3. Spawn a test-writing agent with this document and the guide only (no implementation).
4. Review the tests; they become locked.
5. Implement module by module; commit after each module passes.
6. Run live smoke test on one basin; run independent mutation evaluation.
7. Report plausible vs correct separately.

## Risks
- AWDB and NLDI-style endpoints are flaky; fixtures and retry layer isolate tests from this.
- gridMET access method (THREDDS NetCDF subset vs OPeNDAP) must be confirmed in step 2 before
  its contract is finalized.
- Disk: ~16 GB free; gridMET is fetched per-basin bounding box and discarded after aggregation.
