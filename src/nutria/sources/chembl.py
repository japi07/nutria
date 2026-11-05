"""ChEMBL (EMBL-EBI): moléculas bioactivas, dianas, actividades y mecanismos.

Los filtros de la API (`pref_name__icontains`, `target_synonym__icontains`, `target_components__accession`,
`organism__iexact`) siguen la documentación de ChEMBL y su cliente oficial; no se han probado contra el
servicio real (red bloqueada en el entorno de desarrollo).
"""

from __future__ import annotations

import re

BASE = "https://www.ebi.ac.uk/chembl/api/data"
# Formato de un número de acceso de UniProtKB.
_UNIPROT_ACCESSION_RE = re.compile(r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})")


def molecule_search(client, query: str, limit: int = 10) -> list[dict]:
    data = client.get_json(f"{BASE}/molecule/search.json", params={"q": query, "limit": limit})
    out = []
    for m in data.get("molecules", []):
        props = m.get("molecule_properties") or {}
        structs = m.get("molecule_structures") or {}
        out.append({
            "chembl_id": m.get("molecule_chembl_id"), "nombre": m.get("pref_name"),
            "fase_maxima": m.get("max_phase"), "tipo": m.get("molecule_type"),
            "formula": props.get("full_molformula"), "peso_molecular": props.get("full_mwt"),
            "alogp": props.get("alogp"), "smiles": structs.get("canonical_smiles"),
            "inchikey": structs.get("standard_inchi_key"),
        })
    return out


def _targets(data: dict, organism: str | None, match: str) -> list[dict]:
    out = []
    for t in data.get("targets", []) or []:
        # Comprobación defensiva: el servidor ya filtra por organismo.
        if organism and (t.get("organism") or "").strip().lower() != organism.lower():
            continue
        out.append({"target_chembl_id": t.get("target_chembl_id"), "nombre": t.get("pref_name"),
                    "organismo": t.get("organism"), "tipo": t.get("target_type"), "coincidencia": match})
    return out


def target_search(client, query: str, organism: str | None = None, limit: int = 20,
                  tried: list | None = None) -> list[dict]:
    """Dianas de ChEMBL.

    Sin organismo se usa la búsqueda de texto libre (`target/search.json`). Con `organism` todos los filtros van
    al servidor (`organism__iexact`), así el límite se aplica a las dianas de esa especie, y se prueba por orden
    hasta que alguna estrategia da resultados:
    1. nombre preferido (`pref_name__icontains`), que es el nombre completo de la diana;
    2. número de acceso de UniProt (`target_components__accession`), si la consulta lo parece;
    3. sinónimo o símbolo de gen (`target_synonym__icontains`), p. ej. «PPARA».
    Cada fila indica en `coincidencia` qué estrategia la encontró; `tried` (si se pasa) recibe las
    estrategias probadas y cuántas dianas dio cada una.
    """
    organism = (organism or "").strip() or None
    query = (query or "").strip()
    if not organism:
        data = client.get_json(f"{BASE}/target/search.json", params={"q": query, "limit": limit})
        rows = _targets(data, None, "texto_libre")
        if tried is not None:
            tried.append({"estrategia": "texto_libre", "n": len(rows)})
        return rows
    strategies = [("nombre_preferido", {"pref_name__icontains": query})]
    if _UNIPROT_ACCESSION_RE.fullmatch(query.upper()):
        strategies.append(("accesion_uniprot", {"target_components__accession": query.upper()}))
    strategies.append(("sinonimo_o_simbolo", {"target_synonym__icontains": query}))
    for label, flt in strategies:
        data = client.get_json(f"{BASE}/target.json", params={**flt, "organism__iexact": organism, "limit": limit})
        rows = _targets(data, organism, label)
        if tried is not None:
            tried.append({"estrategia": label, "n": len(rows)})
        if rows:
            return rows
    return []


def release(client) -> dict:
    """Versión de ChEMBL que responde (`status.json`): {"version", "fecha_publicacion"} (vacío si no consta)."""
    data = client.get_json(f"{BASE}/status.json", ttl_days=1)
    if not isinstance(data, dict):
        return {}
    out = {"version": data.get("chembl_db_version"), "fecha_publicacion": data.get("chembl_release_date")}
    return {k: v for k, v in out.items() if v}


def activities(client, molecule_chembl_id: str | None = None, target_chembl_id: str | None = None,
               limit: int = 50) -> list[dict]:
    if not (molecule_chembl_id or target_chembl_id):
        raise ValueError("Indica molecule_chembl_id o target_chembl_id")
    params = {"limit": limit, "molecule_chembl_id": molecule_chembl_id, "target_chembl_id": target_chembl_id}
    data = client.get_json(f"{BASE}/activity.json", params=params)
    return [{
        "molecula": a.get("molecule_chembl_id"), "diana": a.get("target_chembl_id"),
        "diana_nombre": a.get("target_pref_name"), "organismo_diana": a.get("target_organism"),
        "tipo": a.get("standard_type"), "relacion": a.get("standard_relation"),
        "valor": a.get("standard_value"), "unidades": a.get("standard_units"),
        "pchembl": a.get("pchembl_value"), "ensayo": a.get("assay_description"),
        "documento": a.get("document_chembl_id"),
    } for a in data.get("activities", [])]


def mechanisms(client, molecule_chembl_id: str) -> list[dict]:
    data = client.get_json(f"{BASE}/mechanism.json", params={"molecule_chembl_id": molecule_chembl_id})
    return [{"mecanismo": m.get("mechanism_of_action"), "diana": m.get("target_chembl_id"),
             "accion": m.get("action_type")} for m in data.get("mechanisms", [])]
