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
    # Licencia del artículo o del dato según la API (Crossref, OpenAlex, DataCite): URL o código (cc-by...).
    "sources": [("license", "TEXT")],
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
    from .verify.citations import titles_same_work, translation_of
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
    titles = [t for t in [fields.get("title"), *(extra.get("title_variants") or [])] if t]
    problems = []
    if not row["title"]:
        if _primary_field(row) != found_by:
            problems.append(f"{row['id']} no tiene título con el que comparar")
    elif not titles:
        problems.append("el registro verificado no trae título con el que comparar")
    elif not any(titles_same_work(t, row["title"]) for t in titles):
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
    from .verify.citations import titles_same_work
    via = record.get("verified_via")
    problems = []
    if by_doi["pmid"] and by_doi["pmid"] != fields["pmid"]:
        problems.append(f"{by_doi['id']} tiene el PMID {by_doi['pmid']}")
    if by_pmid["doi"] and by_pmid["doi"] != fields["doi"]:
        problems.append(f"{by_pmid['id']} tiene el DOI {by_pmid['doi']}")
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
    titles = [t for t in [fields.get("title"), *(extra.get("title_variants") or [])] if t]
    for row, own_id in ((by_doi, f"doi:{fields['doi']}"), (by_pmid, f"pmid:{fields['pmid']}")):
        if not row["title"]:
            if row["id"] != own_id:
                problems.append(f"{row['id']} no tiene título con el que comparar y no se llama {own_id}")
        elif not titles:
            problems.append("el registro verificado no trae título con el que comparar")
        elif not any(titles_same_work(t, row["title"]) for t in titles):
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
