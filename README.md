# NUTRIA: I+D computacional en nutrición y producción animal

Sistema multiagente para Claude Code que investiga, genera hipótesis y prioriza nuevos ingredientes, combinaciones y
formulaciones para animales de producción (prioridad: rumiantes y vacuno lechero), con evidencia verificable y
decisiones trazables.

> **Antes de usarlo**: la capa de agentes (subagentes, skills y workflows de investigación) **nunca se ha ejecutado de
> principio a fin**, y los clientes de las APIs no se han probado contra los servicios reales. Lee
> [Garantías y limitaciones](#garantías-y-limitaciones) y el estado detallado de cada pieza en
> [`docs/ESTADO_Y_LIMITACIONES.md`](docs/ESTADO_Y_LIMITACIONES.md).

Tiene dos partes:

1. **La organización** (`CLAUDE.md` y `.claude/`): 15 subagentes especializados con contexto propio, 13 skills
   (misiones y procedimientos), 4 workflows (3 de investigación y 1 de auditoría) y hooks de Claude Code. Los hooks
   PreToolUse impiden a los agentes firmar decisiones (`--firma-humana`) y escribir sellos de verificación; el hook
   PostToolUse valida lo que se escribe en `research/`.
2. **Las herramientas** (`nutria`, paquete Python): búsqueda en PubMed, Europe PMC, OpenAlex y EFSA Journal;
   verificación de referencias (existencia, correspondencia con la cita, retractación); registro de evidencia con
   procedencia; doble extracción; metaanálisis y meta-regresión validados frente a metafor; destino ruminal (Ørskov y
   McDonald); potencia; priorización con compuertas firmadas por personas y Monte Carlo; economía del ganadero y de la
   empresa; informes con sello de verificación; segunda opinión multimodelo.

El método completo está en `docs/PROMPT_MAESTRO_v2.md`; por qué se cambió el prompt original, en
`docs/REVISION_PROMPT_ORIGINAL.md`; la arquitectura, en `docs/ARQUITECTURA.md`.

## Instalación

Requiere Python 3.11 o superior.

```bash
git clone <tu-repositorio> nutria
```

```bash
cd nutria
```

```bash
python -m venv .venv && source .venv/bin/activate
```

```bash
pip install -e ".[dev,mixto]"
```

```bash
nutria init
```

```bash
nutria doctor
```

- **Crea el entorno exactamente en `.venv`, dentro del proyecto.** El hook PostToolUse se ejecuta con
  `scripts/hooks/run_hook.sh`, que busca primero el intérprete de `.venv` (`.venv/bin/python`; en Windows,
  `.venv\Scripts\python.exe`) y, si no existe, `python3` o `python` del PATH. Si el intérprete que encuentra no tiene
  las dependencias de `nutria`, el hook bloquea las escrituras en `research/` que debía validar (falla cerrado, no se
  desactiva en silencio). Comprueba el del proyecto con `.venv/bin/python -c "import nutria, jsonschema"`.
- **Activa el entorno antes de abrir Claude Code** (`source .venv/bin/activate` y después `claude`): los agentes
  ejecutan `nutria` desde la terminal de la sesión y tienen que encontrarlo en el PATH.
- `nutria init` crea `nutria.toml` y la base de datos `research/nutria.sqlite`. Si quieres, rellena `email` en
  `nutria.toml` (lo piden NCBI y Crossref para su acceso preferente).
- Las claves de API van en variables de entorno, nunca en ficheros: `OPENALEX_API_KEY` (**OpenAlex la exige en todas
  las peticiones desde el 13-02-2026**; es gratuita en https://openalex.org/settings/api), `NCBI_API_KEY`,
  `LENS_API_TOKEN` y, para la segunda opinión multimodelo, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
  `DEEPSEEK_API_KEY`.
- `nutria doctor` comprueba qué fuentes son accesibles y qué claves hay. Dominios necesarios:
  `eutils.ncbi.nlm.nih.gov`, `pubchem.ncbi.nlm.nih.gov`, `www.ebi.ac.uk`, `api.openalex.org`, `api.crossref.org`,
  `rest.uniprot.org`, `string-db.org`, `reactome.org` (detalle en `docs/FUENTES.md`).
- **R no es un requisito de instalación.** Solo hace falta, con los paquetes metafor y clubSandwich, para ejecutar el
  script que genera `nutria meta exportar-r` (modelo multinivel con varianza robusta). `Rscript` no está en la lista
  de permisos de `.claude/settings.json`: Claude Code pedirá permiso para usarlo.

## Uso con Claude Code

Abre Claude Code en la carpeta del proyecto, con el entorno activado. `CLAUDE.md` convierte la sesión principal en
el director científico y orquestador.

| Orden (skill) | Qué hace |
|---|---|
| `/mision-1 [foco]` | Primera misión paso a paso, con revisión humana entre fases |
| `/oportunidad O-0003` | Evaluación en profundidad de una oportunidad, paso a paso |
| `/revision-sistematica "<pregunta PECO>"` | Revisión sistemática según PRISMA 2020 |
| `/metaanalisis O-0003 "<variable>"` | Síntesis cuantitativa a partir de las extracciones |
| `/informe O-0003` | Informe de 20 secciones, verificado y sellado |
| `/red-team <fichero>` | Revisión adversarial |
| `/estado` | Estado del proyecto y siguientes pasos |

Los workflows (`.claude/workflows/`) **no son órdenes con `/`**: pídeselos a la sesión principal por su nombre y con
sus argumentos JSON, por ejemplo «ejecuta el workflow `mision-1-auto` con `{"fase": "mapa", "foco": "transición en
vacuno lechero"}`». La sesión los lanza con la herramienta Workflow; su avance se sigue con `/workflows`.

| Workflow | Argumentos |
|---|---|
| `mision-1-auto` | Una fase por ejecución, con revisión humana entre fases: `{"fase": "mapa", "foco": "..."}` (por defecto) → `{"fase": "oportunidades", "problemas": [problemas del mapa aprobados por una persona], "n_oportunidades": 12}` → `{"fase": "planes", "seleccion": ["O-0003", "..."]}`. Opcional: `"fecha": "AAAA-MM-DD"` |
| `evaluar-oportunidad-auto` | `{"id": "O-0003"}` u `"O-0003"`; opcional `"max_estudios": 10` (límite de la doble extracción) |
| `red-team-auto` | `{"archivo": "research/informes/O-0003.md", "sensibilidad": "confidencial"}` (`normal` solo con información no confidencial) |
| `comparar-sistemas` | `{"sistemas": [{"nombre": "nutria", "ruta": "nutria"}, {"nombre": "otro", "ruta": "otro"}], "salida": "COMPARACION.md", "prompt_original": "docs/PROMPT_ORIGINAL.md"}` |

### Workflows y número de agentes

Orden de magnitud de los subagentes que lanza cada ejecución, contados en el código de cada workflow. El coste real
en tokens y en tiempo no se ha medido: empieza por las versiones paso a paso.

| Workflow | Para qué | Subagentes por ejecución |
|---|---|---|
| `mision-1-auto` | Misión 1 por fases: mapa de problemas; oportunidades con novedad comprobada, seis compuertas propuestas, crítica, puntuación ciega y ranking; planes de las oportunidades elegidas | Del orden de **180-350** en la fase `oportunidades` (12 por oportunidad: registro, 6 propuestas de compuerta, crítica, lectura del registro y 3 evaluadores, 9 si queda bloqueada; hasta 7 por problema y ronda: generador y 2 comprobaciones de novedad por cada una de sus hasta 3 oportunidades, con 2 rondas extra como máximo; paso 0, ranking y verificación; el registro del workflow da el máximo exacto: 351 con 8 problemas y 12 oportunidades), **7** en la fase `mapa` y **1 + 6 por oportunidad elegida** en la fase `planes` |
| `evaluar-oportunidad-auto` | Evaluación de una oportunidad registrada | Del orden de **30-85**: 17 fijos si no se detiene antes (3 lecturas de compuertas del registro y 2 de la procedencia, 4 de descarte rápido, plan, revisión bibliográfica, 3 evaluadores, informe, su verificación y sello), 3 registros de puntuación solo con sello válido, 3 por estudio con doble extracción (máximo `max_estudios`, 10 por defecto), 1 de metaanálisis si hay al menos 3 estudios comparables y hasta 3 por pregunta de especialista (máximo `max_preguntas`, 10 por defecto): como máximo 84 por defecto; solo 1 si el registro ya tiene una compuerta bloqueada |
| `red-team-auto` | Revisión adversarial de un documento | Del orden de **20-50**: 1 de extracción, 3 refutadores por cada una de sus 5-15 afirmaciones clave (tope `max_afirmaciones`, hasta 30), segunda opinión opcional y consolidación: como máximo 48 por defecto y 93 con 30 afirmaciones |
| `comparar-sistemas` | Auditar o comparar sistemas construidos desde el mismo prompt (no investiga) | Del orden de **100**: inventario, 11 dimensiones y una verificación por cada hallazgo de gravedad media o alta |

Los modelos de cada agente (`model:` en `.claude/agents/*.md`) están elegidos así: `opus` para razonamiento crítico
(director, crítico, regulación, estadística, fisiología, compuestos, nutrición), `sonnet` para tareas de alto volumen
(búsqueda, extracción, verificación, bioinformática, industrial, economía, sostenibilidad) y `inherit` (el modelo de
la sesión) para `evaluador-independiente`. Cámbialos según tu presupuesto. La versión exacta del modelo de cada
subagente no se registra automáticamente: anótala en el Anexo C de cada informe.

## Herramientas `nutria` (resumen)

```bash
nutria buscar pubmed "dairy cows AND rumen-protected choline AND transition" --max 25 --resumenes
```

```bash
nutria fuente agregar --doi 10.1017/S1751731108002280 --tipo-estudio revision_narrativa
```

```bash
# Valores ILUSTRATIVOS: en un análisis real, a, b, kd y kp necesitan fuente registrada o la etiqueta [SUP]
nutria destino ruminal --a 0.1 --b 0.8 --kd 0.08 --kp 0.06 --dosis 50 --unidad-dosis g/animal/d --digestibilidad-intestinal 0.8
```

```bash
nutria extraccion comparar research/datos/colina/extraccion_R1_A.json research/datos/colina/extraccion_R1_B.json
```

```bash
nutria meta analizar ejemplos/validacion_bcg.csv --metodo REML --moderador ablat
```

```bash
nutria puntuar ranking --perfil recomendado --sim 5000
```

```bash
nutria econ ganadero ejemplos/econ_ganadero_ILUSTRATIVO.toml
```

```bash
nutria informe comprobar research/informes/O-0001.md --online --sellar
```

`nutria meta analizar` usa por defecto REML con el ajuste de Hartung-Knapp-Sidik-Jonkman (`--ajuste hksj`). Lista
completa: `nutria --help` y `nutria <orden> --help`. Todas las órdenes aceptan `--json` (salida en JSON; la única
excepción es `puntuar rubrica --markdown`, que imprime la tabla en Markdown), `--agente <nombre>` y `--offline` (solo
caché: la verificación de referencias queda incompleta y no hay sello válido).

Con `--agente evaluador-<n>`, la CLI no muestra puntuaciones (`oportunidad ver` las omite) y rechaza `puntuar
ranking` y `procedencia`; la procedencia no guarda los valores de las puntuaciones, solo quién puntuó (los valores
están en la base de datos). Es una barrera frente a descuidos, no un control de seguridad: un agente con Bash podría
leer la base de datos directamente. `extraccion exportar` (formato `analisis`) solo exporta extracciones conciliadas
(`doble_concordante` o `resuelta`); `--incluir-sin-conciliar` añade las demás para una exploración.

## Garantías y limitaciones

### Lo que garantiza el código (con pruebas automáticas: `python -m pytest -q`)

- **Fuentes**: `nutria fuente agregar` solo registra un DOI o PMID que existe (consulta sin caché). Una afirmación
  `[PUB]` del registro necesita una fuente verificada, no retractada y con la retractación comprobada, además de la
  cita textual y su ubicación.
- **Sello de un informe** (`nutria informe comprobar <informe> --online --sellar`): solo es válido con verificación en
  línea **completa** (todas las referencias existen y corresponden a la cita, su retractación está comprobada en
  Crossref, OpenAlex o PubMed y ninguna queda pendiente por falta de red), estructura completa y ningún error. Guarda
  la huella del texto comprobado (caduca si el informe cambia) y va firmado con una clave que vive fuera del
  repositorio. **Sin red no hay sello válido.**
- **Informe revisado o final**: exige además el sello válido, la tabla de respaldo del Anexo A
  (`| Ref | Afirmación | Respaldo | Motivo |`, con fila `respalda` o `parcial` para cada `[Rn]` de una línea `[PUB]`)
  y `revisado_por_humano`.
- **Referencias con solo URL** (normativa, literatura gris): cuentan como verificadas únicamente si una persona las
  registró con `nutria verificar manual <URL> --firma-humana NOMBRE`.
- **Compuertas**: los agentes solo las proponen. Una propuesta `bloqueada` actúa de inmediato; `superada` solo cuenta
  con firma humana, y salir de `bloqueada` también la exige; las rutas `fuera_de_alcance_*` bloquean automáticamente.
  En el ranking, una oportunidad con alguna compuerta sin resolver o sin firmar queda CONDICIONAL, siempre detrás de
  las APTA, y con menos de 3 evaluadores distintos por criterio queda INSUFICIENTE.
- **Metaanálisis**: reproduce los resultados publicados de metafor para el conjunto clásico BCG (efectos fijos,
  DerSimonian-Laird, REML, Hartung-Knapp, intervalo de confianza de tau², Egger y meta-regresión). El resto de
  métodos (Paule-Mandel, medidas binarias, agregación de efectos dependientes, St-Pierre, validación dejando un
  estudio fuera, potencia) tiene pruebas propias, sin validación frente a otro programa.
- **Secretos**: las claves de API no se escriben en la caché, la procedencia ni los mensajes de error.

### Lo que hacen los hooks de Claude Code

- **PreToolUse** (`scripts/hooks/guard_bash.sh` y `scripts/hooks/guard_write.sh`): bloquean cualquier orden de un
  agente con `--firma-humana` o que toque la clave de los sellos, y la escritura directa de sellos
  (`*.verificacion.json`) con las herramientas de edición. La persona firma desde su propia terminal.
- **PostToolUse** (`scripts/hooks/run_hook.sh` → `scripts/hooks/check_research_file.py`): valida lo que se escribe en
  `research/` con Write, Edit o MultiEdit (informes, planes, revisiones y JSON de oportunidades e hipótesis) y bloquea
  si hay errores. En un borrador solo comprueba la forma (cada `[PUB]` con su `[Rn]`, cada referencia con DOI, PMID
  o URL); que los identificadores existan y correspondan a la cita se comprueba en línea al sellar, y el sello válido
  es obligatorio para marcar un informe como revisado o final.
- Son barreras frente a errores y atajos de los agentes, no un control de seguridad: comparan texto, y el PostToolUse
  no ve lo que se escribe por otras vías, como Bash (compruébalo con `nutria informe sello <informe>`).

### Limitaciones

- **Agentes, skills y workflows nunca ejecutados de principio a fin.** Los subagentes, las skills y los workflows de
  investigación solo se han revisado leyendo su código y, en la auditoría externa, con llamadas a agentes simuladas.
  Su comportamiento real (si siguen las reglas, si los evaluadores se mantienen ciegos, cuánto cuestan) está sin
  comprobar.
- **"Determinista" y "reproducible" se refieren al control de flujo** de los workflows y a los cálculos de `nutria`
  con semilla fija: las respuestas de los agentes cambian entre ejecuciones.
- **Clientes de APIs sin probar contra los servicios reales**: se escribieron según la documentación de cada servicio
  y se prueban con respuestas simuladas, porque el entorno donde se construyeron bloqueaba esos dominios. Hay pruebas
  en vivo opcionales: `NUTRIA_PRUEBAS_EN_VIVO=1 python -m pytest -m vivo tests/test_vivo.py` (OpenAlex necesita además
  `OPENALEX_API_KEY`). Ejecútalas, con `nutria doctor`, antes de confiar en ellos.
- **Lo que el sistema no hace**: no tiene módulos de racionamiento (NASEM, INRAE, FEDNA), simulación metabólica,
  fermentación ruminal, formulación a mínimo coste, docking ni modelos PK/PD. La única herramienta de modelización es
  `nutria destino ruminal` (degradabilidad efectiva de Ørskov y McDonald, 1979); `nutria molecula`, `diana`,
  `proteina`, `red` y `rutas` solo consultan bases de datos. Tampoco ejecuta ni encarga experimentos.
- **Respaldo de las afirmaciones**: que una fuente diga lo que se le atribuye lo juzga un agente (`verificador-citas`,
  tabla del Anexo A) y lo revisa una persona; el código comprueba la tabla, no el contenido de la fuente. La cita
  textual de una afirmación `[PUB]` del registro tampoco se compara automáticamente con la fuente.
- **Regulación**: las conclusiones son preliminares y la mayoría de las normas de `docs/REGULACION_UE.md` está
  pendiente de verificar en su texto consolidado.
- Los ficheros de `ejemplos/` marcados `ILUSTRATIVO` muestran el formato; sus números no son datos.
- El sistema no sustituye a especialistas humanos: las compuertas superadas, la selección de candidatos y cualquier
  paso hacia experimentos requieren firma humana.

Estado detallado, hallazgos de la auditoría externa corregidos y pendientes:
[`docs/ESTADO_Y_LIMITACIONES.md`](docs/ESTADO_Y_LIMITACIONES.md).

## Estructura

```
CLAUDE.md                  constitución del orquestador
.claude/agents/            15 subagentes
.claude/skills/            13 skills (misiones y procedimientos)
.claude/workflows/         4 workflows (3 de investigación y comparar-sistemas)
.claude/settings.json      permisos y hooks
scripts/hooks/             hooks de Claude Code (PreToolUse y PostToolUse)
src/nutria/                herramientas (CLI nutria)
docs/                      método, auditoría, estado y limitaciones, rúbrica, regulación, fuentes, arquitectura
ejemplos/                  plantillas de entrada y conjunto de validación
evals/                     banco de preguntas para evaluar modelos
research/                  registro, procedencia y resultados de la investigación
tests/                     pruebas (las de tests/test_vivo.py, contra los servicios reales, son opcionales)
```

## Personalización

- Pesos y anclas de la rúbrica: copia `src/nutria/data/rubrica.toml` a `config/rubrica.toml`.
- Registro de modelos para segunda opinión: copia `src/nutria/data/modelos.toml` a `config/modelos.toml`, rellena los
  identificadores exactos y actívalos; elige modelo por rol con `nutria llm evaluar evals/dominio_semilla.jsonl`
  (amplía el banco con expertos). `nutria llm preguntar` usa por defecto la sensibilidad `confidencial`: solo modelos
  locales o con acuerdo de no retención.
