"""Resumable bulk pull of ERA5-Land daily soil moisture and SWE for the pilot basins (one request per calendar quarter).

Finished years are cached and skipped on re-run. Usage: python scripts/pull_era5land.py [first_year] [last_year] [workers]
Not production code: it only calls fallsoil.fetch.era5land and logs one line per year.
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

sys.path.insert(0, "src")
from fallsoil.basins import list_basins
from fallsoil.errors import FallsoilError
from fallsoil.fetch import era5land

first = int(sys.argv[1]) if len(sys.argv) > 1 else 1981
last = int(sys.argv[2]) if len(sys.argv) > 2 else datetime.now(timezone.utc).year
workers = int(sys.argv[3]) if len(sys.argv) > 3 else 3
area = era5land.union_area()
print(f"area N,W,S,E = {area}; years {first}-{last}; workers {workers}", flush=True)

def fetch_with_patience(y, qt, tries=60, wait_s=120):
    """CDS rejects submissions while its per-dataset queue is full; wait and retry (the pull is resumable anyway)."""
    for attempt in range(tries):
        try:
            return era5land.fetch_chunk(y, qt, area)
        except FallsoilError as exc:
            if "Maximum number queued" not in str(exc) or attempt == tries - 1:
                raise
            time.sleep(wait_s)


failures, done = [], []
with ThreadPoolExecutor(max_workers=workers) as pool:
    futures = {pool.submit(fetch_with_patience, y, qt): (y, qt) for y in range(first, last + 1) for qt in (1, 2, 3, 4)}
    for fut in as_completed(futures):
        y, qt = futures[fut]
        try:
            print(f"{y}Q{qt}: {fut.result().name}", flush=True)
            done.append((y, qt))
        except FallsoilError as exc:
            failures.append((y, qt, str(exc)))
            print(f"{y}Q{qt}: FAILED {exc}", flush=True)
print(f"done {len(done)} failures {len(failures)}", flush=True)
if len(failures) == 0:
    paths = [era5land.cached_chunk(y, qt, area) for y in range(first, last + 1) for qt in (1, 2, 3, 4)]
    for b in list_basins():
        print("basin means ->", era5land.write_basin_means(b, paths).name, flush=True)
sys.exit(1 if failures else 0)
