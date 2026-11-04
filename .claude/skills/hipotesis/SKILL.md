---
name: hipotesis
description: Procedimiento para generar hipótesis originales y falsables (técnicas de generación, ficha completa, probabilidad previa, criterio de falsación) y registrarlas. Úsalo al generar oportunidades o mecanismos nuevos.
---

# Generación de hipótesis

## Técnicas

1. **Conexiones entre disciplinas**: metabolismo lipídico ↔ función mitocondrial ↔ inflamación ↔ microbiota ↔
   señalización hormonal ↔ eficiencia productiva.
2. **Descubrimiento basado en literatura (modelo ABC)**: si A (problema) se relaciona con B (mecanismo) en una
   literatura y B con C (compuesto) en otra, pero no existen trabajos que unan A y C en la especie diana, A-C es una
   hipótesis candidata. Compruébalo con búsquedas: `nutria buscar pubmed "A AND B"`, `"B AND C"`, `"A AND C"`.
3. **Contradicciones** entre estudios: ¿qué diferencia (dieta basal, etapa, dosis, forma) explicaría resultados opuestos?
4. **Reposicionamiento** desde la nutrición humana, otras especies o la acuicultura, con análisis explícito de la
   transferibilidad (destino digestivo, dosis, coste).
5. **Combinaciones** con mecanismos complementarios (no redundantes), con riesgo de antagonismo analizado.
6. **Resultados negativos**: por qué fracasaron productos parecidos y qué lo evitaría.

## Ficha (esquema `hipotesis.schema.json`)

Fenómeno; mecanismo; compuestos; diana (identificador); efecto esperado; **efecto mínimo relevante**; variables
medibles; evidencia a favor y en contra (con etiquetas y fuentes); incertidumbres; experimentos que la validarían;
**criterio de falsación**; probabilidad previa (0-1, honesta: la mayoría de hipótesis nuevas son falsas); origen
(`ia`, `equipo_humano`, `literatura`, `mixto`) y técnica de generación.

## Registro

`nutria --agente <agente> hipotesis nueva --datos research/hipotesis/<fichero>.json`. Los cambios de estado exigen
motivo: `nutria hipotesis estado H-xxxx refutada --motivo "..."`. Nunca se presenta una hipótesis como descubrimiento.
