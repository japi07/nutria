"""PubChem PUG REST / PUG View: identidad química, propiedades y clasificación GHS de peligros."""

from __future__ import annotations

import re
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
                if text and text.strip() and text not in out:
                    out.append(text)
        for value in node.values():
            _collect_strings(value, wanted, out)
    elif isinstance(node, list):
        for value in node:
            _collect_strings(value, wanted, out)


# Una frase H empieza por su código: «H302 (88.6%): Harmful if swallowed [...]», «H300+H310+H330: ...», «H360FD: ...»
# o una frase suplementaria de la UE («EUH066: ...»). En la misma lista, PubChem pone también textos que NO son frases
# H: «Not Classified», «Reported as not meeting GHS hazard criteria by 1197 of 1201 companies...», «Of the 31
# notification(s) provided by ... with hazard statement code(s):» o la nota sobre los porcentajes.
_H_CODE = re.compile(r"^\s*(?:EU)?H\d{3}[A-Za-z]{0,2}(?:\s*\+\s*(?:EU)?H\d{3}[A-Za-z]{0,2})*(?![0-9])")
# Notificaciones de que la sustancia no se clasifica (no son frases H ni equivalen a «sin peligro»).
_NOT_CLASSIFIED = re.compile(r"\bnot\s+classified\b|not\s+meeting\s+(?:the\s+)?GHS\s+hazard\s+criteria", re.I)


def is_hazard_statement(text: str) -> bool:
    """¿Es una frase H (o EUH) con su código? Los textos sin código de la sección no lo son."""
    return bool(_H_CODE.match(text or ""))


def ghs_hazards(client, cid: int) -> list[str]:
    """Frases H de la clasificación GHS notificada (orientativo; no sustituye una evaluación de riesgo).

    Solo devuelve las cadenas con código H o EUH. Devuelve [] tanto si PubChem no tiene clasificación (404) como si la
    tiene sin frases H («Not Classified», «no cumple los criterios»): para distinguirlo, usa `ghs_classification`."""
    try:
        data = client.get_json(f"{VIEW}/data/compound/{int(cid)}/JSON",
                               params={"heading": "GHS Classification"})
    except NotFoundError:  # el compuesto no tiene clasificación GHS notificada
        return []
    out: list[str] = []
    _collect_strings(data, "Hazard Statements", out)
    return [text for text in out if is_hazard_statement(text)]


# Estados de `ghs_classification`.
GHS_CON_FRASES = "con_frases_H"            # clasificación notificada con frases H (con código H o EUH)
GHS_SIN_FRASES = "clasificado_sin_frases_H"  # hay sección GHS, pero sin frases H (p. ej. «Not Classified» o «no cumple
                                             # los criterios»)
GHS_SIN_CLASIFICACION = "sin_clasificacion"  # PubChem no tiene sección GHS para ese CID (404)
GHS_ERROR = "error"                          # no se pudo consultar (red, servicio, modo offline sin copia)
GHS_NO_CONSULTADO = "no_consultado"          # no se pidió para ese CID


def ghs_not_queried(reason: str) -> dict:
    return {"estado": GHS_NO_CONSULTADO, "frases_H": [], "otras": [], "no_clasificado": [], "detalle": reason}


def ghs_classification(client, cid: int) -> dict:
    """Clasificación GHS notificada en PubChem con un estado explícito (orientativo; no sustituye una evaluación
    de riesgo): {"estado": con_frases_H | clasificado_sin_frases_H | sin_clasificacion | error, "frases_H" (solo las
    cadenas con código H o EUH), "no_clasificado" (notificaciones de que no se clasifica: «Not Classified», «Reported
    as not meeting GHS hazard criteria...»), "otras" (el resto de textos de la sección: palabra de advertencia, notas,
    resumen de notificaciones), "detalle"}. Un error nunca se presenta como «sin frases H», y un texto sin código H
    nunca se presenta como frase H."""
    try:
        data = client.get_json(f"{VIEW}/data/compound/{int(cid)}/JSON",
                               params={"heading": "GHS Classification"})
    except NotFoundError:
        return {"estado": GHS_SIN_CLASIFICACION, "frases_H": [], "otras": [], "no_clasificado": [],
                "detalle": "PubChem no tiene clasificación GHS notificada para este CID (HTTP 404)"}
    except NetworkError as exc:
        return {"estado": GHS_ERROR, "frases_H": [], "otras": [], "no_clasificado": [],
                "detalle": f"No se pudo consultar la clasificación GHS: {exc}"}
    statements: list[str] = []
    _collect_strings(data, "Hazard Statements", statements)
    texts: list[str] = list(statements)
    for wanted in ("GHS Hazard", "Signal", "Notifications Summary"):
        _collect_strings(data, wanted, texts)
    hazards = [text for text in statements if is_hazard_statement(text)]
    not_classified = [text for text in texts if text not in hazards and _NOT_CLASSIFIED.search(text)]
    other = [text for text in texts if text not in hazards]
    if hazards:
        detail = "frases H de las notificaciones agregadas por PubChem"
        if not_classified:
            detail += ("; algunas notificaciones declaran que no se clasifica o que no cumple los criterios GHS (ver "
                       "«no_clasificado»)")
        return {"estado": GHS_CON_FRASES, "frases_H": hazards, "otras": other, "no_clasificado": not_classified,
                "detalle": detail}
    if not_classified:
        detail = (f"PubChem tiene sección GHS para este CID, sin frases H: notificado como «{not_classified[0]}»; "
                  "no equivale a «sin peligro»")
    else:
        detail = ("PubChem tiene sección GHS para este CID, pero sin frases H con código (ver «otras»); no equivale "
                  "a «sin peligro»")
    return {"estado": GHS_SIN_FRASES, "frases_H": [], "otras": other, "no_clasificado": not_classified,
            "detalle": detail}
