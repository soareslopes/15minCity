================================================================================
  GPKG SCHEMA — City Boundary Files
================================================================================

  This document defines the required schema for the GeoPackage (.gpkg) files
  used as the source of city administrative boundaries in GPKG mode.


================================================================================
  FOLDER STRUCTURE
================================================================================

  data/
  ├── US/
  │   └── cities.gpkg
  ├── BR/
  │   └── cities.gpkg
  ├── EU/
  │   └── cities.gpkg
  └── CH/
      └── cities.gpkg

  Each folder corresponds to a country/region code set in config.yaml
  (city_source.country). The file inside must always be named cities.gpkg.


================================================================================
  REQUIRED COLUMNS
================================================================================

  Column       Type          Description
  ----------   -----------   --------------------------------------------------
  GEOID        string        Unique city identifier within the country.
                             Used to build the output folder and file name.
                             Must not have duplicates.

  NAME         string        City display name — used in logs and output paths.

  population   float / int   Total estimated population. May be null.

  geometry     geometry      Administrative boundary of the city.
                             Required — see geometry requirements below.

  Additional columns (state code, NUTS code, data source, etc.) are ignored
  by the pipeline but may be present in the file.


================================================================================
  GEOMETRY REQUIREMENTS
================================================================================

  Type        Polygon or MultiPolygon
  CRS         EPSG:4326 (WGS 84, decimal degrees)
  Min area    Geometries smaller than 1 km² are rejected (point or degenerate)
  Max area    Geometries larger than 5,000 km² are rejected (region or country)

  Invalid or null geometries must be removed before saving the file.


================================================================================
  GEOID UNIQUENESS
================================================================================

  The GEOID field is used to build each city's output folder name:

    {COUNTRY}_{NAME}_{GEOID}    e.g.  US_Portland_4159000

  It must be unique within the file. If two cities share the same NAME
  (e.g. two "Springfield" in different states), the GEOID ensures that
  their output folders do not collide.


================================================================================
  MINIMAL EXAMPLE (United States)
================================================================================

  GEOID     NAME        population   geometry
  -------   ---------   ----------   -------------------
  4159000   Portland    652503       MULTIPOLYGON(...)
  5363000   Seattle     737255       MULTIPOLYGON(...)
  0667000   San Jose    1013240      MULTIPOLYGON(...)


================================================================================
  PREPARING A FILE FOR A NEW COUNTRY
================================================================================

  1. Obtain administrative boundaries from the relevant data source:
       US    — TIGER/Line (Census Bureau)
       BR    — IBGE Malha Municipal
       EU    — Eurostat NUTS / GISCO
       Other — GADM (gadm.org)

  2. Filter to cities above your desired population threshold.

  3. Ensure the columns GEOID, NAME, population, and geometry are present
     with the correct types.

  4. Reproject to EPSG:4326 if necessary.

  5. Remove null or invalid geometries.

  6. Save as cities.gpkg in data/{COUNTRY_CODE}/.


  Example (Python / GeoPandas):

    import geopandas as gpd

    gdf = gpd.read_file("source_file.gpkg")

    gdf = gdf.rename(columns={
        "cod_municipio": "GEOID",
        "nome":          "NAME",
        "pop_total":     "population",
    })

    gdf = gdf[["GEOID", "NAME", "population", "geometry"]]
    gdf = gdf.to_crs("EPSG:4326")
    gdf = gdf[gdf.geometry.notnull() & gdf.geometry.is_valid]
    gdf["GEOID"] = gdf["GEOID"].astype(str)

    gdf.to_file("data/BR/cities.gpkg", driver="GPKG")

================================================================================
