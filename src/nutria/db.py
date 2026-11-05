"""Registro de investigación en SQLite: fuentes, extracciones, afirmaciones, hipótesis,
oportunidades, puntuaciones (con historial), compuertas (con firma humana), verificaciones
manuales y decisiones.

Diseñado para varios agentes concurrentes (WAL + busy_timeout) y para que nada se borre:
los cambios de estado quedan en `decisions` con su motivo, y las puntuaciones sobrescritas
se conservan en `scores_history`.

Reglas que el registro hace cumplir (no solo recomienda):
- una afirmación [PUB] exige fuentes registradas, verificadas y no retractadas, cita textual, ubicación y
  contexto biológico (especie, etapa o estado fisiológico, diseño, unidad experimental, n, dosis y su base, forma);
- una extracción exige que su fuente esté verificada; su estado de doble extracción ('doble_concordante',
  'resuelta') solo lo fija el sistema a partir de una comparación registrada de dos copias depositadas por
  extractores distintos y, si hubo discrepancias, de una conciliación registrada;
- una ruta regulatoria fuera_de_alcance_* bloquea la compuerta regulatoria (al registrar o al clasificar);
- una compuerta "superada" solo cuenta si la firma una persona; salir de "bloqueada" exige firma;
- pasar una oportunidad a "priorizada" o "en_validacion" exige firma y compuertas en regla.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Iterator

from .provenance import current_actor, declared_agent, execution_context, signature_problems, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  doi TEXT, pmid TEXT, pmcid TEXT, openalex_id TEXT,
  title TEXT, authors TEXT, year INTEGER, journal TEXT, volume TEXT, issue TEXT, pages TEXT,
  pub_types TEXT, study_type TEXT, species TEXT,
  retracted INTEGER, verified INTEGER NOT NULL DEFAULT 0, verified_at TEXT, verified_via TEXT,
  url TEXT, abstract TEXT, notes TEXT, added_at TEXT, added_by TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sources_doi ON sources(doi) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_sources_pmid ON sources(pmid) WHERE pmid IS NOT NULL;

CREATE TABLE IF NOT EXISTS extractions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id TEXT NOT NULL REFERENCES sources(id),
  experiment TEXT,
  species TEXT NOT NULL, breed TEXT, sex TEXT, age TEXT, physiological_stage TEXT, parity TEXT,
  design TEXT, experimental_unit TEXT, duration_days REAL, basal_diet TEXT,
  intervention TEXT NOT NULL, dose_value REAL, dose_unit TEXT, dose_basis TEXT, product_form TEXT,
  comparator TEXT, outcome TEXT NOT NULL, outcome_unit TEXT,
  n_ctrl REAL, n_trt REAL, mean_ctrl REAL, mean_trt REAL,
  dispersion_type TEXT, disp_ctrl REAL, disp_trt REAL, p_value REAL,
  location TEXT NOT NULL, funding TEXT, conflicts TEXT,
  extractor TEXT, extracted_at TEXT,
  check_status TEXT NOT NULL DEFAULT 'simple', checked_by TEXT, notes TEXT
);

-- Doble extracción (D2-12): copias depositadas por cada extractor y comparaciones registradas. El estado
-- 'doble_concordante' o 'resuelta' de una extracción solo lo fija el registro a partir de una comparación; las copias
-- y las comparaciones no se modifican ni se borran (una comparación solo admite su conciliación, una vez).
CREATE TABLE IF NOT EXISTS extraction_copies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sha256 TEXT NOT NULL, path TEXT, records TEXT NOT NULL, n_records INTEGER NOT NULL, source_ids TEXT,
  deposited_by TEXT NOT NULL, deposited_at TEXT NOT NULL,
  UNIQUE (sha256, deposited_by)
);

CREATE TABLE IF NOT EXISTS extraction_comparisons (
  id TEXT PRIMARY KEY,
  copy_a INTEGER NOT NULL REFERENCES extraction_copies(id),
  copy_b INTEGER NOT NULL REFERENCES extraction_copies(id),
  file_a TEXT, sha256_a TEXT NOT NULL, actor_a TEXT NOT NULL,
  file_b TEXT, sha256_b TEXT NOT NULL, actor_b TEXT NOT NULL,
  discrepancies TEXT NOT NULL,
  result TEXT NOT NULL CHECK (result IN ('concordante', 'con_discrepancias')),
  compared_by TEXT, compared_at TEXT NOT NULL,
  conciliation TEXT, conciliation_sha256 TEXT, conciliated_by TEXT, conciliated_at TEXT
);

CREATE TRIGGER IF NOT EXISTS extractions_estado_doble_alta BEFORE INSERT ON extractions
WHEN NEW.check_status <> 'simple'
  AND NOT EXISTS (SELECT 1 FROM extraction_comparisons c WHERE c.id = NEW.comparison_id)
BEGIN SELECT RAISE(ABORT, 'el estado de doble extraccion solo lo fija nutria a partir de una comparacion registrada'); END;
CREATE TRIGGER IF NOT EXISTS extractions_estado_doble_cambio
BEFORE UPDATE OF check_status, checked_by, comparison_id, pair_a, pair_b ON extractions
BEGIN SELECT RAISE(ABORT, 'el estado de doble extraccion de una extraccion registrada no se modifica'); END;
CREATE TRIGGER IF NOT EXISTS extraction_copies_sin_cambios BEFORE UPDATE ON extraction_copies
BEGIN SELECT RAISE(ABORT, 'una copia depositada no se modifica'); END;
CREATE TRIGGER IF NOT EXISTS extraction_copies_sin_borrado BEFORE DELETE ON extraction_copies
BEGIN SELECT RAISE(ABORT, 'una copia depositada no se borra'); END;
CREATE TRIGGER IF NOT EXISTS extraction_comparisons_sin_borrado BEFORE DELETE ON extraction_comparisons
BEGIN SELECT RAISE(ABORT, 'una comparacion registrada no se borra'); END;
CREATE TRIGGER IF NOT EXISTS extraction_comparisons_una_conciliacion BEFORE UPDATE ON extraction_comparisons
WHEN OLD.result <> 'con_discrepancias' OR OLD.conciliation IS NOT NULL OR NEW.conciliation IS NULL
  OR NEW.id IS NOT OLD.id OR NEW.copy_a IS NOT OLD.copy_a
  OR NEW.copy_b IS NOT OLD.copy_b OR NEW.file_a IS NOT OLD.file_a OR NEW.sha256_a IS NOT OLD.sha256_a
  OR NEW.actor_a IS NOT OLD.actor_a OR NEW.file_b IS NOT OLD.file_b OR NEW.sha256_b IS NOT OLD.sha256_b
  OR NEW.actor_b IS NOT OLD.actor_b OR NEW.discrepancies IS NOT OLD.discrepancies OR NEW.result IS NOT OLD.result
  OR NEW.compared_by IS NOT OLD.compared_by OR NEW.compared_at IS NOT OLD.compared_at
BEGIN SELECT RAISE(ABORT, 'una comparacion registrada solo admite su conciliacion, una vez'); END;

CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  label TEXT NOT NULL CHECK (label IN ('PUB','DER','HIP','SUP','DESC')),
  evidence_level TEXT, transferability TEXT, source_ids TEXT, quote TEXT, location TEXT,
  entity_id TEXT, created_by TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS opportunities (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL, problem TEXT, species TEXT, production_stage TEXT, mechanism TEXT,
  candidates TEXT, product_type TEXT, regulatory_route TEXT,
  status TEXT NOT NULL DEFAULT 'propuesta', status_reason TEXT, maturity TEXT DEFAULT 'EMC-0',
  data TEXT, created_by TEXT, created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS hypotheses (
  id TEXT PRIMARY KEY,
  opportunity_id TEXT REFERENCES opportunities(id),
  title TEXT NOT NULL, origin TEXT, prior_probability REAL,
  status TEXT NOT NULL DEFAULT 'propuesta', status_reason TEXT,
  data TEXT, created_by TEXT, created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
  rater TEXT NOT NULL, criterion TEXT NOT NULL,
  value REAL NOT NULL CHECK (value >= 0 AND value <= 1),
  low REAL, high REAL, rationale TEXT, evidence TEXT, created_at TEXT,
  UNIQUE (opportunity_id, rater, criterion)
);

CREATE TABLE IF NOT EXISTS scores_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id TEXT NOT NULL, rater TEXT NOT NULL, criterion TEXT NOT NULL,
  value REAL, low REAL, high REAL, rationale TEXT, evidence TEXT, created_at TEXT,
  replaced_at TEXT NOT NULL, replaced_by TEXT
);

CREATE TABLE IF NOT EXISTS gates (
  opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
  gate TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('superada','bloqueada','no_resuelta')),
  rationale TEXT, evidence TEXT, decided_by TEXT, updated_at TEXT,
  human_signoff TEXT, signed_at TEXT,
  PRIMARY KEY (opportunity_id, gate)
);

CREATE TABLE IF NOT EXISTS manual_verifications (
  url TEXT PRIMARY KEY,
  signed_by TEXT NOT NULL, signed_at TEXT NOT NULL, note TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, entity_id TEXT, decision TEXT NOT NULL, rationale TEXT NOT NULL,
  decided_by TEXT, human_signoff TEXT
);

"""

# Columnas añadidas después de la versión 1 del esquema (migración aditiva).
MIGRATIONS = {
    "gates": [("human_signoff", "TEXT"), ("signed_at", "TEXT")],
    "extractions": [("forage_pct_dm", "REAL"), ("ndf_pct_dm", "REAL"), ("starch_pct_dm", "REAL"),
                    ("fat_pct_dm", "REAL"), ("cp_pct_dm", "REAL"), ("dcad_meq_kg_dm", "REAL"),
                    ("ev_ctrl", "REAL"), ("ev_trt", "REAL"), ("not_reported", "TEXT")],
    # Licencia del artículo o del dato según la API (Crossref, OpenAlex, DataCite): URL o código (cc-by...).
    "sources": [("license", "TEXT")],
}
# PDR, ventanas respecto al hito y definición de caso, doble extracción (comparación y pareja de registros), contexto
# biológico de las afirmaciones y clasificación regulatoria de las oportunidades.
for _table, _columns in {
    "extractions": [("rdp_pct_cp", "REAL"), ("rdp_pct_dm", "REAL"), ("time_reference", "TEXT"),
                    ("window_start_days", "REAL"), ("window_end_days", "REAL"), ("case_definition", "TEXT"),
                    ("supp_start_days", "REAL"), ("supp_end_days", "REAL"), ("comparison_id", "TEXT"),
                    ("pair_a", "INTEGER"), ("pair_b", "INTEGER")],
    "claims": [("context", "TEXT")],
    "opportunities": [("functional_group", "TEXT"), ("regulatory_situation", "TEXT"), ("classified_by", "TEXT"),
                      ("classified_at", "TEXT")],
}.items():
    MIGRATIONS.setdefault(_table, []).extend(_columns)

OPPORTUNITY_STATUSES = ("propuesta", "en_evaluacion", "priorizada", "bloqueada", "descartada",
                        "en_validacion")
HYPOTHESIS_STATUSES = ("propuesta", "activa", "en_revision", "refutada", "descartada", "promovida")
GATE_STATUSES = ("superada", "bloqueada", "no_resuelta")
OUT_OF_SCOPE_ROUTES = ("fuera_de_alcance_medicamento", "fuera_de_alcance_biocida")

# ---------------------------------------------------------------------------- firma, roles y trazabilidad
# Reglas que añade este bloque (además de las del encabezado del módulo):
# - sin firma humana, cada compuerta la propone solo su rol propietario (GATE_OWNERS); "bloqueada" la puede proponer
#   cualquiera, y una propuesta sin firma no sustituye una decisión firmada salvo para bloquear (ver `set_gate`);
# - salir de "bloqueada" exige, además de la firma, evidencia comprobable;
# - las puntuaciones de un evaluador solo las registra ese evaluador, y el historial guarda quién las sustituyó; el
#   identificador del evaluador se normaliza (`normalize_rater`) para que no cuente dos veces;
# - `decisions`, `scores_history` e `inventive_contributions` son solo de inserción (triggers de SQLite contra UPDATE,
#   DELETE e INSERT OR REPLACE, que también se crean en los registros ya existentes al conectar); una decisión solo se
#   registra sobre una entidad que existe;
# - la autoría inventiva humana (regla 11) se registra con firma en `inventive_contributions`.
# La firma (--firma-humana) es una atestación sin autenticar: la CLI la rechaza en contexto de agente, pero no
# identifica a la persona, y quien tenga acceso de escritura a la base de datos puede falsificarla.

# Rol que propone cada compuerta (CLAUDE.md, tabla del equipo).
GATE_OWNERS = {
    "seguridad": "compuestos-toxicologia",
    "regulatoria": "regulacion-seguridad",
    "analitica": "desarrollo-industrial",
    "propiedad_intelectual": "economia-mercado-pi",
    "etica": "sostenibilidad-bienestar",
    "destino_digestivo": "fisiologia-bioquimica",
}
# Identificadores del registro que puede citar la evidencia de una compuerta (se comprueba que existan).
REGISTRY_ID_PATTERN = r"(?:[OHA]-\d{4,}|E-?\d+|doi:\S+|pmid:\d+|openalex:\S+)"

TRAZABILIDAD_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventive_contributions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id TEXT NOT NULL, person TEXT NOT NULL, contribution TEXT NOT NULL,
  signed_by TEXT NOT NULL, signed_at TEXT NOT NULL, recorded_by TEXT, context TEXT
);
CREATE TRIGGER IF NOT EXISTS decisions_sin_update BEFORE UPDATE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions es solo de inserción: una decisión registrada no se modifica'); END;
CREATE TRIGGER IF NOT EXISTS decisions_sin_delete BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions es solo de inserción: una decisión registrada no se borra'); END;
CREATE TRIGGER IF NOT EXISTS scores_history_sin_update BEFORE UPDATE ON scores_history
BEGIN SELECT RAISE(ABORT, 'scores_history es solo de inserción: el historial no se modifica'); END;
CREATE TRIGGER IF NOT EXISTS scores_history_sin_delete BEFORE DELETE ON scores_history
BEGIN SELECT RAISE(ABORT, 'scores_history es solo de inserción: el historial no se borra'); END;
CREATE TRIGGER IF NOT EXISTS inventive_contributions_sin_update BEFORE UPDATE ON inventive_contributions
BEGIN SELECT RAISE(ABORT, 'inventive_contributions es solo de inserción: la autoría registrada no se modifica'); END;
CREATE TRIGGER IF NOT EXISTS inventive_contributions_sin_delete BEFORE DELETE ON inventive_contributions
BEGIN SELECT RAISE(ABORT, 'inventive_contributions es solo de inserción: la autoría registrada no se borra'); END;
"""
# INSERT OR REPLACE (y REPLACE INTO) borra la fila que choca sin disparar los triggers de DELETE (recursive_triggers
# está desactivado por defecto): un trigger BEFORE INSERT rechaza cualquier inserción con un id que ya existe. En un id
# automático, NEW.id vale -1 en un trigger BEFORE INSERT, así que las inserciones normales no se ven afectadas.
for _table, _what in (("decisions", "una decisión registrada"), ("scores_history", "el historial"),
                      ("inventive_contributions", "la autoría registrada")):
    TRAZABILIDAD_SCHEMA += (
        f"CREATE TRIGGER IF NOT EXISTS {_table}_sin_reemplazo BEFORE INSERT ON {_table} "
        f"WHEN EXISTS (SELECT 1 FROM {_table} WHERE id = NEW.id) BEGIN SELECT RAISE(ABORT, '{_table} es solo de "
        f"inserción: {_what} no se sustituye (INSERT OR REPLACE)'); END;\n")
del _table, _what


def _ensure_trazabilidad(conn: sqlite3.Connection) -> None:
    """Migración de la trazabilidad (también en registros existentes): columna `context` de `decisions` (contexto de
    ejecución: claude-code o terminal), tabla de autoría inventiva y triggers de solo inserción."""
    if "context" not in {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}:
        conn.execute("ALTER TABLE decisions ADD COLUMN context TEXT")
    conn.executescript(TRAZABILIDAD_SCHEMA)


def require_human_signature(signed: str | None) -> None:
    """Regla 12 en el propio registro: una firma humana solo se guarda desde la terminal de una persona, aunque se
    llame a estas funciones sin pasar por la CLI (ver `provenance.signature_problems`). Sin firma, no hace nada."""
    if not (signed or "").strip():
        return
    problems = signature_problems()
    if problems:
        raise RegistryError("Firma humana rechazada (regla 12): la firma humana no la registra un agente; solo se "
                            "acepta desde la terminal de una persona. Detectado: " + ", ".join(dict.fromkeys(problems)))


class RegistryError(ValueError):
    """Error de integridad del registro (estado inválido, entidad inexistente, regla incumplida...)."""


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    _ensure_trazabilidad(conn)
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '2')")
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('created_at', ?)", (utc_now(),))
    return conn


_savepoint_counter = 0


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Transacción reentrante: dentro de otra transacción usa un SAVEPOINT, así que una
    operación por lotes es atómica aunque llame a funciones que abren su propia transacción."""
    global _savepoint_counter
    if conn.in_transaction:
        _savepoint_counter += 1
        name = f"sp_{_savepoint_counter}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield conn
        except Exception:
            conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
            conn.execute(f"RELEASE SAVEPOINT {name}")
            raise
        else:
            conn.execute(f"RELEASE SAVEPOINT {name}")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _next_id(conn: sqlite3.Connection, table: str, prefix: str) -> str:
    row = conn.execute(
        f"SELECT id FROM {table} WHERE id LIKE ? ORDER BY CAST(substr(id, ?) AS INTEGER) DESC LIMIT 1",
        (f"{prefix}-%", len(prefix) + 2),
    ).fetchone()
    number = int(row["id"].split("-")[1]) + 1 if row else 1
    return f"{prefix}-{number:04d}"


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for key in ("authors", "pub_types", "source_ids", "candidates", "data", "evidence", "not_reported", "context",
                "discrepancies", "conciliation", "records"):
        if isinstance(out.get(key), str):
            try:
                out[key] = json.loads(out[key])
            except ValueError:
                pass
    return out


# ---------------------------------------------------------------------------- fuentes
_SOURCE_FIELDS = ("doi", "pmid", "pmcid", "openalex_id", "title", "authors", "year", "journal", "volume",
                  "issue", "pages", "pub_types", "study_type", "species", "url", "abstract", "notes", "license")
# Identificadores: en un registro existente solo los fija una verificación. Unos datos sin verificar no los
# cambian ni los rellenan (crearían vínculos DOI↔PMID sin comprobar).
_IDENTITY_FIELDS = ("doi", "pmid", "pmcid", "openalex_id")
# Campos protegidos de una fuente verificada: solo cambian con otra verificación.
_PROTECTED_FIELDS = _IDENTITY_FIELDS + ("title", "authors", "year", "journal")
# Campos no críticos: unos datos sin verificar solo pueden rellenarlos si están vacíos.
_NONCRITICAL_FIELDS = ("volume", "issue", "pages", "pub_types", "url", "abstract", "license")
# Campos que aporta quien registra la fuente (`fuente agregar --tipo-estudio --especie --notas`).
_USER_FIELDS = ("study_type", "species", "notes")


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == "[]"


def _source_fields(record: dict) -> dict:
    from .sources.base import normalize_doi
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
    fields = {k: record.get(k) for k in _SOURCE_FIELDS}
    fields.update(doi=normalize_doi(record.get("doi")),
                  pmid=str(record["pmid"]).strip() if record.get("pmid") else None,
                  license=record.get("license") or extra.get("license"),
                  authors=_dumps(record.get("authors") or None), pub_types=_dumps(record.get("pub_types") or None))
    return {k: (None if _empty(v) else v) for k, v in fields.items()}


def _primary_field(row: sqlite3.Row) -> str:
    """Identificador que da nombre a la fila (doi:..., pmid:..., openalex:...): nunca se cambia ni se borra."""
    sid = row["id"]
    return "doi" if sid.startswith("doi:") else ("pmid" if sid.startswith("pmid:") else "openalex_id")


def _trusted_retraction(row: sqlite3.Row) -> int | None:
    """Retractación fiable de una fila: la de una fuente verificada; de una sin verificar, solo un 1 (prudencia)."""
    value = row["retracted"]
    return value if (value == 1 or row["verified"]) else None


def _anchor(record: dict, fields: dict) -> str:
    """Identificador que avala una verificación: el consultado (`extra["verified_by"]`: el DOI en `verify_doi`, el
    PMID en `verify_pmid`). El otro es un dato que las fuentes asocian (el PMID que confirma PubMed para un DOI, el
    DOI que declara PubMed para un PMID) y puede ser de otro trabajo. Sin la marca, el DOI."""
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
    anchor = extra.get("verified_by")
    if anchor in ("doi", "pmid"):
        return anchor
    return "doi" if fields.get("doi") else "pmid"


def _authors_of(value: Any) -> list[str]:
    if isinstance(value, list):
        return value
    try:
        data = json.loads(value) if value else []
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _row_mismatch(row: sqlite3.Row, found_by: str, fields: dict, record: dict, years: bool = True) -> list[str]:
    """Motivos por los que `row`, encontrada por el identificador `found_by`, no es el trabajo verificado: título
    distinto (ninguna variante verificada, ni la traducción de PubMed "[...]" con el mismo primer autor y año) o, con
    `years`, años incompatibles. Vacío si es el mismo trabajo."""
    from .verify.citations import is_main_title_of, titles_same_work, translation_of
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
    # «main_title»: el título sin el subtítulo que Crossref o DataCite depositan aparte, con que se guardaban las filas
    # antes de unirlos (`sources.base.full_title`); una fila así sigue siendo el mismo trabajo.
    titles = [t for t in [fields.get("title"), extra.get("main_title"), *(extra.get("title_variants") or [])] if t]
    problems = []
    if not row["title"]:
        if _primary_field(row) != found_by:
            problems.append(f"{row['id']} no tiene título con el que comparar")
    elif not titles:
        problems.append("el registro verificado no trae título con el que comparar")
    elif not any(titles_same_work(t, row["title"]) or is_main_title_of(row["title"], t) for t in titles):
        row_first = next(iter(_authors_of(row["authors"])), None)
        rec_first = next(iter(_authors_of(fields.get("authors"))), None)
        rec_title = fields.get("title")
        translated = (
            translation_of(rec_title, row["title"], year=fields.get("year"), other_year=row["year"],
                           first_author=rec_first, other_first_author=row_first)
            or translation_of(row["title"], rec_title, year=row["year"], other_year=fields.get("year"),
                              first_author=row_first, other_first_author=rec_first))
        if not translated:
            problems.append(f"el título de {row['id']} («{row['title'][:90]}») no es el del trabajo verificado "
                            f"(«{str(rec_title or '')[:90]}»)")
    if years and fields.get("year") and row["year"] and abs(int(fields["year"]) - int(row["year"])) > 1:
        problems.append(f"años incompatibles ({row['year']} en {row['id']}, {fields['year']} verificado)")
    return problems


def _same_work_or_error(by_doi: sqlite3.Row, by_pmid: sqlite3.Row, fields: dict, record: dict):
    """Antes de fusionar dos filas (una con el DOI y otra con el PMID de un registro VERIFICADO) comprueba que son
    el mismo trabajo: la verificación relaciona ese DOI con ese PMID (`fields`), ninguna fila tiene otro DOI u otro
    PMID, el título de cada fila es el del trabajo verificado (o alguna de sus variantes: el de Crossref, el de
    OpenAlex, el de PubMed, que puede ir traducido) y los años son compatibles. Una fila sin título solo se acepta
    si se llama por el identificador que coincide (doi:<DOI> o pmid:<PMID>). Devuelve (fila que se conserva, fila
    que se elimina) o lanza RegistryError sin tocar nada."""
    from .verify.citations import is_main_title_of, titles_same_work
    via = record.get("verified_via")
    problems = []
    if by_doi["pmid"] and by_doi["pmid"] != fields["pmid"]:
        problems.append(f"{by_doi['id']} tiene el PMID {by_doi['pmid']}")
    if by_pmid["doi"] and by_pmid["doi"] != fields["doi"]:
        problems.append(f"{by_pmid['id']} tiene el DOI {by_pmid['doi']}")
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
    # «main_title»: el título sin el subtítulo que Crossref o DataCite depositan aparte, con que se guardaban las filas
    # antes de unirlos (`sources.base.full_title`); una fila así sigue siendo el mismo trabajo.
    titles = [t for t in [fields.get("title"), extra.get("main_title"), *(extra.get("title_variants") or [])] if t]
    for row, own_id in ((by_doi, f"doi:{fields['doi']}"), (by_pmid, f"pmid:{fields['pmid']}")):
        if not row["title"]:
            if row["id"] != own_id:
                problems.append(f"{row['id']} no tiene título con el que comparar y no se llama {own_id}")
        elif not titles:
            problems.append("el registro verificado no trae título con el que comparar")
        elif not any(titles_same_work(t, row["title"]) or is_main_title_of(row["title"], t) for t in titles):
            problems.append(f"el título de {row['id']} («{row['title'][:90]}») no es el del trabajo verificado")
    years = [y for y in (fields.get("year"), by_doi["year"], by_pmid["year"]) if y]
    if years and max(years) - min(years) > 1:
        problems.append(f"años incompatibles ({', '.join(str(y) for y in years)})")
    if problems:
        raise RegistryError(
            f"No se fusionan {by_doi['id']} y {by_pmid['id']}: la verificación ({via or 'sin indicar'}) dice que el "
            f"DOI {fields['doi']} y el PMID {fields['pmid']} son el mismo trabajo, pero " + "; ".join(problems)
            + ". No se ha cambiado ni borrado nada: revisa las dos fuentes.")
    # Se conserva la fila verificada (sus identificadores pueden estar citados fuera del registro); entre dos
    # iguales, la que se llama por el DOI.
    if by_pmid["verified"] and not by_doi["verified"]:
        return by_pmid, by_doi
    return by_doi, by_pmid


def _merge_duplicate_sources(conn: sqlite3.Connection, keep: sqlite3.Row, drop: sqlite3.Row,
                             actor: str | None, via: str | None, changes: list) -> None:
    """Fusiona en `keep` dos registros del mismo trabajo (comprobado con `_same_work_or_error`)."""
    keep_id, drop_id = keep["id"], drop["id"]
    conn.execute("UPDATE extractions SET source_id = ? WHERE source_id = ?", (keep_id, drop_id))
    for claim in conn.execute("SELECT id, source_ids FROM claims WHERE source_ids LIKE ?", (f'%"{drop_id}"%',)).fetchall():
        ids = [keep_id if s == drop_id else s for s in json.loads(claim["source_ids"] or "[]")]
        conn.execute("UPDATE claims SET source_ids = ? WHERE id = ?", (_dumps(list(dict.fromkeys(ids))), claim["id"]))
    values = (_trusted_retraction(keep), _trusted_retraction(drop))
    updates: dict[str, Any] = {"doi": keep["doi"] or drop["doi"], "pmid": keep["pmid"] or drop["pmid"],
                               "retracted": 1 if 1 in values else (0 if 0 in values else None)}
    copyable = _NONCRITICAL_FIELDS + _USER_FIELDS + (("pmcid", "openalex_id") if drop["verified"] else ())
    for key in copyable:
        if _empty(keep[key]) and not _empty(drop[key]):
            updates[key] = drop[key]
    conn.execute("DELETE FROM sources WHERE id = ?", (drop_id,))
    conn.execute(f"UPDATE sources SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?",
                 [*updates.values(), keep_id])
    _log_decision(conn, keep_id, f"fusión de fuentes duplicadas ({drop_id} -> {keep_id})",
                  f"Mismo trabajo: la verificación ({via or 'sin indicar'}) relaciona el DOI {updates['doi']} con el "
                  f"PMID {updates['pmid']} y los títulos coinciden", actor)
    changes.append({"id": keep_id, "accion": "fusion", "eliminada": drop_id})


def _reverify_command(sid: str) -> str:
    """Orden que vuelve a verificar una fuente, también si ya está verificada (`verificar --pendientes` solo revisa
    las no verificadas o con la retractación desconocida)."""
    kind, _, value = sid.partition(":")
    if kind in ("doi", "pmid") and value:
        return f"`nutria fuente agregar --{kind} {value}`"
    return "`nutria fuente agregar --doi <DOI>` o `--pmid <PMID>` (la fuente no tiene DOI ni PMID con que verificarla)"


def _retraction_warning(sid: str, record: dict, current: int | None, changes: list) -> None:
    """Unos datos sin verificar que dicen "retractado" no cambian el registro, pero no se callan: se avisa (y queda
    en la procedencia) con la orden que lo comprueba."""
    if record.get("retracted") is True and current != 1:
        changes.append({"id": sid, "accion": "aviso_retractacion", "motivo": (
            "los datos sin verificar indican que el trabajo está RETRACTADO; solo una verificación cambia la "
            f"retractación del registro: vuelve a verificarla con {_reverify_command(sid)}")})


def _apply_unverified(conn: sqlite3.Connection, existing: sqlite3.Row, fields: dict, record: dict,
                      changes: list) -> str:
    """Datos sin verificar (p. ej. `buscar --registrar`) sobre una fuente ya registrada: nunca cambian la
    retractación, los identificadores ni (si está verificada) sus campos protegidos; solo rellenan campos vacíos,
    y solo si el trabajo coincide (mismos identificadores y título del mismo trabajo)."""
    from .verify.citations import titles_same_work
    sid = existing["id"]
    conflicts = [f"{k} registrado {existing[k]}, recibido {fields[k]}" for k in ("doi", "pmid")
                 if fields[k] and existing[k] and fields[k] != existing[k]]
    if fields.get("title") and existing["title"] and not titles_same_work(existing["title"], fields["title"]):
        conflicts.append(f"título distinto («{str(fields['title'])[:90]}»)")
    if conflicts:
        changes.append({"id": sid, "accion": "omitido", "motivo": "datos sin verificar que no coinciden con la "
                        "fuente registrada: " + "; ".join(conflicts)})
        return sid
    _retraction_warning(sid, record, existing["retracted"], changes)
    allowed = _NONCRITICAL_FIELDS if existing["verified"] else _NONCRITICAL_FIELDS + ("title", "authors", "year",
                                                                                        "journal")
    updates = {k: fields[k] for k in allowed if fields.get(k) is not None and _empty(existing[k])}
    if updates:
        conn.execute(f"UPDATE sources SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?",
                     [*updates.values(), sid])
        changes.append({"id": sid, "accion": "rellenado_sin_verificar", "campos": sorted(updates)})
    return sid


def _apply_verified(conn: sqlite3.Connection, existing: sqlite3.Row, fields: dict, record: dict,
                    actor: str | None, changes: list) -> str | None:
    """Datos de una verificación sobre una fuente registrada. Devuelve su id, o None si la fila no es este trabajo
    (se le retira el identificador que una búsqueda le asoció y el registro verificado se da de alta aparte)."""
    sid = existing["id"]
    via = record.get("verified_via")
    verified_row = bool(existing["verified"])
    for key in ("doi", "pmid"):
        if verified_row and fields[key] and existing[key] and fields[key] != existing[key]:
            raise RegistryError(
                f"Conflicto: {sid} está verificada con {key} {existing[key]}, pero esta verificación ({via}) da "
                f"{fields[key]}. No se ha cambiado nada: revisa las dos fuentes.")
    primary = _primary_field(existing)
    if not verified_row and (fields.get(primary) is None or fields[primary] != existing[primary]):
        detached = [k for k in ("doi", "pmid", "openalex_id")
                    if k != primary and fields.get(k) and existing[k] == fields[k]]
        if detached:
            conn.execute(f"UPDATE sources SET {', '.join(f'{k} = NULL' for k in detached)} WHERE id = ?", (sid,))
            detail = ", ".join(f"{k} {existing[k]}" for k in detached)
            _log_decision(conn, sid, f"identificador retirado de la fuente sin verificar ({detail})",
                          f"La verificación ({via}) no confirma que {sid} sea ese trabajo: el vínculo venía de datos "
                          "sin verificar. El registro verificado se da de alta aparte", actor)
            changes.append({"id": sid, "accion": "identificador_retirado", "campos": detached})
        return None
    updates: dict[str, Any] = {}
    corrected: dict[str, list] = {}
    for key in _PROTECTED_FIELDS:
        new, old = fields.get(key), existing[key]
        if key == primary and old is not None:
            continue                  # el identificador que da nombre a la fila no cambia
        if new is not None and new != old:
            updates[key] = new
        elif new is None and old is not None and not verified_row and key != primary:
            updates[key] = None   # dato de una búsqueda que la verificación no confirma
        if key in updates and old is not None:
            corrected[key] = [old, updates[key]]
    for key in _NONCRITICAL_FIELDS + _USER_FIELDS:
        if fields.get(key) is not None and fields[key] != existing[key]:
            updates[key] = fields[key]
    new_flag, old_flag = record.get("retracted"), existing["retracted"]
    if new_flag is True or old_flag == 1:
        flag = 1                      # una retractación conocida nunca se borra
    elif new_flag is False:
        flag = 0
    else:
        flag = _trusted_retraction(existing)
    if flag != old_flag:
        updates["retracted"] = flag
        labels = {None: "desconocida", 0: "no retractada", 1: "RETRACTADA"}
        _log_decision(conn, sid, f"retractación: {labels.get(old_flag, old_flag)} -> {labels[flag]}",
                      f"Verificación vía {via}" + ("" if verified_row or old_flag is None else
                                                   " (el valor anterior venía de datos sin verificar)"), actor)
    ids_changed = {k: v for k, v in corrected.items() if k in _IDENTITY_FIELDS}
    if ids_changed:
        _log_decision(conn, sid, "identificadores corregidos por la verificación: " + ", ".join(
            f"{k} {v[0]} -> {v[1] if v[1] is not None else '(vacío)'}" for k, v in ids_changed.items()),
            f"Verificación vía {via}: los anteriores venían de datos sin verificar", actor)
    updates.update(verified=1, verified_at=utc_now(), verified_via=via)
    conn.execute(f"UPDATE sources SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?",
                 [*updates.values(), sid])
    if corrected:
        changes.append({"id": sid, "accion": "corregido_por_verificacion", "campos": corrected})
    return sid


def _insert_source(conn: sqlite3.Connection, fields: dict, record: dict, verified: bool, actor: str | None,
                   changes: list) -> str:
    if fields["doi"]:
        sid = f"doi:{fields['doi']}"
    elif fields["pmid"]:
        sid = f"pmid:{fields['pmid']}"
    elif fields["openalex_id"]:
        sid = f"openalex:{fields['openalex_id']}"
    else:
        raise RegistryError("La fuente necesita DOI, PMID u OpenAlex ID")
    row = {k: v for k, v in fields.items() if v is not None}
    if verified:
        if record.get("retracted") is not None:
            row["retracted"] = int(bool(record["retracted"]))
        row.update(verified=1, verified_at=utc_now(), verified_via=record.get("verified_via"))
    # Sin verificar, la retractación queda desconocida: solo una verificación la fija.
    row.update(id=sid, added_at=utc_now(), added_by=current_actor(actor))
    conn.execute(f"INSERT INTO sources ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                 list(row.values()))
    changes.append({"id": sid, "accion": "alta", "verificada": verified})
    if not verified:
        _retraction_warning(sid, record, None, changes)
    return sid


def upsert_source(conn: sqlite3.Connection, record: dict, actor: str | None = None,
                  changes: list | None = None) -> str:
    """Inserta o actualiza una fuente identificada por DOI, PMID u OpenAlex.

    - Datos sin verificar (`verified` falso, p. ej. `buscar --registrar`) sobre una fuente existente: nunca
      cambian la retractación (tampoco de desconocida a 0) ni los identificadores, ni los campos protegidos de una
      fuente verificada (doi, pmid, pmcid, openalex_id, título, autores, año y revista); solo rellenan campos
      vacíos no críticos, y nada si el trabajo no coincide. Una fuente nueva sin verificar entra con la
      retractación desconocida. Si los datos dicen "retractado", se avisa (`aviso_retractacion`) sin cambiar nada.
    - Una verificación fija los campos protegidos y la retractación; una retractación conocida nunca se borra.
    - Si el DOI y el PMID de un registro verificado están en dos filas distintas, se fusionan solo si son el mismo
      trabajo (`_same_work_or_error`); si no, o si los datos no están verificados, RegistryError sin cambiar nada.
    - Si una sola fila tiene el identificador que la verificación asocia pero no avala (`_anchor`: el PMID de un DOI
      verificado, el DOI que PubMed declara para un PMID) y esa fila está verificada o se llama por él, solo se
      actualiza si es el mismo trabajo (`_row_mismatch`); si no, no se toca y el registro verificado entra aparte sin
      ese identificador (cambio `identificador_no_asociado` y decisión).
    - Si la fila tiene el identificador que avala la verificación pero ya estaba verificada con el título de otro
      trabajo (p. ej. con el DOI que PubMed declaró por error para un PMID), RegistryError sin cambiar nada.

    `changes`, si se pasa, recibe lo que se hizo (altas, rellenos, correcciones, fusiones, datos omitidos, avisos)
    para dejarlo en la procedencia.
    """
    fields = _source_fields(record)
    verified = bool(record.get("verified"))
    done: list[dict] = []          # solo pasa a `changes` si la transacción se confirma
    with transaction(conn):
        source_id = _upsert_source(conn, fields, record, verified, actor, done)
    if changes is not None:
        changes.extend(done)
    return source_id


def _upsert_source(conn: sqlite3.Connection, fields: dict, record: dict, verified: bool, actor: str | None,
                   changes: list) -> str:
    doi, pmid = fields["doi"], fields["pmid"]
    by_doi = conn.execute("SELECT * FROM sources WHERE doi = ?", (doi,)).fetchone() if doi else None
    by_pmid = conn.execute("SELECT * FROM sources WHERE pmid = ?", (pmid,)).fetchone() if pmid else None
    if by_doi is not None and by_pmid is not None and by_doi["id"] != by_pmid["id"]:
        if not verified:
            raise RegistryError(
                f"Conflicto: el DOI {doi} está registrado en {by_doi['id']} y el PMID {pmid} en {by_pmid['id']}. "
                "Unos datos sin verificar no bastan para fusionarlas (pueden ser trabajos distintos): no se ha "
                "cambiado nada. Si son el mismo trabajo, la verificación (`nutria fuente agregar --doi ...`) las "
                "fusionará.")
        keep, drop = _same_work_or_error(by_doi, by_pmid, fields, record)
        _merge_duplicate_sources(conn, keep, drop, actor, record.get("verified_via"), changes)
        existing = conn.execute("SELECT * FROM sources WHERE id = ?", (keep["id"],)).fetchone()
        merged = True
    else:
        existing = by_doi or by_pmid
        merged = False
    not_linked = None
    if verified and existing is not None and not merged:
        found = [k for k, row in (("doi", by_doi), ("pmid", by_pmid)) if row is not None]
        anchor = _anchor(record, fields)
        if anchor not in found:
            # Una sola fila, encontrada por el identificador que la verificación asocia pero no avala (el PMID de un
            # DOI, el DOI que PubMed declara para un PMID): si es una fila verificada o se llama por ese
            # identificador, debe ser el mismo trabajo. Si no lo es, no se toca: el registro verificado entra aparte,
            # sin ese identificador.
            found_by = found[0]
            if existing["verified"] or _primary_field(existing) == found_by:
                problems = _row_mismatch(existing, found_by, fields, record)
                if problems:
                    not_linked = (found_by, fields[found_by], existing["id"], problems)
                    fields = dict(fields, **{found_by: None})
                    existing = None
        elif existing["verified"]:
            # La fila tiene el identificador que avala esta verificación, pero ya estaba verificada como otro trabajo:
            # su identificador pudo venir asociado por otra fuente (el DOI que PubMed declara para un PMID puede ser
            # el de otro trabajo). Ese identificador no puede quedar en dos filas ni reescribirse la fila de otro
            # trabajo (con sus afirmaciones y extracciones): error sin cambiar nada, para revisión humana. Solo se
            # compara el título (un año distinto en dos bases bibliográficas no basta para decir que es otro trabajo).
            problems = _row_mismatch(existing, anchor, fields, record, years=False)
            if problems:
                label = {"doi": "DOI", "pmid": "PMID"}[anchor]
                raise RegistryError(
                    f"Conflicto: {existing['id']} ya está verificada (vía {existing['verified_via'] or 'sin indicar'}) "
                    f"como otro trabajo: {'; '.join(problems)}. Esta verificación ({record.get('verified_via')}) da "
                    f"para el {label} {fields[anchor]} «{str(fields.get('title') or '')[:90]}» ({fields.get('year')}). "
                    f"No se ha cambiado nada: revisa a qué trabajo corresponde el {label} {fields[anchor]} (PubMed "
                    "puede declarar para un PMID el DOI de otro trabajo).")
    if existing is None and fields["openalex_id"]:
        # Solo una fila sin DOI ni PMID se reconoce por el identificador de OpenAlex (OpenAlex puede unir en una
        # obra dos DOI distintos, p. ej. preprint y artículo).
        existing = conn.execute(
            "SELECT * FROM sources WHERE id = ? OR (openalex_id = ? AND doi IS NULL AND pmid IS NULL) LIMIT 1",
            (f"openalex:{fields['openalex_id']}", fields["openalex_id"])).fetchone()
    sid = None
    if existing is not None:
        if not verified:
            return _apply_unverified(conn, existing, fields, record, changes)
        sid = _apply_verified(conn, existing, fields, record, actor, changes)
    if sid is None:
        sid = _insert_source(conn, fields, record, verified, actor, changes)
    if not_linked is not None:
        key, value, other, problems = not_linked
        label = {"doi": "DOI", "pmid": "PMID"}[key]
        motive = (f"La verificación ({record.get('verified_via') or 'sin indicar'}) asocia el {label} {value} a este "
                  f"trabajo, pero {other}, la fuente registrada con ese {label}, es otro trabajo: " + "; ".join(problems)
                  + f". No se ha cambiado {other}; revisa a qué trabajo corresponde ese {label}.")
        _log_decision(conn, sid, f"{label} {value} no asociado (es de {other})", motive, actor)
        changes.append({"id": sid, "accion": "identificador_no_asociado", "campos": [key], "fuente": other,
                        "motivo": motive})
    return sid


def get_source(conn: sqlite3.Connection, source_id: str) -> dict | None:
    return row_to_dict(conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone())


def list_sources(conn: sqlite3.Connection, unverified_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM sources"
    if unverified_only:
        sql += " WHERE verified = 0"
    return [row_to_dict(r) for r in conn.execute(sql + " ORDER BY added_at")]


def _require_usable_source(conn: sqlite3.Connection, source_id: str, purpose: str) -> dict:
    src = get_source(conn, source_id)
    if src is None:
        raise RegistryError(f"La fuente {source_id} no está registrada")
    if not src.get("verified"):
        raise RegistryError(f"La fuente {source_id} no está verificada: ejecuta `nutria verificar --pendientes` "
                            f"o `nutria fuente agregar` antes de usarla para {purpose}")
    if src.get("retracted") == 1:
        raise RegistryError(f"La fuente {source_id} está RETRACTADA: no puede usarse para {purpose}")
    if src.get("retracted") is None:
        raise RegistryError(f"No se ha podido comprobar si {source_id} está retractada: vuelve a verificarla "
                            f"(`nutria verificar --pendientes`) antes de usarla para {purpose}")
    return src


# ---------------------------------------------------------------------------- verificaciones manuales
def add_manual_verification(conn: sqlite3.Connection, url: str, signed_by: str, note: str | None = None) -> None:
    if not signed_by or not signed_by.strip():
        raise RegistryError("La verificación manual exige el nombre de la persona que la hizo")
    require_human_signature(signed_by)
    with transaction(conn):
        conn.execute("INSERT INTO manual_verifications (url, signed_by, signed_at, note) VALUES (?,?,?,?) "
                     "ON CONFLICT(url) DO UPDATE SET signed_by=excluded.signed_by, signed_at=excluded.signed_at, "
                     "note=excluded.note", (url.strip(), signed_by.strip(), utc_now(), note))
        _log_decision(conn, None, f"verificación manual de {url.strip()}", note or "Comprobada por una persona",
                      None, signed_by.strip())


def get_manual_verification(conn: sqlite3.Connection, url: str) -> dict | None:
    row = conn.execute("SELECT * FROM manual_verifications WHERE url = ?", (url.strip(),)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------- extracciones
EXTRACTION_FIELDS = {
    "fuente": "source_id", "experimento": "experiment", "especie": "species", "raza": "breed",
    "sexo": "sex", "edad": "age", "estado_fisiologico": "physiological_stage", "paridad": "parity",
    "diseno": "design", "unidad_experimental": "experimental_unit", "duracion_dias": "duration_days",
    "dieta_basal": "basal_diet", "intervencion": "intervention", "dosis": "dose_value",
    "dosis_unidad": "dose_unit", "dosis_base": "dose_basis", "forma_producto": "product_form",
    "comparador": "comparator", "variable": "outcome", "unidad": "outcome_unit",
    "n_control": "n_ctrl", "n_tratamiento": "n_trt", "media_control": "mean_ctrl",
    "media_tratamiento": "mean_trt", "tipo_dispersion": "dispersion_type",
    "dispersion_control": "disp_ctrl", "dispersion_tratamiento": "disp_trt", "p_valor": "p_value",
    "eventos_control": "ev_ctrl", "eventos_tratamiento": "ev_trt",
    "forraje_pct_ms": "forage_pct_dm", "fnd_pct_ms": "ndf_pct_dm", "almidon_pct_ms": "starch_pct_dm",
    "grasa_pct_ms": "fat_pct_dm", "pb_pct_ms": "cp_pct_dm", "dcad_meq_kg_ms": "dcad_meq_kg_dm",
    "pdr_pct_pb": "rdp_pct_cp", "pdr_pct_ms": "rdp_pct_dm", "hito_referencia": "time_reference",
    "ventana_inicio_dias": "window_start_days", "ventana_fin_dias": "window_end_days",
    "definicion_caso": "case_definition", "suplementacion_inicio_dias": "supp_start_days",
    "suplementacion_fin_dias": "supp_end_days",
    "ubicacion": "location", "financiacion": "funding", "conflictos": "conflicts",
    "estado_verificacion": "check_status", "verificado_por": "checked_by", "notas": "notes",
}
# Campos que solo fija el sistema (doble extracción comparada o conciliada); se ignoran al comparar copias.
EXTRACTION_SYSTEM_FIELDS = ("estado_verificacion", "verificado_por")
COMPARE_IGNORED = frozenset({"notas", *EXTRACTION_SYSTEM_FIELDS})
DOUBLE_CHECKED = ("doble_concordante", "resuelta")


@lru_cache(maxsize=None)
def load_schema(kind: str) -> dict:
    """Esquema JSON del paquete (`src/nutria/data/schemas/<kind>.schema.json`)."""
    return json.loads(resources.files("nutria.data").joinpath(f"schemas/{kind}.schema.json").read_text("utf-8"))


# ---------------------------------------------------------------------------- textos informados y específicos
# Los esquemas marcan con la palabra clave "x-texto" los campos de texto que deben decir algo ("informado"), además
# nombrar una especie, una etapa o un estado concretos ("especifico") o, en los campos de especie, nombrar la especie
# ("especie"). La comprueba `text_problem` sobre palabras normalizadas (sin mayúsculas, tildes ni signos), en tiempo
# lineal: nada de expresiones regulares con retroceso. La aplican la CLI y el registro (`schema_errors`); un validador
# JSON Schema genérico ignora "x-texto" y solo comprueba el tipo, la longitud y que haya letras o cifras.
#
# Vocabulario en plantillas: una por línea; las palabras de una línea se suceden; 'a|b' son alternativas, '?a' es
# opcional y 'pal{o,a,os,as}' da las variantes de la palabra. Todo en minúsculas y sin tildes.
_FILLER_TEMPLATES = """
x|xx|xxx|xyz|abc|asdf|foo|bar|test|prueba|placeholder|0|00|000|blank|tbd|tbc|tba
ejemplo{,s}|example{,s}|sample|dummy|demo|lorem|ipsum|tmp|temp
lorem ipsum
n a|d|c|p|r|s|k
na|nd|nc|np|nr|ns|nk|sd|sn
ns nc
s d|n
null|none|nil|nan|undefined|unknown|unk|missing|pending|ditto|idem|id|unspecified|unreported|unavailable
pendiente{,s}|desconocid{o,a,os,as}|ignorad{o,a,os,as}|nada|vaci{o,a,os,as}|ningun{,o,a,os,as}
no consta{,n}
no se sabe|conoce|indica|informa|especifica|describe|reporta|publica|detalla|menciona|precisa|aplica
no indicad{o,a,os,as}|informad{o,a,os,as}|reportad{o,a,os,as}|publicad{o,a,os,as}|descrit{o,a,os,as}|especificad{o,a,os,as}|definid{o,a,os,as}|determinad{o,a,os,as}|conocid{o,a,os,as}|declarad{o,a,os,as}|detallad{o,a,os,as}|registrad{o,a,os,as}|mencionad{o,a,os,as}|facilitad{o,a,os,as}|proporcionad{o,a,os,as}|precisad{o,a,os,as}|identificad{o,a,os,as}|evaluad{o,a,os,as}|medid{o,a,os,as}|disponible{,s}|aplicable{,s}
no aplica|procede|corresponde|relevante|disponible
sin dato{,s}|informacion|especificar|definir|determinar|indicar|informar|precisar|detallar|concretar|identificar|describir|clasificar|valor{,es}
a|por definir|determinar|concretar|especificar|confirmar|precisar|completar|rellenar
ver arriba|abajo|texto|articulo|fuente|nota{,s}
not reported|stated|given|available|applicable|specified|described|known|determined|defined|recorded|measured
to be determined|defined|confirmed
"""
# Palabras que, solas o juntas, no identifican una especie, una etapa ni un estado (español e inglés).
_GENERIC_TEMPLATES = """
tod{o,a,os,as,es,xs}|vari{os,as,es,xs}|divers{o,a,os,as,es,xs}|distint{o,a,os,as,es,xs}|diferente{,s}|multiple{,s}
cualquier{,a}|cualesquiera|ambos|ambas|otr{o,a,os,as,es,xs}|demas|resto|general{,es,mente}|generic{o,a,os,as}
mixt{o,a,os,as}|indistint{o,a,os,as}
indistintamente|indiferente{,s}|global{,es}|comun{,es}|habitual{,es}|tipic{o,a,os,as}|estandar|normal{,es}
complet{o,a,os,as}|enter{o,a,os,as}|total{,es}|multiespecie{,s}|pluriespecie{,s}|polivalente{,s}|particular{,es}
cada|ciert{o,a,os,as}|algun{,o,a,os,as}|much{o,a,os,as}|numeros{o,a,os,as}|principal{,es}|relevante{,s}|posible{,s}
aplicable{,s}|diana{,s}|objetivo{,s}|interes|indeterminad{o,a,os,as}|indefinid{o,a,os,as}|inespecific{o,a,os,as}
seleccionad{o,a,os,as}|etc|universal{,es,mente}|transversal{,es}|variable{,s}|independiente{,s,mente}|multi
amplio|amplia|amplios|amplias|rango{,s}|espectro|sin|restriccion{,es}|distincion{,es}|limitacion{,es}|fisiologic{o,a,os,as}
especie{,s}|etapa{,s}|fase{,s}|estado{,s}|estadio{,s}|raza{,s}|animal{,es}|ganado{,s}|ganaderia{,s}|edad{,es}
categoria{,s}|tipo{,s}|sexo{,s}|clase{,s}|grupo{,s}|individuo{,s}|ejemplar{,es}|cabeza{,s}|poblacion{,es}|momento{,s}
periodo{,s}|ciclo{,s}|condicion{,es}|situacion{,es}|caso{,s}|explotacion{,es}|sistema{,s}|organismo{,s}|ser{,es}|vivo{,s}
mamifero{,s}|vertebrado{,s}|invertebrado{,s}|vida|ano{,s}|sp|spp|ssp|animalia|mammalia
ganader{o,a,os,as}|domestic{o,a,os,as}|pecuari{o,a,os,as}|zootecnic{o,a,os,as}|productiv{o,a,os,as}
el|la|los|las|lo|un|una|unos|unas|de|del|y|e|o|u|ni|en|a|al|con|para|por|su|sus|segun
all|any|every|each|various|several|many|mixed|different|other{,s}|generic|common|typical|standard|whole|entire|complete
species|stage{,s}|phase{,s}|animal{,s}|livestock|farm{,s}|production|productive|producing|domestic|age{,s}|category
categories|type{,s}|target|of|the|and|or|in|for|an|to|with|overall|broad|range|spectrum|unspecific|nonspecific|non
specific|universal|mammal{,s}|vertebrate{,s}|organism{,s}|life|lifetime|year{,s}|cycle{,s}|period{,s}|state{,s}
condition{,s}|group{,s}|class{,es}|sex{,es}|breed{,s}|kind{,s}|individual{,s}|population{,s}|system{,s}|case{,s}
depende{,n}|dependiendo|dependera{,n}|corresponda{,n}|proceda{,n}|convenga{,n}|siempre|cuando|que|cual{,es}|sea{,n}
necesari{o,a,os,as}|depend{,s,ing}|always|whichever|whatever|appropriate|needed|required|when|as|if|apply|applies
"""
# Combinaciones genéricas de palabras que, solas, pueden ser concretas ('fase de producción' en ponedoras, 'cría').
_GENERIC_PHRASE_TEMPLATES = """
especie{,s}|animal{,es}|ganado{,s}|fases|etapas|estados de|en produccion|granja|abasto|renta|cria
cualquier|toda|todas|varias|distintas|diferentes ?la|las fase{,s}|etapa{,s} de|del ?la produccion|ciclo|crecimiento|cria|vida
produccion animal
food producing
foodproducing
farm animal{,s}
productor{,es,a,as} de alimento{,s}
destinad{o,a,os,as} a la produccion de alimento{,s}
para ?el consumo humano
"""
# Grupos de especies ('rumiantes', 'aves', 'monogástricos'): solos nombran un grupo concreto, pero con palabras
# genéricas ('rumiantes en general', 'todos los rumiantes', 'rumiantes y monogástricos') no nombran la especie.
_GROUP_TEMPLATES = """
rumiante{,s}|monogastric{o,a,os,as}|poligastric{o,a,os,as}|herbivor{o,a,os,as}|omnivor{o,a,os,as}|carnivor{o,a,os,as}
ave{,s}|avicola{,s}|pez|peces|ungulad{o,os}|bovid{o,os}|camelid{o,os}|equid{o,os}|suid{o,os}
ruminant{,s}|monogastric{,s}|herbivore{,s}|omnivore{,s}|carnivore{,s}|poultry|bird{,s}|fish|fishes|ungulate{,s}
bovid{,s}|camelid{,s}|equid{,s}
"""
# Solo en los campos de especie: palabras que describen a los animales sin nombrar la especie ('animales adultos').
_DESCRIPTOR_TEMPLATES = """
adult{o,a,os,as}|joven{,es}|san{o,a,os,as}|enferm{o,a,os,as}|mach{o,os}|hembra{,s}|reproductor{,es,a,as}|cri{a,as}
recien|nacid{o,a,os,as}|gestante{,s}|lactante{,s}|experimental{,es}|laboratorio|modelo{,s}|sujeto{,s}|cohorte{,s}
castrad{o,a,os,as}|esterilizad{o,a,os,as}|destetad{o,a,os,as}|lechal{,es}|engorde|cebo|recria
adult{,s}|young|healthy|sick|male{,s}|female{,s}|breeding|newborn{,s}|laboratory|lab|model{,s}|subject{,s}
"""
# Caracteres que no se ven y no tienen categoría de formato o de control (rellenos del hangul, braille en blanco...).
_INVISIBLE_CHARS = frozenset("͏ᅟᅠ឴឵᠎⠀ㅤﾠ")
_HIDDEN_CATEGORIES = frozenset({"Cf", "Cc", "Co", "Cs", "Cn"})
_WORD_RE = re.compile(r"[^\W_]+")
# Grafías con cifras o signos en lugar de letras ('t0das', 'tod@s', 'tod*s'): solo para reconocer valores genéricos.
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "*": "o"})
_LEET_TOKEN_RE = re.compile(r"[\w@$*]+")


def _word_variants(word: str) -> list[str]:
    if "{" not in word:
        return [word]
    head, rest = word.split("{", 1)
    options, tail = rest.split("}", 1)
    return [head + option + end for option in options.split(",") for end in _word_variants(tail)]


def _expand(templates: str) -> frozenset[tuple[str, ...]]:
    out: set[tuple[str, ...]] = set()
    for line in templates.splitlines():
        sequences: list[tuple[str, ...]] = [()]
        for slot in line.split():
            words = [w for alternative in slot.lstrip("?").split("|") for w in _word_variants(alternative)]
            grown = [seq + (w,) for seq in sequences for w in words]
            sequences = grown + sequences if slot.startswith("?") else grown
        out.update(seq for seq in sequences if seq)
    return frozenset(out)


@lru_cache(maxsize=None)
def _vocabulary() -> dict[str, Any]:
    fillers = _expand(_FILLER_TEMPLATES)
    generic = fillers | _expand(_GENERIC_TEMPLATES) | _expand(_GENERIC_PHRASE_TEMPLATES)
    descriptors = generic | _expand(_DESCRIPTOR_TEMPLATES)
    groups = _expand(_GROUP_TEMPLATES)
    generic_compact = frozenset(c for c in ("".join(t) for t in generic) if len(c) >= 3)
    return {"filler": fillers, "generic": generic, "descriptor": descriptors, "group": groups,
            "generic_group": generic | groups,
            "filler_compact": frozenset("".join(t) for t in fillers), "generic_compact": generic_compact,
            "max_len": max(len(t) for t in descriptors | groups), "compact_max": max(map(len, generic_compact))}


def _fold(text: str) -> str:
    """Forma de comparación: NFKC (anchos completos, ligaduras...), sin tildes ni marcas y en minúsculas."""
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) not in ("Mn", "Me")).casefold()


def text_words(value: Any) -> list[str]:
    """Palabras de un texto en minúsculas, sin tildes ni signos ('Vacuno-Lechero' -> ['vacuno', 'lechero'])."""
    return _WORD_RE.findall(_fold(str(value))) if value is not None else []


def _covered(words: list[str], entries: frozenset[tuple[str, ...]], max_len: int) -> bool:
    """¿Se puede partir la secuencia de palabras en entradas del vocabulario? (programación dinámica, lineal)."""
    reach = [True] + [False] * len(words)
    for i in range(len(words)):
        if reach[i]:
            for size in range(1, min(max_len, len(words) - i) + 1):
                if tuple(words[i:i + size]) in entries:
                    reach[i + size] = True
    return reach[-1]


def _compact_covered(compact: str, entries: frozenset[str], max_len: int) -> bool:
    """Lo mismo con el texto sin separadores ('a n i m a l e s', 'todaslasetapas'), con piezas de 3 letras o más."""
    reach = [True] + [False] * len(compact)
    for i in range(len(compact)):
        if reach[i]:
            for size in range(3, min(max_len, len(compact) - i) + 1):
                if compact[i:i + size] in entries:
                    reach[i + size] = True
    return reach[-1]


def _scripts(word: str) -> set[str]:
    """Alfabetos de las letras de una palabra (la micra 'μ' y las letras modificadoras no cuentan)."""
    out = set()
    for ch in word:
        if ch.isalpha() and ch != "μ":
            script = unicodedata.name(ch, "?").split(" ")[0]
            if script != "MODIFIER":
                out.add(script)
    return out


def _unleet_words(text: str) -> list[str]:
    """Palabras del texto leyendo como letras las cifras y signos de las palabras que los mezclan con letras."""
    def fix(match: re.Match) -> str:
        token = match.group(0)
        if any(ch.isalpha() for ch in token) and any(ch.isdigit() or ch in "@$*" for ch in token):
            return token.translate(_LEET)
        return token
    return _WORD_RE.findall(_LEET_TOKEN_RE.sub(fix, _fold(text)))


def _is_filler(words: list[str], vocab: dict) -> bool:
    if _covered(words, vocab["filler"], vocab["max_len"]):
        return True
    compact = "".join(words)
    period = compact[:(compact + compact).find(compact, 1)]  # 'nana' -> 'na'
    return period in vocab["filler_compact"] or (len(compact) > 1 and len(set(compact)) == 1 and compact.isalpha())


def text_problem(value: Any, mode: str = "informado") -> str | None:
    """Problema de un texto para la palabra clave "x-texto" de los esquemas, o None si vale.

    - informado: no vacío, no solo signos (salvo '%' o '‰'), sin caracteres invisibles ni alfabetos mezclados en una
      palabra y sin valores de relleno ('n/a', 'xxx', 'desconocido', 'no consta', 'not reported'...);
    - especifico: además, no formado solo por palabras genéricas ('todas las etapas', 'varias especies', 'animales de
      producción', 'all species'...), tampoco escritas sin espacios o letra a letra;
    - especie: además, no formado solo por palabras genéricas y descripciones de los animales ('animales adultos').
    """
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFKC", value)
    hidden = sorted({f"U+{ord(ch):04X}" for ch in text if ch in _INVISIBLE_CHARS
                     or (unicodedata.category(ch) in _HIDDEN_CATEGORIES and ch not in "\t\n\r")})
    if hidden:
        return f"contiene caracteres invisibles o de control ({', '.join(hidden[:5])}): escríbelo sin ellos"
    words = text_words(text)
    if not words:
        if mode == "informado" and any(ch in "%‰‱" for ch in text):
            return None
        return "está vacío o solo tiene signos"
    mixed = [w for w in words if len(_scripts(w)) > 1]
    if mixed:
        return f"mezcla alfabetos distintos en una palabra ({mixed[0]!r}): escríbela con un solo alfabeto"
    vocab = _vocabulary()
    if _is_filler(words, vocab):
        return "es un valor de relleno, no un dato"
    if mode in ("especifico", "especie"):
        readings = [words]
        unleet = _unleet_words(text)
        if unleet and unleet != words:
            readings.append(unleet)
        for ws in readings:
            if (_covered(ws, vocab["generic"], vocab["max_len"])
                    or _compact_covered("".join(ws), vocab["generic_compact"], vocab["compact_max"])):
                return "es genérico: no nombra una especie, una etapa ni un estado concretos"
        if mode == "especie" and any(_covered(ws, vocab["descriptor"], vocab["max_len"]) for ws in readings):
            return "no nombra la especie: solo describe a los animales"
        if mode == "especie" and any(_covered(ws, vocab["generic_group"], vocab["max_len"])
                                     and not _covered(ws, vocab["group"], vocab["max_len"]) for ws in readings):
            return ("es genérico: un grupo de especies ('rumiantes', 'aves', 'monogástricos') con palabras genéricas "
                    "no nombra la especie; indica la especie (p. ej. 'vacuno lechero') o, si se dirige a todo el "
                    "grupo, solo el grupo")
    return None


@lru_cache(maxsize=None)
def _validator_class():
    import jsonschema
    from jsonschema.exceptions import ValidationError

    def x_texto(validator, mode, instance, schema):
        if validator.is_type(instance, "string"):
            problem = text_problem(instance, mode)
            if problem:
                yield ValidationError(problem)

    return jsonschema.validators.extend(jsonschema.Draft202012Validator, {"x-texto": x_texto})


@lru_cache(maxsize=None)
def _schema_validator(kind: str):
    return _validator_class()(load_schema(kind))


def _error_tree(err) -> list:
    out = [err]
    for child in sorted(err.context or [], key=lambda c: len(c.path)):
        out.extend(_error_tree(child))
    return out


def schema_error_text(err) -> str:
    """Mensaje legible de un error de jsonschema: el problema concreto de un texto ("x-texto") o el 'x-mensaje' del
    subesquema (o de una de sus alternativas) si lo hay."""
    tree = _error_tree(err)
    for e in tree:
        if e.validator == "x-texto":
            hint = e.schema.get("x-mensaje") if isinstance(e.schema, dict) else None
            return f"{e.instance!r} {e.message}" + (f" ({hint})" if hint else "")
    for k, e in enumerate(tree):
        msg = e.schema.get("x-mensaje") if isinstance(e.schema, dict) else None
        if msg:
            if isinstance(e.instance, (dict, list)):
                if err.validator == "required":
                    return f"{msg} ({err.message})"
                # una alternativa (anyOf) puede concretar qué falta: 'falta la etapa o el estado fisiológico'
                detail = next((d.schema.get("x-mensaje") for d in tree[k + 1:]
                               if isinstance(d.schema, dict) and d.schema.get("x-mensaje")), None)
                return f"{msg}: {detail}" if detail and detail != msg else msg
            return f"{e.instance!r}: {msg}"
    return err.message


def schema_errors(kind: str, data: Any, schema: dict | None = None) -> list[str]:
    """Errores de validación de `data` contra el esquema `kind` (o contra `schema`), legibles y ordenados. Aplica
    también la palabra clave "x-texto" (textos informados y específicos)."""
    validator = _validator_class()(schema) if schema is not None else _schema_validator(kind)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    # En un mismo campo, el problema concreto del texto sustituye a los errores genéricos de longitud o de patrón.
    texts = {tuple(e.path) for e in errors if e.validator == "x-texto"}
    out: list[str] = []
    for e in errors:
        if e.validator in ("minLength", "pattern") and tuple(e.path) in texts:
            continue
        message = f"{'/'.join(map(str, e.path)) or '(raíz)'}: {schema_error_text(e)}"
        if message not in out:
            out.append(message)
    return out


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def plain_text(value: Any) -> str:
    """Minúsculas, sin tildes y con espacios simples: para reconocer y comparar textos libres."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold()
    return " ".join(text.split())


# Variables de incidencia o de trastornos y unidades de proporción o de recuento: una incidencia expresada como
# porcentaje o como media también necesita su definición de caso y su ventana (D4-7).
INCIDENCE_OUTCOME_RE = re.compile(
    r"\b(?:incidencia|prevalencia|riesgo|casos?|cetosis|hipercetonemia|hipocalcemia|fiebre (?:de la leche|vitularia)"
    r"|paresia|mastitis|metritis|endometritis|retencion (?:de (?:la )?placenta|placentaria)|placenta retenida"
    r"|desplazamiento (?:de(?:l)? )?abomaso|abomaso desplazado|acidosis|cojeras?|laminitis|lipidosis|higado graso"
    r"|morbilidad|mortalidad|muertes?|supervivencia|abortos?|distocias?|diarreas?|enfermedad(?:es)?|trastornos?"
    r"|perdidas? (?:de (?:la )?)?gestacion|concepcion|prenez|fertilidad|descartes?|bajas|eliminacion(?:es)?"
    r"|tasa de (?:gestacion|partos?|reposicion|curacion|recidivas?|eliminacion)"
    # los mismos términos en inglés (variables copiadas del artículo)
    r"|incidence|prevalence|risk|ketosis|hyperketon(?:a)?emia|hypocalc(?:a)?emia|milk fever|parturient paresis"
    r"|retained (?:placenta|fetal membranes)|displaced abomasum|lameness|fatty liver|hepatic lipidosis|morbidity"
    r"|mortality|deaths?|survival|abortions?|dystocias?|diarrh(?:o)?ea|diseases?|disorders?|pregnancy (?:loss|rate)"
    r"|pregnancies per ai|pregnancy per ai|conception|fertility|culling|culled|removals?|cure rate"
    # reproducción, otros trastornos y abreviaturas habituales en los artículos (SCK: cetosis subclínica; SCH:
    # hipocalcemia subclínica; SCM: mastitis subclínica; RFM: retención de membranas fetales; LDA: abomaso desplazado
    # a la izquierda; P/IA: gestaciones por inseminación)
    r"|celos?|estros?|anestros?|ovulacion|quistes? ovaricos?|gestantes|prenadas|neumonias?|timpanismo|meteorismo"
    r"|cetonuria|estrus|anestrus|ovulation|ovarian cysts?|pregnant|pneumonia|bloat|ketonuria"
    r"|sck|sch|scm|rfm|lda|p ?/ ?(?:ia|ai)"
    # animales con el trastorno o el estado ('vacas cetósicas', 'vacas vacías', 'terneros enfermos')
    r"|cetosic[oa]s?|hipocalcemic[oa]s?|hipercetonemic[oa]s?|mastitic[oa]s?|coj[oa]s|enferm[oa]s|muert[oa]s"
    r"|abortad[oa]s|prenad[oa]s|vaci[oa]s|descartad[oa]s|eliminad[oa]s|ketotic|hypocalc(?:a)?emic|lame|sick|dead"
    r"|open cows)\b")
# Proporción de animales (o unidades) con algo, por la unidad o por el nombre: '% de vacas', 'proporción de animales',
# 'casos/100 vacas', 'vacas con BHB alto', 'cows with'. Es una incidencia aunque la variable no nombre un trastorno.
_ANIMAL_WORDS = (r"vacas?|novillas?|terneros?|terneras?|becerr[oa]s?|cerdas?|lechones|cerdos?|ovejas?|cabras?"
                 r"|corderos?|cabritos?|aves|gallinas?|pollos?|animales|hembras|machos|individuos|cows?|heifers?|calves"
                 r"|calf|sows?|piglets?|pigs?|ewes?|goats?|lambs?|birds|hens?|broilers?|animals|females|males"
                 r"|individuals")
ANIMAL_PROPORTION_RE = re.compile(
    r"(?:%|‰|\bporcentaje|\bproporcion|\bfraccion|\bpercent(?:age)?|\bproportion|\bfraction)\s*"
    r"(?:(?:de|del|of)\s+)?(?:(?:las|los|la|el|the)\s+)?(?:" + _ANIMAL_WORDS + r")\b"
    r"|\b(?:casos?|eventos?|cases?|events?)\s*(?:/|por|per)\s*(?:100|1\.?000|cada|each)?\s*(?:" + _ANIMAL_WORDS
    + r")\b|\b(?:" + _ANIMAL_WORDS + r")\s+(?:con|que|with|that|having)\b")
# Umbral en el nombre de la variable ('BHB ≥ 1,2 mmol/L', 'RCS > 200 000 células/mL'): con una unidad de proporción,
# es la proporción de animales por encima o por debajo del umbral.
THRESHOLD_RE = re.compile(r"[<>≤≥⩽⩾]\s*=?\s*\d|=\s*[<>]\s*\d"
                          r"|\b(?:mayor|menor|superior|inferior)(?:es)?\s+(?:o igual\s+)?(?:que|a|de)\s+\d"
                          r"|\bpor (?:encima|debajo) de\s+\d|\b(?:above|below|over|under|at least|greater than"
                          r"|less than|al menos)\s+\d")
# Un umbral sobre el tiempo ('tiempo con pH < 5,8') no es una proporción de animales.
TIME_OUTCOME_RE = re.compile(r"\b(?:tiempo|time|duracion|duration|horas?|hours?|minutos?|minutes?)\b")
# La variable empieza por los animales ('vacas gestantes a 150 DEL', '% vacas con...'): proporción de animales.
ANIMAL_START_RE = re.compile(r"^\W*(?:(?:%|porcentaje|proporcion|fraccion|numero|percentage|proportion|number)\s*"
                             r"(?:(?:de|of)\s+)?)?(?:(?:las|los|the)\s+)?(?:" + _ANIMAL_WORDS + r")\b")
# Un nivel cualitativo de un marcador ('BHB alto', 'AGNE elevados', 'high BHB'), al final en español o al principio en
# inglés, con una unidad que solo dice proporción ('%'): proporción de animales por encima o por debajo de un umbral
# sin cifra.
LEVEL_OUTCOME_RE = re.compile(r"\b(?:alt[oa]s?|elevad[oa]s?|baj[oa]s?|high|low|elevated)\W*$"
                              r"|^\W*(?:high|low|elevated)\b")
BARE_PROPORTION_UNITS = frozenset({"%", "porcentaje", "proporcion", "fraccion", "tasa", "percent", "percentage",
                                   "proportion", "fraction", "rate"})
INCIDENCE_UNIT_RE = re.compile(
    r"%|\b(?:porcentaje|proporcion|fraccion|casos?|animales|vacas|novillas|terneros|cerdas|lechones|aves|incidencia"
    r"|prevalencia|riesgo|tasa|n|numero|frecuencia|eventos?|percent(?:age)?|proportion|cases?|animals|cows|heifers"
    r"|calves|sows|piglets|birds|rate|events?)\b|/ ?100\b|por (?:100|cada)\b|per (?:100|cent)\b")
PER_TIME_UNIT_RE = re.compile(r"/ ?(?:h|hora|d|dia|day|min)\b|por (?:hora|dia|minuto)\b|per (?:hour|day|minute)\b")
# Porcentajes de composición o de tiempo ('% del peso fresco', '% de la MS', '% del tiempo'): no son proporciones de
# animales con un evento.
COMPOSITION_UNIT_RE = re.compile(r"\b(?:ms|mo|pb|pv|peso|materia|ingesta|dieta|racion|energia|grasa|proteina|leche"
                                 r"|higado|tejido|fresco|seco|plasma|sangre|suero|tiempo|duracion|horas?|minutos?"
                                 r"|dm|om|cp|bw|weight|matter|intake|diet|ration|energy|fat|protein|milk|liver|tissue"
                                 r"|fresh|dry|blood|serum|time|duration|hours?|minutes?|ag|fa|acidos? grasos?"
                                 r"|fatty acids?)\b")


def incidence_like(outcome: Any, unit: Any) -> bool:
    """Variable de incidencia o de un trastorno expresada como proporción de animales o recuento (no como velocidad
    por tiempo ni como porcentaje de composición): por la unidad ('% de vacas', 'casos/100 vacas'), por el nombre de
    un trastorno ('cetosis', 'SCK', 'retained placenta'), por los animales que la tienen ('vacas con BHB alto') o por
    un umbral en el nombre ('BHB ≥ 1,2 mmol/L') con una unidad de proporción."""
    if ANIMAL_PROPORTION_RE.search(_fold(str(unit or ""))):
        return True
    unit_text = plain_text(unit)
    if (not INCIDENCE_UNIT_RE.search(unit_text) or PER_TIME_UNIT_RE.search(unit_text)
            or COMPOSITION_UNIT_RE.search(unit_text)):
        return False
    outcome_text, outcome_fold = plain_text(outcome), _fold(str(outcome or ""))
    return bool(INCIDENCE_OUTCOME_RE.search(outcome_text) or ANIMAL_PROPORTION_RE.search(outcome_fold)
                or ANIMAL_START_RE.search(outcome_fold)
                or ((THRESHOLD_RE.search(outcome_fold)
                     or (LEVEL_OUTCOME_RE.search(outcome_text) and unit_text.strip(" ()") in BARE_PROPORTION_UNITS))
                    and not TIME_OUTCOME_RE.search(outcome_text)))


def is_incidence(get) -> bool:
    """¿Es un registro de incidencia? `get(campo)` devuelve el valor de un campo del esquema de extracción."""
    return (_is_number(get("eventos_control")) or _is_number(get("eventos_tratamiento"))
            or bool(get("definicion_caso")) or incidence_like(get("variable"), get("unidad")))


def extraction_problems(data: dict) -> list[str]:
    """Reglas de una extracción que el esquema JSON no puede expresar."""
    if not isinstance(data, dict):
        return ["cada extracción debe ser un objeto JSON"]
    problems = []
    required = load_schema("extraccion")["required"]
    declared = data.get("no_consta") if isinstance(data.get("no_consta"), list) else []
    blank = [k for k in required if isinstance(data.get(k), str) and not data[k].strip()]
    if blank:
        problems.append("campos obligatorios vacíos: " + ", ".join(blank))
    undeclared = [k for k in required if data.get(k) is None and k not in declared]
    if undeclared:
        problems.append("campos obligatorios a null sin declararlos en 'no_consta': " + ", ".join(undeclared))
    contradicted = sorted(k for k in declared if isinstance(k, str) and data.get(k) is not None)
    if contradicted:
        problems.append("campos declarados en 'no_consta' que sí tienen valor: " + ", ".join(contradicted))
    for start, end in (("ventana_inicio_dias", "ventana_fin_dias"),
                       ("suplementacion_inicio_dias", "suplementacion_fin_dias")):
        a, b = data.get(start), data.get(end)
        if _is_number(a) and _is_number(b) and a > b:
            problems.append(f"{start} ({a}) es posterior a {end} ({b})")
    rdp, cp = data.get("pdr_pct_ms"), data.get("pb_pct_ms")
    if _is_number(rdp) and _is_number(cp) and rdp > cp:
        problems.append(f"pdr_pct_ms ({rdp}) supera pb_pct_ms ({cp}): la PDR es una fracción de la PB")
    rdp_cp = data.get("pdr_pct_pb")
    if _is_number(rdp) and _is_number(cp) and _is_number(rdp_cp):
        # las dos bases de la PDR deben cuadrar: PDR (% MS) = PDR (% PB) × PB (% MS) / 100
        expected = rdp_cp * cp / 100
        if abs(expected - rdp) > max(1.0, 0.1 * expected):
            problems.append(f"pdr_pct_pb ({rdp_cp} % de la PB) y pdr_pct_ms ({rdp} % de la MS) no cuadran con "
                            f"pb_pct_ms ({cp} % de la MS): {rdp_cp} × {cp} / 100 = {expected:.1f} % de la MS. "
                            "Comprueba la base de cada valor (¿una cifra en % de la PB anotada como % de la MS?)")
    for events, n in (("eventos_control", "n_control"), ("eventos_tratamiento", "n_tratamiento")):
        if _is_number(data.get(events)) and _is_number(data.get(n)) and data[events] > data[n]:
            problems.append(f"{events} ({data[events]}) supera {n} ({data[n]}): los eventos son animales (o unidades) "
                            "con el evento dentro del grupo")
    case, outcome = data.get("definicion_caso"), data.get("variable")
    if isinstance(case, str) and isinstance(outcome, str) and plain_text(case) == plain_text(outcome):
        problems.append("definicion_caso repite el nombre de la variable: indica el criterio y el umbral del artículo "
                        "(p. ej. 'BHB en sangre ≥ 1,2 mmol/L')")
    has_events = _is_number(data.get("eventos_control")) or _is_number(data.get("eventos_tratamiento"))
    if not has_events and incidence_like(outcome, data.get("unidad")):
        missing = [k for k in ("definicion_caso", "hito_referencia", "ventana_inicio_dias", "ventana_fin_dias")
                   if data.get(k) is None]
        if missing:
            problems.append(
                f"'{outcome}' ({data.get('unidad')}) es una incidencia o un trastorno expresado como proporción o "
                f"recuento: como con eventos_control/eventos_tratamiento, faltan {', '.join(missing)} (definición de "
                "caso con su umbral y ventana respecto al hito), para no combinar incidencias definidas de forma "
                "distinta")
    return problems


def _insert_extraction(conn: sqlite3.Connection, data: dict, actor: str | None, status: str,
                       comparison_id: str | None = None, pair: tuple[int | None, int | None] = (None, None),
                       extractor: str | None = None, checked_by: str | None = None) -> int:
    row = {col: data.get(key) for key, col in EXTRACTION_FIELDS.items()
           if key not in EXTRACTION_SYSTEM_FIELDS and data.get(key) is not None}
    if data.get("no_consta"):
        row["not_reported"] = _dumps(data["no_consta"])
    row.update(extractor=extractor or current_actor(actor), extracted_at=utc_now(), check_status=status,
               checked_by=checked_by, comparison_id=comparison_id, pair_a=pair[0], pair_b=pair[1])
    row = {k: v for k, v in row.items() if v is not None}
    cur = conn.execute(f"INSERT INTO extractions ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                       list(row.values()))
    return int(cur.lastrowid)


def _check_extraction(data: dict) -> None:
    problems = schema_errors("extraccion", data) + extraction_problems(data)
    if problems:
        raise RegistryError("Extracción no válida: " + "; ".join(problems[:10]))


def add_extraction(conn: sqlite3.Connection, data: dict, actor: str | None = None) -> int:
    """Registra una extracción simple. La doble extracción solo entra por `register_extraction_comparison`
    (concordante) o `conciliate_extraction_comparison` (resuelta): su estado no se declara."""
    if data.get("estado_verificacion") not in (None, "simple") or data.get("verificado_por") is not None:
        raise RegistryError(
            "estado_verificacion y verificado_por los fija el sistema: `extraccion agregar` solo registra "
            "extracciones simples. 'doble_concordante' sale de `extraccion comparar A.json B.json --registrar` "
            "(dos copias depositadas con `extraccion depositar` por extractores distintos) y 'resuelta', de "
            "`extraccion conciliar <CE-xxxx> --datos <fichero>`. Quita esos campos del JSON.")
    _check_extraction(data)
    _require_usable_source(conn, data["fuente"], "extraer datos")
    with transaction(conn):
        return _insert_extraction(conn, data, actor, "simple")


def list_extractions(conn: sqlite3.Connection, source_id: str | None = None,
                     outcome: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM extractions WHERE 1=1", []
    if source_id:
        sql += " AND source_id = ?"
        args.append(source_id)
    if outcome:
        sql += " AND outcome = ?"
        args.append(outcome)
    return [row_to_dict(r) for r in conn.execute(sql + " ORDER BY id", args)]


def verified_extraction_ids(conn: sqlite3.Connection) -> set[int]:
    """Extracciones cuyo estado de doble extracción respalda el registro: 'doble_concordante' con una comparación
    registrada concordante (o conciliada) y 'resuelta' con una conciliación registrada. Además, la fila debe ser la
    que salió de esa comparación: el registro de la copia A (concordante) o el que construyó la conciliación, con los
    mismos valores (se admite la fusión posterior de dos fuentes del mismo trabajo) y una sola fila por pareja. Un
    estado declarado sin esa comparación (p. ej. de una versión anterior), una fila añadida o unos valores cambiados
    después por fuera de nutria no cuentan."""
    comparisons: dict[str, dict] = {}
    for c in conn.execute(
            "SELECT c.id, c.result, c.conciliation, ca.records AS ra, cb.records AS rb FROM extraction_comparisons c "
            "JOIN extraction_copies ca ON ca.id = c.copy_a JOIN extraction_copies cb ON cb.id = c.copy_b"):
        conciliation = json.loads(c["conciliation"]) if c["conciliation"] else None
        comparisons[c["id"]] = {
            "result": c["result"], "a": json.loads(c["ra"]), "b": json.loads(c["rb"]),
            "detail": {d.get("id"): d for d in (conciliation or {}).get("conciliacion", [])} if conciliation else None}
    out: set[int] = set()
    seen: set[tuple] = set()
    for row in conn.execute("SELECT * FROM extractions WHERE comparison_id IS NOT NULL "
                            "AND check_status IN ('doble_concordante', 'resuelta') ORDER BY id"):
        comp = comparisons.get(row["comparison_id"])
        if comp is None:
            continue
        try:
            if comp["detail"] is not None:
                detail = comp["detail"].get(row["id"])
                if (detail is None or detail.get("estado") != row["check_status"]
                        or (row["pair_a"], row["pair_b"]) != (detail.get("a"), detail.get("b"))):
                    continue
                expected, _status, _problems = _apply_resolutions(
                    comp["a"][detail["a"] - 1], comp["b"][detail["b"] - 1], detail.get("resoluciones") or [], "")
            elif comp["result"] == "concordante" and row["check_status"] == "doble_concordante":
                i = row["pair_a"]
                if not isinstance(i, int) or row["pair_b"] != i or not 1 <= i <= len(comp["a"]):
                    continue
                expected = comp["a"][i - 1]
            else:
                continue
        except (IndexError, KeyError, TypeError):
            continue
        pair = (row["comparison_id"], row["pair_a"], row["pair_b"])
        if pair not in seen and _row_matches(conn, row, expected):
            seen.add(pair)
            out.add(int(row["id"]))
    return out


def _source_aliases(conn: sqlite3.Connection, source_id: Any) -> set[str]:
    """Identificadores con que puede aparecer una fuente: el suyo y sus doi:, pmid: y openalex:. Tras fusionar dos
    registros del mismo trabajo, las copias y comparaciones anteriores citan el identificador que desapareció."""
    out = {str(source_id)}
    row = conn.execute("SELECT id, doi, pmid, openalex_id FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is not None:
        out.update(f"{prefix}:{row[col]}" for prefix, col in (("doi", "doi"), ("pmid", "pmid"),
                                                             ("openalex", "openalex_id")) if row[col])
    return out


def _row_matches(conn: sqlite3.Connection, row: sqlite3.Row, record: dict) -> bool:
    """¿Tiene la extracción registrada los valores del registro del que salió? (sin las notas, que se pueden
    anotar después; la fuente puede haber cambiado de identificador al fusionarse con un duplicado)."""
    for key, col in EXTRACTION_FIELDS.items():
        if key in COMPARE_IGNORED or key == "fuente":
            continue
        if not same_value(_norm_value(key, row[col]), _norm_value(key, record.get(key))):
            return False
    stored = json.loads(row["not_reported"]) if row["not_reported"] else None
    declared = record.get("no_consta") or None
    if not same_value(_norm_value("no_consta", stored or None), _norm_value("no_consta", declared)):
        return False
    return record.get("fuente") in _source_aliases(conn, row["source_id"])


# ---------------------------------------------------------------------------- doble extracción
def _norm_value(key: str, value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split())
    if key == "no_consta" and isinstance(value, list):
        return sorted(map(str, value))
    return value


def same_value(a: Any, b: Any) -> bool:
    """Igualdad para comparar copias: números con tolerancia relativa 1e-9; booleanos solo con booleanos."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if _is_number(a) and _is_number(b):
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)), abs(float(b)))
    return a == b


def _field_diffs(x: dict, y: dict) -> list[dict]:
    out = []
    for key in sorted((set(x) | set(y)) - COMPARE_IGNORED):
        if not same_value(_norm_value(key, x.get(key)), _norm_value(key, y.get(key))):
            out.append({"campo": key, "a": x.get(key), "b": y.get(key)})
    return out


# Valores medidos y metadatos de un registro; el resto de campos (estudio, animales, dieta basal, intervención, dosis,
# variable, ventanas, definición de caso...) identifican la comparación: dos registros que coinciden en todos ellos son
# el mismo dato aunque sus medias, n o eventos difieran (sería una segunda extracción del mismo dato). Un diseño
# factorial (dos dietas basales o dos dosis) da registros distintos.
EXTRACTION_VALUES = ("n_control", "n_tratamiento", "media_control", "media_tratamiento", "tipo_dispersion",
                     "dispersion_control", "dispersion_tratamiento", "eventos_control", "eventos_tratamiento", "p_valor")
EXTRACTION_IDENTITY = tuple(k for k in EXTRACTION_FIELDS if k not in EXTRACTION_VALUES and k not in (
    "ubicacion", "financiacion", "conflictos", "notas", *EXTRACTION_SYSTEM_FIELDS))


def _identity_value(value: Any) -> Any:
    if isinstance(value, str):
        return plain_text(value) or None
    if _is_number(value):
        return round(float(value), 9)
    return value


def extraction_identity(get) -> tuple:
    """Identidad de la comparación (`get(campo)` da el valor de un campo del esquema), sin mayúsculas ni tildes."""
    return tuple(_identity_value(get(k)) for k in EXTRACTION_IDENTITY)


def row_identity(row: Any) -> tuple:
    """Identidad de una extracción registrada (fila de la tabla extractions)."""
    return extraction_identity(lambda k: row[EXTRACTION_FIELDS[k]])


def _double_extracted_duplicate(conn: sqlite3.Connection, record: dict) -> sqlite3.Row | None:
    """Extracción ya registrada por doble extracción de la misma comparación (evita contar dos veces un dato)."""
    identity = extraction_identity(record.get)
    rows = conn.execute("SELECT * FROM extractions WHERE source_id = ? AND comparison_id IS NOT NULL ORDER BY id",
                        (record.get("fuente"),)).fetchall()
    return next((row for row in rows if row_identity(row) == identity), None)


def _insert_double_extracted(conn: sqlite3.Connection, record: dict, actor: str | None, status: str,
                             comparison_id: str, pair: tuple[int, int], extractor: str, checked_by: str) -> int:
    duplicate = _double_extracted_duplicate(conn, record)
    if duplicate is not None:
        changed = [f"{k}: {duplicate[EXTRACTION_FIELDS[k]]!r} frente a {record.get(k)!r}" for k in EXTRACTION_VALUES
                   if not same_value(_identity_value(duplicate[EXTRACTION_FIELDS[k]]), _identity_value(record.get(k)))]
        raise RegistryError(
            f"El registro A{pair[0]}/B{pair[1]} ({record.get('variable')}, {record.get('intervencion')}) es la misma "
            f"comparación que la extracción #{duplicate['id']}, ya registrada por doble extracción "
            f"({duplicate['comparison_id']})" + (f", con otros valores ({'; '.join(changed)})" if changed else "")
            + ": no se registra dos veces el mismo dato. Si es otro dato del mismo estudio (otro experimento, subgrupo, "
            "dieta basal, momento, dosis o definición de caso), distínguelo en el campo correspondiente (experimento, "
            "paridad, estado_fisiologico, dieta_basal, ventana_inicio_dias/ventana_fin_dias, dosis, definicion_caso...)")
    return _insert_extraction(conn, record, actor, status, comparison_id, pair, extractor=extractor,
                              checked_by=checked_by)


def compare_extraction_records(a: list[dict], b: list[dict]) -> list[dict]:
    """Diferencias campo a campo entre dos copias (registro a registro, por posición). Se ignoran las notas y los
    campos que fija el sistema; los textos se comparan sin tener en cuenta espacios repetidos."""
    diffs: list[dict] = []
    if len(a) != len(b):
        diffs.append({"registro": None, "campo": "(número de registros)", "a": len(a), "b": len(b)})
    for i, (x, y) in enumerate(zip(a, b), 1):
        diffs.extend({"registro": i, **d} for d in _field_diffs(x, y))
    return diffs


# Identidad de quien deposita una copia o concilia (declarada con --agente o NUTRIA_AGENT). Se compara por su clave,
# sin mayúsculas, tildes, espacios ni signos ('Extractor_Datos-A' es 'extractor-datos-a'), y solo admite letras
# latinas y cifras, para que una grafía distinta o un homoglifo de otro alfabeto no pasen por otra persona.
_ANONYMOUS_ACTORS = frozenset({"", "cli"})


def actor_key(name: Any) -> str:
    """Clave con la que se comparan dos identidades declaradas ('Extractor_Datos-A' y 'extractor datos a' son la
    misma)."""
    return "".join(text_words(name)) if name is not None else ""


def _identified_actor(actor: str | None, role: str) -> tuple[str, str]:
    """(nombre, clave) de quien deposita una copia o concilia: identificado y escrito con letras latinas y cifras."""
    who = current_actor(actor).strip()
    key = actor_key(who)
    if key in _ANONYMOUS_ACTORS:
        raise RegistryError(f"{role} necesita un responsable identificado: indica --agente <id> (p. ej. "
                            "extractor-datos-A) o la variable NUTRIA_AGENT")
    if not key.isascii():
        raise RegistryError(f"La identidad '{who}' ({role.lower()}) solo puede llevar letras latinas, cifras y "
                            "separadores (guion, guion bajo, punto o espacio): así no se confunde con otra que se le "
                            "parezca")
    return who, key


def _used_copies(conn: sqlite3.Connection) -> tuple[set[int], set[str]]:
    """Copias ya usadas en alguna comparación registrada: (ids, sha256)."""
    ids: set[int] = set()
    shas: set[str] = set()
    for r in conn.execute("SELECT copy_a, copy_b, sha256_a, sha256_b FROM extraction_comparisons"):
        ids.update((r["copy_a"], r["copy_b"]))
        shas.update((r["sha256_a"], r["sha256_b"]))
    return ids, shas


def _exposed_actors(conn: sqlite3.Connection, sources: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Clave de actor -> [(CE-xxxx, fuente)]: comparaciones con discrepancias de esas fuentes en las que participó
    (depositó la copia A o la B, registró la comparación o la concilió). Ya conoce los valores de la otra copia, así
    que no vuelve a extraer esa fuente para una doble extracción."""
    out: dict[str, list[tuple[str, str]]] = {}
    aliases = {sid: _source_aliases(conn, sid) for sid in sources}
    for row in conn.execute(
            "SELECT c.id, c.actor_a, c.actor_b, c.compared_by, c.conciliated_by, ca.source_ids AS sa, "
            "cb.source_ids AS sb FROM extraction_comparisons c JOIN extraction_copies ca ON ca.id = c.copy_a "
            "JOIN extraction_copies cb ON cb.id = c.copy_b WHERE c.result = 'con_discrepancias' "
            "ORDER BY c.compared_at"):
        involved = set(json.loads(row["sa"] or "[]")) | set(json.loads(row["sb"] or "[]"))
        for sid in sources:
            if not aliases[sid] & involved:
                continue
            for who in (row["actor_a"], row["actor_b"], row["compared_by"], row["conciliated_by"]):
                key = actor_key(who)
                if key not in _ANONYMOUS_ACTORS and (row["id"], sid) not in out.get(key, []):
                    out.setdefault(key, []).append((row["id"], sid))
    return out


def _exposure_error(who: str, hits: list[tuple[str, str]]) -> RegistryError:
    where = "; ".join(f"{cid} ({sid})" for cid, sid in hits[:5])
    return RegistryError(
        f"'{who}' participó en una comparación con discrepancias de la misma fuente ({where}): depositó una de sus "
        "copias, la registró o la concilió, así que ya conoce los valores de la otra copia y no hace una extracción "
        "independiente de esa fuente. Una doble extracción nueva de la fuente necesita dos extractores que no hayan "
        "participado en esa comparación; corregir las copias después de ver las discrepancias no sustituye a la "
        "conciliación registrada")


def deposit_extraction_copy(conn: sqlite3.Connection, records: list[dict], sha256: str, path: str | None = None,
                            actor: str | None = None) -> dict:
    """Deposita una copia de doble extracción (sin registrarla para el análisis). La huella sha256 del fichero
    la identifica y queda ligada al extractor que la deposita; si el fichero cambia, es otra copia.

    Una copia depositada es definitiva: mientras no se haya usado en una comparación registrada, su extractor no
    deposita otra de la misma fuente (no se sustituye por una corregida), y quien participó en una comparación con
    discrepancias de una fuente (copia A o B, comparación o conciliación) no deposita más copias de esa fuente."""
    who, key = _identified_actor(actor, "Una copia de doble extracción")
    if not isinstance(records, list) or not records or not all(isinstance(r, dict) for r in records):
        raise RegistryError("La copia debe contener un registro de extracción o una lista no vacía de registros")
    problems = []
    for i, rec in enumerate(records, 1):
        problems += [f"registro {i}: {p}" for p in schema_errors("extraccion", rec) + extraction_problems(rec)]
    if problems:
        raise RegistryError("Copia no válida: " + "; ".join(problems[:10]))
    sources = sorted({r["fuente"] for r in records})
    for sid in sources:
        _require_usable_source(conn, sid, "extraer datos")
    with transaction(conn):
        for existing in conn.execute("SELECT * FROM extraction_copies WHERE sha256 = ? ORDER BY id", (sha256,)):
            if actor_key(existing["deposited_by"]) == key:
                return {"id": existing["id"], "sha256": sha256, "registros": existing["n_records"],
                        "extractor": existing["deposited_by"], "nueva": False}
        hits = _exposed_actors(conn, sources).get(key)
        if hits:
            raise _exposure_error(who, hits)
        used_ids, used_shas = _used_copies(conn)
        aliases = {sid: _source_aliases(conn, sid) for sid in sources}
        for row in conn.execute("SELECT id, sha256, source_ids, deposited_by FROM extraction_copies ORDER BY id"):
            if actor_key(row["deposited_by"]) != key or row["id"] in used_ids or row["sha256"] in used_shas:
                continue
            listed = set(json.loads(row["source_ids"] or "[]"))
            shared = sorted(sid for sid in sources if aliases[sid] & listed)
            if shared:
                raise RegistryError(
                    f"'{who}' ya depositó la copia #{row['id']} con registros de {', '.join(shared)}, aún sin "
                    "comparar: una copia depositada es definitiva y no se sustituye por otra corregida. Compárala con "
                    "la del otro extractor (`nutria extraccion comparar A.json B.json --registrar`) y, si hay "
                    "discrepancias, concíliala releyendo la fuente (`nutria extraccion conciliar`)")
        cur = conn.execute(
            "INSERT INTO extraction_copies (sha256, path, records, n_records, source_ids, deposited_by, deposited_at) "
            "VALUES (?,?,?,?,?,?,?)", (sha256, path, _dumps(records), len(records), _dumps(sources), who, utc_now()))
        return {"id": int(cur.lastrowid), "sha256": sha256, "registros": len(records), "extractor": who, "nueva": True}


def _pending_comparisons(conn: sqlite3.Connection, sources: list[str]) -> list[tuple[str, str]]:
    """Comparaciones con discrepancias sin conciliar que afectan a alguna de las fuentes: [(CE-xxxx, fuente)]."""
    out = []
    aliases = {sid: _source_aliases(conn, sid) for sid in sources}
    for row in conn.execute(
            "SELECT c.id, ca.source_ids AS sa, cb.source_ids AS sb FROM extraction_comparisons c "
            "JOIN extraction_copies ca ON ca.id = c.copy_a JOIN extraction_copies cb ON cb.id = c.copy_b "
            "WHERE c.result = 'con_discrepancias' AND c.conciliation IS NULL ORDER BY c.compared_at"):
        involved = set(json.loads(row["sa"] or "[]")) | set(json.loads(row["sb"] or "[]"))
        out += [(row["id"], sid) for sid in sources if aliases[sid] & involved]
    return out


def _pick_copies(conn: sqlite3.Connection, sha_a: str, sha_b: str) -> tuple[sqlite3.Row, sqlite3.Row]:
    """Elige las copias depositadas que corresponden a los ficheros A y B: depositadas, sin usar en otra
    comparación y de extractores distintos."""
    used: dict[str, str] = {}
    for r in conn.execute("SELECT id, sha256_a, sha256_b FROM extraction_comparisons ORDER BY compared_at"):
        used.setdefault(r["sha256_a"], r["id"])
        used.setdefault(r["sha256_b"], r["id"])
    found = {}
    for label, sha in (("A", sha_a), ("B", sha_b)):
        if sha in used:
            raise RegistryError(
                f"La copia {label} ya se usó en la comparación {used[sha]}: una copia entra en una sola comparación "
                "registrada (si tuvo discrepancias, se concilia con `extraccion conciliar`)")
        rows = conn.execute("SELECT * FROM extraction_copies WHERE sha256 = ? ORDER BY id", (sha,)).fetchall()
        if not rows:
            raise RegistryError(
                f"La copia {label} (sha256 {sha[:12]}…) no está depositada: cada extractor deposita la suya con "
                "`nutria --agente <id> extraccion depositar --datos <fichero>` antes de comparar, y el fichero no "
                "puede cambiar después")
        found[label] = rows
    for a in found["A"]:
        for b in found["B"]:
            if a["id"] != b["id"] and actor_key(a["deposited_by"]) != actor_key(b["deposited_by"]):
                return a, b
    actors = sorted({r["deposited_by"] for r in found["A"] + found["B"]})
    raise RegistryError(f"Las dos copias las depositó el mismo extractor ({', '.join(actors)}; una identidad se "
                        "compara sin mayúsculas, tildes ni signos): la doble extracción exige dos extractores "
                        "distintos que trabajen de forma independiente")


def _simple_extractions_of(conn: sqlite3.Connection, records: list[dict]) -> list[int]:
    """Extracciones simples ya registradas (visibles para cualquiera) de las mismas fuentes y variables que unas copias
    de doble extracción: si una copia se hizo a partir de ellas, no es independiente."""
    wanted: dict[str, set[str]] = {}
    for rec in records:
        wanted.setdefault(str(rec.get("fuente")), set()).add(plain_text(rec.get("variable")))
    out = []
    for row in conn.execute("SELECT id, source_id, outcome FROM extractions WHERE check_status = 'simple' ORDER BY id"):
        aliases = _source_aliases(conn, row["source_id"])
        if any(src in aliases and plain_text(row["outcome"]) in outcomes for src, outcomes in wanted.items()):
            out.append(int(row["id"]))
    return out


def register_extraction_comparison(conn: sqlite3.Connection, sha_a: str, sha_b: str, actor: str | None = None,
                                   file_a: str | None = None, file_b: str | None = None) -> dict:
    """Registra la comparación de dos copias depositadas. Sin discrepancias, registra los registros como
    'doble_concordante'; con discrepancias, solo la comparación (se concilia con `conciliate_extraction_comparison`).
    Quien la registra ve las discrepancias: debe estar identificado, como quien deposita o concilia, para que la regla
    de exposición también le alcance."""
    who, _key = _identified_actor(actor, "La comparación registrada")
    with transaction(conn):
        copy_a, copy_b = _pick_copies(conn, sha_a, sha_b)
        records_a, records_b = json.loads(copy_a["records"]), json.loads(copy_b["records"])
        sources = sorted({r["fuente"] for r in records_a + records_b})
        pending = _pending_comparisons(conn, sources)
        if pending:
            raise RegistryError(
                "; ".join(f"la fuente {sid} tiene la comparación {cid} con discrepancias sin conciliar"
                          for cid, sid in pending)
                + ": concíliala antes de comparar otras copias de la misma fuente (`nutria extraccion conciliar <CE-xxxx> "
                "--datos <fichero>`, resolviendo cada discrepancia releyendo la fuente o declarando en 'no_resueltos' lo "
                "que no se pueda resolver). Corregir las copias después de ver las discrepancias no sustituye a la "
                "conciliación registrada")
        exposed = _exposed_actors(conn, sources)
        for copy_row in (copy_a, copy_b):
            hits = exposed.get(actor_key(copy_row["deposited_by"]))
            if hits:
                raise _exposure_error(copy_row["deposited_by"], hits)
        diffs = compare_extraction_records(records_a, records_b)
        result = "con_discrepancias" if diffs else "concordante"
        comp_id = _next_id(conn, "extraction_comparisons", "CE")
        conn.execute(
            "INSERT INTO extraction_comparisons (id, copy_a, copy_b, file_a, sha256_a, actor_a, file_b, sha256_b, "
            "actor_b, discrepancies, result, compared_by, compared_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (comp_id, copy_a["id"], copy_b["id"], file_a or copy_a["path"], sha_a, copy_a["deposited_by"],
             file_b or copy_b["path"], sha_b, copy_b["deposited_by"], _dumps(diffs), result, who, utc_now()))
        ids = []
        if not diffs:
            for sid in sorted({r["fuente"] for r in records_a}):
                _require_usable_source(conn, sid, "extraer datos")
            extractor = f"{copy_a['deposited_by']} | {copy_b['deposited_by']}"
            for i, rec in enumerate(records_a, 1):
                clean = {k: v for k, v in rec.items() if k not in EXTRACTION_SYSTEM_FIELDS}
                ids.append(_insert_double_extracted(conn, clean, actor, "doble_concordante", comp_id, (i, i),
                                                    extractor, who))
        warnings = []
        if sha_a == sha_b:
            warnings.append("las dos copias son idénticas byte a byte: compruébese que se extrajeron de forma "
                            "independiente")
        simple = _simple_extractions_of(conn, records_a + records_b)
        if simple:
            warnings.append("ya había extracciones simples registradas de las mismas fuentes y variables ("
                            + ", ".join(f"#{i}" for i in simple[:10]) + "), visibles en el registro: compruébese que "
                            "las copias no se hicieron a partir de ellas")
        warning = "; ".join(warnings) or None
        _log_decision(conn, comp_id, f"doble extracción comparada: {result}",
                      f"A: {file_a or copy_a['path']} ({copy_a['deposited_by']}, sha256 {sha_a[:12]}); "
                      f"B: {file_b or copy_b['path']} ({copy_b['deposited_by']}, sha256 {sha_b[:12]}); "
                      f"{len(diffs)} discrepancia(s); registradas: {ids or 'ninguna'}"
                      + "".join(f"; AVISO: {w}" for w in warnings), actor)
        return {"id": comp_id, "resultado": result, "discrepancias": diffs, "ids": ids,
                "extractor_a": copy_a["deposited_by"], "extractor_b": copy_b["deposited_by"],
                "sha256_a": sha_a, "sha256_b": sha_b, "aviso": warning, "avisos": warnings}


def _apply_resolutions(x: dict, y: dict, resolutions: list[dict], where: str) -> tuple[dict, str, list[str]]:
    """Construye el registro conciliado: parte de la copia A y aplica solo las resoluciones declaradas.

    Un valor nulo se conserva como en la copia de la que sale: con origen 'A' o 'B', el campo queda como en esa copia
    (a null y declarado en su 'no_consta' si es obligatorio y la fuente no lo publica; omitido si es opcional). Con
    origen 'fuente' y valor null (la fuente no publica el dato), un campo obligatorio queda a null y, si 'no_consta'
    no tiene su propia resolución (A y B lo declaran igual), el sistema lo añade a 'no_consta'; uno opcional se omite.
    """
    problems: list[str] = []
    by_field: dict[str, dict] = {}
    for res in resolutions:
        field = res["campo"]
        if field in by_field:
            problems.append(f"{where}: dos resoluciones para '{field}'")
        elif field in COMPARE_IGNORED or (field not in EXTRACTION_FIELDS and field != "no_consta"):
            problems.append(f"{where}: '{field}' no es un campo conciliable del esquema de extracción")
        by_field[field] = res
    required = set(load_schema("extraccion")["required"])
    record = {k: v for k, v in x.items() if k not in EXTRACTION_SYSTEM_FIELDS}
    not_published: list[str] = []      # obligatorios que la fuente no publica según la resolución ('fuente': null)
    for key in sorted((set(x) | set(y) | set(by_field)) - COMPARE_IGNORED):
        va, vb = x.get(key), y.get(key)
        differ = not same_value(_norm_value(key, va), _norm_value(key, vb))
        res = by_field.get(key)
        if res is None:
            if differ:
                problems.append(f"{where}: discrepancia sin resolver en '{key}' (A={va!r}, B={vb!r})")
            continue
        origin = res["origen_valor"]
        if not differ:
            # Lo que las dos copias independientes extrajeron igual no lo cambia la conciliación (ni con 'fuente').
            problems.append(f"{where}: resolución no permitida para '{key}': A y B coinciden ({va!r}); la conciliación "
                            "solo resuelve discrepancias (si las dos copias se equivocaron igual, repite la doble "
                            "extracción)")
            continue
        if origin in ("A", "B"):
            chosen = x if origin == "A" else y
            value, keep = chosen.get(key), key in chosen
        else:
            value = res.get("valor")
            keep = value is not None or key in required
            for label, other in (("A", va), ("B", vb)):
                if same_value(_norm_value(key, value), _norm_value(key, other)):
                    problems.append(f"{where}: el valor 'fuente' de '{key}' coincide con la copia {label}: indica "
                                    f"origen_valor '{label}'")
                    break
            else:
                if (_is_number(va) and _is_number(vb) and _is_number(value) and not res.get("confirmado_en_fuente")
                        and same_value(float(value), (float(va) + float(vb)) / 2)):
                    problems.append(f"{where}: '{key}' = {value!r} es el promedio de A ({va!r}) y B ({vb!r}); promediar "
                                    "está prohibido. Si la fuente da exactamente ese valor, añade "
                                    "\"confirmado_en_fuente\": true a la resolución")
                if value is None and key in required:
                    not_published.append(key)
        if keep:
            record[key] = value
        else:
            record.pop(key, None)
    if not_published and "no_consta" not in by_field:
        declared = record.get("no_consta") if isinstance(record.get("no_consta"), list) else []
        record["no_consta"] = declared + [k for k in not_published if k not in declared]
    return record, ("resuelta" if by_field else "doble_concordante"), problems


def conciliate_extraction_comparison(conn: sqlite3.Connection, comparison_id: str, payload: dict,
                                     sha256: str | None = None, actor: str | None = None) -> dict:
    """Registra la conciliación de una comparación con discrepancias. Cada registro de A y B se empareja o se
    declara no resuelto; el sistema construye cada registro conciliado desde la copia A aplicando las resoluciones
    (una por campo en discrepancia, con origen del valor y motivo) y lo registra como 'resuelta' (o
    'doble_concordante' si su pareja no tenía discrepancias)."""
    errors = schema_errors("conciliacion", payload)
    if errors:
        raise RegistryError("Conciliación no válida: " + "; ".join(errors[:8]))
    who, key = _identified_actor(actor, "La conciliación")
    with transaction(conn):
        comp = conn.execute("SELECT * FROM extraction_comparisons WHERE id = ?", (comparison_id,)).fetchone()
        if comp is None:
            raise RegistryError(f"No existe la comparación {comparison_id} (`nutria extraccion comparaciones`)")
        if comp["result"] != "con_discrepancias":
            raise RegistryError(f"{comparison_id} es concordante: sus registros ya constan como 'doble_concordante'")
        if comp["conciliation"] is not None:
            raise RegistryError(f"{comparison_id} ya está conciliada ({comp['conciliated_by']}, {comp['conciliated_at']})")
        if key in (actor_key(comp["actor_a"]), actor_key(comp["actor_b"])):
            raise RegistryError(f"'{who}' depositó una de las copias de {comparison_id} (las identidades se comparan "
                                "sin mayúsculas, tildes ni signos): la conciliación la hace otro extractor o el "
                                "orquestador, no quien extrajo la copia A o la B")
        copies = {r["id"]: json.loads(r["records"]) for r in conn.execute(
            "SELECT id, records FROM extraction_copies WHERE id IN (?, ?)", (comp["copy_a"], comp["copy_b"]))}
        records = {"a": copies[comp["copy_a"]], "b": copies[comp["copy_b"]]}
        entries, unresolved = payload.get("conciliacion") or [], payload.get("no_resueltos") or []
        seen: dict[str, dict[int, str]] = {"a": {}, "b": {}}
        problems: list[str] = []
        for where, entry in ([(f"conciliacion[{k}]", e) for k, e in enumerate(entries, 1)]
                             + [(f"no_resueltos[{k}]", e) for k, e in enumerate(unresolved, 1)]):
            for side in ("a", "b"):
                idx = entry.get(side)
                if idx is None:
                    continue
                if idx > len(records[side]):
                    problems.append(f"{where}: {side}={idx} no existe (la copia {side.upper()} tiene "
                                    f"{len(records[side])} registros)")
                elif idx in seen[side]:
                    problems.append(f"{where}: el registro {side.upper()}{idx} ya aparece en {seen[side][idx]}")
                else:
                    seen[side][idx] = where
        missing = [f"{side.upper()}{i}" for side in ("a", "b") for i in range(1, len(records[side]) + 1)
                   if i not in seen[side]]
        if missing:
            problems.append("registros sin emparejar ni declarar en 'no_resueltos': " + ", ".join(missing))
        built = []
        if not problems:
            for k, entry in enumerate(entries, 1):
                where = f"conciliacion[{k}] (A{entry['a']}, B{entry['b']})"
                rec_a, rec_b = records["a"][entry["a"] - 1], records["b"][entry["b"] - 1]
                if rec_a.get("fuente") != rec_b.get("fuente"):
                    problems.append(f"{where}: son de fuentes distintas ({rec_a.get('fuente')} y {rec_b.get('fuente')}): "
                                    "no son el mismo dato y no se emparejan; decláralos en 'no_resueltos'")
                    continue
                record, status, errs = _apply_resolutions(rec_a, rec_b, entry.get("resoluciones") or [], where)
                errs += [f"{where}: registro resultante: {p}"
                         for p in schema_errors("extraccion", record) + extraction_problems(record)]
                problems += errs
                built.append((entry, record, status))
        if problems:
            raise RegistryError("Conciliación rechazada (no se registra nada): " + "; ".join(problems[:12]))
        # Emparejar registros de variables distintas es responsabilidad de quien concilia: queda avisado y registrado.
        warnings = []
        for entry, _record, _status in built:
            rec_a, rec_b = records["a"][entry["a"] - 1], records["b"][entry["b"] - 1]
            for field in ("variable", "unidad"):
                if plain_text(rec_a.get(field)) != plain_text(rec_b.get(field)):
                    warnings.append(f"A{entry['a']}/B{entry['b']} emparejados con {field} distinta "
                                    f"({rec_a.get(field)!r} en A, {rec_b.get(field)!r} en B): compruébese que son el "
                                    "mismo dato y no dos datos distintos de la fuente")
        extractor = f"{comp['actor_a']} | {comp['actor_b']}"
        detail = []
        for entry, record, status in built:
            _require_usable_source(conn, record["fuente"], "extraer datos")
            new_id = _insert_double_extracted(conn, record, actor, status, comparison_id, (entry["a"], entry["b"]),
                                              extractor, who)
            detail.append({"a": entry["a"], "b": entry["b"], "id": new_id, "estado": status,
                           "resoluciones": entry.get("resoluciones") or []})
        conciliation = {"sha256": sha256, "conciliacion": detail, "no_resueltos": unresolved}
        conn.execute("UPDATE extraction_comparisons SET conciliation = ?, conciliation_sha256 = ?, conciliated_by = ?, "
                     "conciliated_at = ? WHERE id = ?", (_dumps(conciliation), sha256, who, utc_now(), comparison_id))
        ids = [d["id"] for d in detail]
        _log_decision(conn, comparison_id, "doble extracción conciliada",
                      f"{len(ids)} registro(s) registrados ({sum(d['estado'] == 'resuelta' for d in detail)} "
                      f"resueltos); {len(unresolved)} no resuelto(s) sin registrar"
                      + (f"; fichero sha256 {sha256[:12]}" if sha256 else "")
                      + "".join(f"; AVISO: {w}" for w in warnings), actor)
        return {"id": comparison_id, "ids": ids, "registros": detail, "no_resueltos": unresolved, "avisos": warnings}


def list_extraction_comparisons(conn: sqlite3.Connection, pending_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM extraction_comparisons"
    if pending_only:
        sql += " WHERE result = 'con_discrepancias' AND conciliation IS NULL"
    return [row_to_dict(r) for r in conn.execute(sql + " ORDER BY CAST(substr(id, 4) AS INTEGER)")]


# ---------------------------------------------------------------------------- afirmaciones
PUB_CONTEXT_REQUIRED = ("especie", "diseno", "unidad_experimental", "n", "dosis", "dosis_base", "forma_producto")


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def pub_context_problems(data: dict) -> list[str]:
    """Contexto biológico que exige una afirmación [PUB] (regla 3 de CLAUDE.md); 'no_consta' lo declara ausente."""
    ctx = data.get("contexto")
    if not isinstance(ctx, dict):
        return ["falta 'contexto': especie, etapa o estado fisiológico, diseño, unidad experimental, n, dosis con su "
                "base de expresión y forma del producto ('no_consta' para lo que la fuente no informe)"]
    problems = []
    missing = [k for k in PUB_CONTEXT_REQUIRED if _blank(ctx.get(k))]
    if _blank(ctx.get("etapa")) and _blank(ctx.get("estado_fisiologico")):
        missing.append("etapa o estado_fisiologico")
    if missing:
        problems.append("faltan en 'contexto': " + ", ".join(missing))
    if _blank(data.get("transferibilidad")):
        problems.append("falta 'transferibilidad' (especie_diana, otra_especie, in_vitro, in_silico o humano)")
    elif data["transferibilidad"] in ("especie_diana", "otra_especie") and ctx.get("especie") == "no_consta":
        problems.append(f"con transferibilidad '{data['transferibilidad']}' hay que nombrar la especie del estudio (es "
                        "la que se compara con la especie diana)")
    return problems


def add_claim(conn: sqlite3.Connection, data: dict, actor: str | None = None) -> str:
    errors = schema_errors("afirmacion", data)
    if errors:
        raise RegistryError("Afirmación no válida: " + "; ".join(errors[:8]))
    label = data["etiqueta"]
    sources = data.get("fuentes") or []
    if label == "PUB":
        if not sources:
            raise RegistryError("Una afirmación [PUB] necesita al menos una fuente registrada y verificada")
        if not (data.get("cita_textual") or "").strip() or not (data.get("ubicacion") or "").strip():
            raise RegistryError("Una afirmación [PUB] necesita la cita textual de la fuente y su ubicación "
                                "(tabla, figura o página) para poder comprobar el respaldo")
        problems = pub_context_problems(data)
        if problems:
            raise RegistryError("Una afirmación [PUB] necesita su contexto biológico: " + "; ".join(problems))
        for sid in sources:
            _require_usable_source(conn, sid, "respaldar una afirmación [PUB]")
    else:
        for sid in sources:
            if get_source(conn, sid) is None:
                raise RegistryError(f"Fuente no registrada: {sid}")
    with transaction(conn):
        claim_id = _next_id(conn, "claims", "A")
        conn.execute(
            "INSERT INTO claims (id, text, label, evidence_level, transferability, source_ids, quote, "
            "location, entity_id, created_by, created_at, context) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (claim_id, data["texto"], label, data.get("nivel"), data.get("transferibilidad"),
             _dumps(sources), data.get("cita_textual"), data.get("ubicacion"), data.get("entidad"),
             current_actor(actor), utc_now(), _dumps(data.get("contexto"))),
        )
        return claim_id


def list_claims(conn: sqlite3.Connection, entity_id: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM claims", []
    if entity_id:
        sql += " WHERE entity_id = ?"
        args.append(entity_id)
    return [row_to_dict(r) for r in conn.execute(sql + " ORDER BY id", args)]


# ---------------------------------------------------------------------------- oportunidades
CLASSIFICATION_FIELDS = ("ruta_regulatoria_preliminar", "grupo_funcional", "situacion")
# Rol que hace el triage regulatorio y propone la compuerta regulatoria (CLAUDE.md, equipo).
CLASSIFICATION_OWNER = "regulacion-seguridad"


def _block_regulatory_gate(conn: sqlite3.Connection, opp_id: str, route: str, actor: str | None) -> str | None:
    """Bloqueo automático de la compuerta regulatoria por una ruta fuera_de_alcance_* (alta o reclasificación)."""
    current = conn.execute("SELECT status FROM gates WHERE opportunity_id = ? AND gate = 'regulatoria'",
                           (opp_id,)).fetchone()
    if current is not None and current["status"] == "bloqueada":
        return None
    conn.execute(
        "INSERT INTO gates (opportunity_id, gate, status, rationale, decided_by, updated_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(opportunity_id, gate) DO UPDATE SET status = excluded.status, rationale = excluded.rationale, "
        "evidence = NULL, decided_by = excluded.decided_by, updated_at = excluded.updated_at, human_signoff = NULL, "
        "signed_at = NULL",
        (opp_id, "regulatoria", "bloqueada", f"Ruta regulatoria '{route}': fuera del alcance de un pienso",
         "registro", utc_now()))
    _log_decision(conn, opp_id, "compuerta regulatoria -> bloqueada (automática)",
                  f"Ruta regulatoria '{route}'" + (f" (antes: {current['status']})" if current is not None else ""),
                  actor)
    return "bloqueada"


def _reopen_regulatory_gate(conn: sqlite3.Connection, opp_id: str, change: str, actor: str | None) -> str | None:
    """Si cambia la clasificación, una compuerta regulatoria 'superada' (propuesta o firmada) deja de valer."""
    current = conn.execute("SELECT status, human_signoff FROM gates WHERE opportunity_id = ? AND gate = 'regulatoria'",
                           (opp_id,)).fetchone()
    if current is None or current["status"] != "superada":
        return None
    reason = (f"La clasificación regulatoria cambió ({change}) después de la decisión "
              f"{'firmada por ' + current['human_signoff'] if current['human_signoff'] else 'propuesta'}: "
              "hay que revisar la compuerta")
    conn.execute("UPDATE gates SET status = 'no_resuelta', rationale = ?, evidence = NULL, decided_by = 'registro', "
                 "updated_at = ?, human_signoff = NULL, signed_at = NULL WHERE opportunity_id = ? AND gate = 'regulatoria'",
                 (reason, utc_now(), opp_id))
    _log_decision(conn, opp_id, "compuerta regulatoria -> no_resuelta (automática: clasificación modificada)", reason,
                  actor)
    return "no_resuelta"


def classification_errors(candidate: dict) -> list[str]:
    """Valida ruta, grupo funcional y situación (y su coherencia con la alegación prevista) con el esquema de
    oportunidad, sin exigir el resto de campos."""
    schema = load_schema("oportunidad")
    keys = (*CLASSIFICATION_FIELDS, "alegacion_prevista")
    sub = {"type": "object", "required": ["ruta_regulatoria_preliminar"],
           "properties": {k: schema["properties"][k] for k in keys}, "allOf": schema.get("allOf", []),
           "$defs": schema.get("$defs", {})}
    return schema_errors("oportunidad", {k: v for k, v in candidate.items() if k in keys}, schema=sub)


def add_opportunity(conn: sqlite3.Connection, data: dict, actor: str | None = None) -> str:
    """Registra una oportunidad válida según su esquema (especie y etapa concretas, ruta, grupo funcional y
    situación coherentes), también si no entra por la CLI. Una ruta fuera_de_alcance_* bloquea la compuerta
    regulatoria."""
    errors = schema_errors("oportunidad", data)
    if errors:
        raise RegistryError("Oportunidad no válida: " + "; ".join(errors[:8]))
    with transaction(conn):
        opp_id = _next_id(conn, "opportunities", "O")
        now = utc_now()
        route = data.get("ruta_regulatoria_preliminar")
        conn.execute(
            "INSERT INTO opportunities (id, title, problem, species, production_stage, mechanism, "
            "candidates, product_type, regulatory_route, data, created_by, created_at, updated_at, "
            "functional_group, regulatory_situation) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (opp_id, data["titulo"], data.get("problema"), data.get("especie"), data.get("etapa"),
             data.get("mecanismo"), _dumps(data.get("candidatos")), data.get("tipo_producto"),
             route, _dumps(data), current_actor(actor), now, now, data.get("grupo_funcional"),
             data.get("situacion")),
        )
        _log_decision(conn, opp_id, "creada (propuesta)", data.get("justificacion") or "Alta inicial", actor)
        if route in OUT_OF_SCOPE_ROUTES:
            _block_regulatory_gate(conn, opp_id, route, actor)
        return opp_id


def classify_opportunity(conn: sqlite3.Connection, opp_id: str, route: str, reason: str,
                         functional_group: str | None = None, situation: str | None = None,
                         actor: str | None = None, human_signoff: str | None = None) -> dict:
    """Sustituye la clasificación regulatoria (ruta, grupo funcional y situación) por el resultado del triage, con
    decisión registrada y actor.

    - Sin firma humana, solo el rol del triage regulatorio (CLASSIFICATION_OWNER, por --agente o NUTRIA_AGENT)
      clasifica; una ruta fuera_de_alcance_* la puede proponer cualquiera (criterio de prudencia, como "bloqueada").
    - Una ruta fuera_de_alcance_* bloquea en el acto la compuerta regulatoria (mismo mecanismo que el alta).
    - Salir de una ruta fuera_de_alcance_* exige firma humana (rediseño revisado); la compuerta sigue bloqueada
      hasta que una persona la revise.
    - Cualquier otro cambio de clasificación devuelve a 'no_resuelta' una compuerta regulatoria 'superada'.
    """
    if not (reason or "").strip():
        raise RegistryError("La clasificación necesita un motivo (resultado del triage regulatorio)")
    signed = human_signoff.strip() if human_signoff and human_signoff.strip() else None
    # Regla 12 en el propio registro: si el registro comprueba que una firma humana no la registra un agente
    # (`require_human_signature`, como en las compuertas), la comprobación se aplica también al clasificar con firma.
    check_signature = globals().get("require_human_signature")
    if signed and callable(check_signature):
        check_signature(signed)
    role = current_actor(actor).strip()
    if not signed and route not in OUT_OF_SCOPE_ROUTES and role != CLASSIFICATION_OWNER:
        raise RegistryError(
            f"Clasificar una oportunidad (resultado del triage regulatorio) corresponde a {CLASSIFICATION_OWNER} "
            f"(nutria --agente {CLASSIFICATION_OWNER} oportunidad clasificar ...); '{role}' no es ese rol. Cualquier "
            "agente puede proponer una ruta fuera_de_alcance_* (bloquea la compuerta regulatoria) y una persona "
            "clasifica en su terminal con --firma-humana")
    with transaction(conn):
        opp = get_opportunity(conn, opp_id)
        if opp is None:
            raise RegistryError(f"No existe la oportunidad {opp_id}")
        data = dict(opp.get("data") or {})
        candidate = {"ruta_regulatoria_preliminar": route, "grupo_funcional": functional_group, "situacion": situation,
                     "alegacion_prevista": data.get("alegacion_prevista")}
        errors = classification_errors({k: v for k, v in candidate.items() if v is not None})
        if route.startswith("aditivo_") and not functional_group:
            # el triage de un aditivo determina su categoría y grupo funcional (anexo I del Reglamento (CE) 1831/2003)
            errors.append(f"la ruta {route} exige --grupo-funcional (categoría y grupo funcional del anexo I del "
                          "Reglamento (CE) 1831/2003; si ninguno encaja, el grupo 'otros' de su categoría)")
        if errors:
            raise RegistryError("Clasificación no válida: " + "; ".join(errors))
        old = {"ruta": opp.get("regulatory_route"), "grupo_funcional": opp.get("functional_group"),
               "situacion": opp.get("regulatory_situation")}
        new = {"ruta": route, "grupo_funcional": functional_group, "situacion": situation}
        if old["ruta"] in OUT_OF_SCOPE_ROUTES and route not in OUT_OF_SCOPE_ROUTES and not signed:
            raise RegistryError(
                f"{opp_id} tiene la ruta '{old['ruta']}' (fuera de alcance): pasar a '{route}' exige un rediseño "
                "revisado y --firma-humana de una persona; la compuerta regulatoria seguirá bloqueada hasta que la "
                "revise")
        for key, value in zip(CLASSIFICATION_FIELDS, (route, functional_group, situation)):
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        now = utc_now()
        conn.execute(
            "UPDATE opportunities SET regulatory_route = ?, functional_group = ?, regulatory_situation = ?, "
            "classified_by = ?, classified_at = ?, data = ?, updated_at = ? WHERE id = ?",
            (route, functional_group, situation, current_actor(actor), now, _dumps(data), now, opp_id))

        def fmt(c: dict) -> str:
            return " / ".join(str(c[k]) for k in ("ruta", "grupo_funcional", "situacion") if c.get(k)) or "sin ruta"

        change = f"{fmt(old)} -> {fmt(new)}"
        _log_decision(conn, opp_id, f"clasificación regulatoria: {change}", reason, actor, signed)
        if route in OUT_OF_SCOPE_ROUTES:
            gate_change = _block_regulatory_gate(conn, opp_id, route, actor)
        elif old != new:
            gate_change = _reopen_regulatory_gate(conn, opp_id, change, actor)
        else:
            gate_change = None
        return {"id": opp_id, "anterior": old, "nueva": new, "firmada": bool(signed),
                "cambio_compuerta_regulatoria": gate_change,
                "compuerta_regulatoria": effective_gates(conn, opp_id, ["regulatoria"])["regulatoria"]}


def get_opportunity(conn: sqlite3.Connection, opp_id: str) -> dict | None:
    return row_to_dict(conn.execute("SELECT * FROM opportunities WHERE id = ?", (opp_id,)).fetchone())


def list_opportunities(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM opportunities", []
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    return [row_to_dict(r) for r in conn.execute(sql + " ORDER BY id", args)]


def effective_gates(conn: sqlite3.Connection, opp_id: str, gate_ids: list[str] | set[str]) -> dict[str, str]:
    """Estado efectivo de cada compuerta: "bloqueada" actúa siempre (aunque sea una propuesta sin firmar);
    "superada" solo cuenta con firma humana; lo demás, y lo no evaluado, es "no_resuelta"."""
    rows = {r["gate"]: r for r in conn.execute("SELECT * FROM gates WHERE opportunity_id = ?", (opp_id,))}
    out = {}
    for gate in gate_ids:
        row = rows.get(gate)
        if row is None:
            out[gate] = "no_resuelta"
        elif row["status"] == "bloqueada":
            out[gate] = "bloqueada"
        elif row["status"] == "superada" and row["human_signoff"]:
            out[gate] = "superada"
        else:
            out[gate] = "no_resuelta"
    opp = get_opportunity(conn, opp_id)
    if opp and opp.get("regulatory_route") in OUT_OF_SCOPE_ROUTES and "regulatoria" in out:
        out["regulatoria"] = "bloqueada"
    return out


def set_opportunity_status(conn: sqlite3.Connection, opp_id: str, status: str, reason: str,
                           actor: str | None = None, human_signoff: str | None = None,
                           gate_ids: list[str] | set[str] | None = None) -> None:
    if status not in OPPORTUNITY_STATUSES:
        raise RegistryError(f"Estado no válido: {status}. Opciones: {', '.join(OPPORTUNITY_STATUSES)}")
    if not reason.strip():
        raise RegistryError("Todo cambio de estado necesita un motivo")
    opp = get_opportunity(conn, opp_id)
    if opp is None:
        raise RegistryError(f"No existe la oportunidad {opp_id}")
    signed = bool(human_signoff and human_signoff.strip())
    require_human_signature(human_signoff)
    if opp["status"] in ("bloqueada", "descartada") and status != opp["status"] and not signed:
        raise RegistryError(f"Reactivar una oportunidad '{opp['status']}' exige --firma-humana")
    if status in ("priorizada", "en_validacion"):
        if not signed:
            raise RegistryError(f"Pasar a '{status}' exige --firma-humana")
        gates = effective_gates(conn, opp_id, gate_ids or [])
        blocked = [g for g, s in gates.items() if s == "bloqueada"]
        if blocked:
            raise RegistryError(f"No puede pasar a '{status}' con compuertas bloqueadas: {', '.join(blocked)}")
        if status == "en_validacion":
            pending = [g for g, s in gates.items() if s != "superada"]
            if pending:
                raise RegistryError("Pasar a validación experimental exige todas las compuertas superadas y "
                                    f"firmadas. Pendientes: {', '.join(pending)}")
    with transaction(conn):
        conn.execute("UPDATE opportunities SET status = ?, status_reason = ?, updated_at = ? WHERE id = ?",
                     (status, reason, utc_now(), opp_id))
        _log_decision(conn, opp_id, f"estado -> {status}", reason, actor, human_signoff)


# ---------------------------------------------------------------------------- hipótesis
def add_hypothesis(conn: sqlite3.Connection, data: dict, actor: str | None = None) -> str:
    opp = data.get("oportunidad")
    if opp and get_opportunity(conn, opp) is None:
        raise RegistryError(f"No existe la oportunidad {opp}")
    with transaction(conn):
        hyp_id = _next_id(conn, "hypotheses", "H")
        now = utc_now()
        conn.execute(
            "INSERT INTO hypotheses (id, opportunity_id, title, origin, prior_probability, data, "
            "created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (hyp_id, opp, data["titulo"], data.get("origen"), data.get("probabilidad_previa"),
             _dumps(data), current_actor(actor), now, now),
        )
        _log_decision(conn, hyp_id, "creada (propuesta)", f"Origen: {data.get('origen', 'n/d')}", actor)
        return hyp_id


def get_hypothesis(conn: sqlite3.Connection, hyp_id: str) -> dict | None:
    return row_to_dict(conn.execute("SELECT * FROM hypotheses WHERE id = ?", (hyp_id,)).fetchone())


def list_hypotheses(conn: sqlite3.Connection, status: str | None = None,
                    opportunity_id: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM hypotheses WHERE 1=1", []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if opportunity_id:
        sql += " AND opportunity_id = ?"
        args.append(opportunity_id)
    return [row_to_dict(r) for r in conn.execute(sql + " ORDER BY id", args)]


def set_hypothesis_status(conn: sqlite3.Connection, hyp_id: str, status: str, reason: str,
                          actor: str | None = None, human_signoff: str | None = None,
                          gate_ids: list[str] | set[str] | None = None) -> None:
    if status not in HYPOTHESIS_STATUSES:
        raise RegistryError(f"Estado no válido: {status}. Opciones: {', '.join(HYPOTHESIS_STATUSES)}")
    if not reason.strip():
        raise RegistryError("Todo cambio de estado necesita un motivo (registro de hipótesis descartadas)")
    require_human_signature(human_signoff)
    with transaction(conn):
        if conn.execute("SELECT 1 FROM hypotheses WHERE id = ?", (hyp_id,)).fetchone() is None:
            raise RegistryError(f"No existe la hipótesis {hyp_id}")
        conn.execute("UPDATE hypotheses SET status = ?, status_reason = ?, updated_at = ? WHERE id = ?",
                     (status, reason, utc_now(), hyp_id))
        _log_decision(conn, hyp_id, f"estado -> {status}", reason, actor, human_signoff)


# ---------------------------------------------------------------------------- puntuación y compuertas
# Guiones tipográficos (U+2010 a U+2015, U+2212 menos, U+FE58, U+FE63, U+FF0D) que se confunden con el guion ASCII.
_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"), "-")


def normalize_rater(rater: str | None) -> str:
    """Identificador canónico de un evaluador: forma Unicode NFKC, guiones tipográficos como '-', sin espacios
    sobrantes y en minúsculas.

    'evaluador-1', ' evaluador-1 ', 'Evaluador-1' y 'evaluador‐1' (guion U+2010) son el mismo evaluador: se guardan
    igual y cuentan una sola vez para el mínimo de evaluadores distintos del ranking."""
    import unicodedata
    text = unicodedata.normalize("NFKC", str(rater or "")).translate(_DASHES)
    return " ".join(text.split()).casefold()


def add_scores(conn: sqlite3.Connection, opp_id: str, rater: str, items: list[dict],
               valid_criteria: set[str], actor: str | None = None) -> int:
    """Registra (o sustituye, guardando el valor anterior en `scores_history`) las puntuaciones de `rater`.

    El evaluador se guarda normalizado (`normalize_rater`). Con `actor` (quien ejecuta la orden), este debe ser el
    propio evaluador (también normalizado): nadie registra ni sobrescribe las puntuaciones de otro, y el historial
    guarda ese actor real. La CLI exige además que sea un evaluador.
    """
    raw_rater, rater = rater, normalize_rater(rater)
    if not rater:
        raise RegistryError("El campo 'evaluador' no puede estar vacío")
    if actor is not None and normalize_rater(actor) != rater:
        raise RegistryError(f"El evaluador del JSON ('{raw_rater}') no es quien registra ('{actor}'): nadie registra "
                            "ni sobrescribe las puntuaciones de otro evaluador (regla 9)")
    if get_opportunity(conn, opp_id) is None:
        raise RegistryError(f"No existe la oportunidad {opp_id}")
    replaced_by = normalize_rater(actor) if actor is not None else current_actor(None)
    n = 0
    with transaction(conn):
        for item in items:
            crit = item["criterio"]
            if crit not in valid_criteria:
                raise RegistryError(f"Criterio desconocido: {crit}. Válidos: {', '.join(sorted(valid_criteria))}")
            value = float(item["valor"])
            low = float(item.get("min", value))
            high = float(item.get("max", value))
            if not 0 <= low <= value <= high <= 1:
                raise RegistryError(f"{crit}: se requiere 0 <= min <= valor <= max <= 1")
            previous = conn.execute("SELECT * FROM scores WHERE opportunity_id = ? AND rater = ? AND criterion = ?",
                                    (opp_id, rater, crit)).fetchone()
            if previous is not None:
                conn.execute(
                    "INSERT INTO scores_history (opportunity_id, rater, criterion, value, low, high, rationale, "
                    "evidence, created_at, replaced_at, replaced_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (opp_id, rater, crit, previous["value"], previous["low"], previous["high"], previous["rationale"],
                     previous["evidence"], previous["created_at"], utc_now(), replaced_by))
            conn.execute(
                "INSERT INTO scores (opportunity_id, rater, criterion, value, low, high, rationale, evidence, "
                "created_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(opportunity_id, rater, criterion) DO UPDATE "
                "SET value=excluded.value, low=excluded.low, high=excluded.high, rationale=excluded.rationale, "
                "evidence=excluded.evidence, created_at=excluded.created_at",
                (opp_id, rater, crit, value, low, high, item.get("justificacion"),
                 _dumps(item.get("evidencia")), utc_now()),
            )
            n += 1
    return n


def list_scores(conn: sqlite3.Connection, opp_id: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM scores", []
    if opp_id:
        sql += " WHERE opportunity_id = ?"
        args.append(opp_id)
    return [row_to_dict(r) for r in conn.execute(sql + " ORDER BY opportunity_id, criterion, rater", args)]


def set_gate(conn: sqlite3.Connection, opp_id: str, gate: str, status: str, rationale: str,
             valid_gates: set[str], evidence: list | None = None, actor: str | None = None,
             human_signoff: str | None = None) -> None:
    """Registra una propuesta de compuerta o, con firma humana, una decisión.

    - "bloqueada" actúa de inmediato aunque no esté firmada (criterio de prudencia) y la puede proponer cualquiera,
      también sobre una decisión firmada; su evidencia no se valida (un error en ella no impide bloquear);
    - sin firma, cualquier otro estado solo lo propone el rol propietario de la compuerta (GATE_OWNERS), identificado
      con `actor` (--agente) o NUTRIA_AGENT, y nunca sustituye una decisión firmada por una persona;
    - "superada" sin firma queda como propuesta y no cuenta como superada;
    - salir de "bloqueada" exige firma humana y evidencia;
    - la evidencia (salvo en "bloqueada") no admite elementos vacíos ni la propia oportunidad, y los IDs del registro
      que cite deben existir (la CLI, que conoce la raíz del proyecto, comprueba además que los ficheros citados sean
      ficheros regulares dentro del proyecto y que las URL tengan forma de URL).
    """
    if gate not in valid_gates:
        raise RegistryError(f"Compuerta desconocida: {gate}. Válidas: {', '.join(sorted(valid_gates))}")
    if status not in GATE_STATUSES:
        raise RegistryError(f"Estado de compuerta no válido: {status}")
    if not rationale.strip():
        raise RegistryError("La decisión de compuerta necesita un motivo")
    opp = get_opportunity(conn, opp_id)
    if opp is None:
        raise RegistryError(f"No existe la oportunidad {opp_id}")
    signed = human_signoff.strip() if human_signoff and human_signoff.strip() else None
    require_human_signature(signed)
    current = conn.execute("SELECT * FROM gates WHERE opportunity_id = ? AND gate = ?", (opp_id, gate)).fetchone()
    leaving_block = current is not None and current["status"] == "bloqueada" and status != "bloqueada"
    if leaving_block and not signed:
        raise RegistryError(f"La compuerta {gate} de {opp_id} está bloqueada: desbloquearla exige --firma-humana")
    if gate == "regulatoria" and status != "bloqueada" and opp.get("regulatory_route") in OUT_OF_SCOPE_ROUTES \
            and not signed:
        raise RegistryError(f"{opp_id} tiene la ruta '{opp['regulatory_route']}': la compuerta regulatoria solo "
                            "puede cambiarse con --firma-humana (y conviene corregir antes la ruta)")
    if not signed and status != "bloqueada":
        owner = GATE_OWNERS.get(gate)
        role = declared_agent(actor)
        if owner is None:
            raise RegistryError(f"La compuerta {gate} no tiene rol propietario: sin --firma-humana solo se puede "
                                "proponer 'bloqueada'")
        if role != owner:
            who = f"'{role}' no es su rol propietario" if role else "identifícate con --agente"
            raise RegistryError(f"Proponer '{status}' en la compuerta {gate} corresponde a {owner} (nutria --agente "
                                f"{owner} ...); {who}. Cualquier agente puede proponer 'bloqueada', y una persona "
                                "decide en su terminal con --firma-humana.")
        if current is not None and current["human_signoff"]:
            raise RegistryError(f"La compuerta {gate} de {opp_id} ya la decidió y firmó {current['human_signoff']} "
                                f"('{current['status']}'): una propuesta sin firma no sustituye una decisión humana. "
                                "Si hay un motivo nuevo, propón 'bloqueada' (actúa de inmediato) o pide a una persona "
                                "que la revise con --firma-humana.")
    if status == "bloqueada":
        evidence = [str(e).strip() for e in evidence if str(e).strip()] if evidence is not None else None
    else:
        evidence = _check_evidence(conn, evidence, own_id=opp_id)
    if leaving_block and not evidence:
        raise RegistryError(f"Desbloquear la compuerta {gate} de {opp_id} exige --evidencia: IDs del registro "
                            "(afirmaciones A-xxxx, fuentes doi:/pmid:/openalex:), URL o ficheros del proyecto con los "
                            "análisis que justifican levantar el bloqueo")
    with transaction(conn):
        conn.execute(
            "INSERT INTO gates (opportunity_id, gate, status, rationale, evidence, decided_by, updated_at, "
            "human_signoff, signed_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(opportunity_id, gate) DO UPDATE "
            "SET status=excluded.status, rationale=excluded.rationale, evidence=excluded.evidence, "
            "decided_by=excluded.decided_by, updated_at=excluded.updated_at, human_signoff=excluded.human_signoff, "
            "signed_at=excluded.signed_at",
            (opp_id, gate, status, rationale, _dumps(evidence), current_actor(actor), utc_now(), signed,
             utc_now() if signed else None),
        )
        kind = "decisión firmada" if signed else "propuesta"
        if not signed and current is not None and current["human_signoff"]:
            kind += f"; sustituye la decisión firmada por {current['human_signoff']}"
        _log_decision(conn, opp_id, f"compuerta {gate} -> {status} ({kind})", rationale, actor, signed)


def is_registry_id(item: str) -> bool:
    """¿Tiene `item` la forma de un identificador del registro (O-, H-, A-, E<n>, doi:, pmid:, openalex:)?"""
    import re
    return re.fullmatch(REGISTRY_ID_PATTERN, str(item).strip()) is not None


def _check_evidence(conn: sqlite3.Connection, evidence: list | None, own_id: str | None = None) -> list[str] | None:
    """Evidencia sin elementos vacíos; los IDs del registro citados deben existir y no pueden ser la propia entidad
    (`own_id`: una oportunidad no es evidencia de sus propias compuertas)."""
    if evidence is None:
        return None
    items = [str(e).strip() for e in evidence]
    if any(not e for e in items):
        raise RegistryError("La evidencia no puede tener elementos vacíos")
    if own_id and any(e.casefold() == own_id.strip().casefold() for e in items):
        raise RegistryError(f"{own_id} no es evidencia de su propia compuerta: cita las afirmaciones (A-xxxx), fuentes, "
                            "URL o ficheros con el análisis")
    missing = [e for e in items if is_registry_id(e) and not entity_exists(conn, e)]
    if missing:
        raise RegistryError(f"Evidencia que no está en el registro: {', '.join(missing)}")
    return items


def list_gates(conn: sqlite3.Connection, opp_id: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM gates", []
    if opp_id:
        sql += " WHERE opportunity_id = ?"
        args.append(opp_id)
    return [row_to_dict(r) for r in conn.execute(sql + " ORDER BY opportunity_id, gate", args)]


# ---------------------------------------------------------------------------- decisiones
def _log_decision(conn: sqlite3.Connection, entity_id: str | None, decision: str, rationale: str,
                  actor: str | None = None, human_signoff: str | None = None) -> None:
    conn.execute(
        "INSERT INTO decisions (ts, entity_id, decision, rationale, decided_by, human_signoff, context) "
        "VALUES (?,?,?,?,?,?,?)",
        (utc_now(), entity_id, decision, rationale, current_actor(actor), human_signoff, execution_context()),
    )


# Tablas con un identificador de texto que puede citar una decisión o la evidencia de una compuerta. Las opcionales
# (de otras partes del registro) se consultan solo si existen.
_ENTITY_TABLES = ("opportunities", "hypotheses", "claims", "sources")
_OPTIONAL_ENTITY_TABLES = ("extraction_comparisons",)


def entity_exists(conn: sqlite3.Connection, entity_id: str) -> bool:
    """¿Está `entity_id` en el registro? Oportunidades (O-), hipótesis (H-), afirmaciones (A-), fuentes (doi:, pmid:,
    openalex:), extracciones (E<n>) y, si existen esas tablas, otras entidades con identificador de texto."""
    import re
    entity_id = str(entity_id).strip()
    for table in _ENTITY_TABLES + _OPTIONAL_ENTITY_TABLES:
        try:
            if conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (entity_id,)).fetchone():
                return True
        except sqlite3.OperationalError:   # tabla opcional que este registro no tiene
            continue
    m = re.fullmatch(r"E-?(\d+)", entity_id)
    return bool(m and conn.execute("SELECT 1 FROM extractions WHERE id = ?", (int(m.group(1)),)).fetchone())


def add_decision(conn: sqlite3.Connection, entity_id: str | None, decision: str, rationale: str,
                 actor: str | None = None, human_signoff: str | None = None) -> None:
    """Registra una decisión (sin `entity_id`, una decisión general). La entidad, si se indica, debe existir."""
    if not (decision or "").strip():
        raise RegistryError("La decisión no puede estar vacía")
    if not (rationale or "").strip():
        raise RegistryError("Toda decisión necesita un motivo")
    require_human_signature(human_signoff)
    if entity_id is not None:
        if not entity_id.strip():
            raise RegistryError("--entidad vacía: indica un ID del registro u omítela para una decisión general")
        if not entity_exists(conn, entity_id):
            raise RegistryError(f"La entidad {entity_id} no existe en el registro (oportunidad, hipótesis, afirmación, "
                                "fuente o extracción): regístrala antes u omite --entidad para una decisión general")
        entity_id = entity_id.strip()
    with transaction(conn):
        _log_decision(conn, entity_id, decision, rationale, actor, human_signoff)


# ---------------------------------------------------------------------------- revisiones humanas de informes
# Tabla propia, creada al registrar la primera revisión (los registros anteriores no la tienen). Solo admite
# inserciones: una revisión no se cambia ni se borra; si el informe cambia, se registra otra.
_REPORT_REVIEWS_DDL = (
    "CREATE TABLE IF NOT EXISTS report_reviews ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " report TEXT NOT NULL, sha256 TEXT NOT NULL, reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL,"
    " status TEXT, note TEXT, entity_id TEXT, recorded_by TEXT)",
    "CREATE INDEX IF NOT EXISTS ix_report_reviews_sha256 ON report_reviews(sha256)",
    "CREATE TRIGGER IF NOT EXISTS report_reviews_no_update BEFORE UPDATE ON report_reviews BEGIN "
    "SELECT RAISE(ABORT, 'Las revisiones humanas de informes no se modifican: registra una nueva'); END",
    "CREATE TRIGGER IF NOT EXISTS report_reviews_no_delete BEFORE DELETE ON report_reviews BEGIN "
    "SELECT RAISE(ABORT, 'Las revisiones humanas de informes no se borran'); END",
)


def _ensure_report_reviews(conn: sqlite3.Connection) -> None:
    for statement in _REPORT_REVIEWS_DDL:
        conn.execute(statement)


def add_report_review(conn: sqlite3.Connection, report: str, digest: str, reviewer: str, status: str | None = None,
                      note: str | None = None, entity_id: str | None = None, actor: str | None = None) -> int:
    """Registra que una persona revisó el contenido exacto de un informe (huella `digest`, ver
    `nutria.reports.check.review_digest`). La firma la pone la persona con `nutria informe revisar`; es una
    atestación sin autenticar: se rechaza con un agente declarado, dentro de Claude Code (también si lo es un proceso
    antecesor) o sin terminal de control (`require_human_signature`), pero un proceso del mismo usuario que oculte
    esas señales podría llamar a esta función."""
    import os
    if actor or any(os.environ.get(name) for name in ("NUTRIA_AGENT", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")):
        raise RegistryError("La revisión humana de un informe no la registra un agente: la persona que lo revisó ejecuta "
                            "`nutria informe revisar` en su propia terminal")
    if not reviewer or not reviewer.strip():
        raise RegistryError("La revisión humana exige el nombre de la persona que revisó el informe")
    # Regla 12 dentro del propio registro, como en set_gate y classify_opportunity: además de las variables de este
    # proceso, los antecesores (Claude Code) y la terminal de control (petición de P1 y P2).
    require_human_signature(reviewer)
    from .reports.check import reviewer_name_problem      # aquí: check importa db
    problem = reviewer_name_problem(reviewer)
    if problem:
        raise RegistryError(f"No se registra la revisión: {problem}")
    if not digest or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise RegistryError("Huella del informe no válida")
    _ensure_report_reviews(conn)
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO report_reviews (report, sha256, reviewer, reviewed_at, status, note, entity_id, recorded_by) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (report, digest, reviewer.strip(), utc_now(), status, note, entity_id, current_actor(actor)))
        _log_decision(conn, entity_id, f"informe {report} revisado por una persona (estado: {status or 'sin estado'}; "
                                       f"sha256 {digest[:12]})", note or "Revisión humana del contenido exacto del informe",
                      actor, reviewer.strip())
        return int(cur.lastrowid)


def report_reviews(conn: sqlite3.Connection, digest: str | None = None, report: str | None = None) -> list[dict]:
    """Revisiones humanas registradas (por huella del contenido o por informe), de la más antigua a la última."""
    sql, args = "SELECT * FROM report_reviews WHERE 1=1", []
    if digest:
        sql += " AND sha256 = ?"
        args.append(digest)
    if report:
        sql += " AND report = ?"
        args.append(report)
    try:
        return [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]
    except sqlite3.OperationalError:   # registro antiguo sin la tabla: no hay revisiones
        return []


def list_decisions(conn: sqlite3.Connection, entity_id: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM decisions", []
    if entity_id:
        sql += " WHERE entity_id = ?"
        args.append(entity_id)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]


# ---------------------------------------------------------------------------- autoría inventiva
def add_inventive_contribution(conn: sqlite3.Connection, entity_id: str, person: str, contribution: str,
                               signed_by: str, actor: str | None = None) -> int:
    """Registra la aportación inventiva de una persona a una oportunidad o hipótesis (regla 11: la autoría de una
    invención debe ser humana). Exige la firma de una persona; la tabla es solo de inserción, y la decisión
    correspondiente queda en `decisions` (sin el texto de la aportación, que puede ser patentable)."""
    import re
    required = {"--persona": person, "--aportacion": contribution, "--firma-humana": signed_by}
    empty = [name for name, value in required.items() if not (value or "").strip()]
    if empty:
        raise RegistryError(f"Registrar la autoría inventiva exige {', '.join(empty)}: solo una persona puede "
                            "registrarla, en su terminal")
    require_human_signature(signed_by)
    entity_id = (entity_id or "").strip()
    if not re.fullmatch(r"[OH]-\d{4,}", entity_id) or not entity_exists(conn, entity_id):
        raise RegistryError(f"No existe la oportunidad o hipótesis '{entity_id}'")
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO inventive_contributions (entity_id, person, contribution, signed_by, signed_at, recorded_by, "
            "context) VALUES (?,?,?,?,?,?,?)",
            (entity_id, person.strip(), contribution.strip(), signed_by.strip(), utc_now(), current_actor(actor),
             execution_context()))
        _log_decision(conn, entity_id, f"autoría inventiva: {person.strip()}",
                      "Aportación inventiva humana registrada y firmada (detalle en `nutria autoria listar`)",
                      actor, signed_by.strip())
        return int(cur.lastrowid)


def list_inventive_contributions(conn: sqlite3.Connection, entity_id: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM inventive_contributions", []
    if entity_id:
        sql += " WHERE entity_id = ?"
        args.append(entity_id)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]


def summary(conn: sqlite3.Connection) -> dict:
    def count(sql: str) -> list[dict]:
        return [dict(r) for r in conn.execute(sql)]
    return {
        "fuentes": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
        "fuentes_verificadas": conn.execute("SELECT COUNT(*) FROM sources WHERE verified = 1").fetchone()[0],
        "fuentes_retractadas": conn.execute("SELECT COUNT(*) FROM sources WHERE retracted = 1").fetchone()[0],
        "extracciones": conn.execute("SELECT COUNT(*) FROM extractions").fetchone()[0],
        "extracciones_doble_verificadas": len(verified_extraction_ids(conn)),
        "comparaciones_sin_conciliar": conn.execute(
            "SELECT COUNT(*) FROM extraction_comparisons WHERE result = 'con_discrepancias' "
            "AND conciliation IS NULL").fetchone()[0],
        "afirmaciones": count("SELECT label, COUNT(*) AS n FROM claims GROUP BY label"),
        "oportunidades": count("SELECT status, COUNT(*) AS n FROM opportunities GROUP BY status"),
        "hipotesis": count("SELECT status, COUNT(*) AS n FROM hypotheses GROUP BY status"),
        "compuertas_sin_firmar": conn.execute(
            "SELECT COUNT(*) FROM gates WHERE status = 'superada' AND human_signoff IS NULL").fetchone()[0],
        "decisiones": conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
    }
