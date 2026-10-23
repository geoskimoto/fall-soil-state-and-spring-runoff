"""Phase 0 (exploratory): basin polygon, SNOTEL coverage, and flow completeness per candidate gauge.

Raw API responses are saved under the output dir (git-ignored); nothing is overwritten.
Usage: phase0_screen_basins.py OUTDIR
"""
import io
import json
import sys
from pathlib import Path

import pandas as pd
import requests
from shapely.geometry import Point, shape

CANDIDATES = {
    "14226500": "Cowlitz at Packwood (maritime)",
    "14161500": "Lookout Cr (maritime alt)",
    "12457000": "Wenatchee at Plain (transition)",
    "12452800": "Entiat nr Ardenvoir (transition)",
    "13336500": "Selway nr Lowell (continental)",
    "13309220": "MF Salmon (continental)",
    "13331500": "Minam (alternate)",
}
STATES = ["WA", "OR", "ID", "MT"]
BUFFER_DEG = 0.1  # ~10 km, to list near-basin sites

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)


def cached_get(url, path, **kw):
    if not path.exists():
        r = None
        for attempt in range(4):  # AWDB intermittently returns 500; NLDI sometimes times out
            try:
                r = requests.get(url, timeout=60, headers={"User-Agent": "curl/8.5.0", "Accept": "*/*"}, **kw)
            except requests.exceptions.Timeout:
                continue
            if r.status_code < 500:
                break
        if r is None:
            raise RuntimeError(f"timed out after retries: {url}")
        r.raise_for_status()
        path.write_bytes(r.content)
    return path.read_bytes()


# --- SNOTEL stations (all four states) ---
stations = []
for st in STATES:
    raw = cached_get(
        # AWDB rejects percent-encoded triplets, so the query string is built by hand
        f"https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/stations?stationTriplets=*:{st}:SNTL"
        "&activeOnly=false",
        out / f"awdb_{st}_plain.json",
    )
    stations += json.loads(raw)


def with_elements(lst):
    """Statewide element listings exceed the API limit, so fetch elements for only the stations needed."""
    if not lst:
        return []
    trip = ",".join(s["stationTriplet"] for s in lst)
    raw = cached_get(
        f"https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/stations?stationTriplets={trip}"
        "&activeOnly=false&returnStationElements=true",
        out / f"awdb_elems_{abs(hash(trip))}.json",
    )
    return json.loads(raw)


def elem_begin(stn, code):
    b = [e["beginDate"] for e in stn.get("stationElements", []) if e["elementCode"] == code]
    return min(b)[:10] if b else None


rows = []
for site, role in CANDIDATES.items():
    try:
        raw = cached_get(
            f"https://api.water.usgs.gov/nldi/linked-data/nwissite/USGS-{site}/basin",
            out / f"basin_{site}.geojson",
            params={"simplified": "true"},
        )
        poly = shape(json.loads(raw)["features"][0]["geometry"])
    except RuntimeError as exc:  # NLDI is flaky for some sites; keep going and flag it
        print(f"WARNING basin polygon unavailable for {site}: {exc}", file=sys.stderr)
        poly = None
    inside, near = [], []
    for s in stations if poly is not None else []:
        p = Point(s["longitude"], s["latitude"])
        if poly.contains(p):
            inside.append(s)
        elif poly.buffer(BUFFER_DEG).contains(p):
            near.append(s)
    inside, near = with_elements(inside), with_elements(near)
    fmt = lambda lst: [  # noqa: E731
        f'{s["name"]}({int(s["elevation"])}ft WTEQ:{elem_begin(s,"WTEQ")} SMS:{elem_begin(s,"SMS")})' for s in lst
    ]

    # --- daily mean flow completeness by water year ---
    dv = cached_get(
        "https://waterservices.usgs.gov/nwis/dv/",
        out / f"dv_{site}.rdb",
        params={"format": "rdb", "sites": site, "parameterCd": "00060", "statCd": "00003", "startDt": "1900-01-01"},
    ).decode()
    lines = [l for l in dv.splitlines() if not l.startswith("#")]
    df = pd.read_csv(io.StringIO("\n".join([lines[0]] + lines[2:])), sep="\t", dtype=str)
    vcol = [c for c in df.columns if c.endswith("_00060_00003")][0]
    df["date"] = pd.to_datetime(df["datetime"])
    df = df[pd.to_numeric(df[vcol], errors="coerce").notna()]
    df["wy"] = df["date"].dt.year + (df["date"].dt.month >= 10)
    cnt = df.groupby("wy").size()
    full = cnt[cnt >= 350]
    rows.append(
        {
            "site": site,
            "role": role,
            "area_km2": round(poly.area * 111.0 * 111.0 * 0.69, 0) if poly is not None else None,  # rough planar estimate
            "n_snotel_inside": len(inside),
            "n_snotel_near": len(near),
            "complete_wy": len(full),
            "wy_range": f"{full.index.min()}-{full.index.max()}" if len(full) else None,
            "wy_since_1982_complete": int((full.index >= 1982).sum()),
            "_inside": fmt(inside),
            "_near": fmt(near),
        }
    )

res = pd.DataFrame(rows)
res.drop(columns=["_inside", "_near"]).to_csv(out / "basin_screening_summary.csv", index=False)
pd.set_option("display.width", 220, "display.max_colwidth", 60)
print(res.drop(columns=["_inside", "_near"]).to_string(index=False))
for r in rows:
    print(f"\n{r['site']} {r['role']}\n  inside: {r['_inside']}\n  near:   {r['_near']}")
