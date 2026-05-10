import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

# Make pipeline/ importable without installing as a package
sys.path.insert(0, str(Path(__file__).parent / "pipeline"))

from city_pipeline import (
    city_folder_name,
    process_city,
)


def load_config(path="config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def _load_cities_from_gpkg(config):
    """
    Read city list from data/{country}/cities.gpkg.
    Returns a list of (city_name, city_id, city_geom) tuples.
    city_id is used as the unique folder/file identifier in output.
    """
    import geopandas as gpd

    src = config.get("city_source", {})
    country = src.get("country", "")
    sample = src.get("sample", None)

    gpkg_path = Path("data") / country / "cities.gpkg"
    if not gpkg_path.exists():
        print(f"ERROR: GPKG not found at {gpkg_path}")
        sys.exit(1)

    gdf = gpd.read_file(gpkg_path)

    missing = [c for c in ("GEOID", "NAME", "geometry") if c not in gdf.columns]
    if missing:
        print(f"ERROR: cities.gpkg is missing required columns: {missing}")
        sys.exit(1)

    gdf = gdf[gdf.geometry.notnull()].copy()
    gdf["GEOID"] = gdf["GEOID"].astype(str)

    if sample is not None:
        n = int(sample)
        gdf = gdf.sample(n=min(n, len(gdf)), random_state=42).reset_index(drop=True)
        print(f"Random sample: {len(gdf)} cities from {gpkg_path}")
    else:
        print(f"Cities to process: {len(gdf)} (from {gpkg_path})")

    cities = []
    for _, row in gdf.iterrows():
        name = str(row["NAME"])
        geoid = str(row["GEOID"])
        city_id = f"{country}_{name.replace(' ', '_')}_{geoid}"
        cities.append((name, city_id, row.geometry))
    return cities


def _load_cities_from_csv(config):
    """
    Read city list from cities_csv (original OSM mode).
    Returns a list of (city_name, city_id, None) tuples.
    """
    cities_csv = config["paths"]["cities_csv"]
    cities = pd.read_csv(cities_csv, sep=";")
    if "city_name" not in cities.columns:
        print("ERROR: cities.csv must have a 'city_name' column.")
        sys.exit(1)

    city_names = cities["city_name"].dropna().tolist()
    print(f"Cities to process: {len(city_names)} (from {cities_csv})")
    return [(name, city_folder_name(name), None) for name in city_names]


def main():
    parser = argparse.ArgumentParser(description="15-min city accessibility pipeline")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--no-analysis", action="store_true", help="Skip analysis step")
    parser.add_argument(
        "--rerun", action="store_true", help="Reprocess cities even if .gpkg exists"
    )
    args = parser.parse_args()

    config = load_config(args.config)

    output_dir = Path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    mode = config.get("city_source", {}).get("mode", "osm")
    if mode == "gpkg":
        city_list = _load_cities_from_gpkg(config)
    else:
        city_list = _load_cities_from_csv(config)

    partial_path = output_dir / "results_partial.csv"
    if partial_path.exists():
        results = pd.read_csv(partial_path).to_dict("records")
    else:
        results = []

    for i, (city_name, city_id, city_geom) in enumerate(city_list, 1):
        engine = config.get("accessibility", {}).get("engine", "dijkstra")
        gpkg_out = output_dir / "gpkg" / f"{city_id}_{engine}.gpkg"

        if not args.rerun and gpkg_out.exists():
            print(f"[{i}/{len(city_list)}] {city_name} — skip")
            continue

        print(f"\n[{i}/{len(city_list)}] {city_name}  [{city_id}]")
        try:
            row = process_city(city_name, config, city_geom=city_geom, city_id=city_id)
        except Exception as exc:
            print(f"  FATAL ERROR: {exc}")
            row = {
                "city_name": city_name,
                "city_id": city_id,
                "status": "fatal_error",
                "error": str(exc),
            }

        if row:
            results = [r for r in results if r.get("city_id") != city_id]
            results.append(row)
            pd.DataFrame(results).to_csv(partial_path, index=False)

    final_path = output_dir / "results_final.csv"
    pd.DataFrame(results).to_csv(final_path, index=False)
    print(f"\nResults saved → {final_path}")

    if not args.no_analysis and len(results) > 1:
        try:
            from step9_analysis import (
                run_analysis,
            )

            run_analysis(str(final_path), str(output_dir))
        except Exception as exc:
            print(f"Analysis step failed: {exc}")


if __name__ == "__main__":
    main()
