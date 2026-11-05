---
name: puntuacion
description: Procedimiento para puntuar oportunidades con la rúbrica anclada (9 criterios, valor con rango de incertidumbre, justificación y evidencia) de forma ciega e independiente y registrarlo. Lo aplica el subagente evaluador-independiente; úsalo siempre que haya que puntuar.
---

# Puntuación con rúbrica

Puntúan solo los subagentes `evaluador-independiente`: al menos tres por oportunidad, cada uno en su propio contexto,
con un identificador distinto (p. ej. `evaluador-1`, `evaluador-2`, `evaluador-3`) y, si es posible, con modelos
distintos. Quien genera o sintetiza la oportunidad (incluido `director-cientifico`) no puntúa. El orquestador da a
cada evaluador el ID, su identificador y las rutas de la evidencia, nunca las puntuaciones de otros ni la conclusión
esperada.

## Ceguera

- La oportunidad se lee solo con `nutria --agente <evaluador> oportunidad ver <ID> --sin-puntuaciones --json`.
- El evaluador nunca ejecuta `nutria puntuar ranking` ni lee puntuaciones o ficheros de otros evaluadores
  (`research/oportunidades/*_puntuacion_*.json`, interpretaciones del ranking, `nutria procedencia`,
  `research/procedencia.jsonl` o la base de datos).

## Procedimiento

1. Lee las anclas: `docs/RUBRICA.md` (o `nutria puntuar rubrica --markdown`).
2. Lee la oportunidad (sin puntuaciones) y los análisis indicados: especialistas, informe, crítica, propuestas de
   compuertas, `nutria econ ganadero` y `nutria econ desarrollador` (los ejecuta antes `economia-mercado-pi`).
3. Para cada uno de los 9 criterios (`evidencia`, `plausibilidad`, `mejora_productiva`, `originalidad`, `seguridad`,
   `viabilidad_tecnologica`, `viabilidad_regulatoria`, `rentabilidad_ganadero`, `atractivo_empresa`):
   - `valor` en el ancla más cercana (0, 0.25, 0.5, 0.75 o 1). La rúbrica está anclada también en los datos: un
     valor intermedio (p. ej. 0.6) solo se admite con `justificacion_entre_anclas` (texto concreto de al menos 10
     caracteres que explique por qué ninguna ancla describe el caso); sin ella, `nutria puntuar registrar` rechaza el
     fichero entero y no registra nada. Con ella, avisa, añade la justificación a la del criterio y el ranking señala
     el valor como "entre anclas". La incertidumbre no se expresa con valores intermedios, sino con `min` y `max`;
   - `min` y `max`: el rango en el que crees que está el valor real (amplio si la evidencia es escasa; pueden tomar
     cualquier valor entre 0 y 1);
   - `justificacion` concreta (no genérica) y `evidencia` con IDs de fuentes, afirmaciones o ficheros.
   `rentabilidad_ganadero` se ancla en el beneficio neto P10/P50/P90 y la respuesta umbral; `atractivo_empresa`, en
   el rNPV, la distribución del VPN realizado y P(pérdida > umbral) (P(rNPV > 0) no es la probabilidad de ganar
   dinero). Si `nutria econ coherencia` avisa de que el precio, la dosis o los días del ganadero y de la empresa
   difieren, o no se ha comprobado, decláralo en la justificación de los dos criterios. ROI = beneficio/coste:
   "ROI > 1" equivale a beneficio neto positivo.
4. No compenses: una evidencia débil no sube porque la idea sea original.
5. Guarda el JSON (esquema `puntuacion.schema.json`, ejemplo en `ejemplos/puntuacion_ejemplo.json`) en
   `research/oportunidades/<ID>_puntuacion_<evaluador>.json` y regístralo:
   `nutria --agente <evaluador> puntuar registrar --datos <fichero>`.

## Ranking (lo ejecuta el orquestador, nunca un evaluador)

`nutria puntuar ranking --perfil recomendado --sim 5000 [--min-evaluadores 3] [--alfa 50]` (compara también
`--perfil original`; la salida muestra el rango con cada perfil de pesos). Estados: APTA (todas las compuertas
superadas y firmadas por una persona), CONDICIONAL (alguna compuerta sin resolver o sin firmar; se ordena después de
las APTA), BLOQUEADA, INCOMPLETA (criterios sin puntuar) e INSUFICIENTE (menos de 3 evaluadores distintos por
criterio). La puntuación es orientativa: las compuertas bloquean con independencia de ella.

Dentro de CONDICIONAL hay tres subgrupos que se ordenan siempre así, sea cual sea la puntuación: solo pendientes de
firma humana (todas las compuertas propuestas como `superada`), con alguna compuerta sin resolver y con alguna
compuerta **crítica** (`seguridad`, `regulatoria`, `destino_digestivo`) sin resolver. El bloque "AVISOS" de la salida
(en `--json`, por la salida de error y en las `flags` de cada fila) señala si el top-k para planes incluye compuertas
críticas sin resolver, si el orden o el conjunto del top-k cambian entre perfiles de pesos y si el top-k del Monte
Carlo no coincide con el orden determinista del perfil elegido: transcríbelos en la propuesta; un orden que cambia con
los pesos no es robusto. Cada ranking queda en la procedencia con la semilla, las
simulaciones, el alfa, el perfil, las huellas de la rúbrica y de las puntuaciones y el orden resultante.
