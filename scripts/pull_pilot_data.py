"""Exploratory bulk pull for the six pilot basins using the tested fetchers (idempotent; raw responses cached).

USGS daily flow (full record) and gridMET pr/tmmx/tmmn from WY1982 (1981-10-01) to today. Not production code: it only
calls fallsoil.fetch.* and logs one line per request.
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, "src")
from fallsoil.basins import list_basins
from fallsoil.errors import FallsoilError
from fallsoil.fetch.gridmet import fetch_basin_mean
from fallsoil.fetch.usgs_flow import fetch_daily_flow

START = "1981-10-01"
END = datetime.now(timezone.utc).date().isoformat()
failures = []
for b in list_basins():
    jobs = [("usgs", lambda b=b: fetch_daily_flow(b.id))] + [
        (v, lambda b=b, v=v: fetch_basin_mean(b, v, START, END)) for v in ("pr", "tmmx", "tmmn")
    ]
    for name, job in jobs:
        try:
            print(f"{b.id:>9} {b.name[:34]:<34} {name:<5} -> {job().name}", flush=True)
        except FallsoilError as exc:
            failures.append((b.id, name, str(exc)))
            print(f"{b.id:>9} {name:<5} FAILED: {exc}", flush=True)
print("failures:", len(failures))
sys.exit(1 if failures else 0)
