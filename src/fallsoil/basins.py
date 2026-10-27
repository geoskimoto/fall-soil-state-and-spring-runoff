"""Registry of the pilot headwater basins and access to their GAGES-II polygons."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import shapely

from fallsoil import config, manifest
from fallsoil.errors import ConfigError, UnknownBasinError

POLYGON_FILE = "pilot_basins_gagesii.gpkg"
POLYGON_SOURCE = "gagesii_polygon"


@dataclass(frozen=True)
class Basin:
    """A pilot basin. `id` is the USGS gauge number (`gauge`)."""

    id: str
    name: str
    setting: str  # maritime | transition | continental (expansion basins: unclassified until classified from data)
    gauge: str
    polygon_file: str = POLYGON_FILE
    area_sqkm: float | None = None  # set for expansion basins; the pilots use the _DRAIN_SQKM constants

    @property
    def drain_sqkm(self) -> float:
        """GAGES-II DRAIN_SQKM (drainage area, km2) of the gauge. A constant: no file access."""
        return self.area_sqkm if self.area_sqkm is not None else _DRAIN_SQKM[self.id]

    @property
    def polygon_path(self) -> Path:
        return config.get_data_dir() / "raw" / "basins" / self.polygon_file

    def polygon(self):
        """Basin polygon in EPSG:4326 (Polygon or MultiPolygon)."""
        path = self.polygon_path
        if not path.is_file():
            script = "extract_pilot_basins.py" if self.polygon_file == POLYGON_FILE else "extract_expansion_basins.py"
            raise ConfigError(
                f"Basin polygons not found at {path}. Run scripts/{script} to create "
                f"{self.polygon_file} from the GAGES-II boundary shapefiles."
            )
        gdf = gpd.read_file(path, engine="pyogrio")
        rows = gdf[gdf["site_no"] == self.id]
        if rows.empty:
            raise ConfigError(f"Basin {self.id} ({self.name}) is missing from {path}.")
        return rows.to_crs(4326).geometry.iloc[0]

    def polygon_wkb(self) -> bytes:
        return shapely.to_wkb(self.polygon(), output_dimension=2, byte_order=1)

    def polygon_sha256(self) -> str:
        return manifest.sha256_bytes(self.polygon_wkb())


_BASINS = (
    Basin("14161500", "Lookout Creek near Blue River, OR", "maritime", "14161500"),
    Basin("14159200", "South Fork McKenzie River above Cougar Lake, OR", "maritime", "14159200"),
    Basin("12452800", "Entiat River near Ardenvoir, WA", "transition", "12452800"),
    Basin("12457000", "Wenatchee River at Plain, WA", "transition", "12457000"),
    Basin("13336500", "Selway River near Lowell, ID", "continental", "13336500"),
    Basin("13331500", "Minam River at Minam, OR", "continental", "13331500"),
)


# GAGES-II DRAIN_SQKM (km2), keyed by gauge id.
_DRAIN_SQKM = {
    "14161500": 62.4241,
    "14159200": 414.2997,
    "12452800": 526.3533,
    "12457000": 1545.47,
    "13336500": 4959.467,
    "13331500": 618.9291,
}


def list_basins() -> list[Basin]:
    return list(_BASINS)


def get_basin(basin_id) -> Basin:
    for basin in _BASINS:
        if basin.id == basin_id:
            return basin
    valid = ", ".join(b.id for b in _BASINS)
    raise UnknownBasinError(f"Unknown basin {basin_id!r}. Valid basin ids: {valid}.")


def record_polygon_provenance() -> int:
    """Record each pilot polygon's checksum in the manifest; returns the number newly appended."""
    appended = 0
    for basin in _BASINS:
        wkb = basin.polygon_wkb()
        if manifest.record_fetch(
            source=POLYGON_SOURCE,
            url=basin.polygon_path.as_uri(),
            params={"site_no": basin.id},
            content=wkb,
            rows=1,
        ):
            appended += 1
    return appended
