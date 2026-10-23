"""Extract pilot-basin polygons from GAGES-II boundary shapefiles into one small GeoPackage.

Usage: extract_pilot_basins.py SHAPEFILE_DIR OUT_GPKG
Source shapefiles are untrusted downloads: run with `python -I` from outside their directory.
"""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

PILOT = {
    "14161500": "Lookout Cr",
    "14159200": "SF McKenzie abv Cougar L (alt)",
    "12452800": "Entiat",
    "12457000": "Wenatchee at Plain",
    "13336500": "Selway",
    "13331500": "Minam",
    "13309220": "MF Salmon (alt)",
}
src, out = Path(sys.argv[1]), Path(sys.argv[2])
parts = []
for name in ("bas_ref_all", "bas_nonref_WestMnts"):
    g = gpd.read_file(src / f"{name}.shp")
    id_col = next(c for c in g.columns if c.upper() == "GAGE_ID")
    g = g[g[id_col].isin(PILOT)].rename(columns={id_col: "site_no"})
    g["source_file"] = f"{name}.shp"
    parts.append(g)
    print(name, "crs:", g.crs.name if g.crs else None, "found:", sorted(g["site_no"]))
allg = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=parts[0].crs)
allg["role"] = allg["site_no"].map(PILOT)
allg["area_km2_albers"] = (allg.geometry.area / 1e6).round(1)
allg = allg.to_crs(4326)
out.parent.mkdir(parents=True, exist_ok=True)
allg[["site_no", "role", "source_file", "area_km2_albers", "geometry"]].to_file(out, driver="GPKG")
print(allg[["site_no", "role", "area_km2_albers"]].to_string(index=False))
missing = set(PILOT) - set(allg["site_no"])
print("missing:", sorted(missing))
