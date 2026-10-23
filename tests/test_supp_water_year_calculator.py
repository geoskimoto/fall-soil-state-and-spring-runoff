"""Supplementary water_year_calculator tests: argument validation, NaT, wall-clock rule, leap years."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from fallsoil.water_year_calculator import water_year, wy_day, wy_end, wy_start


# ---------------------------------------------------------------- wy_start / wy_end argument validation
@pytest.mark.parametrize("fn", [wy_start, wy_end])
@pytest.mark.parametrize("bad", ["2020", 2020.0, 2020.5, True, False, None, [2020], date(2020, 1, 1),
                                 np.float64(2020.0), pd.Timestamp("2020-01-01")])
def test_wy_start_end_reject_non_integers_with_type_error(fn, bad):
    with pytest.raises(TypeError):
        fn(bad)


@pytest.mark.parametrize("dtype", [np.int16, np.int32, np.int64, np.uint32])
def test_wy_start_end_accept_numpy_integers_and_return_plain_dates(dtype):
    s, e = wy_start(dtype(2021)), wy_end(dtype(2021))
    assert (s, e) == (date(2020, 10, 1), date(2021, 9, 30))
    assert type(s) is date and type(e) is date


def test_wy_start_end_basic_values():
    assert wy_start(2000) == date(1999, 10, 1) and wy_end(2000) == date(2000, 9, 30)


def test_wy_start_plus_one_year_is_next_wy_start_and_lengths_match_leap_rule():
    for wy in (1999, 2000, 2019, 2020, 2023, 2024):
        length = (wy_end(wy) - wy_start(wy)).days + 1
        feb29 = (wy % 4 == 0 and (wy % 100 != 0 or wy % 400 == 0))
        assert length == (366 if feb29 else 365)
        assert wy_start(wy + 1) == date.fromordinal(wy_end(wy).toordinal() + 1)


# ---------------------------------------------------------------- vector type validation
@pytest.mark.parametrize("fn", [water_year, wy_day])
def test_numeric_series_is_type_error(fn):
    with pytest.raises(TypeError):
        fn(pd.Series([1, 2, 3]))
    with pytest.raises(TypeError):
        fn(pd.Series([2020.5, 2021.5]))


@pytest.mark.parametrize("fn", [water_year, wy_day])
def test_string_series_is_type_error(fn):
    with pytest.raises(TypeError):
        fn(pd.Series(["2020-10-01", "2020-10-02"]))


@pytest.mark.parametrize("fn", [water_year, wy_day])
@pytest.mark.parametrize("bad", ["2020-10-01", 20201001, 2020.0, None, [date(2020, 1, 1)], np.nan])
def test_bad_scalars_raise_type_or_value_error(fn, bad):
    with pytest.raises((TypeError, ValueError)):
        fn(bad)


@pytest.mark.parametrize("fn", [water_year, wy_day])
def test_scalar_nat_is_value_error(fn):
    with pytest.raises(ValueError):
        fn(pd.NaT)


# ---------------------------------------------------------------- NaT in vectors
def test_water_year_series_with_nat_gives_na_in_place_and_keeps_others():
    s = pd.Series(pd.to_datetime(["2020-09-30", None, "2020-10-01", None]), index=list("abcd"), name="d")
    out = water_year(s)
    assert list(out.index) == list("abcd") and out.name == "d"
    assert out.isna().tolist() == [False, True, False, True]
    assert out.iloc[0] == 2020 and out.iloc[2] == 2021


def test_wy_day_series_with_nat_including_leap_years():
    s = pd.Series(pd.to_datetime(["2019-10-01", None, "2020-02-29", "2020-10-01", "2020-09-30", None,
                                  "2021-09-30", "2021-01-01"]))
    out = wy_day(s)
    assert out.isna().tolist() == [False, True, False, False, False, True, False, False]
    vals = out.dropna().astype(int).tolist()
    assert vals == [1, 152, 1, 366, 365, 93]


def test_datetimeindex_with_nat_returns_index_with_na():
    idx = pd.DatetimeIndex(["2020-10-01", pd.NaT, "2021-09-30"])
    out = water_year(idx)
    assert isinstance(out, pd.Index)
    assert out.isna().tolist() == [False, True, False]
    assert out[0] == 2021 and out[2] == 2021
    d = wy_day(idx)
    assert d.isna().tolist() == [False, True, False]
    assert d[0] == 1 and d[2] == 365


def test_all_nat_series_gives_all_na():
    s = pd.Series(pd.to_datetime([None, None]))
    assert water_year(s).isna().all() and wy_day(s).isna().all()


def test_empty_datetime_series_gives_empty_result():
    s = pd.Series(pd.to_datetime([]))
    assert len(water_year(s)) == 0 and len(wy_day(s)) == 0


# ---------------------------------------------------------------- wy_day correctness (leap boundaries)
@pytest.mark.parametrize("d, n", [
    (date(2019, 10, 1), 1), (date(2019, 12, 31), 92), (date(2020, 1, 1), 93),
    (date(2020, 2, 29), 152), (date(2020, 3, 1), 153), (date(2020, 9, 30), 366),
    (date(2020, 10, 1), 1), (date(2021, 9, 30), 365), (date(2000, 2, 29), 152), (date(1900, 3, 1), 152),
])
def test_wy_day_scalar_and_vector_agree_with_known_values(d, n):
    assert wy_day(d) == n
    assert wy_day(datetime(d.year, d.month, d.day, 23, 59)) == n
    assert wy_day(pd.Series(pd.to_datetime([d]))).iloc[0] == n


def test_wy_day_matches_arithmetic_for_every_day_of_a_decade():
    days = pd.date_range("2015-10-01", "2025-09-30", freq="D")
    got = wy_day(pd.Series(days)).astype(int).to_numpy()
    wys = water_year(pd.Series(days)).astype(int).to_numpy()
    expected = np.array([(d.date() - wy_start(int(w))).days + 1 for d, w in zip(days, wys)])
    assert (got == expected).all()


# ---------------------------------------------------------------- wall-clock rule
def test_timezone_aware_values_use_wall_clock_date_not_utc():
    ts = pd.Timestamp("2020-09-30 20:00", tz="America/Los_Angeles")  # already Oct 1 in UTC
    assert water_year(ts) == 2020 and wy_day(ts) == 366
    s = pd.Series(pd.to_datetime(["2020-09-30 20:00"]).tz_localize("America/Los_Angeles"))
    assert water_year(s).iloc[0] == 2020 and wy_day(s).iloc[0] == 366
    late = pd.Timestamp("2020-10-01 01:00", tz="Asia/Tokyo")  # still Sep 30 in UTC
    assert water_year(late) == 2021 and wy_day(late) == 1
