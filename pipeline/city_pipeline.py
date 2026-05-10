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


def _run_id(config):
    engine = config.get("accessibility", {}).get("engine", "dijkstra")
    intervals = config.get("accessibility", {}).get("time_intervals_min", [30, 60, 90])
    h3 = config.get("grid", {}).get("h3_level", 9)
    ua = config.get("urban_area", {}).get("method", "discrete")
    istr = "-".join(str(t) for t in intervals)
    run = f"{engine}_i{istr}_h{h3}"
    if ua == "continuous":
        run += "_cont"
    return run


def _step_done(n, total, label, elapsed):
    prefix = f"  [{n}/{total}] {label} "
    print(f"{prefix}{'.' * max(1, 58 - len(prefix))} {elapsed:.1f}s")


def process_city(city_name, config, city_geom=None, city_id=None):
    """
    Run the full 15-min city pipeline for one city.

    Parameters
    ----------
    city_name : str
        Display name of the city (e.g. "Portland" or "Fortaleza, Brazil").
    config : dict
        Loaded from config.yaml.
    city_geom : Shapely geometry or None
        If provided (GPKG mode), skips the Nominatim lookup in step 1.
    city_id : str or None
        Unique folder/file identifier for output (e.g. "US_Portland_4159000").
        If None, derived from city_name via city_folder_name().

    Returns
    -------
    dict  — one summary row for the final CSV, or None on failure.
    """
    if city_id is None:
        city_id = city_folder_name(city_name)

    ua_method = config.get("urban_area", {}).get("method", "discrete")
    STEPS = 10 if ua_method == "continuous" else 9
    _ofs = 1 if ua_method == "continuous" else 0
    t_start = time.time()
    tags_csv = config["paths"]["tags_csv"]
    out_dir = Path(config["paths"]["output_dir"])
    run_dir = out_dir / "results" / _run_id(config)
    city_safe = (
        city_name.lower()
        .replace(", ", "_")
        .replace(" ", "_")
        .replace(",", "")
    )
    city_dir = out_dir / "cities" / city_id
    city_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "gpkg").mkdir(parents=True, exist_ok=True)
    intervals = config["accessibility"]["time_intervals_min"]
    speed = config["accessibility"]["walk_speed_ms"]
    pop_col = config["inequality"]["pop_column"]

    # ------------------------------------------------------------------
    # 1. City geometry — GPKG (local) or Nominatim (OSM)
    # ------------------------------------------------------------------
    t0 = time.time()
    if city_geom is not None:
        geo_source = "GPKG"
    else:
        geo_source = "Nominatim"
        try:
            city_geom = util_ghsl.get_city_geometry(city_name)
        except Exception as exc:
            print(f"  ERROR: geometry not found — {exc}")
            return {"city_name": city_name, "city_id": city_id, "status": "error_geometry"}
    boundary_gdf = gpd.GeoDataFrame({"geometry": [city_geom]}, crs="EPSG:4326")
    boundary_gdf.to_file(city_dir / f"{city_safe}_boundary.gpkg")
    _step_done(1, STEPS, f"Geometry ({geo_source})", time.time() - t0)

    city_row = gpd.GeoDataFrame(
        {"geometry": [city_geom]}, crs="EPSG:4326"
    ).iloc[0]

    # ------------------------------------------------------------------
    # 2. H3 hexagonal grid
    # ------------------------------------------------------------------
    h3_level = config["grid"]["h3_level"]
    t0 = time.time()

    area_km2 = (
        gpd.GeoDataFrame({"geometry": [city_geom]}, crs="EPSG:4326")
        .to_crs("EPSG:3857")
        .area.iloc[0] / 1e6
    )
    if area_km2 < 1:
        print(f"  ERROR: geometry is a point or degenerate ({area_km2:.4f} km²).")
        return {"city_name": city_name, "status": "error_geometry_point"}
    if area_km2 > 5_000:
        print(f"  ERROR: area too large ({area_km2:,.0f} km²) — is this a country/region?")
        return {"city_name": city_name, "status": "error_geometry_too_large"}

    hex_poly = gridify_city(city_row, level=h3_level)
    if hex_poly.empty:
        print("  ERROR: no hexagons generated")
        return {"city_name": city_name, "status": "error_grid"}
    _step_done(2, STEPS, f"H3 grid  res={h3_level} · {len(hex_poly):,} hexagons", time.time() - t0)

    # ------------------------------------------------------------------
    # 3. Walking network (OSMnx → NetworkX) + PBF resolution
    # ------------------------------------------------------------------
    t0 = time.time()

    pbf_path = None
    pbf_cache = out_dir / "cache" / "pbf_cache"
    city_pbf = city_dir / f"{city_safe}.osm.pbf"
    try:
        regional_pbf, _ = util_geofabrik.get_pbf(city_geom, str(pbf_cache))
        if city_pbf.exists():
            pbf_path = str(city_pbf)
        else:
            ok = util_geofabrik.clip_pbf(regional_pbf, city_geom.bounds, city_pbf)
            if ok:
                pbf_path = str(city_pbf)
            else:
                pbf_path = regional_pbf
                print("     WARNING: osmium not found — using full regional PBF")
    except Exception as exc:
        print(f"     WARNING: PBF not available ({exc}) — using Overpass fallback")

    import pickle
    network_path = city_dir / "network.pkl"
    # delete stale graphml from previous runs to avoid confusion
    stale = city_dir / "network.graphml"
    if stale.exists():
        stale.unlink()
    try:
        if network_path.exists():
            with open(network_path, "rb") as _f:
                G = pickle.load(_f)
            net_source = "cache"
        else:
            G = osm_city.get_walking_network(city_geom, config, pbf_path=pbf_path)
            with open(network_path, "wb") as _f:
                pickle.dump(G, _f)
            net_source = "downloaded"
    except Exception as exc:
        print(f"  ERROR: network — {exc}")
        return {"city_name": city_name, "status": "error_network"}
    _step_done(3, STEPS, f"Walking network ({net_source})", time.time() - t0)

    # ------------------------------------------------------------------
    # 4. POI destinations (Geofabrik PBF / pyrosm, fallback: Overpass)
    # ------------------------------------------------------------------
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
    _step_done(4, STEPS, f"POIs  {n_dest:,} destinations · {n_types} types", time.time() - t0)

    # ------------------------------------------------------------------
    # 5. Accessibility
    # ------------------------------------------------------------------
    engine = config["accessibility"].get("engine", "dijkstra")
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
        print(f"  ERROR: accessibility calculation failed — {exc}")
        return {"city_name": city_name, "status": "error_accessibility"}

    if hex_final is None or hex_final.empty:
        print("  ERROR: no hexagons survived the network acceptance filter")
        return {"city_name": city_name, "status": "error_no_hexagons"}
    _step_done(5, STEPS, f"Accessibility ({engine})", time.time() - t0)

    # ------------------------------------------------------------------
    # 6. Population from GHSL
    # ------------------------------------------------------------------
    t0 = time.time()
    ghsl_dir = city_dir / "ghsl"
    ghsl_dir.mkdir(parents=True, exist_ok=True)
    ghsl_cache = out_dir / "cache" / "ghsl_cache"
    ghsl_cache.mkdir(parents=True, exist_ok=True)
    pop_label = "Population (GHSL)"

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
            pop_label = f"Population (GHSL)  {total_pop_est:,.0f} inh"
        else:
            print("     WARNING: GHSL tile not available — population will be NaN")
    except Exception as exc:
        print(f"     WARNING: GHSL download/processing failed — {exc}")

    if pop_col not in hex_final.columns:
        hex_final[pop_col] = np.nan
    else:
        hex_final[pop_col] = hex_final[pop_col].fillna(0)
    _step_done(6, STEPS, pop_label, time.time() - t0)

    # ------------------------------------------------------------------
    # 7. Urban footprint filter  [only when method = "continuous"]
    # ------------------------------------------------------------------
    if ua_method == "continuous":
        t0 = time.time()
        ua_label = "Urban footprint (skipped)"
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
                ensure_tiles=False,
            )
            if footprint is not None:
                centroids = hex_final.geometry.centroid
                within = centroids.within(footprint)
                n_before = len(hex_final)
                hex_final = hex_final[within].copy().reset_index(drop=True)
                gpd.GeoDataFrame(
                    {"geometry": [footprint]}, crs="EPSG:4326"
                ).to_file(city_dir / f"{city_safe}_urban_footprint.gpkg")
                ua_label = f"Urban footprint  {n_before} → {len(hex_final)} hexagons"
            else:
                print("     WARNING: footprint computation failed — using all hexagons")
        except Exception as exc:
            print(f"     WARNING: urban footprint error ({exc}) — using all hexagons")
        _step_done(7, STEPS, ua_label, time.time() - t0)

    # ------------------------------------------------------------------
    # 7/8. Inequality measures
    # ------------------------------------------------------------------
    t0 = time.time()
    ineq_metrics = [
        f"{m}_{t}min"
        for t in intervals
        for m in ("variety", "total_dest")
        if f"{m}_{t}min" in hex_final.columns
    ]
    inequity_results = calc_inequity(hex_final, metrics=ineq_metrics, pop_col=pop_col)
    _step_done(7 + _ofs, STEPS, f"Inequality  {len(inequity_results)} indicators", time.time() - t0)

    # ------------------------------------------------------------------
    # 8/9. Save GeoPackage
    # ------------------------------------------------------------------
    t0 = time.time()
    gpkg_out = run_dir / "gpkg" / f"{city_id}_{engine}.gpkg"
    hex_final.to_file(gpkg_out)
    _step_done(8 + _ofs, STEPS, "GeoPackage saved", time.time() - t0)

    make_accessibility_map(
        hex_final, city_geom,
        f"total_dest_{intervals[0]}min",
        city_dir / f"{city_id}_map_total_dest_{intervals[0]}min.png",
        city_name,
    )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total_pop = hex_final[pop_col].sum() if pop_col in hex_final.columns else np.nan
    area_ha = hex_final.to_crs("EPSG:3857").area.sum() / 10_000
    pop_density = total_pop / area_ha if area_ha > 0 else np.nan

    summary = {
        "city_name": city_name,
        "city_id": city_id,
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

    v15 = summary.get("mean_variety_15min", float("nan"))
    v90 = summary.get("mean_variety_90min", float("nan"))
    g15 = summary.get(f"gini_variety_{intervals[0]}min", float("nan"))

    parts1 = [f"Hexagons: {len(hex_final):,}"]
    if not np.isnan(total_pop):
        parts1 += [f"Pop: {total_pop:,.0f}", f"Density: {pop_density:.1f} inh/ha"]
    print("\n  " + "  |  ".join(parts1))

    parts2 = []
    if not np.isnan(v15):
        parts2.append(f"Variety 15min: {v15:.2f}")
    if not np.isnan(v90):
        parts2.append(f"90min: {v90:.2f}")
    if not np.isnan(g15):
        parts2.append(f"Gini: {g15:.3f}")
    if parts2:
        print("  " + "  |  ".join(parts2))
    print(f"  Runtime: {summary['runtime_s']}s")

    return summary
