"""qc: validation at entry. Returns flags + gap report; never alters source values."""
import logging

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from fallsoil import qc
from fallsoil.errors import DataValidationError, FallsoilError


def flow_df(values, start="2020-01-01", dates=None):
    dates = pd.to_datetime(dates) if dates is not None else pd.date_range(start, periods=len(values))
    return pd.DataFrame({"date": dates, "flow_cfs": values, "qualifier": ["A"] * len(values)})


# ---------------------------------------------------------------- flags: range / missing
def test_clean_flow_has_no_flags_and_no_gaps():
    res = qc.run_qc(flow_df([0.0, 1.0, 250.5]), "flow")
    assert res.ok and res.n_flagged == 0
    assert len(res.gaps) == 0
    assert list(res.flags.index) == [0, 1, 2]
    assert res.flags.dtypes.map(lambda t: t == bool).all()


def test_negative_flow_flagged_zero_flow_not():
    res = qc.run_qc(flow_df([5.0, -0.1, 0.0, -999.0]), "flow")
    assert list(res.flags["flow_cfs_out_of_range"]) == [False, True, False, True]
    assert not res.ok and res.n_flagged >= 2


def test_missing_flow_flagged_as_missing_not_out_of_range():
    res = qc.run_qc(flow_df([5.0, np.nan, 7.0]), "flow")
    assert list(res.flags["flow_cfs_missing"]) == [False, True, False]
    assert list(res.flags["flow_cfs_out_of_range"]) == [False, False, False]


def test_infinite_flow_is_out_of_range():
    res = qc.run_qc(flow_df([1.0, np.inf]), "flow")
    assert list(res.flags["flow_cfs_out_of_range"]) == [False, True]


def test_snotel_ranges():
    df = pd.DataFrame({
        "date": pd.date_range("2021-01-01", periods=4),
        "swe_mm": [0.0, 100.0, -0.1, 50.0],
        "precip_accum_mm": [0.0, 10.0, 20.0, -5.0],
        "temp_avg_c": [-30.0, 35.0, -80.0, 70.0],
        "soil_moisture_pct_2in": [0.0, 50.0, 120.0, -1.0],
    })
    res = qc.run_qc(df, "snotel")
    assert list(res.flags["swe_mm_out_of_range"]) == [False, False, True, False]
    assert list(res.flags["precip_accum_mm_out_of_range"]) == [False, False, False, True]
    assert list(res.flags["temp_avg_c_out_of_range"]) == [False, False, True, True]
    assert list(res.flags["soil_moisture_pct_2in_out_of_range"]) == [False, False, True, True]


def test_snotel_checks_whichever_soil_depths_are_present():
    df = pd.DataFrame({"date": pd.date_range("2021-01-01", periods=2), "swe_mm": [1.0, 2.0],
                       "soil_moisture_pct_8in": [10.0, 200.0], "soil_moisture_pct_20in": [5.0, 6.0]})
    res = qc.run_qc(df, "snotel")
    assert list(res.flags["soil_moisture_pct_8in_out_of_range"]) == [False, True]
    assert not res.flags["soil_moisture_pct_20in_out_of_range"].any()


def test_gridmet_ranges():
    df = pd.DataFrame({"date": pd.date_range("2021-01-01", periods=3),
                       "precip_mm": [0.0, -1.0, 12.0], "provisional": [False] * 3})
    res = qc.run_qc(df, "gridmet")
    assert list(res.flags["precip_mm_out_of_range"]) == [False, True, False]


# ---------------------------------------------------------------- flags: dates
def test_duplicate_dates_flag_every_row_sharing_the_date():
    df = flow_df([1, 2, 3, 4], dates=["2020-01-01", "2020-01-02", "2020-01-02", "2020-01-03"])
    res = qc.run_qc(df, "flow")
    assert list(res.flags["duplicate_date"]) == [False, True, True, False]


def test_non_monotonic_date_flags_the_row_that_goes_backwards():
    df = flow_df([1, 2, 3, 4, 5],
                 dates=["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-02", "2020-01-05"])
    res = qc.run_qc(df, "flow")
    assert list(res.flags["non_monotonic_date"]) == [False, False, False, True, False]


def test_sorted_unique_dates_raise_neither_date_flag():
    res = qc.run_qc(flow_df([1, 2, 3]), "flow")
    assert not res.flags["duplicate_date"].any() and not res.flags["non_monotonic_date"].any()


def test_flags_keep_the_input_index():
    df = flow_df([1.0, -1.0, 3.0])
    df.index = [100, 200, 300]
    res = qc.run_qc(df, "flow")
    assert list(res.flags.index) == [100, 200, 300]


# ---------------------------------------------------------------- gap report
def test_missing_calendar_days_reported_as_gap():
    df = flow_df([1, 2, 3], dates=["2020-01-01", "2020-01-02", "2020-01-06"])
    res = qc.run_qc(df, "flow")
    assert list(res.gaps.columns)[:3] == ["start", "end", "n_days"]
    assert len(res.gaps) == 1
    g = res.gaps.iloc[0]
    assert pd.Timestamp(g["start"]) == pd.Timestamp("2020-01-03")
    assert pd.Timestamp(g["end"]) == pd.Timestamp("2020-01-05")
    assert g["n_days"] == 3


def test_nan_values_count_as_gap_days_and_adjacent_runs_merge():
    df = flow_df([1.0, np.nan, np.nan, 4.0, 5.0, np.nan, 7.0])
    res = qc.run_qc(df, "flow")
    spans = [(pd.Timestamp(r.start), pd.Timestamp(r.end), int(r.n_days)) for r in res.gaps.itertuples()]
    assert spans == [(pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03"), 2),
                     (pd.Timestamp("2020-01-06"), pd.Timestamp("2020-01-06"), 1)]


def test_absent_days_and_nan_days_merge_into_one_run():
    df = flow_df([1.0, np.nan, 9.0], dates=["2020-01-01", "2020-01-02", "2020-01-05"])
    res = qc.run_qc(df, "flow")
    assert len(res.gaps) == 1 and int(res.gaps.iloc[0]["n_days"]) == 3  # Jan 2,3,4


def test_gaps_are_logged_not_silent(caplog):
    df = flow_df([1.0, np.nan, 3.0])
    with caplog.at_level(logging.INFO):
        qc.run_qc(df, "flow")
    assert any(r.name.startswith("fallsoil") and r.levelno >= logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------- never mutates
def test_input_frame_is_unchanged_including_bad_values():
    df = flow_df([5.0, -1.0, np.nan, np.inf],
                 dates=["2020-01-03", "2020-01-01", "2020-01-01", "2020-01-09"])
    before = df.copy(deep=True)
    qc.run_qc(df, "flow")
    pd.testing.assert_frame_equal(df, before)


def test_bad_values_are_flagged_not_removed_or_clipped():
    df = flow_df([5.0, -1.0, np.nan])
    res = qc.run_qc(df, "flow")
    assert len(res.flags) == len(df) == 3
    assert df["flow_cfs"].iloc[1] == -1.0


# ---------------------------------------------------------------- structural failures
def test_string_typed_values_raise_validation_error():
    df = flow_df(["1.0", "2.0"])
    with pytest.raises(DataValidationError) as ei:
        qc.run_qc(df, "flow")
    assert "flow_cfs" in str(ei.value) and isinstance(ei.value, FallsoilError)


def test_non_datetime_date_column_raises():
    df = flow_df([1.0, 2.0])
    df["date"] = ["2020-01-01", "2020-01-02"]
    with pytest.raises(DataValidationError):
        qc.run_qc(df, "flow")


def test_missing_required_column_raises():
    with pytest.raises(DataValidationError):
        qc.run_qc(flow_df([1.0]).drop(columns="flow_cfs"), "flow")
    with pytest.raises(DataValidationError):
        qc.run_qc(flow_df([1.0]).drop(columns="date"), "flow")


def test_snotel_with_no_value_columns_raises():
    with pytest.raises(DataValidationError):
        qc.run_qc(pd.DataFrame({"date": pd.date_range("2021-01-01", periods=2)}), "snotel")


def test_empty_frame_raises_validation_error():
    with pytest.raises(DataValidationError):
        qc.run_qc(flow_df([]), "flow")


def test_unknown_dataset_kind():
    with pytest.raises(ValueError):
        qc.run_qc(flow_df([1.0]), "bogus")


# ---------------------------------------------------------------- property
vals = st.one_of(st.floats(allow_nan=True, allow_infinity=True, width=32),
                 st.floats(min_value=-1e4, max_value=1e5))
day_offsets = st.lists(st.integers(min_value=0, max_value=60), min_size=1, max_size=40)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(values=st.lists(vals, min_size=1, max_size=40), data=st.data())
def test_property_qc_never_mutates_and_matches_oracle(values, data):
    offs = data.draw(st.lists(st.integers(0, 60), min_size=len(values), max_size=len(values)))
    dates = pd.Timestamp("2020-01-01") + pd.to_timedelta(offs, unit="D")
    df = pd.DataFrame({"date": dates, "flow_cfs": np.asarray(values, dtype="float64")})
    before = df.copy(deep=True)
    res = qc.run_qc(df, "flow")
    pd.testing.assert_frame_equal(df, before)  # values, order, index untouched
    v = df["flow_cfs"]
    assert list(res.flags.index) == list(df.index)
    assert list(res.flags["flow_cfs_missing"]) == list(v.isna())
    assert list(res.flags["flow_cfs_out_of_range"]) == list((v < 0) | np.isinf(v))
    assert list(res.flags["duplicate_date"]) == list(df["date"].duplicated(keep=False))
    assert list(res.flags["non_monotonic_date"]) == list(df["date"].diff() < pd.Timedelta(0))


@settings(max_examples=40, deadline=None)
@given(present=st.sets(st.integers(0, 50), min_size=2, max_size=40))
def test_property_gap_days_equal_calendar_days_without_data(present):
    days = sorted(present)
    dates = pd.Timestamp("2020-01-01") + pd.to_timedelta(days, unit="D")
    df = pd.DataFrame({"date": dates, "flow_cfs": np.ones(len(days))})
    res = qc.run_qc(df, "flow")
    expected_missing = (days[-1] - days[0] + 1) - len(days)
    assert int(res.gaps["n_days"].sum()) == expected_missing
    for r in res.gaps.itertuples():  # each reported span really is empty and maximal
        span = pd.date_range(r.start, r.end)
        assert len(span) == r.n_days
        assert not set(span) & set(dates)
