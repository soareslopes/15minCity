# util_gini.py — Gini index utilities

import numpy as np
import pandas as pd


def calculate_gini_index(list_of_values):
    """Territorial Gini coefficient from a list of values."""
    sorted_list = sorted(list_of_values)
    height, area = 0, 0
    for value in sorted_list:
        height += value
        area += height - value / 2.0
    fair_area = height * len(sorted_list) / 2.0
    return (fair_area - area) / fair_area


def calc_gini_pop(gpkg, coluna, populacao="pop_estimada"):
    """
    Population-weighted Gini index.

    Builds a per-inhabitant array from the spatial aggregation and computes Gini.
    Uses the same pure-Python implementation as calculate_gini_index — no pysal needed.
    """
    gdf = gpkg[[coluna, populacao]].copy()
    gdf[coluna] = gdf[coluna].astype("float32")
    gdf[populacao] = gdf[populacao].fillna(0).astype(int)
    grupo = gdf.groupby(coluna).sum().reset_index()

    list_a = grupo[coluna].tolist()
    list_b = grupo[populacao].tolist()
    obs_pop = [val for val, cnt in zip(list_a, list_b) for _ in range(cnt)]
    if len(obs_pop) < 2:
        return np.nan
    return calculate_gini_index(obs_pop)
