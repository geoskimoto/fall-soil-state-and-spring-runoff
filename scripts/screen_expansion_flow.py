"""Re-screen the expansion basins on real usable water years (step 2 of the expansion plan, docs/M2_PLAN.md).

Per basin, over WY1982-2025: `complete_wy` = water years with >= 350 daily values; `usable_wy` = water years whose response
row has status "ok" (the definition behind the pilot counts). A basin passes with usable_wy >= MIN_USABLE. Reads the cached
USGS parquet files only (no network). Writes data/interim/expansion_flow_screen.csv. Usage: screen_expansion_flow.py [MIN_USABLE]
"""
import glob
import sys

import pandas as pd

sys.path.insert(0, "src")
from fallsoil.config import interim_dir
from fallsoil.expansion import load_expansion_basins
from fallsoil.responses import compute_responses

MIN_USABLE = int(sys.argv[1]) if len(sys.argv) > 1 else 30
FIRST_WY, LAST_WY = 1982, 2025
rows, missing = [], []
for b in load_expansion_basins():
    files = sorted(glob.glob(str(interim_dir("usgs") / f"usgs_dv_{b.id}_*.parquet")))
    if not files:
        missing.append(b.id)
        continue
    resp = compute_responses(pd.read_parquet(files[-1]), b.drain_sqkm)
    w = resp[resp.wy.between(FIRST_WY, LAST_WY)]
    ok = w[w.status == "ok"]
    rows.append(dict(
        basin=b.id, name=b.name, area_km2=round(b.drain_sqkm, 1),
        complete_wy=int((w.n_valid_days_wy >= 350).sum()), usable_wy=len(ok),
        first_usable_wy=int(ok.wy.min()) if len(ok) else None, last_usable_wy=int(ok.wy.max()) if len(ok) else None,
        longest_gap_wy=int(((~w.status.eq("ok")).astype(int).groupby((w.status.eq("ok")).cumsum()).sum()).max()) if len(w) else 0,
        pass_screen=len(ok) >= MIN_USABLE,
    ))
T = pd.DataFrame(rows)
out = interim_dir("usgs").parent / "expansion_flow_screen.csv"
T.to_csv(out, index=False)
print(f"basins with flow: {len(T)} (no flow file: {missing or 'none'}); threshold: usable_wy >= {MIN_USABLE}")
print(f"complete_wy  >= {MIN_USABLE}: {int((T.complete_wy >= MIN_USABLE).sum())}")
print(f"usable_wy    >= {MIN_USABLE}: {int(T.pass_screen.sum())}")
print("usable_wy quantiles:", T.usable_wy.quantile([0, .25, .5, .75, 1]).round(0).to_dict())
print("dropped:", T.loc[~T.pass_screen, ["basin", "usable_wy"]].values.tolist())
print("wrote", out)
