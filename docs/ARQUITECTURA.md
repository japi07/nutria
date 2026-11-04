# Arquitectura del sistema

## Capas

```mermaid
flowchart TB
    H([Persona responsable<br/>decisiones con firma]) <--> O
    subgraph CC[Claude Code]
        O[Orquestador / director científico<br/>CLAUDE.md]
        S[Skills: misiones y procedimientos<br/>.claude/skills]
        W[Workflows deterministas<br/>.claude/workflows]
        subgraph AG[Subagentes con contexto propio · .claude/agents]
            A1[bibliográfico] & A2[extractor] & A3[fisiología] & A4[compuestos] & A5[nutrición]
            A6[bioinformático] & A7[estadístico] & A8[crítico] & A9[verificador] & A10[regulación]
            A11[industrial] & A12[economía y PI] & A13[sostenibilidad] & A14[director]
        end
        HK{{Hook PostToolUse<br/>verifica research/}}
    end
    O --> S & W --> AG
    AG -->|nutria CLI| T[Herramientas nutria<br/>búsqueda · verificación · estadística · puntuación · economía · informes]
    T --> R[(Registro SQLite<br/>fuentes, extracciones, afirmaciones,<br/>hipótesis, oportunidades, puntuaciones,<br/>compuertas, decisiones)]
    T --> P[[procedencia.jsonl]]
    T <--> X[(Fuentes externas<br/>PubMed · Europe PMC · OpenAlex · Crossref<br/>ChEMBL · PubChem · UniProt · STRING · Reactome)]
    T -.opcional.-> M[Otros modelos<br/>segunda opinión, banco de pruebas]
    AG -->|escriben| F[research/<br/>informes, planes, revisiones, datos]
    F --> HK
```

**Principio de diseño**: el razonamiento lo hacen los agentes; todo lo que puede comprobarse de forma determinista
(existencia de una referencia, un cálculo, un ranking, una compuerta) lo hacen las herramientas, y queda registrado.

## Embudo de fases y compuertas

```mermaid
flowchart LR
    F1[F1 Mapa de<br/>problemas] --> F2[F2 Oportunidades<br/>+ novedad]
    F2 --> G1{F3 Triage<br/>regulatorio y seguridad}
    G1 -- bloqueada --> D1[(Descartada<br/>con motivo)]
    G1 --> F4[F4 Evidencia] --> F5[F5 Mecanismo y<br/>destino digestivo] --> F6[F6 Cuantificación]
    F6 --> F7{F7 Industrial<br/>compuerta analítica}
    F7 --> F8{F8 Economía y PI<br/>compuerta FTO}
    F8 --> F9[F9 Priorización<br/>Monte Carlo]
    F9 --> F10[F10 Plan de validación<br/>compuerta ética] --> F11([F11 Decisión humana])
    F7 -- bloqueada --> D1
    F8 -- bloqueada --> D1
```

## Flujo de la evidencia

```mermaid
sequenceDiagram
    participant B as investigador-bibliografico
    participant N as nutria
    participant E as extractor-datos (x2)
    participant S as estadistico
    participant V as verificador-citas
    B->>N: buscar pubmed/europepmc/openalex (consultas registradas)
    B->>N: fuente agregar --doi (verifica antes de registrar)
    E->>N: extraccion agregar (doble extracción, ubicación del dato)
    S->>N: extraccion exportar → meta analizar (REML + HKSJ)
    S->>N: afirmacion agregar [DER] con la orden usada
    V->>N: informe comprobar --online --sellar
    N-->>V: sello SHA-256 (caduca si el informe cambia)
```

## Correspondencia con ficheros

| Componente | Ubicación |
|---|---|
| Constitución y reglas | `CLAUDE.md` |
| Método completo | `docs/PROMPT_MAESTRO_v2.md` |
| Roles | `.claude/agents/*.md` (14) |
| Misiones y procedimientos | `.claude/skills/*/SKILL.md` (13) |
| Orquestación determinista | `.claude/workflows/*.js` (3) |
| Permisos y hook | `.claude/settings.json`, `scripts/hooks/check_research_file.py` |
| Herramientas | `src/nutria/` (CLI `nutria`) |
| Esquemas de datos | `src/nutria/data/schemas/*.json` |
| Rúbrica y modelos | `src/nutria/data/rubrica.toml`, `src/nutria/data/modelos.toml` (personalizables en `config/`) |
| Registro y trazas | `research/nutria.sqlite`, `research/procedencia.jsonl` |
| Pruebas | `tests/` (incluye validación del metaanálisis frente a metafor) |
