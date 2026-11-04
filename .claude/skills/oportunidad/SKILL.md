---
name: oportunidad
description: Evaluación en profundidad de una oportunidad registrada (evidencia, mecanismo, cuantificación, formulación, regulación, industrialización, economía, crítica, puntuación e informe) coordinando a los especialistas.
argument-hint: "O-0001"
disable-model-invocation: true
---

# Evaluación en profundidad de $ARGUMENTS

1. **Contexto**: `nutria oportunidad ver $ARGUMENTS --json`. Lee `research/planes/$ARGUMENTS.md` si existe; si no,
   pide antes un plan a `director-cientifico`.
2. **Descarte barato primero**: lanza antes los análisis con más probabilidad de refutar la oportunidad (destino
   digestivo, seguridad, vía regulatoria, coste por animal y día). Si alguno la bloquea, detente y registra el motivo.
3. **Evidencia (F4)**: `investigador-bibliografico` hace la revisión de alcance; si el plan lo exige, usa
   `/revision-sistematica`.
4. **Especialistas en paralelo (F5-F8)**, cada uno con su entregable y sin ver conclusiones ajenas:
   `fisiologia-bioquimica`, `compuestos-toxicologia`, `nutricion-formulacion`, `bioinformatico` (solo si aporta a una
   decisión), `desarrollo-industrial`, `regulacion-seguridad`, `economia-mercado-pi`, `sostenibilidad-bienestar`.
5. **Cuantificación (F6)**: con ≥ 3 estudios comparables en la especie diana, doble extracción independiente (dos
   `extractor-datos`) y `/metaanalisis`.
6. **Crítica**: `critico-red-team` sobre la síntesis; `verificador-citas` sobre las fuentes.
7. **Hipótesis**: actualiza su estado con motivo (`nutria hipotesis estado H-xxxx refutada --motivo "..."`).
8. **Compuertas y puntuación**: compuertas de los especialistas; tres evaluadores independientes (skill `puntuacion`);
   `nutria puntuar ranking`.
9. **Informe**: `/informe $ARGUMENTS`.
10. **Decisión propuesta** (continuar, reformular o descartar) con justificación; queda pendiente de firma humana.
