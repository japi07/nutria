export const meta = {
  name: 'red-team-auto',
  description: 'Revisión adversarial de un documento: un agente que no lo escribió (el crítico, o el verificador de citas si el autor es el crítico) extrae las afirmaciones clave, cada una se somete a tres refutadores con enfoques distintos (fuentes, biología, viabilidad), ninguno del rol autor, y los veredictos se agregan con quórum y la regla de fuentes (sin fuentes comprobadas nunca sale sostenida); segunda opinión opcional de otra familia de modelos según la política de datos',
  whenToUse: 'Antes de una compuerta o de dar por bueno un informe o plan. Argumento: la ruta del fichero, p. ej. "research/informes/O-0003.md", o {"archivo": "...", "sensibilidad": "confidencial", "max_afirmaciones": 15, "autor": "<rol>"} (sensibilidad por defecto "confidencial"; "normal" solo para información no confidencial; como máximo 30 afirmaciones; "autor" es el rol que escribió el documento, o "persona": obligatorio si no se deduce de la ruta, como en informes, planes y análisis con el rol en el nombre). El control de flujo es fijo, pero cada agente puede dar resultados distintos en cada ejecución.',
  phases: [
    { title: 'Afirmaciones', detail: 'extracción, con tope, de las afirmaciones que sostienen la conclusión por un agente que no escribió el documento' },
    { title: 'Refutación', detail: '3 enfoques independientes por afirmación y agregación con quórum y regla de fuentes' },
    { title: 'Otra familia', detail: 'segunda opinión opcional con nutria llm (solo modelos que admita la política de datos)' },
    { title: 'Consolidación', detail: 'tabla de veredictos y acciones propuestas, escrita por un agente que no escribió el documento' },
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
// Tope de afirmaciones: un valor no válido detiene el workflow con un mensaje claro (nunca se sustituye en silencio).
const TOPE_AFIRMACIONES = 30
const MAX_AFIRMACIONES = A.max_afirmaciones === undefined || A.max_afirmaciones === null ? 15 : A.max_afirmaciones
if (!Number.isInteger(MAX_AFIRMACIONES) || MAX_AFIRMACIONES < 1 || MAX_AFIRMACIONES > TOPE_AFIRMACIONES) {
  return { error: `"max_afirmaciones" debe ser un entero entre 1 y ${TOPE_AFIRMACIONES} (recibido: ${JSON.stringify(A.max_afirmaciones)})` }
}

// ---------------------------------------------------------------- autor del documento (quien genera no evalúa)
// Ningún paso lo hace el rol que escribió el documento: ni la extracción de afirmaciones, ni la refutación, ni la
// consolidación. El autor se toma de args.autor o se deduce de la ruta; en cada paso se usa el primer rol candidato
// que no sea el autor. Si no se puede saber quién lo escribió, el workflow no empieza (falla en cerrado): podría ser
// uno de los roles que extraen o refutan.
const ROLES = ['director-cientifico', 'investigador-bibliografico', 'extractor-datos', 'fisiologia-bioquimica',
  'compuestos-toxicologia', 'nutricion-formulacion', 'bioinformatico', 'estadistico', 'critico-red-team', 'verificador-citas',
  'regulacion-seguridad', 'desarrollo-industrial', 'economia-mercado-pi', 'sostenibilidad-bienestar', 'evaluador-independiente']
const AUTORES_VALIDOS = [...ROLES, 'persona']
// Análisis que escriben los workflows y rol que los escribe (research/revisiones/<ID>_descarte_<x>.md, <ID>_triage_<x>.md).
const AUTOR_DE_ANALISIS = {
  regulatoria: 'regulacion-seguridad', seguridad: 'compuestos-toxicologia', destino_digestivo: 'fisiologia-bioquimica',
  modelo_ganadero: 'economia-mercado-pi', analitica: 'desarrollo-industrial', propiedad_intelectual: 'economia-mercado-pi',
  etica: 'sostenibilidad-bienestar',
}
const deducirAutor = (archivo) => {
  const ruta = String(archivo).replace(/\\/g, '/')
  const nombre = ruta.split('/').pop()
  if (/(^|\/)(informes|planes)\//.test(ruta)) return { autor: 'director-cientifico', origen: 'ruta (informes y planes los redacta director-cientifico)' }
  if (/redteam|critica/i.test(nombre)) return { autor: 'critico-red-team', origen: 'nombre del fichero (revisión del crítico)' }
  const rol = ROLES.find((r) => nombre.includes(r))
  if (rol) return { autor: rol, origen: 'nombre del fichero' }
  const m = /_(?:descarte|triage)_([a-z_]+?)(?:_\d{4}-\d{2}-\d{2})?\.[A-Za-z0-9]+$/.exec(nombre)
  if (m && AUTOR_DE_ANALISIS[m[1]]) return { autor: AUTOR_DE_ANALISIS[m[1]], origen: 'nombre del fichero (análisis del descarte o del triage)' }
  if (/_etica_plan/.test(nombre)) return { autor: 'sostenibilidad-bienestar', origen: 'nombre del fichero' }
  if (/_busqueda\./.test(nombre)) return { autor: 'investigador-bibliografico', origen: 'nombre del fichero' }
  if (/^M1a?_(mapa_problemas|ranking)/.test(nombre)) return { autor: 'director-cientifico', origen: 'nombre del fichero (mapa o ranking de la misión 1)' }
  return { autor: null, origen: 'no se deduce de la ruta' }
}
let AUTOR = null
let ORIGEN_AUTOR = null
if (A.autor !== undefined && A.autor !== null && A.autor !== '') {
  if (typeof A.autor !== 'string' || !AUTORES_VALIDOS.includes(A.autor.trim())) {
    return { error: `"autor" no válido: ${JSON.stringify(A.autor)}. Opciones: ${AUTORES_VALIDOS.join(', ')}` }
  }
  AUTOR = A.autor.trim()
  ORIGEN_AUTOR = 'argumento "autor"'
} else {
  const d = deducirAutor(ARCHIVO)
  AUTOR = d.autor
  ORIGEN_AUTOR = d.origen
}
if (!AUTOR) {
  return { documento: ARCHIVO, error: `No se puede deducir de la ruta quién escribió "${ARCHIVO}" y quien genera no evalúa: indica el rol que lo ` +
    `escribió con "autor" (p. ej. {"archivo": "${ARCHIVO}", "autor": "fisiologia-bioquimica"}, o "persona" si lo escribió una persona). ` +
    `Opciones: ${AUTORES_VALIDOS.join(', ')}` }
}
const elegir = (candidatos) => candidatos.find((r) => r !== AUTOR)
const ROL_EXTRACCION = elegir(['critico-red-team', 'verificador-citas'])
const ROL_CONSOLIDACION = ROL_EXTRACCION

// Fichero de salida: incluye el directorio de origen, para que revisar research/planes/O-0001.md y
// research/informes/O-0001.md no escriba en el mismo fichero (planes_O-0001_redteam.md frente a informes_O-0001_redteam.md).
const rutaRedTeam = (archivo) => {
  const segs = String(archivo).replace(/\\/g, '/').split('/').filter((s) => s && s !== '.')
  const k = segs.lastIndexOf('research')
  const partes = (k >= 0 ? segs.slice(k + 1) : segs).map((s) => (s === '..' ? 'arriba' : s))
  const nombre = partes.join('_').replace(/\.[A-Za-z0-9]+$/, '').replace(/[^A-Za-z0-9._-]+/g, '_').replace(/^[_.]+|[_.]+$/g, '') || 'documento'
  return `research/revisiones/${nombre}_redteam.md`
}
const DESTINO = rutaRedTeam(ARCHIVO)

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

const AFIRMACIONES = S({ afirmaciones: { type: 'array', maxItems: MAX_AFIRMACIONES, items: S({ id: STR, texto: STR, importancia: STR }, ['id', 'texto']) } }, ['afirmaciones'])
const VEREDICTO = S({ veredicto: { type: 'string', enum: VEREDICTOS }, motivo: STR, fuentes: STRS, accion: STR }, ['veredicto', 'motivo'])
const OTRA_FAMILIA = S({
  ejecutado: { type: 'boolean' },
  modelos_utilizables: STRS,
  respuestas: { type: 'array', items: S({ modelo: STR, texto: STR, omitido: STR, error: STR }, ['modelo']) },
  motivo: STR,
}, ['ejecutado', 'motivo'])

const ENFOQUES = [
  { id: 'fuentes', texto: '¿existen las fuentes, dicen eso y en qué especie, etapa y dosis? Usa nutria verificar --doi/--pmid con --cita y lee los resúmenes. Si no puedes acceder a la fuente o comprobar que dice eso (p. ej. sin red), el veredicto es "no_verificable"', tipo: elegir(['verificador-citas', 'investigador-bibliografico']) },
  { id: 'biologia', texto: '¿es biológicamente sostenible en la especie diana (destino digestivo, concentraciones alcanzables, adaptación de la microbiota ruminal, explicaciones alternativas, diseño de los estudios)?', tipo: elegir(['critico-red-team', 'fisiologia-bioquimica']) },
  { id: 'viabilidad', texto: '¿resiste un examen regulatorio, de seguridad, industrial y económico?', tipo: elegir(['critico-red-team', 'regulacion-seguridad']) },
]

// Agregación con quórum (al menos 2 veredictos válidos; si no, "sin_quorum") y regla de fuentes: el enfoque
// "fuentes" es el único que comprueba las fuentes. Reglas, en este orden:
// 1. refutada si "fuentes" da "refutada" (la fuente no existe, no dice eso o la contradice) o si hay 2 o más "refutada";
// 2. no_verificable con 2 o más "no_verificable";
// 3. debilitada si hay alguna "refutada" o "debilitada";
// 4. sostenida solo si "fuentes" da "sostenida", hay 2 o más "sostenida" y ninguna "refutada";
// 5. en cualquier otro caso, no_verificable.
// Regla de fuentes: si "fuentes" da "no_verificable" o no da resultado, la afirmación nunca sale "sostenida" (como mucho "debilitada" o "no_verificable"), aunque los otros dos enfoques la sostengan.
const QUORUM = 2
const agregar = (validos) => {
  if (validos.length < QUORUM) return 'sin_quorum'
  const n = (v) => validos.filter((x) => x.veredicto === v).length
  const deFuentes = validos.find((x) => x.enfoque === 'fuentes')
  const fuentes = deFuentes ? deFuentes.veredicto : null
  if (fuentes === 'refutada' || n('refutada') >= 2) return 'refutada'
  if (n('no_verificable') >= 2) return 'no_verificable'
  if (n('refutada') >= 1 || n('debilitada') >= 1) return 'debilitada'
  if (fuentes === 'sostenida' && n('sostenida') >= 2) return 'sostenida'
  return 'no_verificable'
}

// ---------------------------------------------------------------- afirmaciones
phase('Afirmaciones')
const MAXIMO = 1 + MAX_AFIRMACIONES * ENFOQUES.length + 2
log(`Estimación: 1 agente de extracción; después 3 por afirmación (como máximo ${MAX_AFIRMACIONES} afirmaciones: hasta ${MAX_AFIRMACIONES * ENFOQUES.length}), ` +
  `1 de segunda opinión opcional y 1 de consolidación: como máximo ${MAXIMO} agentes` +
  (typeof budget !== 'undefined' && budget && budget.total ? `. Presupuesto restante: ${Math.round(budget.remaining())} tokens` : ''))
log(`Autor del documento: ${AUTOR || 'desconocido'} (${ORIGEN_AUTOR}); extrae y consolida ${ROL_EXTRACCION}; refutan ` +
  ENFOQUES.map((e) => `${e.id}=${e.tipo}`).join(', '))
const extraidas = await lanzar(
  `Lee ${ARCHIVO} e identifica las afirmaciones que sostienen su conclusión (las que, si fueran falsas, cambiarían la decisión): entre ${Math.min(5, MAX_AFIRMACIONES)} y ${MAX_AFIRMACIONES}, ` +
  'ordenadas de la más decisiva a la menos. Cópialas literalmente con un id A1..An. No escribiste este documento: no lo corrijas ni lo defiendas, ' +
  'y no te limites a las afirmaciones que el propio documento destaque. Solo lectura: no modifiques ningún fichero.',
  { label: 'extraccion', phase: 'Afirmaciones', agentType: ROL_EXTRACCION, schema: AFIRMACIONES },
)
const todas = []
const idsVistos = new Set()
for (const af of (extraidas && Array.isArray(extraidas.afirmaciones) ? extraidas.afirmaciones : [])) {
  if (!af || !af.texto) continue
  let id = typeof af.id === 'string' && /^[A-Za-z0-9_.-]{1,20}$/.test(af.id) ? af.id : ''
  if (!id || idsVistos.has(id)) id = `A${todas.length + 1}`
  while (idsVistos.has(id)) id = `${id}b`
  idsVistos.add(id)
  todas.push({ ...af, id })
}
if (!todas.length) return { documento: ARCHIVO, error: 'No se extrajeron afirmaciones', autor: AUTOR, fallos: resumenFallos() }
const afirmaciones = todas.slice(0, MAX_AFIRMACIONES)
const noRevisadas = todas.slice(MAX_AFIRMACIONES)
if (noRevisadas.length) {
  log(`Límite de ${MAX_AFIRMACIONES} afirmaciones: quedan sin revisar ${noRevisadas.length} (${noRevisadas.map((af) => af.id).join(', ')}); súbelo con args.max_afirmaciones (máximo ${TOPE_AFIRMACIONES})`)
}
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
    'Si no puedes comprobarla, nunca "sostenida". No escribiste este documento. Solo lectura: no modifiques el documento ni otros ficheros.\n' +
    `Afirmación ${af.id}: ${af.texto}`,
    { label: `${af.id}:${e.id}`, phase: 'Refutación', agentType: e.tipo, schema: VEREDICTO },
  ))).then((vs) => {
    const validos = vs.map((v, i) => (v && VEREDICTOS.includes(v.veredicto) ? { enfoque: ENFOQUES[i].id, ...v } : null)).filter(Boolean)
    const votos = Object.fromEntries(VEREDICTOS.map((k) => [k, validos.filter((v) => v.veredicto === k).length]))
    const deFuentes = validos.find((v) => v.enfoque === 'fuentes')
    return { ...af, veredictos: validos, votos, validos: validos.length, fuentes: deFuentes ? deFuentes.veredicto : 'sin_resultado', resultado: agregar(validos) }
  }),
)
const resultados = juzgadas.map((j, i) => j || { ...afirmaciones[i], veredictos: [], votos: {}, validos: 0, fuentes: 'sin_resultado', resultado: 'sin_quorum' })
const segundaOpinion = await otraFamilia

// ---------------------------------------------------------------- consolidación
// La consolida un agente que no escribió el documento: no cambia los resultados agregados y solo propone acciones.
phase('Consolidación')
const sinQuorum = resultados.filter((j) => j.resultado === 'sin_quorum').map((j) => j.id)
const consolidado = await lanzar(
  `Escribe ${DESTINO} (crea solo ese fichero; no modifiques ${ARCHIVO} ni ningún otro) con una tabla "afirmación | resultado | motivos por enfoque | acción propuesta" ` +
  'y un resumen de las tres debilidades más graves. No escribiste el documento revisado. Los resultados ya están agregados con quórum y con la regla de fuentes ' +
  '(si el enfoque "fuentes" no pudo comprobarlas, nunca "sostenida"): no los cambies. Las afirmaciones "sin_quorum" no tienen veredicto (menos de 2 veredictos ' +
  `válidos): dilo y propone repetir su revisión.${noRevisadas.length ? ` Las afirmaciones ${noRevisadas.map((af) => af.id).join(', ')} quedaron sin revisar por el límite de ${MAX_AFIRMACIONES}: dilo.` : ''} ` +
  'Integra la segunda opinión de otra familia de modelos, si la hubo, como opinión de un modelo (no como evidencia): dónde coincide y dónde discrepa de los veredictos. ' +
  'Incluye la bibliografía de las referencias [Rn] que copies del documento. Registra como propuestas las acciones que cambien estados con ' +
  `nutria --agente ${ROL_CONSOLIDACION} decision registrar --entidad <ID de la oportunidad o hipótesis, si consta> --decision "..." --motivo "..." (sin --firma-humana: ` +
  'las decide una persona).' +
  (fallos.length ? `\nAgentes que fallaron (decláralo en el documento): ${fallos.map((f) => f.agente).join(', ')}` : '') +
  `\nResultados: ${JSON.stringify(resultados)}\nSegunda opinión de otra familia: ${JSON.stringify(segundaOpinion)}`,
  { label: 'consolidacion', phase: 'Consolidación', agentType: ROL_CONSOLIDACION },
)

return {
  documento: ARCHIVO,
  sensibilidad: SENSIBILIDAD,
  autor: { rol: AUTOR, origen: ORIGEN_AUTOR },
  roles: { extraccion: ROL_EXTRACCION, consolidacion: ROL_CONSOLIDACION, refutacion: Object.fromEntries(ENFOQUES.map((e) => [e.id, e.tipo])) },
  resultados: resultados.map((j) => ({ id: j.id, resultado: j.resultado, fuentes: j.fuentes, validos: j.validos, votos: j.votos })),
  refutadas: resultados.filter((j) => j.resultado === 'refutada').length,
  sin_quorum: sinQuorum,
  no_revisadas: noRevisadas.map((af) => af.id),
  otra_familia: { ejecutado: Boolean(segundaOpinion && segundaOpinion.ejecutado), motivo: segundaOpinion ? segundaOpinion.motivo : null,
    modelos: segundaOpinion && segundaOpinion.modelos_utilizables ? segundaOpinion.modelos_utilizables : [] },
  informe: DESTINO,
  nota: consolidado ? 'Consolidado escrito' : 'No se pudo escribir el consolidado',
  fallos: resumenFallos(),
}
