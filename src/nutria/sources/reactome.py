"""Reactome: búsqueda de rutas y análisis de sobrerrepresentación (con proyección a humano).

`version` devuelve la versión de la base de datos de Reactome que responde (ContentService
`/data/database/version`), para registrarla junto a los resultados. Según la documentación de Reactome; no
comprobado contra el servicio real.
"""

from __future__ import annotations

from .base import checked_version, clean_text

CONTENT = "https://reactome.org/ContentService"
ANALYSIS = "https://reactome.org/AnalysisService"
VERSION_RE = r"\d{1,4}(?:\.\d{1,3})?"   # número de versión de la base de datos, p. ej. «94»


def parse_version(text) -> dict:
    """{"version": "94"} si la respuesta es un número de versión; {"version_no_reconocida": ...} si es otra cosa
    (p. ej. «{}» o una página de error), que no se registra como versión."""
    return checked_version(text, VERSION_RE)


def _version_problem(text) -> str | None:
    """Problema de una respuesta que trae algo que no es una versión (no se guarda en la caché); una respuesta
    vacía no es un problema: solo dice que no hay versión."""
    return "no es un número de versión de Reactome" if "version_no_reconocida" in parse_version(text) else None


def version(client) -> dict:
    """Versión de la base de datos de Reactome: {"version": "..."}. Una respuesta que no es un número de versión
    no se guarda en la caché (`validate`) ni se registra como versión."""
    text = client.get_text(f"{CONTENT}/data/database/version", ttl_days=1,
                           headers={"Accept": "text/plain, */*"}, validate=_version_problem)
    return parse_version(text)


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
