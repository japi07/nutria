export const meta = {
  name: 'mision-1-auto',
  description: 'Misión 1 multiagente: mapa de problemas, ≥10 oportunidades con novedad comprobada, triage regulatorio y de seguridad, crítica adversarial, puntuación ciega por 3 evaluadores y planes revisados para las 3 mejores',
  whenToUse: 'Para ejecutar la primera misión exploratoria completa de forma orquestada y reproducible. Consume muchos tokens; la versión paso a paso con revisión humana es /mision-1. Argumentos opcionales: {"foco": "...", "n_oportunidades": 12, "fecha": "AAAA-MM-DD"}',
  phases: [
    { title: 'Mapa de problemas', detail: '4 enfoques independientes y consolidación' },
    { title: 'Oportunidades', detail: 'generación, comprobación de novedad y registro' },
    { title: 'Triage y crítica', detail: 'regulación, seguridad, industria, PI, ética y red team' },
    { title: 'Puntuación', detail: '3 evaluadores ciegos por oportunidad' },
    { title: 'Selección y planes', detail: 'ranking Monte Carlo, top 3 y planes revisados' },
  ],
}

const A = typeof args === 'string' ? { foco: args } : (args || {})
const FOCO = A.foco || 'nutrición y producción animal con prioridad en rumiantes, especialmente vacuno lechero, sin excluir otras especies'
const N_OPS = A.n_oportunidades || 12
const FECHA = A.fecha || 'no indicada'
const REGLAS = 'Sigue CLAUDE.md: etiquetas [PUB]/[DER]/[HIP]/[SUP]/[DESC], nada de citas de memoria (solo fuentes obtenidas con nutria buscar o WebSearch y verificadas), contexto biológico completo. Atribuye tus órdenes con "nutria --agente <tu-rol>". Si una fuente no responde, dilo y no lo sustituyas por suposiciones.'

const S = (props, required) => ({ type: 'object', properties: props, required })
const STR = { type: 'string' }
const STRS = { type: 'array', items: { type: 'string' } }

const PROBLEMAS = S({ problemas: { type: 'array', items: S({
  titulo: STR, especie: STR, etapa: STR, carga: STR, soluciones_actuales: STR, brecha: STR, mecanismos: STRS, fuentes: STRS,
}, ['titulo', 'especie', 'etapa', 'carga', 'brecha', 'mecanismos', 'fuentes']) } }, ['problemas'])

const CONSOLIDADO = S({
  prioritarios: { type: 'array', items: S({ id: STR, titulo: STR, especie: STR, etapa: STR, justificacion: STR, mecanismos: STRS },
    ['id', 'titulo', 'especie', 'etapa', 'justificacion', 'mecanismos']) },
  desacuerdos: STRS, archivo: STR,
}, ['prioritarios', 'archivo'])

const OPORTUNIDADES = S({ oportunidades: { type: 'array', items: S({
  titulo: STR, problema: STR, especie: STR, etapa: STR, mecanismo: STR, candidatos: STRS, tipo_producto: STR,
  evidencia_resumen: STR, lagunas: STRS, hipotesis: STRS, tecnica: STR,
}, ['titulo', 'problema', 'especie', 'etapa', 'mecanismo', 'candidatos', 'tipo_producto', 'evidencia_resumen', 'lagunas', 'hipotesis']) } },
['oportunidades'])

const NOVEDAD = S({
  veredicto: { type: 'string', enum: ['nueva', 'diferenciada', 'existente'] },
  literatura: STR, patentes: STR, productos: STR, reformulacion: STR,
}, ['veredicto', 'literatura', 'patentes', 'productos'])

const REGISTRO = S({ id: { type: 'string', pattern: '^O-\\d{4}$' }, hipotesis_ids: STRS }, ['id'])
const COMPUERTA = S({
  compuerta: STR, estado: { type: 'string', enum: ['superada', 'no_resuelta', 'bloqueada'] }, motivo: STR, resumen: STR, fuentes: STRS,
}, ['compuerta', 'estado', 'motivo', 'resumen'])
const CRITICA = S({
  veredicto: { type: 'string', enum: ['sostenida', 'debilitada', 'refutada', 'no_verificable'] }, debilidades: STRS, experimento_decisivo: STR,
}, ['veredicto', 'debilidades', 'experimento_decisivo'])
const PUNTUACION = S({ archivo: STR, registrado: { type: 'boolean' }, resumen: STR }, ['archivo', 'registrado'])
const RANKING = S({
  top: { type: 'array', items: S({ id: STR, titulo: STR, p_top: { type: 'number' }, motivo: STR }, ['id', 'titulo', 'motivo']) },
  archivo: STR, resumen: STR,
}, ['top', 'archivo', 'resumen'])
const PLAN = S({ archivo: STR, resumen: STR }, ['archivo', 'resumen'])

const norm = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
  .replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim()

// ---------------------------------------------------------------- M1a: mapa de problemas
phase('Mapa de problemas')
const LENTES = [
  { agente: 'fisiologia-bioquimica', foco: 'problemas fisiológicos y metabólicos sin solución nutricional suficientemente eficaz' },
  { agente: 'nutricion-formulacion', foco: 'eficiencia alimentaria, biodisponibilidad de nutrientes, interacciones entre ingredientes y metabolismo lipídico y proteico' },
  { agente: 'sostenibilidad-bienestar', foco: 'emisiones, eficiencia del nitrógeno, longevidad productiva, bienestar y alternativas a antimicrobianos y al óxido de zinc terapéutico' },
  { agente: 'economia-mercado-pi', foco: 'problemas con mayor coste para el ganadero y huecos de mercado que los productos actuales no cubren' },
]
// Barrera justificada: la consolidación necesita todos los mapas a la vez.
const mapas = (await parallel(LENTES.map((l) => () => agent(
  `${REGLAS}\nMisión 1, paso M1a. Ámbito: ${FOCO}. Tu enfoque: ${l.foco}.\n` +
  'Identifica 6-10 problemas productivos o fisiológicos. Para cada uno: especie y etapa; carga (incidencia o coste CON fuente verificada, o [DESC]); ' +
  'soluciones actuales y su eficacia documentada; brecha; mecanismos implicados; fuentes (IDs doi:/pmid: obtenidos con nutria buscar). Trabaja de forma independiente.',
  { label: `M1a:${l.agente}`, phase: 'Mapa de problemas', agentType: l.agente, schema: PROBLEMAS },
)))).filter(Boolean)
log(`Mapas recibidos: ${mapas.length}/${LENTES.length}`)

const mapa = await agent(
  `${REGLAS}\nConsolida estos mapas de problemas de especialistas independientes. Une duplicados, prioriza por carga × necesidad no cubierta con justificación ` +
  'y conserva los desacuerdos. Escribe research/revisiones/M1a_mapa_problemas.md. Devuelve 6-8 problemas prioritarios con ids P1..Pn.\n' + JSON.stringify(mapas),
  { label: 'M1a:consolidacion', phase: 'Mapa de problemas', agentType: 'director-cientifico', schema: CONSOLIDADO },
)
if (!mapa || !mapa.prioritarios || mapa.prioritarios.length === 0) {
  return { error: 'No se pudo consolidar el mapa de problemas', mapas: mapas.length }
}

// ---------------------------------------------------------------- M1b: oportunidades
phase('Oportunidades')
const GENERADORES = ['fisiologia-bioquimica', 'compuestos-toxicologia', 'nutricion-formulacion']
const generar = (p, i, existentes) => agent(
  `${REGLAS}\nMisión 1, paso M1b. Problema ${p.id}: ${p.titulo} (${p.especie}, ${p.etapa}). Mecanismos: ${(p.mecanismos || []).join('; ')}.\n` +
  'Genera 2-3 oportunidades originales siguiendo .claude/skills/hipotesis/SKILL.md (conexiones entre disciplinas, descubrimiento basado en literatura ABC, ' +
  'contradicciones entre estudios, reposicionamiento, combinaciones con mecanismos complementarios). Cada una con hipótesis falsables. No propongas dosis óptimas.' +
  (existentes.length ? `\nNo repitas estas oportunidades ya propuestas: ${existentes.join(' | ')}` : ''),
  { label: `M1b:gen:${p.id}${existentes.length ? ':extra' : ''}`, phase: 'Oportunidades', agentType: GENERADORES[i % GENERADORES.length], schema: OPORTUNIDADES },
)
const comprobarNovedad = (o, etiqueta) => agent(
  `${REGLAS}\nComprueba la novedad de esta oportunidad en literatura (nutria buscar pubmed/europepmc/openalex), patentes (nutria patentes) y productos comerciales (WebSearch). ` +
  'Veredicto: nueva | diferenciada | existente. Si es existente, propón una reformulación diferenciada o explica por qué descartarla.\n' + JSON.stringify(o),
  { label: `M1b:novedad:${etiqueta}`, phase: 'Oportunidades', agentType: 'investigador-bibliografico', schema: NOVEDAD },
).then((n) => (n ? { ...o, novedad: n } : null))

const porProblema = await pipeline(
  mapa.prioritarios,
  (p, _orig, i) => generar(p, i, []),
  (res, p) => parallel(((res && res.oportunidades) || []).map((o, j) => () => comprobarNovedad({ ...o, problema_id: p.id }, `${p.id}.${j + 1}`))),
)
const vistos = new Set()
let oportunidades = []
const anadir = (lista) => {
  for (const o of lista.flat().filter(Boolean)) {
    const k = norm(o.titulo)
    if (o.novedad.veredicto === 'existente' || vistos.has(k)) continue
    vistos.add(k)
    oportunidades.push(o)
  }
}
anadir(porProblema.filter(Boolean))
let ronda = 0
while (oportunidades.length < N_OPS && ronda < 2) {
  ronda += 1
  log(`Solo ${oportunidades.length} oportunidades nuevas o diferenciadas; ronda extra ${ronda}`)
  const titulos = oportunidades.map((o) => o.titulo)
  const extra = await pipeline(
    mapa.prioritarios,
    (p, _orig, i) => generar(p, i + ronda, titulos),
    (res, p) => parallel(((res && res.oportunidades) || []).map((o, j) => () => comprobarNovedad({ ...o, problema_id: p.id }, `${p.id}.r${ronda}.${j + 1}`))),
  )
  anadir(extra.filter(Boolean))
}
if (oportunidades.length < N_OPS) log(`Aviso: ${oportunidades.length} oportunidades (objetivo ${N_OPS}); se continúa con las disponibles`)
log(`${oportunidades.length} oportunidades pasan a registro y triage`)

// ---------------------------------------------------------------- M1c: registro, triage, crítica y puntuación ciega
const TRIAGE = [
  { agente: 'regulacion-seguridad', compuerta: 'regulatoria', tarea: 'clasificación y vía regulatoria (skill triage-regulatorio). Decide y REGISTRA con nutria puntuar compuerta las compuertas "regulatoria" y "seguridad"; devuelve la regulatoria y resume la de seguridad' },
  { agente: 'desarrollo-industrial', compuerta: 'analitica', tarea: 'viabilidad de fabricación y método analítico; registra la compuerta "analitica"' },
  { agente: 'economia-mercado-pi', compuerta: 'propiedad_intelectual', tarea: 'antecedentes de patentes y libertad de operación preliminar; registra la compuerta "propiedad_intelectual"' },
  { agente: 'sostenibilidad-bienestar', compuerta: 'etica', tarea: 'impacto ambiental, bienestar y ética de la validación futura; registra la compuerta "etica"' },
]
const EVALUADORES = [
  { id: 'evaluador-cientifico', enfoque: 'científico (evidencia, mecanismo, seguridad)' },
  { id: 'evaluador-industrial-regulatorio', enfoque: 'industrial y regulatorio (fabricación, análisis, vía y plazos)' },
  { id: 'evaluador-economico', enfoque: 'económico y de mercado (retorno para el ganadero, mercado, diferenciación)' },
]

const evaluadas = await pipeline(
  oportunidades,
  (o, _orig, i) => agent(
    `${REGLAS}\nRegistra esta oportunidad: escribe research/oportunidades/M1_${String(i + 1).padStart(2, '0')}.json conforme a src/nutria/data/schemas/oportunidad.schema.json ` +
    '(rellena "novedad" con la comprobación realizada) y ejecuta nutria --agente director-cientifico oportunidad nueva --datos <fichero>. Registra cada hipótesis como JSON ' +
    'conforme a hipotesis.schema.json (con efecto mínimo relevante y criterio de falsación) con nutria hipotesis nueva. Devuelve el ID asignado.\n' + JSON.stringify(o),
    { label: `registro:${i + 1}`, phase: 'Oportunidades', agentType: 'director-cientifico', schema: REGISTRO },
  ).then((r) => (r ? { ...o, id: r.id, hipotesis_ids: r.hipotesis_ids || [] } : null)),
  (o) => o && parallel([
    ...TRIAGE.map((t) => () => agent(
      `${REGLAS}\nOportunidad ${o.id}: ${o.titulo}. Tu tarea: ${t.tarea}. Consulta nutria oportunidad ver ${o.id} --sin-puntuaciones --json.`,
      { label: `triage:${t.compuerta}:${o.id}`, phase: 'Triage y crítica', agentType: t.agente, schema: COMPUERTA },
    )),
    () => agent(
      `${REGLAS}\nIntenta refutar la oportunidad ${o.id} (nutria oportunidad ver ${o.id} --sin-puntuaciones --json) y sus hipótesis con tu lista de comprobación completa.`,
      { label: `critica:${o.id}`, phase: 'Triage y crítica', agentType: 'critico-red-team', schema: CRITICA },
    ),
  ]).then((rs) => ({ ...o, compuertas: rs.slice(0, TRIAGE.length), critica: rs[TRIAGE.length] })),
  (o) => o && parallel(EVALUADORES.map((ev) => () => agent(
    `${REGLAS}\nActúa como evaluador independiente con enfoque ${ev.enfoque}. Puntúa la oportunidad ${o.id} siguiendo .claude/skills/puntuacion/SKILL.md: ` +
    `lee nutria oportunidad ver ${o.id} --sin-puntuaciones --json y la crítica de abajo; NO leas puntuaciones de otros evaluadores. ` +
    `Guarda research/oportunidades/${o.id}_puntuacion_${ev.id}.json con "evaluador": "${ev.id}" y regístralo con nutria --agente ${ev.id} puntuar registrar --datos <fichero>.\n` +
    'Crítica del red team: ' + JSON.stringify(o.critica),
    { label: `puntuacion:${ev.id}:${o.id}`, phase: 'Puntuación', agentType: 'director-cientifico', schema: PUNTUACION },
  ))).then((p) => ({ ...o, puntuaciones: p })),
)

// ---------------------------------------------------------------- M1d: ranking, selección y planes
phase('Selección y planes')
const ranking = await agent(
  `${REGLAS}\nEjecuta nutria puntuar ranking --perfil recomendado --sim 5000 --semilla 42 y también --perfil original. Escribe research/revisiones/M1_ranking.md con ambas salidas ` +
  'e interpretación (robustez frente a los pesos, desacuerdos entre evaluadores, compuertas pendientes). Propón las 3 mejores oportunidades NO bloqueadas. ' +
  'La selección es una PROPUESTA pendiente de firma humana: regístrala con nutria decision registrar --decision "propuesta top-3 M1" sin --firma-humana.',
  { label: 'ranking', phase: 'Selección y planes', agentType: 'director-cientifico', schema: RANKING },
)
const top = ((ranking && ranking.top) || []).slice(0, 3)
const planes = await pipeline(
  top,
  (t) => agent(
    `${REGLAS}\nRedacta el plan de investigación computacional de ${t.id} (${t.titulo}) en research/planes/${t.id}.md: pregunta PECO; protocolo de revisión sistemática (PRISMA 2020); ` +
    'datos a extraer; análisis estadísticos; modelos y simulaciones justificados por la decisión que informan; evaluación regulatoria y económica; criterios de descarte fijados de antemano; ' +
    'plan de validación in vitro → ex vivo → in vivo (solo diseño, con cálculo de potencia); cronograma y agentes responsables.',
    { label: `plan:${t.id}`, phase: 'Selección y planes', agentType: 'director-cientifico', schema: PLAN },
  ),
  (plan, t) => plan && agent(
    `${REGLAS}\nRevisa de forma adversarial el plan ${plan.archivo} de ${t.id}: qué falta, qué podría invalidarlo y qué criterio de descarte es débil.`,
    { label: `critica-plan:${t.id}`, phase: 'Selección y planes', agentType: 'critico-red-team', schema: CRITICA },
  ).then((c) => ({ ...t, plan, critica_plan: c })),
  (r, t) => r && agent(
    `${REGLAS}\nIncorpora en ${r.plan.archivo} las correcciones necesarias y añade una sección "Respuesta a la crítica" (qué cambia, qué no y por qué).\n` + JSON.stringify(r.critica_plan),
    { label: `revision-plan:${t.id}`, phase: 'Selección y planes', agentType: 'director-cientifico', schema: PLAN },
  ).then((p2) => ({ ...r, plan_revisado: p2 })),
)

return {
  fecha: FECHA,
  foco: FOCO,
  problemas_prioritarios: mapa.prioritarios.map((p) => `${p.id}: ${p.titulo}`),
  oportunidades: evaluadas.filter(Boolean).map((o) => ({
    id: o.id, titulo: o.titulo, novedad: o.novedad && o.novedad.veredicto,
    critica: o.critica && o.critica.veredicto,
    compuertas: (o.compuertas || []).filter(Boolean).map((c) => `${c.compuerta}:${c.estado}`),
    evaluadores: (o.puntuaciones || []).filter((p) => p && p.registrado).length,
  })),
  ranking: ranking && ranking.archivo,
  top3: planes.filter(Boolean).map((p) => ({ id: p.id, plan: p.plan && p.plan.archivo, critica: p.critica_plan && p.critica_plan.veredicto })),
  pendiente: 'La selección del top 3 y las compuertas son propuestas: requieren validación humana (--firma-humana).',
}
