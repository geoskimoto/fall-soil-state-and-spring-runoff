"""M2a: cfs -> mm/day conversion and Basin.drain_sqkm (interface: tests/INTERFACE_M2A.md)."""
import math

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings, strategies as st

from fallsoil.basins import get_basin, list_basins
from fallsoil.responses import M3_PER_FT3, cfs_to_mm_per_day, mm_per_day_to_cfs

FLOATS = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)
AREAS = st.floats(min_value=0.01, max_value=1e5, allow_nan=False, allow_infinity=False)


def test_conversion_factor_constant():
    assert M3_PER_FT3 == 0.0283168


def test_one_cfs_over_one_sqkm():
    # 0.0283168 m3/s * 86400 s = 2446.5715 m3 per day over 1e6 m2 = 2.4465715 mm
    assert cfs_to_mm_per_day(1.0, 1.0) == pytest.approx(2.4465715, rel=1e-6)


def test_lookout_creek_hand_calculation():
    # 100 cfs on 62.4241 km2: 100 * 2.4465715 / 62.4241 = 3.91929 mm/d
    assert cfs_to_mm_per_day(100.0, 62.4241) == pytest.approx(3.91929, rel=1e-5)


def test_zero_flow_is_zero():
    assert cfs_to_mm_per_day(0.0, 10.0) == 0.0


def test_series_keeps_index_and_propagates_nan():
    s = pd.Series([1.0, np.nan, 3.0], index=pd.date_range("2020-01-01", periods=3), name="flow_cfs")
    out = cfs_to_mm_per_day(s, 2.0)
    assert isinstance(out, pd.Series) and out.index.equals(s.index)
    assert np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(3 * 2.4465715 / 2.0, rel=1e-6)


def test_ndarray_input():
    out = cfs_to_mm_per_day(np.array([1.0, 2.0]), 1.0)
    assert out.shape == (2,) and out[1] == pytest.approx(2 * out[0])


@pytest.mark.parametrize("area", [0, 0.0, -1.0, float("nan"), float("inf"), -0.0])
def test_bad_area_raises_value_error(area):
    with pytest.raises(ValueError):
        cfs_to_mm_per_day(5.0, area)
    with pytest.raises(ValueError):
        mm_per_day_to_cfs(5.0, area)


@settings(max_examples=60, deadline=None)
@given(q=FLOATS, area=AREAS)
def test_round_trip(q, area):
    assert mm_per_day_to_cfs(cfs_to_mm_per_day(q, area), area) == pytest.approx(q, rel=1e-9, abs=1e-12)


@settings(max_examples=60, deadline=None)
@given(q=FLOATS, area=AREAS, factor=st.floats(min_value=0.1, max_value=50.0))
def test_linear_in_inverse_area(q, area, factor):
    # mm/d = k * cfs / area: scaling the area by `factor` divides the depth by `factor`
    assert cfs_to_mm_per_day(q, area * factor) == pytest.approx(cfs_to_mm_per_day(q, area) / factor, rel=1e-9, abs=1e-12)


@settings(max_examples=60, deadline=None)
@given(q=FLOATS, area=AREAS, c=st.floats(min_value=0.0, max_value=100.0))
def test_linear_in_flow(q, area, c):
    assert cfs_to_mm_per_day(c * q, area) == pytest.approx(c * cfs_to_mm_per_day(q, area), rel=1e-9, abs=1e-12)


# ---------------------------------------------------------------- drain_sqkm
GAGES_DRAIN = {
    "14161500": 62.4241,
    "14159200": 414.29970,
    "12452800": 526.35,
    "12457000": 1545.47,
    "13336500": 4959.467,
    "13331500": 618.9291,
}


@pytest.mark.parametrize("gauge, area", sorted(GAGES_DRAIN.items()))
def test_drain_sqkm_values(gauge, area):
    # Entiat is stated as 526.35 in the brief and 526.3533 in GAGES-II: 1e-4 relative covers both.
    assert get_basin(gauge).drain_sqkm == pytest.approx(area, rel=1e-4)


def test_drain_sqkm_is_a_positive_float_for_every_basin():
    for b in list_basins():
        assert isinstance(b.drain_sqkm, float) and b.drain_sqkm > 0 and math.isfinite(b.drain_sqkm)


def test_drain_sqkm_needs_no_data_dir(monkeypatch):
    monkeypatch.delenv("FALLSOIL_DATA_DIR", raising=False)
    assert get_basin("14161500").drain_sqkm > 0


def test_drain_sqkm_agrees_with_polygon_area_within_one_percent(data_dir_with_basins):
    gdf = gpd.read_file(data_dir_with_basins / "raw" / "basins" / "pilot_basins_gagesii.gpkg", engine="pyogrio")
    gdf = gdf.to_crs(5070)  # NAD83 / CONUS Albers equal-area
    for b in list_basins():
        poly_km2 = gdf.loc[gdf["site_no"] == b.id, "geometry"].iloc[0].area / 1e6
        assert abs(b.drain_sqkm - poly_km2) / poly_km2 < 0.01, b.id
