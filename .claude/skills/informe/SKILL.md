---
name: informe
description: Crea, completa y verifica el informe de investigación de 20 secciones de una oportunidad, con la tabla de respaldo del Anexo A, y lo sella cuando la verificación en línea es completa; el paso a revisado exige revisión humana.
argument-hint: "O-xxxx"
disable-model-invocation: true
---

# Informe de $ARGUMENTS

En la evaluación en profundidad, el informe (síntesis, resumen ejecutivo y decisión propuesta) se redacta **después**
de la puntuación ciega de los tres `evaluador-independiente`, que no lo leen: recoge sus puntuaciones con sus
desacuerdos. Las puntuaciones solo se registran si el informe obtiene un sello válido.

1. `nutria --agente <tu-rol> informe nuevo $ARGUMENTS` (si aún no existe `research/informes/$ARGUMENTS.md`).
2. Completa cada sección con el registro y los ficheros de `research/`. Todas las afirmaciones van etiquetadas; las
   publicadas citan `[Rn]`; la bibliografía usa `[Rn] Autores. Año. Título. Revista Vol:páginas. DOI: ... | PMID: ...`.
   Normativa y literatura gris solo con URL valen únicamente si una persona las registró con
   `nutria verificar manual <URL> --firma-humana NOMBRE` (solo personas; ningún agente lo hace).
3. Incluye tablas comparativas, el diagrama del mecanismo en Mermaid, las decisiones (Anexo B) y la procedencia
   (Anexo C: órdenes, fechas de búsqueda, versiones; `nutria --agente <tu-rol> procedencia`). La sección 14 recoge el estado de las seis
   compuertas e indica cuáles son propuestas sin firmar.
4. `nutria --agente <tu-rol> informe comprobar research/informes/$ARGUMENTS.md` y corrige la estructura.
5. `verificador-citas` rellena la **tabla de respaldo del Anexo A**:

   ```
   | Ref | Afirmación | Respaldo | Motivo |
   ```

   con Respaldo en `respalda`, `parcial`, `no_respalda` o `no_verificable`. Cada `[Rn]` usado en una línea `[PUB]`
   necesita una fila `respalda` o `parcial`; si no la tiene, se corrige la afirmación o deja de ser `[PUB]`. Después
   ejecuta `nutria --agente <tu-rol> informe comprobar ... --online --sellar`.
6. `critico-red-team` (o el workflow `red-team-auto`): revisión adversarial; incorpora correcciones.
7. Vuelve a sellar tras cualquier cambio (el sello caduca si el informe cambia). El sello solo es válido con la
   verificación en línea **completa** (todas las referencias verificadas, retractación comprobada, ninguna pendiente
   por falta de red) y la estructura completa. El informe solo pasa a `estado: revisado` (o `final`) con sello válido
   (`nutria --agente <tu-rol> informe sello ...`), el Anexo A de respaldo completo y revisión humana: la persona la registra en su terminal con
   `nutria informe revisar <md> --firma-humana NOMBRE --estado revisado|final`, que escribe `revisado_por_humano` y
   el estado; ningún agente lo rellena ni cambia el estado.
