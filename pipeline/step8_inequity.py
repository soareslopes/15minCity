# step8_inequity.py
# Inequality measures for accessibility distributions.

import warnings

import numpy as np
import pandas as pd

try:
    from libpysal.weights import Queen
    from esda.moran import Moran
    _MORAN_AVAILABLE = True
except ImportError:
    _MORAN_AVAILABLE = False

from util_gini import calculate_gini_index, calc_gini_pop

warnings.filterwarnings("ignore")


# ---------------------------------------------------------------------------
# Individual measures
# ---------------------------------------------------------------------------

def gini(values):
    """Territorial Gini coefficient."""
    v = np.array(values, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) < 2 or v.sum() == 0:
        return np.nan
    return calculate_gini_index(v.tolist())


def palma(values):
    """Palma ratio: mean of top 10% / mean of bottom 40%."""
    v = np.sort(np.array(values, dtype=float))
    v = v[~np.isnan(v)]
    if len(v) < 10:
        return np.nan
    n = len(v)
    top10 = v[int(np.ceil(0.9 * n)):]
    bottom40 = v[: int(np.floor(0.4 * n))]
    if bottom40.sum() == 0:
        return np.nan
    return top10.mean() / bottom40.mean()


def theil_t(values):
    """Theil T index."""
    v = np.array(values, dtype=float)
    v = v[~np.isnan(v) & (v > 0)]
    if len(v) < 2:
        return np.nan
    mu = v.mean()
    if mu == 0:
        return np.nan
    return float(np.sum((v / mu) * np.log(v / mu)) / len(v))


def cv(values):
    """Coefficient of variation (std / mean)."""
    v = np.array(values, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) == 0 or v.mean() == 0:
        return np.nan
    return float(v.std() / v.mean())


def moran_i(gdf, col):
    """
    Global Moran's I with Queen contiguity.

    Returns
    -------
    (I, p_value) or (nan, nan) if unavailable.
    """
    if not _MORAN_AVAILABLE:
        return np.nan, np.nan
    try:
        w = Queen.from_dataframe(gdf, silence_warnings=True)
        w.transform = "r"
        result = Moran(gdf[col].fillna(0).values, w)
        return float(result.I), float(result.p_sim)
    except Exception:
        return np.nan, np.nan


# ---------------------------------------------------------------------------
# Combined calculation
# ---------------------------------------------------------------------------

def calculate_all(gdf, metrics=None, pop_col="pop_estimada"):
    """
    Calculate all inequality measures for each metric column.

    Parameters
    ----------
    gdf : GeoDataFrame
        Hexagon grid with accessibility metrics and population.
    metrics : list of str, optional
        Column names to analyse. Defaults to ['Variety', 'Total_Dest', 'Entropy'].
    pop_col : str
        Column with estimated population per hexagon.

    Returns
    -------
    dict
        Flat dict ready to append to the summary CSV row.
    """
    if metrics is None:
        metrics = ["Variety", "Total_Dest", "Entropy"]

    has_pop = pop_col in gdf.columns and gdf[pop_col].sum() > 0
    result = {}

    for col in metrics:
        if col not in gdf.columns:
            continue
        key = col.lower().replace(" ", "_").replace(",", "")
        vals = gdf[col].dropna().values

        result[f"gini_{key}"] = gini(vals)
        result[f"palma_{key}"] = palma(vals)
        result[f"theil_{key}"] = theil_t(vals)
        result[f"cv_{key}"] = cv(vals)

        mi, mp = moran_i(gdf, col)
        result[f"moran_i_{key}"] = mi
        result[f"moran_p_{key}"] = mp

        if has_pop:
            try:
                result[f"gini_pop_{key}"] = calc_gini_pop(gdf, col, pop_col)
            except Exception:
                result[f"gini_pop_{key}"] = np.nan
        else:
            result[f"gini_pop_{key}"] = np.nan

    return result
