# main.py
# Ponto de entrada do pipeline de acessibilidade urbana 15 minutos.
# Lê cities.csv, processa cada cidade sequencialmente, salva resultados.
# Uso: python main.py [--config config.yaml] [--no-analysis] [--rerun]
#
# PIPELINE — PASSOS POR CIDADE (executados em city_pipeline.py):
# ──────────────────────────────────────────────────────────────
#  Passo 1 │ Geometria da cidade
#           │ Ficheiro : step6_ghsl.py → get_city_geometry()
#           │ Obtém o polígono da cidade via Nominatim / OSMnx
#
#  Passo 2 │ Grid hexagonal H3
#           │ Ficheiro : step2_grid.py → gridify_city()
#           │ Divide a área da cidade em hexágonos de ~160 m de diâmetro
#           │ (resolução H3-10; configurável em config.yaml → grid.h3_level)
#
#  Passo 3 │ Rede pedonal OSM
#           │ Ficheiro : step3_osm_city.py → get_walking_network()
#           │ Descarrega a rede viária pedonal do OpenStreetMap e
#           │ constrói um grafo NetworkX dirigido
#
#  Passo 4 │ Pontos de Interesse (POIs)
#           │ Ficheiro : step3_osm_city.py → get_destinations()
#           │ Suporte  : util_geofabrik.py → get_pbf(), clip_pbf()
#           │ Extrai POIs do OSM e classifica por tipo usando
#           │ Key_Value_DestType.csv; fonte primária: PBF Geofabrik,
#           │ fallback automático: Overpass API
#
#  Passo 5 │ Acessibilidade pedonal
#           │ Ficheiro : step5_accessibility.py → calculate_accessibility()
#           │ Alternativa: step5b_accessibility_pandana.py
#           │ Algoritmo POI-forward Dijkstra: para cada hexágono conta
#           │ quantos destinos de cada tipo estão acessíveis a pé em
#           │ cada intervalo de tempo (config.yaml → accessibility.time_intervals_min)
#
#  Passo 6 │ População GHSL
#           │ Ficheiro : step6_ghsl.py → process_city_complete()
#           │ Descarrega tiles GHS-POP, recorta ao polígono da cidade
#           │ e estima a população por hexágono
#
#  Passo 7 │ Mancha urbana — OPCIONAL
#           │ Ficheiro : step7_urban_area.py → get_urban_footprint()
#           │ Remove hexágonos fora do perímetro urbano contínuo;
#           │ activado quando config.yaml → urban_area.method = "continuous"
#
#  Passo 8 │ Medidas de inequidade
#           │ Ficheiro : step8_inequity.py → calculate_all()
#           │ Calcula Gini territorial, Gini ponderado por população,
#           │ Palma ratio, Theil T, CV e Moran's I
#           │ para cada métrica × intervalo de tempo
#
# PASSO FINAL — após processar todas as cidades:
# ──────────────────────────────────────────────────────────────
#  Passo 9 │ Análise e gráficos comparativos
#           │ Ficheiro : step9_analysis.py → run_analysis()
#           │ Gera 4 gráficos em output/figures/; requer ≥ 2 cidades
# ──────────────────────────────────────────────────────────────

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

from city_pipeline import process_city, city_folder_name


def load_config(path="config.yaml"):
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
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--no-analysis", action="store_true", help="Skip analysis step")
    parser.add_argument("--rerun", action="store_true", help="Reprocess cities even if .gpkg exists")
    args = parser.parse_args()

    config = load_config(args.config)

    cities_csv = config["paths"]["cities_csv"]
    output_dir = Path(config["paths"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    cities = pd.read_csv(cities_csv, sep=";")
    if "city_name" not in cities.columns:
        print("ERROR: cities.csv must have a 'city_name' column.")
        sys.exit(1)

    city_list = cities["city_name"].dropna().tolist()
    print(f"Cities to process: {len(city_list)}")

    # Load existing partial results so skipped cities are included in the final CSV
    partial_path = output_dir / "results_partial.csv"
    if partial_path.exists():
        results = pd.read_csv(partial_path).to_dict("records")
    else:
        results = []

    for i, city_name in enumerate(city_list, 1):
        city_safe = _city_safe(city_name)
        gpkg_path = output_dir / city_folder_name(city_name) / f"{city_safe}.gpkg"

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
            # Replace existing entry for this city if present
            results = [r for r in results if r.get("city_name") != city_name]
            results.append(row)
            pd.DataFrame(results).to_csv(partial_path, index=False)

    final_path = output_dir / "results_final.csv"
    pd.DataFrame(results).to_csv(final_path, index=False)
    print(f"\nResults saved → {final_path}")

    # Passo 9: Análise final (step9_analysis.py)
    if not args.no_analysis and len(results) > 1:
        try:
            from step9_analysis import run_analysis
            run_analysis(str(final_path), str(output_dir))
        except Exception as exc:
            print(f"Analysis step failed: {exc}")


if __name__ == "__main__":
    main()
