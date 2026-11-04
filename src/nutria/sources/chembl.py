"""ChEMBL (EMBL-EBI): moléculas bioactivas, dianas, actividades y mecanismos."""

from __future__ import annotations

BASE = "https://www.ebi.ac.uk/chembl/api/data"


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


def target_search(client, query: str, organism: str | None = None, limit: int = 20) -> list[dict]:
    data = client.get_json(f"{BASE}/target/search.json", params={"q": query, "limit": limit})
    out = []
    for t in data.get("targets", []):
        if organism and (t.get("organism") or "").lower() != organism.lower():
            continue
        out.append({"target_chembl_id": t.get("target_chembl_id"), "nombre": t.get("pref_name"),
                    "organismo": t.get("organism"), "tipo": t.get("target_type")})
    return out


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
