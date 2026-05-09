# util_geofabrik.py
# Finds and downloads the smallest Geofabrik PBF that covers a given city.
# PBFs are cached locally so cities in the same region reuse the same file.

import json
import shutil
import subprocess
from pathlib import Path

import requests
from shapely.geometry import shape

GEOFABRIK_INDEX_URL = "https://download.geofabrik.de/index-v1.json"


def _load_index(cache_dir):
    """Download and cache the Geofabrik region index JSON."""
    index_path = Path(cache_dir) / "geofabrik_index.json"
    if not index_path.exists():
        print("  Downloading Geofabrik region index…")
        r = requests.get(GEOFABRIK_INDEX_URL, timeout=30)
        r.raise_for_status()
        index_path.write_text(r.text, encoding="utf-8")
    return json.loads(index_path.read_text(encoding="utf-8"))


def _find_region(city_geom, index):
    """
    Return (region_id, pbf_url) for the smallest Geofabrik region whose
    geometry contains the city centroid.
    """
    centroid = city_geom.centroid
    candidates = []

    for feature in index["features"]:
        props = feature.get("properties", {})
        geom_json = feature.get("geometry")
        pbf_url = props.get("urls", {}).get("pbf")
        if not geom_json or not pbf_url:
            continue
        try:
            region_geom = shape(geom_json)
            if region_geom.contains(centroid):
                candidates.append((region_geom.area, props["id"], pbf_url))
        except Exception:
            continue

    if not candidates:
        raise ValueError("No Geofabrik region found containing the city centroid.")

    candidates.sort()  # smallest area first
    _, region_id, pbf_url = candidates[0]
    return region_id, pbf_url


def clip_pbf(regional_pbf, bbox, output_path):
    """
    Clip a regional PBF to a city bounding box using osmium-tool.

    Requires osmium-tool: brew install osmium-tool  /  conda install osmium-tool

    Parameters
    ----------
    regional_pbf : str | Path  — source PBF (e.g. nordeste-latest.osm.pbf)
    bbox         : tuple       — (minx, miny, maxx, maxy) in WGS84
    output_path  : str | Path  — destination city PBF

    Returns
    -------
    bool — True if successful, False if osmium-tool not found or failed.
    """
    osmium = shutil.which("osmium")
    if not osmium:
        for _candidate in (
            "/opt/homebrew/bin/osmium",
            "/usr/local/bin/osmium",
            "/usr/bin/osmium",
        ):
            if Path(_candidate).is_file():
                osmium = _candidate
                break
    if not osmium:
        print(f"  DEBUG clip_pbf: osmium not found in PATH or known locations")
        return False
    print(f"  DEBUG clip_pbf: osmium found at {osmium}")
    west, south, east, north = bbox
    result = subprocess.run(
        [
            osmium, "extract",
            "--bbox", f"{west},{south},{east},{north}",
            "--strategy", "complete_ways",
            "--overwrite",
            "-o", str(output_path),
            str(regional_pbf),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  DEBUG clip_pbf: osmium failed (rc={result.returncode}): {result.stderr[:300]}")
    return result.returncode == 0 and Path(output_path).exists()


def get_pbf(city_geom, cache_dir):
    """
    Return the local path to the Geofabrik PBF covering the city.
    Downloads the file if not already in cache.

    Parameters
    ----------
    city_geom  : Shapely geometry (WGS84) — city boundary
    cache_dir  : str — folder to store PBF files

    Returns
    -------
    str — absolute path to the local .osm.pbf file
    str — Geofabrik region ID (e.g. 'brazil', 'europe/portugal')
    """
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    index = _load_index(cache_dir)
    region_id, pbf_url = _find_region(city_geom, index)
    pbf_name = pbf_url.split("/")[-1]
    pbf_path = Path(cache_dir) / pbf_name

    if pbf_path.exists():
        size_mb = pbf_path.stat().st_size / 1e6
        print(f"  PBF cache hit: {pbf_name} ({size_mb:.0f} MB) — region: {region_id}")
        return str(pbf_path), region_id

    print(f"  Downloading PBF: {pbf_name}  (region: {region_id})")
    print(f"  URL: {pbf_url}")
    r = requests.get(pbf_url, stream=True, timeout=600)
    r.raise_for_status()

    total = int(r.headers.get("content-length", 0))
    downloaded = 0

    with open(pbf_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                print(
                    f"     {downloaded/1e6:.0f} MB / {total/1e6:.0f} MB "
                    f"({downloaded/total*100:.0f}%)",
                    end="\r",
                )
    print(f"\n  Download complete: {pbf_path}")
    return str(pbf_path), region_id
