# NUTRIA: I+D computacional en nutrición y producción animal

Sistema multiagente para Claude Code que investiga, genera hipótesis y prioriza nuevos ingredientes, combinaciones y
formulaciones para animales de producción (prioridad: rumiantes y vacuno lechero), con evidencia verificable y
decisiones trazables.

> **Antes de usarlo**: con este sistema **todavía no se ha ejecutado ninguna misión ni investigación**: no hay mapa de
> problemas, oportunidades, hipótesis, extracciones, informes ni planes reales, y nada demuestra aún la calidad de sus
> resultados. La capa de agentes (subagentes, skills y workflows de investigación) **nunca se ha ejecutado de
> principio a fin**, solo con agentes simulados, y los clientes de las APIs no se han probado contra los servicios
> reales. Lee [Garantías y limitaciones](#garantías-y-limitaciones), la guía
> [Primera misión en tu ordenador](#primera-misión-en-tu-ordenador) y el estado detallado de cada pieza en
> [`docs/ESTADO_Y_LIMITACIONES.md`](docs/ESTADO_Y_LIMITACIONES.md).

Tiene dos partes:

1. **La organización** (`CLAUDE.md` y `.claude/`): 15 subagentes especializados con contexto propio, 13 skills
   (misiones y procedimientos), 4 workflows (3 de investigación y 1 de auditoría) y hooks de Claude Code. La barrera
   que impide a un agente firmar como una persona está en la CLI y en el registro: rechazan `--firma-humana` con el
   código 3 si la orden lleva `--agente` o `NUTRIA_AGENT`, si se ejecuta dentro de Claude Code (sus variables de
   entorno en el proceso o en el entorno inicial de un antecesor, o el propio Claude Code entre los antecesores) o si
   no hay terminal de control, y ningún analizador de órdenes admite abreviaturas. Los hooks PreToolUse añaden una
   barrera de texto (firma, entorno, registro, sellos e identidad de los subagentes) y el hook PostToolUse valida lo
   que se escribe en `research/`. Son barreras frente a errores y atajos, no un control de seguridad, y la firma es
   una atestación sin autenticar (ver [Limitaciones](#limitaciones)).
2. **Las herramientas** (`nutria`, paquete Python): búsqueda en PubMed, Europe PMC, OpenAlex y EFSA Journal;
   verificación de referencias (existencia en Crossref, OpenAlex, PubMed, doi.org y DataCite, correspondencia con la
   cita y retractación); metadatos de normativa en EUR-Lex; registro de evidencia con procedencia encadenada; doble
   extracción registrada; metaanálisis y meta-regresión validados frente a metafor; destino ruminal (Ørskov y
   McDonald) y conversión de dosis; potencia; priorización con compuertas firmadas por personas y Monte Carlo;
   economía del ganadero y de la empresa con comprobación de coherencia; informes con sello de verificación; segunda
   opinión multimodelo.

El método completo está en `docs/PROMPT_MAESTRO_v2.md`; por qué se cambió el prompt original, en
`docs/REVISION_PROMPT_ORIGINAL.md`; la arquitectura, en `docs/ARQUITECTURA.md`.

## Instalación

Requiere Python 3.11 o superior (la suite completa se ha probado en Linux con Python 3.11, 3.12 y 3.13). Los pasos
detallados para trabajar en tu ordenador están en
[Usar nutria en tu ordenador](#usar-nutria-en-tu-ordenador-claude-code-en-local). Con **Python 3.11**, cambia en la
orden `pip` el fichero de versiones por `requirements/constraints-py311.txt` (ver abajo).

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
pip install -e ".[dev,mixto]" -c requirements/constraints.txt
```

```bash
nutria init
```

```bash
nutria doctor
```

- **Crea el entorno exactamente en `.venv`, dentro del proyecto.** El hook PostToolUse se ejecuta con
  `scripts/hooks/run_hook.sh`, que busca primero el intérprete de `.venv` (`.venv/bin/python`; en Windows,
  `.venv\Scripts\python.exe`) y, si no existe o no tiene las dependencias, `python3` o `python` del PATH. Si ninguno
  tiene las dependencias de `nutria`, el hook devuelve a Claude Code un bloqueo («block») por cada escritura en
  `research/` que debía validar (falla cerrado, no se desactiva en silencio), aunque el fichero ya está escrito: un
  hook PostToolUse actúa después de escribir. Comprueba el del proyecto con
  `.venv/bin/python -c "import nutria, jsonschema"`.
- **Activa el entorno antes de abrir Claude Code** (`source .venv/bin/activate` y después `claude`): los agentes
  ejecutan `nutria` desde la terminal de la sesión y tienen que encontrarlo en el PATH.
- `requirements/constraints.txt` fija las versiones con las que pasa la suite en Python 3.12 y 3.13 (2026-11-05 y
  2026-11-06), sin ninguna preliminar (wrapt queda fijado en 2.5.2 porque formulaic admite versiones preliminares).
  **No sirve con Python 3.11**: numpy 2.5.3 y scipy 1.18.1 exigen Python 3.12 o superior, y pip termina con
  `ResolutionImpossible`. Con Python 3.11, instala con
  `pip install -e ".[dev,mixto]" -c requirements/constraints-py311.txt`, que fija las versiones con las que pasa la
  suite en Python 3.11 (numpy 2.4.6 y scipy 1.17.1, entre otras). Sin `-c` funcionan otras versiones compatibles, pero
  no se han probado. Node.js (22, para `tests/test_orquestacion.py`) y R (opcional) no se instalan con pip.
- `nutria init` crea `nutria.toml`, la base de datos `research/nutria.sqlite`, la procedencia
  `research/procedencia.jsonl` y las carpetas de `research/`. Si quieres, rellena `email` en `nutria.toml` (lo piden
  NCBI y Crossref para su acceso preferente).
- Las claves de API van en variables de entorno, nunca en ficheros: `OPENALEX_API_KEY` (**OpenAlex la exige en todas
  las peticiones desde el 13-02-2026**; es gratuita en https://openalex.org/settings/api), `NCBI_API_KEY`,
  `LENS_API_TOKEN` y, para la segunda opinión multimodelo, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
  `DEEPSEEK_API_KEY`.
- Dominios que la red debe permitir a `nutria`: `eutils.ncbi.nlm.nih.gov`, `pubchem.ncbi.nlm.nih.gov`,
  `www.ebi.ac.uk`, `api.openalex.org`, `api.crossref.org`, `rest.uniprot.org`, `version-12-0.string-db.org` (STRING
  en su dirección versionada, configurable en `nutria.toml`, sección `[fuentes]`, `string_url`), `reactome.org`,
  `doi.org` y `api.datacite.org` (verificación de DOI), `eur-lex.europa.eu` (`nutria normativa`) y, si se usa,
  `api.lens.org`. La segunda opinión y `nutria llm evaluar` necesitan además los dominios de los proveedores que
  actives en `config/modelos.toml`: `api.anthropic.com`, `api.openai.com`, `generativelanguage.googleapis.com` o
  `api.deepseek.com` (un modelo local, ninguno); `nutria doctor` no los comprueba. Detalle en `docs/FUENTES.md`.
- `nutria doctor` comprueba las fuentes esenciales (PubMed, Europe PMC y Crossref, sin las que no hay búsqueda ni
  verificación) y las demás, Lens si hay token, los dominios de WebFetch que declara `docs/FUENTES.md` y si están
  preautorizados en `.claude/settings.json`, el intérprete del hook, `Rscript` y la versión de cada base (una versión
  que no reconoce sale como AVISO). Sale con 1 si falla algo esencial; con `--offline` no hace peticiones y dice qué
  comprobaría; fuera de un proyecto no deja procedencia.
- **R no es un requisito de instalación.** Solo hace falta, con los paquetes metafor y clubSandwich, para ejecutar el
  script que genera `nutria meta exportar-r` (modelo multinivel con varianza robusta). `Rscript` no está en la lista
  de permisos de `.claude/settings.json`: Claude Code pedirá permiso para usarlo.

## Usar nutria en tu ordenador (Claude Code en local)

Los agentes de Claude Code siguen usando Claude con las condiciones de tu cuenta de Anthropic; `nutria` es la caja de
herramientas que ejecutan desde la terminal, y un modelo local solo puede dar la segunda opinión (abajo).

### Requisitos

- Python 3.11 o superior (probado con 3.11, 3.12 y 3.13) y git.
- Claude Code reciente con la herramienta Workflow, que está en la versión 2.1.318 (binario nativo; compruébalo con
  `claude --version`) y no en el paquete npm `@anthropic-ai/claude-code` 2.1.42. Los workflows de `.claude/workflows/`
  solo se han probado con agentes simulados, no con Claude Code (ver [Uso con Claude Code](#uso-con-claude-code)).
  Sin la herramienta siguen funcionando las skills paso a paso (`/mision-1`, `/oportunidad`...).
- Los hooks se ejecutan con `sh`: en Linux y macOS no hace falta nada más; en Windows, usa Claude Code desde Git Bash
  o desde WSL.
- Node.js 22 solo para ejecutar `tests/test_orquestacion.py`; R, opcional (ver [Instalación](#instalación)).

### Instalación paso a paso

1. Clona el repositorio y entra en él (`git clone <tu-repositorio> nutria` y `cd nutria`).
2. Crea y activa el entorno en `.venv`: `python -m venv .venv` y `source .venv/bin/activate` (en Windows con Git
   Bash, `source .venv/Scripts/activate`).
3. Instala con las versiones probadas: `pip install -e ".[dev,mixto]" -c requirements/constraints.txt` con Python 3.12
   o superior; con Python 3.11, `pip install -e ".[dev,mixto]" -c requirements/constraints-py311.txt` (comprueba la
   versión con `python --version`).
4. Inicializa el proyecto con `nutria init` y comprueba el entorno con `nutria doctor` (con red).
5. Comprueba que la suite pasa en tu máquina: `python -m pytest -q` (sin red; las pruebas en vivo se omiten).

### Claves y correo de contacto

Exporta las claves en el entorno de la terminal desde la que abres Claude Code (por ejemplo en tu `~/.bashrc` o en un
gestor de secretos), nunca en ficheros del proyecto: `OPENALEX_API_KEY` (obligatoria para OpenAlex), `NCBI_API_KEY`
(PubMed con más peticiones por segundo) y `LENS_API_TOKEN` (opcional, para `nutria patentes --lens`). El correo de
contacto va en `nutria.toml`, sección `[contacto]`, campo `email`: con él, Crossref atiende en su pool «polite».
`nutria doctor` dice qué claves ve.

### Primera comprobación real: doctor y pruebas en vivo

Los clientes de las APIs solo se han probado con respuestas simuladas. Con red, la primera comprobación real es:

```bash
nutria doctor
```

```bash
NUTRIA_PRUEBAS_EN_VIVO=1 python -m pytest -m vivo tests/test_vivo.py
```

Las pruebas en vivo consultan PubMed, Europe PMC, OpenAlex (necesita `OPENALEX_API_KEY`), Crossref, ChEMBL, PubChem,
UniProt, STRING, Reactome, Lens (necesita `LENS_API_TOKEN`) y EUR-Lex. Si alguna falla, no confíes en ese cliente
hasta entender por qué. **No cubren doi.org ni DataCite** (`src/nutria/sources/doiorg.py`), la vía por la que un DOI
que no está en Crossref, OpenAlex ni PubMed se da por existente o por inexistente: `nutria doctor` solo comprueba que
esos dominios responden. Hasta que tengan su prueba en vivo, comprueba a mano con
`nutria --json verificar --doi <DOI>` algún DOI que conozcas registrado en DataCite (p. ej. el de un conjunto de
datos) y uno inventado, y revisa que el primero quede verificado y el segundo, no.

### Firma humana: desde tu terminal, fuera de Claude Code

Las órdenes con `--firma-humana` (compuertas superadas, selección de candidatos, verificaciones manuales, revisión de
informes y autoría) se ejecutan en una terminal normal, con el entorno del proyecto activado y **fuera de Claude
Code**: la CLI las rechaza (código 3) si ve las variables de Claude Code, a Claude Code entre los procesos
antecesores, `--agente` o `NUTRIA_AGENT`, o si no hay terminal de control (por eso tampoco valen cron, CI ni `ssh`
sin `-t`). Por ejemplo, con tu nombre:

```bash
nutria puntuar compuerta O-0001 seguridad superada --motivo "Revisado el expediente de toxicología" --evidencia research/revisiones/O-0001_descarte_seguridad.md --firma-humana "Ana Pérez"
```

### Traer una sesión de la nube a tu ordenador

Si empezaste a trabajar en Claude Code en la nube (claude.ai/code), puedes seguir esa sesión en tu terminal con
`claude --teleport`, ejecutado desde tu copia local del repositorio y con la misma cuenta (las opciones exactas
dependen de tu versión: `claude --help`). Lo que la sesión dejó en `research/` (registro, procedencia, informes...)
solo llega si se confirmó con git en su rama; las claves, el entorno virtual y la clave de los sellos son de cada
máquina, así que en la tuya instala el entorno, exporta las claves y ejecuta `nutria doctor` antes de seguir. Los
sellos de los informes firmados en la nube no validan en tu ordenador: vuelve a sellarlos con red (ver «Qué se
versiona» en [Primera misión en tu ordenador](#primera-misión-en-tu-ordenador)).

### Modelos locales para la segunda opinión

`nutria llm preguntar` consulta, para la segunda opinión del crítico, los modelos de `config/modelos.toml` (copia
`src/nutria/data/modelos.toml`). Por defecto trata la información como confidencial y solo la envía a modelos con
`politica_datos = "local"` o `"zero_retention"`. Un modelo servido en tu máquina con una API compatible con OpenAI
(Ollama, LM Studio o vLLM) se declara así en `config/modelos.toml`:

```toml
[[modelo]]
id = "local"
proveedor = "openai_compatible"
base_url = "http://localhost:11434/v1"   # Ollama; LM Studio: http://localhost:1234/v1; vLLM: http://localhost:8000/v1
modelo = "NOMBRE_EXACTO_DEL_MODELO"      # el que muestra tu servidor (p. ej. `ollama list`)
api_key_env = ""
roles = ["critico", "confidencial"]
politica_datos = "local"
verificado = "AAAA-MM-DD"
activo = true
```

Después, `nutria llm modelos` debe mostrarlo como utilizable y `nutria llm preguntar --rol critico --archivo <f>` le
envía el documento (con `--offline` no se consulta ningún modelo, tampoco los locales). Los agentes de Claude Code
siguen usando Claude: el modelo local solo da esa segunda opinión, que es una opinión y no evidencia.

## Primera misión en tu ordenador

1. **Instala y comprueba**: los pasos de [Usar nutria en tu ordenador](#usar-nutria-en-tu-ordenador-claude-code-en-local):
   `nutria init` en la raíz del proyecto, `nutria doctor` con red y sin fallos esenciales, y las pruebas en vivo
   (`NUTRIA_PRUEBAS_EN_VIVO=1 python -m pytest -m vivo tests/test_vivo.py`). Si quieres ver antes el recorrido sin
   red, ejecuta la [demostración sin red](#demostración-sin-red).
2. **Abre Claude Code en el proyecto**, con el entorno activado: `source .venv/bin/activate` y `claude`.
3. **Fase mapa**: escribe `/mision-1 "<foco>"` (paso a paso) o pide a la sesión principal «ejecuta el workflow
   `mision-1-auto` con `{"fase": "mapa", "foco": "transición en vacuno lechero"}`». El resultado queda en
   `research/revisiones/M1a_mapa_problemas_<fecha>.md`.
4. **Revisión humana**: lee el mapa y decide qué problemas siguen (como máximo 8, o `"max_problemas"` hasta 12).
5. **Fase oportunidades**: `{"fase": "oportunidades", "problemas": [...], "n_oportunidades": 12}`. Registra las
   oportunidades, propone las seis compuertas sin firma, critica, puntúa a ciegas y deja el ranking en
   `research/revisiones/M1_ranking_<fecha>.md`.
6. **Decisiones humanas, en tu terminal**: firma las compuertas que des por superadas y la selección de cada
   oportunidad con la decisión «seleccionada M1»:

   ```bash
   nutria decision registrar --entidad O-0003 --decision "seleccionada M1" --motivo "Mejor balance entre evidencia y viabilidad" --firma-humana "Ana Pérez"
   ```

7. **Fase planes**: `{"fase": "planes", "seleccion": ["O-0003", "..."]}` escribe los planes en `research/planes/`.
8. **Qué se versiona**: confirma con git `research/` entera (el registro `nutria.sqlite`, `procedencia.jsonl`, los
   JSON de alta, las revisiones, los planes, los informes con sus sellos y los datos) cuando no haya ninguna orden
   `nutria` en marcha; los ficheros `-wal` y `-shm` del registro, `.cache/` y `.venv/` no se versionan (`.gitignore`).
   La clave de los sellos vive fuera del repositorio (`~/.config/nutria/`) y no se copia, así que **los sellos solo
   valen en la máquina que los firmó**: en otra máquina, o en el clon de otra persona, ningún sello existente valida
   (`nutria informe sello` dice que no es válido, y un informe en revisado o final no pasa esa comprobación ni el
   hook) hasta volver a sellarlo allí con `nutria informe comprobar <informe> --online --sellar`, que necesita red; la
   revisión humana registrada con `nutria informe revisar` sigue valiendo mientras el informe no cambie. Conviene
   añadir `research/procedencia.jsonl -text` a `.gitattributes` para que git no cambie sus finales de línea.

Cada fase consume muchos tokens (ver [Workflows y número de agentes](#workflows-y-número-de-agentes)): si dudas,
empieza por las skills paso a paso.

## Demostración sin red

```bash
python scripts/demo_sin_red.py --limpiar
```

Recorre las herramientas en un proyecto temporal, con los ficheros de `ejemplos/` y `--offline` en todas las órdenes:
`init` y `doctor`, alta de una oportunidad y una hipótesis, destino ruminal y conversión de dosis, doble extracción
(depositar, comparar con registro y conciliar), exportación para el análisis, metaanálisis del conjunto BCG,
economía y coherencia, tres puntuaciones y el ranking, esqueleto de informe y su comprobación, estado y verificación
de la procedencia. Comprueba el código de salida de cada paso y sale con 1 si alguno no es el esperado
(`tests/test_demo_sin_red.py` la ejecuta). Sin red no se puede verificar una fuente, así que la demostración registra,
solo en el proyecto temporal y declarándolo en la procedencia, una fuente de ejemplo dada por verificada: nada de lo
que registra es evidencia. Sin `--limpiar`, el proyecto queda para que lo inspecciones (su ruta sale al final).

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
vacuno lechero"}`». La sesión los lanza con la herramienta Workflow; su avance se sigue con `/workflows`. La
herramienta Workflow está en Claude Code 2.1.318 (binario nativo; compruébalo con `claude --version`) y no en el
paquete npm `@anthropic-ai/claude-code` 2.1.42. Los workflows se han probado con agentes simulados
(`tests/orquestacion_harness.cjs`), no en una ejecución real de principio a fin.

| Workflow | Argumentos |
|---|---|
| `mision-1-auto` | Una fase por ejecución, con revisión humana entre fases: `{"fase": "mapa", "foco": "..."}` (por defecto) → `{"fase": "oportunidades", "problemas": [problemas del mapa aprobados por una persona], "n_oportunidades": 12}` → `{"fase": "planes", "seleccion": ["O-0003", "..."]}`. `"n_oportunidades"` (12 por defecto, hasta 24) es objetivo y tope; `"max_problemas"` (8 por defecto, hasta 12) limita los problemas aprobados, y con más la fase se detiene con un error. Opcional `"fecha": "AAAA-MM-DD"` (debe ser una fecha válida). Cada fase empieza con el paso 0 (`nutria doctor`, `nutria estado` y los ficheros existentes) y nada se sobrescribe entre ejecuciones. La selección solo cuenta firmada por una persona con la decisión «seleccionada M1» |
| `evaluar-oportunidad-auto` | `{"id": "O-0003"}` u `"O-0003"`; opcionales `"max_estudios"` (1-30, 10 por defecto; límite de la doble extracción), `"max_preguntas"` (3-15, 10 por defecto) y `"ejecucion"` (identidades nuevas para los extractores tras una ejecución interrumpida). Con `{"id": "O-0003", "registrar_puntuaciones": [...]}` registra, sin repetir la evaluación, las puntuaciones que quedaron pendientes por falta de sello válido |
| `red-team-auto` | `{"archivo": "research/informes/O-0003.md", "sensibilidad": "confidencial", "autor": "<rol>"}` (`normal` solo con información no confidencial; `"autor"`, el rol que escribió el documento o `"persona"`, es obligatorio si no se deduce de la ruta; `"max_afirmaciones"` de 1 a 30, 15 por defecto). Escribe `research/revisiones/<directorio>_<nombre>_redteam.md` sin sobrescribir revisiones anteriores (-2, -3...) |
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

### Modelos de los agentes

Los modelos de cada agente (`model:` en `.claude/agents/*.md`) se asignaron por criterio y no por rendimiento medido:
`opus` para razonamiento crítico (director, crítico, regulación, estadística, fisiología, compuestos, nutrición),
`sonnet` para tareas de alto volumen (búsqueda, extracción, verificación, bioinformática, industrial, economía,
sostenibilidad) e `inherit` (el modelo de la sesión) para `evaluador-independiente`. **La competencia de dominio de
esos modelos no se ha medido.** Para medirla, activa en `config/modelos.toml` las entradas de los modelos que quieras
comparar (las de Anthropic necesitan `ANTHROPIC_API_KEY`) y ejecuta, una por modelo:

```bash
nutria --json llm evaluar evals/dominio_semilla.jsonl --modelo claude-opus
```

```bash
nutria --json llm evaluar evals/clasificacion_regulatoria.jsonl --modelo claude-opus
```

La salida da la exactitud por modelo en todas las preguntas (`resultados`), solo en las validadas (`solo_validadas`)
y por área (`por_area`: fisiología, nutrición, destino digestivo, regulación, estadística), y guarda cada respuesta en
`research/evals/` (un fichero nuevo por ejecución, sin sobrescribir los anteriores). Elige por `solo_validadas`: las
preguntas con estado `pendiente_validacion` se corrigen aparte hasta que una persona experta las valide; son las
respuestas de libro de texto sin fuente registrada y las que dependen de una norma, o de la parte de una norma, que
`docs/REGULACION_UE.md` marca pendiente de verificar. `evals/dominio_semilla.jsonl` tiene 37 preguntas (8 de
fisiología y 8 de nutrición; 13 pendientes) y `evals/clasificacion_regulatoria.jsonl`, 10 casos de clasificación con
la ruta o la compuerta esperada y las normas de `docs/REGULACION_UE.md` que la sustentan (4 pendientes). La
corrección es léxica: reconoce negaciones («no es X», «no X sino Y»), contradicciones y alternativas excluidas, pero
no entiende la frase. La versión exacta del modelo de cada subagente no se registra automáticamente: anótala en el
Anexo C de cada informe.

## Herramientas `nutria` (resumen)

Ejemplos que funcionan tal cual tras `nutria init`, sin red ni datos previos (los valores de `destino ruminal` y de
`dosis convertir` son ILUSTRATIVOS: en un análisis real cada entrada necesita fuente registrada o la etiqueta `[SUP]`):

```bash
nutria destino ruminal --a 0.1 --b 0.8 --kd 0.08 --kp 0.06 --dosis 50 --unidad-dosis g/animal/d --digestibilidad-intestinal 0.8
```

```bash
nutria dosis convertir 50 --desde g_animal_dia --ingestion-ms 20 --portador concentrado --base tal_cual --ingestion-pienso 8 --precio-eur-kg 5
```

```bash
nutria meta analizar ejemplos/validacion_bcg.csv --metodo REML --moderador ablat
```

```bash
nutria econ ganadero ejemplos/econ_ganadero_ILUSTRATIVO.toml
```

```bash
nutria econ coherencia ejemplos/econ_ganadero_ILUSTRATIVO.toml ejemplos/econ_desarrollador_ILUSTRATIVO.toml
```

```bash
nutria potencia medias --delta 1.5 --de 3 --m 10 --icc 0.1
```

Ejemplos que necesitan datos registrados antes: `oportunidad nueva` registra O-0001 a partir del ejemplo de formato,
`informe nuevo` necesita esa oportunidad, y `puntuar ranking` la muestra como INCOMPLETA (criterios sin puntuar) hasta
que tenga puntuaciones, e INSUFICIENTE mientras no haya tres evaluadores distintos por criterio (la
[demostración sin red](#demostración-sin-red) lo recorre entero):

```bash
nutria --agente director-cientifico oportunidad nueva --datos ejemplos/oportunidad_ejemplo.json
```

```bash
nutria informe nuevo O-0001
```

```bash
nutria puntuar ranking --perfil recomendado --sim 5000
```

Ejemplos que necesitan red (`buscar` con OpenAlex necesita además `OPENALEX_API_KEY`; sustituye el DOI por el del
trabajo que quieras registrar, y el informe tiene que estar completo para que su sello valga):

```bash
nutria buscar pubmed "dairy cows AND rumen-protected choline AND transition" --max 25 --resumenes
```

```bash
nutria fuente agregar --doi "10.NNNN/sustituir-por-el-doi" --tipo-estudio ensayo_controlado --especie "vacuno lechero"
```

```bash
nutria informe comprobar research/informes/O-0001.md --online --sellar
```

La doble extracción necesita fuentes verificadas (con red): cada extractor deposita su copia, un comparador
identificado registra la comparación (código 1 si hay discrepancias, con su identificador CE-xxxx) y, si las hay, un
conciliador distinto de los dos extractores registra la conciliación (plantilla en `ejemplos/conciliacion_ejemplo.json`):

```bash
nutria --agente extractor-datos-A extraccion depositar --datos research/datos/colina/extraccion_R1_A.json
```

```bash
nutria --agente extractor-datos-B extraccion depositar --datos research/datos/colina/extraccion_R1_B.json
```

```bash
nutria --agente extractor-datos-conciliador extraccion comparar research/datos/colina/extraccion_R1_A.json research/datos/colina/extraccion_R1_B.json --registrar
```

```bash
nutria --agente extractor-datos-conciliador extraccion conciliar CE-0001 --datos research/datos/colina/conciliacion_R1.json
```

```bash
nutria --agente estadistico extraccion exportar --variable "producción de leche" --especie "vacuno lechero" --csv research/datos/colina/leche.csv
```

Reglas de la doble extracción: las identidades son declaradas (un mismo agente puede usar varias; ver
[Limitaciones](#limitaciones)) y se comparan sin mayúsculas, tildes ni signos; una copia depositada es
definitiva; quien participó en una comparación con discrepancias (copias, comparación o conciliación) no vuelve a
extraer esa fuente; `comparar` sin `--registrar` se niega (nunca muestra discrepancias sin registrarlas, tampoco
fuera de un proyecto); las copias idénticas y las extracciones simples previas dejan un aviso registrado; la
conciliación puede llevar hacia un dato que la fuente no publica (origen `fuente` con valor nulo); y solo cuentan las
filas que salieron de su comparación o conciliación. `extraccion exportar` (formato `analisis`) solo exporta filas
con doble extracción verificada, de una especie salvo `--mezclar-especies`, con `--etapa` para filtrar el estado
fisiológico y `--casos-completos MOD` para una meta-regresión; excluye, con un AVISO que las lista, las filas sin los
datos del tamaño del efecto (medias y n, o eventos y n); `--incluir-sin-conciliar` añade las demás para una
exploración y `--formato registro` vuelca todo con las columnas internas. El CSV de análisis lleva, entre otras, la
PDR (`pdr_pct_pb`, `pdr_pct_ms`), el hito y las ventanas respecto al parto, `definicion_caso`, la suplementación,
`id_doble_extraccion`, `doble_extraccion_verificada`, `moderadores_no_informados`, el estado efectivo en
`estado_verificacion` y `rho_intrasujeto` (la correlación intrasujeto publicada de un diseño cruzado).

Lista completa: `nutria --help` y `nutria <orden> --help`. Otras órdenes que conviene conocer:

- `nutria normativa eurlex <CELEX> [--idioma]`: título, vigencia y versión consolidada de una norma (no sustituye la
  verificación manual de la persona).
- `nutria afirmacion comprobar [A-xxxx ...]`: compara la cita textual de las afirmaciones `[PUB]` del registro con el
  resumen de su fuente (estados `en_resumen`, `no_en_resumen`, `sin_resumen` y `sin_cita`; 1 si alguna no aparece,
  4 sin red).
- `nutria oportunidad clasificar <ID> --ruta R [--grupo-funcional G] [--situacion S] --motivo "..."`: registra la
  clasificación del triage regulatorio (la propone `regulacion-seguridad`; `--grupo-funcional`, obligatorio en las
  rutas `aditivo_*`).
- `nutria econ desarrollador proyecto.toml --ganadero ganadero.toml [--umbral-perdida EUR]`: rNPV de la empresa con
  la distribución del VPN realizado, P(VPN < 0) y P(pérdida > umbral), y comprobación de que precio, dosis y días
  coinciden con el modelo del ganadero (código 1 si no). `econ coherencia` hace solo esa comprobación: su
  `--tolerancia` admite como máximo 0,05 y la diferencia se expresa en % del valor del ganadero.
- `nutria cache comprobar|exportar|importar`: integridad de la caché de respuestas y su traslado a otra máquina
  (`cache importar <f> --sha256 H` con la huella que mostró `cache exportar`).
- `nutria procedencia verificar`, `nutria autoria registrar|listar` e `nutria informe revisar`: ver
  [Garantías](#lo-que-garantiza-el-código-con-pruebas-automáticas-python--m-pytest--q).
- `nutria llm evaluar <banco.jsonl> [--rol R | --modelo ID]`: ver [Modelos de los agentes](#modelos-de-los-agentes).

### Opciones globales, errores y códigos de salida

Todas las órdenes aceptan `--json` (salida en JSON; la única excepción es `puntuar rubrica --markdown`, que imprime la
tabla en Markdown), `--agente <nombre>` (quién ejecuta la orden, para la procedencia y las reglas de rol) y
`--offline` (solo caché: la verificación de referencias queda incompleta). Con `--offline`, cada copia servida
muestra su fecha y su antigüedad, con un máximo de 180 días (`--antiguedad-maxima DIAS` o, en `nutria.toml`, `[red]`
`antiguedad_maxima_offline_dias`).

Los errores salen siempre en texto por stderr («Prefijo: mensaje») y, con `--json`, además como un único objeto JSON
por stdout: `{"error", "codigo", "tipo"}` y, en los de red, `"motivo"`. `NUTRIA_DEPURAR=1` muestra la traza de un
error interno. Códigos de salida (los mismos que muestra `nutria --help`):

| Código | Significado |
|---|---|
| 0 | La orden terminó y el resultado es el esperado |
| 1 | Resultado negativo o incompleto: discrepancias en `extraccion comparar`, sello no válido, referencia o informe que no supera la verificación, búsqueda o consulta incompleta, caché con copias no válidas, comprobaciones esenciales de `doctor` fallidas, `llm preguntar` o `llm evaluar` sin respuesta o con algún modelo que falló |
| 2 | Entrada no válida o condición previa no cumplida (argumentos, fichero, JSON o CSV, configuración, proyecto sin inicializar, orden no permitida para el rol) |
| 3 | Regla del registro o error de la base de datos (identificador inexistente, transición o compuerta no permitida, fuente sin verificar, firma rechazada...) |
| 4 | Red o servicio. Motivos: `credenciales`, `peticion` (HTTP 4xx), `servicio` (5xx), `limite` (429 o sin turno libre), `respuesta` (no válida), `bloqueado`, `conexion`, `red` y `offline` (sin copia válida en la caché) |
| 5 | Falta una dependencia opcional o un cálculo no converge |
| 6 | El recurso pedido no existe en la fuente externa (HTTP 404) |
| 70 | Error interno inesperado |
| 130 | Orden interrumpida (Ctrl+C) |
| 141 | Quien leía la salida la cerró antes de que terminara (p. ej. `\| head`); si la orden ya había fallado, el código es el de su error |

Cambios de formato que afectan a quien procese la salida: con `--json`, `molecula`, `diana`, `proteina` y `rutas`
devuelven un objeto (`resultados`, `fuente` con su versión, `cache`, `avisos`, `notas`, `incompleto`...) en lugar de
una lista, y `red` añade claves. `buscar` pagina hasta `--max` (PubMed, Europe PMC, EFSA y OpenAlex), muestra los
avisos de la API y sale con 1 si queda incompleta. `diana` busca por nombre, por símbolo o sinónimo del gen y por
número de acceso de UniProt. `molecula pubchem --peligros` da el estado GHS: `con_frases_H` (solo frases con código H
o EUH), `clasificado_sin_frases_H` («Not Classified» o «no cumple los criterios»: no equivale a «sin peligro»),
`sin_clasificacion` (PubChem no tiene datos), `error` (código 1) y `no_consultado`.

### Evaluadores

Con `--agente evaluador-<n>` (o `NUTRIA_AGENT=evaluador-<n>`), la CLI no muestra puntuaciones, decisiones ni el motivo
del último cambio de estado (`oportunidad ver`, `oportunidad listar`, `hipotesis` y `estado --json` los omiten) y
rechaza `puntuar ranking`, `procedencia` y `decision listar`; la procedencia no guarda los valores de las
puntuaciones, solo quién puntuó (los valores están en la base de datos). `puntuar registrar` exige `--agente
evaluador-<n>` igual al campo `evaluador` del JSON, y nadie registra ni sustituye las puntuaciones de otro; el
identificador se normaliza (NFKC, guiones tipográficos, caracteres de formato invisibles, espacios y mayúsculas), así
que sus variantes no cuentan como evaluadores distintos. Un valor entre dos anclas de la rúbrica exige
`justificacion_entre_anclas`. Dentro de un subagente, el hook exige `--agente` en cada orden nutria (en
`evaluador-independiente`, un `--agente` de evaluador) y, a los evaluadores, les bloquea las órdenes que nombran
`nutria.sqlite` (también con comodines que la cubran) o importan nutria. Es una barrera frente a descuidos, no un
control de seguridad, y **la ceguera frente a los ficheros depende de la instrucción al agente**: el hook no ve un
`grep -ra` sobre `research/` (que lee el contenido de `nutria.sqlite`, justificaciones incluidas), `cat` o
`git log -p` de `research/procedencia.jsonl` (texto de las decisiones y motivos de los cambios de estado), los JSON de
puntuación de otros evaluadores (`research/oportunidades/*_puntuacion_*.json`) ni los informes y rankings, y las
herramientas Read, Grep y Glob de Claude Code no tienen hook.

## Garantías y limitaciones

### Lo que garantiza el código (con pruebas automáticas: `python -m pytest -q`)

- **Fuentes**: `nutria fuente agregar` solo registra un DOI o PMID que existe (consulta sin caché), con los
  metadatos de la propia verificación, licencia incluida. Una afirmación `[PUB]` del registro necesita una fuente
  verificada, no retractada y con la retractación comprobada, la cita textual, su ubicación y el contexto biológico.
  Una búsqueda sin verificar (`buscar --registrar`) nunca cambia la retractación, los identificadores ni los campos
  verificados: solo rellena campos vacíos no críticos (queda en la procedencia, con la huella de la consulta) y, si
  dice «retractado», avisa con la orden que vuelve a verificar esa fuente. Dos registros del mismo trabajo (DOI y
  PMID) solo se fusionan cuando una verificación los relaciona y los títulos coinciden; el PMID que da OpenAlex solo
  se asocia a un DOI si PubMed lo confirma; una fuente ya verificada como otro trabajo no se reescribe (código 3,
  para revisión humana).
- **Sello de un informe** (`nutria informe comprobar <informe> --online --sellar`): solo es válido con verificación en
  línea **completa** (todas las referencias existen y corresponden a la cita, su retractación está comprobada en
  Crossref, OpenAlex o PubMed y ninguna queda pendiente por falta de red), estructura completa, ningún
  `[POR COMPLETAR]` ni sección vacía (el esqueleto de `informe nuevo` nunca tiene sello válido) y ningún error. Guarda
  la huella del texto comprobado (caduca si el informe cambia) y va firmado con una clave que vive fuera del
  repositorio. **Sin red no hay sello válido** para un informe que cite algún DOI o PMID: sus referencias quedan
  pendientes. Las que solo tienen URL valen sin red si una persona las verificó manualmente, y con 0 referencias el
  sello dice «no había referencias que verificar». Un DOI, un PMID o un enlace de artículo en el cuerpo que no está
  en la bibliografía es error, y el sello dice cuántos enlaces del cuerpo no se verifican. Fuera de
  `research/informes` el sello puede no comprobar la estructura (`--sin-secciones`), y lo dice;
  `nutria verificar <informe.md> --sellar` aplica a los informes los mismos criterios que
  `informe comprobar --online --sellar`.
- **Correspondencia entre la cita y el identificador**: es una heurística que falla en cerrado, no una garantía. La
  entrada debe empezar por los autores, y su primer autor debe ser el primero del registro; el año, a ±1; si el
  registro tiene título, la cita debe traer el título COMPLETO como secuencia contigua de palabras (normalizado:
  minúsculas, sin acentos ni signos, «Part II» = «Part 2», «D(3)» = «D3»), con el subtítulo; una palabra de más, de
  menos, cambiada o reordenada, otra ortografía, números con letras o la falta del subtítulo son discrepancia. Delante
  del título solo caben los autores y las iniciales del registro, el año y una cabecera de sección; detrás, la
  revista (con abreviaturas ISO 4) y los datos de la cita. Un DOI se busca también en doi.org y DataCite; uno
  registrado sin metadatos comparables (p. ej. de mEDRA o JaLC) queda pendiente (`existe_sin_metadatos`). Una cita
  que reproduce el trabajo real pasa; que la fuente respalde la afirmación lo decide el Anexo A.
- **Informe revisado o final**: exige además el sello válido y:
  (1) la revisión humana registrada con `nutria informe revisar <informe> --firma-humana NOMBRE [--estado revisado|final]`,
  solo por personas y en su terminal, ligada a la huella del contenido (un nombre escrito a mano en
  `revisado_por_humano` o un nombre de relleno no valen);
  (2) la revisión del crítico enlazada en `revision_critica` (un documento de `research/revisiones` que mencione el
  informe);
  (3) la tabla de respaldo del Anexo A (`| Ref | Afirmación | Respaldo | Motivo |`), con una fila por cada afirmación
  con `[Rn]`, lleve la etiqueta que lleve, y por cada referencia, que reproduzca su texto, con `respalda` o
  `parcial`; una `no_respalda` nunca se compensa, y las filas de respaldo van solo en el Anexo A;
  (4) las fuentes de esas afirmaciones, registradas y verificadas;
  (5) las cifras con unidades y los `[Rn]` con su etiqueta, comprobados por frase y, en las tablas, por su estructura;
  (6) las citas solo como `[Rn]` en la misma frase: las autor-año (también las narrativas) y las numéricas («[1]»,
  «(R1)») son error, y la afirmación de una `[PUB]` va en su propia frase;
  (7) en la sección 14, la ruta del registro y el estado efectivo de cada compuerta, sin contar el texto tachado u
  oculto.
  La comprobación de cifras y citas detecta las formas habituales, no todas (ver [Limitaciones](#limitaciones)).
- **Referencias con solo URL** (normativa, literatura gris): cuentan como verificadas únicamente si una persona
  registró cada URL de la entrada con `nutria verificar manual <URL> --firma-humana NOMBRE`.
- **Firma humana**: la CLI y el registro la rechazan (código 3; con `--json`, también el objeto de error por stdout)
  con `--agente` o `NUTRIA_AGENT`, dentro de Claude Code (sus variables de entorno en el proceso o en el entorno
  inicial de un antecesor, o Claude Code entre los antecesores; donde no hay `/proc`, con `ps`) y sin terminal de
  control. Ningún analizador admite abreviaturas de las opciones.
- **Compuertas**: sin firma, cada compuerta la propone solo su rol propietario (`seguridad`, compuestos-toxicologia;
  `regulatoria`, regulacion-seguridad; `analitica`, desarrollo-industrial; `propiedad_intelectual`,
  economia-mercado-pi; `etica`, sostenibilidad-bienestar; `destino_digestivo`, fisiologia-bioquimica), y `bloqueada`
  la propone cualquiera. Una propuesta `bloqueada` actúa de inmediato; `superada` solo cuenta con firma humana; una
  propuesta sin firma no sustituye una decisión firmada, salvo para bloquear. Salir de `bloqueada` exige firma y
  `--evidencia`: IDs del registro que existan, URL o ficheros regulares del proyecto (no valen la propia
  oportunidad, el registro ni los JSON de la oportunidad), y la evidencia se valida siempre salvo al bloquear. La
  compuerta `regulatoria` no se propone ni se firma `superada` sin la clasificación del triage registrada con
  `oportunidad clasificar`, y las rutas `fuera_de_alcance_*` la bloquean automáticamente (salir de ellas exige firma
  humana). En el ranking, una oportunidad con alguna compuerta sin resolver o sin firmar queda CONDICIONAL, siempre
  detrás de las APTA y en tres subgrupos (solo pendientes de firma, con compuertas sin resolver y con compuertas
  críticas —`seguridad`, `regulatoria`, `destino_digestivo`— sin resolver) sea cual sea su puntuación; con menos de 3
  evaluadores distintos por criterio queda INSUFICIENTE, y el ranking no admite `--min-evaluadores` menor que 3.
  P(top-k) se calcula sobre el ranking completo y el ranking avisa cuando el orden o el top-k cambian entre perfiles
  de pesos. Una rúbrica propia en `config/` no puede quitar las seis compuertas.
- **Puntuaciones, decisiones y autoría**: las puntuaciones de un evaluador solo las registra o sustituye él mismo, y
  el historial guarda el actor real. Las decisiones, el historial de puntuaciones y la autoría son solo de inserción
  (disparadores contra UPDATE, DELETE e INSERT OR REPLACE, también en registros existentes); una decisión exige que
  la entidad exista y deja evento, y `decision listar` muestra si se registró desde Claude Code o desde una terminal.
  La autoría inventiva la registra una persona: `nutria autoria registrar --entidad O-xxxx --persona NOMBRE
  --aportacion "..." --firma-humana NOMBRE` (en la procedencia queda solo la huella de la aportación) y se consulta
  con `nutria autoria listar`.
- **Procedencia**: cada evento lleva la orden, el actor, el contexto (Claude Code o terminal), la sesión de Claude
  Code, el commit y los cambios sin confirmar, las versiones de las bibliotecas de cálculo y, cuando aplica, los
  parámetros y las huellas de entrada y salida, encadenado con el anterior por su hash. `nutria procedencia
  verificar` sale con 1 si no es íntegra: líneas corruptas, eventos alterados, cadena rota o eventos sin cadena
  añadidos después, y, si el fichero está en git, si no amplía alguna versión confirmada (HEAD, su historial y las
  puntas anteriores de la rama en su reflog) o si ha desaparecido teniendo eventos confirmados. `nutria procedencia`
  avisa si no es íntegra.
- **Metaanálisis**: reproduce los resultados publicados de metafor para el conjunto clásico BCG (efectos fijos,
  DerSimonian-Laird, REML, Hartung-Knapp, intervalo de confianza de tau², Egger y meta-regresión). Usa por defecto
  REML con el HKSJ modificado max(1, q) (`--ajuste hksj-mod`), que coincide con el estándar si q ≥ 1 y nunca da un
  IC más estrecho que Wald (con `--ajuste hksj` y q < 1 la salida avisa e imprime también la p y el IC modificados).
  Tienen pruebas propias, sin validación frente a otro programa: el intervalo de predicción (t con k − 2 gl y EE de
  Wald; con z, las mismas piezas reproducen `predict()` de metafor en BCG), Paule-Mandel, las medidas binarias, la
  agregación de efectos dependientes, los diseños cruzados con fórmulas de grupos emparejados (la SMD exige la
  correlación intrasujeto), los moderadores categóricos, St-Pierre con elección de la pendiente aleatoria por razón
  de verosimilitudes y ponderación opcional por 1/SEM², la validación dejando un estudio fuera, la potencia por
  corrales y en diseños cruzados y la plantilla de `exportar-r`. La meta-regresión es de casos completos (avisa de
  las filas que excluye). `meta` se niega, salvo con `--exploratorio`, con las filas que el CSV de
  `extraccion exportar` marca sin doble extracción verificada (columnas `doble_extraccion_verificada` y
  `estado_verificacion`, que añade `--incluir-sin-conciliar`); **un CSV preparado a mano o sin esas columnas se
  analiza sin esa comprobación y sin aviso**, así que para un análisis que cuente usa siempre el CSV que exporta
  `extraccion exportar`.
- **Economía**: el modelo de la empresa declara su propio precio, dosis y días por ciclo; `econ coherencia` y
  `econ desarrollador --ganadero` comprueban que coinciden con los del modelo del ganadero (código 1 si no), y
  `econ desarrollador` sin `--ganadero` termina con 0 y solo avisa: la coherencia no se impone al puntuar. Con
  `--exigir-fuentes`, cada entrada lleva su
  fuente etiquetada: una `[PUB]` cita una fuente o afirmación registrada y verificada, y una `[DER]` nombra una orden
  `nutria` que existe o un script que existe en el proyecto o junto al modelo. El Monte Carlo de la empresa declara
  `version_muestreo` (2) en la salida y en la procedencia: con la misma semilla, otra versión del muestreo da otras
  cifras.
- **Caché y versiones de las bases**: cada copia de la caché tiene huella SHA-256, clave y cabeceras confirmadas por
  la procedencia, y las alteradas o plantadas se descartan con aviso, salvo que también se falsifique su evento en
  `research/procedencia.jsonl` (eso queda en el diff de git). Las copias de versiones anteriores de nutria no se
  sirven sin red; con red, la consulta siguiente las renueva. Para otra máquina se exporta y se importa con
  `--sha256`; las fechas las declara el fichero de exportación y nutria comprueba que cuadran con la fecha de
  exportación, no cuándo se obtuvo cada respuesta. Las versiones de las bases se comprueban contra su forma (las no
  reconocibles no se registran) y se avisa si la versión y los resultados son de fechas distintas.
- **Confidencialidad**: con `--sensibilidad confidencial` (`buscar`, `molecula`, `diana`, `proteina`, `red`, `rutas`)
  la procedencia guarda solo huellas, pero la consulta viaja igualmente al servicio externo; `buscar --registrar`
  deja las fuentes en claro en `nutria.sqlite`; `cache exportar` omite las consultas confidenciales salvo
  `--incluir-confidenciales`; `patentes` guarda solo la huella de la consulta salvo `--registrar-consulta`. Una huella
  sin sal permite confirmar una consulta adivinada.
- **Límites de uso de Crossref**: 5 peticiones/s y concurrencia 1 en el pool público, o 10/s y 3 con correo de
  contacto; se comparten solo entre procesos que usan la misma caché y no se han comprobado en vivo.
- **Secretos**: las claves de API no se escriben en la caché, la procedencia ni los mensajes de error.

### Lo que hacen los hooks de Claude Code

- **PreToolUse de Bash** (`scripts/hooks/guard_bash.sh`): una barrera de texto con listas no exhaustivas que bloquea
  (1) la firma: `--firma-humana` y sus prefijos desde `--fir`, también con comillas, escapes, concatenaciones o
  variables, y el texto «firma-humana» o «firma_humana»; (2) tocar `CLAUDECODE`, `CLAUDE_CODE_*`, `AI_AGENT` o
  `NUTRIA_AGENT`, vaciar el entorno (`env -i`, `exec -c`...), cambiar de usuario (`sudo`, `su`, `doas`...),
  desvincular procesos (`setsid`, `nohup`, `disown`, `crontab` salvo `-l`) o darles una terminal propia (`tmux`,
  `screen -dm`, `script`, `expect`, `unbuffer`, `socat`, `ssh -t`, `pty`...); (3) escribir el registro por fuera de
  la CLI: `sqlite3`, `.connect(`, copias, borrados (también `unlink`, `find -delete`, `ex`, `vim -es`...) y
  redirecciones sobre `nutria.sqlite` o `procedencia.jsonl`, también con comodines que puedan cubrirlos; borrar
  `research/` entera; importar `nutria.db` o `nutria.provenance`; las órdenes de git que descartan cambios sin
  confirmar (`stash`, `clean`, `reset --hard`, `checkout`/`restore` de «.» o con `-f`, también abreviadas) o que
  restauran, borran o mueven el registro; y algunas formas de borrar el reflog con el que se comprueba la
  procedencia: `git reflog expire|delete`, `git update-ref`, `gc.reflogExpire` y borrar, vaciar o reescribir `.git`,
  `.git/logs` o `.git/refs` con esa ruta escrita en la orden (no si se entra antes en `.git` con `cd` o `pushd`, ni
  con `find .git... -delete`, ni con `git branch -D` en la sesión principal: ver [Limitaciones](#limitaciones)); (4)
  las órdenes que nombran
  la clave de los sellos o su directorio (`clave_sello`, su variable de entorno, `~/.config/nutria`, también con
  comodines, entrando con `cd` en `.config` o recorriendo `.config` con grep, find o tar) y el módulo de sellos, pero
  no las que recorren todo el directorio personal (`grep -r` o `tar` sobre `~`); (5) dentro de un subagente: cada
  orden nutria lleva `--agente` con un nombre literal y se ejecuta directamente (no desde Python, `xargs` ni `eval`);
  no se admite el nombre exacto de otro rol del proyecto ni `evaluador-<n>`, salvo en `evaluador-independiente`, que
  se identifica siempre como evaluador, no importa nutria y no nombra `nutria.sqlite` en la orden (cualquier otro
  nombre, como `persona` o `regulacion_seguridad`, pasa); no se menciona `NUTRIA_AGENT` ni se usa `set -a`; git solo
  para leer, añadir y confirmar (sin `--amend`) y `git grep` sin `-O`; un comentario no aporta el `--agente`, y
  nutria cuenta también por su ruta (`.venv/bin/nutria`). Las órdenes de solo lectura (`grep`, `cat`, `head`,
  `tail`, `wc`, `ls`...) solo se comprueban frente a la clave y, en un evaluador, frente a `nutria.sqlite`. Falsos
  positivos conocidos, con su alternativa: «--firma» dentro de un texto (escribe «la firma de la persona»); `pytest -k
  firma_humana` (usa `-k "firma and humana"`); un intérprete con un comodín que cubre el registro
  (`python3 x.py research/*`: nombra los ficheros); `sudo`, `nohup`, `git stash` y similares (que los ejecute la
  persona); un mensaje de commit en un heredoc que cita órdenes bloqueadas (`git commit -F` con un fichero).
- **PreToolUse de escritura** (`scripts/hooks/guard_write.sh`): bloquea con Write, Edit o MultiEdit los sellos
  (`*.verificacion.json`), la clave y `~/.config/nutria`, `procedencia.jsonl`, los ficheros `*.sqlite` del registro y
  `.git/`, sin distinguir mayúsculas.
- **PostToolUse** (`scripts/hooks/run_hook.sh` → `scripts/hooks/check_research_file.py`): después de cada escritura
  en `research/` con Write, Edit o MultiEdit, valida el fichero (informes, también en subcarpetas y con la extensión
  en mayúsculas; planes; revisiones; JSON de oportunidades e hipótesis con las reglas del registro) y, si hay
  errores, devuelve a Claude Code la decisión «block» con el motivo, que el agente ve y debe corregir. **No deshace la
  escritura**: un hook PostToolUse actúa con el fichero ya escrito, así que un fichero con errores queda en disco hasta
  que se corrija; lo que garantiza un informe es su sello (`nutria informe sello`). En un borrador solo comprueba la
  forma: etiquetas, `[Rn]` por `[PUB]`, cifras, citas y referencias por frase y en las tablas, la bibliografía sin
  duplicados ni identificadores de artículo fuera de ella y un Anexo A coherente (en
  `research/planes` y `research/revisiones`, las tablas de veredictos no cuentan como tablas de respaldo, y los
  identificadores y «según R1» de un borrador solo avisan). Que los identificadores existan y correspondan a la cita
  se comprueba en línea al sellar; en revisado o final se exigen la revisión humana registrada y el sello válido. El
  estado se lee del front matter (si es ilegible, devuelve «block»). Si un intérprete falla, `run_hook.sh` prueba el
  siguiente, y si ninguno funciona, devuelve también «block».
- **PostToolUse de WebFetch y WebSearch** (`scripts/hooks/registrar_web.sh`): registra cada consulta web en la
  procedencia (fecha, sesión, id de llamada, modo y SHA-256 de la respuesta, nunca su contenido). Por defecto guarda
  solo el dominio y la huella de la URL o de la consulta; con `NUTRIA_PROCEDENCIA_WEB=claro`, la URL y la consulta en
  claro, siempre redactadas (credenciales, parámetros sensibles, fragmento y correos). Nunca bloquea.
- Son barreras frente a errores y atajos de los agentes, no un control de seguridad: comparan texto, el PostToolUse
  no deshace lo escrito ni ve lo que se escribe por otras vías, como Bash (compruébalo con
  `nutria informe sello <informe>`), y los hooks no se protegen a sí mismos (ver [Limitaciones](#limitaciones)).

### Limitaciones

- **Ninguna misión ejecutada.** No se ha ejecutado la misión 1 ni ninguna otra investigación: el registro de un
  proyecto nuevo está vacío, y nada demuestra todavía que el sistema produzca investigación de calidad, original y
  verificable. Hacerlo exige red, claves y las decisiones de una persona (ver
  [Primera misión en tu ordenador](#primera-misión-en-tu-ordenador)).
- **Agentes, skills y workflows nunca ejecutados de principio a fin.** Los subagentes, las skills y los workflows de
  investigación solo se han revisado leyendo su código y con llamadas a agentes simuladas. Su comportamiento real (si
  siguen las reglas, si los evaluadores se mantienen ciegos, cuánto cuestan) está sin comprobar.
- **Competencia de dominio sin medir.** El razonamiento fisiológico, nutricional y regulatorio lo hacen los modelos de
  los agentes, cuya competencia no se ha medido; los bancos de `evals/` permiten medirla
  ([Modelos de los agentes](#modelos-de-los-agentes)), y la mayoría de sus preguntas de fisiología y nutrición están
  pendientes de validación por una persona experta. La clasificación regulatoria real la hacen los agentes con el
  árbol de `docs/REGULACION_UE.md`: los casos de `evals/clasificacion_regulatoria.jsonl` solo fijan la respuesta
  esperada, y la clasificación no se ha probado en uso.
- **"Determinista" y "reproducible" se refieren al control de flujo** de los workflows y a los cálculos de `nutria`
  con semilla fija: las respuestas de los agentes cambian entre ejecuciones.
- **Clientes de APIs sin probar contra los servicios reales**: se escribieron según la documentación de cada servicio
  y todos tienen pruebas con respuestas simuladas, porque el entorno donde se construyeron bloqueaba esos dominios.
  Las pruebas en vivo (`NUTRIA_PRUEBAS_EN_VIVO=1 python -m pytest -m vivo tests/test_vivo.py`, que incluyen Lens y
  EUR-Lex) son la primera comprobación real: ejecútalas, con `nutria doctor`, antes de confiar en ellos. No cubren el
  cliente de doi.org y DataCite (`src/nutria/sources/doiorg.py`), que decide si un DOI ausente de Crossref, OpenAlex y
  PubMed existe o se declara inexistente: `nutria doctor` solo comprueba que esos dominios responden. No hay cliente
  para el Registro de aditivos de la UE, el Catálogo y el Registro de materias primas, OpenFoodTox ni EURL-FA.
- **Lo que el sistema no hace**: no tiene módulos de racionamiento (NASEM, INRAE, FEDNA): no predice la ingestión ni
  evalúa raciones, y las ingestiones son entradas declaradas con fuente o `[SUP]`. Tampoco tiene simulación
  metabólica, fermentación ruminal, formulación a mínimo coste, docking ni modelos PK/PD. Las únicas herramientas de
  modelización son `nutria destino ruminal` (degradabilidad efectiva de Ørskov y McDonald, 1979) y la conversión de
  dosis entre g/animal/día, mg/kg de MS y g/t (`nutria dosis convertir`); `nutria molecula`, `diana`, `proteina`,
  `red` y `rutas` solo consultan bases de datos. Tampoco ejecuta ni encarga experimentos.
- **Respaldo de las afirmaciones**: el código comprueba que cada afirmación con `[Rn]` tenga en el Anexo A su fila,
  que reproduzca su texto, y que una `[PUB]` no se limite a remitir a una frase anterior sin etiqueta; con
  verificación en línea busca las citas literales de la columna Motivo en el resumen de PubMed u OpenAlex y, si no
  están, solo avisa. `nutria afirmacion comprobar` compara la cita textual de las afirmaciones `[PUB]` del registro con
  el resumen. Que una fuente diga lo que se le atribuye lo juzga un agente (`verificador-citas`) y lo revisa una
  persona: el código no comprueba el significado ni el texto completo.
- **Detección de cifras y citas sin etiqueta**: es heurística. No detecta cifras escritas con letras, unidades fuera
  de su lista (las de tiempo se excluyen a propósito), la unidad que solo está en el título o en una nota de la tabla,
  «Autor Año» sin paréntesis en prosa sin «según», «véase» ni verbo de cita, una cita tras una entradilla sin
  palabra de cita, ni afirmaciones sin etiqueta que no llevan cifras ni citas.
- **Firma humana**: es una atestación sin autenticar. En Linux la falsifica una sola orden de Bash lanzada desde
  Claude Code que deje el proceso huérfano, con el entorno vacío y con una terminal propia, si el hook no la reconoce
  (sus listas no son exhaustivas y no ve nombres construidos ni la opción leída de un fichero); en Windows basta
  vaciar el entorno, y en macOS `ps` no da el entorno inicial de los antecesores. También pueden falsificarla una
  copia modificada de nutria, quien escriba en la base de datos o una persona malintencionada. Una persona sin
  terminal de control (ssh sin `-t`, cron, CI, un servicio) no puede firmar.
- **Identidades declaradas**: `--agente` y `NUTRIA_AGENT` son declaraciones. En la sesión principal el hook no
  comprueba el rol; en un subagente solo rechaza el nombre exacto de otro rol y `evaluador-<n>`, y cualquier otro
  nombre (`persona`, `director`, `regulacion_seguridad`) queda tal cual en la procedencia (la CLI solo acepta la
  propuesta de una compuerta, salvo `bloqueada`, del nombre exacto de su rol propietario). En la doble extracción, un
  mismo agente puede depositar las dos copias y comparar con nombres distintos (`extractor-datos-A`,
  `extractor-datos-B`, `extractor-datos-conciliador`), y nada le impide leer la otra copia antes de depositar la suya:
  la independencia de las copias depende de que las hagan agentes distintos que no vean la otra.
- **Ceguera de los evaluadores**: la CLI oculta a un `--agente evaluador-<n>` (o `NUTRIA_AGENT=evaluador-<n>`) las
  puntuaciones ajenas, las decisiones y el motivo de los cambios de estado, y el hook le bloquea las órdenes que
  nombran `nutria.sqlite`. Pero un evaluador puede leer los ficheros de `research/`: un `grep -ra` sobre `research/`
  lee el contenido de `nutria.sqlite` (justificaciones incluidas), `research/procedencia.jsonl` guarda en claro el
  texto de las decisiones y los motivos de los cambios de estado (también en `git log -p`), y están los JSON de
  puntuación de otros evaluadores (`research/oportunidades/*_puntuacion_*.json`), los informes y los rankings; las
  herramientas Read, Grep y Glob de Claude Code no tienen hook. Que no los lea depende de que siga su instrucción.
- **Sello**: cualquier proceso del usuario puede leer la clave HMAC. El hook bloquea las órdenes de Bash que nombran
  la clave o su directorio, pero no las que recorren todo el directorio personal (`grep -r`, `tar`, `cp -r` o
  `rsync` sobre `~`), ni un programa escrito en un fichero, ni una ruta construida, y con Bash se puede escribir un
  fichero de sello (con Write o Edit, no). Un agente que lea la clave puede fabricar un sello válido:
  `nutria informe sello` detecta cambios accidentales, no una falsificación deliberada del mismo usuario. La clave es
  de cada máquina: en otra, ningún sello existente valida hasta volver a sellar (ver
  [Primera misión en tu ordenador](#primera-misión-en-tu-ordenador), «Qué se versiona»).
- **Procedencia**: la cadena detecta corrupción, alteraciones y borrados intermedios, pero no eventos añadidos al
  final con huellas recalculadas ni el truncado de lo no confirmado. La comparación con git usa el repositorio local
  (la rama remota no se compara) y la eluden reescribir la historia y borrar después el reflog de la rama, o seguir en
  otra rama. **El hook no lo impide**: en la sesión principal deja pasar `git commit --amend`, `reset --soft`,
  `rebase` y `git branch -D`, y en cualquier contexto, también en un subagente, borrar el reflog entrando antes en
  `.git` (`cd .git && rm -rf logs`) o con `find .git/logs -delete`, y mover la referencia de la rama sin git; solo
  reconoce `git reflog expire|delete`, `update-ref`, `gc.reflogExpire` y las rutas `.git`, `.git/logs` o `.git/refs`
  escritas en la orden. Un
  agente que quite un evento ya confirmado (p. ej. con un programa escrito en un fichero), reescriba el commit y
  borre el reflog deja la procedencia «íntegra» para `procedencia verificar`. Dos ramas que añadan eventos la rompen
  al fusionarse.
- **Los hooks comparan texto**: los eluden un programa escrito antes en un fichero o un nombre construido de forma
  indirecta (variables, concatenación, base64). Tampoco se protegen a sí mismos: `guard_write.sh` deja editar con
  Write o Edit `scripts/hooks/`, `.claude/settings.json` y `src/nutria/` (la CLI, instalada en modo editable), y con
  Bash también se editan, así que un agente al que se le permitan ediciones sin preguntar puede desactivar un hook o
  cambiar nutria; revisa con `git diff` los cambios en esos ficheros antes de confirmarlos. WebSearch y WebFetch
  envían la consulta al servicio externo: el hook solo decide qué queda registrado, y la huella de una consulta corta
  se puede confirmar adivinándola.
- **Evidencia de compuertas**: vale cualquier fichero regular del proyecto (salvo el registro y los JSON de la
  propia oportunidad), y una URL solo se comprueba por su forma. Las puntuaciones registradas antes de esta versión
  no se renormalizan en el registro, aunque el ranking cuenta sus evaluadores normalizados.
- **Regulación**: las conclusiones son preliminares y la mayoría de las normas de `docs/REGULACION_UE.md` está
  pendiente de verificar en su texto consolidado.
- Los ficheros de `ejemplos/` marcados `ILUSTRATIVO` o «EJEMPLO DE FORMATO» muestran el formato; sus números no son
  datos.
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
scripts/demo_sin_red.py    demostración de extremo a extremo sin red
scripts/auditoria/         generación del estado de la auditoría
src/nutria/                herramientas (CLI nutria)
docs/                      método, auditoría, estado y limitaciones, rúbrica, regulación, fuentes, arquitectura
ejemplos/                  plantillas de entrada y conjunto de validación
evals/                     bancos de preguntas para evaluar modelos (dominio y clasificación regulatoria)
requirements/              versiones probadas (constraints.txt, Python 3.12 o superior; constraints-py311.txt)
research/                  registro, procedencia y resultados de la investigación (vacío hasta la primera misión)
tests/                     pruebas (las de tests/test_vivo.py, contra los servicios reales, son opcionales)
```

## Personalización

- Pesos y anclas de la rúbrica: copia `src/nutria/data/rubrica.toml` a `config/rubrica.toml` (no puede quitar las
  seis compuertas).
- Registro de modelos para la segunda opinión: copia `src/nutria/data/modelos.toml` a `config/modelos.toml`, rellena
  los identificadores exactos y actívalos (un modelo local, como en
  [Modelos locales para la segunda opinión](#modelos-locales-para-la-segunda-opinión)). `nutria llm preguntar` usa por
  defecto la sensibilidad `confidencial`: solo modelos locales o con acuerdo de no retención. Elige modelo con
  `nutria llm evaluar` y amplía los bancos de `evals/` con expertos.
