"""EXPLORATORY first look (not a result): do fall precipitation anomalies explain Apr-Jul runoff beyond winter precip,
winter temperature and spring weather? gridMET basin means + USGS flow, WY1982-2025. No snow or soil-moisture data yet,
no multiple-testing control, no out-of-sample test. For orientation only.
"""
import sys
import numpy as np
import pandas as pd
import statsmodels.api as sm

sys.path.insert(0, "src")
from fallsoil.basins import list_basins
from fallsoil.config import interim_dir
from fallsoil.responses import compute_responses
from fallsoil.water_year_calculator import water_year

pd.set_option("display.width", 250, "display.max_columns", 40)
I = interim_dir("usgs").parent


def load(pattern_dir, prefix):
    import glob
    files = sorted(glob.glob(str(I / pattern_dir / prefix)))
    return pd.read_parquet(files[-1])


rows = []
for b in list_basins():
    flow = load("usgs", f"usgs_dv_{b.id}_*.parquet")
    resp = compute_responses(flow, b.drain_sqkm)
    pr = load("gridmet", f"gridmet_{b.id}_pr_*.parquet").set_index("date")["precip_mm"]
    tx = load("gridmet", f"gridmet_{b.id}_tmmx_*.parquet").set_index("date")["temp_max_c"]
    tn = load("gridmet", f"gridmet_{b.id}_tmmn_*.parquet").set_index("date")["temp_min_c"]
    tm = (tx + tn) / 2
    d = pd.DataFrame({"pr": pr, "tm": tm, "tx": tx})
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
    f = pd.DataFrame(out)
    t = resp.merge(f, on="wy")
    t = t[(t.status == "ok") & t.wy.between(1982, 2025)].copy()
    t["basin"], t["setting"] = b.id, b.setting
    rows.append(t)
T = pd.concat(rows, ignore_index=True)
print("basin-years used:", T.groupby("basin").size().to_dict())

def fit(df, y, extra):
    X = df[["p_decmar_mm", "t_djf_c", "tmax_aprjun_c", "p_aprjun_mm"] + extra].copy()
    X["p_decmar_mm"] = np.log(X["p_decmar_mm"])
    Z = (X - X.mean()) / X.std()
    return sm.OLS(np.log(df[y]), sm.add_constant(Z)).fit(cov_type="HC3"), Z

print("\n=== Response: ln(vol_aprjul_mm). Standardised coefficients (per 1 SD), HC3 robust ===")
res = []
for b in list_basins():
    df = T[T.basin == b.id].dropna(subset=["p_sepnov_mm", "vol_aprjul_mm"])
    base, _ = fit(df, "vol_aprjul_mm", [])
    full, Z = fit(df, "vol_aprjul_mm", ["p_sepnov_mm"])
    c = full.params["p_sepnov_mm"]; lo, hi = full.conf_int().loc["p_sepnov_mm"]
    corr = Z.corr().loc["p_sepnov_mm"].drop("p_sepnov_mm").abs().max()
    res.append(dict(basin=b.id, setting=b.setting, n=len(df), R2_base=round(base.rsquared, 2), R2_with_fall=round(full.rsquared, 2),
                    coef_fall=round(c, 3), ci95=f"[{lo:.2f}, {hi:.2f}]", p=round(full.pvalues["p_sepnov_mm"], 3),
                    max_abs_corr_fall_vs_other=round(corr, 2),
                    coef_decmar=round(full.params["p_decmar_mm"], 2), coef_tmaxAprJun=round(full.params["tmax_aprjun_c"], 2)))
print(pd.DataFrame(res).to_string(index=False))

print("\n=== Spring-pulse day: how often at a window edge (no pulse detected) ===")
edge = T.assign(at_edge=T.spring_pulse_day.isin([T.spring_pulse_day.max(), 304, 305]) )
print(T.groupby("basin").apply(lambda g: pd.Series({"n": len(g), "pulse_day_>=300": int((g.spring_pulse_day >= 300).sum()),
      "median_com_day": g.com_wy_day.median(), "median_frac_aprjul": round(g.frac_aprjul.median(), 2),
      "median_k_days": round(g.recession_k_days.median(), 1)})).to_string())
T.to_parquet("data/interim/first_look_table.parquet")
