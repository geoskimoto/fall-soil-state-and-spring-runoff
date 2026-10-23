"""Recession analysis on falling limbs: linear-reservoir constant and the Brutsaert-Nieber relation."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fallsoil.responses.runs import runs_of_true

_BN_N_BINS = 10  # log-spaced Q bins used to extract the lower envelope
_BN_MIN_ENVELOPE_POINTS = 3  # fewest envelope points a line can be fitted to


def falling_limb_fit_points(season: np.ndarray, min_days: int, skip_days: int) -> list[np.ndarray]:
    """Fit points (mm/d) of every qualifying falling limb in `season` (daily array, NaN = invalid).

    A falling limb is a maximal run of calendar-consecutive valid days with Q > 0 and Q_t < Q_{t-1} at every step;
    it qualifies with >= `min_days` such steps. Its fit points are the days left after dropping the peak day and the
    first `skip_days` decreasing days (quickflow tail). Limbs with fewer than 2 fit points are not usable.
    """
    step_down = np.zeros(season.size, dtype=bool)
    with np.errstate(invalid="ignore"):
        step_down[1:] = (season[1:] > 0) & (season[1:] < season[:-1])
    limbs = []
    for start, stop in runs_of_true(step_down):
        if stop - start < min_days:
            continue
        points = season[start - 1:stop][1 + skip_days:]  # start - 1 is the peak day
        if points.size >= 2:
            limbs.append(points)
    return limbs


def _ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Least-squares line y = intercept + slope x; returns (slope, intercept, r2) (r2 NaN if y is constant)."""
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (intercept + slope * x)
    sst = float(np.sum((y - y.mean()) ** 2))
    r2 = float("nan") if sst == 0.0 else 1.0 - float(np.sum(resid**2)) / sst
    return float(slope), float(intercept), r2


def recession_constant_days(limbs: list[np.ndarray]) -> float:
    """Median over limbs of k = -1/slope of ln Q against day (linear reservoir Q = Q0 exp(-t/k))."""
    ks = []
    for q in limbs:
        slope, _, _ = _ols(np.arange(q.size, dtype=float), np.log(q))
        ks.append(-1.0 / slope)
    return float(np.median(ks))


def _pooled_pairs(limbs: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """(Q_mid, -dQ) per day over all limbs: the mean flow of consecutive fit points and their drop."""
    q_mid = np.concatenate([(q[:-1] + q[1:]) / 2.0 for q in limbs])
    drop = np.concatenate([q[:-1] - q[1:] for q in limbs])
    return q_mid, drop


def lower_envelope(log_x: np.ndarray, log_y: np.ndarray, n_bins: int = _BN_N_BINS) -> tuple[np.ndarray, np.ndarray]:
    """Lowest point of the cloud in each of `n_bins` equal-width bins of log_x (empty bins are skipped)."""
    edges = np.linspace(log_x.min(), log_x.max(), n_bins + 1)
    bin_id = np.digitize(log_x, edges[1:-1])
    xs, ys = [], []
    for b in range(n_bins):
        in_bin = np.flatnonzero(bin_id == b)
        if in_bin.size:
            j = in_bin[np.argmin(log_y[in_bin])]
            xs.append(log_x[j])
            ys.append(log_y[j])
    return np.array(xs), np.array(ys)


@dataclass(frozen=True)
class BNFit:
    """Brutsaert-Nieber fit; `b`, `a`, `r2` are NaN when no line could be fitted (see `cause`)."""

    b: float
    a: float
    r2: float
    n_points: int
    ok: bool
    cause: str = ""


def brutsaert_nieber(limbs: list[np.ndarray], min_points: int, min_r2: float) -> BNFit:
    """Fit -dQ/dt = a Q^b to the lower envelope of the (Q_mid, -dQ) cloud pooled over the limbs.

    Method: points are (Q_mid, -dQ) from consecutive fit points (daily finite differences, mean flow of the pair).
    In log-log space the Q_mid range is cut into 10 equal-width bins and the lowest -dQ of each bin is kept (the lower
    envelope, after Brutsaert & Nieber 1977, which treats the cloud's lower bound as the drainage of the aquifer
    with the least quickflow/evapotranspiration contamination). A least-squares line through the envelope points
    gives b (slope) and ln a (intercept); `r2` is that line's R2. `ok` needs `n_points` (all pooled pairs) >=
    `min_points` and r2 >= `min_r2`.

    Caveats (Dralle et al. 2017, and earlier): a and b depend on how dQ/dt is differenced, the sampling interval,
    measurement noise at low flow and the envelope rule, so values are method dependent. `a` is NOT scale invariant:
    it carries units mm^(1-b) d^(b-2), and a depth/area change rescales it unless b = 1. The minimum per bin is
    sensitive to single noisy low values; treat b as an index, not a physical constant, and report `n_points`/`ok`.
    """
    q_mid, drop = _pooled_pairs(limbs)
    n_points = int(q_mid.size)
    log_x, log_y = np.log(q_mid), np.log(drop)
    if np.ptp(log_x) == 0.0:
        return BNFit(np.nan, np.nan, np.nan, n_points, False, "all pooled points share one flow value")
    env_x, env_y = lower_envelope(log_x, log_y)
    if env_x.size < _BN_MIN_ENVELOPE_POINTS:
        return BNFit(np.nan, np.nan, np.nan, n_points, False,
                     f"lower envelope has {env_x.size} points (< {_BN_MIN_ENVELOPE_POINTS})")
    b, log_a, r2 = _ols(env_x, env_y)
    if np.isnan(r2):
        return BNFit(np.nan, np.nan, np.nan, n_points, False, "lower envelope has no spread in -dQ")
    return BNFit(b, float(np.exp(log_a)), r2, n_points, n_points >= min_points and r2 >= min_r2)
