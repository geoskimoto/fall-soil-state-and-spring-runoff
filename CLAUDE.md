# CLAUDE.md: Fall Soil State and Spring Runoff

Project instructions for Claude Code. These add to the owner's global rules in `~/.claude/CLAUDE.md` (commit format, testing
discipline, data integrity, water year, scheduling). Where this file is stricter it wins. Results so far are in `findings.md`;
the research design is in `docs/PROJECT_GUIDE.md`; the milestone plans and decisions are in `docs/M1_PLAN.md` and `docs/M2_PLAN.md`.

## 1. What this project is

Hydrology research. **Question:** does the soil / shallow-groundwater state at the start of the snow season (fall, Sep to Nov)
change April to July runoff volume, runoff timing, recession and late-season baseflow in Columbia basin headwaters?
**Hypothesis:** dry fall soils must be refilled before melt produces runoff, so a dry fall lowers spring runoff efficiency,
mainly in snow-dominated continental basins and little in maritime ones (fill and spill; nonlinear).

Deliverables: a research paper, then a Plotly Dash explorer. Scale: a few collaborators, low concurrency; the analysis package is
framework-independent. Repo: `geoskimoto/fall-soil-state-and-spring-runoff` (branch `main`). Python package: `src/fallsoil/`.

**Current state (2026-10-28):** 42 basins (6 pilots + 36 screened expansion gauges), WY1982 to 2025. Response metrics, gridMET
forcing and an exploratory per-basin regression are done. Snow and soil moisture (ERA5-Land) are not yet available; that pull is
being restarted on a new machine with `new_deploy.sh`. See `findings.md` section 1 and 9.

## 2. Hard rules

### Testing (project policy, in addition to the global rules)
- **Never modify application code just to make a failing test pass.** If a test fails, report which and why, stop, and ask.
- **Never edit a locked test.** `tests/LOCKED.sha256` holds SHA-256 hashes of 31 files; check with
  `sha256sum -c tests/LOCKED.sha256` (all 31 OK on 2026-10-28). Do not change a locked file or its hash without the owner's approval.
- Tests that implementation code was written alongside (`tests/test_expansion.py`, 14 tests) are **not** an independent
  specification. Say so when reporting.
- Report **pass rate and correctness separately.** A passing suite is plausible, not correct. Currently 891 pass
  (`pytest -m "not live"`, about 20 s); 3 live tests are skipped unless run with `-m live`.
- **Relaxed policy (owner decision 2026-10-24):** no new locked suites or mutation evaluation while the research design iterates.
  Existing locked tests must stay green. Exploratory scripts under `scripts/` are not production code and are not unit-tested.

### Data
- **Never overwrite raw data.** Raw responses are cached byte-identical under `data/raw/<source>/`; a re-pull writes a new file
  (the date range is in the file name) and the scripts take the latest-sorting file. Derived data goes to new files under
  `data/interim/`. Never edit a cached file by hand.
- **Log data-quality issues** (gaps, partial years, failed pulls) in `findings.md` section 7 and in the output tables (`status`,
  `reasons`); do not drop or fill silently. Gap filling is never automatic.
- **Chronological splits only;** never shuffle temporal data. Water year is **Oct 1 to Sep 30** and comes only from
  `fallsoil.water_year_calculator` (single source of truth). Never use calendar-year seasons.
- Units go in names (`flow_cfs`, `vol_aprjul_mm`, `precip_mm`, `temp_max_c`, `sm_l1_m3m3`). USGS days are local standard time,
  SNOTEL PST, gridMET midnight MST; all treated as the same calendar day (stated in metadata, not corrected).
- Distinguish daily mean from instantaneous and peak flow; they are not interchangeable.
- No PII in logs. The data are aggregate hydrology only.

### Secrets and credentials
- The Copernicus CDS key lives in `~/.cdsapirc` (mode 600), created by the owner. **Never read it into output, store it in the repo,
  log it, or put it in `.env`.** `fallsoil.fetch.era5land` does not read or log it. Do not paste it into commands.
- `.env` holds only `FALLSOIL_DATA_DIR` (the data directory) and is git-ignored.

### Behaviour
- Moderate or larger tasks: write the plan, implement in pieces, commit regularly. **Ask before structural changes** beyond the
  request, and before pushing, deleting, or changing existing pilot metadata. Don't refactor surrounding code when fixing a bug.
- **Scheduling:** before any new cron job or scheduled task, examine `crontab -l`, `/etc/cron.d/` and all user crontabs, and record the
  chosen slot and why. None has been created for this project.
- Multi-tenant machines: files inside another user's `/home/<user>/` tree are written as that user, never as root.
- Commit format `<tag>: <summary of all meaningful changes>`, tags `feat fix refactor test docs chore deploy`. End every commit
  with the attribution line the harness specifies. End every reply with a short summary of what changed and why; when something
  fails, say why.
- Label chart axes with units; use colorblind-safe palettes; distinguish observed from modelled data.
- Findings are exploratory until stated otherwise. Do not call a first-look result a finding of the study.

## 3. Setup and commands

```
./new_deploy.sh --help           # full machine setup, optional data rsync, tests, resumable pulls, ERA5 pull
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt     # exact pins
echo "FALLSOIL_DATA_DIR=$PWD/data" > .env
.venv/bin/python -m pytest -q -m "not live"          # 891 tests, ~20 s
.venv/bin/python -m pytest -m live                   # real network calls; opt in
sha256sum -c tests/LOCKED.sha256                     # all 31 locked files must be OK
```

Run scripts from the repo root (they add `src` to `sys.path`). The existing `.venv` here runs Python 3.13; the target is 3.12 or
newer (`requirements.txt` is pinned; there is no `requires-python` in `pyproject.toml`).

## 4. Layout

```
src/fallsoil/
  basins.py            Basin dataclass, six pilots (list_basins), polygon loading; optional polygon_file and area_sqkm
  expansion.py         load_expansion_basins() (62 expansion gauges), polygon provenance
  config.py            FALLSOIL_DATA_DIR from env then ./.env; raw_dir / interim_dir per source
  manifest.py          data/manifest.jsonl: one record per fetch (URL, params, time, SHA-256, rows, code version)
  qc.py, errors.py     validation helpers; FallsoilError hierarchy (FetchError, ConfigError, ZeroOverlapError, ...)
  water_year_calculator.py   single source of truth for water-year logic
  fetch/               usgs_flow, gridmet (THREDDS, area-weighted), snotel, era5land (CDS), http (retry), _common (cache_raw)
  responses/           M2a response metrics: metrics, filters (Eckhardt, Lyne-Hollick), recession, runs, units, driver, params
tests/                 27 test files; INTERFACE.md and INTERFACE_M2A.md are the specs the locked tests were written from
scripts/               exploratory drivers (see section 5); not production code
docs/                  PROJECT_GUIDE, M1_PLAN, M2_PLAN (decisions and status), LITERATURE_REVIEW
findings.md            what has been measured, with caveats and an open-questions list
new_deploy.sh          new-machine setup and ERA5 pull launcher
data/                  git-ignored: raw/ (never overwritten), interim/ (derived), manifest.jsonl
reports/, research_notes/   literature-review working files, git-ignored
```

Data directories (all under `data/`): `raw/{usgs,gridmet,gagesii,basins,screening,era5land}`; `interim/{usgs,gridmet,responses,era5land}`
plus `expansion_flow_screen.csv`, `responses_all.parquet`, `first_look_all.parquet`, `fall_signal_all.csv`, `snow_share.csv`,
and the older six-basin `first_look_table.parquet` (the dashboard used it; do not overwrite it).

## 5. Scripts (order of use)

| Script | Purpose | Writes |
|---|---|---|
| `phase0_gagesii_check.py`, `phase0_screen_gauges.py`, `phase0_screen_basins.py` | Phase 0 pilot screening (2026-10-23) | `data/raw/screening/` |
| `extract_pilot_basins.py` | pilot polygons from GAGES-II | `data/raw/basins/pilot_basins_gagesii.gpkg` |
| `pull_pilot_data.py` | USGS flow + gridMET for the six pilots | `data/interim/{usgs,gridmet}` |
| `first_look.py` | six-basin exploratory regression (superseded by `first_look_all.py`) | `first_look_table.parquet` |
| `phase1_count_candidates.py` | candidate funnel for the expansion (276 to 66) | `data/raw/screening/phase1/columbia_candidates.csv` |
| `extract_expansion_basins.py` | 62 expansion polygons + manifest provenance | `data/raw/basins/expansion_basins_gagesii.gpkg` |
| `pull_expansion_flow.py [pause_s]` | USGS flow for 62 gauges to 2025-10-31; resumable | `data/interim/usgs` |
| `screen_expansion_flow.py` | keep gauges with >= 30 usable WYs | `expansion_flow_screen.csv` |
| `pull_expansion_forcing.py [pause_s]` | gridMET pr/tmmx/tmmn for the 36 passers; resumable | `data/interim/gridmet` |
| `compute_responses_all.py` | response metrics for 42 basins | `responses/`, `responses_all.parquet` |
| `first_look_all.py [OUTDIR]` | 42-basin regression with Benjamini-Hochberg q | `first_look_all.parquet`, `fall_signal_all.csv` |
| `snow_share.py` | snow-share descriptor for classification | `snow_share.csv` |
| `pull_era5land.py FIRST LAST WORKERS` | ERA5-Land by quarter over the six-pilot box; resumable | `data/raw/era5land/*.nc`, `data/interim/era5land/` |

The pull scripts are sequential with a pause, skip files that already exist, and exit 1 if any request failed. Re-run until they
exit 0. They need no cron: run them by hand or in the background.

## 6. Domain notes that are easy to get wrong

- **Basins and settings.** Pilots: Lookout Creek 14161500 and SF McKenzie 14159200 (maritime), Entiat 12452800 and Wenatchee
  12457000 (transition), Minam 13331500 and Selway 13336500 (continental). The 36 expansion basins are `unclassified`. Pilot labels
  were set by region and do not follow the snow-share descriptor (Entiat is snowier than Minam); see `findings.md` section 6.
- **Usable water year** = response status `ok` for WY1982 to 2025. Wenatchee (gap 1979 to 1989) and SF McKenzie (gap 1987 to 2000)
  have real USGS gaps and cannot be repaired.
- **WY2025 needs flow through 2025-10-31** because `q7min_mm_d` (Aug 1 to Oct 31) and `next_oct_mean_mm_d` use October. An
  earlier pull ended 2025-09-30 and left WY2025 partial.
- **Spring-pulse day** is not interpretable in maritime basins; use `com_wy_day` and `frac_aprjul` there.
- **Fall predictors** for WY X are the previous Sep to Nov (a one-year lag applied at assembly).
- **GAGES-II reference status** covers data through 2009 only; later regulation is unchecked.
- **Candidate-screen record length** is a proxy; real usable years come from the flow pull.
- **ERA5-Land requests are per calendar quarter** (not per year as the older plan text said): the CDS cost limit is about 400 fields
  (variables x days). 180 requests for 1981 to 2025. Layer 4 is omitted. Daily statistics use UTC-08:00. The request box is the
  six-pilot union (`union_area()`); the 36 expansion basins are deferred (one box per HUC4 group).
- **gridMET via THREDDS** (`thredds.northwestknowledge.net`) can time out for hours. The fetcher retries four times with backoff;
  the pull scripts are resumable, so re-run them later instead of looping.
- **CDS queue:** requests can wait for hours, and the account limits queued requests. Do not run two ERA5 pulls on one account. A pull
  with no `YYYYQn: ....nc` lines for many hours and `CLOSE-WAIT` sockets (`ss -tnp | grep python`) is hung; stop and restart it
  (finished quarters stay cached).
- **Statistics:** per-basin OLS on ln(volume) with standardized predictors, HC3 errors; 42 basins give about 2 chance hits at
  p<0.05, so always report Benjamini-Hochberg q alongside. Neighbouring basins share storms, so they are not independent tests.

## 7. Decisions on record (do not relitigate without the owner)

- Soil moisture and SWE source: ERA5-Land (primary); University of Arizona 4 km SWE and NLDAS-2 later (2026-10-23).
- SNOTEL is a flag, not a filter (2026-10-27). Re-screen at >= 30 usable WYs since 1982. New basins stay `unclassified` until a data
  rule is chosen (pending). ERA5 for new basins waits until the set is final.
- Testing policy relaxed (2026-10-24), see section 2.
- Spring-pulse proposals (Feb 1 default, "no pulse" NaN, interpret only for transition and continental) are **proposed, not approved**;
  the "no pulse" NaN would change a locked spec.

## 8. Open items (see `findings.md` section 9)

Classify the 36 basins (snow-share thresholds recommended; the owner decides); get ERA5-Land running on the new machine and build
the M2c covariates (snow-on date, soil moisture at snow-on, deficit); extend ERA5 to the expansion basins; pooled cluster-aware
model; decide whether to widen the region; update the dashboard (private artifact; sharing is the owner's call).

## 9. Moving to a new machine

1. Stop any ERA5 pull still running on the old machine (it competes for the CDS queue).
2. Push the repo; `git clone` on the new machine; create `~/.cdsapirc` (mode 600) and accept the ERA5-Land licence on the CDS site.
3. `./new_deploy.sh --data-from <old-host>:<path>/data/` copies missing data (never overwrites, never deletes), runs the tests and
   starts the ERA5 pull in the background (log in `logs/`, pid in `logs/era5land_pull.pid`). `--dry-run` prints the steps first.
   `--pipeline` additionally re-runs the resumable pulls and the analysis. If you skip `--data-from`, `data/raw/gagesii/` (721 MB) and
   the pilot/expansion polygons must be re-created with the extract scripts, and the USGS/gridMET pulls re-run (slow).
