---
name: revision-sistematica
description: Protocolo y ejecución de una revisión sistemática según PRISMA 2020 para una pregunta PECO, con búsqueda en varias fuentes, cribado, doble extracción, riesgo de sesgo y síntesis.
argument-hint: "\"pregunta PECO\" [O-xxxx]"
disable-model-invocation: true
---

# Revisión sistemática: $ARGUMENTS

1. **Protocolo antes de buscar** (`research/revisiones/<tema>_protocolo.md`, con fecha y versión): pregunta PECO;
   criterios de inclusión y exclusión (especie, etapa, diseño, duración mínima, comparador, variables); variables
   primarias y secundarias; fuentes y estrategia; cribado (al menos una muestra con dos cribadores independientes);
   doble extracción; riesgo de sesgo (herramienta SYRCLE adaptada y elementos de REFLECT para ensayos en ganado);
   síntesis prevista (medida, modelo, moderadores preespecificados); certeza de la evidencia. El registro público del
   protocolo (OSF, SYREAF) es una decisión humana: puede comprometer la confidencialidad.
2. **Búsqueda**: `investigador-bibliografico` con la skill `busqueda-bibliografica` (≥ 2 fuentes y búsqueda de nulos).
3. **Cribado** por título y resumen con motivos de exclusión; texto completo cuando esté disponible.
4. **Doble extracción** independiente (dos `extractor-datos`: `extraccion_<fuente>_A.json` y `_B.json`),
   comparación con `nutria extraccion comparar <A> <B>` y conciliación de discrepancias contra la fuente (skill
   `extraccion-datos`).
5. **Riesgo de sesgo** por estudio, en tabla.
6. **Síntesis**: `estadistico` con `/metaanalisis`; si no se puede combinar, síntesis narrativa estructurada explicando
   por qué.
7. **Diagrama PRISMA** (Mermaid) y tabla de características de los estudios.
8. **Verificación**: `verificador-citas` y `critico-red-team`.

Una revisión narrativa o de alcance no se presenta nunca como sistemática.
