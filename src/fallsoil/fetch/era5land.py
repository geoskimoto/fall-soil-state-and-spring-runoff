"""ERA5-Land daily soil moisture and snow water equivalent from the Copernicus Climate Data Store.

Requests are one calendar year over one bounding box (`union_area`), cached byte-identical under
`<data>/raw/era5land/` and recorded in the manifest. Basin means are computed offline from the cached grids by
fractional-overlap area weighting on the 0.1-degree ERA5-Land grid. Daily statistics use UTC-08:00 (PST) days to match
SNOTEL. Credentials live in ~/.cdsapirc and are never read or logged here.
"""
from __future__ import annotations

import io
import logging
import math
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
import xarray as xr

from fallsoil import config, manifest
from fallsoil.basins import Basin, get_basin, list_basins
from fallsoil.errors import EmptyResultError, FetchError, MalformedPayloadError, ZeroOverlapError
from fallsoil.fetch._common import cache_raw, write_parquet

log = logging.getLogger(__name__)

SOURCE = "era5land_daily"
DATASET = "derived-era5-land-daily-statistics"
CELL = 0.1  # ERA5-Land native grid, degrees
# Layer 4 (100-289 cm) is omitted: the CDS cost limit for this dataset (about 400 fields = variables x days) allows
# 4 variables x 3 months per request, and the study uses the 0-100 cm column.
REQUEST_VARIABLES = (
    "volumetric_soil_water_layer_1", "volumetric_soil_water_layer_2",
    "volumetric_soil_water_layer_3", "snow_depth_water_equivalent",
)
QUARTERS = {1: (1, 2, 3), 2: (4, 5, 6), 3: (7, 8, 9), 4: (10, 11, 12)}
# file variable -> (output column, unit)
FILE_VARIABLES = {
    "swvl1": ("sm_l1_m3m3", "m3/m3"), "swvl2": ("sm_l2_m3m3", "m3/m3"),
    "swvl3": ("sm_l3_m3m3", "m3/m3"),
    "sd": ("swe_mm", "mm"),  # file unit is m of water equivalent
}
LAYER_THICKNESS_CM = {"sm_l1_m3m3": 7.0, "sm_l2_m3m3": 21.0, "sm_l3_m3m3": 72.0}  # 0-7, 7-28, 28-100 cm
TIME_CONVENTION = (
    "Daily mean over 24 hourly values; each date is a calendar day in UTC-08:00 (PST, no daylight saving), matching SNOTEL."
)


def union_area(basins: list[Basin] | None = None) -> tuple[float, float, float, float]:
    """(north, west, south, east) bounding box of the basins plus one 0.1-degree cell, snapped outward to 0.1."""
    basins = basins or list_basins()
    bounds = [b.polygon().bounds for b in basins]
    west = math.floor((min(b[0] for b in bounds) - CELL) * 10) / 10
    south = math.floor((min(b[1] for b in bounds) - CELL) * 10) / 10
    east = math.ceil((max(b[2] for b in bounds) + CELL) * 10) / 10
    north = math.ceil((max(b[3] for b in bounds) + CELL) * 10) / 10
    return north, west, south, east


def _stem(year: int, quarter: int, area) -> str:
    n, w, s, e = area
    return f"era5land_daily_{year}Q{quarter}_N{n}_W{abs(w)}_S{s}_E{abs(e)}"


def cached_chunk(year: int, quarter: int, area) -> Path | None:
    """Path of an already-cached raw file for (year, quarter, area), or None."""
    raw = config.raw_dir("era5land")
    stem = _stem(year, quarter, area)
    hits = sorted([*raw.glob(f"{stem}_*.nc"), *raw.glob(f"{stem}_*.zip")])
    return hits[-1] if hits else None


def _raw_ext(body: bytes) -> str:
    """'.zip' or '.nc' for a CDS download. CDS now zips one NetCDF per variable; the zip is cached as delivered."""
    if body[:2] != b"PK":
        return ".nc"
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            names = [n for n in zf.namelist() if n.endswith(".nc")]
    except zipfile.BadZipFile as exc:
        raise MalformedPayloadError("ERA5-Land download is a damaged zip file.") from exc
    if not names:
        raise MalformedPayloadError("ERA5-Land download zip contains no NetCDF file.")
    return ".zip"


def _open_raw(path: Path) -> xr.Dataset:
    """Open a cached raw file in memory: a single NetCDF, or a CDS zip whose NetCDF members are merged."""
    if path.suffix != ".zip":
        return xr.open_dataset(path).load()
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(path) as zf:
        parts = []
        for name in sorted(n for n in zf.namelist() if n.endswith(".nc")):
            member = Path(tmp) / Path(name).name
            member.write_bytes(zf.read(name))
            with xr.open_dataset(member) as ds:
                parts.append(ds.load())
    if not parts:
        raise MalformedPayloadError(f"ERA5-Land file {path.name} contains no NetCDF file.")
    return xr.merge(parts, compat="override", join="exact")


def fetch_chunk(year: int, quarter: int, area=None, *, client=None) -> Path:
    """Download (or reuse) ERA5-Land daily means for one calendar quarter of `year` over `area`; returns the raw path."""
    if not isinstance(year, int) or not 1950 <= year <= 2100:
        raise ValueError(f"year must be an integer between 1950 and 2100, got {year!r}")
    if quarter not in QUARTERS:
        raise ValueError(f"quarter must be 1-4, got {quarter!r}")
    area = area or union_area()
    existing = cached_chunk(year, quarter, area)
    if existing is not None:
        return existing
    if client is None:
        import cdsapi  # imported lazily so the rest of the package works without the CDS client

        client = cdsapi.Client(quiet=True)
    request = {
        "variable": list(REQUEST_VARIABLES), "year": str(year),
        "month": [f"{m:02d}" for m in QUARTERS[quarter]], "day": [f"{d:02d}" for d in range(1, 32)],
        "daily_statistic": "daily_mean", "time_zone": "utc-08:00", "frequency": "1_hourly",
        "area": list(area), "data_format": "netcdf", "download_format": "unarchived",
    }
    try:
        with tempfile.TemporaryDirectory(dir=config.raw_dir("era5land")) as tmp:
            target = Path(tmp) / "download.bin"
            client.retrieve(DATASET, request, str(target))
            body = target.read_bytes()
            ext = _raw_ext(body)
    except FetchError:
        raise
    except Exception as exc:  # the CDS client raises many exception types; present one friendly error
        raise FetchError(f"ERA5-Land download for {year} Q{quarter} failed ({type(exc).__name__}: {str(exc)[-160:]!r}). "
                         "Check ~/.cdsapirc and the CDS licence acceptance, then re-run; finished years are cached.") from exc
    if not body:
        raise EmptyResultError(f"ERA5-Land returned an empty file for {year} Q{quarter}.")
    path = cache_raw(config.raw_dir("era5land"), _stem(year, quarter, area), body, ext)
    manifest.record_fetch(source=SOURCE, url=DATASET, params={**request, "area": list(area)}, content=body, rows=0)
    return path


# ---------------------------------------------------------------- basin means
def _time_name(ds: xr.Dataset) -> str:
    for name in ("valid_time", "time"):
        if name in ds.coords:
            return name
    raise MalformedPayloadError("ERA5-Land file has no time coordinate.")


def _weights(lat, lon, polygon) -> xr.DataArray:
    lon_g, lat_g = np.meshgrid(np.asarray(lon, float), np.asarray(lat, float))
    half = CELL / 2.0
    cells = shapely.box(lon_g - half, lat_g - half, lon_g + half, lat_g + half)
    area = shapely.area(shapely.intersection(cells, polygon))
    total = float(area.sum())
    if total <= 0:
        raise ZeroOverlapError("The basin does not overlap the ERA5-Land grid that was returned.")
    return xr.DataArray(area / total, coords={"latitude": lat, "longitude": lon}, dims=("latitude", "longitude"))


def basin_means(paths: list[Path], basin: Basin | str) -> pd.DataFrame:
    """Area-weighted daily basin means from cached raw files; columns date, sm_l1..l4_m3m3, sm_0_100cm_m3m3, swe_mm."""
    basin = get_basin(basin) if isinstance(basin, str) else basin
    polygon = basin.polygon()
    frames = []
    for path in sorted(paths):
        ds = _open_raw(path)
        try:
            tname = _time_name(ds)
            missing = [v for v in FILE_VARIABLES if v not in ds]
            if missing:
                raise MalformedPayloadError(f"ERA5-Land file {path.name} lacks variables {missing}.")
            w = _weights(ds["latitude"].values, ds["longitude"].values, polygon)
            cols = {}
            for var, (col, _) in FILE_VARIABLES.items():
                da = ds[var]
                if var == "sd":
                    da = da * 1000.0  # m of water equivalent -> mm
                cols[col] = da.weighted(w).mean(dim=("latitude", "longitude")).values.astype("float64")
            frame = pd.DataFrame({"date": pd.DatetimeIndex(ds[tname].values).normalize(), **cols})
        finally:
            ds.close()
        frames.append(frame)
    if not frames:
        raise EmptyResultError("No ERA5-Land files were given.")
    out = pd.concat(frames).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    thick = LAYER_THICKNESS_CM
    out["sm_0_100cm_m3m3"] = sum(out[c] * t for c, t in thick.items()) / sum(thick.values())
    return out


def write_basin_means(basin: Basin | str, paths: list[Path]) -> Path:
    basin = get_basin(basin) if isinstance(basin, str) else basin
    frame = basin_means(paths, basin)
    units = {col: unit for col, unit in FILE_VARIABLES.values()} | {"sm_0_100cm_m3m3": "m3/m3"}
    metadata = {"source": SOURCE, "basin": basin.id, "time_convention": TIME_CONVENTION, "units": units,
                "aggregation": "area_weighted_fractional_overlap_0.1deg",
                "sm_0_100cm": "thickness-weighted mean of layers 1-3 (7, 21, 72 cm)",
                "raw_files": [p.name for p in sorted(paths)], "rows": len(frame)}
    return write_parquet(frame, config.interim_dir("era5land") / f"era5land_{basin.id}.parquet", metadata)
