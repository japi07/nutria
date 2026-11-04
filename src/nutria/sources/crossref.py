"""Crossref REST: metadatos autoritativos por DOI y búsqueda bibliográfica de DOIs."""

from __future__ import annotations

from urllib.parse import quote

from .base import Record

BASE = "https://api.crossref.org"


def _params(cfg, extra: dict | None = None) -> dict:
    params = dict(extra or {})
    if cfg.email:
        params["mailto"] = cfg.email
    return params


def _first(value) -> str | None:
    if isinstance(value, list):
        return value[0] if value else None
    return value


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
    updates = msg.get("update-to") or []
    is_notice = any("retract" in str(u.get("type", "")).lower() for u in updates)
    retracted = bool(title and title.strip().upper().startswith(("RETRACTED", "RETRACTION:")))
    pub_types = [t for t in [msg.get("type")] if t]
    if is_notice:
        pub_types.append("retraction-notice")
    return Record(
        source="crossref", title=title, authors=authors, year=year,
        journal=_first(msg.get("container-title")), volume=msg.get("volume"), issue=msg.get("issue"),
        pages=msg.get("page"), doi=msg.get("DOI"), pub_types=pub_types, url=msg.get("URL"),
        cited_by=msg.get("is-referenced-by-count"),
        retracted=True if retracted else None,
        extra={"publisher": msg.get("publisher"), "update_to": updates},
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
