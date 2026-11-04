export const meta = {
  name: 'red-team-auto',
  description: 'Revisión adversarial de un documento: extrae las afirmaciones clave, somete cada una a tres refutadores con enfoques distintos (fuentes, biología, viabilidad) y agrega los veredictos (sostenida, debilitada, refutada, no_verificable) con quórum; segunda opinión opcional de otra familia de modelos según la política de datos',
  whenToUse: 'Antes de una compuerta o de dar por bueno un informe o plan. Argumento: la ruta del fichero, p. ej. "research/informes/O-0003.md", o {"archivo": "...", "sensibilidad": "confidencial"} (por defecto "confidencial"; "normal" solo para información no confidencial). El control de flujo es fijo, pero cada agente puede dar resultados distintos en cada ejecución.',
  phases: [
    { title: 'Afirmaciones', detail: 'extracción de las afirmaciones que sostienen la conclusión' },
    { title: 'Refutación', detail: '3 enfoques independientes por afirmación y agregación con quórum' },
    { title: 'Otra familia', detail: 'segunda opinión opcional con nutria llm (solo modelos que admita la política de datos)' },
    { title: 'Consolidación', detail: 'tabla de veredictos y acciones' },
  ],
}

// ---------------------------------------------------------------- argumentos
const leerArgs = (a) => {
  if (typeof a === 'string') {
    const t = a.trim()
    if (t.startsWith('{')) {
      try { return JSON.parse(t) } catch (e) { return {} }
    }
    return { archivo: t }
  }
  return a && typeof a === 'object' && !Array.isArray(a) ? a : {}
}
const A = leerArgs(args)
const ARCHIVO = typeof A.archivo === 'string' ? A.archivo.trim() : ''
if (!ARCHIVO) return { error: 'Indica la ruta del documento a revisar, p. ej. "research/informes/O-0003.md" o {"archivo": "..."}' }
const SENSIBILIDADES = ['confidencial', 'normal']
const SENSIBILIDAD = A.sensibilidad === undefined || A.sensibilidad === null || A.sensibilidad === '' ? 'confidencial' : String(A.sensibilidad)
if (!SENSIBILIDADES.includes(SENSIBILIDAD)) {
  return { error: `Sensibilidad no válida: "${SENSIBILIDAD}". Opciones: confidencial (por defecto) o normal (solo para información no confidencial).` }
}

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

// ---------------------------------------------------------------- esquemas y agregación
const S = (props, required) => ({ type: 'object', properties: props, required })
const STR = { type: 'string' }
const STRS = { type: 'array', items: { type: 'string' } }
const VEREDICTOS = ['sostenida', 'debilitada', 'refutada', 'no_verificable']

const AFIRMACIONES = S({ afirmaciones: { type: 'array', items: S({ id: STR, texto: STR, importancia: STR }, ['id', 'texto']) } }, ['afirmaciones'])
const VEREDICTO = S({ veredicto: { type: 'string', enum: VEREDICTOS }, motivo: STR, fuentes: STRS, accion: STR }, ['veredicto', 'motivo'])
const OTRA_FAMILIA = S({
  ejecutado: { type: 'boolean' },
  modelos_utilizables: STRS,
  respuestas: { type: 'array', items: S({ modelo: STR, texto: STR, omitido: STR, error: STR }, ['modelo']) },
  motivo: STR,
}, ['ejecutado', 'motivo'])

const ENFOQUES = [
  { id: 'fuentes', texto: '¿existen las fuentes, dicen eso y en qué especie, etapa y dosis? Usa nutria verificar --doi/--pmid con --cita y lee los resúmenes', tipo: 'verificador-citas' },
  { id: 'biologia', texto: '¿es biológicamente sostenible en la especie diana (destino digestivo, concentraciones alcanzables, explicaciones alternativas, diseño de los estudios)?', tipo: 'critico-red-team' },
  { id: 'viabilidad', texto: '¿resiste un examen regulatorio, de seguridad, industrial y económico?', tipo: 'critico-red-team' },
]

// Quórum: al menos 2 veredictos válidos. Reglas, en este orden: refutada con 2 o más "refutada";
// no_verificable con 2 o más "no_verificable"; debilitada si hay alguna "refutada" o "debilitada";
// sostenida solo con 2 o más "sostenida" y ninguna "refutada". El resto (p. ej. una "sostenida" y una
// "no_verificable") queda no_verificable: "sostenida" exige dos votos.
const QUORUM = 2
const agregar = (validos) => {
  if (validos.length < QUORUM) return 'sin_quorum'
  const n = (v) => validos.filter((x) => x.veredicto === v).length
  if (n('refutada') >= 2) return 'refutada'
  if (n('no_verificable') >= 2) return 'no_verificable'
  if (n('refutada') >= 1 || n('debilitada') >= 1) return 'debilitada'
  if (n('sostenida') >= 2) return 'sostenida'
  return 'no_verificable'
}

// ---------------------------------------------------------------- afirmaciones
phase('Afirmaciones')
log(`Estimación: 1 agente de extracción; después 3 por afirmación (5-15 afirmaciones), 1 de segunda opinión opcional y 1 de consolidación`)
const extraidas = await lanzar(
  `Lee ${ARCHIVO} e identifica las 5-15 afirmaciones que sostienen su conclusión (las que, si fueran falsas, cambiarían la decisión). Cópialas literalmente con un id A1..An.`,
  { label: 'extraccion', phase: 'Afirmaciones', agentType: 'director-cientifico', schema: AFIRMACIONES },
)
const afirmaciones = extraidas && Array.isArray(extraidas.afirmaciones) ? extraidas.afirmaciones.filter((af) => af && af.texto) : []
if (!afirmaciones.length) return { documento: ARCHIVO, error: 'No se extrajeron afirmaciones', fallos: resumenFallos() }
log(`${afirmaciones.length} afirmaciones. Estimación: ${1 + afirmaciones.length * ENFOQUES.length + 2} agentes en total`)

// ---------------------------------------------------------------- segunda opinión de otra familia (opcional, en paralelo)
const omitirOtraFamilia = presupuestoBajo()
if (omitirOtraFamilia) log('Segunda opinión de otra familia omitida por presupuesto')
const otraFamilia = omitirOtraFamilia
  ? Promise.resolve({ ejecutado: false, motivo: `omitida: quedan menos de ${UMBRAL_TOKENS} tokens de presupuesto` })
  : lanzar(
    'Paso opcional de segunda opinión de otra familia de modelos. 1) Ejecuta nutria --agente segunda-opinion llm modelos --json. ' +
    '2) Si algún modelo tiene "utilizable": true y el rol "critico", ejecuta ' +
    `nutria --agente segunda-opinion llm preguntar --rol critico --archivo ${ARCHIVO} --sensibilidad ${SENSIBILIDAD} --json ` +
    'y devuelve las respuestas literalmente (nutria omite los modelos cuya política de datos no admite esa sensibilidad; anota los omitidos y sus motivos). ' +
    '3) Si no hay ninguno, no ejecutes nada más: "ejecutado": false con el motivo. No cambies la sensibilidad ni la configuración de modelos y no envíes el documento por otra vía. ' +
    'La opinión de un modelo no es evidencia.',
    { label: 'otra-familia', phase: 'Otra familia', schema: OTRA_FAMILIA, effort: 'low' },
  ).then((r) => r || { ejecutado: false, motivo: 'fallo del agente de segunda opinión' })

// ---------------------------------------------------------------- refutación
const juzgadas = await pipeline(
  afirmaciones,
  (af) => parallel(ENFOQUES.map((e) => () => lanzar(
    `Intenta REFUTAR esta afirmación de ${ARCHIVO} desde el enfoque "${e.id}": ${e.texto}\n` +
    'Veredicto: "sostenida" (resiste el intento con evidencia comprobada), "debilitada" (se sostiene solo en parte: otra especie, dosis o condiciones, o evidencia débil), ' +
    '"refutada" (la evidencia la contradice, o la fuente no existe o no dice eso) o "no_verificable" (no puedes comprobarla: explica qué faltaría). ' +
    'Si no puedes comprobarla, nunca "sostenida". Solo lectura: no modifiques el documento ni otros ficheros.\n' +
    `Afirmación ${af.id}: ${af.texto}`,
    { label: `${af.id}:${e.id}`, phase: 'Refutación', agentType: e.tipo, schema: VEREDICTO },
  ))).then((vs) => {
    const validos = vs.map((v, i) => (v && VEREDICTOS.includes(v.veredicto) ? { enfoque: ENFOQUES[i].id, ...v } : null)).filter(Boolean)
    const votos = Object.fromEntries(VEREDICTOS.map((k) => [k, validos.filter((v) => v.veredicto === k).length]))
    return { ...af, veredictos: validos, votos, validos: validos.length, resultado: agregar(validos) }
  }),
)
const resultados = juzgadas.map((j, i) => j || { ...afirmaciones[i], veredictos: [], votos: {}, validos: 0, resultado: 'sin_quorum' })
const segundaOpinion = await otraFamilia

// ---------------------------------------------------------------- consolidación
phase('Consolidación')
const destino = ARCHIVO.replace(/^.*\//, '').replace(/\.md$/, '')
const sinQuorum = resultados.filter((j) => j.resultado === 'sin_quorum').map((j) => j.id)
const consolidado = await lanzar(
  `Escribe research/revisiones/${destino}_redteam.md con una tabla "afirmación | resultado | motivos por enfoque | acción" y un resumen de las tres debilidades más graves. ` +
  'Los resultados ya están agregados con quórum: no los cambies. Las afirmaciones "sin_quorum" no tienen veredicto (menos de 2 veredictos válidos): dilo y propone repetir su revisión. ' +
  'Integra la segunda opinión de otra familia de modelos, si la hubo, como opinión de un modelo (no como evidencia): dónde coincide y dónde discrepa de los veredictos. ' +
  'Registra con nutria decision registrar las acciones que cambien estados (sin firma humana).' +
  (fallos.length ? `\nAgentes que fallaron (decláralo en el documento): ${fallos.map((f) => f.agente).join(', ')}` : '') +
  `\nResultados: ${JSON.stringify(resultados)}\nSegunda opinión de otra familia: ${JSON.stringify(segundaOpinion)}`,
  { label: 'consolidacion', phase: 'Consolidación', agentType: 'director-cientifico' },
)

return {
  documento: ARCHIVO,
  sensibilidad: SENSIBILIDAD,
  resultados: resultados.map((j) => ({ id: j.id, resultado: j.resultado, validos: j.validos, votos: j.votos })),
  refutadas: resultados.filter((j) => j.resultado === 'refutada').length,
  sin_quorum: sinQuorum,
  otra_familia: { ejecutado: Boolean(segundaOpinion && segundaOpinion.ejecutado), motivo: segundaOpinion ? segundaOpinion.motivo : null,
    modelos: segundaOpinion && segundaOpinion.modelos_utilizables ? segundaOpinion.modelos_utilizables : [] },
  informe: `research/revisiones/${destino}_redteam.md`,
  nota: consolidado ? 'Consolidado escrito' : 'No se pudo escribir el consolidado',
  fallos: resumenFallos(),
}
