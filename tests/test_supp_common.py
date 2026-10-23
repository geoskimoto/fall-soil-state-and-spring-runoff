"""Supplementary tests for fallsoil.fetch._common: as_date, cache_raw, write_parquet."""
import json
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from fallsoil.fetch import _common
from fallsoil.fetch._common import as_date, cache_raw, write_parquet


# ---------------------------------------------------------------- as_date
def test_date_is_returned_unchanged():
    d = date(2021, 3, 4)
    assert as_date(d) == d and type(as_date(d)) is date


@pytest.mark.parametrize("dt", [datetime(2021, 3, 4, 23, 59, 59), datetime(2021, 3, 4, tzinfo=timezone.utc),
                                pd.Timestamp("2021-03-04 12:00")])
def test_datetime_returns_its_calendar_date_as_plain_date(dt):
    out = as_date(dt)
    assert out == date(2021, 3, 4) and type(out) is date


def test_iso_string_is_parsed():
    assert as_date("2021-03-04") == date(2021, 3, 4)
    assert type(as_date("2021-03-04")) is date


@pytest.mark.parametrize("bad", ["", "2021/03/04", "03-04-2021", "2021-13-01", "2021-02-30", "tomorrow",
                                 "2021-3-4x"])
def test_bad_strings_are_value_error(bad):
    with pytest.raises(ValueError):
        as_date(bad)


@pytest.mark.parametrize("bad", [None, 20210304, 2021.5, b"2021-03-04", ["2021-03-04"], np.datetime64("2021-03-04")])
def test_other_types_are_type_error(bad):
    with pytest.raises(TypeError):
        as_date(bad)


def test_name_appears_in_error_messages():
    with pytest.raises(ValueError, match="start"):
        as_date("junk", "start")
    with pytest.raises(TypeError, match="end"):
        as_date(5, "end")


def test_default_name_is_generic_date():
    with pytest.raises(ValueError, match="date"):
        as_date("junk")
    with pytest.raises(TypeError, match="date"):
        as_date(5)


# ---------------------------------------------------------------- cache_raw
def test_cache_raw_creates_nested_directories_and_stores_bytes_identically(tmp_path):
    target = tmp_path / "a" / "b" / "c"
    content = bytes(range(256)) + b"\r\n\x00"
    p = cache_raw(target, "stem", content, ".rdb")
    assert p.parent == target and p.suffix == ".rdb" and p.name.startswith("stem")
    assert p.read_bytes() == content


def test_cache_raw_identical_content_returns_same_path_and_one_file(tmp_path):
    a = cache_raw(tmp_path, "s", b"abc", ".json")
    b = cache_raw(tmp_path, "s", b"abc", ".json")
    assert a == b and len(list(tmp_path.iterdir())) == 1


def test_cache_raw_changed_content_gets_a_new_file_and_old_is_kept(tmp_path):
    a = cache_raw(tmp_path, "s", b"v1", ".nc")
    b = cache_raw(tmp_path, "s", b"v2", ".nc")
    assert a != b
    assert a.read_bytes() == b"v1" and b.read_bytes() == b"v2"
    assert len(list(tmp_path.iterdir())) == 2


def test_cache_raw_never_overwrites_an_existing_file(tmp_path):
    p = cache_raw(tmp_path, "s", b"original", ".json")
    p.write_bytes(b"tampered")
    again = cache_raw(tmp_path, "s", b"original", ".json")
    assert again == p and p.read_bytes() == b"tampered"


def test_cache_raw_leaves_no_temp_files(tmp_path):
    cache_raw(tmp_path, "s", b"x", ".rdb")
    cache_raw(tmp_path, "s", b"y", ".rdb")
    assert all(not f.name.endswith(".tmp") for f in tmp_path.iterdir())


def test_cache_raw_different_extension_or_stem_do_not_collide(tmp_path):
    a = cache_raw(tmp_path, "s", b"x", ".rdb")
    b = cache_raw(tmp_path, "s", b"x", ".json")
    c = cache_raw(tmp_path, "t", b"x", ".rdb")
    assert len({a, b, c}) == 3


def test_cache_raw_accepts_empty_content(tmp_path):
    p = cache_raw(tmp_path, "s", b"", ".rdb")
    assert p.read_bytes() == b""


# ---------------------------------------------------------------- write_parquet
def df3():
    return pd.DataFrame({"date": pd.date_range("2020-01-01", periods=3), "v": [1.0, np.nan, 3.0],
                         "q": ["A", "", "P e"]})


def meta_of(path):
    return json.loads(pq.read_schema(path).metadata[b"fallsoil"])


def test_write_parquet_round_trips_data_and_metadata(tmp_path):
    meta = {"source": "x", "units": {"v": "mm"}, "params": {"a": [1, 2]}, "rows": 3, "nested": {"k": None}}
    out = write_parquet(df3(), tmp_path / "d" / "e" / "f.parquet", meta)
    assert out == tmp_path / "d" / "e" / "f.parquet"
    back = pd.read_parquet(out)
    pd.testing.assert_frame_equal(back, df3(), check_dtype=False)
    assert meta_of(out) == meta


def test_metadata_key_is_exactly_fallsoil(tmp_path):
    out = write_parquet(df3(), tmp_path / "f.parquet", {"source": "x"})
    assert b"fallsoil" in pq.read_schema(out).metadata


def test_pandas_schema_metadata_is_preserved_alongside(tmp_path):
    out = write_parquet(df3(), tmp_path / "f.parquet", {"source": "x"})
    assert b"pandas" in pq.read_schema(out).metadata


def test_index_is_not_written_as_a_column(tmp_path):
    df = df3()
    df.index = [10, 20, 30]
    out = write_parquet(df, tmp_path / "f.parquet", {"source": "x"})
    names = pq.read_schema(out).names
    assert names == ["date", "v", "q"]
    back = pd.read_parquet(out)
    assert list(back.columns) == ["date", "v", "q"]


def test_non_json_native_metadata_values_are_stringified_not_fatal(tmp_path):
    out = write_parquet(df3(), tmp_path / "f.parquet", {"d": date(2020, 1, 2), "p": tmp_path})
    m = meta_of(out)
    assert m["d"] == "2020-01-02" and m["p"] == str(tmp_path)


def test_success_leaves_no_tmp_file(tmp_path):
    write_parquet(df3(), tmp_path / "f.parquet", {"source": "x"})
    assert [f.name for f in tmp_path.iterdir()] == ["f.parquet"]


def test_rewrite_replaces_the_target_atomically(tmp_path):
    p = tmp_path / "f.parquet"
    write_parquet(df3(), p, {"v": 1})
    new = df3().iloc[:1]
    write_parquet(new, p, {"v": 2})
    assert len(pd.read_parquet(p)) == 1 and meta_of(p) == {"v": 2}
    assert [f.name for f in tmp_path.iterdir()] == ["f.parquet"]


def test_failure_during_write_leaves_no_target_and_no_tmp(tmp_path, monkeypatch):
    def boom(table, where, *a, **k):
        open(where, "wb").write(b"PAR1 partial garbage")
        raise OSError("disk full")
    monkeypatch.setattr(_common.pq, "write_table", boom)
    with pytest.raises(OSError):
        write_parquet(df3(), tmp_path / "f.parquet", {"source": "x"})
    assert list(tmp_path.iterdir()) == []


def test_failure_does_not_damage_a_previously_written_target(tmp_path, monkeypatch):
    p = tmp_path / "f.parquet"
    write_parquet(df3(), p, {"v": 1})
    before = p.read_bytes()

    def boom(table, where, *a, **k):
        open(where, "wb").write(b"partial")
        raise OSError("disk full")
    monkeypatch.setattr(_common.pq, "write_table", boom)
    with pytest.raises(OSError):
        write_parquet(df3(), p, {"v": 2})
    assert p.read_bytes() == before
    assert [f.name for f in tmp_path.iterdir()] == ["f.parquet"]


def test_unconvertible_frame_fails_without_creating_files(tmp_path):
    bad = pd.DataFrame({"x": [1, "a", object()]})
    with pytest.raises(Exception):
        write_parquet(bad, tmp_path / "sub" / "f.parquet", {"source": "x"})
    assert not (tmp_path / "sub" / "f.parquet").exists()
    assert not list((tmp_path / "sub").glob("*.tmp")) if (tmp_path / "sub").exists() else True
