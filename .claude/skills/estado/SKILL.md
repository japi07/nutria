---
name: estado
description: Resume el estado del proyecto (registro, ranking, compuertas propuestas sin firmar o sin resolver, informes sin sello válido, extracciones sin doble verificación, verificaciones manuales pendientes) y propone los siguientes pasos.
disable-model-invocation: true
---

# Estado del proyecto

1. `nutria estado` y `nutria puntuar ranking --sim 2000 --min-evaluadores 3` (estados APTA, CONDICIONAL, BLOQUEADA,
   INCOMPLETA, INSUFICIENTE).
2. Pendientes (`nutria oportunidad ver <ID> --json` muestra las compuertas registradas y las efectivas):
   - **compuertas sin firmar**: propuestas `superada` de los agentes que no cuentan hasta que una persona las firme
     (`nutria puntuar compuerta <ID> <compuerta> superada --motivo "..." --firma-humana NOMBRE`, en su terminal);
   - compuertas `no_resuelta` o sin proponer (de las seis: `seguridad`, `regulatoria`, `analitica`,
     `propiedad_intelectual`, `etica`, `destino_digestivo`) y compuertas `bloqueada` cuyo motivo debe revisar una
     persona;
   - oportunidades INSUFICIENTES (menos de 3 `evaluador-independiente` distintos) o INCOMPLETAS;
   - informes sin sello válido (`nutria informe sello <md>` para cada `research/informes/*.md`), sin tabla de
     respaldo en el Anexo A o sin revisión humana;
   - referencias con solo URL pendientes de verificación manual (solo una persona: `nutria verificar manual <URL> --firma-humana NOMBRE`);
   - hipótesis activas sin experimentos ni criterio de falsación;
   - extracciones sin doble verificación (estado `simple` o `discrepancia`);
   - fuentes sin verificar (`nutria fuente listar --no-verificadas`).
3. Propón 3-5 siguientes pasos priorizados y quién debe hacerlos (agente o persona). Las firmas son siempre de una
   persona.
