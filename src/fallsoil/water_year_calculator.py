"""Water-year logic. This module is the single source of truth for water years.

A water year runs Oct 1 - Sep 30 and is labelled by the calendar year in which it ends
(WY2021 = 2020-10-01 .. 2021-09-30). Inputs are used as given: the wall-clock date counts and
no time-zone conversion is applied.
"""
from __future__ import annotations

import numbers
from datetime import date, datetime

import pandas as pd

_OCT = 10
# Days from Oct 1 through Dec 31 inclusive, i.e. the offset of Jan 1 within a water year.
_DAYS_OCT_TO_DEC = 92


def _is_vector(d) -> bool:
    return isinstance(d, (pd.Series, pd.DatetimeIndex))


def _check_scalar(d) -> date:
    if d is pd.NaT or (isinstance(d, datetime) and pd.isna(d)):
        raise ValueError("water year is undefined for NaT")
    if isinstance(d, date):  # datetime and pd.Timestamp are date subclasses
        return d
    raise TypeError(f"expected a date, datetime, Timestamp, Series or DatetimeIndex, got {type(d).__name__}")


def _parts(d):
    """Return (year, month, dayofyear, is_leap_year) for a datetime-like vector."""
    if isinstance(d, pd.Series) and not pd.api.types.is_datetime64_any_dtype(d):
        raise TypeError(f"expected datetime values, got dtype {d.dtype}")
    values = d.dt if isinstance(d, pd.Series) else d
    return values.year, values.month, values.day_of_year, values.is_leap_year


def _wrap(like, values):
    """Return values (array-like of the same length) as Int64 in the container type of `like`."""
    arr = pd.array(values, dtype="Int64")
    if isinstance(like, pd.Series):
        return pd.Series(arr, index=like.index, name=like.name)
    return pd.Index(arr, name=like.name)


def water_year(d):
    """Water year (int) containing `d`; vector input gives a nullable Int64 Series/Index (NaT -> <NA>)."""
    if _is_vector(d):
        year, month, _, _ = _parts(d)
        return _wrap(d, pd.Series(year) + (pd.Series(month) >= _OCT).astype(int).values)
    d = _check_scalar(d)
    return d.year + 1 if d.month >= _OCT else d.year


def wy_start(wy: int) -> date:
    """First day (Oct 1) of water year `wy`."""
    _check_year(wy)
    return date(int(wy) - 1, _OCT, 1)


def wy_end(wy: int) -> date:
    """Last day (Sep 30) of water year `wy`."""
    _check_year(wy)
    return date(int(wy), 9, 30)


def wy_day(d):
    """1-based day of the water year (Oct 1 = 1)."""
    if _is_vector(d):
        year, month, doy, leap = _parts(d)
        year, month, doy, leap = (pd.Series(x).reset_index(drop=True) for x in (year, month, doy, leap))
        oct1_doy = 274 + leap.astype("float").fillna(0).astype(int)  # Oct 1 is day 274 (275 in leap years)
        out = (doy - oct1_doy + 1).where(month >= _OCT, _DAYS_OCT_TO_DEC + doy)
        return _wrap(d, out.values)
    d = _check_scalar(d)
    start = date(d.year if d.month >= _OCT else d.year - 1, _OCT, 1)
    return (date(d.year, d.month, d.day) - start).days + 1


def _check_year(wy) -> None:
    if isinstance(wy, bool) or not isinstance(wy, numbers.Integral):
        raise TypeError(f"water year must be an integer, got {type(wy).__name__}")
