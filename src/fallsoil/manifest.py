"""Append-only JSONL provenance log of every fetch (source, URL, params, content hash, code version)."""
from __future__ import annotations

import fcntl
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from fallsoil import __version__, config
from fallsoil.errors import ManifestError


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@lru_cache(maxsize=1)
def _code_version() -> str:
    """Short git commit of the package source if available, else the package version."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip() or __version__
    except (OSError, subprocess.SubprocessError):
        return __version__


def _canonical_params(params: Mapping) -> str:
    return json.dumps(params, sort_keys=True, default=str, separators=(",", ":"))


def _key(record: Mapping) -> tuple:
    return record["source"], record["url"], _canonical_params(record["params"]), record["sha256"]


def read_manifest(path: Path | None = None) -> list[dict]:
    """All records in file order. A missing file reads as empty; a corrupt line raises ManifestError."""
    path = Path(path) if path is not None else config.manifest_path()
    if not path.exists():
        return []
    records = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict):
                    raise ValueError("not an object")
                _key(rec)
            except (ValueError, KeyError) as exc:
                raise ManifestError(
                    f"The manifest {path} is corrupt at line {lineno} ({exc}). "
                    "It was left untouched; fix or restore that line before fetching again."
                ) from exc
            records.append(rec)
    return records


def record_fetch(
    *, source: str, url: str, params: Mapping, content: bytes, rows: int, path: Path | None = None
) -> bool:
    """Append a provenance record. Returns False (writing nothing) if an identical one exists."""
    path = Path(path) if path is not None else config.manifest_path()
    record = {
        "source": source,
        "url": url,
        "params": dict(params),
        "retrieved_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": sha256_bytes(content),
        "rows": int(rows),
        "code_version": _code_version(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as fh:  # lock so concurrent fetchers cannot interleave or duplicate lines
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            if any(_key(r) == _key(record) for r in read_manifest(path)):
                return False
            fh.seek(0, 2)
            if fh.tell() > 0:
                fh.seek(-1, 2)
                needs_newline = fh.read(1) != b"\n"
                fh.seek(0, 2)
                if needs_newline:
                    fh.write(b"\n")
            fh.write((json.dumps(record, default=str) + "\n").encode("utf-8"))
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    return True
