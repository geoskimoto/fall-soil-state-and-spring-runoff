"""ERA5-Land downloads that CDS delivers as a zip of one NetCDF per variable (seen 2026-10-28).

Written alongside the fix in fallsoil.fetch.era5land, so not an independent specification.
"""
from __future__ import annotations

import io
import zipfile

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from fallsoil.errors import MalformedPayloadError
from fallsoil.fetch import era5land

VALUES = {"swvl1": 0.30, "swvl2": 0.25, "swvl3": 0.20, "sd": 0.10}  # sd in m of water equivalent
MEMBER = {"swvl1": "volumetric_soil_water_layer_1", "swvl2": "volumetric_soil_water_layer_2",
          "swvl3": "volumetric_soil_water_layer_3", "sd": "snow_depth_water_equivalent"}


def _cds_zip(area, days: int = 3) -> bytes:
    """A zip shaped like the CDS delivery: one constant-valued NetCDF per variable on a 0.1-degree grid over `area`."""
    north, west, south, east = area
    lat = np.round(np.arange(north, south - 0.05, -0.1), 1)
    lon = np.round(np.arange(west, east + 0.05, 0.1), 1)
    time = pd.date_range("1981-04-01", periods=days, freq="D")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for var, value in VALUES.items():
            data = np.full((days, lat.size, lon.size), value)
            ds = xr.Dataset({var: (("valid_time", "latitude", "longitude"), data)},
                            coords={"valid_time": time, "latitude": lat, "longitude": lon, "number": 0})
            zf.writestr(f"{MEMBER[var]}_0_daily-mean.nc", ds.to_netcdf())
    return buf.getvalue()


class FakeClient:
    def __init__(self, body: bytes):
        self.body, self.calls = body, 0

    def retrieve(self, dataset, request, target):
        self.calls += 1
        with open(target, "wb") as fh:
            fh.write(self.body)


def test_multi_file_zip_is_cached_byte_identical_and_reused(data_dir_with_basins):
    area = era5land.union_area()
    body = _cds_zip(area)
    client = FakeClient(body)
    path = era5land.fetch_chunk(1981, 2, area, client=client)
    assert path.suffix == ".zip"
    assert path.read_bytes() == body
    assert era5land.cached_chunk(1981, 2, area) == path
    assert era5land.fetch_chunk(1981, 2, area, client=client) == path
    assert client.calls == 1


def test_basin_means_merge_the_zip_members(data_dir_with_basins):
    area = era5land.union_area()
    path = era5land.fetch_chunk(1981, 2, area, client=FakeClient(_cds_zip(area)))
    out = era5land.basin_means([path], "14161500")
    assert list(out["date"]) == list(pd.date_range("1981-04-01", periods=3, freq="D"))
    assert out["sm_l1_m3m3"].tolist() == pytest.approx([0.30] * 3)
    assert out["sm_l3_m3m3"].tolist() == pytest.approx([0.20] * 3)
    assert out["swe_mm"].tolist() == pytest.approx([100.0] * 3)
    assert out["sm_0_100cm_m3m3"].tolist() == pytest.approx([(0.30 * 7 + 0.25 * 21 + 0.20 * 72) / 100] * 3)


def test_zip_without_netcdf_is_rejected_and_nothing_cached(data_dir_with_basins):
    area = era5land.union_area()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no data")
    with pytest.raises(MalformedPayloadError):
        era5land.fetch_chunk(1981, 2, area, client=FakeClient(buf.getvalue()))
    assert era5land.cached_chunk(1981, 2, area) is None
