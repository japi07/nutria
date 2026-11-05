"""ChEMBL (EMBL-EBI): moléculas bioactivas, dianas, actividades y mecanismos.

Los filtros de la API (`pref_name__icontains`, `target_synonym__icontains`, `target_components__accession`,
`organism__iexact`) siguen la documentación de ChEMBL y su cliente oficial; no se han probado contra el
servicio real (red bloqueada en el entorno de desarrollo).
"""

from __future__ import annotations

import re

from ..http import NetworkError
from .base import checked_version

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


def _component_names(target: dict) -> set[str]:
    """Números de acceso y sinónimos (símbolos de gen incluidos) de los componentes de una diana, en mayúsculas."""
    names: set[str] = set()
    for component in target.get("target_components") or []:
        if not isinstance(component, dict):
            continue
        if component.get("accession"):
            names.add(str(component["accession"]).strip().upper())
        for synonym in component.get("target_component_synonyms") or []:
            value = synonym.get("component_synonym") if isinstance(synonym, dict) else synonym
            if value:
                names.add(str(value).strip().upper())
    return names


def _targets(data: dict, organism: str | None, match: str, query: str = "") -> list[dict]:
    out = []
    wanted = query.strip().upper()
    for t in (data.get("targets", []) if isinstance(data, dict) else []) or []:
        # Comprobación defensiva: el servidor ya filtra por organismo.
        if organism and (t.get("organism") or "").strip().lower() != organism.lower():
            continue
        exact = bool(wanted) and (wanted in _component_names(t)
                                  or wanted in {str(t.get("pref_name") or "").strip().upper(),
                                                str(t.get("target_chembl_id") or "").strip().upper()})
        out.append({"target_chembl_id": t.get("target_chembl_id"), "nombre": t.get("pref_name"),
                    "organismo": t.get("organism"), "tipo": t.get("target_type"), "coincidencia": match,
                    "coincidencias": [match], "exacta": exact})
    return out


def target_search(client, query: str, organism: str | None = None, limit: int = 20,
                  tried: list | None = None) -> list[dict]:
    """Dianas de ChEMBL.

    Sin organismo se usa la búsqueda de texto libre (`target/search.json`). Con `organism` todos los filtros van
    al servidor (`organism__iexact`), así el límite se aplica a las dianas de esa especie, y se combinan SIEMPRE
    estas estrategias (no basta con que la primera dé algo: un nombre que contiene la consulta por casualidad, p.
    ej. «CAT» en «Cathepsin», no debe ocultar la diana cuyo símbolo de gen es «CAT»):
    1. nombre preferido (`pref_name__icontains`), que es el nombre completo de la diana;
    2. número de acceso de UniProt (`target_components__accession`), si la consulta lo parece;
    3. sinónimo o símbolo de gen (`target_synonym__icontains`), p. ej. «PPARA».
    Las dianas se deduplican; primero van las coincidencias exactas (`exacta`: la consulta es el símbolo de gen,
    un sinónimo, el número de acceso, el nombre o el identificador de la diana) y después el resto en el orden de
    las estrategias, hasta `limit`. Cada fila indica en `coincidencia` la primera estrategia que la encontró y en
    `coincidencias` todas. `tried` (si se pasa) recibe cada estrategia con cuántas dianas dio y si llegó al límite
    (`truncada`: puede haber más dianas que no se han visto). Si una estrategia falla (p. ej. el servicio rechaza un
    filtro), las demás siguen y su entrada en `tried` lleva el `error`; si fallan todas, se lanza el primer error.
    """
    organism = (organism or "").strip() or None
    query = (query or "").strip()
    if not organism:
        data = client.get_json(f"{BASE}/target/search.json", params={"q": query, "limit": limit})
        rows = _targets(data, None, "texto_libre", query)
        if tried is not None:
            tried.append({"estrategia": "texto_libre", "n": len(rows), "truncada": len(rows) >= limit})
        return rows
    strategies = [("nombre_preferido", {"pref_name__icontains": query})]
    if _UNIPROT_ACCESSION_RE.fullmatch(query.upper()):
        strategies.append(("accesion_uniprot", {"target_components__accession": query.upper()}))
    strategies.append(("sinonimo_o_simbolo", {"target_synonym__icontains": query}))
    merged: dict[str, dict] = {}
    errors: list[NetworkError] = []
    for label, flt in strategies:
        try:
            data = client.get_json(f"{BASE}/target.json", params={**flt, "organism__iexact": organism, "limit": limit})
        except NetworkError as exc:
            errors.append(exc)
            if tried is not None:
                tried.append({"estrategia": label, "n": 0, "truncada": False, "error": str(exc)})
            continue
        rows = _targets(data, organism, label, query)
        if tried is not None:
            tried.append({"estrategia": label, "n": len(rows), "truncada": len(rows) >= limit})
        for row in rows:
            key = row["target_chembl_id"] or f"sin_id_{len(merged)}"
            if key in merged:
                merged[key]["coincidencias"].append(label)
                merged[key]["exacta"] = merged[key]["exacta"] or row["exacta"]
            else:
                merged[key] = row
    if errors and len(errors) == len(strategies):
        raise errors[0]
    ordered = sorted(merged.values(), key=lambda row: not row["exacta"])   # estable: exactas primero
    return ordered[:limit]


VERSION_RE = r"(?i)chembl_\d{1,3}(?:\.\d{1,2})?"   # p. ej. «ChEMBL_35»
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:.]+Z?)?")


def parse_release(data) -> dict:
    """Versión de una respuesta de `status.json`: {"version", "fecha_publicacion"}; lo que no tiene la forma
    esperada no se registra como versión ({"version_no_reconocida": ...})."""
    if not isinstance(data, dict):
        return checked_version(data, VERSION_RE)
    out = checked_version(data.get("chembl_db_version"), VERSION_RE)
    date = data.get("chembl_release_date")
    if out.get("version") and isinstance(date, str) and _DATE_RE.fullmatch(date.strip()):
        out["fecha_publicacion"] = date.strip()
    return out


def _release_problem(data) -> str | None:
    """Problema de una respuesta que trae algo que no es una versión (no se guarda en la caché); una respuesta
    vacía no es un problema: solo dice que no hay versión."""
    return "no trae una versión de ChEMBL reconocible" if "version_no_reconocida" in parse_release(data) else None


def release(client) -> dict:
    """Versión de ChEMBL que responde (`status.json`): {"version", "fecha_publicacion"}. Una respuesta sin versión
    reconocible no se guarda en la caché (`validate`) ni se registra como versión."""
    return parse_release(client.get_json(f"{BASE}/status.json", ttl_days=1, validate=_release_problem))


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
