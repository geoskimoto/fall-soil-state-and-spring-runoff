"""basins: registry of the 6 pilot basins; polygons from the GAGES-II geopackage."""
import hashlib

import geopandas as gpd
import shapely
import pytest

from fallsoil import manifest
from fallsoil.basins import Basin, get_basin, list_basins, record_polygon_provenance
from fallsoil.errors import FallsoilError, UnknownBasinError

PILOT = {
    "14161500": ("maritime", 62.4),
    "14159200": ("maritime", 414.3),
    "12452800": ("transition", 526.4),
    "12457000": ("transition", 1545.5),
    "13336500": ("continental", 4959.5),
    "13331500": ("continental", 618.9),
}


def test_registry_has_exactly_the_six_pilot_basins():
    assert {b.id for b in list_basins()} == set(PILOT)
    assert len(list_basins()) == 6


def test_alternate_not_in_registry_is_unknown():
    # MF Salmon (13309220) is in the geopackage but is NOT a pilot basin.
    with pytest.raises(UnknownBasinError):
        get_basin("13309220")


@pytest.mark.parametrize("bid", sorted(PILOT))
def test_lookup_by_id_returns_complete_record(bid, data_dir_with_basins):
    b = get_basin(bid)
    assert isinstance(b, Basin)
    assert b.id == bid and b.gauge == bid
    assert b.name and isinstance(b.name, str)
    assert b.setting == PILOT[bid][0]
    assert b.polygon_path.exists()


def test_ids_are_unique_and_names_nonempty():
    ids = [b.id for b in list_basins()]
    assert len(ids) == len(set(ids))
    assert all(b.name.strip() for b in list_basins())


@pytest.mark.parametrize("bad", ["nope", "", "99999999", None, 14161500])
def test_unknown_id_raises_friendly_error(bad):
    with pytest.raises(UnknownBasinError) as ei:
        get_basin(bad)
    assert isinstance(ei.value, FallsoilError)
    assert str(ei.value)  # friendly, non-empty


def test_unknown_id_error_lists_valid_ids():
    with pytest.raises(UnknownBasinError) as ei:
        get_basin("nope")
    assert "14161500" in str(ei.value)


@pytest.mark.parametrize("bid", sorted(PILOT))
def test_polygon_is_valid_wgs84_with_expected_area(bid, data_dir_with_basins):
    poly = get_basin(bid).polygon()
    assert poly.is_valid and not poly.is_empty
    assert poly.geom_type in {"Polygon", "MultiPolygon"}
    minx, miny, maxx, maxy = poly.bounds
    assert -125 < minx < maxx < -113 and 43 < miny < maxy < 49  # Pacific Northwest, lon/lat degrees
    area_km2 = gpd.GeoSeries([poly], crs=4326).to_crs(5070).area.iloc[0] / 1e6
    assert area_km2 == pytest.approx(PILOT[bid][1], rel=0.03)


def test_lookout_creek_is_smaller_than_a_few_gridmet_cells(data_dir_with_basins):
    # Plan: Lookout Cr. ~62 km2 spans only ~4 cells of ~4 km; guards the area-weighting rationale.
    minx, miny, maxx, maxy = get_basin("14161500").polygon().bounds
    assert (maxx - minx) / (1 / 24) < 6 and (maxy - miny) / (1 / 24) < 6


def test_polygon_missing_file_raises_friendly_error(data_dir):
    with pytest.raises(FallsoilError) as ei:
        get_basin("14161500").polygon()
    assert "pilot_basins_gagesii" in str(ei.value) or "basin" in str(ei.value).lower()


def expected_polygon_sha(bid):
    """Independent derivation: sha256 of the 2-D little-endian WKB of the geopackage polygon."""
    gdf = gpd.read_file(next((get_basin(bid).polygon_path,)), engine="pyogrio")
    geom = gdf.loc[gdf["site_no"] == bid, "geometry"].iloc[0]
    return hashlib.sha256(shapely.to_wkb(geom, output_dimension=2, byte_order=1)).hexdigest()


@pytest.mark.parametrize("bid", sorted(PILOT))
def test_polygon_sha256_matches_geopackage_derived_value(bid, data_dir_with_basins):
    b = get_basin(bid)
    assert b.polygon_sha256() == expected_polygon_sha(bid)
    assert len(b.polygon_sha256()) == 64


def test_polygon_checksums_are_distinct_per_basin(data_dir_with_basins):
    assert len({b.polygon_sha256() for b in list_basins()}) == 6


def test_polygon_checksum_recorded_in_manifest_idempotently(data_dir_with_basins):
    assert record_polygon_provenance() == 6
    recs = [r for r in manifest.read_manifest() if r["source"] == "gagesii_polygon"]
    assert len(recs) == 6
    by_site = {r["params"]["site_no"]: r["sha256"] for r in recs}
    assert by_site == {b.id: expected_polygon_sha(b.id) for b in list_basins()}
    assert record_polygon_provenance() == 0          # nothing new appended
    assert len(manifest.read_manifest()) == 6
