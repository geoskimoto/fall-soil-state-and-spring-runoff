"""EXPLORATORY first look on all 42 basins (6 pilots + 36 expansion; not a result). Same model as scripts/first_look.py:
ln(Apr-Jul volume) on log(Dec-Mar precip), Dec-Feb temperature, Apr-Jun max temperature and Apr-Jun precip, with and without
Sep-Nov precip of the previous fall; standardized predictors, HC3 errors, per basin, WY1982-2025. No snow or soil-moisture
data, no out-of-sample test, no autocorrelation correction. Adds a Benjamini-Hochberg q-value across basins because 42 tests
produce about two nominal hits by chance. Basins missing any gridMET file are skipped and listed.
Reads cached data only. Writes first_look_all.parquet and fall_signal_all.csv (new files; first_look_table.parquet is untouched).
Usage: first_look_all.py [OUTDIR]   (default data/interim)
"""
import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.multitest import multipletests

sys.path.insert(0, "src")
from fallsoil.basins import list_basins
from fallsoil.config import interim_dir
from fallsoil.expansion import load_expansion_basins
from fallsoil.water_year_calculator import water_year

I = interim_dir("usgs").parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else I
OUT.mkdir(parents=True, exist_ok=True)
FIRST_WY, LAST_WY = 1982, 2025

R = pd.read_parquet(I / "responses_all.parquet")
meta = {b.id: b for b in list(list_basins()) + load_expansion_basins()}


def load(pattern):
    files = sorted(glob.glob(str(I / "gridmet" / pattern)))
    return pd.read_parquet(files[-1]) if files else None


rows, skipped = [], []
for bid in R.basin.unique():
    pr, tx, tn = (load(f"gridmet_{bid}_{v}_*.parquet") for v in ("pr", "tmmx", "tmmn"))
    if pr is None or tx is None or tn is None:
        skipped.append(bid)
        continue
    pr, tx, tn = (x.set_index("date")[c] for x, c in ((pr, "precip_mm"), (tx, "temp_max_c"), (tn, "temp_min_c")))
    d = pd.DataFrame({"pr": pr, "tx": tx, "tm": (tx + tn) / 2})
    d["wy"] = water_year(d.index.to_series()).astype(int)
    d["m"] = d.index.month
    out = []
    for wy, g in d.groupby("wy"):
        prev = d[(d.index >= f"{wy-1}-09-01") & (d.index <= f"{wy-1}-11-30")]
        out.append({
            "wy": wy,
            "p_sepnov_mm": prev.pr.sum() if len(prev) >= 85 else np.nan,
            "p_decmar_mm": g[g.m.isin([12, 1, 2, 3])].pr.sum(),
            "p_aprjun_mm": g[g.m.isin([4, 5, 6])].pr.sum(),
            "t_djf_c": g[g.m.isin([12, 1, 2])].tm.mean(),
            "tmax_aprjun_c": g[g.m.isin([4, 5, 6])].tx.mean(),
        })
    t = R[(R.basin == bid) & (R.status == "ok")].merge(pd.DataFrame(out), on="wy")
    rows.append(t[t.wy.between(FIRST_WY, LAST_WY)])
T = pd.concat(rows, ignore_index=True)
print(f"basins with forcing: {T.basin.nunique()} of {R.basin.nunique()}; skipped (missing gridMET): {skipped or 'none'}")


def fit(df, extra):
    X = df[["p_decmar_mm", "t_djf_c", "tmax_aprjun_c", "p_aprjun_mm"] + extra].copy()
    X["p_decmar_mm"] = np.log(X["p_decmar_mm"])
    Z = (X - X.mean()) / X.std()
    return sm.OLS(np.log(df["vol_aprjul_mm"]), sm.add_constant(Z)).fit(cov_type="HC3")


res = []
for bid, g in T.groupby("basin"):
    df = g.dropna(subset=["p_sepnov_mm", "vol_aprjul_mm"])
    df = df[df.vol_aprjul_mm > 0]
    if len(df) < 15:
        print(f"{bid}: only {len(df)} usable years with fall precipitation; no fit")
        continue
    base, full = fit(df, []), fit(df, ["p_sepnov_mm"])
    lo, hi = full.conf_int().loc["p_sepnov_mm"]
    b = meta[bid]
    res.append(dict(basin=bid, name=b.name, setting=b.setting, group="pilot" if b.setting != "unclassified" else "expansion",
                    area_km2=round(b.drain_sqkm, 1), n=len(df), r2_base=round(base.rsquared, 3), r2_with_fall=round(full.rsquared, 3),
                    coef_fall=round(full.params["p_sepnov_mm"], 3), ci_lo=round(lo, 3), ci_hi=round(hi, 3),
                    p=round(full.pvalues["p_sepnov_mm"], 4)))
F = pd.DataFrame(res)
F["q_bh"] = multipletests(F.p, method="fdr_bh")[1].round(4)
T.to_parquet(OUT / "first_look_all.parquet")
F.to_csv(OUT / "fall_signal_all.csv", index=False)
print(f"fit {len(F)} basins; nominal p<0.05: {int((F.p < 0.05).sum())} (about {0.05 * len(F):.1f} expected by chance); "
      f"BH q<0.05: {int((F.q_bh < 0.05).sum())}")
print(f"positive coefficient: {int((F.coef_fall > 0).sum())} of {len(F)}; median coef {F.coef_fall.median():+.3f}; "
      f"median R2 gain {(F.r2_with_fall - F.r2_base).median():+.3f}")
print(F.sort_values("p").head(8)[["basin", "name", "setting", "n", "coef_fall", "ci_lo", "ci_hi", "p", "q_bh"]].to_string(index=False))
print("wrote", OUT / "first_look_all.parquet", "and", OUT / "fall_signal_all.csv")
