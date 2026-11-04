# Rúbrica de priorización (v2.0)

Puntuaciones como fracción del máximo con rango de incertidumbre `[min, max]`. La puntuación es orientativa y no demuestra eficacia; las compuertas bloquean con independencia de la puntuación.

## Pesos por perfil

| Criterio | Eje | recomendado | original |
|---|---|---|---|
| Solidez de la evidencia en la especie y etapa diana (`evidencia`) | viabilidad | 20 | 20 |
| Plausibilidad fisiológica y bioquímica (`plausibilidad`) | viabilidad | 10 | 15 |
| Magnitud esperada de la mejora productiva o sanitaria (`mejora_productiva`) | valor | 15 | 15 |
| Originalidad, diferenciación y protegibilidad (`originalidad`) | valor | 10 | 15 |
| Seguridad (animal, consumidor, usuario, medio ambiente) (`seguridad`) | viabilidad | 10 | 10 |
| Viabilidad tecnológica e industrial (`viabilidad_tecnologica`) | viabilidad | 10 | 10 |
| Viabilidad regulatoria (vía, plazo y coste) (`viabilidad_regulatoria`) | viabilidad | 10 | 5 |
| Potencial económico (ganadero y empresa) (`potencial_economico`) | valor | 15 | 10 |

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

### Potencial económico (ganadero y empresa) (`potencial_economico`, eje valor)

Retorno para el ganadero (beneficio neto por animal y día, probabilidad de ROI > 1) y atractivo para la empresa (mercado direccionable, margen, rNPV).

- **0**: Coste por animal y día superior al beneficio esperado en cualquier escenario.
- **0.25**: ROI positivo solo en el escenario optimista o mercado muy pequeño.
- **0.5**: ROI positivo en el escenario intermedio; mercado moderado.
- **0.75**: ROI positivo en el escenario conservador o mercado grande con margen atractivo.
- **1**: ROI robusto (P(ROI>1) alta) y mercado grande con barreras de entrada a competidores.

## Compuertas (bloqueo independiente de la puntuación)

Estados: `superada`, `no_resuelta` (candidato condicional: no puede avanzar a validación experimental), `bloqueada` (fuera del ranking).

- **Seguridad** (`seguridad`): bloquea si señal de toxicidad relevante a dosis previsibles; residuos preocupantes en alimentos; riesgo inaceptable para usuario o medio ambiente.
- **Clasificación y vía regulatoria** (`regulatoria`): bloquea si el efecto buscado o la alegación implican medicamento veterinario; sustancia prohibida en piensos; ninguna vía legal en los mercados objetivo.
- **Cuantificación analítica** (`analitica`): bloquea si imposible identificar y cuantificar el activo (o un marcador) en el producto y en el pienso.
- **Libertad de operación (FTO)** (`propiedad_intelectual`): bloquea si patente vigente que bloquea la explotación en mercados clave sin licencia ni alternativa de diseño.
- **Ética y bienestar animal** (`etica`): bloquea si la validación exige procedimientos no autorizables o el producto compromete el bienestar animal.

