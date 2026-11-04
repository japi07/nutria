# NUTRIA: I+D computacional en nutrición y producción animal

Sistema multiagente para Claude Code que investiga, genera hipótesis y prioriza nuevos ingredientes, combinaciones y
formulaciones para animales de producción (prioridad: rumiantes y vacuno lechero), con evidencia verificable y
decisiones trazables.

Tiene dos partes:

1. **La organización** (`CLAUDE.md` y `.claude/`): 14 subagentes especializados con contexto propio, 13 skills
   (misiones y procedimientos), 3 workflows deterministas y un hook que bloquea informes con referencias no verificables.
2. **Las herramientas** (`nutria`, paquete Python): búsqueda en PubMed, Europe PMC, OpenAlex y EFSA Journal;
   verificación de referencias (existencia, correspondencia con la cita, retractación); registro de evidencia con
   procedencia; metaanálisis y meta-regresión validados frente a metafor; potencia; priorización con compuertas y
   Monte Carlo; economía del ganadero y de la empresa; informes con sello de verificación; segunda opinión multimodelo.

El método completo está en `docs/PROMPT_MAESTRO_v2.md`; por qué se cambió el prompt original, en
`docs/REVISION_PROMPT_ORIGINAL.md`.

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

`nutria init` crea `nutria.toml` y la base de datos `research/nutria.sqlite`. Si quieres, rellena `email` en
`nutria.toml` (lo piden NCBI, Crossref y OpenAlex para su acceso preferente). Las claves de API van en variables de
entorno, nunca en ficheros: `NCBI_API_KEY`, `OPENALEX_API_KEY`, `LENS_API_TOKEN` y, para la segunda opinión
multimodelo, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`.

`nutria doctor` comprueba qué fuentes son accesibles. Dominios necesarios: `eutils.ncbi.nlm.nih.gov`,
`pubchem.ncbi.nlm.nih.gov`, `www.ebi.ac.uk`, `api.openalex.org`, `api.crossref.org`, `rest.uniprot.org`,
`string-db.org`, `reactome.org` (detalle en `docs/FUENTES.md`).

## Uso con Claude Code

Abre Claude Code en la carpeta del proyecto. `CLAUDE.md` convierte la sesión principal en el director científico y
orquestador.

| Orden | Qué hace |
|---|---|
| `/mision-1 [foco]` | Primera misión paso a paso, con revisión humana entre fases |
| `/mision-1-auto` | La misma misión como workflow multiagente determinista (consume muchos tokens) |
| `/oportunidad O-0003` | Evaluación en profundidad de una oportunidad |
| `/evaluar-oportunidad-auto` | Lo mismo como workflow (8 especialistas, verificación y puntuación ciega) |
| `/revision-sistematica "<pregunta PECO>"` | Revisión sistemática según PRISMA 2020 |
| `/metaanalisis O-0003 "<variable>"` | Síntesis cuantitativa a partir de las extracciones |
| `/informe O-0003` | Informe de 20 secciones, verificado y sellado |
| `/red-team <fichero>` · `/red-team-auto` | Revisión adversarial |
| `/estado` | Estado del proyecto y siguientes pasos |

Los modelos de cada agente (`model:` en `.claude/agents/*.md`) están elegidos así: `opus` para razonamiento crítico
(director, crítico, regulación, estadística, fisiología, compuestos, nutrición) y `sonnet` para tareas de alto volumen
(búsqueda, extracción, verificación, bioinformática, industrial, economía, sostenibilidad). Cámbialos según tu
presupuesto.

## Herramientas `nutria` (resumen)

```bash
nutria buscar pubmed "dairy cows AND rumen-protected choline AND transition" --max 25 --resumenes
```

```bash
nutria fuente agregar --doi 10.1017/S1751731108002280 --tipo-estudio revision_narrativa
```

```bash
nutria meta analizar ejemplos/validacion_bcg.csv --metodo REML --hksj --moderador ablat
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

Lista completa: `nutria --help` y `nutria <orden> --help`. Todas las órdenes aceptan `--json`, `--agente <nombre>` y
`--offline` (solo caché).

## Garantías y límites

- **Verificado en este repositorio**: 42 pruebas automáticas (`pytest`), incluida la reproducción exacta de los
  resultados publicados de metafor para el conjunto clásico BCG (efectos fijos, DerSimonian-Laird, REML,
  Hartung-Knapp, Egger y meta-regresión).
- **Pendiente de probar en tu entorno**: los clientes de las APIs externas se escribieron según su documentación y se
  probaron con respuestas de ejemplo, pero no contra los servicios reales, porque el entorno donde se construyó
  bloqueaba esos dominios. Ejecuta `nutria doctor` y una búsqueda real antes de confiar en ellos.
- Los ficheros de `ejemplos/` marcados `ILUSTRATIVO` muestran el formato; sus números no son datos.
- El sistema no sustituye a especialistas humanos: las compuertas, la selección de candidatos y cualquier paso hacia
  experimentos requieren validación humana. Las conclusiones regulatorias son preliminares.

## Estructura

```
CLAUDE.md                  constitución del orquestador
.claude/agents/            14 subagentes
.claude/skills/            13 skills (misiones y procedimientos)
.claude/workflows/         3 workflows deterministas
.claude/settings.json      permisos y hook de verificación
scripts/hooks/             hook PostToolUse
src/nutria/                herramientas (CLI nutria)
docs/                      método, auditoría, rúbrica, regulación, fuentes, arquitectura
ejemplos/                  plantillas de entrada y conjunto de validación
evals/                     banco de preguntas para evaluar modelos
research/                  registro, procedencia y resultados de la investigación
tests/                     pruebas
```

## Personalización

- Pesos y anclas de la rúbrica: copia `src/nutria/data/rubrica.toml` a `config/rubrica.toml`.
- Registro de modelos para segunda opinión: copia `src/nutria/data/modelos.toml` a `config/modelos.toml`, rellena los
  identificadores exactos y actívalos; elige modelo por rol con `nutria llm evaluar evals/dominio_semilla.jsonl`
  (amplía el banco con expertos: el semilla tiene 10 preguntas).
