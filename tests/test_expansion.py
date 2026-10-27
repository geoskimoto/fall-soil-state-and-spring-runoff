"""Expansion-basin registry (fallsoil.expansion). Not part of the locked suites (relaxed-testing policy, 2026-10-24)."""
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from fallsoil.basins import Basin, get_basin, list_basins
from fallsoil.errors import ConfigError, UnknownBasinError
from fallsoil.expansion import (
    EXPANSION_POLYGON_FILE, UNCLASSIFIED, load_expansion_basins, record_expansion_polygon_provenance,
)

ROWS = [
    # STAID is stored without its leading zero by pandas when the CSV is read naively; the loader must restore it
    dict(STAID="12010000", STANAME="Alpha Creek", DRAIN_SQKM=120.5, pilot=False, selected=True),
    dict(STAID="14161500", STANAME="Lookout Creek", DRAIN_SQKM=62.4, pilot=True, selected=True),
    dict(STAID="13200000", STANAME="Beta River", DRAIN_SQKM=800.0, pilot=False, selected=False),
    dict(STAID="08000100", STANAME="Gamma Fork", DRAIN_SQKM=300.0, pilot=False, selected=True),
]


@pytest.fixture
def csv_path(tmp_path):
    path = tmp_path / "candidates.csv"
    pd.DataFrame(ROWS).to_csv(path, index=False)
    return path


def test_loads_only_selected_non_pilot_gauges_sorted_by_id(csv_path):
    assert [b.id for b in load_expansion_basins(csv_path)] == ["08000100", "12010000"]


def test_ids_keep_leading_zeros_even_when_csv_has_numbers(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text("STAID,STANAME,DRAIN_SQKM,pilot,selected\n8000100,Gamma Fork,300.0,False,True\n")
    assert [b.id for b in load_expansion_basins(path)] == ["08000100"]


def test_basin_record_fields(csv_path):
    b = {x.id: x for x in load_expansion_basins(csv_path)}["12010000"]
    assert isinstance(b, Basin)
    assert (b.name, b.gauge, b.setting) == ("Alpha Creek", "12010000", UNCLASSIFIED)
    assert b.drain_sqkm == pytest.approx(120.5)
    assert b.polygon_path.name == EXPANSION_POLYGON_FILE


def test_pilot_registry_is_untouched(csv_path):
    load_expansion_basins(csv_path)
    assert len(list_basins()) == 6
    with pytest.raises(UnknownBasinError):
        get_basin("12010000")
    assert get_basin("14161500").drain_sqkm == pytest.approx(62.4241)
    assert get_basin("14161500").polygon_path.name == "pilot_basins_gagesii.gpkg"


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="phase1_count_candidates"):
        load_expansion_basins(tmp_path / "nope.csv")


def test_missing_column_is_a_config_error(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text("STAID,STANAME\n12010000,Alpha\n")
    with pytest.raises(ConfigError, match="DRAIN_SQKM"):
        load_expansion_basins(path)


@pytest.mark.parametrize("area", ["0", "-5", "nan"])
def test_non_positive_or_missing_area_is_rejected(tmp_path, area):
    path = tmp_path / "c.csv"
    path.write_text(f"STAID,STANAME,DRAIN_SQKM,pilot,selected\n12010000,Alpha,{area},False,True\n")
    with pytest.raises(ConfigError, match="12010000"):
        load_expansion_basins(path)


def test_duplicate_ids_are_rejected(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text("STAID,STANAME,DRAIN_SQKM,pilot,selected\n12010000,A,10,False,True\n12010000,B,10,False,True\n")
    with pytest.raises(ConfigError, match="12010000"):
        load_expansion_basins(path)


def test_default_path_is_under_the_data_dir(data_dir):
    with pytest.raises(ConfigError, match=str(data_dir)):
        load_expansion_basins()


def _write_polygons(data_dir):
    dest = data_dir / "raw" / "basins"
    dest.mkdir(parents=True, exist_ok=True)
    gdf = gpd.GeoDataFrame({"site_no": ["12010000", "08000100"]}, geometry=[box(-123, 46, -122.5, 46.5), box(-110, 44, -109.5, 44.5)], crs=4326)
    gdf.to_file(dest / EXPANSION_POLYGON_FILE, driver="GPKG")


def test_polygon_comes_from_the_expansion_file(csv_path, data_dir):
    _write_polygons(data_dir)
    b = {x.id: x for x in load_expansion_basins(csv_path)}["12010000"]
    assert b.polygon().bounds == pytest.approx((-123, 46, -122.5, 46.5))


def test_missing_polygon_row_is_a_config_error(csv_path, data_dir):
    _write_polygons(data_dir)
    gdf = gpd.read_file(data_dir / "raw" / "basins" / EXPANSION_POLYGON_FILE)
    gdf[gdf.site_no == "08000100"].to_file(data_dir / "raw" / "basins" / EXPANSION_POLYGON_FILE, driver="GPKG")
    b = {x.id: x for x in load_expansion_basins(csv_path)}["12010000"]
    with pytest.raises(ConfigError, match="12010000"):
        b.polygon()


def test_provenance_is_recorded_once_per_polygon_and_is_idempotent(csv_path, data_dir):
    _write_polygons(data_dir)
    basins = load_expansion_basins(csv_path)
    assert record_expansion_polygon_provenance(basins) == 2
    assert record_expansion_polygon_provenance(basins) == 0
