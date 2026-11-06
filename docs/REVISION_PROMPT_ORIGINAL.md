# Auditoría del prompt original

Documento revisado: `PROMPT_ORIGINAL.md` (versión 1). Versión corregida: `PROMPT_MAESTRO_v2.md`.
Implementación: este repositorio (`CLAUDE.md`, `.claude/`, paquete `nutria`).

## Veredicto

El prompt tiene buenas intenciones científicas: evita inventar datos, separa hipótesis de hallazgos, exige evidencia
contraria, bloquea candidatos inseguros y trabaja de forma iterativa. Todo eso se conserva.

Su problema de fondo es que **describe un sistema pero no lo construye**. Pide a un único modelo que "actúe como" diez
agentes independientes, que consulte bases de datos de suscripción sin herramientas, que ejecute simulaciones que no
puede ejecutar y que "nunca invente" sin ningún mecanismo que lo compruebe. Con ese prompt, un modelo produce texto
plausible con una precisión aparente que no tiene respaldo: es justo lo que el propio prompt quiere evitar.

Además hay errores de dominio que cambian el resultado: la frontera entre pienso y medicamento veterinario no aparece,
el mapa regulatorio incluye una norma que no aplica y omite otras decisivas, la estadística no recoge las
particularidades de los ensayos en ganadería, la modelización prioriza el docking por delante del destino digestivo
del compuesto y el sistema de puntuación da falsa precisión.

La corrección tiene dos partes:
1. **Prompt corregido y modular** (`PROMPT_MAESTRO_v2.md`): sirve para cualquier modelo y es la referencia del método.
2. **Organización convertida en código**: subagentes con contextos separados, procedimientos (skills), workflows con
   el control de flujo fijo (las respuestas de cada agente cambian entre ejecuciones), herramientas que verifican
   referencias y registran la procedencia, metaanálisis validado, priorización con compuertas y Monte Carlo, y un
   hook que, después de cada escritura, devuelve al agente los errores de forma de un informe y, en revisado o final,
   la falta de revisión humana registrada o de sello válido (verificación en línea completa); no deshace la
   escritura: lo que garantiza un informe es su sello. Con esa organización todavía no se ha ejecutado ninguna
   misión: ver la última sección.

## Qué se conserva

- La progresión por fases, la prioridad en rumiantes y vacuno lechero y la apertura a otras especies.
- La ficha de hipótesis (fenómeno, mecanismo, diana, variables, evidencia a favor y en contra, experimentos).
- Las prohibiciones: no inventar publicaciones, dosis ni datos; no presentar hipótesis de IA como hallazgos; no usar el
  docking como prueba de eficacia; no confundir asociación con causalidad.
- El bloqueo de candidatos por seguridad o regulación con independencia de la puntuación: una compuerta bloqueada
  saca al candidato del ranking; una sin resolver o sin firmar lo deja CONDICIONAL (siempre detrás de los APTA y sin
  poder pasar a validación experimental) hasta que una persona firme su resolución.
- El informe de 20 secciones, el registro de hipótesis descartadas y la idea de no confundir complejidad con calidad.

## Problemas críticos (cambian el resultado)

### C1. Independencia simulada en un solo contexto

**Qué dice**: "Actúa como un equipo multidisciplinar" y "los agentes no deben limitarse a confirmar las conclusiones de
los demás".
**Problema**: un mismo modelo, con el mismo contexto, que genera una hipótesis y después la "critica" tiende a
confirmarla: sus errores están correlacionados. El prompt pide independencia pero no da ningún medio para lograrla.
**Corrección**: agentes con contextos separados; quien genera no evalúa; evaluadores ciegos (no ven las puntuaciones de
otros); un crítico con el mandato explícito de refutar; opción de segunda opinión de otra familia de modelos; los
desacuerdos se registran, no se promedian.
**Implementación**: 15 subagentes en `.claude/agents/`, entre ellos `evaluador-independiente`, el único que puntúa;
`nutria oportunidad ver --sin-puntuaciones`; el ranking marca INSUFICIENTE una oportunidad con menos de tres
evaluadores distintos por criterio; workflows con refutación por mayoría (`red-team-auto`) y tres evaluadores
ciegos por oportunidad (`mision-1-auto`).

### C2. "Nunca inventes" sin mecanismo que lo haga cumplir

**Problema**: una instrucción no evita las tres formas habituales de error de un modelo con la bibliografía: referencias
inexistentes, identificadores reales asignados al trabajo equivocado y fuentes reales citadas para algo que no dicen.
**Corrección**: etiqueta obligatoria por afirmación (`[PUB]`, `[DER]`, `[HIP]`, `[SUP]`, `[DESC]`); verificación
automática de existencia (Crossref, PubMed, OpenAlex), de correspondencia entre identificador y cita y de retractación;
verificación de respaldo por un agente que lee la fuente (tabla del Anexo A); sello de verificación con huella SHA-256
que caduca al editar el informe y que solo es válido con verificación en línea completa.
**Implementación**: `nutria verificar`, `nutria fuente agregar` (solo registra lo verificado),
`nutria informe comprobar --online --sellar`, `nutria informe revisar` (revisión humana registrada),
`nutria afirmacion comprobar`, agente `verificador-citas` y hook `scripts/hooks/check_research_file.py`, que después
de cada escritura devuelve al agente («block») los informes con `[PUB]` sin referencia, referencias sin
identificador, citas fuera del formato `[Rn]` o marcados como revisados o finales sin revisión humana registrada ni
sello válido (el fichero queda escrito; la garantía es el sello). La correspondencia entre identificador y cita es
una heurística que falla en cerrado (la cita debe traer el título completo del registro).

### C3. Encuadre "farmacológico" sin frontera regulatoria, y regulación al final

**Problema**: en la UE, la etiqueta o presentación de un pienso no puede alegar que previene, trata o cura enfermedades
(Reglamento (CE) 767/2009, art. 13, con excepciones limitadas). Es medicamento veterinario (Reglamento (UE) 2019/6,
art. 4), con otro marco, otros costes y otros plazos, lo que se presenta como tratamiento o prevención de enfermedades
y también lo que se usa para restablecer, corregir o modificar funciones fisiológicas ejerciendo una acción
farmacológica, inmunológica o metabólica: con este criterio funcional, reformular la alegación no basta. La frontera
se analiza con la Recomendación 2011/25/UE [R12]. El prompt sitúa la regulación en el paso 9 de 13, cuando ya se ha
invertido en evidencia, mecanismo y formulación.
**Corrección**: triage regulatorio inmediatamente después de generar oportunidades (fase F3), como compuerta. Se
reformula "desarrollo farmacológico" como "compuestos bioactivos con efecto nutricional o fisiológico".
**Implementación**: agente `regulacion-seguridad`, skill `triage-regulatorio`, compuerta `regulatoria`.

### C4. Mapa regulatorio incompleto y con un error

**Error**: el Reglamento (UE) 2015/2283 regula los **nuevos alimentos para consumo humano**, no los piensos. Solo es
pertinente si el ingrediente también se destina a alimentación humana.
**Omisiones decisivas**:
- Reglamento (CE) 429/2008: normas de desarrollo para preparar y presentar solicitudes de aditivos.
- Reglamento (UE) 2019/962: añadió a los aditivos zootécnicos el grupo funcional de **estabilizadores de las
  condiciones fisiológicas** (sustancias que, administradas a animales sanos, favorecen su estado fisiológico, incluida
  la resiliencia frente a factores de estrés) [R13]. Es una vía posible para productos de resiliencia frente al estrés
  oxidativo o calórico en animales sanos, siempre que no actúen como medicamento por función (Reglamento (UE) 2019/6,
  art. 4); un producto antiinflamatorio, por ejemplo, plantea antes esa pregunta.
- Reglamento (UE) 2020/354: usos previstos de los **piensos para fines de nutrición específicos** (piensos dietéticos),
  con entradas como la reducción del riesgo de cetosis o de fiebre de la leche [R14]. Es una vía de comercialización
  que no exige autorizar un aditivo nuevo si los ingredientes ya están autorizados.
- Recomendación 2011/25/UE (materia prima frente a aditivo, biocida y medicamento), Reglamento (CE) 183/2005
  (higiene), Directiva 2002/32/CE (sustancias indeseables), Reglamento (UE) 68/2013 (Catálogo de materias primas) y
  Registro de materias primas, Reglamentos (UE) 2019/4 y 2019/6 (piensos medicamentosos y medicamentos), Reglamentos
  (CE) 1829/2003 y 1830/2003 (OMG, relevantes para productos de fermentación), Reglamento (CE) 396/2005 (residuos de
  plaguicidas en extractos vegetales), Reglamentos (CE) 1069/2009 y (UE) 142/2011 (subproductos animales), Reglamento
  (CE) 999/2001 (EET) y Directiva 2010/63/UE (en España, Real Decreto 53/2013) para cualquier procedimiento con animales.
- Añadidas tras la auditoría de la implementación: la Directiva 96/22/CE y el anexo III del Reglamento (CE) 767/2009
  (sustancias y materiales prohibidos o restringidos), el art. 13.1 y 13.2 de ese reglamento (alegaciones permitidas)
  y el Reglamento (UE) 2019/1381 (asesoramiento previo de EFSA y notificación de los estudios encargados para una
  solicitud). Detalle y estado de verificación en `docs/REGULACION_UE.md`.
**Implicaciones comerciales ausentes**: un aditivo necesita un método analítico evaluado por el laboratorio de
referencia de la UE (EURL-FA); las autorizaciones de aditivos zootécnicos se conceden a un titular concreto, lo que
crea una barrera de entrada con efecto económico (verificar condiciones vigentes); un extracto autorizado como
aromatizante (aditivo sensorial) no puede comercializarse alegando efectos zootécnicos.
**Implementación**: `docs/REGULACION_UE.md` y skill `triage-regulatorio`.

### C5. Fuentes inaccesibles tratadas como accesibles, y fuentes clave ausentes

**Problema**: Google Scholar no tiene API; Web of Science, Scopus y CAB Abstracts exigen suscripción; Cochrane apenas
cubre la producción animal. Un modelo sin acceso "citará" esas fuentes de memoria.
**Ausencias**: índices abiertos con API (OpenAlex, Crossref, Semantic Scholar), AGRIS (FAO), Feedipedia, EFSA Journal y
OpenFoodTox, el Registro de aditivos de la UE, bases de patentes, y recursos específicos de ganadería (Animal QTLdb,
FAANG, bases de metaboloma bovino y ganadero, catálogos de genomas del microbioma ruminal, MGnify).
**Licencias** (proyecto comercial): KEGG y DrugBank restringen el uso comercial; los pesos de AlphaFold 3 se
distribuyen con condiciones de uso no comercial. Hay que comprobarlas antes de construir sobre ellas.
**Implementación**: `docs/FUENTES.md` (acceso real, API, licencia, orden de `nutria`) y clientes para PubMed, Europe
PMC, OpenAlex, Crossref, EFSA Journal, ChEMBL, PubChem, UniProt, STRING, Reactome y patentes.

### C6. Estadística sin las particularidades de la ciencia animal

1. **Dependencia**: varios tratamientos frente a un control compartido, o varias variables por estudio, violan la
   independencia que asumen los modelos de efectos fijos y aleatorios simples. Hacen falta modelos multinivel o
   varianza robusta.
2. **Diseños**: los cuadrados latinos y crossovers (varianza intraanimal, riesgo de efecto residual) no se combinan sin
   más con diseños continuos.
3. **Unidad experimental**: cuando el corral es la unidad, n son corrales, no animales.
4. **Dispersión**: la mayoría de artículos publica un SEM combinado, no la DE por grupo; la conversión debe ser explícita.
5. **Método de la disciplina**: el metaanálisis de medias de tratamiento con el estudio como efecto aleatorio
   (St-Pierre 2001 [R6]; Sauvant et al. 2008 [R7] y 2020 [R8]; Lean et al. 2009 [R9]).
6. **Medida de efecto**: diferencia de medias en unidades naturales (kg/día, puntos de conversión), traducible a euros.
7. **Sesgo de publicación**: las pruebas de asimetría tienen poca potencia con menos de 10 estudios.
8. **PRISMA 2020** [R1] es para revisiones sistemáticas; la primera misión es una revisión de alcance (PRISMA-ScR [R2]).
9. **Riesgo de sesgo y calidad del reporte**: SYRCLE [R3], REFLECT para ensayos en ganado [R4], ARRIVE 2.0 [R5], y una
   valoración de la certeza de la evidencia.
10. **Modelos predictivos**: validación agrupada por estudio (dejar un estudio fuera), no particiones aleatorias de
    filas del mismo estudio.
11. **Efecto mínimo relevante** fijado antes de analizar, y control de la multiplicidad.

**Implementación**: `nutria.stats`: efectos fijos, DL, REML, Hartung-Knapp [R11] (por defecto, en su variante
modificada max(1, q), que coincide con la estándar si q ≥ 1), intervalo de confianza de tau², Egger y
meta-regresión, validados frente a los resultados publicados de metafor [R10] (`tests/test_meta.py`). Tienen pruebas
propias pero no validación frente a otro programa: el intervalo de predicción (t con k − 2 gl y EE de Wald, con
pruebas numéricas en `tests/test_estadistica_v2.py`: BCG calculado a mano y, con z, la coincidencia con `predict()`
de metafor), Paule-Mandel, uno-fuera, subgrupos, medidas binarias (RR, OR, RD), agregación de efectos dependientes
(`--agregar-estudio --rho`), diseños cruzados (fórmulas de grupos emparejados con la correlación intrasujeto),
moderadores categóricos, el modelo de St-Pierre con descomposición intra y entre estudios y elección de la pendiente
aleatoria por razón de verosimilitudes, validación dejando un estudio fuera (`nutria meta validar-predictivo`),
potencia por animales, por corrales y en diseños cruzados, y la plantilla de `nutria meta exportar-r`, que genera el
script de R (metafor) para modelos multinivel con la misma inferencia.

### C7. Puntuación 0-100 con falsa precisión

**Problemas**: sin anclas, dos evaluadores dan números distintos para lo mismo; un único evaluador; sin incertidumbre;
evidencia y originalidad tiran en direcciones opuestas (lo muy original casi nunca tiene evidencia) y su suma esconde
el conflicto; la viabilidad regulatoria vale 5/100 aunque es lo que más condiciona plazo y coste; no se comprueba si el
ranking depende de los pesos elegidos.
**Corrección**: rúbrica anclada (0-0,25-0,5-0,75-1) con rango [mín, máx]; al menos tres evaluadores ciegos; dos ejes
(valor frente a viabilidad y confianza); seis compuertas (seguridad, regulatoria, analítica, libertad de operación,
ética y destino digestivo) que los agentes proponen y que solo cuentan como superadas con firma humana; el potencial
económico se divide en rentabilidad para el ganadero y atractivo para la empresa; Monte Carlo sobre puntuaciones y
pesos que da la probabilidad de quedar entre los primeros. El perfil de pesos original se conserva para comparar.
**Implementación**: `nutria puntuar`, `docs/RUBRICA.md`, `src/nutria/data/rubrica.toml`.

### C8. Economía con perspectivas mezcladas

**Problema**: el retorno para el ganadero y el atractivo para la empresa son preguntas distintas. Faltan el coste y el
plazo del desarrollo y de la autorización, la probabilidad de éxito de cada fase y una definición de cómo se construyen
los escenarios.
**Corrección**: presupuesto parcial del ganadero (escenarios coherentes por percentiles, Monte Carlo, respuesta umbral)
y valor actual neto ajustado por riesgo (rNPV) para la empresa.
**Implementación**: `nutria econ ganadero` y `nutria econ desarrollador`.

## Problemas importantes

### I1. Modelización con las prioridades invertidas

En rumiantes, la microbiota degrada o transforma muchos compuestos antes de que lleguen a la diana (por ejemplo, la
biohidrogenación de los ácidos grasos insaturados). Lo que no llega no actúa, por buena que sea su unión a un
receptor. El orden correcto: destino digestivo y concentraciones alcanzables → modelos de nutrición (NASEM 2021 [R16],
INRAE) y de fermentación → ciclo de vida → y solo para preguntas concretas, docking o dinámica molecular. MATLAB exige
licencia y Cytoscape es una aplicación gráfica; en flujos automatizados conviene networkx/igraph.

### I2. La arquitectura multimodelo es una lista de marcas

Asignar funciones por reputación de marca no es una arquitectura. MedGemma y OpenBioLLM están orientados a la medicina
humana; los nombres y versiones de los modelos cambian cada pocos meses; enviar ideas confidenciales a servicios
externos tiene consecuencias de confidencialidad y propiedad intelectual; y un modelo de lenguaje nunca es una fuente.
**Corrección**: elegir modelo por rol con un banco de preguntas del dominio; registro de modelos con versión, fecha de
verificación y política de datos; segunda opinión entre familias solo donde aporta (crítica, verificación); datos
confidenciales solo a modelos locales o con no retención.
**Implementación**: `nutria llm modelos|preguntar|evaluar`, `src/nutria/data/modelos.toml`, `evals/`.

### I3. Faltan agentes y el director no puede ser un agente más

Faltan el **extractor de datos** (el verdadero cuello de botella de un metaanálisis), el **verificador de citas**
(distinto del crítico), **mercado y propiedad intelectual**, y **sostenibilidad y bienestar**. El director científico
es la sesión principal que orquesta; además existe como subagente para planes y síntesis.

### I4. Propiedad intelectual y confidencialidad

La divulgación previa destruye la novedad de una patente. Las oficinas y tribunales de referencia (EPO, Reino Unido,
EE. UU.) han rechazado designar a una IA como inventora en los casos DABUS: hay que documentar la contribución humana.
**Implementación**: regla 11 de `CLAUDE.md`, registro de decisiones con `--firma-humana`, política de datos por modelo.

### I5. Validación experimental

- La "fermentación ruminal simulada" necesita líquido ruminal de animales donantes (habitualmente canulados, lo que es
  un procedimiento con animales que requiere autorización) o de matadero. La técnica in situ es un ensayo in vivo.
- Si el objetivo es un dossier europeo, los ensayos deben diseñarse desde el principio según la guía de eficacia
  vigente del Panel FEEDAP [R19], aplicable desde el 1-12-2024, que sustituye a la de 2018 [R15]; los estudios
  encargados para respaldar la solicitud se notifican a EFSA (Reglamento (UE) 2019/1381); los estudios de seguridad
  para el dossier suelen exigir buenas prácticas de laboratorio.
- Potencia con varianzas reales (las del metaanálisis). Las variables de salud requieren muestras grandes: comparar una
  incidencia del 30 % con una del 20 % con potencia del 80 % y α = 0,05 exige unos 294 animales por grupo
  [DER] (cálculo: `nutria potencia proporciones --p1 0.3 --p2 0.2`).

### I6. Primera misión

"Identifica al menos 10 oportunidades" en una sola pasada produce ideas genéricas y ya comercializadas, porque no hay
mapa de lo que existe. Se reestructura en M1a (mapa de problemas por carga y necesidad no cubierta), M1b (generación
con técnicas explícitas, como el descubrimiento basado en literatura de Swanson [R17], y comprobación de novedad), M1c
(triage, crítica y puntuación ciega) y M1d (selección con firma humana y planes revisados). Expectativa realista: la
fase computacional entrega candidatos priorizados y planes de validación, no un producto validado.

### I7. Dimensiones ausentes

Bienestar con un marco explícito (modelo de los Cinco Dominios [R18]); sostenibilidad con métodos (directrices del IPCC,
ciclo de vida según ISO 14040/14044, reglas de categoría para piensos) y control del traslado de impactos; One Health y
resistencias (por ejemplo, la retirada del óxido de zinc terapéutico en lechones en la UE en 2022 y la coselección por
metales); financiación y conflictos de interés de los ensayos; método analítico; estabilidad en granulado y premezcla.

### I8. Formato monolítico

Un prompt de unas 4.000 palabras diluye la atención del modelo y no se puede ejecutar. Se separa en constitución
(`CLAUDE.md`), roles (agentes), procedimientos (skills), referencia (`docs/`) y herramientas (código). Las salidas son
legibles y, además, estructuradas (JSON validado contra esquemas).

## Ajustes menores

| Original | Corrección |
|---|---|
| "Ensayos clínicos cuando sean aplicables" | "Ensayos controlados aleatorizados en granja o centro experimental" |
| "Farmacocinética y farmacodinámica" | "ADME y cinética en la especie diana, incluido el metabolismo microbiano" |
| "Efecto esperado" | "Efecto esperado, efecto mínimo relevante y criterio de falsación" |
| "Escenarios conservadores, intermedios y optimistas" | Definidos por percentiles coherentes + Monte Carlo |
| Registro de especie, raza, edad... | Añadir diseño, unidad experimental, dispersión, forma del producto, financiación |
| "DrugBank cuando su licencia lo permita" | Extender la comprobación de licencia a KEGG y AlphaFold 3 |
| "Reglamento (UE) 2015/2283 cuando corresponda" | Solo si el ingrediente también es alimento humano |
| Revisión sistemática para todo | Revisión de alcance en la misión 1; sistemática por oportunidad seleccionada |
| Sin presupuesto ni criterio de parada | Criterios de descarte fijados de antemano y presupuesto por fase |

## Trazabilidad

| Prompt original | Prompt v2 | Implementación |
|---|---|---|
| 1. Identidad y objetivo | §1, §2 | `CLAUDE.md` |
| 2. Ámbitos científicos | §4 | Agentes especialistas |
| 3. Motor de investigación | §5 | `nutria buscar`, `nutria fuente`, `docs/FUENTES.md` |
| 4. Hipótesis | §6 | Skill `hipotesis`, esquema y registro de hipótesis |
| 5. Estadística | §7 | `nutria meta`, `nutria potencia`, skill `metaanalisis` |
| 6. Modelización | §8 | `nutria destino ruminal` (único modelo implementado); `nutria molecula/diana/proteina/red/rutas` solo consultan bases de datos |
| 7. Diseño de productos | §9 | Agentes de nutrición e industrial, esquema de oportunidad |
| 8. Multimodelo | §10 | `.claude/agents/`, workflows, `nutria llm` |
| 9. Seguridad y regulación | §11 | Agente y skill de regulación, `docs/REGULACION_UE.md` |
| 10. Validación futura | §12 | Planes, `nutria potencia` |
| 11. Economía | §13 | `nutria econ` |
| 12. Priorización | §14 | `nutria puntuar`, `docs/RUBRICA.md` |
| 13. Informes | §15 | Plantilla, `nutria informe`, hook |
| 14. Metodología | §16, §17 | Registro de decisiones, procedencia |
| 15. Primera misión | §18 | `/mision-1`, workflow `mision-1-auto` |

## Referencias

Estado de verificación a 2026-11-04: "verificada" = comprobada con búsqueda web en la fuente o en índices
bibliográficos; "pendiente" = sin comprobar en esta sesión (la red bloqueaba Crossref y PubMed). Esa comprobación por
búsqueda web no dejó traza en el registro de nutria ni en la procedencia, así que no es reproducible, y la frontera
entre "verificada" y "pendiente" refleja solo lo que se llegó a buscar. La vía reproducible es
`nutria verificar docs/REVISION_PROMPT_ORIGINAL.md` en un entorno con red, que comprueba cada DOI o PMID y deja la
traza; las referencias con solo URL valen únicamente con la verificación manual firmada por una persona
(`nutria verificar manual <URL> --firma-humana NOMBRE`; los agentes no pueden hacerla).

[R1] Page MJ, McKenzie JE, Bossuyt PM, et al. 2021. The PRISMA 2020 statement: an updated guideline for reporting systematic reviews. BMJ 372:n71. DOI: 10.1136/bmj.n71 (pendiente)
[R2] Tricco AC, Lillie E, Zarin W, et al. 2018. PRISMA Extension for Scoping Reviews (PRISMA-ScR): checklist and explanation. Ann Intern Med 169(7):467-473. DOI: 10.7326/M18-0850 (pendiente)
[R3] Hooijmans CR, Rovers MM, de Vries RB, et al. 2014. SYRCLE's risk of bias tool for animal studies. BMC Med Res Methodol 14:43. DOI: 10.1186/1471-2288-14-43 (pendiente)
[R4] O'Connor AM, Sargeant JM, Gardner IA, et al. 2010. The REFLECT statement: methods and processes of creating reporting guidelines for randomized controlled trials for livestock and food safety. Prev Vet Med 93:11-18. DOI: 10.1016/j.prevetmed.2009.10.008 (verificada parcialmente: el DOI varía entre las versiones publicadas en distintas revistas)
[R5] Percie du Sert N, Hurst V, Ahluwalia A, et al. 2020. The ARRIVE guidelines 2.0: updated guidelines for reporting animal research. PLoS Biol 18(7):e3000410. DOI: 10.1371/journal.pbio.3000410 (pendiente)
[R6] St-Pierre NR. 2001. Invited review: Integrating quantitative findings from multiple studies using mixed model methodology. J Dairy Sci 84(4):741-755. DOI: 10.3168/jds.S0022-0302(01)74530-4 (pendiente de verificación con `nutria verificar`: es el DOI que usan las pruebas de `tests/test_citations.py`; antes figuraba solo la portada de la revista)
[R7] Sauvant D, Schmidely P, Daudin JJ, St-Pierre NR. 2008. Meta-analyses of experimental data in animal nutrition. Animal 2(8):1203-1214. DOI: 10.1017/S1751731108002280 (verificada)
[R8] Sauvant D, Letourneau-Montminy MP, Schmidely P, Boval M, Loncke C, Daniel JB. 2020. Review: Use and misuse of meta-analysis in Animal Science. Animal 14(S2):s207-s222. DOI: 10.1017/S1751731120001688 (verificada)
[R9] Lean IJ, Rabiee AR, Duffield TF, Dohoo IR. 2009. Invited review: Use of meta-analysis in animal health and reproduction: methods and applications. J Dairy Sci 92(8):3545-3565. DOI: 10.3168/jds.2009-2140 (verificada)
[R10] Viechtbauer W. 2010. Conducting meta-analyses in R with the metafor package. J Stat Softw 36(3):1-48. DOI: 10.18637/jss.v036.i03 (pendiente)
[R11] IntHout J, Ioannidis JPA, Borm GF. 2014. The Hartung-Knapp-Sidik-Jonkman method for random effects meta-analysis is straightforward and considerably outperforms the standard DerSimonian-Laird method. BMC Med Res Methodol 14:25. DOI: 10.1186/1471-2288-14-25 (pendiente)
[R12] Recomendación 2011/25/UE de la Comisión, de 14 de enero de 2011, por la que se establecen directrices para la distinción entre materias primas para piensos, aditivos para piensos, biocidas y medicamentos veterinarios. URL: https://eur-lex.europa.eu/eli/reco/2011/25/oj (verificada)
[R13] Reglamento (UE) 2019/962 de la Comisión, que modifica el anexo I del Reglamento (CE) 1831/2003 para establecer dos nuevos grupos funcionales de aditivos. URL: https://eur-lex.europa.eu/eli/reg/2019/962/oj (verificada)
[R14] Reglamento (UE) 2020/354 de la Comisión, de 4 de marzo de 2020, por el que se establece una lista de usos previstos de los piensos destinados a fines de nutrición específicos. URL: https://eur-lex.europa.eu/eli/reg/2020/354/oj (verificada; entrada de fiebre de la leche modificada por el Reglamento (UE) 2024/2899)
[R15] EFSA FEEDAP Panel. 2018. Guidance on the assessment of the efficacy of feed additives. EFSA J 16(5):5274. DOI: 10.2903/j.efsa.2018.5274 (verificada; sustituida por la guía de 2024 [R19], aplicable desde el 1-12-2024: ya no es la vigente)
[R16] National Academies of Sciences, Engineering, and Medicine. 2021. Nutrient Requirements of Dairy Cattle: Eighth Revised Edition. Washington, DC: The National Academies Press. DOI: 10.17226/25806 (verificada)
[R17] Swanson DR. 1986. Fish oil, Raynaud's syndrome, and undiscovered public knowledge. Perspect Biol Med 30(1):7-18. DOI: 10.1353/pbm.1986.0087 (verificada)
[R18] Mellor DJ, Beausoleil NJ, Littlewood KE, et al. 2020. The 2020 Five Domains Model: Including Human-Animal Interactions in Assessments of Animal Welfare. Animals 10(10):1870. DOI: 10.3390/ani10101870 (verificada)
[R19] EFSA FEEDAP Panel. 2024. Guidance on the assessment of the efficacy of feed additives. EFSA J 22(7):e8856. DOI: 10.2903/j.efsa.2024.8856 (verificada (2026-11-04): revista, DOI, adopción el 6-6-2024, aplicación desde el 1-12-2024 y sustitución de [R15]; título exacto pendiente de verificar)

## Auditoría posterior de la implementación

Esta revisión trata del prompt original. La implementación que se construyó a partir de ella pasó después una
auditoría externa de línea base, cuyos hallazgos se identifican como Dx-y. Lo que ya se ha corregido, lo que
queda pendiente y los límites reales del sistema están en `docs/ESTADO_Y_LIMITACIONES.md`. Esa auditoría motivó,
en este documento, la nota de [R15], la referencia [R19], el criterio funcional del medicamento veterinario (C3 y
C4), la corrección del estado de [R6] y su DOI (pendiente de verificación) y la aclaración de qué significa
"verificada". Hasta la fecha no se ha ejecutado ninguna misión ni investigación con la implementación: nada demuestra
aún la calidad de los resultados que produzca.
