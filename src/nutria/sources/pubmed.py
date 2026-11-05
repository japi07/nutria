"""PubMed / MEDLINE vía NCBI E-utilities (esearch, esummary, efetch).

`search_records` pagina esearch (`retstart`) hasta reunir `retmax` identificadores, pide los resúmenes por
lotes y devuelve los avisos y errores que PubMed incluye en la respuesta (`errorlist`, `warninglist`, `ERROR`):
una frase no encontrada o ignorada cambia el resultado y debe verse. Si falla una página posterior a la
primera, se devuelve lo reunido con `incompleto=True` y un aviso.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .base import Record, collect_pages, normalize_doi, year_from

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ESEARCH_PAGE = 500         # identificadores por página de esearch
ESUMMARY_BATCH = 200       # identificadores por petición de esummary/efetch (URL de longitud razonable)
ESEARCH_LIMIT = 10000      # esearch solo permite recuperar los primeros 10 000 identificadores
_ESEARCH_LISTS = {
    "phrasesnotfound": "frases no encontradas", "fieldsnotfound": "campos no encontrados",
    "phrasesignored": "frases ignoradas", "quotedphrasesnotfound": "frases entre comillas no encontradas",
    "outputmessages": "mensajes",
}

# DOI que puede ir sin comillas en un término de Entrez; el resto (paréntesis, corchetes, dos puntos,
# espacios...) se entrecomilla para que no se interprete como sintaxis de búsqueda.
_PLAIN_DOI_RE = re.compile(r"[a-z0-9._/-]+")


def _base_params(cfg) -> dict:
    params = {"tool": cfg.tool}
    if cfg.email:
        params["email"] = cfg.email
    key = cfg.secret("NCBI_API_KEY")
    if key:
        params["api_key"] = key
    return params


def esearch_warnings(result: dict) -> list[str]:
    """Avisos y errores de una respuesta de esearch (`ERROR`, `errorlist`, `warninglist`), en texto."""
    out: list[str] = []
    if not isinstance(result, dict):
        return out
    if result.get("ERROR"):
        out.append(f"PubMed (error): {result['ERROR']}")
    for group, label in (("errorlist", "error"), ("warninglist", "aviso")):
        block = result.get(group)
        if not isinstance(block, dict):
            continue
        for name, values in block.items():
            values = values if isinstance(values, list) else [values]
            values = [str(v) for v in values if v not in (None, "")]
            if values:
                out.append(f"PubMed ({label}, {_ESEARCH_LISTS.get(name, name)}): {'; '.join(values)}")
    return out


def search(client, query: str, retmax: int = 20, mindate: str | None = None,
           maxdate: str | None = None, sort: str = "relevance", retstart: int = 0) -> dict:
    """Devuelve {count, ids, query_translation, avisos, error}. Las fechas usan AAAA o AAAA/MM/DD."""
    params = {"db": "pubmed", "term": query, "retmax": retmax, "retmode": "json", "sort": sort,
              **_base_params(client.cfg)}
    if retstart:
        params["retstart"] = retstart
    if mindate or maxdate:
        params.update(datetype="pdat", mindate=mindate or "1800", maxdate=maxdate or "3000")
    data = client.get_json(f"{EUTILS}/esearch.fcgi", params=params, ttl_days=7)
    result = data.get("esearchresult", {}) if isinstance(data, dict) else {}
    return {
        "count": int(result.get("count", 0) or 0),
        "ids": [str(i) for i in result.get("idlist", []) or []],
        "query_translation": result.get("querytranslation"),
        "avisos": esearch_warnings(result),
        "error": bool(result.get("ERROR")) or not isinstance(data, dict) or "esearchresult" not in data,
    }


def pmid_for_doi(client, doi: str) -> str | None:
    """PMID del registro de PubMed con ese DOI (esearch con el término `<doi>[doi]`).

    Devuelve None si PubMed no tiene el DOI o si lo tienen varios registros (ambiguo). Sirve como
    comprobación alternativa de retractación: `summaries` marca el tipo "Retracted Publication".
    """
    norm = normalize_doi(doi)
    if not norm or '"' in norm:
        return None
    term_doi = norm if _PLAIN_DOI_RE.fullmatch(norm) else f'"{norm}"'
    params = {"db": "pubmed", "term": f"{term_doi}[doi]", "retmode": "json", "retmax": 2,
              **_base_params(client.cfg)}
    data = client.get_json(f"{EUTILS}/esearch.fcgi", params=params)
    ids = [str(i) for i in ((data.get("esearchresult") or {}).get("idlist") or [])]
    return ids[0] if len(ids) == 1 else None


def summaries(client, pmids: list[str]) -> list[Record]:
    """Registros de esummary, por lotes de `ESUMMARY_BATCH` identificadores."""
    if not pmids:
        return []
    if len(pmids) > ESUMMARY_BATCH:
        out: list[Record] = []
        for start in range(0, len(pmids), ESUMMARY_BATCH):
            out.extend(summaries(client, pmids[start:start + ESUMMARY_BATCH]))
        return out
    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "json", **_base_params(client.cfg)}
    data = client.get_json(f"{EUTILS}/esummary.fcgi", params=params)
    result = data.get("result", {})
    records = []
    for uid in result.get("uids", []):
        item = result.get(uid, {})
        if not item or item.get("error"):
            continue
        ids = {a.get("idtype"): a.get("value") for a in item.get("articleids", [])}
        pub_types = list(item.get("pubtype", []) or [])
        authors = [a.get("name") for a in item.get("authors", [])
                   if a.get("authtype", "Author") == "Author" and a.get("name")]
        records.append(Record(
            source="pubmed", pmid=str(uid), doi=ids.get("doi"), pmcid=ids.get("pmc"),
            title=item.get("title"), authors=authors,
            year=year_from(item.get("pubdate") or item.get("epubdate")),
            journal=item.get("fulljournalname") or item.get("source"),
            volume=item.get("volume") or None, issue=item.get("issue") or None,
            pages=item.get("pages") or None, pub_types=pub_types,
            retracted="Retracted Publication" in pub_types,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
        ))
    return records


def abstracts(client, pmids: list[str]) -> dict[str, dict]:
    """Resumen, MeSH, tipos de publicación y DOI por PMID (efetch XML)."""
    if not pmids:
        return {}
    if len(pmids) > ESUMMARY_BATCH:
        out: dict[str, dict] = {}
        for start in range(0, len(pmids), ESUMMARY_BATCH):
            out.update(abstracts(client, pmids[start:start + ESUMMARY_BATCH]))
        return out
    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml", **_base_params(client.cfg)}
    xml_text = client.get_text(f"{EUTILS}/efetch.fcgi", params=params, headers={"Accept": "application/xml"})
    return parse_efetch(xml_text)


def parse_efetch(xml_text: str) -> dict[str, dict]:
    root = ET.fromstring(xml_text)
    out: dict[str, dict] = {}
    for art in root.iter("PubmedArticle"):
        pmid = art.findtext("./MedlineCitation/PMID")
        if not pmid:
            continue
        parts = []
        for node in art.iterfind(".//Abstract/AbstractText"):
            text = "".join(node.itertext()).strip()
            label = node.get("Label")
            if text:
                parts.append(f"{label}: {text}" if label else text)
        doi = None
        for aid in art.iterfind(".//PubmedData/ArticleIdList/ArticleId"):
            if aid.get("IdType") == "doi" and aid.text:
                doi = aid.text.strip()
        out[pmid] = {
            "abstract": "\n".join(parts) or None,
            "mesh": [m.text for m in art.iterfind(".//MeshHeadingList/MeshHeading/DescriptorName") if m.text],
            "pub_types": [p.text for p in art.iterfind(".//PublicationTypeList/PublicationType") if p.text],
            "doi": doi,
        }
    return out


def search_records(client, query: str, retmax: int = 20, mindate: str | None = None,
                   maxdate: str | None = None, with_abstracts: bool = False) -> dict:
    """Busca y devuelve hasta `retmax` registros, paginando esearch y esummary.

    Devuelve {count, query_translation, records, avisos, paginas, incompleto}. Un fallo en la primera
    petición se propaga; en las siguientes, el resultado queda marcado como incompleto con un aviso.
    """
    from ..http import NetworkError

    translation: dict = {}

    def page(cursor, size):
        start = int(cursor or 0)
        found = search(client, query, retmax=size, mindate=mindate, maxdate=maxdate, retstart=start)
        if not start:
            translation["value"] = found["query_translation"]
            translation["error"] = found["error"]
        following = start + len(found["ids"])
        more = found["ids"] and following < min(found["count"], ESEARCH_LIMIT)
        return {"count": found["count"], "records": found["ids"], "next": following if more else None,
                "avisos": found["avisos"]}

    collected = collect_pages(page, min(int(retmax), ESEARCH_LIMIT), ESEARCH_PAGE, "PubMed (esearch)")
    avisos = list(collected["avisos"])
    incompleto = collected["incompleto"] or bool(translation.get("error"))
    if retmax > ESEARCH_LIMIT and collected["count"] > ESEARCH_LIMIT:
        avisos.append(f"PubMed: esearch solo permite recuperar los primeros {ESEARCH_LIMIT} identificadores; "
                      "divide la búsqueda (p. ej. por años) para ver el resto")
    ids = list(dict.fromkeys(collected["records"]))
    records: list[Record] = []
    for start in range(0, len(ids), ESUMMARY_BATCH):
        try:
            records.extend(summaries(client, ids[start:start + ESUMMARY_BATCH]))
        except NetworkError as exc:
            if not start:
                raise
            avisos.append(f"PubMed (esummary): falló el lote {start // ESUMMARY_BATCH + 1} ({exc}); "
                          "los resultados están INCOMPLETOS")
            incompleto = True
            break
    if with_abstracts and records:
        try:
            extra = abstracts(client, [r.pmid for r in records if r.pmid])
        except NetworkError as exc:
            avisos.append(f"PubMed (efetch): no se pudieron descargar los resúmenes ({exc})")
            extra = {}
            incompleto = True
        for rec in records:
            info = extra.get(rec.pmid or "", {})
            rec.abstract = info.get("abstract")
            rec.extra["mesh"] = info.get("mesh", [])
    return {"count": collected["count"], "query_translation": translation.get("value"), "records": records,
            "avisos": avisos, "paginas": collected["paginas"], "incompleto": incompleto}
