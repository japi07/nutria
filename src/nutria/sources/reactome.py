"""Reactome: búsqueda de rutas y análisis de sobrerrepresentación (con proyección a humano).

`version` devuelve la versión de la base de datos de Reactome que responde (ContentService
`/data/database/version`), para registrarla junto a los resultados. Según la documentación de Reactome; no
comprobado contra el servicio real.
"""

from __future__ import annotations

from .base import clean_text

CONTENT = "https://reactome.org/ContentService"
ANALYSIS = "https://reactome.org/AnalysisService"


def version(client) -> dict:
    """Versión de la base de datos de Reactome: {"version": "..."} (vacío si la respuesta no la trae)."""
    text = client.get_text(f"{CONTENT}/data/database/version", ttl_days=1,
                           headers={"Accept": "text/plain, */*"})
    value = str(text or "").strip()
    return {"version": value} if value and len(value) < 40 else {}


def search(client, query: str, species: str = "Bos taurus", types: str = "Pathway") -> list[dict]:
    params = {"query": query, "species": species, "types": types, "cluster": "true"}
    data = client.get_json(f"{CONTENT}/search/query", params=params)
    out = []
    for group in data.get("results", []):
        for entry in group.get("entries", []):
            out.append({"id": entry.get("stId"), "nombre": clean_text(entry.get("name")),
                        "especie": entry.get("species"), "tipo": entry.get("exactType")})
    return out


def analyze_genes(client, genes: list[str], page_size: int = 25) -> dict:
    """Sobrerrepresentación de rutas. Usa la proyección a humano: los identificadores de otras
    especies se mapean a ortólogos humanos, así que el resultado es una aproximación."""
    body = "\n".join(g.strip() for g in genes if g.strip())
    data = client.request("POST", f"{ANALYSIS}/identifiers/projection",
                          params={"interactors": "false", "pageSize": page_size, "page": 1,
                                  "sortBy": "ENTITIES_FDR", "order": "ASC", "resource": "TOTAL"},
                          data=body, headers={"Content-Type": "text/plain"}, expect="json")
    pathways = []
    for p in data.get("pathways", []):
        ent = p.get("entities") or {}
        pathways.append({"id": p.get("stId"), "nombre": p.get("name"), "fdr": ent.get("fdr"),
                         "p": ent.get("pValue"), "encontradas": ent.get("found"), "total": ent.get("total")})
    return {"token": (data.get("summary") or {}).get("token"),
            "no_encontrados": data.get("identifiersNotFound"), "rutas": pathways}
