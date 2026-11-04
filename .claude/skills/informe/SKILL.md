---
name: informe
description: Crea, completa y verifica el informe de investigación de 20 secciones de una oportunidad y lo sella cuando todas sus referencias están verificadas.
argument-hint: "O-xxxx"
disable-model-invocation: true
---

# Informe de $ARGUMENTS

1. `nutria informe nuevo $ARGUMENTS` (si aún no existe `research/informes/$ARGUMENTS.md`).
2. Completa cada sección con el registro y los ficheros de `research/`. Todas las afirmaciones van etiquetadas; las
   publicadas citan `[Rn]`; la bibliografía usa `[Rn] Autores. Año. Título. Revista Vol:páginas. DOI: ... | PMID: ...`.
3. Incluye tablas comparativas, el diagrama del mecanismo en Mermaid, la tabla de afirmaciones (Anexo A), las
   decisiones (Anexo B) y la procedencia (Anexo C: órdenes, fechas de búsqueda, versiones; `nutria procedencia`).
4. `nutria informe comprobar research/informes/$ARGUMENTS.md` y corrige la estructura.
5. `verificador-citas`: `nutria informe comprobar ... --online --sellar` y comprobación de respaldo de cada `[PUB]`.
6. `critico-red-team`: revisión adversarial; incorpora correcciones.
7. Vuelve a sellar tras cualquier cambio. El informe solo pasa a `estado: revisado` con sello válido
   (`nutria informe sello ...`) y revisión humana (`revisado_por_humano` en el encabezado).
