"""PubChem PUG REST / PUG View: identidad química, propiedades y clasificación GHS de peligros."""

from __future__ import annotations

from urllib.parse import quote

from ..http import NetworkError, NotFoundError

BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
VIEW = "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view"

PROPERTIES = ["MolecularFormula", "MolecularWeight", "XLogP", "TPSA", "HBondDonorCount",
              "HBondAcceptorCount", "RotatableBondCount", "InChIKey", "IUPACName"]


def compound_by_name(client, name: str) -> list[dict]:
    """Propiedades de los compuestos que coinciden con un nombre (puede haber varios CID)."""
    base_url = f"{BASE}/compound/name/{quote(name, safe='')}/property"
    try:
        data = client.get_json(f"{base_url}/{','.join(PROPERTIES + ['SMILES'])}/JSON")
    except NetworkError as exc:
        # Las versiones antiguas de PUG REST solo conocen IsomericSMILES.
        if "HTTP 400" not in str(exc):
            raise
        data = client.get_json(f"{base_url}/{','.join(PROPERTIES + ['IsomericSMILES'])}/JSON")
    props = (data.get("PropertyTable") or {}).get("Properties", [])
    for item in props:
        if "SMILES" not in item and "IsomericSMILES" in item:
            item["SMILES"] = item.pop("IsomericSMILES")
    return props


def synonyms(client, cid: int, limit: int = 25) -> list[str]:
    data = client.get_json(f"{BASE}/compound/cid/{int(cid)}/synonyms/JSON")
    info = (data.get("InformationList") or {}).get("Information", [])
    return (info[0].get("Synonym", []) if info else [])[:limit]


def _collect_strings(node, wanted: str, out: list[str]) -> None:
    if isinstance(node, dict):
        if wanted.lower() in str(node.get("Name", "")).lower():
            for swm in (node.get("Value") or {}).get("StringWithMarkup", []) or []:
                text = swm.get("String")
                if text and text not in out:
                    out.append(text)
        for value in node.values():
            _collect_strings(value, wanted, out)
    elif isinstance(node, list):
        for value in node:
            _collect_strings(value, wanted, out)


def ghs_hazards(client, cid: int) -> list[str]:
    """Frases H de la clasificación GHS notificada (orientativo; no sustituye una evaluación de riesgo)."""
    try:
        data = client.get_json(f"{VIEW}/data/compound/{int(cid)}/JSON",
                               params={"heading": "GHS Classification"})
    except NotFoundError:  # el compuesto no tiene clasificación GHS notificada
        return []
    out: list[str] = []
    _collect_strings(data, "Hazard Statements", out)
    return out
