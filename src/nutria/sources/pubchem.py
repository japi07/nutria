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
    """Frases H de la clasificación GHS notificada (orientativo; no sustituye una evaluación de riesgo).

    Devuelve [] tanto si PubChem no tiene clasificación como si la tiene sin frases H: para distinguirlo,
    usa `ghs_classification`."""
    try:
        data = client.get_json(f"{VIEW}/data/compound/{int(cid)}/JSON",
                               params={"heading": "GHS Classification"})
    except NotFoundError:  # el compuesto no tiene clasificación GHS notificada
        return []
    out: list[str] = []
    _collect_strings(data, "Hazard Statements", out)
    return out


# Estados de `ghs_classification`.
GHS_CON_FRASES = "con_frases_H"            # clasificación notificada con frases H
GHS_SIN_FRASES = "clasificado_sin_frases_H"  # hay sección GHS, pero sin frases H (p. ej. «no cumple los criterios»)
GHS_SIN_CLASIFICACION = "sin_clasificacion"  # PubChem no tiene sección GHS para ese CID (404)
GHS_ERROR = "error"                          # no se pudo consultar (red, servicio, modo offline sin copia)
GHS_NO_CONSULTADO = "no_consultado"          # no se pidió para ese CID


def ghs_not_queried(reason: str) -> dict:
    return {"estado": GHS_NO_CONSULTADO, "frases_H": [], "detalle": reason}


def ghs_classification(client, cid: int) -> dict:
    """Clasificación GHS notificada en PubChem con un estado explícito (orientativo; no sustituye una evaluación
    de riesgo): {"estado": con_frases_H | clasificado_sin_frases_H | sin_clasificacion | error, "frases_H",
    "otras" (otras frases de la sección), "detalle"}. Un error nunca se presenta como «sin frases H»."""
    try:
        data = client.get_json(f"{VIEW}/data/compound/{int(cid)}/JSON",
                               params={"heading": "GHS Classification"})
    except NotFoundError:
        return {"estado": GHS_SIN_CLASIFICACION, "frases_H": [], "otras": [],
                "detalle": "PubChem no tiene clasificación GHS notificada para este CID (HTTP 404)"}
    except NetworkError as exc:
        return {"estado": GHS_ERROR, "frases_H": [], "otras": [],
                "detalle": f"No se pudo consultar la clasificación GHS: {exc}"}
    hazards: list[str] = []
    _collect_strings(data, "Hazard Statements", hazards)
    other: list[str] = []
    _collect_strings(data, "GHS Hazard", other)
    _collect_strings(data, "Signal", other)
    if hazards:
        return {"estado": GHS_CON_FRASES, "frases_H": hazards, "otras": [o for o in other if o not in hazards],
                "detalle": "frases H de las notificaciones agregadas por PubChem"}
    return {"estado": GHS_SIN_FRASES, "frases_H": [], "otras": other,
            "detalle": "PubChem tiene sección GHS para este CID, pero sin frases H (p. ej., notificado como que no "
                       "cumple los criterios de clasificación); no equivale a «sin peligro»"}
