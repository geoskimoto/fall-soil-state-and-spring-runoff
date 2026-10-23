"""Opt-in live smoke tests (real network). Run with:  pytest -m live
Small requests only: one small basin, one station, a few days/months."""
import pandas as pd
import pytest

from fallsoil import qc
from fallsoil.fetch.gridmet import fetch_basin_mean
from fallsoil.fetch.snotel import fetch_snotel_daily
from fallsoil.fetch.usgs_flow import fetch_daily_flow

pytestmark = pytest.mark.live


def test_live_usgs_lookout_creek_one_water_year(data_dir):
    df = pd.read_parquet(fetch_daily_flow("14161500", start="2020-10-01", end="2021-09-30"))
    assert len(df) >= 360 and (df["flow_cfs"].dropna() >= 0).all()
    assert qc.run_qc(df, "flow").flags["duplicate_date"].sum() == 0


def test_live_snotel_alpine_meadows_one_month(data_dir):
    df = pd.read_parquet(fetch_snotel_daily("908:WA:SNTL", ("WTEQ", "PREC", "TAVG"),
                                            start="2021-01-01", end="2021-01-31"))
    assert len(df) >= 28 and {"swe_mm", "precip_accum_mm", "temp_avg_c"} <= set(df.columns)
    assert qc.run_qc(df, "snotel").flags["temp_avg_c_out_of_range"].sum() == 0


def test_live_gridmet_lookout_creek_one_month(data_dir_with_basins):
    p = fetch_basin_mean("14161500", "pr", "2021-01-01", "2021-01-31")
    df = pd.read_parquet(p)
    assert len(df) == 31 and (df["precip_mm"] >= 0).all()
    # Jan 2021 is far older than the 60-day provisional window: all permanent
    assert not df["provisional"].any()
