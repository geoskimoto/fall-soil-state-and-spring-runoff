"""Per-water-year response metrics from a daily flow series in mm/d.

Conventions (see tests/INTERFACE_M2A.md): a valid day is finite and >= 0; invalid days are excluded, counted as
missing for the completeness gates and never clipped or filled. Windows are calendar-date ranges of year `wy`
(Jan-Sep belong to WY `wy`); those reaching October use October of calendar year `wy` (the following water year).
Water years and day-of-water-year numbers come from `fallsoil.water_year_calculator` only.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from fallsoil.errors import DataValidationError
from fallsoil.responses import recession
from fallsoil.responses.columns import METRIC_COLUMNS
from fallsoil.responses.filters import eckhardt_baseflow, lyne_hollick_baseflow
from fallsoil.responses.params import ResponseParams
from fallsoil.responses.runs import runs_of_true
from fallsoil.water_year_calculator import wy_day, wy_end, wy_start

_NAN = float("nan")
_SEASON_RECESSION = ((6, 1), (10, 31))
_PEAK_WINDOW = ((3, 1), (7, 31))
_APRJUL = ((4, 1), (7, 31))
_APRSEP = ((4, 1), (9, 30))
_SPRING_END = (7, 31)
_LOWFLOW_WINDOW = ((8, 1), (10, 31))
_AUGSEP = ((8, 1), (9, 30))
_NEXT_OCT = ((10, 1), (10, 31))
_Q7_DAYS = 7
_PEAK_FRACTION = 0.5  # com threshold and days_peak_to_half level


# --------------------------------------------------------------------------- input validation
def validate_daily_series(flow_mm_d) -> pd.Series:
    """Check the structure of a daily flow series; returns it as a float Series (input is not modified)."""
    if not isinstance(flow_mm_d, pd.Series):
        raise DataValidationError("flow_mm_d must be a pandas Series with a DatetimeIndex.")
    if not isinstance(flow_mm_d.index, pd.DatetimeIndex):
        raise DataValidationError("flow_mm_d must have a DatetimeIndex of calendar days.")
    if flow_mm_d.empty:
        raise DataValidationError("flow_mm_d is empty.")
    if not pd.api.types.is_numeric_dtype(flow_mm_d) or pd.api.types.is_bool_dtype(flow_mm_d):
        raise DataValidationError(f"flow_mm_d must be numeric, got dtype {flow_mm_d.dtype}.")
    idx = flow_mm_d.index
    if idx.tz is not None or idx.hasnans or (idx != idx.normalize()).any():
        raise DataValidationError("flow_mm_d index must be naive whole calendar days (no time of day, no NaT).")
    if idx.has_duplicates:
        raise DataValidationError("flow_mm_d index has duplicate dates.")
    return flow_mm_d.astype(float)


# --------------------------------------------------------------------------- bookkeeping
class _Ledger:
    """Collects metric values and the reason for every NaN."""

    def __init__(self):
        self.values: dict = {}
        self.reasons: list[str] = []

    def fail(self, cols, cause: str) -> None:
        for c in cols:
            self.values[c] = _NAN
        self.reasons.append(f"{', '.join(cols)}: {cause}")


def _label(md) -> str:
    return f"{calendar.month_abbr[md[0]]} {md[1]}"


@dataclass
class _Year:
    """Daily flow of one water year plus the following October (NaN = invalid or absent day)."""

    wy: int
    params: ResponseParams
    q: np.ndarray
    n_wy: int
    day_numbers: np.ndarray
    start: pd.Timestamp

    def offset(self, md) -> int:
        """Index of calendar date `md` in calendar year `wy` (Oct 1 of year `wy` lies in the next water year)."""
        return (pd.Timestamp(year=self.wy, month=md[0], day=md[1]) - self.start).days

    def bounds(self, first, last) -> tuple[int, int]:
        return self.offset(first), self.offset(last) + 1

    def window(self, first, last):
        """(values, None) when the window passes the completeness gate, else (None, cause)."""
        i0, i1 = self.bounds(first, last)
        values = self.q[i0:i1]
        n_valid = int(np.isfinite(values).sum())
        need = self.params.min_window_fraction * values.size
        if n_valid < need:
            return None, (f"only {n_valid} valid days of {values.size} in {_label(first)}-{_label(last)} "
                          f"({self.params.min_window_fraction:.0%} required)")
        return values, None

    @property
    def wy_q(self) -> np.ndarray:
        return self.q[:self.n_wy]

    def day_of(self, index: int) -> float:
        return float(self.day_numbers[index])


def _build_year(series: pd.Series, wy: int, params: ResponseParams) -> tuple[_Year, np.ndarray]:
    start = pd.Timestamp(wy_start(wy))
    n_wy = (pd.Timestamp(wy_end(wy)) - start).days + 1
    full = pd.date_range(start, pd.Timestamp(year=int(wy), month=10, day=31), freq="D")
    raw = series.reindex(full).to_numpy(dtype=float)
    valid = np.isfinite(raw) & (raw >= 0)
    day_numbers = wy_day(full[:n_wy]).to_numpy(dtype=int)
    return _Year(int(wy), params, np.where(valid, raw, _NAN), n_wy, day_numbers, start), raw[:n_wy]


# --------------------------------------------------------------------------- metric groups
def _volumes(yr: _Year, led: _Ledger) -> None:
    vol = float(np.nansum(yr.wy_q))
    led.values["vol_wy_mm"] = vol
    for col, win in (("vol_aprjul_mm", _APRJUL), ("vol_aprsep_mm", _APRSEP)):
        values, cause = yr.window(*win)
        if values is None:
            led.fail([col, "frac_aprjul"] if col == "vol_aprjul_mm" else [col], cause)
        else:
            led.values[col] = float(np.nansum(values))
    if "frac_aprjul" not in led.values:
        if vol == 0.0:
            led.fail(["frac_aprjul"], "total water-year volume is 0")
        else:
            led.values["frac_aprjul"] = led.values["vol_aprjul_mm"] / vol


def _centre_of_mass(yr: _Year, led: _Ledger) -> None:
    cum = np.cumsum(np.nan_to_num(yr.wy_q))
    if cum[-1] == 0.0:
        led.fail(["com_wy_day"], "total water-year volume is 0")
    else:
        led.values["com_wy_day"] = yr.day_of(int(np.argmax(cum >= _PEAK_FRACTION * cum[-1])))


def _spring_pulse(yr: _Year, led: _Ledger) -> None:
    first = yr.params.spring_pulse_start
    values, cause = yr.window(first, _SPRING_END)
    if values is None:
        led.fail(["spring_pulse_day"], cause)
        return
    departure = np.cumsum(np.nan_to_num(values - np.nanmean(values)))
    departure[~np.isfinite(values)] = np.inf  # the minimum must fall on a valid day
    led.values["spring_pulse_day"] = yr.day_of(yr.offset(first) + int(np.argmin(departure)))


def _peak(yr: _Year, led: _Ledger) -> None:
    cols = ["peak_mm_d", "peak_wy_day", "days_peak_to_half"]
    values, cause = yr.window(*_PEAK_WINDOW)
    if values is None:
        led.fail(cols, cause)
        return
    i_peak = yr.offset(_PEAK_WINDOW[0]) + int(np.nanargmax(values))
    peak = float(yr.q[i_peak])
    led.values["peak_mm_d"], led.values["peak_wy_day"] = peak, yr.day_of(i_peak)
    after = yr.q[i_peak + 1:yr.n_wy]
    with np.errstate(invalid="ignore"):
        below = np.flatnonzero(after <= _PEAK_FRACTION * peak)
    if below.size == 0:
        led.fail(["days_peak_to_half"], "flow never falls to half of the peak within the water year")
    else:
        led.values["days_peak_to_half"] = float(below[0] + 1)


def _flashiness(yr: _Year, led: _Ledger) -> None:
    q = yr.wy_q
    both = np.isfinite(q[1:]) & np.isfinite(q[:-1])
    denominator = float(q[1:][both].sum())
    if denominator == 0.0:
        led.fail(["rb_flashiness"], "sum of flow over the usable days is 0")
    else:
        led.values["rb_flashiness"] = float(np.abs(np.diff(q))[both].sum()) / denominator


def _recession(yr: _Year, led: _Ledger) -> None:
    p = yr.params
    i0, i1 = yr.bounds(*_SEASON_RECESSION)
    limbs = recession.falling_limb_fit_points(yr.q[i0:i1], p.recession_min_days, p.recession_skip_days)
    led.values["recession_n_segments"] = len(limbs)
    bn_cols = ["bn_exponent_b", "bn_a", "bn_r2"]
    if not limbs:
        cause = (f"no falling limb with >= {p.recession_min_days} strictly decreasing days "
                 f"in {_label(_SEASON_RECESSION[0])}-{_label(_SEASON_RECESSION[1])}")
        led.fail(["recession_k_days"], cause)
        led.fail(bn_cols, cause)
        led.values["bn_n_points"], led.values["bn_fit_ok"] = 0, False
        return
    led.values["recession_k_days"] = recession.recession_constant_days(limbs)
    fit = recession.brutsaert_nieber(limbs, p.bn_min_points, p.bn_min_r2)
    led.values["bn_n_points"], led.values["bn_fit_ok"] = fit.n_points, bool(fit.ok)
    if fit.cause:
        led.fail(bn_cols, fit.cause)
    else:
        led.values.update(bn_exponent_b=fit.b, bn_a=fit.a, bn_r2=fit.r2)


def _baseflow(yr: _Year, led: _Ledger) -> None:
    """Filter each maximal run of valid days separately (never bridging a gap) and sum over the water year."""
    p = yr.params
    q = yr.wy_q
    eck = lh = 0.0
    for i0, i1 in runs_of_true(np.isfinite(q)):
        eck += float(eckhardt_baseflow(q[i0:i1], p.eckhardt_alpha, p.eckhardt_bfi_max).sum())
        lh += float(lyne_hollick_baseflow(q[i0:i1], p.lh_alpha, p.lh_passes).sum())
    led.values["baseflow_eck_wy_mm"], led.values["baseflow_lh_wy_mm"] = eck, lh
    vol = led.values["vol_wy_mm"]
    for col, total in (("bfi_wy_eck", eck), ("bfi_wy_lh", lh)):
        if vol == 0.0:
            led.fail([col], "total water-year volume is 0")
        else:
            led.values[col] = total / vol


def _low_flow(yr: _Year, led: _Ledger) -> None:
    values, cause = yr.window(*_LOWFLOW_WINDOW)
    if values is None:
        led.fail(["q7min_mm_d"], cause)
    else:
        means = sliding_window_view(values, _Q7_DAYS).mean(axis=1)  # NaN where any of the 7 days is invalid
        if np.isnan(means).all():
            led.fail(["q7min_mm_d"], f"no run of {_Q7_DAYS} consecutive valid days in the window")
        else:
            led.values["q7min_mm_d"] = float(np.nanmin(means))
    for col, win in (("augsep_mean_mm_d", _AUGSEP), ("next_oct_mean_mm_d", _NEXT_OCT)):
        values, cause = yr.window(*win)
        if values is None:
            led.fail([col], cause)
        else:
            led.values[col] = float(np.nanmean(values))


_GROUPS = (_volumes, _centre_of_mass, _spring_pulse, _peak, _flashiness, _recession, _baseflow, _low_flow)


# --------------------------------------------------------------------------- public computation
def _gate_failed(wy: int, n_days: int, n_valid: int, n_negative: int, params: ResponseParams) -> dict:
    out = {c: _NAN for c in METRIC_COLUMNS}
    out.update(wy=int(wy), n_days_wy=n_days, n_valid_days_wy=n_valid, n_negative_days_wy=n_negative,
               recession_n_segments=0, bn_n_points=0, bn_fit_ok=False, status="nan",
               reasons=(f"all metrics: only {n_valid} valid days of {n_days} in water year {wy} "
                        f"({params.min_wy_valid_days} required)"))
    return out


def compute_wy_metrics(series: pd.Series, wy: int, params: ResponseParams) -> dict:
    """`wy_metrics` for an already validated series (it may be empty or hold only part of the record)."""
    yr, raw_wy = _build_year(series, wy, params)
    n_valid = int(np.isfinite(yr.wy_q).sum())
    n_negative = int((np.isfinite(raw_wy) & (raw_wy < 0)).sum())
    if n_valid < params.min_wy_valid_days:
        return _gate_failed(wy, yr.n_wy, n_valid, n_negative, params)
    led = _Ledger()
    for group in _GROUPS:
        group(yr, led)
    unexplained = [c for c in METRIC_COLUMNS if np.isnan(led.values[c]) and not any(c in r for r in led.reasons)]
    if unexplained:  # defensive: a NaN must never go unexplained
        led.reasons.append(f"{', '.join(unexplained)}: not computable from the available data")
    nan_cols = [c for c in METRIC_COLUMNS if np.isnan(led.values[c])]
    out = dict(led.values)
    out.update(wy=int(wy), n_days_wy=yr.n_wy, n_valid_days_wy=n_valid, n_negative_days_wy=n_negative,
               status="partial" if nan_cols else "ok", reasons="; ".join(led.reasons))
    return out


def wy_metrics(flow_mm_d: pd.Series, wy: int, params: ResponseParams | None = None) -> dict:
    """Response metrics of water year `wy` from a daily series `flow_mm_d` (mm/d, DatetimeIndex).

    Returns the `TABLE_COLUMNS` entries except `frac_provisional`. Raises `DataValidationError` for a series that is
    not a non-empty numeric Series on unique naive calendar days.
    """
    return compute_wy_metrics(validate_daily_series(flow_mm_d), wy, params or ResponseParams())
