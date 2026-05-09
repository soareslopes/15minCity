# step3_osm_city.py
# OSMnx-based walking network and POI extraction.

import time
import warnings

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
from shapely.geometry import box

warnings.filterwarnings("ignore")

# Overpass endpoints — tried in order if the previous one fails
_OVERPASS_ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]


def _set_overpass_endpoint(index=0):
    """Set OSMnx Overpass endpoint — handles attribute name across versions."""
    url = _OVERPASS_ENDPOINTS[index % len(_OVERPASS_ENDPOINTS)]
    for attr in ("overpass_endpoint", "overpass_url"):
        if hasattr(ox.settings, attr):
            setattr(ox.settings, attr, url)
            return
    print("  WARNING: could not set Overpass endpoint (unknown attribute name)")


# ---------------------------------------------------------------------------
# Walking network
# ---------------------------------------------------------------------------


def _get_network_from_pbf(pbf_path, polygon, config=None):
    """Extract walking network from a local PBF file using pyrosm."""
    try:
        import pyrosm
    except ImportError:
        raise ImportError("pyrosm is required for PBF-based network extraction.")

    buffer_m = config["osm"]["bbox_buffer_m"] if config else 1_000
    buffered = (
        gpd.GeoDataFrame([1], geometry=[polygon], crs="EPSG:4326")
        .to_crs("EPSG:3857")
    )
    buffered.geometry = buffered.buffer(buffer_m)
    poly = buffered.to_crs("EPSG:4326").geometry.iloc[0]

    bbox = list(poly.bounds)  # [west, south, east, north]
    osm = pyrosm.OSM(pbf_path, bounding_box=bbox)

    print("  Extracting walking network from PBF (pyrosm)…")
    nodes, edges = osm.get_network(network_type="walking", nodes=True)

    if nodes is None or edges is None or len(nodes) == 0 or len(edges) == 0:
        raise ValueError("No walking network found in PBF for this area.")

    # Use pyrosm's own graph builder to avoid boolean coercion issues
    G = osm.to_graph(nodes, edges, graph_type="networkx", retain_all=False)
    if G is None or G.number_of_nodes() == 0:
        raise ValueError("pyrosm.to_graph returned an empty graph.")

    # Ensure 'length' in metres on every edge
    for u, v, data in G.edges(data=True):
        if not data.get('length'):
            data['length'] = 1.0

    G = ox.truncate.largest_component(G, strongly=True)
    print(f"  Network ready (PBF): {len(G.nodes)} nodes, {len(G.edges)} edges")
    return G


def get_walking_network(polygon, config=None, pbf_path=None):
    """
    Download and return a NetworkX walking graph for a city polygon.
    If pbf_path is provided, extracts from local PBF (no internet).

    Parameters
    ----------
    polygon  : Shapely geometry (WGS84)
    config   : dict, optional — reads osm.bbox_buffer_m (default 1000 m)
    pbf_path : str or None — path to a local .osm.pbf file

    Returns
    -------
    G : nx.MultiDiGraph — edges carry 'length' in metres
    """
    if pbf_path:
        try:
            return _get_network_from_pbf(pbf_path, polygon, config)
        except ImportError as exc:
            print(f"  {exc} — falling back to OSMnx.")
        except Exception as exc:
            print(f"  PBF network extraction failed ({exc}) — falling back to OSMnx.")

    buffer_m = config["osm"]["bbox_buffer_m"] if config else 1_000
    buffered = gpd.GeoDataFrame([1], geometry=[polygon], crs="EPSG:4326").to_crs(
        "EPSG:3857"
    )
    buffered.geometry = buffered.buffer(buffer_m)
    buffered = buffered.to_crs("EPSG:4326")

    print("  Downloading walking network (OSMnx)…")
    t0 = time.time()
    last_exc = None
    for attempt, endpoint_idx in enumerate(range(len(_OVERPASS_ENDPOINTS))):
        _set_overpass_endpoint(endpoint_idx)
        try:
            G = ox.graph_from_polygon(
                buffered.geometry.iloc[0],
                network_type="walk",
                retain_all=False,
            )
            print(
                f"  Network ready: {len(G.nodes)} nodes, {len(G.edges)} edges "
                f"— {time.strftime('%H:%M:%S', time.gmtime(time.time() - t0))}"
            )
            return G
        except Exception as exc:
            last_exc = exc
            err = str(exc).lower()
            if "timed out" in err or "timeout" in err or "connect" in err:
                print(f"  Endpoint {_OVERPASS_ENDPOINTS[endpoint_idx]} failed — trying next…")
                time.sleep(5 * (attempt + 1))
            else:
                raise
    raise last_exc


# ---------------------------------------------------------------------------
# POI tiling helpers
# ---------------------------------------------------------------------------


def _make_tiles(polygon, tile_size_m=1000):
    """
    Divide the bounding box of polygon into square tiles of tile_size_m × tile_size_m.
    Returns a list of Shapely box geometries in WGS84.
    Each tile has only 4 vertices → minimal Overpass query string.
    """
    gdf = gpd.GeoDataFrame({"geometry": [polygon]}, crs="EPSG:4326").to_crs("EPSG:3857")
    minx, miny, maxx, maxy = gdf.total_bounds
    xs = np.arange(minx, maxx, tile_size_m)
    ys = np.arange(miny, maxy, tile_size_m)
    tiles_proj = [
        box(x, y, min(x + tile_size_m, maxx), min(y + tile_size_m, maxy))
        for x in xs
        for y in ys
    ]
    tiles_wgs84 = (
        gpd.GeoDataFrame({"geometry": tiles_proj}, crs="EPSG:3857")
        .to_crs("EPSG:4326")
        .geometry.tolist()
    )
    return tiles_wgs84


def _query_tile(tile, osm_tags, max_retries=5, delay=0.4):
    """
    Query all POI tags for one tile.
    - Rotates Overpass endpoints on timeout failures.
    - Retries up to max_retries times with progressive sleep.
    - Adds a short delay after each successful call to avoid throttling.
    Returns a GeoDataFrame or None.
    """
    endpoint_idx = 0
    _set_overpass_endpoint(endpoint_idx)

    for attempt in range(max_retries):
        try:
            result = ox.features_from_polygon(tile, tags=osm_tags)
            time.sleep(delay)
            return result if not result.empty else None
        except Exception as exc:
            err = str(exc).lower()
            if "400" in str(exc) or "too long" in err:
                return None  # query too large — no point retrying
            if "timed out" in err or "timeout" in err or "connect" in err:
                # Rotate to next endpoint on connection/timeout errors
                endpoint_idx = (endpoint_idx + 1) % len(_OVERPASS_ENDPOINTS)
                _set_overpass_endpoint(endpoint_idx)
                time.sleep(5 * (attempt + 1))
            elif attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return None


def _reset_and_get_osmid(features):
    """
    Reset GeoDataFrame index and return a copy with an 'osmid' string column.
    Handles both MultiIndex (element_type, osmid) and plain Index.
    """
    if isinstance(features.index, pd.MultiIndex) or features.index.name is not None:
        features = features.reset_index()
    else:
        features = features.reset_index(drop=True)

    if "osmid" not in features.columns:
        features["osmid"] = features.index.astype(str)
    features["osmid"] = features["osmid"].astype(str)
    return features


# ---------------------------------------------------------------------------
# POI processing helpers
# ---------------------------------------------------------------------------


def _get_area_m2(gdf):
    return gdf.to_crs("EPSG:3857").geometry.area


def _centroids_latlon(gdf):
    gdf = gdf.copy()
    proj = gdf.to_crs("EPSG:3857")
    gdf["geometry"] = proj.geometry.centroid.to_crs("EPSG:4326")
    gdf["lat"] = gdf.geometry.y
    gdf["lon"] = gdf.geometry.x
    return gdf


# ---------------------------------------------------------------------------
# Main POI function
# ---------------------------------------------------------------------------


def _get_pois_from_pbf(pbf_path, polygon, osm_tags):
    """
    Extract POI features from a local PBF file using pyrosm.
    Returns a GeoDataFrame with osmid, geometry, and OSM tag columns.
    """
    try:
        import pyrosm
    except ImportError:
        raise ImportError("pyrosm is required for PBF-based POI extraction. "
                          "Run: pip install pyrosm")

    bbox = list(polygon.bounds)  # [west, south, east, north]
    osm = pyrosm.OSM(pbf_path, bounding_box=bbox)

    # Combine all tag filters (amenity, leisure, shop, tourism, building)
    custom_filter = {k: v for k, v in osm_tags.items()}

    print("  Extracting POIs from PBF (pyrosm)…")
    features = osm.get_pois(custom_filter=custom_filter)

    if features is None or features.empty:
        return None

    # Normalise osmid column — pyrosm may use 'id', 'osm_id', or index
    for candidate in ("osmid", "id", "osm_id"):
        if candidate in features.columns:
            if candidate != "osmid":
                features = features.rename(columns={candidate: "osmid"})
            break
    else:
        features = features.reset_index(drop=False)
        if "osmid" not in features.columns:
            features["osmid"] = features.index.astype(str)
    features["osmid"] = features["osmid"].astype(str)

    print(f"  {len(features)} raw features from PBF")
    return features


def get_destinations(polygon, tags_csv, pbf_path=None, tile_size_m=4000):
    """
    Extract and classify POIs from OSM for a city polygon.

    If pbf_path is provided, reads from a local Geofabrik PBF file (fast,
    no network calls). Otherwise falls back to tiled Overpass API queries.

    Parameters
    ----------
    polygon    : Shapely geometry (WGS84)
    tags_csv   : str — path to Key_Value_DestType.csv (sep=';')
    pbf_path   : str or None — path to a local .osm.pbf file
    tile_size_m: int — Overpass tile size in metres (fallback only)

    Returns
    -------
    pd.DataFrame — columns: osmid, lat, lon, name, Type, Type_Name
    """
    df_kv = pd.read_csv(tags_csv, sep=";")
    df_kv = df_kv[df_kv["Type"] > 0][
        ["Key", "Value", "Key_Value", "Type", "Type_Name"]
    ].drop_duplicates()
    osm_tags = df_kv.groupby("Key")["Value"].apply(list).to_dict()

    t0 = time.time()

    # ── Source: PBF (preferred) or Overpass (fallback) ────────────────
    if pbf_path:
        try:
            features = _get_pois_from_pbf(pbf_path, polygon, osm_tags)
            if features is None:
                print("  No features from PBF — falling back to Overpass.")
                pbf_path = None
        except ImportError as exc:
            print(f"  {exc} — falling back to Overpass.")
            pbf_path = None

    if not pbf_path:
        _set_overpass_endpoint(0)
        print(f"  Overpass endpoint: {_OVERPASS_ENDPOINTS[0]}")
        tiles = _make_tiles(polygon, tile_size_m=tile_size_m)
        n_tiles = len(tiles)
        print(f"  Downloading POIs — {n_tiles} tiles of {tile_size_m}m × {tile_size_m}m")

        parts = []
        failed = 0
        for i, tile in enumerate(tiles):
            result = _query_tile(tile, osm_tags)
            if result is not None:
                parts.append(result)
            else:
                failed += 1
            if (i + 1) % 20 == 0 or (i + 1) == n_tiles:
                print(f"     {i+1}/{n_tiles} tiles "
                      f"({len(parts)} with features, {failed} failed/empty)")

        if not parts:
            print("  No features downloaded from any tile.")
            return pd.DataFrame(columns=["osmid", "lat", "lon", "name", "Type", "Type_Name"])

        features = pd.concat(parts)
        features = features[~features.index.duplicated(keep="first")]
        print(f"  Merged: {len(features)} raw features ({len(parts)} tiles with data)")
        features = _reset_and_get_osmid(features)

    if "access" in features.columns:
        features = features[~features["access"].isin(["private", "customers", "no"])]
    features = features[
        ~features.geometry.geom_type.isin(["LineString", "MultiLineString"])
    ]

    # ── Classify per key ─────────────────────────────────────────────
    assigned_polygons = gpd.GeoDataFrame(
        columns=["osmid", "Type", "geometry"], crs="EPSG:4326"
    )
    all_dests = []

    for key in ["amenity", "leisure", "shop", "tourism"]:
        if key not in features.columns:
            continue
        subset = features[features[key].notna()].copy()
        if subset.empty:
            continue

        subset["Key_Value"] = key + "|" + subset[key].astype(str)
        subset = subset.merge(
            df_kv[["Key_Value", "Type", "Type_Name"]], on="Key_Value", how="left"
        )
        subset = subset[subset["Type"].notna()].copy()
        if subset.empty:
            continue

        if key == "leisure":
            poly_mask = subset.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
            if poly_mask.any():
                subset.loc[poly_mask, "_area_m2"] = _get_area_m2(
                    subset[poly_mask]
                ).values
            if "_area_m2" not in subset.columns:
                subset["_area_m2"] = 0
            subset["_area_m2"] = subset["_area_m2"].fillna(0)
            for kv, min_area in [
                ("leisure|swimming_pool", 500),
                ("leisure|garden", 2_000),
                ("leisure|park", 2_000),
                ("leisure|nature_reserve", 2_000),
            ]:
                mask = (
                    (subset["Key_Value"] == kv)
                    & (subset["_area_m2"] < min_area)
                    & (subset["_area_m2"] > 0)
                )
                subset = subset[~mask]

        poly_rows = subset[subset.geometry.geom_type.isin(["Polygon", "MultiPolygon"])][
            ["osmid", "Type", "geometry"]
        ].copy()
        assigned_polygons = pd.concat([assigned_polygons, poly_rows], ignore_index=True)

        subset = _centroids_latlon(subset)
        cols = ["osmid", "lat", "lon", "Type", "Type_Name"]
        if "name" in subset.columns:
            cols.append("name")
        all_dests.append(subset[cols])

    # Buildings: only those not captured by other keys
    if "building" in features.columns:
        bld = features[
            features["building"].notna()
            & features.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
        ].copy()
        for other_key in ["amenity", "shop", "leisure", "tourism"]:
            if other_key in bld.columns:
                bld = bld[bld[other_key].isna()]

        if not bld.empty:
            bld["Key_Value"] = "building|" + bld["building"].astype(str)
            bld = bld.merge(
                df_kv[["Key_Value", "Type", "Type_Name"]], on="Key_Value", how="left"
            )
            bld = bld[bld["Type"].notna()].copy()

        if not bld.empty and not assigned_polygons.empty:
            bld_c = bld.copy()
            bld_c["geometry"] = bld_c.to_crs("EPSG:3857").geometry.centroid.to_crs(
                "EPSG:4326"
            )
            sj = bld_c.sjoin(
                assigned_polygons.rename(
                    columns={"Type": "Type_poly", "osmid": "osmid_poly"}
                ),
                how="left",
                predicate="within",
            )
            same_type = sj[sj["Type"] == sj["Type_poly"]]["osmid"].unique()
            bld = bld[~bld["osmid"].isin(same_type)]

        if not bld.empty:
            bld = _centroids_latlon(bld)
            cols = ["osmid", "lat", "lon", "Type", "Type_Name"]
            if "name" in bld.columns:
                cols.append("name")
            all_dests.append(bld[cols])

    if not all_dests:
        print("  No destinations classified.")
        return pd.DataFrame(
            columns=["osmid", "lat", "lon", "name", "Type", "Type_Name"]
        )

    destinations = pd.concat(all_dests, ignore_index=True)
    destinations = destinations.drop_duplicates(subset="osmid")
    destinations = destinations[
        destinations["lat"].notna() & destinations["lon"].notna()
    ]
    if "name" not in destinations.columns:
        destinations["name"] = np.nan

    # Filter to original polygon — bbox tiles may include POIs outside city boundary
    n_before = len(destinations)
    gdf_dest = gpd.GeoDataFrame(
        destinations,
        geometry=gpd.points_from_xy(destinations["lon"], destinations["lat"]),
        crs="EPSG:4326",
    )
    inside = gdf_dest[gdf_dest.within(polygon)].index
    destinations = destinations.loc[inside].reset_index(drop=True)
    n_clipped = n_before - len(destinations)
    if n_clipped:
        print(f"  Clipped {n_clipped} POIs outside city boundary")

    print(
        f"  {len(destinations)} destinations"
        f" — {time.strftime('%H:%M:%S', time.gmtime(time.time() - t0))}"
    )
    return destinations
