"""Configuration: the data directory comes from FALLSOIL_DATA_DIR (environment, then ./.env)."""
from __future__ import annotations

import os
from pathlib import Path

from fallsoil.errors import ConfigError

ENV_VAR = "FALLSOIL_DATA_DIR"
SOURCES = ("usgs", "snotel", "gridmet")


def _from_dotenv(name: str) -> str | None:
    """Read `name` from ./.env (simple KEY=VALUE lines; optional quotes; # comments)."""
    path = Path.cwd() / ".env"
    if not path.is_file():
        return None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip().strip("'\"")
    return None


def get_data_dir() -> Path:
    """Resolve and create the data directory. The real environment wins over ./.env."""
    raw = os.environ.get(ENV_VAR)
    if raw is None or not raw.strip():
        raw = _from_dotenv(ENV_VAR)
    if raw is None or not raw.strip():
        raise ConfigError(
            f"{ENV_VAR} is not set. Set it in the environment or in a .env file to the directory "
            "where fetched data should be stored."
        )
    path = Path(raw.strip())
    if path.exists() and not path.is_dir():
        raise ConfigError(f"{ENV_VAR} points to {path}, which is not a directory.")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"Cannot create the data directory {path} ({ENV_VAR}): {exc.strerror}.") from exc
    if not os.access(path, os.W_OK | os.X_OK):
        raise ConfigError(f"The data directory {path} ({ENV_VAR}) is not writable.")
    return path


def _subdir(kind: str, source: str) -> Path:
    if not isinstance(source, str) or not source.strip() or source in {".", ".."} or "/" in source or "\\" in source:
        raise ValueError(f"invalid source directory name: {source!r}")
    path = get_data_dir() / kind / source
    path.mkdir(parents=True, exist_ok=True)
    return path


def raw_dir(source: str) -> Path:
    """<data>/raw/<source>, created on demand. Raw responses are cached here and never overwritten."""
    return _subdir("raw", source)


def interim_dir(source: str) -> Path:
    """<data>/interim/<source>, created on demand. Basin-level parquet outputs live here."""
    return _subdir("interim", source)


def manifest_path() -> Path:
    """<data>/manifest.jsonl (append-only provenance log)."""
    return get_data_dir() / "manifest.jsonl"
