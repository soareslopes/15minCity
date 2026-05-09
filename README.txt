15-Min City — Pipeline de Acessibilidade Urbana
================================================

Pipeline generalizado para calcular acessibilidade pedonal em qualquer lista
de cidades, a partir do nome.
Baseado no projecto original Cidade15min (David Vale / André Lopes, CITTA/ULisboa).


O QUE FAZ
----------

Para cada cidade da lista:

  1. Geometria        — busca o polígono da cidade no OpenStreetMap (via OSMnx / Nominatim)
  2. Grid H3          — divide a cidade em hexágonos (~160 m de diâmetro, resolução 10)
  3. Rede pedonal     — descarrega a rede viária pedonal do OSM (OSMnx + Overpass API)
                        e constrói um grafo NetworkX
  4. POIs             — descarrega pontos de interesse do OSM, classifica por tipo
                        usando Key_Value_DestType.csv
  5. Acessibilidade   — para cada hexágono, conta quantos destinos de cada tipo
                        estão acessíveis a pé em 15, 30, 45, 60, 75 e 90 minutos
  6. Indicadores      — Variety (nº de tipos presentes), Total_Dest (total de destinos),
                        Entropy (diversidade de Shannon), Total_Dest_01 (normalizado)
  7. População        — descarrega o tile GHS-POP, recorta ao polígono e estima
                        a população por hexágono
  8. Inequidade       — Gini territorial, Gini ponderado por pop, Palma, Theil T,
                        CV, Moran's I — para cada indicador × intervalo de tempo
  9. Output           — salva um .gpkg por cidade e agrega em results_final.csv
 10. Análise          — gráficos de retornos decrescentes e distribuições de inequidade


MOTOR DE ACESSIBILIDADE
-----------------------

Usa NetworkX com rede pedonal OSM. Não usa Pandana, OpenTripPlanner nem R5Py.

Estratégia POI-forward Dijkstra:

  1. Para cada nó OSM único onde existe um POI, corre single_source_dijkstra_path_length
     uma única vez com o cutoff máximo (90 min × velocidade). Os resultados ficam numa
     matriz NumPy float32 de forma (n_POI_únicos × n_hexágonos).

  2. Para cada limiar de tempo (15, 30, … 90 min), aplica-se um simples threshold
     na matriz — sem nova travessia do grafo.

  3. Para cada tipo de POI e limiar, conta-se o número de POIs acessíveis por
     hexágono com uma soma vectorizada NumPy.

  Cada nó POI é percorrido uma única vez; todos os intervalos e todos os tipos
  de destino saem desta passagem.

Filtro de aceitação: hexágonos cujo centróide está a mais de 120 m do nó de rede
mais próximo são excluídos (parques, água, zonas sem rede).


ESTRUTURA DE FICHEIROS
----------------------

  main.py                          <- PONTO DE ENTRADA — correr este
  config.yaml                      <- toda a configuração aqui
  cities.csv                       <- lista de cidades (editar)

  city_pipeline.py                 <- orquestrador do pipeline por cidade
                                      (chama os módulos de passo abaixo)

  — Módulos de passo (por ordem de execução) —
  step2_grid.py                    <- Passo 2: grid H3 hexagonal
  step3_osm_city.py                <- Passos 3+4: rede pedonal + POIs via OSMnx
  step5_accessibility.py           <- Passo 5: acessibilidade (Dijkstra, NetworkX + NumPy)
  step5b_accessibility_pandana.py  <- Passo 5 (alternativa Pandana)
  step6_ghsl.py                    <- Passos 1+6: geometria da cidade + população GHSL
  step7_urban_area.py              <- Passo 7: mancha urbana (opcional, método "continuous")
  step8_inequity.py                <- Passo 8: Gini, Palma, Theil, CV, Moran's I
  step9_analysis.py                <- Passo 9: gráficos e análise final

  — Utilitários —
  util_geofabrik.py                <- download e clip de ficheiros PBF Geofabrik
  util_gini.py                     <- cálculo do índice de Gini
  util_nearest.py                  <- join KD-tree de vizinho mais próximo

  Key_Value_DestType.csv           <- (copiar do projecto original)

  output/                 <- gerado automaticamente
    cities/
      fortaleza_brazil.gpkg
      lisboa_portugal.gpkg
    figures/
      diminishing_returns.png
      density_vs_accessibility.png
      inequity_distributions.png
      gini_vs_density.png
    results_final.csv
    results_partial.csv


INPUTS NECESSÁRIOS
------------------

  Ficheiro                Formato          Descrição
  --------                -------          ---------
  cities.csv              CSV              Uma linha por cidade, coluna city_name
  Key_Value_DestType.csv  CSV (sep=;)      Classificação OSM -> tipo de destino
  config.yaml             YAML             Caminhos e parâmetros

Formato de cities.csv:

  city_name
  Fortaleza, Brazil
  Lisboa, Portugal
  Oslo, Norway
  Buenos Aires, Argentina

  O nome deve ser reconhecível pelo Nominatim/OSM. Se a pesquisa falhar,
  adicionar o país: "Porto, Portugal" em vez de "Porto".

Onde obter Key_Value_DestType.csv:
  Está no projecto original em 00_OSMClasses/Key_Value_DestType.csv.
  Copiar para esta pasta.


CONFIGURAÇÃO (config.yaml)
--------------------------

  paths:
    cities_csv: "cities.csv"
    output_dir: "output"
    tags_csv: "Key_Value_DestType.csv"

  osm:
    bbox_buffer_m: 1000             # buffer (m) à volta do polígono da cidade

  grid:
    h3_level: 10                    # resolução H3 (~160 m de diâmetro)
    hex_acceptance_dist: 120        # dist. máx. centróide -> nó (metros)

  accessibility:
    time_intervals_min: [15, 30, 45, 60, 75, 90]  # intervalos de tempo (minutos)
    walk_speed_ms: 1.2              # velocidade pedonal (m/s; 1.2 ≈ 4.3 km/h)

  ghsl:
    dataset: "population"           # ou built_surface, settlement_model
    year: 2025

  inequality:
    pop_column: "pop_estimada"


COMO CORRER
-----------

1. Instalar dependências:

   pip install geopandas osmnx networkx h3 rasterio \
               requests pyproj shapely pyyaml scipy matplotlib \
               libpysal esda

   Nota: libpysal e esda são opcionais (Moran's I). Se não instalados,
   essa medida fica NaN e o resto corre normalmente.

2. Preparar inputs:

   cp ../00_OSMClasses/Key_Value_DestType.csv .
   # editar cities.csv com as cidades pretendidas

3. Correr:

   python main.py

   Opções:
     python main.py --config config.yaml    usar config alternativo
     python main.py --no-analysis           saltar gráficos


OUTPUTS
-------

output/results_final.csv — uma linha por cidade. Colunas principais:

  city_name               Nome da cidade
  status                  success ou error_*
  n_hexagons              Nº de hexágonos com rede válida
  total_pop               População total estimada (GHSL)
  pop_density_ha          Densidade populacional (hab/ha)
  mean_variety_15min      Média de Variety por hexágono a 15 min
  mean_variety_30min      Idem a 30 min
  ...                     (idem para 45, 60, 75, 90 min)
  mean_total_dest_15min   Média de Total_Dest a 15 min
  mean_entropy_15min      Média de Entropy a 15 min
  mean_education_15min    Média de destinos de Educação acessíveis a 15 min
  mean_healthcare_15min   Idem para Saúde
  ...                     (idem para cada tipo de destino x cada intervalo)
  gini_variety_15min      Gini territorial para Variety a 15 min
  gini_pop_variety_15min  Gini ponderado por população
  palma_variety_15min     Palma ratio
  theil_variety_15min     Theil T index
  cv_variety_15min        Coeficiente de variação
  moran_i_variety_15min   Moran's I (autocorrelação espacial)
  ...                     (idem para cada métrica x cada intervalo)
  runtime_s               Tempo de processamento (segundos)

output/cities/{city}.gpkg
  Grid H3 com todos os atributos por hexágono. Abrir no QGIS directamente.
  Colunas incluem:
    {tipo}_{t}min           contagem de POIs acessíveis em t minutos
    variety_{t}min          número de tipos com count > 0
    total_dest_{t}min       total de POIs acessíveis
    entropy_{t}min          entropia de Shannon
    total_dest_01_{t}min    total_dest normalizado (0-1)
    pop_estimada            população estimada GHSL por hexágono

output/figures/
  diminishing_returns.png       Box plots de Variety/Total_Dest/Entropy por intervalo
  density_vs_accessibility.png  Scatter pop. density vs acessibilidade (ref. 15 min)
  inequity_distributions.png    Histogramas de Gini/Palma/Theil/CV por métrica
  gini_vs_density.png           Scatter Gini vs pop. density


NOTAS E LIMITAÇÕES
------------------

  - Cidades muito grandes (São Paulo, Beijing): download de POIs via Overpass
    pode ser lento ou fazer timeout. Aumentar osm.bbox_buffer_m.

  - Cidades fora do OSM: se o Nominatim não encontrar o polígono, o pipeline
    regista error_geometry e passa à próxima cidade.

  - Memória: a matriz de distâncias é float32 (n_POI_únicos × n_hexágonos).
    Para cidades grandes (~50 000 hexágonos, ~10 000 POIs únicos) ocupa ~2 GB RAM.

  - GHSL tiles: cada tile tem ~500 MB-1 GB. TIF apagados após processamento.
    Apenas o .gpkg de pontos é mantido por cidade.

  - Cidades no limiar entre tiles GHSL: descarregados automaticamente todos
    os tiles que intersectam o bbox da cidade (pode ser 2 ou 4 tiles).

  - Moran's I: requer libpysal e esda. Se não instalados, valor fica NaN.

  - Rede pedonal: network_type="walk" do OSMnx (exclui autoestradas e acessos
    privados). Apenas pedestres.

  - Desempenho: ~3-10 min por cidade (cidade europeia média), dependendo do
    nº de POIs e da qualidade da ligação à internet.


MEDIDAS DE INEQUIDADE
---------------------

Para cada indicador (Variety, Total_Dest) x cada intervalo de tempo:

  Gini territorial    Desigualdade entre hexágonos (território)
  Gini ponderado pop  Desigualdade entre habitantes
  Palma ratio         Média top 10% / média bottom 40%
  Theil T             Sensível a diferenças no topo da distribuição
  CV                  Desvio padrão / média
  Moran's I           Autocorrelação espacial (clustering)


CORRESPONDÊNCIA COM O PROJECTO ORIGINAL
----------------------------------------

  Projecto original                           Este projecto
  -----------------                           -------------
  0_GetOsmPbfByCountry.py                     substituídos por OSMnx (sem PBF, sem osmnet)
  0_SeparateCityByCountry.py                  substituídos por OSMnx (sem PBF, sem osmnet)
  1_Accessibility_EuropeanCities_OSM_v2.py    step5_accessibility.py (NetworkX + NumPy)
  3_PopFromRaster_GHS.py                      city_pipeline.py + step6_ghsl.py
  4_GiniCalculation.py                        city_pipeline.py + step8_inequity.py
  5_Pop_PopDensity.py                         city_pipeline.py + step8_inequity.py
  6_JoinDfs.py                                city_pipeline.py + step8_inequity.py
  2_Graficos.py                               step9_analysis.py
  7_DiminishingReturns.py                     step9_analysis.py
  8_ExponentialDecayUpwards.py                step9_analysis.py
