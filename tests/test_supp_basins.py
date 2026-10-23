"""Supplementary basins tests: immutability, CRS, 2-D WKB checksum, provenance records, missing rows."""
import dataclasses
import hashlib
import re
import shutil
from pathlib import Path

import geopandas as gpd
import pytest
import shapely

from conftest import REAL_GPKG
from fallsoil import manifest
from fallsoil.basins import Basin, get_basin, list_basins, record_polygon_provenance
from fallsoil.errors import FallsoilError, UnknownBasinError

IDS = ["14161500", "14159200", "12452800", "12457000", "13336500", "13331500"]


def real_gdf():
    return gpd.read_file(REAL_GPKG, engine="pyogrio")


def install_gpkg(data_dir, gdf):
    dest = data_dir / "raw" / "basins"
    dest.mkdir(parents=True, exist_ok=True)
    gdf.to_file(dest / REAL_GPKG.name, driver="GPKG", engine="pyogrio")
    return dest / REAL_GPKG.name


# ---------------------------------------------------------------- immutability / registry
def test_basin_is_frozen():
    b = list_basins()[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.name = "other"
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.id = "1"
    assert dataclasses.is_dataclass(b)


def test_basin_is_hashable_and_equal_by_value():
    a, b = get_basin("14161500"), get_basin("14161500")
    assert a == b and hash(a) == hash(b) and len({a, b}) == 1
    assert len(set(list_basins())) == 6


def test_registry_order_and_settings_match_spec():
    got = [(b.id, b.setting) for b in list_basins()]
    assert got == [("14161500", "maritime"), ("14159200", "maritime"), ("12452800", "transition"),
                   ("12457000", "transition"), ("13336500", "continental"), ("13331500", "continental")]
    assert all(b.id == b.gauge for b in list_basins())


@pytest.mark.parametrize("bid, key", [("14161500", "Lookout"), ("14159200", "McKenzie"), ("12452800", "Entiat"),
                                      ("12457000", "Wenatchee"), ("13336500", "Selway"), ("13331500", "Minam")])
def test_names_identify_the_river(bid, key):
    assert key in get_basin(bid).name


def test_unknown_basin_message_lists_every_valid_id_comma_separated():
    with pytest.raises(UnknownBasinError) as ei:
        get_basin("nope")
    msg = str(ei.value)
    assert all(i in msg for i in IDS)
    assert re.search(r"\d{8}, \d{8}", msg)
    assert "nope" in msg


# ---------------------------------------------------------------- polygon CRS and geometry
def test_polygon_is_in_degrees_inside_the_pacific_northwest(data_dir_with_basins):
    for b in list_basins():
        minx, miny, maxx, maxy = b.polygon().bounds
        assert -125.0 < minx < maxx < -113.0 and 42.0 < miny < maxy < 49.5, b.id
        assert b.polygon().is_valid


def test_polygon_matches_geometry_stored_in_the_file(data_dir_with_basins):
    gdf = real_gdf()
    for b in list_basins():
        stored = gdf[gdf["site_no"] == b.id].geometry.iloc[0]
        assert b.polygon().equals_exact(stored, 1e-9)


def test_projected_source_is_reprojected_to_epsg4326(data_dir):
    gdf = real_gdf()
    original = gdf[gdf["site_no"] == "14161500"].geometry.iloc[0]
    install_gpkg(data_dir, gdf.to_crs(5070))
    poly = get_basin("14161500").polygon()
    minx, miny, maxx, maxy = poly.bounds
    assert -125 < minx < -121 and 43 < miny < 45  # degrees, not metres
    assert poly.symmetric_difference(original).area < 1e-6 * original.area + 1e-9


def test_polygon_selects_the_requested_row_only(data_dir_with_basins):
    a, b = get_basin("14161500").polygon(), get_basin("13336500").polygon()
    assert not a.equals(b) and a.area < b.area


# ---------------------------------------------------------------- checksum uses 2-D WKB
def test_sha256_matches_independent_2d_wkb_derivation(data_dir_with_basins):
    gdf = real_gdf()
    for b in list_basins():
        geom = gdf[gdf["site_no"] == b.id].geometry.iloc[0]
        expected = hashlib.sha256(shapely.to_wkb(shapely.force_2d(geom), byte_order=1)).hexdigest()
        assert b.polygon_sha256() == expected


def test_sha256_ignores_z_coordinates_in_the_source_file(data_dir):
    gdf = real_gdf()
    row = gdf[gdf["site_no"] == "14161500"].copy()
    flat = row.geometry.iloc[0]
    row["geometry"] = [shapely.force_3d(flat, z=123.0)]
    install_gpkg(data_dir, row)
    b = get_basin("14161500")
    expected = hashlib.sha256(shapely.to_wkb(shapely.force_2d(flat), byte_order=1)).hexdigest()
    assert b.polygon_sha256() == expected
    assert b.polygon_wkb() == shapely.to_wkb(shapely.force_2d(flat), byte_order=1)


# ---------------------------------------------------------------- provenance records
def test_provenance_records_have_expected_shape(data_dir_with_basins):
    assert record_polygon_provenance() == 6
    recs = manifest.read_manifest()
    assert len(recs) == 6
    by_site = {r["params"]["site_no"]: r for r in recs}
    assert set(by_site) == set(IDS)
    for bid, r in by_site.items():
        b = get_basin(bid)
        assert r["source"] == "gagesii_polygon"
        assert r["params"] == {"site_no": bid}
        assert r["rows"] == 1
        assert r["url"].startswith("file://") and r["url"].endswith("pilot_basins_gagesii.gpkg")
        assert r["sha256"] == hashlib.sha256(b.polygon_wkb()).hexdigest() == b.polygon_sha256()


def test_provenance_url_is_a_valid_file_uri_pointing_at_the_geopackage(data_dir_with_basins):
    record_polygon_provenance()
    from urllib.parse import unquote, urlparse
    for r in manifest.read_manifest():
        path = Path(unquote(urlparse(r["url"]).path))
        assert path == data_dir_with_basins / "raw" / "basins" / "pilot_basins_gagesii.gpkg"
        assert path.is_file()


def test_provenance_is_idempotent(data_dir_with_basins):
    assert record_polygon_provenance() == 6
    assert record_polygon_provenance() == 0
    assert len(manifest.read_manifest()) == 6


def test_provenance_appends_only_changed_polygons_after_file_update(data_dir_with_basins):
    record_polygon_provenance()
    gdf = real_gdf()
    mask = gdf["site_no"] == "14161500"
    gdf.loc[mask, "geometry"] = gdf.loc[mask, "geometry"].translate(0.001, 0.0)
    install_gpkg(data_dir_with_basins, gdf)
    assert record_polygon_provenance() == 1
    assert len(manifest.read_manifest()) == 7


# ---------------------------------------------------------------- missing rows / files
def test_basin_missing_from_geopackage_raises_fallsoil_error_naming_it(data_dir):
    gdf = real_gdf()
    install_gpkg(data_dir, gdf[gdf["site_no"] != "14161500"])
    with pytest.raises(FallsoilError) as ei:
        get_basin("14161500").polygon()
    assert "14161500" in str(ei.value)
    assert get_basin("14159200").polygon() is not None  # others still fine


def test_missing_geopackage_raises_fallsoil_error_naming_path(data_dir):
    with pytest.raises(FallsoilError) as ei:
        get_basin("14161500").polygon()
    assert "pilot_basins_gagesii.gpkg" in str(ei.value)


def test_provenance_with_missing_row_fails_without_partial_silent_success(data_dir):
    gdf = real_gdf()
    install_gpkg(data_dir, gdf[gdf["site_no"] != "13331500"])
    with pytest.raises(FallsoilError) as ei:
        record_polygon_provenance()
    assert "13331500" in str(ei.value)
