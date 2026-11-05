export const meta = {
  name: 'evaluar-oportunidad-auto',
  description: 'Evaluación en profundidad de una oportunidad: compuertas efectivas leídas del registro, descarte rápido en paralelo (regulación, toxicología, destino digestivo y modelo del ganadero), evidencia con doble extracción y metaanálisis si hay al menos 3 estudios comparables, especialistas con crítica y verificación, puntuación ciega por 3 evaluadores independientes antes de la síntesis, informe con Anexo A y comprobación literal del sello; las puntuaciones solo se registran con sello válido',
  whenToUse: 'Cuando una oportunidad registrada (O-xxxx) necesita una evaluación completa. Argumento: el ID, p. ej. "O-0003", o {"id": "O-0003", "max_estudios": 10, "max_preguntas": 10}. Se detiene antes de gastar más si alguna compuerta está bloqueada en el registro o alguien la propone "bloqueada". Con {"id": "O-0003", "registrar_puntuaciones": [...]} solo comprueba el sello y registra las puntuaciones que quedaron pendientes. El control de flujo es fijo, pero cada agente puede dar resultados distintos en cada ejecución. Consume muchos tokens.',
  phases: [
    { title: 'Compuertas del registro', detail: 'compuertas efectivas (salida literal de nutria oportunidad ver) al empezar, tras el descarte rápido y antes de puntuar' },
    { title: 'Descarte rápido', detail: 'regulación, toxicología, destino digestivo y modelo del ganadero en paralelo' },
    { title: 'Plan', detail: 'pregunta PECO, variables y preguntas para cada especialista (con tope)' },
    { title: 'Evidencia', detail: 'revisión bibliográfica, doble extracción y metaanálisis' },
    { title: 'Especialistas', detail: 'análisis independientes y propuestas de compuertas' },
    { title: 'Verificación', detail: 'crítica y verificación de fuentes de cada aportación' },
    { title: 'Puntuación', detail: '3 evaluadores independientes y ciegos con modelos distintos, antes de la síntesis y del informe' },
    { title: 'Síntesis e informe', detail: 'informe de 20 secciones con las puntuaciones emitidas, decisión propuesta y Anexo A de respaldo' },
    { title: 'Sello y registro', detail: 'comprobación literal del sello; las puntuaciones se registran solo si es válido' },
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

// Preguntas que se lanzan siempre (sus agentes proponen compuertas); cuentan dentro del tope de preguntas.
const OBLIGATORIAS = [
  { agente: 'desarrollo-industrial', pregunta: 'Viabilidad de fabricación, estabilidad y método analítico para identificar y cuantificar el activo (o un marcador) en el producto, la premezcla y el pienso.', entregable: 'ficha industrial y propuesta de la compuerta analitica' },
  { agente: 'economia-mercado-pi', pregunta: 'Valor para la empresa (nutria econ desarrollador, entradas con fuente o [SUP]), mercado, productos competidores, patentes y libertad de operación preliminar (la novedad frente a productos y patentes es tuya; la de la literatura, de investigador-bibliografico).', entregable: 'rNPV, mercado, novedad en productos y patentes y propuesta de la compuerta propiedad_intelectual' },
  { agente: 'sostenibilidad-bienestar', pregunta: 'Impacto ambiental, bienestar y ética de la validación futura (3R, Directiva 2010/63/UE).', entregable: 'propuesta de la compuerta etica' },
]

// Opciones enteras con tope: un valor no válido detiene el workflow con un mensaje claro (nunca se sustituye en silencio).
const opcionEntera = (nombre, min, max, defecto) => {
  const v = A[nombre]
  if (v === undefined || v === null) return { valor: defecto }
  if (!Number.isInteger(v) || v < min || v > max) {
    return { error: `"${nombre}" debe ser un entero entre ${min} y ${max} (recibido: ${JSON.stringify(v)})` }
  }
  return { valor: v }
}
const OPCIONES = {
  max_estudios: opcionEntera('max_estudios', 1, 30, 10),
  max_preguntas: opcionEntera('max_preguntas', OBLIGATORIAS.length, 15, 10),
}
const erroresOpciones = Object.values(OPCIONES).filter((o) => o.error).map((o) => o.error)
if (erroresOpciones.length) return { id: ID, error: erroresOpciones.join('; ') }
const MAX_ESTUDIOS = OPCIONES.max_estudios.valor
const MAX_PREGUNTAS = OPCIONES.max_preguntas.valor
const MIN_COMPARABLES = 3
const DIR = `research/datos/${ID}`
const RUTA_INFORME = `research/informes/${ID}.md`
const REGLAS = 'Sigue CLAUDE.md: etiquetas [PUB]/[DER]/[HIP]/[SUP]/[DESC], nada de citas de memoria, contexto biológico completo. Atribuye tus órdenes con "nutria --agente <tu-rol>" y nunca uses --firma-humana.'

const EVALUADORES = [
  { id: 'evaluador-1', modelo: 'opus' },
  { id: 'evaluador-2', modelo: 'sonnet' },
  { id: 'evaluador-3', modelo: 'fable' },
]
const MODELO_REINTENTO = 'opus'
const N_CRITERIOS = 9
const rutaPuntuacion = (evaluador) => `research/oportunidades/${ID}_puntuacion_${evaluador}.json`

// Modo "solo registrar": puntuaciones emitidas en una ejecución anterior cuyo informe no tenía sello válido.
// Cada elemento: {"evaluador": "evaluador-N", "sha256": "<64 hex>", "modelo": "..."} tal como lo devolvió esa ejecución.
const SHA_RE = /^[0-9a-f]{64}$/
const normSha = (s) => String(s || '').trim().split(/\s+/)[0].toLowerCase()
let PENDIENTES = null
if (A.registrar_puntuaciones !== undefined && A.registrar_puntuaciones !== null) {
  const lista = Array.isArray(A.registrar_puntuaciones) ? A.registrar_puntuaciones : []
  const validas = lista.filter((p) => p && typeof p === 'object' && typeof p.evaluador === 'string' &&
    /^evaluador-\d+$/.test(p.evaluador) && SHA_RE.test(normSha(p.sha256)))
  const ids = validas.map((p) => p.evaluador)
  if (!lista.length || validas.length !== lista.length || new Set(ids).size !== ids.length) {
    return { id: ID, error: '"registrar_puntuaciones" debe ser la lista "puntuaciones_pendientes" que devolvió la evaluación anterior: ' +
      '[{"evaluador": "evaluador-N", "sha256": "<64 caracteres hexadecimales>"}], sin evaluadores repetidos' }
  }
  PENDIENTES = validas.map((p) => ({ evaluador: p.evaluador, sha256: normSha(p.sha256),
    modelo: typeof p.modelo === 'string' && p.modelo ? p.modelo : MODELO_REINTENTO, archivo: rutaPuntuacion(p.evaluador) }))
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

// ---------------------------------------------------------------- esquemas
const S = (props, required) => ({ type: 'object', properties: props, required })
const STR = { type: 'string' }
const STRS = { type: 'array', items: { type: 'string' } }
const NUM = { type: 'number' }
const INT = { type: 'integer' }
const BOOL = { type: 'boolean' }
const COMPUERTAS = ['seguridad', 'regulatoria', 'analitica', 'propiedad_intelectual', 'etica', 'destino_digestivo']
const ESTADOS_COMPUERTA = ['superada', 'no_resuelta', 'bloqueada']
const ESTADO_COMPUERTA = { type: 'string', enum: ESTADOS_COMPUERTA }
// Estados de la oportunidad con los que no se gasta en una evaluación (salir de ellos exige firma humana).
const ESTADOS_DETENIDOS = ['bloqueada', 'descartada']

const ESPECIALISTAS = ['investigador-bibliografico', 'estadistico', 'fisiologia-bioquimica', 'compuestos-toxicologia',
  'nutricion-formulacion', 'bioinformatico', 'regulacion-seguridad', 'desarrollo-industrial', 'economia-mercado-pi',
  'sostenibilidad-bienestar']
// Quién propone cada compuerta (los agentes solo proponen; una persona firma). "bloqueada" la puede proponer cualquiera.
const COMPUERTA_DE = {
  'regulacion-seguridad': 'regulatoria',
  'compuestos-toxicologia': 'seguridad',
  'fisiologia-bioquimica': 'destino_digestivo',
  'desarrollo-industrial': 'analitica',
  'economia-mercado-pi': 'propiedad_intelectual',
  'sostenibilidad-bienestar': 'etica',
}

// Salida literal de una orden: el workflow la interpreta él mismo en vez de fiarse de lo que declare el agente.
const SALIDA_ORDEN = S({ orden: STR, codigo_salida: INT, salida: STR }, ['orden', 'codigo_salida', 'salida'])
const DESCARTE = S({ analisis: STR, estado: ESTADO_COMPUERTA, motivo: STR, resumen: STR, archivo: STR, fuentes: STRS },
  ['analisis', 'estado', 'motivo', 'resumen'])
const PLAN = S({
  peco: STR,
  variables: { type: 'array', items: STR, minItems: 1 },
  preguntas: { type: 'array', maxItems: MAX_PREGUNTAS, items: S({ agente: { type: 'string', enum: ESPECIALISTAS }, pregunta: STR, entregable: STR }, ['agente', 'pregunta', 'entregable']) },
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
const PUNTUACION_EMITIDA = S({ archivo: STR, sha256: STR, resumen: STR }, ['archivo', 'sha256'])
const INFORME = S({ archivo: STR, decision_propuesta: STR, resumen: STR }, ['archivo', 'decision_propuesta'])
const VERIF_INFORME = S({
  archivo: STR, filas_anexo_a: NUM,
  problemas: { type: 'array', items: S({ ref: STR, estado: STR, motivo: STR }, ['ref', 'estado', 'motivo']) },
}, ['problemas'])
const SELLO = S({ comprobar: SALIDA_ORDEN, sello: SALIDA_ORDEN }, ['comprobar', 'sello'])
const REGISTRO_PUNTUACION = S({ sha256: STR, coincide: BOOL, orden: STR, codigo_salida: INT, salida: STR },
  ['sha256', 'coincide', 'codigo_salida', 'salida'])

// ---------------------------------------------------------------- utilidades de interpretación
const parsear = (texto) => {
  try { return { ok: true, valor: JSON.parse(String(texto)) } } catch (e) { return { ok: false, valor: null } }
}
const esObjeto = (x) => Boolean(x) && typeof x === 'object' && !Array.isArray(x)
const recorte = (t) => String(t === undefined || t === null ? '' : t).slice(0, 300)

// Compuertas efectivas del registro, leídas de la salida literal de nutria oportunidad ver (falla en cerrado).
const ordenRegistro = `nutria --agente orquestador --json oportunidad ver ${ID} --sin-puntuaciones`
const interpretarRegistro = (r) => {
  if (!r) return { ok: false, motivo: 'el agente no devolvió la salida del registro (fallo de agente)' }
  if (r.codigo_salida !== 0) return { ok: false, motivo: `la orden terminó con código ${r.codigo_salida}: ${recorte(r.salida)}` }
  const p = parsear(r.salida)
  if (!p.ok || !esObjeto(p.valor)) return { ok: false, motivo: 'la salida no es el JSON de nutria oportunidad ver' }
  const o = p.valor
  if (o.id !== ID) return { ok: false, motivo: `la salida corresponde a ${o.id || 'otra entidad'}, no a ${ID}` }
  const ef = o.compuertas_efectivas
  if (!esObjeto(ef)) return { ok: false, motivo: 'la salida no incluye compuertas_efectivas' }
  const faltan = COMPUERTAS.filter((g) => !(g in ef))
  const raras = Object.keys(ef).filter((g) => !ESTADOS_COMPUERTA.includes(ef[g]))
  if (faltan.length || raras.length) {
    return { ok: false, motivo: `compuertas_efectivas incompleta o con estados no válidos (faltan: ${faltan.join(', ') || 'ninguna'}; no válidas: ${raras.join(', ') || 'ninguna'})` }
  }
  const filas = Array.isArray(o.compuertas) ? o.compuertas.filter(esObjeto) : []
  const bloqueadas = Object.keys(ef).filter((g) => ef[g] === 'bloqueada').map((g) => {
    const fila = filas.find((f) => f.gate === g)
    return {
      compuerta: g,
      motivo: fila && fila.rationale ? fila.rationale : (o.regulatory_route ? `ruta regulatoria ${o.regulatory_route}` : 'sin motivo registrado'),
      propuesta_por: fila ? fila.decided_by || null : null,
      firmada: Boolean(fila && fila.human_signoff),
    }
  })
  return { ok: true, estado: o.status || null, ruta: o.regulatory_route || null, efectivas: ef, bloqueadas }
}
const leerRegistro = async (momento) => interpretarRegistro(await lanzar(
  `Lectura del registro de ${ID} (${momento}). Ejecuta exactamente esta orden y ninguna otra: ${ordenRegistro}\n` +
  'Devuelve la orden ejecutada, su código de salida y su salida estándar literal y completa (sin resumir, corregir ni reformatear). ' +
  'No modifiques ningún fichero ni el registro.',
  { label: `compuertas-registro:${momento}`, phase: 'Compuertas del registro', schema: SALIDA_ORDEN, effort: 'low' },
))

// Sello del informe: el workflow interpreta la salida literal de nutria informe comprobar e informe sello.
const interpretarSello = (r) => {
  if (!r) return { valido: false, motivo: 'el agente del sello no devolvió resultado (fallo de agente)' }
  const c = esObjeto(r.comprobar) ? r.comprobar : {}
  const s = esObjeto(r.sello) ? r.sello : {}
  const ps = parsear(s.salida)
  if (!ps.ok || !esObjeto(ps.valor) || typeof ps.valor.valido !== 'boolean') {
    return { valido: false, motivo: 'la salida de nutria informe sello no es interpretable' }
  }
  if (ps.valor.valido !== true) return { valido: false, motivo: `nutria informe sello: ${ps.valor.mensaje || 'sello no válido'}` }
  if (s.codigo_salida !== 0) return { valido: false, motivo: `salida incoherente: "valido": true con código de salida ${s.codigo_salida}` }
  const pc = parsear(c.salida)
  if (!pc.ok || !esObjeto(pc.valor)) return { valido: false, motivo: 'la salida de nutria informe comprobar no es interpretable' }
  const resumen = esObjeto(pc.valor.resumen) ? pc.valor.resumen : null
  const errores = (Array.isArray(pc.valor.incidencias) ? pc.valor.incidencias : []).filter((i) => esObjeto(i) && i.severity === 'error')
  if (!resumen || resumen.completa !== true) {
    return { valido: false, motivo: `verificación en línea incompleta (${resumen ? `${resumen.pendientes} pendiente(s), ${resumen.con_error} con error` : 'sin resumen'})` }
  }
  if (errores.length) return { valido: false, motivo: `nutria informe comprobar: ${errores.length} error(es): ${recorte(errores.map((i) => i.message).join('; '))}` }
  if (c.codigo_salida !== 0) return { valido: false, motivo: `nutria informe comprobar terminó con código ${c.codigo_salida}` }
  return { valido: true, motivo: ps.valor.mensaje || 'sello válido', referencias: resumen.total }
}

// Registro de una puntuación: lo que confirma la salida literal de nutria puntuar registrar.
const interpretarRegistroPuntuacion = (r, p) => {
  if (!r) return { registrado: false, motivo: 'el agente de registro no devolvió resultado (fallo de agente)' }
  const integra = r.coincide === true && normSha(r.sha256) === p.sha256
  const q = parsear(r.salida)
  const confirmado = r.codigo_salida === 0 && q.ok && esObjeto(q.valor) && q.valor.oportunidad === ID && q.valor.evaluador === p.evaluador
  if (confirmado && !integra) {
    return { registrado: true, criterios: q.valor.criterios, alerta: 'registrada aunque el sha256 del fichero no coincide con el de la puntuación emitida: revísalo' }
  }
  if (!integra) return { registrado: false, motivo: 'el fichero de puntuación cambió después de puntuar (sha256 distinto): no se registra' }
  if (!confirmado) return { registrado: false, motivo: `nutria puntuar registrar no confirma el registro (código ${r.codigo_salida}): ${recorte(r.salida)}` }
  const r2 = { registrado: true, criterios: q.valor.criterios }
  if (q.valor.criterios !== N_CRITERIOS) r2.alerta = `registrados ${q.valor.criterios} criterios de ${N_CRITERIOS}`
  return r2
}

// ---------------------------------------------------------------- pasos comunes: sello y registro de puntuaciones
const comprobarSello = () => lanzar(
  `Comprobación literal del sello del informe ${RUTA_INFORME}. Ejecuta exactamente estas dos órdenes, en este orden, y ninguna otra: ` +
  `1) nutria --agente verificador-citas --json informe comprobar ${RUTA_INFORME} --online --sellar; ` +
  `2) nutria --agente verificador-citas --json informe sello ${RUTA_INFORME}. ` +
  'Devuelve para cada una la orden, su código de salida y su salida estándar literal y completa (sin resumir, corregir ni reformatear). ' +
  'No modifiques el informe ni ningún otro fichero y no registres verificaciones manuales.',
  { label: `sello:${ID}`, phase: 'Sello y registro', agentType: 'verificador-citas', schema: SELLO, effort: 'low' },
)
const registrarPuntuacion = (p) => lanzar(
  `Eres el evaluador independiente "${p.evaluador}". Tu puntuación de ${ID} ya está en ${p.archivo} y el informe de ${ID} tiene un sello válido: ahora se registra. ` +
  `1) Calcula el sha256 de ${p.archivo} (sha256sum o shasum -a 256); debe ser ${p.sha256}. Si no coincide, no registres nada: devuelve "coincide": false. ` +
  `2) Si coincide, ejecuta sin modificar el fichero: nutria --agente ${p.evaluador} --json puntuar registrar --datos ${p.archivo} ` +
  'y devuelve la orden, su código de salida y su salida estándar literal y completa. No leas otros ficheros ni ejecutes otras órdenes.',
  { label: `registro-puntuacion:${p.evaluador}`, phase: 'Sello y registro', agentType: 'evaluador-independiente', model: p.modelo,
    schema: REGISTRO_PUNTUACION, effort: 'low' },
).then((r) => ({ ...p, ...interpretarRegistroPuntuacion(r, p) }))

const pendientesDe = (lista) => lista.filter((p) => !p.registrado).map((p) => ({ evaluador: p.evaluador, sha256: p.sha256, modelo: p.modelo }))
const siguienteSinSello = (motivo, pendientes) => `El informe ${RUTA_INFORME} no tiene un sello válido (${motivo}): no se da por bueno y ` +
  (pendientes.length
    ? 'las puntuaciones emitidas NO se han registrado. Corrige el informe (referencias, Anexo A) y vuelve a verificarlo; después lanza este workflow ' +
      `por su nombre con la herramienta Workflow y los argumentos ${JSON.stringify({ id: ID, registrar_puntuaciones: pendientes })} para comprobar ` +
      'el sello y registrar las puntuaciones sin repetir la evaluación.'
    : 'no hay puntuaciones emitidas que registrar: hará falta una nueva puntuación ciega.') +
  ' Sin red no hay sello válido. La decisión y el paso del informe a "revisado" requieren firma o revisión humana.'

const SIGUIENTE_BLOQUEO = 'Una persona revisa el motivo. Salir de una compuerta "bloqueada" (o de un estado bloqueada o descartada) exige firma ' +
  'humana en su terminal; la propuesta de descarte económico la decide una persona con nutria decision registrar. Si se rediseña la oportunidad, ' +
  'vuelve a lanzar este workflow por su nombre con la herramienta Workflow.'
const SIGUIENTE_ILEGIBLE = 'Sin las compuertas efectivas del registro no se gasta en la evaluación (falla en cerrado). Comprueba que la orden ' +
  `${ordenRegistro} funciona (nutria doctor) y vuelve a lanzar este workflow por su nombre con la herramienta Workflow.`
const bloqueoDeRegistro = (reg) => {
  if (!reg.ok) return { tipo: 'registro_ilegible', motivo: `no se pudieron leer las compuertas del registro: ${reg.motivo}`, siguiente: SIGUIENTE_ILEGIBLE }
  if (reg.bloqueadas.length) {
    return { tipo: 'compuertas_bloqueadas', motivo: `compuertas bloqueadas en el registro: ${reg.bloqueadas.map((b) => b.compuerta).join(', ')}`, siguiente: SIGUIENTE_BLOQUEO }
  }
  if (ESTADOS_DETENIDOS.includes(reg.estado)) return { tipo: 'estado', motivo: `la oportunidad está en estado "${reg.estado}"`, siguiente: SIGUIENTE_BLOQUEO }
  return null
}

// ================================================================ modo "solo registrar"
if (PENDIENTES) {
  phase('Compuertas del registro')
  log(`Registro de ${PENDIENTES.length} puntuación(es) pendiente(s) de ${ID}. Estimación: ${2 + PENDIENTES.length} agentes ` +
    `(lectura del registro, comprobación del sello y ${PENDIENTES.length} registro(s))`)
  const reg = await leerRegistro('inicio')
  const b = bloqueoDeRegistro(reg)
  if (b) {
    return { id: ID, detenida_en: 'Compuertas del registro', motivo: b.motivo, bloqueos: reg.ok ? reg.bloqueadas : [],
      puntuaciones_pendientes: pendientesDe(PENDIENTES), fallos: resumenFallos(), siguiente: b.siguiente }
  }
  phase('Sello y registro')
  const sello = interpretarSello(await comprobarSello())
  if (!sello.valido) {
    return { id: ID, informe: { archivo: RUTA_INFORME, sello_valido: false, motivo_sello: sello.motivo }, evaluadores_registrados: 0,
      puntuaciones_pendientes: pendientesDe(PENDIENTES), fallos: resumenFallos(), siguiente: siguienteSinSello(sello.motivo, pendientesDe(PENDIENTES)) }
  }
  const hechos = (await parallel(PENDIENTES.map((p) => () => registrarPuntuacion(p))))
    .map((x, i) => x || { ...PENDIENTES[i], registrado: false, motivo: 'error inesperado' })
  return {
    id: ID,
    informe: { archivo: RUTA_INFORME, sello_valido: true, motivo_sello: sello.motivo },
    evaluadores: hechos.map((p) => ({ id: p.evaluador, modelo: p.modelo, registrado: Boolean(p.registrado), motivo: p.motivo || null, alerta: p.alerta || null })),
    evaluadores_registrados: hechos.filter((p) => p.registrado).length,
    puntuaciones_pendientes: pendientesDe(hechos),
    fallos: resumenFallos(),
    siguiente: 'Con 3 evaluadores registrados, nutria puntuar ranking. La decisión, las compuertas "superada" y el paso del informe a "revisado" ' +
      'requieren firma o revisión humana.',
  }
}

// ================================================================ evaluación completa
const N_LECTURAS = 3
const FIJOS = N_LECTURAS + 4 + 1 + 1 + EVALUADORES.length + 2 + 1 + EVALUADORES.length
const MAXIMO = FIJOS + EVALUADORES.length + 3 * MAX_ESTUDIOS + 1 + 3 * MAX_PREGUNTAS
log(`Evaluación de ${ID}. Estimación: como máximo ${MAXIMO} agentes; si no se detiene antes, al menos ${FIJOS} fijos (${N_LECTURAS} lecturas de ` +
  `compuertas del registro, 4 de descarte rápido, plan, revisión bibliográfica, ${EVALUADORES.length} evaluadores, informe y su verificación, ` +
  `sello y ${EVALUADORES.length} registros de puntuación), más 3 por estudio con doble extracción (máximo ${MAX_ESTUDIOS}), 1 de metaanálisis ` +
  `si hay al menos ${MIN_COMPARABLES} estudios comparables, hasta 3 por pregunta de especialista (máximo ${MAX_PREGUNTAS} preguntas) y hasta ` +
  `${EVALUADORES.length} reintentos de evaluadores` +
  (typeof budget !== 'undefined' && budget && budget.total ? `. Presupuesto restante: ${Math.round(budget.remaining())} tokens` : ''))

const detener = (fase, extra, siguiente) => ({ id: ID, detenida_en: fase, ...extra, fallos: resumenFallos(), siguiente: siguiente || SIGUIENTE_BLOQUEO })

// ================================================================ compuertas del registro (antes de gastar)
phase('Compuertas del registro')
const registroInicial = await leerRegistro('inicio')
const bloqueoInicial = bloqueoDeRegistro(registroInicial)
if (bloqueoInicial) {
  log(`Evaluación detenida antes de empezar: ${bloqueoInicial.motivo}`)
  return detener('Compuertas del registro', { motivo: bloqueoInicial.motivo, bloqueos: registroInicial.ok ? registroInicial.bloqueadas : [] },
    bloqueoInicial.siguiente)
}

// ================================================================ descarte rápido
phase('Descarte rápido')
const DESCARTE_RAPIDO = [
  { agente: 'regulacion-seguridad', analisis: 'regulatoria', registrable: true,
    tarea: 'clasificación y vía regulatoria con la skill triage-regulatorio (primero pienso dietético, después medicamento por alegación y por función, sustancias prohibidas o restringidas, incluidos los moduladores ruminales antimicrobianos, y alegaciones)' },
  { agente: 'compuestos-toxicologia', analisis: 'seguridad', registrable: true,
    tarea: 'identidad del candidato y señales de seguridad para la especie diana, el consumidor, el usuario y el medio ambiente a la dosis prevista' },
  { agente: 'fisiologia-bioquimica', analisis: 'destino_digestivo', registrable: true,
    tarea: '¿llega el activo a la diana en cantidad relevante a una dosis viable? En rumiantes, calcula la degradabilidad efectiva y la fracción que escapa con nutria destino ruminal (a, b y kd con fuente; kp con fuente o [SUP]) o con una fracción de escape publicada, y ten en cuenta la adaptación de la microbiota ruminal (las incubaciones cortas en lote no la capturan)' },
  { agente: 'economia-mercado-pi', analisis: 'modelo_ganadero', registrable: false,
    tarea: `presupuesto parcial del ganadero con nutria econ ganadero ${DIR}/econ_ganadero.toml (cada entrada con fuente o [SUP]): coste por animal y día, respuesta umbral, P(beneficio neto > 0) y escenarios P10/P50/P90` },
]
const promptDescarte = (d) => `${REGLAS}\nDescarte rápido de ${ID} (nutria --agente ${d.agente} oportunidad ver ${ID} --sin-puntuaciones --json). Tu análisis: ${d.tarea}. ` +
  `Guarda tu análisis en research/revisiones/${ID}_descarte_${d.analisis}.md. ` +
  (d.registrable
    ? `PROPÓN la compuerta "${d.analisis}" y regístrala como propuesta, sin --firma-humana: nutria --agente ${d.agente} puntuar compuerta ${ID} ${d.analisis} ` +
      '<superada|no_resuelta|bloqueada> --motivo "...". "bloqueada" actúa de inmediato y detiene esta evaluación; "superada" no cuenta hasta que la firme una persona. ' +
      'Devuelve el mismo estado que registres.'
    : 'No es una compuerta del registro: devuelve "bloqueada" solo si el beneficio neto es negativo incluso en el escenario optimista (P90) o el coste por animal y día es ' +
      'inasumible (ancla 0 de rentabilidad_ganadero), y en ese caso registra la propuesta de descarte con nutria decision registrar --entidad ' + ID +
      ' --decision "propuesta de descarte (modelo del ganadero)" --motivo "..." sin --firma-humana; "no_resuelta" si faltan datos; "superada" en otro caso.')

const descarte = await parallel(DESCARTE_RAPIDO.map((d) => () => lanzar(promptDescarte(d),
  { label: `descarte:${d.analisis}`, phase: 'Descarte rápido', agentType: d.agente, schema: DESCARTE })))
const resumenDescarte = DESCARTE_RAPIDO.map((d, i) => {
  const r = descarte[i]
  return { analisis: d.analisis, agente: d.agente, estado: r ? r.estado : 'sin_resultado', motivo: r ? r.motivo : 'el agente no devolvió resultado',
    archivo: r && r.archivo ? r.archivo : `research/revisiones/${ID}_descarte_${d.analisis}.md` }
})
const bloqueos = resumenDescarte.filter((d) => d.estado === 'bloqueada')
if (bloqueos.length) {
  log(`Descarte rápido: ${bloqueos.map((b) => b.analisis).join(', ')} propone(n) "bloqueada"; la evaluación se detiene`)
  return detener('Descarte rápido', {
    bloqueos: bloqueos.map((b) => ({ analisis: b.analisis, agente: b.agente, motivo: b.motivo, archivo: b.archivo })),
    descarte_rapido: resumenDescarte,
  })
}
const sinResultado = resumenDescarte.filter((d) => d.estado === 'sin_resultado').map((d) => d.analisis)
if (sinResultado.length) log(`Descarte rápido incompleto (${sinResultado.join(', ')} sin resultado): se continúa, pero esas propuestas quedan pendientes`)
// Un agente puede registrar "bloqueada" y no decirlo (o fallar después de registrarla): el registro manda.
const registroDescarte = await leerRegistro('descarte')
const bloqueoDescarte = bloqueoDeRegistro(registroDescarte)
if (bloqueoDescarte) {
  log(`Evaluación detenida tras el descarte rápido: ${bloqueoDescarte.motivo}`)
  return detener('Descarte rápido', { motivo: bloqueoDescarte.motivo, bloqueos: registroDescarte.ok ? registroDescarte.bloqueadas : [],
    descarte_rapido: resumenDescarte }, bloqueoDescarte.siguiente)
}

// ================================================================ plan
phase('Plan')
const plan = await lanzar(
  `${REGLAS}\nPrepara la evaluación de ${ID} (nutria --agente director-cientifico oportunidad ver ${ID} --sin-puntuaciones --json; plan en research/planes/${ID}.md si existe). ` +
  'Formula la pregunta PECO y las variables de resultado (la primera es la principal para la extracción y el metaanálisis). Después, una pregunta concreta y su entregable ' +
  `para cada especialista que aporte a la decisión (omite los que no aporten; como máximo ${MAX_PREGUNTAS} preguntas en total, incluidas las de desarrollo-industrial, ` +
  'economia-mercado-pi y sostenibilidad-bienestar, que participan siempre para proponer sus compuertas). La fase de evidencia ya lanza la revisión bibliográfica, ' +
  'la doble extracción y el metaanálisis: asigna preguntas a investigador-bibliografico o estadistico para tareas adicionales (p. ej. novedad en la literatura, ' +
  'potencia, modelos predictivos); la novedad frente a productos y patentes es de economia-mercado-pi.\nDescarte rápido: ' + JSON.stringify(resumenDescarte),
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
  'variable y unidad combinables, diseño compatible), con el motivo. En los moduladores ruminales, anota la duración de cada ensayo: una incubación corta en lote ' +
  'no captura la adaptación de la microbiota.',
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
if (omitidos.length) log(`Límite de ${MAX_ESTUDIOS} estudios para la doble extracción: quedan sin extraer ${omitidos.length} (${omitidos.join(', ')}); súbelo con args.max_estudios (máximo 30)`)
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
// Tope de preguntas: las obligatorias primero (proponen compuertas) y después las del plan, en su orden. Si el plan
// trae su propia pregunta para un agente obligatorio, se le añade la obligatoria: la propuesta de compuerta y la
// novedad frente a productos y patentes no dependen de cómo redacte el plan la pregunta.
const delPlan = (Array.isArray(plan.preguntas) ? plan.preguntas : []).filter((q) => q && ESPECIALISTAS.includes(q.agente) && q.pregunta)
const usadasDelPlan = new Set()
const obligatorias = OBLIGATORIAS.map((o) => {
  const q = delPlan.find((x) => x.agente === o.agente)
  if (!q) return o
  usadasDelPlan.add(q)
  return { agente: o.agente, pregunta: `${q.pregunta} Además (obligatorio): ${o.pregunta}`, entregable: `${q.entregable}; ${o.entregable}` }
})
const resto = delPlan.filter((q) => !usadasDelPlan.has(q))
const preguntas = [...obligatorias, ...resto.slice(0, MAX_PREGUNTAS - obligatorias.length)]
const sinLanzar = resto.slice(MAX_PREGUNTAS - obligatorias.length)
if (sinLanzar.length) {
  log(`Límite de ${MAX_PREGUNTAS} preguntas de especialista: quedan sin lanzar ${sinLanzar.length} (${sinLanzar.map((q) => q.agente).join(', ')}); súbelo con args.max_preguntas (máximo 15)`)
}
log(`Especialistas: ${preguntas.length} preguntas (hasta ${preguntas.length * 3} agentes con crítica y verificación); después, 1 lectura del registro, ` +
  `${EVALUADORES.length} evaluadores, informe, su verificación, sello y ${EVALUADORES.length} registros de puntuación`)
const contextoEvidencia = `Evidencia disponible: revisión ${resumenEvidencia.revision || 'no disponible (fallo de agente)'}; ${comparables} estudio(s) con doble extracción registrada en ${DIR}/; ` +
  `metaanálisis: ${metaanalisis ? metaanalisis.archivo : 'no realizado'}; análisis del descarte rápido: ${resumenDescarte.map((d) => `${d.analisis}=${d.archivo}`).join(', ')}.`
const promptEspecialista = (q, i) => {
  const compuerta = COMPUERTA_DE[q.agente]
  const yaPropuesta = DESCARTE_RAPIDO.some((d) => d.agente === q.agente && d.registrable)
  let nota = ''
  if (compuerta && yaPropuesta) {
    nota = ` Si tu análisis cambia la propuesta de la compuerta "${compuerta}", regístrala de nuevo como propuesta, sin --firma-humana ` +
      `(nutria --agente ${q.agente} puntuar compuerta ${ID} ${compuerta} <estado> --motivo "...") y devuélvela en "compuerta_propuesta".`
  } else if (compuerta) {
    nota = ` PROPÓN la compuerta "${compuerta}" y regístrala como propuesta, sin --firma-humana: nutria --agente ${q.agente} puntuar compuerta ${ID} ${compuerta} ` +
      '<superada|no_resuelta|bloqueada> --motivo "..." y devuélvela en "compuerta_propuesta". "bloqueada" actúa de inmediato y detiene esta evaluación; ' +
      '"superada" no cuenta hasta que la firme una persona.'
  }
  nota += ' Si ves un motivo para bloquear otra compuerta, puedes proponerla "bloqueada" (con tu propio --agente); las demás propuestas son de su rol propietario.'
  return `${REGLAS}\nOportunidad ${ID}. Tu pregunta: ${q.pregunta}\nEntregable: ${q.entregable}.\n${contextoEvidencia}\n` +
    `Guarda tu análisis en research/revisiones/${ID}_${String(i + 1).padStart(2, '0')}_${q.agente}.md y devuelve las afirmaciones clave con etiqueta y fuentes.` + nota
}

// Cada aportación se verifica en cuanto termina (pipeline, sin esperar al resto). Quien la escribió no elige qué se
// somete a refutación: el crítico y el verificador leen el fichero completo y eligen ellos las afirmaciones; las que
// declara el especialista son solo una guía.
const aportaciones = await pipeline(
  preguntas,
  (q, _orig, i) => lanzar(promptEspecialista(q, i), { label: `${q.agente}:${i + 1}`, phase: 'Especialistas', agentType: q.agente, schema: APORTACION }),
  (ap, q, i) => {
    if (!ap) return { agente: q.agente, pregunta: q.pregunta, fallo: true }
    const archivo = ap.archivo || `research/revisiones/${ID}_${String(i + 1).padStart(2, '0')}_${q.agente}.md`
    return parallel([
      () => lanzar(`${REGLAS}\nIntenta refutar el análisis de ${q.agente} sobre ${ID}. Lee completo ${archivo} (no lo escribiste) e identifica tú las afirmaciones ` +
        'que sostienen su conclusión; no te limites a las que el autor declara como clave (abajo, solo como guía). Para cada una que no resista, di por qué; ' +
        `veredicto global y debilidades.\nAfirmaciones que declara el autor: ${JSON.stringify(ap.afirmaciones || [])}`,
        { label: `critica:${q.agente}:${i + 1}`, phase: 'Verificación', agentType: 'critico-red-team', schema: CRITICA }),
      () => lanzar(`${REGLAS}\nVerifica las fuentes de TODAS las afirmaciones [PUB] de ${archivo} (léelo completo; no te limites a las que declara su autor, ` +
        'abajo solo como guía): existencia, correspondencia, retractación y si respaldan la afirmación (respalda, parcial, no_respalda o no_verificable; ' +
        'sin poder comprobarla, no_verificable). Solo lectura: no modifiques ficheros.\n' +
        `Afirmaciones que declara el autor: ${JSON.stringify((ap.afirmaciones || []).filter((a) => a && a.etiqueta === 'PUB'))}`,
        { label: `fuentes:${q.agente}:${i + 1}`, phase: 'Verificación', agentType: 'verificador-citas', schema: VERIF }),
    ]).then(([critica, verif]) => ({ agente: q.agente, ...ap, archivo, critica, verif }))
  },
)
const aportacionesValidas = aportaciones.filter((a) => a && !a.fallo)
const sinAportacion = aportaciones.map((a, i) => (a && !a.fallo ? null : preguntas[i].agente)).filter(Boolean)
const resumenAportaciones = () => aportaciones.map((a, i) => (a && !a.fallo
  ? { agente: a.agente, archivo: a.archivo, compuerta_propuesta: a.compuerta_propuesta || null,
    critica: a.critica ? a.critica.veredicto : 'sin crítica (fallo de agente)',
    fuentes_problematicas: a.verif ? a.verif.problemas.length : 'sin verificar (fallo de agente)' }
  : { agente: preguntas[i].agente, archivo: null, fallo: 'sin aportación (fallo de agente)' }))

// Una propuesta "bloqueada" de cualquier especialista detiene la evaluación antes de puntuar y del informe.
const bloqueosEspecialistas = aportacionesValidas
  .filter((a) => esObjeto(a.compuerta_propuesta) && a.compuerta_propuesta.estado === 'bloqueada')
  .map((a) => ({ agente: a.agente, compuerta: a.compuerta_propuesta.compuerta, motivo: a.compuerta_propuesta.motivo, archivo: a.archivo }))
if (bloqueosEspecialistas.length) {
  log(`Especialistas: ${bloqueosEspecialistas.map((b) => `${b.compuerta} (${b.agente})`).join(', ')} propuesta(s) "bloqueada"; la evaluación se detiene antes de puntuar`)
  return detener('Especialistas', { bloqueos: bloqueosEspecialistas, descarte_rapido: resumenDescarte, evidencia: resumenEvidencia,
    aportaciones: resumenAportaciones() })
}
const registroPrevio = await leerRegistro('antes-de-puntuar')
const bloqueoPrevio = bloqueoDeRegistro(registroPrevio)
if (bloqueoPrevio) {
  log(`Evaluación detenida antes de puntuar: ${bloqueoPrevio.motivo}`)
  return detener('Especialistas', { motivo: bloqueoPrevio.motivo, bloqueos: registroPrevio.ok ? registroPrevio.bloqueadas : [],
    descarte_rapido: resumenDescarte, evidencia: resumenEvidencia, aportaciones: resumenAportaciones() }, bloqueoPrevio.siguiente)
}

// ================================================================ puntuación ciega (antes de la síntesis y del informe)
phase('Puntuación')
// Los evaluadores leen la evidencia de los especialistas, la crítica y la verificación; nunca un informe, una decisión
// propuesta ni puntuaciones. Emiten su puntuación sin registrarla: se registra solo si el informe obtiene un sello válido.
const archivosEvidencia = [...new Set([
  ...resumenDescarte.map((d) => d.archivo),
  ...(biblio && biblio.archivo ? [biblio.archivo] : []),
  ...aportacionesValidas.map((a) => a.archivo).filter(Boolean),
  ...(metaanalisis && metaanalisis.archivo ? [metaanalisis.archivo] : []),
])]
const criticasYVerificacion = aportacionesValidas.map((a) => ({
  agente: a.agente, archivo: a.archivo,
  critica: a.critica ? { veredicto: a.critica.veredicto, debilidades: a.critica.debilidades || [], experimento_decisivo: a.critica.experimento_decisivo || null } : 'sin crítica (fallo de agente)',
  fuentes_problematicas: a.verif ? a.verif.problemas : 'sin verificar (fallo de agente)',
}))
const promptEvaluador = (ev) =>
  `Eres el evaluador independiente "${ev.id}". Puntúa la oportunidad ${ID} con los 9 criterios de docs/RUBRICA.md siguiendo .claude/skills/puntuacion/SKILL.md, ` +
  'a ciegas: puntúas antes de que se redacten la síntesis, el informe y la decisión propuesta. ' +
  `Lee la oportunidad solo con nutria --agente ${ev.id} oportunidad ver ${ID} --sin-puntuaciones --json (ignora su campo "decisiones": no es evidencia) ` +
  `y solo esta evidencia: ${archivosEvidencia.join(', ')}; las salidas de ${DIR}/ (extracciones, metaanálisis, nutria econ y nutria destino ruminal); ` +
  'y las críticas y la verificación de fuentes de abajo. No leas nada más de research/: ni research/informes/ (ningún informe ni decisión propuesta, tampoco ' +
  'de evaluaciones anteriores), ni research/planes/, ni revisiones de red team (*redteam*), ni rankings, ni puntuaciones o ficheros *_puntuacion_* de otros ' +
  'evaluadores. No ejecutes nutria puntuar ranking ni nutria procedencia. ' +
  `Guarda tu puntuación en ${rutaPuntuacion(ev.id)} con "evaluador": "${ev.id}" y "oportunidad": "${ID}", conforme a src/nutria/data/schemas/puntuacion.schema.json ` +
  '(compruébalo, p. ej. con python3 y jsonschema). NO la registres: se registra después, solo si el informe obtiene un sello válido. ' +
  'Devuelve la ruta, el sha256 del fichero (sha256sum o shasum -a 256) y un resumen breve de los criterios con más incertidumbre.\n' +
  'Críticas y verificación de fuentes de cada aportación: ' + JSON.stringify(criticasYVerificacion)
const emitir = async (ev) => {
  const base = { phase: 'Puntuación', agentType: 'evaluador-independiente', schema: PUNTUACION_EMITIDA }
  const comoEmitida = (r, modelo, reintento) => {
    const sha = normSha(r.sha256)
    return SHA_RE.test(sha)
      ? { evaluador: ev.id, modelo, reintento, emitida: true, archivo: rutaPuntuacion(ev.id), sha256: sha, resumen: r.resumen || null }
      : { evaluador: ev.id, modelo, reintento, emitida: false, archivo: rutaPuntuacion(ev.id), fallo: 'sin sha256 válido del fichero: no se puede comprobar su integridad ni registrar' }
  }
  const r = await lanzar(promptEvaluador(ev), { ...base, label: `puntuacion:${ev.id}`, model: ev.modelo })
  if (r) return comoEmitida(r, ev.modelo, false)
  if (presupuestoBajo()) {
    log(`Reintento de ${ev.id} omitido: quedan menos de ${UMBRAL_TOKENS} tokens de presupuesto`)
    return { evaluador: ev.id, modelo: ev.modelo, reintento: false, emitida: false, fallo: 'sin resultado; reintento omitido por presupuesto' }
  }
  log(`${ev.id} (${ev.modelo}) no devolvió resultado; se reintenta con ${MODELO_REINTENTO}`)
  const r2 = await lanzar(promptEvaluador(ev), { ...base, label: `puntuacion:${ev.id}:reintento`, model: MODELO_REINTENTO })
  if (r2) return comoEmitida(r2, MODELO_REINTENTO, true)
  return { evaluador: ev.id, modelo: MODELO_REINTENTO, reintento: true, emitida: false, fallo: 'sin resultado tras el reintento' }
}
const puntuaciones = (await parallel(EVALUADORES.map((ev) => () => emitir(ev))))
  .map((p, i) => p || { evaluador: EVALUADORES[i].id, modelo: EVALUADORES[i].modelo, reintento: false, emitida: false, fallo: 'error inesperado' })
const emitidas = puntuaciones.filter((p) => p.emitida)
log(`Puntuación ciega: ${emitidas.length}/${EVALUADORES.length} puntuaciones emitidas (sin registrar hasta comprobar el sello del informe)`)

// ================================================================ síntesis e informe (después de puntuar)
phase('Síntesis e informe')
const informe = await lanzar(
  `${REGLAS}\nSintetiza la evaluación de ${ID} siguiendo .claude/skills/informe/SKILL.md: nutria informe nuevo ${ID} (o actualiza ${RUTA_INFORME}; el informe va en esa ruta). ` +
  'Integra el descarte rápido, la evidencia (revisión, doble extracción y metaanálisis si lo hubo) y las aportaciones, conservando los desacuerdos y las críticas; ' +
  'corrige o retira las afirmaciones con fuentes problemáticas. La sección 14 indica el estado de las seis compuertas y cuáles son propuestas sin firmar. ' +
  `La oportunidad ya la han puntuado a ciegas ${emitidas.length} evaluador(es) independiente(s): lee sus puntuaciones en ` +
  `${emitidas.map((p) => p.archivo).join(', ') || '(ninguna emitida)'} sin modificarlas (se registran solo si el informe obtiene un sello válido) y recógelas en la ` +
  'sección 0 con su intervalo y sus desacuerdos, sin promediarlos para ocultarlos, junto con el estado de las compuertas y una decisión propuesta (continuar, ' +
  'reformular o descartar) pendiente de firma humana. No puntúes. Deja el estado en borrador: no rellenes revisado_por_humano ni la tabla del Anexo A ' +
  '(la rellena verificador-citas).' +
  (fallos.length ? `\nAgentes que fallaron (decláralo en Limitaciones): ${fallos.map((f) => f.agente).join(', ')}` : '') +
  `\nDescarte rápido: ${JSON.stringify(resumenDescarte)}\nEvidencia: ${JSON.stringify(resumenEvidencia)}\nAportaciones: ${JSON.stringify(aportacionesValidas)}`,
  { label: `informe:${ID}`, phase: 'Síntesis e informe', agentType: 'director-cientifico', schema: INFORME },
)
if (informe && informe.archivo && informe.archivo !== RUTA_INFORME) log(`El informe se declaró en ${informe.archivo}; se verifica y se sella ${RUTA_INFORME}`)
const verificacionInforme = informe
  ? await lanzar(
    `${REGLAS}\nVerifica el informe ${RUTA_INFORME}. 1) Rellena la tabla de respaldo del Anexo A ("| Ref | Afirmación | Respaldo | Motivo |", Respaldo: respalda | parcial | ` +
    'no_respalda | no_verificable) leyendo cada fuente; cada [Rn] usado en una línea [PUB] necesita una fila respalda o parcial. ' +
    `2) Ejecuta nutria --agente verificador-citas informe comprobar ${RUTA_INFORME} --online --sellar. No registres verificaciones manuales (las referencias con solo URL ` +
    'las registra una persona) y no cambies el estado ni revisado_por_humano. Lista cada problema de respaldo o de verificación; la validez del sello la comprueba ' +
    'después otro agente con la salida literal de nutria.',
    { label: `verificacion-informe:${ID}`, phase: 'Síntesis e informe', agentType: 'verificador-citas', schema: VERIF_INFORME },
  )
  : null

// ================================================================ sello y registro de las puntuaciones
phase('Sello y registro')
const sello = informe ? interpretarSello(await comprobarSello()) : { valido: false, motivo: 'no hay informe (fallo de agente)' }
let resultadoPuntuaciones = puntuaciones.map((p) => ({ ...p, registrado: false,
  motivo: p.emitida ? `sin registrar: el informe no tiene un sello válido (${sello.motivo})` : undefined }))
if (sello.valido && emitidas.length) {
  const hechos = await parallel(emitidas.map((p) => () => registrarPuntuacion(p)))
  resultadoPuntuaciones = puntuaciones.map((p) => {
    if (!p.emitida) return { ...p, registrado: false }
    const h = hechos[emitidas.indexOf(p)]
    return h || { ...p, registrado: false, motivo: 'error inesperado' }
  })
} else if (!sello.valido) {
  log(`Sello no válido (${sello.motivo}): las ${emitidas.length} puntuaciones emitidas no se registran`)
}
const registrados = resultadoPuntuaciones.filter((p) => p.registrado).length
const pendientes = pendientesDe(resultadoPuntuaciones.filter((p) => p.emitida))
const alertas = resultadoPuntuaciones.filter((p) => p.alerta).map((p) => `${p.evaluador}: ${p.alerta}`)
alertas.forEach((a) => log(`Alerta: ${a}`))

return {
  id: ID,
  descarte_rapido: resumenDescarte,
  evidencia: resumenEvidencia,
  aportaciones: resumenAportaciones(),
  sin_aportacion: sinAportacion,
  preguntas_sin_lanzar: sinLanzar.map((q) => q.agente),
  informe: informe
    ? { archivo: RUTA_INFORME, decision_propuesta: informe.decision_propuesta, sello_valido: sello.valido, motivo_sello: sello.motivo,
      problemas_de_citas: verificacionInforme ? verificacionInforme.problemas : 'sin verificación (fallo de agente)' }
    : null,
  evaluadores: resultadoPuntuaciones.map((p) => ({ id: p.evaluador, modelo: p.modelo, reintento: Boolean(p.reintento), emitida: Boolean(p.emitida),
    registrado: Boolean(p.registrado), fallo: p.fallo || p.motivo || null, alerta: p.alerta || null })),
  evaluadores_registrados: registrados,
  puntuaciones_pendientes: pendientes,
  fallos: resumenFallos(),
  siguiente: sello.valido
    ? `nutria puntuar ranking (con menos de 3 evaluadores registrados ${ID} queda INSUFICIENTE). Antes de pasar el informe a revisado, lanza el workflow ` +
      `red-team-auto por su nombre con la herramienta Workflow y los argumentos {"archivo": "${RUTA_INFORME}"}. La decisión sobre ${ID}, las compuertas ` +
      '"superada" y el paso del informe a "revisado" requieren firma o revisión humana.' +
      (pendientes.length ? ` Puntuaciones sin registrar: vuelve a lanzar este workflow con ${JSON.stringify({ id: ID, registrar_puntuaciones: pendientes })}.` : '')
    : siguienteSinSello(sello.motivo, pendientes),
}
