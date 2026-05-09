#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu May  7 16:06:54 2026

@author: davidvale

Sacar delimitação e população das cidades EUA > 100.000 habitantes
"""

import geopandas as gpd
import pandas as pd
import requests

# =========================================================
# CONFIG
# =========================================================
YEAR = 2023
MIN_POPULATION = 100_000

OUTPUT_FILE = f"us_places_over_{MIN_POPULATION:,}.gpkg".replace(",", "k")

# =========================================================
# US STATE FIPS
# =========================================================
# 50 states + DC
STATE_FIPS = [
    "01","02","04","05","06","08","09","10","11","12",
    "13","15","16","17","18","19","20","21","22","23",
    "24","25","26","27","28","29","30","31","32","33",
    "34","35","36","37","38","39","40","41","42","44",
    "45","46","47","48","49","50","51","53","54","55","56"
]

# =========================================================
# DOWNLOAD TIGER PLACE DATA
# =========================================================
print("Downloading TIGER PLACE geometries...")

gdfs = []

for i, fips in enumerate(STATE_FIPS, start=1):

    tiger_url = (
        f"https://www2.census.gov/geo/tiger/TIGER{YEAR}/PLACE/"
        f"tl_{YEAR}_{fips}_place.zip"
    )

    print(f"[{i:02d}/{len(STATE_FIPS)}] {fips}")

    try:
        gdf_state = gpd.read_file(tiger_url)

        # Keep only useful columns
        cols = [
            "GEOID",
            "NAME",
            "STATEFP",
            "geometry"
        ]

        cols_existing = [
            c for c in cols
            if c in gdf_state.columns
        ]

        gdf_state = gdf_state[cols_existing]

        gdfs.append(gdf_state)

    except Exception as e:
        print(f"Failed {fips}: {e}")

# Merge all states
gdf = pd.concat(gdfs, ignore_index=True)

print(f"\nTotal places loaded: {len(gdf):,}")

# =========================================================
# DOWNLOAD ACS POPULATION
# =========================================================
# B01003_001E = total population

acs_url = (
    f"https://api.census.gov/data/{YEAR}/acs/acs5"
    "?get=NAME,B01003_001E"
    "&for=place:*"
)

print("\nDownloading ACS population data...")

response = requests.get(acs_url, timeout=60)
response.raise_for_status()

data = response.json()

df = pd.DataFrame(
    data[1:],
    columns=data[0]
)

# =========================================================
# CLEAN ACS TABLE
# =========================================================
df = df.rename(columns={
    "B01003_001E": "population"
})

df["population"] = pd.to_numeric(
    df["population"],
    errors="coerce"
)

# Create GEOID compatible with TIGER
df["GEOID"] = df["state"] + df["place"]

# Keep only necessary fields
df = df[[
    "GEOID",
    "NAME",
    "population"
]]

print(f"Population records: {len(df):,}")

# =========================================================
# JOIN
# =========================================================
print("\nJoining geometries + population...")

merged = gdf.merge(
    df,
    on="GEOID",
    how="left",
    suffixes=("", "_acs")
)

# =========================================================
# FILTER
# =========================================================
filtered = merged[
    merged["population"] >= MIN_POPULATION
].copy()

filtered = filtered.sort_values(
    "population",
    ascending=False
)

print(
    f"\nCities with population >= "
    f"{MIN_POPULATION:,}: "
    f"{len(filtered):,}"
)

# =========================================================
# OPTIONAL CLEANUP
# =========================================================
# Remove invalid geometries if any
filtered = filtered[
    filtered.geometry.notnull()
]

filtered = filtered[
    filtered.is_valid
]

# Reproject to WGS84 (usually already EPSG:4269)
filtered = filtered.to_crs(4326)

# =========================================================
# SAVE
# =========================================================
print(f"\nSaving: {OUTPUT_FILE}")

filtered.to_file(
    OUTPUT_FILE,
    driver="GPKG"
)

print("\nDone.")
print(filtered.head())