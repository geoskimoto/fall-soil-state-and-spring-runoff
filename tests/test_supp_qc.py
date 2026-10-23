"""Supplementary qc tests: trailing/leading gaps and inclusive range boundaries (spec: INTERFACE.md)."""
import logging

import numpy as np
import pandas as pd
import pytest

from fallsoil.qc import run_qc

EPS = 1e-6


def frame(values, col="flow_cfs", start="2021-01-01"):
    n = len(values)
    return pd.DataFrame({"date": pd.date_range(start, periods=n, freq="D"),
                         col: np.array(values, dtype="float64")})


def gap_rows(res):
    return [(g.start.date().isoformat(), g.end.date().isoformat(), int(g.n_days))
            for g in res.gaps.itertuples()]


# ---------------------------------------------------------------- gaps at the series edges
def test_trailing_nan_run_is_one_gap_ending_at_last_date():
    df = frame([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, np.nan, np.nan, np.nan])  # Jan 8..10 missing
    res = run_qc(df, "flow")
    assert gap_rows(res) == [("2021-01-08", "2021-01-10", 3)]


def test_single_trailing_missing_day_is_a_gap_of_one():
    df = frame([1.0, 2.0, 3.0, np.nan])
    res = run_qc(df, "flow")
    assert gap_rows(res) == [("2021-01-04", "2021-01-04", 1)]


def test_leading_nan_run_is_a_gap_from_first_row():
    df = frame([np.nan, np.nan, 3.0, 4.0])
    res = run_qc(df, "flow")
    assert gap_rows(res) == [("2021-01-01", "2021-01-02", 2)]


def test_leading_and_trailing_and_interior_gaps_all_reported_in_order():
    df = frame([np.nan, 2.0, np.nan, np.nan, 5.0, np.nan])
    res = run_qc(df, "flow")
    assert gap_rows(res) == [("2021-01-01", "2021-01-01", 1),
                             ("2021-01-03", "2021-01-04", 2),
                             ("2021-01-06", "2021-01-06", 1)]


def test_trailing_gap_with_absent_rows_not_just_nan_rows():
    # last data day is Jan 3; the frame ends with an all-NaN row on Jan 6 (Jan 4-5 have no row at all)
    df = pd.DataFrame({"date": pd.to_datetime(["2021-01-01", "2021-01-02", "2021-01-03", "2021-01-06"]),
                       "flow_cfs": [1.0, 2.0, 3.0, np.nan]})
    res = run_qc(df, "flow")
    assert gap_rows(res) == [("2021-01-04", "2021-01-06", 3)]


def test_trailing_gap_makes_result_not_ok_and_logs_warning(caplog):
    df = frame([1.0, 2.0, np.nan, np.nan])
    with caplog.at_level(logging.WARNING, logger="fallsoil"):
        res = run_qc(df, "flow")
    assert not res.ok
    recs = [r for r in caplog.records if r.name.startswith("fallsoil") and r.levelno == logging.WARNING]
    assert recs, "gap must be logged at WARNING on a fallsoil.* logger"
    text = " ".join(r.getMessage() for r in recs)
    assert "2021-01-03" in text and "2021-01-04" in text


def test_gap_log_not_emitted_when_no_gap(caplog):
    with caplog.at_level(logging.WARNING, logger="fallsoil"):
        run_qc(frame([1.0, 2.0, 3.0]), "flow")
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_all_nan_series_is_one_gap_over_whole_span():
    res = run_qc(frame([np.nan, np.nan, np.nan]), "flow")
    assert gap_rows(res) == [("2021-01-01", "2021-01-03", 3)]


def test_gaps_empty_frame_has_documented_columns_and_dtypes():
    res = run_qc(frame([1.0, 2.0]), "flow")
    assert list(res.gaps.columns) == ["start", "end", "n_days"]
    assert len(res.gaps) == 0


# ---------------------------------------------------------------- range boundaries
# (dataset, column, lower, upper); None = unbounded on that side. Limits are inclusive.
CASES = [
    ("flow", "flow_cfs", 0.0, None),
    ("snotel", "swe_mm", 0.0, None),
    ("snotel", "precip_accum_mm", 0.0, None),
    ("gridmet", "precip_mm", 0.0, None),
    ("snotel", "temp_avg_c", -60.0, 50.0),
    ("gridmet", "temp_max_c", -60.0, 50.0),
    ("gridmet", "temp_min_c", -60.0, 50.0),
    ("snotel", "soil_moisture_pct_8in", 0.0, 100.0),
]


def oor(dataset, col, values):
    res = run_qc(frame(values, col=col), dataset)
    return list(res.flags[f"{col}_out_of_range"])


@pytest.mark.parametrize("dataset,col,lo,hi", CASES)
def test_lower_limit_is_inclusive_and_just_below_is_flagged(dataset, col, lo, hi):
    assert oor(dataset, col, [lo, lo - EPS]) == [False, True]


@pytest.mark.parametrize("dataset,col,lo,hi", [c for c in CASES if c[3] is not None])
def test_upper_limit_is_inclusive_and_just_above_is_flagged(dataset, col, lo, hi):
    assert oor(dataset, col, [hi, hi + EPS]) == [False, True]


@pytest.mark.parametrize("dataset,col,lo,hi", [c for c in CASES if c[3] is None])
def test_unbounded_above_columns_never_flag_large_finite_values(dataset, col, lo, hi):
    assert oor(dataset, col, [1e6, 1e12]) == [False, False]


@pytest.mark.parametrize("dataset,col,lo,hi", CASES)
def test_interior_values_not_flagged_and_infinities_flagged(dataset, col, lo, hi):
    mid = 10.0 if hi is None else (lo + hi) / 2
    assert oor(dataset, col, [mid, np.inf, -np.inf]) == [False, True, True]


@pytest.mark.parametrize("dataset,col,lo,hi", CASES)
def test_nan_is_missing_only_never_out_of_range(dataset, col, lo, hi):
    res = run_qc(frame([5.0 if hi is None else (lo + hi) / 2, np.nan], col=col), dataset)
    assert list(res.flags[f"{col}_missing"]) == [False, True]
    assert list(res.flags[f"{col}_out_of_range"]) == [False, False]


def test_temperature_limits_are_exactly_minus60_and_50_not_nearby_values():
    # guards against shifted limits (e.g. 49/51, 59/61)
    assert oor("gridmet", "temp_max_c", [-59.5, -60.0, 49.5, 50.0]) == [False] * 4
    assert oor("gridmet", "temp_max_c", [-60.5, 50.5]) == [True, True]


def test_percent_limits_are_exactly_0_and_100_not_nearby_values():
    col = "soil_moisture_pct_2in"
    assert oor("snotel", col, [0.0, 99.5, 100.0]) == [False] * 3
    assert oor("snotel", col, [-0.5, 100.5]) == [True, True]


def test_zero_flow_is_valid_and_negative_flow_flagged_but_kept():
    df = frame([0.0, -0.001, 3.0])
    res = run_qc(df, "flow")
    assert list(res.flags["flow_cfs_out_of_range"]) == [False, True, False]
    assert df["flow_cfs"].tolist() == [0.0, -0.001, 3.0]  # never clipped or mutated
    assert res.n_flagged == 1


def test_flagged_values_do_not_create_gaps():
    res = run_qc(frame([-5.0, np.inf, 2.0]), "flow")
    assert len(res.gaps) == 0
