# step2_grid.py — H3 hexagonal grid generation (H3 v4 API)

import geopandas as gpd
import h3
import shapely


def _hex_to_polygon(h3_index):
    """Convert an H3 cell to a Shapely Polygon (lng, lat order for WGS84)."""
    # cell_to_boundary returns [(lat, lng), ...] — swap to (lng, lat) for Shapely
    boundary = h3.cell_to_boundary(h3_index)
    return shapely.geometry.Polygon([(lng, lat) for lat, lng in boundary])


def _fill_polygon(polygon, level):
    """Return a list of H3 cell IDs covering a single Shapely Polygon."""
    poly_geojson = gpd.GeoSeries([polygon]).__geo_interface__
    poly_geojson = poly_geojson["features"][0]["geometry"]
    return h3.geo_to_cells(poly_geojson, level)


def gridify_city(city_gdf, level=9):
    """
    Creates H3 hexagonal grid for a city.

    Parameters
    ----------
    city_gdf : GeoDataFrame row (pd.Series) with a .geometry attribute
    level : int  — H3 resolution (default 9 ≈ 160 m diameter)

    Returns
    -------
    GeoDataFrame with columns h3_id, geometry (polygons, EPSG:4326)
    """
    city_geom = city_gdf.geometry
    rows = []

    if city_geom.geom_type == "Polygon":
        polygons = [city_geom]
    else:
        # MultiPolygon or GeometryCollection: use .geoms (Shapely 2.x)
        polygons = [part for part in city_geom.geoms if part.geom_type == "Polygon"]
        # also handle nested MultiPolygons inside a GeometryCollection
        for part in city_geom.geoms:
            if part.geom_type == "MultiPolygon":
                polygons.extend(list(part.geoms))

    seen = set()
    for polygon in polygons:
        for h3_id in _fill_polygon(polygon, level):
            if h3_id not in seen:
                seen.add(h3_id)
                rows.append({"h3_id": h3_id, "geometry": _hex_to_polygon(h3_id)})

    h3_hexagons = gpd.GeoDataFrame(rows, columns=["h3_id", "geometry"])
    h3_hexagons = h3_hexagons.set_geometry("geometry").set_crs("EPSG:4326")
    return h3_hexagons
