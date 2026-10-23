"""Shared fixtures and builders for the fallsoil Milestone 1 test suite.

No live network in the default run: every HTTP call is intercepted by `responses`.
Tests marked `live` are skipped unless selected with `-m live`.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import responses as responses_lib
import xarray as xr

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_GPKG = REPO_ROOT / "data" / "raw" / "basins" / "pilot_basins_gagesii.gpkg"

CELL = 1.0 / 24.0  # gridMET resolution in degrees


# --------------------------------------------------------------------------- live gating
def pytest_collection_modifyitems(config, items):
    markexpr = config.getoption("-m") or ""
    if "live" in markexpr and "not live" not in markexpr:
        return
    skip_live = pytest.mark.skip(reason="live network test; run with -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


# --------------------------------------------------------------------------- environment
@pytest.fixture(autouse=True)
def _fast_http(monkeypatch):
    """Retry backoff is 0 s in tests (documented env override in INTERFACE.md)."""
    monkeypatch.setenv("FALLSOIL_HTTP_BACKOFF_S", "0")


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "fallsoil_data"
    d.mkdir()
    monkeypatch.setenv("FALLSOIL_DATA_DIR", str(d))
    return d


@pytest.fixture
def data_dir_with_basins(data_dir) -> Path:
    """data_dir with the real GAGES-II pilot polygons available at raw/basins/."""
    dest = data_dir / "raw" / "basins"
    dest.mkdir(parents=True)
    shutil.copy(REAL_GPKG, dest / REAL_GPKG.name)
    return data_dir


@pytest.fixture
def mocked():
    """Intercepts every `requests` call. Unmatched URLs raise ConnectionError."""
    with responses_lib.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


def all_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def parquet_files(root: Path) -> list[Path]:
    return [p for p in all_files(root) if p.suffix == ".parquet"]


# --------------------------------------------------------------------------- URL patterns
USGS_URL = re.compile(r"^https://waterservices\.usgs\.gov/nwis/dv")
AWDB_URL = re.compile(r"^https://wcc\.sc\.egov\.usda\.gov/awdbRestApi/services/v1/data")
GRIDMET_URL = re.compile(r"^http://thredds\.northwestknowledge\.net:8080/thredds/ncss/agg_met_")


# --------------------------------------------------------------------------- USGS builders
def make_rdb(site: str, rows: list[tuple[str, str, str]], *, ts: str = "113793") -> str:
    """rows = (YYYY-MM-DD, value-as-text, qualifier-text). Mirrors real NWIS dv RDB."""
    col = f"{ts}_00060_00003"
    head = (
        "# ---------------------------------- WARNING ----------------------------------------\n"
        "# Some of the data that you have obtained from this U.S. Geological Survey database may not\n"
        "# have received Director's approval.\n"
        "#\n"
        f"#    USGS {site} SYNTHETIC TEST SITE\n"
        "#\n"
        "#     A  Approved for publication -- Processing and review completed.\n"
        "#     e  Value has been estimated.\n"
        "#     P  Provisional data subject to revision.\n"
        "#\n"
        f"agency_cd\tsite_no\tdatetime\t{col}\t{col}_cd\n"
        "5s\t15s\t20d\t14n\t10s\n"
    )
    body = "".join(f"USGS\t{site}\t{d}\t{v}\t{q}\n" for d, v, q in rows)
    return head + body


def default_rdb_rows(n=10, start="2020-09-28"):
    days = pd.date_range(start, periods=n, freq="D")
    return [(d.strftime("%Y-%m-%d"), f"{10.0 + i}", "A") for i, d in enumerate(days)]


# --------------------------------------------------------------------------- AWDB builders
def awdb_series(code, values, *, unit, depth_in=None, start="2020-10-01"):
    days = pd.date_range(start, periods=len(values), freq="D")
    height_depth = None if depth_in is None else {"value": -depth_in, "unitCode": "in"}
    return {
        "stationElement": {
            "elementCode": code,
            "ordinal": 1,
            "heightDepth": height_depth,
            "durationName": "DAILY",
            "dataPrecision": 1,
            "storedUnitCode": unit,
            "originalUnitCode": unit,
            "beginDate": "1990-01-01 00:00",
            "endDate": "2100-01-01 00:00",
            "derivedData": False,
        },
        "values": [
            ({"date": d.strftime("%Y-%m-%d"), "value": v} if v is not None
             else {"date": d.strftime("%Y-%m-%d")})
            for d, v in zip(days, values)
        ],
    }


def awdb_payload(triplet, series: list[dict]) -> str:
    return json.dumps([{"stationTriplet": triplet, "data": series}])


# --------------------------------------------------------------------------- gridMET builders
def grid_axes(lat0_edge, lon0_edge, n_lat, n_lon, *, descending_lat=False):
    """Cell centres for a 1/24-degree grid whose SW corner edge is (lat0_edge, lon0_edge)."""
    lat = lat0_edge + CELL * (np.arange(n_lat) + 0.5)
    lon = lon0_edge + CELL * (np.arange(n_lon) + 0.5)
    if descending_lat:
        lat = lat[::-1]
    return lat, lon


def make_grid(values_fn, lat, lon, n_days=3, start="2021-03-01", name="precipitation_amount",
              units="mm") -> xr.DataArray:
    days = pd.date_range(start, periods=n_days, freq="D")
    data = np.empty((n_days, len(lat), len(lon)))
    for t in range(n_days):
        for i, la in enumerate(lat):
            for j, lo in enumerate(lon):
                data[t, i, j] = values_fn(t, i, j, la, lo)
    return xr.DataArray(data, coords={"day": days, "lat": lat, "lon": lon},
                        dims=("day", "lat", "lon"), name=name, attrs={"units": units})


def grid_to_netcdf_bytes(da: xr.DataArray) -> bytes:
    ds = da.to_dataset(name=da.name)
    ds.attrs["note1"] = "synthetic gridMET-like subset for tests"
    ds.attrs["note3"] = "prose only: provisional data may change (no numeric marker, as in real files)"
    return bytes(ds.to_netcdf())


def bounds_to_polygon(south, west, north, east):
    from shapely.geometry import box
    return box(west, south, east, north)
