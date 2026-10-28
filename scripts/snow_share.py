"""Climate descriptor for classifying the unclassified expansion basins (not a classification itself).

For each basin in fall_signal_all.csv: share of Nov-Mar precipitation that falls on days with mean temperature below 0 C
(`snow_share_novmar`), and mean Dec-Feb temperature (`t_djf_c`), both from cached gridMET basin means, WY1982-2025. Uses no
runoff, so it cannot leak into the fall-signal test. Reads cached data only; writes the new file data/interim/snow_share.csv.
Basin mean temperature ignores elevation range, so the share is coarse. Usage: python scripts/snow_share.py
"""
import glob
import sys

import pandas as pd

sys.path.insert(0, "src")
from fallsoil.config import interim_dir

I = interim_dir("usgs").parent
S = pd.read_csv(I / "fall_signal_all.csv", dtype={"basin": str})
rows = []
for bid in S.basin:
    def load(v):
        return pd.read_parquet(sorted(glob.glob(str(I / "gridmet" / f"gridmet_{bid}_{v}_*.parquet")))[-1]).set_index("date")
    d = pd.DataFrame({"pr": load("pr").precip_mm, "t": (load("tmmx").temp_max_c + load("tmmn").temp_min_c) / 2})
    d = d[(d.index >= "1981-10-01") & (d.index <= "2025-09-30")]
    w = d[d.index.month.isin([11, 12, 1, 2, 3])]
    rows.append(dict(basin=bid, snow_share_novmar=round(w.pr[w.t < 0].sum() / w.pr.sum(), 3),
                     t_djf_c=round(d[d.index.month.isin([12, 1, 2])].t.mean(), 2)))
R = S[["basin", "name", "setting"]].merge(pd.DataFrame(rows)).sort_values("snow_share_novmar")
R.to_csv(I / "snow_share.csv", index=False)
print(R.to_string(index=False))
print("wrote", I / "snow_share.csv")
