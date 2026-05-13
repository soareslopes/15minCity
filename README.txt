WEBSITE:
https://masti-git-main-fabioppimentas-projects.vercel.app/about

================================================================================
  15-MINUTE CITY — Urban Accessibility Pipeline
================================================================================

  A generalised pipeline to measure 15-minute city accessibility indicators
  for any list of cities worldwide, using only the city name as input.

  Based on the original Cidade15min project
  (David Vale / André Lopes, CITTA / University of Lisbon).


================================================================================
  HOW TO RUN
================================================================================

  1. Prerequisites
  ----------------
  Python 3.11 (recommended; 3.12+ may have dependency issues).

  osmium-tool is optional but speeds up POI extraction:
    brew install osmium-tool


  2. Set up the environment
  -------------------------
    python3.11 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt


  3. Configure the city source
  ----------------------------
  Set city_source.mode in config.yaml:

  Option A — GPKG (recommended): boundaries from a local GeoPackage.
    city_source:
      mode: gpkg
      country: US          # folder inside data/ → data/US/cities.gpkg
      sample: null         # null = all cities; integer = random N for testing

    The pipeline reads all city boundaries directly from the GPKG and skips
    the Nominatim lookup entirely. See readme_gpkg.txt for the required schema.

  Option B — OSM: Nominatim lookup (original behaviour).
    city_source:
      mode: osm

    Edit config_cities.csv — one city per line:
      city_name
      Fortaleza, Ceará, Brazil
      Lisbon, Portugal
      Oslo, Norway

    The name must be recognisable by Nominatim/OSM.
    If the search fails, add the country: "Porto, Portugal" instead of "Porto".


  4. (Optional) Pre-download PBF for offline use
  -----------------------------------------------
  Download the Geofabrik regional PBF for your target region and place it in:

    output/cache/pbf_cache/

  Example for Northeast Brazil:
    output/cache/pbf_cache/nordeste-latest.osm.pbf

  When a PBF is present, the pipeline uses it for both the walking network and
  POI extraction — no internet calls needed.


  5. Run
  ------
    python main.py

  Options:
    --config config.yaml           Use an alternative config file
    --rerun                        Reprocess cities even if output exists
    --no-analysis                  Skip the final comparative charts (step 9)


================================================================================
  FILE STRUCTURE
================================================================================

  15minCity/
  │
  ├── main.py                           Entry point — run this
  ├── requirements.txt
  ├── README.txt
  ├── readme_gpkg.txt                   Required GPKG schema for city boundaries
  │
  ├── config.yaml                       All parameters and paths
  ├── config_cities.csv                 City list (used only when mode: osm)
  ├── config_osm_key_types.csv          OSM tag → destination type mapping
  │
  ├── data/                             Local city boundary files (mode: gpkg)
  │   ├── US/
  │   │   └── cities.gpkg
  │   ├── BR/
  │   │   └── cities.gpkg
  │   └── EU/
  │       └── cities.gpkg
  │
  ├── pipeline/
  │   ├── city_pipeline.py              Per-city orchestrator
  │   ├── step2_grid.py                 H3 hexagonal grid
  │   ├── step3_osm_city.py             Walking network + POIs (OSMnx / PBF)
  │   ├── step5_accessibility.py        Accessibility — Dijkstra (NetworkX)
  │   ├── step5b_accessibility_pandana.py   Accessibility — Pandana (faster)
  │   ├── step6_ghsl.py                 City geometry + GHSL population
  │   ├── step7_urban_area.py           Urban footprint filter (optional)
  │   ├── step8_inequity.py             Gini, Palma, Theil, CV, Moran's I
  │   ├── step9_analysis.py             Comparative charts
  │   ├── util_geofabrik.py             Geofabrik PBF download and clip
  │   ├── util_gini.py                  Gini index calculation
  │   └── util_maps.py                  PNG map generation (Positron basemap)
  │
  └── output/                           Generated automatically
      ├── cities/                       Per-city working files (shared across runs)
      │   └── US_Portland_4159000/
      │       ├── network.pkl           Walking network cache (pickle)
      │       ├── portland.osm.pbf      Clipped city PBF (if osmium available)
      │       ├── portland_boundary.gpkg
      │       ├── US_Portland_4159000_map_total_dest_30min.png
      │       └── ghsl/                 GHSL population tiles
      ├── results/                      One subfolder per run configuration
      │   └── pandana_i30-45-60_h9/     Run ID derived from config.yaml
      │       ├── results_partial.csv   Saved after each city (resume-safe)
      │       ├── results_final.csv     Written at the end of each run
      │       ├── gpkg/
      │       │   └── US_Portland_4159000_pandana.gpkg
      │       └── figures/              Comparative charts (requires >= 2 cities)
      │           ├── diminishing_returns.png
      │           ├── density_vs_accessibility.png
      │           ├── inequity_distributions.png
      │           └── gini_vs_density.png
      └── cache/
          ├── pbf_cache/                Geofabrik regional PBFs (shared)
          └── ghsl_cache/               GHSL population tiles (shared)


  Run ID format
  -------------
  The run subfolder is derived automatically from config.yaml:

    {engine}_i{intervals}_h{h3_level}[_cont]

  Examples:
    pandana_i30-45-60_h9          pandana engine, 30/45/60 min, H3 level 9
    dijkstra_i30-60-90_h9         dijkstra engine, 30/60/90 min, H3 level 9
    pandana_i30-60-90_h9_cont     same but with continuous urban footprint


================================================================================
  PIPELINE STEPS
================================================================================

  For each city, up to 9 sequential steps are run (10 if urban_area: continuous):

  Step 1   City boundary polygon
           GPKG mode: read directly from data/{country}/cities.gpkg (no internet)
           OSM mode:  query Nominatim / OSMnx
           Module: step6_ghsl.py (OSM mode only)

  Step 2   H3 hexagonal grid (resolution 9, ~174 m per hexagon)
           Module: step2_grid.py

  Step 3   Walking network — extracted from local PBF if available,
           otherwise downloaded from OSMnx / Overpass API
           Cached as network.pkl in the city folder
           Module: step3_osm_city.py

  Step 4   POI extraction and classification by destination type
           Module: step3_osm_city.py + util_geofabrik.py

  Step 5   Pedestrian accessibility per hexagon (all time thresholds)
           Engine: dijkstra (exact) or pandana (faster, set in config.yaml)
           Module: step5_accessibility.py / step5b_accessibility_pandana.py

  Step 6   Population estimates from GHSL GHS-POP tiles
           Module: step6_ghsl.py

  Step 7   Urban footprint filter (only when urban_area.method = "continuous")
           Module: step7_urban_area.py

  Step 8   Spatial inequality metrics (Gini, Palma, Theil, CV, Moran's I)
           Module: step8_inequity.py

  Step 9   Save GeoPackage + accessibility map
           Module: city_pipeline.py + util_maps.py


================================================================================
  ACCESSIBILITY ENGINES
================================================================================

  Dijkstra (default, exact)
  -------------------------
  POI-forward strategy on the OSM walking network:

  1. For each unique OSM node containing a POI, run
     single_source_dijkstra_path_length once with the maximum cutoff.
     Results are stored in a NumPy float32 matrix (n_POI_nodes x n_hexagons).

  2. For each time threshold, a simple threshold is applied to the matrix —
     no additional graph traversal.

  3. Accessible counts per hexagon are computed with a vectorised NumPy sum.

  Each POI node is traversed exactly once. All time intervals and destination
  types are derived from this single pass.

  Pandana (faster for large cities)
  ----------------------------------
  Builds a contraction-hierarchies network (pandana.Network) from the OSM
  graph and uses aggregate queries to count POIs within each time threshold.
  Significantly faster on cities with many hexagons or POIs.
  Requires numpy<2 (pinned in requirements.txt).

  Acceptance filter (both engines)
  ---------------------------------
  Hexagons whose centroid is more than 120 m from the nearest network node
  are excluded (parks, water bodies, areas without street coverage).


================================================================================
  OUTPUT FILES
================================================================================

  output/results/{run_id}/results_final.csv
  ------------------------------------------
  One row per successfully processed city. Key columns:

    city_name               City display name
    city_id                 Unique identifier used for folder/file naming
                            GPKG mode: "{country}_{NAME}_{GEOID}"
                            OSM mode:  "{ISO3}_{CityName}"
    status                  success (error cities are not saved)
    n_hexagons              Number of valid hexagons after acceptance filter
    total_pop               Estimated population (GHSL)
    pop_density_ha          Population density (inhabitants/ha)
    mean_variety_Xmin       Mean destination type variety at X min
    mean_total_dest_Xmin    Mean total destinations at X min
    gini_variety_Xmin       Territorial Gini for variety at X min
    gini_pop_variety_Xmin   Population-weighted Gini
    palma_variety_Xmin      Palma ratio
    moran_i_variety_Xmin    Moran's I (spatial autocorrelation)
    runtime_s               Processing time in seconds

  Columns repeat for each time interval defined in config.yaml.


  output/results/{run_id}/gpkg/{city_id}_{engine}.gpkg
  ------------------------------------------------------
  H3 hexagonal grid with all attributes. Open directly in QGIS.
  Key columns per hexagon:

    {type}_{t}min       POI count by destination type and time threshold
    variety_{t}min      Number of destination types with count > 0
    total_dest_{t}min   Total accessible destinations
    entropy_{t}min      Shannon entropy of destination mix
    pop_estimada        Estimated population (GHSL)


================================================================================
  INEQUALITY METRICS
================================================================================

  Computed for each indicator (variety, total_dest) x each time interval:

    Territorial Gini          Inequality between hexagons
    Population-weighted Gini  Inequality between inhabitants
    Palma ratio               Mean top 10% / mean bottom 40%
    Theil T                   Sensitive to differences at the top
    CV                        Coefficient of variation (std / mean)
    Moran's I                 Spatial autocorrelation (clustering)


================================================================================
  CONFIGURATION  (config.yaml)
================================================================================

  paths:
    cities_csv: "config_cities.csv"    # city list for OSM mode
    output_dir: "output"
    tags_csv:   "config_osm_key_types.csv"

  city_source:
    mode: gpkg                     # "gpkg" (local GPKG) or "osm" (Nominatim)
    country: US                    # subfolder inside data/ (gpkg mode only)
    sample: null                   # null = all cities; integer = random N

  grid:
    h3_level: 9                    # H3 resolution (~174 m hexagon diameter)
    hex_acceptance_dist: 120       # Max centroid-to-node distance (metres)

  accessibility:
    engine: pandana                # "dijkstra" or "pandana"
    time_intervals_min: [30, 60, 90]
    walk_speed_ms: 1.2             # 1.2 m/s ≈ 4.3 km/h
    pandana_max_pois_per_type: 50  # only used when engine: pandana

  urban_area:
    method: "discrete"             # "discrete" or "continuous" (GHSL-derived)

  ghsl:
    dataset: "population"
    year: 2025

================================================================================
