---
name: mision-1
description: Primera misión exploratoria paso a paso con revisión humana entre fases. Construye el mapa de problemas, genera al menos 10 oportunidades (al menos 6 en rumiantes) sin duplicar las registradas, hace el triage con las seis compuertas y la puntuación ciega por evaluadores independientes de las no bloqueadas, y redacta planes computacionales, criticados con la lista de validación y con la compuerta ética repropuesta, para las oportunidades que elija una persona.
argument-hint: "[foco opcional, p. ej. vacuno lechero en transición]"
disable-model-invocation: true
---

# Misión 1: investigación exploratoria

Foco indicado: $ARGUMENTS
Si no hay foco: nutrición y producción animal con prioridad en rumiantes, sobre todo vacuno lechero, sin excluir
otras especies.

Esta misión es una **revisión de alcance con exploración de horizonte** (no una revisión sistemática). Su producto
son oportunidades priorizadas e hipótesis falsables, no efectos demostrados.

El workflow `mision-1-auto` (no es una orden con `/`: lánzalo por su nombre con la herramienta Workflow) ejecuta las
mismas fases, una por invocación y con un punto de control humano entre ellas: `{"fase": "mapa"}` (por defecto) →
`{"fase": "oportunidades", "problemas": [...aprobados por una persona...]}` → `{"fase": "planes", "seleccion": ["O-xxxx", ...]}`.
Cada fase empieza con el paso 0. Cada entregable (mapa, ranking, planes) pasa por `verificador-citas`
(`nutria --agente <tu-rol> verificar <md>`) antes de presentarse, y la verificación solo cuenta como completa si lo dice la salida de
`nutria`. Requiere Claude Code con la herramienta Workflow: está en la versión 2.1.318 del binario nativo
(`claude --version`), pero no en el paquete npm `@anthropic-ai/claude-code` 2.1.42. Los workflows se han probado con
agentes simulados (`tests/orquestacion_harness.cjs`), no en una ejecución real de principio a fin.

Topes del workflow (cada uno falla con un mensaje claro si se supera): como máximo 8 problemas aprobados
(`"max_problemas"`, hasta 12); `"n_oportunidades"` (12 por defecto, como máximo 24) es a la vez el objetivo y el tope
de oportunidades que se registran; como máximo 3 oportunidades por generador y 2 rondas extra. Nada se descarta en
silencio: un problema sin título o con un id repetido, o una `"fecha"` que no exista, detienen la fase antes de
lanzar ningún agente. El registro del workflow da una estimación del número de agentes y su máximo.

## Paso 0. Preparación (en cada fase)

1. `nutria --agente <tu-rol> doctor`. Si alguna fuente no es accesible, dilo al usuario y continúa con las demás (el workflow acepta
   el estado de cada fuente como texto, "OK (200)", o como objeto, `{"estado": "ok", ...}`).
2. `nutria --agente <tu-rol> estado` (en texto: una línea `O-xxxx [estado] título` por oportunidad). No dupliques oportunidades ya
   registradas: compara los títulos normalizados con los registrados (el workflow no genera, comprueba ni registra de
   nuevo una oportunidad con el mismo título normalizado; si la salida no cuadra con el resumen por estados, o no hay
   registro, no continúa la fase "oportunidades").
3. Fecha de la ejecución (`"fecha"` o la del sistema) y ficheros ya existentes en `research/revisiones`,
   `research/oportunidades`, `research/hipotesis` y `research/planes`: la fecha va en los nombres de fichero, que no
   se pisan entre ejecuciones (si un nombre ya existe, se añade `-2`, `-3`...).
4. Lee `docs/PROMPT_MAESTRO_v2.md` §18 (primera misión).

## Paso 1 (M1a, fase "mapa"). Mapa de problemas

Lanza en paralelo, de forma independiente:
- `fisiologia-bioquimica`: problemas fisiológicos y metabólicos sin solución nutricional suficientemente eficaz
  (transición, movilización lipídica e hígado graso, cetosis, hipocalcemia subclínica, estrés oxidativo, inflamación,
  estrés por calor, salud ruminal e intestinal, fertilidad).
- `nutricion-formulacion`: eficiencia alimentaria, biodisponibilidad, interacciones entre ingredientes, metabolismo
  lipídico y proteico.
- `sostenibilidad-bienestar`: emisiones y eficiencia del nitrógeno, longevidad productiva, bienestar, alternativas a
  antimicrobianos y al óxido de zinc terapéutico en lechones.
- `economia-mercado-pi`: problemas con mayor coste para el ganadero y huecos de mercado.

Cada uno devuelve problemas con: especie y etapa, carga (incidencia o coste con fuente, o `[DESC]`), **soluciones
actuales y su eficacia documentada**, brecha, mecanismos implicados y fuentes.

`director-cientifico` consolida en `research/revisiones/M1a_mapa_problemas_<fecha>.md` (sin sobrescribir un mapa
anterior): une duplicados, prioriza por carga × necesidad no cubierta (justificado), conserva los desacuerdos y
**conserva las soluciones actuales de cada problema** (los generadores las necesitan para no proponer lo que ya
existe). `verificador-citas` ejecuta `nutria --agente verificador-citas verificar research/revisiones/M1a_mapa_problemas_<fecha>.md`.

**Punto de control humano**: presenta el mapa y su verificación; una persona aprueba la lista de problemas (o ajusta
el foco) antes de continuar.

## Paso 2 (M1b, fase "oportunidades"). Al menos 10 oportunidades

- Genera con la skill `hipotesis` (conexiones entre disciplinas, descubrimiento basado en literatura ABC,
  contradicciones entre estudios, reposicionamiento desde otras especies o la nutrición humana, combinaciones con
  mecanismos complementarios). Reparte la generación entre `fisiologia-bioquimica`, `compuestos-toxicologia` y
  `nutricion-formulacion`, y pasa a cada generador el problema aprobado con sus soluciones actuales. Cada hipótesis
  sale completa del generador, con su efecto mínimo relevante y su criterio de falsación: nadie los completa después.
- Requisitos: ≥ 10 oportunidades, ≥ 6 en rumiantes; cada una con problema, mecanismo, candidatos, tipo de producto,
  evidencia resumida con etiquetas, lagunas e hipótesis falsables. Si faltan oportunidades en rumiantes, genera más
  (solo para los problemas de rumiantes y dentro del tope); si no es posible, avísalo.
- **Novedad**: `investigador-bibliografico` (literatura) y `economia-mercado-pi` (productos y patentes) comprueban
  cada una. Si cualquiera de los dos la da por existente, se reformula o se descarta, con motivo; si falta una de las
  dos comprobaciones, no se registra.
- Registra cada oportunidad el agente que la generó: JSON en `research/oportunidades/M1_<fecha>_<título normalizado>.json`
  (sin sobrescribir ficheros existentes) y `nutria --agente <generador> oportunidad nueva --datos <f>`; hipótesis
  con `nutria --agente <tu-rol> hipotesis nueva` en `research/hipotesis/M1_<fecha>_<título normalizado>_H<n>.json`.

## Paso 3 (M1c, fase "oportunidades"). Triage, crítica y puntuación ciega

Para cada oportunidad, en paralelo, cada especialista **propone** la compuerta de la que es propietario y la registra
sin `--firma-humana` (`nutria --agente <rol> puntuar compuerta <ID> <compuerta> <estado> --motivo "..."`):
- `regulacion-seguridad`: clasificación y vía (skill `triage-regulatorio`) → `regulatoria`;
- `compuestos-toxicologia`: seguridad → `seguridad`;
- `fisiologia-bioquimica`: destino digestivo con `nutria --agente fisiologia-bioquimica destino ruminal` → `destino_digestivo`;
- `desarrollo-industrial`: fabricación y método analítico → `analitica`;
- `economia-mercado-pi`: patentes y FTO preliminar → `propiedad_intelectual`; además ejecuta `nutria --agente economia-mercado-pi econ ganadero <toml> --exigir-fuentes`
  (entradas con fuente o `[SUP]`) antes de la puntuación;
- `sostenibilidad-bienestar`: impacto, bienestar y ética de la validación → `etica`;
- `critico-red-team`: intento de refutación, sin ver puntuaciones.

Una propuesta `bloqueada` actúa de inmediato; `superada` no cuenta hasta que la firme una persona. Después se leen las
compuertas efectivas del registro (`nutria --agente <tu-rol> oportunidad ver <ID> --sin-puntuaciones --json`): **una oportunidad con
alguna compuerta propuesta o efectiva `bloqueada` no se puntúa**; se informa como bloqueada con su motivo. Si no se
pueden leer sus compuertas, tampoco se puntúa (falla en cerrado).

Las demás las puntúan **tres `evaluador-independiente`** por oportunidad, con identificadores distintos
(`evaluador-1`, `evaluador-2`, `evaluador-3`) y, si es posible, modelos distintos, cada uno sin ver las puntuaciones de
los demás: puntúan los 9 criterios con la skill `puntuacion` y lo registran (`nutria --agente <id> puntuar registrar`).

Ranking (lo ejecuta el orquestador, no un evaluador): `nutria --agente director-cientifico puntuar ranking --perfil recomendado --sim 5000` y
`--perfil original`. Guarda ambas salidas e interprétalas en `research/revisiones/M1_ranking_<fecha>.md` (estados APTA,
CONDICIONAL, BLOQUEADA, INCOMPLETA, INSUFICIENTE; robustez frente a los pesos; desacuerdos; compuertas pendientes de
firma). `verificador-citas` ejecuta `nutria --agente verificador-citas verificar research/revisiones/M1_ranking_<fecha>.md`.

## Paso 4 (M1d, fase "planes"). Selección y planes

1. Propón las 3 mejores oportunidades no bloqueadas (las APTA antes que las CONDICIONALES): P(top-3), cuadrante,
   desacuerdos y compuertas pendientes.
2. **Punto de control humano**: la selección la decide una persona, que la firma en su terminal
   (`nutria decision registrar --entidad <ID> --decision "seleccionada M1" --motivo "..." --firma-humana "<nombre>"`).
3. Antes de cada plan, lee las compuertas efectivas y las decisiones del registro: si alguna compuerta está
   `bloqueada`, no se redacta el plan; si la selección no tiene firma humana en el registro, el plan es provisional
   (lo comprueba el workflow con la salida de `nutria`, no lo declara el agente que redacta el plan). Manda la última
   decisión firmada sobre la selección: las propuestas sin firma no cuentan, y "no seleccionada", "deseleccionada" o
   una retirada posterior la anulan.
4. Para cada oportunidad elegida, `director-cientifico` redacta `research/planes/<ID>.md` (si ya existe, copia antes
   la versión anterior a `research/planes/<ID>_anterior_<fecha>.md` y la cita: no se pierde): pregunta PECO; protocolo
   de revisión sistemática (PRISMA 2020); datos a extraer; análisis estadísticos; modelos y simulaciones justificados
   por la decisión que informan; evaluación regulatoria y económica; criterios de descarte; plan de validación
   in vitro → ex vivo → in vivo (solo diseño) con la lista de validación del director; cronograma y responsables.
5. `critico-red-team` revisa cada plan **con la misma lista de validación**, punto por punto (donantes o matadero,
   Directiva 2010/63/UE, in situ como in vivo, guía FEEDAP, BPL, potencia, registro previo, notificación a EFSA,
   duración de los ensayos frente a la adaptación de la microbiota); `director-cientifico` incorpora las correcciones.
6. Con el diseño experimental ya escrito (F10), `sostenibilidad-bienestar` **vuelve a proponer la compuerta `etica`**
   sobre ese diseño (el workflow informa si quedó registrada según la salida literal de `nutria --agente <tu-rol> puntuar compuerta`:
   una propuesta sin firma no sustituye una ética ya firmada por una persona), y `verificador-citas` ejecuta
   `nutria --agente <tu-rol> verificar research/planes/<ID>.md`.

## Entregables

- `research/revisiones/M1a_mapa_problemas_<fecha>.md` (verificado)
- ≥ 10 oportunidades (≥ 6 en rumiantes) e hipótesis registradas, sin duplicar las existentes, con las seis compuertas
  propuestas y puntuaciones de 3 evaluadores en las no bloqueadas
- `research/revisiones/M1_ranking_<fecha>.md` (verificado)
- Planes en `research/planes/` para las oportunidades elegidas, revisados por el crítico con la lista de validación,
  con la compuerta ética repropuesta sobre su diseño y verificados
- Resumen final: qué se encontró, qué se descartó y por qué, qué falta, qué agentes fallaron y qué decisiones
  humanas quedan pendientes (firmas de compuertas, selección, revisión de informes)
