"""Supplementary snotel tests: request parameters, element handling, payload validation, column order."""
import json
import logging
from datetime import date
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pyarrow.parquet as pq
import pytest
import responses

from conftest import AWDB_URL, all_files, awdb_payload, awdb_series, parquet_files
from fallsoil import manifest
from fallsoil.errors import EmptyResultError, MalformedPayloadError
from fallsoil.fetch.snotel import fetch_snotel_daily

TRIP = "908:WA:SNTL"


def serve(mocked, text):
    mocked.add(responses.GET, AWDB_URL, body=text.encode())
    return text.encode()


def full_payload(depths=(2, 8, 20)):
    series = [awdb_series("WTEQ", [0.0, 1.0], unit="in"),
              awdb_series("PREC", [5.0, 5.5], unit="in"),
              awdb_series("TAVG", [32.0, 50.0], unit="degF")]
    for k, dep in enumerate(depths):
        series.append(awdb_series("SMS", [20.0 + k, 21.0 + k], unit="pct", depth_in=dep))
    return awdb_payload(TRIP, series)


def query(mocked, i=0):
    return {k: v[0] for k, v in parse_qs(urlparse(mocked.calls[i].request.url).query).items()}


def meta_of(path):
    return json.loads(pq.read_schema(path).metadata[b"fallsoil"])


# ---------------------------------------------------------------- request parameters
def test_request_parameters_with_explicit_dates(mocked, data_dir):
    serve(mocked, full_payload())
    fetch_snotel_daily(TRIP, ("WTEQ", "PREC", "TAVG", "SMS"), "2020-10-01", "2020-10-02")
    parsed = urlparse(mocked.calls[0].request.url)
    assert parsed.netloc == "wcc.sc.egov.usda.gov"
    assert parsed.path == "/awdbRestApi/services/v1/data"
    q = query(mocked)
    assert q["stationTriplets"] == TRIP
    assert q["elements"] == "WTEQ,PREC,TAVG,SMS"
    assert q["duration"] == "DAILY"
    assert q["beginDate"] == "2020-10-01" and q["endDate"] == "2020-10-02"


def test_elements_are_sent_in_requested_order(mocked, data_dir):
    serve(mocked, full_payload())
    fetch_snotel_daily(TRIP, ("TAVG", "WTEQ"), "2020-10-01", "2020-10-02")
    assert query(mocked)["elements"] == "TAVG,WTEQ"


def test_default_dates_send_an_early_begin_and_a_current_end(mocked, data_dir):
    serve(mocked, full_payload())
    fetch_snotel_daily(TRIP)
    q = query(mocked)
    begin, end = date.fromisoformat(q["beginDate"]), date.fromisoformat(q["endDate"])
    assert begin <= date(1990, 1, 1)
    assert end >= date(2026, 1, 1) and end >= begin
    assert q["elements"] == "WTEQ,PREC,TAVG,SMS"


def test_date_objects_are_sent_as_iso(mocked, data_dir):
    serve(mocked, full_payload())
    fetch_snotel_daily(TRIP, start=date(2020, 10, 1), end=date(2020, 10, 2))
    q = query(mocked)
    assert q["beginDate"] == "2020-10-01" and q["endDate"] == "2020-10-02"


# ---------------------------------------------------------------- outputs
def test_output_layout_and_metadata(mocked, data_dir):
    body = serve(mocked, full_payload())
    out = fetch_snotel_daily(TRIP, start="2020-10-01", end="2020-10-02")
    assert out.parent == data_dir / "interim" / "snotel" and out.suffix == ".parquet"
    raws = all_files(data_dir / "raw" / "snotel")
    assert len(raws) == 1 and raws[0].suffix == ".json" and raws[0].read_bytes() == body
    meta = meta_of(out)
    assert meta["source"] == "snotel_awdb" and meta["station"] == TRIP
    assert "PST" in meta["time_convention"]
    assert meta["units"]["swe_mm"] == "mm" and meta["units"]["precip_accum_mm"] == "mm"
    assert meta["units"]["temp_avg_c"] == "degC"
    assert meta["units"]["soil_moisture_pct_2in"] in {"percent", "pct", "%"}
    assert meta["url"] == "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data"
    assert meta["params"]["stationTriplets"] == TRIP
    assert meta["params"]["elements"] == "WTEQ,PREC,TAVG,SMS"
    assert meta["params"]["duration"] == "DAILY"
    assert meta["params"]["beginDate"] == "2020-10-01" and meta["params"]["endDate"] == "2020-10-02"
    assert meta["rows"] == 2
    assert meta["raw_file"] == raws[0].name


def test_manifest_record_reflects_request(mocked, data_dir):
    serve(mocked, full_payload())
    fetch_snotel_daily(TRIP, ("WTEQ",), "2020-10-01", "2020-10-02")
    (rec,) = manifest.read_manifest()
    assert rec["source"] == "snotel_awdb" and rec["rows"] == 2
    assert rec["params"]["stationTriplets"] == TRIP and rec["params"]["elements"] == "WTEQ"
    assert rec["params"]["duration"] == "DAILY"
    assert rec["params"]["beginDate"] == "2020-10-01"


def test_open_ended_fetch_manifest_end_is_stable_across_days(mocked, data_dir):
    # refetching identical content with no explicit end must dedupe (no today's date in provenance)
    serve(mocked, full_payload())
    fetch_snotel_daily(TRIP, start="2020-10-01")
    fetch_snotel_daily(TRIP, start="2020-10-01")
    assert len(manifest.read_manifest()) == 1


# ---------------------------------------------------------------- column order
def test_columns_follow_requested_element_order(mocked, data_dir):
    serve(mocked, full_payload(depths=(2,)))
    df = pd.read_parquet(fetch_snotel_daily(TRIP, ("TAVG", "SMS", "PREC", "WTEQ"), "2020-10-01", "2020-10-02"))
    assert list(df.columns) == ["date", "temp_avg_c", "soil_moisture_pct_2in", "precip_accum_mm", "swe_mm"]


def test_default_columns_are_in_default_element_order(mocked, data_dir):
    serve(mocked, full_payload(depths=(2,)))
    df = pd.read_parquet(fetch_snotel_daily(TRIP, start="2020-10-01", end="2020-10-02"))
    assert list(df.columns) == ["date", "swe_mm", "precip_accum_mm", "temp_avg_c", "soil_moisture_pct_2in"]


def test_soil_moisture_depths_are_in_numerically_ascending_order(mocked, data_dir):
    # payload lists depths in descending order; output must be ascending by depth (2, 8, 20),
    # not lexicographic ("20in" < "8in").
    serve(mocked, full_payload(depths=(20, 8, 2)))
    df = pd.read_parquet(fetch_snotel_daily(TRIP, ("SMS",), "2020-10-01", "2020-10-02"))
    assert list(df.columns) == ["date", "soil_moisture_pct_2in", "soil_moisture_pct_8in",
                                "soil_moisture_pct_20in"]


def test_soil_moisture_depths_ascending_with_mixed_digit_widths(mocked, data_dir):
    serve(mocked, full_payload(depths=(40, 4, 8)))
    df = pd.read_parquet(fetch_snotel_daily(TRIP, ("SMS",), "2020-10-01", "2020-10-02"))
    assert list(df.columns)[1:] == ["soil_moisture_pct_4in", "soil_moisture_pct_8in", "soil_moisture_pct_40in"]


# ---------------------------------------------------------------- input validation
def test_end_before_start_rejected_before_network(mocked, data_dir):
    with pytest.raises(ValueError):
        fetch_snotel_daily(TRIP, start="2020-10-02", end="2020-10-01")
    assert len(mocked.calls) == 0


def test_end_equal_to_start_is_allowed(mocked, data_dir):
    serve(mocked, awdb_payload(TRIP, [awdb_series("WTEQ", [1.0], unit="in")]))
    out = fetch_snotel_daily(TRIP, ("WTEQ",), "2020-10-01", "2020-10-01")
    assert len(pd.read_parquet(out)) == 1


@pytest.mark.parametrize("elements", [(), [], ""])
def test_empty_elements_rejected_before_network(mocked, data_dir, elements):
    with pytest.raises(ValueError):
        fetch_snotel_daily(TRIP, elements)
    assert len(mocked.calls) == 0


def test_single_string_element_is_accepted_as_one_element(mocked, data_dir):
    serve(mocked, full_payload())
    df = pd.read_parquet(fetch_snotel_daily(TRIP, "WTEQ", "2020-10-01", "2020-10-02"))
    assert list(df.columns) == ["date", "swe_mm"]
    assert query(mocked)["elements"] == "WTEQ"


def test_unknown_element_rejected_before_network(mocked, data_dir):
    with pytest.raises(ValueError):
        fetch_snotel_daily(TRIP, ("WTEQ", "BOGUS"))
    assert len(mocked.calls) == 0


@pytest.mark.parametrize("trip", [908, None, b"908:WA:SNTL", ["908:WA:SNTL"]])
def test_non_string_triplet_is_type_error(mocked, data_dir, trip):
    with pytest.raises(TypeError):
        fetch_snotel_daily(trip)
    assert len(mocked.calls) == 0


@pytest.mark.parametrize("trip", ["908", "908:WA", "abc:WA:SNTL", "908:wa:SNTL", "908:WA:SNTL:X", "", "908:WA:SNTL\n"])
def test_malformed_triplet_is_value_error(mocked, data_dir, trip):
    with pytest.raises(ValueError):
        fetch_snotel_daily(trip)
    assert len(mocked.calls) == 0


# ---------------------------------------------------------------- payload validation
def test_duplicate_dates_within_one_series_are_malformed(mocked, data_dir):
    s = awdb_series("WTEQ", [1.0, 2.0, 3.0], unit="in")
    s["values"][1]["date"] = s["values"][0]["date"]
    serve(mocked, awdb_payload(TRIP, [s]))
    with pytest.raises(MalformedPayloadError) as ei:
        fetch_snotel_daily(TRIP, ("WTEQ",), "2020-10-01", "2020-10-03")
    assert "WTEQ" in str(ei.value)
    assert parquet_files(data_dir) == [] and manifest.read_manifest() == []


def test_station_missing_from_payload_is_malformed(mocked, data_dir):
    serve(mocked, awdb_payload("999:WA:SNTL", [awdb_series("WTEQ", [1.0], unit="in")]))
    with pytest.raises(MalformedPayloadError) as ei:
        fetch_snotel_daily(TRIP, ("WTEQ",))
    assert TRIP in str(ei.value)
    assert parquet_files(data_dir) == []


def test_requested_station_found_among_several(mocked, data_dir):
    payload = json.dumps([
        {"stationTriplet": "999:WA:SNTL", "data": [awdb_series("WTEQ", [9.0], unit="in")]},
        {"stationTriplet": TRIP, "data": [awdb_series("WTEQ", [2.0], unit="in")]},
    ])
    serve(mocked, payload)
    df = pd.read_parquet(fetch_snotel_daily(TRIP, ("WTEQ",), "2020-10-01", "2020-10-01"))
    assert df["swe_mm"].tolist() == pytest.approx([2.0 * 25.4])


@pytest.mark.parametrize("payload", [
    '{"stationTriplet": "908:WA:SNTL"}',     # not a list
    '"text"', "42", "null",
])
def test_non_list_payload_is_malformed(mocked, data_dir, payload):
    serve(mocked, payload)
    with pytest.raises(MalformedPayloadError):
        fetch_snotel_daily(TRIP, ("WTEQ",))


def test_non_dict_station_entries_do_not_match(mocked, data_dir):
    serve(mocked, json.dumps(["x", 3, None]))
    with pytest.raises(MalformedPayloadError):
        fetch_snotel_daily(TRIP, ("WTEQ",))


def test_station_without_data_section_is_malformed(mocked, data_dir):
    serve(mocked, json.dumps([{"stationTriplet": TRIP}]))
    with pytest.raises(MalformedPayloadError):
        fetch_snotel_daily(TRIP, ("WTEQ",))


def test_station_with_empty_data_list_is_empty_result_naming_triplet(mocked, data_dir):
    serve(mocked, json.dumps([{"stationTriplet": TRIP, "data": []}]))
    with pytest.raises(EmptyResultError) as ei:
        fetch_snotel_daily(TRIP, ("WTEQ",))
    assert TRIP in str(ei.value)


def test_empty_list_payload_is_empty_result_naming_triplet(mocked, data_dir):
    serve(mocked, "[]")
    with pytest.raises(EmptyResultError) as ei:
        fetch_snotel_daily(TRIP, ("WTEQ",))
    assert TRIP in str(ei.value)


def test_malformed_series_entry_is_malformed(mocked, data_dir):
    serve(mocked, json.dumps([{"stationTriplet": TRIP, "data": ["not-a-dict"]}]))
    with pytest.raises(MalformedPayloadError):
        fetch_snotel_daily(TRIP, ("WTEQ",))


def test_series_without_values_list_is_malformed(mocked, data_dir):
    s = awdb_series("WTEQ", [1.0], unit="in")
    s["values"] = "oops"
    serve(mocked, awdb_payload(TRIP, [s]))
    with pytest.raises(MalformedPayloadError) as ei:
        fetch_snotel_daily(TRIP, ("WTEQ",))
    assert "WTEQ" in str(ei.value)


def test_value_entry_without_date_is_malformed(mocked, data_dir):
    s = awdb_series("WTEQ", [1.0, 2.0], unit="in")
    del s["values"][1]["date"]
    serve(mocked, awdb_payload(TRIP, [s]))
    with pytest.raises(MalformedPayloadError):
        fetch_snotel_daily(TRIP, ("WTEQ",))


def test_soil_moisture_series_without_depth_is_malformed(mocked, data_dir):
    s = awdb_series("SMS", [20.0], unit="pct", depth_in=None)
    serve(mocked, awdb_payload(TRIP, [s]))
    with pytest.raises(MalformedPayloadError) as ei:
        fetch_snotel_daily(TRIP, ("SMS",))
    assert "depth" in str(ei.value).lower()


def test_soil_moisture_series_with_non_numeric_depth_is_malformed(mocked, data_dir):
    s = awdb_series("SMS", [20.0], unit="pct", depth_in=2)
    s["stationElement"]["heightDepth"] = {"value": "deep", "unitCode": "in"}
    serve(mocked, awdb_payload(TRIP, [s]))
    with pytest.raises(MalformedPayloadError):
        fetch_snotel_daily(TRIP, ("SMS",))


def test_depth_given_as_dict_without_value_is_malformed(mocked, data_dir):
    s = awdb_series("SMS", [20.0], unit="pct", depth_in=2)
    s["stationElement"]["heightDepth"] = {"unitCode": "in"}
    serve(mocked, awdb_payload(TRIP, [s]))
    with pytest.raises(MalformedPayloadError):
        fetch_snotel_daily(TRIP, ("SMS",))


def test_series_without_element_code_is_malformed(mocked, data_dir):
    s = awdb_series("WTEQ", [1.0], unit="in")
    del s["stationElement"]["elementCode"]
    serve(mocked, awdb_payload(TRIP, [s]))
    with pytest.raises(MalformedPayloadError):
        fetch_snotel_daily(TRIP, ("WTEQ",))


def test_date_with_time_suffix_is_read_as_calendar_day(mocked, data_dir):
    s = awdb_series("WTEQ", [1.0], unit="in")
    s["values"][0]["date"] = "2020-10-01 00:00"
    serve(mocked, awdb_payload(TRIP, [s]))
    df = pd.read_parquet(fetch_snotel_daily(TRIP, ("WTEQ",)))
    assert df["date"].tolist() == [pd.Timestamp("2020-10-01")]


def test_truncated_date_is_malformed(mocked, data_dir):
    s = awdb_series("WTEQ", [1.0], unit="in")
    s["values"][0]["date"] = "2020-10-0"
    serve(mocked, awdb_payload(TRIP, [s]))
    with pytest.raises(MalformedPayloadError):
        fetch_snotel_daily(TRIP, ("WTEQ",))


def test_duplicate_series_for_same_column_keeps_first_and_warns(mocked, data_dir, caplog):
    a = awdb_series("WTEQ", [1.0], unit="in")
    b = awdb_series("WTEQ", [9.0], unit="in")
    serve(mocked, awdb_payload(TRIP, [a, b]))
    with caplog.at_level(logging.WARNING):
        df = pd.read_parquet(fetch_snotel_daily(TRIP, ("WTEQ",), "2020-10-01", "2020-10-01"))
    assert df["swe_mm"].tolist() == pytest.approx([25.4])
    assert any(r.levelno == logging.WARNING and ("WTEQ" in r.getMessage() or "swe_mm" in r.getMessage())
               for r in caplog.records)


def test_requested_element_absent_logs_warning_naming_code(mocked, data_dir, caplog):
    serve(mocked, awdb_payload(TRIP, [awdb_series("WTEQ", [1.0], unit="in")]))
    with caplog.at_level(logging.WARNING):
        df = pd.read_parquet(fetch_snotel_daily(TRIP, ("WTEQ", "TAVG"), "2020-10-01", "2020-10-01"))
    assert list(df.columns) == ["date", "swe_mm"]
    assert any(r.levelno == logging.WARNING and "TAVG" in r.getMessage() for r in caplog.records)


def test_unrequested_element_present_in_payload_is_ignored_without_warning(mocked, data_dir, caplog):
    serve(mocked, full_payload(depths=(2,)))
    with caplog.at_level(logging.WARNING):
        df = pd.read_parquet(fetch_snotel_daily(TRIP, ("WTEQ",), "2020-10-01", "2020-10-02"))
    assert list(df.columns) == ["date", "swe_mm"]
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_wrong_stored_unit_names_element(mocked, data_dir):
    serve(mocked, awdb_payload(TRIP, [awdb_series("PREC", [1.0], unit="mm")]))
    with pytest.raises(MalformedPayloadError) as ei:
        fetch_snotel_daily(TRIP, ("PREC",))
    assert "PREC" in str(ei.value) and "unit" in str(ei.value).lower()
