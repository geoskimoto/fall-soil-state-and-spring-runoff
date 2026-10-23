"""fetch.snotel: NRCS AWDB daily JSON -> parquet with metric, unit-named columns."""
import hashlib
import json
import logging

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
import requests
import responses
from hypothesis import HealthCheck, given, settings, strategies as st

from conftest import AWDB_URL, all_files, awdb_payload, awdb_series, parquet_files
from fallsoil import manifest
from fallsoil.errors import EmptyResultError, FallsoilError, FetchError, HttpError, MalformedPayloadError
from fallsoil.fetch.snotel import (celsius_to_fahrenheit, fahrenheit_to_celsius, fetch_snotel_daily,
                                   inches_to_mm, mm_to_inches)

TRIP = "908:WA:SNTL"


def full_payload(n=3, depths=(2, 8, 20), swe=None):
    swe = swe if swe is not None else [0.0, 1.0, 10.0][:n]
    series = [
        awdb_series("WTEQ", swe, unit="in"),
        awdb_series("PREC", [5.0, 5.5, 6.0][:n], unit="in"),
        awdb_series("TAVG", [32.0, 50.0, 14.0][:n], unit="degF"),
    ]
    for k, dep in enumerate(depths):
        series.append(awdb_series("SMS", [20.0 + k, 21.0 + k, 22.0 + k][:n], unit="pct", depth_in=dep))
    return awdb_payload(TRIP, series)


def serve(mocked, text):
    mocked.add(responses.GET, AWDB_URL, body=text.encode())
    return text.encode()


# ---------------------------------------------------------------- unit conversions (explicit numbers)
@pytest.mark.parametrize("inch, mm", [(0.0, 0.0), (1.0, 25.4), (10.0, 254.0), (0.5, 12.7)])
def test_inches_to_mm_examples(inch, mm):
    assert inches_to_mm(inch) == pytest.approx(mm, abs=1e-9)


@pytest.mark.parametrize("f, c", [(32.0, 0.0), (212.0, 100.0), (-40.0, -40.0), (50.0, 10.0), (14.0, -10.0),
                                  (98.6, 37.0)])
def test_fahrenheit_to_celsius_examples(f, c):
    assert fahrenheit_to_celsius(f) == pytest.approx(c, abs=1e-9)


def test_conversions_vectorised():
    s = pd.Series([0.0, 1.0, 2.0])
    out = inches_to_mm(s)
    assert isinstance(out, pd.Series) and list(out) == pytest.approx([0.0, 25.4, 50.8])
    assert list(fahrenheit_to_celsius(np.array([32.0, 212.0]))) == pytest.approx([0.0, 100.0])


def test_conversions_propagate_nan():
    assert np.isnan(inches_to_mm(float("nan"))) and np.isnan(fahrenheit_to_celsius(float("nan")))


finite = st.floats(min_value=-1e4, max_value=1e4, allow_nan=False)


@given(x=finite)
def test_property_inches_mm_roundtrip(x):
    assert mm_to_inches(inches_to_mm(x)) == pytest.approx(x, rel=1e-12, abs=1e-12)


@given(x=finite)
def test_property_f_c_roundtrip(x):
    assert celsius_to_fahrenheit(fahrenheit_to_celsius(x)) == pytest.approx(x, rel=1e-12, abs=1e-9)


@given(a=finite, b=finite)
def test_property_conversions_are_monotonic_increasing(a, b):
    lo, hi = sorted([a, b])
    assert inches_to_mm(lo) <= inches_to_mm(hi)
    assert fahrenheit_to_celsius(lo) <= fahrenheit_to_celsius(hi)


# ---------------------------------------------------------------- integration (recorded-shape fixtures)
def test_columns_values_and_units(mocked, data_dir):
    serve(mocked, full_payload())
    p = fetch_snotel_daily(TRIP, ("WTEQ", "PREC", "TAVG", "SMS"), start="2020-10-01", end="2020-10-03")
    df = pd.read_parquet(p)
    assert set(df.columns) == {"date", "swe_mm", "precip_accum_mm", "temp_avg_c",
                               "soil_moisture_pct_2in", "soil_moisture_pct_8in", "soil_moisture_pct_20in"}
    assert list(df["date"]) == list(pd.date_range("2020-10-01", periods=3))
    assert list(df["swe_mm"]) == pytest.approx([0.0, 25.4, 254.0])
    assert list(df["precip_accum_mm"]) == pytest.approx([127.0, 139.7, 152.4])
    assert list(df["temp_avg_c"]) == pytest.approx([0.0, 10.0, -10.0])
    # soil moisture is already percent: passed through, NOT multiplied by 25.4
    assert list(df["soil_moisture_pct_2in"]) == pytest.approx([20.0, 21.0, 22.0])
    assert list(df["soil_moisture_pct_8in"]) == pytest.approx([21.0, 22.0, 23.0])
    assert list(df["soil_moisture_pct_20in"]) == pytest.approx([22.0, 23.0, 24.0])


def test_only_requested_elements_are_returned(mocked, data_dir):
    serve(mocked, full_payload())
    df = pd.read_parquet(fetch_snotel_daily(TRIP, ("WTEQ",)))
    assert set(df.columns) == {"date", "swe_mm"}


def test_default_elements_request_everything_relevant(mocked, data_dir):
    serve(mocked, full_payload())
    df = pd.read_parquet(fetch_snotel_daily(TRIP))
    assert {"swe_mm", "precip_accum_mm", "temp_avg_c", "soil_moisture_pct_2in"} <= set(df.columns)


def test_soil_depth_columns_follow_station_depths(mocked, data_dir):
    serve(mocked, full_payload(depths=(4, 40)))
    df = pd.read_parquet(fetch_snotel_daily(TRIP, ("SMS",)))
    assert set(df.columns) == {"date", "soil_moisture_pct_4in", "soil_moisture_pct_40in"}


def test_missing_values_stay_nan_and_rows_are_kept(mocked, data_dir):
    series = [awdb_series("WTEQ", [1.0, None, 3.0], unit="in")]
    serve(mocked, awdb_payload(TRIP, series))
    df = pd.read_parquet(fetch_snotel_daily(TRIP, ("WTEQ",)))
    assert len(df) == 3 and np.isnan(df["swe_mm"].iloc[1])
    assert df["swe_mm"].iloc[0] == pytest.approx(25.4)


def test_requested_element_absent_from_station_warns_and_is_not_invented(mocked, data_dir, caplog):
    # Pilot stations mostly have no soil sensor: that must be loud (log), never a fabricated column.
    serve(mocked, awdb_payload(TRIP, [awdb_series("WTEQ", [1.0, 2.0], unit="in")]))
    with caplog.at_level(logging.WARNING):
        df = pd.read_parquet(fetch_snotel_daily(TRIP, ("WTEQ", "SMS")))
    assert set(df.columns) == {"date", "swe_mm"}
    assert any("SMS" in r.getMessage() and r.levelno >= logging.WARNING for r in caplog.records)


def test_unexpected_stored_unit_is_rejected_not_silently_converted(mocked, data_dir):
    serve(mocked, awdb_payload(TRIP, [awdb_series("WTEQ", [1.0], unit="mm")]))
    with pytest.raises(MalformedPayloadError) as ei:
        fetch_snotel_daily(TRIP, ("WTEQ",))
    assert "WTEQ" in ei.value.user_message
    assert parquet_files(data_dir) == []


def test_temperature_in_celsius_payload_is_rejected(mocked, data_dir):
    serve(mocked, awdb_payload(TRIP, [awdb_series("TAVG", [1.0], unit="degC")]))
    with pytest.raises(MalformedPayloadError):
        fetch_snotel_daily(TRIP, ("TAVG",))


def test_metadata_time_zone_and_units(mocked, data_dir):
    serve(mocked, full_payload())
    meta = json.loads(pq.read_schema(fetch_snotel_daily(TRIP)).metadata[b"fallsoil"])
    assert "PST" in meta["time_convention"]
    assert meta["source"] == "snotel_awdb" and meta["station"] == TRIP
    assert meta["units"]["swe_mm"] == "mm" and meta["units"]["temp_avg_c"] in {"degC", "C"}
    assert meta["units"]["soil_moisture_pct_2in"] in {"percent", "pct", "%"}


def test_request_names_station_and_daily_duration(mocked, data_dir):
    serve(mocked, full_payload())
    fetch_snotel_daily(TRIP, ("WTEQ",), start="2020-10-01", end="2020-10-03")
    url = mocked.calls[0].request.url
    assert "908%3AWA%3ASNTL" in url or TRIP in url
    assert "DAILY" in url.upper() and "2020-10-01" in url and "2020-10-03" in url


# ---------------------------------------------------------------- raw cache + manifest
def test_raw_json_cached_byte_identical_and_manifested(mocked, data_dir):
    body = serve(mocked, full_payload())
    p = fetch_snotel_daily(TRIP)
    assert [f.read_bytes() for f in all_files(data_dir / "raw" / "snotel")] == [body]
    (r,) = manifest.read_manifest()
    assert r["source"] == "snotel_awdb" and r["sha256"] == hashlib.sha256(body).hexdigest()
    assert r["rows"] == len(pd.read_parquet(p))


def test_identical_refetch_idempotent_and_changed_content_never_overwrites(mocked, data_dir):
    v1 = full_payload().encode()
    mocked.add(responses.GET, AWDB_URL, body=v1)
    fetch_snotel_daily(TRIP)
    fetch_snotel_daily(TRIP)
    assert len(manifest.read_manifest()) == 1 and len(all_files(data_dir / "raw" / "snotel")) == 1
    v2 = full_payload(swe=[0.0, 2.0, 10.0]).encode()
    mocked.replace(responses.GET, AWDB_URL, body=v2)
    fetch_snotel_daily(TRIP)
    assert sorted(f.read_bytes() for f in all_files(data_dir / "raw" / "snotel")) == sorted([v1, v2])
    assert len(manifest.read_manifest()) == 2


# ---------------------------------------------------------------- failure handling
def clean(data_dir):
    assert parquet_files(data_dir) == [] and manifest.read_manifest() == []


@pytest.mark.parametrize("bad", ["", "908", "908:WA", "908:WA:SNTL:x", "abc:WA:SNTL", "908:wa:SNTL", None])
def test_bad_triplet_rejected_before_network(mocked, data_dir, bad):
    with pytest.raises((ValueError, TypeError)):
        fetch_snotel_daily(bad)
    assert len(mocked.calls) == 0


def test_unknown_element_rejected_before_network(mocked, data_dir):
    with pytest.raises(ValueError):
        fetch_snotel_daily(TRIP, ("WTEQ", "BOGUS"))
    assert len(mocked.calls) == 0


def test_500_and_timeout_and_404(mocked, data_dir):
    mocked.add(responses.GET, AWDB_URL, status=500)
    with pytest.raises(HttpError) as ei:
        fetch_snotel_daily(TRIP)
    assert ei.value.user_message and len(mocked.calls) > 1
    clean(data_dir)
    mocked.replace(responses.GET, AWDB_URL, body=requests.exceptions.ReadTimeout("t"))
    with pytest.raises(FetchError):
        fetch_snotel_daily(TRIP)
    clean(data_dir)
    n = len(mocked.calls)
    mocked.replace(responses.GET, AWDB_URL, status=404)
    with pytest.raises(HttpError) as ei:
        fetch_snotel_daily(TRIP)
    assert ei.value.status_code == 404 and len(mocked.calls) == n + 1
    clean(data_dir)


@pytest.mark.parametrize("body", [
    "not json at all", "", "{}", '{"data": []}', '[{"stationTriplet": "908:WA:SNTL"}]',
    '[{"stationTriplet": "908:WA:SNTL", "data": [{"stationElement": {}, "values": "x"}]}]',
    '[{"stationTriplet": "908:WA:SNTL", "data": [{"stationElement": {"elementCode": "WTEQ", '
    '"storedUnitCode": "in"}, "values": [{"date": "garbage", "value": 1.0}]}]}]',
    '[{"stationTriplet": "908:WA:SNTL", "data": [{"stationElement": {"elementCode": "WTEQ", '
    '"storedUnitCode": "in"}, "values": [{"date": "2020-10-01", "value": "wet"}]}]}]',
])
def test_malformed_payload(mocked, data_dir, body):
    serve(mocked, body)
    with pytest.raises((MalformedPayloadError, EmptyResultError)) as ei:
        fetch_snotel_daily(TRIP, ("WTEQ",))
    assert isinstance(ei.value, FallsoilError) and ei.value.user_message
    clean(data_dir)


@pytest.mark.parametrize("body", ["[]", '[{"stationTriplet": "908:WA:SNTL", "data": []}]'])
def test_empty_result(mocked, data_dir, body):
    serve(mocked, body)
    with pytest.raises(EmptyResultError) as ei:
        fetch_snotel_daily(TRIP, ("WTEQ",))
    assert TRIP in ei.value.user_message
    clean(data_dir)


def test_element_present_but_zero_values_is_empty_result(mocked, data_dir):
    serve(mocked, awdb_payload(TRIP, [awdb_series("WTEQ", [], unit="in")]))
    with pytest.raises(EmptyResultError):
        fetch_snotel_daily(TRIP, ("WTEQ",))
    clean(data_dir)


# ---------------------------------------------------------------- property
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(swe_in=st.lists(st.floats(min_value=0, max_value=500, allow_nan=False), min_size=1, max_size=20))
def test_property_swe_mm_is_25_4_times_inches_and_stays_nonnegative(swe_in, tmp_path_factory, monkeypatch):
    monkeypatch.setenv("FALLSOIL_DATA_DIR", str(tmp_path_factory.mktemp("d")))
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, AWDB_URL, body=awdb_payload(TRIP, [awdb_series("WTEQ", swe_in, unit="in")]))
        df = pd.read_parquet(fetch_snotel_daily(TRIP, ("WTEQ",)))
    assert np.allclose(df["swe_mm"], np.array(swe_in) * 25.4, rtol=1e-12)
    assert (df["swe_mm"] >= 0).all()
