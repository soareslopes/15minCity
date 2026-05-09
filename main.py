import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

# Make pipeline/ importable without installing as a package
sys.path.insert(0, str(Path(__file__).parent / "pipeline"))

from city_pipeline import process_city, city_folder_name


def load_config(path="config/config.yaml"):
    with open(path) as f:
        return yaml.safe_load(f)


def _city_safe(city_name):
    return (
        city_name.lower()
        .replace(", ", "_")
        .replace(" ", "_")
        .replace(",", "")
    )


def main():
    parser = argparse.ArgumentParser(description="15-min city accessibility pipeline")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--no-analysis", action="store_true", help="Skip analysis step")
    parser.add_argument("--rerun", action="store_true", help="Reprocess cities even if .gpkg exists")
    args = parser.parse_args()

    config = load_config(args.config)

    output_dir = Path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    cities_csv = config["paths"]["cities_csv"]
    cities = pd.read_csv(cities_csv, sep=";")
    if "city_name" not in cities.columns:
        print("ERROR: cities.csv must have a 'city_name' column.")
        sys.exit(1)

    city_list = cities["city_name"].dropna().tolist()
    print(f"Cities to process: {len(city_list)}")

    partial_path = output_dir / "results_partial.csv"
    if partial_path.exists():
        results = pd.read_csv(partial_path).to_dict("records")
    else:
        results = []

    for i, city_name in enumerate(city_list, 1):
        gpkg_path = output_dir / "gpkg" / f"{city_folder_name(city_name)}.gpkg"

        if not args.rerun and gpkg_path.exists():
            print(f"\n[{i}/{len(city_list)}] {city_name} — SKIP (already done)")
            continue

        print(f"\n{'='*55}")
        print(f"[{i}/{len(city_list)}] {city_name}")
        print(f"{'='*55}")
        try:
            row = process_city(city_name, config)
        except Exception as exc:
            print(f"  FATAL ERROR: {exc}")
            row = {"city_name": city_name, "status": "fatal_error", "error": str(exc)}

        if row:
            results = [r for r in results if r.get("city_name") != city_name]
            results.append(row)
            pd.DataFrame(results).to_csv(partial_path, index=False)

    final_path = output_dir / "results_final.csv"
    pd.DataFrame(results).to_csv(final_path, index=False)
    print(f"\nResults saved → {final_path}")

    if not args.no_analysis and len(results) > 1:
        try:
            from step9_analysis import run_analysis
            run_analysis(str(final_path), str(output_dir))
        except Exception as exc:
            print(f"Analysis step failed: {exc}")


if __name__ == "__main__":
    main()
