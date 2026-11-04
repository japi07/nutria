"""Crossref REST: metadatos autoritativos por DOI y búsqueda bibliográfica de DOIs.

Retractaciones: Crossref incluye en su API las de sus miembros y las de Retraction Watch en el campo
`update-to` (cada entrada con DOI, tipo y fuente). `parse` marca `retracted=True` si el propio registro
trae en `update-to` o en `updated-by` una entrada de tipo `retraction` o `withdrawal` (sin distinguir
mayúsculas), o si el título lo declara. Si no hay información, `retracted` queda en None (desconocido),
nunca en False: que Crossref no traiga aviso no demuestra que el trabajo no esté retractado.
"""

from __future__ import annotations

from urllib.parse import quote

from .base import Record

BASE = "https://api.crossref.org"

# Tipos de actualización de Crossref que equivalen a retractar el trabajo (sin distinguir mayúsculas).
RETRACTION_UPDATE_TYPES = frozenset({"retraction", "withdrawal"})
_RETRACTED_TITLE_PREFIXES = ("RETRACTED", "RETRACTION:")


def _params(cfg, extra: dict | None = None) -> dict:
    params = dict(extra or {})
    if cfg.email:
        params["mailto"] = cfg.email
    return params


def _first(value) -> str | None:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _entries(value) -> list:
    if isinstance(value, dict):
        return [value]
    return value if isinstance(value, list) else []


def retraction_updates(msg: dict) -> list[dict]:
    """Entradas de `updated-by` y `update-to` del propio registro de tipo retracción o retirada."""
    found = []
    for field_name in ("updated-by", "update-to"):
        for entry in _entries(msg.get(field_name)):
            if not isinstance(entry, dict):
                continue
            if str(entry.get("type", "")).strip().lower() in RETRACTION_UPDATE_TYPES:
                found.append({"field": field_name, "type": entry.get("type"), "doi": entry.get("DOI"),
                              "source": entry.get("source")})
    return found


def parse(msg: dict) -> Record:
    authors = []
    for a in msg.get("author", []) or []:
        name = " ".join(x for x in (a.get("family"), a.get("given")) if x) or a.get("name")
        if name:
            authors.append(name)
    year = None
    for key in ("issued", "published-print", "published-online", "created"):
        parts = (msg.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = int(parts[0][0])
            break
    title = _first(msg.get("title"))
    updates = retraction_updates(msg)
    retracted_by_title = bool(title and str(title).strip().upper().startswith(_RETRACTED_TITLE_PREFIXES))
    pub_types = [t for t in [msg.get("type")] if t]
    return Record(
        source="crossref", title=title, authors=authors, year=year,
        journal=_first(msg.get("container-title")), volume=msg.get("volume"), issue=msg.get("issue"),
        pages=msg.get("page"), doi=msg.get("DOI"), pub_types=pub_types, url=msg.get("URL"),
        cited_by=msg.get("is-referenced-by-count"),
        retracted=True if (updates or retracted_by_title) else None,
        extra={"publisher": msg.get("publisher"), "update_to": _entries(msg.get("update-to")),
               "updated_by": _entries(msg.get("updated-by")), "retraction_updates": updates},
    )


def work(client, doi: str) -> Record:
    data = client.get_json(f"{BASE}/works/{quote(doi, safe='/')}", params=_params(client.cfg))
    return parse(data.get("message", {}))


def search_bibliographic(client, citation: str, rows: int = 5) -> list[dict]:
    """Encuentra DOIs candidatos para una cita en texto libre (devuelve puntuación de Crossref)."""
    data = client.get_json(f"{BASE}/works", params=_params(client.cfg, {"query.bibliographic": citation,
                                                                         "rows": rows}))
    items = (data.get("message") or {}).get("items", [])
    return [{"score": it.get("score"), "record": parse(it)} for it in items]
