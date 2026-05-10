# Estrutura dos ficheiros GPKG em `data/`

Este documento define o esquema obrigatório para os ficheiros GeoPackage (`.gpkg`) usados como fonte de limites administrativos das cidades no pipeline.

---

## Estrutura de pastas

```
data/
├── US/
│   └── cities.gpkg
├── BR/
│   └── cities.gpkg
├── EU/
│   └── cities.gpkg
└── CH/
    └── cities.gpkg
```

Cada pasta corresponde a um código de país/região configurável em `config.yaml` (`city_source.country`). O ficheiro dentro deve chamar-se sempre `cities.gpkg`.

---

## Colunas obrigatórias

| Coluna       | Tipo        | Descrição                                                                 |
|--------------|-------------|---------------------------------------------------------------------------|
| `GEOID`      | string      | Identificador único da cidade dentro do país. Usado como chave de pasta e ficheiro de output. Não pode ter duplicados. |
| `NAME`       | string      | Nome da cidade para display, logging e nome de pasta de output.           |
| `population` | float / int | População total estimada. Pode ser `null` se não disponível.              |
| `geometry`   | geometry    | Limite administrativo da cidade. **Obrigatório.** Ver requisitos abaixo.  |

Colunas adicionais (ex: código de estado, código NUTS, fonte de dados) são ignoradas pelo pipeline mas podem estar presentes.

---

## Requisitos de geometria

- **Tipo:** `Polygon` ou `MultiPolygon`
- **CRS:** `EPSG:4326` (WGS 84, graus decimais)
- **Área mínima:** geometrias com menos de 1 km² são rejeitadas pelo pipeline (ponto ou polígono degenerado)
- **Área máxima:** geometrias com mais de 5 000 km² são rejeitadas (região ou país em vez de cidade)
- Geometrias inválidas ou nulas devem ser removidas antes de guardar o ficheiro

---

## Unicidade de `GEOID`

O campo `GEOID` é usado para gerar o nome da pasta de output de cada cidade (`{COUNTRY}_{NAME}_{GEOID}`). Deve ser único dentro do ficheiro. Em caso de cidades com o mesmo `NAME` (ex: dois "Springfield" em estados diferentes), o `GEOID` garante que os outputs não colidem.

---

## Exemplo mínimo (EUA)

| GEOID   | NAME      | population | geometry          |
|---------|-----------|------------|-------------------|
| 4159000 | Portland  | 652503     | MULTIPOLYGON(...) |
| 5363000 | Seattle   | 737255     | MULTIPOLYGON(...) |
| 0667000 | San Jose  | 1013240    | MULTIPOLYGON(...) |

---

## Como preparar um ficheiro para um novo país

1. Obter os limites administrativos na fonte de dados relevante (ex: TIGER para EUA, IBGE para Brasil, GADM para outros)
2. Filtrar apenas cidades com população acima do limiar desejado
3. Garantir que as colunas `GEOID`, `NAME`, `population` e `geometry` estão presentes com os tipos corretos
4. Reprojectar para `EPSG:4326` se necessário
5. Remover geometrias nulas ou inválidas
6. Guardar como `cities.gpkg` na pasta `data/{COUNTRY_CODE}/`

```python
import geopandas as gpd

gdf = gpd.read_file("fonte_original.gpkg")

# Renomear colunas para o esquema obrigatório
gdf = gdf.rename(columns={
    "cod_municipio": "GEOID",
    "nome":          "NAME",
    "pop_total":     "population",
})

# Manter só as colunas necessárias (+ geometry é automático)
gdf = gdf[["GEOID", "NAME", "population", "geometry"]]

# Garantir CRS correto e geometrias válidas
gdf = gdf.to_crs("EPSG:4326")
gdf = gdf[gdf.geometry.notnull() & gdf.is_valid]
gdf["GEOID"] = gdf["GEOID"].astype(str)

gdf.to_file("data/BR/cities.gpkg", driver="GPKG")
```
