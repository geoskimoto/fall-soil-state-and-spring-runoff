"""fetch.gridmet: area-weighted (fractional-overlap) basin means from a gridded NetCDF subset."""
import hashlib
from datetime import date, datetime, timedelta, timezone
import json
import math
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
import requests
import responses
import xarray as xr
from hypothesis import HealthCheck, given, settings, strategies as st
from shapely.geometry import Polygon, box

from conftest import (CELL, GRIDMET_URL, all_files, grid_axes, grid_to_netcdf_bytes, make_grid,
                      parquet_files)
from fallsoil import manifest
from fallsoil.basins import get_basin
from fallsoil.errors import (EmptyResultError, FallsoilError, FetchError, HttpError,
                             MalformedPayloadError, ZeroOverlapError)
from fallsoil.fetch.gridmet import (area_weighted_mean, cell_weights, fetch_basin_mean,
                                    kelvin_to_celsius)

C = CELL
LAT0, LON0 = 44.0, -122.0  # SW corner edge of the synthetic grid (multiples of 1/24 deg)


def grid(values_fn, n_lat=6, n_lon=6, n_days=2, descending=False, **kw):
    lat, lon = grid_axes(LAT0, LON0, n_lat, n_lon, descending_lat=descending)
    return make_grid(values_fn, lat, lon, n_days=n_days, **kw)


def idx_value(t, i, j, la, lo):
    """Smooth field in cell-index units + 100/day, so weighted means are easy to predict."""
    return 100.0 * t + 10.0 * (la - LAT0) / C + (lo - LON0) / C


def W(da, polygon):
    w = cell_weights(da["lat"].values, da["lon"].values, polygon)
    return w


def cell_poly(i, j, fi0, fi1, fj0, fj1):
    """Box inside grid cell-index space: rows i+fi0..i+fi1, cols j+fj0..j+fj1 (in cell units)."""
    return box(LON0 + (j + fj0) * C, LAT0 + (i + fi0) * C, LON0 + (j + fj1) * C, LAT0 + (i + fi1) * C)


# ---------------------------------------------------------------- weights: known fractional overlaps
def test_basin_smaller_than_one_cell_gets_that_cell_alone():
    da = grid(lambda t, i, j, la, lo: 100.0 * i + j)
    poly = cell_poly(2, 3, 0.3, 0.5, 0.2, 0.7)  # inside cell (row2, col3)
    w = W(da, poly)
    assert float(w.sum()) == pytest.approx(1.0)
    assert float(w.isel(lat=2, lon=3)) == pytest.approx(1.0)
    out = area_weighted_mean(da, poly)
    assert list(out.values) == pytest.approx([203.0, 203.0])


def test_basin_spanning_four_cells_with_75_25_and_50_50_overlaps():
    da = grid(lambda t, i, j, la, lo: 10.0 * i + j + 100.0 * t)
    poly = box(LON0 + 1.25 * C, LAT0 + 1.5 * C, LON0 + 2.25 * C, LAT0 + 2.5 * C)
    w = W(da, poly)
    exp = {(1, 1): 0.375, (1, 2): 0.125, (2, 1): 0.375, (2, 2): 0.125}
    for i in range(6):
        for j in range(6):
            assert float(w.isel(lat=i, lon=j)) == pytest.approx(exp.get((i, j), 0.0), abs=5e-3)
    out = area_weighted_mean(da, poly)
    expected_t0 = 0.375 * 11 + 0.125 * 12 + 0.375 * 21 + 0.125 * 22
    assert out.values == pytest.approx([expected_t0, expected_t0 + 100.0], abs=0.05)


def test_no_cell_centre_inside_polygon_still_uses_fractional_overlap():
    # polygon covers cols 1.6..2.2 (cell units); centres sit at 1.5 and 2.5, neither inside.
    da = grid(lambda t, i, j, la, lo: float(j))
    poly = box(LON0 + 1.6 * C, LAT0 + 1.2 * C, LON0 + 2.2 * C, LAT0 + 1.8 * C)
    w = W(da, poly)
    assert float(w.isel(lat=1, lon=1)) == pytest.approx(0.4 / 0.6, abs=5e-3)
    assert float(w.isel(lat=1, lon=2)) == pytest.approx(0.2 / 0.6, abs=5e-3)
    assert area_weighted_mean(da, poly).values == pytest.approx([1.0 * 2 / 3 + 2.0 * 1 / 3] * 2, abs=5e-3)


def test_non_rectangular_polygon_triangle_splits_two_cells_evenly():
    da = grid(lambda t, i, j, la, lo: float(j))
    tri = Polygon([(LON0 + 1 * C, LAT0 + 1 * C), (LON0 + 3 * C, LAT0 + 1 * C), (LON0 + 2 * C, LAT0 + 2 * C)])
    w = W(da, tri)
    assert float(w.isel(lat=1, lon=1)) == pytest.approx(0.5, abs=5e-3)
    assert float(w.isel(lat=1, lon=2)) == pytest.approx(0.5, abs=5e-3)
    assert float(w.sum()) == pytest.approx(1.0)


def test_polygon_covering_whole_cell_exactly():
    da = grid(lambda t, i, j, la, lo: 7.0 * i + 1.0 * j)
    poly = cell_poly(0, 0, 0, 1, 0, 1)
    out = area_weighted_mean(da, poly)
    assert out.values == pytest.approx([0.0, 0.0])


def test_descending_latitude_axis_gives_same_answer():
    poly = box(LON0 + 1.25 * C, LAT0 + 1.5 * C, LON0 + 2.25 * C, LAT0 + 2.5 * C)
    a = area_weighted_mean(grid(idx_value), poly)
    b = area_weighted_mean(grid(idx_value, descending=True), poly)
    assert a.values == pytest.approx(b.values)


def test_extra_dimensions_preserved_and_lat_lon_reduced():
    da = grid(idx_value, n_days=4)
    out = area_weighted_mean(da, cell_poly(1, 1, 0.1, 0.9, 0.1, 0.9))
    assert out.dims == ("day",) and out.sizes["day"] == 4


def test_constant_field_returns_constant():
    da = grid(lambda *a: 3.5)
    poly = box(LON0 + 0.7 * C, LAT0 + 0.2 * C, LON0 + 3.9 * C, LAT0 + 4.1 * C)
    assert area_weighted_mean(da, poly).values == pytest.approx([3.5, 3.5])


# ---------------------------------------------------------------- zero overlap fails loudly
@pytest.mark.parametrize("poly", [
    box(LON0 + 1, LAT0 + 1, LON0 + 1.1, LAT0 + 1.1),                    # entirely outside the grid
    box(LON0 + 6 * C, LAT0, LON0 + 7 * C, LAT0 + 6 * C),                 # touches the grid edge only
    box(LON0 - 5, LAT0 - 5, LON0 - 4, LAT0 - 4),
])
def test_zero_overlap_raises(poly):
    da = grid(idx_value)
    with pytest.raises(ZeroOverlapError) as ei:
        area_weighted_mean(da, poly)
    assert isinstance(ei.value, FallsoilError) and isinstance(ei.value, ValueError)
    assert ei.value.user_message
    with pytest.raises(ZeroOverlapError):
        cell_weights(da["lat"].values, da["lon"].values, poly)


def test_empty_polygon_raises():
    with pytest.raises(ZeroOverlapError):
        area_weighted_mean(grid(idx_value), Polygon())


# ---------------------------------------------------------------- weights: property
def rect_oracle(lat, lon, s, w, n, e):
    """Exact fractional overlaps of an axis-aligned box with 1/24-degree cells."""
    ov_lat = np.array([max(0.0, min(n, la + C / 2) - max(s, la - C / 2)) for la in lat])
    ov_lon = np.array([max(0.0, min(e, lo + C / 2) - max(w, lo - C / 2)) for lo in lon])
    m = np.outer(ov_lat, ov_lon)
    return m / m.sum()


frac = st.floats(min_value=0.0, max_value=6.0)


@settings(max_examples=60, deadline=None)
@given(a=frac, b=frac, c=frac, d=frac)
def test_property_weights_nonneg_sum_to_one_and_match_exact_overlap(a, b, c, d):
    x0, x1 = sorted([a, b]); y0, y1 = sorted([c, d])
    if x1 - x0 < 1e-3 or y1 - y0 < 1e-3:
        return
    lat, lon = grid_axes(LAT0, LON0, 6, 6)
    poly = box(LON0 + x0 * C, LAT0 + y0 * C, LON0 + x1 * C, LAT0 + y1 * C)
    w = cell_weights(lat, lon, poly).values
    assert (w >= 0).all() and w.sum() == pytest.approx(1.0)
    exp = rect_oracle(lat, lon, LAT0 + y0 * C, LON0 + x0 * C, LAT0 + y1 * C, LON0 + x1 * C)
    assert np.allclose(w, exp, atol=5e-3)


@settings(max_examples=40, deadline=None)
@given(a=frac, b=frac, c=frac, d=frac, seed=st.integers(0, 10_000))
def test_property_mean_is_bounded_by_cell_values_and_exact_for_constants(a, b, c, d, seed):
    x0, x1 = sorted([a, b]); y0, y1 = sorted([c, d])
    if x1 - x0 < 1e-3 or y1 - y0 < 1e-3:
        return
    rng = np.random.default_rng(seed)
    vals = rng.uniform(-50, 200, size=(6, 6))
    da = grid(lambda t, i, j, la, lo: vals[i, j], n_days=1)
    poly = box(LON0 + x0 * C, LAT0 + y0 * C, LON0 + x1 * C, LAT0 + y1 * C)
    m = float(area_weighted_mean(da, poly).values[0])
    w = cell_weights(da["lat"].values, da["lon"].values, poly).values
    touched = vals[w > 0]
    assert touched.min() - 1e-9 <= m <= touched.max() + 1e-9
    assert m == pytest.approx(float((w * vals).sum()), abs=1e-9)


# ---------------------------------------------------------------- unit conversion
def test_kelvin_to_celsius_examples():
    assert kelvin_to_celsius(273.15) == pytest.approx(0.0)
    assert kelvin_to_celsius(300.0) == pytest.approx(26.85)
    assert kelvin_to_celsius(233.15) == pytest.approx(-40.0)


# ---------------------------------------------------------------- fetch_basin_mean (integration)
BASIN = "14161500"


def lookout_grid(n_days=3, units="mm", name="precipitation_amount", start="2021-03-01"):
    poly = get_basin(BASIN).polygon()
    minx, miny, maxx, maxy = poly.bounds
    lat0 = math.floor(miny / C) * C - C
    lon0 = math.floor(minx / C) * C - C
    n_lat = int(math.ceil((maxy - lat0) / C)) + 2
    n_lon = int(math.ceil((maxx - lon0) / C)) + 2
    lat, lon = grid_axes(lat0, lon0, n_lat, n_lon, descending_lat=True)  # gridMET lat is descending
    da = make_grid(lambda t, i, j, la, lo: 50.0 * t + 2000.0 * (la - lat0) + 700.0 * (lo - lon0),
                   lat, lon, n_days=n_days, name=name, units=units, start=start)
    return da, grid_to_netcdf_bytes(da), poly


def oracle_mean(da, poly):
    num = np.zeros(da.sizes["day"]); den = 0.0
    for i, la in enumerate(da["lat"].values):
        for j, lo in enumerate(da["lon"].values):
            a = box(lo - C / 2, la - C / 2, lo + C / 2, la + C / 2).intersection(poly).area
            if a > 0:
                num += a * da.values[:, i, j]; den += a
    return num / den


NOW = date(2030, 1, 1)  # injected clock far after every fixture date -> deterministic, all permanent


def fetch(mocked, body, variable="pr", start="2021-03-01", end="2021-03-03", status=200, now=NOW):
    mocked.add(responses.GET, GRIDMET_URL, body=body, status=status)
    return fetch_basin_mean(BASIN, variable, start, end, now=now)


def test_precip_basin_mean_matches_independent_area_weighting(mocked, data_dir_with_basins):
    da, body, poly = lookout_grid()
    df = pd.read_parquet(fetch(mocked, body))
    assert list(df["date"]) == list(pd.date_range("2021-03-01", periods=3))
    assert list(df.columns)[:2] == ["date", "precip_mm"] and "provisional" in df.columns
    assert df["precip_mm"].to_numpy() == pytest.approx(oracle_mean(da, poly), rel=1e-2)


def test_request_bbox_covers_the_whole_polygon(mocked, data_dir_with_basins):
    da, body, poly = lookout_grid()
    fetch(mocked, body)
    url = mocked.calls[0].request.url
    q = {k: float(v[0]) for k, v in parse_qs(urlparse(url).query).items()
         if k in {"north", "south", "east", "west"}}
    minx, miny, maxx, maxy = poly.bounds
    assert q["south"] <= miny and q["north"] >= maxy and q["west"] <= minx and q["east"] >= maxx
    assert "agg_met_pr_" in url and "2021-03-01" in url


def test_temperature_variables_are_converted_from_kelvin(mocked, data_dir_with_basins):
    da, _, poly = lookout_grid(name="daily_maximum_temperature", units="K")
    da = da * 0 + 273.15 + 10.0
    da.name = "daily_maximum_temperature"; da.attrs["units"] = "K"
    df = pd.read_parquet(fetch(mocked, grid_to_netcdf_bytes(da), variable="tmmx"))
    assert "temp_max_c" in df.columns and "temp_c" not in df.columns
    assert df["temp_max_c"].to_numpy() == pytest.approx([10.0] * 3)


def meta_of(p):
    return json.loads(pq.read_schema(p).metadata[b"fallsoil"])


def prov_run(mocked, retrieval, first_day, n_days):
    _, body, _ = lookout_grid(n_days=n_days, start=first_day)
    last = (pd.Timestamp(first_day) + pd.Timedelta(days=n_days - 1)).strftime("%Y-%m-%d")
    p = fetch(mocked, body, start=first_day, end=last, now=retrieval)
    return pd.read_parquet(p), meta_of(p)


def test_provisional_boundary_is_exactly_day_60_vs_61(mocked, data_dir_with_basins):
    R = date(2021, 5, 1)
    first = R - timedelta(days=62)
    df, meta = prov_run(mocked, R, first.isoformat(), 63)  # R-62 .. R
    age = (pd.Timestamp(R) - df["date"]).dt.days
    assert list(df["provisional"]) == list(age <= 60)
    by_age = dict(zip(age, df["provisional"]))
    assert not by_age[61] and by_age[60]   # 61 days old: permanent; 60 days old: provisional
    assert not by_age[62] and by_age[0]
    assert df["provisional"].dtype == bool


def test_request_wholly_in_the_past_is_all_permanent(mocked, data_dir_with_basins):
    df, meta = prov_run(mocked, date(2022, 1, 1), "2021-03-01", 3)
    assert not df["provisional"].any()
    assert meta["n_provisional_days"] == 0
    assert meta["provisional_start"] is None and meta["provisional_end"] is None


def test_request_wholly_recent_is_all_provisional(mocked, data_dir_with_basins):
    df, meta = prov_run(mocked, date(2021, 3, 5), "2021-03-01", 3)
    assert df["provisional"].all()
    assert meta["n_provisional_days"] == 3
    assert (meta["provisional_start"], meta["provisional_end"]) == ("2021-03-01", "2021-03-03")


def test_straddling_request_records_retrieval_date_and_provisional_range(mocked, data_dir_with_basins):
    R = date(2021, 5, 1)
    first = R - timedelta(days=64)               # R-64 .. R-59
    df, meta = prov_run(mocked, R, first.isoformat(), 6)
    ages = list((pd.Timestamp(R) - df["date"]).dt.days)
    assert ages == [64, 63, 62, 61, 60, 59]
    assert list(df["provisional"]) == [a <= 60 for a in ages]
    assert meta["retrieval_date"] == R.isoformat()
    assert meta["n_provisional_days"] == 2
    assert meta["provisional_start"] == (R - timedelta(days=60)).isoformat()
    assert meta["provisional_end"] == (R - timedelta(days=59)).isoformat()
    assert meta["provisional_window_days"] == 60


def test_now_accepts_datetime_and_uses_its_utc_date(mocked, data_dir_with_basins):
    df, meta = prov_run(mocked, datetime(2021, 3, 5, 23, 30, tzinfo=timezone.utc), "2021-03-01", 3)
    assert meta["retrieval_date"] == "2021-03-05" and df["provisional"].all()


def test_default_clock_is_the_real_today_when_now_is_omitted(mocked, data_dir_with_basins):
    _, body, _ = lookout_grid()
    mocked.add(responses.GET, GRIDMET_URL, body=body)
    meta = meta_of(fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03"))
    assert meta["retrieval_date"] == datetime.now(timezone.utc).date().isoformat()


def test_metadata_time_convention_is_mountain_standard_time_not_utc_day(mocked, data_dir_with_basins):
    _, body, _ = lookout_grid()
    tc = meta_of(fetch(mocked, body))["time_convention"].lower()
    assert "mountain standard time" in tc or "mst" in tc
    assert "midnight" in tc and "07:00 utc" in tc
    assert "utc day" not in tc and "utc-day" not in tc and "utc days" not in tc


def test_metadata_documents_units_and_aggregation(mocked, data_dir_with_basins):
    _, body, _ = lookout_grid()
    meta = meta_of(fetch(mocked, body))
    assert meta["units"]["precip_mm"] == "mm"
    assert meta["aggregation"] == "area_weighted_fractional_overlap"
    assert meta["source"] == "gridmet" and meta["basin"] == BASIN


def gridmet_raw(d):
    return all_files(d / "raw" / "gridmet")


def test_raw_netcdf_cached_byte_identical(mocked, data_dir_with_basins):
    _, body, _ = lookout_grid()
    fetch(mocked, body)
    assert [f.read_bytes() for f in gridmet_raw(data_dir_with_basins)] == [body]


def test_identical_refetch_keeps_one_raw_file_and_changed_content_never_overwrites(mocked, data_dir_with_basins):
    _, v1, _ = lookout_grid()
    fetch(mocked, v1)
    fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03", now=NOW)
    assert len(gridmet_raw(data_dir_with_basins)) == 1
    da2, v2, _ = lookout_grid()
    v2 = grid_to_netcdf_bytes(da2 + 1.0)
    mocked.replace(responses.GET, GRIDMET_URL, body=v2)
    fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03", now=NOW)
    assert sorted(f.read_bytes() for f in gridmet_raw(data_dir_with_basins)) == sorted([v1, v2])


def test_failed_fetch_leaves_no_parquet(mocked, data_dir_with_basins):
    mocked.add(responses.GET, GRIDMET_URL, body=b"junk")
    with pytest.raises(MalformedPayloadError):
        fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03", now=NOW)
    assert parquet_files(data_dir_with_basins) == []


def test_manifest_records_sha256_of_downloaded_bytes(mocked, data_dir_with_basins):
    _, body, _ = lookout_grid()
    p = fetch(mocked, body)
    (r,) = manifest.read_manifest()
    assert r["source"] == "gridmet" and r["sha256"] == hashlib.sha256(body).hexdigest()
    assert r["rows"] == len(pd.read_parquet(p)) and r["url"].startswith("http://thredds.northwestknowledge.net")
    fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03", now=NOW)
    assert len(manifest.read_manifest()) == 1  # identical re-fetch: no duplicate


def test_unknown_variable_or_basin_rejected_before_network(mocked, data_dir_with_basins):
    with pytest.raises(ValueError):
        fetch_basin_mean(BASIN, "bogus", "2021-03-01", "2021-03-03")
    from fallsoil.errors import UnknownBasinError
    with pytest.raises(UnknownBasinError):
        fetch_basin_mean("nope", "pr", "2021-03-01", "2021-03-03")
    with pytest.raises(ValueError):
        fetch_basin_mean(BASIN, "pr", "2021-03-05", "2021-03-01")  # end before start
    assert len(mocked.calls) == 0


# ---------------------------------------------------------------- failure handling
def clean(d):
    assert parquet_files(d) == [] and manifest.read_manifest() == []


def test_zero_overlap_between_returned_grid_and_basin_fails_loudly(mocked, data_dir_with_basins):
    lat, lon = grid_axes(30.0, -100.0, 4, 4)  # server returned the wrong region
    da = make_grid(lambda *a: 1.0, lat, lon)
    mocked.add(responses.GET, GRIDMET_URL, body=grid_to_netcdf_bytes(da))
    with pytest.raises(ZeroOverlapError):
        fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03")
    clean(data_dir_with_basins)


def test_wrong_units_in_file_rejected(mocked, data_dir_with_basins):
    _, body, _ = lookout_grid(units="inches")
    mocked.add(responses.GET, GRIDMET_URL, body=body)
    with pytest.raises(MalformedPayloadError) as ei:
        fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03")
    assert "unit" in ei.value.user_message.lower()
    clean(data_dir_with_basins)


def test_expected_variable_missing_rejected(mocked, data_dir_with_basins):
    _, body, _ = lookout_grid(name="something_else")
    mocked.add(responses.GET, GRIDMET_URL, body=body)
    with pytest.raises(MalformedPayloadError):
        fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03")
    clean(data_dir_with_basins)


@pytest.mark.parametrize("body", [b"", b"<html>502 Bad Gateway</html>", b"CDF\x01garbage" * 3])
def test_non_netcdf_payload_rejected(mocked, data_dir_with_basins, body):
    mocked.add(responses.GET, GRIDMET_URL, body=body)
    with pytest.raises(MalformedPayloadError) as ei:
        fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03")
    assert ei.value.user_message
    clean(data_dir_with_basins)


def test_zero_time_steps_is_empty_result(mocked, data_dir_with_basins):
    da, _, _ = lookout_grid(n_days=2)
    body = grid_to_netcdf_bytes(da.isel(day=slice(0, 0)))
    mocked.add(responses.GET, GRIDMET_URL, body=body)
    with pytest.raises(EmptyResultError):
        fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03")
    clean(data_dir_with_basins)


def test_http_failures(mocked, data_dir_with_basins):
    mocked.add(responses.GET, GRIDMET_URL, status=503)
    with pytest.raises(HttpError) as ei:
        fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03")
    assert len(mocked.calls) > 1 and ei.value.user_message
    clean(data_dir_with_basins)
    mocked.replace(responses.GET, GRIDMET_URL, body=requests.exceptions.ConnectTimeout("t"))
    with pytest.raises(FetchError):
        fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03")
    clean(data_dir_with_basins)
    n = len(mocked.calls)
    mocked.replace(responses.GET, GRIDMET_URL, status=400)
    with pytest.raises(HttpError):
        fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03")
    assert len(mocked.calls) == n + 1
    clean(data_dir_with_basins)
