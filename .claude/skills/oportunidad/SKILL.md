---
name: oportunidad
description: Evaluación en profundidad de una oportunidad registrada empezando por el descarte rápido (regulación, toxicología, destino digestivo y modelo del ganadero), y después evidencia con doble extracción, cuantificación, especialistas, crítica, informe y puntuación ciega por evaluadores independientes.
argument-hint: "O-0001"
disable-model-invocation: true
---

# Evaluación en profundidad de $ARGUMENTS

`/evaluar-oportunidad-auto` ejecuta este mismo procedimiento como workflow.

1. **Contexto**: `nutria oportunidad ver $ARGUMENTS --sin-puntuaciones --json`. Lee `research/planes/$ARGUMENTS.md` si
   existe; si no, pide antes un plan a `director-cientifico`.
2. **Descarte rápido (primero, en paralelo)**: los análisis más baratos con más probabilidad de refutar la
   oportunidad, cada uno con su propuesta registrada sin `--firma-humana`:
   - `regulacion-seguridad`: skill `triage-regulatorio` → compuerta `regulatoria`;
   - `compuestos-toxicologia`: señales de seguridad → compuerta `seguridad`;
   - `fisiologia-bioquimica`: destino digestivo con `nutria destino ruminal` → compuerta `destino_digestivo`;
   - `economia-mercado-pi`: modelo del ganadero con `nutria econ ganadero` (coste por animal y día, respuesta
     umbral, P10/P50/P90).
   Si alguno propone `bloqueada`, **detente**: registra el motivo (`nutria decision registrar ...`, sin firma) y
   preséntalo a una persona. Salir de `bloqueada` exige firma humana.
3. **Evidencia (F4)**: `investigador-bibliografico` hace la revisión de alcance; si el plan lo exige, usa
   `/revision-sistematica`.
4. **Doble extracción y cuantificación (F6)**: con estudios cuantitativos en la especie diana, dos `extractor-datos`
   independientes escriben `research/datos/$ARGUMENTS/extraccion_<fuente>_A.json` y `..._B.json`; la conciliación
   ejecuta `nutria extraccion comparar <A> <B>` y registra solo la versión conciliada. Con ≥ 3 estudios comparables,
   `estadistico` aplica `/metaanalisis`.
5. **Especialistas en paralelo (F5-F8)**, cada uno con su entregable y sin ver conclusiones ajenas, sin excluir a
   `investigador-bibliografico` ni a `estadistico` si el plan les asigna preguntas: `fisiologia-bioquimica`,
   `compuestos-toxicologia`, `nutricion-formulacion`, `bioinformatico` (solo si aporta a una decisión),
   `desarrollo-industrial` (compuerta `analitica`), `regulacion-seguridad`, `economia-mercado-pi` (rNPV y compuerta
   `propiedad_intelectual`), `sostenibilidad-bienestar` (compuerta `etica`).
6. **Crítica**: `critico-red-team` sobre cada aportación y la síntesis; `verificador-citas` sobre las fuentes.
7. **Hipótesis**: actualiza su estado con motivo (`nutria hipotesis estado H-xxxx refutada --motivo "..."`).
8. **Informe**: `/informe $ARGUMENTS` (Anexo A de respaldo lo rellena `verificador-citas`).
9. **Puntuación**: tres `evaluador-independiente` (identificadores distintos y, si es posible, modelos distintos)
   con la skill `puntuacion`, a ciegas; después, `nutria puntuar ranking`.
10. **Decisión propuesta** (continuar, reformular o descartar) con justificación; queda pendiente de firma humana.
    Informa también de los agentes que fallaron y de lo que quedó sin hacer.
