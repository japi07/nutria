"""UniProtKB REST: proteínas por gen y especie (p. ej. ortólogos bovinos).

La versión de UniProt que respondió se lee de las cabeceras `X-UniProt-Release` y `X-UniProt-Release-Date`
(documentadas por UniProt; no comprobadas contra el servicio real), que el cliente HTTP conserva también en la
caché.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .base import checked_version

BASE = "https://rest.uniprot.org/uniprotkb"
FIELDS = "accession,id,protein_name,gene_names,organism_name,organism_id,length,reviewed,cc_function"


def _protein_name(entry: dict) -> str | None:
    desc = entry.get("proteinDescription") or {}
    rec = (desc.get("recommendedName") or {}).get("fullName") or {}
    if rec.get("value"):
        return rec["value"]
    for sub in desc.get("submissionNames", []) or []:
        value = (sub.get("fullName") or {}).get("value")
        if value:
            return value
    return None


def _function(entry: dict) -> str | None:
    for comment in entry.get("comments", []) or []:
        if comment.get("commentType") == "FUNCTION":
            texts = [t.get("value") for t in comment.get("texts", []) if t.get("value")]
            if texts:
                return " ".join(texts)
    return None


def parse(entry: dict) -> dict:
    genes = [(g.get("geneName") or {}).get("value") for g in entry.get("genes", []) or []]
    return {
        "accesion": entry.get("primaryAccession"),
        "entrada": entry.get("uniProtkbId"),
        "proteina": _protein_name(entry),
        "genes": [g for g in genes if g],
        "organismo": (entry.get("organism") or {}).get("scientificName"),
        "taxon": (entry.get("organism") or {}).get("taxonId"),
        "longitud": (entry.get("sequence") or {}).get("length"),
        "revisada": str(entry.get("entryType", "")).lower().startswith("uniprotkb reviewed"),
        "funcion": _function(entry),
        "url": f"https://www.uniprot.org/uniprotkb/{entry.get('primaryAccession')}",
    }


def search(client, query: str, taxon: int | None = None, size: int = 10,
           reviewed_only: bool = False) -> list[dict]:
    q = f"({query})"
    if taxon:
        q += f" AND (organism_id:{taxon})"
    if reviewed_only:
        q += " AND (reviewed:true)"
    data = client.get_json(f"{BASE}/search", params={"query": q, "fields": FIELDS, "format": "json",
                                                     "size": size})
    return [parse(e) for e in data.get("results", [])]


RELEASE_RE = r"\d{4}_\d{2}"    # p. ej. «2024_06»
_DATE_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z ,./-]{3,29}")


def parse_release(headers) -> dict:
    """Versión de UniProt en unas cabeceras de respuesta: {"version", "fecha_publicacion"}; lo que no tiene la forma
    esperada no se registra como versión ({"version_no_reconocida": ...})."""
    headers = {str(k).lower(): v for k, v in (headers or {}).items()} if isinstance(headers, dict) else {}
    out = checked_version(headers.get("x-uniprot-release"), RELEASE_RE)
    date = headers.get("x-uniprot-release-date")
    if out.get("version") and isinstance(date, str) and _DATE_RE.fullmatch(date.strip()):
        out["fecha_publicacion"] = date.strip()
    return out


def release(client) -> dict:
    """Versión de UniProt de la última respuesta (red o caché): {"version", "fecha_publicacion"}; vacío si
    la respuesta no traía las cabeceras."""
    return parse_release((getattr(client, "response_headers", None) or {}).get(urlparse(BASE).hostname, {}))
