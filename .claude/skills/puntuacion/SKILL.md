---
name: puntuacion
description: Procedimiento para puntuar oportunidades con la rúbrica anclada (8 criterios, valor con rango de incertidumbre, justificación y evidencia) de forma independiente y registrarlo. Úsalo siempre que un agente actúe como evaluador.
---

# Puntuación con rúbrica

1. Lee las anclas: `docs/RUBRICA.md` (o `nutria puntuar rubrica --markdown`).
2. Lee la oportunidad (`nutria oportunidad ver <ID> --sin-puntuaciones --json`) y los informes de especialistas indicados. **No leas las
   puntuaciones de otros evaluadores.**
3. Para cada criterio (`evidencia`, `plausibilidad`, `mejora_productiva`, `originalidad`, `seguridad`,
   `viabilidad_tecnologica`, `viabilidad_regulatoria`, `potencial_economico`):
   - `valor` en el ancla más cercana (0, 0.25, 0.5, 0.75 o 1);
   - `min` y `max`: el rango en el que crees que está el valor real (amplio si la evidencia es escasa);
   - `justificacion` concreta (no genérica) y `evidencia` con IDs de fuentes o afirmaciones.
4. No compenses: una evidencia débil no sube porque la idea sea original.
5. Guarda el JSON (esquema `puntuacion.schema.json`, ejemplo en `ejemplos/puntuacion_ejemplo.json`) en
   `research/oportunidades/<ID>_puntuacion_<evaluador>.json` y regístralo:
   `nutria --agente <evaluador> puntuar registrar --datos <fichero>`.

La puntuación es orientativa: el ranking (`nutria puntuar ranking`) informa de la probabilidad de quedar entre los
primeros y de los desacuerdos; las compuertas pueden bloquear con independencia de la puntuación.
