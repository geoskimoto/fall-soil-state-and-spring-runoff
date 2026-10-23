"""M2a integration: fetch.usgs_flow parquet -> responses driver (mocked HTTP only; no live network).

1. A synthetic multi-year hydrograph is served as a real-format NWIS RDB, parsed by the existing fetcher, and the
   resulting parquet is run through the driver: planted truths must come back.
2. The cached real Lookout Creek payload (data/raw/screening/dv_14161500.rdb) is parsed the same way and only
   physically sensible BOUNDS are asserted (never exact values).
"""
import numpy as np
import pandas as pd
import pytest
import responses

import m2_builders as B
from conftest import REPO_ROOT, USGS_URL, make_rdb
from fallsoil import manifest
from fallsoil.fetch.usgs_flow import fetch_daily_flow
from fallsoil.responses import compute_basin_responses, write_responses_parquet

SITE = "14161500"
AREA = 62.4241  # GAGES-II DRAIN_SQKM for Lookout Creek (the registry value is tested in test_m2a_units_basins)
CENTERS = {2001: (6, 3), 2002: (5, 20), 2003: (6, 15), 2004: (5, 1), 2005: (6, 28), 2006: (6, 10)}
REAL_RDB = REPO_ROOT / "data" / "raw" / "screening" / "dv_14161500.rdb"


def serve(mocked, text):
    mocked.add(responses.GET, USGS_URL, body=text.encode(), status=200)


# ---------------------------------------------------------------- synthetic, exact NWIS format
@pytest.fixture
def synthetic(mocked, data_dir):
    s = pd.concat([B.gaussian_year(w, center=c, sigma_days=15.0, amp=20.0, base=0.5) for w, c in CENTERS.items()])
    qual = np.array(["A"] * len(s), dtype=object)
    qual[-30:] = "P"                      # last 30 days of the record are provisional
    qual[100:120] = "A e"                 # some estimated days
    frame = B.to_usgs_frame(s, AREA, qual)
    serve(mocked, make_rdb(SITE, B.to_rdb_rows(frame)))
    path = fetch_daily_flow(SITE)
    return s, path


def test_pipeline_recovers_planted_truth_per_water_year(synthetic):
    s, path = synthetic
    t = compute_basin_responses(path, SITE)
    B.check_table_invariants(t)
    assert list(t["wy"]) == list(CENTERS)
    for _, r in t.iterrows():
        wy = int(r["wy"])
        one = s[B.wy_dates(wy)]
        assert r["peak_wy_day"] == B.wy_day_of(B.cal_date(wy, *CENTERS[wy]))
        assert r["vol_wy_mm"] == pytest.approx(one.sum(), rel=1e-6)
        assert abs(r["com_wy_day"] - B.truth_com_day(one)) <= 1
        assert r["frac_aprjul"] == pytest.approx(B.window(one, wy, (4, 1), (7, 31)).sum() / one.sum(), rel=1e-6)
        assert 0.0 < r["bfi_wy_eck"] <= 1.0 and 0.0 < r["bfi_wy_lh"] <= 1.0
        assert r["status"] != "nan"
    # a later pulse has a later centre of mass and a later spring-pulse onset than an earlier one
    by = t.set_index("wy")
    assert by.loc[2005, "com_wy_day"] > by.loc[2004, "com_wy_day"]
    assert by.loc[2005, "spring_pulse_day"] > by.loc[2004, "spring_pulse_day"]


def test_provisional_share_and_record_end_flow_through(synthetic):
    _, path = synthetic
    t = compute_basin_responses(path, SITE).set_index("wy")
    assert t.loc[2006, "frac_provisional"] == pytest.approx(30 / 365, rel=1e-9)
    assert (t.loc[2001:2005, "frac_provisional"] == 0.0).all()
    assert "next_oct_mean_mm_d" in t.loc[2006, "reasons"]   # no October after the final Sep 30
    assert np.isfinite(t.loc[2005, "next_oct_mean_mm_d"])


def test_driver_output_can_be_written_next_to_the_inputs(synthetic, data_dir):
    _, path = synthetic
    t = compute_basin_responses(path, SITE)
    out = write_responses_parquet(t, data_dir / "processed" / "responses" / f"{SITE}.parquet")
    assert out.is_relative_to(data_dir) and out.is_file()
    assert len(pd.read_parquet(out)) == len(CENTERS)


# ---------------------------------------------------------------- real cached payload (bounds only)
@pytest.fixture(scope="module")
def real_text():
    if not REAL_RDB.is_file():
        pytest.fail(f"cached screening payload is missing: {REAL_RDB}")
    return REAL_RDB.read_text()


@pytest.fixture
def real_table(mocked, data_dir, real_text):
    serve(mocked, real_text)
    path = fetch_daily_flow(SITE)
    return compute_basin_responses(path, SITE), path


def test_real_lookout_creek_water_years_are_physically_sensible(real_table):
    t, _ = real_table
    B.check_table_invariants(t)
    complete = t[(t["wy"] >= 1982) & (t["wy"] <= 2025)]
    assert len(complete) == 44 and (complete["status"] != "nan").all()
    # Bounds are physical, not fitted: Lookout Creek (62 km2, ~2300 mm precipitation) yields roughly 800-3000 mm/yr;
    # the cache holds no year outside 500..3500. Rain-dominated maritime flow puts the 50 percent point of annual flow
    # between mid-Dec and mid-Jun (WY day ~75..260), well before a continental snowmelt basin would.
    assert complete["frac_aprjul"].between(0.0, 1.0, inclusive="neither").all()
    assert complete["vol_wy_mm"].between(500.0, 3500.0).all()
    assert complete["com_wy_day"].between(50, 260).all()
    assert complete["peak_wy_day"].between(B.wy_day_of(pd.Timestamp(2001, 3, 1)), B.wy_day_of(pd.Timestamp(2000, 7, 31))).all()  # leap-year upper bound
    assert (complete["peak_mm_d"] > 0).all()
    assert complete["rb_flashiness"].between(0.0, 2.0, inclusive="neither").all()
    assert complete["bfi_wy_eck"].between(0.0, 1.0).all() and complete["bfi_wy_lh"].between(0.0, 1.0).all()
    assert (complete["baseflow_eck_wy_mm"] <= complete["vol_wy_mm"] + 1e-9).all()
    assert (complete["baseflow_lh_wy_mm"] <= complete["vol_wy_mm"] + 1e-9).all()
    finite_k = complete["recession_k_days"].dropna()
    assert finite_k.between(1.0, 1000.0).all()
    assert complete["augsep_mean_mm_d"].dropna().between(0.0, 10.0).all()
    assert complete["frac_provisional"].between(0.0, 1.0).all()


def test_real_record_edges_and_provisional_tail(real_table):
    t, _ = real_table
    by = t.set_index("wy")
    assert by.index.min() == 1950 and by.index.max() == 2027
    assert by.loc[2027, "status"] == "nan" and by.loc[2027, "n_valid_days_wy"] < 60    # record ends 2026-10-22
    assert by.loc[2026, "frac_provisional"] > 0.0          # recent data are provisional ("P"), kept and measured
    assert by.loc[1990, "frac_provisional"] == 0.0


def test_real_volumes_equal_an_independent_sum_of_the_parquet(real_table):
    t, path = real_table
    raw = pd.read_parquet(path)
    raw = raw[raw["flow_cfs"].notna() & (raw["flow_cfs"] >= 0)]
    wy = raw["date"].dt.year + (raw["date"].dt.month >= 10)
    mm = pd.Series(B.cfs_to_mm_d(raw["flow_cfs"].to_numpy(), AREA))
    expected = mm.groupby(wy.to_numpy()).sum()
    by = t.set_index("wy")
    for y in (1982, 1997, 2001, 2015, 2025):
        assert by.loc[y, "vol_wy_mm"] == pytest.approx(expected.loc[y], rel=1e-9)


def test_pipeline_does_not_touch_the_manifest_or_raw_files(real_table, data_dir):
    before = sorted(p.name for p in (data_dir / "raw").rglob("*") if p.is_file())
    n_records = len(manifest.read_manifest())
    t, path = real_table
    compute_basin_responses(path, SITE)
    assert sorted(p.name for p in (data_dir / "raw").rglob("*") if p.is_file()) == before
    assert len(manifest.read_manifest()) == n_records
