---
name: evaluacion-economica
description: Procedimiento de evaluación económica en dos perspectivas (presupuesto parcial del ganadero y rNPV de la empresa) con entradas trazables, escenarios coherentes y Monte Carlo. Úsalo antes de puntuar, para preparar las salidas de los criterios rentabilidad_ganadero y atractivo_empresa, y para el capítulo económico de los informes.
---

# Evaluación económica

Las ejecuta `economia-mercado-pi` **antes** de que los `evaluador-independiente` puntúen: los dos criterios económicos
de la rúbrica (`rentabilidad_ganadero` y `atractivo_empresa`, `docs/RUBRICA.md`) se anclan en estas salidas.

## Ganadero (presupuesto parcial → `rentabilidad_ganadero`)

1. Copia `ejemplos/econ_ganadero_ILUSTRATIVO.toml` a `research/datos/<ID>/econ_ganadero.toml`.
2. Sustituye **todos** los valores por datos con fuente (precios de mercado, respuesta del metaanálisis) o márcalos
   como `[SUP]`. Cada entrada admite una tabla con `fuente` (`{ dist = "pert", min = ..., moda = ..., max = ...,
   fuente = "[PUB] doi:10.xxxx/yyyy" }` o `{ valor = 50, fuente = "[SUP] ..." }`); el informe lista cada entrada con
   su fuente y avisa de las que no la tienen. Una fuente `[PUB]` cita el identificador de una fuente registrada y
   verificada (`doi:...`, `pmid:...`, `openalex:W...`) o de una afirmación `[PUB]` registrada (`A-xxxx`); una `[DER]`,
   la orden `nutria` o el script que da el valor. Usa distribuciones (`pert`, `triangular`, `normal`) para expresar
   incertidumbre.
3. **Dosis** en una sola base de `[coste]`: `dosis_g_dia` (g/animal/día), `dosis_mg_kg_ms` (mg/kg de MS de la ración;
   exige `[racion].ingestion_ms_kg_dia`) o `dosis_g_t` (g/t del pienso portador; exige `[racion].portador`
   `racion_completa` o `concentrado`, `[racion].base` `tal_cual` o `ms` y la ingestión de ese pienso). Las ingestiones
   se declaran con fuente o `[SUP]`: `nutria` no predice la ingestión (el racionamiento NASEM, INRAE o FEDNA queda fuera
   de alcance). Para convertir una dosis a mano, con salida `[DER]` trazable:
   `nutria --agente economia-mercado-pi dosis convertir <valor> --desde g_animal_dia|mg_kg_ms|g_t --ingestion-ms KG [--portador P --base B
   --ingestion-pienso KG --ms-pienso F] [--precio-eur-kg P]`.
4. `nutria --agente economia-mercado-pi econ ganadero <fichero> --sim 10000 --exigir-fuentes` (`--exigir-fuentes`
   falla si alguna entrada declarada no tiene `fuente` con etiqueta `[PUB]`, `[DER]`, `[SUP]` o `[DESC]`, si una
   `[PUB]` no cita una fuente o afirmación registrada y verificada en el registro del proyecto o si una `[DER]` no
   indica la orden o el script). Los valores centrales usan el mismo estadístico para todas las entradas: la moda
   (por defecto) o la mediana (`--valor-central mediana`); el informe lo declara. Da:
   - la dosis en las tres bases y el coste del producto por animal y día y **por tonelada de pienso**;
   - escenarios coherentes conservador (P10), intermedio (P50) y optimista (P90) del beneficio neto simulado y
     P(neto > 0). ROI = beneficio/coste: "ROI > 1" equivale a beneficio neto positivo;
   - la **respuesta umbral** de cada beneficio con valores centrales coherentes (coste, valor unitario y días con el
     mismo estadístico: moda con moda o mediana con mediana): la primera cifra EXCLUYE los demás beneficios (a cero);
     la segunda los incluye en su valor central. Compara ambas con la respuesta esperada según la evidencia;
   - la **sensibilidad (tornado)**: el neto con cada entrada en su P10 y su P90 y las demás en su valor central,
     ordenado por amplitud. Responde a "¿qué parámetro domina la incertidumbre?";
   - las correlaciones declaradas, la comparación con la alternativa y la lista de entradas con su fuente.
5. **Correlaciones** (p. ej. precio de la leche y respuesta): por defecto las entradas son independientes. Si hay una
   relación relevante, decláralo con `[[correlacion]]` (`entre = ["<entrada>", "<entrada>"]`, `rho`, `fuente`) o con
   `[correlaciones]` (`variables` y `matriz`); se aplican con una cópula gaussiana y la matriz se comprueba (simétrica,
   diagonal 1, definida positiva): si no lo es, el modelo no se evalúa. Los nombres de entrada son los de la lista
   "Entradas y fuentes" del informe (p. ej. `coste.precio_eur_kg`, `beneficio[1].respuesta`).
6. **Alternativa** (opcional, en el mismo informe): `[alternativa]` con `no_hacer_nada = true` (el neto ya se mide
   frente a no usar el producto) u otro producto con `[alternativa.coste]` (dosis y precio) y `[alternativa.respuesta]`
   (su respuesta en cada beneficio, por nombre). Se evalúa en las mismas simulaciones (valores unitarios, días y ración
   compartidos): diferencia de neto y P(producto > alternativa).
7. Interpreta: ¿la respuesta umbral es plausible según la evidencia? ¿Qué entrada encabeza el tornado y qué dato la
   acotaría? ¿Supera el producto a la alternativa comercial?

## Empresa (rNPV → `atractivo_empresa`)

1. Copia `ejemplos/econ_desarrollador_ILUSTRATIVO.toml` a `research/datos/<ID>/econ_desarrollador.toml`; define fases
   (computacional, in vitro, in vivo, dossier y autorización) con coste, duración y probabilidad de éxito con fuente
   o `[SUP]`.
2. **Ventas construidas** en `[mercado]`: `censo_direccionable` × `adopcion_pico` × `ciclos_por_animal_anio` ×
   `dias_por_ciclo` × `dosis_g_dia` / 1000 × `precio_eur_kg` × (1 − `margen_distribucion`), con el **mismo precio, dosis
   y días que el modelo del ganadero** (el precio es el que paga el ganadero; `margen_distribucion`, la parte que se
   queda la distribución). `ventas_pico_eur_anio` como dato sigue admitido, pero no puede comprobarse.
   Años hasta el pico y de comercialización, márgenes y tasa de descuento admiten distribuciones; opcionales:
   `inversion_lanzamiento_eur`, `impuesto_beneficios`, `erosion_precio_anual`, `coste_renovacion_eur` con
   `anio_renovacion` y `multiplo_valor_terminal` (sin él, no hay valor terminal). El modelo no trae valores de
   referencia de plazo ni de coste regulatorio: van con fuente o `[SUP]`.
3. **Coherencia**: `nutria --agente economia-mercado-pi econ coherencia research/datos/<ID>/econ_ganadero.toml
   research/datos/<ID>/econ_desarrollador.toml` compara precio, dosis (convertida a g/animal/día) y días: el valor
   central y el P5-P95 (si la dosis del ganadero está en mg/kg de MS o en g/t, su P5-P95 en g/animal/día sale de una
   simulación con las ingestiones; declara en la empresa una distribución equivalente). Sale con código 1 y "AVISO DE
   COHERENCIA" si difieren o si las ventas son un dato. No entregues modelos incoherentes: los dos criterios económicos
   se apoyarían en precios incompatibles.
4. `nutria --agente economia-mercado-pi econ desarrollador <fichero> --ganadero <econ_ganadero.toml> --exigir-fuentes
   [--umbral-perdida EUR]`. Informa de dos cosas distintas:
   - **incertidumbre de los parámetros**: rNPV central, su distribución y P(rNPV > 0). El rNPV es un valor esperado
     ya ajustado por el riesgo de las fases: P(rNPV > 0) mide la confianza en que ese valor esperado sea positivo y
     no es la probabilidad de que el proyecto gane dinero;
   - **simulación de resultados**: el éxito o fracaso de cada fase se muestrea en secuencia y se obtiene el VPN
     realizado (lo que la empresa ganaría o perdería de verdad): su distribución (percentiles, que incluyen los
     fracasos), el desglose por fase en la que fracasa, P(VPN realizado > 0), P(VPN realizado < 0) y
     **P(pérdida > umbral)**, con el umbral de `[proyecto].umbral_perdida_eur` o `--umbral-perdida` (por defecto, el
     50 % del coste central de desarrollo). Con probabilidades de éxito bajas lo más probable es perder lo invertido
     aunque el rNPV sea positivo;
   - la sensibilidad (tornado) del rNPV central y la lista de entradas con su fuente.

## Reglas

- No mezcles perspectivas: un ROI alto para el ganadero no implica un negocio viable, ni al revés. Pero los dos
  modelos deben usar el mismo precio, dosis y días (`nutria --agente economia-mercado-pi econ coherencia`).
- Coste del producto por animal y día = dosis (g/animal/día) × precio (€/kg) / 1000; coste de inclusión por tonelada
  de pienso = dosis (g/t) × precio (€/kg) / 1000 (1 mg/kg = 1 g/t). Indica siempre la base (tal cual o MS) y el pienso
  portador (ración completa o concentrado).
- Cada número de entrada tiene fuente o etiqueta `[SUP]` (`--exigir-fuentes`). El modelo no sustituye a un ensayo de
  campo.
- Guarda entradas y salidas en `research/datos/<ID>/` e indica sus rutas: los evaluadores las leen; no puntúes tú.
