export const meta = {
  name: 'mision-1-auto',
  description: 'Misión 1 multiagente por fases con revisión humana entre ellas: mapa de problemas (fase "mapa"); oportunidades con novedad comprobada, seis compuertas propuestas sin firma, crítica, puntuación ciega por 3 evaluadores independientes y ranking (fase "oportunidades"); planes revisados y verificados de las oportunidades que elija una persona (fase "planes")',
  whenToUse: 'Para ejecutar la primera misión exploratoria orquestada, una fase por invocación: {"fase": "mapa", "foco": "..."} (por defecto); después {"fase": "oportunidades", "problemas": [problemas del mapa aprobados por una persona], "n_oportunidades": 12}; después {"fase": "planes", "seleccion": ["O-0003", ...]} con los IDs que elija una persona. Opcional: "fecha": "AAAA-MM-DD". El control de flujo es fijo, pero cada agente puede dar resultados distintos en cada ejecución. Consume muchos tokens; la versión paso a paso es /mision-1.',
  phases: [
    { title: 'Mapa de problemas', detail: '4 enfoques independientes, consolidación y verificación de citas' },
    { title: 'Oportunidades', detail: 'generación con las soluciones actuales, novedad y registro' },
    { title: 'Triage y crítica', detail: 'seis compuertas propuestas sin firma, modelo del ganadero y red team' },
    { title: 'Puntuación', detail: '3 evaluadores independientes y ciegos con modelos distintos' },
    { title: 'Ranking', detail: 'ranking Monte Carlo interpretado y verificado' },
    { title: 'Planes', detail: 'plan, crítica, revisión y verificación de citas por oportunidad elegida' },
  ],
}

// ---------------------------------------------------------------- argumentos
const leerArgs = (a) => {
  if (typeof a === 'string') {
    const t = a.trim()
    if (t.startsWith('{')) {
      try { return JSON.parse(t) } catch (e) { return { foco: t } }
    }
    return t ? { foco: t } : {}
  }
  return a && typeof a === 'object' && !Array.isArray(a) ? a : {}
}
const A = leerArgs(args)
const FASES = ['mapa', 'oportunidades', 'planes']
const FASE = A.fase === undefined || A.fase === null || A.fase === '' ? 'mapa' : String(A.fase)
if (!FASES.includes(FASE)) {
  return { error: `Fase no válida: "${FASE}". Opciones: ${FASES.join(', ')} (por defecto "mapa").` }
}
const FOCO = A.foco || 'nutrición y producción animal con prioridad en rumiantes, especialmente vacuno lechero, sin excluir otras especies'
const N_OPS = Number.isInteger(A.n_oportunidades) && A.n_oportunidades > 0 ? A.n_oportunidades : 12
const FECHA = A.fecha || 'no indicada'
const REGLAS = 'Sigue CLAUDE.md: etiquetas [PUB]/[DER]/[HIP]/[SUP]/[DESC], nada de citas de memoria (solo fuentes obtenidas con nutria buscar o WebSearch y verificadas), contexto biológico completo. Atribuye tus órdenes con "nutria --agente <tu-rol>" y nunca uses --firma-humana. Si una fuente no responde, dilo y no lo sustituyas por suposiciones.'

// ---------------------------------------------------------------- fallos y presupuesto
// Los fallos de agentes se cuentan y se informan; nunca se descartan en silencio.
const fallos = []
const lanzar = async (prompt, opts) => {
  let r = null
  let motivo = 'sin resultado (agente omitido o con error terminal)'
  try {
    r = await agent(prompt, opts)
  } catch (e) {
    r = null
    motivo = `error: ${String((e && e.message) || e).slice(0, 200)}`
  }
  if (r === null || r === undefined) {
    fallos.push({ agente: opts.label, fase: opts.phase, motivo })
    log(`Fallo de agente: ${opts.label} (${motivo})`)
    return null
  }
  return r
}
const resumenFallos = () => ({ total: fallos.length, detalle: fallos })
const UMBRAL_TOKENS = 100000
const presupuestoBajo = () => typeof budget !== 'undefined' && budget !== null && Boolean(budget.total) &&
  budget.remaining() < UMBRAL_TOKENS

// ---------------------------------------------------------------- esquemas
const S = (props, required) => ({ type: 'object', properties: props, required })
const STR = { type: 'string' }
const STRS = { type: 'array', items: { type: 'string' } }
const NUM = { type: 'number' }
const BOOL = { type: 'boolean' }

const PROBLEMAS = S({ problemas: { type: 'array', items: S({
  titulo: STR, especie: STR, etapa: STR, carga: STR, soluciones_actuales: STR, brecha: STR, mecanismos: STRS, fuentes: STRS,
}, ['titulo', 'especie', 'etapa', 'carga', 'soluciones_actuales', 'brecha', 'mecanismos', 'fuentes']) } }, ['problemas'])

const CONSOLIDADO = S({
  prioritarios: { type: 'array', items: S({
    id: STR, titulo: STR, especie: STR, etapa: STR, justificacion: STR, mecanismos: STRS,
    soluciones_actuales: STR, brecha: STR, fuentes: STRS,
  }, ['id', 'titulo', 'especie', 'etapa', 'justificacion', 'mecanismos', 'soluciones_actuales']) },
  desacuerdos: STRS, archivo: STR,
}, ['prioritarios', 'archivo'])

const OPORTUNIDADES = S({ oportunidades: { type: 'array', items: S({
  titulo: STR, problema: STR, especie: STR, etapa: STR, mecanismo: STR, candidatos: STRS, tipo_producto: STR,
  evidencia_resumen: STR, diferenciacion: STR, lagunas: STRS, hipotesis: STRS, tecnica: STR,
}, ['titulo', 'problema', 'especie', 'etapa', 'mecanismo', 'candidatos', 'tipo_producto', 'evidencia_resumen', 'diferenciacion', 'lagunas', 'hipotesis']) } },
['oportunidades'])

const NOVEDAD = S({
  veredicto: { type: 'string', enum: ['nueva', 'diferenciada', 'existente'] },
  literatura: STR, patentes: STR, productos: STR, reformulacion: STR,
}, ['veredicto', 'literatura', 'patentes', 'productos'])

const REGISTRO = S({ id: { type: 'string', pattern: '^O-\\d{4}$' }, hipotesis_ids: STRS }, ['id'])
const COMPUERTA = S({
  compuerta: STR, estado: { type: 'string', enum: ['superada', 'no_resuelta', 'bloqueada'] }, motivo: STR, resumen: STR,
  archivo: STR, fuentes: STRS,
}, ['compuerta', 'estado', 'motivo', 'resumen'])
const CRITICA = S({
  veredicto: { type: 'string', enum: ['sostenida', 'debilitada', 'refutada', 'no_verificable'] }, debilidades: STRS, experimento_decisivo: STR,
}, ['veredicto', 'debilidades', 'experimento_decisivo'])
const PUNTUACION = S({ archivo: STR, registrado: BOOL, resumen: STR }, ['archivo', 'registrado'])
const RANKING = S({
  top: { type: 'array', items: S({ id: { type: 'string', pattern: '^O-\\d{4}$' }, titulo: STR, estado: STR, p_top: NUM, motivo: STR }, ['id', 'titulo', 'motivo']) },
  archivo: STR, resumen: STR,
}, ['top', 'archivo', 'resumen'])
const PLAN = S({ archivo: STR, resumen: STR, seleccion_firmada: BOOL }, ['archivo', 'resumen'])
const VERIFICACION = S({
  archivo: STR, referencias: NUM, verificadas: NUM, completa: BOOL,
  problemas: { type: 'array', items: S({ ref: STR, estado: STR, motivo: STR }, ['ref', 'estado', 'motivo']) },
  resumen: STR,
}, ['archivo', 'completa', 'problemas', 'resumen'])

// ---------------------------------------------------------------- equipo
const LENTES = [
  { agente: 'fisiologia-bioquimica', foco: 'problemas fisiológicos y metabólicos sin solución nutricional suficientemente eficaz' },
  { agente: 'nutricion-formulacion', foco: 'eficiencia alimentaria, biodisponibilidad de nutrientes, interacciones entre ingredientes y metabolismo lipídico y proteico' },
  { agente: 'sostenibilidad-bienestar', foco: 'emisiones, eficiencia del nitrógeno, longevidad productiva, bienestar y alternativas a antimicrobianos y al óxido de zinc terapéutico' },
  { agente: 'economia-mercado-pi', foco: 'problemas con mayor coste para el ganadero y huecos de mercado que los productos actuales no cubren' },
]
const GENERADORES = ['fisiologia-bioquimica', 'compuestos-toxicologia', 'nutricion-formulacion']
// Cada especialista PROPONE su compuerta (sin firma humana): solo una persona la firma.
const TRIAGE = [
  { agente: 'regulacion-seguridad', compuerta: 'regulatoria', tarea: 'clasificación y vía regulatoria con la skill triage-regulatorio (primero pienso dietético, después medicamento por alegación y por función, sustancias prohibidas o restringidas y alegaciones)' },
  { agente: 'compuestos-toxicologia', compuerta: 'seguridad', tarea: 'seguridad para la especie diana, el consumidor, el usuario y el medio ambiente a la dosis prevista' },
  { agente: 'fisiologia-bioquimica', compuerta: 'destino_digestivo', tarea: 'destino digestivo a la dosis prevista; en rumiantes, calcula con nutria destino ruminal (entradas con fuente o [SUP])' },
  { agente: 'desarrollo-industrial', compuerta: 'analitica', tarea: 'viabilidad de fabricación y método analítico para el activo o un marcador' },
  { agente: 'economia-mercado-pi', compuerta: 'propiedad_intelectual', tarea: 'antecedentes de patentes y libertad de operación preliminar; además, antes de la puntuación, ejecuta nutria econ ganadero (entradas con fuente o [SUP]) y deja el modelo y la salida en research/datos/<ID>/' },
  { agente: 'sostenibilidad-bienestar', compuerta: 'etica', tarea: 'impacto ambiental, bienestar y ética de la validación futura' },
]
// Tres evaluadores independientes con modelos distintos; identificadores estables por puesto (un reintento
// sobrescribe lo que hubiera registrado el intento fallido en vez de añadir un cuarto evaluador).
const EVALUADORES = [
  { id: 'evaluador-1', modelo: 'opus' },
  { id: 'evaluador-2', modelo: 'sonnet' },
  { id: 'evaluador-3', modelo: 'fable' },
]
const MODELO_REINTENTO = 'opus'

const puntuar = async (ev, opId, prompt) => {
  const base = { phase: 'Puntuación', agentType: 'evaluador-independiente', schema: PUNTUACION }
  const r = await lanzar(prompt, { ...base, label: `puntuacion:${ev.id}:${opId}`, model: ev.modelo })
  if (r) return { ...r, evaluador: ev.id, modelo: ev.modelo, reintento: false }
  if (presupuestoBajo()) {
    log(`Reintento de ${ev.id} para ${opId} omitido: quedan menos de ${UMBRAL_TOKENS} tokens de presupuesto`)
    return { evaluador: ev.id, modelo: ev.modelo, reintento: false, registrado: false, fallo: 'sin resultado; reintento omitido por presupuesto' }
  }
  log(`${ev.id} (${ev.modelo}) no devolvió resultado para ${opId}; se reintenta con ${MODELO_REINTENTO}`)
  const r2 = await lanzar(prompt, { ...base, label: `puntuacion:${ev.id}:${opId}:reintento`, model: MODELO_REINTENTO })
  if (r2) return { ...r2, evaluador: ev.id, modelo: MODELO_REINTENTO, reintento: true }
  return { evaluador: ev.id, modelo: MODELO_REINTENTO, reintento: true, registrado: false, fallo: 'sin resultado tras el reintento' }
}

const verificarCitas = (archivo, etiqueta, fase) => lanzar(
  `${REGLAS}\nVerifica las referencias del entregable ${archivo} antes de que se presente: ejecuta nutria --agente verificador-citas verificar ${archivo} ` +
  '(existencia, correspondencia y retractación, en línea) y comprueba que cada afirmación [PUB] tiene una fuente que la respalda. No reescribas el documento ' +
  'ni registres verificaciones manuales (las referencias con solo URL las registra una persona). "completa" es true solo si todas las referencias quedaron ' +
  'verificadas, con la retractación comprobada y ninguna pendiente por falta de red. Lista cada problema.',
  { label: `verificacion:${etiqueta}`, phase: fase, agentType: 'verificador-citas', schema: VERIFICACION },
)

const norm = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  .replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim()

// ================================================================ fase "mapa" (M1a)
if (FASE === 'mapa') {
  phase('Mapa de problemas')
  log(`Fase "mapa". Estimación: ${LENTES.length + 2} agentes (${LENTES.length} enfoques, consolidación y verificación de citas)`)
  // Barrera justificada: la consolidación necesita todos los mapas a la vez.
  const mapas = (await parallel(LENTES.map((l) => () => lanzar(
    `${REGLAS}\nMisión 1, paso M1a. Ámbito: ${FOCO}. Tu enfoque: ${l.foco}.\n` +
    'Identifica 6-10 problemas productivos o fisiológicos. Para cada uno: especie y etapa; carga (incidencia o coste CON fuente verificada, o [DESC]); ' +
    'soluciones actuales y su eficacia documentada (con fuentes, o [DESC]); brecha; mecanismos implicados; fuentes (IDs doi:/pmid: obtenidos con nutria buscar). Trabaja de forma independiente.',
    { label: `M1a:${l.agente}`, phase: 'Mapa de problemas', agentType: l.agente, schema: PROBLEMAS },
  ).then((r) => (r ? { enfoque: l.agente, problemas: r.problemas } : null))))).filter(Boolean)
  log(`Mapas recibidos: ${mapas.length}/${LENTES.length}`)
  if (!mapas.length) return { fase: FASE, error: 'Ningún enfoque devolvió problemas', fallos: resumenFallos() }

  const faltan = LENTES.filter((l) => !mapas.some((m) => m.enfoque === l.agente)).map((l) => l.agente)
  const mapa = await lanzar(
    `${REGLAS}\nConsolida estos mapas de problemas de especialistas independientes. Une duplicados, prioriza por carga × necesidad no cubierta con justificación ` +
    'y conserva los desacuerdos. Conserva para cada problema sus soluciones actuales y su eficacia documentada, con fuentes: los generadores de oportunidades las necesitan. ' +
    'Escribe research/revisiones/M1a_mapa_problemas.md. Devuelve 6-8 problemas prioritarios con ids P1..Pn.' +
    (faltan.length ? `\nFaltan los enfoques ${faltan.join(', ')} por fallo de agente: dilo en el mapa como limitación.` : '') +
    '\n' + JSON.stringify(mapas),
    { label: 'M1a:consolidacion', phase: 'Mapa de problemas', agentType: 'director-cientifico', schema: CONSOLIDADO },
  )
  if (!mapa || !Array.isArray(mapa.prioritarios) || mapa.prioritarios.length === 0) {
    return { fase: FASE, error: 'No se pudo consolidar el mapa de problemas', mapas: mapas.length, fallos: resumenFallos() }
  }
  const verificacion = await verificarCitas(mapa.archivo, 'M1a', 'Mapa de problemas')

  return {
    fase: FASE,
    fecha: FECHA,
    foco: FOCO,
    mapa: mapa.archivo,
    enfoques_recibidos: `${mapas.length}/${LENTES.length}`,
    problemas: mapa.prioritarios,
    desacuerdos: mapa.desacuerdos || [],
    verificacion_citas: verificacion,
    fallos: resumenFallos(),
    siguiente: 'Punto de control humano: una persona revisa el mapa y su verificación de citas, aprueba la lista de problemas (puede editarla) ' +
      'y relanza con {"fase": "oportunidades", "problemas": [<problemas aprobados, con sus soluciones_actuales>]}.',
  }
}

// ================================================================ fase "oportunidades" (M1b y M1c)
if (FASE === 'oportunidades') {
  const PROBLEMAS_APROBADOS = (Array.isArray(A.problemas) ? A.problemas : [])
    .map((p, i) => {
      if (typeof p === 'string' && p.trim()) return { id: `P${i + 1}`, titulo: p.trim() }
      if (p && typeof p === 'object' && typeof p.titulo === 'string' && p.titulo.trim()) return { ...p, id: p.id ? String(p.id) : `P${i + 1}` }
      return null
    })
    .filter(Boolean)
  if (!PROBLEMAS_APROBADOS.length) {
    return {
      fase: FASE,
      error: 'La fase "oportunidades" exige args.problemas: la lista de problemas del mapa aprobada por una persona ' +
        '(objetos {id, titulo, especie, etapa, mecanismos, soluciones_actuales} como los devuelve la fase "mapa", o títulos). Ejecuta antes la fase "mapa".',
    }
  }

  phase('Oportunidades')
  const P = PROBLEMAS_APROBADOS.length
  const porOportunidad = 1 + TRIAGE.length + 1 + EVALUADORES.length
  log(`Fase "oportunidades": ${P} problemas aprobados, objetivo ${N_OPS} oportunidades. Estimación: unos ${P * 4 + N_OPS * porOportunidad + 2} agentes ` +
    `(por problema, generador y ~3 comprobaciones de novedad; por oportunidad, registro, ${TRIAGE.length} propuestas de compuerta, crítica y ${EVALUADORES.length} evaluadores; ` +
    `ranking y verificación), más hasta ${2 * P * 4} en rondas extra y los reintentos de evaluadores`)

  const describirProblema = (p) => [
    `Problema ${p.id}: ${p.titulo}` + (p.especie || p.etapa ? ` (${[p.especie, p.etapa].filter(Boolean).join(', ')})` : '') + '.',
    `Mecanismos: ${(Array.isArray(p.mecanismos) && p.mecanismos.length) ? p.mecanismos.join('; ') : '[DESC]'}.`,
    `Soluciones actuales y su eficacia documentada: ${p.soluciones_actuales || '[DESC] no constan en la lista aprobada; búscalas antes de proponer'}.`,
    p.brecha ? `Brecha: ${p.brecha}.` : '',
  ].filter(Boolean).join('\n')

  const generar = (p, i, existentes, ronda) => lanzar(
    `${REGLAS}\nMisión 1, paso M1b. Ámbito: ${FOCO}.\n${describirProblema(p)}\n` +
    'Genera 2-3 oportunidades originales siguiendo .claude/skills/hipotesis/SKILL.md (conexiones entre disciplinas, descubrimiento basado en literatura ABC, ' +
    'contradicciones entre estudios, reposicionamiento, combinaciones con mecanismos complementarios). Cada una debe superar o complementar de forma diferenciada ' +
    'las soluciones actuales (explícalo en "diferenciacion") y llevar hipótesis falsables. No propongas dosis óptimas.' +
    (existentes.length ? `\nNo repitas estas oportunidades ya propuestas: ${existentes.join(' | ')}` : ''),
    { label: `M1b:gen:${p.id}${ronda ? `:r${ronda}` : ''}`, phase: 'Oportunidades', agentType: GENERADORES[i % GENERADORES.length], schema: OPORTUNIDADES },
  )
  const comprobarNovedad = (o, etiqueta) => lanzar(
    `${REGLAS}\nComprueba la novedad de esta oportunidad en literatura (nutria buscar pubmed/europepmc/openalex), patentes (nutria patentes) y productos comerciales (WebSearch), ` +
    'teniendo en cuenta las soluciones actuales del problema. Veredicto: nueva | diferenciada | existente. Si es existente, propón una reformulación diferenciada o explica por qué descartarla.\n' +
    JSON.stringify(o),
    { label: `M1b:novedad:${etiqueta}`, phase: 'Oportunidades', agentType: 'investigador-bibliografico', schema: NOVEDAD },
  ).then((n) => ({ ...o, novedad: n }))
  const conProblema = (o, p) => ({ ...o, problema_id: p.id, soluciones_actuales_problema: p.soluciones_actuales || '[DESC]' })

  const vistos = new Set()
  const oportunidades = []
  const noRegistradas = []
  const anadir = (lista) => {
    for (const o of lista.flat().filter(Boolean)) {
      const k = norm(o.titulo)
      if (!o.novedad) { noRegistradas.push({ titulo: o.titulo, motivo: 'novedad sin comprobar (fallo de agente)' }); continue }
      if (o.novedad.veredicto === 'existente') { noRegistradas.push({ titulo: o.titulo, motivo: 'existente sin diferenciación' }); continue }
      if (vistos.has(k)) { noRegistradas.push({ titulo: o.titulo, motivo: 'duplicada' }); continue }
      vistos.add(k)
      oportunidades.push(o)
    }
  }
  const ronda0 = await pipeline(
    PROBLEMAS_APROBADOS,
    (p, _orig, i) => generar(p, i, [], 0),
    (res, p) => parallel(((res && res.oportunidades) || []).map((o, j) => () => comprobarNovedad(conProblema(o, p), `${p.id}.${j + 1}`))),
  )
  anadir(ronda0.filter(Boolean))
  let ronda = 0
  while (oportunidades.length < N_OPS && ronda < 2) {
    if (presupuestoBajo()) {
      log(`Rondas extra omitidas: quedan ${Math.round(budget.remaining())} tokens de presupuesto (< ${UMBRAL_TOKENS}); se continúa con ${oportunidades.length} oportunidades`)
      break
    }
    ronda += 1
    const r = ronda
    log(`Solo ${oportunidades.length} oportunidades nuevas o diferenciadas; ronda extra ${r}`)
    const titulos = oportunidades.map((o) => o.titulo)
    const extra = await pipeline(
      PROBLEMAS_APROBADOS,
      (p, _orig, i) => generar(p, i + r, titulos, r),
      (res, p) => parallel(((res && res.oportunidades) || []).map((o, j) => () => comprobarNovedad(conProblema(o, p), `${p.id}.r${r}.${j + 1}`))),
    )
    anadir(extra.filter(Boolean))
  }
  if (oportunidades.length < N_OPS) log(`Aviso: ${oportunidades.length} oportunidades (objetivo ${N_OPS}); se continúa con las disponibles`)
  if (noRegistradas.length) log(`${noRegistradas.length} propuestas no pasan a registro (existentes, duplicadas o sin comprobar); se informan en el resultado`)
  log(`${oportunidades.length} oportunidades pasan a registro, triage y puntuación`)

  const promptEvaluador = (ev, o) =>
    `Eres el evaluador independiente "${ev.id}". Puntúa la oportunidad ${o.id} con los 9 criterios de docs/RUBRICA.md siguiendo .claude/skills/puntuacion/SKILL.md. ` +
    `Lee la oportunidad solo con nutria --agente ${ev.id} oportunidad ver ${o.id} --sin-puntuaciones --json y la evidencia de abajo (análisis de triage, ` +
    `modelo del ganadero en research/datos/${o.id}/ y crítica). No ejecutes nutria puntuar ranking ni leas puntuaciones o ficheros de otros evaluadores. ` +
    `Guarda research/oportunidades/${o.id}_puntuacion_${ev.id}.json con "evaluador": "${ev.id}" y regístralo con nutria --agente ${ev.id} puntuar registrar --datos <fichero>.\n` +
    'Análisis de triage: ' + JSON.stringify((o.compuertas || []).filter(Boolean).map((c) => ({
      agente: c.agente, compuerta: c.compuerta, estado_propuesto: c.resultado ? c.resultado.estado : null,
      resumen: c.resultado ? c.resultado.resumen : 'sin análisis (fallo de agente)', archivo: c.resultado ? c.resultado.archivo : null,
    }))) +
    '\nCrítica del red team: ' + JSON.stringify(o.critica)

  const evaluadas = await pipeline(
    oportunidades,
    (o, _orig, i) => lanzar(
      `${REGLAS}\nRegistra esta oportunidad. Antes, comprueba con nutria oportunidad listar --json que no esté ya registrada (mismo título o equivalente): si lo está, ` +
      `no la dupliques y devuelve su ID. Si no, escribe research/oportunidades/M1_${String(i + 1).padStart(2, '0')}.json conforme a src/nutria/data/schemas/oportunidad.schema.json ` +
      '(rellena "novedad" con la comprobación realizada; "ruta_regulatoria_preliminar": "indeterminada" si no está clara, porque la clasificación la propone regulacion-seguridad) ' +
      'y ejecuta nutria --agente director-cientifico oportunidad nueva --datos <fichero>. Registra cada hipótesis como JSON conforme a hipotesis.schema.json ' +
      '(con efecto mínimo relevante y criterio de falsación) con nutria hipotesis nueva. Devuelve el ID asignado.\n' + JSON.stringify(o),
      { label: `registro:${i + 1}`, phase: 'Oportunidades', agentType: 'director-cientifico', schema: REGISTRO },
    ).then((r) => (r ? { ...o, id: r.id, hipotesis_ids: r.hipotesis_ids || [] } : null)),
    (o) => o && parallel([
      ...TRIAGE.map((t) => () => lanzar(
        `${REGLAS}\nOportunidad ${o.id}: ${o.titulo}. Tu tarea: ${t.tarea.split('<ID>').join(o.id)}. Consulta nutria --agente ${t.agente} oportunidad ver ${o.id} --sin-puntuaciones --json. ` +
        `PROPÓN la compuerta "${t.compuerta}" y regístrala como propuesta, sin --firma-humana: nutria --agente ${t.agente} puntuar compuerta ${o.id} ${t.compuerta} ` +
        '<superada|no_resuelta|bloqueada> --motivo "...". "bloqueada" actúa de inmediato; "superada" no cuenta hasta que la firme una persona. ' +
        `Guarda tu análisis en research/revisiones/${o.id}_triage_${t.compuerta}.md.`,
        { label: `triage:${t.compuerta}:${o.id}`, phase: 'Triage y crítica', agentType: t.agente, schema: COMPUERTA },
      ).then((c) => ({ agente: t.agente, compuerta: t.compuerta, resultado: c }))),
      () => lanzar(
        `${REGLAS}\nIntenta refutar la oportunidad ${o.id} (nutria --agente critico-red-team oportunidad ver ${o.id} --sin-puntuaciones --json) y sus hipótesis con tu lista de comprobación completa.`,
        { label: `critica:${o.id}`, phase: 'Triage y crítica', agentType: 'critico-red-team', schema: CRITICA },
      ),
    ]).then((rs) => ({ ...o, compuertas: rs.slice(0, TRIAGE.length), critica: rs[TRIAGE.length] })),
    (o) => o && parallel(EVALUADORES.map((ev) => () => puntuar(ev, o.id, promptEvaluador(ev, o))))
      .then((p) => ({ ...o, puntuaciones: p })),
  )
  evaluadas.forEach((o, i) => {
    if (!o) noRegistradas.push({ titulo: oportunidades[i].titulo, motivo: 'registro fallido (fallo de agente)' })
  })
  const registradas = evaluadas.filter(Boolean)

  phase('Ranking')
  const ranking = await lanzar(
    `${REGLAS}\nEjecuta nutria --agente director-cientifico puntuar ranking --perfil recomendado --sim 5000 --semilla 42 --min-evaluadores 3 --alfa 50 y también con --perfil original. ` +
    'Escribe research/revisiones/M1_ranking.md con ambas salidas e interpretación: estado de cada oportunidad (APTA, CONDICIONAL, BLOQUEADA, INCOMPLETA, INSUFICIENTE), ' +
    'robustez frente a los pesos (rango con cada perfil), desacuerdos entre evaluadores y compuertas pendientes de firma humana. Propón como candidatas a planes las 3 mejores ' +
    'NO bloqueadas (las APTA antes que las CONDICIONALES). Es una PROPUESTA: la selección la decide una persona. Regístrala con nutria decision registrar ' +
    '--decision "propuesta top-3 M1" --motivo "..." sin --firma-humana.' +
    (fallos.length ? `\nAgentes que fallaron (decláralo en el documento): ${fallos.map((f) => f.agente).join(', ')}` : ''),
    { label: 'ranking', phase: 'Ranking', agentType: 'director-cientifico', schema: RANKING },
  )
  const verificacion = ranking ? await verificarCitas(ranking.archivo, 'ranking', 'Ranking') : null

  return {
    fase: FASE,
    fecha: FECHA,
    foco: FOCO,
    problemas: PROBLEMAS_APROBADOS.map((p) => `${p.id}: ${p.titulo}`),
    oportunidades: registradas.map((o) => ({
      id: o.id,
      titulo: o.titulo,
      novedad: o.novedad.veredicto,
      critica: o.critica ? o.critica.veredicto : 'sin crítica (fallo de agente)',
      compuertas_propuestas: (o.compuertas || []).filter(Boolean).map((c) => `${c.compuerta}:${c.resultado ? c.resultado.estado : 'sin propuesta (fallo de agente)'}`),
      evaluadores: (o.puntuaciones || []).filter(Boolean).map((p) => ({ id: p.evaluador, modelo: p.modelo, reintento: p.reintento, registrado: Boolean(p.registrado) })),
      evaluadores_registrados: (o.puntuaciones || []).filter((p) => p && p.registrado).length,
    })),
    no_registradas: noRegistradas,
    ranking: ranking ? ranking.archivo : null,
    propuesta_para_planes: ranking ? ranking.top : [],
    verificacion_citas: verificacion,
    fallos: resumenFallos(),
    pendiente: 'Las compuertas son propuestas: "superada" solo cuenta con firma humana. Con menos de 3 evaluadores registrados la oportunidad queda INSUFICIENTE.',
    siguiente: 'Punto de control humano: una persona elige las oportunidades para planes, firma la selección en su terminal ' +
      '(nutria decision registrar --entidad <ID> --decision "seleccionada M1" ...) y relanza con {"fase": "planes", "seleccion": ["O-xxxx", ...]}.',
  }
}

// ================================================================ fase "planes" (M1d)
const SELECCION = [...new Set((Array.isArray(A.seleccion) ? A.seleccion : (typeof A.seleccion === 'string' ? [A.seleccion] : []))
  .map((x) => String(x).trim()).filter(Boolean))]
const invalidos = SELECCION.filter((id) => !/^O-\d{4}$/.test(id))
if (!SELECCION.length || invalidos.length) {
  return {
    fase: FASE,
    error: 'La fase "planes" exige args.seleccion: la lista de IDs (O-xxxx) que eligió una persona tras el ranking.' +
      (invalidos.length ? ` IDs no válidos: ${invalidos.join(', ')}.` : ''),
  }
}
phase('Planes')
log(`Fase "planes": ${SELECCION.length} oportunidades elegidas. Estimación: ${SELECCION.length * 4} agentes (plan, crítica, revisión y verificación de citas por oportunidad)`)
const planes = await pipeline(
  SELECCION,
  (id) => lanzar(
    `${REGLAS}\nRedacta el plan de investigación computacional de ${id} en research/planes/${id}.md. Lee nutria --agente director-cientifico oportunidad ver ${id} --sin-puntuaciones --json ` +
    `y los análisis de research/revisiones/ sobre ${id}. Incluye: pregunta PECO; protocolo de revisión sistemática (PRISMA 2020); datos a extraer (doble extracción); ` +
    'análisis estadísticos; modelos y simulaciones justificados por la decisión que informan; evaluación regulatoria y económica; criterios de descarte fijados de antemano; ' +
    'plan de validación in vitro → ex vivo → in vivo (solo diseño) con TODOS los puntos de tu lista de validación (líquido ruminal de donantes con procedimiento autorizado o de matadero; ' +
    'Directiva 2010/63/UE y, en España, Real Decreto 53/2013; la técnica in situ es in vivo; guía de eficacia FEEDAP vigente, EFSA J 2024;22(7):e8856; BPL en los estudios de seguridad del dossier; ' +
    'potencia con varianzas reales; registro previo del protocolo si no compromete la confidencialidad; notificación a EFSA de los estudios encargados para un dossier, ' +
    'Reglamento (UE) 2019/1381, art. 32b); cronograma y agentes responsables. ' +
    `Comprueba con nutria decision listar --entidad ${id} si la selección tiene firma humana y dilo en el plan (sin ella, el plan es provisional).`,
    { label: `plan:${id}`, phase: 'Planes', agentType: 'director-cientifico', schema: PLAN },
  ),
  (plan, id) => (plan
    ? lanzar(
      `${REGLAS}\nRevisa de forma adversarial el plan ${plan.archivo} de ${id}: qué falta (incluida la lista de validación), qué podría invalidarlo y qué criterio de descarte es débil.`,
      { label: `critica-plan:${id}`, phase: 'Planes', agentType: 'critico-red-team', schema: CRITICA },
    ).then((c) => ({ id, plan, critica_plan: c }))
    : { id, plan: null }),
  (r, id) => (r && r.plan && r.critica_plan
    ? lanzar(
      `${REGLAS}\nIncorpora en ${r.plan.archivo} las correcciones necesarias y añade una sección "Respuesta a la crítica" (qué cambia, qué no y por qué).\n` + JSON.stringify(r.critica_plan),
      { label: `revision-plan:${id}`, phase: 'Planes', agentType: 'director-cientifico', schema: PLAN },
    ).then((p2) => ({ ...r, plan_revisado: p2 }))
    : r),
  (r, id) => (r && r.plan
    ? verificarCitas((r.plan_revisado || r.plan).archivo, `plan:${id}`, 'Planes').then((v) => ({ ...r, verificacion_citas: v }))
    : r),
)

return {
  fase: FASE,
  fecha: FECHA,
  planes: planes.map((r, i) => {
    if (!r || !r.plan) return { id: SELECCION[i], plan: null, motivo: 'no se pudo redactar el plan (fallo de agente)' }
    const final = r.plan_revisado || r.plan
    return {
      id: r.id,
      plan: final.archivo,
      critica: r.critica_plan ? r.critica_plan.veredicto : 'sin crítica (fallo de agente)',
      revisado_tras_critica: Boolean(r.plan_revisado),
      seleccion_firmada: typeof final.seleccion_firmada === 'boolean' ? final.seleccion_firmada : null,
      verificacion_citas: r.verificacion_citas || null,
    }
  }),
  fallos: resumenFallos(),
  pendiente: 'Los planes son propuestas: cualquier paso hacia experimentos requiere la decisión firmada de una persona y todas las autorizaciones.',
}
