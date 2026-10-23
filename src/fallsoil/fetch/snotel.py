"""NRCS AWDB daily SNOTEL data -> station parquet in metric, unit-named columns.

AWDB stores SWE and accumulated precipitation in inches, air temperature in degrees F, and soil
moisture in percent. Daily values use the station's fixed standard time (PST, UTC-8, no daylight
saving; every WA/OR/ID/MT SNOTEL station reports dataTimeZone -8).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from fallsoil import config, manifest
from fallsoil.errors import EmptyResultError, MalformedPayloadError
from fallsoil.fetch import http
from fallsoil.fetch._common import as_date, cache_raw, write_parquet

log = logging.getLogger(__name__)

URL = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data"
SOURCE = "snotel_awdb"
DEFAULT_START = "1950-01-01"
MM_PER_INCH = 25.4
_TRIPLET_RE = re.compile(r"\d+:[A-Z]{2}:[A-Z]+")  # matched with fullmatch: `$` would accept a trailing newline
TIME_CONVENTION = (
    "Daily values in the station's fixed standard time: PST (UTC-8, no daylight saving), "
    "as reported by NRCS AWDB for SNOTEL stations."
)
# element -> (output column, required storedUnitCode, output unit)
_ELEMENTS = {
    "WTEQ": ("swe_mm", "in", "mm"),
    "PREC": ("precip_accum_mm", "in", "mm"),
    "TAVG": ("temp_avg_c", "degF", "degC"),
    "SMS": ("soil_moisture_pct", "pct", "percent"),
}
DEFAULT_ELEMENTS = ("WTEQ", "PREC", "TAVG", "SMS")


# ---------------------------------------------------------------- unit conversions (NaN-propagating)
def inches_to_mm(x):
    return x * MM_PER_INCH


def mm_to_inches(x):
    return x / MM_PER_INCH


def fahrenheit_to_celsius(x):
    return (x - 32.0) * 5.0 / 9.0


def celsius_to_fahrenheit(x):
    return x * 9.0 / 5.0 + 32.0


_CONVERT = {"WTEQ": inches_to_mm, "PREC": inches_to_mm, "TAVG": fahrenheit_to_celsius, "SMS": lambda x: x}


# ---------------------------------------------------------------- parsing
def _check_triplet(triplet) -> str:
    if not isinstance(triplet, str):
        raise TypeError(f"triplet must be a string like '908:WA:SNTL', got {type(triplet).__name__}")
    if not _TRIPLET_RE.fullmatch(triplet):
        raise ValueError(f"triplet must look like '908:WA:SNTL' (id:STATE:NETWORK), got {triplet!r}")
    return triplet


def _check_elements(elements) -> tuple[str, ...]:
    if isinstance(elements, str):
        elements = (elements,)
    elements = tuple(elements)
    if not elements:
        raise ValueError("elements must not be empty")
    unknown = [e for e in elements if e not in _ELEMENTS]
    if unknown:
        raise ValueError(f"unknown element code(s) {unknown}; supported: {', '.join(_ELEMENTS)}")
    return tuple(dict.fromkeys(elements))


def _series_to_column(series: dict, triplet: str) -> tuple[str, str, pd.Series] | None:
    """Return (element code, output column name, converted values indexed by date)."""
    element = series.get("stationElement")
    values = series.get("values")
    if not isinstance(element, dict) or "elementCode" not in element:
        raise MalformedPayloadError(f"AWDB response for {triplet} has a series without an element code.")
    code = element["elementCode"]
    if code not in _ELEMENTS:
        return None
    column, unit_expected, _ = _ELEMENTS[code]
    unit = element.get("storedUnitCode")
    if unit != unit_expected:
        raise MalformedPayloadError(
            f"AWDB element {code} for {triplet} is stored in unit {unit!r}, expected {unit_expected!r}; "
            "refusing to guess a conversion."
        )
    if not isinstance(values, list):
        raise MalformedPayloadError(f"AWDB element {code} for {triplet} has no list of values.")
    if code == "SMS":
        depth = element.get("heightDepth")
        if not isinstance(depth, dict) or not isinstance(depth.get("value"), (int, float)):
            raise MalformedPayloadError(f"AWDB soil-moisture series for {triplet} has no sensor depth.")
        column = f"{column}_{abs(int(depth['value']))}in"

    dates, data = [], []
    for item in values:
        if not isinstance(item, dict) or "date" not in item:
            raise MalformedPayloadError(f"AWDB element {code} for {triplet} has a value without a date.")
        date = pd.to_datetime(str(item["date"])[:10], format="%Y-%m-%d", errors="coerce")
        if pd.isna(date):
            raise MalformedPayloadError(f"AWDB element {code} for {triplet} has an unreadable date {item['date']!r}.")
        v = item.get("value")
        if v is None:
            number = float("nan")
        elif isinstance(v, bool) or not isinstance(v, (int, float)):
            raise MalformedPayloadError(f"AWDB element {code} for {triplet} has a non-numeric value {v!r}.")
        else:
            number = float(v)
        dates.append(date)
        data.append(number)
    s = pd.Series(data, index=pd.DatetimeIndex(dates), dtype="float64")
    if s.index.has_duplicates:
        raise MalformedPayloadError(f"AWDB element {code} for {triplet} has duplicate dates.")
    return code, column, _CONVERT[code](s)


def _parse(body: bytes, triplet: str, elements: tuple[str, ...]) -> tuple[pd.DataFrame, dict]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MalformedPayloadError(f"AWDB did not return readable JSON for {triplet}.") from exc
    if not isinstance(payload, list):
        raise MalformedPayloadError(f"AWDB response for {triplet} was not a list of stations.")
    if not payload:
        raise EmptyResultError(f"AWDB returned no data for station {triplet}.")
    station = next((s for s in payload if isinstance(s, dict) and s.get("stationTriplet") == triplet), None)
    if station is None:
        raise MalformedPayloadError(f"AWDB response did not include station {triplet}.")
    series_list = station.get("data")
    if not isinstance(series_list, list):
        raise MalformedPayloadError(f"AWDB response for {triplet} has no data section.")
    if not series_list:
        raise EmptyResultError(f"AWDB returned no series for station {triplet}.")

    columns: dict[str, pd.Series] = {}
    units: dict[str, str] = {}
    present = set()
    for series in series_list:
        if not isinstance(series, dict):
            raise MalformedPayloadError(f"AWDB response for {triplet} has a malformed series.")
        parsed = _series_to_column(series, triplet)
        if parsed is None:
            continue
        code, column, values = parsed
        if code not in elements:
            continue
        present.add(code)
        if column in columns:
            log.warning("station %s has more than one %s series; keeping the first", triplet, column)
            continue
        columns[column] = values
        units[column] = _ELEMENTS[code][2]

    for code in elements:
        if code not in present:
            log.warning("station %s has no %s data in the response; no column created", triplet, code)
    if not columns or all(len(s) == 0 for s in columns.values()):
        raise EmptyResultError(f"AWDB returned no values for station {triplet} in the requested period.")

    order = sorted(columns, key=lambda c: (_column_rank(c, elements), _depth_in(c), c))
    frame = pd.concat([columns[c] for c in order], axis=1, keys=order).sort_index()
    frame.index.name = "date"
    return frame.reset_index(), units


def _depth_in(column: str) -> int:
    """Sensor depth in inches from a `..._<depth>in` column name (0 if none), for numeric ordering."""
    match = re.search(r"_(\d+)in$", column)
    return int(match.group(1)) if match else 0


def _column_rank(column: str, elements: tuple[str, ...]) -> int:
    for rank, code in enumerate(elements):
        base = _ELEMENTS[code][0]
        if column == base or column.startswith(base + "_"):
            return rank
    return len(elements)


# ---------------------------------------------------------------- fetch
def fetch_snotel_daily(triplet: str, elements=DEFAULT_ELEMENTS, start=None, end=None) -> Path:
    """Fetch daily SNOTEL data, cache the raw JSON, write parquet, record provenance."""
    triplet = _check_triplet(triplet)
    elements = _check_elements(elements)
    start_d = as_date(start, "start") if start is not None else None
    end_d = as_date(end, "end") if end is not None else None
    if start_d and end_d and end_d < start_d:
        raise ValueError("end must not be before start")

    begin = start_d.isoformat() if start_d else DEFAULT_START
    finish = end_d.isoformat() if end_d else datetime.now(timezone.utc).date().isoformat()
    # The query string is built by hand: AWDB is picky about percent-encoded triplets.
    query = f"{URL}?stationTriplets={triplet}&elements={','.join(elements)}&duration=DAILY&beginDate={begin}&endDate={finish}"
    body = http.get(query)
    frame, units = _parse(body, triplet, elements)

    # Provenance records the request, with 'latest' rather than today's date so re-fetches dedupe.
    params = {"stationTriplets": triplet, "elements": ",".join(elements), "duration": "DAILY",
              "beginDate": begin, "endDate": end_d.isoformat() if end_d else "latest"}
    safe = triplet.replace(":", "_")
    stem = f"snotel_{safe}_{begin}_{params['endDate']}"
    raw_path = cache_raw(config.raw_dir("snotel"), stem, body, ".json")
    metadata = {
        "source": SOURCE, "station": triplet, "time_convention": TIME_CONVENTION, "units": units,
        "url": URL, "params": params, "raw_file": raw_path.name, "rows": len(frame),
    }
    out = write_parquet(frame, config.interim_dir("snotel") / f"{stem}.parquet", metadata)
    manifest.record_fetch(source=SOURCE, url=URL, params=params, content=body, rows=len(frame))
    return out
