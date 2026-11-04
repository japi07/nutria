---
name: mision-1
description: Primera misión exploratoria paso a paso con revisión humana entre fases. Construye el mapa de problemas, genera al menos 10 oportunidades, hace el triage con las seis compuertas y la puntuación ciega por evaluadores independientes, y redacta planes computacionales para las oportunidades que elija una persona.
argument-hint: "[foco opcional, p. ej. vacuno lechero en transición]"
disable-model-invocation: true
---

# Misión 1: investigación exploratoria

Foco indicado: $ARGUMENTS
Si no hay foco: nutrición y producción animal con prioridad en rumiantes, sobre todo vacuno lechero, sin excluir
otras especies.

Esta misión es una **revisión de alcance con exploración de horizonte** (no una revisión sistemática). Su producto
son oportunidades priorizadas e hipótesis falsables, no efectos demostrados.

`/mision-1-auto` ejecuta las mismas fases como workflow, una por invocación y con un punto de control humano entre
ellas: `{"fase": "mapa"}` (por defecto) → `{"fase": "oportunidades", "problemas": [...aprobados por una persona...]}`
→ `{"fase": "planes", "seleccion": ["O-xxxx", ...]}`. Cada entregable (mapa, ranking, planes) pasa por
`verificador-citas` (`nutria verificar <md>`) antes de presentarse.

## Paso 0. Preparación

1. `nutria doctor`. Si alguna fuente no es accesible, dilo al usuario y continúa con las demás.
2. `nutria estado`. No dupliques oportunidades ya registradas.
3. Lee `docs/PROMPT_MAESTRO_v2.md` §18 (primera misión).

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

`director-cientifico` consolida en `research/revisiones/M1a_mapa_problemas.md`: une duplicados, prioriza por carga ×
necesidad no cubierta (justificado), conserva los desacuerdos y **conserva las soluciones actuales de cada problema**
(los generadores las necesitan para no proponer lo que ya existe). `verificador-citas` ejecuta
`nutria verificar research/revisiones/M1a_mapa_problemas.md`.

**Punto de control humano**: presenta el mapa y su verificación; una persona aprueba la lista de problemas (o ajusta
el foco) antes de continuar.

## Paso 2 (M1b, fase "oportunidades"). Al menos 10 oportunidades

- Genera con la skill `hipotesis` (conexiones entre disciplinas, descubrimiento basado en literatura ABC,
  contradicciones entre estudios, reposicionamiento desde otras especies o la nutrición humana, combinaciones con
  mecanismos complementarios). Reparte la generación entre `fisiologia-bioquimica`, `compuestos-toxicologia` y
  `nutricion-formulacion`, y pasa a cada generador el problema aprobado con sus soluciones actuales.
- Requisitos: ≥ 10 oportunidades, ≥ 6 en rumiantes; cada una con problema, mecanismo, candidatos, tipo de producto,
  evidencia resumida con etiquetas, lagunas e hipótesis falsables.
- **Novedad**: `investigador-bibliografico` (literatura) y `economia-mercado-pi` (productos y patentes) comprueban
  cada una. Reformula o descarta lo que ya exista sin diferenciación, con motivo.
- Registra: JSON en `research/oportunidades/` y `nutria --agente <agente> oportunidad nueva --datos <f>`; hipótesis
  con `nutria hipotesis nueva`.

## Paso 3 (M1c, fase "oportunidades"). Triage, crítica y puntuación ciega

Para cada oportunidad, en paralelo, cada especialista **propone** su compuerta y la registra sin `--firma-humana`
(`nutria --agente <rol> puntuar compuerta <ID> <compuerta> <estado> --motivo "..."`):
- `regulacion-seguridad`: clasificación y vía (skill `triage-regulatorio`) → `regulatoria`;
- `compuestos-toxicologia`: seguridad → `seguridad`;
- `fisiologia-bioquimica`: destino digestivo con `nutria destino ruminal` → `destino_digestivo`;
- `desarrollo-industrial`: fabricación y método analítico → `analitica`;
- `economia-mercado-pi`: patentes y FTO preliminar → `propiedad_intelectual`; además ejecuta `nutria econ ganadero`
  (entradas con fuente o `[SUP]`) antes de la puntuación;
- `sostenibilidad-bienestar`: impacto, bienestar y ética de la validación → `etica`;
- `critico-red-team`: intento de refutación, sin ver puntuaciones.

Una propuesta `bloqueada` actúa de inmediato; `superada` no cuenta hasta que la firme una persona.

Después, **tres `evaluador-independiente`** por oportunidad, con identificadores distintos (`evaluador-1`,
`evaluador-2`, `evaluador-3`) y, si es posible, modelos distintos, cada uno sin ver las puntuaciones de los demás:
puntúan los 9 criterios con la skill `puntuacion` y lo registran (`nutria --agente <id> puntuar registrar`).

Ranking (lo ejecuta el orquestador, no un evaluador): `nutria puntuar ranking --perfil recomendado --sim 5000` y
`--perfil original`. Guarda ambas salidas e interprétalas en `research/revisiones/M1_ranking.md` (estados APTA,
CONDICIONAL, BLOQUEADA, INCOMPLETA, INSUFICIENTE; robustez frente a los pesos; desacuerdos; compuertas pendientes de
firma). `verificador-citas` ejecuta `nutria verificar research/revisiones/M1_ranking.md`.

## Paso 4 (M1d, fase "planes"). Selección y planes

1. Propón las 3 mejores oportunidades no bloqueadas (las APTA antes que las CONDICIONALES): P(top-3), cuadrante,
   desacuerdos y compuertas pendientes.
2. **Punto de control humano**: la selección la decide una persona, que la firma en su terminal
   (`nutria decision registrar --entidad <ID> --decision "seleccionada M1" --motivo "..." --firma-humana "<nombre>"`).
3. Para cada oportunidad elegida, `director-cientifico` redacta `research/planes/<ID>.md`: pregunta PECO; protocolo
   de revisión sistemática (PRISMA 2020); datos a extraer; análisis estadísticos; modelos y simulaciones justificados
   por la decisión que informan; evaluación regulatoria y económica; criterios de descarte; plan de validación
   in vitro → ex vivo → in vivo (solo diseño) con la lista de validación del director; cronograma y responsables.
4. `critico-red-team` revisa cada plan; `director-cientifico` incorpora las correcciones; `verificador-citas`
   ejecuta `nutria verificar research/planes/<ID>.md`.

## Entregables

- `research/revisiones/M1a_mapa_problemas.md` (verificado)
- ≥ 10 oportunidades e hipótesis registradas, con las seis compuertas propuestas y puntuaciones de 3 evaluadores
- `research/revisiones/M1_ranking.md` (verificado)
- Planes en `research/planes/` para las oportunidades elegidas, revisados por el crítico y verificados
- Resumen final: qué se encontró, qué se descartó y por qué, qué falta, qué agentes fallaron y qué decisiones
  humanas quedan pendientes (firmas de compuertas, selección, revisión de informes)
