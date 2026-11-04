---
name: estado
description: Resume el estado del proyecto (registro, ranking, compuertas pendientes, informes sin sello, extracciones sin doble verificación) y propone los siguientes pasos.
disable-model-invocation: true
---

# Estado del proyecto

1. `nutria estado` y `nutria puntuar ranking --sim 2000`.
2. Pendientes:
   - compuertas `no_resuelta` (`nutria oportunidad ver <ID> --json`);
   - informes sin sello válido (`nutria informe sello <md>` para cada `research/informes/*.md`);
   - hipótesis activas sin experimentos ni criterio de falsación;
   - extracciones sin doble verificación;
   - fuentes sin verificar (`nutria fuente listar --no-verificadas`).
3. Propón 3-5 siguientes pasos priorizados y quién debe hacerlos.
