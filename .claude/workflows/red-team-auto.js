export const meta = {
  name: 'red-team-auto',
  description: 'Revisión adversarial de un documento: extrae las afirmaciones clave y somete cada una a tres refutadores con enfoques distintos (fuentes, biología, viabilidad); sobrevive solo lo que no refuta la mayoría',
  whenToUse: 'Antes de una compuerta o de dar por bueno un informe o plan. Argumento: la ruta del fichero, p. ej. "research/informes/O-0003.md".',
  phases: [
    { title: 'Afirmaciones', detail: 'extracción de las afirmaciones que sostienen la conclusión' },
    { title: 'Refutación', detail: '3 enfoques independientes por afirmación' },
    { title: 'Consolidación', detail: 'tabla de veredictos y acciones' },
  ],
}

const A = typeof args === 'string' ? { archivo: args } : (args || {})
if (!A.archivo) return { error: 'Indica la ruta del documento a revisar' }
const S = (props, required) => ({ type: 'object', properties: props, required })
const STR = { type: 'string' }

const AFIRMACIONES = S({ afirmaciones: { type: 'array', items: S({ id: STR, texto: STR, importancia: STR }, ['id', 'texto']) } }, ['afirmaciones'])
const VEREDICTO = S({ refutada: { type: 'boolean' }, motivo: STR, fuentes: { type: 'array', items: STR }, accion: STR }, ['refutada', 'motivo'])
const ENFOQUES = [
  { id: 'fuentes', texto: '¿existen las fuentes, dicen eso y en qué especie, etapa y dosis? Usa nutria verificar y lee los resúmenes', tipo: 'verificador-citas' },
  { id: 'biologia', texto: '¿es biológicamente sostenible en la especie diana (destino digestivo, concentraciones alcanzables, explicaciones alternativas, diseño de los estudios)?', tipo: 'critico-red-team' },
  { id: 'viabilidad', texto: '¿resiste un examen regulatorio, de seguridad, industrial y económico?', tipo: 'critico-red-team' },
]

phase('Afirmaciones')
const extraidas = await agent(
  `Lee ${A.archivo} e identifica las 5-15 afirmaciones que sostienen su conclusión (las que, si fueran falsas, cambiarían la decisión). Cópialas literalmente con un id A1..An.`,
  { label: 'extraccion', phase: 'Afirmaciones', agentType: 'director-cientifico', schema: AFIRMACIONES },
)
if (!extraidas || !extraidas.afirmaciones.length) return { error: 'No se extrajeron afirmaciones' }

const juzgadas = await pipeline(
  extraidas.afirmaciones,
  (af) => parallel(ENFOQUES.map((e) => () => agent(
    `Intenta REFUTAR esta afirmación de ${A.archivo} desde el enfoque "${e.id}": ${e.texto}. Si no puedes comprobarla, considérala refutada (refutada=true) y explica qué faltaría.\nAfirmación ${af.id}: ${af.texto}`,
    { label: `${af.id}:${e.id}`, phase: 'Refutación', agentType: e.tipo, schema: VEREDICTO },
  ))).then((vs) => {
    const validos = vs.map((v, i) => (v ? { enfoque: ENFOQUES[i].id, ...v } : null)).filter(Boolean)
    const refutaciones = validos.filter((v) => v.refutada).length
    return { ...af, veredictos: validos,
      resultado: refutaciones >= 2 ? 'refutada' : (refutaciones === 1 ? 'debilitada' : 'sostenida') }
  }),
)

phase('Consolidación')
const destino = A.archivo.replace(/^.*\//, '').replace(/\.md$/, '')
const consolidado = await agent(
  `Escribe research/revisiones/${destino}_redteam.md con una tabla "afirmación | resultado | motivos por enfoque | acción" y un resumen de las tres debilidades más graves. ` +
  'Registra con nutria decision registrar las acciones que cambien estados (sin firma humana). Datos:\n' + JSON.stringify(juzgadas.filter(Boolean)),
  { label: 'consolidacion', phase: 'Consolidación', agentType: 'director-cientifico' },
)

return {
  documento: A.archivo,
  resultados: juzgadas.filter(Boolean).map((j) => ({ id: j.id, resultado: j.resultado })),
  refutadas: juzgadas.filter((j) => j && j.resultado === 'refutada').length,
  informe: `research/revisiones/${destino}_redteam.md`,
  nota: consolidado ? 'Consolidado escrito' : 'No se pudo escribir el consolidado',
}
