"""Quality control at pipeline entry: validate structure, flag problems, report gaps.

QC never alters, drops or clips values. Bad values are kept and flagged; gaps are reported and
logged, not filled.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from fallsoil.errors import DataValidationError

log = logging.getLogger(__name__)

DATASETS = ("flow", "snotel", "gridmet")
_FIXED_COLUMNS = {
    "flow": ("flow_cfs",),
    "snotel": ("swe_mm", "precip_accum_mm", "temp_avg_c"),
    "gridmet": ("precip_mm", "temp_max_c", "temp_min_c"),
}
_SOIL_PREFIX = "soil_moisture_pct_"
TEMP_RANGE_C = (-60.0, 50.0)
PCT_RANGE = (0.0, 100.0)


@dataclass(frozen=True)
class QCResult:
    flags: pd.DataFrame  # bool flags, same index as the input
    gaps: pd.DataFrame  # columns: start, end, n_days

    @property
    def n_flagged(self) -> int:
        """Number of rows with at least one flag."""
        return int(self.flags.any(axis=1).sum()) if len(self.flags.columns) else 0

    @property
    def ok(self) -> bool:
        return self.n_flagged == 0 and len(self.gaps) == 0


def _value_columns(df: pd.DataFrame, dataset: str) -> list[str]:
    cols = [c for c in _FIXED_COLUMNS[dataset] if c in df.columns]
    if dataset == "snotel":
        cols += [c for c in df.columns if c.startswith(_SOIL_PREFIX)]
    if not cols:
        raise DataValidationError(
            f"No {dataset} value columns found; expected at least one of "
            f"{', '.join(_FIXED_COLUMNS[dataset])}"
            + (f" or {_SOIL_PREFIX}*" if dataset == "snotel" else "")
            + "."
        )
    return cols


def _out_of_range(col: str, v: pd.Series) -> pd.Series:
    if col.startswith(_SOIL_PREFIX):
        lo, hi = PCT_RANGE
        bad = (v < lo) | (v > hi)
    elif col.startswith("temp_"):
        lo, hi = TEMP_RANGE_C
        bad = (v < lo) | (v > hi)
    else:  # flow_cfs, swe_mm, precip_accum_mm, precip_mm: non-negative
        bad = v < 0
    return (bad | np.isinf(v)).astype(bool)


def _gap_report(df: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    empty = pd.DataFrame({"start": pd.Series(dtype="datetime64[ns]"),
                          "end": pd.Series(dtype="datetime64[ns]"),
                          "n_days": pd.Series(dtype="int64")})
    dates = df["date"]
    if dates.notna().sum() == 0:
        return empty
    day = dates.dt.normalize()
    has_data = df[value_cols].notna().any(axis=1) & day.notna()
    days_with_data = set(day[has_data])
    full = pd.date_range(day.min(), day.max(), freq="D")
    missing = np.array([d not in days_with_data for d in full])
    runs, start = [], None
    for i, is_missing in enumerate(missing):
        if is_missing and start is None:
            start = i
        elif not is_missing and start is not None:
            runs.append((full[start], full[i - 1], i - start))
            start = None
    if start is not None:
        runs.append((full[start], full[-1], len(full) - start))
    if not runs:
        return empty
    return pd.DataFrame(runs, columns=["start", "end", "n_days"]).astype({"n_days": "int64"})


def run_qc(df: pd.DataFrame, dataset: str) -> QCResult:
    """Validate `df` and return flags and a gap report. `df` is never modified."""
    if dataset not in DATASETS:
        raise ValueError(f"unknown dataset {dataset!r}; expected one of {', '.join(DATASETS)}")
    if "date" not in df.columns:
        raise DataValidationError("Missing required column 'date'.")
    if not pd.api.types.is_datetime64_any_dtype(df["date"]):
        raise DataValidationError(f"Column 'date' must hold datetimes, found dtype {df['date'].dtype}.")
    if len(df) == 0:
        raise DataValidationError(f"The {dataset} data frame is empty; there is nothing to check.")

    value_cols = _value_columns(df, dataset)
    for col in value_cols:
        s = df[col]
        if not pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
            raise DataValidationError(f"Column '{col}' must be numeric, found dtype {s.dtype}.")

    flags = pd.DataFrame(index=df.index)
    for col in value_cols:
        v = df[col].astype("float64")
        flags[f"{col}_missing"] = v.isna().astype(bool)
        flags[f"{col}_out_of_range"] = _out_of_range(col, v)
    flags["duplicate_date"] = df["date"].duplicated(keep=False).astype(bool)
    flags["non_monotonic_date"] = (df["date"].diff() < pd.Timedelta(0)).astype(bool)

    gaps = _gap_report(df, value_cols)
    result = QCResult(flags=flags, gaps=gaps)

    if len(gaps):
        log.warning("%s QC: %d gap(s) totalling %d day(s) with no usable data (first: %s to %s)",
                    dataset, len(gaps), int(gaps["n_days"].sum()),
                    gaps["start"].iloc[0].date(), gaps["end"].iloc[0].date())
    if result.n_flagged:
        log.warning("%s QC: %d of %d rows carry at least one flag (values kept as-is)",
                    dataset, result.n_flagged, len(df))
    return result
