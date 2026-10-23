"""USGS NWIS daily-value mean discharge (parameter 00060, statistic 00003) -> basin parquet.

Daily values are daily means in the gauge's local standard time. Provisional ("P") and estimated
("e") qualifiers are kept verbatim; negative, missing and duplicate rows are kept for QC to flag.
"""
from __future__ import annotations

import io
import logging
import re
from pathlib import Path

import pandas as pd

from fallsoil import config, manifest
from fallsoil.errors import EmptyResultError, MalformedPayloadError
from fallsoil.fetch import http
from fallsoil.fetch._common import as_date, cache_raw, write_parquet

log = logging.getLogger(__name__)

URL = "https://waterservices.usgs.gov/nwis/dv/"
SOURCE = "usgs_dv"
PARAM_CD, STAT_CD = "00060", "00003"
DEFAULT_START = "1900-01-01"  # NWIS returns only the latest day if no start is given
_SITE_RE = re.compile(r"\d{8,15}")  # matched with fullmatch: `$` would accept a trailing newline
TIME_CONVENTION = (
    "Daily mean discharge; each date is a calendar day in the gauge's local standard time "
    "(USGS reports daily values in local standard time, not UTC)."
)


def parse_qualifier(code: str | None) -> frozenset[str]:
    """Split a qualifier string such as 'A e' into its tokens; blank/None gives an empty set."""
    return frozenset(code.split()) if code else frozenset()


def _check_site(site) -> str:
    if not isinstance(site, str):
        raise TypeError(f"site must be a string of 8-15 digits, got {type(site).__name__}")
    if not _SITE_RE.fullmatch(site):
        raise ValueError(f"site must be 8-15 digits (a USGS site number), got {site!r}")
    return site


def _parse_rdb(text: str, site: str) -> pd.DataFrame:
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    if len(lines) < 2 or "\t" not in lines[0]:
        raise MalformedPayloadError(
            f"USGS did not return a daily-values table for site {site}; the response was not in the expected format."
        )
    header = lines[0].split("\t")
    value_cols = [c for c in header if c.endswith(f"_{PARAM_CD}_{STAT_CD}")]
    if "datetime" not in header or not value_cols:
        raise MalformedPayloadError(
            f"USGS response for site {site} has no daily mean discharge (parameter {PARAM_CD}) column."
        )
    if len(value_cols) > 1:
        log.warning("site %s has %d discharge series; using the first (%s)", site, len(value_cols), value_cols[0])
    vcol = value_cols[0]
    qcol = f"{vcol}_cd" if f"{vcol}_cd" in header else None
    if len(lines) == 2:  # header + format row only
        raise EmptyResultError(f"USGS returned no daily discharge values for site {site} in the requested period.")

    raw = pd.read_csv(io.StringIO("\n".join([lines[0]] + lines[2:])), sep="\t", dtype=str, keep_default_na=False)
    dates = pd.to_datetime(raw["datetime"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any():
        bad = raw["datetime"][dates.isna()].iloc[0]
        raise MalformedPayloadError(f"USGS response for site {site} contains an unreadable date: {bad!r}.")
    flow = pd.to_numeric(raw[vcol].str.strip().replace("", pd.NA), errors="coerce")
    nonnumeric = flow.isna() & raw[vcol].str.strip().ne("")
    if nonnumeric.any():
        bad = raw[vcol][nonnumeric].iloc[0]
        raise MalformedPayloadError(f"USGS response for site {site} contains a non-numeric flow value: {bad!r}.")
    out = pd.DataFrame(
        {
            "date": dates,
            "flow_cfs": flow.astype("float64"),
            "qualifier": raw[qcol].str.strip() if qcol else "",
        }
    )
    return out.sort_values("date", kind="stable").reset_index(drop=True)


def fetch_daily_flow(site: str, start=None, end=None) -> Path:
    """Fetch daily mean discharge for `site`, cache the raw RDB, write parquet, record provenance."""
    site = _check_site(site)
    start_d = as_date(start, "start") if start is not None else None
    end_d = as_date(end, "end") if end is not None else None
    if start_d and end_d and end_d < start_d:
        raise ValueError("end must not be before start")

    params = {
        "format": "rdb", "sites": site, "parameterCd": PARAM_CD, "statCd": STAT_CD,
        "startDt": start_d.isoformat() if start_d else DEFAULT_START,
    }
    if end_d:
        params["endDt"] = end_d.isoformat()

    body = http.get(URL, params)
    try:
        df = _parse_rdb(body.decode("utf-8"), site)
    except UnicodeDecodeError as exc:
        raise MalformedPayloadError(f"USGS response for site {site} was not readable text.") from exc

    stem = f"usgs_dv_{site}_{params['startDt']}_{params.get('endDt', 'latest')}"
    raw_path = cache_raw(config.raw_dir("usgs"), stem, body, ".rdb")
    metadata = {
        "source": SOURCE, "site": site, "time_convention": TIME_CONVENTION,
        "statistic": "daily_mean", "units": {"flow_cfs": "ft3/s"},
        "url": URL, "params": params, "raw_file": raw_path.name, "rows": len(df),
    }
    out = write_parquet(df, config.interim_dir("usgs") / f"{stem}.parquet", metadata)
    manifest.record_fetch(source=SOURCE, url=URL, params=params, content=body, rows=len(df))
    return out
