# Rúbrica de priorización (v2.1)

Puntuaciones como fracción del máximo con rango de incertidumbre `[min, max]`. La puntuación es orientativa y no demuestra eficacia; las compuertas bloquean con independencia de la puntuación.

## Pesos por perfil

| Criterio | Eje | recomendado | original | uniforme |
|---|---|---|---|---|
| Solidez de la evidencia en la especie y etapa diana (`evidencia`) | viabilidad | 20 | 20 | 1 |
| Plausibilidad fisiológica y bioquímica (`plausibilidad`) | viabilidad | 10 | 15 | 1 |
| Magnitud esperada de la mejora productiva o sanitaria (`mejora_productiva`) | valor | 15 | 15 | 1 |
| Originalidad, diferenciación y protegibilidad (`originalidad`) | valor | 10 | 15 | 1 |
| Seguridad (animal, consumidor, usuario, medio ambiente) (`seguridad`) | viabilidad | 10 | 10 | 1 |
| Viabilidad tecnológica e industrial (`viabilidad_tecnologica`) | viabilidad | 10 | 10 | 1 |
| Viabilidad regulatoria (vía, plazo y coste) (`viabilidad_regulatoria`) | viabilidad | 10 | 5 | 1 |
| Rentabilidad para el ganadero (`rentabilidad_ganadero`) | valor | 8 | 5 | 1 |
| Atractivo para la empresa (`atractivo_empresa`) | valor | 7 | 5 | 1 |

## Anclas de puntuación

### Solidez de la evidencia en la especie y etapa diana (`evidencia`, eje viabilidad)

Calidad, cantidad y coherencia de la evidencia publicada sobre el efecto productivo o fisiológico en la especie y etapa objetivo, ponderada por riesgo de sesgo.

- **0**: Sin estudios en la especie diana; solo razonamiento, in silico o analogía.
- **0.25**: Solo in vitro/ex vivo o estudios en otra especie; o un único estudio in vivo con alto riesgo de sesgo.
- **0.5**: 1-3 ensayos in vivo en la especie diana con resultados inconsistentes o riesgo de sesgo moderado.
- **0.75**: Varios ensayos controlados concordantes, o metaanálisis con heterogeneidad moderada.
- **1**: Revisión sistemática/metaanálisis con efecto consistente, baja heterogeneidad y bajo riesgo de sesgo.

### Plausibilidad fisiológica y bioquímica (`plausibilidad`, eje viabilidad)

Coherencia del mecanismo propuesto con la fisiología de la especie, incluido el destino digestivo (degradación ruminal, biohidrogenación, absorción) a la dosis prevista.

- **0**: El mecanismo contradice la fisiología conocida o el compuesto no llega a la diana.
- **0.25**: Mecanismo especulativo; destino digestivo desconocido o probablemente desfavorable.
- **0.5**: Mecanismo plausible con apoyo en otras especies; destino digestivo incierto.
- **0.75**: Mecanismo respaldado en la especie diana; llegada a la diana probable a la dosis prevista.
- **1**: Mecanismo demostrado en la especie diana con marcadores y relación dosis-respuesta.

### Magnitud esperada de la mejora productiva o sanitaria (`mejora_productiva`, eje valor)

Tamaño del efecto esperable sobre variables con valor económico (respuesta productiva, incidencia de enfermedad, eficiencia) frente al efecto mínimo relevante definido a priori.

- **0**: Efecto esperado nulo o por debajo de lo detectable en granja.
- **0.25**: Efecto pequeño, por debajo del mínimo relevante o solo en marcadores intermedios.
- **0.5**: Efecto cercano al mínimo relevante en una variable productiva.
- **0.75**: Efecto claramente superior al mínimo relevante en una variable productiva clave.
- **1**: Efecto grande y múltiple (producción + salud/fertilidad) con traducción económica directa.

### Originalidad, diferenciación y protegibilidad (`originalidad`, eje valor)

Novedad científica frente a la literatura, diferenciación frente a productos existentes y posibilidad de protección (patente, know-how, autorización ligada al titular).

- **0**: Producto equivalente ya en el mercado; sin diferenciación.
- **0.25**: Variación menor de productos existentes; protección difícil.
- **0.5**: Combinación o aplicación nueva con diferenciación defendible; protección incierta.
- **0.75**: Mecanismo o combinación poco explorados; diferenciación clara y vía de protección plausible.
- **1**: Concepto nuevo, sin anticipación conocida en literatura ni patentes, con protección sólida.

### Seguridad (animal, consumidor, usuario, medio ambiente) (`seguridad`, eje viabilidad)

Margen de seguridad, datos toxicológicos disponibles, residuos y transferencia a leche/carne/huevos, riesgos para el usuario y el medio ambiente.

- **0**: Señal de toxicidad a dosis previsibles o residuos preocupantes.
- **0.25**: Datos de seguridad escasos; incertidumbres relevantes sin vía clara para resolverlas.
- **0.5**: Datos parciales; margen de seguridad estimable pero no establecido en la especie diana.
- **0.75**: Buen perfil en especies relacionadas; margen amplio esperado.
- **1**: Seguridad establecida en la especie diana (p. ej. evaluación EFSA favorable de la sustancia).

### Viabilidad tecnológica e industrial (`viabilidad_tecnologica`, eje viabilidad)

Estabilidad (almacenamiento, granulado, premezcla), protección/liberación, compatibilidad, método analítico para cuantificar el activo, suministro y escalado.

- **0**: Inestable o imposible de formular/cuantificar con tecnología disponible.
- **0.25**: Retos tecnológicos mayores sin solución conocida.
- **0.5**: Retos resolubles con desarrollo específico (p. ej. protección ruminal, marcador analítico).
- **0.75**: Tecnología conocida y suministro disponible; desarrollo menor.
- **1**: Formulación, análisis y suministro industriales ya probados para materiales similares.

### Viabilidad regulatoria (vía, plazo y coste) (`viabilidad_regulatoria`, eje viabilidad)

Claridad de la clasificación (materia prima, aditivo y grupo funcional, pienso dietético), datos exigidos, plazo y coste hasta la comercialización en los mercados objetivo.

- **0**: Sin vía regulatoria o el efecto buscado es de medicamento veterinario.
- **0.25**: Requiere nueva autorización de aditivo zootécnico con incertidumbres de seguridad/eficacia importantes.
- **0.5**: Nueva autorización de aditivo con vía clara pero plazo y coste elevados.
- **0.75**: Sustancias ya autorizadas/catalogadas; requiere notificación o ampliación limitada.
- **1**: Comercializable como materia prima catalogada o pienso dietético con alegaciones ya previstas.

### Rentabilidad para el ganadero (`rentabilidad_ganadero`, eje valor)

Presupuesto parcial por animal con `nutria econ ganadero`: beneficio neto en los escenarios P10, P50 y P90 y respuesta umbral frente a la respuesta esperada según la evidencia. ROI = beneficio/coste; ROI > 1 equivale a beneficio neto positivo.

- **0**: Beneficio neto negativo incluso en el escenario optimista (P90), o coste por animal y día inasumible.
- **0.25**: Beneficio neto positivo solo en el escenario optimista (P90).
- **0.5**: Beneficio neto positivo en el escenario intermedio (P50), pero no en el conservador (P10).
- **0.75**: Beneficio neto positivo también en el escenario conservador (P10).
- **1**: Beneficio neto positivo en P10 con margen amplio y respuesta umbral claramente por debajo de la respuesta esperada.

### Atractivo para la empresa (`atractivo_empresa`, eje valor)

Mercado direccionable, margen, coste y plazo del desarrollo y de la autorización, probabilidad de éxito y valor ajustado por riesgo (`nutria econ desarrollador`), barreras de entrada a competidores.

- **0**: VPN ajustado por riesgo central negativo y sin vía razonable para mejorarlo.
- **0.25**: VPN ajustado por riesgo negativo o marginal; mercado pequeño o plazo regulatorio muy largo.
- **0.5**: VPN ajustado por riesgo central positivo, pero sensible a supuestos clave.
- **0.75**: VPN ajustado por riesgo positivo en la mayoría de supuestos; mercado relevante.
- **1**: VPN ajustado por riesgo claramente positivo, mercado grande y barreras de entrada (protección o autorización ligada al titular).

## Compuertas (bloqueo independiente de la puntuación)

Estados: `superada`, `no_resuelta` (candidato condicional: no puede avanzar a validación experimental), `bloqueada` (fuera del ranking).

- **Seguridad** (`seguridad`): bloquea si señal de toxicidad relevante a dosis previsibles; residuos preocupantes en alimentos; riesgo inaceptable para usuario o medio ambiente.
- **Clasificación y vía regulatoria** (`regulatoria`): bloquea si el efecto buscado o la alegación implican medicamento veterinario; sustancia prohibida en piensos; ninguna vía legal en los mercados objetivo.
- **Cuantificación analítica** (`analitica`): bloquea si imposible identificar y cuantificar el activo (o un marcador) en el producto y en el pienso.
- **Libertad de operación (FTO)** (`propiedad_intelectual`): bloquea si patente vigente que bloquea la explotación en mercados clave sin licencia ni alternativa de diseño.
- **Destino digestivo** (`destino_digestivo`): bloquea si el activo no llega a la diana en cantidad relevante a una dosis viable (degradación o transformación ruminal, digestión o absorción), según datos publicados o el cálculo de `nutria destino ruminal`.
- **Ética y bienestar animal** (`etica`): bloquea si la validación exige procedimientos no autorizables o el producto compromete el bienestar animal.

