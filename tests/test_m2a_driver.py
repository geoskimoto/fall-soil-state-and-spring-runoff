"""M2a: per-basin driver, input validation, qualifiers, metadata (interface: tests/INTERFACE_M2A.md)."""
import json
import math

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

import m2_builders as B
from fallsoil.basins import get_basin
from fallsoil.errors import DataValidationError, UnknownBasinError
from fallsoil.qc import run_qc
from fallsoil.responses import (METRIC_COLUMNS, TABLE_COLUMNS, ResponseParams, compute_basin_responses,
                                compute_responses, write_responses_parquet)

AREA = 62.4241   # Lookout Creek
CENTERS = {2001: (6, 3), 2002: (5, 20), 2003: (6, 15)}


def three_years():
    return pd.concat([B.gaussian_year(w, center=c, sigma_days=15.0, amp=20.0, base=0.5) for w, c in CENTERS.items()])


def frame(s=None, qualifier="A"):
    return B.to_usgs_frame(three_years() if s is None else s, AREA, qualifier)


def same_tables(a, b):
    assert list(a.columns) == list(b.columns) and len(a) == len(b)
    for c in a.columns:
        for x, y in zip(a[c], b[c]):
            if isinstance(x, str) or isinstance(y, str):
                assert x == y, c
            elif pd.isna(x) or pd.isna(y):
                assert pd.isna(x) and pd.isna(y), c
            else:
                assert x == pytest.approx(y, rel=1e-12), c


# ---------------------------------------------------------------- shape of the result
def test_one_row_per_water_year_with_documented_columns_and_dtypes():
    t = compute_responses(frame(), AREA)
    B.check_table_invariants(t)
    assert list(t["wy"]) == [2001, 2002, 2003]
    assert isinstance(t.index, pd.RangeIndex)
    for c in ("wy", "n_days_wy", "n_valid_days_wy", "n_negative_days_wy", "recession_n_segments", "bn_n_points"):
        assert pd.api.types.is_integer_dtype(t[c]), c
    for c in METRIC_COLUMNS + ("frac_provisional",):
        assert pd.api.types.is_float_dtype(t[c]), c
    assert t["bn_fit_ok"].dtype == bool
    assert list(t["n_days_wy"]) == [365, 365, 365]


def test_planted_truth_is_recovered_end_to_end():
    s = three_years()
    t = compute_responses(frame(s), AREA)
    for _, r in t.iterrows():
        wy = int(r["wy"])
        assert r["peak_wy_day"] == B.wy_day_of(B.cal_date(wy, *CENTERS[wy]))
        assert r["peak_mm_d"] == pytest.approx(20.5, rel=1e-6)
        assert r["vol_wy_mm"] == pytest.approx(s[B.wy_dates(wy)].sum(), rel=1e-6)   # cfs round trip
        one = s[B.wy_dates(wy)]
        assert abs(r["com_wy_day"] - B.truth_com_day(one)) <= 1
        assert r["spring_pulse_day"] < r["peak_wy_day"]
    assert t.loc[0, "peak_wy_day"] != t.loc[1, "peak_wy_day"]
    assert t.loc[2, "status"] == "partial" and "next_oct_mean_mm_d" in t.loc[2, "reasons"]  # record ends Sep 30, 2003


def test_input_is_not_mutated_and_order_does_not_matter():
    f = frame()
    shuffled = f.sample(frac=1.0, random_state=5).reset_index(drop=True)
    before = shuffled.copy()
    a, b = compute_responses(f, AREA), compute_responses(shuffled, AREA)
    pd.testing.assert_frame_equal(shuffled, before)
    same_tables(a, b)


def test_extra_columns_are_ignored():
    f = frame()
    f["site_no"] = "14161500"
    same_tables(compute_responses(f, AREA), compute_responses(frame(), AREA))


# ---------------------------------------------------------------- validation
def test_duplicate_dates_raise_data_validation_error():
    f = frame()
    with pytest.raises(DataValidationError, match="(?i)duplicate"):
        compute_responses(pd.concat([f, f.iloc[[100]]], ignore_index=True), AREA)


def test_string_dates_raise_data_validation_error():
    f = frame()
    f["date"] = f["date"].dt.strftime("%Y-%m-%d")
    with pytest.raises(DataValidationError, match="date"):
        compute_responses(f, AREA)


def test_missing_columns_raise_data_validation_error():
    with pytest.raises(DataValidationError, match="flow_cfs"):
        compute_responses(frame().drop(columns="flow_cfs"), AREA)
    with pytest.raises(DataValidationError, match="date"):
        compute_responses(frame().drop(columns="date"), AREA)


def test_non_numeric_flow_raises_data_validation_error():
    f = frame()
    f["flow_cfs"] = f["flow_cfs"].astype(str)
    with pytest.raises(DataValidationError, match="flow_cfs"):
        compute_responses(f, AREA)


def test_empty_frame_raises_data_validation_error():
    with pytest.raises(DataValidationError):
        compute_responses(frame().iloc[0:0], AREA)


@pytest.mark.parametrize("area", [0, 0.0, -5.0, float("nan"), float("inf")])
def test_bad_area_raises_value_error(area):
    with pytest.raises(ValueError):
        compute_responses(frame(), area)


# ---------------------------------------------------------------- record edges
def test_partial_first_and_last_water_years_are_nan_rows_with_reasons():
    idx = pd.date_range("2000-01-01", "2003-12-31")   # WY2000 from Jan 1; WY2004 only Oct-Dec 2003
    s = pd.Series(1.0, index=idx, name="flow_mm_d")
    t = compute_responses(frame(s), AREA)
    B.check_table_invariants(t)
    assert list(t["wy"]) == [2000, 2001, 2002, 2003, 2004]
    assert list(t["status"][[0, 4]]) == ["nan", "nan"]
    assert list(t["n_valid_days_wy"][[0, 4]]) == [274, 92]
    assert t.loc[1:3, "status"].isin(["ok", "partial"]).all()
    assert t.loc[1, "vol_wy_mm"] == pytest.approx(365.0, rel=1e-6)


def test_a_missing_middle_water_year_still_gets_a_row():
    s = pd.concat([B.gaussian_year(2001), B.gaussian_year(2004)])
    t = compute_responses(frame(s), AREA)
    B.check_table_invariants(t)
    assert list(t["wy"]) == [2001, 2002, 2003, 2004]
    assert list(t.loc[[1, 2], "n_valid_days_wy"]) == [0, 0]
    assert list(t.loc[[1, 2], "status"]) == ["nan", "nan"]


def test_sep30_and_oct1_land_in_different_water_years():
    s = pd.concat([B.constant_year(2020, 1.0), B.constant_year(2021, 1.0)])
    s.loc[pd.Timestamp("2020-09-30")] = 100.0
    s.loc[pd.Timestamp("2020-10-01")] = 1000.0
    t = compute_responses(frame(s), AREA).set_index("wy")
    assert t.loc[2020, "vol_wy_mm"] == pytest.approx(465.0, rel=1e-6)
    assert t.loc[2021, "vol_wy_mm"] == pytest.approx(1364.0, rel=1e-6)


def test_negative_flows_are_flagged_by_qc_and_excluded_by_the_metrics():
    s = B.constant_year(2001, 1.0)
    f = frame(s)
    f.loc[10:25, "flow_cfs"] = -3.0   # 16 negative days -> 349 valid -> gate fails (clipping would keep them valid)
    assert run_qc(f, "flow").flags["flow_cfs_out_of_range"].sum() == 16
    t = compute_responses(f, AREA)
    assert t.loc[0, "n_negative_days_wy"] == 16 and t.loc[0, "n_valid_days_wy"] == 349
    assert t.loc[0, "status"] == "nan"
    f15 = frame(s)
    f15.loc[10:24, "flow_cfs"] = -3.0
    t15 = compute_responses(f15, AREA)
    assert t15.loc[0, "status"] != "nan" and t15.loc[0, "vol_wy_mm"] == pytest.approx(350.0, rel=1e-6)


# ---------------------------------------------------------------- qualifiers
def _with_p(n_last, label="P"):
    s = three_years()
    q = np.array(["A"] * len(s), dtype=object)
    wy2 = B.wy_dates(2002)
    q[s.index.get_indexer(wy2[-n_last:])] = label
    return s, q


def test_frac_provisional_is_the_share_of_valid_days_in_the_water_year():
    s, q = _with_p(73)
    t = compute_responses(B.to_usgs_frame(s, AREA, q), AREA).set_index("wy")
    assert t.loc[2002, "frac_provisional"] == pytest.approx(73 / 365, rel=1e-12)
    assert t.loc[2001, "frac_provisional"] == 0.0 and t.loc[2003, "frac_provisional"] == 0.0


@pytest.mark.parametrize("label, counts", [("P", True), ("P e", True), ("A e", False), ("e", False), ("Ice", False), ("", False)])
def test_only_tokens_containing_p_count_as_provisional(label, counts):
    s, q = _with_p(36, label)
    t = compute_responses(B.to_usgs_frame(s, AREA, q), AREA).set_index("wy")
    assert t.loc[2002, "frac_provisional"] == pytest.approx(36 / 365 if counts else 0.0, abs=1e-12)


def test_none_qualifier_is_not_provisional():
    s, q = _with_p(10)
    q[:] = None
    t = compute_responses(B.to_usgs_frame(s, AREA, q), AREA)
    assert (t["frac_provisional"] == 0.0).all()


def test_provisional_and_estimated_values_are_kept_and_change_no_metric():
    s, q = _with_p(100, "P e")
    flagged = compute_responses(B.to_usgs_frame(s, AREA, q), AREA)
    clean = compute_responses(B.to_usgs_frame(s, AREA, "A"), AREA)
    cols = [c for c in clean.columns if c not in ("frac_provisional", "reasons")]
    same_tables(flagged[cols], clean[cols])


def test_invalid_days_do_not_count_toward_frac_provisional():
    s, q = _with_p(73)
    damaged = s.copy()
    damaged.loc[B.wy_dates(2002)[-5:]] = np.nan   # 5 of the 73 provisional days have no value
    t = compute_responses(B.to_usgs_frame(damaged, AREA, q), AREA).set_index("wy")
    assert t.loc[2002, "frac_provisional"] == pytest.approx(68 / 360, rel=1e-12)


def test_missing_qualifier_column_gives_nan_with_reason():
    t = compute_responses(frame().drop(columns="qualifier"), AREA)
    assert t["frac_provisional"].isna().all()
    for r in t.itertuples():
        assert "frac_provisional" in r.reasons
    assert (t["status"] != "ok").all()
    assert t.loc[0, "vol_wy_mm"] > 0   # everything else still computed


# ---------------------------------------------------------------- metadata and parquet
def test_metadata_records_units_parameters_and_policies():
    p = ResponseParams(min_wy_valid_days=340, spring_pulse_start=(1, 1))
    t = compute_responses(frame(), AREA, p)
    md = t.attrs["fallsoil"]
    assert md["source"] == "fallsoil.responses" and md["area_sqkm"] == AREA
    assert md["params"] == json.loads(json.dumps(p.as_dict()))
    assert md["params"]["min_wy_valid_days"] == 340
    assert md["negative_flow_policy"] == "excluded_not_clipped" and md["gap_policy"] == "flagged_not_filled"
    assert md["diagnostic_columns"] == ["days_peak_to_half"]
    assert "local standard time" in md["time_convention"] and "calendar day" in md["time_convention"]
    units = md["units"]
    assert set(units) == set(TABLE_COLUMNS) - {"status", "reasons"}
    for c in TABLE_COLUMNS:
        if c.endswith("_mm_d"):
            assert units[c] == "mm/d", c
        elif c.endswith("_mm"):
            assert units[c] == "mm", c
    for c in ("frac_aprjul", "rb_flashiness", "bfi_wy_eck", "bfi_wy_lh", "frac_provisional"):
        assert units[c] == "1"
    assert units["recession_k_days"] == "d" and units["days_peak_to_half"] == "d"
    assert units["com_wy_day"] == units["peak_wy_day"] == units["spring_pulse_day"] == "day of water year"


def test_no_physical_column_lacks_a_unit_in_its_name():
    unitless_ok = {"wy", "n_days_wy", "n_valid_days_wy", "n_negative_days_wy", "frac_aprjul", "com_wy_day",
                   "spring_pulse_day", "peak_wy_day", "rb_flashiness", "recession_k_days", "recession_n_segments",
                   "bn_exponent_b", "bn_a", "bn_n_points", "bn_r2", "bn_fit_ok", "bfi_wy_eck", "bfi_wy_lh",
                   "days_peak_to_half", "frac_provisional", "status", "reasons"}
    for c in TABLE_COLUMNS:
        assert c in unitless_ok or c.endswith("_mm") or c.endswith("_mm_d"), c


def test_parquet_round_trip_with_schema_metadata(tmp_path):
    t = compute_responses(frame(), AREA)
    out = write_responses_parquet(t, tmp_path / "nested" / "dir" / "responses.parquet")
    assert out.exists() and out.suffix == ".parquet"
    md = json.loads(pq.read_schema(out).metadata[b"fallsoil"])
    assert md["params"] == ResponseParams().as_dict() and md["area_sqkm"] == AREA
    assert md["units"]["vol_wy_mm"] == "mm"
    back = pd.read_parquet(out)
    assert list(back.columns) == list(TABLE_COLUMNS)
    same_tables(back, t)


# ---------------------------------------------------------------- basin entry point
def test_basin_driver_uses_the_registry_area():
    s = three_years()
    f = B.to_usgs_frame(s, get_basin("14161500").drain_sqkm)
    for basin in ("14161500", get_basin("14161500")):
        t = compute_basin_responses(f, basin)
        assert t.loc[0, "vol_wy_mm"] == pytest.approx(s[B.wy_dates(2001)].sum(), rel=1e-6)
        assert t.attrs["fallsoil"]["basin"] == "14161500"
        assert t.attrs["fallsoil"]["area_sqkm"] == pytest.approx(62.4241, rel=1e-9)


def test_basin_driver_reads_a_parquet_path(tmp_path):
    f = frame()
    p = tmp_path / "flow.parquet"
    f.to_parquet(p)
    same_tables(compute_basin_responses(p, "14161500"), compute_basin_responses(f, "14161500"))
    same_tables(compute_basin_responses(str(p), "14161500"), compute_basin_responses(f, "14161500"))


def test_basin_driver_unknown_basin():
    with pytest.raises(UnknownBasinError):
        compute_basin_responses(frame(), "99999999")


def test_basin_driver_missing_file_is_a_friendly_error(tmp_path):
    from fallsoil.errors import FallsoilError
    with pytest.raises((FallsoilError, FileNotFoundError)):
        compute_basin_responses(tmp_path / "nope.parquet", "14161500")
