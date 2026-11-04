---
name: metaanalisis
description: Procedimiento de síntesis cuantitativa con nutria, desde las extracciones registradas hasta el metaanálisis, la meta-regresión, la sensibilidad, el sesgo y la certeza. Úsalo con al menos 3 estudios comparables en la especie diana.
argument-hint: "O-xxxx \"variable\""
disable-model-invocation: true
---

# Metaanálisis: $ARGUMENTS

1. **Exporta** las extracciones: `nutria extraccion exportar --variable "<variable>" --csv research/datos/<tema>/<variable>.csv`.
2. **Prepara el CSV de análisis** con columnas `estudio, comparacion, media_trt, media_ctrl, disp_trt, disp_ctrl,
   tipo_disp, n_trt, n_ctrl` y los moderadores (dosis, diseño, etapa, financiación...). Separa o excluye especies
   distintas, unidades incompatibles y diseños no combinables, y documenta por qué.
3. **Modelo principal**: `nutria meta analizar <csv> --medida MD --metodo REML --hksj`. Informa estimación con IC,
   tau², I², intervalo de predicción y uno-fuera.
4. **Dependencia**: si hay varios tratamientos frente al mismo control, `nutria meta exportar-r <csv>` y ejecuta el
   modelo multinivel con varianza robusta en R. Para dosis-respuesta entre estudios: `nutria meta stpierre <csv>
   --respuesta <col> --dosis <col> --estudio estudio`.
5. **Moderadores y subgrupos** solo preespecificados: `--moderador dosis`, `--subgrupo diseno`. Con menos de unos 10
   estudios por moderador, el resultado es exploratorio.
6. **Sensibilidad**: uno-fuera; sin estudios de alto riesgo de sesgo; sin estudios financiados por el fabricante;
   DL frente a REML; con y sin HKSJ.
7. **Sesgo de publicación**: embudo y Egger solo si k ≥ 10; si no, decláralo como no evaluable.
8. **Certeza** por variable: riesgo de sesgo, inconsistencia, evidencia indirecta, imprecisión, sesgo de publicación.
9. **Interpretación**: efecto en unidades naturales frente al efecto mínimo relevante fijado de antemano; traducción
   económica con `nutria econ ganadero`.
10. **Registro**: guarda órdenes y salidas en `research/datos/<tema>/` y añade afirmaciones `[DER]` con la orden usada.
