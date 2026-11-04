# Estado y limitaciones de nutria

Generado a partir de la reauditoría del 2026-11-04 sobre la rama `correcciones-auditoria` (commit `17cce15`). Este documento lo produce un script a partir de los resultados guardados en `docs/auditoria/`: no se edita a mano.

> **Cómo leerlo.** La auditoría de línea base (commit `236a161`) confirmó 151 defectos en 11 dimensiones (`docs/auditoria/AUDITORIA_LINEA_BASE.md`, `docs/auditoria/hallazgos_linea_base.json`). Tras las correcciones, una reauditoría revisó cada defecto contra el código actual: en cada bloque, un agente verificador lo clasificó (corregido, mitigado, pendiente o no aplica) leyendo el código y ejecutando pruebas, y un agente escéptico intentó refutar cada «corregido» o «mitigado». Solo cuenta como corregido lo que el escéptico confirmó. Los verificadores son modelos de lenguaje: pueden equivocarse y otra ejecución puede dar resultados distintos. Ninguna persona ha revisado aún esta clasificación.

## Resumen

| Estado final | Alta | Media | Baja | Total |
|---|---|---|---|---|
| Pendiente (el defecto sigue, total o parcialmente) | 11 | 42 | 35 | 88 |
| Mitigado y declarado (defecto de diseño que el código no puede eliminar del todo) | 2 | 16 | 5 | 23 |
| Corregido (confirmado por el escéptico) | 8 | 24 | 8 | 40 |
| **Total** | 21 | 82 | 48 | 151 |

## Pendientes (88)

Defectos que siguen presentes total o parcialmente, incluidos los «corregidos» que el escéptico refutó. Son el trabajo prioritario antes de usar el sistema para decisiones.

| ID | Gravedad | Defecto original | Situación actual |
|---|---|---|---|
| D1-3 | alta | Criterio 1. La independencia es nominal: - Los 'tres evaluadores ciegos' son el mismo agentType director-cientifico (opus, mismo prompt), cambiando solo una cadena de 'enfoque'. | La independencia ya es real en el rol, el modelo y la puntuación, pero se sigue incumpliendo la regla 9 (CLAUDE.md:40-44): |
| D2-2 | alta | Quien genera evalúa, en contra del protocolo del propio sistema. | El director ya no puntúa lo que genera, pero sigue decidiendo qué afirmaciones de sus propios informes y planes se someten a refutación, y los evaluadores siguen viendo la conclusión que él propone. |
| D3-2 | alta | Las referencias que solo tienen URL nunca se verifican: | Refutado por el escéptico: El cambio de código es real: |
| D3-3 | alta | Ningún control ejecutable comprueba que la fuente respalde la afirmación, aunque es uno de los 3 fallos típicos que el propio sistema declara (verificador-citas.md:9-10). | Ha mejorado: el veredicto ya está ligado al sello (el Anexo A es obligatorio en revisado y final), la verificación se hace después del informe del director y la limitación de fondo está declarada. |
| D3-4 | alta | La regla de etiquetar toda afirmación relevante (CLAUDE.md:13) apenas se impone en el código. | Solo se corrigieron las citas autor-año y las [PUB] dentro de una cita '>'. |
| D3-5 | alta | El 'bloqueo automático' es débil y falla en abierto: | Refutado por el escéptico: Lo enumerado al pie de la letra está corregido. |
| D4-2 | alta | Los sistemas de racionamiento (NASEM 2021, INRAE, FEDNA) solo se citan; | Refutado por el escéptico: Solo la parte de racionamiento está mitigada. |
| D5-8 | alta | Las compuertas de seguridad y regulatoria no están protegidas: | Refutado por el escéptico: Lo reproduje sin ninguna persona en /tmp/claude-0/-home-user/138dffb0-b92f-5c6c-af2b-97d3ea9fa185/scratchpad/ext45/p1: |
| D8-1 | alta | Las compuertas sin resolver no bloquean. Una oportunidad sin ninguna compuerta evaluada tiene las 5 en 'no_resuelta' por defecto y queda CONDICIONAL. | Refutado por el escéptico: El mecanismo está corregido: |
| D8-2 | alta | Las decisiones de compuerta no exigen firma humana ni evidencia, y cualquier agente puede revertirlas. | Refutado por el escéptico: El registro ya exige firma para desbloquear y para que 'superada' cuente: |
| D10-1 | alta | La garantía antialucinación que declara el README no se cumple, y la CLI muestra mensajes engañosos. | Refutado por el escéptico: Lo principal está corregido y lo he reproducido en /tmp/claude-0/-home-user/138dffb0-b92f-5c6c-af2b-97d3ea9fa185/scratchpad/ext_b6v/proj (script ext_b6v/scripts/mk_report.py). |
| D1-5 | media | Criterio 2. El hook y el sello no cubren lo que prometen: | Refutado por el escéptico: Los huecos concretos sí están cerrados. |
| D1-7 | media | Criterio 3 (descartar pronto y barato). En evaluar-oportunidad-auto, el plan genera la lista 'descarte_rapido' pero nunca se usa: | Refutado por el escéptico: Solo está corregida la primera mitad: |
| D1-10 | media | Criterio 9. La rúbrica vuelve a mezclar las dos perspectivas económicas: | La mezcla de perspectivas en una sola puntuación y la lectura de P(rNPV>0) están corregidas. |
| D1-11 | media | Criterio 11. La confidencialidad solo se aplica en el router multimodelo, que está inactivo y cuya sensibilidad por defecto es 'normal': | Se corrigió el valor por defecto del router y se declara el marco de Claude Code, pero siguen cuatro carencias del hallazgo: |
| D1-12 | media | Criterio 12. Los requisitos de validación son correctos en el método, pero no llegan a quien escribe los planes: | Los requisitos de validación ya llegan a quien escribe el plan y se añadió la notificación a EFSA. |
| D2-1 | media | La ceguera de los evaluadores no existe en la práctica: | Las instrucciones de rol ya no rompen la ceguera, y la CLI la aplica cuando el evaluador se identifica con --agente. |
| D2-5 | media | La firma humana no autentica a nadie y ninguna transición la exige. | Las transiciones ya exigen firma, pero la firma sigue sin autenticar a nadie, y el hook que debía impedir firmar a los agentes se salta con una abreviatura que argparse acepta. |
| D2-8 | media | El 'descarte barato primero' no está codificado. | El descarte barato solo está codificado a la entrada de la evaluación profunda. |
| D2-10 | media | red-team-auto equipara 'no comprobable' con 'refutada' y reduce el veredicto a un booleano. | Refutado por el escéptico: Corregido solo en parte: |
| D2-12 | media | La doble extracción independiente no está orquestada ni se puede distinguir. | La doble extracción está orquestada y existe la orden de comparación, pero que un registro sea concordante o esté conciliado sigue siendo una declaración. |
| D3-6 | media | El sello no está autenticado y apenas certifica nada: | Refutado por el escéptico: La mejora es real: |
| D3-7 | media | La correspondencia identificador-cita es débil: | La parte (a) está corregida y la (c) solo en parte: |
| D3-10 | media | El registro no exige lo que define [PUB]: | El registro ya exige lo que define [PUB], y las extracciones necesitan una fuente verificada; |
| D3-11 | media | La procedencia y las decisiones no tienen integridad ni autenticación: | Salvo la firma obligatoria para priorizar, validar o reactivar, todo sigue igual: |
| D3-12 | media | Huecos de trazabilidad: - Las puntuaciones se sobrescriben sin historial; | Están corregidos el historial de puntuaciones y los eventos de verificación, y el sello registra su estado real, aunque no los avisos. |
| D4-1 | media | No se ha ejecutado ninguna investigación. | Sigue sin ejecutarse ninguna investigación, así que no hay contenido de dominio aplicado que juzgar. |
| D4-3 | media | La regla 'destino digestivo primero' no se impone: | Refutado por el escéptico: El núcleo está corregido: |
| D4-4 | media | Las concentraciones alcanzables in vivo y la dosis que llega al intestino son solo una instrucción, sin herramienta ni campo de datos. | Refutado por el escéptico: La herramienta y los campos están corregidos: |
| D4-5 | media | El contexto biológico que el sistema llama 'obligatorio' no lo exigen los esquemas. | Corrección parcial: extracciones (salvo las cadenas vacías) e hipótesis ya exigen el contexto biológico. |
| D4-6 | media | La dieta basal es texto libre y no puede usarse como moderador. | Refutado por el escéptico: La parte principal funciona: |
| D4-7 | media | La fisiología del periodo de transición aparece solo como palabras clave. | Hay mejora documental, pero el defecto de datos sigue. |
| D4-10 | media | No se ha medido la competencia de dominio de los modelos en los que descansa todo el razonamiento fisiológico y nutricional. | La competencia de dominio sigue sin medirse y el banco no tiene preguntas de fisiología o nutrición que distingan entre modelos. |
| D5-4 | media | No hay control de sustancias prohibidas o restringidas. | Refutado por el escéptico: Hay ya una lista de comprobación (REGULACION_UE.md:93-108; |
| D5-7 | media | La base legal apenas está verificada y la herramienta no puede verificarla: | Refutado por el escéptico: La mitigación declarada ('el sello falla cerrado ante normativa sin verificación humana') no se sostiene. |
| D5-11 | media | Las alegaciones permitidas solo se tratan como prohibiciones. | Hay campo y documentación de alegaciones, pero la sección 14 puede seguir sin clasificación ni compuertas y pasar la comprobación: |
| D5-16 | media | Nada está comprobado en uso. No existe ninguna clasificación, compuerta ni informe regulatorio real, y el árbol no tiene casos de prueba: | Sigue sin haber ninguna clasificación, compuerta ni informe regulatorio real, ni casos de prueba de clasificación con respuesta esperada. |
| D6-4 | media | El modelo de St-Pierre: - Su docstring dice que estima la pendiente dentro de cada estudio, y no es así con intercepto aleatorio. | Refutado por el escéptico: Está corregido el docstring, la descomposición de Mundlak por defecto y las pruebas. |
| D6-7 | media | HKSJ, el modelo principal recomendado: - No tiene variante modificada max(1,q). | Refutado por el escéptico: Ya no falla con q=0: |
| D7-4 | media | Los intervalos mínimos por host solo se respetan dentro de un proceso, porque viven en el diccionario _last de cada HttpClient. | Corrección parcial: el intervalo por host ya se respeta entre procesos (en POSIX). |
| D7-7 | media | El registro de consultas está incompleto, aunque http.py dice dejar 'constancia de cada consulta': | Mejora parcial: ya quedan las copias servidas desde caché, los fallos y un evento por orden. |
| D7-10 | media | El modo sin red es frágil y opaco: - Con --offline se sirve caché de cualquier antigüedad sin indicar la fecha. | Corrección parcial: el modo offline ya no depende de las credenciales, pero sigue sirviendo copias de cualquier antigüedad sin avisar a quien lee la salida ni poner un límite. |
| D7-11 | media | `diana --organismo` filtra en el cliente después del límite. | Corrección parcial: el filtro de especie va al servidor y desaparece el límite previo al filtro. |
| D7-12 | media | Ningún cliente se ha probado contra los servicios reales. | Refutado por el escéptico: Medí la cobertura de las líneas dentro de funciones con sys.monitoring (cov.py). |
| D8-4 | media | La robustez frente a los pesos es débil por defecto y puede dar una falsa seguridad. | Refutado por el escéptico: Se añadieron alfa=50 (DE de los pesos 3,6-5,6 puntos, comprobado) y el rango determinista con cada perfil en la tabla. |
| D8-7 | media | El informe de empresa da P(rNPV>0) como cifra principal, pero esa cifra mide la incertidumbre de los parámetros sobre un valor esperado ya ajustado por riesgo. | Refutado por el escéptico: El informe ya separa P(rNPV>0) ('no es la probabilidad de que el proyecto gane dinero') de la simulación de resultados: |
| D9-3 | media | Una búsqueda posterior borra la marca de retractación y sustituye metadatos ya verificados, sin dejar rastro. | Refutado por el escéptico: El caso original está corregido: |
| D9-4 | media | Entradas previsibles provocan excepciones sin capturar: | Refutado por el escéptico: Comprobé que (a) fusión, (b) 'faltan media_ctrl' (exit 2), (c) yi idénticos con hksj (exit 0), (d) stpierre sin statsmodels (exit 5) y el moderador constante ('no estimable') están bien. |
| D9-7 | media | La procedencia no basta para reproducir resultados, y las puntuaciones se sobrescriben sin historial. | Corrección parcial: las puntuaciones ya no se pierden y la economía guarda la huella de su entrada. |
| D9-10 | media | El hook falla en abierto: si no puede importar sus dependencias o se rompe, no bloquea. | Refutado por el escéptico: Los casos originales están corregidos y bloquean (s_hook.py, copia privada del repositorio): |
| D9-12 | media | Las pruebas cubren sobre todo los caminos felices y no hay infraestructura para reproducir el entorno. | Corrección parcial: las pruebas han mejorado mucho, pero siguen sin probarse los hooks ni varios clientes y el router, quedan conexiones sin cerrar y no hay infraestructura para reproducir el entorno. |
| D10-3 | media | El coste en tokens y tiempo no se cuantifica ni se acota. | Refutado por el escéptico: Hay mitigación real en la documentación. |
| D10-4 | media | Las limitaciones declaradas no cubren lo principal. | Refutado por el escéptico: La mitigación es real en el README: |
| D2-15 | baja | mision-1-auto no es idempotente ni hace el paso 0 de la skill. | Sigue sin paso 0 (doctor y estado), con nombres de fichero que una segunda ejecución sobrescribe, de modo que el JSON deja de corresponder a la oportunidad registrada, y sin comprobar el mínimo en rumiantes. |
| D2-16 | baja | Colisión de nombres en la salida de red-team: | La segunda revisión sigue sobrescribiendo la primera. |
| D2-17 | baja | Dependencia de versión no documentada. Los workflows (.claude/workflows, invocación /nombre) existen en el binario nativo instalado, pero no en el paquete npm @anthropic-ai/claude-code 2.1.42 que también está en el entorno. | La dependencia de versión sigue sin documentarse: |
| D2-18 | baja | La cobertura de roles en el triage de M1 es incompleta. | La compuerta de seguridad ya la propone el toxicólogo. |
| D3-13 | baja | La lectura de la bibliografía es frágil. Si un [Rn] está duplicado, solo se verifica la última entrada, así que una primera entrada inventada pasa sin comprobar. | Sin cambios: los [Rn] duplicados y una sección anterior con 'referencias' en el título siguen ocultando entradas inventadas a la verificación. |
| D3-15 | baja | La existencia de un DOI solo se consulta en Crossref y OpenAlex, no en el registro de DOI (doi.org). | Sigue igual: un DOI válido de otra agencia que no esté en OpenAlex se da por inexistente. |
| D3-16 | baja | Las pruebas no cubren ninguno de los modos de fallo anteriores: | La cobertura ha mejorado en lo que se corrigió, pero los hooks, la comprobación en línea de informes y varios modos de fallo siguen sin ninguna prueba. |
| D4-11 | baja | La escala de madurez acepta como EMC-2 el efecto in vivo de 'un análogo cercano'. | La escala sigue aceptando la evidencia de un análogo, aunque en rumiantes la forma química o la protección cambian el destino digestivo. |
| D5-12 | baja | La clasificación registrada es incompleta y no se concilia con el triage. | Sin cambios efectivos. Además, como la ruta queda 'indeterminada' y no puede actualizarse, el bloqueo automático de las rutas fuera_de_alcance_* casi nunca se activa en el flujo automático. |
| D5-13 | baja | OMG y microorganismos: la tabla aplica el R. | Sin cambios. |
| D5-14 | baja | El banco de evaluación regulatoria tiene 5 preguntas de memoria y ninguna de clasificación. | Hay más preguntas y alguna de clasificación, pero la corrección sigue dando por buenas respuestas erróneas. |
| D5-15 | baja | Las fuentes regulatorias no tienen acceso determinista. | El acceso a las fuentes regulatorias sigue dependiendo de WebFetch y del LLM, sin cambios de código ni de permisos. |
| D6-3 | baja | Los diseños (crossover y cuadrado latino frente a paralelo) y la unidad experimental se controlan solo por instrucciones. | Sin cambios de código. Mezclar crossover o cuadrado latino con paralelos y animales con corrales pasa sin aviso, y los tamaños de efecto ignoran el diseño. |
| D6-9 | baja | La heterogeneidad se da sin incertidumbre. | Refutado por el escéptico: La mitad del defecto sí está resuelta: |
| D6-11 | baja | En potencia, el efecto de diseño se calcula pero no se aplica: | Sin cambios: n_por_grupo sigue contando animales y no aplica el efecto de diseño. |
| D6-12 | baja | Las pruebas no cubren la parte propia de la ciencia animal y la documentación sobrevende la validación. | Refutado por el escéptico: Hay pruebas nuevas para SEM frente a DE, HKSJ, medidas binarias, dependencia, St-Pierre y LOSO; |
| D7-13 | baja | Las búsquedas no son exhaustivas y se pierden avisos de la API: | Sin cambios: cada búsqueda solo ve la primera página y se pierden los avisos de la API. |
| D7-14 | baja | Los errores tienen una semántica confusa: | Corrección parcial: dos detalles arreglados, pero sigue lo esencial: |
| D7-15 | baja | Las bases que cambian con el tiempo no son reproducibles. | Sin cambios: ni URL versionada de STRING ni versión de STRING, Reactome, ChEMBL o UniProt junto a los resultados. |
| D7-16 | baja | Las licencias están documentadas con inexactitudes y no se registran por fuente: | Siguen las inexactitudes concretas y ningún registro guarda la licencia del artículo o del dato; |
| D7-17 | baja | Los DOI SICI antiguos de Wiley se truncan en '<', porque DOI_RE excluye '<' y '>'. | Sin cambios: el DOI SICI se trunca y una referencia legítima se verificaría con un identificador erróneo (previsiblemente 'no existe'). |
| D8-11 | baja | La rúbrica está anclada en el texto, pero no en los datos: | Sin cambios: la rúbrica sigue anclada en el texto y no en los datos. |
| D8-12 | baja | La respuesta umbral del informe de texto confunde: | Sin cambios en el cálculo ni en el texto. |
| D8-13 | baja | El presupuesto parcial del ganadero se queda corto frente a lo pedido: | No cambió ninguno de los seis puntos: coste por tonelada, fórmula de la skill, correlaciones, análisis de sensibilidad, vínculo de cada entrada con su fuente y comparación con alternativas. |
| D8-14 | baja | El modelo rNPV es correcto pero básico. - Toma las ventas pico como dato de entrada en lugar de construirlas como mercado direccionable × adopción × precio, que es lo que piden el prompt v2 y el agente. | El modelo solo añadió la simulación de resultados (D8-7). |
| D9-8 | baja | La clave de la caché depende de las credenciales y la caché no se versiona: | Corrección parcial: el modo offline ya no falla al cambiar las credenciales, pero las respuestas siguen sin poder reproducirse en otra máquina. |
| D9-16 | baja | La CLI tiene inconsistencias: --offline, --json, NUTRIA_HOME y códigos de salida. | Corrección parcial: --json ya funciona en las órdenes de escritura, pero siguen las fugas de red en offline (doctor y router LLM), la incoherencia de init con NUTRIA_HOME y la ambigüedad del exit 1. |
| D10-8 | baja | --json no cumple lo prometido y los códigos de salida no son fiables. | --json ya funciona en todas las órdenes, pero los errores no tienen estructura y los códigos de salida siguen sin documentar y son incoherentes. |
| D10-9 | baja | Los workflows se presentan como órdenes con barra sin explicar cómo se invocan ni cómo se les pasan argumentos. | Refutado por el escéptico: Lo sustancial está corregido. |
| D10-10 | baja | Dos órdenes producen el mismo sello con criterios distintos. | Refutado por el escéptico: El caso de la auditoría está corregido. |
| D10-11 | baja | La secuencia de ejemplos del README no funciona tal cual en una instalación nueva, y no hay ninguna demostración de extremo a extremo sin red. | Sin cambios: 2 de los 8 ejemplos fallan porque los ficheros no existen, 2 necesitan red y 1 devuelve una tabla vacía. |
| D10-12 | baja | `nutria doctor` informa de menos de lo que promete la documentación. | Lo esencial no ha cambiado: sale con 0 aunque todo esté bloqueado, ignora --offline y no comprueba Lens, WebFetch, R ni el intérprete del hook, aunque FUENTES.md remite a él para comprobar esos dominios. |
| D11-1 | baja | No se ha ejecutado la primera misión ni ninguna otra investigación. | Sigue sin ejecutarse ninguna misión, así que nada demuestra todavía que el sistema produzca investigación de calidad. |
| D11-2 | baja | No hay registro ni trazas con los que auditar el poco contenido de dominio que existe. | Ha mejorado la honestidad: se define qué significa 'verificada' y se propone `nutria verificar` para cerrar las pendientes, y ya existe nutria.toml. |
| D11-3 | baja | R6 (St-Pierre 2001) es la base del enfoque de metaanálisis con modelos mixtos que adopta el sistema (nutria meta stpierre). | Solo se cambió la etiqueta de 'verificada' a 'pendiente'. |

## Mitigados y declarados (23)

Defectos de diseño que el código no puede eliminar del todo (dependen de personas, de servicios externos o de la disciplina de los agentes). Están reducidos y declarados.

| ID | Gravedad | Defecto original | Situación actual |
|---|---|---|---|
| D1-2 | alta | Criterio 3 (y 8). La frontera y las compuertas no se hacen cumplir: | La frontera y las compuertas se hacen cumplir en código, y las cuatro reproducciones originales ya no se dan tal cual; |
| D2-4 | alta | Las compuertas las deciden y registran agentes sin validación humana, y cuentan al instante. | Las compuertas son ya propuestas y solo una firma las da por superadas. |
| D1-8 | media | Criterio 6. La dependencia (varios tratamientos frente a un control común) no se modela ni se avisa en Python: | La dependencia se avisa y se modela en Python, Egger ya no se informa con k<10 y la validación agrupada por estudio es una orden. |
| D1-9 | media | Criterio 10. En el flujo automático, el mapa de lo que ya existe se recoge pero se descarta antes de generar: | Lo que ya existe llega a los generadores y se han restablecido los puntos de control humanos; |
| D1-13 | media | Criterio 7. 'Destino digestivo primero' es solo una instrucción: | Ya no es solo una instrucción: hay herramienta, una compuerta que bloquea y un paso de triage antes de puntuar. |
| D2-3 | media | Los 'tres evaluadores independientes' son el mismo agentType, con el mismo modelo (opus), el mismo CLAUDE.md y la misma crítica como entrada. | Los tres evaluadores son ya un rol propio, con identificadores estables y modelos distintos, y el motor exige tres. |
| D2-14 | media | No hay diversidad de familias de modelos en la crítica ni en la verificación orquestadas. | Ya existe un camino para la segunda opinión de otra familia, y se informa cuando no se usa. |
| D5-2 | media | Orden erróneo del árbol: la pregunta '¿prevenir, tratar o curar una enfermedad?' va antes que la de pienso dietético, que solo se alcanza por la rama 'No'. | Corregido el error principal: los usos dietéticos listados ya no acaban clasificados como medicamento. |
| D5-9 | media | Una compuerta sin resolver no bloquea. Por defecto vale 'no_resuelta' (CONDICIONAL), que se ordena igual que APTA y puede entrar en el top 3 con plan. | Corregido el fallo reproducido: una CONDICIONAL ya no adelanta a una APTA. |
| D5-10 | media | La compuerta de seguridad del triage la decide el agente regulatorio sin el especialista en toxicología: | La compuerta de seguridad la propone ya el toxicólogo en los tres flujos y queda registrada. |
| D6-2 | media | La dependencia entre comparaciones (control compartido, varias variables por estudio) no se trata en Python ni genera aviso. | Ahora hay aviso, agregación en Python (con pruebas) y una plantilla R que no repite el error del SEM. |
| D6-5 | media | No existe validación de modelos predictivos agrupada por estudio: | La validación agrupada por estudio ya existe, se prueba y funciona por CLI. |
| D6-6 | media | El sesgo de publicación con k pequeño está mal resuelto: | Corregido lo grave: Egger con k pequeño va contra la skill y el fallo con vi iguales. |
| D6-8 | media | No hay medidas para variables binarias (RR, OR, RD, Mantel-Haenszel, Peto) ni recuentos de eventos en el esquema. | Las incidencias binarias ya se pueden sintetizar dentro del sistema, desde el esquema hasta el análisis. |
| D7-5 | media | El modelo de acceso a OpenAlex está desfasado. | Código y documentación ya tratan la clave como obligatoria, y la retractación tiene una fuente alternativa. |
| D7-9 | media | Hay fuentes que pide el encargo y que son clave para el dominio, pero no tienen integración en código: | Las fuentes regulatorias, nutricionales y ómicas siguen sin cliente, analizador ni procedencia, pero un error de lectura de un agente ya no puede dar una compuerta por superada sin firma humana. |
| D8-8 | media | En la misión 1 se puntúa el potencial económico, que pesa 15, sin ejecutar el modelo económico. | rentabilidad_ganadero (peso 8) ya se puntúa con el modelo del ganadero ejecutado antes. |
| D8-9 | media | No están garantizados ni el número de evaluadores ni su independencia. | Corregidos el umbral de 3 evaluadores, el recuento por criterio y que los evaluadores de los workflows fueran el director. |
| D1-15 | baja | Criterios 1 y 2. red-team-auto cuenta lo no comprobable como refutado: | Sin red las afirmaciones ya no salen refutadas, y el resultado coincide con el veredicto del crítico. |
| D2-6 | baja | mision-1-auto suprime los dos puntos de control humanos de /mision-1. | La API de Workflow no tiene ninguna primitiva para esperar a una persona, y el diseño lo compensa partiendo la misión en fases con revisión humana entre ellas: |
| D6-10 | baja | Incoherencias en la CLI: - FE se cambia en silencio por REML. | Corregido el error de la coma decimal. PM y los moderadores categóricos ya no abortan el análisis, pero siguen sin estar admitidos. |
| D8-10 | baja | No se pueden reconstruir puntuaciones ni rankings anteriores: | Ya se conservan las puntuaciones sobrescritas y la huella de las entradas. |
| D8-15 | baja | Las pruebas no cubren la semántica crítica de la dimensión. | La mayor parte de la semántica crítica ya tiene pruebas. |

## Corregidos y confirmados (40)

| ID | Gravedad | Defecto original |
|---|---|---|
| D1-1 | alta | Criterio 2. La salvaguarda contra referencias inventadas se degrada en silencio y emite una certificación falsa: |
| D2-7 | alta | La evaluación en profundidad automática no puede usar al investigador bibliográfico, al extractor ni al estadístico, porque el enum de especialistas los excluye. |
| D3-1 | alta | Sin red, la verificación en línea falla en abierto. |
| D5-1 | alta | Frontera con el medicamento incompleta: solo detecta el medicamento veterinario por la alegación o el efecto buscado ('prevenir, tratar o curar'). |
| D6-1 | alta | Sesgo silencioso: si el CSV no trae la columna tipo_disp, `meta analizar` toma cualquier dispersión como DE sin avisar. |
| D7-1 | alta | La comprobación de retractación de los DOI de Crossref depende de OpenAlex y se desactiva en silencio si OpenAlex falla. |
| D7-3 | alta | Sin red, o con errores de red, la verificación se queda en avisos y el sello se marca como verificado en línea. |
| D9-2 | alta | La comprobación de retractación falla en abierto y usa respuestas en caché antiguas. |
| D1-4 | media | Criterio 6. La conversión explícita del SEM combinado, que la revisión presenta como lo más habitual en nutrición animal, se anula con un valor por defecto silencioso: |
| D1-6 | media | Criterios 3 y 5. La regla de frontera se reduce a la alegación terapéutica ('¿prevenir, tratar o curar?'): |
| D2-9 | media | red-team-auto falla en abierto: no exige quórum. |
| D3-8 | media | Una retractación desconocida se trata como 'no retractado', sin aviso. |
| D3-9 | media | La caché HTTP se puede envenenar sin dejar rastro. |
| D4-8 | media | El modelo del ganadero usa los mismos días para el coste y para la respuesta. |
| D4-9 | media | La herramienta de dosis-respuesta (St-Pierre) afirma que estima la pendiente 'dentro de cada estudio' y elimina el sesgo entre estudios. |
| D5-3 | media | El nodo D clasifica como materia prima todo lo que cubre necesidades nutricionales. |
| D5-5 | media | Vía de autorización incompleta. Falta el R. |
| D5-6 | media | Guía FEEDAP de eficacia desfasada. La que se da como 'verificada' es la de 2018 (EFSA J 16(5):5274, DOI 10.2903/j.efsa.2018.5274). |
| D7-2 | media | Las claves de API y el correo se filtran a ficheros versionables. |
| D7-8 | media | `buscar efsa` ignora --desde y --hasta (solo la rama europepmc añade PUB_YEAR), pero los registra en la procedencia como si se hubieran aplicado. |
| D8-3 | media | Las compuertas no condicionan las transiciones de estado, y el estado de registro no condiciona el ranking: |
| D8-5 | media | El escenario 'conservador' del ganadero está mal construido cuando la respuesta puede ser negativa. |
| D8-6 | media | Las anclas de 'potencial_economico' no encajan con el código y mezclan perspectivas. |
| D9-1 | media | La clave de API y el correo de contacto se filtran al sello de verificación, a stdout/stderr y al contexto de los agentes. |
| D9-5 | media | Algunos resultados numéricos salen erróneos sin ningún aviso. |
| D9-6 | media | El sello puede cubrir contenido que nunca se comprobó (condición de carrera TOCTOU), y su significado depende de la orden que lo genera. |
| D9-9 | media | Las altas por lotes no son atómicas: un error a mitad deja altas parciales y el reintento crea duplicados. |
| D9-11 | media | Los workflows tratan los fallos de los agentes como resultados favorables. |
| D9-13 | media | La comprobación del año de la cita toma los años que aparecen dentro del DOI. |
| D10-2 | media | La instalación crea un venv, pero no dice que debe estar activo al abrir Claude Code. |
| D10-5 | media | No queda claro lo que el sistema NO hace. |
| D10-6 | media | El flujo documentado `extraccion exportar → meta analizar` falla con una traza de Python. |
| D1-14 | baja | Criterio 8. No se exigen 3 evaluadores: - El motor solo avisa si hay menos de 2. |
| D2-11 | baja | Conflicto de rol en todos los subagentes. |
| D2-13 | baja | 'Determinista' y 'reproducible' exageran: |
| D3-14 | baja | Un DOI que solo está en OpenAlex se verifica pero no se puede registrar, porque `fuente agregar` vuelve a pedirlo a Crossref. |
| D7-6 | baja | `fuente agregar --doi` no registra los DOI que solo están en OpenAlex (por ejemplo, de DataCite o mEDRA, como los datasets de Zenodo). |
| D9-14 | baja | `fuente agregar --doi` falla con los DOI que solo se verifican en OpenAlex. |
| D9-15 | baja | El límite de frecuencia se aplica por proceso, no de forma global. |
| D10-7 | baja | Un diagnóstico auxiliar aborta todo el metaanálisis con un mensaje opaco. |

## Limitaciones que no dependen de un defecto concreto

- **Agentes, skills y workflows sin ejecución real de principio a fin.** Se han revisado leyendo su código y con pruebas que simulan las respuestas de los agentes (`tests/test_orquestacion.py`); su comportamiento con modelos reales, su coste en tokens y su tiempo no se han medido.
- **Clientes de API sin probar contra los servicios reales.** La red de este entorno bloquea las fuentes científicas; las pruebas en vivo existen (`tests/test_vivo.py`) y se activan con `NUTRIA_PRUEBAS_EN_VIVO=1`.
- **Normativa pendiente de verificación humana.** Lo marcado «pendiente» en `docs/REGULACION_UE.md` debe comprobarlo una persona en EUR-Lex (texto consolidado, CELEX y fecha) y registrarlo con `nutria verificar manual <URL> --firma-humana NOMBRE`.
- **La ceguera de los evaluadores es una barrera, no un control de seguridad.** La CLI oculta puntuaciones, ranking y procedencia a `--agente evaluador-*`, pero un agente con Bash podría leer la base de datos directamente.
- **Los hooks frenan errores y atajos de los agentes, no a un atacante.** Comparan texto de órdenes y rutas.
- **Sin modelos de racionamiento ni simulación metabólica.** La única herramienta de modelización es el destino ruminal de Ørskov y McDonald; los requerimientos se calculan con software oficial o validado (p. ej. NASEM 2021).
- **La versión exacta del modelo de cada subagente no se registra automáticamente**: anótala en el Anexo C de cada informe.
- **Fase exclusivamente computacional.** Nada de lo que produce el sistema es un resultado experimental; las hipótesis de IA no son hallazgos.

## Cómo se regenera

Los datos de la reauditoría están en `docs/auditoria/reauditoria.json` (veredicto del verificador y del escéptico para cada defecto). Para repetirla, lanza desde Claude Code el workflow `scripts/auditoria/reauditoria.js` (herramienta Workflow con `scriptPath`; argumentos: `archivo` = `docs/auditoria/hallazgos_linea_base.json`, `raiz` = la raíz del repositorio e `ids` = los identificadores por dimensión) y regenera este documento con `python scripts/auditoria/gen_estado.py <resultados.json> docs/auditoria/hallazgos_linea_base.json . <commit> [fecha]`.
