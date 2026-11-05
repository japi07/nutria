---
tipo: informe_investigacion
oportunidad: {opp_id}
version: 0.1
fecha: {fecha}
estado: borrador
{contexto_fm}
autores: [orquestador]
revision_critica: null  # research/revisiones/<fichero>.md con la revisión adversarial del crítico
revisado_por_humano: null
---

# {titulo}

> **Convención obligatoria.** Cada afirmación relevante lleva una etiqueta: `[PUB]` resultado publicado
> (con referencia `[Rn]` verificable y una fila de respaldo por afirmación en el Anexo A), `[DER]` derivado por
> cálculo propio a partir de datos publicados (indica el cálculo o la orden `nutria` usada), `[HIP]` hipótesis,
> `[SUP]` supuesto de trabajo, `[DESC]` dato desconocido (indica cómo obtenerlo). Cada frase con una cifra con
> unidades o con una referencia `[Rn]` lleva su etiqueta, también en las tablas. Las hipótesis generadas por IA
> nunca se presentan como hallazgos. `[POR COMPLETAR]` marca lo que falta por redactar: en `revisado` o `final` es
> un error. Para pasar a `estado: revisado` o `final` hacen falta: el respaldo de cada afirmación `[PUB]` en el
> Anexo A, la revisión adversarial del crítico enlazada en `revision_critica`, la revisión humana registrada por la
> persona que revisa (`nutria informe revisar`, solo personas) y, después, la verificación en línea completa y
> sellada (`nutria informe comprobar <informe> --online --sellar`).

## 0. Resumen ejecutivo

[POR COMPLETAR] 5-8 líneas: problema, hipótesis principal, nivel de evidencia, puntuación con intervalo,
compuertas y decisión propuesta.

## 1. Título de la investigación

{titulo}

{contexto}

## 2. Problema productivo o fisiológico

{problema}

## 3. Estado actual del conocimiento

[POR COMPLETAR]

## 4. Necesidad científica no resuelta

[POR COMPLETAR]

## 5. Hipótesis principal

{hipotesis}

## 6. Hipótesis alternativas

[POR COMPLETAR]

## 7. Mecanismo de acción propuesto

[POR COMPLETAR] Mecanismo en la especie diana, con el diagrama en Mermaid.

### 7.1. Destino digestivo en la especie diana

<!-- Regla 5: antes de las dianas moleculares, degradación o transformación ruminal, biohidrogenación y llegada al
     intestino a la dosis prevista (nutria destino ruminal; compuerta destino_digestivo). Sustituye [SUP] por
     [PUB] con su [Rn], o por [DER] con el cálculo, cuando proceda. -->

{destino}

## 8. Ingredientes o compuestos candidatos

{candidatos}

## 9. Evidencia científica disponible

<!-- Tabla: estudio [Rn] | tipo | especie/etapa | n | dosis | variable | efecto | riesgo de sesgo. Cada fila con
     un resultado publicado lleva [PUB] y su fila en el Anexo A. -->

[POR COMPLETAR]

## 10. Estudios favorables y desfavorables

[POR COMPLETAR]

## 11. Resultados de los análisis estadísticos

<!-- Modelo, estimador de tau², IC, intervalo de predicción, heterogeneidad, sensibilidad, sesgo de publicación. -->

[POR COMPLETAR]

## 12. Resultados de las simulaciones

<!-- Modelo usado, versión, supuestos, validación y límites. Nunca como prueba de eficacia. -->

[POR COMPLETAR]

## 13. Limitaciones e incertidumbres

[POR COMPLETAR]

## 14. Seguridad y regulación

[POR COMPLETAR] Clasificación propuesta, sustancias prohibidas o restringidas, seguridad (toxicología y
residuos) y vía de autorización.

{ruta}

Estado de las compuertas en el registro (estado efectivo: «superada» solo cuenta con firma humana; una propuesta
«bloqueada» actúa de inmediato):

{compuertas}

Alegación prevista (Reglamento (CE) 767/2009, art. 13): {alegacion}

## 15. Formulación teórica candidata

<!-- Rangos exploratorios justificados, nunca dosis "óptimas" sin datos. -->

[POR COMPLETAR]

## 16. Comparación con productos comerciales

[POR COMPLETAR]

## 17. Costes y viabilidad económica

<!-- Perspectiva ganadero (nutria econ ganadero) y empresa (nutria econ desarrollador). -->

[POR COMPLETAR]

## 18. Plan de validación experimental

[POR COMPLETAR]

## 19. Conclusión

[POR COMPLETAR]

## 20. Bibliografía científica verificable

<!-- Formato: [R1] Autores. Año. Título. Revista Vol:páginas. DOI: 10.xxxx/xxxx | PMID: nnnnnnnn. Cada [Rn] aparece
     una sola vez; una entrada puede ocupar varias líneas (hasta la siguiente entrada o una línea en blanco).
     Normativa y literatura gris solo con URL: requieren `nutria verificar manual <URL> --firma-humana NOMBRE`
     (solo personas). -->

[POR COMPLETAR]

## Anexo A. Tabla de afirmaciones y respaldo

<!-- La rellena verificador-citas leyendo cada fuente: una fila por cada afirmación [PUB] y referencia, con la
     afirmación copiada tal como aparece en el informe (su frase, hasta «.», «!» o «?»: lo que precede a la
     etiqueta desde el inicio de la frase o la etiqueta anterior y, si es la última de la frase, también lo que
     la sigue; en orden, al menos la mitad de sus palabras y un mínimo de 4; «...» separa fragmentos de 2 o más
     palabras). Una fila que reproduce por igual dos afirmaciones distintas no cuenta para ninguna. Respaldo:
     respalda | parcial | no_respalda | no_verificable. Una fila no_respalda no se compensa con otra respalda ni
     se puede dejar sin asignar. Las citas literales entre comillas de la columna Motivo se buscan en el resumen
     de la fuente (no en el texto completo). -->

| Ref | Afirmación | Respaldo | Motivo |
|---|---|---|---|

## Anexo B. Registro de decisiones

{decisiones}

## Anexo C. Procedencia y versiones

<!-- Órdenes nutria ejecutadas, fecha de las búsquedas, versiones de bases de datos y modelos. -->

[POR COMPLETAR]
