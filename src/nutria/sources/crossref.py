"""Crossref REST: metadatos autoritativos por DOI y búsqueda bibliográfica de DOIs.

Retractaciones: Crossref incluye en su API las de sus miembros y las de Retraction Watch en el campo
`update-to` (cada entrada con DOI, tipo y fuente). `parse` marca `retracted=True` si el propio registro
trae en `update-to` o en `updated-by` una entrada de tipo `retraction` o `withdrawal` (sin distinguir
mayúsculas), o si el título lo declara. Si no hay información, `retracted` queda en None (desconocido),
nunca en False: que Crossref no traiga aviso no demuestra que el trabajo no esté retractado.

Licencia: el campo `license` de Crossref es una lista de URL con su `content-version` (`vor`, versión publicada;
`am`, manuscrito aceptado; `tdm`, minería de textos; `unspecified`) y su `delay-in-days` (embargo). `extra["license"]`
guarda la de la versión publicada si existe (si no, la del manuscrito aceptado y la no especificada, indicándolo entre
paréntesis, igual que un embargo: "<URL> (manuscrito aceptado; embargo: 365 días)"); una licencia solo de minería de
textos (`tdm`) no es la del artículo y no se toma como tal. `extra["licenses"]` guarda todas.
Para comprobar la cita, `extra` guarda además los apellidos de los autores (`families`), los subtítulos, los demás
títulos del registro y el título original (`alternative_titles`: revistas bilingües y traducciones) y los títulos de
la revista (completos y abreviados).
"""

from __future__ import annotations

from urllib.parse import quote

from .base import Record, full_title

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


_LICENSE_PREFERENCE = ("vor", "am", "unspecified")


def _strings(value) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [str(x).strip() for x in items if isinstance(x, str) and x.strip()]


def licenses(msg: dict) -> list[dict]:
    """Entradas de licencia del registro: [{"url", "version", "delay_days"}]."""
    out = []
    for entry in _entries(msg.get("license")):
        if isinstance(entry, dict) and entry.get("URL"):
            out.append({"url": str(entry["URL"]), "version": str(entry.get("content-version") or "unspecified"),
                        "delay_days": entry.get("delay-in-days")})
    return out


def _delay(entry: dict) -> int:
    try:
        return max(0, int(entry.get("delay_days") or 0))
    except (TypeError, ValueError):
        return 0


_VERSION_NOTES = {"am": "manuscrito aceptado", "unspecified": "versión no especificada"}


def best_license(msg: dict) -> str | None:
    """URL de la licencia de la versión publicada (o la mejor disponible según `_LICENSE_PREFERENCE`); None si solo
    hay licencias de minería de textos o ninguna. Si no es la de la versión publicada o tiene embargo, lo dice entre
    paréntesis tras la URL ("... (manuscrito aceptado; embargo: 365 días)"): la licencia CC del manuscrito aceptado
    de un artículo de suscripción no es la del artículo publicado. Entre varias de la misma versión, la que se aplica
    antes."""
    found = licenses(msg)
    for version in _LICENSE_PREFERENCE:
        entries = [entry for entry in found if entry["version"].lower() == version]
        if entries:
            entry = min(entries, key=_delay)
            notes = [_VERSION_NOTES[version]] if version in _VERSION_NOTES else []
            if _delay(entry):
                notes.append(f"embargo: {_delay(entry)} días")
            return entry["url"] + (f" ({'; '.join(notes)})" if notes else "")
    return None


def parse(msg: dict) -> Record:
    authors, families = [], []
    for a in msg.get("author", []) or []:
        if not isinstance(a, dict):
            continue
        name = " ".join(x for x in (a.get("family"), a.get("given")) if x) or a.get("name")
        if name:
            authors.append(name)
            families.append(a.get("family") or a.get("name"))
    year = None
    for key in ("issued", "published-print", "published-online", "created"):
        parts = (msg.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = int(parts[0][0])
            break
    main_title = _first(msg.get("title"))
    subtitles = _strings(msg.get("subtitle"))
    # Crossref deposita el subtítulo aparte: el título del registro es el completo (`full_title`).
    title = full_title(main_title, subtitles)
    updates = retraction_updates(msg)
    retracted_by_title = bool(main_title and str(main_title).strip().upper().startswith(_RETRACTED_TITLE_PREFIXES))
    pub_types = [t for t in [msg.get("type")] if t]
    return Record(
        source="crossref", title=title, authors=authors, year=year,
        journal=_first(msg.get("container-title")), volume=msg.get("volume"), issue=msg.get("issue"),
        pages=msg.get("page"), doi=msg.get("DOI"), pub_types=pub_types, url=msg.get("URL"),
        cited_by=msg.get("is-referenced-by-count"),
        retracted=True if (updates or retracted_by_title) else None,
        extra={"publisher": msg.get("publisher"), "update_to": _entries(msg.get("update-to")),
               "updated_by": _entries(msg.get("updated-by")), "retraction_updates": updates,
               "families": families, "subtitles": subtitles, "main_title": main_title,
               # revistas bilingües: los demás títulos del registro y el título original de una traducción
               "alternative_titles": _strings(msg.get("title"))[1:] + _strings(msg.get("original-title")),
               "container_titles": _strings(msg.get("container-title")),
               "short_container_titles": _strings(msg.get("short-container-title")),
               "license": best_license(msg), "licenses": licenses(msg)},
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
