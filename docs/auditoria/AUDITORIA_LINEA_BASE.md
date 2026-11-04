# Auditoría de línea base: nutria

- **Fecha:** 2026-11-04. Auditoría externa e independiente, en modo de solo lectura.
- **Sistema evaluado:** nutria (`/home/user/nutria`), commit `236a161`. Salvo que se indique otra cosa, las rutas citadas son relativas a `/home/user/nutria` y siguen el formato `ruta:línea`.
- **Versión auditada:** los hallazgos y las citas `ruta:línea` corresponden al commit `236a161`, que es lo que se evaluó. Para consultar esa versión, usa `git show 236a161:<ruta>`.
- **Cambios posteriores, no evaluados:** desde las 17:44 UTC, mientras se redactaba este informe, aparecieron en `/home/user/nutria` cambios sin confirmar que esta auditoría no hizo:
  - 32 ficheros modificados, con unas 3.500 líneas añadidas;
  - ficheros nuevos como `src/nutria/sello.py`, `src/nutria/destino.py`, `src/nutria/stats/predictive.py`, `scripts/hooks/guard_*.sh`, `nutria.toml` y `research/procedencia.jsonl`.

  Estos cambios no se han evaluado. Por eso, los números de línea del árbol de trabajo actual pueden no coincidir con los citados aquí.
- **Encargo de referencia:** `docs/PROMPT_ORIGINAL.md`.
- **Escala de puntuación** (definida en el arnés de la auditoría, `.claude/workflows/comparar-sistemas.js:101`, que no forma parte del sistema):
  - 0: ausente.
  - 1: mencionado pero no implementado.
  - 2: parcial o con errores importantes.
  - 3: correcto pero básico.
  - 4: sólido.
  - 5: excelente y comprobado.
  - n/a: la dimensión no aplica.
- **Gravedad de los hallazgos:** la que quedó tras la verificación adversarial.

## 1. Resumen ejecutivo

- **Alcance.** Solo se ha auditado nutria. motor-ganadero, el nombre que el usuario da en Claude Code al otro sistema, no está en este entorno: en `/home/user` solo hay `nutria/`, `nutria.zip` y `COMPARAR_EN_LOCAL.md`. Por eso esto es una línea base, no una comparación.
- **Qué es nutria.** Un andamiaje que aún no ha producido investigación: `research/` solo contiene `.gitkeep` y la misión 1 no se ha ejecutado. Tiene dos partes: prompts para Claude Code (14 subagentes, 13 skills, 3 workflows y 1 hook) y un paquete Python de 4.516 líneas con una CLI de 24 órdenes y 42 pruebas que pasan.
- **Puntuaciones (escala 0-5).** D1 3 · D2 2 · D3 2 · D4 2 · D5 2 · D6 3 · D7 2 · D8 3 · D9 2 · D10 3 · D11 n/a. La media simple es 2,4; la calcula este informe, no viene de las evaluaciones.
- **Lo sólido.** Diagnostica bien los 12 fallos del prompt original. El núcleo de metaanálisis reproduce los valores publicados de metafor (dat.bcg) y coincide con statsmodels. La aritmética económica es correcta, el registro funciona con concurrencia y hay clientes reales para 10 APIs, con caché y modo sin red.
- **Lo grave: los controles que deberían hacer cumplir esas correcciones fallan en abierto.** Sin red, un DOI inventado obtiene «Sello válido» y el hook no bloquea. El código no comprueba las referencias que solo tienen URL ni que la fuente respalde la afirmación. Las compuertas no exigen firma humana, las no resueltas no bloquean y cualquier agente puede revertirlas.
- **Otros fallos graves.** Los «tres evaluadores ciegos» son el mismo agente, y su propio prompt le manda consultar las puntuaciones. El SEM se trata como DE sin aviso. El árbol regulatorio no incluye el criterio funcional de medicamento veterinario.
- **Hallazgos.** 111 confirmados (21 de gravedad alta, 82 media y 8 baja) y ninguno descartado. Además, 40 debilidades menores sin verificar.
- **Prioridad.** (1) Que el sello, el hook y las compuertas fallen en cerrado e impongan la firma humana. (2) Que la evaluación sea realmente independiente. (3) Corregir el tratamiento del SEM y el árbol regulatorio. (4) Después, ejecutar la misión 1 con red.

## 2. Inventario

### 2.1 nutria (tipo: mixto)

**Qué hace realmente:** es un andamiaje con dos capas. El razonamiento científico recae por completo en los LLM. El código registra, verifica identificadores, calcula y sella, pero no hace investigación por sí mismo.

**Capa 1: prompts para Claude Code**

- `CLAUDE.md` (114 líneas) convierte la sesión principal en «director científico y orquestador». Fija 12 reglas no negociables: etiquetas [PUB]/[DER]/[HIP]/[SUP]/[DESC], prohibición de citar de memoria, destino digestivo primero, compuertas por encima de la puntuación, frontera entre pienso y medicamento, y humano en el bucle. También define las fases F0-F11 y la tabla de delegación (`CLAUDE.md:41-58`).
- 14 subagentes en `.claude/agents/`, de 24 a 37 líneas cada uno: 7 con opus y 7 con sonnet.

| Subagente | Modelo | Herramientas | Función principal |
|---|---|---|---|
| director-cientifico | opus | Read, Grep, Glob, Bash, Write (sin web) | Planes PECO, síntesis, coherencia antes de las compuertas. En los workflows hace también de los 3 «evaluadores ciegos» |
| investigador-bibliografico | sonnet | + WebSearch, WebFetch | Búsquedas reproducibles en al menos 2 fuentes, búsqueda de nulos, registro verificado, recuentos PRISMA |
| extractor-datos | sonnet | Read, Grep, Glob, Bash, Write, WebFetch | Extracción con tipo de dispersión, unidad experimental y ubicación; doble extracción según el prompt |
| fisiologia-bioquimica | opus | + web | Destino digestivo, concentraciones alcanzables, mecanismos |
| compuestos-toxicologia | opus | + web | Ficha de compuesto, ADME, toxicología EFSA, residuos; propone la compuerta de seguridad |
| nutricion-formulacion | opus | + web | Dieta basal, NASEM 2021/INRAE/FEDNA, protección ruminal, rangos exploratorios |
| bioinformatico | sonnet | + web | UniProt, STRING, Reactome, ómicas, FDR, ortólogos |
| estadistico | opus (effort: high) | sin web | REML+HKSJ, dependencia (rma.mv en R), St-Pierre, certeza, potencia |
| critico-red-team | opus (effort: high) | sin Write | Refutación con 9 comprobaciones; veredicto sostenida, debilitada, refutada o no verificable |
| verificador-citas | sonnet | Read, Grep, Glob, Bash, WebFetch | `nutria informe comprobar --online --sellar` y respaldo de cada [PUB]; el respaldo lo juzga el LLM |
| regulacion-seguridad | opus (effort: high) | + web, Write | Clasificación UE; compuertas «regulatoria» y «seguridad» |
| desarrollo-industrial | sonnet | + web | Fabricación, estabilidad, método analítico; compuerta «analitica» |
| economia-mercado-pi | sonnet | + web | Ganadero frente a empresa (rNPV), mercado, patentes; compuerta «propiedad_intelectual» |
| sostenibilidad-bienestar | sonnet | + web | Metano, ciclo de vida, bienestar, One Health, 3R; compuerta «etica» |

- **13 skills.**
  - 7 que solo puede invocar el usuario: `/mision-1`, `/oportunidad`, `/revision-sistematica`, `/metaanalisis`, `/informe`, `/red-team` y `/estado`.
  - 6 que puede invocar el modelo: busqueda-bibliografica, extraccion-datos, hipotesis, puntuacion, evaluacion-economica y triage-regulatorio.
- **3 workflows versionados.** Su API es correcta: agent, parallel, pipeline, agentType y schema.
  - `mision-1-auto.js` (215 líneas): mapa con 4 lentes; oportunidades con comprobación de novedad; triaje de compuertas y crítico; 3 evaluadores, ranking con `--semilla 42`, top 3 y planes. No tiene puntos de control humanos.
  - `evaluar-oportunidad-auto.js` (85 líneas): plan del director, especialistas en pipeline con crítico y verificador, síntesis, informe sellado y 3 evaluadores.
  - `red-team-auto.js` (59 líneas): de 5 a 15 afirmaciones, cada una con 3 refutadores y regla de mayoría.
  - `comparar-sistemas.js` (184 líneas) **no forma parte del sistema**. No está versionado (git lo marca como `??`) ni incluido en `nutria.zip`, y es el arnés de esta auditoría. `README.md:9` y `docs/ARQUITECTURA.md:74` hablan de 3 workflows.
- **Hook PostToolUse** `scripts/hooks/check_research_file.py` (86 líneas), sobre Write|Edit|MultiEdit (`.claude/settings.json:38-47`).
- **`.claude/settings.json`:**
  - Permite `Bash(nutria *)`, `python -m nutria`, `pytest`, WebSearch y WebFetch en 23 dominios.
  - Pide confirmación solo para `git push`.

**Capa 2: código** (`src/nutria`, 4.516 líneas; la CLI está en `cli.py`, de 991 líneas, con 24 órdenes y códigos de salida 2, 3 y 4)

| Módulo | Qué implementa |
|---|---|
| `db.py` | SQLite en modo WAL con 8 tablas (fuentes, extracciones, afirmaciones, oportunidades, hipótesis, puntuaciones, compuertas, decisiones); IDs creados dentro de una transacción IMMEDIATE; los cambios de estado exigen motivo |
| `provenance.py` | JSONL de solo adición con flock, eliminación de claves sensibles e instantánea del entorno con el commit de git |
| `http.py` | Caché en disco (30 días; 7 en búsquedas), intervalo mínimo por host, reintentos ante 429/5xx, error de proxy explícito y modo sin red |
| `sources/` | PubMed, Europe PMC (incluido EFSA Journal), OpenAlex, Crossref, ChEMBL, PubChem, UniProt, STRING, Reactome y patentes. Lens solo funciona con token; sin él se generan enlaces |
| `verify/citations.py` | Extracción de DOI y PMID; existencia en Crossref (con OpenAlex como respaldo) y PubMed; correspondencia de título y año; retractación |
| `stats/` | Tamaños de efecto con conversión de SEM, SED e IC95; FE, DL, REML, PM, HKSJ, intervalo de predicción, Egger, uno-fuera y subgrupos; meta-regresión con Knapp-Hartung; St-Pierre con statsmodels; potencia |
| `scoring/engine.py` + `data/rubrica.toml` | 8 criterios en 2 ejes, perfiles «recomendado» y «original», 5 compuertas, Monte Carlo triangular con pesos Dirichlet (alfa 200), P(top-k) |
| `econ/` | Distribuciones; presupuesto parcial del ganadero (escenarios P10/P90, Monte Carlo, respuesta umbral); rNPV por fases |
| `reports/` | Plantilla de 20 secciones con anexos, comprobación estructural y en línea, sello SHA-256 en `<informe>.verificacion.json` |
| `llm/router.py` | Segunda opinión multimodelo con política de datos; los 6 modelos están en `activo=false` (`src/nutria/data/modelos.toml:21-75`) |
| `data/schemas/` | Oportunidad, hipótesis, extracción, afirmación y puntuación (JSON Schema 2020-12) |
| `meta exportar-r` | Script R con metafor (`rma` con knha, `rma.mv` con estudio y comparación, y `robust`) |

**Documentos**

- `README.md` (141 líneas).
- `docs/PROMPT_ORIGINAL.md` (120).
- `docs/PROMPT_MAESTRO_v2.md` (446): método corregido.
- `docs/REVISION_PROMPT_ORIGINAL.md` (276): fallos críticos C1-C8 e importantes I1-I8, más 18 referencias cuyo estado «verificada/pendiente» declara el propio documento.
- `docs/REGULACION_UE.md` (84): árbol en Mermaid y 21 filas normativas, solo 3 marcadas «verificada».
- `docs/RUBRICA.md`, `docs/FUENTES.md` y `docs/ARQUITECTURA.md`.
- `ejemplos/`: plantillas marcadas ILUSTRATIVO y `validacion_bcg.csv`.
- `evals/dominio_semilla.jsonl`: 10 preguntas (5 de regulación, 4 de estadística y 1 de fisiología).

**Pruebas**

- Resultado: 42 pruebas en 9 ficheros, «42 passed in 2.31s», con Python 3.13.18, pytest 9.2.0, numpy 2.5.3, scipy 1.18.1, statsmodels 0.15.0 y pandas 3.0.5. Se ejecutaron sobre una copia, porque el paquete está instalado en modo editable sobre `src/`.
- Cobertura de líneas: aproximadamente 61 % (medida en D9-12).
  - 0 %: `llm/router.py`, `stats/mixed.py`, chembl, pubchem, stringdb, reactome y patents.
  - `http.py` 26 %, `cli.py` 52 % y `db.py` 62 %.
  - `stats/meta.py` 88 % y scoring 96 %.
- Sin ninguna prueba: el hook, los workflows, la verificación en línea de informes, el intervalo de predicción, St-Pierre y `exportar-r`.
- Como R no está instalado, nada se recalculó con metafor. Las pruebas comparan con valores de referencia fijados en `tests/test_meta.py:11-64`.

**Resultados de investigación:** ninguno.

- Las carpetas `research/{datos,evals,hipotesis,informes,oportunidades,planes,revisiones}` solo contienen `.gitkeep`.
- En el commit auditado no existen `research/nutria.sqlite`, `research/procedencia.jsonl` ni `nutria.toml`. Los dos últimos aparecieron después, a las 17:55 UTC, con los cambios ajenos a la auditoría descritos en la cabecera.
- No hay mapa de problemas, oportunidades, hipótesis, puntuaciones, compuertas, informes ni planes.

**Cómo se usa**

- Instalación (`README.md:21-54`): crear un entorno con `python -m venv .venv`, ejecutar `pip install -e ".[dev,mixto]"` y después `nutria init` y `nutria doctor`. Las claves van solo en variables de entorno.
- En Claude Code se abre la carpeta y `CLAUDE.md` convierte la sesión en orquestador.
- Órdenes del usuario: `/mision-1`, `/oportunidad`, `/revision-sistematica`, `/metaanalisis`, `/informe`, `/red-team` y `/estado`.
- Workflows: `mision-1-auto`, `evaluar-oportunidad-auto` y `red-team-auto`. No se ha podido comprobar que funcionen como órdenes con barra.
- CLI directa: todas las órdenes aceptan `--json`, `--agente` y `--offline`.

**Límites reales, según el inventario**

- Ningún cliente de API se ha probado contra los servicios reales; `README.md:112-114` lo reconoce y el proxy de este entorno bloquea los 9 hosts.
- Si la fuente respalda la afirmación lo decide un agente LLM, no el código (`src/nutria/verify/citations.py:7-9`).
- Sin red, el sello sigue siendo «válido».
- La firma humana, la ceguera de los evaluadores, la doble extracción y la aprobación de compuertas dependen solo de instrucciones.

### 2.2 motor-ganadero

**No disponible en este entorno.** Ninguna búsqueda con `find / -iname '*motor*ganadero*'` lo encuentra, ni la de los auditores ni la repetida al redactar este informe.

`/home/user/COMPARAR_EN_LOCAL.md:3-4` explica que la sesión en la nube no puede leer carpetas del ordenador local. Para hacer la comparación aquí, `/home/user/COMPARAR_EN_LOCAL.md:41-45` propone dos vías:

- subir motor-ganadero a un repositorio de GitHub;
- compartirlo por Remote Control.

Sin inventario de motor-ganadero, en todas las dimensiones «mejor» es n/a.

## 3. Cuadro de puntuaciones por dimensión

Hallazgos «A/M/B» = gravedad alta / media / baja.

| Dim. | Título | nutria | motor-ganadero | Mejor | Hallazgos confirmados (A/M/B) | Debilidades menores sin verificar |
|---|---|:-:|:-:|:-:|:-:|:-:|
| D1 | Detección y corrección de los fallos del prompt original | **3** | no disponible | n/a | 13 (3/10/0) | 2 |
| D2 | Arquitectura multiagente y orquestación | **2** | no disponible | n/a | 14 (3/8/3) | 4 |
| D3 | Antialucinación, verificación y trazabilidad | **2** | no disponible | n/a | 12 (5/7/0) | 4 |
| D4 | Rigor científico en nutrición y fisiología animal | **2** | no disponible | n/a | 10 (1/9/0) | 1 |
| D5 | Regulación UE y seguridad | **2** | no disponible | n/a | 12 (2/10/0) | 4 |
| D6 | Estadística y metaanálisis | **3** | no disponible | n/a | 8 (1/6/1) | 4 |
| D7 | Integración con fuentes de datos | **2** | no disponible | n/a | 12 (2/9/1) | 5 |
| D8 | Priorización, compuertas y economía | **3** | no disponible | n/a | 9 (2/7/0) | 6 |
| D9 | Ingeniería: pruebas, robustez y reproducibilidad | **2** | no disponible | n/a | 13 (1/11/1) | 3 |
| D10 | Operación y usabilidad | **3** | no disponible | n/a | 6 (1/5/0) | 6 |
| D11 | Resultados de investigación producidos | **n/a** | no disponible | n/a | 2 (0/0/2) | 1 |
| | **Total** | | | | **111 (21/82/8)** | **40** |

Cómo leer el cuadro:

- **Origen de las puntuaciones.** Las puso el auditor de cada dimensión. Como solo había un sistema, el arnés no ejecutó la verificación de los veredictos comparativos (`.claude/workflows/comparar-sistemas.js:125-131`). Por eso ninguna puntuación tiene una segunda verificación independiente.
- **Hallazgos.** Los hallazgos sí pasaron todos por la verificación adversarial.
- **D11 (n/a).** El propio criterio de D11 indica que se usa «n/a» cuando no hay resultados de investigación, y que eso no penaliza (`comparar-sistemas.js:98-99`).

### 3.1 Justificación y fortalezas por dimensión

**D1, puntuación 3.** Detecta con precisión de dominio los 12 fallos del prompt original (`docs/REVISION_PROMPT_ORIGINAL.md:38-204`, tabla de trazabilidad en `:233-251`). El grado de corrección varía por criterio:

| Criterios | Estado de la corrección |
|---|---|
| 4 y 5 | Corregidos y llevados a los documentos de trabajo |
| 8 | Sólido en código y con pruebas |
| 6, 7, 9, 10 y 12 | Parciales |
| 1, 2, 3 y 11 | Con errores importantes, reproducidos |

No llega a 4 porque fallan los tres controles de los que depende todo lo demás: antialucinación, compuertas con frontera regulatoria e independencia. A eso se suma el tratamiento del SEM como DE sin aviso. No baja a 2 porque la detección es completa y varias correcciones están en código con pruebas.

Fortalezas:
- Regulación corregida:
  - El 2015/2283 queda limitado a alimentos humanos.
  - Se añaden el 429/2008, el 2019/962, el 2020/354 y la Rec. 2011/25/UE.
  - Evidencia: `docs/REGULACION_UE.md:48-53`, `:64`; `docs/PROMPT_MAESTRO_v2.md:289-306`.
- Frontera pienso/medicamento y triage temprano con soporte en los datos. Evidencia: `CLAUDE.md:29-30`; `src/nutria/data/schemas/oportunidad.schema.json:17-19`; `src/nutria/data/rubrica.toml:109-112`; `.claude/workflows/mision-1-auto.js:136-165`.
- Verificación de identificadores en código. `fuente agregar` no registra nada sin verificar y el sello caduca al editar el informe. Evidencia: `src/nutria/verify/citations.py:95-105`, `:127-169`; `src/nutria/cli.py:196-207`; `src/nutria/reports/check.py:158-166`.
- Particularidades estadísticas implementadas:
  - Conversiones de SEM, SED e IC95.
  - REML con HKSJ.
  - Potencia para diseños crossover y efecto de diseño.
  - Evidencia: `src/nutria/stats/effect_sizes.py:18-43`; `src/nutria/stats/meta.py:200-271`; `src/nutria/stats/power.py:33-57`.
- Rúbrica anclada con rango, dos ejes y Monte Carlo (`src/nutria/data/rubrica.toml:1-135`; `src/nutria/scoring/engine.py:144-235`). La economía va en dos modelos separados (`src/nutria/econ/farm.py:90-126`; `src/nutria/econ/rnpv.py:58-72`).
- Validación experimental correcta: animales donantes o matadero, in situ como in vivo, Directiva 2010/63/UE. Evidencia: `docs/PROMPT_MAESTRO_v2.md:318-335`.
- Propiedad intelectual:
  - Exige autoría humana.
  - Registra el campo `origen`.
  - El router aplica una política de datos.
  - Evidencia: `CLAUDE.md:35-37`; `src/nutria/data/schemas/hipotesis.schema.json:24`; `src/nutria/llm/router.py:124-126`.

**D2, puntuación 2.** La parte formal es excelente y está comprobada:
- El frontmatter de los 14 agentes y las 13 skills es válido.
- Los workflows se ejecutan sin errores con `agent()` simulado: 141 llamadas en el caso de 6 problemas × 2 oportunidades.
- La cobertura de roles es completa.

Fallan los dos criterios centrales:
- La independencia es nominal. Los tres evaluadores son el mismo agentType, su prompt les manda consultar puntuaciones y quien genera es también quien evalúa.
- El humano en el bucle solo existe en las instrucciones: no hay firma en las compuertas, `en_validacion` se alcanza sin firma y `mision-1-auto` no tiene puntos de control.

Además, `red-team-auto` falla en abierto y `evaluar-oportunidad-auto` no cubre F4 ni F6.

Fortalezas:
- Roles con herramientas acordes a su función. Evidencia: `CLAUDE.md:43-58`; `.claude/agents/critico-red-team.md:4`; `.claude/agents/verificador-citas.md:4`.
- Formatos válidos de Claude Code:
  - `effort: high` está aceptado según `cli.js` 2.1.42 y el binario nativo.
  - Los workflows guardados se invocan como `/nombre` o `Workflow({name})`, según el binario.
  - Evidencia: `.claude/agents/*.md:1-8`; `.claude/workflows/mision-1-auto.js:1-12`.
- Crítico con mandato explícito de refutar y refutación desde varios enfoques. Evidencia: `.claude/agents/critico-red-team.md:10-34`; `.claude/workflows/red-team-auto.js:19-23`, `:34-42`.
- Bucle hasta N acotado a 2 rondas, con aviso explícito si no se alcanza (`.claude/workflows/mision-1-auto.js:104-133`).
- La vía manual está bien diseñada:
  - `disable-model-invocation`.
  - Puntos de control humanos.
  - Campos `decided_by` y `human_signoff`.
  - Motivo obligatorio.
  - Evidencia: `.claude/skills/mision-1/SKILL.md:5`, `:41`, `:75-76`; `src/nutria/db.py:88-92`.

**D3, puntuación 2.** Hay mecanismos ejecutables reales:
- Verificación de DOI y PMID.
- Comprobación estructural de los informes.
- Sello SHA-256.
- Hook.
- Registro con motivo y procedencia.

Pero la garantía central falla en abierto. Con la red bloqueada, que es el estado real de este entorno, un DOI inventado obtiene sello válido. Con red simulada también pasan:
- referencias solo con URL;
- un `[R1]` duplicado;
- años incluidos dentro del DOI;
- un DOI ajeno con título parecido;
- una retractación que no se ha podido comprobar.

Además, el respaldo de cada afirmación solo lo juzga un LLM, y el sello, la caché y la procedencia se pueden falsificar.

Fortalezas:
- Comprobación de existencia, correspondencia y retractación (`src/nutria/verify/citations.py:127-169`; `src/nutria/sources/pubmed.py:60`; `src/nutria/sources/openalex.py:68`).
- Detecta `[PUB]` sin `[Rn]`, `[Rn]` sin entrada en la bibliografía y entradas sin identificador (`src/nutria/reports/check.py:78-119`).
- El sello caduca al editar el informe y el hook lo exige para «revisado» y «final» (`src/nutria/reports/check.py:143-169`; `scripts/hooks/check_research_file.py:30-37`).
- El alta de fuentes falla en cerrado (`src/nutria/cli.py:196-206`). `[PUB]` exige una fuente registrada y todo cambio exige motivo (`src/nutria/db.py:265-269`, `:319-330`).
- Procedencia sin secretos y con SHA-256 de cada respuesta. Evidencia: `src/nutria/provenance.py:27-30`, `:55-64`, `:89-112`; `src/nutria/http.py:130-132`.

**D4, puntuación 2.** Las instrucciones son correctas pero genéricas, y no se encontraron errores fisiológicos claros en los prompts. Los problemas:
- No hay ninguna implementación cuantitativa de nutrición ni de fisiología ruminal.
- El contexto biológico no se impone.
- «Destino digestivo primero» no es una compuerta.
- Hay dos errores verificados en herramientas: la pendiente de St-Pierre y los días de leche que caen en el periodo seco.
- No hay resultados aplicados.

Fortalezas:
- «Destino digestivo primero» está bien formulado (`CLAUDE.md:24-25`; `.claude/agents/fisiologia-bioquimica.md:14-25`; `docs/PROMPT_MAESTRO_v2.md:204-206`).
- Lista de contexto biológico y reglas de extracción propias de la ciencia animal (`CLAUDE.md:19-21`; `.claude/agents/extractor-datos.md:16-24`).
- Los sistemas de racionamiento se identifican con su edición (`.claude/agents/nutricion-formulacion.md:13-22`).
- Metodología correcta:
  - In situ cuenta como in vivo.
  - Los donantes requieren autorización.
  - n = 294 por grupo, comprobado.
  - Evidencia: `docs/PROMPT_MAESTRO_v2.md:321-332`; `src/nutria/stats/power.py:60-71`.
- La rúbrica se ancla a especie, etapa y destino digestivo (`src/nutria/data/rubrica.toml:10-30`). Los problemas propuestos son pertinentes y los taxones NCBI son correctos (`.claude/skills/mision-1/SKILL.md:26-32`; `src/nutria/sources/base.py:12-22`).

**D5, puntuación 2.** El mapa normativo es amplio y en general exacto, y corrige el error del 2015/2283. Los fallos:
- La lógica de clasificación tiene errores normativos: falta el criterio funcional de medicamento, el orden del árbol es incorrecto y el nodo de materia prima es demasiado amplio.
- Faltan el 2019/1381, el art. 11.2 del 1831/2003, las alegaciones permitidas y la guía FEEDAP de 2024.
- Las compuertas no tienen firma ni comprueban la coherencia con la ruta regulatoria.
- Solo 3 de 21 normas están verificadas.
- No se ha hecho ningún triage real.

Fortalezas:
- Mapa normativo con enlaces ELI (`docs/REGULACION_UE.md:44-66`). Tabla de grupos funcionales al día, incluido el 4(e) (`:34-40`, `:49`).
- Lo que se declara verificado sobre piensos dietéticos (2020/354 y 2024/2899) es correcto (`docs/REGULACION_UE.md:52`).
- Triage en F3 (`docs/PROMPT_MAESTRO_v2.md:86`). Reglas prudentes sobre alegaciones, aromatizantes, método analítico y titular (`docs/REGULACION_UE.md:28-30`, `:77-79`).
- Una compuerta «bloqueada» saca la oportunidad del Monte Carlo (`src/nutria/scoring/engine.py:171-176`, `:202-203`). El esquema de seguridad sigue a FEEDAP (`.claude/skills/triage-regulatorio/SKILL.md:34-37`).

**D6, puntuación 3.** El núcleo de metaanálisis es correcto y se comprobó de forma independiente: REML reimplementado en 300 conjuntos aleatorios, con una discrepancia máxima de 2e-6. Falla en lo propio de la nutrición animal:
- Trata el SEM como DE sin aviso.
- La dependencia entre comparaciones solo se maneja en una plantilla de R sin probar.
- Los diseños (crossover, cuadrado latino) solo se controlan por instrucciones.
- St-Pierre tiene un error tipo I inflado.
- Egger se calcula con k ≥ 3.
- HKSJ falla con q = 0.
- No hay medidas para variables binarias ni validación agrupada por estudio.

Fortalezas:
- Estimadores (`src/nutria/stats/meta.py:100-170`, `:197-248`), con dat.bcg:
  - FE −0,4303.
  - REML: tau² 0,3132 y estimación −0,7145.
  - HKSJ [−1,1084; −0,3206].
  - DL y PM idénticos a `statsmodels.combine_effects`.
- Meta-regresión con Knapp-Hartung correcta (`src/nutria/stats/metareg.py:61-135`; `tests/test_meta.py:56-64`).
- Conversión de dispersiones, SMD y ROM según `escalc` (`src/nutria/stats/effect_sizes.py:18-70`). La potencia coincide con statsmodels (`src/nutria/stats/power.py:14-89`).
- Guía específica de la disciplina y avisos honestos (`.claude/agents/estadistico.md:12-32`; `src/nutria/stats/meta.py:221-223`, `:270-271`).

**D7, puntuación 2.** Hay 10 clientes de API reales con una capa HTTP común, pero ninguno se ha probado contra el servicio real. Con respuestas simuladas se reprodujeron estos errores:
- La retractación se da por buena si falla OpenAlex.
- Las claves de API acaban en el sello.
- Sin red, el sello sigue siendo válido.
- Los límites de frecuencia solo valen dentro de un proceso.
- `fuente agregar` falla con DOI que solo están en OpenAlex.
- La procedencia queda incompleta.
- `buscar efsa` no aplica las fechas.

Además, el acceso a OpenAlex está desfasado y las fuentes regulatorias solo se consultan por WebFetch.

Fortalezas:
- Endpoints y campos coherentes con la documentación pública (`src/nutria/sources/*.py`).
- Caché, modo sin red, reintentos con Retry-After y error de proxy explícito (`src/nutria/http.py:78-134`; `src/nutria/config.py:96-105`).
- Procedencia de las consultas a red con las claves ocultas (`src/nutria/provenance.py:55-64`). Los secretos solo se leen del entorno (`src/nutria/config.py:75-79`).
- Documentación honesta de límites y licencias (`README.md:112-114`; `docs/FUENTES.md:3-4`, `:20`, `:43`).

**D8, puntuación 3 (en el límite inferior).** Todos los elementos están implementados y la aritmética es correcta: rNPV recalculado a mano, inversa triangular contrastada con scipy y media PERT. No llega a 4 por estos motivos:
- Las compuertas sin resolver no bloquean.
- Se pueden desbloquear sin firma.
- Las transiciones de estado ignoran las compuertas.
- La perturbación de pesos con alfa = 200 es débil.
- El escenario conservador está mal construido.
- El ROI es incoherente.
- P(rNPV>0) se presta a confusión.
- El criterio económico se puntúa sin modelo.
- Los evaluadores son clones.

Fortalezas:
- Rúbrica anclada de 8 criterios con dos perfiles validados (`src/nutria/data/rubrica.toml:8-135`; `src/nutria/scoring/engine.py:56-88`).
- Incertidumbre triangular por evaluador y P(top-k) reproducible con semilla (`src/nutria/db.py:392-396`; `src/nutria/scoring/engine.py:133-141`, `:188-228`).
- «Bloqueada» funciona (`src/nutria/scoring/engine.py:173-176`, `:202-203`). El rNPV es correcto: −56,8625 a mano frente al mismo valor del código (`src/nutria/econ/rnpv.py:58-72`).
- Presupuesto parcial con escenarios, Monte Carlo y umbral (`src/nutria/econ/farm.py:62-126`). Las dos perspectivas están separadas en código y CLI (`src/nutria/cli.py:616-630`).

**D9, puntuación 2.** La base es real:
- Concurrencia probada: 8 procesos con 10 altas cada uno dan 80 IDs únicos.
- Determinismo con semilla.
- Estadística que reproduce los valores de referencia de metafor.
- Secretos fuera de los ficheros en el camino normal.

Pero hay errores importantes que las 42 pruebas no detectan:
- La clave de API acaba en el sello.
- La comprobación de retractación usa la caché.
- La marca de retractación se puede borrar.
- Hay trazas sin capturar y conversiones silenciosas.
- El sello tiene una condición de carrera (TOCTOU).
- No hay historial de puntuaciones.
- Las altas por lotes no son atómicas.
- Hook y workflows fallan en abierto.
- No hay CI ni fichero de bloqueo de dependencias.

Fortalezas:
- La CLI reproduce dat.bcg (`tests/test_meta.py:8-79`; `src/nutria/stats/meta.py:68-77`).
- Registro concurrente: WAL, `BEGIN IMMEDIATE` y flock (`src/nutria/db.py:105-112`, `:123-141`; `src/nutria/provenance.py:100-112`).
- Monte Carlo determinista con la semilla registrada (`src/nutria/scoring/engine.py:206`; `src/nutria/econ/farm.py:100`; `src/nutria/cli.py:451-452`).
- Las claves de los LLM y de Lens van en cabeceras y fuera de la clave de caché (`src/nutria/llm/router.py:73-75`; `src/nutria/http.py:82-83`). Hay controles de entrada: JSON Schema, CHECK y motivo obligatorio (`src/nutria/cli.py:38-45`; `src/nutria/db.py:319-330`).

**D10, puntuación 3.** Funcionan bien:
- La instalación, probada en un venv limpio con Python 3.11 y 3.13 (43-50 s).
- El diagnóstico.
- La coherencia entre documentación y código.

Falla en tres puntos:
- La honestidad de las garantías operativas: el hook «que bloquea» y el «Sello válido» sin verificación real.
- El coste no está cuantificado: entre 141 y 263 subagentes por misión automática.
- Hay flujos documentados que no funcionan como se describen.

Fortalezas:
- Instalación y requisito de versión correctos (`README.md:21-45`; `pyproject.toml:10-20`). Arranque guiado con diagnósticos claros (`src/nutria/cli.py:48-51`, `:98-119`; `src/nutria/http.py:106-109`).
- Inventario, opciones y rúbrica coinciden entre documentación y código; `docs/RUBRICA.md` es idéntico a `nutria puntuar rubrica --markdown`.
- Límites declarados (`README.md:112-117`) y fuentes explicadas una a una (`docs/FUENTES.md:7-20`, `:39-57`, `:74-80`).

**D11, n/a.** No hay resultados de investigación: `research/` solo contiene 7 ficheros `.gitkeep`, hay un único commit y `nutria.zip` contiene lo mismo.

Fortalezas:
- Los ejemplos no se presentan como resultados (`ejemplos/oportunidad_ejemplo.json:2`; `ejemplos/econ_ganadero_ILUSTRATIVO.toml:1`, `:30`; `README.md:115`).
- Una muestra de 7 referencias (2 de ellas marcadas «pendiente») y del Reglamento 2024/2899, comprobada con WebSearch, coincide con lo citado (`docs/REVISION_PROMPT_ORIGINAL.md:260-272`).
- El banco semilla da respuestas correctas (`evals/dominio_semilla.jsonl:6`, `:7`, `:9`).

## 4. Hallazgos CONFIRMADOS (nutria), ordenados por gravedad

Los 111 hallazgos de esta sección superaron la verificación adversarial, que buscaba refutarlos leyendo los ficheros reales. La gravedad que se indica es la que quedó tras esa verificación.

Formato de cada hallazgo:

- **Hallazgo:** qué falla.
- **Evidencia:** citas `ruta:línea` y, cuando la hubo, la reproducción. Todas las reproducciones se hicieron en copias temporales, nunca en `/home/user/nutria`.
- **Verificación:** matices, agravantes o atenuantes que aportó quien intentó refutar el hallazgo.

Varios hallazgos describen el mismo defecto desde dimensiones distintas. Se mantienen por separado para conservar la trazabilidad, y el plan de la sección 7 los agrupa.

### 4.1 Gravedad ALTA (21)

#### 4.1.1 Antialucinación: sello, verificación de referencias y hook

**D1-1 (D1). El sello certifica referencias que nadie ha verificado.**

- **Hallazgo:**
  - Sin red, cada referencia que no se puede verificar queda como simple «aviso».
  - El sello guarda `en_linea=true`, que refleja lo que se pidió y no lo que se consiguió, y se considera válido si hay 0 errores.
  - La CLI informa de «referencias verificadas en línea».
  - Contradice `README.md:10` y `docs/REVISION_PROMPT_ORIGINAL.md:25`, `:58-60`.
- **Evidencia:**
  - `src/nutria/reports/check.py:133-135`, `:147-155`, `:158-169`; `src/nutria/cli.py:634-650`.
  - Reproducción con el DOI inventado `10.9999/doi.inventado.2021.001` en un informe con «estado: revisado»:
    - `nutria --offline informe comprobar ... --online --sellar` responde «0 error(es), 1 aviso(s) | referencias verificadas en línea» y termina con código 0.
    - `informe sello` responde «Sello válido».
    - El hook no bloquea.
- **Verificación:** el fallo es más amplio de lo descrito.
  - No hace falta `--offline`: basta cualquier fallo de red, como un proxy bloqueado, un 429 persistente o un 404 de Crossref seguido de un fallo de OpenAlex.
  - Decir que ocurre «en silencio» exagera un poco, porque se imprime `[AVISO]`. Aun así, todas las señales que usan la máquina y el humano dan por buena una referencia no verificada.
  - Con red operativa, el mecanismo funciona.

**D3-1 (D3). Con el proxy real, la verificación en línea falla en abierto.**

- **Hallazgo:** es el mismo defecto, reproducido sin `--offline`.
  - La política del sistema es incoherente: `fuente agregar` sí falla en cerrado (`src/nutria/cli.py:198-199`).
  - `.claude/settings.json:4` permite `Bash(nutria *)` sin pedir confirmación.
- **Evidencia:**
  - `src/nutria/reports/check.py:133-135`, `:149`, `:158-169`; `src/nutria/cli.py:644`, `:648-649`.
  - T1.md, con el DOI inventado:
    - Resultado: «0 error(es), 1 aviso(s) | referencias verificadas en línea», «Sello válido» y el hook termina con código 0.
    - El `.verificacion.json` guarda `status: error_red` y `ok=false` para esa referencia, pero nadie usa ese dato.
- **Verificación:**
  - El propio error de proxy aconseja usar `--offline` (`src/nutria/http.py:106-109`), que lleva al mismo resultado.
  - Ninguna prueba cubre el fallo de red (`tests/test_report_check.py:31-38`).
  - `revisado_por_humano: null` no impidió el paso.

**D7-3 (D7). Sin red, el sello declara «verificado en línea».**

- **Hallazgo:** sin red, o con errores de red, la verificación se queda en avisos y el sello lleva `en_linea:true`. El evento de procedencia `sello_informe` solo guarda el número de errores.
- **Evidencia:** `src/nutria/reports/check.py:133-135`, `:149`, `:158-169`; `src/nutria/cli.py:640-651`. Reproducido con `python -m nutria --offline informe comprobar nota.md --online --sellar` y un DOI inventado.
- **Verificación:**
  - Se da en un escenario realista que el propio sistema prevé (`src/nutria/cli.py:117`; `CLAUDE.md:86`).
  - Atenuante: el JSON del sello conserva `avisos=1` y el estado `offline`.

**D10-1 (D10). La garantía antialucinación que anuncia el README no se cumple.**

- **Hallazgo:**
  - `README.md:10` promete un hook que «bloquea informes con referencias no verificables».
  - «Garantías y límites» (`README.md:107-117`) no menciona que esto se degrada sin red.
  - El error de red recomienda `--offline` (`src/nutria/http.py:108`).
- **Evidencia:**
  - O-0002.md, con 20 secciones, «estado: final» y un DOI inventado, obtiene «Sello válido (…, 1 aviso(s))» y el hook no devuelve nada.
  - El esqueleto vacío que genera `informe nuevo` también obtiene «Sello válido».
  - Además: `.claude/settings.json:38-45`; `scripts/hooks/check_research_file.py:29-34`.
- **Verificación:**
  - El fallo es más amplio: ocurre también con red parcial, con 429/5xx, con timeouts y con `nutria verificar <informe.md>`.
  - `docs/PROMPT_MAESTRO_v2.md` y la skill `informe` prometen «todas sus referencias verificadas».
  - Lo del esqueleto vacío es menor.

**D3-2 (D3). Las referencias que solo tienen URL nunca se verifican.**

- **Hallazgo:**
  - Solo generan un aviso, y el sello es válido aunque haya red.
  - Es el camino previsto para toda la normativa UE (EUR-Lex) y la literatura gris, y también una vía de escape para referencias inventadas.
- **Evidencia:**
  - `src/nutria/reports/check.py:111-116`; `src/nutria/verify/citations.py:172-177`; `docs/PROMPT_MAESTRO_v2.md:387-388`.
  - «[R1] Inventado A. 2022 … https://www.ejemplo-inventado.org/articulo-123» obtiene sello válido, y `nutria verificar H6.md --sellar` termina con código 0.
- **Verificación:**
  - `crossref.search_bibliographic` existe pero no se usa en ningún sitio.
  - `fuente agregar` exige DOI o PMID.
  - El propio `docs/REVISION_PROMPT_ORIGINAL.md:264` cita St-Pierre 2001 solo con la URL de la portada de la revista y la marca como «verificada».

**D3-3 (D3). Ningún control ejecutable comprueba que la fuente respalde la afirmación.**

- **Hallazgo:**
  - En una afirmación [PUB], `cita_textual` y la ubicación son opcionales y nunca se comparan con el resumen, aunque el código ya sabe obtenerlo.
  - El veredicto de `verificador-citas` es texto libre y no queda ligado al sello.
  - En `evaluar-oportunidad-auto` solo se revisan las [PUB] «clave». El informe final del director no se vuelve a revisar, y `sello_valido` lo declara el propio LLM.
- **Evidencia:**
  - `src/nutria/verify/citations.py:7-9`; `src/nutria/data/schemas/afirmacion.schema.json:5`, `:12-13`; `src/nutria/db.py:262-279`; `src/nutria/reports/check.py:147-155`.
  - `.claude/workflows/evaluar-oportunidad-auto.js:35`, `:56`, `:61-76`; `.claude/agents/verificador-citas.md:16-18`, `:25`.
  - Contradice `docs/PROMPT_MAESTRO_v2.md:389-391`.
- **Verificación:** reproducido. Un informe «revisado» cuya única [PUB] contradice el resumen registrado de su fuente obtiene sello válido y el hook lo acepta.

**D3-4 (D3). La regla de etiquetado apenas se impone.**

- **Hallazgo:**
  - `CLAUDE.md:13` exige etiquetar toda afirmación relevante, pero solo es error una [PUB] sin [Rn] en la misma línea.
  - Pasan sin ninguna incidencia:
    - citas autor-año que no están en la bibliografía;
    - una [PUB] dentro de una cita «>» o de un encabezado;
    - «[pub]» en minúsculas;
    - varias [PUB] con un único [Rn];
    - cifras en % o en L/d.
  - La expresión regular de unidades no detecta «%» por el `\b` final.
- **Evidencia:** `src/nutria/reports/check.py:32-33`, `:91`, `:97`, `:101-104`. Los casos S1-S7 se reprodujeron; el caso de control S0 sí da error.
- **Verificación:**
  - El «%» solo se detecta si va pegado a una letra o seguido de «/d».
  - Un informe que reúne todos estos casos obtiene «Sello válido, 0 aviso(s)».

**D3-5 (D3). El «bloqueo automático» es débil y falla en abierto.**

- **Hallazgo:**
  - Es un hook PostToolUse: cuando actúa, el fichero ya está escrito.
  - Solo se dispara con Write, Edit y MultiEdit, así que no ve lo que se escribe con la CLI ni con Bash.
  - Solo revisa `research/informes/*.md` y `research/{oportunidades,hipotesis}/*.json`.
  - No verifica en línea ni comprueba `revisado_por_humano`.
  - Si el sello está corrupto, termina con código 1, que no bloquea.
  - Si el `python3` del PATH no tiene `requests`, el ImportError hace que deje pasar todo.
- **Evidencia:**
  - `.claude/settings.json:38-47`; `scripts/hooks/check_research_file.py:9-10`, `:30-34`, `:63-82`; `README.md:10`.
  - Reproducido:
    - H1 (sello corrupto): código 1 y sin `block`.
    - H2 y H2b (sin sello): no bloquean.
    - H3 (`python3 -S`): código 0 sin salida.
    - H6b (plan): no se comprueba.
    - T1: no bloquea.
- **Verificación:**
  - Precisión: el valor de «estado» sí admite mayúsculas, porque se aplica `.lower()`. Lo que debe ir en minúsculas y en la columna 0 es la clave «estado:», y el valor no puede ir entre comillas.
  - Agravante: el hook acepta un sello falsificado con Write.

**D7-1 (D7). La retractación depende de OpenAlex y deja de comprobarse sin aviso.**

- **Hallazgo:**
  - `crossref.parse` solo marca un trabajo como retractado si el título empieza por «RETRACTED» o «RETRACTION:». El campo `update-to` solo etiqueta los avisos.
  - Si OpenAlex lanza un NetworkError, queda `retracted=None` y `ok=True`, y `check_report_online` no emite nada.
- **Evidencia:**
  - `src/nutria/sources/crossref.py:38-49`; `src/nutria/verify/citations.py:95-97`, `:146-151`; `src/nutria/reports/check.py:131-139`.
  - Con OpenAlex bloqueado:
    - `verificar --doi` responde «verificada» (código 0).
    - El informe tiene 0 errores y 0 avisos, y recibe «Sello válido».
  - Con OpenAlex accesible, el mismo DOI sale «RETRACTADO» (código 2).
- **Verificación:**
  - El alcance es mayor: también callan los 401, 403, 409, 429, 404 y el modo sin red.
  - Según su documentación (consultada por búsqueda web), Crossref ya marca la retractación en `updated-by`, un campo que el código no lee.

**D9-2 (D9). La retractación falla en abierto y se sirve desde caché antigua.**

- **Hallazgo:**
  - Además del caso D7-1, la verificación reutiliza respuestas guardadas en caché durante 30 días, sin límite en modo sin red, y no hay forma de desactivarlo.
  - Aun así, el informe se sella como «verificado en línea».
- **Evidencia:**
  - `src/nutria/verify/citations.py:95-97`, `:146-151`; `src/nutria/http.py:86-92`; `src/nutria/config.py:28`.
  - Reproducido: un trabajo no retractado el día 0 que OpenAlex marca después como retractado.
    - `informe comprobar --online --sellar` hace 0 peticiones, imprime «referencias verificadas en línea» y sella como válido.
    - Sin caché, sale «[ERROR] … está RETRACTADO».
- **Verificación:**
  - Ninguna prueba cubre el caso `None` ni la caché (`tests/test_citations.py:28-40`).
  - Atenuantes parciales: el prefijo «RETRACTED» en el título y la vía PubMed cuando hay PMID.

#### 4.1.2 Compuertas y humano en el bucle

**D1-2 (D1). La frontera regulatoria y las compuertas no se hacen cumplir.**

- **Hallazgo:**
  - La ruta `fuera_de_alcance_medicamento` se guarda, pero nada la usa.
  - Sin compuertas registradas, todas valen `no_resuelta`, y una oportunidad CONDICIONAL se ordena igual que una APTA.
  - `puntuar compuerta` no admite `--firma-humana`.
  - Cualquier agente puede marcar cualquier compuerta.
  - El estado de registro «bloqueada» no saca la oportunidad del ranking.
  - Una candidata pasa a `en_validacion` sin firma.
- **Evidencia:**
  - Código: `src/nutria/db.py:293-301`, `:318-330`, `:417-436`; `src/nutria/scoring/engine.py:172`, `:177-180`, `:230-233`; `src/nutria/cli.py:444`, `:854-859`.
  - Reglas que se incumplen: `CLAUDE.md:27-28`, `:38-39`; `docs/PROMPT_ORIGINAL.md:105`.
  - Reproducido:
    - Un «producto para tratar la mastitis clínica», con ruta `fuera_de_alcance_medicamento`, queda 2.º como CONDICIONAL.
    - Pasa a APTA cuando `--agente economia-mercado-pi` marca la compuerta regulatoria como superada.
    - Pasar `--firma-humana` a la orden de compuerta da «unrecognized arguments».
- **Verificación:**
  - Una compuerta «bloqueada» sí se aplica, y hay prueba de ello.
  - Llamarlo «retroceso» es discutible. Aun así, la v2 rebaja «no resuelta = bloqueada» a «condicional» aunque dice conservar el bloqueo (`docs/REVISION_PROMPT_ORIGINAL.md:33`).

**D2-4 (D2). Las compuertas las deciden y registran agentes, sin validación humana, y cuentan al instante.**

- **Hallazgo:** las instrucciones se contradicen y el código no lo resuelve.
  - `CLAUDE.md:38-39` exige validación humana.
  - Pero `CLAUDE.md:55` dice que `regulacion-seguridad` «decide» las compuertas.
  - Su prompt incluye «Qué decides» y la orden de registrarlas (`.claude/agents/regulacion-seguridad.md:13`, `:36-37`).
  - `mision-1-auto` ordena a 4 agentes que las registren (`.claude/workflows/mision-1-auto.js:136-141`).
  - La tabla de compuertas no tiene campo de firma (`src/nutria/db.py:80-86`, `:417-435`).
- **Evidencia:** `--agente regulacion-seguridad puntuar compuerta O-0001 seguridad bloqueada` deja O-0001 como BLOQUEADA sin ninguna firma.
- **Verificación:** el hallazgo se queda corto.
  - Si un agente marca las 5 compuertas como superadas, la oportunidad queda APTA.
  - `.claude/workflows/mision-1-auto.js:214` dice al usuario que las compuertas requieren `--firma-humana`, una opción que no existe.
  - `tests/test_registry_cli.py:21-25` da por correcta una compuerta registrada sin firma.

**D5-8 (D5). Las compuertas de seguridad y regulatoria no están protegidas.**

- **Hallazgo:**
  - El actor lo declara él mismo, con `--agente` o `NUTRIA_AGENT`.
  - Basta un motivo cualquiera; no hay firma ni control de rol.
  - El cambio cuenta de inmediato y no se comprueba la coherencia con la ruta regulatoria.
- **Evidencia:**
  - `src/nutria/cli.py:435-438`, `:854-859`; `src/nutria/db.py:417-435`; `src/nutria/provenance.py:51-52`; `src/nutria/scoring/engine.py:171-180`; `src/nutria/data/schemas/oportunidad.schema.json:17-20`.
  - Reproducido:
    - O-0001, con ruta `fuera_de_alcance_medicamento`, quedó APTA cuando `economia-mercado-pi` puso las 5 compuertas en «superada» con el motivo «x».
    - `sostenibilidad-bienestar` revirtió una compuerta regulatoria bloqueada con el motivo «y».
- **Verificación:** atenuante: cada cambio queda en la tabla `decisions`, así que se puede rastrear después, pero no se impide.

**D8-1 (D8). Las compuertas sin resolver no bloquean.**

- **Hallazgo:**
  - Una oportunidad sin ninguna compuerta evaluada queda CONDICIONAL.
  - Aun así compite en el Monte Carlo, se ordena junto a las APTA y puede entrar en el top 3 con plan de investigación.
  - Contradice `docs/PROMPT_ORIGINAL.md:105` y `CLAUDE.md:27-28`.
- **Evidencia:**
  - `src/nutria/scoring/engine.py:172`, `:177-180`, `:202-203`, `:230-233`; `.claude/workflows/mision-1-auto.js:179`.
  - Reproducido:
    - O-0001, sin compuertas, queda 1.ª con P(top1)=1,00, por delante de una APTA con las 5 compuertas superadas.
    - Con 4 candidatas, dos CONDICIONALES ocupan los puestos 1 y 2, y la APTA O-0004 queda fuera del top 3 (P(top3)=0,01).
- **Verificación:**
  - La regla «las CONDICIONALES no pueden pasar a validación» (`src/nutria/scoring/engine.py:259-260`) no está implementada (`src/nutria/db.py:319-330`).
  - `docs/REVISION_PROMPT_ORIGINAL.md:33` afirma que el bloqueo «se conserva».

**D8-2 (D8). Las decisiones de compuerta no exigen firma ni evidencia, y cualquier agente puede revertirlas.**

- **Evidencia:**
  - `src/nutria/cli.py:854-859` (`--evidencia` es opcional, `:859`) y `:436-437`; `src/nutria/db.py:417-435`.
  - Reproducido:
    - Se ejecuta `--agente regulacion-seguridad … seguridad bloqueada` y después `--agente cualquiera … seguridad superada --motivo x`.
    - O-0001 vuelve al puesto 1 con P(top1)=1,00.
    - `decision listar` muestra los dos cambios, ambos sin firma.
- **Verificación:** es peor de lo descrito.
  - Cumplir `CLAUDE.md:38-39` es imposible.
  - Al revertir, se sobrescribe la fila y se pierde la evidencia del bloqueo.
  - Los cambios de compuerta no llegan a `procedencia.jsonl`.
  - Se aceptan cambios anónimos (`decided_by='cli'`).

#### 4.1.3 Independencia de la evaluación y cobertura de los workflows

**D1-3 (D1). La independencia de los evaluadores es solo nominal.**

- **Hallazgo:**
  - Los «tres evaluadores ciegos» son el mismo agentType, `director-cientifico` (opus, mismo prompt); solo cambia una cadena de «enfoque».
  - En la evaluación profunda, el director redacta el informe con la decisión propuesta y los evaluadores leen ese informe.
  - En la misión 1, el director redacta el efecto mínimo relevante y el criterio de falsación de cada hipótesis.
  - La segunda opinión de otra familia de modelos está desactivada.
  - La ceguera depende de un flag opcional.
- **Evidencia:**
  - Workflows: `.claude/workflows/mision-1-auto.js:142-146`, `:166-172`, `:34-38` frente a `:151-153`; `.claude/workflows/evaluar-oportunidad-auto.js:62-68`, `:71-76`.
  - Skill y plantilla: `.claude/skills/mision-1/SKILL.md:65-66`; `src/nutria/reports/plantilla_informe.md:21`.
  - Reglas y diagnóstico: `CLAUDE.md:31-32`; `docs/REVISION_PROMPT_ORIGINAL.md:42-43`.
  - Código: `src/nutria/data/modelos.toml:21-75`; `src/nutria/cli.py:397-399`.
- **Verificación:**
  - Agravantes:
    - El prompt del director le ordena consultar `oportunidad ver --json` sin el flag, y también `puntuar ranking` (`.claude/agents/director-cientifico.md:23-24`).
    - Con `--sin-puntuaciones` se siguen viendo las decisiones.
    - Tres nombres distintos bastan para silenciar el aviso de «un solo evaluador».
  - Matiz: cada `agent()` tiene su propio contexto. Esto corrige la parte de «mismo contexto» del diagnóstico C1, pero no la de «mismo modelo y mismo prompt».

**D2-2 (D2). Quien genera, evalúa.**

- **Hallazgo:**
  - En `evaluar-oportunidad-auto`, `director-cientifico` redacta la síntesis y el informe con «Propón una decisión», y tres `director-cientifico` lo puntúan leyendo ese mismo informe.
  - En `red-team-auto`, el director elige qué afirmaciones se someten a refutación.
  - En `mision-1-auto`, el director redacta el JSON de cada oportunidad que luego se puntúa.
- **Evidencia:**
  - `.claude/workflows/evaluar-oportunidad-auto.js:62-67` (`:65`), `:71-75` (`:73`); `.claude/workflows/red-team-auto.js:26-29`; `.claude/workflows/mision-1-auto.js:150-154`, `:166-171`.
  - Incumple `docs/PROMPT_MAESTRO_v2.md:254`, `:269-270` y `CLAUDE.md:31-32`.
- **Verificación:**
  - La «puntuación ciega» que se anuncia (`.claude/workflows/evaluar-oportunidad-auto.js:3`, `:10`) no lo es.
  - Por separado, los casos de `red-team-auto` y `mision-1-auto` serían de gravedad media; la gravedad alta se mantiene por `evaluar-oportunidad-auto`.

**D2-7 (D2). La evaluación profunda automática no puede usar a 3 especialistas clave.**

- **Hallazgo:**
  - El enum de especialistas de `evaluar-oportunidad-auto` excluye al investigador bibliográfico, al extractor y al estadístico.
  - En consecuencia, puntúa sin revisión de evidencia (F4), sin doble extracción y sin metaanálisis (F6).
  - El README la presenta como «Lo mismo» que `/oportunidad`, que sí incluye esos pasos.
- **Evidencia:** `.claude/workflows/evaluar-oportunidad-auto.js:3`, `:24-25`, `:27`; `.claude/skills/oportunidad/SKILL.md:14-15`, `:19-20`; `README.md:65-66`; `CLAUDE.md:91-94`.
- **Verificación:**
  - Confirmado leyendo el código; el workflow no se ejecutó.
  - Afecta al criterio «evidencia», el de más peso (20).
  - Matiz: los especialistas tienen búsqueda web, y `verificador-citas` revisa sus [PUB].

#### 4.1.4 Dominio: regulación, nutrición y estadística

**D5-1 (D5). La frontera con el medicamento veterinario está incompleta.**

- **Hallazgo:**
  - Solo detecta el medicamento por la alegación o el efecto buscado («prevenir, tratar o curar»).
  - Omite el criterio funcional del Reglamento (UE) 2019/6, art. 4.1.b: es medicamento lo que restaura, corrige o modifica funciones fisiológicas mediante una acción farmacológica, inmunológica o metabólica.
  - El remedio que propone, rediseñar la alegación hacia un efecto fisiológico, no saca al producto de esa categoría.
  - La revisión llama a los estabilizadores de las condiciones fisiológicas la «categoría natural» para productos contra la inflamación.
  - El sistema busca mecanismos farmacológicos (`ChEMBL --mecanismos`).
- **Evidencia:** `docs/REGULACION_UE.md:12-13`; `.claude/skills/triage-regulatorio/SKILL.md:12-14`; `.claude/agents/regulacion-seguridad.md:15-17`; `CLAUDE.md:29-30`; `docs/REVISION_PROMPT_ORIGINAL.md:79-82`; `.claude/agents/compuestos-toxicologia.md:17-18`.
- **Verificación:**
  - EUR-Lex estaba bloqueado, así que el texto legal no se consultó directamente; la premisa coincide con el conocimiento del verificador.
  - Como la firma humana de las compuertas no existe en código, marcar por error una compuerta como superada lleva la oportunidad a APTA.

**D4-2 (D4). Los sistemas de racionamiento solo se citan; no están implementados.**

- **Hallazgo:**
  - NASEM 2021, INRAE y FEDNA no tienen implementación. Faltan:
    - predicción de la ingestión;
    - energía (ENl/UFL) y proteína (PM/PDI);
    - evaluación de raciones y fermentación ruminal;
    - tablas de composición y optimizador de fórmulas.
  - Tampoco hay conversión de dosis entre g/animal/d, mg/kg de MS y g/t.
  - El modelo del ganadero solo admite `dosis_g_dia` (`src/nutria/econ/farm.py:55`).
- **Evidencia:** `.claude/agents/nutricion-formulacion.md:13-14`; `docs/PROMPT_MAESTRO_v2.md:132`, `:207-209`; `docs/FUENTES.md:59-64`; `docs/PROMPT_ORIGINAL.md:74`; `CLAUDE.md:106`.
- **Verificación:** el problema es más grave de lo descrito.
  - Con `dosis_mg_kg_ms=500`, el modelo da coste 0, ROI NaN y umbral 0, sin ningún aviso.
  - Ninguna herramienta calcula el coste por tonelada.
  - La trazabilidad hace corresponder «Modelización» solo con consultas moleculares.

**D6-1 (D6). El SEM se trata como DE sin avisar.**

- **Hallazgo:**
  - Si el CSV no trae la columna `tipo_disp`, `meta analizar` trata la dispersión como DE sin avisar (`src/nutria/cli.py:505`).
  - `extraccion exportar` escribe `source_id`, `mean_trt` y `dispersion_type` (`src/nutria/db.py:32-45`; `src/nutria/cli.py:346-349`), nombres que el análisis no reconoce.
- **Evidencia:** 3 estudios con SEM combinado, REML con prueba z:

  | Caso | EE | p |
  |---|---|---|
  | Con `tipo_disp` | 0,4762 | 0,053 |
  | Sin la columna | 0,1503 | 1,2e-9 |

  - Con SMD, g pasa de 0,475 a 1,478.
  - El CSV exportado, sin tocar, provoca un TypeError sin capturar.
- **Verificación:**
  - Con MD, REML y HKSJ, la estimación combinada apenas cambia. Sí cambian la prueba z por defecto, la sensibilidad sin HKSJ, la SMD y la heterogeneidad.
  - Si el artículo no indica el tipo de dispersión, la regla de no imputar deja la celda vacía y el análisis imputa SD sin decir nada.
  - D1-4 y D10-6 describen el mismo defecto con gravedad media.

### 4.2 Gravedad MEDIA (82)

#### D1. Detección y corrección de los fallos del prompt original (10)

**D1-4. El SEM combinado se trata como DE por defecto, y así se anula su conversión (criterio 6).**
- **Problema:** si falta `tipo_disp`, la dispersión se toma como DE (`src/nutria/cli.py:505`). Además, `extraccion exportar` escribe `dispersion_type`/`mean_trt`, mientras que `meta analizar` lee `tipo_disp`/`media_trt` (`src/nutria/db.py:229`; `src/nutria/cli.py:343-349` frente a `:492-510`).
- **Reproducción:**
  - Sin `tipo_disp`: EE 0,1497, p = 2,6e-9, I² 19,5 %.
  - Con `SEM_combinado`: EE 0,4762, p = 0,056, I² 0 %.
- **Verificación:** se rebaja de alta a media.
  - La skill pide incluir la columna (`.claude/skills/metaanalisis/SKILL.md:11-12`).
  - El fallo solo es silencioso si las dos dispersiones están rellenas.
  - Con HKSJ, el efecto combinado cambia poco.
  - `exportar-r` también trata `disp_*` como DE (`src/nutria/cli.py:576-580`).

**D1-5. El hook y el sello no cubren lo que prometen (criterio 2).**
- **Problema:**
  - El hook es PostToolUse y solo vigila Write, Edit y MultiEdit, y solo informes y JSON.
  - Si falla un import, deja pasar todo.
  - No comprueba `revisado_por_humano` ni las líneas que empiezan por «>».
  - El sello no está autenticado.
  - Los entregables de la misión 1 (`research/revisiones/`, `research/planes/`) no pasan por ninguna verificación automática.
- **Evidencia:** `.claude/settings.json:38-40`; `scripts/hooks/check_research_file.py:30-34`, `:76-80`, `:81-82`; `src/nutria/reports/check.py:91`, `:158-169`; `.claude/skills/informe/SKILL.md:18-19`.
- **Verificación:**
  - En el caso normal funciona: bloquea un [PUB] sin [Rn] y un informe «revisado» sin sello.
  - Los entregables de M1 son documentos intermedios.

**D1-6. La frontera se reduce a la alegación terapéutica, y el árbol manda a «materia prima» todo lo que cubre necesidades nutricionales (criterios 3 y 5).**
- **Problema:**
  - Omite el criterio funcional del art. 4.1.b del Reglamento (UE) 2019/6.
  - Vitaminas, oligoelementos y aminoácidos (art. 5.3.d del Reglamento 1831/2003) caerían en la rama equivocada.
- **Evidencia:** `docs/REGULACION_UE.md:12-13`, `:16-18`; `.claude/skills/triage-regulatorio/SKILL.md:12-18`; `CLAUDE.md:29-30`; `.claude/agents/regulacion-seguridad.md:15-17`.
- **Verificación:**
  - La condición de «animales sanos» de los estabilizadores solo aparece en la revisión.
  - La parte del árbol, por sí sola, sería de gravedad baja.
  - El texto legal solo se contrastó con fuentes secundarias.

**D1-7. `descarte_rapido` se genera pero no se usa (criterio 3).**
- **Problema:** `evaluar-oportunidad-auto` lanza a la vez a todos los especialistas, sin una puerta previa y sin comprobar si ya hay compuertas bloqueadas.
- **Evidencia:** `.claude/workflows/evaluar-oportunidad-auto.js:27`, `:41`, `:47-59`. Contradice `.claude/skills/oportunidad/SKILL.md:12-13`, `docs/PROMPT_MAESTRO_v2.md:78-79` y `.claude/agents/director-cientifico.md:26-27`.
- **Verificación:**
  - Supone hasta unas 29 ejecuciones de agente por oportunidad.
  - Atenúa que `mision-1-auto` sí hace el triaje F3 antes de puntuar.

**D1-8. La dependencia entre comparaciones no se modela ni se avisa (criterio 6).**
- **Problema:**
  - Las filas de un mismo estudio se tratan como independientes.
  - `rma.mv` solo aparece en un script R que no se prueba, y R no está instalado.
  - Egger se calcula desde k = 3.
- **Evidencia:** `src/nutria/cli.py:492-510`, `:536-543`, `:571-594`; `src/nutria/stats/meta.py:251-271`.
- **Reproducción:** tres dosis frente a un control común, más otros 2 estudios, dan «k = 5».
- **Verificación:**
  - Con la covarianza del control compartido, el peso del estudio A baja del 52,6 % al 35,7 % y el IC95 pasa a incluir 0.
  - HKSJ usa gl = filas − 1, cuando deberían ser los estudios.
  - Matices:
    - `meta stpierre` sí trata el estudio como efecto aleatorio en Python.
    - Egger avisa cuando k < 10.

**D1-9. En `mision-1-auto` se descarta el mapa de lo que ya existe (criterio 10).**
- **Problema:**
  - `soluciones_actuales` no es obligatorio y se pierde en la consolidación.
  - Los generadores solo reciben id, título, especie, etapa y mecanismos.
  - La novedad se comprueba después de generar.
  - `nutria patentes` sin Lens solo construye URLs.
  - No hay un mapa de productos autorizados.
  - No hay control humano tras M1a.
- **Evidencia:** `.claude/workflows/mision-1-auto.js:24-32`, `:79-102`, `:176-200`; `src/nutria/sources/patents.py:14-21`; `src/nutria/cli.py:320-327`; `docs/PROMPT_MAESTRO_v2.md:416-420`.
- **Verificación:**
  - El workflow nunca pasa `--lens`.
  - Las oportunidades marcadas «existente» se descartan solo en memoria, sin registrar el motivo.
  - La vía manual lo corrige en parte.

**D1-10. La rúbrica vuelve a mezclar las perspectivas económicas (criterio 9).**
- **Problema:**
  - `potencial_economico (ganadero y empresa)` es una sola puntuación, con anclas del tipo «ROI positivo en el escenario conservador **o** mercado grande».
  - No hay ningún vínculo entre el precio en el modelo del ganadero y las ventas en el de la empresa.
  - P(rNPV>0) es fácil de malinterpretar.
- **Evidencia:** `src/nutria/data/rubrica.toml:93-102`; `docs/RUBRICA.md:90-98`; `.claude/skills/evaluacion-economica/SKILL.md:27`; `src/nutria/econ/farm.py:45-59`; `src/nutria/econ/rnpv.py:58-72`, `:92-96`.
- **Verificación:**
  - Ningún ancla usa el rNPV y las anclas se solapan.
  - Pasar de 0,25 a 0,75 mueve 7,5 puntos del total.

**D1-11. La confidencialidad solo se aplica en el router multimodelo, que está inactivo y por defecto usa sensibilidad «normal» (criterio 11).**
- **Problema:**
  - Claude figura como «standard», pero toda la orquestación pasa por Claude Code.
  - WebSearch está preaprobado.
  - `--firma-humana` es texto libre.
  - No hay ningún campo para la contribución inventiva humana.
  - No se menciona el Reglamento (UE) 2019/1381.
- **Evidencia:** `src/nutria/llm/router.py:124-126`; `src/nutria/cli.py:847`, `:876`, `:961`; `src/nutria/data/modelos.toml:8-31`; `.claude/settings.json:8`; `src/nutria/data/schemas/hipotesis.schema.json:24`.
- **Reproducción:** se registró «firma: Dra. Inventada».
- **Verificación:**
  - 4 de los 14 agentes no tienen WebSearch.
  - Hacer una búsqueda no equivale a una divulgación en sentido de patentes.
  - El argumento sobre `procedencia.jsonl` en git es débil.

**D1-12. Los requisitos de validación no llegan a quien redacta y revisa los planes (criterio 12).**
- **Problema:**
  - Donantes o matadero, Directiva 2010/63/UE, in situ como in vivo, FEEDAP y BPL no aparecen en el prompt del director, ni en la orden del plan, ni en la lista del crítico.
  - La compuerta ética se decide en M1c, antes de que exista ningún plan.
  - Falta la notificación de estudios a EFSA (art. 32 ter del Reglamento 178/2002, introducido por el 2019/1381).
- **Evidencia:** `docs/PROMPT_MAESTRO_v2.md:318-335`; `.claude/agents/director-cientifico.md:13-19`; `.claude/workflows/mision-1-auto.js:140`, `:184-200`; `.claude/agents/critico-red-team.md:13-28`.
- **Verificación:**
  - Existe un camino indirecto y genérico (`CLAUDE.md` → §3, fase F10).
  - La notificación a EFSA solo se confirmó con fuentes secundarias.

**D1-13. «Destino digestivo primero» es solo una instrucción (criterio 7).**
- **Problema:**
  - Hay clientes de dianas moleculares, pero nada para degradación ruminal, biohidrogenación, tasa de paso ni NASEM/INRAE, que son las prioridades 1 y 2 del método.
  - En `mision-1-auto`, la plausibilidad se puntúa sin ningún paso de fisiología.
- **Evidencia:** `src/nutria/sources/`; `docs/PROMPT_MAESTRO_v2.md:204-213`; `.claude/workflows/mision-1-auto.js:136-141`; `src/nutria/data/rubrica.toml:24-30`.
- **Reproducción:** una oportunidad con un compuesto libre, sin protección ruminal y con plausibilidad 1 quedó APTA/Prioridad.
- **Verificación:**
  - Exagera decir que solo depende del crítico: el prompt del director también lo pide.
  - PubChem no es un cliente de dianas.

#### D2. Arquitectura multiagente y orquestación (8)

**D2-1. El propio prompt del evaluador rompe la ceguera, y el código no la impone.**
- **Problema:**
  - `.claude/agents/director-cientifico.md:23-24` manda ejecutar `oportunidad ver --json` (sin `--sin-puntuaciones`) y `puntuar ranking` «antes de opinar».
  - `evaluador` es texto libre y se guarda por nombre (upsert), así que un evaluador puede sobrescribir a otro.
- **Evidencia:** `src/nutria/cli.py:398-399`, `:430`; `src/nutria/db.py:397-401`; `src/nutria/scoring/engine.py:195-198`.
- **Reproducción:**
  - `ver --json` devuelve 16 puntuaciones ajenas.
  - `--agente critico-red-team` sobrescribe las puntuaciones de `evaluador-economico`.
- **Verificación:** se mantiene en media y no en alta, porque no está demostrado que la ceguera falle en la práctica: el sistema nunca se ha ejecutado y la orden del workflow prohíbe leer.

**D2-3. Los tres evaluadores comparten agentType, modelo y entradas.**
- **Problema:**
  - La procedencia los registra como tres agentes que no existen.
  - El motor cuenta los evaluadores por nombre, así que el aviso de «evaluador único» desaparece.
- **Evidencia:** `.claude/workflows/mision-1-auto.js:142-146`, `:166-171`; `.claude/workflows/evaluar-oportunidad-auto.js:71-75`; `src/nutria/scoring/engine.py:161-170`, `:199-200`.
- **Verificación:**
  - Con `rank()`: 1 nombre da aviso; 3 nombres con puntuaciones idénticas no lo dan.
  - `red-team-auto` sí diversifica los agentType.

**D2-5. La firma humana no autentica a nadie y ninguna transición la exige.**
- **Evidencia:** `src/nutria/cli.py:847`, `:876`; `src/nutria/db.py:95-96`, `:319-330`, `:447-453`; `src/nutria/provenance.py:51-52`; contradice `src/nutria/scoring/engine.py:259-260`.
- **Reproducción:**
  - «O-0001 -> en_validacion» funciona con la compuerta de seguridad bloqueada.
  - «firma: Dra. Inventada» queda registrada con `decided_by director-cientifico`.
- **Verificación:**
  - `.claude/settings.json:4-6` permite `Bash(nutria *)` sin preguntar.
  - La firma falsa acaba en los informes (`src/nutria/reports/render.py:31-35`).

**D2-8. El «descarte barato primero» no está codificado.**
- **Problema:** `evaluar-oportunidad-auto` no usa `descarte_rapido`, `compuerta_propuesta` ni el veredicto del crítico; siempre llega a la síntesis y a los 3 evaluadores.
- **Evidencia:** `.claude/workflows/evaluar-oportunidad-auto.js:27`, `:47-76`; `.claude/workflows/mision-1-auto.js:156-172`.
- **Reproducción (arnés):** con una compuerta propuesta como «bloqueada» y la crítica «refutada», se lanzan igualmente las 3 puntuaciones.
- **Verificación:** lo que dice sobre `mision-1-auto` exagera: la especificación de M1 manda puntuar todo tras el triaje.

**D2-9. `red-team-auto` falla en abierto porque no exige quórum.**
- **Problema:** si los 3 refutadores devuelven `null`, o si responde uno y no refuta, la afirmación sale «sostenida».
- **Evidencia:** `.claude/workflows/red-team-auto.js:37-41`, reproducido con `agent()` simulado.
- **Verificación:**
  - El umbral es absoluto (≥ 2 refutaciones), así que cuantos menos refutadores responden, más difícil es refutar.
  - La salida no indica cuántos veredictos válidos hubo.

**D2-10. `red-team-auto` cuenta «no comprobable» como «refutada».**
- **Problema:** el veredicto queda reducido a un booleano.
- **Evidencia:** `.claude/workflows/red-team-auto.js:18`, `:35`, `:49`, frente a `.claude/agents/critico-red-team.md:32` y `.claude/agents/verificador-citas.md:17-18`.
- **Verificación:**
  - Los otros dos workflows sí conservan `no_verificable`.
  - Exagera decir que sin red todo sale refutado, porque existen la caché y los resúmenes registrados.
  - También exagera decir que registra cambios de estado: `decision registrar` solo inserta una fila.

**D2-12. La doble extracción independiente ni se orquesta ni se puede distinguir.**
- **Problema:**
  - Ningún workflow la lanza.
  - Las dos extracciones usan la misma ruta y el mismo actor.
  - El estado lo declara quien escribe.
  - No hay ninguna orden para comparar o conciliar.
- **Evidencia:** `.claude/agents/extractor-datos.md:3`, `:28`, `:34-35`; `src/nutria/db.py:43-44`, `:236-246`; `src/nutria/data/schemas/extraccion.schema.json:39`; `src/nutria/cli.py:810-820`.
- **Verificación:**
  - No hay forma de cambiar `check_status` después del registro.
  - `nutria estado` cuenta como «doble verificación» lo que solo está autodeclarado.
  - La exportación saca las filas duplicadas.

**D2-14. Toda la crítica y la verificación las hace una sola familia de modelos.**
- **Problema:** todos los agentes son Claude, ningún workflow llama a `nutria llm` y los modelos alternativos están inactivos.
- **Evidencia:** `docs/PROMPT_MAESTRO_v2.md:275`; `docs/REVISION_PROMPT_ORIGINAL.md:45`; `src/nutria/data/modelos.toml:21-75`; `.claude/skills/red-team/SKILL.md:13-14`.
- **Verificación:**
  - Ningún modelo de otra familia tiene asignado el rol «verificacion».
  - De otra familia hay 4, no 6.
  - La documentación alterna entre «Usa» y «opcional».

#### D3. Antialucinación, verificación y trazabilidad (7)

**D3-6. El sello no está autenticado y apenas certifica nada.**
- **Problema:**
  - Es un JSON sin firma.
  - `stamp_status` solo mira `sha256`, `en_linea` y `errores`.
  - `verificar <md> --sellar` e `informe comprobar --sin-secciones` sellan sin exigir las 20 secciones.
  - No exige referencias verificadas ni revisión humana.
- **Evidencia:** `src/nutria/reports/check.py:147-169`; `src/nutria/cli.py:249-250`, `:643-644`, `:766`, `:941-942`.
- **Reproducción:**
  - Un sello escrito a mano da «Sello válido (None, 0 aviso(s))».
  - Un informe «final» sin referencias tiene sello válido.
- **Verificación:**
  - Dentro de `research/informes`, el hook sí exige las secciones.
  - El problema de fondo es que el sello no está ligado a ninguna verificación real.

**D3-7. La correspondencia entre identificador y cita es débil.**
- **Problema:**
  - El año se toma del número de 4 cifras más cercano en toda la entrada, incluido el DOI.
  - Basta con que el 50 % de las palabras del título real aparezca en cualquier parte de la entrada.
  - No se comparan autores ni revista.
- **Evidencia:** `src/nutria/verify/citations.py:25`, `:65-72`, `:99-105`, `:116-118`.
- **Reproducción (red simulada):**
  - T4: DOI `jds.2019-…` con una cita de 2012, sello válido.
  - T5: DOI de otro trabajo con cobertura 0,75, sello válido.
  - T7: autores inventados, sello válido.
- **Verificación:**
  - Lo de las páginas exagera.
  - `title_similarity` existe pero no se usa.
  - Con títulos formados por otras palabras, la discrepancia sí se detecta.

**D3-8. Una retractación desconocida se trata como «no retractado» sin aviso.**
- **Problema:** depende de OpenAlex y usa una caché de 30 días.
- **Evidencia:** `src/nutria/sources/crossref.py:38-40`, `:49`; `src/nutria/verify/citations.py:96-97`, `:146-151`; `src/nutria/reports/check.py:136`; `src/nutria/http.py:88-92`. Reproducido en T6.
- **Verificación:**
  - Con PMID, PubMed sí detecta la retractación.
  - El prefijo «RETRACTION:» suele marcar el aviso de retractación, no el artículo retractado.

**D3-9. La caché HTTP se puede envenenar sin dejar rastro.**
- **Problema:**
  - Son JSON sin ningún control de integridad, guardados en `.cache/nutria`, que git ignora.
  - Se sirven sin red, y lo servido desde caché no queda en la procedencia.
- **Evidencia:** `src/nutria/http.py:82-93`, `:130-132`; `src/nutria/config.py:25`; `.gitignore:4`.
- **Reproducción:** con una entrada plantada, `verificar --doi` da «verificada» y `fuente agregar` da «Registrada y verificada».
- **Verificación:**
  - Un `ts` futuro hace que la entrada no caduque nunca.
  - El DOI inventado también supera el sello.
  - Requiere escribir deliberadamente en el proyecto.

**D3-10. El registro no exige lo que define una [PUB].**
- **Problema:**
  - Una [PUB] debería apoyarse en una «fuente registrada y verificada», pero `add_claim` y `add_extraction` aceptan fuentes sin verificar o retractadas.
  - `buscar --registrar` registra sin verificar.
  - La comprobación de informes no consulta el registro.
- **Evidencia:** `src/nutria/db.py:236-238`, `:262-269`; `src/nutria/cli.py:159-163`, `:740`; `src/nutria/reports/check.py:17-19`.
- **Verificación:**
  - Sin red, un informe que cita un DOI no registrado y otro retractado obtuvo «Sello válido».
  - Atenuante: el alta sin verificar es intencionada y se ve.

**D3-11. La procedencia y las decisiones no tienen integridad ni autenticación.**
- **Problema:**
  - El JSONL no tiene cadena de hashes y las líneas corruptas se ignoran.
  - El actor y la firma son texto libre.
  - No hay triggers que impidan modificar o borrar.
  - `add_decision` no comprueba que la entidad exista.
- **Evidencia:** `src/nutria/provenance.py:51-52`, `:100-111`, `:125-128`; `src/nutria/db.py:88-92`, `:447-461`.
- **Reproducción:**
  - Un evento «revision_humana» forjado aparece en `nutria procedencia`.
  - Se aceptó una decisión firmada sobre O-0001 antes de que O-0001 existiera.
- **Verificación:**
  - Las decisiones, los cambios de estado y las compuertas no llegan a `procedencia.jsonl`.
  - Atenuante: la API no ofrece borrado.

**D3-12. Hay huecos de trazabilidad.**
- **Problema:**
  - Las puntuaciones se sobrescriben sin historial.
  - Lo que se sirve desde caché no deja eventos.
  - El evento del sello no recoge los avisos.
  - Los eventos no guardan el commit (solo `init`) ni el modelo del subagente (`docs/PROMPT_ORIGINAL.md:87`).
- **Evidencia:** `src/nutria/db.py:397-404`; `src/nutria/cli.py:80`, `:223-246`, `:431-432`, `:645-646`; `src/nutria/provenance.py:92-99`.
- **Verificación:** con red, `verificar` sí deja eventos `http`; aun así, los valores anteriores se pierden.

#### D4. Rigor científico en nutrición y fisiología animal (9)

**D4-1. No se ha ejecutado ninguna investigación.**
- **Problema:**
  - No hay salidas con las que juzgar la exactitud fisiológica o nutricional aplicada.
  - Todo el razonamiento de dominio se delega en los LLM.
- **Evidencia:** `research/*` solo contiene `.gitkeep`; no existe `research/nutria.sqlite`; `docs/PROMPT_ORIGINAL.md:115-118`; `docs/ARQUITECTURA.md:29-30`.
- **Verificación:** se rebaja de alta a media.
  - Calificarla de alta penalizaría dos veces lo que ya se valora en D11 (`comparar-sistemas.js:98-99`).
  - Hay salvaguardas de dominio correctas.
  - La debilidad real es otra:
    - No existe ningún modelo determinista de nutrición ni de destino digestivo.
    - El banco de preguntas tiene una sola de fisiología.
    - El esquema de extracción no exige dieta basal ni base de la dosis.

**D4-3. «Destino digestivo primero» no se impone.**
- **Problema:**
  - No es una compuerta.
  - Su criterio pesa 10/100 en el perfil recomendado (15 en el original).
  - Los workflows no descartan por destino digestivo antes de puntuar.
- **Evidencia:** `src/nutria/data/rubrica.toml:104-127`, `:131`, `:135`; `.claude/workflows/mision-1-auto.js:136-146`, `:161-172`; `.claude/workflows/evaluar-oportunidad-auto.js:27`, `:39-59`; los esquemas no tienen ningún campo de destino digestivo.
- **Reproducción:**
  - Candidato con plausibilidad 0 y el resto de criterios a 1: 90,0, APTA, puesto 1.
  - Candidato con todos los criterios a 0,75: 75,0, por detrás del anterior.
- **Verificación:** se rebaja de alta a media.
  - La regla sí existe como instrucción de parada en el agente de fisiología y en `/oportunidad`.
  - Con el perfil original el candidato también queda primero.
  - La reproducción usa un perfil extremo, aunque escenarios realistas también lo reproducen.

**D4-4. La concentración alcanzable in vivo y la dosis que llega al intestino son solo instrucciones.**
- **Problema:**
  - No hay herramienta ni campo para el escape ruminal, la digestibilidad intestinal ni la comparación entre la concentración in vitro y la in vivo.
  - No se advierte de la adaptación microbiana ni de las incubaciones cortas en lote.
- **Evidencia:** `.claude/agents/fisiologia-bioquimica.md:14-19`; `src/nutria/sources/pubchem.py:12-13`; `src/nutria/data/schemas/extraccion.schema.json:24`; `docs/PROMPT_MAESTRO_v2.md:321`.
- **Verificación:**
  - `add_extraction` descarta sin avisar claves como `escape_ruminal_pct`.
  - Las IC50/EC50 de ChEMBL se obtienen, pero no se registran ni se comparan.

**D4-5. Los esquemas no exigen el contexto biológico que el sistema declara obligatorio.**
- **Problema:** se aceptan:
  - extracciones sin etapa, dieta basal, dosis, base, forma, n ni medias;
  - hipótesis sin especie;
  - afirmaciones [PUB] sin especie;
  - oportunidades con especie «animales» y etapa «todas».
- **Evidencia:** `CLAUDE.md:19-21`; `src/nutria/data/schemas/extraccion.schema.json:5-6`; `hipotesis.schema.json:5-25`; `afirmacion.schema.json:5-15`; `oportunidad.schema.json:10-11`; `src/nutria/reports/render.py:37-42`.
- **Reproducción:** los 4 registros mínimos se aceptan como válidos.
- **Verificación:** se rebaja de alta a media.
  - La regla de no imputar impide exigir valores. El defecto real es que no se distingue «no consta» de «olvidado».
  - Los campos obligatorios aceptan cadenas vacías.
  - Por ahora el riesgo es latente.

**D4-6. La dieta basal es texto libre y no puede usarse como moderador.**
- **Problema:**
  - La hipótesis «el efecto depende de la dieta basal» no se puede contrastar sin recodificar a mano.
  - La exportación no filtra por especie ni por etapa.
- **Evidencia:** `src/nutria/data/schemas/extraccion.schema.json:19`; `.claude/agents/nutricion-formulacion.md:15-16`; `src/nutria/cli.py:342`, `:558`, `:814-819`.
- **Verificación:**
  - St-Pierre convierte el texto en NaN y descarta esas filas.
  - El análisis por subgrupos crea un subgrupo por cada cadena distinta, con k = 1.
  - La parte del filtro por especie es la más débil del hallazgo.

**D4-7. La fisiología del periodo de transición aparece solo como palabras clave.**
- **Problema:**
  - No hay contenido sobre balance energético negativo, AGNE y BHB, la caída de la ingestión preparto, hipocalcemia y DCAD, ni VLDL.
  - Faltan campos para el momento respecto al parto y para la definición de caso o el umbral.
- **Evidencia:** `.claude/skills/mision-1/SKILL.md:27`; `.claude/agents/fisiologia-bioquimica.md:10`; `src/nutria/data/schemas/extraccion.schema.json:9-41`.
- **Verificación:**
  - Un campo añadido como `dias_respecto_parto` supera la validación y se pierde sin aviso.
  - Por la vía yi/vi se pueden combinar incidencias con definiciones distintas.

**D4-8. El modelo del ganadero usa los mismos días para el coste y para la respuesta.**
- **Problema:**
  - En la plantilla de transición (−21 a +21 d) se valoran 42 días de leche, cuando unos 21 corresponden al periodo seco.
  - El resultado es que la respuesta umbral queda reducida a la mitad.
- **Evidencia:** `src/nutria/econ/farm.py:64`, `:68`, `:115-118`; `ejemplos/econ_ganadero_ILUSTRATIVO.toml:5-8`, `:16-20`; `.claude/skills/evaluacion-economica/SKILL.md:10-11`.
- **Reproducción:** el umbral sale en 0,610 kg/d, frente a 1,220 kg/d si se cuentan solo los 21 días de lactación.
- **Verificación:**
  - Puede cambiar la decisión: expresando la leche por ciclo, P(neto>0) pasa de 0,55 a 0,20.
  - «No puede representar el efecto residual» exagera: se puede con `por_dia=false`, aunque no está documentado.

**D4-9. St-Pierre no estima la pendiente «dentro de cada estudio», como afirma.**
- **Problema:**
  - El intercepto aleatorio mezcla información de dentro y de entre estudios.
  - No hay opción de efectos fijos de estudio ni de descomposición dentro/entre.
- **Evidencia:** `src/nutria/stats/mixed.py:4-6`, `:33-36`. Es la herramienta indicada en `docs/PROMPT_MAESTRO_v2.md:183` y en `.claude/skills/metaanalisis/SKILL.md:17-18`.
- **Reproducción (simulación, pendiente real 1,0):**

  | Escenario | Pendiente estimada |
  |---|---|
  | Sin correlación dosis-estudio | 1,003 |
  | Correlación fuerte, 2 dosis por estudio | 0,348 |
  | Correlación fuerte, 4 dosis por estudio | 0,758-0,774 |
  | Con efecto fijo de estudio | 1,00 |

- **Verificación:**
  - La simulación es una prueba de estrés: en diseños realistas el sesgo ronda el 2-9 %, y puede llegar al 10-25 %.
  - El estudio como efecto aleatorio es el método publicado.
  - St-Pierre (2001) no se pudo consultar porque el dominio está bloqueado.

**D4-10. No se ha medido la competencia de dominio de los modelos.**
- **Problema:**
  - El banco de evaluación tiene una pregunta de fisiología, trivial, y ninguna de nutrición.
  - Los modelos se fijaron a priori, en contra de `docs/PROMPT_MAESTRO_v2.md:273-274`.
- **Evidencia:** `evals/dominio_semilla.jsonl:1-10`; `README.md:73-76`; `research/evals/` está vacío.
- **Verificación:** con todos los modelos en `activo=false`, `nutria llm evaluar` no puede puntuar ninguno.

#### D5. Regulación UE y seguridad (10)

**D5-2. El árbol de clasificación pregunta en orden equivocado.**
- **Problema:**
  - «¿Previene, trata o cura?» va antes que la pregunta sobre pienso dietético.
  - Por eso, usos del Reglamento 2020/354 que el propio documento pone de ejemplo (fiebre de la leche, cetosis) acabarían clasificados como medicamento.
  - Faltan las excepciones del art. 13.3 del Reglamento 767/2009.
  - El árbol contradice su propia tabla, que clasifica los coccidiostáticos como aditivo.
- **Evidencia:** `docs/REGULACION_UE.md:12-13`, `:22-25`, `:40`, `:52`; `.claude/skills/triage-regulatorio/SKILL.md:12-14` frente a `:28-29`.
- **Verificación:**
  - La clasificación errónea es el resultado probable, no seguro.
  - Las excepciones se citan de memoria, sin EUR-Lex.

**D5-3. El nodo D manda a «materia prima» todo lo que cubre necesidades nutricionales.**
- **Problema:** según el art. 5.3.d del Reglamento 1831/2003, cubrir necesidades nutricionales es una función de los aditivos. Las vitaminas, los oligoelementos, los aminoácidos y la urea son aditivos de la categoría 3.
- **Evidencia:** `docs/REGULACION_UE.md:16-18` frente a `:38`; `.claude/skills/triage-regulatorio/SKILL.md:16-18`; `src/nutria/data/schemas/oportunidad.schema.json:14-16`.
- **Verificación:**
  - La misma regla aparece en `docs/PROMPT_MAESTRO_v2.md:294`.
  - Atenuantes: el esquema admite `aditivo_nutricional` y el agente consulta el Registro de aditivos.
  - El riesgo está en las sustancias nuevas, que no figuran en el Registro.

**D5-4. No se comprueban sustancias prohibidas o restringidas.**
- **Problema:**
  - La compuerta bloquea por «sustancia prohibida», pero no hay ninguna lista con la que comprobarlo.
  - Falta el art. 11.2 del Reglamento 1831/2003, que prohíbe autorizar como aditivos antibióticos distintos de coccidiostáticos e histomonóstatos.
  - Falta el anexo III del Reglamento 767/2009.
- **Evidencia:** `src/nutria/data/rubrica.toml:110-112`; `docs/RUBRICA.md:105`; `docs/REGULACION_UE.md:44-66`.
- **Verificación:**
  - Decir que no hay «ninguna referencia» exagera: se citan la Directiva 2002/32/CE y los Reglamentos 999/2001 y 1069/2009.
  - Faltan además la Directiva 96/22/CE y el art. 107.2 del Reglamento 2019/6.

**D5-5. La vía de autorización está incompleta.**
- **Problema:**
  - Falta el Reglamento 2019/1381: notificación previa de los estudios a EFSA y asesoramiento previo (arts. 32 bis y 32 ter del Reglamento 178/2002).
  - Falta el Reglamento 378/2005 (EURL-FA).
- **Evidencia:** `docs/PROMPT_MAESTRO_v2.md:304-306`, `:331-335`; `docs/REGULACION_UE.md:21`, `:44-66`; `.claude/skills/triage-regulatorio/SKILL.md:24-26`.
- **Verificación:**
  - El hallazgo mezcla dos cosas distintas: el prerregistro científico y la notificación a EFSA. El sistema no trata la notificación como opcional: simplemente no la contempla.
  - La consecuencia sería la inadmisibilidad o el retraso del expediente.
  - La parte del 378/2005 es menor.
  - Todo esto se contrastó con fuentes secundarias.

**D5-6. La guía FEEDAP de eficacia está desfasada.**
- **Problema:**
  - Se marca como «verificada» la de 2018 (EFSA J 16(5):5274), que ya fue sustituida por la de 2024 (EFSA J 22(7):e8856), aplicable desde el 1-12-2024.
  - «Verificada» solo significa que la referencia existe.
- **Evidencia:** `docs/REGULACION_UE.md:70-73`; `docs/REVISION_PROMPT_ORIGINAL.md:191-192`, `:273`.
- **Verificación:**
  - La etiqueta «verificada» hace que nadie la vuelva a revisar.
  - Afecta a los estabilizadores y a los reductores de metano.
  - No se pudo comprobar qué cambió exactamente en 2024.

**D5-7. La base legal está poco verificada y la herramienta no puede verificarla.**
- **Problema:**
  - Solo 3 de las 21 filas están marcadas «verificada».
  - Falta el CELEX y la fecha de la versión consolidada.
  - Las referencias que solo tienen URL generan un aviso, y `verificar` solo reconoce DOI y PMID.
- **Evidencia:** `docs/REGULACION_UE.md:3-6`, `:46-66`; `src/nutria/reports/check.py:113-114`; `src/nutria/verify/citations.py:22-23`, `:45-51`.
- **Reproducción:** un informe con el Reglamento 1831/2003 enlazado a EUR-Lex y con una URL inventada da 2 avisos y «Sello válido».
- **Verificación:**
  - Los 23 enlaces son ELI `/oj`, es decir, al acto original y no a la versión consolidada.
  - El estado de verificación se declara con transparencia.

**D5-9. Una compuerta sin resolver no bloquea.**
- **Problema:**
  - Una oportunidad con compuertas «no_resuelta» (CONDICIONAL) compite igual que una APTA y puede entrar en el top 3.
  - La compuerta de seguridad solo bloquea ante señales positivas, no ante la falta de datos críticos.
- **Evidencia:** `src/nutria/scoring/engine.py:172`, `:177-180`, `:230-233`; `src/nutria/data/rubrica.toml:107`; `docs/PROMPT_ORIGINAL.md:105`.
- **Reproducción:** O-0002, sin ninguna compuerta evaluada, quedó primera por delante de dos APTA.
- **Verificación:** la regla «no puede pasar a validación» no se aplica (`src/nutria/db.py:319-330`).

**D5-10. La compuerta de seguridad la decide el agente regulatorio sin el toxicólogo.**
- **Problema:**
  - En M1, `compuestos-toxicologia` solo genera ideas.
  - El esquema COMPUERTA devuelve una sola compuerta, y la de seguridad solo se resume.
  - En `evaluar-oportunidad-auto`, `compuerta_propuesta` no se registra.
- **Evidencia:** `.claude/workflows/mision-1-auto.js:46-48`, `:90`, `:137`, `:209`; `.claude/workflows/evaluar-oportunidad-auto.js:31`; `.claude/agents/compuestos-toxicologia.md:3`, `:32-33`.
- **Verificación:**
  - Que decida el agente regulatorio es una elección de diseño declarada; el defecto es que falta la conexión con el toxicólogo.
  - La vía manual sí lo incluye.

**D5-11. Las alegaciones permitidas solo se tratan como prohibiciones.**
- **Problema:**
  - Faltan los arts. 13.1, 13.2 y 10 del Reglamento 767/2009, la excepción de los desequilibrios nutricionales y el código de etiquetado.
  - No hay campo para la alegación prevista.
  - La sección 14 del informe puede quedar vacía.
- **Evidencia:** `docs/REGULACION_UE.md:28-30`; `docs/PROMPT_MAESTRO_v2.md:243`; `.claude/skills/triage-regulatorio/SKILL.md:41-48`; `src/nutria/reports/plantilla_informe.md:65-67`; `src/nutria/reports/check.py:84`.
- **Reproducción:** con la sección 14 vacía, el informe da 0 errores.
- **Verificación:**
  - Atenuante: la fase actual excluye formular alegaciones.
  - El art. 13 se contrastó en legislation.gov.uk.

**D5-16. Nada de la parte regulatoria se ha comprobado en uso.**
- **Problema:** no hay ninguna clasificación, compuerta ni informe regulatorio real, y no hay casos de prueba del árbol.
- **Evidencia:** `research/oportunidades/.gitkeep`; `research/informes/.gitkeep`; `tests/test_scoring.py:30-34`; `tests/test_registry_cli.py:21`.
- **Verificación:**
  - Solo 2 de las 41 funciones de prueba tocan compuertas, y ninguna la clasificación.
  - Las 5 preguntas regulatorias del banco no son casos de clasificación.

#### D6. Estadística y metaanálisis (6)

**D6-2. La dependencia entre comparaciones no se trata en Python ni genera aviso.**
- **Problema:** la única vía es una plantilla R sin probar, que además no convierte el SEM.
- **Evidencia:** `src/nutria/stats/meta.py:5-8`; `src/nutria/cli.py:492-546`, `:579-580`, `:588-590`.
- **Reproducción:**
  - 6 filas de 3 estudios (dos de ellos con varias comparaciones) se tratan como k = 6, sin aviso.
  - En el análisis «uno fuera», la etiqueta «sin E1» solo quita 1 de las 3 filas de E1.
- **Verificación:**
  - Con H0 cierta, el análisis ingenuo rechaza el 14,0 % de las veces, frente al 7,7 % con GLS.
  - `meta stpierre` cubre la dosis-respuesta en Python.
  - La pendiente ingenua no es anticonservadora.

**D6-4. St-Pierre tiene varias carencias.**
- **Problema:**
  - El docstring es inexacto.
  - Por defecto no incluye pendiente aleatoria.
  - Usa p de Wald z, sin Satterthwaite ni Kenward-Roger.
  - No pondera y no tiene pruebas.
- **Evidencia:** `src/nutria/stats/mixed.py:4-6`, `:34-36`, `:42-46`, `:51`; `src/nutria/cli.py:900`.
- **Reproducción (simulación):**
  - La pendiente estimada es 0,570 frente a un valor real de 0,5.
  - El error tipo I es del 18,3 % por defecto y del 9,7 % con `--pendiente-aleatoria`.
- **Verificación:**
  - Ajustes con covarianza singular salen con «convergencia: True».
  - `docs/REVISION_PROMPT_ORIGINAL.md:130-132` presenta St-Pierre como validado, y no lo está.

**D6-5. No hay validación de modelos predictivos agrupada por estudio.**
- **Problema:** solo se menciona. Tampoco hay modelos bayesianos ni PCA, que pide el encargo.
- **Evidencia:** `.claude/agents/estadistico.md:29-30`; `docs/PROMPT_MAESTRO_v2.md:195-196`; `docs/PROMPT_ORIGINAL.md:67`, `:71`.
- **Verificación:**
  - El análisis «uno fuera» mide influencia, no valida.
  - La parte de «regresión multivariante» exagera: la meta-regresión con varios moderadores sí existe.

**D6-6. El sesgo de publicación con k pequeño está mal resuelto.**
- **Problema:**
  - Egger se ejecuta con k ≥ 3, cuando la skill exige k ≥ 10.
  - Si todas las vi son iguales, se aborta el análisis completo.
  - No hay alternativas en Python.
  - El Egger clásico se aplica también a la SMD.
- **Evidencia:** `src/nutria/cli.py:536-540`, `:593`; `src/nutria/stats/meta.py:257-266`; `.claude/skills/metaanalisis/SKILL.md:23`.
- **Verificación:**
  - La propia guía es incoherente: otros documentos dicen que con k < 10 el resultado es «orientativo».
  - Con SMD, sin sesgo real, el test rechaza entre el 13 % y el 21 % de las veces.
  - Con vi = 0,2 devuelve un Egger sin sentido.

**D6-7. HKSJ, el modelo principal, tiene fallos.**
- **Problema:**
  - No ofrece la variante max(1, q).
  - Con q = 0 lanza un ZeroDivisionError que no se captura.
  - Con q < 1 puede dar un IC más estrecho que Wald.
  - No es el método por defecto en la CLI.
- **Evidencia:** `src/nutria/stats/meta.py:214-223`; `src/nutria/cli.py:892`, `:985`; `.claude/skills/metaanalisis/SKILL.md:14`.
- **Reproducción (k = 3, SEM):**

  | Método | p |
  |---|---|
  | HKSJ | 0,030 |
  | Wald | 0,053 |
  | HKSJ modificado | 0,193 |

- **Verificación:**
  - Con efectos casi idénticos sale EE 0,0000 y p = 1,1e-48.
  - Falla también en el análisis por subgrupos.

**D6-8. No hay medidas para variables binarias.**
- **Problema:** faltan RR, OR, RD, Mantel-Haenszel y Peto, y el esquema no tiene campos para eventos.
- **Evidencia:** `src/nutria/cli.py:889`; `src/nutria/stats/effect_sizes.py:73`; `src/nutria/data/schemas/extraccion.schema.json:28-34`; `tests/conftest.py:16-18`.
- **Verificación:**
  - La vía yi/vi funciona y reproduce dat.bcg, pero obliga a calcular fuera y rompe la trazabilidad.
  - No hay OR, MH ni Peto, ni tratamiento de celdas con cero.
  - Los campos de eventos que se añadan se descartan sin aviso.

#### D7. Integración con fuentes de datos (9)

**D7-2. Las claves de API y el correo acaban en ficheros que se versionan.**
- **Problema:**
  - Los errores HTTP incluyen `resp.url`, que contiene `api_key`, `email` y `mailto`.
  - Ese texto pasa a `Verification.message`, al sello `<informe>.verificacion.json` y a stdout/stderr.
  - La limpieza de secretos solo se aplica a `procedencia.jsonl`.
- **Evidencia:** `src/nutria/http.py:115-116`, `:124-125`; `src/nutria/verify/citations.py:137-144`, `:160-163`; `src/nutria/reports/check.py:133-135`, `:147-155`; `src/nutria/provenance.py:55-64`; `.gitignore:1-8`.
- **Reproducción:**
  - «SECRETO-OA-123» y «SECRETO-NCBI-456» aparecen en el sello, pero no en la procedencia.
  - La clave de NCBI aparece en el stderr de `buscar pubmed`.
- **Verificación:** se rebaja de alta a media.
  - Las claves expuestas son de bajo privilegio, y las de los LLM van en cabeceras.
  - El correo ya es versionable porque está en `nutria.toml`.
  - Aun así, el sello que contiene la clave se da por válido, y esto contradice `README.md:49` y `src/nutria/config.py:77`.

**D7-4. Los límites de frecuencia solo se respetan dentro de un mismo proceso.**
- **Problema:**
  - El estado del limitador (`_last`) vive en cada instancia, y cada orden es un proceso nuevo.
  - El intervalo de Crossref (0,05 s, es decir, 20 peticiones/s) supera los límites que Crossref publica desde el 1-12-2025: 5/s en el pool público y 10/s en el polite.
- **Evidencia:** `src/nutria/http.py:3-5`, `:35-45`, `:56-68`; `src/nutria/config.py:20`; `.claude/workflows/mision-1-auto.js:71`, `:107`.
- **Reproducción:** dos procesos llamaron a string-db.org con 0,020 s de diferencia, cuando el mínimo es 1 s.
- **Verificación:**
  - Los límites de Crossref solo se confirmaron mediante búsqueda web.
  - WebFetch y `doctor` quedan fuera del limitador.
  - Atenúan el problema los reintentos y la caché.

**D7-5. El acceso a OpenAlex sigue un modelo desfasado.**
- **Problema:**
  - El sistema trata la clave como opcional y confía en el «polite pool» por email.
  - Según el changelog de OpenAlex, consultado por búsqueda web, la clave es obligatoria desde el 13-2-2026.
  - `doctor` prueba OpenAlex sin clave.
- **Evidencia:** `src/nutria/config.py:18-20`; `README.md:47-48`; `docs/FUENTES.md:14`; `src/nutria/sources/openalex.py:13-20`; `src/nutria/cli.py:88`, `:105`.
- **Verificación:**
  - Simulación con respuestas 401 y 429:
    - un DOI que está en Crossref sale «verificada» sin que se compruebe la retractación;
    - un DOI inventado se queda en aviso y el sello sale válido.
  - Sin clave sigue habiendo una cuota de prueba, así que `doctor` puede dar OK.
  - No se comprobó en vivo.

**D7-7. El registro de consultas está incompleto.**
- **Problema:** el sistema afirma dejar «constancia de cada consulta», pero:
  - no hay evento para las respuestas servidas desde caché ni para las peticiones fallidas;
  - `molecula`, `diana`, `proteina`, `rutas` y `verificar` no tienen evento propio;
  - solo se guarda el SHA-256 de la respuesta, no su contenido;
  - WebFetch y WebSearch no se registran.
- **Evidencia:** `src/nutria/http.py:3-5`, `:88-92`, `:130-132`; `src/nutria/cli.py:254-317`; `.gitignore:4`; `.claude/settings.json:37-49`.
- **Reproducción:** 0 eventos en tres casos: consultas servidas desde caché, una búsqueda bloqueada por el proxy y respuestas 404/503.
- **Verificación:**
  - `verificar <md> --sellar` sí deja un evento `sello_informe`.
  - Las fuentes regulatorias, que deciden la compuerta regulatoria, no dejan ningún rastro.

**D7-8. `buscar efsa` ignora `--desde` y `--hasta`, pero los registra como aplicados.**
- **Evidencia:** `src/nutria/cli.py:146-152`, `:164-167`.
- **Reproducción:**
  - Consulta enviada: `(selenium yeast) AND JOURNAL:"EFSA Journal"`, sin `PUB_YEAR`.
  - Evento registrado: con `desde` 2018 y `hasta` 2020.
- **Verificación:**
  - Si hay red, el evento `http` permite reconstruir la consulta real.
  - Si la respuesta sale de caché o se trabaja sin red, solo queda el evento engañoso.

**D7-9. Fuentes clave del encargo no están integradas en el código.**
- **Problema:** solo se consultan con WebFetch o WebSearch, sin analizador, sin verificación y sin procedencia. Afecta a:
  - Registro UE de aditivos, Catálogo y Registro de materias primas, OpenFoodTox, EURL-FA y EMA;
  - Feedipedia, FEDNA, INRAE, NASEM y FAO;
  - PDB, AlphaFold DB, GO, GEO/ArrayExpress, MetaboLights, PRIDE y Ensembl.
- **Evidencia:** `docs/PROMPT_ORIGINAL.md:41`, `:45`, `:47`; `docs/FUENTES.md:23-32`, `:40-53`, `:57-64`; `.claude/agents/regulacion-seguridad.md:18-21`.
- **Verificación:**
  - GO está cubierto en parte a través de STRING.
  - La compuerta de seguridad también usa EFSA Journal, frases H y ChEMBL.
  - NASEM e INRAE son libros sin API.

**D7-10. El modo sin red es frágil y opaco.**
- **Problema:**
  - Sirve caché de cualquier antigüedad sin indicar su fecha.
  - La clave de caché incluye la `api_key` y el email.
- **Evidencia:** `src/nutria/http.py:81-96`; `src/nutria/sources/pubmed.py:12-19`; `src/nutria/sources/openalex.py:13-20`.
- **Reproducción:**
  - Una caché de 400 días se sirvió sin ningún aviso.
  - `--offline buscar pubmed` sin `NCBI_API_KEY` responde «no hay copia en caché» (exit 4); con la clave, termina con exit 0.
- **Verificación:**
  - Con una caché de 400 días, `verificar` responde «verificada» y el sello lleva la fecha de hoy.
  - También afecta a Crossref.

**D7-11. `diana --organismo` filtra en el cliente, después de aplicar el límite.**
- **Problema:** a ChEMBL solo se envían `q` y `limit`, y la especie se filtra entre los 20 primeros resultados.
- **Evidencia:** `src/nutria/sources/chembl.py:24-32`; `src/nutria/cli.py:273-281`.
- **Reproducción:** «Bos taurus» devolvió `[]`.
- **Verificación:**
  - El resultado vacío estaba garantizado por cómo se construyó la prueba (20 dianas humanas simuladas).
  - No se pudo comprobar si ChEMBL tiene una diana PPARA bovina.
  - Agravante: la comparación es por igualdad exacta, así que «vacuno» devuelve `[]` en silencio.

**D7-12. Ningún cliente se ha probado contra los servicios reales.**
- **Problema:**
  - Solo hay pruebas para 5 analizadores, y con respuestas sintéticas.
  - No hay pruebas para `http.py` ni para los clientes de chembl, pubchem, stringdb, reactome y patents.
- **Evidencia:** `README.md:112-114`; `tests/test_parsers.py:1-3`.
- **Verificación:**
  - Ninguna prueba ejecuta `HttpClient.request` y `tests/fixtures/` está vacío.
  - Si un analizador fallara y devolviera título None, la cita saldría «verificada» sin comparar el título.

#### D8. Priorización, compuertas y economía (7)

**D8-3. Las compuertas no condicionan los cambios de estado, y el estado de registro no condiciona el ranking.**
- **Problema:**
  - Una oportunidad puede pasar a `en_validacion` con la compuerta de seguridad bloqueada, cuatro sin resolver y sin firma.
  - Una oportunidad en estado «bloqueada» sigue compitiendo en el ranking.
- **Evidencia:** `src/nutria/db.py:319-330`; `src/nutria/cli.py:403`, `:444`, `:847`; contradice `docs/PROMPT_MAESTRO_v2.md:369-370` y `src/nutria/scoring/engine.py:259-260`.
- **Verificación:** la compuerta «bloqueada» sí funciona. El estado de registro «bloqueada», en cambio, no tiene un significado documentado, y las dos nociones resultan incoherentes.

**D8-4. La robustez frente a los pesos es débil por defecto.**
- **Problema:**
  - Con alfa = 200, la Dirichlet solo mueve cada peso entre 2,11 y 2,81 puntos (DE).
  - El código no compara los dos perfiles; esa comparación la hace un LLM.
- **Evidencia:** `src/nutria/scoring/engine.py:9-10`, `:144-145`, `:208-210`; `src/nutria/cli.py:864`; `.claude/workflows/mision-1-auto.js:178-179`.
- **Reproducción:**
  - Con el perfil «recomendado» gana O-B, con P(top1) = 0,96.
  - Con el perfil «original» gana O-A, con P(top1) = 0,994.
- **Verificación:**
  - La probabilidad de que la Dirichlet cubra el perfil original es de unos 0,00015.
  - En el 6,2 % de 20.000 pares aleatorios cambia el ganador.

**D8-5. El escenario «conservador» del ganadero está mal construido cuando la respuesta puede ser negativa.**
- **Problema:**
  - Multiplica el P10 de la respuesta por el P10 del precio.
  - Si la respuesta es negativa, un precio bajo reduce la pérdida en lugar de empeorar el escenario.
- **Evidencia:** `src/nutria/econ/farm.py:3-6`, `:78-87`, `:90-96`; `docs/PROMPT_MAESTRO_v2.md:346-347`.
- **Reproducción:**
  - Escenario conservador: −18,38 EUR; con el precio P90 serían −26,13 EUR.
  - En el ejemplo, el conservador da −11,66 frente a un P5 de −10,39 del Monte Carlo.
- **Verificación:**
  - Que caiga fuera del intervalo P5-P95 ocurre en el ejemplo, pero no en general.
  - Está en el límite con la gravedad baja.

**D8-6. Las anclas de `potencial_economico` no encajan con el código.**
- **Problema:**
  - El código calcula ROI = beneficio/coste, así que «ROI positivo» se cumple aunque el ganadero pierda dinero.
  - La alternativa «o mercado grande» mezcla las dos perspectivas económicas.
- **Evidencia:** `src/nutria/econ/farm.py:72-74`, `:133-135`; `src/nutria/data/rubrica.toml:99-102`; `.claude/skills/evaluacion-economica/SKILL.md:27`.
- **Reproducción:** con el ejemplo, ROI 0,064 y neto −11,66 EUR por vaca.
- **Verificación:**
  - La rúbrica usa a la vez «ROI positivo» y «ROI > 1».
  - Pasar de 0 a 0,75 en este criterio cambia el cuadrante de «Mejora incremental» a «Prioridad».

**D8-7. P(rNPV>0) no es la probabilidad de ganar dinero.**
- **Problema:**
  - P(rNPV>0) mide la incertidumbre de los parámetros sobre un valor ya ponderado por el riesgo.
  - El informe no da la probabilidad de pérdida.
- **Evidencia:** `src/nutria/econ/rnpv.py:83-98`, `:103-108`; `.claude/skills/evaluacion-economica/SKILL.md:22-23`.
- **Reproducción:**
  - El informe da P(rNPV>0) = 0,45, con una probabilidad acumulada de 0,17.
  - Simulando el éxito o fracaso de cada fase, P(VAN>0) = 0,175 y P(pérdida > 1 M EUR) = 0,21.
- **Verificación:**
  - El P5 que se muestra (−0,48 M EUR) es unas 3 veces menos grave que el real (−1,46 M EUR).
  - El defecto está en cómo se rotula y en la falta de riesgo a la baja.

**D8-8. En la misión 1 se puntúa el potencial económico (peso 15) sin ejecutar el modelo económico.**
- **Problema:**
  - En el triaje, el agente económico solo evalúa la propiedad intelectual.
  - Las anclas piden escenarios que en ese momento no existen.
- **Evidencia:** `.claude/workflows/mision-1-auto.js:136-146` (`:139`), `:166-172`; `.claude/skills/mision-1/SKILL.md:56-67`; `src/nutria/data/rubrica.toml:98-102`, `:131`.
- **Verificación:**
  - El evaluador económico no tiene acceso web ni precios con fuente.
  - Con un económico intuitivo de 0,75, una oportunidad con evidencia 0,5 supera a otra con evidencia 0,75 (53,8 frente a 51,2).

**D8-9. No están garantizados ni el número ni la independencia de los evaluadores.**
- **Problema:**
  - Solo se avisa con menos de 2 evaluadores, aunque el método exige al menos 3.
  - Los evaluadores se cuentan sobre todos los criterios a la vez.
  - El nombre del evaluador es texto libre.
- **Evidencia:** `src/nutria/scoring/engine.py:161-170`, `:199-200`; `docs/PROMPT_MAESTRO_v2.md:269-271`, `:372`; `src/nutria/data/schemas/puntuacion.schema.json:8`; `.claude/agents/director-cientifico.md:23-24`.
- **Reproducción:** si A puntúa 4 criterios y B los otros 4, el sistema registra 2 evaluadores, marca APTA y no avisa.
- **Verificación:**
  - El aviso no cambia el estado.
  - Los workflows cuentan los evaluadores mediante un booleano autodeclarado.

#### D9. Ingeniería: pruebas, robustez y reproducibilidad (11)

**D9-1. La clave de API y el correo se filtran al sello, a stdout/stderr y al contexto de los agentes.**
- **Evidencia:** `src/nutria/http.py:116`, `:125`; `src/nutria/verify/citations.py:139-144`, `:162-163`; `src/nutria/reports/check.py:133-135`, `:147-154`; `.gitignore:1-8`.
- **Reproducción:**
  - Con un HTTP 400 simulado, el sello contiene dos veces la clave y dos veces el correo.
  - `verificar --pmid` y `fuente agregar --pmid` muestran ambos por pantalla.
- **Verificación:** se rebaja a media.
  - Solo afecta a credenciales que viajan en la URL; son opcionales y de poco valor.
  - Hace falta un fallo de red para que ocurra.
  - Agravante: también se filtra con fallos de conexión (`src/nutria/http.py:134`).

**D9-3. Una búsqueda posterior borra la marca de retractación y sustituye metadatos ya verificados, sin dejar rastro.**
- **Evidencia:** `src/nutria/db.py:184-185`, `:200-205`; `src/nutria/sources/pubmed.py:60`; `src/nutria/sources/openalex.py:68`; `src/nutria/sources/europepmc.py:40`; `src/nutria/cli.py:159-163`.
- **Reproducción:**
  - Se parte de una fuente con `retracted=1`.
  - Tras `buscar pubmed … --registrar`, queda `retracted=0` y otro título, mientras `verified=1` sigue intacto.
- **Verificación:** se rebaja de alta a media.
  - La marca no alimenta ninguna barrera.
  - La verificación en línea del informe vuelve a detectar la retractación.
  - Lo que queda es un defecto de integridad del registro.

**D9-4. Entradas previsibles provocan excepciones sin capturar: traza y salida 1.**
- **Problema:** cuatro casos.
  - `IntegrityError` UNIQUE en `sources.pmid`.
  - `TypeError` si falta una media, también con el CSV que genera la propia exportación.
  - `ZeroDivisionError` con yi idénticos y `--hksj`.
  - `RuntimeError` en `meta stpierre` si no está statsmodels.
- **Evidencia:** `src/nutria/cli.py:977-987`, `:500-505`; `src/nutria/db.py:29-30`, `:170-173`; `src/nutria/stats/meta.py:215-218`; `src/nutria/stats/mixed.py:19-23`.
- **Verificación:**
  - Los códigos de salida 2, 3 y 4 no están documentados.
  - El primer caso deja el registro incoherente y la CLI no ofrece forma de arreglarlo.

**D9-5. Hay resultados numéricos erróneos sin ningún aviso.**
- **Problema:**
  - Con `--decimal ','` se borran todos los puntos, de modo que «0.5» pasa a valer 5,0.
  - Egger con precisión constante devuelve «EE 57208962.111, p = 1».
  - Si falta `tipo_disp`, se asume SD.
- **Evidencia:** `src/nutria/cli.py:473-489`, `:505`; `src/nutria/stats/meta.py:257-268`.
- **Reproducción:** con el problema de la coma, la estimación sale 1,8809 en lugar de unos 0,38.
- **Verificación:**
  - Sin HKSJ, la conclusión se invierte.
  - Las cifras exactas no se reprodujeron porque los CSV no estaban disponibles; los mecanismos sí.

**D9-6. El sello puede cubrir contenido que nunca se comprobó (condición de carrera TOCTOU).**
- **Problema:**
  - El informe se lee al principio, la verificación en línea tarda minutos y el sha256 se calcula sobre el fichero en el momento de sellar.
  - `verificar <md> --sellar` además desactiva la comprobación de secciones.
- **Evidencia:** `src/nutria/cli.py:249-250`, `:636`, `:643-644`; `src/nutria/reports/check.py:149`.
- **Reproducción:** se editó el informe durante la verificación y salió «Sello válido» con una referencia [R9] inexistente.
- **Verificación:**
  - Con agentes trabajando en paralelo, el caso es plausible.
  - Un DOI inexistente queda certificado.

**D9-7. La procedencia no permite reproducir los resultados, y las puntuaciones se sobrescriben sin historial.**
- **Problema:**
  - La sobrescritura va contra el principio declarado en `src/nutria/db.py:4-5`.
  - Los eventos de puntuación y de ranking no guardan valores ni rúbrica.
  - No se guarda el sha256 de los CSV y TOML de entrada.
  - Las versiones solo se registran en `init`.
  - El JSONL no tiene cadena de hashes.
- **Evidencia:** `src/nutria/db.py:397-404`; `src/nutria/cli.py:430-432`, `:451-452`, `:563-566`, `:627-628`; `src/nutria/provenance.py:67-86`, `:100-112`.
- **Reproducción:** dos rankings con las mismas entradas registradas dieron puntuaciones compuestas de 45,0 y 100,0, y solo queda el último valor.
- **Verificación:** contradice el requisito de `docs/PROMPT_ORIGINAL.md:87`.

**D9-9. Las altas por lotes no son atómicas.**
- **Problema:** si un elemento falla a mitad del lote, las altas anteriores quedan hechas; al reintentar, se duplican.
- **Evidencia:** `src/nutria/cli.py:333-340`, `:359-366`, `:380-387`; `src/nutria/db.py:291-304`.
- **Reproducción:**
  - Un lote de 3 con el tercero inválido crea O-0001 y O-0002 y sale con código 1.
  - El reintento crea O-0003 a O-0005.
- **Verificación:**
  - El error no dice qué elemento falló.
  - Lo más expuesto son las extracciones: se exportan duplicadas y no tienen estado «descartada».

**D9-10. El hook falla en abierto.**
- **Problema:**
  - Usa el `python3` del PATH.
  - Ante un `ImportError`, no bloquea.
  - Con un sello corrupto sale con código 1, que no bloquea.
- **Evidencia:** `.claude/settings.json:38-40`, `:44`; `README.md:31-37`; `scripts/hooks/check_research_file.py:10`, `:81-82`; `src/nutria/reports/check.py:162`.
- **Reproducción:** con un intérprete sin `requests`, un informe «final» con 23 errores pasa sin ninguna salida.
- **Verificación:**
  - Sin `jsonschema` pasa incluso un JSON inválido.
  - Atenuante: funciona si se arranca Claude Code con el venv activado.

**D9-11. Los workflows tratan como favorable que un agente falle.**
- **Problema:**
  - En `red-team-auto`, si los 3 refutadores devuelven null, el resultado es «sostenida».
  - `evaluar-oportunidad-auto` pasa aportaciones con crítica o verificación nulas sin marcarlas, y descarta en silencio a los especialistas que fallan.
- **Evidencia:** `.claude/workflows/red-team-auto.js:38-41`; `.claude/workflows/evaluar-oportunidad-auto.js:53-58`, `:66`, `:80-81`.
- **Verificación:** los null se ven en el JSON, pero ninguna instrucción dice cómo tratarlos.

**D9-12. Las pruebas cubren sobre todo los caminos felices y no hay infraestructura para reproducir el entorno.**
- **Problema:**
  - Cobertura en torno al 61 %.
  - 0 % en el router, en `mixed` y en 5 clientes; 26 % en `http.py`.
  - No hay pruebas del hook, de los workflows, de la concurrencia, de la limpieza de secretos ni del paso exportar→analizar.
  - Aparecen 19 ResourceWarning.
  - No hay lockfile, CI, lint ni tipos.
- **Evidencia:** `src/nutria/cli.py:48-51`; `tests/conftest.py:13`; `pyproject.toml:11-20`.
- **Verificación:**
  - Sí hay pruebas negativas.
  - El núcleo estadístico está bien cubierto: `meta` 88 %, `scoring` 96 %.
  - El paso exportar→analizar falla sin que ninguna prueba lo detecte.

**D9-13. La comprobación del año de la cita toma los años que aparecen dentro del DOI.**
- **Evidencia:** `src/nutria/verify/citations.py:25`, `:116-118`.
- **Reproducción:**
  - Una cita de «2005» con el DOI `10.3168/jds.2019-16789` no se marca como discrepante.
  - Control: con un DOI sin año, sí se detecta.
- **Verificación:**
  - No afecta a los DOI antiguos de J Dairy Sci.
  - También deja pasar un DOI real de otro trabajo con título parecido.

#### D10. Operación y usabilidad (5)

**D10-2. La instalación no avisa de que el venv debe estar activo al abrir Claude Code.**
- **Problema:**
  - El hook usa el `python3` del PATH y, si le faltan dependencias, se desactiva sin avisar.
  - `doctor` no lo detecta.
- **Evidencia:** `README.md:31-37`, `:56-59`; `.claude/settings.json:44`; `scripts/hooks/check_research_file.py:10`, `:81-82`; `src/nutria/cli.py:98-120`.
- **Reproducción:**
  - Con el venv, el hook bloquea un informe con 23 errores.
  - Sin las dependencias, el mismo informe pasa con exit 0.
- **Verificación:**
  - El caso silencioso real se da cuando la CLI funciona (por ejemplo, `.venv/bin/nutria`) pero el hook sigue apagado.
  - `doctor` informa de las versiones del venv, lo que da una falsa tranquilidad.

**D10-3. El coste en tokens y en tiempo no se cuantifica ni se acota.**
- **Problema:**
  - La documentación solo dice que «consume muchos tokens».
  - `n_oportunidades` funciona como mínimo, no como tope.
  - Ningún workflow usa el presupuesto (budget).
- **Evidencia:** `README.md:64`; `.claude/workflows/mision-1-auto.js:4`, `:16`, `:121-133`; `docs/REVISION_PROMPT_ORIGINAL.md:231`.
- **Simulación:**

  | Workflow | Subagentes lanzados |
  |---|---|
  | `mision-1-auto` | 141 a 263 (con 24 oportunidades, el doble del objetivo) |
  | `evaluar-oportunidad-auto` | 29 |
  | `red-team-auto` | 47 |

- **Verificación:**
  - Alrededor del 60 % de los subagentes corre en opus.
  - Si se agotara el objetivo de tokens a mitad de M1c, el ranking podría abortar. Esto se emuló; no se ejecutó.

**D10-4. Las limitaciones declaradas no cubren lo principal.**
- **Problema:**
  - No se dice que la capa de agentes nunca se ha ejecutado.
  - «Determinista» y «reproducible» solo valen para el flujo de control.
  - La versión de los modelos no queda registrada.
- **Evidencia:** `README.md:64`, `:107-117`; `.claude/workflows/mision-1-auto.js:4`; `.claude/workflows/evaluar-oportunidad-auto.js:4`; `src/nutria/provenance.py:92-99`; `docs/PROMPT_MAESTRO_v2.md:396-397`.
- **Verificación:**
  - Los eventos `llm` sí guardan el modelo.
  - El Anexo C pide las versiones, pero solo a mano.

**D10-5. No queda claro lo que el sistema no hace.**
- **Problema:**
  - No hay módulo de modelización, simulación ni optimización de formulaciones.
  - Aun así, la trazabilidad hace corresponder «Modelización» con órdenes que solo consultan bases de datos.
  - R, metafor y clubSandwich no figuran como requisitos.
  - `settings.json` no permite `Rscript` ni algunos dominios citados en las fuentes.
- **Evidencia:** `docs/REVISION_PROMPT_ORIGINAL.md:242`; `pyproject.toml:11-20`; `README.md:21`; `.claude/skills/metaanalisis/SKILL.md:16-17`; `src/nutria/reports/plantilla_informe.md:59-61`; `.claude/settings.json:3-29`.
- **Verificación:**
  - Hay Monte Carlo y St-Pierre, aunque no son modelos biológicos.
  - Lo que no está en la lista de permitidos pide permiso; no queda bloqueado.

**D10-6. El flujo documentado `extraccion exportar → meta analizar` falla con una traza.**
- **Problema:**
  - Conviven tres nomenclaturas de columnas.
  - La ayuda (`--help`) no documenta el formato del CSV.
  - Si solo se renombran las medias, el SEM se trata como DE sin avisar.
- **Evidencia:** `docs/ARQUITECTURA.md:60`; `src/nutria/db.py:40-41`, `:229`; `src/nutria/cli.py:503-505`, `:883-894`, `:985`.
- **Reproducción:**
  - Con 3 extracciones de tipo `SEM_combinado`, la exportación seguida del análisis da un TypeError.
  - Renombrando solo las medias, sale EE 0,2186 y p 0,0395, sin ningún aviso.
- **Verificación:** el procedimiento detallado incluye un paso de preparación que, si se sigue, evita el fallo.

### 4.3 Gravedad BAJA (8)

**D2-6 (D2). `mision-1-auto` elimina los dos puntos de control humanos de `/mision-1`.**
- **Problema:** redacta los planes del top 3 antes de cualquier firma.
- **Evidencia:** `.claude/skills/mision-1/SKILL.md:41`, `:75-76` frente a `.claude/workflows/mision-1-auto.js:79-89`, `:176-200`, `:214`.
- **Por qué es baja:**
  - Lo relativo a las compuertas estaba mal atribuido.
  - Es un modo declarado que el usuario elige.
  - Nada es irreversible.
  - El README lo presenta como «la misma misión».

**D2-11 (D2). Hay un conflicto de rol: los subagentes reciben un `CLAUDE.md` escrito para el orquestador.**
- **Evidencia:** `CLAUDE.md:3-5`, `:60-61`, `:112-114`; `.claude/workflows/mision-1-auto.js:18`; `.claude/workflows/evaluar-oportunidad-auto.js:19`.
- **Por qué es baja:**
  - Ningún subagente tiene la herramienta Agent, así que las órdenes de orquestación no tienen efecto.
  - No se ha comprobado en una ejecución real.
  - Se arregla con una línea.

**D2-13 (D2). «Determinista» y «reproducible» exageran.**
- **Problema:**
  - El top 3 lo elige un agente leyendo el texto del ranking, sin validación en código.
  - Qué especialistas intervienen y qué afirmaciones se atacan lo deciden agentes.
- **Evidencia:** `README.md:10`, `:64`, `:125`; `.claude/workflows/mision-1-auto.js:4`, `:177-183`; `src/nutria/cli.py:453`.
- **Por qué es baja:**
  - «Workflow determinista» es terminología habitual para el flujo de control.
  - Aun así, falta validar en código que los IDs del top 3 estén en estado APTA o CONDICIONAL.

**D6-3 (D6). El diseño del ensayo (crossover, cuadrado latino) y la unidad experimental solo se controlan por instrucciones.**
- **Evidencia:** `src/nutria/stats/effect_sizes.py:76-97`; `src/nutria/data/schemas/extraccion.schema.json:16-17`.
- **Reproducción:** con el mismo efecto de 1 kg/d, g = 0,245 en paralelo y 0,630 en crossover.
- **Por qué es baja:**
  - Solo afecta a la SMD, que no es la medida por defecto.
  - Con MD, el resultado no depende del diseño.
  - Existe `--subgrupo`.

**D7-6 (D7). `fuente agregar --doi` no registra los DOI que solo están en OpenAlex (DataCite, mEDRA, Zenodo).**
- **Evidencia:** `src/nutria/cli.py:196-202`; `src/nutria/verify/citations.py:130-136`.
- **Reproducción:**
  - `verificar --doi 10.5281/zenodo.123456` termina con código 0.
  - `fuente agregar` responde «Error de red: … 404» con código 4.
- **Por qué es baja:**
  - Falla en cerrado y hay una vía alternativa: `buscar openalex --registrar` seguido de `verificar --pendientes`.
  - Aun así, el error se presenta como un fallo de red, lo que induce a error.

**D9-8 (D9). La clave de la caché depende de las credenciales.**
- **Problema:** el modo sin red y la reproducción en otra máquina fallan si cambian la clave de API o el correo.
- **Evidencia:** `src/nutria/http.py:81-84`; `src/nutria/sources/pubmed.py:12-19`; `.gitignore:4`.
- **Por qué es baja:**
  - Excluir la caché de git es lo habitual.
  - Ningún workflow depende de `--offline`.
  - El arreglo es trivial.
  - Aun así, el mensaje de error («no hay copia en caché») engaña.

**D11-1 (D11). No se ha ejecutado la primera misión ni ninguna otra investigación.**
- **Evidencia:** `docs/PROMPT_ORIGINAL.md:115-118`; `docs/PROMPT_MAESTRO_v2.md:416-439`; `git ls-files research` solo devuelve ficheros `.gitkeep`; hay un único commit, `236a161`.
- **Por qué es baja:**
  - D11 se puntúa n/a, sin penalización.
  - Es un estado del proyecto, no un defecto de diseño.

**D11-2 (D11). No hay registro ni trazas con los que auditar el contenido de dominio.**
- **Problema:**
  - El estado «verificada» de 10 de las 18 referencias (más 2 parciales) y de 3 de las 21 normas es autodeclarado.
  - R2 y R11, marcadas «pendiente», se verifican en un minuto.
- **Evidencia:** `CLAUDE.md:98-99`; `README.md:132`; `docs/REVISION_PROMPT_ORIGINAL.md:255-276`; `docs/REGULACION_UE.md:4-6`, `:46-66`.
- **Por qué es baja:**
  - Es falso que la falta de `nutria init` cause el problema: la búsqueda web nunca deja traza en la procedencia.
  - El criterio «pendiente = sin comprobar en esta sesión» está definido.
  - No se encontró ninguna referencia marcada «verificada» que fuera falsa.

## 5. Hallazgos descartados por la verificación y por qué

**Ninguno.** El arnés ordenó a cada verificador que intentara refutar el hallazgo y que lo marcara como no real si los ficheros no lo sostenían, si ya estaba resuelto en otra parte o si exageraba (`.claude/workflows/comparar-sistemas.js:120-122`). Los 111 hallazgos sometidos a verificación se confirmaron como reales.

La verificación sí corrigió la gravedad de varios hallazgos y matizó parte de su contenido. Esas correcciones se recogen a continuación.

### 5.1 Gravedad rebajada (16 hallazgos)

| Hallazgo | Cambio | Motivo de la verificación |
|---|---|---|
| D1-4 | alta → media | La skill pide la columna `tipo_disp`. El fallo silencioso exige las dos dispersiones rellenas, y con HKSJ el efecto combinado apenas cambia |
| D2-1 | queda en media, no alta | La fuga de puntuaciones a los evaluadores es posible, pero no está demostrada: el sistema nunca se ha ejecutado |
| D2-6 | rebajado a baja | La parte de las compuertas estaba mal atribuida. Es un modo declarado y elegido por el usuario, y nada es irreversible |
| D2-11 | media → baja | Los subagentes no tienen la herramienta Agent y no se ha comprobado el efecto en ejecución |
| D2-13 | media → baja | «Workflow determinista» es la terminología habitual del flujo de control, y la salida del ranking muestra el estado |
| D4-1 | alta → media | Penalizaría dos veces lo que ya cubre D11 (`comparar-sistemas.js:98-99`) |
| D4-3 | alta → media | La regla existe como instrucción con parada, el perfil «original» da el mismo resultado y la reproducción usaba un perfil extremo |
| D4-5 | alta → media | La regla de no imputar impide exigir valores, y el riesgo es latente |
| D6-3 | rebajado a baja | Solo afecta a la SMD. Con MD, que es la medida por defecto, el efecto no depende del diseño |
| D7-2 | alta → media | Las claves expuestas son de bajo privilegio; las de pago van en cabeceras |
| D7-6 | media → baja | Falla en cerrado y existe una vía alternativa para registrar |
| D9-1 | alta → media | Solo afecta a credenciales en la URL, opcionales y de poco valor, y exige un fallo de red |
| D9-3 | alta → media | La marca de retractación no alimenta ninguna barrera, y la verificación del informe la vuelve a detectar |
| D9-8 | rebajado a baja | Lo que se promete para la reproducibilidad está versionado, y el arreglo es trivial |
| D11-1 | alta → baja | D11 se puntúa n/a sin penalizar, y se trata de un estado del proyecto |
| D11-2 | media → baja | La frontera entre «verificada» y «pendiente» está definida, y no apareció ninguna referencia marcada «verificada» que fuera falsa |

### 5.2 Afirmaciones matizadas o refutadas en parte, sin cambio de gravedad

- **D1-3:**
  - Cada `agent()` tiene contexto propio. Por eso, decir que «no corrige los errores correlacionados» exagera: se corrige la parte de «mismo contexto».
  - La segunda opinión desactivada es un valor por defecto razonable.
- **D1-8 y D6-2:** que la dependencia «no se modela en Python» es inexacto.
  - `meta stpierre` trata el estudio como efecto aleatorio.
  - Egger sí avisa con k < 10.
  - La pendiente ingenua no es anticonservadora.
- **D2-10:**
  - «Sin red, todo sale refutado» es una extrapolación, porque existen la caché y los resúmenes registrados.
  - «Registra cambios de estado» es inexacto: `decision registrar` solo inserta una fila.
- **D3-5:** el valor de «estado» admite mayúsculas. Lo que debe ir en minúsculas y en la columna 0 es la clave «estado:».
- **D3-7:** lo relativo a las páginas exagera, porque requiere una coincidencia de ±1 con el año.
- **D4-4:**
  - PubChem también da sinónimos y frases H.
  - ChEMBL da IC50 y EC50, aunque no se registran.
- **D5-4:** decir «ninguna referencia» exagera. Sí se citan la Directiva 2002/32/CE y los Reglamentos 999/2001, 1069/2009, 142/2011 y 396/2005.
- **D5-5:**
  - El hallazgo mezcla el prerregistro científico con la notificación a EFSA.
  - La consecuencia sería un expediente inadmisible o retrasado, no ensayos inútiles.
- **D6-5:** «sin regresión multivariante» exagera. Existe meta-regresión con varios moderadores y St-Pierre con covariables.
- **D7-9:**
  - GO es accesible a través de STRING.
  - EMA no es una fuente clave.
  - NASEM e INRAE son libros sin API.
- **D7-11:** la reproducción estaba garantizada por cómo se construyó (20 dianas humanas simuladas).
- **D8-3:** el estado de registro «bloqueada» no tiene un significado documentado. La compuerta «bloqueada» sí funciona.
- **D9-4:**
  - Los códigos de salida 2, 3 y 4 no están documentados.
  - Con un moderador constante, el programa termina limpio con exit 2.
- **D9-12:** sí hay pruebas negativas. Lo que no está probado son las rutas de error de red y de E/S.
- **D10-5:**
  - Monte Carlo y St-Pierre existen, aunque no son modelos biológicos.
  - Lo que no está en la lista de permitidos pide permiso; no queda bloqueado.
- **D11-2:** la cadena causal «por no ejecutar `init`» es falsa, porque la búsqueda web nunca deja traza en `procedencia.jsonl`.

### 5.3 Hallazgos que la verificación encontró más graves de lo descrito

- **D1-1, D3-1, D7-3 y D10-1:** el sello falla en abierto con cualquier fallo de red: proxy, 429 persistente, timeout o red parcial. No hace falta `--offline`.
- **D2-4 y D8-2:**
  - Cumplir `CLAUDE.md:38-39` es imposible: la orden de compuerta no acepta `--firma-humana`.
  - Revertir una compuerta borra la evidencia del bloqueo.
  - `mision-1-auto.js:214` afirma lo contrario.
- **D3-2:** incluso con red, un informe que solo tiene referencias URL se sella con `en_linea: true` sin hacer ninguna petición.
- **D3-9:** un `ts` futuro en la caché hace que la entrada no caduque nunca.
- **D4-2:** con una dosis expresada en otra base, el modelo económico da coste 0 y ROI NaN sin avisar.
- **D7-1 y D9-2:** también se calla ante 401, 403, 409, 429, 404 y en modo sin red. Además, Crossref ya marca la retractación en `updated-by`, campo que el código no lee.

## 6. Debilidades menores sin verificar (40)

Son debilidades que el auditor de cada dimensión calificó de gravedad baja y que, por diseño del arnés, no pasaron por la verificación adversarial (`.claude/workflows/comparar-sistemas.js:115-118`).

«Reproducido» significa que lo reprodujo ese auditor, no un verificador independiente. Deben tratarse como **indicios pendientes de confirmar**.

**D1**
- **D1-14.** No se exigen 3 evaluadores: con 2 no hay aviso.
  - La puntuación compuesta se muestra con un decimal.
  - Con α = 200, los pesos apenas se perturban (±2-3 puntos).
  - Evidencia: `src/nutria/scoring/engine.py:199-200`, `:208-210`, `:249`; `src/nutria/cli.py:864`; `docs/PROMPT_MAESTRO_v2.md:372`.
  - Reproducido: O-0001, con 2 evaluadores, queda en el puesto 1 sin aviso.
- **D1-15.** `red-team-auto` cuenta como refutado lo que no se puede comprobar.
  - Sin red no discrimina, y contradice el veredicto «no verificable» del crítico.
  - Evidencia: `.claude/workflows/red-team-auto.js:35`; `.claude/agents/critico-red-team.md:32`; `src/nutria/verify/citations.py:7-9`.

**D2**
- **D2-15.** `mision-1-auto` no es idempotente y no ejecuta el paso 0 (`doctor`/`estado`).
  - Usa nombres fijos (`M1_01.json`), así que al repetirlo duplica oportunidades.
  - No comprueba el mínimo de 6 oportunidades en rumiantes.
  - Evidencia: `.claude/workflows/mision-1-auto.js:59-60`, `:109-119`, `:151`; `.claude/skills/mision-1/SKILL.md:19-21`, `:49`.
- **D2-16.** Colisión de nombres: revisar `research/planes/O-0001.md` y `research/informes/O-0001.md` escribe el mismo `O-0001_redteam.md`.
  - Evidencia: `.claude/workflows/red-team-auto.js:46-48`, `:57`; `.claude/skills/red-team/SKILL.md:15`.
- **D2-17.** Dependencia de versión no documentada.
  - Los workflows existen en el binario nativo, pero no en el paquete npm 2.1.42 del entorno.
  - El README no indica qué versión mínima de Claude Code hace falta.
  - Evidencia: `README.md:56-71`; `/opt/node22/lib/node_modules/@anthropic-ai/claude-code/cli.js`; `/opt/claude-code/bin/claude`.
- **D2-18.** En el triaje de M1, la compuerta de seguridad solo la ve `regulacion-seguridad`, y la novedad solo `investigador-bibliografico`.
  - Evidencia: `.claude/workflows/mision-1-auto.js:98-102`, `:136-141`; `.claude/agents/compuestos-toxicologia.md:3`, `:32-33`; `.claude/skills/mision-1/SKILL.md:51-52`.

**D3**
- **D3-13.** Si un [Rn] aparece duplicado, solo se verifica la última entrada.
  - Además, solo cuenta el primer encabezado de bibliografía y cada entrada debe ocupar una línea.
  - Evidencia: `src/nutria/reports/check.py:30`, `:56-60`, `:69-73`.
  - Reproducido (T3): un [R1] inventado seguido de uno real da sello válido.
- **D3-14.** Un DOI que solo está en OpenAlex se verifica, pero no se puede registrar. Es el mismo problema que D7-6.
  - Evidencia: `src/nutria/cli.py:196-202`; `src/nutria/verify/citations.py:131-136`.
- **D3-15.** La existencia de un DOI no se consulta en doi.org.
  - Un DOI de DataCite o mEDRA que no esté en OpenAlex daría «no existe». No se ha comprobado en vivo.
  - Evidencia: `src/nutria/verify/citations.py:127-136`; `src/nutria/reports/check.py:131-132`.
- **D3-16.** Las 11 pruebas de D3 pasan, pero no cubren ninguno de sus modos de fallo.
  - Evidencia: `tests/test_citations.py:1-51`; `tests/test_report_check.py:1-38`; `tests/test_registry_cli.py:1-49`.

**D4**
- **D4-11.** La escala de madurez acepta como EMC-2 el efecto in vivo de «un análogo cercano». Esto contradice el registro de la forma del producto y el principio de destino digestivo.
  - Evidencia: `docs/PROMPT_MAESTRO_v2.md:40` frente a `CLAUDE.md:19-25` y `docs/PROMPT_MAESTRO_v2.md:61-64`.

**D5**
- **D5-12.** La ruta regulatoria registrada está incompleta.
  - Faltan coccidiostáticos e histomonóstatos, pienso complementario o compuesto y premezcla.
  - No hay grupo funcional ni situación administrativa.
  - La rellena el director antes del triaje y nunca se concilia con él.
  - Evidencia: `src/nutria/data/schemas/oportunidad.schema.json:17-20`; `docs/PROMPT_MAESTRO_v2.md:302`; `.claude/workflows/mision-1-auto.js:34-38`, `:151-153`.
- **D5-13.** OMG y microorganismos: aplica el Reglamento 1829/2003 a todo producto de fermentación con cepas modificadas.
  - No menciona QPS.
  - La skill omite los genes de resistencia y la toxigenicidad.
  - Evidencia: `docs/REGULACION_UE.md:59`; `.claude/skills/triage-regulatorio/SKILL.md:34-37`; `docs/PROMPT_MAESTRO_v2.md:285-287`.
- **D5-14.** El banco regulatorio son 5 preguntas de memoria, ninguna de clasificación.
  - La corrección por subcadena acepta respuestas erróneas.
  - Evidencia: `evals/dominio_semilla.jsonl:1-5`; `src/nutria/llm/router.py:144-160`.
  - Reproducido: reg-01, reg-02 y reg-05 dan True con respuestas falsas.
- **D5-15.** No hay acceso determinista a las fuentes regulatorias.
  - `settings.json` no preautoriza `joint-research-centre.ec.europa.eu`, el Registro de materias primas, `boe.es` ni `ema.europa.eu`.
  - Evidencia: `.claude/settings.json:9-31`; `docs/FUENTES.md:28-32`, `:78-80`; `docs/REGULACION_UE.md:65`.

**D6**
- **D6-9.** La heterogeneidad se da sin IC (Q-profile).
  - El intervalo de predicción no tiene pruebas y no coincide con `predict()` de metafor: en BCG da [−2,0084; 0,5793] frente a [−1,8667; 0,4376].
  - Evidencia: `src/nutria/stats/meta.py:229-238`; `src/nutria/cli.py:584`.
- **D6-10.** Incoherencias de la CLI:
  - FE se cambia por REML sin avisar.
  - PM con moderadores aborta.
  - No admite moderadores categóricos.
  - Con `--decimal ','`, «1.0» se lee como 10.
  - Evidencia: `src/nutria/cli.py:476-480`, `:541`, `:549`, `:558-559`; `src/nutria/stats/metareg.py:76-84`.
- **D6-11.** En la potencia, el efecto de diseño se calcula pero no se aplica.
  - Da 29 animales por grupo cuando harían falta 12 corrales.
  - Evidencia: `src/nutria/cli.py:600-606`; `src/nutria/stats/power.py:33-36`.
- **D6-12.** Las pruebas no cubren lo propio de la ciencia animal, y la documentación presenta como validados St-Pierre y la potencia.
  - Evidencia: `tests/test_meta.py:8-79`; `tests/test_power.py:11-14`; `docs/REVISION_PROMPT_ORIGINAL.md:130-132`.

**D7**
- **D7-13.** No hay paginación: se descarta `next_cursor`, OpenAlex siempre pide `page=1` y PubMed no usa `retstart`.
  - Se ignoran `errorlist` y `warninglist`.
  - Evidencia: `src/nutria/sources/europepmc.py:46-55`; `src/nutria/sources/openalex.py:72-84`; `src/nutria/sources/pubmed.py:22-35`.
  - Reproducido: una frase no encontrada da «0 resultados» sin aviso.
- **D7-14.** Los errores son confusos:
  - Un recurso inexistente aparece como «Error de red».
  - `doctor` hace 9 peticiones aunque se use `--offline` y siempre sale con 0.
  - `ghs_hazards` devuelve `[]` ante errores que contienen «404».
  - Evidencia: `src/nutria/http.py:26-27`, `:115-116`; `src/nutria/cli.py:85-120`; `src/nutria/sources/pubchem.py:53-64`.
- **D7-15.** STRING se consulta sin URL versionada, y no se registran las versiones de STRING, Reactome, ChEMBL ni UniProt.
  - Evidencia: `src/nutria/sources/stringdb.py:5`; `src/nutria/sources/reactome.py:7-8`; `src/nutria/cli.py:90-94`.
- **D7-16.** Licencias inexactas:
  - Feedipedia aparece como «Abierto», pero solo permite uso no comercial.
  - Faltan las condiciones comerciales de Lens (no confirmado en sus términos oficiales).
  - Reactome se da como CC BY cuando es CC0.
  - No se registra la licencia de cada dato.
  - Evidencia: `docs/FUENTES.md:45`, `:64`, `:71`; `src/nutria/sources/base.py:72-92`.
- **D7-17.** Los DOI SICI antiguos de Wiley se truncan en «<».
  - Evidencia: `src/nutria/verify/citations.py:22`, `:45-53`. Reproducido.

**D8**
- **D8-10.** No se pueden reconstruir puntuaciones ni rankings anteriores.
  - Las puntuaciones se sobrescriben (UPSERT).
  - La economía no guarda el hash del TOML.
  - Los dos workflows escriben el mismo JSON de puntuación.
  - Evidencia: `src/nutria/db.py:397-404`; `src/nutria/cli.py:431-432`, `:451-452`, `:627-628`.
- **D8-11.** La rúbrica está anclada en el texto, pero no en los datos: acepta cualquier valor entre 0 y 1, por ejemplo 0,37.
  - Evidencia: `src/nutria/data/schemas/puntuacion.schema.json:17-19`; `src/nutria/db.py:392-396`.
- **D8-12.** La respuesta umbral del informe confunde.
  - Omite los demás beneficios y mezcla el coste mediano con el valor modal.
  - Da 0,610 kg/d frente a una respuesta modal de 0,5, con un neto de +0,82 EUR.
  - Evidencia: `src/nutria/econ/farm.py:115-125`, `:139-140`.
- **D8-13.** El presupuesto parcial se queda corto:
  - no calcula el coste por tonelada, y a la fórmula de la skill le falta «/1000»;
  - no admite correlaciones ni hace análisis de sensibilidad;
  - no indica la fuente de cada entrada;
  - no compara con alternativas comerciales.
  - Evidencia: `src/nutria/econ/farm.py:64`; `.claude/skills/evaluacion-economica/SKILL.md:15`, `:28`; `docs/PROMPT_MAESTRO_v2.md:241`, `:341`.
- **D8-14.** El rNPV es correcto pero básico:
  - Las ventas pico son un dato de entrada.
  - Los años y la tasa de descuento son fijos.
  - No incluye capex, impuestos ni valor terminal.
  - El coste regulatorio siempre es [SUP].
  - Evidencia: `src/nutria/econ/rnpv.py:47-53`, `:58-72`; `docs/PROMPT_MAESTRO_v2.md:343-344`.
- **D8-15.** Las pruebas no cubren la semántica crítica de D8.
  - El rNPV solo se prueba con tasa 0.
  - El auditor comprobó a mano el caso al 10 %: −56,8625, coincidente.
  - Evidencia: `tests/test_scoring.py:30-56`; `tests/test_econ.py:39-59`.

**D9**
- **D9-14.** `fuente agregar --doi` falla con los DOI que solo están en OpenAlex, y el mensaje de error muestra el correo.
  - Evidencia: `src/nutria/cli.py:196-202`; `src/nutria/verify/citations.py:131-136`.
- **D9-15.** El límite de frecuencia se aplica por proceso.
  - Espera con el bloqueo tomado, e incluso después del último intento.
  - Evidencia: `src/nutria/http.py:35-45`, `:56-68`, `:111-123`.
- **D9-16.** Inconsistencias de la CLI:
  - `--offline doctor` sigue usando la red.
  - El router LLM no respeta el modo sin red.
  - `--json` se ignora en varias órdenes de escritura.
  - `init` con `NUTRIA_HOME` escribe `nutria.toml` en el directorio actual.
  - Evidencia: `src/nutria/cli.py:72-79`, `:102-105`, `:350`, `:404`, `:433`, `:438`, `:463`; `src/nutria/llm/router.py:73`, `:86`, `:100`; `src/nutria/config.py:43-45`.

**D10**
- **D10-7.** Si todas las vi son iguales, Egger aborta todo el metaanálisis con «Singular matrix», aunque el modelo de efectos aleatorios se calcule bien.
  - Evidencia: `src/nutria/cli.py:536-537`; `src/nutria/stats/meta.py:265`.
  - Reproducido con 3 estudios y vi = 0,72.
- **D10-8.** Al menos 9 órdenes ignoran `--json`, y los códigos de salida no están documentados y son incoherentes.
  - Evidencia: `README.md:104-105`; `CLAUDE.md:65`; `src/nutria/cli.py:81`, `:350`, `:404`, `:433`, `:438`, `:463`, `:525`, `:664`, `:669`, `:979-987`.
- **D10-9.** Los workflows se presentan como órdenes con barra sin explicar cómo se invocan ni cómo se pasan sus argumentos.
  - Dos de ellos exigen argumento.
  - «La misma misión» oculta que la versión automática no tiene controles humanos.
  - Evidencia: `README.md:64`, `:66`, `:70`; `CLAUDE.md:112-114`; `.claude/workflows/evaluar-oportunidad-auto.js:14-18`; `.claude/workflows/red-team-auto.js:14-15`.
- **D10-10.** `verificar <md> --sellar` no está documentado y sella sin exigir las secciones.
  - Reproducido: el mismo informe da 19 errores con `informe comprobar` y «Sello válido» con `verificar --sellar`.
  - Evidencia: `src/nutria/cli.py:249`, `:766`; `.claude/skills/estado/SKILL.md:12`.
- **D10-11.** Los ejemplos del README no funcionan tal cual en una instalación nueva.
  - `README.md:101` da «No such file», `:93` devuelve una tabla vacía y `:81` y `:85` necesitan red.
- **D10-12.** `doctor` sale con 0 aunque todo esté bloqueado e ignora `--offline`.
  - No comprueba Lens, los dominios de WebFetch, R ni el intérprete del hook.
  - Evidencia: `src/nutria/cli.py:85-95`, `:102-105`, `:120`; `docs/FUENTES.md:76-80`.

**D11**
- **D11-3.** R6 (St-Pierre 2001), la base del enfoque de modelos mixtos, se cita sin DOI y con la portada genérica de la revista.
  - El DOI existe: `10.3168/jds.S0022-0302(01)74530-4`, obtenido por WebSearch.
  - Evidencia: `docs/REVISION_PROMPT_ORIGINAL.md:264`; `docs/PROMPT_ORIGINAL.md:109`.

## 7. Plan de corrección priorizado

Criterio de prioridad:

1. Primero, que los controles que el sistema anuncia **fallen en cerrado** y que la firma humana se imponga en el código.
2. Después, la independencia de la evaluación y el método estadístico.
3. Luego, el contenido de dominio.
4. Por último, ingeniería y documentación.

Cada acción indica los hallazgos que corrige. Las acciones proceden de los hallazgos y de las correcciones que propusieron los propios auditores; no añaden defectos nuevos.

### P0. Imprescindible antes de producir investigación real

1. **Que el sello solo certifique lo que se ha verificado de verdad** (D1-1, D3-1, D7-3, D10-1, D3-2, D3-6, D5-7, D9-6, D10-10, D3-13).
   - `en_linea` debe reflejar el resultado obtenido, no la opción que se pidió.
   - Una referencia en estado `offline`, `error_red`, solo con URL o con `retracted=None` debe impedir el «Sello válido», o como mínimo dejarlo marcado de forma explícita como condicional.
   - El código de salida debe ser distinto de 0 en ese caso.
   - El mensaje «referencias verificadas en línea» solo debe aparecer cuando lo estén todas.
   - El sello debe registrar qué se comprobó (secciones y modo) y hacer lo mismo con `verificar --sellar` y con `informe comprobar`.
   - El hash debe calcularse sobre el texto comprobado, no sobre el fichero en el momento de sellar.
   - El sello debe quedar ligado a un evento de procedencia.
   - Una referencia [Rn] duplicada debe dar error.
   - Para la normativa, exigir CELEX y fecha de la versión consolidada, o una verificación manual registrada.
2. **Retractación y caché** (D7-1, D9-2, D3-8, D9-3, D7-5, D3-9, D7-10).
   - Leer el campo `updated-by` de Crossref.
   - Tratar una retractación desconocida como impedimento para sellar.
   - Verificar sin caché antes de sellar.
   - Mostrar la antigüedad de la caché y quitar las credenciales de su clave.
   - No pasar nunca `retracted` de 1 a 0 ni sobrescribir metadatos verificados con otros sin verificar.
   - Tratar la clave de OpenAlex como obligatoria y comprobarla en `doctor`.
3. **Hook** (D3-5, D1-5, D9-10, D10-2).
   - Añadir un control previo (PreToolUse) y hacer que falle en cerrado ante un `ImportError` o un sello corrupto.
   - Usar el intérprete del venv, o que `doctor` compruebe cuál se usa.
   - Cubrir lo que se escribe por Bash o por la CLI, así como `research/planes`, `research/revisiones` y `research/datos`.
   - Comprobar `revisado_por_humano`.
   - Leer el campo «estado» de forma robusta.
4. **Secretos** (D7-2, D9-1, D9-14). Eliminar `api_key`, `email` y `mailto` de `resp.url` y de los mensajes de error antes de escribirlos en el sello o mostrarlos por pantalla.
5. **Compuertas y firma humana en el código** (D1-2, D2-4, D2-5, D5-8, D5-9, D8-1, D8-2, D8-3, D3-11, D1-11).
   - Añadir `puntuar compuerta --firma-humana` y distinguir entre compuerta propuesta y validada.
   - Que las compuertas en `no_resuelta` no compitan con las APTA ni puedan entrar en el top-k.
   - Que `en_validacion` y `promovida` exijan compuertas superadas y firma.
   - Excluir del ranking las oportunidades en estado de registro «bloqueada».
   - Que la ruta `fuera_de_alcance_medicamento` bloquee automáticamente la compuerta regulatoria.
   - Conservar el historial y la evidencia de cada cambio de compuerta y llevarlo a la procedencia.
   - Que un agente no pueda firmar: como mínimo, poner `--firma-humana` en la lista `ask` de `.claude/settings.json`, tal como propone D2-5.

### P1. Independencia, método estadístico y workflows

6. **Evaluación independiente** (D1-3, D2-1, D2-2, D2-3, D8-9, D2-14, D2-11, D1-14).
   - Crear un agente evaluador propio, sin la orden de consultar el registro ni el ranking.
   - Activar `--sin-puntuaciones` por defecto.
   - Que los evaluadores no lean el informe que contiene la decisión propuesta.
   - Validar `evaluador` contra `--agente` e impedir que se sobrescriban las puntuaciones de otro evaluador.
   - Exigir al menos 3 evaluadores por criterio.
   - Variar el modelo y, cuando haya claves, usar una segunda familia de modelos para la crítica y la verificación.
   - Limitar a la sesión principal el rol de orquestador que define `CLAUDE.md`.
7. **Metaanálisis** (D6-1, D1-4, D10-6, D9-5, D1-8, D6-2, D6-6, D6-7, D6-4, D4-9, D9-4, D6-8, D6-5, D6-3).
   - `tipo_disp` obligatorio, con una única nomenclatura de columnas para exportar y analizar.
   - Detectar estudios repetidos y avisar o bloquear; hacer el uno-fuera y los grados de libertad por estudio.
   - Ofrecer un modelo multinivel o robusto en Python, o comprobar que R está disponible.
   - Calcular Egger solo con k ≥ 10, sin abortar el análisis.
   - HKSJ con la variante max(1, q) y control de q = 0.
   - St-Pierre con el estudio como efecto fijo o centrado por estudio, gl corregidos y pruebas.
   - Capturar las excepciones previsibles.
   - Añadir medidas binarias con campos de eventos y validación agrupada por estudio.
8. **Workflows** (D1-7, D2-7, D2-8, D4-3, D2-9, D2-10, D9-11, D1-9, D2-6, D2-13, D8-8, D5-10, D2-12, D1-12, D10-3).
   - `evaluar-oportunidad-auto`:
     - Hacer una criba previa con `descarte_rapido` (aspecto regulatorio, destino digestivo y coste por animal y día) y parar si alguna compuerta está bloqueada.
     - Incluir al investigador bibliográfico, al extractor y al estadístico.
   - `red-team-auto`:
     - Exigir un quórum mínimo.
     - Separar el estado `no_verificable`.
   - `mision-1-auto`:
     - Dividirlo en fases con revisión humana entre ellas.
     - Validar en código el top 3.
     - Conservar las soluciones existentes.
     - Incluir al toxicólogo en la compuerta de seguridad.
     - Ejecutar el modelo económico antes de puntuar.
     - Llevar a la orden del plan y a la lista del crítico los requisitos de validación: Directiva 2010/63/UE, FEEDAP y notificación a EFSA.
   - Orquestar la doble extracción con rutas y actores distintos y una orden de conciliación.
   - Fijar un presupuesto y un número máximo de oportunidades.

### P2. Dominio

9. **Regulación** (D5-1, D1-6, D5-2, D5-3, D5-4, D5-5, D5-6, D5-7, D5-11, D5-16).
   - Incorporar el criterio funcional del art. 4.1.b del Reglamento (UE) 2019/6 en el árbol, la skill, el agente y `CLAUDE.md`.
   - Reordenar el árbol: pienso dietético y excepciones del art. 13.3 antes del nodo de medicamento.
   - Añadir nodos para aditivo nutricional y premezcla.
   - Crear una lista de sustancias prohibidas o restringidas: art. 11.2 del Reglamento 1831/2003, anexo III del 767/2009 y Directiva 96/22/CE.
   - Incluir el Reglamento 2019/1381, el 378/2005 y la guía FEEDAP de eficacia de 2024.
   - Añadir las alegaciones permitidas y un campo para la alegación prevista.
   - Verificar en EUR-Lex las 18 normas pendientes.
   - Añadir casos de prueba para el árbol.
10. **Nutrición y fisiología** (D4-2, D1-13, D4-4, D4-5, D4-6, D4-7, D4-8, D4-10).
    - Añadir campos estructurados:
      - destino digestivo y escape ruminal;
      - base de la dosis;
      - componentes de la dieta basal;
      - momento respecto al parto;
      - definición de caso o umbral.
    - Distinguir «no consta» de «olvidado» y avisar de los campos descartados.
    - Convertir la dosis entre bases y calcular el coste por tonelada.
    - Convertir el destino digestivo en compuerta o veto.
    - En el modelo del ganadero, separar los días de coste de los de respuesta.
    - Añadir al banco preguntas de nutrición y fisiología.
11. **Economía y priorización** (D1-10, D8-4, D8-5, D8-6, D8-7).
    - Dividir el criterio de potencial económico en dos: ganadero y empresa.
    - Redactar las anclas de forma coherente con el ROI que calcula el código.
    - Comparar los perfiles de pesos en código y justificar alfa.
    - Construir el escenario conservador teniendo en cuenta el signo de la respuesta.
    - Rotular bien P(rNPV>0) y añadir la probabilidad de pérdida.

### P3. Fuentes, ingeniería, documentación y validación

12. **Fuentes** (D7-4, D7-6, D7-7, D7-8, D7-9, D7-11, D7-12, D9-8).
    - Limitar la frecuencia entre procesos y no superar 5 peticiones/s en Crossref.
    - Que `fuente agregar` acepte DOI que solo estén en OpenAlex.
    - Registrar en la procedencia los aciertos de caché y los fallos.
    - Aplicar las fechas en `buscar efsa`.
    - Filtrar en el servidor de ChEMBL.
    - Hacer pruebas en vivo cuando haya red.
13. **Ingeniería** (D9-7, D9-9, D9-12, D9-13, D3-7, D3-10, D3-12).
    - Guardar el historial de puntuaciones y las huellas de las entradas en la procedencia.
    - Hacer atómicas las altas por lotes.
    - Calcular el año de la cita sin contar el que aparece en el DOI, y comparar los autores.
    - Que [PUB] solo admita fuentes verificadas y no retractadas.
    - Añadir CI, fichero de bloqueo de dependencias y pruebas de rutas de error, del hook y de los workflows.
14. **Documentación** (D10-1, D10-3, D10-4, D10-5, D2-13). En el README:
    - declarar los límites reales y que la capa de agentes no se ha ejecutado;
    - explicar qué no hace (modelización) y que R es un requisito;
    - estimar el coste por misión;
    - matizar «determinista» y «reproducible»;
    - registrar la versión del modelo de cada subagente.
15. **Validación final** (D11-1, D4-1, D5-16).
    - Una vez cerrado P0, ejecutar la misión 1 con red y los dominios permitidos.
    - Verificar una muestra de referencias.
    - Repetir esta auditoría.
    - Para comparar con motor-ganadero, aportar su repositorio o su carpeta (`/home/user/COMPARAR_EN_LOCAL.md:41-45`).

## 8. Limitaciones de esta evaluación

1. **Solo se evaluó un sistema.** motor-ganadero no está en este entorno, así que no hay comparación: la columna «mejor» es n/a en todas las dimensiones y no se puede proponer qué adoptar de cada sistema.
2. **Las puntuaciones no tuvieron una segunda verificación.** Con un único sistema, el arnés no verifica los veredictos (`comparar-sistemas.js:125-131`). Los hallazgos sí se verificaron uno a uno.
3. **No había red hacia las fuentes.**
   - El proxy denegó la conexión (CONNECT 403) a los 9 hosts de `nutria doctor` y a `api.lens.org`.
   - WebFetch no resolvió EUR-Lex, legislation.gov.uk, doi.org, efsa.europa.eu, crossref.org ni otros dominios de documentación.
   - Consecuencias:
     - Ningún cliente de API se probó en vivo. Las reproducciones que implican red usaron respuestas simuladas.
     - Los textos legales no se leyeron en fuente primaria. Las valoraciones se apoyan en el conocimiento de los auditores y en búsquedas web secundarias, y deben revisarse con un profesional regulatorio. Afecta a:
       - art. 4.1.b del Reglamento 2019/6;
       - arts. 5.3.d y 11.2 del Reglamento 1831/2003;
       - arts. 10 y 13 y anexo III del Reglamento 767/2009 (el art. 13 se contrastó en legislation.gov.uk);
       - Reglamento 2019/1381 y arts. 32 bis y 32 ter del Reglamento 178/2002;
       - Reglamento 378/2005;
       - anexo de la Recomendación 2011/25/UE;
       - considerando 16 del Reglamento 1829/2003.
     - Hay hechos externos confirmados solo mediante búsqueda web:
       - clave obligatoria de OpenAlex desde el 13-2-2026;
       - límites de Crossref desde el 1-12-2025 y su campo `updated-by`;
       - guía FEEDAP de 2024;
       - licencias de Feedipedia, Reactome y Lens;
       - DOI de St-Pierre (2001).
     - De lo que los documentos declaran pendiente (6 referencias y 18 normas), solo se comprobaron R2 y R11. Formaban parte de una muestra de 7 referencias que, junto con el Reglamento 2024/2899, se verificó por búsqueda web. El resto sigue sin verificar.
4. **R no está instalado.** No se ejecutó metafor ni el script de `nutria meta exportar-r`. La concordancia con metafor se basa en los valores de referencia fijados en `tests/test_meta.py:11-64` y en statsmodels, y el intervalo de predicción de metafor se calculó a mano.
5. **La capa de Claude Code no se ejecutó con agentes reales.** Los workflows solo se ejecutaron con `agent()` simulado. Quedan sin comprobar en ejecución:
   - si los subagentes reciben `CLAUDE.md` (D2-11);
   - si `/mision-1-auto` funciona como orden con barra (el texto del binario sugiere que sí);
   - si los evaluadores llegan a leer puntuaciones ajenas (D2-1);
   - el coste real en tokens: las cifras de D10-3 son recuentos de llamadas simuladas.
6. **No hay resultados de investigación.** D4, D5 y D11 no pudieron juzgar la exactitud aplicada; los riesgos descritos son latentes.
7. **Algunas cifras no se pudieron rehacer exactamente** al verificar: D4-9, D6-4 y D9-5, por falta de parámetros o de los CSV, y D7-11, cuyo resultado estaba garantizado por cómo se construyó la prueba. Los mecanismos sí se confirmaron.
8. **Efectos de la auditoría sobre el sistema.** Todo se hizo en solo lectura, salvo este informe, que está fuera de `/home/user/nutria`.
   - **Fichero residual.** A las 17:22 UTC, cuando se redactó este informe, existe `src/nutria/__pycache__/__init__.cpython-313.pyc`, fechado a las 15:08:02 UTC.
     - No está en el commit ni en `nutria.zip` (11:12), y git lo ignora (`!!`).
     - Probablemente lo creó algún proceso de esta auditoría al importar el paquete, que está instalado en modo editable, pero no se puede atribuir con certeza. Un auditor ya había creado y borrado otro antes, y otros auditores no vieron ninguno en su momento.
     - No se ha borrado para no modificar el sistema. Puede eliminarse sin ningún efecto con `rm -r /home/user/nutria/src/nutria/__pycache__`.
   - **Fechas de modificación y estado del repositorio.** Cambiaron las fechas de `src/nutria/` (15:08) y de `.git/` (17:14 UTC). A las 17:23 UTC, `git --no-optional-locks status --porcelain --ignored` solo mostraba `?? .claude/workflows/comparar-sistemas.js` y `!! src/nutria/__pycache__/`.
   - **Cambios ajenos a la auditoría.** Desde las 17:44 UTC aparecieron los cambios sin confirmar que se describen en la cabecera. Esta auditoría no los hizo y no los ha evaluado. Con ellos aparecieron también `.pytest_cache/` y `__pycache__/` en varios subpaquetes y en `tests/`.
   - **Arnés dentro del sistema.** `.claude/workflows/comparar-sistemas.js` sigue dentro del árbol de nutria sin versionar. Si se usa el sistema tal cual, aparecerá un cuarto workflow que no forma parte de lo entregado.
9. **Fecha de referencia.** Los hechos externos (versiones de guías, políticas de API y licencias) se valoraron a 2026-11-04.

## 9. Revisión de completitud

**Alcance y método.** Esta revisión crítica del informe se hizo el 2026-11-04, hacia las 18:10 UTC, en modo de solo lectura sobre `/home/user/nutria`.

- **Citas.** Las de nutria se comprobaron con `git show 236a161:<ruta>`. Las líneas de este informe se citan como «l. N».
- **Pruebas y cobertura.** Se ejecutaron en una copia de 236a161 (`git archive`) en un directorio temporal, sin caché de pytest ni bytecode. No se escribió nada dentro de `/home/user/nutria`.
- **Independencia.** Esta revisión es la fase «completitud» del mismo arnés (`.claude/workflows/comparar-sistemas.js:168-172`) y la hizo el modelo claude-opus-5-5. Comparte, por tanto, la limitación de independencia descrita en 9.2.

### 9.1 Lo que se comprobó y se sostiene

- **El inventario es exacto.**
  - 4.516 líneas de Python en `src/nutria` y 991 en `cli.py`.
  - 14 agentes (7 opus y 7 sonnet) y 13 skills, 7 de ellas con `disable-model-invocation`.
  - Workflows de 215, 85 y 59 líneas; hook de 86 líneas; `CLAUDE.md` de 114.
  - 21 filas normativas, 3 de ellas «verificada», y 23 dominios para WebFetch.
  - `research/` solo contiene 7 `.gitkeep`.
- **Pruebas y cobertura.**
  - La copia de 236a161 da «42 passed». Son 41 funciones más una parametrizada con 2 casos, lo que cuadra con las «41 funciones» de D5-16.
  - Con coverage 7.17.1, la cobertura total es del 61 % y los valores por módulo coinciden con los del informe: `cli.py` 52 %, `db.py` 62 %, `http.py` 26 %, `stats/meta.py` 88 %, `scoring/engine.py` 96 %, y 0 % en el router, en `mixed` y en 5 clientes.
- **Los hallazgos graves muestreados se confirman en el código:**
  - sello válido con avisos de red, y `en_linea` según lo pedido (`src/nutria/reports/check.py:133-135`, `:147-169`);
  - referencias solo con URL sin verificar (`:113-114`) y `\b` tras «%» (`:32`);
  - una CONDICIONAL se ordena como una APTA (`src/nutria/scoring/engine.py:172-180`, `:230-233`);
  - compuerta sin `--firma-humana` y sin campo de firma (`src/nutria/cli.py:854-859`; `src/nutria/db.py:80-86`);
  - el SEM se trata como DE (`src/nutria/cli.py:505`);
  - los tres evaluadores son `director-cientifico` (`.claude/workflows/mision-1-auto.js:142-171`) y su prompt les manda consultar el ranking (`.claude/agents/director-cientifico.md:23-24`);
  - mayoría sin quórum en `red-team-auto` (`.claude/workflows/red-team-auto.js:37-41`);
  - el árbol empieza por «¿prevenir, tratar o curar?» (`docs/REGULACION_UE.md:12-13`);
  - el hook deja pasar todo ante un ImportError (`scripts/hooks/check_research_file.py:81-82`).
- **motor-ganadero.**
  - El usuario indica que motor-ganadero es el nombre del otro sistema en Code, y el informe lo trata así (l. 24).
  - Sigue sin estar accesible. En el sistema de ficheros no hay ninguna ruta con «ganadero», salvo un ejemplo de nutria. La sesión tampoco tiene acceso a ningún repositorio de GitHub cuyo nombre contenga «motor» o «ganadero».
  - Es correcto que el informe no le atribuya nada.
- **Precisiones.**
  - **D7-4.** Cita `src/nutria/config.py:20` para el intervalo de 0,05 s de Crossref. Esa línea es el correo vacío, que solo importa para el pool público; el intervalo está en `src/nutria/http.py:37`.
  - **Inicio de los cambios ajenos.** No empezaron a las 17:44 (l. 6, l. 2111):
    - A las 17:39:54 UTC hubo una ejecución de pytest dentro del árbol (`.pytest_cache/`, `tests/__pycache__/`).
    - `src/nutria/data/rubrica.toml` cambió a las 17:43:03.
    - No se puede atribuir a nadie.
  - **Los cambios continúan.**
    - `src/nutria/http.py` cambió a las 18:05:46, durante esta revisión pero no por ella.
    - Hacia las 18:07, `git diff --stat HEAD` daba 39 ficheros y 4.064 líneas añadidas, no 32 y 3.500.
    - Los hallazgos siguen valiendo para 236a161, pero la descripción del árbol de trabajo está desfasada.

### 9.2 Independencia del marco de evaluación y sesgo a favor de nutria

Es lo más importante, y el informe no lo declara, aunque se presenta como «externa e independiente» (l. 3).

1. **Los criterios salen del autodiagnóstico de nutria.**
   - Los 12 criterios de D1 (`comparar-sistemas.js:79`) se corresponden uno a uno con C1, C2, C3, C4 (error y omisiones), C6, I1, C7, C8, I6, I4 e I5 de `docs/REVISION_PROMPT_ORIGINAL.md:38-204`. Coinciden incluso los ejemplos: el 2019/962, los donantes de líquido ruminal y la Directiva 2010/63/UE.
   - Los criterios de D6 (`comparar-sistemas.js:89`) repiten C6 y su lista de implementación (`docs/REVISION_PROMPT_ORIGINAL.md:110-133`). Los de D8 (`comparar-sistemas.js:93`) repiten C7 y C8 (`docs/REVISION_PROMPT_ORIGINAL.md:134-153`).
   - Por construcción, D1 solo mide si se detectan fallos que nutria ya había identificado. Por eso «Diagnostica bien los 12 fallos» (l. 27, l. 171) y «No baja a 2 porque la detección es completa» (l. 180) son circulares.
   - Hay indicios de que el arnés se escribió desde el lado de nutria, aunque la autoría no se puede confirmar:
     - `/home/user/COMPARAR_EN_LOCAL.md:13` llama a nutria «este sistema»;
     - `comparar-sistemas.js` está fechado a las 11:20:08 UTC, 8 minutos después de `nutria.zip` y 37 segundos antes que `COMPARAR_EN_LOCAL.md`.
   - `COMPARAR_EN_LOCAL.md:19-29` propone usar este mismo arnés para comparar con motor-ganadero. El sesgo pasaría a la comparación: motor-ganadero se mediría con las prioridades de diseño de nutria.
2. **Todas las fases las hace el mismo agente.**
   - Ningún `agent()` del arnés fija `agentType` ni modelo (`comparar-sistemas.js:67-72`, `:103-112`, `:119-124`, `:155-172`). Auditor, verificador y redactor son, previsiblemente, el mismo agente por defecto.
   - Es la misma independencia nominal que el informe reprocha a nutria (D1-3, D2-3, D2-14).
   - El informe tampoco registra qué modelo y qué versión hicieron cada fase, que es lo que exige a nutria (D3-12, D10-4).
   - Por eso, que la verificación descartara 0 de 111 hallazgos (l. 1692) no prueba, por sí solo, que sean sólidos.
3. **La verificación es asimétrica.**
   - Solo se verifican los hallazgos de gravedad alta y media, y al verificador se le pide refutarlos (`comparar-sistemas.js:115-122`).
   - Nadie comprueba si alguna debilidad calificada de «baja» merecía más gravedad.
   - La verificación rebajó 16 hallazgos (l. 1696) y no subió ninguno, ni siquiera cuando encontró agravantes (l. 1754-1764; por ejemplo, D3-9 sigue en media).
   - El efecto neto favorece al sistema auditado.
4. **Las puntuaciones no se verifican en ningún modo.**
   - Incluso al comparar, el arnés solo intenta refutar el texto del veredicto y el campo «mejor» (`comparar-sistemas.js:125-131`). Tampoco recalcula las puntuaciones después de la verificación.
   - Por tanto, «Como solo había un sistema … Por eso ninguna puntuación tiene una segunda verificación» (l. 165) es una inferencia errónea: en la comparación con motor-ganadero tampoco se verificarán.
5. **D11 = n/a contradice la escala del propio arnés.**
   - La escala reserva «n/a» para las dimensiones que no aplican (`comparar-sistemas.js:101`). Como el encargo exige la primera misión (`docs/PROMPT_ORIGINAL.md:115-118`), lo coherente sería 0 («ausente»). El informe sigue la regla contraria de `comparar-sistemas.js:99`.
   - El argumento de la doble penalización con que se rebaja D4-1 (l. 995) no se sostiene: D11 no penaliza (l. 167), así que no hay una primera penalización.
   - En consecuencia, el incumplimiento más visible del encargo, que no se haya ejecutado ninguna misión, se trata de forma incoherente:
     - D11 no lo penaliza;
     - la verificación rebaja D4-1 y D11-1 para no penalizarlo;
     - la nota de D4 lo sigue citando (l. 256).

### 9.3 Puntuaciones que no se siguen de la escala ni de los hallazgos

6. **La escala no se aplica de forma coherente.**
   - En la escala (l. 12-19), 2 es «parcial o con errores importantes» y 3 es «correcto pero básico». Aun así, D1, D6, D8 y D10 reciben 3 con hallazgos confirmados de gravedad alta:
     - **D1.** Reconoce errores importantes reproducidos en 4 de sus 12 criterios (1, 2, 3 y 11) y solo 3 corregidos sin reservas (4, 5 y 8) (l. 173-178).
     - **D8.** Incumple su propio criterio de «compuertas que bloquean» (`comparar-sistemas.js:93`; D8-1 y D8-2).
     - **D6.** Tiene D6-1, el ZeroDivisionError de HKSJ (D6-7) y un Egger que aborta el análisis (D6-6).
     - **D10.** Tiene D10-1 y D10-6.
   - La correspondencia entre hallazgos y nota no es estable:
     - con 1 hallazgo de gravedad alta, D9 recibe 2, mientras que D6 y D10 reciben 3;
     - con 2, D5 y D7 reciben 2, y D8 recibe 3;
     - con 3, D2 recibe 2, y D1 recibe 3.
   - Si la escala se aplicara literalmente, ninguna dimensión pasaría de 2, y la media sería 2,0 o menos, no 2,4 (l. 26). El informe debería explicitar con qué regla se aparta de la escala.
7. **La regla contra la doble penalización se aplica de forma selectiva.**
   - Se invoca para rebajar D4-1 y D11-1.
   - Sin embargo, el defecto del sello cuenta como 4 hallazgos de gravedad alta (D1-1, D3-1, D7-3 y D10-1) y el de las compuertas como 5 (D1-2, D2-4, D5-8, D8-1 y D8-2). Así, cada uno penaliza 4 o 5 dimensiones.
8. **Los recuentos no son de defectos distintos.**
   - Los 21 hallazgos de gravedad alta corresponden a unos 12-13 defectos:
     - el sello ante fallos de red;
     - las referencias que solo tienen URL;
     - el respaldo de la afirmación por la fuente;
     - el etiquetado;
     - el hook;
     - la retractación;
     - las compuertas que no bloquean;
     - las compuertas sin firma;
     - la independencia de los evaluadores;
     - los especialistas que faltan en `evaluar-oportunidad-auto`;
     - la frontera con el medicamento;
     - el racionamiento;
     - el SEM.
   - «111 (21/82/8)» (l. 30) cuenta hallazgos por dimensión, no defectos. Convendría añadir un recuento por causa raíz.
9. **El mismo defecto recibe gravedades distintas.**
   - El SEM tratado como DE aparece como D6-1 (alta), D1-4 (media) y D10-6 (media).
   - La rebaja de D1-4 se apoya en que «Con HKSJ, el efecto combinado cambia poco» (l. 730). Pero HKSJ es opcional (`src/nutria/cli.py:892`, `store_true`), como recuerda el propio D6-7 (l. 1243).
   - Con la prueba z, la que se usa por defecto, p pasa de 0,053-0,056 a 1,2e-9-2,6e-9 (l. 708-709, l. 725-726). La rebaja no se sigue de los datos.

### 9.4 Afirmaciones sin evidencia suficiente o contradictorias

10. **D10, «la coherencia entre documentación y código» (l. 361).**
    - Contradicen esta fortaleza:
      - D10-1 y D10-6;
      - D2-7, porque el README presenta `evaluar-oportunidad-auto` como «Lo mismo» que `/oportunidad`;
      - D2-4, porque `.claude/workflows/mision-1-auto.js:214` promete un `--firma-humana` que no existe;
      - D10-9 y D10-11.
    - Su evidencia (l. 370) solo cubre el inventario, las opciones y la rúbrica.
11. **D7, «Endpoints y campos coherentes con la documentación pública» (l. 313).**
    - Solo cita el propio código.
    - Los dominios de documentación no eran accesibles (l. 2079).
    - D7-5 (OpenAlex) y D7-1 (`updated-by` de Crossref) la contradicen en parte.
12. **Resumen ejecutivo (l. 27).**
    - «Clientes reales para 10 APIs»: ninguno se ha probado en vivo (l. 128) y 5 tienen una cobertura del 0 %.
    - «La aritmética económica es correcta»: convive con D4-2 (coste 0 y ROI NaN sin aviso), D4-8, D8-5 y D8-6. La frase debería limitarse al rNPV y al muestreo.
13. **D2, «La parte formal es excelente y está comprobada» (l. 202).**
    - Usa la redacción del nivel 5 para algo que solo se ejecutó con `agent()` simulado (l. 2098-2102).
    - Además, D2-17 indica que los workflows no existen en el paquete npm del entorno.
14. **Contradicciones en D1.**
    - **Criterio 8 (falsa precisión).** Se califica de «Sólido en código y con pruebas» (l. 176), pese a D8-4 (confirmado: con alfa = 200 los pesos apenas se perturban), D8-11 y D1-14.
    - **Potencia y efecto de diseño.** El informe la presenta como fortaleza implementada (l. 192), mientras que D6-11 dice que se calcula pero no se aplica (l. 1841-1843). En realidad, la CLI muestra el efecto de diseño y una nota que pide multiplicar por él, pero no ajusta n (`src/nutria/cli.py:602-606`). Ninguna de las dos frases es exacta.
15. **D6-9 (l. 1832-1834) presenta como discrepancia que el intervalo de predicción no coincida con `predict()` de metafor.**
    - nutria usa t(k−2)·√(τ² + EE²) (`src/nutria/stats/meta.py:235-238`). Es la fórmula de Higgins, Thompson y Spiegelhalter (2009), la que recomienda el manual Cochrane.
    - Con dat.bcg da exactamente [−2,0084; 0,5793], porque t(11) = 2,201 y √(0,3132 + 0,1798²) = 0,5878. Con z = 1,96 sale [−1,8666; 0,4376], que es lo que metafor da por defecto.
    - Es una elección defendible; lo que falta es documentarla y probarla.
16. **D4-8 (l. 1059) atribuye a la plantilla una ventana de −21 a +21 d.**
    - La plantilla solo dice «vacas en transición» y `dias = 42` (`ejemplos/econ_ganadero_ILUSTRATIVO.toml:4-8`).
    - La conclusión es plausible con la definición habitual del periodo de transición, pero es un supuesto del auditor y debería presentarse como tal.
17. **Las reproducciones no se pueden repetir.**
    - No se conservan ni se adjuntan las entradas ni los scripts de:
      - T1.md, O-0002.md, H1-H6, S0-S7 y T3-T7;
      - las simulaciones de D4-9, D6-2, D6-4 y D8-4;
      - los «300 conjuntos aleatorios» (l. 282);
      - los «141 a 263 subagentes» (l. 1581).
    - Tampoco se identifican las 7 referencias de la muestra (l. 377).
    - El informe no cumple la trazabilidad que exige a nutria (D3-12, D9-7).
18. **Debilidades menores nunca verificadas que pueden estar infravaloradas.**
    - **D3-13 (l. 1797-1800).** Con un [Rn] duplicado gana la última entrada (`src/nutria/reports/check.py:69-73`). Así, una referencia inventada seguida de una real obtiene sello válido, incluso con red. Es de la misma clase que D3-2 (alta) y ya figura en P0 (l. 1947).
    - **D5-14 (l. 1823-1826).** La corrección por subcadena invalida el banco con el que se elegirían los modelos. El riesgo es latente.
    - **D9-16 (l. 1903-1908).** El router LLM ignora `--offline`, lo que afecta a la confidencialidad (criterio 11). Es latente mientras los modelos sigan inactivos.

### 9.5 Partes del encargo sin evaluación a fondo

Las 11 dimensiones siguen el diseño de nutria. Estas partes del encargo no tienen evaluación propia, ni de debilidades ni de fortalezas:

19. **Modelización molecular y simulaciones** (`docs/PROMPT_ORIGINAL.md:21`, `:73-76`, `:108`): docking, dinámica molecular, PK/PD, fermentación, digestibilidad y optimización de fórmulas.
    - El informe solo constata que no existen (D10-5, l. 1601; D4-2).
    - El criterio 7 de D1 adopta la postura de nutria (`docs/REVISION_PROMPT_ORIGINAL.md:157-163`), así que un sistema que implementara el §6 no recibiría crédito.
    - Falta juzgar si dejar fuera el §6 es una decisión justificada o un hueco.
20. **Diseño de producto, formulación y desarrollo industrial** (`docs/PROMPT_ORIGINAL.md:78-81`, `:85`, `:100`). El agente `desarrollo-industrial` (`.claude/agents/desarrollo-industrial.md:11-24`) y la compuerta «analitica» (`src/nutria/data/rubrica.toml:115-116`) solo aparecen en el inventario (l. 57).
21. **Sostenibilidad, bienestar y One Health** (`docs/PROMPT_ORIGINAL.md:37`, `:116`).
    - La propia nutria los declaró dimensiones ausentes (I7, `docs/REVISION_PROMPT_ORIGINAL.md:206-211`).
    - Aun así, el informe no evalúa:
      - el agente (`.claude/agents/sostenibilidad-bienestar.md:11-22`), que cubre IPCC 2019, ISO 14040/14044, Cinco Dominios y coselección de resistencias;
      - la compuerta «etica» (`src/nutria/data/rubrica.toml:125-127`).
22. **El contenido de la evaluación de seguridad** (`docs/PROMPT_ORIGINAL.md:90`; criterio en `src/nutria/data/rubrica.toml:57-58`): animal de destino, consumidor y residuos, usuario y medio ambiente. D5 juzga la clasificación y quién decide la compuerta, pero no la calidad de esa evaluación ni el agente `compuestos-toxicologia`.
23. **Generación de hipótesis y originalidad** (`docs/PROMPT_ORIGINAL.md:50-64`; 15 puntos en `:104`).
    - Puede ser una fortaleza no reconocida: `src/nutria/data/schemas/hipotesis.schema.json:5-7` exige los campos del §4, el efecto mínimo relevante y el criterio de falsación.
    - También admite probabilidad previa y técnica de generación (`:22-25`).
    - Su uso no se ha comprobado.
24. **Método de revisión sistemática.**
    - El cribado doble, el riesgo de sesgo (SYRCLE y REFLECT), la certeza de la evidencia y PRISMA solo existen como instrucciones (`.claude/skills/revision-sistematica/SKILL.md:10-22`; `.claude/agents/estadistico.md:27-31`).
    - El esquema de extracción no tiene ningún campo para el riesgo de sesgo (`src/nutria/data/schemas/extraccion.schema.json:7-41`), y ningún workflow orquesta el cribado doble.
    - El informe audita la doble extracción (D2-12), pero no esto, que alimenta el criterio de más peso: la evidencia, con 20 puntos.
25. **Especies distintas del vacuno lechero** (`docs/PROMPT_ORIGINAL.md:37`).
    - El informe no menciona ni una vez el vacuno de carne, el porcino, la avicultura, el ovino, el caprino ni la acuicultura.
    - `TAXA` sí las recoge (`src/nutria/sources/base.py:12-22`), pero no se han evaluado el racionamiento, los modelos ni las fuentes para monogástricos o acuicultura.
26. **Seguridad del sistema de agentes frente a la inyección de instrucciones.**
    - 12 de los 14 agentes tienen WebFetch y todos tienen Bash (`.claude/agents/*.md:4`).
    - `.claude/settings.json:4-6` permite sin confirmación `nutria *`, `python -m nutria *` y `python3 -m nutria *`.
    - Ni `CLAUDE.md` ni los agentes ni las skills piden tratar como datos el contenido descargado; una búsqueda no encontró nada.
    - Junto con D2-4 y D5-8, una página manipulada podría llevar a un agente a cambiar compuertas o firmar decisiones. Es un indicio, sin verificación adversarial.
    - El remedio mínimo de P0, poner `--firma-humana` en la lista `ask` (l. 1978), no lo impediría:
      - la misma orden puede lanzarse con `python -m nutria` o `python3 -m nutria`, que también están permitidas;
      - una firma tecleada por un agente no autentica a una persona.
    - Hace falta una autenticación fuera del alcance de los agentes.

### 9.6 Conclusión de la revisión

Los hallazgos están bien anclados en el código: todo lo muestreado se confirma, el inventario es exacto y las limitaciones se declaran con transparencia.

Las debilidades del informe son de método:

- un marco de evaluación derivado del autodiagnóstico de nutria y ejecutado por un mismo agente, sin declararlo;
- puntuaciones que no siguen la escala y que nadie verifica;
- una verificación que solo puede rebajar;
- recuentos inflados por duplicados;
- ocho partes del encargo sin evaluación a fondo.

Antes de usar este informe como línea base para comparar con motor-ganadero:

1. Declarar el origen del arnés y el modelo de cada fase.
2. Derivar los criterios de `docs/PROMPT_ORIGINAL.md`, no de la revisión de nutria, y añadir dimensiones para los puntos 19-26.
3. Puntuar con una regla explícita después de la verificación, y verificar también las puntuaciones.
4. Verificar las debilidades «baja» y permitir que la verificación suba la gravedad.
5. Dar los recuentos por defecto distinto.
6. Adjuntar los scripts y las entradas de las reproducciones.
