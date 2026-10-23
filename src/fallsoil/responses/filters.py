"""Recursive digital baseflow filters (pure functions on gap-free daily flow)."""
from __future__ import annotations

import numpy as np


def _check_flow(flow) -> np.ndarray:
    q = np.asarray(flow, dtype=float)
    if q.ndim != 1 or q.size == 0:
        raise ValueError("flow must be a non-empty 1-D array")
    if not np.all(np.isfinite(q)) or np.any(q < 0):
        raise ValueError("flow must be finite and >= 0 (a NaN is never skipped; split the series at gaps)")
    return q


def _check_alpha(alpha: float) -> None:
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")


def eckhardt_baseflow(flow, alpha: float = 0.98, bfi_max: float = 0.80) -> np.ndarray:
    """Eckhardt (2005) two-parameter filter.

    b_t = ((1 - bfi_max) alpha b_{t-1} + (1 - alpha) bfi_max q_t) / (1 - alpha bfi_max), capped at q_t,
    started from b_0 = bfi_max q_0. Under constant flow the steady state is bfi_max * q (BFI -> bfi_max, not 1).
    """
    q = _check_flow(flow)
    _check_alpha(alpha)
    if not 0 < bfi_max <= 1:
        raise ValueError(f"bfi_max must be in (0, 1], got {bfi_max}")
    denom = 1.0 - alpha * bfi_max
    out = np.empty_like(q)
    prev = bfi_max * q[0]
    out[0] = prev
    for t in range(1, q.size):
        prev = min(((1.0 - bfi_max) * alpha * prev + (1.0 - alpha) * bfi_max * q[t]) / denom, q[t])
        out[t] = prev
    return out


def _lyne_hollick_pass(x: np.ndarray, alpha: float) -> np.ndarray:
    """One recursive pass: returns x minus the filtered quickflow (quickflow clipped to 0..x_t)."""
    out = np.empty_like(x)
    quick = 0.0
    out[0] = x[0]
    for t in range(1, x.size):
        quick = alpha * quick + 0.5 * (1.0 + alpha) * (x[t] - x[t - 1])
        quick = min(max(quick, 0.0), x[t])
        out[t] = x[t] - quick
    return out


def lyne_hollick_baseflow(flow, alpha: float = 0.925, passes: int = 3) -> np.ndarray:
    """Lyne-Hollick (1979) one-parameter filter, `passes` passes alternating forward, backward, forward, ...

    Each pass filters the previous pass's baseflow. Under constant flow no quickflow is removed (BFI = 1).
    """
    q = _check_flow(flow)
    _check_alpha(alpha)
    if passes < 1:
        raise ValueError(f"passes must be >= 1, got {passes}")
    base = q
    for i in range(passes):
        base = _lyne_hollick_pass(base, alpha) if i % 2 == 0 else _lyne_hollick_pass(base[::-1], alpha)[::-1]
    return np.ascontiguousarray(base, dtype=float)
