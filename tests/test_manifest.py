"""manifest: append-only JSONL provenance log; identical content is not duplicated."""
import hashlib
import json
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings, strategies as st

from fallsoil import manifest
from fallsoil.errors import FallsoilError

URL = "https://example.test/x"


def rec(path, content=b"abc", params=None, source="usgs_dv", url=URL, rows=3):
    return manifest.record_fetch(source=source, url=url, params=params or {"site": "1"},
                                 content=content, rows=rows, path=path)


def test_sha256_matches_hashlib():
    assert manifest.sha256_bytes(b"hello") == hashlib.sha256(b"hello").hexdigest()
    assert len(manifest.sha256_bytes(b"")) == 64


def test_first_record_is_appended_with_all_required_fields(tmp_path):
    p = tmp_path / "m.jsonl"
    assert rec(p) is True
    (r,) = manifest.read_manifest(p)
    assert set(r) >= {"source", "url", "params", "retrieved_utc", "sha256", "rows", "code_version"}
    assert r["source"] == "usgs_dv" and r["url"] == URL and r["params"] == {"site": "1"}
    assert r["sha256"] == hashlib.sha256(b"abc").hexdigest()
    assert r["rows"] == 3
    assert isinstance(r["code_version"], str) and r["code_version"]
    ts = datetime.fromisoformat(r["retrieved_utc"].replace("Z", "+00:00"))
    assert ts.tzinfo is not None and ts.utcoffset().total_seconds() == 0


def test_file_is_jsonl_one_object_per_line(tmp_path):
    p = tmp_path / "m.jsonl"
    rec(p); rec(p, content=b"other")
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    assert all(isinstance(json.loads(l), dict) for l in lines)


def test_identical_refetch_adds_no_duplicate(tmp_path):
    p = tmp_path / "m.jsonl"
    assert rec(p) is True
    before = p.read_bytes()
    assert rec(p) is False
    assert p.read_bytes() == before
    assert len(manifest.read_manifest(p)) == 1


def test_param_order_does_not_defeat_idempotence(tmp_path):
    p = tmp_path / "m.jsonl"
    rec(p, params={"a": 1, "b": 2})
    assert rec(p, params={"b": 2, "a": 1}) is False


def test_changed_content_appends_new_record_and_keeps_old_line_untouched(tmp_path):
    p = tmp_path / "m.jsonl"
    rec(p, content=b"v1")
    first_line = p.read_bytes()
    assert rec(p, content=b"v2") is True
    assert p.read_bytes().startswith(first_line)  # append-only
    shas = [r["sha256"] for r in manifest.read_manifest(p)]
    assert shas == [hashlib.sha256(b"v1").hexdigest(), hashlib.sha256(b"v2").hexdigest()]


def test_same_content_different_params_or_source_is_a_distinct_record(tmp_path):
    p = tmp_path / "m.jsonl"
    rec(p)
    assert rec(p, params={"site": "2"}) is True
    assert rec(p, source="snotel_awdb") is True
    assert len(manifest.read_manifest(p)) == 3


def test_content_returning_to_an_earlier_version_is_not_hidden(tmp_path):
    # v1, v2, v1 again: v1 already recorded for these params, so no duplicate line is added,
    # but the history stays intact and ordered.
    p = tmp_path / "m.jsonl"
    rec(p, content=b"v1"); rec(p, content=b"v2")
    assert rec(p, content=b"v1") is False
    assert len(manifest.read_manifest(p)) == 2


def test_missing_manifest_reads_as_empty(tmp_path):
    assert manifest.read_manifest(tmp_path / "nope.jsonl") == []


def test_default_path_is_data_dir_manifest(data_dir):
    manifest.record_fetch(source="s", url=URL, params={}, content=b"z", rows=0)
    assert (data_dir / "manifest.jsonl").exists()
    assert len(manifest.read_manifest()) == 1


def test_parent_directory_is_created(tmp_path):
    p = tmp_path / "sub" / "m.jsonl"
    rec(p)
    assert p.exists()


def test_corrupt_line_raises_friendly_error_and_does_not_rewrite_file(tmp_path):
    p = tmp_path / "m.jsonl"
    rec(p)
    with p.open("a") as f:
        f.write("{not json\n")
    before = p.read_bytes()
    with pytest.raises(FallsoilError) as ei:
        manifest.read_manifest(p)
    assert "line 2" in str(ei.value) or "2" in str(ei.value)
    with pytest.raises(FallsoilError):
        rec(p, content=b"new")
    assert p.read_bytes() == before


@settings(max_examples=40, deadline=None)
@given(blobs=st.lists(st.binary(max_size=40), min_size=1, max_size=8))
def test_property_one_record_per_distinct_content(tmp_path_factory, blobs):
    p = tmp_path_factory.mktemp("m") / "m.jsonl"
    for b in blobs:
        rec(p, content=b)
        rec(p, content=b)  # immediate repeat never duplicates
    assert len(manifest.read_manifest(p)) == len(set(blobs))
    assert [r["sha256"] for r in manifest.read_manifest(p)] == [
        hashlib.sha256(b).hexdigest() for b in dict.fromkeys(blobs)]
