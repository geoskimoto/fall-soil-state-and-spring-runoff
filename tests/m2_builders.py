"""Shared synthetic-hydrograph builders for the Milestone 2a (fallsoil.responses) tests.

Everything here is INDEPENDENT of fallsoil.responses: truths are computed with plain numpy/pandas
arithmetic so a test never checks the implementation against itself. All daily series are
`pd.Series` named "flow_mm_d" with a daily DatetimeIndex (naive calendar days).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

M3_PER_FT3 = 0.0283168  # exact factor stated in docs/M2_PLAN.md
SECONDS_PER_DAY = 86400.0


# --------------------------------------------------------------------------- units (independent)
def mm_d_to_cfs(flow_mm_d, area_sqkm: float):
    """mm/day over `area_sqkm` -> cfs, written out from first principles."""
    return np.asarray(flow_mm_d, dtype=float) / 1000.0 * (area_sqkm * 1e6) / SECONDS_PER_DAY / M3_PER_FT3


def cfs_to_mm_d(flow_cfs, area_sqkm: float):
    return np.asarray(flow_cfs, dtype=float) * M3_PER_FT3 * SECONDS_PER_DAY / (area_sqkm * 1e6) * 1000.0


# --------------------------------------------------------------------------- calendar helpers
def wy_dates(wy: int) -> pd.DatetimeIndex:
    """Every calendar day of water year `wy` (Oct 1 of wy-1 .. Sep 30 of wy)."""
    return pd.date_range(f"{wy - 1}-10-01", f"{wy}-09-30", freq="D")


def cal_date(wy: int, month: int, day: int) -> pd.Timestamp:
    """Calendar date (month, day) that falls inside water year `wy`."""
    year = wy - 1 if month >= 10 else wy
    return pd.Timestamp(year=year, month=month, day=day)


def wy_day_of(ts: pd.Timestamp) -> int:
    """1-based day of water year, Oct 1 = 1. Independent of fallsoil.water_year_calculator."""
    start_year = ts.year if ts.month >= 10 else ts.year - 1
    return (ts.normalize() - pd.Timestamp(year=start_year, month=10, day=1)).days + 1


def n_days_in_wy(wy: int) -> int:
    return len(wy_dates(wy))


def _series(index: pd.DatetimeIndex, values) -> pd.Series:
    return pd.Series(np.asarray(values, dtype=float), index=index, name="flow_mm_d")


def concat_wys(builder, wys) -> pd.Series:
    """Concatenate `builder(wy)` for consecutive water years into one continuous series."""
    return pd.concat([builder(w) for w in wys])


# --------------------------------------------------------------------------- one-WY builders
def constant_year(wy: int, value: float = 1.0) -> pd.Series:
    idx = wy_dates(wy)
    return _series(idx, np.full(len(idx), value))


def gaussian_year(wy: int, center=(6, 3), sigma_days: float = 15.0, amp: float = 20.0,
                  base: float = 0.5) -> pd.Series:
    """base + amp * exp(-0.5 ((d - center)/sigma)^2), d in whole days. Peak lands exactly on `center`."""
    idx = wy_dates(wy)
    c = cal_date(wy, *center)
    d = (idx - c).days.to_numpy(dtype=float)
    return _series(idx, base + amp * np.exp(-0.5 * (d / sigma_days) ** 2))


def lognormal_year(wy: int, start=(3, 1), mu: float = 4.0, sigma: float = 0.5, amp: float = 400.0,
                   base: float = 0.3) -> pd.Series:
    """Right-skewed pulse: base + amp * lognormal-pdf(days since `start`). Zero before `start`."""
    idx = wy_dates(wy)
    t = (idx - cal_date(wy, *start)).days.to_numpy(dtype=float)
    pdf = np.zeros_like(t)
    pos = t > 0
    pdf[pos] = np.exp(-((np.log(t[pos]) - mu) ** 2) / (2 * sigma**2)) / (t[pos] * sigma * np.sqrt(2 * np.pi))
    return _series(idx, base + amp * pdf)


def step_year(wy: int, low: float, high: float, step=(4, 15)) -> pd.Series:
    """`low` before the step date, `high` from the step date (inclusive) to the end of the WY."""
    idx = wy_dates(wy)
    s = cal_date(wy, *step)
    return _series(idx, np.where(idx >= s, high, low))


def delta_year(wy: int, day=(3, 1), magnitude: float = 100.0, base: float = 0.0) -> pd.Series:
    idx = wy_dates(wy)
    vals = np.full(len(idx), base)
    vals[idx.get_loc(cal_date(wy, *day))] = magnitude
    return _series(idx, vals)


def power_recession(q0: float, a: float, b: float, n_steps: int) -> np.ndarray:
    """Exact solution of dQ/dt = -a Q^b from Q(0)=q0, sampled at t = 0..n_steps (days)."""
    t = np.arange(n_steps + 1, dtype=float)
    if b == 1.0:
        return q0 * np.exp(-a * t)
    return (q0 ** (1.0 - b) + (b - 1.0) * a * t) ** (1.0 / (1.0 - b))


def recession_year(wy: int, events, lead: float = 0.2) -> pd.Series:
    """Flat `lead` flow with planted recession events.

    events: list of (peak_month_day, values) where `values` is the recession sampled at t=0.. (t=0 is
    the peak day itself, so strictly decreasing steps start on t=1). After an event the flow stays
    constant at its last value (constant = not strictly decreasing, so the falling limb ends cleanly).
    Events must not overlap.
    """
    idx = wy_dates(wy)
    vals = np.full(len(idx), lead)
    for (m, d), v in events:
        i0 = idx.get_loc(cal_date(wy, m, d))
        v = np.asarray(v, dtype=float)
        vals[i0:i0 + len(v)] = v
        vals[i0 + len(v):] = v[-1]
    return _series(idx, vals)


def exp_recession_year(wy: int, k_days: float, q0: float = 10.0, peak=(7, 1), n_steps: int = 40) -> pd.Series:
    t = np.arange(n_steps + 1)
    return recession_year(wy, [(peak, q0 * np.exp(-t / k_days))])


def power_recession_year(wy: int, b: float, a_at_q0: float = 0.05) -> pd.Series:
    """Two power-law falling limbs sharing the SAME (a, b); inside Jun 1 - Oct 31.

    `a_at_q0` is the relative daily decline at the first event's peak (a * Q0^(b-1)), which keeps the
    daily discretisation small (<= 5 percent per day). Returns the series; the planted truth is
    (a, b) with a = a_at_q0 / Q0^(b-1), Q0 = 10 mm/d.
    """
    q01, q02 = 10.0, 8.0
    a = a_at_q0 / q01 ** (b - 1.0)
    ev1 = power_recession(q01, a, b, 60)
    ev2 = power_recession(q02, a, b, 40)
    return recession_year(wy, [((6, 5), ev1), ((8, 15), ev2)])


def power_recession_a(b: float, a_at_q0: float = 0.05) -> float:
    return a_at_q0 / 10.0 ** (b - 1.0)


def spiky_baseflow_year(wy: int, base: float = 2.0, spike: float = 20.0, spike_days=(50, 300)) -> pd.Series:
    """Constant `base` plus single-day storm spikes of +`spike` at the given 0-based day-of-WY offsets."""
    idx = wy_dates(wy)
    vals = np.full(len(idx), base)
    for i in spike_days:
        vals[i] += spike
    return _series(idx, vals)


def sawtooth_year(wy: int, low: float = 1.0, high: float = 3.0) -> pd.Series:
    idx = wy_dates(wy)
    return _series(idx, np.where(np.arange(len(idx)) % 2 == 0, low, high))


def sine_year(wy: int, mean: float = 5.0, amp: float = 2.0) -> pd.Series:
    idx = wy_dates(wy)
    t = np.arange(len(idx))
    return _series(idx, mean + amp * np.sin(2 * np.pi * t / 365.0))


# --------------------------------------------------------------------------- independent truths
def truth_com_day(s: pd.Series) -> int:
    """First day-of-WY at which the cumulative flow reaches 50 percent of the total (NaN = 0 flow)."""
    v = s.fillna(0.0).to_numpy()
    cum = np.cumsum(v)
    i = int(np.argmax(cum >= 0.5 * cum[-1]))
    return wy_day_of(s.index[i])


def truth_rb(s: pd.Series) -> float:
    v = s.to_numpy()
    return float(np.abs(np.diff(v)).sum() / v[1:].sum())


def window(s: pd.Series, wy: int, start_md, end_md) -> pd.Series:
    return s[(s.index >= cal_date(wy, *start_md)) & (s.index <= cal_date(wy, *end_md))]


# --------------------------------------------------------------------------- damage helpers
def with_nan(s: pd.Series, dates) -> pd.Series:
    out = s.copy()
    out.loc[pd.DatetimeIndex(dates)] = np.nan
    return out


def without_days(s: pd.Series, dates) -> pd.Series:
    return s.drop(pd.DatetimeIndex(dates))


def first_n_days(wy: int, start_md, n: int) -> pd.DatetimeIndex:
    return pd.date_range(cal_date(wy, *start_md), periods=n, freq="D")


# --------------------------------------------------------------------------- USGS-format frames
def to_usgs_frame(flow_mm_d: pd.Series, area_sqkm: float, qualifier="A") -> pd.DataFrame:
    """Frame in the exact schema fetch.usgs_flow writes: date (datetime64), flow_cfs (float64), qualifier (str)."""
    q = qualifier if not isinstance(qualifier, (list, np.ndarray, pd.Series)) else list(qualifier)
    return pd.DataFrame({
        "date": pd.DatetimeIndex(flow_mm_d.index).astype("datetime64[ns]"),
        "flow_cfs": mm_d_to_cfs(flow_mm_d.to_numpy(), area_sqkm).astype("float64"),
        "qualifier": q,
    })


def to_rdb_rows(frame: pd.DataFrame) -> list[tuple[str, str, str]]:
    """Rows for conftest.make_rdb. Flow is written with 9 decimals so the text round trip is ~1e-9 exact;
    NaN flow becomes a blank value (as NWIS does)."""
    rows = []
    for d, f, q in zip(frame["date"], frame["flow_cfs"], frame["qualifier"]):
        rows.append((d.strftime("%Y-%m-%d"), "" if pd.isna(f) else f"{f:.9f}", q))
    return rows


# --------------------------------------------------------------------------- table-level invariants
def check_table_invariants(table: pd.DataFrame, min_wy_valid_days: int = 350) -> None:
    """Invariants every driver output must satisfy (see INTERFACE_M2A.md: status / reasons semantics)."""
    from fallsoil.responses import METRIC_COLUMNS, TABLE_COLUMNS

    assert list(table.columns) == list(TABLE_COLUMNS)
    assert table["wy"].is_unique and table["wy"].is_monotonic_increasing
    float_cols = list(METRIC_COLUMNS) + ["frac_provisional"]
    for _, r in table.iterrows():
        nan_cols = [c for c in float_cols if pd.isna(r[c])]
        assert (r["reasons"] == "") == (r["status"] == "ok")
        assert (r["status"] == "nan") == (r["n_valid_days_wy"] < min_wy_valid_days)
        if r["status"] == "ok":
            assert not nan_cols
        elif r["status"] == "partial":
            assert nan_cols, "partial means at least one NaN metric"
            for c in nan_cols:
                assert c in r["reasons"], f"WY{r['wy']}: NaN {c} has no recorded reason"
        else:
            assert r["status"] == "nan"
            assert all(pd.isna(r[c]) for c in METRIC_COLUMNS)
            assert "valid" in r["reasons"].lower()
            assert r["recession_n_segments"] == 0 and r["bn_n_points"] == 0 and not r["bn_fit_ok"]
