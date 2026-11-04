"""Patentes: enlaces de búsqueda reproducibles y cliente opcional de la API de Lens.org.

La libertad de operación (FTO) exige análisis por un profesional de patentes; aquí solo
se automatiza la búsqueda inicial de antecedentes y su registro.
"""

from __future__ import annotations

from urllib.parse import quote

from ..http import NetworkError


def search_links(query: str) -> dict[str, str]:
    q = quote(query)
    return {
        "espacenet": f"https://worldwide.espacenet.com/patent/search?q={q}",
        "google_patents": f"https://patents.google.com/?q={q}",
        "lens": f"https://www.lens.org/lens/search/patent/list?q={q}",
        "patentscope": "https://patentscope.wipo.int/search/es/search.jsf (pegar la consulta)",
    }


def _get(obj, path: str):
    for part in path.split("."):
        if isinstance(obj, list):
            obj = obj[0] if obj else None
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def lens_search(client, query: str, size: int = 20) -> list[dict]:
    token = client.cfg.secret("LENS_API_TOKEN")
    if not token:
        raise NetworkError("Falta LENS_API_TOKEN en el entorno (https://www.lens.org/lens/user/subscriptions)")
    body = {"query": {"query_string": {"query": query}}, "size": size}
    data = client.request("POST", "https://api.lens.org/patent/search", json_body=body,
                          headers={"Authorization": f"Bearer {token}"}, expect="json")
    out = []
    for item in data.get("data", []):
        titles = _get(item, "biblio.invention_title")
        title = None
        if isinstance(titles, list) and titles:
            title = titles[0].get("text") if isinstance(titles[0], dict) else str(titles[0])
        applicants = _get(item, "biblio.parties") or {}
        names = []
        for app in (applicants.get("applicants") or []) if isinstance(applicants, dict) else []:
            name = _get(app, "extracted_name.value")
            if name:
                names.append(name)
        out.append({"lens_id": item.get("lens_id"), "jurisdiccion": item.get("jurisdiction"),
                    "numero": item.get("doc_number"), "tipo": item.get("kind"),
                    "fecha_publicacion": item.get("date_published"), "titulo": title,
                    "solicitantes": names, "estado": _get(item, "legal_status.patent_status")})
    return out
