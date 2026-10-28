#!/usr/bin/env bash
# new_deploy.sh - set this project up on a new machine and start (or resume) the ERA5-Land pull.
#
# Run from a fresh `git clone` of the repo on the NEW machine. Safe to re-run: every step is idempotent, finished ERA5
# quarters are cached under data/raw/era5land/ and skipped, and no raw file is ever overwritten.
#
# What it does, in order:
#   1. preflight    Python >= 3.12, free disk, ~/.cdsapirc present with mode 600 (contents are never read or printed)
#   2. environment  .venv from the pinned requirements.txt; .env with FALLSOIL_DATA_DIR (no secrets)
#   3. data         optional rsync of data/ from the old machine (--data-from); copies only files that do not exist yet
#   4. tests        pytest -m "not live" (the 891-test suite; existing locked tests must stay green)
#   5. pipeline     optional (--pipeline): resumable flow and gridMET pulls, response metrics, 42-basin first look
#   6. ERA5-Land    scripts/pull_era5land.py in the background, logging to logs/, pid in logs/era5land_pull.pid
#
# Usage: ./new_deploy.sh [options]
#   --data-from SRC    rsync source for data/, e.g. user@old-host:/home/user/projects/dry-fall-effects/data/ (never deletes)
#   --first YEAR       first ERA5 year (default 1981)         --last YEAR   last ERA5 year (default 2025)
#   --workers N        concurrent CDS requests (default 2; the CDS queue limits requests per account)
#   --pipeline         also run the resumable flow/gridMET pulls and analysis scripts (needs data/ from --data-from)
#   --skip-tests       skip step 4                            --min-free-gb N   required free disk, GB (default 20)
#   --no-pull          set up only; do not start the ERA5 pull
#   --dry-run          print what would run and change nothing
#   -h, --help         show this header
#
# Before the first run: create ~/.cdsapirc (mode 600) with your CDS key, and accept the licence for the dataset
# derived-era5-land-daily-statistics on the CDS website. Stop any ERA5 pull still running on the old machine: CDS limits
# queued requests per account, and a second pull from the same account competes with it.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

DATA_FROM="" FIRST=1981 LAST=2025 WORKERS=2 PIPELINE=0 SKIP_TESTS=0 NO_PULL=0 DRY=0 MIN_FREE_GB=20
while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-from) DATA_FROM="${2:?--data-from needs a value}"; shift 2 ;;
    --first) FIRST="${2:?}"; shift 2 ;;
    --last) LAST="${2:?}"; shift 2 ;;
    --workers) WORKERS="${2:?}"; shift 2 ;;
    --min-free-gb) MIN_FREE_GB="${2:?}"; shift 2 ;;
    --pipeline) PIPELINE=1; shift ;;
    --skip-tests) SKIP_TESTS=1; shift ;;
    --no-pull) NO_PULL=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

log() { printf '\n== %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
run() { if [[ $DRY -eq 1 ]]; then printf '[dry-run] %s\n' "$*"; else "$@"; fi; }
[[ $FIRST =~ ^[0-9]{4}$ && $LAST =~ ^[0-9]{4}$ && $FIRST -le $LAST ]] || die "--first/--last must be years with first <= last"
[[ $WORKERS =~ ^[0-9]+$ && $WORKERS -ge 1 ]] || die "--workers must be a positive integer"

# 1. preflight -------------------------------------------------------------------------------------------------------
log "1/6 preflight"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || die "$PY not found; install Python 3.12 or newer (or set PYTHON=/path/to/python)"
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 12))' || die "Python 3.12 or newer is required (found $("$PY" -V))"
echo "python: $("$PY" -V)"
free_gb=$(df --output=avail -BG . | tail -1 | tr -dc '0-9')
echo "free disk here: ${free_gb} GB (need >= ${MIN_FREE_GB})"
[[ $free_gb -ge $MIN_FREE_GB ]] || die "less than ${MIN_FREE_GB} GB free; free space or lower --min-free-gb"
if [[ $NO_PULL -eq 0 ]]; then
  [[ -f "$HOME/.cdsapirc" ]] || die "~/.cdsapirc not found. Create it with your CDS url and key (never commit it), then chmod 600."
  [[ "$(stat -c %a "$HOME/.cdsapirc")" == "600" ]] || die "~/.cdsapirc must be mode 600: chmod 600 ~/.cdsapirc"
  grep -q '^key:' "$HOME/.cdsapirc" || die "~/.cdsapirc has no 'key:' line"
  echo "~/.cdsapirc: present, mode 600 (contents not read)"
fi

# 2. environment -----------------------------------------------------------------------------------------------------
log "2/6 environment"
if [[ ! -d .venv ]]; then run "$PY" -m venv .venv; else echo ".venv exists"; fi
run .venv/bin/python -m pip install --quiet --upgrade pip
run .venv/bin/python -m pip install --quiet -r requirements.txt
if [[ ! -f .env ]]; then
  if [[ $DRY -eq 1 ]]; then echo "[dry-run] write .env with FALLSOIL_DATA_DIR=$PWD/data"; else echo "FALLSOIL_DATA_DIR=$PWD/data" > .env; fi
else
  echo ".env exists ($(grep -c . .env) lines; not modified)"
fi
run mkdir -p data logs

# 3. data ------------------------------------------------------------------------------------------------------------
log "3/6 data"
if [[ -n "$DATA_FROM" ]]; then
  # --ignore-existing: never overwrite a file already here. No --delete. Half-finished CDS temp dirs are not copied.
  run rsync -a --ignore-existing --info=stats1 --exclude 'raw/era5land/tmp*' "${DATA_FROM%/}/" data/
else
  echo "no --data-from: data/ is whatever is already here (raw pulls are re-downloadable but slow; see CLAUDE.md)"
fi

# 4. tests -----------------------------------------------------------------------------------------------------------
log "4/6 tests"
if [[ $SKIP_TESTS -eq 1 ]]; then echo "skipped"; else run .venv/bin/python -m pytest -q -m "not live"; fi

# 5. pipeline (optional) ---------------------------------------------------------------------------------------------
log "5/6 pipeline"
if [[ $PIPELINE -eq 1 ]]; then
  [[ $DRY -eq 1 || -f data/raw/basins/expansion_basins_gagesii.gpkg ]] \
    || die "--pipeline needs data/ from the old machine (missing data/raw/basins/expansion_basins_gagesii.gpkg); use --data-from"
  for step in pull_expansion_flow.py screen_expansion_flow.py pull_expansion_forcing.py compute_responses_all.py first_look_all.py; do
    echo "-- scripts/$step"
    # the two pull scripts exit 1 when any request failed; they are resumable, so report and carry on
    if [[ $DRY -eq 1 ]]; then echo "[dry-run] .venv/bin/python scripts/$step"; else .venv/bin/python "scripts/$step" || echo "WARNING: $step exited $? (re-run; pulls resume)"; fi
  done
else
  echo "skipped (use --pipeline to re-run the resumable pulls and analysis)"
fi

# 6. ERA5-Land -------------------------------------------------------------------------------------------------------
log "6/6 ERA5-Land pull"
if [[ $NO_PULL -eq 1 ]]; then echo "skipped (--no-pull)"; exit 0; fi
PIDFILE=logs/era5land_pull.pid
if [[ -f $PIDFILE ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  die "an ERA5 pull is already running here (pid $(cat "$PIDFILE")); not starting a second one"
fi
have=0
[[ -d data/raw/era5land ]] && have=$(find data/raw/era5land -maxdepth 1 \( -name '*.nc' -o -name '*.zip' \) | wc -l)
want=$(( (LAST - FIRST + 1) * 4 ))
echo "cached quarters: ${have} of ${want}; the pull skips those and requests the rest"
LOG="logs/era5land_pull_$(date -u +%Y%m%dT%H%M%SZ).log"
if [[ $DRY -eq 1 ]]; then
  echo "[dry-run] nohup .venv/bin/python -u scripts/pull_era5land.py $FIRST $LAST $WORKERS > $LOG 2>&1 &"
else
  nohup .venv/bin/python -u scripts/pull_era5land.py "$FIRST" "$LAST" "$WORKERS" > "$LOG" 2>&1 &
  echo $! > "$PIDFILE"
  echo "started pid $(cat "$PIDFILE"), log $LOG"
fi
cat <<MSG

Monitor:   tail -f ${LOG}
Progress:  ls data/raw/era5land/*.{nc,zip} 2>/dev/null | wc -l   (target ${want}; one file per year-quarter)
Stop:      kill \$(cat ${PIDFILE})                (safe: finished quarters stay cached)
Resume:    ./new_deploy.sh --skip-tests          (re-run the same command; it picks up where the cache ends)
CDS requests can queue for hours. A healthy pull shows 'YYYYQn: <file>' lines; no lines for many hours with
CLOSE-WAIT sockets (ss -tnp | grep python) is the hung state seen on the old machine.
MSG
