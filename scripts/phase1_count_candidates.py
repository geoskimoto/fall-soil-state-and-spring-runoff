"""Phase 1 (exploratory): count candidate gauges for the Columbia-basin expansion. Nothing is pulled into the data tree.

Stages: GAGES-II Ref, HUC02=17 -> area 50-2000 km2 -> Columbia HUC4 1701-1709 -> USGS daily-flow record length.
SNOTEL stations inside the GAGES-II polygon are counted as a flag, not a filter (decision 2026-10-27: gridded SWE and
soil moisture carry the analysis; SNOTEL only validates). `selected` = >= 40 years with data. Record length is a proxy
from the USGS series catalog (days with data / 365.25), not a count of complete water years (>= 350 values); the real
count needs the flow pull. Raw responses are cached under OUTDIR and never overwritten.
Usage: phase1_count_candidates.py OUTDIR
"""
import io
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
PILOT = {"14161500", "14159200", "12452800", "12457000", "13336500", "13331500"}
HEAD = {"User-Agent": "curl/8.5.0", "Accept": "*/*"}


def get(url, path):
    if not path.exists():
        for _ in range(4):
            r = requests.get(url, timeout=90, headers=HEAD)
            if r.status_code < 500:
                break
        r.raise_for_status()
        path.write_bytes(r.content)
    return path.read_bytes()


pts = gpd.read_file(REPO / "data/raw/gagesii/pts/gagesII_9322_sept30_2011.shp")
funnel = []
c = pts[(pts.CLASS == "Ref") & (pts.HUC02 == "17")].copy()
funnel.append(("GAGES-II Ref, HUC02 = 17 (Pacific Northwest)", len(c)))
c = c[c.DRAIN_SQKM.between(50, 2000)]
funnel.append(("area 50-2000 km2", len(c)))

# USGS site info with HUC8 and the daily-discharge series catalog
rows = []
ids = list(c.STAID)
for i in range(0, len(ids), 40):
    chunk = ids[i:i + 40]
    url = ("https://waterservices.usgs.gov/nwis/site/?format=rdb&seriesCatalogOutput=true"
           f"&parameterCd=00060&outputDataTypeCd=dv&sites={','.join(chunk)}")
    txt = get(url, OUT / f"site_cat_{i}.rdb").decode()
    lines = [l for l in txt.splitlines() if l and not l.startswith("#")]
    rows.append(pd.read_csv(io.StringIO("\n".join([lines[0]] + lines[2:])), sep="\t", dtype=str))
S = pd.concat(rows, ignore_index=True)
S = S[(S.data_type_cd == "dv") & (S.parm_cd == "00060") & (S.stat_cd == "00003")].copy()
for col in ("count_nu",):
    S[col] = pd.to_numeric(S[col])
S["begin"] = pd.to_datetime(S.begin_date, errors="coerce")
S["end"] = pd.to_datetime(S.end_date, errors="coerce")
S = S.sort_values("count_nu", ascending=False).drop_duplicates("site_no")
S["huc4"] = S.huc_cd.str[:4]
S["years_with_data"] = (S.count_nu / 365.25).round(1)
start82 = pd.Timestamp("1981-10-01")
S["span_since_wy1982_yr"] = ((S.end - S.begin.where(S.begin > start82, start82)).dt.days / 365.25).clip(lower=0).round(1)
c = c.merge(S[["site_no", "huc_cd", "huc4", "begin", "end", "count_nu", "years_with_data", "span_since_wy1982_yr"]],
            left_on="STAID", right_on="site_no", how="left")
funnel.append(("with a USGS daily-discharge series", int(c.count_nu.notna().sum())))
c = c[c.huc4.between("1701", "1709")]
funnel.append(("Columbia basin (HUC4 1701-1709; drops coast, Puget Sound, closed basins)", len(c)))

# SNOTEL stations (AWDB), then point-in-polygon against GAGES-II reference polygons
st = []
for s in ("WA", "OR", "ID", "MT", "WY", "NV", "UT"):
    p = REPO / "data/raw/screening" / f"awdb_{s}_plain.json"
    if p.exists():
        st += json.loads(p.read_bytes())
    else:
        st += json.loads(get(f"https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/stations?stationTriplets=*:{s}:SNTL&activeOnly=false", OUT / f"awdb_{s}_plain.json"))
sn = pd.DataFrame(st)
sn["begin"] = pd.to_datetime(sn.beginDate, errors="coerce")
sn = gpd.GeoDataFrame(sn, geometry=gpd.points_from_xy(sn.longitude, sn.latitude), crs=4326)
poly = gpd.read_file(REPO / "data/raw/gagesii/boundaries/extracted/boundaries-shapefiles-by-aggeco/bas_ref_all.shp")
poly = poly[poly.GAGE_ID.isin(c.STAID)] if "GAGE_ID" in poly else poly
print("polygon columns:", poly.columns.tolist(), len(poly))
idcol = "GAGE_ID" if "GAGE_ID" in poly else poly.columns[0]
poly = poly.to_crs(4326)
j = gpd.sjoin(sn, poly[[idcol, "geometry"]], predicate="within")
n_in = j.groupby(idcol).size().rename("snotel_inside")
n_in_old = j[j.begin <= pd.Timestamp("1992-10-01")].groupby(idcol).size().rename("snotel_inside_pre1993")
c = c.merge(n_in, left_on="STAID", right_index=True, how="left").merge(n_in_old, left_on="STAID", right_index=True, how="left")
c[["snotel_inside", "snotel_inside_pre1993"]] = c[["snotel_inside", "snotel_inside_pre1993"]].fillna(0).astype(int)
c["has_polygon"] = c.STAID.isin(poly[idcol])
c["pilot"] = c.STAID.isin(PILOT)
c["selected"] = c.years_with_data >= 40

print("\nFUNNEL")
for k, v in funnel:
    print(f"  {v:5d}  {k}")
print(f"  {len(c):5d}  Columbia, area, flow (carried forward)")
print(f"  {int(c.has_polygon.sum()):5d}  of those have a GAGES-II polygon")
for thr in (30, 35, 40):
    a = (c.years_with_data >= thr)
    b = a & (c.snotel_inside >= 1)
    d = (c.span_since_wy1982_yr >= thr) & (c.snotel_inside >= 1)
    print(f"  >= {thr} yr with data: {int(a.sum()):4d};  + >=1 SNOTEL inside: {int(b.sum()):4d};  same using record since WY1982 (upper bound): {int(d.sum()):4d}")
f = c[c.selected]
print(f"\nselected (>= 40 yr with data): {len(f)}; with >= 1 SNOTEL inside: {int((f.snotel_inside >= 1).sum())}")
print("selected by state:", f.STATE.value_counts().to_dict())
print("selected by HUC4:", f.huc4.value_counts().sort_index().to_dict())
print("pilot basins among selected:", sorted(set(f.STAID) & PILOT))
print("selected still reporting (end >= 2025):", int((f.end >= "2025-01-01").sum()))
print("selected by area band:", pd.cut(f.DRAIN_SQKM, [50, 200, 500, 1000, 2000]).value_counts().sort_index().to_dict())
cols = ["STAID", "STANAME", "STATE", "huc4", "DRAIN_SQKM", "HCDN_2009", "begin", "end", "years_with_data", "span_since_wy1982_yr", "snotel_inside", "snotel_inside_pre1993", "has_polygon", "pilot", "selected"]
c[cols].sort_values(["huc4", "STAID"]).to_csv(OUT / "columbia_candidates.csv", index=False)
print("\nwrote", OUT / "columbia_candidates.csv")
