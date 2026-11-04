'use strict'
// Ejecuta un workflow de .claude/workflows con agentes simulados, para las pruebas de tests/test_orquestacion.py.
//
// Uso: node tests/orquestacion_harness.cjs <workflow.js>   (escenario JSON por la entrada estándar)
// Escenario: {"args": ..., "reglas": [...], "presupuesto_total": null | n, "coste_por_agente": n}
// Cada regla: {"label" | "labelPrefix" | "labelRegex" | "agentType" | "model" | "promptIncludes": ..., "veces": n,
//              "respuesta": <valor devuelto, null incluido> | "lanzar": "<mensaje de error>"}.
// La primera regla que coincide decide la respuesta; sin regla, se genera una respuesta mínima a partir del esquema.
// Salida (stdout): {"resultado", "llamadas", "logs", "fases", "error"}.
const fs = require('fs')

const ruta = process.argv[2]
const escenario = JSON.parse(fs.readFileSync(0, 'utf8') || '{}')
const reglas = Array.isArray(escenario.reglas) ? escenario.reglas : []

// Como en el entorno real de los workflows, estas fuentes de no determinismo no están disponibles.
const FechaOriginal = Date
Date.now = () => { throw new Error('Date.now() no está disponible en los workflows') }
Math.random = () => { throw new Error('Math.random() no está disponible en los workflows') }
global.Date = class extends FechaOriginal {
  constructor(...a) {
    if (a.length === 0) throw new Error('new Date() sin argumentos no está disponible en los workflows')
    super(...a)
  }
}

const porDefecto = (esquema) => {
  if (!esquema || typeof esquema !== 'object') return 'texto'
  if (Array.isArray(esquema.enum)) return esquema.enum[0]
  switch (esquema.type) {
    case 'object': {
      const o = {}
      for (const k of esquema.required || []) o[k] = porDefecto((esquema.properties || {})[k])
      return o
    }
    case 'array': return [porDefecto(esquema.items)]
    case 'string': return esquema.pattern === '^O-\\d{4}$' ? 'O-0001' : 'x'
    case 'number': case 'integer': return 1
    case 'boolean': return true
    default: return null
  }
}

const coincide = (r, ll) => {
  if (r.label !== undefined && r.label !== ll.label) return false
  if (r.labelPrefix !== undefined && !String(ll.label || '').startsWith(r.labelPrefix)) return false
  if (r.labelRegex !== undefined && !new RegExp(r.labelRegex).test(String(ll.label || ''))) return false
  if (r.agentType !== undefined && r.agentType !== ll.agentType) return false
  if (r.model !== undefined && r.model !== ll.model) return false
  if (r.promptIncludes !== undefined && !ll.prompt.includes(r.promptIncludes)) return false
  return true
}

const llamadas = []
const logs = []
const fases = []
let gastado = 0
const total = escenario.presupuesto_total === undefined ? null : escenario.presupuesto_total
const budget = {
  total,
  spent: () => gastado,
  remaining: () => (total === null ? Infinity : Math.max(0, total - gastado)),
}

const agent = async (prompt, opts = {}) => {
  const ll = {
    prompt: String(prompt), label: opts.label, agentType: opts.agentType, model: opts.model, phase: opts.phase,
    effort: opts.effort, schema: opts.schema || null,
  }
  llamadas.push(ll)
  gastado += escenario.coste_por_agente || 0
  await Promise.resolve()
  for (const r of reglas) {
    if (!coincide(r, ll)) continue
    if (r.veces !== undefined) {
      if (r.veces <= 0) continue
      r.veces -= 1
    }
    if (r.lanzar !== undefined) throw new Error(r.lanzar)
    return r.respuesta === undefined ? porDefecto(ll.schema) : JSON.parse(JSON.stringify(r.respuesta))
  }
  return porDefecto(ll.schema)
}
const parallel = async (thunks) => Promise.all(thunks.map(async (t) => {
  try { return await t() } catch (e) { return null }
}))
const pipeline = async (items, ...etapas) => Promise.all(items.map(async (item, i) => {
  let acc = item
  for (const etapa of etapas) {
    try { acc = await etapa(acc, item, i) } catch (e) { return null }
  }
  return acc
}))
const phase = (titulo) => { fases.push(titulo) }
const log = (mensaje) => { logs.push(String(mensaje)) }
const workflow = async () => { throw new Error('workflow() no se simula en las pruebas') }

const fuente = fs.readFileSync(ruta, 'utf8').replace(/^export const meta/m, 'const meta')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const cuerpo = new AsyncFunction('args', 'agent', 'parallel', 'pipeline', 'phase', 'log', 'budget', 'workflow', fuente)
const args = escenario.args === undefined || escenario.args === null ? undefined : escenario.args

cuerpo(args, agent, parallel, pipeline, phase, log, budget, workflow)
  .then((resultado) => ({ resultado, error: null }))
  .catch((e) => ({ resultado: null, error: String((e && e.stack) || e) }))
  .then(({ resultado, error }) => {
    process.stdout.write(JSON.stringify({ resultado, llamadas, logs, fases, error }))
  })
