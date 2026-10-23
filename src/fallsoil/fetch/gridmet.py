"""gridMET daily forcing -> area-weighted basin means.

A bounding-box subset is requested from the Northwest Knowledge Network THREDDS NetCDF Subset
Service and reduced to one value per day using each ~4 km (1/24 degree) cell's fractional overlap
with the basin polygon. Cell-centre inclusion is too coarse for small basins (Lookout Cr. spans only
about four cells). The server is plain HTTP, so the response is validated (NetCDF structure, variable,
dimensions, units) rather than trusted.

gridMET days are approximately calendar days ending at midnight Mountain Standard Time. Files carry
no numeric marker for provisional data, so any day within PROVISIONAL_WINDOW_DAYS of the retrieval
date is flagged provisional.
"""
from __future__ import annotations

import logging
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
import xarray as xr

from fallsoil import config, manifest
from fallsoil.basins import Basin, get_basin
from fallsoil.errors import EmptyResultError, MalformedPayloadError, ZeroOverlapError
from fallsoil.fetch import http
from fallsoil.fetch._common import as_date, cache_raw, write_parquet

log = logging.getLogger(__name__)

SOURCE = "gridmet"
BASE_URL = "http://thredds.northwestknowledge.net:8080/thredds/ncss/agg_met_{var}_1979_CurrentYear_CONUS.nc"
CELL = 1.0 / 24.0  # gridMET resolution in degrees
PROVISIONAL_WINDOW_DAYS = 60
MIN_TOTAL_OVERLAP_DEG2 = 1e-12
TIME_CONVENTION = (
    "Each 'day' is approximately the calendar day ending at midnight Mountain Standard Time "
    "(MST, UTC-7), i.e. 07:00 UTC on the following calendar day (gridMET file note)."
)
# variable -> (file variable, expected file units, output column, output unit)
VARIABLES = {
    "pr": ("precipitation_amount", "mm", "precip_mm", "mm"),
    "tmmx": ("daily_maximum_temperature", "K", "temp_max_c", "degC"),
    "tmmn": ("daily_minimum_temperature", "K", "temp_min_c", "degC"),
}


def kelvin_to_celsius(x):
    return x - 273.15


# ---------------------------------------------------------------- area weighting
def cell_weights(lat, lon, polygon) -> xr.DataArray:
    """Fractional-overlap weights (dims lat, lon; non-negative; sum to 1) of 1/24-degree cells."""
    lat = np.asarray(lat, dtype="float64")
    lon = np.asarray(lon, dtype="float64")
    if polygon is None or polygon.is_empty:
        raise ZeroOverlapError("The basin polygon is empty, so it cannot overlap the data grid.")
    lon_grid, lat_grid = np.meshgrid(lon, lat)
    half = CELL / 2.0
    cells = shapely.box(lon_grid - half, lat_grid - half, lon_grid + half, lat_grid + half)
    area = shapely.area(shapely.intersection(cells, polygon))
    total = float(area.sum())
    if total <= MIN_TOTAL_OVERLAP_DEG2:
        raise ZeroOverlapError(
            "The basin does not overlap the returned data grid. The server may have returned the wrong "
            "region, or the grid may be too small to cover the basin."
        )
    return xr.DataArray(area / total, coords={"lat": lat, "lon": lon}, dims=("lat", "lon"), name="weight")


def area_weighted_mean(da: xr.DataArray, polygon) -> xr.DataArray:
    """Mean of `da` over lat/lon weighted by each cell's fractional overlap with `polygon`."""
    weights = cell_weights(da["lat"].values, da["lon"].values, polygon)
    return da.weighted(weights).mean(dim=("lat", "lon"))


# ---------------------------------------------------------------- response handling
def _open_netcdf(body: bytes, variable: str) -> xr.Dataset:
    """Open NetCDF bytes (classic or HDF5-based) fully into memory; any failure is a malformed payload."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gridmet.nc"
            path.write_bytes(body)
            with xr.open_dataset(path) as ds:
                return ds.load()
    except Exception as exc:  # xarray/netCDF4/scipy raise many types for corrupt files
        raise MalformedPayloadError(
            f"The gridMET server did not return a readable NetCDF file for variable {variable}."
        ) from exc


def _extract(ds: xr.Dataset, file_var: str, units: str) -> xr.DataArray:
    if file_var not in ds.data_vars:
        raise MalformedPayloadError(f"The gridMET response is missing the expected variable {file_var!r}.")
    da = ds[file_var]
    if not {"day", "lat", "lon"} <= set(da.dims):
        raise MalformedPayloadError(
            f"gridMET variable {file_var} has dimensions {da.dims}; expected day, lat and lon."
        )
    found = da.attrs.get("units")
    if found != units:
        raise MalformedPayloadError(
            f"gridMET variable {file_var} has unit {found!r}; expected {units!r}. Refusing to guess a conversion."
        )
    if da.sizes["day"] == 0:
        raise EmptyResultError("gridMET returned no days for the requested period.")
    return da


def _retrieval_date(now) -> date:
    if now is None:
        return datetime.now(timezone.utc).date()
    if isinstance(now, datetime):
        return (now.astimezone(timezone.utc) if now.tzinfo else now).date()
    if isinstance(now, date):
        return now
    raise TypeError(f"now must be a date, datetime or None, got {type(now).__name__}")


def _resolve_basin(basin) -> Basin:
    return basin if isinstance(basin, Basin) else get_basin(basin)


# ---------------------------------------------------------------- fetch
def fetch_basin_mean(basin, variable: str, start, end, *, now=None) -> Path:
    """Fetch a gridMET variable for `basin`, reduce to area-weighted daily means, write parquet."""
    if variable not in VARIABLES:
        raise ValueError(f"unknown gridMET variable {variable!r}; supported: {', '.join(VARIABLES)}")
    file_var, units, column, out_unit = VARIABLES[variable]
    basin = _resolve_basin(basin)
    start_d, end_d = as_date(start, "start"), as_date(end, "end")
    if end_d < start_d:
        raise ValueError("end must not be before start")
    retrieved = _retrieval_date(now)

    polygon = basin.polygon()
    minx, miny, maxx, maxy = polygon.bounds
    margin = CELL  # one cell of padding so every cell overlapping the polygon is returned
    params = {
        "var": file_var,
        "north": f"{maxy + margin:.4f}", "south": f"{miny - margin:.4f}",
        "east": f"{maxx + margin:.4f}", "west": f"{minx - margin:.4f}",
        "horizontalStride": "1",
        "time_start": f"{start_d.isoformat()}T00:00:00Z", "time_end": f"{end_d.isoformat()}T00:00:00Z",
        "accept": "netcdf",
    }
    url = BASE_URL.format(var=variable)
    body = http.get(url, params)

    ds = _open_netcdf(body, variable)
    da = _extract(ds, file_var, units)
    means = area_weighted_mean(da, polygon)  # raises ZeroOverlapError if the grid misses the basin
    values = means.values.astype("float64")
    if variable != "pr":
        values = kelvin_to_celsius(values)

    dates = pd.DatetimeIndex(pd.to_datetime(means["day"].values)).normalize()
    age_days = np.array([(retrieved - d.date()).days for d in dates])
    frame = pd.DataFrame({"date": dates, column: values, "provisional": age_days <= PROVISIONAL_WINDOW_DAYS})
    frame = frame.sort_values("date", kind="stable").reset_index(drop=True)
    prov_dates = frame.loc[frame["provisional"], "date"]

    stem = f"gridmet_{basin.id}_{variable}_{start_d.isoformat()}_{end_d.isoformat()}"
    raw_path = cache_raw(config.raw_dir("gridmet"), stem, body, ".nc")
    metadata = {
        "source": SOURCE, "basin": basin.id, "variable": variable, "time_convention": TIME_CONVENTION,
        "units": {column: out_unit},
        "aggregation": "area_weighted_fractional_overlap",
        "retrieval_date": retrieved.isoformat(),
        "provisional_window_days": PROVISIONAL_WINDOW_DAYS,
        "n_provisional_days": int(len(prov_dates)),
        "provisional_start": prov_dates.min().date().isoformat() if len(prov_dates) else None,
        "provisional_end": prov_dates.max().date().isoformat() if len(prov_dates) else None,
        "url": url, "params": params, "raw_file": raw_path.name, "rows": len(frame),
    }
    out = write_parquet(frame, config.interim_dir("gridmet") / f"{stem}.parquet", metadata)
    manifest.record_fetch(source=SOURCE, url=url, params=params, content=body, rows=len(frame))
    return out
