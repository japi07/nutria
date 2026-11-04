export const meta = {
  name: 'evaluar-oportunidad-auto',
  description: 'Evaluación en profundidad de una oportunidad: descarte rápido en paralelo (regulación, toxicología, destino digestivo y modelo del ganadero), evidencia con doble extracción y metaanálisis si hay al menos 3 estudios comparables, especialistas con crítica y verificación, informe con Anexo A verificado y puntuación ciega por 3 evaluadores independientes con modelos distintos',
  whenToUse: 'Cuando una oportunidad registrada (O-xxxx) necesita una evaluación completa. Argumento: el ID, p. ej. "O-0003", o {"id": "O-0003", "max_estudios": 10}. Se detiene si el descarte rápido propone "bloqueada". El control de flujo es fijo, pero cada agente puede dar resultados distintos en cada ejecución. Consume muchos tokens.',
  phases: [
    { title: 'Descarte rápido', detail: 'regulación, toxicología, destino digestivo y modelo del ganadero en paralelo' },
    { title: 'Plan', detail: 'pregunta PECO, variables y preguntas para cada especialista' },
    { title: 'Evidencia', detail: 'revisión bibliográfica, doble extracción y metaanálisis' },
    { title: 'Especialistas', detail: 'análisis independientes y propuestas de compuertas' },
    { title: 'Verificación', detail: 'crítica y verificación de fuentes de cada aportación' },
    { title: 'Síntesis e informe', detail: 'informe de 20 secciones, Anexo A de respaldo y sello' },
    { title: 'Puntuación', detail: '3 evaluadores independientes y ciegos con modelos distintos' },
  ],
}

// ---------------------------------------------------------------- argumentos
const leerArgs = (a) => {
  if (typeof a === 'string') {
    const t = a.trim()
    if (t.startsWith('{')) {
      try { return JSON.parse(t) } catch (e) { return {} }
    }
    return { id: t }
  }
  return a && typeof a === 'object' && !Array.isArray(a) ? a : {}
}
const A = leerArgs(args)
const ID = typeof A.id === 'string' ? A.id.trim() : ''
if (!/^O-\d{4}$/.test(ID)) {
  return { error: 'Indica el ID de la oportunidad, p. ej. "O-0003" o {"id": "O-0003"}' }
}
const MAX_ESTUDIOS = Number.isInteger(A.max_estudios) && A.max_estudios > 0 ? A.max_estudios : 10
const MIN_COMPARABLES = 3
const DIR = `research/datos/${ID}`
const REGLAS = 'Sigue CLAUDE.md: etiquetas [PUB]/[DER]/[HIP]/[SUP]/[DESC], nada de citas de memoria, contexto biológico completo. Atribuye tus órdenes con "nutria --agente <tu-rol>" y nunca uses --firma-humana.'

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
const ESTADO_COMPUERTA = { type: 'string', enum: ['superada', 'no_resuelta', 'bloqueada'] }

const ESPECIALISTAS = ['investigador-bibliografico', 'estadistico', 'fisiologia-bioquimica', 'compuestos-toxicologia',
  'nutricion-formulacion', 'bioinformatico', 'regulacion-seguridad', 'desarrollo-industrial', 'economia-mercado-pi',
  'sostenibilidad-bienestar']
// Quién propone cada compuerta (los agentes solo proponen; una persona firma).
const COMPUERTA_DE = {
  'regulacion-seguridad': 'regulatoria',
  'compuestos-toxicologia': 'seguridad',
  'fisiologia-bioquimica': 'destino_digestivo',
  'desarrollo-industrial': 'analitica',
  'economia-mercado-pi': 'propiedad_intelectual',
  'sostenibilidad-bienestar': 'etica',
}

const DESCARTE = S({ analisis: STR, estado: ESTADO_COMPUERTA, motivo: STR, resumen: STR, archivo: STR, fuentes: STRS },
  ['analisis', 'estado', 'motivo', 'resumen'])
const PLAN = S({
  peco: STR,
  variables: { type: 'array', items: STR, minItems: 1 },
  preguntas: { type: 'array', items: S({ agente: { type: 'string', enum: ESPECIALISTAS }, pregunta: STR, entregable: STR }, ['agente', 'pregunta', 'entregable']) },
}, ['peco', 'variables', 'preguntas'])
const ESTUDIOS = S({
  archivo: STR,
  estudios: { type: 'array', items: S({
    fuente: { type: 'string', pattern: '^(doi|pmid|openalex):.+' }, titulo: STR, especie: STR, etapa: STR, tipo_estudio: STR,
    datos_cuantitativos: BOOL, comparable: BOOL, motivo: STR,
  }, ['fuente', 'especie', 'tipo_estudio', 'datos_cuantitativos', 'comparable']) },
  evidencia_contraria: STRS, lagunas: STRS,
}, ['archivo', 'estudios'])
const EXTRACCION = S({ archivo: STR, registros: NUM, no_consta: STRS }, ['archivo', 'registros'])
const CONCILIACION = S({
  archivo: STR, discrepancias: NUM, resueltas: NUM, registrada: BOOL,
  estado_verificacion: { type: 'string', enum: ['doble_concordante', 'resuelta', 'discrepancia'] }, ids: STRS, motivo: STR,
}, ['discrepancias', 'registrada', 'estado_verificacion'])
const META = S({ archivo: STR, csv: STR, k: NUM, orden: STR, resumen: STR, certeza: STR }, ['archivo', 'k', 'resumen'])
const APORTACION = S({
  archivo: STR, resumen: STR,
  afirmaciones: { type: 'array', items: S({ texto: STR, etiqueta: { type: 'string', enum: ['PUB', 'DER', 'HIP', 'SUP', 'DESC'] }, fuentes: STRS }, ['texto', 'etiqueta']) },
  compuerta_propuesta: S({ compuerta: STR, estado: ESTADO_COMPUERTA, motivo: STR }, ['compuerta', 'estado', 'motivo']),
}, ['archivo', 'resumen', 'afirmaciones'])
const CRITICA = S({ veredicto: { type: 'string', enum: ['sostenida', 'debilitada', 'refutada', 'no_verificable'] }, debilidades: STRS, experimento_decisivo: STR }, ['veredicto', 'debilidades'])
const VERIF = S({ revisadas: NUM, problemas: { type: 'array', items: S({ fuente: STR, estado: STR, motivo: STR }, ['fuente', 'estado', 'motivo']) } }, ['revisadas', 'problemas'])
const INFORME = S({ archivo: STR, decision_propuesta: STR, resumen: STR }, ['archivo', 'decision_propuesta'])
const VERIF_INFORME = S({
  archivo: STR, filas_anexo_a: NUM, verificacion_completa: BOOL, sello_valido: BOOL, errores: NUM,
  problemas: { type: 'array', items: S({ ref: STR, estado: STR, motivo: STR }, ['ref', 'estado', 'motivo']) },
}, ['verificacion_completa', 'sello_valido', 'problemas'])
const PUNTUACION = S({ archivo: STR, registrado: BOOL, resumen: STR }, ['archivo', 'registrado'])

const EVALUADORES = [
  { id: 'evaluador-1', modelo: 'opus' },
  { id: 'evaluador-2', modelo: 'sonnet' },
  { id: 'evaluador-3', modelo: 'fable' },
]
const MODELO_REINTENTO = 'opus'

log(`Evaluación de ${ID}. Estimación: ${4 + 1 + 1 + 2 + EVALUADORES.length} agentes fijos (descarte rápido, plan, revisión bibliográfica, informe y su verificación, ` +
  `evaluadores), más 3 por estudio con doble extracción (máximo ${MAX_ESTUDIOS}), 1 de metaanálisis si hay al menos ${MIN_COMPARABLES} estudios comparables, ` +
  `hasta 3 por pregunta de especialista y hasta ${EVALUADORES.length} reintentos de evaluadores`)

// ================================================================ descarte rápido
phase('Descarte rápido')
const DESCARTE_RAPIDO = [
  { agente: 'regulacion-seguridad', analisis: 'regulatoria', registrable: true,
    tarea: 'clasificación y vía regulatoria con la skill triage-regulatorio (primero pienso dietético, después medicamento por alegación y por función, sustancias prohibidas o restringidas y alegaciones)' },
  { agente: 'compuestos-toxicologia', analisis: 'seguridad', registrable: true,
    tarea: 'identidad del candidato y señales de seguridad para la especie diana, el consumidor, el usuario y el medio ambiente a la dosis prevista' },
  { agente: 'fisiologia-bioquimica', analisis: 'destino_digestivo', registrable: true,
    tarea: '¿llega el activo a la diana en cantidad relevante a una dosis viable? En rumiantes, calcula la degradabilidad efectiva y la fracción que escapa con nutria destino ruminal (a, b y kd con fuente; kp con fuente o [SUP]) o con una fracción de escape publicada' },
  { agente: 'economia-mercado-pi', analisis: 'modelo_ganadero', registrable: false,
    tarea: `presupuesto parcial del ganadero con nutria econ ganadero ${DIR}/econ_ganadero.toml (cada entrada con fuente o [SUP]): coste por animal y día, respuesta umbral, P(beneficio neto > 0) y escenarios P10/P50/P90` },
]
const promptDescarte = (d) => `${REGLAS}\nDescarte rápido de ${ID} (nutria --agente ${d.agente} oportunidad ver ${ID} --sin-puntuaciones --json). Tu análisis: ${d.tarea}. ` +
  `Guarda tu análisis en research/revisiones/${ID}_descarte_${d.analisis}.md. ` +
  (d.registrable
    ? `PROPÓN la compuerta "${d.analisis}" y regístrala como propuesta, sin --firma-humana: nutria --agente ${d.agente} puntuar compuerta ${ID} ${d.analisis} ` +
      '<superada|no_resuelta|bloqueada> --motivo "...". "bloqueada" actúa de inmediato y detiene esta evaluación; "superada" no cuenta hasta que la firme una persona.'
    : 'No es una compuerta del registro: devuelve "bloqueada" solo si el beneficio neto es negativo incluso en el escenario optimista (P90) o el coste por animal y día es ' +
      'inasumible (ancla 0 de rentabilidad_ganadero), y en ese caso registra la propuesta de descarte con nutria decision registrar --entidad ' + ID +
      ' --decision "propuesta de descarte (modelo del ganadero)" --motivo "..." sin --firma-humana; "no_resuelta" si faltan datos; "superada" en otro caso.')

const descarte = await parallel(DESCARTE_RAPIDO.map((d) => () => lanzar(promptDescarte(d),
  { label: `descarte:${d.analisis}`, phase: 'Descarte rápido', agentType: d.agente, schema: DESCARTE })))
const resumenDescarte = DESCARTE_RAPIDO.map((d, i) => {
  const r = descarte[i]
  return { analisis: d.analisis, agente: d.agente, estado: r ? r.estado : 'sin_resultado', motivo: r ? r.motivo : 'el agente no devolvió resultado', archivo: r ? r.archivo || null : null }
})
const bloqueos = resumenDescarte.filter((d) => d.estado === 'bloqueada')
if (bloqueos.length) {
  log(`Descarte rápido: ${bloqueos.map((b) => b.analisis).join(', ')} propone(n) "bloqueada"; la evaluación se detiene`)
  return {
    id: ID,
    detenida_en: 'Descarte rápido',
    bloqueos: bloqueos.map((b) => ({ analisis: b.analisis, agente: b.agente, motivo: b.motivo, archivo: b.archivo })),
    descarte_rapido: resumenDescarte,
    fallos: resumenFallos(),
    siguiente: 'Una persona revisa el motivo. Una compuerta propuesta "bloqueada" solo se desbloquea con firma humana (en su terminal); la propuesta de ' +
      'descarte económico la decide una persona con nutria decision registrar. Si se rediseña la oportunidad, vuelve a lanzar este workflow.',
  }
}
const sinResultado = resumenDescarte.filter((d) => d.estado === 'sin_resultado').map((d) => d.analisis)
if (sinResultado.length) log(`Descarte rápido incompleto (${sinResultado.join(', ')} sin resultado): se continúa, pero esas propuestas quedan pendientes`)

// ================================================================ plan
phase('Plan')
const plan = await lanzar(
  `${REGLAS}\nPrepara la evaluación de ${ID} (nutria --agente director-cientifico oportunidad ver ${ID} --sin-puntuaciones --json; plan en research/planes/${ID}.md si existe). ` +
  'Formula la pregunta PECO y las variables de resultado (la primera es la principal para la extracción y el metaanálisis). Después, una pregunta concreta y su entregable ' +
  'para cada especialista que aporte a la decisión (omite los que no aporten). La fase de evidencia ya lanza la revisión bibliográfica, la doble extracción y el metaanálisis: ' +
  'asigna preguntas a investigador-bibliografico o estadistico para tareas adicionales (p. ej. novedad, potencia, modelos predictivos). desarrollo-industrial, ' +
  'economia-mercado-pi y sostenibilidad-bienestar participarán siempre para proponer sus compuertas.\nDescarte rápido: ' + JSON.stringify(resumenDescarte),
  { label: `plan:${ID}`, phase: 'Plan', agentType: 'director-cientifico', schema: PLAN },
)
if (!plan) {
  return { id: ID, error: 'No se pudo preparar el plan', descarte_rapido: resumenDescarte, fallos: resumenFallos() }
}
const VARIABLES = (Array.isArray(plan.variables) ? plan.variables : []).filter(Boolean)
const VARIABLE = VARIABLES[0] || 'variable principal de la pregunta PECO'

// ================================================================ evidencia
phase('Evidencia')
const biblio = await lanzar(
  `${REGLAS}\nRevisión de alcance de la evidencia de ${ID} con la skill busqueda-bibliografica. PECO: ${plan.peco}. Variables: ${VARIABLES.join('; ') || VARIABLE} ` +
  `(principal: ${VARIABLE}). Busca en al menos dos fuentes y busca activamente evidencia contraria; registra y verifica las fuentes incluidas (nutria fuente agregar) ` +
  `y documenta la búsqueda en research/revisiones/${ID}_busqueda.md. Devuelve los estudios incluidos con su identificador registrado (doi:, pmid: u openalex:), ` +
  'si publican datos cuantitativos extraíbles de la variable principal (medias, dispersión y n por tratamiento) y si son comparables entre sí (misma especie y etapa, ' +
  'variable y unidad combinables, diseño compatible), con el motivo.',
  { label: `bibliografia:${ID}`, phase: 'Evidencia', agentType: 'investigador-bibliografico', schema: ESTUDIOS },
)
const estudios = biblio && Array.isArray(biblio.estudios) ? biblio.estudios.filter(Boolean) : []
const fuentesVistas = new Set()
const candidatos = []
for (const e of estudios) {
  if (!e.fuente || !e.datos_cuantitativos || !e.comparable || fuentesVistas.has(e.fuente)) continue
  fuentesVistas.add(e.fuente)
  candidatos.push(e)
}
const aExtraer = candidatos.slice(0, MAX_ESTUDIOS)
const omitidos = candidatos.slice(MAX_ESTUDIOS).map((e) => e.fuente)
if (omitidos.length) log(`Límite de ${MAX_ESTUDIOS} estudios para la doble extracción: quedan sin extraer ${omitidos.length} (${omitidos.join(', ')}); súbelo con args.max_estudios`)
log(`Evidencia: ${estudios.length} estudios incluidos, ${candidatos.length} con datos cuantitativos comparables; doble extracción de ${aExtraer.length} (${aExtraer.length * 3} agentes)`)

const slug = (f) => String(f).replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^_+|_+$/g, '')
const rutaExtraccion = (e, copia) => `${DIR}/extraccion_${slug(e.fuente)}_${copia}.json`
const extraer = (e, copia) => lanzar(
  `${REGLAS}\nExtracción ${copia} (independiente) de la fuente ${e.fuente} para ${ID}. Variables: ${VARIABLES.join('; ') || VARIABLE}. ` +
  'Sigue .claude/skills/extraccion-datos/SKILL.md y el esquema src/nutria/data/schemas/extraccion.schema.json. ' +
  `Escribe la lista de registros en ${rutaExtraccion(e, copia)} (un registro por comparación y variable, en el orden en que aparecen en la fuente). ` +
  'NO la registres con nutria extraccion agregar y no leas la otra copia ni otros ficheros extraccion_*: la comparación es posterior y ciega.',
  { label: `extraccion:${copia}:${e.fuente}`, phase: 'Evidencia', agentType: 'extractor-datos', schema: EXTRACCION },
)
const conciliar = (e) => lanzar(
  `${REGLAS}\nConcilia la doble extracción de ${e.fuente} (${ID}): ejecuta nutria --agente extractor-datos extraccion comparar ${rutaExtraccion(e, 'A')} ${rutaExtraccion(e, 'B')} ` +
  '(código de salida 1 si hay discrepancias). Si coinciden, marca los registros "doble_concordante". Si no, resuelve cada discrepancia releyendo la fuente ' +
  '(nunca promediando ni eligiendo al azar) y márcala "resuelta"; un registro que no puedas resolver queda como "discrepancia" en el fichero y NO se registra. ' +
  `Escribe la versión conciliada en ${DIR}/extraccion_${slug(e.fuente)}_conciliada.json y regístrala con nutria --agente extractor-datos extraccion agregar --datos <fichero>.`,
  { label: `conciliacion:${e.fuente}`, phase: 'Evidencia', agentType: 'extractor-datos', schema: CONCILIACION },
)
const extracciones = await pipeline(
  aExtraer,
  (e) => parallel([() => extraer(e, 'A'), () => extraer(e, 'B')]),
  (ab, e) => (ab && ab[0] && ab[1]
    ? conciliar(e).then((c) => (c ? { fuente: e.fuente, ...c } : { fuente: e.fuente, registrada: false, motivo: 'conciliación fallida (fallo de agente)' }))
    : { fuente: e.fuente, registrada: false, motivo: 'doble extracción incompleta (fallo de agente): no se registra una extracción simple' }),
)
const conciliadas = extracciones.filter((x) => x && x.registrada)
const comparables = conciliadas.length
const sinRegistrar = extracciones.filter((x) => x && !x.registrada).map((x) => ({ fuente: x.fuente, motivo: x.motivo || 'no registrada' }))
let metaanalisis = null
if (comparables >= MIN_COMPARABLES) {
  metaanalisis = await lanzar(
    `${REGLAS}\nMetaanálisis de "${VARIABLE}" para ${ID} con .claude/skills/metaanalisis/SKILL.md: nutria extraccion exportar --variable "${VARIABLE}" --csv ${DIR}/${slug(VARIABLE)}.csv; ` +
    'nutria meta analizar <csv> (REML, ajuste hksj por defecto; --agregar-estudio --rho 0.5 si hay comparaciones dependientes); sensibilidad, sesgo de publicación solo si k ≥ 10 ' +
    `y certeza. Separa especies, unidades o diseños no combinables y explica por qué. Guarda órdenes y salidas en ${DIR}/ y registra las afirmaciones [DER] con la orden usada.`,
    { label: `metaanalisis:${ID}`, phase: 'Evidencia', agentType: 'estadistico', schema: META },
  )
} else {
  log(`Metaanálisis no lanzado: ${comparables} estudio(s) comparable(s) con doble extracción registrada (mínimo ${MIN_COMPARABLES})`)
}
const resumenEvidencia = {
  revision: biblio ? biblio.archivo : null,
  estudios_incluidos: estudios.length,
  comparables_con_datos: candidatos.length,
  doble_extraccion_registrada: comparables,
  sin_registrar: sinRegistrar,
  omitidos_por_limite: omitidos,
  metaanalisis: metaanalisis ? { archivo: metaanalisis.archivo, k: metaanalisis.k, resumen: metaanalisis.resumen } : null,
  evidencia_contraria: biblio && biblio.evidencia_contraria ? biblio.evidencia_contraria : [],
}

// ================================================================ especialistas
phase('Especialistas')
const OBLIGATORIAS = [
  { agente: 'desarrollo-industrial', pregunta: 'Viabilidad de fabricación, estabilidad y método analítico para identificar y cuantificar el activo (o un marcador) en el producto, la premezcla y el pienso.', entregable: 'ficha industrial y propuesta de la compuerta analitica' },
  { agente: 'economia-mercado-pi', pregunta: 'Valor para la empresa (nutria econ desarrollador, entradas con fuente o [SUP]), mercado, competidores, patentes y libertad de operación preliminar.', entregable: 'rNPV, mercado y propuesta de la compuerta propiedad_intelectual' },
  { agente: 'sostenibilidad-bienestar', pregunta: 'Impacto ambiental, bienestar y ética de la validación futura (3R, Directiva 2010/63/UE).', entregable: 'propuesta de la compuerta etica' },
]
const preguntas = (Array.isArray(plan.preguntas) ? plan.preguntas : []).filter((q) => q && ESPECIALISTAS.includes(q.agente))
for (const o of OBLIGATORIAS) {
  if (!preguntas.some((q) => q.agente === o.agente)) preguntas.push(o)
}
log(`Especialistas: ${preguntas.length} preguntas (hasta ${preguntas.length * 3} agentes con crítica y verificación); después, informe (2 agentes) y ${EVALUADORES.length} evaluadores`)
const contextoEvidencia = `Evidencia disponible: revisión ${resumenEvidencia.revision || 'no disponible (fallo de agente)'}; ${comparables} estudio(s) con doble extracción registrada en ${DIR}/; ` +
  `metaanálisis: ${metaanalisis ? metaanalisis.archivo : 'no realizado'}; análisis del descarte rápido: ${resumenDescarte.map((d) => `${d.analisis}=${d.archivo || 'sin fichero'}`).join(', ')}.`
const promptEspecialista = (q, i) => {
  const compuerta = COMPUERTA_DE[q.agente]
  const yaPropuesta = DESCARTE_RAPIDO.some((d) => d.agente === q.agente && d.registrable)
  let nota = ''
  if (compuerta && yaPropuesta) {
    nota = ` Si tu análisis cambia la propuesta de la compuerta "${compuerta}", regístrala de nuevo como propuesta, sin --firma-humana ` +
      `(nutria --agente ${q.agente} puntuar compuerta ${ID} ${compuerta} <estado> --motivo "...").`
  } else if (compuerta) {
    nota = ` PROPÓN la compuerta "${compuerta}" y regístrala como propuesta, sin --firma-humana: nutria --agente ${q.agente} puntuar compuerta ${ID} ${compuerta} ` +
      '<superada|no_resuelta|bloqueada> --motivo "...". "bloqueada" actúa de inmediato; "superada" no cuenta hasta que la firme una persona.'
  }
  return `${REGLAS}\nOportunidad ${ID}. Tu pregunta: ${q.pregunta}\nEntregable: ${q.entregable}.\n${contextoEvidencia}\n` +
    `Guarda tu análisis en research/revisiones/${ID}_${String(i + 1).padStart(2, '0')}_${q.agente}.md y devuelve las afirmaciones clave con etiqueta y fuentes.` + nota
}

// Cada aportación se verifica en cuanto termina (pipeline, sin esperar al resto).
const aportaciones = await pipeline(
  preguntas,
  (q, _orig, i) => lanzar(promptEspecialista(q, i), { label: `${q.agente}:${i + 1}`, phase: 'Especialistas', agentType: q.agente, schema: APORTACION }),
  (ap, q, i) => {
    if (!ap) return { agente: q.agente, pregunta: q.pregunta, fallo: true }
    const pub = (ap.afirmaciones || []).filter((a) => a && a.etiqueta === 'PUB')
    return parallel([
      () => lanzar(`${REGLAS}\nIntenta refutar este análisis de ${q.agente} sobre ${ID} (${ap.archivo}).\n${JSON.stringify(ap.afirmaciones || [])}`,
        { label: `critica:${q.agente}:${i + 1}`, phase: 'Verificación', agentType: 'critico-red-team', schema: CRITICA }),
      () => (pub.length
        ? lanzar(`${REGLAS}\nVerifica las fuentes de estas afirmaciones (existencia, correspondencia, retractación y si respaldan la afirmación: respalda, parcial, no_respalda o no_verificable). Solo lectura: no modifiques ficheros. ${JSON.stringify(pub)}`,
          { label: `fuentes:${q.agente}:${i + 1}`, phase: 'Verificación', agentType: 'verificador-citas', schema: VERIF })
        : Promise.resolve({ revisadas: 0, problemas: [] })),
    ]).then(([critica, verif]) => ({ agente: q.agente, ...ap, critica, verif }))
  },
)

// ================================================================ síntesis, informe y verificación
phase('Síntesis e informe')
const aportacionesValidas = aportaciones.filter((a) => a && !a.fallo)
const sinAportacion = aportaciones.map((a, i) => (a && !a.fallo ? null : preguntas[i].agente)).filter(Boolean)
const informe = await lanzar(
  `${REGLAS}\nSintetiza la evaluación de ${ID} siguiendo .claude/skills/informe/SKILL.md: nutria informe nuevo ${ID} (o actualiza research/informes/${ID}.md). ` +
  'Integra el descarte rápido, la evidencia (revisión, doble extracción y metaanálisis si lo hubo) y las aportaciones, conservando los desacuerdos y las críticas; ' +
  'corrige o retira las afirmaciones con fuentes problemáticas. La sección 14 indica el estado de las seis compuertas y cuáles son propuestas sin firmar. ' +
  'Deja el estado en borrador: no rellenes revisado_por_humano ni la tabla del Anexo A (la rellena verificador-citas). Propón una decisión (continuar, reformular o descartar) ' +
  'pendiente de firma humana. No puntúes ni consultes puntuaciones.' +
  (fallos.length ? `\nAgentes que fallaron (decláralo en Limitaciones): ${fallos.map((f) => f.agente).join(', ')}` : '') +
  `\nDescarte rápido: ${JSON.stringify(resumenDescarte)}\nEvidencia: ${JSON.stringify(resumenEvidencia)}\nAportaciones: ${JSON.stringify(aportacionesValidas)}`,
  { label: `informe:${ID}`, phase: 'Síntesis e informe', agentType: 'director-cientifico', schema: INFORME },
)
const verificacionInforme = informe
  ? await lanzar(
    `${REGLAS}\nVerifica el informe ${informe.archivo}. 1) Rellena la tabla de respaldo del Anexo A ("| Ref | Afirmación | Respaldo | Motivo |", Respaldo: respalda | parcial | ` +
    'no_respalda | no_verificable) leyendo cada fuente; cada [Rn] usado en una línea [PUB] necesita una fila respalda o parcial. ' +
    `2) Ejecuta nutria --agente verificador-citas informe comprobar ${informe.archivo} --online --sellar. No registres verificaciones manuales (las referencias con solo URL ` +
    'las registra una persona) y no cambies el estado ni revisado_por_humano. Informa de si la verificación en línea fue completa (todas las referencias verificadas, ' +
    'retractación comprobada, ninguna pendiente por falta de red) y de si el sello es válido.',
    { label: `verificacion-informe:${ID}`, phase: 'Síntesis e informe', agentType: 'verificador-citas', schema: VERIF_INFORME },
  )
  : null

// ================================================================ puntuación ciega
phase('Puntuación')
const promptEvaluador = (ev) =>
  `Eres el evaluador independiente "${ev.id}". Puntúa la oportunidad ${ID} con los 9 criterios de docs/RUBRICA.md siguiendo .claude/skills/puntuacion/SKILL.md. ` +
  `Lee la oportunidad solo con nutria --agente ${ev.id} oportunidad ver ${ID} --sin-puntuaciones --json, el informe ${informe ? informe.archivo : `research/informes/${ID}.md (si existe)`}, ` +
  `los análisis research/revisiones/${ID}_*.md y las salidas de ${DIR}/ (incluidas las de nutria econ y nutria destino ruminal). ` +
  'No ejecutes nutria puntuar ranking ni leas puntuaciones o ficheros *_puntuacion_* de otros evaluadores. No te guíes por la decisión que proponga el informe: puntúa la evidencia. ' +
  `Guarda research/oportunidades/${ID}_puntuacion_${ev.id}.json con "evaluador": "${ev.id}" y regístralo con nutria --agente ${ev.id} puntuar registrar --datos <fichero>.`
const puntuar = async (ev) => {
  const base = { phase: 'Puntuación', agentType: 'evaluador-independiente', schema: PUNTUACION }
  const r = await lanzar(promptEvaluador(ev), { ...base, label: `puntuacion:${ev.id}`, model: ev.modelo })
  if (r) return { ...r, evaluador: ev.id, modelo: ev.modelo, reintento: false }
  if (presupuestoBajo()) {
    log(`Reintento de ${ev.id} omitido: quedan menos de ${UMBRAL_TOKENS} tokens de presupuesto`)
    return { evaluador: ev.id, modelo: ev.modelo, reintento: false, registrado: false, fallo: 'sin resultado; reintento omitido por presupuesto' }
  }
  log(`${ev.id} (${ev.modelo}) no devolvió resultado; se reintenta con ${MODELO_REINTENTO}`)
  const r2 = await lanzar(promptEvaluador(ev), { ...base, label: `puntuacion:${ev.id}:reintento`, model: MODELO_REINTENTO })
  if (r2) return { ...r2, evaluador: ev.id, modelo: MODELO_REINTENTO, reintento: true }
  return { evaluador: ev.id, modelo: MODELO_REINTENTO, reintento: true, registrado: false, fallo: 'sin resultado tras el reintento' }
}
const puntuaciones = (await parallel(EVALUADORES.map((ev) => () => puntuar(ev))))
  .map((p, i) => p || { evaluador: EVALUADORES[i].id, modelo: EVALUADORES[i].modelo, registrado: false, fallo: 'error inesperado' })
const registrados = puntuaciones.filter((p) => p.registrado).length

return {
  id: ID,
  descarte_rapido: resumenDescarte,
  evidencia: resumenEvidencia,
  aportaciones: aportaciones.map((a, i) => (a && !a.fallo
    ? { agente: a.agente, archivo: a.archivo, compuerta_propuesta: a.compuerta_propuesta || null,
      critica: a.critica ? a.critica.veredicto : 'sin crítica (fallo de agente)',
      fuentes_problematicas: a.verif ? a.verif.problemas.length : 'sin verificar (fallo de agente)' }
    : { agente: preguntas[i].agente, archivo: null, fallo: 'sin aportación (fallo de agente)' })),
  sin_aportacion: sinAportacion,
  informe: informe
    ? { archivo: informe.archivo, decision_propuesta: informe.decision_propuesta,
      verificacion_completa: verificacionInforme ? verificacionInforme.verificacion_completa : null,
      sello_valido: verificacionInforme ? verificacionInforme.sello_valido : null,
      problemas_de_citas: verificacionInforme ? verificacionInforme.problemas : null }
    : null,
  evaluadores: puntuaciones.map((p) => ({ id: p.evaluador, modelo: p.modelo, reintento: Boolean(p.reintento), registrado: Boolean(p.registrado), fallo: p.fallo || null })),
  evaluadores_registrados: registrados,
  fallos: resumenFallos(),
  siguiente: `nutria puntuar ranking (con menos de 3 evaluadores registrados ${ID} queda INSUFICIENTE); /red-team-auto research/informes/${ID}.md antes de pasarlo a revisado. ` +
    `La decisión sobre ${ID}, las compuertas "superada" y el paso del informe a "revisado" requieren firma o revisión humana.`,
}
