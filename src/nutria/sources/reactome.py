"""Reactome: búsqueda de rutas y análisis de sobrerrepresentación (con proyección a humano)."""

from __future__ import annotations

from .base import clean_text

CONTENT = "https://reactome.org/ContentService"
ANALYSIS = "https://reactome.org/AnalysisService"


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
