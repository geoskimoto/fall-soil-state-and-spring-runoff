"""Phase 0 (exploratory): pull USGS metadata + daily-flow record extent for candidate gauges.

Writes raw responses to data/raw/screening/ (git-ignored) and prints a summary table.
"""
import sys
from pathlib import Path

import pandas as pd
from dataretrieval import nwis

CANDIDATES = {
    "14232500": "Cispus (maritime)",
    "12457000": "Wenatchee (transition)",
    "12452500": "Entiat (transition)",
    "13336500": "Selway (continental)",
    "13309220": "MF Salmon (continental)",
    "13331500": "Minam (alternate)",
}
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

info, _ = nwis.get_info(sites=list(CANDIDATES), siteOutput="expanded")
info.to_csv(OUT / "site_info_raw.csv", index=False)

rows = []
for site in CANDIDATES:
    cat, _ = nwis.get_info(sites=site, seriesCatalogOutput=True, parameterCd="00060")
    dv = cat[cat["data_type_cd"] == "dv"]
    s = info[info["site_no"] == site].iloc[0]
    rows.append(
        {
            "site_no": site,
            "role": CANDIDATES[site],
            "name": s["station_nm"],
            "da_sqmi": s.get("drain_area_va"),
            "elev_ft": s.get("alt_va"),
            "dv_begin": dv["begin_date"].min() if len(dv) else None,
            "dv_end": dv["end_date"].max() if len(dv) else None,
            "dv_count": dv["count_nu"].max() if len(dv) else None,
        }
    )
df = pd.DataFrame(rows)
df.to_csv(OUT / "gauge_summary.csv", index=False)
pd.set_option("display.width", 200, "display.max_colwidth", 45)
print(df.to_string(index=False))
