# city_pipeline.py
# Full pipeline for a single city: geometry, grid, network, accessibility,
# population, inequality. Returns one summary dict for the final CSV.

import time
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pycountry

import step6_ghsl as util_ghsl
import util_geofabrik
import step7_urban_area as _urban_area
from step5_accessibility import _safe_col, calculate_accessibility
from step2_grid import gridify_city
from step8_inequity import calculate_all as calc_inequity
import step3_osm_city as osm_city
from util_maps import make_accessibility_map

warnings.filterwarnings("ignore")


def _get_iso3(country_name):
    try:
        return pycountry.countries.search_fuzzy(country_name)[0].alpha_3
    except Exception:
        return country_name[:3].upper()


def city_folder_name(city_name):
    """Return folder name like 'BRA_Eusébio' for a city string."""
    city_first = city_name.split(",")[0].strip()
    country_raw = city_name.split(",")[-1].strip()
    iso3 = _get_iso3(country_raw)
    return f"{iso3}_{city_first}"


def _step(n, total, label):
    print(f"\n  [{n}/{total}] {label}")


def process_city(city_name, config):
    """
    Run the full 15-min city pipeline for one city.

    Parameters
    ----------
    city_name : str
        OSM-recognisable name, e.g. "Fortaleza, Brazil".
    config : dict
        Loaded from config.yaml.

    Returns
    -------
    dict  — one summary row for the final CSV, or None on failure.
    """
    ua_method = config.get("urban_area", {}).get("method", "discrete")
    STEPS = 10 if ua_method == "continuous" else 9
    _ofs = 1 if ua_method == "continuous" else 0
    t_start = time.time()
    tags_csv = config["paths"]["tags_csv"]
    out_dir = Path(config["paths"]["output_dir"])
    city_safe = (
        city_name.lower()
        .replace(", ", "_")
        .replace(" ", "_")
        .replace(",", "")
    )
    city_dir = out_dir / "cities" / city_folder_name(city_name)
    city_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "gpkg").mkdir(parents=True, exist_ok=True)
    intervals = config["accessibility"]["time_intervals_min"]
    speed = config["accessibility"]["walk_speed_ms"]
    pop_col = config["inequality"]["pop_column"]

    # ------------------------------------------------------------------
    # 1. City geometry (OSMnx / Nominatim)
    # ------------------------------------------------------------------
    _step(1, STEPS, "City geometry (Nominatim / OSMnx)")
    t0 = time.time()
    try:
        city_geom = util_ghsl.get_city_geometry(city_name)
    except Exception as exc:
        print(f"     ERROR: geometry not found — {exc}")
        return {"city_name": city_name, "status": "error_geometry"}
    boundary_gdf = gpd.GeoDataFrame({"geometry": [city_geom]}, crs="EPSG:4326")
    boundary_gdf.to_file(city_dir / f"{city_safe}_boundary.gpkg")
    print(f"     OK — boundary polygon obtained and saved ({time.time()-t0:.1f}s)")

    city_row = gpd.GeoDataFrame(
        {"geometry": [city_geom]}, crs="EPSG:4326"
    ).iloc[0]

    # ------------------------------------------------------------------
    # 2. H3 hexagonal grid
    # ------------------------------------------------------------------
    h3_level = config["grid"]["h3_level"]
    _step(2, STEPS, f"H3 hexagonal grid (resolution {h3_level})")
    t0 = time.time()

    # Sanity check: reject if geometry is implausibly large for a city
    area_km2 = (
        gpd.GeoDataFrame({"geometry": [city_geom]}, crs="EPSG:4326")
        .to_crs("EPSG:3857")
        .area.iloc[0] / 1e6
    )
    print(f"     City area: {area_km2:,.0f} km²")
    if area_km2 < 1:
        print(f"     ERROR: geometry is a point or degenerate ({area_km2:.4f} km²).")
        print(f"     Nominatim returned a point instead of a polygon for '{city_name}'.")
        print("     Try a more specific name, e.g. include the state and country.")
        return {"city_name": city_name, "status": "error_geometry_point"}
    if area_km2 > 5_000:
        print(f"     ERROR: area too large for a city ({area_km2:,.0f} km²).")
        print(f"     Is '{city_name}' a country or region? Use a specific city name.")
        return {"city_name": city_name, "status": "error_geometry_too_large"}

    hex_poly = gridify_city(city_row, level=h3_level)
    if hex_poly.empty:
        print("     ERROR: no hexagons generated")
        return {"city_name": city_name, "status": "error_grid"}
    print(f"     OK — {len(hex_poly):,} hexagons (~{len(hex_poly)*0.73:.0f} km² effective area)"
          f" ({time.time()-t0:.1f}s)")

    # ------------------------------------------------------------------
    # 3. Walking network (OSMnx → NetworkX) + PBF resolution
    # ------------------------------------------------------------------
    _step(3, STEPS, "Walking network (OSMnx → NetworkX graph)")
    t0 = time.time()

    # Resolve PBF here so both network (step 3) and POIs (step 4) can use it
    pbf_path = None
    pbf_cache = out_dir / "cache" / "pbf_cache"
    city_pbf = city_dir / f"{city_safe}.osm.pbf"
    try:
        regional_pbf, region_id = util_geofabrik.get_pbf(city_geom, str(pbf_cache))
        if city_pbf.exists():
            pbf_path = str(city_pbf)
            print(f"     City PBF cache hit: {city_pbf.stat().st_size / 1e6:.1f} MB")
        else:
            print("     Clipping regional PBF to city bbox (osmium)…")
            ok = util_geofabrik.clip_pbf(regional_pbf, city_geom.bounds, city_pbf)
            if ok:
                pbf_path = str(city_pbf)
                print(f"     City PBF ready: {city_pbf.stat().st_size / 1e6:.1f} MB")
            else:
                pbf_path = regional_pbf
                print("     WARNING: osmium not found — using full regional PBF"
                      "\n     Install with: brew install osmium-tool")
    except Exception as exc:
        print(f"     WARNING: PBF not available ({exc}) — will use Overpass fallback")

    network_path = city_dir / "network.graphml"
    try:
        if network_path.exists():
            import osmnx as ox
            G = ox.load_graphml(network_path)
            print(f"     Loaded from cache: {network_path.name} ({time.time()-t0:.1f}s)")
        else:
            G = osm_city.get_walking_network(city_geom, config, pbf_path=pbf_path)
            import osmnx as ox
            ox.save_graphml(G, network_path)
            print(f"     OK — graph saved to cache ({time.time()-t0:.1f}s)")
    except Exception as exc:
        print(f"     ERROR: network — {exc}")
        return {"city_name": city_name, "status": "error_network"}

    # ------------------------------------------------------------------
    # 4. POI destinations (Geofabrik PBF / pyrosm, fallback: Overpass)
    # ------------------------------------------------------------------
    _step(4, STEPS, "POI destinations (Geofabrik PBF / pyrosm)")
    t0 = time.time()

    try:
        destinations = osm_city.get_destinations(
            city_geom, tags_csv,
            pbf_path=pbf_path,
            tile_size_m=config.get("osm", {}).get("poi_tile_size_m", 4000),
        )
    except Exception as exc:
        print(f"     WARNING: POI extraction failed ({exc}) — continuing with 0 destinations")
        destinations = pd.DataFrame(
            columns=["osmid", "lat", "lon", "name", "Type", "Type_Name"]
        )
    n_dest = len(destinations)
    n_types = destinations["Type_Name"].nunique() if n_dest else 0
    print(f"     OK — {n_dest:,} destinations across {n_types} types ({time.time()-t0:.1f}s)")

    # ------------------------------------------------------------------
    # 5. Accessibility
    # ------------------------------------------------------------------
    engine = config["accessibility"].get("engine", "dijkstra")
    _step(5, STEPS, f"Accessibility — {engine} engine for {intervals} min")
    print(f"     Walking speed: {speed} m/s  |  Max cutoff: {max(intervals)*60*speed:.0f} m")
    t0 = time.time()
    try:
        if engine == "pandana":
            from step5b_accessibility_pandana import calculate_accessibility_pandana
            hex_final = calculate_accessibility_pandana(
                G,
                hex_poly,
                destinations,
                tags_csv,
                time_intervals_min=intervals,
                walk_speed_ms=speed,
                hex_acceptance_dist=config["grid"]["hex_acceptance_dist"],
                max_pois_per_type=config["accessibility"].get(
                    "pandana_max_pois_per_type", 50
                ),
            )
        else:
            hex_final = calculate_accessibility(
                G,
                hex_poly,
                destinations,
                tags_csv,
                time_intervals_min=intervals,
                walk_speed_ms=speed,
                hex_acceptance_dist=config["grid"]["hex_acceptance_dist"],
            )
    except Exception as exc:
        print(f"     ERROR: accessibility calculation failed — {exc}")
        return {"city_name": city_name, "status": "error_accessibility"}

    if hex_final is None or hex_final.empty:
        print("     ERROR: no hexagons survived the network acceptance filter")
        return {"city_name": city_name, "status": "error_no_hexagons"}
    print(f"     OK — accessibility matrix complete ({time.time()-t0:.1f}s)")

    # ------------------------------------------------------------------
    # 6. Population from GHSL
    # ------------------------------------------------------------------
    _step(6, STEPS, "Population estimates (GHSL GHS-POP tiles)")
    t0 = time.time()
    ghsl_dir = city_dir / "ghsl"
    ghsl_dir.mkdir(parents=True, exist_ok=True)
    ghsl_cache = out_dir / "cache" / "ghsl_cache"
    ghsl_cache.mkdir(parents=True, exist_ok=True)

    try:
        gpkg_path = util_ghsl.process_city_complete(
            city_name,
            str(ghsl_dir),
            dataset=config["ghsl"]["dataset"],
            year=config["ghsl"]["year"],
            ghsl_cache_dir=str(ghsl_cache),
            city_geom=city_geom,
        )
        if gpkg_path and Path(gpkg_path).exists():
            pop_pts = gpd.read_file(gpkg_path)
            joined = gpd.sjoin(
                pop_pts,
                hex_final[["h3_id", "geometry"]],
                how="inner",
                predicate="within",
            )
            pop_per_hex = (
                joined.groupby("h3_id")["value"]
                .sum()
                .reset_index()
                .rename(columns={"value": pop_col})
            )
            hex_final = hex_final.merge(pop_per_hex, on="h3_id", how="left")
            total_pop_est = pop_per_hex[pop_col].sum()
            print(f"     OK — estimated population: {total_pop_est:,.0f} inhabitants ({time.time()-t0:.1f}s)")
        else:
            print("     WARNING: GHSL tile not available — population will be NaN")
    except Exception as exc:
        print(f"     WARNING: GHSL download/processing failed — {exc}")

    if pop_col not in hex_final.columns:
        hex_final[pop_col] = np.nan
    else:
        hex_final[pop_col] = hex_final[pop_col].fillna(0)

    # ------------------------------------------------------------------
    # 7. Urban footprint filter  [only when method = "continuous"]
    # ------------------------------------------------------------------
    if ua_method == "continuous":
        _step(7, STEPS, "Urban footprint filter (GHSL-derived)")
        t0 = time.time()
        try:
            ua_cfg = config.get("urban_area", {})
            footprint = _urban_area.get_urban_footprint(
                city_geom,
                str(ghsl_cache),
                dataset=config["ghsl"]["dataset"],
                year=config["ghsl"].get("year"),
                smooth_sigma=ua_cfg.get("smooth_sigma", 3),
                threshold_percentile=ua_cfg.get("threshold_percentile", 30),
                closing_radius_px=ua_cfg.get("closing_radius_px", 5),
                min_fragment_km2=ua_cfg.get("min_fragment_km2", 1.0),
                ensure_tiles=False,  # tiles already in cache from step 6
            )
            if footprint is not None:
                centroids = hex_final.geometry.centroid
                within = centroids.within(footprint)
                n_before = len(hex_final)
                hex_final = hex_final[within].copy().reset_index(drop=True)
                n_removed = n_before - len(hex_final)
                print(f"     {n_before} → {len(hex_final)} hexagons "
                      f"({n_removed} non-urban removed, "
                      f"{len(hex_final)/n_before*100:.0f}% retained)")
                gpd.GeoDataFrame(
                    {"geometry": [footprint]}, crs="EPSG:4326"
                ).to_file(city_dir / f"{city_safe}_urban_footprint.gpkg")
                print(f"     Footprint saved  ({time.time()-t0:.1f}s)")
            else:
                print("     WARNING: footprint computation failed — using all hexagons")
        except Exception as exc:
            print(f"     WARNING: urban footprint error ({exc}) — using all hexagons")

    # ------------------------------------------------------------------
    # 7/8. Inequality measures
    # ------------------------------------------------------------------
    _step(7 + _ofs, STEPS, "Inequality measures (Gini, Palma, Theil, CV, Moran's I)")
    t0 = time.time()
    ineq_metrics = [
        f"{m}_{t}min"
        for t in intervals
        for m in ("variety", "total_dest")
        if f"{m}_{t}min" in hex_final.columns
    ]
    print(f"     Metrics: {ineq_metrics}")
    inequity_results = calc_inequity(hex_final, metrics=ineq_metrics, pop_col=pop_col)
    print(f"     OK — {len(inequity_results)} inequality indicators computed ({time.time()-t0:.1f}s)")

    # ------------------------------------------------------------------
    # 8/9. Save GeoPackage
    # ------------------------------------------------------------------
    _step(8 + _ofs, STEPS, "Saving GeoPackage")
    t0 = time.time()
    gpkg_out = out_dir / "gpkg" / f"{city_folder_name(city_name)}.gpkg"
    hex_final.to_file(gpkg_out)
    n_cols = len(hex_final.columns)
    print(f"     OK — {gpkg_out.name}  ({len(hex_final):,} hexagons × {n_cols} columns)"
          f" ({time.time()-t0:.1f}s)")

    metric_map = f"total_dest_{intervals[0]}min"
    map_path = city_dir / f"{city_folder_name(city_name)}_map_{metric_map}.png"
    make_accessibility_map(hex_final, city_geom, metric_map, map_path, city_name)

    # ------------------------------------------------------------------
    # 9/10. Summary row
    # ------------------------------------------------------------------
    _step(9 + _ofs, STEPS, "Building summary row")
    total_pop = hex_final[pop_col].sum() if pop_col in hex_final.columns else np.nan
    area_ha = hex_final.to_crs("EPSG:3857").area.sum() / 10_000
    pop_density = total_pop / area_ha if area_ha > 0 else np.nan

    summary = {
        "city_name": city_name,
        "status": "success",
        "n_hexagons": len(hex_final),
        "total_pop": total_pop,
        "pop_density_ha": pop_density,
    }

    for t in intervals:
        for metric in ("variety", "total_dest", "entropy"):
            col = f"{metric}_{t}min"
            summary[f"mean_{col}"] = (
                hex_final[col].mean() if col in hex_final.columns else np.nan
            )

    df_kv = pd.read_csv(tags_csv, sep=";")
    df_kv = df_kv[df_kv["Type"] > 0]
    type_names = (
        df_kv.groupby(["Type", "Type_Name"])
        .size()
        .reset_index()
        .sort_values("Type")["Type_Name"]
        .tolist()
    )
    for t in intervals:
        for tn in type_names:
            col = f"{_safe_col(tn)}_{t}min"
            summary[f"mean_{col}"] = (
                hex_final[col].mean() if col in hex_final.columns else np.nan
            )

    summary.update(inequity_results)
    summary["runtime_s"] = round(time.time() - t_start, 1)

    # Quick summary printout
    v15 = summary.get("mean_variety_15min", float("nan"))
    v90 = summary.get("mean_variety_90min", float("nan"))
    g15 = summary.get(f"gini_variety_{intervals[0]}min", float("nan"))
    print("\n  ── Summary ──────────────────────────────────────")
    print(f"     Hexagons     : {len(hex_final):,}")
    print(f"     Population   : {total_pop:,.0f} inh  ({pop_density:.1f} inh/ha)" if not np.isnan(total_pop) else "     Population   : n/a")
    print(f"     Variety 15min: {v15:.2f}  |  Variety 90min: {v90:.2f}" if not np.isnan(v15) else "")
    print(f"     Gini variety ({intervals[0]}min): {g15:.3f}" if not np.isnan(g15) else "")
    print(f"     Total runtime: {summary['runtime_s']}s")
    print("  ─────────────────────────────────────────────────")

    return summary
