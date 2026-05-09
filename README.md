# 15-Minute City — Urban Accessibility Pipeline

A generalised pipeline to measure 15-minute city accessibility indicators for any list of cities worldwide, using only the city name as input.

Based on the original Cidade15min project (David Vale / André Lopes, CITTA / University of Lisbon).

---

## How to Run

### 1. Prerequisites

- Python 3.11 (recommended; 3.12+ may have dependency issues)
- `osmium-tool` for clipping regional PBFs to city boundaries (optional but speeds up POI extraction):
  ```bash
  brew install osmium-tool
  ```

### 2. Set up the environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure cities

Edit `config/cities.csv` — one city per line, using an OSM-recognisable name:

```
city_name
Fortaleza, Ceará, Brazil
Lisbon, Portugal
Oslo, Norway
Buenos Aires, Argentina
```

### 4. (Optional) Pre-download PBF for offline use

Download the Geofabrik regional PBF for your target region and place it in `output/cache/pbf_cache/`. The pipeline will use it for both the walking network and POI extraction without any internet calls.

Example for Northeast Brazil:
```
output/cache/pbf_cache/nordeste-latest.osm.pbf
```

### 5. Run

```bash
python main.py
```

**Options:**

| Flag | Description |
|------|-------------|
| `--config config/config.yaml` | Use an alternative config file |
| `--rerun` | Reprocess cities even if output already exists |
| `--no-analysis` | Skip the final comparative charts (step 9) |

---

## Project Structure

```
15minCity/
│
├── main.py                          # Entry point — run this
├── requirements.txt
├── .gitignore
├── README.md
│
├── config/
│   ├── config.yaml                  # All parameters and paths
│   ├── cities.csv                   # List of cities to process
│   └── Key_Value_DestType.csv       # OSM tag → destination type mapping
│
├── pipeline/
│   ├── city_pipeline.py             # Per-city orchestrator
│   ├── step2_grid.py                # H3 hexagonal grid
│   ├── step3_osm_city.py            # Walking network + POIs (OSMnx / PBF)
│   ├── step5_accessibility.py       # Accessibility (Dijkstra, NetworkX + NumPy)
│   ├── step5b_accessibility_pandana.py  # Accessibility (Pandana, faster for large cities)
│   ├── step6_ghsl.py                # City geometry + GHSL population
│   ├── step7_urban_area.py          # Urban footprint filter (optional)
│   ├── step8_inequity.py            # Gini, Palma, Theil, CV, Moran's I
│   ├── step9_analysis.py            # Comparative charts
│   ├── util_geofabrik.py            # Geofabrik PBF download and clip
│   ├── util_gini.py                 # Gini index calculation
│   └── util_maps.py                 # PNG map generation (Positron basemap)
│
└── output/                          # Generated automatically
    ├── results_final.csv
    ├── results_partial.csv
    ├── gpkg/
    │   ├── BRA_Fortaleza.gpkg       # Final hexagon grid per city
    │   └── PRT_Lisbon.gpkg
    ├── cities/
    │   ├── BRA_Fortaleza/           # Per-city working files
    │   │   ├── network.graphml
    │   │   ├── BRA_Fortaleza.osm.pbf
    │   │   ├── BRA_Fortaleza_boundary.gpkg
    │   │   ├── BRA_Fortaleza_map_total_dest_30min.png
    │   │   └── ghsl/
    │   └── PRT_Lisbon/
    ├── figures/                     # Comparative charts (requires ≥ 2 cities)
    │   ├── diminishing_returns.png
    │   ├── density_vs_accessibility.png
    │   ├── inequity_distributions.png
    │   └── gini_vs_density.png
    └── cache/
        ├── pbf_cache/               # Geofabrik regional PBFs (shared across cities)
        └── ghsl_cache/              # GHSL population tiles (shared across cities)
```

---

## Pipeline Steps

For each city, the pipeline runs 9 sequential steps:

| Step | Description | Module |
|------|-------------|--------|
| 1 | City boundary polygon (Nominatim / OSMnx) | `step6_ghsl.py` |
| 2 | H3 hexagonal grid (resolution 9, ~174 m) | `step2_grid.py` |
| 3 | Walking network (OSMnx or PBF via pyrosm) | `step3_osm_city.py` |
| 4 | POI extraction and classification | `step3_osm_city.py` + `util_geofabrik.py` |
| 5 | Pedestrian accessibility per hexagon | `step5_accessibility.py` |
| 6 | Population estimates (GHSL GHS-POP) | `step6_ghsl.py` |
| 7 | Urban footprint filter *(optional)* | `step7_urban_area.py` |
| 8 | Spatial inequality metrics | `step8_inequity.py` |
| 9 | Save GeoPackage + map | `city_pipeline.py` + `util_maps.py` |

---

## Accessibility Engine

Uses a **POI-forward Dijkstra** strategy on the OSM walking network:

1. For each unique OSM node containing a POI, run `single_source_dijkstra_path_length` once with the maximum cutoff. Results are stored in a NumPy `float32` matrix of shape `(n_unique_POI_nodes × n_hexagons)`.
2. For each time threshold (30, 60, 90 min), a simple threshold is applied to the matrix — no additional graph traversal.
3. For each POI type and threshold, accessible POI counts per hexagon are computed with a vectorised NumPy sum.

Each POI node is traversed exactly once; all time intervals and all destination types are derived from this single pass.

**Acceptance filter:** hexagons whose centroid is more than 120 m from the nearest network node are excluded (parks, water bodies, areas without street coverage).

---

## Output Files

### `output/results_final.csv`
One row per city. Key columns:

| Column | Description |
|--------|-------------|
| `city_name` | City name |
| `status` | `success` or `error_*` |
| `n_hexagons` | Number of valid hexagons |
| `total_pop` | Estimated population (GHSL) |
| `mean_variety_30min` | Mean destination type variety at 30 min |
| `mean_total_dest_30min` | Mean total destinations at 30 min |
| `gini_variety_30min` | Territorial Gini for variety at 30 min |
| `gini_pop_variety_30min` | Population-weighted Gini |
| `palma_variety_30min` | Palma ratio |
| `moran_i_variety_30min` | Moran's I (spatial autocorrelation) |
| `runtime_s` | Processing time in seconds |

*(columns repeat for each time interval: 30, 60, 90 min)*

### `output/gpkg/BRA_CityName.gpkg`
H3 hexagonal grid with all attributes. Open directly in QGIS. Key columns per hexagon:

- `{type}_{t}min` — POI count by destination type and time threshold
- `variety_{t}min` — number of destination types with count > 0
- `total_dest_{t}min` — total accessible destinations
- `entropy_{t}min` — Shannon entropy of destination mix
- `pop_estimada` — estimated population (GHSL)

---

## Inequality Metrics

Computed for each indicator (`variety`, `total_dest`) × each time interval:

| Metric | Description |
|--------|-------------|
| Territorial Gini | Inequality between hexagons |
| Population-weighted Gini | Inequality between inhabitants |
| Palma ratio | Mean top 10% / mean bottom 40% |
| Theil T | Sensitive to differences at the top of the distribution |
| CV | Coefficient of variation (std / mean) |
| Moran's I | Spatial autocorrelation (clustering) |

---

## Configuration (`config/config.yaml`)

```yaml
paths:
  cities_csv: "config/cities.csv"
  output_dir: "output"
  tags_csv:   "config/Key_Value_DestType.csv"

grid:
  h3_level: 9                    # H3 resolution (~174 m diameter)
  hex_acceptance_dist: 120       # Max centroid-to-node distance (metres)

accessibility:
  engine: dijkstra               # "dijkstra" or "pandana"
  time_intervals_min: [30, 60, 90]
  walk_speed_ms: 1.2             # 1.2 m/s ≈ 4.3 km/h

urban_area:
  method: "discrete"             # "discrete" (admin boundary) or "continuous" (GHSL-derived)

ghsl:
  dataset: "population"
  year: 2025
```
