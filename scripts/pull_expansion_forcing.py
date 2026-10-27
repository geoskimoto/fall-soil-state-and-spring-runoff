"""Resumable gridMET pull (pr, tmmx, tmmn) for the expansion basins that passed the flow re-screen (step 3 of the expansion plan).

Reads data/interim/expansion_flow_screen.csv (scripts/screen_expansion_flow.py) and pulls WY1982 start (1981-10-01) to the end
of WY2025 (2025-09-30), so file names are stable. A variable whose parquet already exists is skipped; requests are sequential
with a pause. Not production code: it only calls fallsoil.fetch.gridmet and logs one line per request.
Usage: python scripts/pull_expansion_forcing.py [pause_s]
"""
import sys
import time

import pandas as pd

sys.path.insert(0, "src")
from fallsoil.config import interim_dir
from fallsoil.errors import FallsoilError
from fallsoil.expansion import load_expansion_basins
from fallsoil.fetch.gridmet import fetch_basin_mean

START, END = "1981-10-01", "2025-09-30"
pause_s = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
screen = pd.read_csv(interim_dir("usgs").parent / "expansion_flow_screen.csv", dtype={"basin": str})
passed = set(screen.loc[screen.pass_screen, "basin"])
basins = [b for b in load_expansion_basins() if b.id in passed]
print(f"{len(basins)} basins x 3 variables; {START} to {END}; pause {pause_s}s", flush=True)

failures, fetched, skipped = [], 0, 0
for b in basins:
    for v in ("pr", "tmmx", "tmmn"):
        if (interim_dir("gridmet") / f"gridmet_{b.id}_{v}_{START}_{END}.parquet").exists():
            skipped += 1
            continue
        try:
            print(f"{b.id} {v:<4} -> {fetch_basin_mean(b, v, START, END).name}", flush=True)
            fetched += 1
        except FallsoilError as exc:
            failures.append((b.id, v, str(exc)))
            print(f"{b.id} {v:<4} FAILED: {exc}", flush=True)
        time.sleep(pause_s)
print(f"fetched {fetched}, skipped {skipped}, failures {len(failures)}", flush=True)
sys.exit(1 if failures else 0)
