"""Streamflow unit conversion between volumetric flow (cfs) and catchment depth (mm/day)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

M3_PER_FT3 = 0.0283168
_SECONDS_PER_DAY = 86400.0
_M2_PER_KM2 = 1e6
_MM_PER_M = 1000.0


def _check_area(area_sqkm) -> float:
    try:
        area = float(area_sqkm)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"area_sqkm must be a finite number > 0, got {area_sqkm!r}") from exc
    if not (math.isfinite(area) and area > 0):
        raise ValueError(f"area_sqkm must be a finite number > 0, got {area_sqkm!r}")
    return area


def _cfs_per_mm_d(area_sqkm) -> float:
    """mm/d produced by 1 cfs over `area_sqkm`."""
    area = _check_area(area_sqkm)
    return M3_PER_FT3 * _SECONDS_PER_DAY / (area * _M2_PER_KM2) * _MM_PER_M


def _scale(values, factor: float, divide: bool):
    if isinstance(values, pd.Series):
        return values / factor if divide else values * factor
    arr = np.asarray(values, dtype=float)
    out = arr / factor if divide else arr * factor
    return float(out) if out.ndim == 0 else out


def cfs_to_mm_per_day(flow_cfs, area_sqkm):
    """cfs -> mm/day over `area_sqkm` (km2). Scalar, ndarray or Series (index kept, NaN propagates)."""
    return _scale(flow_cfs, _cfs_per_mm_d(area_sqkm), divide=False)


def mm_per_day_to_cfs(flow_mm_d, area_sqkm):
    """mm/day over `area_sqkm` (km2) -> cfs; the exact inverse of `cfs_to_mm_per_day`."""
    return _scale(flow_mm_d, _cfs_per_mm_d(area_sqkm), divide=True)
