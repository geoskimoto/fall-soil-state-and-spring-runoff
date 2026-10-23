"""Per-basin driver: USGS daily-flow frame (cfs) -> one row of response metrics per water year."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa

from fallsoil import basins as basins_mod
from fallsoil.basins import Basin
from fallsoil.errors import DataValidationError
from fallsoil.fetch._common import write_parquet
from fallsoil.responses.columns import (BOOL_COLUMNS, DIAGNOSTIC_COLUMNS, FLOAT_COLUMNS, INT_COLUMNS,
                                        TABLE_COLUMNS, column_units)
from fallsoil.responses.metrics import compute_wy_metrics
from fallsoil.responses.params import ResponseParams
from fallsoil.responses.units import _check_area, cfs_to_mm_per_day
from fallsoil.water_year_calculator import water_year

_PROVISIONAL_TOKEN = r"(?:^|\s)P(?:\s|$)"
_TIME_CONVENTION = ("USGS daily mean values; each value belongs to its calendar day in the gauge's local standard "
                    "time (no time-zone conversion applied). Water year = Oct 1 - Sep 30, labelled by its end year.")


def _validate_frame(flow: pd.DataFrame) -> None:
    if not isinstance(flow, pd.DataFrame):
        raise DataValidationError("flow must be a DataFrame with columns 'date' and 'flow_cfs'.")
    for col in ("date", "flow_cfs"):
        if col not in flow.columns:
            raise DataValidationError(f"flow is missing the required column '{col}'.")
    if flow.empty:
        raise DataValidationError("flow is empty.")
    dates = flow["date"]
    if not pd.api.types.is_datetime64_any_dtype(dates):
        raise DataValidationError(f"flow column 'date' must be datetime64, got {dates.dtype}.")
    if dates.dt.tz is not None or dates.isna().any():
        raise DataValidationError("flow column 'date' must be naive and without missing values.")
    if not pd.api.types.is_numeric_dtype(flow["flow_cfs"]) or pd.api.types.is_bool_dtype(flow["flow_cfs"]):
        raise DataValidationError(f"flow column 'flow_cfs' must be numeric, got {flow['flow_cfs'].dtype}.")
    if dates.duplicated().any():
        raise DataValidationError("flow has duplicate dates; one daily value per date is required.")


def _provisional_fractions(dates: pd.Series, valid: np.ndarray, qualifier: pd.Series) -> pd.Series:
    """Per water year: valid days whose qualifier tokens include "P" / valid days (indexed by wy)."""
    provisional = qualifier.fillna("").astype(str).str.contains(_PROVISIONAL_TOKEN, regex=True).to_numpy()
    counts = pd.DataFrame({"wy": water_year(dates).to_numpy(dtype=int), "valid": valid,
                           "provisional": valid & provisional}).groupby("wy").sum()
    return counts["provisional"] / counts["valid"].where(counts["valid"] > 0)


def _add_provisional(row: dict, fractions: pd.Series | None) -> dict:
    """Attach frac_provisional; without a qualifier column it is NaN with a reason (and the row is not "ok")."""
    row["frac_provisional"] = _frac_for(row["wy"], fractions)
    if fractions is None:
        row["reasons"] = "; ".join(filter(None, [row["reasons"], "frac_provisional: no 'qualifier' column in the input"]))
        if row["status"] == "ok":
            row["status"] = "partial"
    return row


def _frac_for(wy: int, fractions: pd.Series | None) -> float:
    if fractions is None or wy not in fractions.index:
        return float("nan")
    return float(fractions.loc[wy])


def _assemble(rows: list[dict]) -> pd.DataFrame:
    table = pd.DataFrame(rows, columns=list(TABLE_COLUMNS))
    dtypes = {**{c: "int64" for c in INT_COLUMNS}, **{c: bool for c in BOOL_COLUMNS},
              **{c: "float64" for c in FLOAT_COLUMNS}}
    return table.astype(dtypes).reset_index(drop=True)


def compute_responses(flow: pd.DataFrame, area_sqkm: float, params: ResponseParams | None = None) -> pd.DataFrame:
    """Response-metric table (one row per water year, first to last date inclusive) from a daily-flow frame.

    `flow` has `date` (datetime64) and `flow_cfs`, plus an optional `qualifier` (the `fetch.usgs_flow` schema).
    Flow is converted to mm/d once; provisional/estimated values are kept. The input is never modified.
    """
    params = params or ResponseParams()
    area = _check_area(area_sqkm)
    _validate_frame(flow)
    cols = ["date", "flow_cfs"] + (["qualifier"] if "qualifier" in flow.columns else [])
    df = flow[cols].sort_values("date").reset_index(drop=True)
    flow_mm_d = pd.Series(cfs_to_mm_per_day(df["flow_cfs"].to_numpy(dtype=float), area),
                          index=pd.DatetimeIndex(df["date"]), name="flow_mm_d")
    if (df["date"] != df["date"].dt.normalize()).any():
        raise DataValidationError("flow column 'date' must hold whole calendar days (no time of day).")
    valid = np.isfinite(flow_mm_d.to_numpy()) & (flow_mm_d.to_numpy() >= 0)
    fractions = _provisional_fractions(df["date"], valid, df["qualifier"]) if "qualifier" in df else None

    rows = []
    for wy in range(water_year(flow_mm_d.index[0]), water_year(flow_mm_d.index[-1]) + 1):
        window = flow_mm_d.loc[pd.Timestamp(year=wy - 1, month=10, day=1):pd.Timestamp(year=wy, month=10, day=31)]
        rows.append(_add_provisional(compute_wy_metrics(window, wy, params), fractions))
    table = _assemble(rows)
    table.attrs["fallsoil"] = {
        "source": "fallsoil.responses",
        "area_sqkm": area,
        "params": params.as_dict(),
        "units": column_units(),
        "time_convention": _TIME_CONVENTION,
        "negative_flow_policy": "excluded_not_clipped",
        "gap_policy": "flagged_not_filled",
        "diagnostic_columns": list(DIAGNOSTIC_COLUMNS),
    }
    return table


def _read_flow(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise DataValidationError(f"Flow file not found: {path}. Fetch it first with fallsoil.fetch.usgs_flow.")
    try:
        return pd.read_parquet(path)
    except (OSError, pa.ArrowException) as exc:
        raise DataValidationError(f"Could not read flow parquet {path}: {exc}") from exc


def compute_basin_responses(flow, basin: Basin | str, params: ResponseParams | None = None) -> pd.DataFrame:
    """`compute_responses` for a pilot basin; the area is the registry `Basin.drain_sqkm`.

    `flow` is a frame or the path of a `fetch.usgs_flow` parquet; `basin` a `Basin` or gauge id
    (`UnknownBasinError` if it is not a pilot basin).
    """
    basin = basin if isinstance(basin, Basin) else basins_mod.get_basin(basin)
    frame = _read_flow(Path(flow)) if isinstance(flow, (str, Path)) else flow
    table = compute_responses(frame, basin.drain_sqkm, params)
    table.attrs["fallsoil"]["basin"] = basin.id
    return table


def write_responses_parquet(table: pd.DataFrame, path) -> Path:
    """Write the table with its `table.attrs["fallsoil"]` metadata under the `fallsoil` schema key."""
    return write_parquet(table, Path(path), table.attrs.get("fallsoil", {}))
