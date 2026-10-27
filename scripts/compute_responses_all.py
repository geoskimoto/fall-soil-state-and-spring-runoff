"""Compute the M2a water-year response metrics for the six pilots and the expansion basins that passed the flow screen.

Reads cached USGS flow parquet only (no network). Writes one parquet per basin (all years, with the fallsoil metadata) under
data/interim/responses/, and a combined table of WY1982-2025 at data/interim/responses_all.parquet with basin, setting and
area_km2. Expansion basins stay `unclassified`. Usage: python scripts/compute_responses_all.py
"""
import glob
import sys

import pandas as pd

sys.path.insert(0, "src")
from fallsoil.basins import list_basins
from fallsoil.config import interim_dir
from fallsoil.expansion import load_expansion_basins
from fallsoil.responses import compute_responses, write_responses_parquet

FIRST_WY, LAST_WY = 1982, 2025
interim = interim_dir("usgs").parent
out_dir = interim / "responses"
out_dir.mkdir(parents=True, exist_ok=True)

screen = pd.read_csv(interim / "expansion_flow_screen.csv", dtype={"basin": str})
passed = set(screen.loc[screen.pass_screen, "basin"])
basins = list(list_basins()) + [b for b in load_expansion_basins() if b.id in passed]
print(f"{len(basins)} basins ({len(list_basins())} pilots + {len(basins) - len(list_basins())} expansion)", flush=True)

parts = []
for b in basins:
    files = sorted(glob.glob(str(interim_dir("usgs") / f"usgs_dv_{b.id}_*.parquet")))
    if not files:
        sys.exit(f"No cached flow for {b.id}; run the flow pull first.")
    table = compute_responses(pd.read_parquet(files[-1]), b.drain_sqkm)
    table.attrs["fallsoil"]["basin"] = b.id
    write_responses_parquet(table, out_dir / f"responses_{b.id}.parquet")
    w = table[table.wy.between(FIRST_WY, LAST_WY)].copy()
    w.insert(0, "basin", b.id)
    w["setting"], w["area_km2"] = b.setting, b.drain_sqkm
    parts.append(w)
    print(f"{b.id} {b.name[:38]:<38} usable {int((w.status == 'ok').sum()):>2} of {len(w):>2} WYs", flush=True)

T = pd.concat(parts, ignore_index=True)
T.to_parquet(interim / "responses_all.parquet")
print("\nstatus counts:", T.status.value_counts().to_dict())
print("combined rows:", len(T), "| basins:", T.basin.nunique())
reasons = T.loc[T.status != "ok", "reasons"].fillna("").str.split("; ").explode().str.replace(r"[:(].*", "", regex=True).value_counts()
print("top non-ok reasons:", reasons.head(6).to_dict())
