"""Column contract of the per-water-year response table."""
from __future__ import annotations

TABLE_COLUMNS = (
    "wy", "n_days_wy", "n_valid_days_wy", "n_negative_days_wy",
    "vol_wy_mm", "vol_aprjul_mm", "vol_aprsep_mm", "frac_aprjul", "com_wy_day", "spring_pulse_day",
    "peak_mm_d", "peak_wy_day", "rb_flashiness", "recession_k_days", "recession_n_segments",
    "bn_exponent_b", "bn_a", "bn_n_points", "bn_r2", "bn_fit_ok",
    "baseflow_eck_wy_mm", "baseflow_lh_wy_mm", "bfi_wy_eck", "bfi_wy_lh",
    "q7min_mm_d", "augsep_mean_mm_d", "next_oct_mean_mm_d", "days_peak_to_half",
    "frac_provisional", "status", "reasons",
)

INT_COLUMNS = ("wy", "n_days_wy", "n_valid_days_wy", "n_negative_days_wy", "recession_n_segments", "bn_n_points")
BOOL_COLUMNS = ("bn_fit_ok",)
TEXT_COLUMNS = ("status", "reasons")

# Float metrics computed by `wy_metrics` (every float column except frac_provisional, which the driver adds).
METRIC_COLUMNS = tuple(
    c for c in TABLE_COLUMNS
    if c not in INT_COLUMNS + BOOL_COLUMNS + TEXT_COLUMNS + ("frac_provisional",)
)
FLOAT_COLUMNS = METRIC_COLUMNS + ("frac_provisional",)

DIAGNOSTIC_COLUMNS = ("days_peak_to_half",)

_UNIT_OVERRIDES = {
    "wy": "water year",
    "recession_k_days": "d",
    "days_peak_to_half": "d",
    "bn_a": "mm^(1-b) d^(b-2)",
}


def column_units() -> dict:
    """Unit of every column except status/reasons (dimensionless ratios and counts are "1")."""
    units = {}
    for c in TABLE_COLUMNS:
        if c in TEXT_COLUMNS:
            continue
        if c in _UNIT_OVERRIDES:
            units[c] = _UNIT_OVERRIDES[c]
        elif c.endswith("_mm_d"):
            units[c] = "mm/d"
        elif c.endswith("_mm"):
            units[c] = "mm"
        elif c.endswith("_day"):
            units[c] = "day of water year"
        else:
            units[c] = "1"
    return units
