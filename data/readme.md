
# Dados Originais Brutos

Este diretório contém os conjuntos de dados originais do projeto **15minCity**, organizados por país.
O objetivo desta pasta é centralizar as fontes primárias que servem de base para as análises espaciais subsequentes.

## Conteúdo

Os dados estão estruturados no formato **GeoPackage (`.gpkg`)**. Cada ficheiro contém:

* **Cidades:** Lista das áreas urbanas selecionadas para o estudo.
* **Geometrias:** Delimitações vetoriais (polígonos) das cidades e regiões de interesse.
* **Atributos:** Dados tabulares associados a cada geometria necessários para a análise.

## Estrutura de Ficheiros

A organização segue a nomenclatura de códigos de país (ex: ISO 3166-1 alpha-3):

```text
data/
├── BR/
│   └── cidades.gpkg
├── US/
│   └── cidades.gpkg
├── OUTROS PAISES
│   └── cidades.gpkg
