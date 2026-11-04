export const meta = {
  name: 'evaluar-oportunidad-auto',
  description: 'Evaluación en profundidad de una oportunidad: 8 especialistas independientes, verificación y crítica de cada aportación, síntesis, informe sellado y puntuación ciega por 3 evaluadores',
  whenToUse: 'Cuando una oportunidad registrada (O-xxxx) necesita evaluación completa y reproducible. Argumento: el ID, p. ej. "O-0003" o {"id": "O-0003"}.',
  phases: [
    { title: 'Plan', detail: 'preguntas concretas para cada especialista' },
    { title: 'Especialistas', detail: 'análisis independientes en paralelo' },
    { title: 'Verificación', detail: 'crítica y verificación de fuentes de cada aportación' },
    { title: 'Síntesis e informe', detail: 'informe de 20 secciones con sello de verificación' },
    { title: 'Puntuación', detail: '3 evaluadores ciegos y ranking' },
  ],
}

const A = typeof args === 'string' ? { id: args } : (args || {})
const ID = A.id
if (!ID || !/^O-\d{4}$/.test(ID)) {
  return { error: 'Indica el ID de la oportunidad, p. ej. "O-0003"' }
}
const REGLAS = 'Sigue CLAUDE.md: etiquetas [PUB]/[DER]/[HIP]/[SUP]/[DESC], nada de citas de memoria, contexto biológico completo. Atribuye tus órdenes con "nutria --agente <tu-rol>".'
const S = (props, required) => ({ type: 'object', properties: props, required })
const STR = { type: 'string' }
const STRS = { type: 'array', items: { type: 'string' } }

const ESPECIALISTAS = ['fisiologia-bioquimica', 'compuestos-toxicologia', 'nutricion-formulacion', 'bioinformatico',
  'regulacion-seguridad', 'desarrollo-industrial', 'economia-mercado-pi', 'sostenibilidad-bienestar']

const PLAN = S({ preguntas: { type: 'array', items: S({ agente: { type: 'string', enum: ESPECIALISTAS }, pregunta: STR, entregable: STR }, ['agente', 'pregunta', 'entregable']) }, descarte_rapido: STRS }, ['preguntas'])
const APORTACION = S({
  archivo: STR, resumen: STR,
  afirmaciones: { type: 'array', items: S({ texto: STR, etiqueta: { type: 'string', enum: ['PUB', 'DER', 'HIP', 'SUP', 'DESC'] }, fuentes: STRS }, ['texto', 'etiqueta']) },
  compuerta_propuesta: STR,
}, ['archivo', 'resumen', 'afirmaciones'])
const CRITICA = S({ veredicto: { type: 'string', enum: ['sostenida', 'debilitada', 'refutada', 'no_verificable'] }, debilidades: STRS, experimento_decisivo: STR }, ['veredicto', 'debilidades'])
const VERIF = S({ revisadas: { type: 'number' }, problemas: { type: 'array', items: S({ fuente: STR, estado: STR, motivo: STR }, ['fuente', 'estado', 'motivo']) } }, ['revisadas', 'problemas'])
const INFORME = S({ archivo: STR, sello_valido: { type: 'boolean' }, errores: { type: 'number' }, decision_propuesta: STR }, ['archivo', 'sello_valido', 'decision_propuesta'])
const PUNTUACION = S({ archivo: STR, registrado: { type: 'boolean' } }, ['archivo', 'registrado'])

phase('Plan')
const plan = await agent(
  `${REGLAS}\nPrepara la evaluación de ${ID} (nutria oportunidad ver ${ID} --sin-puntuaciones --json; plan en research/planes/${ID}.md si existe). ` +
  'Formula una pregunta concreta y un entregable para cada especialista que aporte a la decisión (omite los que no aporten) e indica primero los análisis de descarte rápido.',
  { label: `plan:${ID}`, phase: 'Plan', agentType: 'director-cientifico', schema: PLAN },
)
if (!plan) return { error: 'No se pudo preparar el plan' }

// Cada aportación se verifica en cuanto termina (pipeline, sin esperar al resto).
const aportaciones = await pipeline(
  plan.preguntas,
  (q) => agent(
    `${REGLAS}\nOportunidad ${ID}. Tu pregunta: ${q.pregunta}\nEntregable: ${q.entregable}. Guarda tu análisis en research/revisiones/${ID}_${q.agente}.md y devuelve las afirmaciones clave con etiqueta y fuentes.`,
    { label: `${q.agente}`, phase: 'Especialistas', agentType: q.agente, schema: APORTACION },
  ),
  (ap, q) => ap && parallel([
    () => agent(`${REGLAS}\nIntenta refutar este análisis de ${q.agente} sobre ${ID} (${ap.archivo}).\n${JSON.stringify(ap.afirmaciones)}`,
      { label: `critica:${q.agente}`, phase: 'Verificación', agentType: 'critico-red-team', schema: CRITICA }),
    () => agent(`${REGLAS}\nVerifica las fuentes de estas afirmaciones (existencia, correspondencia, retractación y si respaldan la afirmación): ${JSON.stringify(ap.afirmaciones.filter((a) => a.etiqueta === 'PUB'))}`,
      { label: `fuentes:${q.agente}`, phase: 'Verificación', agentType: 'verificador-citas', schema: VERIF }),
  ]).then(([critica, verif]) => ({ agente: q.agente, ...ap, critica, verif })),
)

phase('Síntesis e informe')
const informe = await agent(
  `${REGLAS}\nSintetiza la evaluación de ${ID} siguiendo .claude/skills/informe/SKILL.md: nutria informe nuevo ${ID} (o actualiza research/informes/${ID}.md), ` +
  'integra las aportaciones conservando los desacuerdos y las críticas, corrige las afirmaciones con fuentes problemáticas, ejecuta nutria informe comprobar --online --sellar ' +
  'y corrige hasta que no haya errores (si la red bloquea la verificación, dilo). Propón una decisión (continuar, reformular o descartar) pendiente de firma humana.\n' +
  JSON.stringify(aportaciones.filter(Boolean)),
  { label: `informe:${ID}`, phase: 'Síntesis e informe', agentType: 'director-cientifico', schema: INFORME },
)

phase('Puntuación')
const EVALUADORES = ['evaluador-cientifico', 'evaluador-industrial-regulatorio', 'evaluador-economico']
const puntuaciones = await parallel(EVALUADORES.map((ev) => () => agent(
  `${REGLAS}\nActúa como ${ev}. Puntúa ${ID} con .claude/skills/puntuacion/SKILL.md leyendo nutria oportunidad ver ${ID} --sin-puntuaciones --json y el informe ${informe ? informe.archivo : ''}. ` +
  `No leas puntuaciones de otros evaluadores. Guarda research/oportunidades/${ID}_puntuacion_${ev}.json y regístralo con nutria --agente ${ev} puntuar registrar --datos <fichero>.`,
  { label: `puntuacion:${ev}`, phase: 'Puntuación', agentType: 'director-cientifico', schema: PUNTUACION },
)))

return {
  id: ID,
  aportaciones: aportaciones.filter(Boolean).map((a) => ({ agente: a.agente, archivo: a.archivo, critica: a.critica && a.critica.veredicto,
    fuentes_problematicas: a.verif ? a.verif.problemas.length : null })),
  informe,
  evaluadores_registrados: puntuaciones.filter((p) => p && p.registrado).length,
  siguiente: `nutria puntuar ranking; la decisión sobre ${ID} requiere firma humana.`,
}
