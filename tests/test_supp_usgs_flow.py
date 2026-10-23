"""Supplementary usgs_flow tests: request parameters, metadata, input checks, multi-series payloads."""
import json
import logging
from datetime import date
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pyarrow.parquet as pq
import pytest
import responses

from conftest import USGS_URL, all_files, default_rdb_rows, make_rdb, parquet_files
from fallsoil import manifest
from fallsoil.errors import MalformedPayloadError
from fallsoil.fetch.usgs_flow import fetch_daily_flow

SITE = "14161500"


def serve(mocked, text):
    mocked.add(responses.GET, USGS_URL, body=text.encode())
    return text.encode()


def query(mocked, i=0):
    return {k: v[0] for k, v in parse_qs(urlparse(mocked.calls[i].request.url).query).items()}


def meta_of(path):
    return json.loads(pq.read_schema(path).metadata[b"fallsoil"])


# ---------------------------------------------------------------- request parameters
def test_request_params_with_start_and_end(mocked, data_dir):
    serve(mocked, make_rdb(SITE, default_rdb_rows()))
    fetch_daily_flow(SITE, "2020-09-28", "2020-10-07")
    parsed = urlparse(mocked.calls[0].request.url)
    assert parsed.netloc == "waterservices.usgs.gov" and parsed.path.rstrip("/") == "/nwis/dv"
    q = query(mocked)
    assert q["sites"] == SITE and q["parameterCd"] == "00060" and q["statCd"] == "00003"
    assert q["format"] == "rdb"
    assert q["startDt"] == "2020-09-28" and q["endDt"] == "2020-10-07"


def test_no_end_means_no_endDt_parameter(mocked, data_dir):
    serve(mocked, make_rdb(SITE, default_rdb_rows()))
    fetch_daily_flow(SITE, "2020-09-28")
    q = query(mocked)
    assert "endDt" not in q and q["startDt"] == "2020-09-28"


def test_default_start_requests_the_full_historical_record(mocked, data_dir):
    # NWIS returns only the latest day when startDt is absent, so a start must be sent and be early.
    serve(mocked, make_rdb(SITE, default_rdb_rows()))
    fetch_daily_flow(SITE)
    q = query(mocked)
    assert "startDt" in q
    assert date.fromisoformat(q["startDt"]) <= date(1950, 1, 1)
    assert "endDt" not in q


def test_date_objects_are_sent_as_iso(mocked, data_dir):
    serve(mocked, make_rdb(SITE, default_rdb_rows()))
    fetch_daily_flow(SITE, date(2020, 9, 28), date(2020, 10, 7))
    q = query(mocked)
    assert q["startDt"] == "2020-09-28" and q["endDt"] == "2020-10-07"


# ---------------------------------------------------------------- outputs
def test_output_locations_extensions_and_metadata(mocked, data_dir):
    body = serve(mocked, make_rdb(SITE, default_rdb_rows()))
    out = fetch_daily_flow(SITE, "2020-09-28", "2020-10-07")
    assert out.parent == data_dir / "interim" / "usgs" and out.suffix == ".parquet"
    raws = all_files(data_dir / "raw" / "usgs")
    assert len(raws) == 1 and raws[0].suffix == ".rdb" and raws[0].read_bytes() == body
    meta = meta_of(out)
    assert meta["source"] == "usgs_dv" and meta["site"] == SITE
    assert meta["units"] == {"flow_cfs": "ft3/s"} or meta["units"] == {"flow_cfs": "cfs"}
    assert meta["url"] == "https://waterservices.usgs.gov/nwis/dv/"
    assert meta["params"]["sites"] == SITE and meta["params"]["parameterCd"] == "00060"
    assert meta["params"]["statCd"] == "00003" and meta["params"]["format"] == "rdb"
    assert meta["params"]["startDt"] == "2020-09-28" and meta["params"]["endDt"] == "2020-10-07"
    assert meta["rows"] == 10
    assert meta["raw_file"] == raws[0].name
    tc = meta["time_convention"].lower()
    assert "local standard time" in tc and "daily mean" in tc


def test_manifest_record_matches_request(mocked, data_dir):
    serve(mocked, make_rdb(SITE, default_rdb_rows()))
    fetch_daily_flow(SITE, "2020-09-28", "2020-10-07")
    (rec,) = manifest.read_manifest()
    assert rec["source"] == "usgs_dv" and rec["rows"] == 10
    assert rec["url"] == "https://waterservices.usgs.gov/nwis/dv/"
    assert rec["params"]["sites"] == SITE and rec["params"]["parameterCd"] == "00060"
    assert rec["params"]["statCd"] == "00003" and rec["params"]["startDt"] == "2020-09-28"


def test_rows_sorted_stably_with_duplicates_in_file_order_and_clean_index(mocked, data_dir):
    rows = [("2020-01-02", "5", "A"), ("2020-01-01", "1", "A"), ("2020-01-02", "9", "P")]
    serve(mocked, make_rdb(SITE, rows))
    out = fetch_daily_flow(SITE, "2020-01-01", "2020-01-02")
    df = pd.read_parquet(out)
    assert list(df.columns) == ["date", "flow_cfs", "qualifier"]
    assert df["date"].tolist() == list(pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-02"]))
    assert df["flow_cfs"].tolist() == [1.0, 5.0, 9.0]  # equal dates keep file order (stable)
    assert df["qualifier"].tolist() == ["A", "A", "P"]
    assert str(df["date"].dtype).startswith("datetime64")
    assert list(df.index) == [0, 1, 2]


# ---------------------------------------------------------------- input checks
def test_end_before_start_is_value_error_before_any_network_call(mocked, data_dir):
    with pytest.raises(ValueError):
        fetch_daily_flow(SITE, "2020-10-02", "2020-10-01")
    assert len(mocked.calls) == 0
    assert parquet_files(data_dir) == []


def test_end_equal_to_start_is_allowed(mocked, data_dir):
    serve(mocked, make_rdb(SITE, [("2020-10-01", "3.0", "A")]))
    out = fetch_daily_flow(SITE, "2020-10-01", "2020-10-01")
    assert len(pd.read_parquet(out)) == 1


@pytest.mark.parametrize("site", [14161500, None, 14161500.0, b"14161500"])
def test_non_string_site_is_type_error_before_network(mocked, data_dir, site):
    with pytest.raises(TypeError):
        fetch_daily_flow(site)
    assert len(mocked.calls) == 0


@pytest.mark.parametrize("site", ["", "1234567", "1234567A", "1" * 16, " 14161500", "14161500\n", "abc"])
def test_malformed_site_is_value_error_before_network(mocked, data_dir, site):
    with pytest.raises(ValueError):
        fetch_daily_flow(site)
    assert len(mocked.calls) == 0


@pytest.mark.parametrize("n", [8, 15])
def test_site_length_limits_are_inclusive(mocked, data_dir, n):
    site = "1" * n
    serve(mocked, make_rdb(site, [("2020-10-01", "3.0", "A")]))
    fetch_daily_flow(site, "2020-10-01", "2020-10-01")


@pytest.mark.parametrize("kw", [{"start": 20200101}, {"end": 20200101}, {"start": 1.5}])
def test_non_date_start_end_types_rejected_before_network(mocked, data_dir, kw):
    with pytest.raises(TypeError):
        fetch_daily_flow(SITE, **kw)
    assert len(mocked.calls) == 0


@pytest.mark.parametrize("kw", [{"start": "2020/01/01"}, {"end": "nope"}, {"start": "2020-13-01"}])
def test_bad_date_strings_rejected_before_network(mocked, data_dir, kw):
    with pytest.raises(ValueError):
        fetch_daily_flow(SITE, **kw)
    assert len(mocked.calls) == 0


# ---------------------------------------------------------------- multiple discharge series
def two_series_rdb(site=SITE):
    c1, c2 = "113793_00060_00003", "113794_00060_00003"
    head = (f"# header\n#\nagency_cd\tsite_no\tdatetime\t{c1}\t{c1}_cd\t{c2}\t{c2}_cd\n"
            "5s\t15s\t20d\t14n\t10s\t14n\t10s\n")
    body = (f"USGS\t{site}\t2020-10-01\t10.0\tA\t99.0\tP\n"
            f"USGS\t{site}\t2020-10-02\t11.0\tA\t98.0\tP\n")
    return head + body


def test_two_discharge_series_uses_first_and_warns(mocked, data_dir, caplog):
    serve(mocked, two_series_rdb())
    with caplog.at_level(logging.WARNING):
        out = fetch_daily_flow(SITE, "2020-10-01", "2020-10-02")
    df = pd.read_parquet(out)
    assert df["flow_cfs"].tolist() == [10.0, 11.0]
    assert df["qualifier"].tolist() == ["A", "A"]
    warns = [r for r in caplog.records if r.levelno == logging.WARNING and r.name.startswith("fallsoil")]
    assert warns and any(SITE in r.getMessage() for r in warns)


def test_single_series_emits_no_warning(mocked, data_dir, caplog):
    serve(mocked, make_rdb(SITE, default_rdb_rows()))
    with caplog.at_level(logging.WARNING):
        fetch_daily_flow(SITE, "2020-09-28", "2020-10-07")
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


# ---------------------------------------------------------------- malformed structure
@pytest.mark.parametrize("text", [
    "agency_cd\tsite_no\tdatetime\n",            # a header line alone is not a table
    "just one line without tabs\n",
    "",
    "# only comments\n# nothing else\n",
])
def test_degenerate_bodies_are_malformed(mocked, data_dir, text):
    serve(mocked, text)
    with pytest.raises(MalformedPayloadError):
        fetch_daily_flow(SITE, "2020-10-01", "2020-10-02")
    assert parquet_files(data_dir) == [] and manifest.read_manifest() == []


def test_undecodable_bytes_are_malformed(mocked, data_dir):
    mocked.add(responses.GET, USGS_URL, body=b"\xff\xfe\x00\x80\x81")
    with pytest.raises(MalformedPayloadError):
        fetch_daily_flow(SITE, "2020-10-01", "2020-10-02")
    assert parquet_files(data_dir) == []
