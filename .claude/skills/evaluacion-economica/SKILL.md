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
   como `[SUP]` en `supuestos`. Usa distribuciones (`pert`, `triangular`, `normal`) para expresar incertidumbre.
3. `nutria --agente economia-mercado-pi econ ganadero <fichero> --sim 10000`: escenarios coherentes conservador
   (P10), intermedio (P50) y optimista (P90) del beneficio neto simulado, P(neto > 0) y respuesta umbral (la mínima
   respuesta que paga el producto). ROI = beneficio/coste: "ROI > 1" equivale a beneficio neto positivo.
4. Interpreta: ¿la respuesta umbral es plausible según la evidencia? ¿Qué parámetro domina la incertidumbre?

## Empresa (rNPV → `atractivo_empresa`)

1. Copia `ejemplos/econ_desarrollador_ILUSTRATIVO.toml` a `research/datos/<ID>/econ_desarrollador.toml`; define fases
   (computacional, in vitro, in vivo, dossier y autorización) con coste, duración y probabilidad de éxito con fuente
   o `[SUP]`; mercado (ventas pico, años hasta el pico, margen, costes comerciales).
2. `nutria --agente economia-mercado-pi econ desarrollador <fichero>`: rNPV central, distribución, P(rNPV > 0),
   probabilidad acumulada de éxito y años hasta el mercado.

## Reglas

- No mezcles perspectivas: un ROI alto para el ganadero no implica un negocio viable, ni al revés.
- Coste de inclusión por tonelada de pienso = dosis (g/t) × precio; coste por animal y día = dosis (g/d) × precio.
- Cada número de entrada tiene fuente o etiqueta `[SUP]`. El modelo no sustituye a un ensayo de campo.
- Guarda entradas y salidas en `research/datos/<ID>/` e indica sus rutas: los evaluadores las leen; no puntúes tú.
