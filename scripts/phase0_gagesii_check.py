"""Phase 0 (exploratory): GAGES-II reference/regulation attributes for candidate gauges.

Usage: phase0_gagesii_check.py CONTERM_XLSX
"""
import sys

import pandas as pd

SITES = ["14226500", "14161500", "12457000", "12452800", "13336500", "13309220", "13331500"]
path = sys.argv[1]
xl = pd.ExcelFile(path)
print("sheets:", xl.sheet_names)
for sh in ("BasinID", "Bas_Classif", "HydroMod_Dams", "HydroMod_Other"):
    if sh not in xl.sheet_names:
        continue
    df = xl.parse(sh, dtype={"STAID": str})
    df["STAID"] = df["STAID"].str.zfill(8)
    sub = df[df["STAID"].isin(SITES)]
    print(f"\n== {sh}: {len(df)} rows, found {len(sub)}/{len(SITES)}")
    print(sub.T.to_string() if len(sub) else "none")
