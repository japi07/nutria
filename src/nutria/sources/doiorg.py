"""Registro de DOI (doi.org) y DataCite: existencia y metadatos de un DOI que Crossref y OpenAlex no conocen.

Crossref solo tiene los DOI de sus miembros, y OpenAlex no indexa todos los de otras agencias. Antes de dar un
DOI por inexistente hay que preguntar al registro de DOI:

- `handle_exists`: API de handles del proxy de doi.org (`https://doi.org/api/handles/<doi>`). `responseCode` 1
  significa registrado; 100 (con HTTP 404), que el DOI no existe. Es la única fuente que permite afirmar que un
  DOI no existe.
- `registration_agency`: agencia que registró el DOI (`https://doi.org/doiRA/<doi>`): Crossref, DataCite, mEDRA,
  JaLC, KISTI, CNKI, Airiti, ISTIC, OP (Oficina de Publicaciones de la UE), EIDR...
- `datacite_work`: metadatos de un DOI de DataCite (`https://api.datacite.org/dois/<doi>`) como `Record`.

DataCite no publica retractaciones: su registro deja la retractación como desconocida (None), salvo que el
título la declare ("RETRACTED"/"WITHDRAWN"). Los DOI de otras agencias (mEDRA, JaLC...) no tienen aquí un cliente
de metadatos: su verificación queda pendiente.
"""

from __future__ import annotations

from urllib.parse import quote

from ..http import NetworkError
from .base import Record, normalize_doi, year_from

HANDLES = "https://doi.org/api/handles"
DOI_RA = "https://doi.org/doiRA"
DATACITE = "https://api.datacite.org"

_RETRACTED_TITLE_PREFIXES = ("RETRACTED", "RETRACTION:", "WITHDRAWN")


def _path(doi: str) -> str:
    """El DOI como segmento de ruta: se codifica todo salvo '/' (también '#', '<', '>' y ';' de los SICI)."""
    return quote(normalize_doi(doi) or doi, safe="/")


def handle_exists(client, doi: str) -> bool:
    """True si el DOI está registrado en doi.org; False si no existe. Lanza `NetworkError` si no se puede saber."""
    from ..http import NotFoundError
    try:
        data = client.get_json(f"{HANDLES}/{_path(doi)}")
    except NotFoundError:
        return False
    code = data.get("responseCode") if isinstance(data, dict) else None
    if code == 1:
        return True
    if code == 100:
        return False
    raise NetworkError(f"Respuesta inesperada del registro de DOI para {doi}: responseCode={code!r}")


def registration_agency(client, doi: str) -> str | None:
    """Nombre de la agencia de registro ("Crossref", "DataCite", "mEDRA"...) o None si doi.org no la da."""
    data = client.get_json(f"{DOI_RA}/{_path(doi)}")
    items = data if isinstance(data, list) else [data]
    for item in items:
        if isinstance(item, dict) and item.get("RA"):
            return str(item["RA"]).strip() or None
    return None


def _first_title(titles: list, title_type: str | None) -> str | None:
    for item in titles:
        if isinstance(item, dict) and item.get("title") and item.get("titleType") == title_type:
            return str(item["title"])
    return None


def _license(rights: list) -> str | None:
    for item in rights:
        if isinstance(item, dict):
            value = item.get("rightsUri") or item.get("rightsIdentifier") or item.get("rights")
            if value:
                return str(value)
    return None


def parse_datacite(data: dict) -> Record:
    """Convierte la respuesta JSON:API de DataCite (`{"data": {"attributes": ...}}`) en un `Record`."""
    payload = data.get("data") if isinstance(data, dict) else None
    attrs = (payload or {}).get("attributes") or {}
    titles = [t for t in attrs.get("titles") or [] if isinstance(t, dict)]
    title = _first_title(titles, None) or next((str(t["title"]) for t in titles if t.get("title")), None)
    subtitles = [str(t["title"]) for t in titles if t.get("titleType") == "Subtitle" and t.get("title")]
    alternatives = [str(t["title"]) for t in titles
                    if t.get("titleType") in ("AlternativeTitle", "TranslatedTitle") and t.get("title")]
    authors, families = [], []
    for creator in attrs.get("creators") or []:
        if not isinstance(creator, dict):
            continue
        family, given = creator.get("familyName"), creator.get("givenName")
        name = " ".join(x for x in (family, given) if x) or creator.get("name")
        if not name:
            continue
        if not family and creator.get("nameType") != "Organizational" and "," in str(name):
            family = str(name).split(",", 1)[0]
        authors.append(str(name))
        families.append(family)
    container = attrs.get("container") if isinstance(attrs.get("container"), dict) else {}
    types = attrs.get("types") or {}
    publisher = attrs.get("publisher")
    if isinstance(publisher, dict):
        publisher = publisher.get("name")
    retracted = True if title and title.strip().upper().startswith(_RETRACTED_TITLE_PREFIXES) else None
    return Record(
        source="datacite", title=title, authors=authors, year=year_from(attrs.get("publicationYear")),
        journal=container.get("title"), volume=container.get("volume"), issue=container.get("issue"),
        doi=attrs.get("doi") or (payload or {}).get("id"),
        pub_types=[str(t) for t in (types.get("resourceTypeGeneral"), types.get("resourceType")) if t],
        url=attrs.get("url"), retracted=retracted,
        extra={"families": families, "subtitles": subtitles, "alternative_titles": alternatives,
               "publisher": publisher, "license": _license(attrs.get("rightsList") or []),
               "state": attrs.get("state"), "registration_agency": "DataCite"},
    )


def datacite_work(client, doi: str) -> Record:
    """Metadatos de un DOI de DataCite. Lanza `NotFoundError` si DataCite no lo tiene."""
    return parse_datacite(client.get_json(f"{DATACITE}/dois/{_path(doi)}"))
