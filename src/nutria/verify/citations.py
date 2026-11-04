"""Verificación de referencias bibliográficas.

Comprueba tres cosas que un LLM suele fallar:
1. que el identificador (DOI/PMID) existe;
2. que el identificador corresponde al trabajo citado (título y año coinciden con la cita);
3. que el trabajo no está retractado.
No comprueba si la fuente respalda la afirmación concreta: eso lo hace el agente
`verificador-citas` leyendo el resumen o el texto (ver .claude/agents/verificador-citas.md).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher

from ..http import NetworkError, NotFoundError, OfflineError
from ..sources import crossref, openalex, pubmed
from ..sources.base import normalize_doi

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>\]\[|]+)", re.IGNORECASE)
PMID_RE = re.compile(r"\bPMID:?\s*(\d{5,9})\b", re.IGNORECASE)
PMCID_RE = re.compile(r"\b(PMC\d{4,9})\b")
YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")

_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "that", "this", "their", "between", "during",
    "los", "las", "del", "por", "para", "con", "una", "sobre", "entre", "como", "que",
}


def clean_doi(raw: str) -> str:
    doi = raw.strip()
    while doi and doi[-1] in ".,;:'\"":
        doi = doi[:-1]
    # Los DOI pueden contener paréntesis; solo se quita el de cierre si queda desequilibrado.
    while doi.endswith(")") and doi.count(")") > doi.count("("):
        doi = doi[:-1]
    while doi.endswith("]") and doi.count("]") > doi.count("["):
        doi = doi[:-1]
    return normalize_doi(doi) or doi


def extract_identifiers(text: str) -> dict[str, list[str]]:
    dois = []
    for match in DOI_RE.findall(text or ""):
        doi = clean_doi(match)
        if doi not in dois:
            dois.append(doi)
    pmids = list(dict.fromkeys(PMID_RE.findall(text or "")))
    pmcids = list(dict.fromkeys(PMCID_RE.findall(text or "")))
    return {"doi": dois, "pmid": pmids, "pmcid": pmcids}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower())


def _tokens(text: str) -> set[str]:
    return {t for t in _normalize(text).split() if len(t) > 2 and t not in _STOPWORDS}


def title_coverage(real_title: str | None, citation_text: str | None) -> float | None:
    """Fracción de palabras significativas del título real presentes en la cita (0-1)."""
    if not real_title or not citation_text:
        return None
    title_tokens = _tokens(real_title)
    if not title_tokens:
        return None
    return len(title_tokens & _tokens(citation_text)) / len(title_tokens)


def title_similarity(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return SequenceMatcher(None, _normalize(a).strip(), _normalize(b).strip()).ratio()


@dataclass
class Verification:
    identifier: str
    kind: str                       # doi | pmid
    status: str                     # verificada | no_encontrada | error_red | offline
    title: str | None = None
    year: int | None = None
    journal: str | None = None
    authors: list[str] | None = None
    retracted: bool | None = None
    coverage: float | None = None   # correspondencia del título real con la cita
    year_in_citation: int | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "verificada" and not self.retracted and not self.mismatch

    @property
    def mismatch(self) -> bool:
        if self.coverage is not None and self.coverage < 0.5:
            return True
        if self.year and self.year_in_citation and abs(self.year - self.year_in_citation) > 1:
            return True
        return False

    def to_dict(self) -> dict:
        data = asdict(self)
        data.update(ok=self.ok, discrepancia=self.mismatch)
        return data


def _apply_citation(result: Verification, citation_text: str | None) -> Verification:
    if citation_text and result.status == "verificada":
        result.coverage = title_coverage(result.title, citation_text)
        years = [int(y) for y in YEAR_RE.findall(citation_text)]
        if years and result.year:
            result.year_in_citation = min(years, key=lambda y: abs(y - result.year))
        if result.mismatch:
            result.message = ("El identificador existe pero NO corresponde a la cita "
                              f"(título real: «{result.title}», {result.year}).")
    if result.retracted:
        result.message = (result.message + " " if result.message else "") + "Trabajo RETRACTADO."
    return result


def verify_doi(client, doi: str, citation_text: str | None = None) -> Verification:
    doi = clean_doi(doi)
    rec = None
    try:
        rec = crossref.work(client, doi)
    except NotFoundError:
        try:
            rec = openalex.work_by_doi(client, doi)
        except NotFoundError:
            return Verification(doi, "doi", "no_encontrada", message="DOI inexistente en Crossref y OpenAlex")
        except OfflineError as exc:
            return Verification(doi, "doi", "offline", message=str(exc))
        except NetworkError as exc:
            return Verification(doi, "doi", "error_red", message=str(exc))
    except OfflineError as exc:
        return Verification(doi, "doi", "offline", message=str(exc))
    except NetworkError as exc:
        return Verification(doi, "doi", "error_red", message=str(exc))

    retracted = rec.retracted
    if retracted is None and rec.source == "crossref":
        try:
            retracted = openalex.work_by_doi(client, doi).retracted
        except NetworkError:
            retracted = None
    result = Verification(doi, "doi", "verificada", title=rec.title, year=rec.year, journal=rec.journal,
                          authors=rec.authors[:5], retracted=retracted)
    return _apply_citation(result, citation_text)


def verify_pmid(client, pmid: str, citation_text: str | None = None) -> Verification:
    try:
        records = pubmed.summaries(client, [pmid])
    except OfflineError as exc:
        return Verification(pmid, "pmid", "offline", message=str(exc))
    except NetworkError as exc:
        return Verification(pmid, "pmid", "error_red", message=str(exc))
    if not records:
        return Verification(pmid, "pmid", "no_encontrada", message="PMID inexistente en PubMed")
    rec = records[0]
    result = Verification(pmid, "pmid", "verificada", title=rec.title, year=rec.year, journal=rec.journal,
                          authors=rec.authors[:5], retracted=rec.retracted)
    return _apply_citation(result, citation_text)


def verify_entry(client, entry_text: str) -> list[Verification]:
    """Verifica todos los identificadores de una entrada bibliográfica."""
    ids = extract_identifiers(entry_text)
    results = [verify_doi(client, d, entry_text) for d in ids["doi"]]
    results += [verify_pmid(client, p, entry_text) for p in ids["pmid"]]
    return results
