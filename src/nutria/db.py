"""Registro de investigación en SQLite: fuentes, extracciones, afirmaciones, hipótesis,
oportunidades, puntuaciones (con historial), compuertas (con firma humana), verificaciones
manuales y decisiones.

Diseñado para varios agentes concurrentes (WAL + busy_timeout) y para que nada se borre:
los cambios de estado quedan en `decisions` con su motivo, y las puntuaciones sobrescritas
se conservan en `scores_history`.

Reglas que el registro hace cumplir (no solo recomienda):
- una afirmación [PUB] exige fuentes registradas, verificadas y no retractadas, más cita textual y ubicación;
- una extracción exige que su fuente esté verificada;
- una compuerta "superada" solo cuenta si la firma una persona; salir de "bloqueada" exige firma;
- pasar una oportunidad a "priorizada" o "en_validacion" exige firma y compuertas en regla.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
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
    for key in ("authors", "pub_types", "source_ids", "candidates", "data", "evidence", "not_reported"):
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
    "ubicacion": "location", "financiacion": "funding", "conflictos": "conflicts",
    "estado_verificacion": "check_status", "verificado_por": "checked_by", "notas": "notes",
}


def add_extraction(conn: sqlite3.Connection, data: dict, actor: str | None = None) -> int:
    _require_usable_source(conn, data["fuente"], "extraer datos")
    row = {col: data.get(key) for key, col in EXTRACTION_FIELDS.items() if data.get(key) is not None}
    if data.get("no_consta"):
        row["not_reported"] = _dumps(data["no_consta"])
    row.update(extractor=current_actor(actor), extracted_at=utc_now())
    with transaction(conn):
        cur = conn.execute(
            f"INSERT INTO extractions ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
            list(row.values()),
        )
        return int(cur.lastrowid)


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


# ---------------------------------------------------------------------------- afirmaciones
def add_claim(conn: sqlite3.Connection, data: dict, actor: str | None = None) -> str:
    label = data["etiqueta"]
    sources = data.get("fuentes") or []
    if label == "PUB":
        if not sources:
            raise RegistryError("Una afirmación [PUB] necesita al menos una fuente registrada y verificada")
        if not (data.get("cita_textual") or "").strip() or not (data.get("ubicacion") or "").strip():
            raise RegistryError("Una afirmación [PUB] necesita la cita textual de la fuente y su ubicación "
                                "(tabla, figura o página) para poder comprobar el respaldo")
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
            "location, entity_id, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (claim_id, data["texto"], label, data.get("nivel"), data.get("transferibilidad"),
             _dumps(sources), data.get("cita_textual"), data.get("ubicacion"), data.get("entidad"),
             current_actor(actor), utc_now()),
        )
        return claim_id


def list_claims(conn: sqlite3.Connection, entity_id: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM claims", []
    if entity_id:
        sql += " WHERE entity_id = ?"
        args.append(entity_id)
    return [row_to_dict(r) for r in conn.execute(sql + " ORDER BY id", args)]


# ---------------------------------------------------------------------------- oportunidades
def add_opportunity(conn: sqlite3.Connection, data: dict, actor: str | None = None) -> str:
    with transaction(conn):
        opp_id = _next_id(conn, "opportunities", "O")
        now = utc_now()
        route = data.get("ruta_regulatoria_preliminar")
        conn.execute(
            "INSERT INTO opportunities (id, title, problem, species, production_stage, mechanism, "
            "candidates, product_type, regulatory_route, data, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (opp_id, data["titulo"], data.get("problema"), data.get("especie"), data.get("etapa"),
             data.get("mecanismo"), _dumps(data.get("candidatos")), data.get("tipo_producto"),
             route, _dumps(data), current_actor(actor), now, now),
        )
        _log_decision(conn, opp_id, "creada (propuesta)", data.get("justificacion") or "Alta inicial", actor)
        if route in OUT_OF_SCOPE_ROUTES:
            conn.execute(
                "INSERT INTO gates (opportunity_id, gate, status, rationale, decided_by, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (opp_id, "regulatoria", "bloqueada", f"Ruta regulatoria '{route}': fuera del alcance de un pienso",
                 "registro", now))
            _log_decision(conn, opp_id, "compuerta regulatoria -> bloqueada (automática)",
                          f"Ruta regulatoria '{route}'", actor)
        return opp_id


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
    atestación sin autenticar: se rechaza con un agente declarado o dentro de Claude Code, pero un proceso del mismo
    usuario que oculte esas señales podría llamar a esta función."""
    import os
    if actor or any(os.environ.get(name) for name in ("NUTRIA_AGENT", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")):
        raise RegistryError("La revisión humana de un informe no la registra un agente: la persona que lo revisó ejecuta "
                            "`nutria informe revisar` en su propia terminal")
    if not reviewer or not reviewer.strip():
        raise RegistryError("La revisión humana exige el nombre de la persona que revisó el informe")
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


def summary(conn: sqlite3.Connection) -> dict:
    def count(sql: str) -> list[dict]:
        return [dict(r) for r in conn.execute(sql)]
    return {
        "fuentes": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
        "fuentes_verificadas": conn.execute("SELECT COUNT(*) FROM sources WHERE verified = 1").fetchone()[0],
        "fuentes_retractadas": conn.execute("SELECT COUNT(*) FROM sources WHERE retracted = 1").fetchone()[0],
        "extracciones": conn.execute("SELECT COUNT(*) FROM extractions").fetchone()[0],
        "extracciones_doble_verificadas": conn.execute(
            "SELECT COUNT(*) FROM extractions WHERE check_status IN ('doble_concordante','resuelta')").fetchone()[0],
        "afirmaciones": count("SELECT label, COUNT(*) AS n FROM claims GROUP BY label"),
        "oportunidades": count("SELECT status, COUNT(*) AS n FROM opportunities GROUP BY status"),
        "hipotesis": count("SELECT status, COUNT(*) AS n FROM hypotheses GROUP BY status"),
        "compuertas_sin_firmar": conn.execute(
            "SELECT COUNT(*) FROM gates WHERE status = 'superada' AND human_signoff IS NULL").fetchone()[0],
        "decisiones": conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
    }
