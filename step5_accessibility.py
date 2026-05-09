# step5_accessibility.py
# Accessibility calculation: POI-forward Dijkstra + numpy distance matrix.
# Replaces the Pandana-based version. Only requires NetworkX (via OSMnx).

import time

import geopandas as gpd  # noqa: E402
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd


def _safe_col(type_name):
    """Convert a Type_Name string to a safe column prefix."""
    return (
        type_name.lower()
        .replace(" ", "_")
        .replace(",", "")
        .replace("&", "and")
        .replace("/", "_")
        .replace("-", "_")
    )


def _nearest_nodes_and_dist(G, xs, ys):
    """
    Return (node_ids, straight_line_distances_m) for each (x, y) point.

    Uses OSMnx for fast nearest-node lookup, then Euclidean distance
    approximation in metres (accurate enough for the 120 m filter).
    """
    node_ids = ox.nearest_nodes(G, X=xs, Y=ys)
    nodes_xy = {n: (d["x"], d["y"]) for n, d in G.nodes(data=True)}

    xs_arr = np.array(xs, dtype=float)
    ys_arr = np.array(ys, dtype=float)
    node_xs = np.array([nodes_xy[n][0] for n in node_ids], dtype=float)
    node_ys = np.array([nodes_xy[n][1] for n in node_ids], dtype=float)

    mean_lat = ys_arr.mean()
    dx = (xs_arr - node_xs) * 111_320 * np.cos(np.radians(mean_lat))
    dy = (ys_arr - node_ys) * 111_320
    return node_ids, np.sqrt(dx ** 2 + dy ** 2)


def _add_indicators(gdf, type_names, intervals):
    """Add Variety, Total_Dest, Entropy, Total_Dest_01 per interval in place."""
    for t in intervals:
        cols = [f"{_safe_col(tn)}_{t}min" for tn in type_names]
        present = [c for c in cols if c in gdf.columns]
        if not present:
            continue

        gdf[f"total_dest_{t}min"] = gdf[present].sum(axis=1)
        gdf[f"variety_{t}min"] = (gdf[present] > 0).sum(axis=1).astype(int)

        tot = gdf[f"total_dest_{t}min"].replace(0, np.nan)
        props = gdf[present].div(tot, axis=0)
        ln_p = props.apply(np.log).replace([np.inf, -np.inf], np.nan)
        entropy_raw = (props * ln_p).sum(axis=1, skipna=True)
        n_t = max(len(present), 1)
        gdf[f"entropy_{t}min"] = (
            (entropy_raw / np.log(n_t) * -1).fillna(0).clip(lower=0)
        )

        tot_range = gdf[f"total_dest_{t}min"].max() - gdf[f"total_dest_{t}min"].min()
        gdf[f"total_dest_01_{t}min"] = (
            (gdf[f"total_dest_{t}min"] - gdf[f"total_dest_{t}min"].min())
            / (tot_range + 1e-9)
        )


def calculate_accessibility(
    G,
    hex_gdf,
    destinations,
    tags_csv,
    time_intervals_min,
    walk_speed_ms=1.2,
    hex_acceptance_dist=120,
):
    """
    POI-forward Dijkstra accessibility for multiple time intervals.

    Strategy
    --------
    For each unique POI network node, run single_source_dijkstra ONCE with
    the maximum cutoff distance (longest interval × speed). All time
    thresholds are then applied in O(1) via numpy comparisons — no repeated
    graph traversal.

    Parameters
    ----------
    G : nx.MultiDiGraph
        OSMnx walking graph (WGS84, edges with 'length' in metres).
    hex_gdf : GeoDataFrame
        H3 hexagon polygons, column 'h3_id', CRS EPSG:4326.
    destinations : DataFrame
        Columns: lat, lon, Type, Type_Name (output of osm_city.get_destinations).
    tags_csv : str
        Path to Key_Value_DestType.csv (sep=';').
    time_intervals_min : list[int]
        Walking time thresholds, e.g. [15, 30, 45, 60, 75, 90].
    walk_speed_ms : float
        Walking speed in m/s (default 1.2 m/s ≈ 4.3 km/h).
    hex_acceptance_dist : float
        Maximum straight-line metres from hex centroid to nearest network
        node. Hexagons beyond this are excluded (parks, water, etc.).

    Returns
    -------
    GeoDataFrame
        Hexagon grid with columns:
          {type}_{t}min       — count of accessible POIs of that type
          variety_{t}min      — number of types with count > 0
          total_dest_{t}min   — total POIs across all types
          entropy_{t}min      — Shannon entropy over types
          total_dest_01_{t}min — normalised total (0–1 within city)
        for each t in time_intervals_min.
    """
    # ---- type definitions ------------------------------------------------
    df_kv = pd.read_csv(tags_csv, sep=";")
    df_kv = df_kv[df_kv["Type"] > 0]
    type_groups = (
        df_kv.groupby(["Type", "Type_Name"])
        .size()
        .reset_index()
        .sort_values("Type")
    )
    type_names = type_groups["Type_Name"].tolist()

    # ---- map hex centroids to nearest graph nodes -----------------------
    hex_gdf = hex_gdf.copy()
    centroids = hex_gdf.geometry.centroid
    hex_node_ids, hex_node_dists = _nearest_nodes_and_dist(
        G, centroids.x.tolist(), centroids.y.tolist()
    )
    hex_gdf["_node_id"] = hex_node_ids
    hex_gdf["_node_dist"] = hex_node_dists
    hex_gdf = (
        hex_gdf[hex_gdf["_node_dist"] <= hex_acceptance_dist]
        .copy()
        .reset_index(drop=True)
    )

    n_hex = len(hex_gdf)
    if n_hex == 0:
        print("  WARNING: no hexagons within acceptance distance of network.")
        return gpd.GeoDataFrame()

    hex_nodes = hex_gdf["_node_id"].tolist()
    # One node can serve multiple hexagons — keep ALL indices, not just the last
    node_to_idxs: dict = {}
    for i, node in enumerate(hex_nodes):
        node_to_idxs.setdefault(node, []).append(i)
    print(f"  {n_hex} hexagons matched to network")

    # ---- handle empty destinations ---------------------------------------
    if destinations is None or len(destinations) == 0:
        for t in time_intervals_min:
            for tn in type_names:
                hex_gdf[f"{_safe_col(tn)}_{t}min"] = 0
        _add_indicators(hex_gdf, type_names, time_intervals_min)
        return hex_gdf.drop(columns=["_node_id", "_node_dist"])

    # ---- map POIs to nearest graph nodes --------------------------------
    valid = destinations.dropna(subset=["lat", "lon"]).copy().reset_index(drop=True)
    poi_nodes = ox.nearest_nodes(G, X=valid["lon"].tolist(), Y=valid["lat"].tolist())
    valid["_poi_node"] = poi_nodes

    # order-preserving unique POI nodes
    unique_poi_nodes = list(dict.fromkeys(poi_nodes))
    poi_node_to_idx = {n: i for i, n in enumerate(unique_poi_nodes)}
    n_unique = len(unique_poi_nodes)

    # ---- POI-forward Dijkstra -------------------------------------------
    # Run on reversed graph so distances are "POI node → hex node" in the
    # original directed graph (i.e. a pedestrian walking *from* the hex *to*
    # the POI).  Reversing once is cheaper than running hex-forward Dijkstra
    # for every hex centroid.
    max_dist_m = max(time_intervals_min) * 60 * walk_speed_ms
    print(f"  Dijkstra from {n_unique} POI nodes (max {max_dist_m:.0f} m)…")
    t0 = time.time()

    G_rev = G.reverse(copy=False)

    # distance matrix: shape (n_unique_poi_nodes, n_hex), init to inf
    dist_mat = np.full((n_unique, n_hex), np.inf, dtype=np.float64)

    for i, poi_node in enumerate(unique_poi_nodes):
        if (i + 1) % 200 == 0:
            print(f"    {i + 1}/{n_unique} ({time.time() - t0:.0f}s)")
        try:
            lengths = nx.single_source_dijkstra_path_length(
                G_rev, poi_node, cutoff=max_dist_m, weight="length"
            )
            for node, dist in lengths.items():
                for j in node_to_idxs.get(node, []):
                    dist_mat[i, j] = min(dist_mat[i, j], dist)
        except Exception:  # noqa: BLE001
            pass

    print(f"  Dijkstra done in {time.time() - t0:.1f}s")

    # ---- count per type per interval ------------------------------------
    sorted_intervals = sorted(time_intervals_min)
    for t_idx, t_min in enumerate(sorted_intervals):
        dist_m = float(t_min * 60 * walk_speed_ms)
        for type_name in type_names:
            col = f"{_safe_col(type_name)}_{t_min}min"
            type_rows = valid[valid["Type_Name"] == type_name]
            if type_rows.empty:
                hex_gdf[col] = 0
                continue
            row_idx = [poi_node_to_idx[n] for n in type_rows["_poi_node"]]
            counts = (dist_mat[row_idx, :] <= dist_m).sum(axis=0).astype(int)
            # Enforce monotonicity: count at t >= count at t-1
            if t_idx > 0:
                prev_col = f"{_safe_col(type_name)}_{sorted_intervals[t_idx - 1]}min"
                if prev_col in hex_gdf.columns:
                    counts = np.maximum(counts, hex_gdf[prev_col].values)
            hex_gdf[col] = counts

    # ---- composite indicators -------------------------------------------
    _add_indicators(hex_gdf, type_names, time_intervals_min)
    return hex_gdf.drop(columns=["_node_id", "_node_dist"])
