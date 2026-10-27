"""Extract the expansion-basin polygons from the GAGES-II boundary shapefiles into one small GeoPackage.

Usage: extract_expansion_basins.py CANDIDATES_CSV SHAPEFILE_DIR OUT_GPKG
The gauges are the selected, non-pilot rows of columbia_candidates.csv (scripts/phase1_count_candidates.py).
Source shapefiles are untrusted downloads: run with `python -I` from outside their directory.
"""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

csv, src, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
cand = pd.read_csv(csv, dtype={"STAID": str})
cand["STAID"] = cand["STAID"].str.zfill(8)
want = cand[(cand["selected"].astype(str).str.lower() == "true") & (cand["pilot"].astype(str).str.lower() != "true")]
area = want.set_index("STAID")["DRAIN_SQKM"]

parts = []
for name in ("bas_ref_all", "bas_nonref_WestMnts"):
    g = gpd.read_file(src / f"{name}.shp")
    id_col = next(c for c in g.columns if c.upper() == "GAGE_ID")
    g = g[g[id_col].isin(area.index)].rename(columns={id_col: "site_no"})
    g["source_file"] = f"{name}.shp"
    parts.append(g)
    print(name, "crs:", g.crs.name if g.crs else None, "found:", len(g))
allg = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=parts[0].crs)
dup = allg["site_no"][allg["site_no"].duplicated()].tolist()
missing = sorted(set(area.index) - set(allg["site_no"]))
if dup or missing:
    sys.exit(f"Refusing to write: duplicated polygons {sorted(set(dup))}, missing polygons {missing}.")

allg["area_km2_albers"] = (allg.geometry.area / 1e6).round(1)
allg["drain_sqkm"] = allg["site_no"].map(area)
allg["area_ratio"] = (allg["area_km2_albers"] / allg["drain_sqkm"]).round(3)
allg = allg.to_crs(4326)
out.parent.mkdir(parents=True, exist_ok=True)
allg[["site_no", "source_file", "area_km2_albers", "drain_sqkm", "area_ratio", "geometry"]].to_file(out, driver="GPKG")
print(f"wrote {len(allg)} polygons to {out}")
print("polygon area / DRAIN_SQKM: min %.2f median %.2f max %.2f" % (allg.area_ratio.min(), allg.area_ratio.median(), allg.area_ratio.max()))
odd = allg[(allg.area_ratio < 0.9) | (allg.area_ratio > 1.1)]
print("gauges where the two areas differ by more than 10%%: %s" % (sorted(odd.site_no) or "none"))
