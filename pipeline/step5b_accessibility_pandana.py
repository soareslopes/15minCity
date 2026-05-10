# step5b_accessibility_pandana.py
# Pandana-based accessibility (alternative to the Dijkstra engine).
# One network precompute, then one nearest_pois query per POI type.

import time

import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd

from step5_accessibility import _add_indicators, _nearest_nodes_and_dist, _safe_col


def _build_pandana_network(G, max_dist_m):
    """Convert an OSMnx MultiDiGraph to a Pandana Network and precompute."""
    try:
        import pandana
    except ImportError:
        raise ImportError("pandana is required. Run: pip install pandana")

    nodes, edges = ox.graph_to_gdfs(G)
    edges = edges.reset_index()  # columns: u, v, key, length, …

    node_list = list(nodes.index)
    node_to_idx = {nid: i for i, nid in enumerate(node_list)}

    from_idx = edges["u"].map(node_to_idx)
    to_idx = edges["v"].map(node_to_idx)
    valid = from_idx.notna() & to_idx.notna()

    net = pandana.Network(
        nodes["x"].values,
        nodes["y"].values,
        from_idx[valid].astype(int).values,
        to_idx[valid].astype(int).values,
        edges.loc[valid, ["length"]],
        twoway=False,  # OSMnx walk network already has edges in both directions
    )
    net.precompute(max_dist_m + 1)
    return net


def calculate_accessibility_pandana(
    G,
    hex_gdf,
    destinations,
    tags_csv,
    time_intervals_min,
    walk_speed_ms=1.2,
    hex_acceptance_dist=120,
    max_pois_per_type=50,
):
    """
    Pandana-based accessibility for multiple time intervals.

    Parameters
    ----------
    G : nx.MultiDiGraph
    hex_gdf : GeoDataFrame — H3 hexagons, CRS EPSG:4326
    destinations : DataFrame — columns lat, lon, Type, Type_Name
    tags_csv : str — path to config_osm_key_types.csv (sep=';')
    time_intervals_min : list[int]
    walk_speed_ms : float
    hex_acceptance_dist : float — max metres from hex centroid to network node
    max_pois_per_type : int — cap on POIs counted per type per hexagon

    Returns
    -------
    GeoDataFrame with same columns as the Dijkstra engine.
    """
    df_kv = pd.read_csv(tags_csv, sep=";")
    df_kv = df_kv[df_kv["Type"] > 0]
    type_names = (
        df_kv.groupby(["Type", "Type_Name"])
        .size()
        .reset_index()
        .sort_values("Type")["Type_Name"]
        .tolist()
    )

    max_dist_m = max(time_intervals_min) * 60 * walk_speed_ms

    print(f"  Building Pandana network (precompute ≤ {max_dist_m:.0f} m)…")
    t0 = time.time()
    net = _build_pandana_network(G, max_dist_m)
    print(f"  Network ready ({time.time() - t0:.1f}s)")

    # ---- map hex centroids to network nodes --------------------------------
    hex_gdf = hex_gdf.copy()
    centroids = hex_gdf.geometry.centroid
    _, hex_node_dists = _nearest_nodes_and_dist(
        G, centroids.x.tolist(), centroids.y.tolist()
    )
    hex_pandana_idxs = net.get_node_ids(centroids.x.values, centroids.y.values)

    hex_gdf["_node_dist"] = hex_node_dists
    hex_gdf["_pandana_idx"] = hex_pandana_idxs
    hex_gdf = (
        hex_gdf[hex_gdf["_node_dist"] <= hex_acceptance_dist]
        .copy()
        .reset_index(drop=True)
    )

    n_hex = len(hex_gdf)
    if n_hex == 0:
        print("  WARNING: no hexagons within acceptance distance of network.")
        return gpd.GeoDataFrame()
    print(f"  {n_hex} hexagons matched to network")

    # ---- empty destinations ------------------------------------------------
    if destinations is None or len(destinations) == 0:
        for t in time_intervals_min:
            for tn in type_names:
                hex_gdf[f"{_safe_col(tn)}_{t}min"] = 0
        _add_indicators(hex_gdf, type_names, time_intervals_min)
        return hex_gdf.drop(columns=["_node_dist", "_pandana_idx"])

    # ---- POI query per type ------------------------------------------------
    valid_dest = destinations.dropna(subset=["lat", "lon"]).copy()
    sorted_intervals = sorted(time_intervals_min)
    hex_idxs = hex_gdf["_pandana_idx"].values

    for type_name in type_names:
        col_base = _safe_col(type_name)
        type_rows = valid_dest[valid_dest["Type_Name"] == type_name]

        if type_rows.empty:
            for t in time_intervals_min:
                hex_gdf[f"{col_base}_{t}min"] = 0
            continue

        net.set_pois(
            col_base,
            max_dist_m,
            max_pois_per_type,
            type_rows["lon"],
            type_rows["lat"],
        )
        # DataFrame: index 0..N-1, columns 1..max_pois_per_type, values = distances
        poi_dists = net.nearest_pois(max_dist_m, col_base, num_pois=max_pois_per_type)

        for t_idx, t_min in enumerate(sorted_intervals):
            dist_m = float(t_min * 60 * walk_speed_ms)
            col = f"{col_base}_{t_min}min"

            counts = (poi_dists.loc[hex_idxs] <= dist_m).sum(axis=1).values.astype(int)

            if t_idx > 0:
                prev_col = f"{col_base}_{sorted_intervals[t_idx - 1]}min"
                if prev_col in hex_gdf.columns:
                    counts = np.maximum(counts, hex_gdf[prev_col].values)

            hex_gdf[col] = counts

    _add_indicators(hex_gdf, type_names, time_intervals_min)
    return hex_gdf.drop(columns=["_node_dist", "_pandana_idx"])
