# step7_urban_area.py
# Derives a continuous urban footprint from GHSL raster data.
# Used when urban_area.method = "continuous" in config.yaml.

import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import geometry_mask, shapes
from rasterio.merge import merge as rasterio_merge
from scipy.ndimage import binary_closing, gaussian_filter
from scipy.ndimage import label as ndlabel
from shapely.geometry import shape
from shapely.ops import unary_union

from step6_ghsl import _build_tile_url, _get_dataset_config, get_required_tiles


def _ensure_tiles(city_geom, cache_dir, dataset, year):
    """Download any missing GHSL tiles for the city. Fast no-op if all cached."""
    import requests
    from zipfile import ZipFile

    cfg = _get_dataset_config(dataset)
    year = year or cfg["default_year"]
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    for tid in get_required_tiles(city_geom.bounds):
        out_tif = cache_dir / f"{tid}.tif"
        if out_tif.exists():
            continue
        url = _build_tile_url(cfg, year, tid)
        zip_path = str(out_tif) + ".zip"
        try:
            with requests.get(url, stream=True, timeout=600) as r:
                r.raise_for_status()
                with open(zip_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                        f.write(chunk)
            with ZipFile(zip_path) as z:
                tifs = [n for n in z.namelist() if n.endswith(".tif")]
                if tifs:
                    z.extract(tifs[0], str(cache_dir))
                    extracted = cache_dir / tifs[0]
                    if extracted != out_tif:
                        extracted.rename(out_tif)
            Path(zip_path).unlink(missing_ok=True)
        except Exception as exc:
            Path(zip_path).unlink(missing_ok=True)
            print(f"  WARNING urban_area: could not download tile {tid}: {exc}")


def get_urban_footprint(
    city_geom,
    ghsl_cache_dir,
    dataset="population",
    year=None,
    smooth_sigma=3,
    threshold_percentile=30,
    closing_radius_px=5,
    min_fragment_km2=1.0,
    ensure_tiles=True,
):
    """
    Derive a continuous urban footprint from GHSL raster data.

    Steps
    -----
    1. Load GHSL tiles, merge if multiple, mask to city boundary.
    2. Gaussian smoothing to create a continuous density surface.
    3. Adaptive threshold (percentile of non-zero pixels).
    4. Morphological closing — fills gaps, connects nearby fragments.
    5. Keep connected components above min_fragment_km2.
    6. Vectorise to a single Shapely polygon (WGS84).

    Parameters
    ----------
    city_geom           : Shapely geometry (WGS84)
    ghsl_cache_dir      : str — folder with cached GHSL tile TIFs
    dataset             : str — GHSL dataset key (default "population")
    year                : int or None — GHSL year (None → dataset default)
    smooth_sigma        : float — Gaussian sigma in pixels (1px ≈ 100m)
    threshold_percentile: int — percentile of non-zero pixels as lower threshold
    closing_radius_px   : int — morphological closing radius in pixels
    min_fragment_km2    : float — drop isolated patches smaller than this area
    ensure_tiles        : bool — download missing tiles automatically

    Returns
    -------
    Shapely geometry (WGS84) or None if computation fails (caller falls back
    to discrete / city boundary).
    """
    cache_dir = Path(ghsl_cache_dir)

    if ensure_tiles:
        _ensure_tiles(city_geom, cache_dir, dataset, year)

    cfg = _get_dataset_config(dataset)
    year = year or cfg["default_year"]
    tile_ids = get_required_tiles(city_geom.bounds)
    tile_paths = [
        str(cache_dir / f"{tid}.tif")
        for tid in tile_ids
        if (cache_dir / f"{tid}.tif").exists()
    ]

    if not tile_paths:
        print("  WARNING urban_area: no GHSL tiles found — falling back to discrete")
        return None

    # ---- load and merge tiles -------------------------------------------
    srcs = [rasterio.open(p) for p in tile_paths]
    if len(srcs) == 1:
        data = srcs[0].read(1).astype(float)
        transform = srcs[0].transform
        raster_crs = srcs[0].crs
        nodata_val = srcs[0].nodata
        srcs[0].close()
    else:
        merged, transform = rasterio_merge(srcs)
        raster_crs = srcs[0].crs
        nodata_val = srcs[0].nodata
        for s in srcs:
            s.close()
        data = merged[0].astype(float)

    # ---- mask to city boundary -----------------------------------------
    city_proj = (
        gpd.GeoDataFrame([1], geometry=[city_geom], crs="EPSG:4326")
        .to_crs(raster_crs)
        .geometry.iloc[0]
    )
    city_mask = geometry_mask(
        [city_proj], transform=transform, invert=True, out_shape=data.shape
    )
    if nodata_val is not None:
        data = np.where(data == nodata_val, 0.0, data)
    data = np.clip(data, 0.0, None) * city_mask

    # ---- step 1: Gaussian smoothing ------------------------------------
    smoothed = gaussian_filter(data, sigma=float(smooth_sigma))

    # ---- step 2: adaptive threshold ------------------------------------
    nonzero = smoothed[smoothed > 0]
    if len(nonzero) == 0:
        print("  WARNING urban_area: all-zero raster — falling back to discrete")
        return None
    threshold = np.percentile(nonzero, int(threshold_percentile))
    binary = smoothed >= threshold

    # ---- step 3: morphological closing ---------------------------------
    r = max(1, int(closing_radius_px))
    struct = np.ones((2 * r + 1, 2 * r + 1), dtype=bool)
    closed = binary_closing(binary, structure=struct)

    # ---- step 4: connected components ----------------------------------
    labeled, n_comp = ndlabel(closed)
    if n_comp == 0:
        return None

    pixel_area_m2 = abs(transform.a * transform.e)
    min_pixels = max(1, int(min_fragment_km2 * 1e6 / pixel_area_m2))
    sizes = np.bincount(labeled.ravel())
    sizes[0] = 0  # background
    keep = np.where(sizes >= min_pixels)[0]
    if len(keep) == 0:
        keep = np.array([int(np.argmax(sizes))])

    mask_arr = np.isin(labeled, keep).astype(np.uint8)

    # ---- step 5: vectorise ---------------------------------------------
    polys = [shape(g) for g, v in shapes(mask_arr, transform=transform) if v == 1]
    if not polys:
        return None

    footprint = unary_union(polys)

    # ---- reproject to WGS84 and clip to city ---------------------------
    footprint_wgs84 = (
        gpd.GeoDataFrame([1], geometry=[footprint], crs=raster_crs)
        .to_crs("EPSG:4326")
        .geometry.iloc[0]
    )
    clipped = footprint_wgs84.intersection(city_geom)
    return clipped if not clipped.is_empty else None
