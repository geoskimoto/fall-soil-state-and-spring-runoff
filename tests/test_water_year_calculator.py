"""water_year_calculator: Oct 1 - Sep 30, WY labelled by the calendar year in which it ends."""
import calendar
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, strategies as st

from fallsoil.water_year_calculator import water_year, wy_day, wy_end, wy_start

dates_st = st.dates(min_value=date(1800, 1, 1), max_value=date(2200, 12, 31))
wy_st = st.integers(min_value=1801, max_value=2200)


# ---------------------------------------------------------------- unit: boundaries
@pytest.mark.parametrize(
    "d, wy",
    [
        (date(2020, 9, 30), 2020),   # last day of WY2020
        (date(2020, 10, 1), 2021),   # first day of WY2021
        (date(2020, 12, 31), 2021),
        (date(2021, 1, 1), 2021),
        (date(2021, 9, 30), 2021),
        (date(2021, 10, 1), 2022),
        (date(2000, 2, 29), 2000),   # leap day
        (date(1999, 10, 1), 2000),
        (date(1900, 3, 1), 1900),    # 1900 is not a leap year
    ],
)
def test_water_year_boundaries(d, wy):
    assert water_year(d) == wy


def test_water_year_accepts_datetime_and_timestamp_using_calendar_date_as_given():
    assert water_year(datetime(2020, 9, 30, 23, 59)) == 2020
    assert water_year(datetime(2020, 10, 1, 0, 0)) == 2021
    assert water_year(pd.Timestamp("2020-10-01")) == 2021
    # tz-aware: no silent conversion; the wall-clock date is what counts
    assert water_year(datetime(2020, 9, 30, 23, 0, tzinfo=timezone.utc)) == 2020


def test_water_year_returns_plain_int():
    assert isinstance(water_year(date(2020, 1, 1)), int)


def test_water_year_series_preserves_index_and_values():
    s = pd.Series(pd.to_datetime(["2020-09-30", "2020-10-01", "2021-02-28"]), index=[10, 11, 12])
    out = water_year(s)
    assert isinstance(out, pd.Series)
    assert list(out.index) == [10, 11, 12]
    assert [int(v) for v in out] == [2020, 2021, 2021]


def test_water_year_series_nat_is_missing_not_a_bogus_year():
    s = pd.Series(pd.to_datetime(["2020-09-30", None]))
    out = water_year(s)
    assert int(out.iloc[0]) == 2020
    assert pd.isna(out.iloc[1])


def test_water_year_accepts_datetimeindex():
    out = water_year(pd.DatetimeIndex(["2020-09-30", "2020-10-01"]))
    assert [int(v) for v in out] == [2020, 2021]


@pytest.mark.parametrize("bad", ["2020-10-01", 20201001, None, 3.5])
def test_water_year_rejects_non_date_input_loudly(bad):
    with pytest.raises((TypeError, ValueError)):
        water_year(bad)


def test_water_year_scalar_nat_raises():
    with pytest.raises(ValueError):
        water_year(pd.NaT)


@pytest.mark.parametrize("wy", [2000, 2020, 2024])
def test_leap_water_years_have_366_days(wy):
    assert (wy_end(wy) - wy_start(wy)).days + 1 == 366
    assert wy_day(wy_end(wy)) == 366


@pytest.mark.parametrize("wy", [1900, 2019, 2021, 2100])
def test_nonleap_water_years_have_365_days(wy):
    assert (wy_end(wy) - wy_start(wy)).days + 1 == 365
    assert wy_day(wy_end(wy)) == 365


def test_wy_start_end_exact_dates():
    assert wy_start(2021) == date(2020, 10, 1)
    assert wy_end(2021) == date(2021, 9, 30)
    assert type(wy_start(2021)) is date


def test_wy_day_examples():
    assert wy_day(date(2020, 10, 1)) == 1
    assert wy_day(date(2020, 10, 2)) == 2
    assert wy_day(date(2021, 1, 1)) == 93        # Oct31+Nov30+Dec31 = 92 days before
    assert wy_day(date(2020, 9, 30)) == 366      # WY2020 is leap (Feb 29 2020)
    assert wy_day(date(2021, 9, 30)) == 365


def test_wy_day_series():
    s = pd.Series(pd.to_datetime(["2020-10-01", "2021-09-30"]))
    assert [int(v) for v in wy_day(s)] == [1, 365]


# ---------------------------------------------------------------- property
@given(a=dates_st, b=dates_st)
def test_water_year_monotonic_non_decreasing(a, b):
    lo, hi = sorted([a, b])
    assert water_year(lo) <= water_year(hi)


@given(d=dates_st)
def test_water_year_is_calendar_year_or_next(d):
    assert water_year(d) == (d.year + 1 if d.month >= 10 else d.year)


@given(wy=wy_st)
def test_each_water_year_has_365_or_366_days_and_366_iff_feb29_inside(wy):
    n = (wy_end(wy) - wy_start(wy)).days + 1
    assert n == (366 if calendar.isleap(wy) else 365)


@given(wy=wy_st)
def test_start_end_round_trip_with_water_year(wy):
    assert water_year(wy_start(wy)) == wy
    assert water_year(wy_end(wy)) == wy
    assert water_year(wy_start(wy) - pd.Timedelta(days=1).to_pytimedelta()) == wy - 1
    assert water_year(wy_end(wy) + pd.Timedelta(days=1).to_pytimedelta()) == wy + 1
    assert wy_day(wy_start(wy)) == 1


@given(d=dates_st)
def test_wy_day_increments_by_one_within_a_water_year(d):
    nxt = (pd.Timestamp(d) + pd.Timedelta(days=1)).date()
    if water_year(nxt) == water_year(d):
        assert wy_day(nxt) == wy_day(d) + 1
    else:
        assert wy_day(nxt) == 1


@given(d=dates_st)
def test_scalar_and_series_agree(d):
    s = pd.Series([pd.Timestamp(d)])
    assert int(water_year(s).iloc[0]) == water_year(d)
    assert int(wy_day(s).iloc[0]) == wy_day(d)
