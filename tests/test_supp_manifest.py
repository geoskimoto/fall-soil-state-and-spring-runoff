"""Supplementary manifest tests: missing trailing newline, concurrent appends, structural corruption."""
import json
import multiprocessing as mp

import pytest

from fallsoil import manifest
from fallsoil.errors import ManifestError

URL = "https://example.test/x"


def rec(path, content=b"abc", params=None, source="usgs_dv", url=URL, rows=3):
    return manifest.record_fetch(source=source, url=url, params=params or {"site": "1"},
                                 content=content, rows=rows, path=path)


def valid_line(content=b"seed", **over):
    d = {"source": "usgs_dv", "url": URL, "params": {"site": "seed"},
         "retrieved_utc": "2021-01-01T00:00:00+00:00", "sha256": manifest.sha256_bytes(content),
         "rows": 1, "code_version": "abc"}
    d.update(over)
    return json.dumps(d)


def lines_of(path):
    return path.read_bytes().decode().split("\n")


# ---------------------------------------------------------------- trailing newline handling
def test_append_after_line_without_trailing_newline_keeps_both_records_valid(tmp_path):
    p = tmp_path / "m.jsonl"
    p.write_bytes(valid_line().encode())  # no trailing newline
    assert rec(p, content=b"new") is True
    raw = p.read_bytes().decode()
    assert raw.endswith("\n")
    parts = raw.rstrip("\n").split("\n")
    assert len(parts) == 2
    assert [json.loads(x)["sha256"] for x in parts] == [manifest.sha256_bytes(b"seed"),
                                                        manifest.sha256_bytes(b"new")]
    assert len(manifest.read_manifest(p)) == 2


def test_append_after_properly_terminated_file_adds_no_blank_line(tmp_path):
    p = tmp_path / "m.jsonl"
    rec(p, content=b"a")
    rec(p, content=b"b")
    raw = p.read_bytes().decode()
    assert raw.endswith("\n") and "\n\n" not in raw
    assert raw.count("\n") == 2


def test_existing_bytes_are_a_prefix_after_append(tmp_path):
    p = tmp_path / "m.jsonl"
    rec(p, content=b"a")
    before = p.read_bytes()
    rec(p, content=b"b")
    assert p.read_bytes().startswith(before)


def test_every_record_ends_with_exactly_one_newline(tmp_path):
    p = tmp_path / "m.jsonl"
    for i in range(4):
        rec(p, content=str(i).encode())
    assert p.read_bytes().count(b"\n") == 4
    assert all(json.loads(l) for l in p.read_text().splitlines())


def test_default_path_is_manifest_jsonl_in_data_dir_and_parents_are_created(data_dir):
    assert manifest.record_fetch(source="s", url=URL, params={}, content=b"x", rows=0) is True
    assert (data_dir / "manifest.jsonl").exists()
    assert len(manifest.read_manifest()) == 1
    nested = data_dir / "a" / "b" / "m.jsonl"
    assert rec(nested) is True and nested.exists()


def test_params_order_and_nesting_dedupe_but_value_differences_do_not(tmp_path):
    p = tmp_path / "m.jsonl"
    assert rec(p, params={"a": 1, "b": {"x": 1, "y": 2}}) is True
    assert rec(p, params={"b": {"y": 2, "x": 1}, "a": 1}) is False
    assert rec(p, params={"a": 1, "b": {"x": 1, "y": 3}}) is True
    assert len(manifest.read_manifest(p)) == 2


def test_each_key_component_distinguishes_records(tmp_path):
    p = tmp_path / "m.jsonl"
    assert rec(p) is True
    assert rec(p, source="other") is True
    assert rec(p, url=URL + "2") is True
    assert rec(p, content=b"zzz") is True
    assert rec(p, params={"site": "2"}) is True
    assert rec(p) is False
    assert len(manifest.read_manifest(p)) == 5


def test_rows_difference_alone_does_not_create_a_new_record(tmp_path):
    p = tmp_path / "m.jsonl"
    assert rec(p, rows=3) is True
    assert rec(p, rows=99) is False


# ---------------------------------------------------------------- concurrency
def _worker(path, wid, n, barrier, results):
    barrier.wait()
    out = []
    for i in range(n):
        out.append(rec_for(path, f"w{wid}-{i}".encode()))
    out.append(rec_for(path, b"shared-by-everyone"))
    results.put((wid, out))


def rec_for(path, content):
    return manifest.record_fetch(source="usgs_dv", url=URL, params={"k": "v"}, content=content,
                                 rows=1, path=path)


def test_concurrent_processes_neither_lose_duplicate_nor_interleave_records(tmp_path):
    ctx = mp.get_context("fork")
    p = tmp_path / "m.jsonl"
    nproc, per = 6, 8
    barrier, results = ctx.Barrier(nproc), ctx.Queue()
    procs = [ctx.Process(target=_worker, args=(p, w, per, barrier, results)) for w in range(nproc)]
    for pr in procs:
        pr.start()
    got = [results.get(timeout=120) for _ in procs]
    for pr in procs:
        pr.join(timeout=120)
        assert pr.exitcode == 0

    lines = [l for l in p.read_text().split("\n") if l != ""]
    records = [json.loads(l) for l in lines]  # raises if any line is torn or merged
    assert len(records) == nproc * per + 1
    shas = [r["sha256"] for r in records]
    assert len(set(shas)) == len(shas)  # no duplicates
    expected = {manifest.sha256_bytes(f"w{w}-{i}".encode()) for w in range(nproc) for i in range(per)}
    expected.add(manifest.sha256_bytes(b"shared-by-everyone"))
    assert set(shas) == expected  # nothing lost
    shared_results = [out[-1] for _, out in got]
    assert shared_results.count(True) == 1  # identical concurrent record deduped to one writer
    assert all(all(out[:-1]) for _, out in got)  # every distinct record reported as newly written
    assert len(manifest.read_manifest(p)) == len(records)


def test_concurrent_identical_records_collapse_to_one(tmp_path):
    ctx = mp.get_context("fork")
    p = tmp_path / "m.jsonl"
    n = 8
    barrier, results = ctx.Barrier(n), ctx.Queue()

    def target(path, barrier, results):
        barrier.wait()
        results.put(rec_for(path, b"same"))

    procs = [ctx.Process(target=target, args=(p, barrier, results)) for _ in range(n)]
    for pr in procs:
        pr.start()
    outs = [results.get(timeout=120) for _ in procs]
    for pr in procs:
        pr.join(timeout=120)
    assert sorted(outs) == [False] * (n - 1) + [True]
    assert len(manifest.read_manifest(p)) == 1
    assert p.read_text().count("\n") == 1


# ---------------------------------------------------------------- structural corruption
def snapshot(p):
    return p.read_bytes()


@pytest.mark.parametrize("bad, label", [
    ("[1, 2, 3]", "array"),
    ('"just a string"', "string"),
    ("42", "number"),
    ("null", "null"),
    ("{not json", "garbage"),
    (json.dumps({"source": "s", "url": "u", "params": {}}), "missing sha256"),
    (json.dumps({"url": "u", "params": {}, "sha256": "x"}), "missing source"),
    (json.dumps({"source": "s", "params": {}, "sha256": "x"}), "missing url"),
    (json.dumps({"source": "s", "url": "u", "sha256": "x"}), "missing params"),
])
def test_bad_line_raises_manifest_error_naming_line_and_leaves_file_untouched(tmp_path, bad, label):
    p = tmp_path / "m.jsonl"
    p.write_text(valid_line() + "\n" + bad + "\n" + valid_line(b"later") + "\n")
    before = snapshot(p)
    with pytest.raises(ManifestError) as ei:
        manifest.read_manifest(p)
    assert "2" in str(ei.value), label
    import re
    assert re.search(r"line\s*2\b", str(ei.value)), str(ei.value)
    with pytest.raises(ManifestError) as ei2:
        rec(p, content=b"fresh")
    assert re.search(r"line\s*2\b", str(ei2.value))
    assert snapshot(p) == before


def test_line_numbers_count_blank_lines_as_physical_lines(tmp_path):
    p = tmp_path / "m.jsonl"
    p.write_text(valid_line() + "\n\n[1]\n")
    with pytest.raises(ManifestError) as ei:
        manifest.read_manifest(p)
    import re
    assert re.search(r"line\s*3\b", str(ei.value))


def test_corrupt_first_line_is_reported_as_line_one(tmp_path):
    p = tmp_path / "m.jsonl"
    p.write_text("[]\n" + valid_line() + "\n")
    with pytest.raises(ManifestError) as ei:
        manifest.read_manifest(p)
    import re
    assert re.search(r"line\s*1\b", str(ei.value))


def test_blank_lines_are_tolerated(tmp_path):
    p = tmp_path / "m.jsonl"
    p.write_text("\n" + valid_line() + "\n\n   \n" + valid_line(b"b") + "\n")
    assert len(manifest.read_manifest(p)) == 2


def test_missing_manifest_reads_as_empty_and_default_missing_too(tmp_path, data_dir):
    assert manifest.read_manifest(tmp_path / "nope.jsonl") == []
    assert manifest.read_manifest() == []
