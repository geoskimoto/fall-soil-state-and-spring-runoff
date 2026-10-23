"""Helpers shared by the fetchers: date handling, raw-response cache, parquet writing."""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from fallsoil.manifest import sha256_bytes

METADATA_KEY = b"fallsoil"


def as_date(value, name: str = "date") -> date:
    """Accept a `date`/`datetime` or an ISO `YYYY-MM-DD` string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc
    raise TypeError(f"{name} must be a date or ISO string, got {type(value).__name__}")


def cache_raw(directory: Path, stem: str, content: bytes, ext: str) -> Path:
    """Store the raw response byte-identical. Never overwrites: changed content gets its own file.

    The file name embeds the content hash, so an identical re-fetch resolves to the existing file.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}_{sha256_bytes(content)[:12]}{ext}"
    if not path.exists():
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, path)
    return path


def write_parquet(df: pd.DataFrame, path: Path, metadata: dict) -> Path:
    """Write `df` with `metadata` under the `fallsoil` schema key. Atomic: no partial file on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    existing = table.schema.metadata or {}
    table = table.replace_schema_metadata({**existing, METADATA_KEY: json.dumps(metadata, default=str).encode()})
    tmp = path.with_name(path.name + ".tmp")
    try:
        pq.write_table(table, tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path
