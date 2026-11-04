---
name: evaluacion-economica
description: Procedimiento de evaluación económica en dos perspectivas (presupuesto parcial del ganadero y rNPV de la empresa) con entradas trazables, escenarios coherentes y Monte Carlo. Úsalo para el criterio de potencial económico y el capítulo económico de los informes.
---

# Evaluación económica

## Ganadero (presupuesto parcial)

1. Copia `ejemplos/econ_ganadero_ILUSTRATIVO.toml` a `research/datos/<tema>/econ_ganadero.toml`.
2. Sustituye **todos** los valores por datos con fuente (precios de mercado, respuesta del metaanálisis) o márcalos
   como `[SUP]` en `supuestos`. Usa distribuciones (`pert`, `triangular`, `normal`) para expresar incertidumbre.
3. `nutria econ ganadero <fichero> --sim 10000`: escenarios conservador, intermedio y optimista, P(neto > 0) y
   respuesta umbral (la mínima respuesta que paga el producto).
4. Interpreta: ¿la respuesta umbral es plausible según la evidencia? ¿Qué parámetro domina la incertidumbre?

## Empresa (rNPV)

1. Copia `ejemplos/econ_desarrollador_ILUSTRATIVO.toml`; define fases (computacional, in vitro, in vivo, dossier y
   autorización) con coste, duración y probabilidad de éxito con fuente o `[SUP]`; mercado (ventas pico, años hasta el
   pico, margen, costes comerciales).
2. `nutria econ desarrollador <fichero>`: rNPV central, distribución, P(rNPV > 0), probabilidad acumulada de éxito y
   años hasta el mercado.

## Reglas

- No mezcles perspectivas: un ROI alto para el ganadero no implica un negocio viable, ni al revés.
- Coste de inclusión por tonelada de pienso = dosis (g/t) × precio; coste por animal y día = dosis (g/d) × precio.
- Cada número de entrada tiene fuente o etiqueta `[SUP]`. El modelo no sustituye a un ensayo de campo.
