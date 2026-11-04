---
tipo: informe_investigacion
oportunidad: {opp_id}
version: 0.1
fecha: {fecha}
estado: borrador
autores: [orquestador]
revisado_por_humano: null
---

# {titulo}

> **Convención obligatoria.** Cada afirmación relevante lleva una etiqueta: `[PUB]` resultado publicado
> (con referencia `[Rn]` verificable y fila de respaldo en el Anexo A), `[DER]` derivado por cálculo propio a
> partir de datos publicados (indica el cálculo o la orden `nutria` usada), `[HIP]` hipótesis, `[SUP]` supuesto
> de trabajo, `[DESC]` dato desconocido (indica cómo obtenerlo). Las hipótesis generadas por IA nunca se
> presentan como hallazgos. Para pasar a `estado: revisado` o `final` hacen falta: verificación en línea
> completa y sellada (`nutria informe comprobar <informe> --online --sellar`), respaldo comprobado de cada
> referencia `[PUB]` en el Anexo A y `revisado_por_humano` con el nombre de la persona que lo revisó.

## 0. Resumen ejecutivo

<!-- 5-8 líneas: problema, hipótesis principal, nivel de evidencia, puntuación con intervalo, compuertas, decisión propuesta. -->

## 1. Título de la investigación

{titulo}

## 2. Problema productivo o fisiológico

{problema}

## 3. Estado actual del conocimiento

## 4. Necesidad científica no resuelta

## 5. Hipótesis principal

{hipotesis}

## 6. Hipótesis alternativas

## 7. Mecanismo de acción propuesto

<!-- Incluye el destino digestivo en la especie diana (degradación ruminal, biohidrogenación, absorción). -->

## 8. Ingredientes o compuestos candidatos

{candidatos}

## 9. Evidencia científica disponible

<!-- Tabla: estudio [Rn] | tipo | especie/etapa | n | dosis | variable | efecto | riesgo de sesgo -->

## 10. Estudios favorables y desfavorables

## 11. Resultados de los análisis estadísticos

<!-- Modelo, estimador de tau², IC, intervalo de predicción, heterogeneidad, sensibilidad, sesgo de publicación. -->

## 12. Resultados de las simulaciones

<!-- Modelo usado, versión, supuestos, validación y límites. Nunca como prueba de eficacia. -->

## 13. Limitaciones e incertidumbres

## 14. Seguridad y regulación

<!-- Clasificación propuesta, vía de autorización, estado de las compuertas: {compuertas} -->

## 15. Formulación teórica candidata

<!-- Rangos exploratorios justificados, nunca dosis "óptimas" sin datos. -->

## 16. Comparación con productos comerciales

## 17. Costes y viabilidad económica

<!-- Perspectiva ganadero (nutria econ ganadero) y empresa (nutria econ desarrollador). -->

## 18. Plan de validación experimental

## 19. Conclusión

## 20. Bibliografía científica verificable

<!-- Formato: [R1] Autores. Año. Título. Revista Vol:páginas. DOI: 10.xxxx/xxxx | PMID: nnnnnnnn.
     Normativa y literatura gris solo con URL: requieren `nutria verificar manual <URL> --firma-humana NOMBRE`. -->

## Anexo A. Tabla de afirmaciones y respaldo

<!-- La rellena verificador-citas leyendo cada fuente. Respaldo: respalda | parcial | no_respalda | no_verificable.
     Cada [Rn] usado en una línea [PUB] necesita una fila con respalda o parcial. -->

| Ref | Afirmación | Respaldo | Motivo |
|---|---|---|---|

## Anexo B. Registro de decisiones

{decisiones}

## Anexo C. Procedencia y versiones

<!-- Órdenes nutria ejecutadas, fecha de las búsquedas, versiones de bases de datos y modelos. -->
