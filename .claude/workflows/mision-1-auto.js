export const meta = {
  name: 'mision-1-auto',
  description: 'Misión 1 multiagente por fases con revisión humana entre ellas, cada una precedida del paso 0 (nutria doctor y nutria estado): mapa de problemas (fase "mapa"); oportunidades con novedad comprobada en literatura, productos y patentes, deduplicadas contra el registro y con tope, seis compuertas propuestas sin firma, crítica, puntuación ciega por 3 evaluadores independientes de las no bloqueadas y ranking (fase "oportunidades"); planes con la lista de validación, crítica con la misma lista, compuerta ética repropuesta sobre el diseño y citas verificadas de las oportunidades que elija una persona (fase "planes")',
  whenToUse: 'Para ejecutar la primera misión exploratoria orquestada, una fase por invocación: {"fase": "mapa", "foco": "..."} (por defecto); después {"fase": "oportunidades", "problemas": [problemas del mapa aprobados por una persona; como máximo 8, o "max_problemas" hasta 12], "n_oportunidades": 12 (objetivo y tope, como máximo 24)}; después {"fase": "planes", "seleccion": ["O-0003", ...]} con los IDs que elija una persona. Opcional: "fecha": "AAAA-MM-DD" (nombres de fichero; si falta, la del sistema). El control de flujo es fijo, pero cada agente puede dar resultados distintos en cada ejecución. Consume muchos tokens; la versión paso a paso es /mision-1.',
  phases: [
    { title: 'Preparación', detail: 'paso 0: nutria doctor, nutria estado y fecha (salida literal)' },
    { title: 'Mapa de problemas', detail: '4 enfoques independientes, consolidación y verificación de citas' },
    { title: 'Oportunidades', detail: 'generación con las soluciones actuales, novedad en literatura, productos y patentes, deduplicación contra el registro, tope y registro' },
    { title: 'Triage y crítica', detail: 'seis compuertas propuestas sin firma, modelo del ganadero, red team y compuertas efectivas del registro' },
    { title: 'Puntuación', detail: '3 evaluadores independientes y ciegos con modelos distintos; las oportunidades bloqueadas no se puntúan' },
    { title: 'Ranking', detail: 'ranking Monte Carlo interpretado y verificado' },
    { title: 'Planes', detail: 'plan, crítica con la lista de validación, revisión, compuerta ética repropuesta sobre el diseño y verificación de citas' },
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
  n_oportunidades: opcionEntera('n_oportunidades', 1, 24, 12),
  max_problemas: opcionEntera('max_problemas', 1, 12, 8),
}
const erroresOpciones = Object.values(OPCIONES).filter((o) => o.error).map((o) => o.error)
if (A.fecha !== undefined && A.fecha !== null && !(typeof A.fecha === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(A.fecha))) {
  erroresOpciones.push(`"fecha" debe tener el formato AAAA-MM-DD (recibido: ${JSON.stringify(A.fecha)})`)
}
if (erroresOpciones.length) return { fase: FASE, error: erroresOpciones.join('; ') }
const FOCO = A.foco || 'nutrición y producción animal con prioridad en rumiantes, especialmente vacuno lechero, sin excluir otras especies'
// n_oportunidades es a la vez objetivo y tope: nunca se registran más.
const N_OPS = OPCIONES.n_oportunidades.valor
const MAX_PROBLEMAS = OPCIONES.max_problemas.valor
const MAX_POR_GENERADOR = 3
const MAX_RONDAS_EXTRA = 2
// Mínimo del encargo: al menos 10 oportunidades, al menos 6 en rumiantes (la misma proporción si el tope es menor).
const MINIMO_MISION = { oportunidades: 10, rumiantes: 6 }
const OBJ_RUM = N_OPS >= MINIMO_MISION.oportunidades ? MINIMO_MISION.rumiantes : Math.ceil(N_OPS * 0.6)
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
const textoPresupuesto = () => (typeof budget !== 'undefined' && budget && budget.total ? `. Presupuesto restante: ${Math.round(budget.remaining())} tokens` : '')

// ---------------------------------------------------------------- esquemas
const S = (props, required) => ({ type: 'object', properties: props, required })
const STR = { type: 'string' }
const STRS = { type: 'array', items: { type: 'string' } }
const NUM = { type: 'number' }
const INT = { type: 'integer' }
const BOOL = { type: 'boolean' }
const COMPUERTAS = ['seguridad', 'regulatoria', 'analitica', 'propiedad_intelectual', 'etica', 'destino_digestivo']
const ESTADOS_COMPUERTA = ['superada', 'no_resuelta', 'bloqueada']
const ESTADOS_DETENIDOS = ['bloqueada', 'descartada']
const VEREDICTOS_NOVEDAD = ['nueva', 'diferenciada', 'existente']

// Salida literal de una orden: el workflow la interpreta él mismo en vez de fiarse de lo que declare el agente.
const SALIDA_ORDEN = S({ orden: STR, codigo_salida: INT, salida: STR }, ['orden', 'codigo_salida', 'salida'])
const PASO0 = S({ doctor: SALIDA_ORDEN, estado: SALIDA_ORDEN, fecha: SALIDA_ORDEN, ficheros: SALIDA_ORDEN }, ['doctor', 'estado', 'fecha', 'ficheros'])

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

// El generador define cada hipótesis completa (efecto mínimo relevante y criterio de falsación incluidos): nadie la completa después.
const HIPOTESIS = S({ enunciado: STR, efecto_minimo_relevante: STR, criterio_falsacion: STR, variables: STRS },
  ['enunciado', 'efecto_minimo_relevante', 'criterio_falsacion'])
const OPORTUNIDADES = S({ oportunidades: { type: 'array', maxItems: MAX_POR_GENERADOR, items: S({
  titulo: STR, problema: STR, especie: STR, etapa: STR, mecanismo: STR, candidatos: STRS, tipo_producto: STR,
  evidencia_resumen: STR, diferenciacion: STR, lagunas: STRS, hipotesis: { type: 'array', minItems: 1, items: HIPOTESIS }, tecnica: STR,
}, ['titulo', 'problema', 'especie', 'etapa', 'mecanismo', 'candidatos', 'tipo_producto', 'evidencia_resumen', 'diferenciacion', 'lagunas', 'hipotesis']) } },
['oportunidades'])

// Novedad: la literatura la comprueba investigador-bibliografico; los productos y las patentes, economia-mercado-pi.
const NOVEDAD_LITERATURA = S({
  veredicto: { type: 'string', enum: VEREDICTOS_NOVEDAD }, literatura: STR, consultas: STRS, reformulacion: STR,
}, ['veredicto', 'literatura'])
const NOVEDAD_MERCADO = S({
  veredicto: { type: 'string', enum: VEREDICTOS_NOVEDAD }, productos: STR, patentes: STR, reformulacion: STR,
}, ['veredicto', 'productos', 'patentes'])

const REGISTRO = S({ id: { type: 'string', pattern: '^O-\\d{4}$' }, archivo: STR, ya_registrada: BOOL, hipotesis_ids: STRS }, ['id'])
const COMPUERTA = S({
  compuerta: STR, estado: { type: 'string', enum: ESTADOS_COMPUERTA }, motivo: STR, resumen: STR,
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
// La crítica del plan responde punto por punto a la lista de validación (LISTA_VALIDACION, más abajo).
const CRITICA_PLAN = S({
  veredicto: { type: 'string', enum: ['sostenida', 'debilitada', 'refutada', 'no_verificable'] }, debilidades: STRS, experimento_decisivo: STR,
  lista_validacion: { type: 'array', items: S({ punto: INT, estado: { type: 'string', enum: ['cubre', 'cubre_mal', 'omite'] }, motivo: STR }, ['punto', 'estado', 'motivo']) },
}, ['veredicto', 'debilidades', 'experimento_decisivo', 'lista_validacion'])
const VERIFICACION = S({
  archivo: STR, orden: STR, codigo_salida: INT, salida: STR,
  problemas: { type: 'array', items: S({ ref: STR, estado: STR, motivo: STR }, ['ref', 'estado', 'motivo']) },
  resumen: STR,
}, ['archivo', 'codigo_salida', 'salida', 'problemas'])

// ---------------------------------------------------------------- equipo
const LENTES = [
  { agente: 'fisiologia-bioquimica', foco: 'problemas fisiológicos y metabólicos sin solución nutricional suficientemente eficaz' },
  { agente: 'nutricion-formulacion', foco: 'eficiencia alimentaria, biodisponibilidad de nutrientes, interacciones entre ingredientes y metabolismo lipídico y proteico' },
  { agente: 'sostenibilidad-bienestar', foco: 'emisiones, eficiencia del nitrógeno, longevidad productiva, bienestar y alternativas a antimicrobianos y al óxido de zinc terapéutico' },
  { agente: 'economia-mercado-pi', foco: 'problemas con mayor coste para el ganadero y huecos de mercado que los productos actuales no cubren' },
]
const GENERADORES = ['fisiologia-bioquimica', 'compuestos-toxicologia', 'nutricion-formulacion']
// Cada especialista PROPONE la compuerta de la que es propietario (sin firma humana): solo una persona la firma.
const TRIAGE = [
  { agente: 'regulacion-seguridad', compuerta: 'regulatoria', tarea: 'clasificación y vía regulatoria con la skill triage-regulatorio (primero pienso dietético, después medicamento por alegación y por función, sustancias prohibidas o restringidas, incluidos los moduladores ruminales antimicrobianos, y alegaciones)' },
  { agente: 'compuestos-toxicologia', compuerta: 'seguridad', tarea: 'seguridad para la especie diana, el consumidor, el usuario y el medio ambiente a la dosis prevista' },
  { agente: 'fisiologia-bioquimica', compuerta: 'destino_digestivo', tarea: 'destino digestivo a la dosis prevista; en rumiantes, calcula con nutria destino ruminal (entradas con fuente o [SUP]) y ten en cuenta la adaptación de la microbiota ruminal' },
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
// Lista de validación de los planes: la misma para quien redacta el plan y para quien lo critica.
const LISTA_VALIDACION = [
  'líquido ruminal de donantes con procedimiento autorizado o de matadero (indica cuál y por qué)',
  'Directiva 2010/63/UE y, en España, Real Decreto 53/2013, con aprobación del comité de ética y las 3R',
  'la técnica in situ es in vivo y necesita las mismas autorizaciones',
  'diseño conforme a la guía de eficacia FEEDAP vigente, EFSA J 2024;22(7):e8856 (aplicable desde el 1-12-2024; comprueba que sigue vigente)',
  'BPL en los estudios de seguridad del dossier que las requieran',
  'potencia con varianzas reales (de un metaanálisis o de estudios comparables, con fuente) y la unidad experimental correcta',
  'registro previo del protocolo si no compromete la confidencialidad (la decisión es humana)',
  'notificación a EFSA de los estudios encargados para un dossier, Reglamento (UE) 2019/1381, art. 32b, y asesoramiento previo (art. 32a)',
  'duración de los ensayos de fermentación ruminal: las incubaciones cortas en lote no capturan la adaptación de la microbiota (cultivo continuo, RUSITEC o in vivo)',
]
const textoLista = LISTA_VALIDACION.map((x, i) => `${i + 1}) ${x}`).join('; ')

// ---------------------------------------------------------------- utilidades
const parsear = (texto) => {
  try { return { ok: true, valor: JSON.parse(String(texto)) } } catch (e) { return { ok: false, valor: null } }
}
const esObjeto = (x) => Boolean(x) && typeof x === 'object' && !Array.isArray(x)
const recorte = (t) => String(t === undefined || t === null ? '' : t).slice(0, 300)
const norm = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  .replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim()
// Título normalizado para nombres de fichero: palabras unidas por guiones, cortado en un límite de palabra (48 caracteres).
const slugTitulo = (t) => {
  let slug = ''
  for (const palabra of norm(t).split(' ').filter(Boolean)) {
    const siguiente = slug ? `${slug}-${palabra}` : palabra
    if (siguiente.length > 48) {
      if (!slug) slug = palabra.slice(0, 48)
      break
    }
    slug = siguiente
  }
  return slug || 'oportunidad'
}
// Especies de rumiantes (en el campo especie, ya normalizado: sin tildes ni signos).
const RUMIANTES_RE = /\b(vacun\w*|bovin\w*|vacas?|novill\w*|terner\w*|bueye?s?|toros?|ovin\w*|ovejas?|corder\w*|caprin\w*|cabras?|cabrit\w*|rumiant\w*|bufal\w*|cattle|cows?|heifers?|calf|calves|steers?|bulls?|sheep|ewes?|lambs?|goats?|kids?|ruminants?|buffalo\w*|dairy|bos|ovis|capra|bubalus)\b/
const esRumiante = (especie) => RUMIANTES_RE.test(norm(especie))
const contarRumiantes = (lista) => lista.filter((o) => esRumiante(o.especie)).length

// Paso 0 de la skill: nutria doctor (fuentes accesibles), nutria estado (oportunidades ya registradas), la fecha y los
// ficheros que ya existen (para elegir nombres que no pisen los de ejecuciones anteriores).
const DIRECTORIOS = ['research/revisiones', 'research/oportunidades', 'research/hipotesis', 'research/planes']
const prepararPaso0 = async () => {
  phase('Preparación')
  const r = await lanzar(
    'Paso 0 de la misión 1 (preparación de .claude/skills/mision-1/SKILL.md). Ejecuta exactamente estas cuatro órdenes, en este orden, y ninguna otra: ' +
    `1) nutria --agente orquestador --json doctor; 2) nutria --agente orquestador --json estado; 3) date -u +%F; 4) ls -1 ${DIRECTORIOS.join(' ')}. ` +
    'Devuelve para cada una la orden, su código de salida y su salida estándar literal y completa (sin resumir, corregir ni reformatear). No modifiques ningún fichero.',
    { label: 'paso0', phase: 'Preparación', schema: PASO0, effort: 'low' },
  )
  const p0 = { ok: false, motivo: null, fuentes_no_accesibles: [], avisos: [], registradas: [], fecha: null, ficheros: null }
  if (!r) {
    p0.motivo = 'el agente del paso 0 no devolvió resultado (fallo de agente)'
    return p0
  }
  const d = esObjeto(r.doctor) ? parsear(r.doctor.salida) : { ok: false }
  if (d.ok && esObjeto(d.valor) && esObjeto(d.valor.red)) {
    p0.fuentes_no_accesibles = Object.keys(d.valor.red).filter((k) => !String(d.valor.red[k]).startsWith('OK')).map((k) => `${k}: ${d.valor.red[k]}`)
    if (esObjeto(d.valor.claves) && d.valor.claves.OPENALEX_API_KEY === false) p0.avisos.push('falta OPENALEX_API_KEY: OpenAlex no es accesible (la exige desde el 13-02-2026)')
  } else {
    p0.avisos.push('no se pudo interpretar la salida de nutria doctor: se desconoce qué fuentes son accesibles')
  }
  const e = esObjeto(r.estado) && r.estado.codigo_salida === 0 ? parsear(r.estado.salida) : { ok: false }
  if (e.ok && esObjeto(e.valor) && Array.isArray(e.valor.lista_oportunidades)) {
    p0.registradas = e.valor.lista_oportunidades.filter(esObjeto).map((o) => ({ id: o.id, titulo: o.title || '', estado: o.status || null }))
    p0.ok = true
  } else {
    p0.motivo = `no se pudo interpretar la salida de nutria estado (código ${esObjeto(r.estado) ? r.estado.codigo_salida : '?'})`
  }
  const f = esObjeto(r.fecha) ? String(r.fecha.salida || '').trim() : ''
  if (/^\d{4}-\d{2}-\d{2}$/.test(f)) p0.fecha = f
  // ls -1 de varios directorios: cabecera "dir:" y un nombre por línea (un directorio que no exista solo da error en
  // stderr). Sin cabeceras reconocibles, cada nombre cuenta como ocupado en todos los directorios (falla en cerrado).
  if (esObjeto(r.ficheros) && typeof r.ficheros.salida === 'string') {
    p0.ficheros = new Map(DIRECTORIOS.map((dir) => [dir, new Set()]))
    const sueltos = []
    let actual = null
    let cabeceras = 0
    for (const linea of r.ficheros.salida.split('\n').map((l) => l.trim()).filter(Boolean)) {
      if (linea.endsWith(':')) {
        const dir = linea.slice(0, -1).replace(/^\.\//, '').replace(/\/+$/, '')
        actual = p0.ficheros.has(dir) ? dir : null
        cabeceras += 1
        continue
      }
      const nombre = linea.split('/').pop()
      if (actual) p0.ficheros.get(actual).add(nombre)
      else sueltos.push(nombre)
    }
    if (!cabeceras) sueltos.forEach((nombre) => DIRECTORIOS.forEach((dir) => p0.ficheros.get(dir).add(nombre)))
  } else {
    p0.avisos.push('no se pudo leer qué ficheros existen: los nombres se eligen con la fecha y el título, y los agentes no sobrescriben ficheros existentes')
  }
  if (p0.fuentes_no_accesibles.length) log(`Paso 0: fuentes no accesibles (${p0.fuentes_no_accesibles.join('; ')}); se continúa con las demás y la verificación de citas quedará incompleta`)
  p0.avisos.forEach((a) => log(`Paso 0: ${a}`))
  if (!p0.ok) log(`Paso 0: ${p0.motivo}`)
  return p0
}
const resumenPaso0 = (p0) => ({ registro_leido: p0.ok, oportunidades_registradas: p0.registradas.length,
  fuentes_no_accesibles: p0.fuentes_no_accesibles, avisos: p0.avisos, motivo: p0.motivo })
const fechaDe = (p0) => {
  if (typeof A.fecha === 'string') return A.fecha
  if (p0.fecha) return p0.fecha
  log('Sin fecha (ni args.fecha ni la del sistema): los ficheros llevan "sin-fecha" en el nombre')
  return 'sin-fecha'
}
// Nombres de fichero que no pisan los existentes (según el paso 0) ni los ya asignados en esta ejecución:
// <base><ext>, y si existe, <base>-2<ext>, <base>-3<ext>... Cada destino es [directorio, sufijo] y todos deben estar
// libres (p. ej. la oportunidad research/oportunidades/<base>.json y su primera hipótesis research/hipotesis/<base>_H1.json).
const asignados = new Set()
const nombreLibre = (p0, base, destinos) => {
  const ocupado = ([dir, sufijo], candidato) => {
    const nombre = `${candidato}${sufijo}`
    return asignados.has(`${dir}/${nombre}`) || Boolean(p0.ficheros && p0.ficheros.get(dir) && p0.ficheros.get(dir).has(nombre))
  }
  let candidato = base
  for (let n = 2; destinos.some((d) => ocupado(d, candidato)); n += 1) candidato = `${base}-${n}`
  destinos.forEach(([dir, sufijo]) => asignados.add(`${dir}/${candidato}${sufijo}`))
  return candidato
}
const REVISIONES = 'research/revisiones'

// Compuertas efectivas de una oportunidad, leídas de la salida literal de nutria oportunidad ver (falla en cerrado).
const leerRegistro = async (id, etiqueta, fase) => {
  const r = await lanzar(
    `Lectura del registro de ${id}. Ejecuta exactamente esta orden y ninguna otra: nutria --agente orquestador --json oportunidad ver ${id} --sin-puntuaciones\n` +
    'Devuelve la orden ejecutada, su código de salida y su salida estándar literal y completa (sin resumir, corregir ni reformatear). No modifiques ningún fichero ni el registro.',
    { label: `compuertas-registro:${etiqueta}`, phase: fase, schema: SALIDA_ORDEN, effort: 'low' },
  )
  if (!r) return { ok: false, motivo: 'el agente no devolvió la salida del registro (fallo de agente)' }
  if (r.codigo_salida !== 0) return { ok: false, motivo: `la orden terminó con código ${r.codigo_salida}: ${recorte(r.salida)}` }
  const p = parsear(r.salida)
  if (!p.ok || !esObjeto(p.valor)) return { ok: false, motivo: 'la salida no es el JSON de nutria oportunidad ver' }
  const o = p.valor
  if (o.id !== id) return { ok: false, motivo: `la salida corresponde a ${o.id || 'otra entidad'}, no a ${id}` }
  const ef = o.compuertas_efectivas
  if (!esObjeto(ef) || COMPUERTAS.some((g) => !(g in ef)) || Object.keys(ef).some((g) => !ESTADOS_COMPUERTA.includes(ef[g]))) {
    return { ok: false, motivo: 'compuertas_efectivas ausente, incompleta o con estados no válidos' }
  }
  const filas = Array.isArray(o.compuertas) ? o.compuertas.filter(esObjeto) : []
  const bloqueadas = Object.keys(ef).filter((g) => ef[g] === 'bloqueada').map((g) => {
    const fila = filas.find((f) => f.gate === g)
    return { compuerta: g, motivo: fila && fila.rationale ? fila.rationale : (o.regulatory_route ? `ruta regulatoria ${o.regulatory_route}` : 'sin motivo registrado'),
      propuesta_por: fila ? fila.decided_by || null : null }
  })
  return { ok: true, estado: o.status || null, efectivas: ef, bloqueadas }
}

// Verificación de citas: el workflow decide si es completa con la salida literal de nutria verificar.
const verificarCitas = (archivo, etiqueta, fase) => lanzar(
  `${REGLAS}\nVerifica las referencias del entregable ${archivo} antes de que se presente. 1) Ejecuta exactamente nutria --agente verificador-citas --json verificar ${archivo} ` +
  '(existencia, correspondencia y retractación, en línea) y devuelve la orden, su código de salida y su salida estándar literal y completa (sin resumir ni corregir). ' +
  '2) Comprueba que cada afirmación [PUB] tiene una fuente que la respalda (respalda, parcial, no_respalda o no_verificable) y lista cada problema. No reescribas el ' +
  'documento ni registres verificaciones manuales (las referencias con solo URL las registra una persona). Si la verificación es completa lo decide el workflow ' +
  'con la salida literal de nutria.',
  { label: `verificacion:${etiqueta}`, phase: fase, agentType: 'verificador-citas', schema: VERIFICACION },
).then((v) => {
  if (!v) return { archivo, completa: false, motivo: 'sin verificación (fallo de agente)', problemas: [] }
  const p = parsear(v.salida)
  const res = p.ok && esObjeto(p.valor) && esObjeto(p.valor.resumen) ? p.valor.resumen : null
  const errores = p.ok && esObjeto(p.valor) && Array.isArray(p.valor.incidencias)
    ? p.valor.incidencias.filter((i) => esObjeto(i) && i.severity === 'error').length : null
  const completa = Boolean(res) && res.completa === true && errores === 0 && v.codigo_salida === 0
  let motivo = null
  if (!res) motivo = 'la salida de nutria verificar no es interpretable'
  else if (!completa) motivo = `verificación incompleta o con errores (código ${v.codigo_salida}; ${res.pendientes} pendiente(s), ${res.con_error} con error, ${errores} error(es))`
  return { archivo: v.archivo || archivo, completa, motivo, codigo_salida: v.codigo_salida, referencias: res ? res.total : null,
    verificadas: res ? res.verificadas : null, problemas: Array.isArray(v.problemas) ? v.problemas : [], resumen: v.resumen || null }
})

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

// ================================================================ fase "mapa" (M1a)
if (FASE === 'mapa') {
  log(`Fase "mapa". Estimación: ${1 + LENTES.length + 2} agentes (paso 0, ${LENTES.length} enfoques, consolidación y verificación de citas)${textoPresupuesto()}`)
  const p0 = await prepararPaso0()
  const FECHA = fechaDe(p0)
  phase('Mapa de problemas')
  // Barrera justificada: la consolidación necesita todos los mapas a la vez.
  const mapas = (await parallel(LENTES.map((l) => () => lanzar(
    `${REGLAS}\nMisión 1, paso M1a. Ámbito: ${FOCO}. Tu enfoque: ${l.foco}.\n` +
    'Identifica 6-10 problemas productivos o fisiológicos. Para cada uno: especie y etapa; carga (incidencia o coste CON fuente verificada, o [DESC]); ' +
    'soluciones actuales y su eficacia documentada (con fuentes, o [DESC]); brecha; mecanismos implicados; fuentes (IDs doi:/pmid: obtenidos con nutria buscar). Trabaja de forma independiente.' +
    (p0.fuentes_no_accesibles.length ? `\nFuentes no accesibles según nutria doctor: ${p0.fuentes_no_accesibles.join('; ')}.` : ''),
    { label: `M1a:${l.agente}`, phase: 'Mapa de problemas', agentType: l.agente, schema: PROBLEMAS },
  ).then((r) => (r ? { enfoque: l.agente, problemas: r.problemas } : null))))).filter(Boolean)
  log(`Mapas recibidos: ${mapas.length}/${LENTES.length}`)
  if (!mapas.length) return { fase: FASE, error: 'Ningún enfoque devolvió problemas', preparacion: resumenPaso0(p0), fallos: resumenFallos() }

  const faltan = LENTES.filter((l) => !mapas.some((m) => m.enfoque === l.agente)).map((l) => l.agente)
  const rutaMapa = `${REVISIONES}/${nombreLibre(p0, `M1a_mapa_problemas_${FECHA}`, [[REVISIONES, '.md']])}.md`
  const mapa = await lanzar(
    `${REGLAS}\nConsolida estos mapas de problemas de especialistas independientes. Une duplicados, prioriza por carga × necesidad no cubierta con justificación ` +
    'y conserva los desacuerdos. Conserva para cada problema sus soluciones actuales y su eficacia documentada, con fuentes: los generadores de oportunidades las necesitan. ' +
    `Escribe ${rutaMapa} (es un fichero nuevo: si ya existiera, no lo sobrescribas; añade -2, -3... al nombre y devuelve la ruta real). Devuelve 6-8 problemas prioritarios con ids P1..Pn.` +
    (faltan.length ? `\nFaltan los enfoques ${faltan.join(', ')} por fallo de agente: dilo en el mapa como limitación.` : '') +
    '\n' + JSON.stringify(mapas),
    { label: 'M1a:consolidacion', phase: 'Mapa de problemas', agentType: 'director-cientifico', schema: CONSOLIDADO },
  )
  if (!mapa || !Array.isArray(mapa.prioritarios) || mapa.prioritarios.length === 0) {
    return { fase: FASE, error: 'No se pudo consolidar el mapa de problemas', mapas: mapas.length, preparacion: resumenPaso0(p0), fallos: resumenFallos() }
  }
  const archivoMapa = mapa.archivo || rutaMapa
  const verificacion = await verificarCitas(archivoMapa, 'M1a', 'Mapa de problemas')

  return {
    fase: FASE,
    fecha: FECHA,
    foco: FOCO,
    preparacion: resumenPaso0(p0),
    mapa: archivoMapa,
    enfoques_recibidos: `${mapas.length}/${LENTES.length}`,
    problemas: mapa.prioritarios,
    desacuerdos: mapa.desacuerdos || [],
    verificacion_citas: verificacion,
    fallos: resumenFallos(),
    siguiente: 'Punto de control humano: una persona revisa el mapa y su verificación de citas, aprueba la lista de problemas (puede editarla; como máximo ' +
      `${MAX_PROBLEMAS} o "max_problemas" hasta 12) y relanza este workflow por su nombre con la herramienta Workflow y ` +
      '{"fase": "oportunidades", "problemas": [<problemas aprobados, con sus soluciones_actuales>]}.',
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
  if (PROBLEMAS_APROBADOS.length > MAX_PROBLEMAS) {
    return {
      fase: FASE,
      error: `Se aprobaron ${PROBLEMAS_APROBADOS.length} problemas y el tope es ${MAX_PROBLEMAS}: una persona elige cuáles quedan ` +
        '(o sube "max_problemas", como máximo 12). No se recorta la lista en silencio.',
    }
  }
  const P = PROBLEMAS_APROBADOS.length
  const porOportunidad = 1 + TRIAGE.length + 1 + 1 + EVALUADORES.length
  const maximo = 1 + (1 + MAX_RONDAS_EXTRA) * P * (1 + 2 * MAX_POR_GENERADOR) + N_OPS * (porOportunidad + EVALUADORES.length) + 2
  log(`Fase "oportunidades": ${P} problemas aprobados (tope ${MAX_PROBLEMAS}), objetivo y tope de ${N_OPS} oportunidades (al menos ${OBJ_RUM} en rumiantes). ` +
    `Estimación: unos ${1 + P * (1 + 2 * 2.5) + N_OPS * porOportunidad + 2} agentes y como máximo ${maximo} agentes: paso 0; por problema y ronda, 1 generador y 2 comprobaciones ` +
    `de novedad (literatura; productos y patentes) por cada una de sus hasta ${MAX_POR_GENERADOR} oportunidades (hasta ${MAX_RONDAS_EXTRA} rondas extra); por oportunidad ` +
    `seleccionada (como máximo ${N_OPS}), registro, ${TRIAGE.length} propuestas de compuerta, crítica, lectura del registro y ${EVALUADORES.length} evaluadores ` +
    `(${porOportunidad - EVALUADORES.length} si queda bloqueada; hasta ${EVALUADORES.length} reintentos); ranking y verificación${textoPresupuesto()}`)

  const p0 = await prepararPaso0()
  const FECHA = fechaDe(p0)
  if (!p0.ok) {
    return {
      fase: FASE,
      detenida_en: 'Preparación',
      error: `Sin el registro no se pueden deduplicar las oportunidades (${p0.motivo}): la fase no continúa.`,
      preparacion: resumenPaso0(p0),
      fallos: resumenFallos(),
      siguiente: 'Comprueba nutria doctor y nutria estado y relanza la fase por su nombre con la herramienta Workflow.',
    }
  }
  // Títulos normalizados de las oportunidades ya registradas: no se generan, comprueban ni registran de nuevo.
  const registradasPorTitulo = new Map(p0.registradas.filter((o) => norm(o.titulo)).map((o) => [norm(o.titulo), o.id]))

  phase('Oportunidades')
  const describirProblema = (p) => [
    `Problema ${p.id}: ${p.titulo}` + (p.especie || p.etapa ? ` (${[p.especie, p.etapa].filter(Boolean).join(', ')})` : '') + '.',
    `Mecanismos: ${(Array.isArray(p.mecanismos) && p.mecanismos.length) ? p.mecanismos.join('; ') : '[DESC]'}.`,
    `Soluciones actuales y su eficacia documentada: ${p.soluciones_actuales || '[DESC] no constan en la lista aprobada; búscalas antes de proponer'}.`,
    p.brecha ? `Brecha: ${p.brecha}.` : '',
  ].filter(Boolean).join('\n')

  const noRegistradas = []
  const candidatas = []
  const vistos = new Set()
  const titulosConocidos = () => [...p0.registradas.map((o) => o.titulo), ...candidatas.map((o) => o.titulo)].filter(Boolean)
  const generar = (p, i, ronda, prioridadRumiantes) => {
    const generador = GENERADORES[(i + ronda) % GENERADORES.length]
    const conocidos = titulosConocidos()
    return lanzar(
      `${REGLAS}\nMisión 1, paso M1b. Ámbito: ${FOCO}.\n${describirProblema(p)}\n` +
      `Genera 2-${MAX_POR_GENERADOR} oportunidades originales (como máximo ${MAX_POR_GENERADOR}) siguiendo .claude/skills/hipotesis/SKILL.md (conexiones entre disciplinas, descubrimiento basado en literatura ABC, ` +
      'contradicciones entre estudios, reposicionamiento, combinaciones con mecanismos complementarios). Cada una debe superar o complementar de forma diferenciada ' +
      'las soluciones actuales (explícalo en "diferenciacion") y llevar hipótesis falsables completas: para cada una, su efecto mínimo relevante y su criterio de ' +
      'falsación (los defines tú; nadie los completa después). No propongas dosis óptimas.' +
      (prioridadRumiantes ? `\nFaltan oportunidades en rumiantes (al menos ${OBJ_RUM} de ${N_OPS}): si el problema lo admite, propón oportunidades en rumiantes.` : '') +
      (conocidos.length ? `\nNo repitas estas oportunidades ya registradas o propuestas: ${conocidos.join(' | ')}` : ''),
      { label: `M1b:gen:${p.id}${ronda ? `:r${ronda}` : ''}`, phase: 'Oportunidades', agentType: generador, schema: OPORTUNIDADES },
    ).then((r) => ({ generador, oportunidades: r && Array.isArray(r.oportunidades) ? r.oportunidades.filter(Boolean) : [] }))
  }
  const comprobarNovedad = (o, etiqueta) => parallel([
    () => lanzar(
      `${REGLAS}\nComprueba la novedad en la LITERATURA científica de esta oportunidad (nutria buscar pubmed/europepmc/openalex; anota consultas exactas y fecha), ` +
      'teniendo en cuenta las soluciones actuales del problema. Veredicto: nueva | diferenciada | existente. Si es existente, propón una reformulación diferenciada o ' +
      'explica por qué descartarla. Los productos comerciales y las patentes los comprueba economia-mercado-pi.\n' + JSON.stringify(o),
      { label: `M1b:novedad-literatura:${etiqueta}`, phase: 'Oportunidades', agentType: 'investigador-bibliografico', schema: NOVEDAD_LITERATURA },
    ),
    () => lanzar(
      `${REGLAS}\nComprueba la novedad de esta oportunidad frente a PRODUCTOS comerciales (WebSearch: qué alegan, con qué evidencia y a qué precio) y PATENTES ` +
      '(nutria patentes), teniendo en cuenta las soluciones actuales del problema. Veredicto: nueva | diferenciada | existente. Si es existente, propón una ' +
      'reformulación diferenciada o explica por qué descartarla. La literatura la comprueba investigador-bibliografico.\n' + JSON.stringify(o),
      { label: `M1b:novedad-mercado:${etiqueta}`, phase: 'Oportunidades', agentType: 'economia-mercado-pi', schema: NOVEDAD_MERCADO },
    ),
  ]).then(([literatura, mercado]) => ({ ...o, novedad_literatura: literatura, novedad_mercado: mercado }))
  const combinarNovedad = (lit, mer) => {
    if (!lit || !mer || !VEREDICTOS_NOVEDAD.includes(lit.veredicto) || !VEREDICTOS_NOVEDAD.includes(mer.veredicto)) {
      const falta = [!lit || !VEREDICTOS_NOVEDAD.includes(lit.veredicto) ? 'literatura' : null,
        !mer || !VEREDICTOS_NOVEDAD.includes(mer.veredicto) ? 'productos y patentes' : null].filter(Boolean).join(' y ')
      return { ok: false, motivo: `novedad sin comprobar en ${falta} (fallo de agente)` }
    }
    const vs = [lit.veredicto, mer.veredicto]
    const veredicto = vs.includes('existente') ? 'existente' : (vs.includes('diferenciada') ? 'diferenciada' : 'nueva')
    return { ok: true, veredicto, literatura: lit.literatura, productos: mer.productos, patentes: mer.patentes,
      reformulacion: [lit.reformulacion, mer.reformulacion].filter(Boolean).join(' | ') || null }
  }
  // Genera para unos problemas y comprueba la novedad; descarta antes lo ya registrado (no gasta en comprobarlo).
  const ronda = (problemas, r, prioridadRumiantes) => pipeline(
    problemas,
    (p) => generar(p, PROBLEMAS_APROBADOS.indexOf(p), r, prioridadRumiantes),
    (res, p) => {
      if (res.oportunidades.length > MAX_POR_GENERADOR) {
        log(`${p.id}: el generador devolvió ${res.oportunidades.length} oportunidades; se usan las ${MAX_POR_GENERADOR} primeras (tope por generador)`)
      }
      return parallel(res.oportunidades.slice(0, MAX_POR_GENERADOR).map((o, j) => () => {
        const k = norm(o.titulo)
        if (k && registradasPorTitulo.has(k)) {
          noRegistradas.push({ titulo: o.titulo, motivo: `ya registrada (${registradasPorTitulo.get(k)})` })
          return Promise.resolve(null)
        }
        if (k && vistos.has(k)) {  // ya aceptada en una ronda anterior de esta ejecución: no se comprueba de nuevo
          noRegistradas.push({ titulo: o.titulo, motivo: 'duplicada' })
          return Promise.resolve(null)
        }
        return comprobarNovedad({ ...o, problema_id: p.id, soluciones_actuales_problema: p.soluciones_actuales || '[DESC]', generador: res.generador },
          `${p.id}${r ? `.r${r}` : ''}.${j + 1}`)
      }))
    },
  )
  const anadir = (lista) => {
    for (const o of lista.flat().filter(Boolean)) {
      const k = norm(o.titulo)
      if (!k) { noRegistradas.push({ titulo: o.titulo || null, motivo: 'sin título' }); continue }
      const nov = combinarNovedad(o.novedad_literatura, o.novedad_mercado)
      if (!nov.ok) { noRegistradas.push({ titulo: o.titulo, motivo: nov.motivo }); continue }
      if (nov.veredicto === 'existente') { noRegistradas.push({ titulo: o.titulo, motivo: 'existente sin diferenciación', reformulacion: nov.reformulacion }); continue }
      if (vistos.has(k) || registradasPorTitulo.has(k)) { noRegistradas.push({ titulo: o.titulo, motivo: 'duplicada' }); continue }
      vistos.add(k)
      const limpia = { ...o, novedad: nov }
      delete limpia.novedad_literatura
      delete limpia.novedad_mercado
      candidatas.push(limpia)
    }
  }
  anadir((await ronda(PROBLEMAS_APROBADOS, 0, false)).filter(Boolean))
  let rondasExtra = 0
  while (rondasExtra < MAX_RONDAS_EXTRA) {
    const faltanTotal = candidatas.length < N_OPS
    const faltanRumiantes = contarRumiantes(candidatas) < OBJ_RUM
    if (!faltanTotal && !faltanRumiantes) break
    const problemasRonda = faltanTotal ? PROBLEMAS_APROBADOS : PROBLEMAS_APROBADOS.filter((p) => !p.especie || esRumiante(p.especie))
    if (!problemasRonda.length) {
      log(`Faltan oportunidades en rumiantes (${contarRumiantes(candidatas)}/${OBJ_RUM}), pero ningún problema aprobado es de rumiantes: no se generan más`)
      break
    }
    if (presupuestoBajo()) {
      log(`Rondas extra omitidas: quedan ${Math.round(budget.remaining())} tokens de presupuesto (< ${UMBRAL_TOKENS}); se continúa con ${candidatas.length} oportunidades`)
      break
    }
    rondasExtra += 1
    log(`${candidatas.length}/${N_OPS} oportunidades nuevas o diferenciadas (${contarRumiantes(candidatas)}/${OBJ_RUM} en rumiantes); ronda extra ${rondasExtra} ` +
      `con ${problemasRonda.length} problema(s)`)
    anadir((await ronda(problemasRonda, rondasExtra, faltanRumiantes)).filter(Boolean))
  }

  // Tope: como máximo N_OPS; primero las de rumiantes hasta el objetivo y después el resto, en el orden en que se propusieron.
  const elegidas = new Set(candidatas.filter((o) => esRumiante(o.especie)).slice(0, OBJ_RUM))
  for (const o of candidatas) {
    if (elegidas.size >= N_OPS) break
    elegidas.add(o)
  }
  const seleccion = candidatas.filter((o) => elegidas.has(o))
  candidatas.filter((o) => !elegidas.has(o)).forEach((o) => noRegistradas.push({ titulo: o.titulo, motivo: `fuera del tope n_oportunidades (${N_OPS})` }))
  const nRumiantes = contarRumiantes(seleccion)
  const minimo = {
    requisito: `al menos ${MINIMO_MISION.oportunidades} oportunidades, al menos ${MINIMO_MISION.rumiantes} en rumiantes`,
    oportunidades: seleccion.length, rumiantes: nRumiantes,
    cumple: seleccion.length >= MINIMO_MISION.oportunidades && nRumiantes >= MINIMO_MISION.rumiantes,
  }
  if (seleccion.length < N_OPS) log(`Aviso: ${seleccion.length} oportunidades (objetivo ${N_OPS}); se continúa con las disponibles`)
  if (!minimo.cumple) log(`Aviso: no se cumple el mínimo de la misión (${minimo.requisito}): ${seleccion.length} oportunidades, ${nRumiantes} en rumiantes`)
  if (noRegistradas.length) log(`${noRegistradas.length} propuestas no pasan a registro (ya registradas, existentes, duplicadas, sin comprobar o fuera del tope); se informan en el resultado`)

  // Ficheros que no se pisan entre ejecuciones: fecha + título normalizado, con sufijo -2, -3... si ya existe un fichero
  // con ese nombre (según el paso 0) o se asignó en esta ejecución.
  for (const o of seleccion) {
    o.base_fichero = nombreLibre(p0, `M1_${FECHA}_${slugTitulo(o.titulo)}`, [['research/oportunidades', '.json'], ['research/hipotesis', '_H1.json']])
  }
  if (presupuestoBajo()) {
    const motivo = `quedan ${Math.round(budget.remaining())} tokens de presupuesto (< ${UMBRAL_TOKENS}): no se registra ni se evalúa ninguna oportunidad para no dejar la misión a medias`
    log(`Fase detenida: ${motivo}`)
    return { fase: FASE, fecha: FECHA, detenida_en: 'Presupuesto', motivo, preparacion: resumenPaso0(p0),
      sin_registrar: seleccion.map((o) => ({ titulo: o.titulo, especie: o.especie, novedad: o.novedad.veredicto })), no_registradas: noRegistradas,
      minimo_mision: minimo, fallos: resumenFallos(), siguiente: 'Relanza la fase con más presupuesto.' }
  }
  log(`${seleccion.length} oportunidades pasan a registro, triage y puntuación: hasta ${seleccion.length * (porOportunidad + EVALUADORES.length) + 2} agentes más ` +
    `(${porOportunidad} por oportunidad, ${porOportunidad - EVALUADORES.length} si queda bloqueada; ranking y verificación)`)

  const promptRegistro = (o) =>
    `${REGLAS}\nRegistra la oportunidad que generaste (es una transcripción: no añadas contenido nuevo; lo que falte va como [DESC]). Antes, comprueba con ` +
    `nutria --agente ${o.generador} oportunidad listar --json que no esté ya registrada con un título equivalente: si lo está, no la dupliques y devuelve su ID con ` +
    `"ya_registrada": true. Si no, escribe research/oportunidades/${o.base_fichero}.json (es un fichero nuevo: si ya existiera, no lo sobrescribas; añade -2, -3... al nombre) ` +
    'conforme a src/nutria/data/schemas/oportunidad.schema.json (rellena "novedad" con las comprobaciones de literatura, productos y patentes; ' +
    '"ruta_regulatoria_preliminar": "indeterminada" si no está clara, porque la clasificación la propone regulacion-seguridad) y ejecuta ' +
    `nutria --agente ${o.generador} oportunidad nueva --datos <fichero>. Registra cada hipótesis, con el efecto mínimo relevante y el criterio de falsación que diste, ` +
    `como JSON conforme a src/nutria/data/schemas/hipotesis.schema.json en research/hipotesis/${o.base_fichero}_H<n>.json (sin sobrescribir ficheros existentes) ` +
    `con nutria --agente ${o.generador} hipotesis nueva --datos <fichero>. Devuelve el ID asignado, el fichero y los IDs de las hipótesis.\n` + JSON.stringify(o)
  const promptEvaluador = (ev, o) =>
    `Eres el evaluador independiente "${ev.id}". Puntúa la oportunidad ${o.id} con los 9 criterios de docs/RUBRICA.md siguiendo .claude/skills/puntuacion/SKILL.md. ` +
    `Lee la oportunidad solo con nutria --agente ${ev.id} oportunidad ver ${o.id} --sin-puntuaciones --json (ignora su campo "decisiones": no es evidencia) y la ` +
    `evidencia de abajo (análisis de triage, modelo del ganadero en research/datos/${o.id}/ y crítica). No ejecutes nutria puntuar ranking ni leas puntuaciones o ` +
    'ficheros de otros evaluadores, rankings, informes (research/informes/) ni planes (research/planes/). ' +
    `Guarda research/oportunidades/${o.id}_puntuacion_${ev.id}.json con "evaluador": "${ev.id}" y regístralo con nutria --agente ${ev.id} puntuar registrar --datos <fichero>.\n` +
    'Análisis de triage: ' + JSON.stringify((o.compuertas || []).filter(Boolean).map((c) => ({
      agente: c.agente, compuerta: c.compuerta, estado_propuesto: c.resultado ? c.resultado.estado : null,
      resumen: c.resultado ? c.resultado.resumen : 'sin análisis (fallo de agente)', archivo: c.resultado ? c.resultado.archivo : null,
    }))) +
    '\nCrítica del red team: ' + JSON.stringify(o.critica)

  const evaluadas = await pipeline(
    seleccion,
    (o, _orig, i) => lanzar(promptRegistro(o), { label: `registro:${i + 1}`, phase: 'Oportunidades', agentType: o.generador, schema: REGISTRO })
      .then((r) => (r && typeof r.id === 'string' && /^O-\d{4}$/.test(r.id)
        ? { ...o, id: r.id, archivo: r.archivo || null, ya_registrada: r.ya_registrada === true, hipotesis_ids: r.hipotesis_ids || [] }
        : null)),
    (o) => (o && !o.ya_registrada
      ? parallel([
        ...TRIAGE.map((t) => () => lanzar(
          `${REGLAS}\nOportunidad ${o.id}: ${o.titulo}. Tu tarea: ${t.tarea.split('<ID>').join(o.id)}. Consulta nutria --agente ${t.agente} oportunidad ver ${o.id} --sin-puntuaciones --json. ` +
          `PROPÓN la compuerta "${t.compuerta}" (eres su rol propietario) y regístrala como propuesta, sin --firma-humana: nutria --agente ${t.agente} puntuar compuerta ${o.id} ${t.compuerta} ` +
          '<superada|no_resuelta|bloqueada> --motivo "...". "bloqueada" actúa de inmediato y la oportunidad no se puntúa; "superada" no cuenta hasta que la firme una persona. ' +
          `Devuelve el mismo estado que registres. Guarda tu análisis en research/revisiones/${o.id}_triage_${t.compuerta}.md.`,
          { label: `triage:${t.compuerta}:${o.id}`, phase: 'Triage y crítica', agentType: t.agente, schema: COMPUERTA },
        ).then((c) => ({ agente: t.agente, compuerta: t.compuerta, resultado: c }))),
        () => lanzar(
          `${REGLAS}\nIntenta refutar la oportunidad ${o.id} (nutria --agente critico-red-team oportunidad ver ${o.id} --sin-puntuaciones --json) y sus hipótesis con tu lista de comprobación completa.`,
          { label: `critica:${o.id}`, phase: 'Triage y crítica', agentType: 'critico-red-team', schema: CRITICA },
        ),
      ]).then((rs) => ({ ...o, compuertas: rs.slice(0, TRIAGE.length), critica: rs[TRIAGE.length] }))
      : o),
    // El registro manda: un agente puede registrar "bloqueada" sin decirlo o fallar después de registrarla.
    (o) => (o && !o.ya_registrada ? leerRegistro(o.id, `M1c:${o.id}`, 'Triage y crítica').then((reg) => ({ ...o, registro: reg })) : o),
    (o) => {
      if (!o || o.ya_registrada) return o
      const propuestas = (o.compuertas || []).filter((c) => c && c.resultado && c.resultado.estado === 'bloqueada')
        .map((c) => ({ compuerta: c.compuerta, agente: c.agente, motivo: c.resultado.motivo, origen: 'propuesta del triage' }))
      const delRegistro = o.registro.ok
        ? o.registro.bloqueadas.filter((b) => !propuestas.some((p) => p.compuerta === b.compuerta)).map((b) => ({ ...b, origen: 'registro' }))
        : []
      const bloqueos = [...propuestas, ...delRegistro]
      if (bloqueos.length) {
        log(`${o.id}: compuerta(s) bloqueada(s) (${bloqueos.map((b) => b.compuerta).join(', ')}): no se puntúa`)
        return { ...o, bloqueada: true, bloqueos }
      }
      if (!o.registro.ok) {
        log(`${o.id}: no se pudieron leer sus compuertas del registro (${o.registro.motivo}): no se puntúa`)
        return { ...o, sin_puntuar: `no se pudieron leer las compuertas del registro (${o.registro.motivo})` }
      }
      if (ESTADOS_DETENIDOS.includes(o.registro.estado)) return { ...o, sin_puntuar: `la oportunidad está en estado "${o.registro.estado}"` }
      return parallel(EVALUADORES.map((ev) => () => puntuar(ev, o.id, promptEvaluador(ev, o)))).then((p) => ({ ...o, puntuaciones: p }))
    },
  )
  evaluadas.forEach((o, i) => {
    if (!o) noRegistradas.push({ titulo: seleccion[i].titulo, motivo: 'registro fallido (fallo de agente)' })
    else if (o.ya_registrada) noRegistradas.push({ titulo: o.titulo, motivo: `ya registrada (${o.id}) según el agente de registro` })
  })
  const registradas = evaluadas.filter((o) => o && !o.ya_registrada)
  const bloqueadas = registradas.filter((o) => o.bloqueada)

  phase('Ranking')
  const rutaRanking = `${REVISIONES}/${nombreLibre(p0, `M1_ranking_${FECHA}`, [[REVISIONES, '.md']])}.md`
  const ranking = await lanzar(
    `${REGLAS}\nEjecuta nutria --agente director-cientifico puntuar ranking --perfil recomendado --sim 5000 --semilla 42 --min-evaluadores 3 --alfa 50 y también con --perfil original. ` +
    `Escribe ${rutaRanking} (es un fichero nuevo: si ya existiera, no lo sobrescribas; añade -2, -3... y devuelve la ruta real) con ambas salidas e interpretación: estado de cada oportunidad ` +
    '(APTA, CONDICIONAL, BLOQUEADA, INCOMPLETA, INSUFICIENTE), robustez frente a los pesos (rango con cada perfil), desacuerdos entre evaluadores y compuertas pendientes ' +
    'de firma humana. Propón como candidatas a planes las 3 mejores NO bloqueadas (las APTA antes que las CONDICIONALES). Es una PROPUESTA: la selección la decide una ' +
    'persona. Regístrala con nutria decision registrar --decision "propuesta top-3 M1" --motivo "..." sin --firma-humana.' +
    (bloqueadas.length ? `\nNo se puntuaron por tener compuertas bloqueadas: ${bloqueadas.map((o) => `${o.id} (${o.bloqueos.map((b) => b.compuerta).join(', ')})`).join('; ')}.` : '') +
    (fallos.length ? `\nAgentes que fallaron (decláralo en el documento): ${fallos.map((f) => f.agente).join(', ')}` : ''),
    { label: 'ranking', phase: 'Ranking', agentType: 'director-cientifico', schema: RANKING },
  )
  const verificacion = ranking ? await verificarCitas(ranking.archivo || rutaRanking, 'ranking', 'Ranking') : null

  return {
    fase: FASE,
    fecha: FECHA,
    foco: FOCO,
    preparacion: resumenPaso0(p0),
    problemas: PROBLEMAS_APROBADOS.map((p) => `${p.id}: ${p.titulo}`),
    oportunidades: registradas.map((o) => ({
      id: o.id,
      titulo: o.titulo,
      especie: o.especie || null,
      archivo: o.archivo,
      novedad: o.novedad.veredicto,
      critica: o.critica ? o.critica.veredicto : 'sin crítica (fallo de agente)',
      compuertas_propuestas: (o.compuertas || []).filter(Boolean).map((c) => `${c.compuerta}:${c.resultado ? c.resultado.estado : 'sin propuesta (fallo de agente)'}`),
      bloqueada: Boolean(o.bloqueada),
      bloqueos: o.bloqueos || [],
      sin_puntuar: o.sin_puntuar || (o.bloqueada ? 'compuertas bloqueadas: no se puntúa' : null),
      evaluadores: (o.puntuaciones || []).filter(Boolean).map((p) => ({ id: p.evaluador, modelo: p.modelo, reintento: p.reintento, registrado: Boolean(p.registrado) })),
      evaluadores_registrados: (o.puntuaciones || []).filter((p) => p && p.registrado).length,
    })),
    bloqueadas: bloqueadas.map((o) => ({ id: o.id, titulo: o.titulo, bloqueos: o.bloqueos })),
    minimo_mision: minimo,
    no_registradas: noRegistradas,
    ranking: ranking ? ranking.archivo || rutaRanking : null,
    propuesta_para_planes: ranking ? ranking.top : [],
    verificacion_citas: verificacion,
    fallos: resumenFallos(),
    pendiente: 'Las compuertas son propuestas: "superada" solo cuenta con firma humana. Con menos de 3 evaluadores registrados la oportunidad queda INSUFICIENTE; ' +
      'las bloqueadas no se puntúan.',
    siguiente: 'Punto de control humano: una persona elige las oportunidades para planes, firma la selección en su terminal ' +
      '(nutria decision registrar --entidad <ID> --decision "seleccionada M1" ...) y relanza este workflow por su nombre con la herramienta Workflow y ' +
      '{"fase": "planes", "seleccion": ["O-xxxx", ...]}.',
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
log(`Fase "planes": ${SELECCION.length} oportunidades elegidas. Estimación: ${1 + SELECCION.length * 6} agentes (paso 0; por oportunidad, lectura de sus ` +
  `compuertas del registro, plan, crítica, revisión, compuerta ética sobre el diseño y verificación de citas)${textoPresupuesto()}`)
const p0 = await prepararPaso0()
const FECHA = fechaDe(p0)
// Nombres asignados antes del pipeline, en el orden de la selección (no dependen de qué agente termine antes): la
// versión anterior de cada plan, si existe, se conserva en vez de sobrescribirse, y el análisis ético no pisa otro.
const RUTAS_PLAN = Object.fromEntries(SELECCION.map((id) => [id, {
  anterior: `research/planes/${nombreLibre(p0, `${id}_anterior_${FECHA}`, [['research/planes', '.md']])}.md`,
  etica: `${REVISIONES}/${nombreLibre(p0, `${id}_etica_plan_${FECHA}`, [[REVISIONES, '.md']])}.md`,
}]))
const planExiste = (id) => (p0.ficheros && p0.ficheros.get('research/planes') ? p0.ficheros.get('research/planes').has(`${id}.md`) : null)
phase('Planes')
const planes = await pipeline(
  SELECCION,
  (id) => leerRegistro(id, `planes:${id}`, 'Planes').then((reg) => ({ id, registro: reg })),
  (r, id) => {
    const reg = r.registro
    if (!reg.ok) return { ...r, plan: null, motivo: `no se pudieron leer las compuertas del registro (${reg.motivo}): no se redacta el plan` }
    if (reg.bloqueadas.length) {
      return { ...r, plan: null, bloqueada: true, motivo: `compuertas bloqueadas: ${reg.bloqueadas.map((b) => `${b.compuerta} (${b.motivo})`).join('; ')}: no se redacta el plan` }
    }
    if (ESTADOS_DETENIDOS.includes(reg.estado)) return { ...r, plan: null, motivo: `la oportunidad está en estado "${reg.estado}": no se redacta el plan` }
    return lanzar(
      `${REGLAS}\nRedacta el plan de investigación computacional de ${id} en research/planes/${id}.md. Lee nutria --agente director-cientifico oportunidad ver ${id} --sin-puntuaciones --json ` +
      `y los análisis de research/revisiones/ sobre ${id}. Incluye: pregunta PECO; protocolo de revisión sistemática (PRISMA 2020); datos a extraer (doble extracción); ` +
      'análisis estadísticos; modelos y simulaciones justificados por la decisión que informan; evaluación regulatoria y económica; criterios de descarte fijados de antemano; ' +
      `plan de validación in vitro → ex vivo → in vivo (solo diseño) con TODOS los puntos de tu lista de validación: ${textoLista}; cronograma y agentes responsables. ` +
      `Comprueba con nutria decision listar --entidad ${id} si la selección tiene firma humana y dilo en el plan (sin ella, el plan es provisional).` +
      (planExiste(id) === false ? '' : ` Si research/planes/${id}.md ya existe, no se pierde: antes de escribir, cópialo sin cambios a ${RUTAS_PLAN[id].anterior} ` +
        '(es un fichero nuevo) y cita esa versión anterior en el plan nuevo.'),
      { label: `plan:${id}`, phase: 'Planes', agentType: 'director-cientifico', schema: PLAN },
    ).then((plan) => ({ ...r, plan }))
  },
  (r, id) => (r.plan
    ? lanzar(
      `${REGLAS}\nRevisa de forma adversarial el plan ${r.plan.archivo} de ${id}: qué falta, qué podría invalidarlo y qué criterio de descarte es débil. ` +
      `Comprueba uno por uno los puntos de la lista de validación, que el plan debe cubrir todos: ${textoLista}. Para cada punto (1 a ${LISTA_VALIDACION.length}), ` +
      'devuelve en "lista_validacion" si el plan lo cubre ("cubre"), lo cubre mal ("cubre_mal") o lo omite ("omite"), con el motivo. No escribiste el plan.',
      { label: `critica-plan:${id}`, phase: 'Planes', agentType: 'critico-red-team', schema: CRITICA_PLAN },
    ).then((c) => ({ ...r, critica_plan: c }))
    : r),
  (r, id) => (r.plan && r.critica_plan
    ? lanzar(
      `${REGLAS}\nIncorpora en ${r.plan.archivo} las correcciones necesarias y añade una sección "Respuesta a la crítica" (qué cambia, qué no y por qué).\n` + JSON.stringify(r.critica_plan),
      { label: `revision-plan:${id}`, phase: 'Planes', agentType: 'director-cientifico', schema: PLAN },
    ).then((p2) => ({ ...r, plan_revisado: p2 }))
    : r),
  // F10: con el diseño experimental del plan ya escrito, sostenibilidad-bienestar vuelve a proponer la compuerta ética.
  (r, id) => (r.plan
    ? parallel([
      () => lanzar(
        `${REGLAS}\nEl plan ${(r.plan_revisado || r.plan).archivo} de ${id} ya tiene el diseño de la validación experimental (F10). Revisa ese diseño con la ética de la ` +
        'validación: 3R; Directiva 2010/63/UE y, en España, Real Decreto 53/2013 (procedimientos con animales, incluidos los donantes de líquido ruminal; la técnica ' +
        'in situ es in vivo); alternativas (líquido de matadero, métodos in vitro) y bienestar. VUELVE A PROPONER la compuerta "etica" con este diseño y regístrala como ' +
        `propuesta, sin --firma-humana: nutria --agente sostenibilidad-bienestar puntuar compuerta ${id} etica <superada|no_resuelta|bloqueada> --motivo "...". ` +
        `Devuelve el mismo estado que registres. Guarda tu análisis en ${RUTAS_PLAN[id].etica}. No modifiques el plan.`,
        { label: `etica-plan:${id}`, phase: 'Planes', agentType: 'sostenibilidad-bienestar', schema: COMPUERTA },
      ),
      () => verificarCitas((r.plan_revisado || r.plan).archivo, `plan:${id}`, 'Planes'),
    ]).then(([etica, v]) => ({ ...r, etica, verificacion_citas: v }))
    : r),
)

// Cobertura de la lista de validación según la crítica: puntos omitidos, mal cubiertos o sin respuesta.
const resumenLista = (lista) => {
  const filas = (Array.isArray(lista) ? lista : []).filter((x) => esObjeto(x) && Number.isInteger(x.punto))
  const de = (estado) => filas.filter((x) => x.estado === estado).map((x) => x.punto)
  const respondidos = new Set(filas.map((x) => x.punto))
  return { omitidos: de('omite'), mal_cubiertos: de('cubre_mal'),
    sin_respuesta: LISTA_VALIDACION.map((_, i) => i + 1).filter((n) => !respondidos.has(n)) }
}
const conEticaBloqueada = planes.filter((r) => r && r.plan && r.etica && r.etica.estado === 'bloqueada').map((r) => r.id)
if (conEticaBloqueada.length) log(`Compuerta ética repropuesta "bloqueada" sobre el diseño: ${conEticaBloqueada.join(', ')}`)

return {
  fase: FASE,
  fecha: FECHA,
  preparacion: resumenPaso0(p0),
  planes: planes.map((r, i) => {
    if (!r || !r.plan) {
      return { id: SELECCION[i], plan: null, bloqueada: Boolean(r && r.bloqueada),
        motivo: r && r.motivo ? r.motivo : 'no se pudo redactar el plan (fallo de agente)' }
    }
    const final = r.plan_revisado || r.plan
    return {
      id: r.id,
      plan: final.archivo,
      critica: r.critica_plan ? r.critica_plan.veredicto : 'sin crítica (fallo de agente)',
      lista_validacion: r.critica_plan ? resumenLista(r.critica_plan.lista_validacion) : 'sin crítica (fallo de agente)',
      revisado_tras_critica: Boolean(r.plan_revisado),
      seleccion_firmada: typeof final.seleccion_firmada === 'boolean' ? final.seleccion_firmada : null,
      etica: r.etica ? { estado: r.etica.estado, motivo: r.etica.motivo } : 'sin propuesta (fallo de agente)',
      bloqueada: Boolean(r.etica && r.etica.estado === 'bloqueada'),
      verificacion_citas: r.verificacion_citas || null,
    }
  }),
  fallos: resumenFallos(),
  pendiente: 'Los planes son propuestas: cualquier paso hacia experimentos requiere la decisión firmada de una persona, todas las compuertas superadas ' +
    'y firmadas (incluida la ética, repropuesta sobre el diseño) y todas las autorizaciones.',
}
