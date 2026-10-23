"""M2a: hypothesis property tests on the per-basin driver (interface: tests/INTERFACE_M2A.md)."""
import math

import numpy as np
import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import m2_builders as B
from fallsoil.responses import ResponseParams, compute_responses

AREA = 120.0
WYS = (2003, 2004, 2005)   # WY2004 is a leap year (366 days)
SETTINGS = settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def random_mm(seed: int, wys=WYS) -> pd.Series:
    """Positive, persistent random daily flow (AR(1) in log space plus a spring bump), in mm/d."""
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex(np.concatenate([B.wy_dates(w) for w in wys]))
    n = len(idx)
    x = np.zeros(n)
    eps = rng.normal(0.0, 0.35, n)
    for i in range(1, n):
        x[i] = 0.93 * x[i - 1] + eps[i]
    doy = idx.day_of_year.to_numpy()
    bump = 2.0 * np.exp(-0.5 * ((doy - 150) / 30.0) ** 2)
    return pd.Series(np.exp(x * 0.8) * (0.5 + bump), index=idx, name="flow_mm_d")


def frame_of(s: pd.Series, area=AREA) -> pd.DataFrame:
    return B.to_usgs_frame(s, area)


def num_equal(a, b, rel=1e-9):
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    if pd.isna(a) or pd.isna(b):
        return bool(pd.isna(a) and pd.isna(b))
    return a == pytest.approx(b, rel=rel, abs=1e-12)


seeds = st.integers(min_value=0, max_value=10**6)


# ---------------------------------------------------------------- volume conservation
@SETTINGS
@given(seed=seeds)
def test_volume_conservation_and_window_consistency(seed):
    s = random_mm(seed)
    t = compute_responses(frame_of(s), AREA)
    B.check_table_invariants(t)
    for _, r in t.iterrows():
        wy = int(r["wy"])
        in_wy = s[(s.index >= B.wy_dates(wy)[0]) & (s.index <= B.wy_dates(wy)[-1])]
        assert r["vol_wy_mm"] == pytest.approx(in_wy.sum(), rel=1e-9)
        aprjul = B.window(s, wy, (4, 1), (7, 31)).sum()
        augsep = B.window(s, wy, (8, 1), (9, 30)).sum()
        assert r["vol_aprjul_mm"] == pytest.approx(aprjul, rel=1e-9)
        assert r["vol_aprsep_mm"] - r["vol_aprjul_mm"] == pytest.approx(augsep, rel=1e-8)
        assert 0.0 < r["vol_aprjul_mm"] <= r["vol_aprsep_mm"] <= r["vol_wy_mm"] * (1 + 1e-12)
        assert r["frac_aprjul"] == pytest.approx(r["vol_aprjul_mm"] / r["vol_wy_mm"], rel=1e-12)
        assert 0.0 < r["frac_aprjul"] < 1.0
    assert t["vol_wy_mm"].sum() == pytest.approx(s.sum(), rel=1e-9)  # nothing lost between water years


@SETTINGS
@given(seed=seeds)
def test_timing_metrics_stay_in_their_ranges(seed):
    t = compute_responses(frame_of(random_mm(seed)), AREA)
    for _, r in t.iterrows():
        n = int(r["n_days_wy"])
        assert 1 <= r["com_wy_day"] <= n
        assert B.wy_day_of(pd.Timestamp(r["wy"], 3, 1)) <= r["peak_wy_day"] <= B.wy_day_of(pd.Timestamp(r["wy"], 7, 31))
        assert B.wy_day_of(pd.Timestamp(r["wy"], 2, 1)) <= r["spring_pulse_day"] <= B.wy_day_of(pd.Timestamp(r["wy"], 7, 31))
        assert r["rb_flashiness"] >= 0.0


def test_peak_is_the_window_maximum_hence_at_least_the_window_mean():
    s = random_mm(7)
    t = compute_responses(frame_of(s), AREA)
    for _, r in t.iterrows():
        w = B.window(s, int(r["wy"]), (3, 1), (7, 31))
        assert r["peak_mm_d"] == pytest.approx(w.max(), rel=1e-9)
        assert r["peak_mm_d"] >= w.mean()


# ---------------------------------------------------------------- baseflow bounds at year level
@SETTINGS
@given(seed=seeds)
def test_baseflow_never_exceeds_flow(seed):
    t = compute_responses(frame_of(random_mm(seed)), AREA)
    for _, r in t.iterrows():
        for b, v in (("baseflow_eck_wy_mm", "bfi_wy_eck"), ("baseflow_lh_wy_mm", "bfi_wy_lh")):
            assert 0.0 <= r[b] <= r["vol_wy_mm"] * (1 + 1e-12)
            assert 0.0 <= r[v] <= 1.0 + 1e-12


# ---------------------------------------------------------------- scaling
INVARIANT = ["n_valid_days_wy", "com_wy_day", "spring_pulse_day", "peak_wy_day", "frac_aprjul", "rb_flashiness",
             "bfi_wy_eck", "bfi_wy_lh", "days_peak_to_half", "recession_n_segments", "recession_k_days", "status"]
SCALES_LINEARLY = ["vol_wy_mm", "vol_aprjul_mm", "vol_aprsep_mm", "peak_mm_d", "baseflow_eck_wy_mm",
                   "baseflow_lh_wy_mm", "q7min_mm_d", "augsep_mean_mm_d", "next_oct_mean_mm_d"]


@SETTINGS
@given(seed=seeds, c=st.floats(min_value=0.05, max_value=40.0))
def test_scaling_flow_scales_volumes_and_leaves_shape_metrics_unchanged(seed, c):
    # Every shape metric is a ratio, a day index, an argmin/argmax or a slope of ln Q: all homogeneous of degree 0.
    # Volumes, peak, filters and low-flow means are homogeneous of degree 1.
    f = frame_of(random_mm(seed))
    base = compute_responses(f, AREA)
    g = f.copy()
    g["flow_cfs"] = g["flow_cfs"] * c
    scaled = compute_responses(g, AREA)
    for col in INVARIANT:
        for a, b in zip(base[col], scaled[col]):
            assert num_equal(a, b, rel=1e-8), (col, a, b)
    for col in SCALES_LINEARLY:
        for a, b in zip(base[col], scaled[col]):
            assert num_equal(a * c, b, rel=1e-8), (col, a, b)


@SETTINGS
@given(seed=seeds, c=st.floats(min_value=0.1, max_value=20.0))
def test_larger_area_gives_proportionally_smaller_depths_and_same_shape(seed, c):
    f = frame_of(random_mm(seed))
    a = compute_responses(f, AREA)
    b = compute_responses(f, AREA * c)
    for col in ["com_wy_day", "frac_aprjul", "rb_flashiness", "bfi_wy_eck", "spring_pulse_day"]:
        for x, y in zip(a[col], b[col]):
            assert num_equal(x, y, rel=1e-8), col
    for x, y in zip(a["vol_wy_mm"], b["vol_wy_mm"]):
        assert y == pytest.approx(x / c, rel=1e-9)


# ---------------------------------------------------------------- calendar shift
@SETTINGS
@given(seed=seeds, k=st.integers(min_value=1, max_value=5))
def test_shifting_dates_by_whole_leap_cycles_changes_nothing_but_the_label(seed, k):
    # +4k calendar years preserves every leap pattern (no 2100 in range), so each WY must give identical metrics.
    f = frame_of(random_mm(seed))
    g = f.copy()
    g["date"] = g["date"] + pd.DateOffset(years=4 * k)
    a, b = compute_responses(f, AREA), compute_responses(g, AREA)
    assert list(b["wy"]) == [w + 4 * k for w in a["wy"]]
    for col in a.columns:
        if col in ("wy", "reasons"):   # reason text may legitimately quote dates
            continue
        for x, y in zip(a[col], b[col]):
            assert num_equal(x, y, rel=1e-9), (col, x, y)


# ---------------------------------------------------------------- gaps and gates
@SETTINGS
@given(seed=seeds, frac=st.floats(min_value=0.0, max_value=0.12))
def test_random_gaps_follow_the_gates_and_nothing_is_filled(seed, frac):
    s = random_mm(seed, wys=(2003, 2004))
    rng = np.random.default_rng(seed + 1)
    mask = rng.random(len(s)) < frac
    damaged = s.mask(mask)
    t = compute_responses(frame_of(damaged), AREA)
    B.check_table_invariants(t)
    for _, r in t.iterrows():
        wy = int(r["wy"])
        sel = (damaged.index >= B.wy_dates(wy)[0]) & (damaged.index <= B.wy_dates(wy)[-1])
        in_wy = damaged[sel]
        assert r["n_valid_days_wy"] == in_wy.notna().sum()
        if r["status"] != "nan":
            assert r["vol_wy_mm"] == pytest.approx(in_wy.sum(), rel=1e-9)   # sum of what exists, no fill
            assert 1 <= r["com_wy_day"] <= r["n_days_wy"]


def test_changing_the_gate_parameter_changes_the_status_boundary():
    s = random_mm(11, wys=(2003,))
    s = s.mask(np.arange(len(s)) < 20)      # 345 valid days
    f = frame_of(s)
    assert compute_responses(f, AREA).loc[0, "status"] == "nan"
    assert compute_responses(f, AREA, ResponseParams(min_wy_valid_days=340)).loc[0, "status"] != "nan"
