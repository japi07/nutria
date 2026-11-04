---
name: metaanalisis
description: Procedimiento de síntesis cuantitativa con nutria, desde las extracciones registradas con doble extracción hasta el metaanálisis, la meta-regresión, la dosis-respuesta de St-Pierre, la sensibilidad, el sesgo y la certeza. Úsalo con al menos 3 estudios comparables en la especie diana.
argument-hint: "O-xxxx \"variable\""
disable-model-invocation: true
---

# Metaanálisis: $ARGUMENTS

1. **Doble extracción previa**: solo entran extracciones conciliadas (`doble_concordante` o `resuelta`; ver
   `nutria extraccion comparar A.json B.json` en la skill `extraccion-datos`).
2. **Exporta** en formato de análisis: `nutria extraccion exportar --variable "<variable>" --csv research/datos/<ID>/<variable>.csv`
   (columnas `estudio, comparacion, media_trt, media_ctrl, disp_trt, disp_ctrl, tipo_disp, n_trt, n_ctrl, variable,
   unidad, especie, diseno, dosis, ...` y la dieta basal numérica). Separa o excluye especies distintas, unidades
   incompatibles y diseños no combinables, y documenta por qué.
3. **Modelo principal**: `nutria meta analizar <csv> --medida MD --metodo REML` (ajuste de Hartung-Knapp-Sidik-Jonkman
   por defecto, `--ajuste hksj`). Opciones: `--medida MD|SMD|ROM|RR|OR|RD`, `--metodo REML|DL|PM|FE`,
   `--ajuste hksj|hksj-mod|ninguno`. Informa estimación con IC, tau², I², intervalo de predicción y uno-fuera.
4. **Dependencia**: si hay varios tratamientos frente al mismo control o varias comparaciones por estudio,
   `--agregar-estudio --rho 0.5` (declara la correlación supuesta y prueba otros valores) o
   `nutria meta exportar-r <csv>` y el modelo multinivel con varianza robusta en R.
5. **Dosis-respuesta entre estudios** (St-Pierre, estudio como efecto aleatorio):
   `nutria meta stpierre <csv> --respuesta <col> --dosis <col> --estudio estudio [--covariable ...] [--pendiente-aleatoria]`;
   por defecto descompone la pendiente en sus componentes intra y entre estudios; informa ambas.
6. **Moderadores y subgrupos** solo preespecificados: `--moderador dosis`, `--subgrupo diseno`. Con menos de unos 10
   estudios por moderador, el resultado es exploratorio.
7. **Sensibilidad**: uno-fuera; sin estudios de alto riesgo de sesgo; sin estudios financiados por el fabricante;
   DL frente a REML; `--ajuste hksj` frente a `--ajuste ninguno`; otros valores de `--rho`.
8. **Sesgo de publicación**: embudo y Egger solo si k ≥ 10; si no, decláralo como no evaluable.
9. **Modelos predictivos**: validación agrupada por estudio,
   `nutria meta validar-predictivo <csv> --respuesta <Y> --predictores <X1> <X2> --estudio <S>`.
10. **Certeza** por variable: riesgo de sesgo, inconsistencia, evidencia indirecta, imprecisión, sesgo de publicación.
11. **Interpretación**: efecto en unidades naturales frente al efecto mínimo relevante fijado de antemano; traducción
    económica con `nutria econ ganadero`.
12. **Registro**: guarda órdenes y salidas en `research/datos/<ID>/` y añade afirmaciones `[DER]` con la orden usada.
