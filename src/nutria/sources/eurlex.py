"""EUR-Lex: metadatos de un acto de la UE por su número CELEX (título, fecha, ELI y última versión consolidada).

Cliente determinista mínimo, sin interpretación: descarga la página de información del documento
(`https://eur-lex.europa.eu/legal-content/<IDIOMA>/ALL/?uri=CELEX:<CELEX>`) y lee
- los metadatos ELI que EUR-Lex incrusta en la página (`<meta ... property="eli:title|eli:date_document|
  eli:date_publication|eli:in_force" ...>` y el `about` del recurso `eli:LegalResource`);
- los enlaces a versiones consolidadas del mismo acto (CELEX `0AAAATNNNN-AAAAMMDD`), de los que toma la más
  reciente.
Lo que la página no trae queda en None con un aviso: nunca se deduce. Si la respuesta no contiene ningún
metadato reconocible (p. ej. una página de verificación anti-robots o un cambio de formato), se lanza
`NetworkError` en lugar de devolver datos vacíos.

Escrito según el formato publicado de EUR-Lex y de ELI; NO se ha probado contra el servicio real (la red del
entorno de desarrollo bloqueaba eur-lex.europa.eu). Los metadatos no sustituyen la lectura del texto oficial.
"""

from __future__ import annotations

import html
import re

from ..http import NetworkError

BASE = "https://eur-lex.europa.eu"
# Sector (1-9, C, E, 0 para consolidados), año, tipo de documento (1-2 letras), número; opcionalmente el sufijo de
# corrección «(01)» o la fecha de una versión consolidada «-AAAAMMDD».
CELEX_RE = re.compile(r"^[0-9CE]\d{4}[A-Z]{1,2}\d{4}(?:\(\d{2}\))?(?:-\d{8})?$")
LANGUAGES = ("BG", "ES", "CS", "DA", "DE", "ET", "EL", "EN", "FR", "GA", "HR", "IT", "LV", "LT", "HU", "MT", "NL",
             "PL", "PT", "RO", "SK", "SL", "FI", "SV")
_META_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_celex(value: str) -> str:
    """CELEX en mayúsculas y sin espacios; ValueError si no tiene el formato de un número CELEX."""
    text = re.sub(r"\s+", "", str(value or "")).upper()
    if text.startswith("CELEX:"):
        text = text[len("CELEX:"):]
    if not CELEX_RE.match(text):
        raise ValueError(f"Número CELEX no válido: {value!r} (ejemplo: 32009R0767 para el Reglamento (CE) 767/2009)")
    return text


def page_url(celex: str, language: str = "ES") -> str:
    return f"{BASE}/legal-content/{language}/ALL/?uri=CELEX:{celex}"


def text_url(celex: str, language: str = "ES") -> str:
    return f"{BASE}/legal-content/{language}/TXT/?uri=CELEX:{celex}"


def _metas(page: str) -> list[dict]:
    out = []
    for tag in _META_RE.findall(page):
        attrs = {}
        for name, double, single in _ATTR_RE.findall(tag):
            attrs[name.lower()] = html.unescape(double if double or not single else single)
        out.append(attrs)
    return out


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()
    return text or None


def _consolidated(page: str, celex: str) -> list[tuple[str, str]]:
    """Versiones consolidadas del mismo acto enlazadas en la página: [(CELEX, AAAA-MM-DD)], ordenadas."""
    base = re.sub(r"\(\d{2}\)$", "", re.sub(r"-\d{8}$", "", celex))
    core = base[1:]   # sin el sector: los consolidados llevan sector 0
    pattern = re.compile(r"CELEX(?::|%3A)(0" + re.escape(core) + r")-(\d{8})", re.IGNORECASE)
    found = {}
    for number, day in pattern.findall(page):
        found[f"{number.upper()}-{day}"] = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    return sorted(found.items(), key=lambda item: item[1])


def parse_page(page: str, celex: str, language: str = "ES") -> dict:
    """Metadatos de la página de EUR-Lex de un acto. Lo que no consta queda en None y genera un aviso."""
    language = language.upper()
    metas = _metas(page)
    titles, eli_resources = [], []
    values: dict[str, str] = {}
    in_force = None
    for meta in metas:
        prop = meta.get("property", "")
        content = meta.get("content")
        if "eli:legalresource" in meta.get("typeof", "").lower() and meta.get("about", "").startswith("http"):
            eli_resources.append(meta["about"])
        if prop == "eli:title" and content:
            lang = (meta.get("lang") or meta.get("xml:lang") or "").upper()
            titles.append((lang, content))
        elif prop in ("eli:date_document", "eli:date_publication") and content and prop not in values:
            if _DATE_RE.match(content.strip()):
                values[prop] = content.strip()
        elif prop == "eli:in_force" and in_force is None:
            resource = meta.get("resource") or content or ""
            if "notinforce" in resource.lower().replace("-", "").replace("_", ""):
                in_force = False
            elif "inforce" in resource.lower().replace("-", "").replace("_", ""):
                in_force = True
    title = next((t for lang, t in titles if lang[:2] == language), None) or (titles[0][1] if titles else None)
    eli = min(eli_resources, key=len) if eli_resources else None
    if not title and not eli:
        raise NetworkError(f"EUR-Lex respondió sin metadatos reconocibles para CELEX {celex} (¿página de "
                           "verificación anti-robots, documento inexistente o cambio de formato?): no se deduce nada",
                           motivo="respuesta")
    avisos = []
    fallback_title = None
    if not title:
        match = _TITLE_RE.search(page)
        fallback_title = _clean(match.group(1)) if match else None
        avisos.append("La página no trae el título ELI (eli:title)" +
                      ("; se muestra el título de la página web" if fallback_title else ""))
    if not eli:
        avisos.append("La página no trae el identificador ELI del acto")
    if "eli:date_document" not in values:
        avisos.append("La página no trae la fecha del documento (eli:date_document)")
    versions = _consolidated(page, celex)
    if not versions:
        avisos.append("La página no enlaza versiones consolidadas del acto: no se sabe si las hay")
    latest = versions[-1] if versions else None
    return {
        "celex": celex,
        "titulo": _clean(title) or fallback_title,
        "titulo_fuente": "eli:title" if title else ("title" if fallback_title else None),
        "fecha_documento": values.get("eli:date_document"),
        "fecha_publicacion": values.get("eli:date_publication"),
        "eli": eli,
        "en_vigor": in_force,
        "url": text_url(celex, language),
        "url_informacion": page_url(celex, language),
        "ultima_consolidada": ({"celex": latest[0], "fecha": latest[1], "url": text_url(latest[0], language)}
                               if latest else None),
        "versiones_consolidadas": len(versions),
        "idioma": language,
        "avisos": avisos,
    }


def document(client, celex: str, language: str = "ES") -> dict:
    """Metadatos de un acto por su CELEX (con caché de 7 días y procedencia del cliente HTTP).

    Una página sin metadatos reconocibles (verificación anti-robots, error) no se guarda en la caché: la consulta
    siguiente vuelve a pedirla en lugar de servir esa página durante 7 días."""
    celex = normalize_celex(celex)
    language = str(language or "ES").upper()
    if language not in LANGUAGES:
        raise ValueError(f"Idioma no válido para EUR-Lex: {language} (usa uno de {', '.join(LANGUAGES)})")

    def recognisable(page) -> str | None:
        try:
            parse_page(str(page or ""), celex, language)
        except NetworkError as exc:
            return str(exc)
        return None

    page = client.get_text(page_url(celex, language), ttl_days=7,
                           headers={"Accept": "text/html,application/xhtml+xml"}, validate=recognisable)
    return parse_page(str(page or ""), celex, language)
