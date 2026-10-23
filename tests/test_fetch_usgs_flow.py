"""fetch.usgs_flow: NWIS daily-value RDB -> parquet (date, flow_cfs, qualifier)."""
import hashlib
import json
from datetime import date

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
import requests
import responses
from hypothesis import HealthCheck, given, settings, strategies as st

from conftest import USGS_URL, all_files, default_rdb_rows, make_rdb, parquet_files, REPO_ROOT
from fallsoil import manifest
from fallsoil.errors import EmptyResultError, FallsoilError, FetchError, HttpError, MalformedPayloadError
from fallsoil.fetch.usgs_flow import fetch_daily_flow, parse_qualifier

SITE = "14161500"


def serve(mocked, text, status=200):
    mocked.add(responses.GET, USGS_URL, body=text.encode(), status=status)
    return text.encode()


def read(path):
    return pd.read_parquet(path)


# ---------------------------------------------------------------- qualifier parsing (unit)
@pytest.mark.parametrize("code, tokens", [
    ("A", {"A"}), ("P", {"P"}), ("e", {"e"}), ("A e", {"A", "e"}), ("P e", {"P", "e"}),
    ("P Ice", {"P", "Ice"}), ("", set()), (None, set()), ("  A  ", {"A"}),
])
def test_parse_qualifier(code, tokens):
    assert set(parse_qualifier(code)) == tokens


# ---------------------------------------------------------------- happy path
def test_parquet_schema_and_values(mocked, data_dir):
    serve(mocked, make_rdb(SITE, [("2020-10-01", "11.0", "A"), ("2020-10-02", "12.5", "A")]))
    p = fetch_daily_flow(SITE)
    assert p.suffix == ".parquet" and p.is_relative_to(data_dir / "interim")
    df = read(p)
    assert {"date", "flow_cfs", "qualifier"} <= set(df.columns)
    assert pd.api.types.is_datetime64_any_dtype(df["date"])
    assert pd.api.types.is_float_dtype(df["flow_cfs"])
    assert list(df["date"]) == [pd.Timestamp("2020-10-01"), pd.Timestamp("2020-10-02")]
    assert list(df["flow_cfs"]) == [11.0, 12.5]


def test_no_column_without_a_unit_in_its_name(mocked, data_dir):
    serve(mocked, make_rdb(SITE, default_rdb_rows()))
    cols = set(read(fetch_daily_flow(SITE)).columns)
    assert "flow" not in cols and "discharge" not in cols and "flow_cfs" in cols


def test_provisional_and_estimated_qualifiers_are_retained(mocked, data_dir):
    rows = [("2026-09-28", "10.0", "A"), ("2026-09-29", "11.0", "A e"),
            ("2026-09-30", "12.0", "P"), ("2026-10-01", "13.0", "P e")]
    serve(mocked, make_rdb(SITE, rows))
    df = read(fetch_daily_flow(SITE))
    assert len(df) == 4  # nothing dropped
    assert list(df["qualifier"]) == ["A", "A e", "P", "P e"]
    assert list(df["flow_cfs"]) == [10.0, 11.0, 12.0, 13.0]


def test_negative_and_missing_flow_rows_are_kept_for_qc_to_flag(mocked, data_dir):
    rows = [("2020-10-01", "5.0", "A"), ("2020-10-02", "-1.0", "P"), ("2020-10-03", "", "Ice"),
            ("2020-10-04", "7.0", "A")]
    serve(mocked, make_rdb(SITE, rows))
    df = read(fetch_daily_flow(SITE))
    assert len(df) == 4
    assert df["flow_cfs"].iloc[1] == -1.0           # not clipped
    assert np.isnan(df["flow_cfs"].iloc[2])          # not filled, not dropped
    assert df["qualifier"].iloc[2] == "Ice"
    from fallsoil import qc
    res = qc.run_qc(df, "flow")
    assert list(res.flags["flow_cfs_out_of_range"]) == [False, True, False, False]
    assert list(res.flags["flow_cfs_missing"]) == [False, False, True, False]


def test_output_is_chronological_and_missing_days_are_not_filled(mocked, data_dir):
    rows = [("2020-10-01", "1.0", "A"), ("2020-10-05", "2.0", "A")]
    serve(mocked, make_rdb(SITE, rows))
    df = read(fetch_daily_flow(SITE))
    assert len(df) == 2


def test_time_zone_and_units_metadata_documented(mocked, data_dir):
    serve(mocked, make_rdb(SITE, default_rdb_rows()))
    meta = json.loads(pq.read_schema(fetch_daily_flow(SITE)).metadata[b"fallsoil"])
    assert "local standard time" in meta["time_convention"].lower()
    assert "daily mean" in meta["time_convention"].lower() or meta.get("statistic") == "daily_mean"
    assert meta["units"]["flow_cfs"] in {"ft3/s", "cfs"}
    assert meta["source"] == "usgs_dv" and meta["site"] == SITE


def test_real_cached_payload_parses(mocked, data_dir):
    raw = (REPO_ROOT / "data/raw/screening/dv_14161500.rdb").read_bytes()
    mocked.add(responses.GET, USGS_URL, body=raw)
    df = read(fetch_daily_flow(SITE))
    assert df["date"].is_monotonic_increasing and df["date"].is_unique
    assert df["date"].iloc[0] == pd.Timestamp("1949-10-01") and df["flow_cfs"].iloc[0] == 11.0


def test_start_end_are_sent_to_server(mocked, data_dir):
    serve(mocked, make_rdb(SITE, default_rdb_rows()))
    fetch_daily_flow(SITE, start="2020-09-28", end=date(2020, 10, 7))
    url = mocked.calls[0].request.url
    assert "2020-09-28" in url and "2020-10-07" in url and SITE in url and "00060" in url


# ---------------------------------------------------------------- raw cache + manifest
def raw_files(data_dir):
    return all_files(data_dir / "raw" / "usgs")


def test_raw_response_cached_byte_identical(mocked, data_dir):
    body = serve(mocked, make_rdb(SITE, default_rdb_rows()))
    fetch_daily_flow(SITE)
    assert [f.read_bytes() for f in raw_files(data_dir)] == [body]


def test_manifest_record_written_with_sha256_and_rows(mocked, data_dir):
    body = serve(mocked, make_rdb(SITE, default_rdb_rows(10)))
    fetch_daily_flow(SITE)
    (r,) = manifest.read_manifest()
    assert r["source"] == "usgs_dv" and r["rows"] == 10
    assert r["sha256"] == hashlib.sha256(body).hexdigest()
    assert r["url"].startswith("https://waterservices.usgs.gov/")
    assert SITE in json.dumps(r["params"])


def test_identical_refetch_is_idempotent(mocked, data_dir):
    serve(mocked, make_rdb(SITE, default_rdb_rows()))
    p1 = fetch_daily_flow(SITE)
    snap = {f: f.read_bytes() for f in raw_files(data_dir)}
    p2 = fetch_daily_flow(SITE)
    assert p1 == p2
    assert len(manifest.read_manifest()) == 1
    assert {f: f.read_bytes() for f in raw_files(data_dir)} == snap


def test_changed_upstream_never_overwrites_existing_raw_file(mocked, data_dir):
    old = make_rdb(SITE, [("2026-09-30", "10.0", "P")]).encode()
    new = make_rdb(SITE, [("2026-09-30", "11.0", "A")]).encode()  # provisional revised + approved
    mocked.add(responses.GET, USGS_URL, body=old)
    fetch_daily_flow(SITE)
    old_files = {f: f.read_bytes() for f in raw_files(data_dir)}
    mocked.replace(responses.GET, USGS_URL, body=new)
    fetch_daily_flow(SITE)
    for f, content in old_files.items():
        assert f.exists() and f.read_bytes() == content        # original untouched
    assert sorted(f.read_bytes() for f in raw_files(data_dir)) == sorted([old, new])
    assert len(manifest.read_manifest()) == 2                    # revision is recorded


def test_input_validation_before_network(mocked, data_dir):
    for bad in ["", "abc", "1234", None, "14161500; drop"]:
        with pytest.raises((ValueError, TypeError)):
            fetch_daily_flow(bad)
    assert len(mocked.calls) == 0


# ---------------------------------------------------------------- failure handling
def assert_clean_failure(data_dir):
    assert parquet_files(data_dir) == []                         # no partial parquet
    assert manifest.read_manifest() == []                        # failed fetch is not recorded


def test_500_is_retried_then_friendly_error(mocked, data_dir):
    mocked.add(responses.GET, USGS_URL, status=500)
    with pytest.raises(HttpError) as ei:
        fetch_daily_flow(SITE)
    assert ei.value.user_message and isinstance(ei.value, FetchError)
    assert len(mocked.calls) > 1
    assert_clean_failure(data_dir)


def test_timeout_friendly_error(mocked, data_dir):
    mocked.add(responses.GET, USGS_URL, body=requests.exceptions.ReadTimeout("t"))
    with pytest.raises(FetchError) as ei:
        fetch_daily_flow(SITE)
    assert ei.value.user_message
    assert_clean_failure(data_dir)


def test_404_not_retried(mocked, data_dir):
    mocked.add(responses.GET, USGS_URL, status=404, body=b"No sites found")
    with pytest.raises(HttpError) as ei:
        fetch_daily_flow(SITE)
    assert ei.value.status_code == 404 and len(mocked.calls) == 1
    assert_clean_failure(data_dir)


@pytest.mark.parametrize("body", [
    b"<html><body>Service Unavailable</body></html>",
    b"",
    b"\x00\x01\x02 binary garbage",
    make_rdb(SITE, [("2020-10-01", "abc", "A")]).encode(),               # non-numeric flow
    make_rdb(SITE, [("not-a-date", "1.0", "A")]).encode(),               # bad date
    make_rdb(SITE, [("2020-10-01", "1.0", "A")]).encode().replace(b"00060", b"00065"),  # wrong parameter
])
def test_malformed_payload_raises_friendly_error(mocked, data_dir, body):
    mocked.add(responses.GET, USGS_URL, body=body)
    with pytest.raises((MalformedPayloadError, EmptyResultError)) as ei:
        fetch_daily_flow(SITE)
    assert isinstance(ei.value, FallsoilError) and ei.value.user_message
    assert_clean_failure(data_dir)


def test_header_only_payload_is_empty_result_error(mocked, data_dir):
    serve(mocked, make_rdb(SITE, []))
    with pytest.raises(EmptyResultError) as ei:
        fetch_daily_flow(SITE)
    assert SITE in ei.value.user_message
    assert_clean_failure(data_dir)


def test_duplicate_dates_in_payload_are_kept_for_qc_not_collapsed(mocked, data_dir):
    rows = [("2020-10-01", "1.0", "A"), ("2020-10-01", "2.0", "P"), ("2020-10-02", "3.0", "A")]
    serve(mocked, make_rdb(SITE, rows))
    df = read(fetch_daily_flow(SITE))
    assert len(df) == 3


# ---------------------------------------------------------------- property
tokens = st.sampled_from(["A", "P", "e", "Ice", "Eqp"])


@given(toks=st.lists(tokens, max_size=3, unique=True))
def test_property_parse_qualifier_roundtrips_token_set(toks):
    assert set(parse_qualifier(" ".join(toks))) == set(toks)


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(flows=st.lists(st.floats(min_value=-50, max_value=1e6, allow_nan=False), min_size=1, max_size=30),
       quals=st.lists(st.sampled_from(["A", "P", "A e", "P e", ""]), min_size=30, max_size=30))
def test_property_every_row_survives_with_value_and_qualifier_intact(flows, quals, tmp_path_factory,
                                                                     monkeypatch):
    d = tmp_path_factory.mktemp("d")
    monkeypatch.setenv("FALLSOIL_DATA_DIR", str(d))
    days = pd.date_range("2000-01-01", periods=len(flows))
    rows = [(x.strftime("%Y-%m-%d"), repr(float(f)), quals[i]) for i, (x, f) in enumerate(zip(days, flows))]
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, USGS_URL, body=make_rdb(SITE, rows).encode())
        df = pd.read_parquet(fetch_daily_flow(SITE))
    assert len(df) == len(flows)
    assert np.allclose(df["flow_cfs"].to_numpy(), flows, rtol=0, atol=0)
    assert [("" if q is None else q) for q in df["qualifier"]] == quals[:len(flows)]
