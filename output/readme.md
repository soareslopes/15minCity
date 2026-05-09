# Outputs

Este diretório contém todos os dados e visualizações gerados automaticamente pelo pipeline **Urban Accessibility**. A estrutura está organizada para separar resultados consolidados, dados geoespaciais por cidade, ficheiros de processamento intermédio (cache) e análises comparativas.

## Estrutura de Conteúdo

### 1. Resultados Tabulares (`/output/`)
* **`results_final.csv`**: Tabela consolidada com os indicadores finais de acessibilidade e métricas de desigualdade para todas as cidades processadas.
* **`results_partial.csv`**: Backup dos resultados durante o processamento (útil em execuções de longa duração).

### 2. Dados Geoespaciais (`/output/gpkg/`)
Contém ficheiros **GeoPackage** com a grelha hexagonal (H3) final para cada cidade. Estes ficheiros são ideais para análise em SIG (QGIS/ArcGIS).
* *Camadas incluídas:* Geometria hexagonal, contagem de destinos por categoria, tempos de viagem (Dijkstra/Pandana) e dados populacionais (GHSL).

### 3. Repositório por Cidade (`/output/cities/[ID_CIDADE]/`)
Pasta individualizada para cada cidade processada, contendo:
* **`network.graphml`**: Grafo da rede pedonal extraído do OSM.
* **`[ID].osm.pbf`**: Extrato local do OpenStreetMap utilizado na análise.
* **`boundary.gpkg`**: Polígono do limite administrativo/urbano da cidade.
* **`map_total_dest_30min.png`**: Mapa estático de visualização rápida da acessibilidade.
* **`ghsl/`**: Recortes locais dos dados de densidade populacional.

### 4. Análises Comparativas (`/output/figures/`)
Gráficos gerados no Passo 9 (quando 2 ou mais cidades são processadas) para permitir o benchmarking urbano:
* **`density_vs_accessibility.png`**: Correlação entre densidade urbana e tempos de acesso.
* **`inequity_distributions.png`**: Distribuição estatística das desigualdades.
* **`gini_vs_density.png`**: Análise do Índice de Gini face à densidade populacional.

### 5. Cache de Dados (`/output/cache/`)
Armazenamento de ficheiros pesados para evitar downloads redundantes e permitir execução offline:
* **`pbf_cache/`**: Ficheiros PBF regionais (Geofabrik).
* **`ghsl_cache/`**: Tiles globais do Global Human Settlement Layer (população).

---

## Observações Técnicas
* **Sistema de Referência:** Os outputs geoespaciais utilizam coordenadas geográficas (WGS84), exceto quando cálculos de área locais são exigidos pelo pipeline.
* **Reprocessamento:** Para atualizar os ficheiros desta pasta, utilize a flag `--rerun` ao executar o `main.py`.
* **Privacidade e Licença:** Os dados no `pbf_cache` derivam do OpenStreetMap e estão sujeitos à licença [ODbL](https://www.openstreetmap.org/copyright).

---
*Baseado no projeto original Cidade15min (David Vale / André Lopes, CITTA / Universidade de Lisboa).*
