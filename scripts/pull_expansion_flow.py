"""Resumable USGS daily-flow pull for the expansion basins (step 2 of the expansion plan, docs/M2_PLAN.md).

Full record up to the end of WY2025 (2025-09-30), so file names are stable and the pull is reproducible. A basin whose
parquet already exists is skipped; requests are sequential with a pause between them. Not production code: it only calls
fallsoil.fetch.usgs_flow and logs one line per basin. Usage: python scripts/pull_expansion_flow.py [pause_s]
"""
import sys
import time

sys.path.insert(0, "src")
from fallsoil.config import interim_dir
from fallsoil.errors import FallsoilError
from fallsoil.expansion import load_expansion_basins
from fallsoil.fetch.usgs_flow import DEFAULT_START, fetch_daily_flow

END = "2025-09-30"
pause_s = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
basins = load_expansion_basins()
print(f"{len(basins)} expansion basins; flow to {END}; pause {pause_s}s", flush=True)

failures, fetched, skipped = [], 0, 0
for b in basins:
    target = interim_dir("usgs") / f"usgs_dv_{b.id}_{DEFAULT_START}_{END}.parquet"
    if target.exists():
        skipped += 1
        continue
    try:
        print(f"{b.id} {b.name[:40]:<40} -> {fetch_daily_flow(b.id, end=END).name}", flush=True)
        fetched += 1
    except FallsoilError as exc:
        failures.append((b.id, str(exc)))
        print(f"{b.id} FAILED: {exc}", flush=True)
    time.sleep(pause_s)
print(f"fetched {fetched}, skipped {skipped}, failures {len(failures)}", flush=True)
sys.exit(1 if failures else 0)
