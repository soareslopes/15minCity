# Biblioteca padrão
import os
import time
from io import BytesIO
from typing import List, Tuple
from zipfile import ZipFile

# Terceiros
import geopandas as gpd
import numpy as np
import osmnx as ox
import pandas as pd
import rasterio
import requests
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.mask import mask
from rasterio.merge import merge
from rasterio.transform import xy
from shapely.geometry import Point, box, shape

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO
# ---------------------------------------------------------------------------

GHSL_DATASETS = {
    "population": {
        "product": "GHS_POP_GLOBE_R2023A",
        "prefix": "GHS_POP",
        "suffix": "GLOBE_R2023A_54009_100",
        "version": "V1-0",
        "resolution": 100,
        "default_year": 2025,
        "short_name": "pop",
        "column_name": "population",
    },
    "built_surface": {
        "product": "GHS_BUILT_S_GLOBE_R2023A",
        "prefix": "GHS_BUILT_S",
        "suffix": "GLOBE_R2023A_54009_100",
        "version": "V1-0",
        "resolution": 100,
        "default_year": 2025,
        "short_name": "built_s",
        "column_name": "built_surface",
    },
    "built_volume": {
        "product": "GHS_BUILT_V_GLOBE_R2023A",
        "prefix": "GHS_BUILT_V",
        "suffix": "GLOBE_R2023A_54009_100",
        "version": "V1-0",
        "resolution": 100,
        "default_year": 2025,
        "short_name": "built_v",
        "column_name": "built_volume",
    },
    "settlement_model": {
        "product": "GHS_SMOD_GLOBE_R2023A",
        "prefix": "GHS_SMOD",
        "suffix": "GLOBE_R2023A_54009_1000",
        "version": "V2-0",
        "resolution": 1000,
        "default_year": 2025,
        "short_name": "smod",
        "column_name": "settlement_model",
    },
    "built_characteristics": {
        "product": "GHS_BUILT_C_GLOBE_R2023A",
        "prefix": "GHS_BUILT_C_MSZ",
        "suffix": "GLOBE_R2023A_54009_10",
        "version": "V1-0",
        "resolution": 10,
        "default_year": 2018,
        "short_name": "built_c",
        "column_name": "built_characteristics",
    },
}

BASE_URL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL"
_COUNTRIES_URL = (
    "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_countries.zip"
)
_countries_cache = None  # evita re-download em chamadas múltiplas na mesma sessão


# ---------------------------------------------------------------------------
# HELPERS INTERNOS
# ---------------------------------------------------------------------------


def _load_countries() -> gpd.GeoDataFrame:
    global _countries_cache
    if _countries_cache is None:
        _countries_cache = gpd.read_file(_COUNTRIES_URL)
    return _countries_cache


def _get_dataset_config(dataset: str) -> dict:
    if dataset not in GHSL_DATASETS:
        valid = ", ".join(GHSL_DATASETS.keys())
        raise ValueError(f"Dataset '{dataset}' inválido. Opções: {valid}")
    return GHSL_DATASETS[dataset]


def _build_tile_url(cfg: dict, year: int, tile: str) -> str:
    product = cfg["product"]
    prefix = cfg["prefix"]
    suffix = cfg["suffix"]
    version = cfg["version"]
    ver_clean = version.replace("-", "_")
    folder_url = f"{BASE_URL}/{product}/{prefix}_E{year}_{suffix}/{version}/tiles/"
    filename = f"{prefix}_E{year}_{suffix}_{ver_clean}_{tile}.zip"
    return folder_url + filename


# ---------------------------------------------------------------------------
# API PÚBLICA
# ---------------------------------------------------------------------------


def get_country_bounds(country_name: str) -> Tuple[float, float, float, float]:
    """Retorna (minx, miny, maxx, maxy) do país."""
    countries = _load_countries()
    country = countries[
        countries["NAME"].str.contains(country_name, case=False, na=False)
    ]

    if country.empty:
        first = country_name[0].upper()
        suggestions = sorted(n for n in countries["NAME"] if n.startswith(first))
        print(f"País '{country_name}' não encontrado.\nPaíses com '{first}':")
        for s in suggestions:
            print(f"  - {s}")
        raise ValueError("País não encontrado")

    return country.total_bounds


def get_country_geometry(country_name: str):
    """Retorna a geometria Shapely do país."""
    countries = _load_countries()
    country = countries[
        countries["NAME"].str.contains(country_name, case=False, na=False)
    ]
    if country.empty:
        raise ValueError(f"País '{country_name}' não encontrado")
    return country.geometry.iloc[0]


def get_required_tiles(bbox: Tuple[float, float, float, float]) -> List[str]:
    """Retorna lista de tile IDs (ex: 'R5_C19') que cobrem o bbox."""
    minx, miny, maxx, maxy = bbox
    tile_size = 1_000_000
    origin_x, origin_y = -41_000, 0

    transformer = Transformer.from_crs(
        CRS.from_epsg(4326), CRS.from_string("ESRI:54009")
    )
    corners = [(miny, minx), (miny, maxx), (maxy, minx), (maxy, maxx)]
    coords = [transformer.transform(lat, lon) for lat, lon in corners]

    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]

    min_col = int((min(xs) - origin_x) // tile_size) + 19
    max_col = int((max(xs) - origin_x) // tile_size) + 19
    min_row = int((origin_y - max(ys)) // tile_size) + 10
    max_row = int((origin_y - min(ys)) // tile_size) + 10

    return [
        f"R{r}_C{c}"
        for r in range(min_row, max_row + 1)
        for c in range(min_col, max_col + 1)
    ]


def test_country_tiles_urls(
    country_name: str, dataset: str = "population"
) -> List[str]:
    """Exibe as URLs dos tiles para um país/dataset sem baixar."""
    cfg = _get_dataset_config(dataset)
    year = cfg["default_year"]

    print(f"Testando tiles: {country_name} / {dataset}")
    print("-" * 50)
    bbox = get_country_bounds(country_name)
    tiles = get_required_tiles(bbox)
    print(f"Bounding box : {bbox}")
    print(f"Total de tiles: {len(tiles)}\n")
    for i, tile in enumerate(tiles, 1):
        print(f"{i:2d}. {tile} -> {_build_tile_url(cfg, year, tile)}")
    return tiles


def download_ghsl_country(
    country_name: str,
    output_dir: str = "ghsl_data",
    year: int = None,
    dataset: str = "population",
) -> str:
    """Baixa tiles GHSL para um país."""
    cfg = _get_dataset_config(dataset)
    year = year or cfg["default_year"]
    os.makedirs(output_dir, exist_ok=True)

    tiles = get_required_tiles(get_country_bounds(country_name))
    print(f"[{dataset}] Baixando {len(tiles)} tiles — {country_name} ({year})...")

    downloaded = 0
    for i, tile in enumerate(tiles, 1):
        url = _build_tile_url(cfg, year, tile)
        output_file = os.path.join(output_dir, f"{tile}.tif")
        if os.path.exists(output_file):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            print(f"  {i}/{len(tiles)} JÁ EXISTE ({size_mb:.1f} MB), pulando — {tile}")
            downloaded += 1
            continue
        try:
            r = requests.get(url, timeout=300)
            r.raise_for_status()
            with ZipFile(BytesIO(r.content)) as z:
                tifs = [f for f in z.namelist() if f.endswith(".tif")]
                if tifs:
                    z.extract(tifs[0], output_dir)
                    os.rename(os.path.join(output_dir, tifs[0]), output_file)
                    downloaded += 1
            print(f"  {i}/{len(tiles)} OK")
        except Exception as e:
            print(f"  {i}/{len(tiles)} ERRO: {e}")

    print(f"Concluído: {downloaded}/{len(tiles)} arquivos")
    return output_dir


def crop_tiles_to_country(input_dir: str, country_name: str) -> List[str]:
    """Recorta tiles ao contorno do país + buffer de 10% da maior dimensão."""
    country_geom = get_country_geometry(country_name)

    minx, miny, maxx, maxy = country_geom.bounds
    buffer_km = max(maxx - minx, maxy - miny) * 111 * 0.1

    country_mol = gpd.GeoDataFrame(
        [1], geometry=[country_geom], crs="EPSG:4326"
    ).to_crs("ESRI:54009")
    buffered = country_mol.geometry.buffer(buffer_km * 1000).iloc[0]
    buffered_gs = gpd.GeoSeries([buffered], crs="ESRI:54009")

    tif_files = [
        f
        for f in os.listdir(input_dir)
        if f.endswith(".tif") and not f.startswith("redux_")
    ]
    if not tif_files:
        return []

    print(f"Recortando {len(tif_files)} tiles (buffer {buffer_km:.1f} km)...")
    cropped = []

    for tif_file in tif_files:
        input_path = os.path.join(input_dir, tif_file)
        output_path = os.path.join(input_dir, f"redux_{tif_file}")
        try:
            with rasterio.open(input_path) as src:
                tile_gs = gpd.GeoSeries([box(*src.bounds)], crs=src.crs).to_crs(
                    "ESRI:54009"
                )
                if not tile_gs.intersects(buffered_gs).any():
                    continue

                out_image, out_transform = mask(
                    src,
                    [buffered],
                    crop=True,
                    all_touched=True,
                    filled=True,
                    nodata=src.nodata,
                )
                if out_image.size == 0 or np.all(out_image == src.nodata):
                    continue

                out_meta = src.meta.copy()
                out_meta.update(
                    {
                        "height": out_image.shape[1],
                        "width": out_image.shape[2],
                        "transform": out_transform,
                    }
                )
                with rasterio.open(output_path, "w", **out_meta) as dst:
                    dst.write(out_image)
                cropped.append(output_path)
        except Exception as e:
            print(f"  ERRO em {tif_file}: {e}")

    print(f"Recortados: {len(cropped)} arquivos")
    return cropped


def cleanup_files(input_dir: str, prefixes: List[str]) -> None:
    """Remove arquivos .tif temporários pelos prefixos informados."""
    for prefix in prefixes:
        for f in os.listdir(input_dir):
            if f.startswith(prefix) and f.endswith(".tif"):
                try:
                    os.remove(os.path.join(input_dir, f))
                except Exception:
                    pass


def tif_to_points_gpkg(
    input_dir: str,
    input_prefix: str = "redux_",
    target_crs: str = "EPSG:4326",
) -> List[str]:
    """Converte TIFFs em GeoPackages de pontos."""
    tif_files = [
        f
        for f in os.listdir(input_dir)
        if f.startswith(input_prefix) and f.endswith(".tif")
    ]
    if not tif_files:
        return []

    gpkg_files = []
    for tif_file in tif_files:
        input_path = os.path.join(input_dir, tif_file)
        output_path = os.path.join(input_dir, tif_file.replace(".tif", "_points.gpkg"))
        try:
            with rasterio.open(input_path) as src:
                data = src.read(1)
                nodata = src.nodata
                valid = (
                    (data != nodata) & (data > 0) if nodata is not None else data > 0
                )
                rows, cols = np.where(valid)
                if len(rows) == 0:
                    continue

                xs, ys = xy(src.transform, rows, cols)
                gdf = gpd.GeoDataFrame(
                    {"value": data[rows, cols]},
                    geometry=[Point(x, y) for x, y in zip(xs, ys)],
                    crs=src.crs,
                )
                if str(src.crs) != target_crs:
                    gdf = gdf.to_crs(target_crs)

                gdf.to_file(output_path, driver="GPKG")
                gpkg_files.append(output_path)
        except Exception as e:
            print(f"  ERRO em {tif_file}: {e}")

    return gpkg_files


def create_unified_points_gpkg(
    input_dir: str,
    country_name: str,
    input_prefix: str = "redux_",
    cleanup_individual: bool = True,
    dataset: str = "population",
) -> str:
    """Unifica GeoPackages individuais em um único arquivo."""
    print(f"Unificando pontos — {country_name} / {dataset}...")

    gpkg_files = tif_to_points_gpkg(input_dir, input_prefix)
    if not gpkg_files:
        print("Nenhum arquivo válido encontrado.")
        return None
    cfg = _get_dataset_config(dataset)

    short = cfg["short_name"]
    name = country_name.lower().replace(" ", "_")
    unified_path = os.path.join(input_dir, f"{name}_{short}_points.gpkg")

    try:
        if len(gpkg_files) == 1:
            os.rename(gpkg_files[0], unified_path)
        else:
            gdfs = []
            for f in gpkg_files:
                gdf = gpd.read_file(f)
                gdf["tile"] = os.path.basename(f).replace("_points.gpkg", "")
                gdfs.append(gdf)
            gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True)).to_file(
                unified_path, driver="GPKG"
            )

            if cleanup_individual:
                for f in gpkg_files:
                    try:
                        os.remove(f)
                    except Exception:
                        pass

        final = gpd.read_file(unified_path)
        size = os.path.getsize(unified_path) / (1024 * 1024)
        print(f"Concluído: {len(final):,} pontos, {size:.1f} MB → {unified_path}")
        return unified_path

    except Exception as e:
        print(f"Erro ao unificar: {e}")
        return None


def save_csv_from_gpkg(folder: str, country: str, dataset: str = "population") -> None:
    """Salva pontos do GeoPackage como CSV (lon, lat, valor)."""
    cfg = _get_dataset_config(dataset)
    short = cfg["short_name"]
    col = cfg["column_name"]
    name = country.replace(" ", "_").lower()

    gpkg_path = os.path.join(folder, f"{name}_{short}_points.gpkg")
    if not os.path.exists(gpkg_path):
        print(f"ERRO: arquivo não encontrado — {gpkg_path}")
        print("Execute process_country_complete() antes de save_csv_from_gpkg().")
        return
    gdf = gpd.read_file(gpkg_path)[["geometry", "value"]]
    gdf = gdf.rename(columns={"value": col})
    gdf["lon"] = gdf.geometry.x
    gdf["lat"] = gdf.geometry.y
    gdf = gdf[["lon", "lat", col]]

    csv_path = os.path.join(folder, f"{name}_{short}.csv")
    if os.path.exists(csv_path):
        print(f"CSV já existe, pulando: {csv_path}")
        return
    gdf.to_csv(csv_path, index=False)
    print(f"CSV salvo: {csv_path} ({len(gdf):,} linhas)")


def process_country_complete(
    country_name: str,
    output_dir: str = None,
    cleanup: bool = True,
    year: int = None,
    dataset: str = "population",
) -> str:
    """
    Pipeline completo para um país: download → recorte → GeoPackage de pontos.

    Parâmetros
    ----------
    country_name : str
        Nome do país em inglês (ex: "Norway", "Germany").
    output_dir : str, optional
        Pasta de saída. Se None, usa 'data/{country_name}'.
    cleanup : bool, default True
        Remove arquivos TIF temporários após processamento.
    year : int, optional
        Ano dos dados. Se None, usa o ano padrão do dataset.
    dataset : str, default "population"
        Dataset GHSL a baixar. Opções:
        - "population"           → GHS-POP (100m)
        - "built_surface"        → GHS-BUILT-S (100m)
        - "built_volume"         → GHS-BUILT-V (100m)
        - "settlement_model"     → GHS-SMOD (1000m)
        - "built_characteristics"→ GHS-BUILT-C (10m) ⚠️ arquivos muito grandes

    Retorna
    -------
    str
        Caminho para o GeoPackage unificado de pontos.

    Exemplo
    -------
    >>> process_country_complete("Norway", "data/norway", dataset="population")
    >>> process_country_complete("Monaco", dataset="built_surface", year=2020)
    """
    cfg = _get_dataset_config(dataset)
    year = year or cfg["default_year"]
    short = cfg["short_name"]

    if not output_dir:
        output_dir = f"data/{country_name.lower().replace(' ', '_')}"
    os.makedirs(output_dir, exist_ok=True)

    cfg = _get_dataset_config(dataset)
    name = country_name.lower().replace(" ", "_")
    gpkg_path = os.path.join(output_dir, f"{name}_{short}_points.gpkg")

    print(f"=== [{dataset}] {country_name} ===")

    if os.path.exists(gpkg_path):
        size_mb = os.path.getsize(gpkg_path) / (1024 * 1024)
        print(
            f"GeoPackage já existe ({size_mb:.1f} MB), pulando para CSV — {gpkg_path}"
        )
        return gpkg_path

    download_ghsl_country(country_name, output_dir, year, dataset)
    crop_tiles_to_country(output_dir, country_name)
    unified_gpkg = create_unified_points_gpkg(
        output_dir,
        country_name,
        input_prefix="redux_",
        cleanup_individual=True,
        dataset=dataset,
    )
    if cleanup:
        cleanup_files(output_dir, ["R", "redux_"])

    print(f"=== Concluído: {country_name} / {dataset} ===")
    return unified_gpkg


def merge_country_tiles(
    input_dir: str, country_name: str, output_file: str = None
) -> str:
    """Mescla tiles raster em um único arquivo."""
    if not output_file:
        output_file = f"{country_name.lower().replace(' ', '_')}_merged.tif"

    tif_files = [
        os.path.join(input_dir, f) for f in os.listdir(input_dir) if f.endswith(".tif")
    ]
    if not tif_files:
        raise ValueError("Nenhum arquivo TIF encontrado")

    datasets = [rasterio.open(f) for f in tif_files]
    mosaic, out_trans = merge(datasets)
    for ds in datasets:
        ds.close()

    country_gdf = gpd.GeoDataFrame(
        [1], geometry=[get_country_geometry(country_name)], crs="EPSG:4326"
    ).to_crs("ESRI:54009")

    with rasterio.open(tif_files[0]) as src:
        out_image, out_transform = mask(
            src, country_gdf.geometry.values, crop=True, all_touched=True
        )
        profile = src.profile.copy()

    profile.update(
        {
            "height": out_image.shape[1],
            "width": out_image.shape[2],
            "transform": out_transform,
        }
    )
    with rasterio.open(output_file, "w", **profile) as dst:
        dst.write(out_image)

    return output_file


### CITY FUNCTIONS


def get_city_geometry(city_name: str):
    """
    Retorna a geometria Shapely de uma cidade via Nominatim (polygon direto).

    Queries Nominatim with polygon_geojson=1 to get the actual boundary
    polygon (not a bounding box). Tries up to 3 query variants — full name,
    city+country (dropping state), then city alone — to handle cases where
    Nominatim's top-50 results don't include the municipality in the first
    query.
    """
    parts = [p.strip() for p in city_name.split(",")]
    variants = [city_name]
    if len(parts) >= 3:
        variants.append(f"{parts[0]}, {parts[-1]}")   # city + country
    if len(parts) >= 2:
        variants.append(parts[0])                      # city name only

    for idx, variant in enumerate(variants):
        if idx > 0:
            time.sleep(1)  # Nominatim fair-use: 1 req/s
        try:
            resp = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": variant,
                    "format": "geojson",
                    "limit": 50,
                    "polygon_geojson": 1,
                },
                headers={"User-Agent": "15min-city-research/1.0"},
                timeout=60,
            )
            resp.raise_for_status()
            features = resp.json().get("features", [])
        except Exception:
            continue

        for feat in features:
            geom_json = feat.get("geometry", {})
            if geom_json.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            geom = shape(geom_json)
            area_km2 = (
                gpd.GeoDataFrame({"geometry": [geom]}, crs="EPSG:4326")
                .to_crs("EPSG:3857")
                .area.iloc[0] / 1e6
            )
            if area_km2 > 1:
                return geom

    raise ValueError(
        f"Nominatim returned no polygon boundary (area > 1 km²) for '{city_name}'. "
        "Tried full name, city+country, and city-only queries. "
        "Check that the city exists in OSM Nominatim."
    )


def crop_tiles_to_geometry(
    input_dir: str, geom, buffer_km: float = None, output_dir: str = None
) -> List[str]:
    """
    Recorta tiles a uma geometria arbitrária + buffer.
    Substitui crop_tiles_to_country() — funciona para países e cidades.

    Parâmetros
    ----------
    geom : Shapely geometry (WGS84)
    buffer_km : float, optional
        Se None, calcula 10% da maior dimensão do bbox.
    output_dir : str, optional
        Pasta onde os ficheiros redux_ são escritos. Se None, usa input_dir.
    """
    if buffer_km is None:
        minx, miny, maxx, maxy = geom.bounds
        buffer_km = max(maxx - minx, maxy - miny) * 111 * 0.1

    out_dir = output_dir or input_dir
    os.makedirs(out_dir, exist_ok=True)

    geom_mol = gpd.GeoDataFrame([1], geometry=[geom], crs="EPSG:4326").to_crs(
        "ESRI:54009"
    )
    buffered = geom_mol.geometry.buffer(buffer_km * 1000).iloc[0]
    buffered_gs = gpd.GeoSeries([buffered], crs="ESRI:54009")

    tif_files = [
        f
        for f in os.listdir(input_dir)
        if f.endswith(".tif") and not f.startswith("redux_")
    ]
    if not tif_files:
        return []

    print(f"Recortando {len(tif_files)} tiles (buffer {buffer_km:.1f} km)...")
    cropped = []

    for tif_file in tif_files:
        input_path = os.path.join(input_dir, tif_file)
        output_path = os.path.join(out_dir, f"redux_{tif_file}")
        try:
            with rasterio.open(input_path) as src:
                tile_gs = gpd.GeoSeries([box(*src.bounds)], crs=src.crs).to_crs(
                    "ESRI:54009"
                )
                if not tile_gs.intersects(buffered_gs).any():
                    continue
                out_image, out_transform = mask(
                    src,
                    [buffered],
                    crop=True,
                    all_touched=True,
                    filled=True,
                    nodata=src.nodata,
                )
                if out_image.size == 0 or np.all(out_image == src.nodata):
                    continue
                out_meta = src.meta.copy()
                out_meta.update(
                    {
                        "height": out_image.shape[1],
                        "width": out_image.shape[2],
                        "transform": out_transform,
                    }
                )
                with rasterio.open(output_path, "w", **out_meta) as dst:
                    dst.write(out_image)
                cropped.append(output_path)
        except Exception as e:
            print(f"  ERRO em {tif_file}: {e}")

    print(f"Recortados: {len(cropped)} arquivos")
    return cropped


def process_city_complete(
    city_name: str,
    output_dir: str = None,
    cleanup: bool = True,
    year: int = None,
    dataset: str = "population",
    buffer_km: float = None,
    ghsl_cache_dir: str = None,
    city_geom=None,
) -> str:
    """
    Pipeline completo para uma cidade: download → recorte → GeoPackage de pontos.

    Parâmetros
    ----------
    city_name : str
        Nome da cidade reconhecido pelo OSM (ex: "Fortaleza, Brazil").
    output_dir : str, optional
        Pasta de saída. Se None, usa 'data/{city_name}'.
    cleanup : bool, default True
        Remove arquivos TIF temporários após processamento.
    year : int, optional
        Ano dos dados. Se None, usa o ano padrão do dataset.
    dataset : str, default "population"
        Dataset GHSL a baixar.
        Dataset GHSL a baixar. Opções:
        - "population"           → GHS-POP (100m)
        - "built_surface"        → GHS-BUILT-S (100m)
        - "built_volume"         → GHS-BUILT-V (100m)
        - "settlement_model"     → GHS-SMOD (1000m)
        - "built_characteristics"→ GHS-BUILT-C (10m) ⚠️ arquivos muito grandes

    buffer_km : float, optional
        Buffer em km. Se None, calcula 10% da maior dimensão do bbox.

    Retorna
    -------
    str
        Caminho para o GeoPackage unificado de pontos.

    Exemplo
    -------
    >>> process_city_complete("Fortaleza, Brazil", "data/fortaleza", dataset="population")
    >>> process_city_complete("Oslo, Norway", dataset="built_surface")
    """
    cfg = _get_dataset_config(dataset)
    year = year or cfg["default_year"]
    short = cfg["short_name"]
    name = city_name.lower().replace(" ", "_").replace(",", "")

    if not output_dir:
        output_dir = f"data/{name}"
    os.makedirs(output_dir, exist_ok=True)

    gpkg_path = os.path.join(output_dir, f"{name}_{short}_points.gpkg")

    print(f"=== [{dataset}] {city_name} ===")

    if os.path.exists(gpkg_path):
        size_mb = os.path.getsize(gpkg_path) / (1024 * 1024)
        print(f"GeoPackage já existe ({size_mb:.1f} MB), pulando — {gpkg_path}")
        return gpkg_path

    # Use pre-computed geometry if provided (avoids a second Nominatim request)
    if city_geom is None:
        city_geom = get_city_geometry(city_name)
    bbox = city_geom.bounds  # (minx, miny, maxx, maxy)

    # Raw tiles go to shared cache (if provided) or city output dir
    tile_dir = ghsl_cache_dir if ghsl_cache_dir else output_dir
    if ghsl_cache_dir:
        os.makedirs(ghsl_cache_dir, exist_ok=True)

    tiles = get_required_tiles(bbox)
    print(f"[{dataset}] Baixando {len(tiles)} tiles — {city_name} ({year})...")
    downloaded = 0
    for i, tile in enumerate(tiles, 1):
        url = _build_tile_url(cfg, year, tile)
        output_file = os.path.join(tile_dir, f"{tile}.tif")
        if os.path.exists(output_file):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            print(f"  {i}/{len(tiles)} JÁ EXISTE ({size_mb:.1f} MB), pulando — {tile}")
            downloaded += 1
            continue
        try:
            zip_path = output_file + ".zip"
            with requests.get(url, stream=True, timeout=600) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                done = 0
                with open(zip_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=4 * 1024 * 1024):
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            print(f"  {i}/{len(tiles)} {done/1e6:.0f}/{total/1e6:.0f} MB", end="\r")
            with ZipFile(zip_path) as z:
                tifs = [f for f in z.namelist() if f.endswith(".tif")]
                if tifs:
                    z.extract(tifs[0], tile_dir)
                    os.rename(os.path.join(tile_dir, tifs[0]), output_file)
                    downloaded += 1
            os.remove(zip_path)
            print(f"  {i}/{len(tiles)} OK                    ")
        except Exception as e:
            print(f"  {i}/{len(tiles)} ERRO: {e}")
    print(f"Concluído: {downloaded}/{len(tiles)} arquivos")

    # Crop from tile_dir into output_dir (redux_ files go to city folder)
    crop_tiles_to_geometry(tile_dir, city_geom, buffer_km, output_dir=output_dir)

    unified_gpkg = create_unified_points_gpkg(
        output_dir,
        name,
        input_prefix="redux_",
        cleanup_individual=True,
        dataset=dataset,
    )
    # Only clean up redux_ cropped files; never delete shared tile cache
    if cleanup:
        cleanup_files(output_dir, ["redux_"])

    print(f"=== Concluído: {city_name} / {dataset} ===")
    return unified_gpkg


def test_city_tiles_urls(city_name: str, dataset: str = "population") -> List[str]:
    """Exibe as URLs dos tiles para uma cidade/dataset sem baixar."""
    cfg = _get_dataset_config(dataset)
    year = cfg["default_year"]

    print(f"Testando tiles: {city_name} / {dataset}")
    print("-" * 50)
    bbox = get_city_geometry(city_name).bounds
    tiles = get_required_tiles(bbox)
    print(f"Bounding box : {bbox}")
    print(f"Total de tiles: {len(tiles)}\n")
    for i, tile in enumerate(tiles, 1):
        print(f"{i:2d}. {tile} -> {_build_tile_url(cfg, year, tile)}")
    return tiles
