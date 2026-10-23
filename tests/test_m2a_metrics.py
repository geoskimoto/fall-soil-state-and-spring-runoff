"""M2a: per-water-year metrics recover planted synthetic truth (interface: tests/INTERFACE_M2A.md).

Every tolerance below is derived from the arithmetic of the planted series (stated beside each test),
not from any implementation's output.
"""
import dataclasses
import json
import math

import numpy as np
import pandas as pd
import pytest

import m2_builders as B
from fallsoil.errors import DataValidationError
from fallsoil.responses import (METRIC_COLUMNS, TABLE_COLUMNS, ResponseParams, eckhardt_baseflow,
                                lyne_hollick_baseflow, wy_metrics)
from fallsoil.water_year_calculator import wy_day

WY = 2021   # 365 days (Oct 2020 - Sep 2021)
LEAP = 2020  # 366 days (contains Feb 29, 2020)


def ts(year, month, day):
    return pd.Timestamp(year=year, month=month, day=day)


def cal_days(year, month, day, n):
    """n consecutive calendar days starting at (year, month, day); `year` is the CALENDAR year."""
    return pd.date_range(ts(year, month, day), periods=n, freq="D")


def two_years(builder, wy=WY):
    return pd.concat([builder(wy), builder(wy + 1)])


# =============================================================================== structure
def test_result_keys_types_and_columns():
    m = wy_metrics(B.gaussian_year(WY), WY)
    assert set(m) == set(TABLE_COLUMNS) - {"frac_provisional"}
    assert set(METRIC_COLUMNS) <= set(m)
    assert m["wy"] == WY and m["n_days_wy"] == 365 and m["n_valid_days_wy"] == 365 and m["n_negative_days_wy"] == 0
    assert isinstance(m["bn_fit_ok"], (bool, np.bool_))
    assert m["status"] in {"ok", "partial", "nan"} and isinstance(m["reasons"], str)


def test_metric_columns_are_the_float_metrics_in_table_order():
    assert list(TABLE_COLUMNS) == [
        "wy", "n_days_wy", "n_valid_days_wy", "n_negative_days_wy",
        "vol_wy_mm", "vol_aprjul_mm", "vol_aprsep_mm", "frac_aprjul", "com_wy_day", "spring_pulse_day",
        "peak_mm_d", "peak_wy_day", "rb_flashiness", "recession_k_days", "recession_n_segments",
        "bn_exponent_b", "bn_a", "bn_n_points", "bn_r2", "bn_fit_ok",
        "baseflow_eck_wy_mm", "baseflow_lh_wy_mm", "bfi_wy_eck", "bfi_wy_lh",
        "q7min_mm_d", "augsep_mean_mm_d", "next_oct_mean_mm_d", "days_peak_to_half",
        "frac_provisional", "status", "reasons"]
    ints = {"wy", "n_days_wy", "n_valid_days_wy", "n_negative_days_wy", "recession_n_segments", "bn_n_points",
            "bn_fit_ok", "status", "reasons", "frac_provisional"}
    assert set(METRIC_COLUMNS) == set(TABLE_COLUMNS) - ints
    assert "frac_provisional" not in METRIC_COLUMNS


def test_fully_featured_year_is_status_ok():
    def year(wy):
        return B.recession_year(wy, [((6, 1), 10.0 * np.exp(-np.arange(41) / 15.0))])
    m = wy_metrics(two_years(year), WY)
    assert m["status"] == "ok" and m["reasons"] == ""
    for c in METRIC_COLUMNS:
        assert math.isfinite(m[c]), c


def test_missing_next_october_makes_year_partial_and_every_nan_column_is_named():
    m = wy_metrics(B.gaussian_year(WY), WY)  # no data after Sep 30
    assert m["status"] == "partial"
    nan_cols = [c for c in METRIC_COLUMNS if isinstance(m[c], float) and math.isnan(m[c])]
    assert {"next_oct_mean_mm_d", "q7min_mm_d"} <= set(nan_cols)
    for c in nan_cols:
        assert c in m["reasons"], c
    assert math.isfinite(m["vol_wy_mm"]) and math.isfinite(m["peak_mm_d"])


# =============================================================================== volumes
@pytest.mark.parametrize("wy, n_wy", [(WY, 365), (LEAP, 366)])
def test_constant_flow_volumes(wy, n_wy):
    m = wy_metrics(B.constant_year(wy, 1.0), wy)
    assert m["n_days_wy"] == n_wy
    assert m["vol_wy_mm"] == pytest.approx(n_wy, abs=1e-9)
    assert m["vol_aprjul_mm"] == pytest.approx(122.0, abs=1e-9)   # 30+31+30+31
    assert m["vol_aprsep_mm"] == pytest.approx(183.0, abs=1e-9)   # + Aug 31 + Sep 30
    assert m["frac_aprjul"] == pytest.approx(122.0 / n_wy, rel=1e-12)


def test_volumes_equal_independent_window_sums():
    s = B.gaussian_year(WY, center=(5, 10), sigma_days=20.0, amp=15.0, base=0.7)
    m = wy_metrics(s, WY)
    assert m["vol_wy_mm"] == pytest.approx(s.sum(), rel=1e-12)
    assert m["vol_aprjul_mm"] == pytest.approx(B.window(s, WY, (4, 1), (7, 31)).sum(), rel=1e-12)
    assert m["vol_aprsep_mm"] == pytest.approx(B.window(s, WY, (4, 1), (9, 30)).sum(), rel=1e-12)
    assert m["frac_aprjul"] == pytest.approx(m["vol_aprjul_mm"] / m["vol_wy_mm"], rel=1e-12)
    assert m["vol_aprjul_mm"] <= m["vol_aprsep_mm"] <= m["vol_wy_mm"]


def test_data_outside_the_water_year_does_not_enter_the_volume():
    s = pd.concat([B.constant_year(WY - 1, 50.0), B.constant_year(WY, 1.0), B.constant_year(WY + 1, 50.0)])
    assert wy_metrics(s, WY)["vol_wy_mm"] == pytest.approx(365.0, abs=1e-9)


def test_sep30_and_oct1_belong_to_different_water_years():
    s = pd.concat([B.constant_year(2020, 1.0), B.constant_year(2021, 1.0)])
    s.loc[ts(2020, 9, 30)] = 100.0   # last day of WY2020
    s.loc[ts(2020, 10, 1)] = 1000.0  # first day of WY2021
    m20, m21 = wy_metrics(s, 2020), wy_metrics(s, 2021)
    assert m20["vol_wy_mm"] == pytest.approx(365 * 1.0 + 100.0, abs=1e-9)   # 366 days, leap
    assert m21["vol_wy_mm"] == pytest.approx(364 * 1.0 + 1000.0, abs=1e-9)  # 365 days
    assert m20["n_days_wy"] == 366 and m21["n_days_wy"] == 365


# =============================================================================== centre of mass
@pytest.mark.parametrize("wy, day", [(WY, (3, 1)), (LEAP, (3, 1)), (WY, (10, 1)), (WY, (9, 30)), (LEAP, (2, 29)), (WY, (12, 31))])
def test_delta_pulse_com_is_its_water_year_day(wy, day):
    s = B.delta_year(wy, day, 100.0, base=0.0)
    m = wy_metrics(s, wy)
    d = B.cal_date(wy, *day)
    assert m["com_wy_day"] == B.wy_day_of(d) == wy_day(d.date())
    assert 1 <= m["com_wy_day"] <= m["n_days_wy"]


def test_leap_year_shifts_day_numbers_after_feb_29():
    a = wy_metrics(B.delta_year(WY, (3, 1)), WY)["com_wy_day"]
    b = wy_metrics(B.delta_year(LEAP, (3, 1)), LEAP)["com_wy_day"]
    assert (a, b) == (152, 153)


def test_gaussian_pulse_com_within_one_day_of_planted_centre():
    # Symmetric about an integer day c: cumulative flow first reaches 50 percent on day c (+/- 1 for the
    # discrete base flow, itself uniform and so also centred only approximately). Truth is the planted centre.
    s = B.gaussian_year(WY, center=(5, 1), sigma_days=10.0, amp=50.0, base=0.0)
    m = wy_metrics(s, WY)
    assert abs(m["com_wy_day"] - B.wy_day_of(ts(2021, 5, 1))) <= 1


@pytest.mark.parametrize("center, sigma", [((6, 3), 15.0), ((4, 20), 25.0)])
def test_gaussian_with_base_flow_matches_independent_cumulative_sum(center, sigma):
    s = B.gaussian_year(WY, center=center, sigma_days=sigma, amp=20.0, base=0.5)
    assert abs(wy_metrics(s, WY)["com_wy_day"] - B.truth_com_day(s)) <= 1


def test_lognormal_snowmelt_pulse_com():
    s = B.lognormal_year(WY)
    m = wy_metrics(s, WY)
    assert abs(m["com_wy_day"] - B.truth_com_day(s)) <= 1
    # right-skewed pulse: the 50 percent point lies after the mode (mode = exp(mu - sigma^2) days after Mar 1)
    mode_day = B.wy_day_of(ts(2021, 3, 1)) + math.exp(4.0 - 0.25)
    assert m["com_wy_day"] > mode_day


# =============================================================================== peak
def test_peak_uses_only_mar1_to_jul31():
    s = B.gaussian_year(WY, center=(6, 3), amp=20.0, base=0.5)
    s.loc[ts(2021, 2, 10)] = 40.0   # bigger winter rain peak, outside the window
    s.loc[ts(2021, 8, 5)] = 60.0    # bigger late-summer spike, outside the window
    m = wy_metrics(s, WY)
    assert m["peak_mm_d"] == pytest.approx(20.5, rel=1e-12)
    assert m["peak_wy_day"] == B.wy_day_of(ts(2021, 6, 3))


@pytest.mark.parametrize("day, expected_in", [((3, 1), True), ((7, 31), True), ((2, 28), False), ((8, 1), False)])
def test_peak_window_boundaries_are_inclusive(day, expected_in):
    s = B.delta_year(WY, day, magnitude=50.0, base=1.0)
    s.loc[ts(2021, 5, 15)] = 10.0
    m = wy_metrics(s, WY)
    want = B.cal_date(WY, *day) if expected_in else ts(2021, 5, 15)
    assert m["peak_wy_day"] == B.wy_day_of(want)
    assert m["peak_mm_d"] == (50.0 if expected_in else 10.0)


def test_peak_tie_takes_first_day():
    s = B.constant_year(WY, 1.0)
    s.loc[[ts(2021, 4, 10), ts(2021, 6, 10)]] = 7.0
    assert wy_metrics(s, WY)["peak_wy_day"] == B.wy_day_of(ts(2021, 4, 10))


# =============================================================================== spring pulse onset
@pytest.mark.parametrize("step", [(3, 20), (4, 15), (5, 10)])
def test_step_up_gives_last_low_day(step):
    # D_t falls while Q < window mean and rises after; low < mean < high, so the minimum is the last low day,
    # i.e. the day BEFORE the planted step.
    s = B.step_year(WY, low=1.0, high=5.0, step=step)
    got = wy_metrics(s, WY)["spring_pulse_day"]
    assert got == B.wy_day_of(B.cal_date(WY, *step)) - 1


def test_step_in_leap_year_counts_feb_29():
    s = B.step_year(LEAP, low=1.0, high=5.0, step=(4, 15))
    got = wy_metrics(s, LEAP)["spring_pulse_day"]
    assert got == wy_day(ts(2020, 4, 15).date()) - 1 == B.wy_day_of(ts(2020, 4, 15)) - 1


def test_winter_flood_before_the_window_does_not_move_the_onset():
    s = B.step_year(WY, low=1.0, high=5.0, step=(4, 15))
    s.loc[cal_days(2021, 1, 5, 6)] = 80.0  # January flood, before Feb 1
    assert wy_metrics(s, WY)["spring_pulse_day"] == B.wy_day_of(ts(2021, 4, 15)) - 1


def test_onset_precedes_peak_for_a_snowmelt_pulse():
    m = wy_metrics(B.gaussian_year(WY, center=(6, 3), sigma_days=15.0), WY)
    assert m["spring_pulse_day"] < m["peak_wy_day"]
    assert B.wy_day_of(ts(2021, 2, 1)) <= m["spring_pulse_day"] <= B.wy_day_of(ts(2021, 7, 31))


def _jan_high_series():
    # Jan 1-31 at 30; Feb 1-Apr 14 at 1; Apr 15-Jul 31 at 5
    s = B.step_year(WY, low=1.0, high=5.0, step=(4, 15))
    s.loc[cal_days(2021, 1, 1, 31)] = 30.0
    return s


def test_default_window_starts_feb_1():
    # Feb1-Jul31 mean = (73*1 + 108*5)/181 = 3.39; D falls through the low stretch, rises after the step.
    assert wy_metrics(_jan_high_series(), WY)["spring_pulse_day"] == B.wy_day_of(ts(2021, 4, 14))


def test_configurable_start_changes_the_window_mean():
    # Jan 1 start: window mean 7.28 > 5, so D = sum(Q - mean) is positive after January, falls to exactly 0 at the
    # end of the window (departures sum to zero) and never gets below that: the minimum is the last window day.
    p = ResponseParams(spring_pulse_start=(1, 1))
    got = wy_metrics(_jan_high_series(), WY, p)["spring_pulse_day"]
    assert got == B.wy_day_of(ts(2021, 7, 31))


def test_spring_pulse_ignores_flow_after_jul_31():
    s = B.step_year(WY, low=1.0, high=5.0, step=(4, 15))
    s.loc[ts(2021, 8, 1):] = 0.01   # recession after the window
    assert wy_metrics(s, WY)["spring_pulse_day"] == B.wy_day_of(ts(2021, 4, 15)) - 1


# =============================================================================== flashiness
def test_constant_flow_has_zero_flashiness():
    assert wy_metrics(B.constant_year(WY, 3.0), WY)["rb_flashiness"] == pytest.approx(0.0, abs=1e-12)


def test_sawtooth_is_highly_flashy():
    # 1,3,1,3...: sum|dQ| = 364*2 = 728 and sum of Q over days 2..365 = 728 -> exactly 1.0 (RB definition);
    # summing Q over all 365 days gives 728/729 = 0.9986. Either reading is within 0.01 of 1.
    m = wy_metrics(B.sawtooth_year(WY), WY)
    assert m["rb_flashiness"] == pytest.approx(1.0, abs=0.01)
    assert m["rb_flashiness"] > 0.9


def test_smooth_sine_has_low_flashiness_matching_the_formula():
    s = B.sine_year(WY)
    got = wy_metrics(s, WY)["rb_flashiness"]
    assert got == pytest.approx(B.truth_rb(s), rel=0.01)  # 1/365 difference between denominator readings
    assert got < 0.01


def test_flashiness_ranks_spiky_above_smooth():
    smooth = wy_metrics(B.gaussian_year(WY), WY)["rb_flashiness"]
    spiky = wy_metrics(B.spiky_baseflow_year(WY, base=1.0, spike=30.0, spike_days=(40, 90, 140, 190, 240, 290)), WY)["rb_flashiness"]
    assert spiky > 5 * smooth


# =============================================================================== recession constant
@pytest.mark.parametrize("k", [8.0, 20.0, 45.0])
def test_linear_reservoir_recovers_k_within_one_percent(k):
    # Q = Q0 exp(-t/k) is exactly linear in ln Q, so an OLS slope returns k to floating point; 1 percent is the
    # stated acceptance, leaving room for any reasonable fitting choice.
    m = wy_metrics(B.exp_recession_year(WY, k_days=k), WY)
    assert m["recession_n_segments"] == 1
    assert m["recession_k_days"] == pytest.approx(k, rel=0.01)


def test_two_limbs_with_the_same_k_give_that_k():
    t = np.arange(36)
    s = B.recession_year(WY, [((6, 10), 10.0 * np.exp(-t / 18.0)), ((8, 20), 6.0 * np.exp(-t / 18.0))])
    m = wy_metrics(s, WY)
    assert m["recession_n_segments"] == 2
    assert m["recession_k_days"] == pytest.approx(18.0, rel=0.01)


def test_first_two_days_after_the_peak_are_excluded_from_the_fit():
    # A fast quickflow tail (and an inflated peak day) sits on the first days of the limb. Fitting from the peak
    # would bias k by several percent; after dropping the peak day and the first two decreasing days the residual
    # contamination is 3*exp(-6) = 0.0074 mm/d on 8.6 mm/d (< 0.1 percent) and decays, so k is within 0.1 percent.
    t = np.arange(41)
    q = 10.0 * np.exp(-t / 20.0) + 3.0 * np.exp(-t / 0.5)
    q[0] = 5.0 * q[1]  # the peak day itself (any value above day 1)
    m = wy_metrics(B.recession_year(WY, [((7, 1), q)]), WY)
    assert m["recession_n_segments"] == 1
    assert m["recession_k_days"] == pytest.approx(20.0, rel=0.01)


def test_short_falling_limb_does_not_qualify():
    t = np.arange(5)  # 4 strictly decreasing steps < 7
    m = wy_metrics(B.recession_year(WY, [((7, 1), 10.0 * np.exp(-t / 5.0))]), WY)
    assert m["recession_n_segments"] == 0
    assert math.isnan(m["recession_k_days"]) and "recession_k_days" in m["reasons"]
    assert not m["bn_fit_ok"]
    assert m["bn_n_points"] == 0 and math.isnan(m["bn_exponent_b"])


def test_limb_outside_jun1_oct31_does_not_count():
    t = np.arange(41)
    m = wy_metrics(B.recession_year(WY, [((4, 1), 10.0 * np.exp(-t / 15.0))]), WY)  # Apr 1 - May 11
    assert m["recession_n_segments"] == 0 and math.isnan(m["recession_k_days"])


def test_plateau_splits_a_limb_into_two_segments():
    t = np.arange(21)
    q = 10.0 * np.exp(-t / 15.0)
    q = np.concatenate([q[:11], q[10:]])  # repeated value => a non-strict step
    m = wy_metrics(B.recession_year(WY, [((7, 1), q)]), WY)
    assert m["recession_n_segments"] == 2  # 10 and 10 decreasing steps, both >= 7
    assert m["recession_k_days"] == pytest.approx(15.0, rel=0.01)


def test_missing_day_breaks_a_limb():
    t = np.arange(41)
    s = B.recession_year(WY, [((7, 1), 10.0 * np.exp(-t / 15.0))])
    s = B.with_nan(s, [ts(2021, 7, 21)])  # 20 steps in; two limbs of ~19 steps each remain
    m = wy_metrics(s, WY)
    assert m["recession_n_segments"] == 2
    assert m["recession_k_days"] == pytest.approx(15.0, rel=0.01)


# =============================================================================== Brutsaert-Nieber
@pytest.mark.parametrize("b", [1.5, 2.0])
def test_power_law_recession_recovers_exponent(b):
    # Tolerance 0.1 absolute. Sources of bias for noise-free data on the exact curve: (1) daily finite differences
    # with the 2-point mean flow, relative error O(eps^2) with eps <= 5 percent/day (a direct calculation for
    # these series gives |db| < 0.04); (2) log-binning of the lower envelope.  0.1 keeps both with margin.
    m = wy_metrics(B.power_recession_year(WY, b), WY)
    assert m["recession_n_segments"] == 2
    assert m["bn_exponent_b"] == pytest.approx(b, abs=0.1)
    assert m["bn_n_points"] >= 10
    assert m["bn_fit_ok"] and m["bn_r2"] > 0.95
    assert m["bn_a"] > 0


def test_linear_reservoir_has_bn_exponent_one_and_a_equal_inverse_k():
    # dQ/dt = -Q/k  =>  b = 1 and a = 1/k (per day). a is not scale invariant in general (Dralle et al.) but b = 1
    # makes it dimension-free, so it can be checked: a within 15 percent covers a b error of 0.1 at ln Q ~ 1.
    k = 20.0
    m = wy_metrics(B.exp_recession_year(WY, k_days=k), WY)
    assert m["bn_exponent_b"] == pytest.approx(1.0, abs=0.1)
    assert m["bn_a"] == pytest.approx(1.0 / k, rel=0.15)


def test_too_few_points_gives_a_poor_fit_flag_but_still_k():
    t = np.arange(11)  # 10 steps: qualifies, but at most 8 fit days / 7 pairs < bn_min_points (10)
    m = wy_metrics(B.recession_year(WY, [((7, 1), 10.0 * np.exp(-t / 10.0))]), WY)
    assert m["recession_n_segments"] == 1 and m["recession_k_days"] == pytest.approx(10.0, rel=0.01)
    assert not m["bn_fit_ok"]
    assert 0 < m["bn_n_points"] < 10


# =============================================================================== baseflow (year level)
def test_constant_flow_baseflow_indices():
    m = wy_metrics(B.constant_year(WY, 1.0), WY)
    assert m["bfi_wy_lh"] == pytest.approx(1.0, abs=1e-6)       # nothing to remove
    assert m["bfi_wy_eck"] == pytest.approx(0.80, abs=0.01)     # Eckhardt steady state is BFImax, not 1
    assert m["baseflow_lh_wy_mm"] == pytest.approx(365.0, rel=1e-6)


def test_year_level_baseflow_equals_the_public_filters_on_a_gap_free_year():
    s = B.spiky_baseflow_year(WY)
    m = wy_metrics(s, WY)
    assert m["baseflow_eck_wy_mm"] == pytest.approx(eckhardt_baseflow(s.to_numpy()).sum(), rel=1e-9)
    assert m["baseflow_lh_wy_mm"] == pytest.approx(lyne_hollick_baseflow(s.to_numpy()).sum(), rel=1e-9)
    assert m["bfi_wy_eck"] == pytest.approx(m["baseflow_eck_wy_mm"] / m["vol_wy_mm"], rel=1e-12)
    assert m["bfi_wy_lh"] == pytest.approx(m["baseflow_lh_wy_mm"] / m["vol_wy_mm"], rel=1e-12)


def test_known_baseflow_plus_storm_spikes_is_bounded():
    s = B.spiky_baseflow_year(WY, base=2.0, spike=20.0, spike_days=(50, 300))
    m = wy_metrics(s, WY)
    for key in ("bfi_wy_eck", "bfi_wy_lh"):
        assert 0.0 < m[key] < 1.0 or m[key] == pytest.approx(1.0)
    assert 0.0 <= m["baseflow_eck_wy_mm"] <= m["vol_wy_mm"] + 1e-9
    assert 0.0 <= m["baseflow_lh_wy_mm"] <= m["vol_wy_mm"] + 1e-9
    assert m["bfi_wy_lh"] < 1.0  # the two storms are partly quickflow


def test_filters_are_restarted_after_a_gap_not_bridged():
    s = B.spiky_baseflow_year(WY)
    gap = B.with_nan(s, cal_days(2021, 1, 10, 10))
    m = wy_metrics(gap, WY)
    runs = [r.to_numpy() for r in (gap.iloc[:gap.index.get_loc(ts(2021, 1, 10))],
                                    gap.iloc[gap.index.get_loc(ts(2021, 1, 20)):])]
    expected = sum(lyne_hollick_baseflow(r).sum() for r in runs)
    assert m["baseflow_lh_wy_mm"] == pytest.approx(expected, rel=1e-9)
    assert m["vol_wy_mm"] == pytest.approx(gap.sum(), rel=1e-12)  # NaN days contribute nothing, nothing is filled


def test_filter_parameters_are_taken_from_params():
    s = B.constant_year(WY, 1.0)
    m = wy_metrics(s, WY, ResponseParams(eckhardt_bfi_max=0.5))
    assert m["bfi_wy_eck"] == pytest.approx(0.5, abs=0.01)
    spiky = B.spiky_baseflow_year(WY)
    one = wy_metrics(spiky, WY, ResponseParams(lh_passes=1))["baseflow_lh_wy_mm"]
    three = wy_metrics(spiky, WY)["baseflow_lh_wy_mm"]
    assert one != pytest.approx(three, rel=1e-6)


# =============================================================================== low-flow season
def _flat_two_years(value=1.0):
    return pd.concat([B.constant_year(WY, value), B.constant_year(WY + 1, value)])


def test_q7min_finds_a_seven_day_dip_and_not_a_six_day_one():
    s = _flat_two_years()
    s.loc[cal_days(2021, 9, 1, 7)] = 0.2
    assert wy_metrics(s, WY)["q7min_mm_d"] == pytest.approx(0.2, rel=1e-12)
    s = _flat_two_years()
    s.loc[cal_days(2021, 9, 1, 6)] = 0.2
    assert wy_metrics(s, WY)["q7min_mm_d"] == pytest.approx((6 * 0.2 + 1.0) / 7.0, rel=1e-12)


def test_q7min_window_runs_into_october_of_the_same_calendar_year():
    s = _flat_two_years()
    s.loc[cal_days(2021, 10, 10, 7)] = 0.1   # October 2021 belongs to WY2022 but to WY2021's Aug-Oct window
    assert wy_metrics(s, WY)["q7min_mm_d"] == pytest.approx(0.1, rel=1e-12)


def test_q7min_ignores_dips_outside_aug1_oct31_and_runs_straddling_the_edge():
    s = _flat_two_years()
    s.loc[cal_days(2021, 7, 1, 7)] = 0.0         # July: outside
    s.loc[cal_days(2021, 11, 1, 7)] = 0.0        # November: outside
    assert wy_metrics(s, WY)["q7min_mm_d"] == pytest.approx(1.0, rel=1e-12)
    s = _flat_two_years()
    s.loc[cal_days(2021, 7, 28, 7)] = 0.2        # Jul 28 - Aug 3: only the Aug 1-3 part can be in a window run
    # best in-window 7-day run starts Aug 1: 3 dip days + 4 normal
    assert wy_metrics(s, WY)["q7min_mm_d"] == pytest.approx((3 * 0.2 + 4 * 1.0) / 7.0, rel=1e-12)


def test_augsep_and_next_october_means():
    s = _flat_two_years()
    s.loc[cal_days(2021, 8, 1, 61)] = 2.0     # Aug 1 - Sep 30
    s.loc[cal_days(2021, 10, 1, 31)] = 3.0    # October after the water year
    m = wy_metrics(s, WY)
    assert m["augsep_mean_mm_d"] == pytest.approx(2.0, rel=1e-12)
    assert m["next_oct_mean_mm_d"] == pytest.approx(3.0, rel=1e-12)


def test_means_use_valid_days_only():
    s = _flat_two_years()
    s.loc[cal_days(2021, 8, 1, 61)] = 2.0
    s = B.with_nan(s, [ts(2021, 8, 10), ts(2021, 8, 11)])
    assert wy_metrics(s, WY)["augsep_mean_mm_d"] == pytest.approx(2.0, rel=1e-12)


def test_next_october_is_nan_when_the_record_ends_at_the_water_year():
    m = wy_metrics(B.constant_year(WY, 1.0), WY)
    assert math.isnan(m["next_oct_mean_mm_d"]) and "next_oct_mean_mm_d" in m["reasons"]
    assert m["status"] == "partial"


# =============================================================================== peak-to-half diagnostic
def test_days_peak_to_half_on_exponential_decline():
    # Q = 10 exp(-t ln2/12.5) crosses 5 between t = 12 (5.18) and t = 13 (4.88): first day <= half is t = 13.
    t = np.arange(61)
    m = wy_metrics(B.recession_year(WY, [((6, 1), 10.0 * np.exp(-t * math.log(2) / 12.5))]), WY)
    assert m["peak_mm_d"] == pytest.approx(10.0)
    assert m["days_peak_to_half"] == 13


def test_days_peak_to_half_nan_when_flow_never_halves():
    m = wy_metrics(B.step_year(WY, low=1.0, high=5.0, step=(4, 15)), WY)
    assert math.isnan(m["days_peak_to_half"]) and "days_peak_to_half" in m["reasons"]
    assert m["status"] == "partial"


# =============================================================================== completeness gates
def _gate_series(case_wy=WY):
    return pd.concat([B.gaussian_year(case_wy, center=(6, 3), base=0.5), B.gaussian_year(case_wy + 1, center=(6, 3), base=0.5)])


# (columns, first missing day (calendar year WY), max days missing that still pass)
# need = ceil(0.95 * N): N=122 -> 116, N=183 -> 174, N=181 -> 172, N=153 -> 146, N=92 -> 88, N=61 -> 58, N=31 -> 30.
# 0.95*N is never an integer for these windows, so the boundary is unambiguous in floating point.
WINDOW_CASES = [
    (("vol_aprjul_mm", "frac_aprjul"), (4, 1), 6),
    (("vol_aprsep_mm",), (4, 1), 9),
    (("spring_pulse_day",), (2, 1), 9),
    (("peak_mm_d", "peak_wy_day", "days_peak_to_half"), (3, 1), 7),
    (("q7min_mm_d",), (8, 1), 4),
    (("augsep_mean_mm_d",), (8, 1), 3),
    (("next_oct_mean_mm_d",), (10, 1), 1),
]


@pytest.mark.parametrize("how", ["nan", "missing_rows"])
@pytest.mark.parametrize("cols, start, max_ok", WINDOW_CASES, ids=[c[0][0] for c in WINDOW_CASES])
def test_window_gate_boundary(cols, start, max_ok, how):
    base = _gate_series()
    damage = B.with_nan if how == "nan" else B.without_days
    ok = wy_metrics(damage(base, cal_days(WY, *start, max_ok)), WY)
    bad = wy_metrics(damage(base, cal_days(WY, *start, max_ok + 1)), WY)
    for c in cols:
        assert math.isfinite(ok[c]), (c, "should pass the 95 percent gate")
        assert math.isnan(bad[c]), (c, "should fail the 95 percent gate")
        assert c in bad["reasons"]
    assert bad["status"] == "partial" and ok["status"] in {"ok", "partial"}
    assert math.isfinite(bad["vol_wy_mm"])  # the WY gate still holds, and only the affected metrics are NaN


def test_failing_one_window_leaves_unrelated_metrics_intact():
    base = _gate_series()
    ref = wy_metrics(base, WY)
    bad = wy_metrics(B.with_nan(base, cal_days(WY, 4, 1, 7)), WY)   # kills Apr-Jul volume only
    assert math.isnan(bad["vol_aprjul_mm"])
    assert bad["peak_mm_d"] == ref["peak_mm_d"] and bad["q7min_mm_d"] == pytest.approx(ref["q7min_mm_d"])
    assert bad["spring_pulse_day"] == ref["spring_pulse_day"]


@pytest.mark.parametrize("wy, n", [(WY, 365), (LEAP, 366)])
def test_wy_gate_is_exactly_350_valid_days(wy, n):
    base = B.gaussian_year(wy)
    ok = wy_metrics(B.with_nan(base, B.wy_dates(wy)[: n - 350]), wy)
    bad = wy_metrics(B.with_nan(base, B.wy_dates(wy)[: n - 349]), wy)
    assert ok["n_valid_days_wy"] == 350 and ok["status"] in {"ok", "partial"} and math.isfinite(ok["vol_wy_mm"])
    assert bad["n_valid_days_wy"] == 349 and bad["status"] == "nan"
    assert all(math.isnan(bad[c]) for c in METRIC_COLUMNS)
    assert "valid" in bad["reasons"].lower()
    assert bad["recession_n_segments"] == 0 and bad["bn_n_points"] == 0 and not bad["bn_fit_ok"]


def test_wy_gate_counts_absent_rows_like_nans():
    base = B.gaussian_year(WY)
    assert wy_metrics(B.without_days(base, B.wy_dates(WY)[:15]), WY)["n_valid_days_wy"] == 350
    assert wy_metrics(B.without_days(base, B.wy_dates(WY)[:16]), WY)["status"] == "nan"


def test_wy_gate_when_com_would_otherwise_exist():
    m = wy_metrics(B.with_nan(B.gaussian_year(WY), B.wy_dates(WY)[:16]), WY)
    assert math.isnan(m["com_wy_day"]) and math.isnan(m["vol_wy_mm"])


def test_params_move_the_gates():
    base = B.with_nan(B.gaussian_year(WY), B.wy_dates(WY)[:10])   # 355 valid
    assert wy_metrics(base, WY, ResponseParams(min_wy_valid_days=355))["status"] != "nan"
    assert wy_metrics(base, WY, ResponseParams(min_wy_valid_days=356))["status"] == "nan"
    one_missing = _gate_series()
    one_missing = B.with_nan(one_missing, [ts(2021, 5, 5)])
    assert math.isnan(wy_metrics(one_missing, WY, ResponseParams(min_window_fraction=1.0))["vol_aprjul_mm"])
    assert math.isfinite(wy_metrics(one_missing, WY, ResponseParams(min_window_fraction=0.99))["vol_aprjul_mm"])
    many = B.with_nan(_gate_series(), cal_days(2021, 4, 1, 30))   # 92 of 122 Apr-Jul days, 335 of 365 in the WY
    assert math.isnan(wy_metrics(many, WY, ResponseParams(min_wy_valid_days=300))["vol_aprjul_mm"])
    relaxed = ResponseParams(min_wy_valid_days=300, min_window_fraction=0.5)
    assert math.isfinite(wy_metrics(many, WY, relaxed)["vol_aprjul_mm"])


# =============================================================================== invalid values
@pytest.mark.parametrize("n_bad, status_nan", [(15, False), (16, True)])
def test_negative_values_are_excluded_and_counted_never_clipped(n_bad, status_nan):
    # If negatives were clipped to 0 they would remain valid days and the gate would pass at 16.
    s = B.constant_year(WY, 1.0)
    s.iloc[10:10 + n_bad] = -5.0
    m = wy_metrics(s, WY)
    assert m["n_negative_days_wy"] == n_bad and m["n_valid_days_wy"] == 365 - n_bad
    assert (m["status"] == "nan") is status_nan
    if not status_nan:
        assert m["vol_wy_mm"] == pytest.approx(365.0 - n_bad, abs=1e-9)  # -5 not added, 0 not substituted


def test_negative_value_is_not_the_minimum_and_does_not_enter_means():
    s = _flat_two_years(2.0)
    s.loc[ts(2021, 9, 3)] = -9.0
    m = wy_metrics(s, WY)
    assert m["q7min_mm_d"] == pytest.approx(2.0, rel=1e-12)  # runs containing the bad day are not formed
    assert m["augsep_mean_mm_d"] == pytest.approx(2.0, rel=1e-12)
    assert m["n_negative_days_wy"] == 1


def test_infinite_values_are_invalid():
    s = B.constant_year(WY, 1.0)
    s.iloc[5] = np.inf
    m = wy_metrics(s, WY)
    assert m["n_valid_days_wy"] == 364 and math.isfinite(m["vol_wy_mm"]) and m["vol_wy_mm"] == pytest.approx(364.0)


def test_zero_flow_is_valid():
    m = wy_metrics(B.delta_year(WY, (5, 5), 10.0, base=0.0), WY)
    assert m["n_valid_days_wy"] == 365 and m["vol_wy_mm"] == pytest.approx(10.0)


def test_all_zero_year_gives_nan_ratios_with_reasons_not_exceptions():
    m = wy_metrics(B.constant_year(WY, 0.0), WY)
    assert m["vol_wy_mm"] == 0.0
    for c in ("com_wy_day", "frac_aprjul", "rb_flashiness", "bfi_wy_eck", "bfi_wy_lh"):
        assert math.isnan(m[c]), c
        assert c in m["reasons"], c


# =============================================================================== partial years, structure errors
def test_partial_first_year_is_nan_with_reason():
    s = pd.Series(1.0, index=pd.date_range("2021-01-01", "2021-09-30"), name="flow_mm_d")   # 273 days of WY2021
    m = wy_metrics(s, WY)
    assert m["status"] == "nan" and m["n_valid_days_wy"] == 273
    assert "valid" in m["reasons"].lower()


def test_partial_last_year_is_nan_with_reason():
    s = pd.Series(1.0, index=pd.date_range("2020-10-01", "2021-06-30"), name="flow_mm_d")  # 273 days
    m = wy_metrics(s, WY)
    assert m["status"] == "nan" and all(math.isnan(m[c]) for c in METRIC_COLUMNS)


def test_water_year_absent_from_the_series():
    m = wy_metrics(B.constant_year(WY, 1.0), 2019)
    assert m["n_valid_days_wy"] == 0 and m["status"] == "nan" and m["n_days_wy"] == 365
    assert all(math.isnan(m[c]) for c in METRIC_COLUMNS)


def test_input_series_is_not_mutated_and_order_does_not_matter():
    s = B.gaussian_year(WY)
    shuffled = s.sample(frac=1.0, random_state=3)
    before = shuffled.copy()
    a, b = wy_metrics(s, WY), wy_metrics(shuffled, WY)
    pd.testing.assert_series_equal(shuffled, before)
    assert a.keys() == b.keys()
    for k in a:
        if isinstance(a[k], float) and math.isnan(a[k]):
            assert math.isnan(b[k])
        elif isinstance(a[k], (float, np.floating)):
            assert b[k] == pytest.approx(a[k], rel=1e-12), k
        else:
            assert a[k] == b[k], k


def test_non_datetime_index_raises_data_validation_error():
    with pytest.raises(DataValidationError):
        wy_metrics(pd.Series(np.ones(365), name="flow_mm_d"), WY)
    with pytest.raises(DataValidationError):
        wy_metrics(pd.Series(np.ones(3), index=["2021-01-01", "2021-01-02", "2021-01-03"]), WY)


def test_empty_series_raises_data_validation_error():
    with pytest.raises(DataValidationError):
        wy_metrics(pd.Series([], index=pd.DatetimeIndex([]), dtype=float), WY)


def test_duplicate_index_raises_data_validation_error():
    s = B.constant_year(WY, 1.0)
    dup = pd.concat([s, s.iloc[:1]])
    with pytest.raises(DataValidationError):
        wy_metrics(dup, WY)


def test_non_numeric_series_raises_data_validation_error():
    s = pd.Series(["a"] * 5, index=pd.date_range("2021-01-01", periods=5))
    with pytest.raises(DataValidationError):
        wy_metrics(s, WY)


# =============================================================================== ResponseParams
def test_response_params_defaults_and_serialisation():
    p = ResponseParams()
    assert (p.min_wy_valid_days, p.min_window_fraction, tuple(p.spring_pulse_start)) == (350, 0.95, (2, 1))
    assert (p.eckhardt_alpha, p.eckhardt_bfi_max, p.lh_alpha, p.lh_passes) == (0.98, 0.80, 0.925, 3)
    assert (p.recession_min_days, p.recession_skip_days, p.bn_min_points, p.bn_min_r2) == (7, 2, 10, 0.8)
    d = p.as_dict()
    assert set(d) == {f.name for f in dataclasses.fields(p)}
    assert json.loads(json.dumps(d)) == d  # JSON-serialisable and round-trips (tuples are lists)


def test_response_params_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        ResponseParams().min_wy_valid_days = 10


@pytest.mark.parametrize("kw", [
    {"min_wy_valid_days": 0}, {"min_wy_valid_days": 400}, {"min_window_fraction": 0.0},
    {"min_window_fraction": 1.5}, {"eckhardt_alpha": 1.0}, {"eckhardt_alpha": 0.0}, {"lh_alpha": 1.2},
    {"eckhardt_bfi_max": 0.0}, {"eckhardt_bfi_max": 1.1}, {"lh_passes": 0}, {"spring_pulse_start": (13, 1)},
    {"spring_pulse_start": (2, 30)},
])
def test_response_params_validation(kw):
    with pytest.raises(ValueError):
        ResponseParams(**kw)


def test_unknown_parameter_is_rejected():
    with pytest.raises(TypeError):
        ResponseParams(not_a_parameter=1)
