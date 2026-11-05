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

from .provenance import current_actor, utc_now

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
                  "issue", "pages", "pub_types", "study_type", "species", "url", "abstract", "notes")
_BIBLIO_FIELDS = ("title", "authors", "year", "journal", "volume", "issue", "pages", "pub_types")


def _merge_duplicate_sources(conn: sqlite3.Connection, keep: sqlite3.Row, drop: sqlite3.Row,
                             actor: str | None) -> None:
    """Fusiona dos registros del mismo trabajo (uno con DOI y otro con PMID) en `keep`."""
    keep_id, drop_id = keep["id"], drop["id"]
    conn.execute("UPDATE extractions SET source_id = ? WHERE source_id = ?", (keep_id, drop_id))
    for claim in conn.execute("SELECT id, source_ids FROM claims WHERE source_ids LIKE ?", (f'%"{drop_id}"%',)).fetchall():
        ids = [keep_id if s == drop_id else s for s in json.loads(claim["source_ids"] or "[]")]
        conn.execute("UPDATE claims SET source_ids = ? WHERE id = ?", (_dumps(list(dict.fromkeys(ids))), claim["id"]))
    retracted = max(int(keep["retracted"] or 0), int(drop["retracted"] or 0)) if (
        keep["retracted"] is not None or drop["retracted"] is not None) else None
    pmid = keep["pmid"] or drop["pmid"]
    doi = keep["doi"] or drop["doi"]
    conn.execute("DELETE FROM sources WHERE id = ?", (drop_id,))
    conn.execute("UPDATE sources SET pmid = ?, doi = ?, retracted = ? WHERE id = ?", (pmid, doi, retracted, keep_id))
    _log_decision(conn, keep_id, f"fusión de fuentes duplicadas ({drop_id} -> {keep_id})",
                  "El mismo trabajo estaba registrado por DOI y por PMID", actor)


def upsert_source(conn: sqlite3.Connection, record: dict, actor: str | None = None) -> str:
    """Inserta o fusiona una fuente identificada por DOI, PMID u OpenAlex.

    - Un registro sin verificar nunca sobrescribe los metadatos de uno verificado (solo rellena huecos).
    - La marca de retractación nunca se borra: una vez retractado, sigue retractado.
    - Si el mismo trabajo existe dos veces (por DOI y por PMID), se fusionan.
    """
    doi = (record.get("doi") or "").lower() or None
    pmid = str(record["pmid"]) if record.get("pmid") else None
    incoming_verified = bool(record.get("verified"))
    with transaction(conn):
        by_doi = conn.execute("SELECT * FROM sources WHERE doi = ?", (doi,)).fetchone() if doi else None
        by_pmid = conn.execute("SELECT * FROM sources WHERE pmid = ?", (pmid,)).fetchone() if pmid else None
        if by_doi is not None and by_pmid is not None and by_doi["id"] != by_pmid["id"]:
            _merge_duplicate_sources(conn, by_doi, by_pmid, actor)
            by_pmid = None
        existing = by_doi or by_pmid
        if existing is not None:
            existing = conn.execute("SELECT * FROM sources WHERE id = ?", (existing["id"],)).fetchone()
        fields = {k: record.get(k) for k in _SOURCE_FIELDS}
        fields.update(doi=doi, pmid=pmid, authors=_dumps(record.get("authors")),
                      pub_types=_dumps(record.get("pub_types")))
        if existing is None:
            source_id = f"doi:{doi}" if doi else (f"pmid:{pmid}" if pmid else
                                                   (f"openalex:{record['openalex_id']}" if record.get("openalex_id") else None))
            if source_id is None:
                raise RegistryError("La fuente necesita DOI, PMID u OpenAlex ID")
            row = {k: v for k, v in fields.items() if v is not None}
            if record.get("retracted") is not None:
                row["retracted"] = int(bool(record["retracted"]))
            if incoming_verified:
                row.update(verified=1, verified_at=utc_now(), verified_via=record.get("verified_via"))
            row.update(id=source_id, added_at=utc_now(), added_by=current_actor(actor))
            conn.execute(f"INSERT INTO sources ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                         list(row.values()))
            return source_id
        updates: dict[str, Any] = {}
        protected = bool(existing["verified"]) and not incoming_verified
        for key, value in fields.items():
            if value is None:
                continue
            if protected and key in _BIBLIO_FIELDS and existing[key] is not None:
                continue  # no se pisan metadatos verificados con datos sin verificar
            updates[key] = value
        if record.get("retracted") is not None:
            new_flag = int(bool(record["retracted"]))
            old_flag = existing["retracted"]
            # Una retractación conocida nunca se borra; "no retractado" solo se acepta de una verificación.
            if new_flag == 1 or old_flag is None or (incoming_verified and old_flag != 1):
                updates["retracted"] = max(new_flag, int(old_flag or 0)) if old_flag is not None else new_flag
        if incoming_verified:
            updates.update(verified=1, verified_at=utc_now(), verified_via=record.get("verified_via"))
        if updates:
            conn.execute(f"UPDATE sources SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?",
                         [*updates.values(), existing["id"]])
        return existing["id"]


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
tod{o,a,os,as}|vari{os,as}|divers{o,a,os,as}|distint{o,a,os,as}|diferente{,s}|multiple{,s}|cualquier{,a}|cualesquiera
ambos|ambas|otr{o,a,os,as}|demas|resto|general{,es,mente}|generic{o,a,os,as}|mixt{o,a,os,as}|indistint{o,a,os,as}
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
"""
# Combinaciones genéricas de palabras que, solas, pueden ser concretas ('fase de producción' en ponedoras, 'cría').
_GENERIC_PHRASE_TEMPLATES = """
especie{,s}|animal{,es}|ganado{,s}|fases|etapas|estados de|en produccion|granja|abasto|renta|cria
cualquier|toda|todas|varias|distintas|diferentes ?la|las fase{,s}|etapa{,s} de|del ?la produccion|ciclo|crecimiento|cria|vida
produccion animal
food producing
foodproducing
farm animal{,s}
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
    generic_compact = frozenset(c for c in ("".join(t) for t in generic) if len(c) >= 3)
    return {"filler": fillers, "generic": generic, "descriptor": descriptors,
            "filler_compact": frozenset("".join(t) for t in fillers), "generic_compact": generic_compact,
            "max_len": max(len(t) for t in descriptors), "compact_max": max(map(len, generic_compact))}


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
        if (_covered(words, vocab["generic"], vocab["max_len"])
                or _compact_covered("".join(words), vocab["generic_compact"], vocab["compact_max"])):
            return "es genérico: no nombra una especie, una etapa ni un estado concretos"
        if mode == "especie" and _covered(words, vocab["descriptor"], vocab["max_len"]):
            return "no nombra la especie: solo describe a los animales"
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
    for e in tree:
        msg = e.schema.get("x-mensaje") if isinstance(e.schema, dict) else None
        if msg:
            if isinstance(e.instance, (dict, list)):
                return f"{msg} ({err.message})" if err.validator == "required" else msg
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
    r"|perdidas? (?:de (?:la )?)?gestacion|concepcion|prenez|fertilidad|descartes?|bajas"
    r"|tasa de (?:gestacion|partos?|reposicion|curacion|recidivas?|eliminacion))\b")
INCIDENCE_UNIT_RE = re.compile(
    r"%|\b(?:porcentaje|proporcion|fraccion|casos?|animales|vacas|novillas|terneros|cerdas|lechones|aves|incidencia"
    r"|prevalencia|riesgo|tasa|n|numero|frecuencia|eventos?)\b|/ ?100\b|por (?:100|cada)\b")
PER_TIME_UNIT_RE = re.compile(r"/ ?(?:h|hora|d|dia|min)\b|por (?:hora|dia|minuto)\b")
# Porcentajes de composición o de tiempo ('% del peso fresco', '% de la MS', '% del tiempo'): no son proporciones de
# animales con un evento.
COMPOSITION_UNIT_RE = re.compile(r"\b(?:ms|mo|pb|pv|peso|materia|ingesta|dieta|racion|energia|grasa|proteina|leche"
                                 r"|higado|tejido|fresco|seco|plasma|sangre|suero|tiempo|duracion|horas?|minutos?)\b")


def incidence_like(outcome: Any, unit: Any) -> bool:
    """Variable de incidencia o de un trastorno expresada como proporción de animales o recuento (no como velocidad
    por tiempo ni como porcentaje de composición)."""
    unit_text = plain_text(unit)
    return bool(INCIDENCE_OUTCOME_RE.search(plain_text(outcome)) and INCIDENCE_UNIT_RE.search(unit_text)
                and not PER_TIME_UNIT_RE.search(unit_text) and not COMPOSITION_UNIT_RE.search(unit_text))


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
    registrada concordante (o conciliada) y 'resuelta' con una conciliación registrada. Un estado declarado sin
    esa comparación (p. ej. de una versión anterior) no cuenta."""
    rows = conn.execute(
        "SELECT e.id FROM extractions e JOIN extraction_comparisons c ON c.id = e.comparison_id "
        "WHERE (e.check_status = 'doble_concordante' AND (c.result = 'concordante' OR c.conciliation IS NOT NULL)) "
        "OR (e.check_status = 'resuelta' AND c.conciliation IS NOT NULL)").fetchall()
    return {int(r[0]) for r in rows}


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
    for row in conn.execute(
            "SELECT c.id, c.actor_a, c.actor_b, c.compared_by, c.conciliated_by, ca.source_ids AS sa, "
            "cb.source_ids AS sb FROM extraction_comparisons c JOIN extraction_copies ca ON ca.id = c.copy_a "
            "JOIN extraction_copies cb ON cb.id = c.copy_b WHERE c.result = 'con_discrepancias' "
            "ORDER BY c.compared_at"):
        involved = set(json.loads(row["sa"] or "[]")) | set(json.loads(row["sb"] or "[]"))
        for sid in sources:
            if sid not in involved:
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


def deposited_shas(conn: sqlite3.Connection, shas: list[str]) -> set[str]:
    """Cuáles de esas huellas sha256 son copias de doble extracción depositadas."""
    return {r["sha256"] for r in conn.execute(
        f"SELECT DISTINCT sha256 FROM extraction_copies WHERE sha256 IN ({', '.join('?' for _ in shas)})",
        list(shas))} if shas else set()


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
        for row in conn.execute("SELECT id, sha256, source_ids, deposited_by FROM extraction_copies ORDER BY id"):
            if actor_key(row["deposited_by"]) != key or row["id"] in used_ids or row["sha256"] in used_shas:
                continue
            shared = sorted(set(json.loads(row["source_ids"] or "[]")) & set(sources))
            if shared:
                raise RegistryError(
                    f"'{who}' ya depositó la copia #{row['id']} con registros de {', '.join(shared)}, aún sin comparar: "
                    "una copia depositada es definitiva y no se sustituye por otra corregida. Compárala con la del otro "
                    "extractor (`nutria extraccion comparar A.json B.json --registrar`) y, si hay discrepancias, "
                    "concíliala releyendo la fuente (`nutria extraccion conciliar`)")
        cur = conn.execute(
            "INSERT INTO extraction_copies (sha256, path, records, n_records, source_ids, deposited_by, deposited_at) "
            "VALUES (?,?,?,?,?,?,?)", (sha256, path, _dumps(records), len(records), _dumps(sources), who, utc_now()))
        return {"id": int(cur.lastrowid), "sha256": sha256, "registros": len(records), "extractor": who, "nueva": True}


def _pending_comparisons(conn: sqlite3.Connection, sources: list[str]) -> list[tuple[str, str]]:
    """Comparaciones con discrepancias sin conciliar que afectan a alguna de las fuentes: [(CE-xxxx, fuente)]."""
    out = []
    for row in conn.execute(
            "SELECT c.id, ca.source_ids AS sa, cb.source_ids AS sb FROM extraction_comparisons c "
            "JOIN extraction_copies ca ON ca.id = c.copy_a JOIN extraction_copies cb ON cb.id = c.copy_b "
            "WHERE c.result = 'con_discrepancias' AND c.conciliation IS NULL ORDER BY c.compared_at"):
        involved = set(json.loads(row["sa"] or "[]")) | set(json.loads(row["sb"] or "[]"))
        out += [(row["id"], sid) for sid in sources if sid in involved]
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
                        "compara sin mayúsculas, tildes ni signos): la doble extracción exige dos extractores distintos "
                        "que trabajen de forma independiente")


def register_extraction_comparison(conn: sqlite3.Connection, sha_a: str, sha_b: str, actor: str | None = None,
                                   file_a: str | None = None, file_b: str | None = None) -> dict:
    """Registra la comparación de dos copias depositadas. Sin discrepancias, registra los registros como
    'doble_concordante'; con discrepancias, solo la comparación (se concilia con `conciliate_extraction_comparison`)."""
    who = current_actor(actor)
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
        warning = ("las dos copias son idénticas byte a byte: compruébese que se extrajeron de forma independiente"
                   if sha_a == sha_b else None)
        _log_decision(conn, comp_id, f"doble extracción comparada: {result}",
                      f"A: {file_a or copy_a['path']} ({copy_a['deposited_by']}, sha256 {sha_a[:12]}); "
                      f"B: {file_b or copy_b['path']} ({copy_b['deposited_by']}, sha256 {sha_b[:12]}); "
                      f"{len(diffs)} discrepancia(s); registradas: {ids or 'ninguna'}"
                      + (f"; AVISO: {warning}" if warning else ""), actor)
        return {"id": comp_id, "resultado": result, "discrepancias": diffs, "ids": ids,
                "extractor_a": copy_a["deposited_by"], "extractor_b": copy_b["deposited_by"],
                "sha256_a": sha_a, "sha256_b": sha_b, "aviso": warning}


def _apply_resolutions(x: dict, y: dict, resolutions: list[dict], where: str) -> tuple[dict, str, list[str]]:
    """Construye el registro conciliado: parte de la copia A y aplica solo las resoluciones declaradas."""
    problems: list[str] = []
    by_field: dict[str, dict] = {}
    for res in resolutions:
        field = res["campo"]
        if field in by_field:
            problems.append(f"{where}: dos resoluciones para '{field}'")
        elif field in COMPARE_IGNORED or (field not in EXTRACTION_FIELDS and field != "no_consta"):
            problems.append(f"{where}: '{field}' no es un campo conciliable del esquema de extracción")
        by_field[field] = res
    record = {k: v for k, v in x.items() if k not in EXTRACTION_SYSTEM_FIELDS}
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
            value = va if origin == "A" else vb
        else:
            value = res.get("valor")
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
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
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
                      + (f"; fichero sha256 {sha256[:12]}" if sha256 else ""), actor)
        return {"id": comparison_id, "ids": ids, "registros": detail, "no_resueltos": unresolved}


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
    elif data["transferibilidad"] == "especie_diana" and ctx.get("especie") == "no_consta":
        problems.append("con transferibilidad 'especie_diana' hay que nombrar la especie del estudio")
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
    with transaction(conn):
        if conn.execute("SELECT 1 FROM hypotheses WHERE id = ?", (hyp_id,)).fetchone() is None:
            raise RegistryError(f"No existe la hipótesis {hyp_id}")
        conn.execute("UPDATE hypotheses SET status = ?, status_reason = ?, updated_at = ? WHERE id = ?",
                     (status, reason, utc_now(), hyp_id))
        _log_decision(conn, hyp_id, f"estado -> {status}", reason, actor, human_signoff)


# ---------------------------------------------------------------------------- puntuación y compuertas
def add_scores(conn: sqlite3.Connection, opp_id: str, rater: str, items: list[dict],
               valid_criteria: set[str]) -> int:
    if get_opportunity(conn, opp_id) is None:
        raise RegistryError(f"No existe la oportunidad {opp_id}")
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
                     previous["evidence"], previous["created_at"], utc_now(), current_actor(None)))
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

    - "bloqueada" actúa de inmediato aunque no esté firmada (criterio de prudencia);
    - "superada" sin firma queda como propuesta y no cuenta como superada;
    - salir de "bloqueada" exige firma humana.
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
    current = conn.execute("SELECT * FROM gates WHERE opportunity_id = ? AND gate = ?", (opp_id, gate)).fetchone()
    if current is not None and current["status"] == "bloqueada" and status != "bloqueada" and not signed:
        raise RegistryError(f"La compuerta {gate} de {opp_id} está bloqueada: desbloquearla exige --firma-humana")
    if gate == "regulatoria" and status != "bloqueada" and opp.get("regulatory_route") in OUT_OF_SCOPE_ROUTES \
            and not signed:
        raise RegistryError(f"{opp_id} tiene la ruta '{opp['regulatory_route']}': la compuerta regulatoria solo "
                            "puede cambiarse con --firma-humana (y conviene corregir antes la ruta)")
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
        _log_decision(conn, opp_id, f"compuerta {gate} -> {status} ({kind})", rationale, actor, signed)


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
        "INSERT INTO decisions (ts, entity_id, decision, rationale, decided_by, human_signoff) "
        "VALUES (?,?,?,?,?,?)",
        (utc_now(), entity_id, decision, rationale, current_actor(actor), human_signoff),
    )


def add_decision(conn: sqlite3.Connection, entity_id: str | None, decision: str, rationale: str,
                 actor: str | None = None, human_signoff: str | None = None) -> None:
    if not rationale.strip():
        raise RegistryError("Toda decisión necesita un motivo")
    with transaction(conn):
        _log_decision(conn, entity_id, decision, rationale, actor, human_signoff)


def list_decisions(conn: sqlite3.Connection, entity_id: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM decisions", []
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
