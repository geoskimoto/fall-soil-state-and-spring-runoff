"""Supplementary gridmet tests: tmmn/tmmx end-to-end, single-day, boundary overlap, request params, metadata."""
import math
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
import responses
from shapely.geometry import Polygon, box

from conftest import (CELL, GRIDMET_URL, all_files, grid_axes, grid_to_netcdf_bytes, make_grid,
                      parquet_files)
from fallsoil import manifest
from fallsoil.basins import get_basin
from fallsoil.errors import MalformedPayloadError, ZeroOverlapError
from fallsoil.fetch.gridmet import cell_weights, fetch_basin_mean

C = CELL
BASIN = "14161500"
NOW = date(2030, 1, 1)
FILE_VAR = {"pr": "precipitation_amount", "tmmx": "daily_maximum_temperature",
            "tmmn": "daily_minimum_temperature"}
FILE_UNIT = {"pr": "mm", "tmmx": "K", "tmmn": "K"}
COLUMN = {"pr": "precip_mm", "tmmx": "temp_max_c", "tmmn": "temp_min_c"}


def lookout_body(name, units, base, n_days=3, start="2021-03-01"):
    """Spatially constant field base + t over a grid covering Lookout Creek."""
    poly = get_basin(BASIN).polygon()
    minx, miny, maxx, maxy = poly.bounds
    lat0 = math.floor(miny / C) * C - C
    lon0 = math.floor(minx / C) * C - C
    n_lat = int(math.ceil((maxy - lat0) / C)) + 2
    n_lon = int(math.ceil((maxx - lon0) / C)) + 2
    lat, lon = grid_axes(lat0, lon0, n_lat, n_lon, descending_lat=True)
    da = make_grid(lambda t, i, j, la, lo: base + t, lat, lon, n_days=n_days, name=name, units=units,
                   start=start)
    return grid_to_netcdf_bytes(da)


def run(mocked, variable, body, start="2021-03-01", end="2021-03-03", now=NOW):
    mocked.add(responses.GET, GRIDMET_URL, body=body)
    return fetch_basin_mean(BASIN, variable, start, end, now=now)


def meta_of(path):
    import json
    return json.loads(pq.read_schema(path).metadata[b"fallsoil"])


# ---------------------------------------------------------------- 3. temperature variables
@pytest.mark.parametrize("variable", ["tmmn", "tmmx"])
def test_temperature_is_converted_from_kelvin_to_celsius(mocked, data_dir_with_basins, variable):
    body = lookout_body(FILE_VAR[variable], "K", 280.0)
    df = pd.read_parquet(run(mocked, variable, body))
    col = COLUMN[variable]
    assert list(df.columns)[:2] == ["date", col]
    assert df[col].to_numpy() == pytest.approx([280.0 - 273.15 + t for t in range(3)], abs=1e-6)
    assert "provisional" in df.columns


@pytest.mark.parametrize("variable", ["tmmn", "tmmx"])
def test_temperature_metadata_names_column_and_degC_unit(mocked, data_dir_with_basins, variable):
    body = lookout_body(FILE_VAR[variable], "K", 270.0)
    meta = meta_of(run(mocked, variable, body))
    assert meta["units"] == {COLUMN[variable]: "degC"}  # spec: temperatures are degC


@pytest.mark.parametrize("variable", ["tmmn", "tmmx"])
@pytest.mark.parametrize("bad_unit", ["degC", "C", "mm", "F", ""])
def test_temperature_in_wrong_unit_is_rejected_without_outputs(mocked, data_dir_with_basins, variable, bad_unit):
    body = lookout_body(FILE_VAR[variable], bad_unit, 10.0)
    with pytest.raises(MalformedPayloadError) as ei:
        run(mocked, variable, body)
    assert "unit" in str(ei.value).lower()
    assert parquet_files(data_dir_with_basins) == [] and manifest.read_manifest() == []


def test_precip_given_in_kelvin_is_rejected(mocked, data_dir_with_basins):
    body = lookout_body(FILE_VAR["pr"], "K", 5.0)
    with pytest.raises(MalformedPayloadError):
        run(mocked, "pr", body)


def test_tmmn_rejects_file_carrying_the_tmmx_variable(mocked, data_dir_with_basins):
    body = lookout_body(FILE_VAR["tmmx"], "K", 280.0)
    with pytest.raises(MalformedPayloadError):
        run(mocked, "tmmn", body)


# ---------------------------------------------------------------- 4. edge cases
def test_single_day_fetch_start_equals_end(mocked, data_dir_with_basins):
    body = lookout_body(FILE_VAR["pr"], "mm", 7.0, n_days=1)
    df = pd.read_parquet(run(mocked, "pr", body, start="2021-03-01", end="2021-03-01"))
    assert list(df["date"]) == [pd.Timestamp("2021-03-01")]
    assert df["precip_mm"].iloc[0] == pytest.approx(7.0)


def test_end_before_start_raises_value_error_and_makes_no_request(mocked, data_dir_with_basins):
    with pytest.raises(ValueError):
        fetch_basin_mean(BASIN, "pr", "2021-03-02", "2021-03-01", now=NOW)
    assert len(mocked.calls) == 0


def test_end_one_day_before_start_raises_but_same_day_does_not(mocked, data_dir_with_basins):
    with pytest.raises(ValueError):
        fetch_basin_mean(BASIN, "pr", date(2021, 3, 2), date(2021, 3, 1), now=NOW)
    body = lookout_body(FILE_VAR["pr"], "mm", 1.0, n_days=1, start="2021-03-02")
    run(mocked, "pr", body, start=date(2021, 3, 2), end=date(2021, 3, 2))


def lat_lon(n=4):
    return grid_axes(44.0, -122.0, n, n)


def test_polygon_exactly_touching_grid_edge_has_zero_overlap():
    lat, lon = lat_lon()
    west_edge = -122.0
    touching = box(west_edge - 0.05, 44.0 + C, west_edge, 44.0 + 2 * C)  # shares only the west edge
    with pytest.raises(ZeroOverlapError):
        cell_weights(lat, lon, touching)


def test_polygon_touching_at_a_single_corner_has_zero_overlap():
    lat, lon = lat_lon()
    corner = box(-122.1, 43.9, -122.0, 44.0)
    with pytest.raises(ZeroOverlapError):
        cell_weights(lat, lon, corner)


def test_tiny_nonzero_overlap_succeeds_and_weights_sum_to_one():
    lat, lon = lat_lon()
    sliver = box(-122.0 - 0.05, 44.0 + C, -122.0 + 1e-5, 44.0 + 2 * C)  # 1e-5 deg inside the grid
    w = cell_weights(lat, lon, sliver)
    assert float(w.sum()) == pytest.approx(1.0)
    assert (w.values >= 0).all()
    assert float(w.isel(lon=0).sum()) == pytest.approx(1.0)  # all weight in the westernmost column


def test_empty_polygon_raises_zero_overlap():
    lat, lon = lat_lon()
    with pytest.raises(ZeroOverlapError):
        cell_weights(lat, lon, Polygon())


def test_zero_overlap_is_also_a_value_error():
    lat, lon = lat_lon()
    with pytest.raises(ValueError):
        cell_weights(lat, lon, box(10, 10, 11, 11))


@pytest.mark.parametrize("bad_now", ["2021-03-05", 20210305, 1.5])
def test_now_of_wrong_type_is_rejected(mocked, data_dir_with_basins, bad_now):
    body = lookout_body(FILE_VAR["pr"], "mm", 1.0)
    mocked.add(responses.GET, GRIDMET_URL, body=body)
    with pytest.raises(TypeError):
        fetch_basin_mean(BASIN, "pr", "2021-03-01", "2021-03-03", now=bad_now)


def test_aware_non_utc_now_uses_its_utc_date(mocked, data_dir_with_basins):
    body = lookout_body(FILE_VAR["pr"], "mm", 1.0)
    tz = timezone(timedelta(hours=-8))
    now = datetime(2021, 3, 5, 20, 0, tzinfo=tz)  # = 2021-03-06 04:00 UTC
    meta = meta_of(run(mocked, "pr", body, now=now))
    assert meta["retrieval_date"] == "2021-03-06"


def test_naive_datetime_now_uses_its_calendar_date(mocked, data_dir_with_basins):
    body = lookout_body(FILE_VAR["pr"], "mm", 1.0)
    meta = meta_of(run(mocked, "pr", body, now=datetime(2021, 3, 5, 23, 59)))
    assert meta["retrieval_date"] == "2021-03-05"


# ---------------------------------------------------------------- 5. request parameters and outputs
@pytest.mark.parametrize("variable", ["pr", "tmmx", "tmmn"])
def test_outgoing_request_url_and_query(mocked, data_dir_with_basins, variable):
    body = lookout_body(FILE_VAR[variable], FILE_UNIT[variable], 5.0)
    run(mocked, variable, body)
    req = mocked.calls[0].request
    parsed = urlparse(req.url)
    assert parsed.path.endswith(f"/agg_met_{variable}_1979_CurrentYear_CONUS.nc")
    assert parsed.netloc == "thredds.northwestknowledge.net:8080"
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert q["var"] == FILE_VAR[variable]
    assert q["horizontalStride"] == "1"
    assert q["accept"] == "netcdf"
    assert q["time_start"].startswith("2021-03-01")
    assert q["time_end"].startswith("2021-03-03")
    minx, miny, maxx, maxy = get_basin(BASIN).polygon().bounds
    assert float(q["north"]) >= maxy and float(q["south"]) <= miny
    assert float(q["east"]) >= maxx and float(q["west"]) <= minx
    # padded by no more than a few cells
    assert float(q["north"]) - maxy < 4 * C and miny - float(q["south"]) < 4 * C
    assert float(q["east"]) - maxx < 4 * C and minx - float(q["west"]) < 4 * C


def test_single_day_request_has_same_start_and_end_date(mocked, data_dir_with_basins):
    body = lookout_body(FILE_VAR["pr"], "mm", 1.0, n_days=1)
    run(mocked, "pr", body, start="2021-03-01", end="2021-03-01")
    q = parse_qs(urlparse(mocked.calls[0].request.url).query)
    assert q["time_start"][0][:10] == q["time_end"][0][:10] == "2021-03-01"


def test_request_dates_accept_date_objects(mocked, data_dir_with_basins):
    body = lookout_body(FILE_VAR["pr"], "mm", 1.0)
    run(mocked, "pr", body, start=date(2021, 3, 1), end=date(2021, 3, 3))
    q = parse_qs(urlparse(mocked.calls[0].request.url).query)
    assert q["time_start"][0].startswith("2021-03-01") and q["time_end"][0].startswith("2021-03-03")


def test_bad_start_or_end_string_is_value_error_before_network(mocked, data_dir_with_basins):
    with pytest.raises(ValueError):
        fetch_basin_mean(BASIN, "pr", "03/01/2021", "2021-03-03", now=NOW)
    with pytest.raises(ValueError):
        fetch_basin_mean(BASIN, "pr", "2021-03-01", "not-a-date", now=NOW)
    assert len(mocked.calls) == 0


@pytest.mark.parametrize("variable", ["pr", "tmmx", "tmmn"])
def test_output_layout_and_metadata_provenance_keys(mocked, data_dir_with_basins, variable):
    body = lookout_body(FILE_VAR[variable], FILE_UNIT[variable], 5.0)
    out = run(mocked, variable, body)
    d = data_dir_with_basins
    assert out.parent == d / "interim" / "gridmet" and out.suffix == ".parquet"
    raws = all_files(d / "raw" / "gridmet")
    assert len(raws) == 1 and raws[0].suffix == ".nc" and raws[0].read_bytes() == body
    meta = meta_of(out)
    assert meta["source"] == "gridmet" and meta["basin"] == BASIN
    assert meta["variable"] == variable
    assert meta["url"].startswith("http://thredds.northwestknowledge.net:8080/thredds/ncss/")
    assert meta["url"].endswith(f"agg_met_{variable}_1979_CurrentYear_CONUS.nc")
    assert meta["params"]["var"] == FILE_VAR[variable]
    assert meta["params"]["horizontalStride"] == "1" and meta["params"]["accept"] == "netcdf"
    assert meta["rows"] == 3
    assert meta["raw_file"] == raws[0].name
    assert "units" in meta and "time_convention" in meta


def test_manifest_record_has_gridmet_url_and_params(mocked, data_dir_with_basins):
    body = lookout_body(FILE_VAR["tmmn"], "K", 270.0)
    run(mocked, "tmmn", body)
    (rec,) = manifest.read_manifest()
    assert rec["source"] == "gridmet" and rec["rows"] == 3
    assert rec["url"].endswith("agg_met_tmmn_1979_CurrentYear_CONUS.nc")
    assert rec["params"]["var"] == "daily_minimum_temperature"
    assert rec["params"]["accept"] == "netcdf"
    assert rec["sha256"] == manifest.sha256_bytes(body)


def test_rows_sorted_by_date_even_if_file_days_are_reversed(mocked, data_dir_with_basins):
    poly = get_basin(BASIN).polygon()
    minx, miny, maxx, maxy = poly.bounds
    lat0 = math.floor(miny / C) * C - C
    lon0 = math.floor(minx / C) * C - C
    lat, lon = grid_axes(lat0, lon0, int(math.ceil((maxy - lat0) / C)) + 2,
                         int(math.ceil((maxx - lon0) / C)) + 2, descending_lat=True)
    da = make_grid(lambda t, i, j, la, lo: float(t), lat, lon, n_days=3, name=FILE_VAR["pr"], units="mm")
    da = da.isel(day=slice(None, None, -1))
    df = pd.read_parquet(run(mocked, "pr", grid_to_netcdf_bytes(da)))
    assert df["date"].is_monotonic_increasing
    assert df["precip_mm"].tolist() == pytest.approx([0.0, 1.0, 2.0])  # value follows its date, not file position
    assert list(df.index) == [0, 1, 2]
