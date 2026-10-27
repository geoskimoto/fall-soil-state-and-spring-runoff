"""Registry of the Columbia-basin expansion basins, read from the Phase 1 candidate screen.

The six pilots stay in `fallsoil.basins` (list_basins / get_basin are unchanged). The expansion basins are the rows of
`columbia_candidates.csv` (scripts/phase1_count_candidates.py) with `selected` true and `pilot` false. They share the
`Basin` type, so the fetchers accept them directly; their polygons live in a separate GeoPackage
(scripts/extract_expansion_basins.py) and their setting stays `unclassified` until it is assigned from the data.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from fallsoil import config, manifest
from fallsoil.basins import Basin
from fallsoil.errors import ConfigError

EXPANSION_POLYGON_FILE = "expansion_basins_gagesii.gpkg"
POLYGON_SOURCE = "gagesii_polygon"
UNCLASSIFIED = "unclassified"
CANDIDATES_RELPATH = Path("raw") / "screening" / "phase1" / "columbia_candidates.csv"
REQUIRED_COLUMNS = ("STAID", "STANAME", "DRAIN_SQKM", "pilot", "selected")


def candidates_path() -> Path:
    return config.get_data_dir() / CANDIDATES_RELPATH


def load_expansion_basins(candidates_csv: Path | str | None = None) -> list[Basin]:
    """Selected non-pilot gauges from the candidate screen, sorted by gauge id."""
    path = Path(candidates_csv) if candidates_csv is not None else candidates_path()
    if not path.is_file():
        raise ConfigError(
            f"Candidate screen not found at {path}. Run scripts/phase1_count_candidates.py to create columbia_candidates.csv."
        )
    df = pd.read_csv(path, dtype={"STAID": str})
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ConfigError(f"{path} is missing required column(s): {', '.join(missing)}.")
    df["STAID"] = df["STAID"].str.strip().str.zfill(8)
    flag = lambda col: df[col].astype(str).str.strip().str.lower() == "true"  # noqa: E731
    rows = df[flag("selected") & ~flag("pilot")].sort_values("STAID")
    dup = rows.loc[rows["STAID"].duplicated(), "STAID"].tolist()
    if dup:
        raise ConfigError(f"{path} lists gauge(s) more than once: {', '.join(sorted(set(dup)))}.")
    basins = []
    for r in rows.itertuples(index=False):
        area = float(r.DRAIN_SQKM)
        if not math.isfinite(area) or area <= 0:
            raise ConfigError(f"Gauge {r.STAID} has no usable drainage area (DRAIN_SQKM = {r.DRAIN_SQKM!r}) in {path}.")
        basins.append(
            Basin(r.STAID, str(r.STANAME), UNCLASSIFIED, r.STAID, polygon_file=EXPANSION_POLYGON_FILE, area_sqkm=area)
        )
    return basins


def record_expansion_polygon_provenance(basins: list[Basin]) -> int:
    """Record each polygon's checksum in the manifest; returns the number newly appended."""
    appended = 0
    for basin in basins:
        wkb = basin.polygon_wkb()
        if manifest.record_fetch(
            source=POLYGON_SOURCE, url=basin.polygon_path.as_uri(), params={"site_no": basin.id}, content=wkb, rows=1
        ):
            appended += 1
    return appended
