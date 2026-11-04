---
name: mision-1
description: Primera misión exploratoria paso a paso con revisión humana entre fases. Construye el mapa de problemas, genera al menos 10 oportunidades, hace el triage y la puntuación independiente, y redacta planes computacionales para las 3 mejores.
argument-hint: "[foco opcional, p. ej. vacuno lechero en transición]"
disable-model-invocation: true
---

# Misión 1: investigación exploratoria

Foco indicado: $ARGUMENTS
Si no hay foco: nutrición y producción animal con prioridad en rumiantes, sobre todo vacuno lechero, sin excluir
otras especies.

Esta misión es una **revisión de alcance con exploración de horizonte** (no una revisión sistemática). Su producto
son oportunidades priorizadas e hipótesis falsables, no efectos demostrados.

## Paso 0. Preparación

1. `nutria doctor`. Si alguna fuente no es accesible, dilo al usuario y continúa con las demás.
2. `nutria estado`. No dupliques oportunidades ya registradas.
3. Lee `docs/PROMPT_MAESTRO_v2.md` §18 (primera misión).

## Paso 1 (M1a). Mapa de problemas

Lanza en paralelo, de forma independiente:
- `fisiologia-bioquimica`: problemas fisiológicos y metabólicos sin solución nutricional suficientemente eficaz
  (transición, movilización lipídica e hígado graso, cetosis, hipocalcemia subclínica, estrés oxidativo, inflamación,
  estrés por calor, salud ruminal e intestinal, fertilidad).
- `nutricion-formulacion`: eficiencia alimentaria, biodisponibilidad, interacciones entre ingredientes, metabolismo
  lipídico y proteico.
- `sostenibilidad-bienestar`: emisiones y eficiencia del nitrógeno, longevidad productiva, bienestar, alternativas a
  antimicrobianos y al óxido de zinc terapéutico en lechones.
- `economia-mercado-pi`: problemas con mayor coste para el ganadero y huecos de mercado.

Cada uno devuelve problemas con: especie y etapa, carga (incidencia o coste con fuente, o `[DESC]`), soluciones
actuales y su eficacia documentada, brecha, mecanismos implicados y fuentes.

Consolida en `research/revisiones/M1a_mapa_problemas.md`: une duplicados, prioriza por carga × necesidad no cubierta
(justificado) y conserva los desacuerdos.

**Punto de control humano**: presenta el mapa y pregunta si se ajusta el foco antes de continuar.

## Paso 2 (M1b). Al menos 10 oportunidades

- Genera con la skill `hipotesis` (conexiones entre disciplinas, descubrimiento basado en literatura ABC,
  contradicciones entre estudios, reposicionamiento desde otras especies o la nutrición humana, combinaciones con
  mecanismos complementarios). Reparte la generación entre `fisiologia-bioquimica`, `compuestos-toxicologia` y
  `nutricion-formulacion`.
- Requisitos: ≥ 10 oportunidades, ≥ 6 en rumiantes; cada una con problema, mecanismo, candidatos, tipo de producto,
  evidencia resumida con etiquetas, lagunas e hipótesis falsables.
- **Novedad**: `investigador-bibliografico` (literatura) y `economia-mercado-pi` (productos y patentes) comprueban
  cada una. Reformula o descarta lo que ya exista sin diferenciación, con motivo.
- Registra: JSON en `research/oportunidades/` y `nutria --agente <agente> oportunidad nueva --datos <f>`; hipótesis
  con `nutria hipotesis nueva`.

## Paso 3 (M1c). Triage y evaluación independiente

Para cada oportunidad, en paralelo:
- `regulacion-seguridad`: clasificación y vía; compuertas `regulatoria` y `seguridad`.
- `desarrollo-industrial`: compuerta `analitica`.
- `economia-mercado-pi`: compuerta `propiedad_intelectual` (preliminar).
- `sostenibilidad-bienestar`: compuerta `etica`.
- `critico-red-team`: intento de refutación, sin ver puntuaciones.

Después, **tres evaluadores independientes**: tres subagentes `director-cientifico` con enfoques científico,
industrial-regulatorio y económico, cada uno sin ver las puntuaciones de los demás. Puntúan los 8 criterios con la
skill `puntuacion` y lo registran (`nutria puntuar registrar`).

Ranking: `nutria puntuar ranking --perfil recomendado --sim 5000` y `--perfil original`. Guarda ambas salidas e
interprétalas en `research/revisiones/M1_ranking.md` (robustez, desacuerdos, compuertas pendientes).

## Paso 4 (M1d). Selección y planes

1. Propón las 3 mejores oportunidades no bloqueadas: P(top-3), cuadrante, desacuerdos y compuertas pendientes.
2. **Punto de control humano**: la selección la confirma una persona
   (`nutria decision registrar --entidad <ID> --decision "seleccionada M1" --motivo "..." --firma-humana "<nombre>"`).
3. Para cada una, `director-cientifico` redacta `research/planes/<ID>.md`: pregunta PECO; protocolo de revisión
   sistemática (PRISMA 2020); datos a extraer; análisis estadísticos; modelos y simulaciones justificados por la
   decisión que informan; evaluación regulatoria y económica; criterios de descarte; plan de validación in vitro →
   ex vivo → in vivo (solo diseño); cronograma y responsables.
4. `critico-red-team` revisa cada plan; incorpora las correcciones.

## Entregables

- `research/revisiones/M1a_mapa_problemas.md`
- ≥ 10 oportunidades e hipótesis registradas, con compuertas y puntuaciones de 3 evaluadores
- `research/revisiones/M1_ranking.md`
- 3 planes en `research/planes/`, revisados por el crítico
- Resumen final: qué se encontró, qué se descartó y por qué, qué falta y qué decisiones humanas quedan pendientes
