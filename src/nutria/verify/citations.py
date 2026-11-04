"""Verificación de referencias bibliográficas (falla cerrado).

Comprueba lo que un LLM suele fallar:
1. que el identificador (DOI/PMID) existe;
2. que corresponde al trabajo citado: título (cobertura de sus palabras significativas), año (tomado del
   texto de la cita tras quitar identificadores y páginas) y primer autor;
3. que el trabajo no está retractado, con una cadena de comprobación (Crossref, OpenAlex, PubMed). Si
   ninguna fuente permite saberlo, la retractación queda como DESCONOCIDA y la verificación no es válida.
No comprueba si la fuente respalda la afirmación concreta: eso se registra en la tabla de respaldo del
Anexo A del informe (agente `verificador-citas`), que la comprobación del informe exige.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

from ..http import NetworkError, NotFoundError, OfflineError
from ..sources import crossref, openalex, pubmed
from ..sources.base import normalize_doi

try:  # el paquete de fuentes aporta la redacción de secretos
    from ..http import redact as _redact
except ImportError:  # pragma: no cover
    def _redact(text, cfg=None):  # type: ignore[no-redef]
        return text

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>\]\[|]+)", re.IGNORECASE)
PMID_RE = re.compile(r"\bPMID:?\s*(\d{5,9})\b", re.IGNORECASE)
PMCID_RE = re.compile(r"\b(PMC\d{4,9})\b")
URL_RE = re.compile(r"https?://\S+")
YEAR_RE = re.compile(r"(?<!\d)(1[6-9]\d{2}|20\d{2})(?!\d)")
PAGES_RE = re.compile(r"(?<!\d)\d{1,6}\s*[-–]\s*\d{1,6}(?!\d)")
VOLUME_PAGES_RE = re.compile(r"\b\d+\s*\(\s*[\w\-]+\s*\)\s*:\s*[\w\-–]+")

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


def _strip_identifiers(text: str) -> str:
    text = URL_RE.sub(" ", text or "")
    text = DOI_RE.sub(" ", text)
    text = PMID_RE.sub(" ", text)
    text = PMCID_RE.sub(" ", text)
    text = re.sub(r"\bdoi\s*:?", " ", text, flags=re.IGNORECASE)
    return text


def citation_year(citation_text: str | None) -> int | None:
    """Año de la cita: el primero que aparece tras quitar identificadores, volumen(número):páginas y
    rangos de páginas (así un "2019" dentro del DOI o unas páginas 2007-2016 no se toman por el año)."""
    if not citation_text:
        return None
    text = _strip_identifiers(citation_text)
    text = VOLUME_PAGES_RE.sub(" ", text)
    text = PAGES_RE.sub(" ", text)
    match = YEAR_RE.search(text)
    return int(match.group(1)) if match else None


def title_coverage(real_title: str | None, citation_text: str | None) -> float | None:
    """Fracción de palabras significativas del título real presentes en la cita (0-1)."""
    if not real_title or not citation_text:
        return None
    title_tokens = _tokens(real_title)
    if not title_tokens:
        return None
    return len(title_tokens & _tokens(_strip_identifiers(citation_text))) / len(title_tokens)


def title_similarity(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return SequenceMatcher(None, _normalize(a).strip(), _normalize(b).strip()).ratio()


def first_author_surname(authors: list[str] | None) -> str | None:
    """Apellido del primer autor tal como lo devuelven Crossref ("Apellido Nombre"), PubMed ("Apellido AB")
    u OpenAlex ("Nombre Apellido"): se devuelven candidatos separados por '|'."""
    if not authors:
        return None
    first = _normalize(authors[0]).split()
    if not first:
        return None
    candidates = {first[0], first[-1]}
    return "|".join(sorted(c for c in candidates if len(c) > 1)) or None


def author_matches(authors: list[str] | None, citation_text: str | None) -> bool | None:
    surname = first_author_surname(authors)
    if not surname or not citation_text:
        return None
    tokens = set(_normalize(_strip_identifiers(citation_text)).split())
    return any(c in tokens for c in surname.split("|"))


COVERAGE_MIN = 0.6


@dataclass
class Verification:
    identifier: str
    kind: str                       # doi | pmid
    status: str                     # verificada | no_encontrada | error_red | offline
    title: str | None = None
    year: int | None = None
    journal: str | None = None
    authors: list[str] | None = None
    retracted: bool | None = None   # None = desconocido (la verificación no es válida)
    retraction_checked_via: list[str] = field(default_factory=list)
    coverage: float | None = None
    year_in_citation: int | None = None
    author_ok: bool | None = None
    message: str = ""

    @property
    def mismatch(self) -> bool:
        if self.coverage is not None and self.coverage < COVERAGE_MIN:
            return True
        if self.year and self.year_in_citation and abs(self.year - self.year_in_citation) > 1:
            return True
        if self.author_ok is False:
            return True
        return False

    @property
    def ok(self) -> bool:
        return self.status == "verificada" and self.retracted is False and not self.mismatch

    def to_dict(self) -> dict:
        data = asdict(self)
        data.update(ok=self.ok, discrepancia=self.mismatch)
        return data


def _apply_citation(result: Verification, citation_text: str | None) -> Verification:
    if citation_text and result.status == "verificada":
        result.coverage = title_coverage(result.title, citation_text)
        result.year_in_citation = citation_year(citation_text)
        result.author_ok = author_matches(result.authors, citation_text)
        if result.mismatch:
            reasons = []
            if result.coverage is not None and result.coverage < COVERAGE_MIN:
                reasons.append(f"título (cobertura {result.coverage:.2f})")
            if result.year and result.year_in_citation and abs(result.year - result.year_in_citation) > 1:
                reasons.append(f"año (cita {result.year_in_citation}, real {result.year})")
            if result.author_ok is False:
                reasons.append("primer autor")
            result.message = (f"El identificador existe pero NO corresponde a la cita ({', '.join(reasons)}); "
                              f"trabajo real: «{result.title}», {result.year}.")
    if result.retracted:
        result.message = (result.message + " " if result.message else "") + "Trabajo RETRACTADO."
    elif result.retracted is None and result.status == "verificada":
        result.message = (result.message + " " if result.message else "") + \
            "No se pudo comprobar si está retractado (verificación incompleta)."
    return result


def _retraction_from_openalex(client, doi: str) -> bool | None:
    try:
        return openalex.work_by_doi(client, doi).retracted
    except NetworkError:
        return None


def _retraction_from_pubmed(client, doi: str) -> bool | None:
    finder = getattr(pubmed, "pmid_for_doi", None)
    if finder is None:
        return None
    try:
        pmid = finder(client, doi)
        if not pmid:
            return None
        records = pubmed.summaries(client, [pmid])
    except NetworkError:
        return None
    return records[0].retracted if records else None


def verify_doi(client, doi: str, citation_text: str | None = None) -> Verification:
    cfg = getattr(client, "cfg", None)
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
            return Verification(doi, "doi", "offline", message=_redact(str(exc), cfg))
        except NetworkError as exc:
            return Verification(doi, "doi", "error_red", message=_redact(str(exc), cfg))
    except OfflineError as exc:
        return Verification(doi, "doi", "offline", message=_redact(str(exc), cfg))
    except NetworkError as exc:
        return Verification(doi, "doi", "error_red", message=_redact(str(exc), cfg))

    checked = [rec.source] if rec.retracted is not None else []
    retracted = rec.retracted
    if retracted is not True:
        # Crossref solo marca la retractación en algunos registros: se confirma con otras fuentes.
        for name, check in (("openalex", _retraction_from_openalex), ("pubmed", _retraction_from_pubmed)):
            if name == rec.source:
                continue
            value = check(client, doi)
            if value is not None:
                checked.append(name)
                retracted = True if value else (False if retracted is None or retracted is False else retracted)
                if value:
                    break
    result = Verification(doi, "doi", "verificada", title=rec.title, year=rec.year, journal=rec.journal,
                          authors=rec.authors[:5], retracted=retracted, retraction_checked_via=checked)
    return _apply_citation(result, citation_text)


def verify_pmid(client, pmid: str, citation_text: str | None = None) -> Verification:
    cfg = getattr(client, "cfg", None)
    try:
        records = pubmed.summaries(client, [pmid])
    except OfflineError as exc:
        return Verification(pmid, "pmid", "offline", message=_redact(str(exc), cfg))
    except NetworkError as exc:
        return Verification(pmid, "pmid", "error_red", message=_redact(str(exc), cfg))
    if not records:
        return Verification(pmid, "pmid", "no_encontrada", message="PMID inexistente en PubMed")
    rec = records[0]
    # PubMed marca las publicaciones retractadas con el tipo "Retracted Publication": False es información real.
    result = Verification(pmid, "pmid", "verificada", title=rec.title, year=rec.year, journal=rec.journal,
                          authors=rec.authors[:5], retracted=bool(rec.retracted),
                          retraction_checked_via=["pubmed"])
    return _apply_citation(result, citation_text)


def verify_entry(client, entry_text: str) -> list[Verification]:
    """Verifica todos los identificadores de una entrada bibliográfica."""
    ids = extract_identifiers(entry_text)
    results = [verify_doi(client, d, entry_text) for d in ids["doi"]]
    results += [verify_pmid(client, p, entry_text) for p in ids["pmid"]]
    return results
