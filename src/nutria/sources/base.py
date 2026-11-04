"""Estructura común para registros bibliográficos y utilidades de normalización."""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field

_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/",
                 "doi.org/", "doi:", "doi ")

TAXA = {
    "vacuno": 9913, "bovino": 9913, "bos taurus": 9913,
    "porcino": 9823, "cerdo": 9823, "sus scrofa": 9823,
    "pollo": 9031, "aves": 9031, "gallus gallus": 9031,
    "ovino": 9940, "oveja": 9940, "ovis aries": 9940,
    "caprino": 9925, "cabra": 9925, "capra hircus": 9925,
    "salmon": 8030, "salmo salar": 8030,
    "trucha": 8022, "oncorhynchus mykiss": 8022,
    "humano": 9606, "homo sapiens": 9606,
    "raton": 10090, "mus musculus": 10090,
}

SCIENTIFIC_NAMES = {9913: "Bos taurus", 9823: "Sus scrofa", 9031: "Gallus gallus", 9940: "Ovis aries",
                    9925: "Capra hircus", 8030: "Salmo salar", 8022: "Oncorhynchus mykiss",
                    9606: "Homo sapiens", 10090: "Mus musculus"}


def taxon_id(value: str | int) -> int:
    """Convierte 'vacuno', 'Bos taurus' o '9913' en un identificador taxonómico NCBI."""
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text.isdigit():
        return int(text)
    if text in TAXA:
        return TAXA[text]
    raise ValueError(f"Especie desconocida: {value}. Usa un taxon NCBI numérico o uno de: {', '.join(sorted(TAXA))}")


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    text = doi.strip()
    lowered = text.lower()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.strip().rstrip(".,;")
    return text.lower() or None


_TAG_RE = re.compile(r"<[^>]+>")


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = html.unescape(_TAG_RE.sub("", str(value)))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def year_from(value) -> int | None:
    if value is None:
        return None
    match = re.search(r"(1[6-9]\d{2}|20\d{2})", str(value))
    return int(match.group(1)) if match else None


@dataclass
class Record:
    source: str
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    journal: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    openalex_id: str | None = None
    pub_types: list[str] = field(default_factory=list)
    abstract: str | None = None
    url: str | None = None
    cited_by: int | None = None
    is_oa: bool | None = None
    retracted: bool | None = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.doi = normalize_doi(self.doi)
        self.title = clean_text(self.title)
        self.authors = [a for a in (clean_text(x) for x in self.authors) if a]

    @property
    def key(self) -> str | None:
        if self.doi:
            return f"doi:{self.doi}"
        if self.pmid:
            return f"pmid:{self.pmid}"
        if self.openalex_id:
            return f"openalex:{self.openalex_id}"
        return None

    def to_dict(self) -> dict:
        return asdict(self)

    def citation(self) -> str:
        """Cita en formato compacto con identificadores verificables."""
        authors = ", ".join(self.authors[:3]) + (" et al." if len(self.authors) > 3 else "")
        parts = [authors or "[sin autores]", f"({self.year})" if self.year else "(s.f.)",
                 (self.title or "[sin título]").rstrip(".") + "."]
        if self.journal:
            loc = self.journal
            if self.volume:
                loc += f" {self.volume}"
                if self.issue:
                    loc += f"({self.issue})"
            if self.pages:
                loc += f":{self.pages}"
            parts.append(loc + ".")
        if self.doi:
            parts.append(f"DOI: {self.doi}")
        if self.pmid:
            parts.append(f"PMID: {self.pmid}")
        if self.retracted:
            parts.append("[RETRACTADO]")
        return " ".join(parts)
