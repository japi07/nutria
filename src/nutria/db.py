"""Registro de investigación en SQLite: fuentes, extracciones, afirmaciones, hipótesis,
oportunidades, puntuaciones, compuertas y decisiones.

Diseñado para varios agentes concurrentes (WAL + busy_timeout) y para que nada se
borre: los cambios de estado quedan en la tabla `decisions` con su motivo.
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

CREATE TABLE IF NOT EXISTS gates (
  opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
  gate TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('superada','bloqueada','no_resuelta')),
  rationale TEXT, evidence TEXT, decided_by TEXT, updated_at TEXT,
  PRIMARY KEY (opportunity_id, gate)
);

CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, entity_id TEXT, decision TEXT NOT NULL, rationale TEXT NOT NULL,
  decided_by TEXT, human_signoff TEXT
);
"""

OPPORTUNITY_STATUSES = ("propuesta", "en_evaluacion", "priorizada", "bloqueada", "descartada",
                        "en_validacion")
HYPOTHESIS_STATUSES = ("propuesta", "activa", "en_revision", "refutada", "descartada", "promovida")
GATE_STATUSES = ("superada", "bloqueada", "no_resuelta")


class RegistryError(ValueError):
    """Error de integridad del registro (estado inválido, entidad inexistente...)."""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1')")
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('created_at', ?)", (utc_now(),))
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
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
    for key in ("authors", "pub_types", "source_ids", "candidates", "data", "evidence"):
        if isinstance(out.get(key), str):
            try:
                out[key] = json.loads(out[key])
            except ValueError:
                pass
    return out


# ---------------------------------------------------------------------------- fuentes
def upsert_source(conn: sqlite3.Connection, record: dict, actor: str | None = None) -> str:
    """Inserta o fusiona una fuente identificada por DOI, PMID u OpenAlex."""
    doi = (record.get("doi") or "").lower() or None
    pmid = record.get("pmid") or None
    existing = None
    with transaction(conn):
        if doi:
            existing = conn.execute("SELECT * FROM sources WHERE doi = ?", (doi,)).fetchone()
        if existing is None and pmid:
            existing = conn.execute("SELECT * FROM sources WHERE pmid = ?", (pmid,)).fetchone()
        fields = {
            "doi": doi, "pmid": pmid, "pmcid": record.get("pmcid"),
            "openalex_id": record.get("openalex_id"), "title": record.get("title"),
            "authors": _dumps(record.get("authors")), "year": record.get("year"),
            "journal": record.get("journal"), "volume": record.get("volume"),
            "issue": record.get("issue"), "pages": record.get("pages"),
            "pub_types": _dumps(record.get("pub_types")), "study_type": record.get("study_type"),
            "species": record.get("species"), "url": record.get("url"),
            "abstract": record.get("abstract"), "notes": record.get("notes"),
        }
        if record.get("retracted") is not None:
            fields["retracted"] = int(bool(record["retracted"]))
        if record.get("verified"):
            fields.update(verified=1, verified_at=utc_now(), verified_via=record.get("verified_via"))
        if existing is None:
            source_id = f"doi:{doi}" if doi else (f"pmid:{pmid}" if pmid else
                                                   f"openalex:{record.get('openalex_id')}")
            if source_id.endswith(":None"):
                raise RegistryError("La fuente necesita DOI, PMID u OpenAlex ID")
            fields.update(id=source_id, added_at=utc_now(), added_by=current_actor(actor))
            cols = [k for k, v in fields.items() if v is not None]
            conn.execute(
                f"INSERT INTO sources ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [fields[c] for c in cols],
            )
            return source_id
        updates = {k: v for k, v in fields.items() if v is not None}
        if updates:
            conn.execute(
                f"UPDATE sources SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?",
                [*updates.values(), existing["id"]],
            )
        return existing["id"]


def get_source(conn: sqlite3.Connection, source_id: str) -> dict | None:
    return row_to_dict(conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone())


def list_sources(conn: sqlite3.Connection, unverified_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM sources"
    if unverified_only:
        sql += " WHERE verified = 0"
    return [row_to_dict(r) for r in conn.execute(sql + " ORDER BY added_at")]


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
    "ubicacion": "location", "financiacion": "funding", "conflictos": "conflicts",
    "estado_verificacion": "check_status", "verificado_por": "checked_by", "notas": "notes",
}


def add_extraction(conn: sqlite3.Connection, data: dict, actor: str | None = None) -> int:
    if get_source(conn, data["fuente"]) is None:
        raise RegistryError(f"La fuente {data['fuente']} no está registrada; regístrala antes de extraer datos")
    row = {col: data.get(key) for key, col in EXTRACTION_FIELDS.items() if data.get(key) is not None}
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
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]


# ---------------------------------------------------------------------------- afirmaciones
def add_claim(conn: sqlite3.Connection, data: dict, actor: str | None = None) -> str:
    label = data["etiqueta"]
    sources = data.get("fuentes") or []
    if label == "PUB" and not sources:
        raise RegistryError("Una afirmación [PUB] necesita al menos una fuente registrada")
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
        conn.execute(
            "INSERT INTO opportunities (id, title, problem, species, production_stage, mechanism, "
            "candidates, product_type, regulatory_route, data, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (opp_id, data["titulo"], data.get("problema"), data.get("especie"), data.get("etapa"),
             data.get("mecanismo"), _dumps(data.get("candidatos")), data.get("tipo_producto"),
             data.get("ruta_regulatoria_preliminar"), _dumps(data), current_actor(actor), now, now),
        )
        _log_decision(conn, opp_id, "creada (propuesta)", data.get("justificacion") or "Alta inicial", actor)
        return opp_id


def get_opportunity(conn: sqlite3.Connection, opp_id: str) -> dict | None:
    return row_to_dict(conn.execute("SELECT * FROM opportunities WHERE id = ?", (opp_id,)).fetchone())


def list_opportunities(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM opportunities", []
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    return [row_to_dict(r) for r in conn.execute(sql + " ORDER BY id", args)]


def set_opportunity_status(conn: sqlite3.Connection, opp_id: str, status: str, reason: str,
                           actor: str | None = None, human_signoff: str | None = None) -> None:
    if status not in OPPORTUNITY_STATUSES:
        raise RegistryError(f"Estado no válido: {status}. Opciones: {', '.join(OPPORTUNITY_STATUSES)}")
    if not reason.strip():
        raise RegistryError("Todo cambio de estado necesita un motivo")
    with transaction(conn):
        if conn.execute("SELECT 1 FROM opportunities WHERE id = ?", (opp_id,)).fetchone() is None:
            raise RegistryError(f"No existe la oportunidad {opp_id}")
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
                          actor: str | None = None, human_signoff: str | None = None) -> None:
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
             valid_gates: set[str], evidence: list | None = None, actor: str | None = None) -> None:
    if gate not in valid_gates:
        raise RegistryError(f"Compuerta desconocida: {gate}. Válidas: {', '.join(sorted(valid_gates))}")
    if status not in GATE_STATUSES:
        raise RegistryError(f"Estado de compuerta no válido: {status}")
    if not rationale.strip():
        raise RegistryError("La decisión de compuerta necesita un motivo")
    if get_opportunity(conn, opp_id) is None:
        raise RegistryError(f"No existe la oportunidad {opp_id}")
    with transaction(conn):
        conn.execute(
            "INSERT INTO gates (opportunity_id, gate, status, rationale, evidence, decided_by, updated_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(opportunity_id, gate) DO UPDATE SET status=excluded.status, "
            "rationale=excluded.rationale, evidence=excluded.evidence, decided_by=excluded.decided_by, "
            "updated_at=excluded.updated_at",
            (opp_id, gate, status, rationale, _dumps(evidence), current_actor(actor), utc_now()),
        )
        _log_decision(conn, opp_id, f"compuerta {gate} -> {status}", rationale, actor)


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
        "extracciones_doble_verificadas": conn.execute(
            "SELECT COUNT(*) FROM extractions WHERE check_status IN ('doble_concordante','resuelta')").fetchone()[0],
        "afirmaciones": count("SELECT label, COUNT(*) AS n FROM claims GROUP BY label"),
        "oportunidades": count("SELECT status, COUNT(*) AS n FROM opportunities GROUP BY status"),
        "hipotesis": count("SELECT status, COUNT(*) AS n FROM hypotheses GROUP BY status"),
        "decisiones": conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
    }
