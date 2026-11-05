"""Verificación de referencias bibliográficas (falla cerrado).

Comprueba lo que un LLM suele fallar:

1. Que el identificador existe. Un DOI se busca en Crossref y OpenAlex y, si ninguno lo conoce, en el registro
   de DOI (doi.org: API de handles y agencia de registro) y, si es de DataCite, en su API; un DOI de otra agencia
   también puede tener metadatos en PubMed. Solo se declara inexistente si doi.org no lo tiene registrado. Un DOI
   registrado del que ninguna fuente da metadatos comparables (p. ej. de mEDRA o JaLC fuera de PubMed) queda
   PENDIENTE (`existe_sin_metadatos`), nunca verificado. Un PMID se busca en PubMed.
2. Que corresponde al trabajo citado (`compare_citation`). Es una heurística, no una garantía; la cita se lee
   como [autores] [año] [título] [revista, volumen, páginas] en cualquiera de los estilos habituales:
   - primer autor: el primer nombre de la cita debe ser el PRIMER autor del registro, no uno cualquiera;
   - año: el de la cita (sin contar identificadores, volumen, páginas, fechas de consulta ni el propio
     título) debe estar a ±1 año del real;
   - título: si la cita lo trae, se compara de forma simétrica (palabras significativas normalizadas en los
     dos sentidos; ver `TITLE_MIN`), sin admitir sustituciones de palabras; y no puede haber delante texto que no
     sea de los autores del registro ni el año (un título inventado delante del real);
   - revista: si la cita la trae, el primer tramo con contenido tras el título (o tras los autores, si no hay
     título) debe ser la revista del registro, con tolerancia a abreviaturas ISO 4 ("J Dairy Sci" = "Journal
     of Dairy Science"); no se compara en libros, capítulos, actas ni conjuntos de datos.
3. Que el trabajo no está retractado, con una cadena de comprobación (Crossref, OpenAlex, PubMed). Si ninguna
   fuente permite saberlo, la retractación queda como DESCONOCIDA y la verificación no es válida.

No comprueba si la fuente respalda la afirmación concreta: eso se registra en la tabla de respaldo del Anexo A
del informe (agente `verificador-citas`), que la comprobación del informe exige.
"""

from __future__ import annotations

import bisect
import html
import re
import unicodedata
from dataclasses import dataclass, field, fields
from urllib.parse import unquote

from ..http import NetworkError, NotFoundError, OfflineError
from ..sources import crossref, doiorg, openalex, pubmed
from ..sources.base import Record, normalize_doi

try:  # el paquete de fuentes aporta la redacción de secretos
    from ..http import redact as _redact
except ImportError:  # pragma: no cover
    def _redact(text, cfg=None):  # type: ignore[no-redef]
        return text

# ============================================================================ identificadores
# Un DOI es "10.<registrante>/<sufijo>". El sufijo admite casi cualquier carácter: se excluyen los espacios, las
# comillas y los delimitadores de Markdown/HTML. '<' y '>' solo se aceptan como segmento SICI de los DOI antiguos
# de Wiley y de la AMS ("...75:1<1::AID-JSFA834>3.0.CO;2-#"): un '<' seguido de una cifra o de ':' y cerrado
# por '>' sin espacios. Así una etiqueta HTML (<br>, </a>) o un enlace automático <https://doi.org/...> no se
# toman por parte del DOI. '#', ';', '(' y ')' forman parte del DOI; la puntuación final se quita después
# (`clean_doi`).
_DOI_CHAR = r"[^\s\"<>\[\]{}|`\\^]"
_SICI_SEGMENT = r"<[0-9:][^\s\"<>\[\]{}|`\\^]*>"
DOI_RE = re.compile(rf"(?<![\w])(10\.\d{{4,9}}/(?:{_SICI_SEGMENT}|{_DOI_CHAR})+)", re.IGNORECASE)
PMID_RE = re.compile(r"\bPMID:?\s*(\d{5,9})\b", re.IGNORECASE)
PMCID_RE = re.compile(r"\b(PMC\d{4,9})\b")
URL_RE = re.compile(r"https?://\S+")
YEAR_RE = re.compile(r"(?<![\w])(1[6-9]\d{2}|20\d{2})(?!\d)")
PAGES_RE = re.compile(r"(?<!\d)\d{1,6}\s*[-–]\s*\d{1,6}(?!\d)")
VOLUME_PAGES_RE = re.compile(r"\b\d+\s*\(\s*[\w\-]+\s*\)\s*:\s*[\w\-–]+")

_ESCAPED_MD_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!<>])")      # 10.1000/a\_b escrito en Markdown
_TRAILING = ".,;:'\"!?*_”’»›"                                   # puntuación de la frase que sigue al DOI
_CLOSERS = {")": "(", "]": "[", ">": "<", "}": "{"}
# Etiquetas HTML/XML (<i>, </sub>, <mml:math ...>): una letra tras '<' o '</'; "< 0,05" o "<5 %" no lo son.
_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9:_-]*(?:\s[^<>]*)?/?>")


def _unescape(text: str) -> str:
    """Texto de la cita sin entidades HTML (&lt; &gt; &amp; de un DOI SICI escrito en Markdown o HTML) ni escapes
    de Markdown (10.1000/a\\_b)."""
    return _ESCAPED_MD_RE.sub(r"\1", html.unescape(text or ""))


def _trim_trailing(raw: str) -> str:
    """Quita la puntuación final de la frase y los cierres desequilibrados (')' sin su '(' en el DOI)."""
    doi = (raw or "").strip()
    while doi:
        last = doi[-1]
        if last in _TRAILING:
            doi = doi[:-1]
        elif last in _CLOSERS and doi.count(last) > doi.count(_CLOSERS[last]):
            doi = doi[:-1]
        else:
            break
    return doi


def clean_doi(raw: str) -> str:
    doi = _trim_trailing(raw)
    return normalize_doi(doi) or doi


def _doi_from_match(text: str, match: re.Match, url_spans: list[tuple[int, int]]) -> str:
    raw = match.group(1)
    if any(start <= match.start(1) < end for start, end in url_spans):
        # Dentro de una URL, '?' abre la consulta y '#' el fragmento: los que forman parte del DOI van
        # codificados (%3F, %23). Solo se conserva un '#' final (dígito de control de un SICI sin codificar).
        raw = _trim_trailing(raw).split("?", 1)[0]
        if "#" in raw[:-1]:
            raw = raw[:raw.index("#")]
        raw = unquote(raw)
    return clean_doi(raw)


def extract_identifiers(text: str) -> dict[str, list[str]]:
    text = _unescape(text)
    url_spans = [m.span() for m in URL_RE.finditer(text)]
    dois: list[str] = []
    for match in DOI_RE.finditer(text):
        doi = _doi_from_match(text, match, url_spans)
        if doi and doi not in dois:
            dois.append(doi)
    pmids = list(dict.fromkeys(PMID_RE.findall(text)))
    pmcids = list(dict.fromkeys(PMCID_RE.findall(text)))
    return {"doi": dois, "pmid": pmids, "pmcid": pmcids}


# ============================================================================ normalización
_FOLD = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ζ": "zeta", "η": "eta",
         "θ": "theta", "κ": "kappa", "λ": "lambda", "μ": "mu", "µ": "mu", "π": "pi", "σ": "sigma",
         "τ": "tau", "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega", "ß": "ss", "æ": "ae", "œ": "oe",
         "ø": "o", "ł": "l", "đ": "d", "ð": "d", "þ": "th"}
_WORD_RE = re.compile(r"[^\W_]+")

_STOPWORDS = frozenset({
    # inglés
    "a", "an", "the", "of", "in", "on", "at", "to", "by", "as", "or", "is", "be", "it", "if", "so", "up", "we",
    "vs", "and", "for", "with", "from", "into", "onto", "that", "this", "these", "those", "their", "its", "are",
    "was", "were", "has", "have", "had", "can", "may", "does", "did", "but", "than", "via", "upon", "which",
    "versus",
    # español, francés, portugués, italiano y alemán
    "el", "la", "los", "las", "lo", "un", "una", "unos", "unas", "de", "del", "al", "en", "con", "por", "para",
    "sobre", "entre", "como", "que", "su", "sus", "es", "son", "le", "les", "des", "du", "et", "au", "aux",
    "une", "dans", "par", "sur", "da", "do", "das", "dos", "em", "um", "uma", "di", "della", "dei", "degli",
    "der", "die", "und", "von", "mit", "im", "zur", "zum", "bei",
})
_PARTICLES = frozenset({"de", "del", "della", "der", "den", "di", "da", "das", "dos", "do", "du", "la", "le",
                        "van", "von", "ter", "ten", "st", "ste", "saint", "san", "mac", "mc", "bin", "ibn", "al",
                        "el", "y", "e"})
# Palabras de la cita que no son del título ni de la revista ni del autor.
_NOISE = frozenset({"et", "al", "eds", "ed", "editor", "editors", "editores", "coord", "comp", "in", "en",
                    "available", "disponible", "online", "internet", "accessed", "cited", "retrieved", "vol",
                    "volume", "no", "num", "pp", "pages", "page", "suppl", "supplement", "issue", "doi", "pmid",
                    "pmcid", "epub", "ahead", "print", "published", "art", "article", "from", "url", "http",
                    "https", "www", "org", "linea"})
_LEAD_CONJ = frozenset({"and", "y", "et", "e", "und", "en"})
# Cabeceras de sección que algunas revistas anteponen al título ("Invited review:", "Short communication:", J Anim
# Sci "Special topics—", "Board-invited review:", "Ruminant Nutrition Symposium:"): la cita puede traerlas o no.
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:short\s+communication|technical\s+note|(?:board[\s-]+)?invited\s+review|symposium\s+review|"
    r"graduate\s+student\s+literature\s+review|review(?:\s+article)?|mini[\s-]?review|research\s+note|"
    r"research\s+article|original\s+article|brief\s+(?:report|communication)|rapid\s+communication|"
    r"letter(?:\s+to\s+the\s+editor)?|editorial|retracted(?:\s+article)?|retraction|withdrawn|"
    r"expression\s+of\s+concern|special\s+topics?|(?:[a-z][\w/&-]*\s+){0,3}symposium)\s*[:.\-–—]\s*",
    re.IGNORECASE)
_LANGUAGES = (r"spanish|english|french|german|portuguese|italian|chinese|japanese|russian|polish|dutch|turkish|"
              r"korean|czech|espa[nñ]ol|ingl[eé]s|franc[eé]s|alem[aá]n|portugu[eé]s|italiano|chino|japon[eé]s")
_ANNOTATION_RE = re.compile(
    r"[\[(]\s*(?:review|revisi[oó]n|abstract|resumen|(?:article|art[ií]culo)\s+(?:in|en)\s+\w+|"
    rf"(?:in|en)\s+(?:{_LANGUAGES})\b[^\])\[(]*|internet|online|en\s+l[ií]nea|epub[^\])\[(]*|"
    r"published\s+online[^\])\[(]*|cited[^\])\[(]*|consultad[oa][^\])\[(]*|accessed[^\])\[(]*|"
    r"accedido[^\])\[(]*|letter|carta|editorial|erratum[^\])\[(]*|corrigendum|preprint|open\s+access)"
    r"\s*[\])]", re.IGNORECASE)
_ACCESS_RE = re.compile(r"(?i)\b(?:accessed|consultad[oa]|retrieved|cited|visto|accedido|fecha\s+de\s+consulta)"
                        r"\b[^.;\])]*")
_MARKER_RE = re.compile(r"^\s*(?:[-*+]\s+|\d{1,3}[.)]\s+)?(?:\[R\d+\]\s*[:.\-–]?\s*)?", re.IGNORECASE)
_LABEL_RE = re.compile(r"\b(?:doi|pmid|pmcid)\b\s*:?", re.IGNORECASE)
_BOUNDARY_RE = re.compile(
    r"[.?!]+[\"'”’»)\]*_]*\s+"                                    # fin de frase
    r"|\(\s*(?:1[6-9]|20)\d{2}[a-z]?\s*\)"                         # (2020) o (2020a)
    r"|(?<=[,;])\s*(?:1[6-9]|20)\d{2}[a-z]?\s*(?=[,.;:])")         # , 2020,


_MONTHS = frozenset({
    "jan", "january", "feb", "february", "mar", "march", "apr", "april", "jun", "june", "jul", "july", "aug",
    "august", "sep", "sept", "september", "oct", "october", "nov", "november", "dec", "december", "enero",
    "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "setiembre", "octubre",
    "noviembre", "diciembre", "ene", "abr", "ago", "dic", "spring", "summer", "autumn", "winter"})
# Palabras de los prefijos de título ("Short communication:", "Invited review:"...): pueden ir antes del título.
_TITLE_PREFIX_WORDS = frozenset({
    "short", "communication", "technical", "note", "invited", "review", "symposium", "graduate", "student",
    "literature", "mini", "minireview", "research", "original", "article", "brief", "report", "rapid", "letter",
    "editor", "editorial", "retracted", "retraction", "withdrawn", "expression", "concern", "special", "topics",
    "topic", "board"})


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").lower()
    text = "".join(_FOLD.get(ch, ch) for ch in text)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _clean_meta(text) -> str:
    """Metadato del registro (título, revista, autor) sin etiquetas JATS/MathML (CH<sub>4</sub> -> CH4) ni
    entidades HTML (&amp;): Crossref las trae en subtítulos y títulos de revista."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", _TAG_RE.sub("", html.unescape(str(text)))).strip()


def _name_norm(norm: str) -> str:
    """Forma de comparación de un apellido: transliteraciones alemanas y nórdicas (Müller = Mueller,
    Schröder = Schroeder). Se aplica a los dos lados de la comparación."""
    return norm.replace("ae", "a").replace("oe", "o").replace("ue", "u")


@dataclass
class _Tok:
    raw: str
    norm: str
    start: int
    end: int
    seg: int = 0


def _tokens_of(text: str) -> list[_Tok]:
    out = []
    for match in _WORD_RE.finditer(text or ""):
        norm = re.sub(r"[^a-z0-9]+", "", _fold(match.group(0)))
        if norm:
            out.append(_Tok(match.group(0), norm, match.start(), match.end()))
    return out


def _is_sig(norm: str) -> bool:
    """Palabra significativa: al menos 2 caracteres, no solo cifras y no vacía de contenido."""
    return len(norm) >= 2 and not norm.isdigit() and norm not in _STOPWORDS


def _has_digit(norm: str) -> bool:
    return any(ch.isdigit() for ch in norm)


def _is_initial(raw: str) -> bool:
    """Inicial de un nombre: 1 a 3 letras mayúsculas ("J", "JD", "ABC")."""
    return raw.isalpha() and raw.isupper() and len(raw) <= 3


def _strip_identifiers(text: str) -> str:
    text = URL_RE.sub(" ", text or "")
    text = DOI_RE.sub(" ", text)
    text = PMID_RE.sub(" ", text)
    text = PMCID_RE.sub(" ", text)
    return _LABEL_RE.sub(" ", text)


def _clean_citation(text: str | None) -> str:
    """Texto de la cita sin marcador [Rn], identificadores, URL, etiquetas HTML (CH<sub>4</sub> -> CH4) ni
    anotaciones ("[Review]", "(in Spanish)")."""
    text = unicodedata.normalize("NFC", _unescape(text))
    text = _MARKER_RE.sub("", text, count=1)
    text = _strip_identifiers(text)
    text = _TAG_RE.sub("", text)
    return _ANNOTATION_RE.sub(" ", text)


def _year_from_clean(text: str, exclude: tuple[int, int] | None = None) -> int | None:
    if exclude:
        text = text[:exclude[0]] + " " * (exclude[1] - exclude[0]) + text[exclude[1]:]
    text = _ACCESS_RE.sub(" ", text)
    text = VOLUME_PAGES_RE.sub(" ", text)
    text = PAGES_RE.sub(" ", text)
    match = YEAR_RE.search(text)
    return int(match.group(1)) if match else None


def citation_year(citation_text: str | None) -> int | None:
    """Año de la cita: el primero que aparece tras quitar identificadores, volumen(número):páginas, rangos de
    páginas y fechas de consulta (así un "2019" dentro del DOI o unas páginas 2007-2016 no se toman por el año).
    La verificación excluye además el título (`compare_citation`)."""
    if not citation_text:
        return None
    return _year_from_clean(_clean_citation(citation_text))


# ============================================================================ títulos
# Umbral de la similitud de títulos, en los dos sentidos. Con TITLE_MIN = 0,9 la cita puede omitir como mucho
# el 10 % de las palabras significativas del título real (una de cada diez) o añadir como mucho un 10 %, pero
# no las dos cosas a la vez: una palabra cambiada por otra (sustitución) es lo que distingue dos trabajos del
# mismo grupo y tema ("... rumen-protected choline ..." frente a "... rumen-protected methionine ..."), así que
# siempre es discrepancia. Solo se emparejan antes, sin contar como sustitución, las variantes SISTEMÁTICAS:
# ortografía británica y estadounidense (fibre/fiber, oestrus/estrus, sulphur/sulfur, -ise/-ize), singular y
# plural (study/studies) y compuestos (rumen-protected / rumenprotected). No se toleran erratas ni palabras
# "parecidas": nitrate/nitrite o choline/chlorine son trabajos distintos. Las menciones de edición ("8th ed.",
# "Eighth Revised Edition") no cuentan: la edición la distingue el año. El umbral anterior (0,6, asimétrico)
# dejaba pasar un título distinto del mismo tema con el 73 % de palabras comunes.
TITLE_MIN = 0.9
# Las cifras y las negaciones no entran en esa tolerancia: una que falte o sobre es siempre discrepancia
# (`TitleComparison.critical_differ`).
_CRITICAL_WORDS = frozenset({"not", "no", "non", "nor", "neither", "never", "without", "sin", "ni", "nunca", "nao",
                             "sem", "nicht", "kein", "keine", "keinen", "ohne", "pas", "sans", "senza", "nessun"})

_ORDINALS = (r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|"
             r"primera|segunda|tercera|cuarta|quinta|sexta|s[eé]ptima|octava|novena|d[eé]cima")
_EDITION_RE = re.compile(
    rf"\b(?:(?:\d+\s*(?:st|nd|rd|th|\.?\s*[ªa])|{_ORDINALS})\.?\s+)?(?:rev(?:ised)?\.?\s+|revisada\s+)?"
    r"(?:ed(?:ition|ici[oó]n)?\b\.?)(?:\s+rev(?:ised|isada)?\b\.?)?", re.IGNORECASE)


def _strip_title_prefix(text: str) -> str:
    return _TITLE_PREFIX_RE.sub("", text or "", count=1)


def _strip_edition(text: str) -> str:
    """Quita las menciones de edición ("8th ed.", "Eighth Revised Edition", "2.ª edición") de un título; solo las
    que llevan la palabra edición (ed., edition, edición), para no tocar "Second" o "Revised" sueltos."""
    return _EDITION_RE.sub(" ", text or "")


# Palabras tras las que una letra suelta distingue el trabajo: "vitamin D" frente a "vitamin E", "Part A" frente a
# "Part B", "type I" frente a "type II". La letra cuenta unida a esa palabra ("vitamin_d"); el resto de letras
# sueltas no cuenta (iniciales, "E. coli", artículos y conjunciones de una letra).
_LETTER_HEADS = frozenset({
    "vitamin", "vitamins", "vitamina", "vitaminas", "type", "types", "tipo", "part", "parte", "phase", "fase",
    "experiment", "experimento", "trial", "study", "group", "grupo", "factor", "serotype", "serogroup", "serovar",
    "class", "clase", "grade", "stage", "line", "strain", "series", "section", "chapter", "appendix", "annex",
    "subunit", "complex", "protein", "receptor", "fraction", "fraccion"})


def _is_qualifier(toks: list[_Tok]) -> bool:
    """¿Es la última palabra de `toks` una letra que califica a la anterior ("vitamin D", "Part B")?"""
    return (len(toks) >= 2 and len(toks[-1].norm) == 1 and toks[-1].norm.isalpha()
            and toks[-2].norm in _LETTER_HEADS)


def _title_word(norm: str) -> bool:
    """Palabra que cuenta al comparar títulos: las significativas y también las cifras, porque "50 g/d" frente a
    "100 g/d" o "Experiment 1" frente a "Experiment 2" son trabajos distintos."""
    return _is_sig(norm) or norm.isdigit()


def _title_tokens(text: str | None) -> list[str]:
    toks = _tokens_of(_strip_edition(_clean_meta(text)))
    out = []
    for i, tok in enumerate(toks):
        if _title_word(tok.norm):
            out.append(tok.norm)
        elif len(tok.norm) == 1 and tok.norm.isalpha() and i and toks[i - 1].norm in _LETTER_HEADS:
            out.append(f"{toks[i - 1].norm}_{tok.norm}")
    return out


@dataclass
class TitleComparison:
    recall: float              # fracción de palabras del título real presentes en la cita
    precision: float           # fracción de palabras del título citado presentes en el real
    missing: list[str]         # palabras del título real que faltan en la cita
    extra: list[str]           # palabras de la cita que no están en el título real

    @property
    def score(self) -> float:
        if not self.recall or not self.precision:
            return 0.0
        return 2 * self.recall * self.precision / (self.recall + self.precision)

    @property
    def critical_differ(self) -> bool:
        """Una cifra (o palabra con cifras: "50", "CH4", "D3") o una negación ("not", "without", "sin") de un título
        que falta en el otro: otra dosis, otro experimento u otro año son otro trabajo, y quitar o poner una negación
        invierte lo que dice el título, aunque el resto coincida."""
        return any(_has_digit(w) or w in _CRITICAL_WORDS for w in self.missing + self.extra)

    @property
    def ok(self) -> bool:
        """Mismo título: ≥ TITLE_MIN en los dos sentidos, sin sustituciones y con las mismas cifras y negaciones."""
        return (self.recall >= TITLE_MIN and self.precision >= TITLE_MIN
                and not (self.missing and self.extra) and not self.critical_differ)

    @property
    def same_work(self) -> bool:
        """Criterio para fusionar registros: un título contiene al otro (p. ej. con o sin subtítulo), comparten al
        menos la mitad de las palabras y no hay sustituciones."""
        return not (self.missing and self.extra) and min(self.recall, self.precision) >= 0.5


def _pair_compounds(missing: list[str], extra: list[str], real: list[str], cited: list[str]):
    for pool, other, seq in ((extra, missing, real), (missing, extra, cited)):
        for word in list(pool):
            for size in (2, 3):
                for i in range(len(seq) - size + 1):
                    parts = seq[i:i + size]
                    if "".join(parts) == word and all(p in other for p in parts):
                        pool.remove(word)
                        for p in parts:
                            other.remove(p)
                        break
                else:
                    continue
                break
    return missing, extra


_UK_US_WORDS = {"mould": "mold", "moulds": "molds", "mouldy": "moldy", "grey": "gray", "aluminium": "aluminum",
                "plough": "plow", "programme": "program", "programmes": "programs", "tyre": "tire",
                "ageing": "aging", "yoghurt": "yogurt", "yoghurts": "yogurts", "draught": "draft"}
_UK_US_SUFFIXES = (("isation", "ization"), ("ising", "izing"), ("ised", "ized"), ("ise", "ize"), ("ysing", "yzing"),
                   ("ysed", "yzed"), ("yse", "yze"), ("ogue", "og"), ("ence", "ense"), ("chre", "cher"),
                   ("tre", "ter"), ("bre", "ber"), ("gre", "ger"), ("vre", "ver"), ("our", "or"))


def _us_spelling(word: str) -> str:
    """Ortografía estadounidense de una palabra británica (ae/oe -> e, ph -> f, ll -> l, leuc- -> leuk-, -ise -> -ize,
    -tre/-bre -> -ter/-ber: fibre = fiber, titre = titer; -our -> -or...). Se aplica a las dos palabras comparadas,
    así que solo importa que coincidan."""
    w = word.replace("ae", "e").replace("oe", "e").replace("ph", "f").replace("ll", "l")
    if w.startswith("leuc"):
        w = "leuk" + w[4:]
    for uk, us in _UK_US_SUFFIXES:
        if w.endswith(uk):
            return w[:-len(uk)] + us
    return w


def _loose_forms(word: str) -> set[str]:
    """Formas canónicas de una palabra: la propia y, si parece un plural, sus singulares posibles (studies ->
    study, analyses -> analysis, doses -> dose), todas en ortografía estadounidense."""
    w = _UK_US_WORDS.get(word, word)
    stems = {w}
    if len(w) > 4 and w.endswith("ies"):
        stems.add(w[:-3] + "y")
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        stems.add(w[:-1])
        if w.endswith("es"):
            stems.add(w[:-2])
            if w.endswith("ses"):
                stems.add(w[:-3] + "sis")
    return {_us_spelling(_UK_US_WORDS.get(s, s)) for s in stems}


def _spelling_variant(a: str, b: str) -> bool:
    """¿Son `a` y `b` la misma palabra con otra ortografía o en plural? Solo variantes sistemáticas
    (`_loose_forms`): nunca palabras simplemente parecidas (nitrate/nitrite, choline/chlorine, goats/goals)."""
    return a == b or bool(_loose_forms(a) & _loose_forms(b))


def _compare(real: list[str], cited: list[str]) -> TitleComparison:
    a_list, b_list = list(dict.fromkeys(real)), list(dict.fromkeys(cited))
    if not a_list or not b_list:
        return TitleComparison(0.0, 0.0, a_list, b_list)
    a_set, b_set = set(a_list), set(b_list)
    missing = [t for t in a_list if t not in b_set]
    extra = [t for t in b_list if t not in a_set]
    missing, extra = _pair_compounds(missing, extra, real, cited)
    for word in list(extra):
        match = next((m for m in missing if _spelling_variant(word, m)), None)
        if match is not None:
            extra.remove(word)
            missing.remove(match)
    return TitleComparison(1 - len(missing) / len(a_list), 1 - len(extra) / len(b_list), missing, extra)


def compare_titles(real: str | None, cited: str | None) -> TitleComparison | None:
    """Compara dos títulos (también sin y con prefijo "Short communication:", "Review:"...)."""
    best = None
    for r in {real or "", _strip_title_prefix(real or "")}:
        for c in {cited or "", _strip_title_prefix(cited or "")}:
            ra, ca = _title_tokens(r), _title_tokens(c)
            if not ra or not ca:
                continue
            cmp = _compare(ra, ca)
            if best is None or (cmp.ok, cmp.score) > (best.ok, best.score):
                best = cmp
    return best


def title_similarity(a: str | None, b: str | None) -> float | None:
    """Similitud simétrica (F1 de palabras significativas, 0-1) entre dos títulos."""
    cmp = compare_titles(a, b)
    return None if cmp is None else cmp.score


def titles_same_work(a: str | None, b: str | None) -> bool:
    """¿Son títulos del mismo trabajo? (criterio de fusión de registros; ver `TitleComparison.same_work`)."""
    cmp = compare_titles(a, b)
    return bool(cmp and cmp.same_work)


# ============================================================================ autores
def _author_candidates(name: str | None, family: str | None = None, order: str | None = None) -> set[str]:
    """Formas normalizadas del apellido de un autor del registro (Crossref "Apellido Nombre", PubMed
    "Apellido AB", OpenAlex "Nombre Apellido"); con el apellido explícito (`family`) solo se usa ese. Con
    `order="given_family"` (OpenAlex: "Juan Carlos García Pérez") el primer nombre no cuenta como apellido, para
    que una cita de "Juan Smith" no pase por el nombre de pila de "Juan Pérez"."""
    def pick(tokens: list[_Tok]) -> list[str]:
        return [t.norm for t in tokens if len(t.norm) >= 2 and not _is_initial(t.raw)
                and t.norm not in _PARTICLES and t.norm not in _STOPWORDS]
    if family:
        found = pick(_tokens_of(_clean_meta(family)))
        if found:
            return {_name_norm(w) for w in found}
    tokens = _tokens_of(_clean_meta(name))
    words = pick(tokens)
    if order == "given_family" and len(words) >= 2:
        words = words[1:]
    return {_name_norm(w) for w in words} or {_name_norm(t.norm) for t in tokens if len(t.norm) >= 2}


def _all_author_words(authors) -> set[str]:
    """Todas las palabras de los nombres del registro (apellidos y nombres de pila): no son texto ajeno a la cita."""
    return {_name_norm(t.norm) for name in authors or [] for t in _tokens_of(_clean_meta(name))
            if len(t.norm) >= 2}


def _author_parts(name: str | None, family: str | None = None, order: str | None = None) -> tuple[set[str], str]:
    """(apellidos, iniciales del nombre de pila) de un autor del registro: PubMed "Pérez JC" -> ({"perez"}, "jc");
    Crossref "Pérez Juan" con apellido "Pérez" -> ({"perez"}, "j"); OpenAlex "Juan Pérez" -> ({"perez"}, "j")."""
    families = _author_candidates(name, family, order)
    initials = ""
    for tok in _tokens_of(_clean_meta(name)):
        if _name_norm(tok.norm) in families or tok.norm in _PARTICLES or not tok.raw[:1].isalpha():
            continue
        initials += tok.norm if _is_initial(tok.raw) else tok.norm[0]
    return families, initials


def _given_names(text: str, toks: list[_Tok], parts: list[tuple[set[str], str]]) -> set[int]:
    """Posiciones de `toks` que son nombres de pila escritos completos de un autor del registro que solo trae sus
    iniciales (cita "Pérez, Juan, Ana García" frente a PubMed "Pérez J", "García A"): palabras pegadas a su apellido,
    tantas como iniciales tiene y cada una con una de ellas como inicial. Detrás del apellido ("Pérez, Juan Carlos")
    van tras coma o espacio; delante ("Ana García", "y Luis López"), el primer nombre debe ir al principio o tras una
    coma o una conjunción, para que una palabra cualquiera pegada a un apellido ("... Laminitis Avocado García") no
    pase por nombre de pila."""
    found: set[int] = set()

    def chain(i: int, step: int, initials: str) -> list[int]:
        out, j = [], i + step
        while 0 <= j < len(toks) and len(out) < len(initials):
            a, b = (toks[j - 1], toks[j]) if step == 1 else (toks[j], toks[j + 1])
            if not re.fullmatch(r"\s*,?\s*" if (step == 1 and not out) else r"[\s-]*", text[a.end:b.start]):
                break
            if toks[j].norm[:1] not in initials or not toks[j].raw[:1].isupper() or len(toks[j].norm) < 2:
                break
            out.append(j)
            j += step
        return out

    for families, initials in parts:
        if not initials:
            continue
        for i, tok in enumerate(toks):
            if _name_norm(tok.norm) not in families:
                continue
            found.update(chain(i, 1, initials))
            before = chain(i, -1, initials)
            if before:
                f = before[-1]
                if f == 0 or re.search(r"[,;&]", text[toks[f - 1].end:toks[f].start]) or toks[f - 1].norm in _LEAD_CONJ:
                    found.update(before)
    return found


def _acronym(name: str | None) -> str | None:
    """Sigla de un autor institucional ("European Food Safety Authority" -> "efsa")."""
    words = [t.norm for t in _tokens_of(name or "") if _is_sig(t.norm) and t.raw[:1].isupper()]
    return "".join(w[0] for w in words) if len(words) >= 3 else None


def _lead_tokens(text: str, toks: list[_Tok]) -> list[_Tok]:
    """Primer autor de la cita: palabras del principio hasta la primera coma, punto y coma, '&', paréntesis,
    cifra, conjunción ("and", "y", "et al."), palabra en minúscula (salvo partículas) o fin de frase. Tras
    apellido e iniciales, otro apellido seguido de sus iniciales es el segundo autor: ni "Smith J. Pérez J." ni
    "Smith J Pérez J" hacen de Pérez el primer autor; "Juan A. Pérez" (nombre, inicial y apellido) sí es uno."""
    lead: list[_Tok] = []
    prev_end = 0
    separator = r"[,;&(\[:]|[.?!]\s"
    for idx, tok in enumerate(toks):
        gap = text[prev_end:tok.start]
        if lead:
            if re.search(r"[,;&(\[:]", gap):
                break
            if re.search(r"[.?!]\s", gap + " ") and not _is_initial(lead[-1].raw):
                break
            if (_is_initial(lead[-1].raw) and not _is_initial(tok.raw)
                    and any(not _is_initial(t.raw) for t in lead)):
                nxt = toks[idx + 1] if idx + 1 < len(toks) else None
                if nxt is not None and _is_initial(nxt.raw) and not re.search(separator, text[tok.end:nxt.start] + " "):
                    break             # apellido + iniciales + otro apellido con iniciales: segundo autor
        if _has_digit(tok.norm) or tok.norm in _LEAD_CONJ:
            break
        if tok.raw[:1].islower() and tok.norm not in _PARTICLES:
            break
        lead.append(tok)
        prev_end = tok.end
        if len(lead) >= 8:
            break
    return lead


# ============================================================================ revistas
_JOURNAL_STOP = frozenset({"of", "the", "and", "for", "in", "on", "de", "la", "le", "les", "des", "du", "et",
                           "y", "e", "und", "fur", "der", "die", "das", "del", "della", "di", "el", "los", "las",
                           "an", "a", "da", "do", "dos", "part", "section"})
_JOURNAL_NOISE = frozenset({"internet", "online", "print", "electronic", "linea", "vol", "volume", "no", "pp",
                            "suppl", "issue"})
# Palabras que cortan el tramo de la revista (identificadores, volumen, páginas, fechas de consulta).
_JOURNAL_END = frozenset({"doi", "pmid", "pmcid", "http", "https", "www", "epub", "vol", "volume", "pp",
                          "pages", "page", "suppl", "issue", "available", "disponible", "accessed", "retrieved"})
_JOURNAL_LEAD_NOISE = frozenset({"in", "en"})
# Notas que pueden ir entre el título y la revista y no son una revista ("Review.", "In press.", "Spanish.").
_NOT_A_JOURNAL = frozenset({
    "review", "erratum", "corrigendum", "comment", "published", "abstract", "editorial", "letter", "retracted",
    "retraction", "preprint", "press", "accepted", "manuscript", "corrected", "proof", "first", "early", "view",
    "advance", "access", "ahead", "of", "spanish", "english", "french", "german", "portuguese", "italian",
    "chinese", "japanese", "russian", "polish", "dutch", "turkish", "korean", "czech", "espanol", "ingles",
    "frances", "aleman", "portugues", "italiano", "chino", "japones"})
_NON_JOURNAL_TYPES = frozenset({
    "bookchapter", "book", "monograph", "editedbook", "referencebook", "referenceentry", "bookpart", "booksection",
    "bookset", "bookseries", "proceedingsarticle", "proceedings", "conferencepaper", "conferenceproceeding",
    "report", "reportseries", "dataset", "postedcontent", "preprint", "dissertation", "thesis", "standard",
    "component", "peerreview", "paratext", "software", "collection", "image", "audiovisual", "model",
    "workflow", "other"})


# Calificativo de país al final del nombre: PNAS es "Proceedings of the National Academy of Sciences" para
# Crossref, "... of the United States of America" en su título completo y "Proc Natl Acad Sci U S A" en NLM.
_COUNTRY_TAILS = (("u", "s", "a"), ("usa",), ("united", "states", "of", "america"))


def _journal_words(text: str, abbreviated: bool) -> list[str]:
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text or "")
    toks = _tokens_of(text)
    norms = [t.norm for t in toks]
    for tail in _COUNTRY_TAILS:
        if len(norms) > len(tail) and tuple(norms[-len(tail):]) == tail:
            toks = toks[:-len(tail)]
            break
    out = []
    for tok in toks:
        word = tok.norm
        if word in _JOURNAL_NOISE or word.isdigit():
            continue
        # En la forma abreviada se conservan las letras sueltas ("U S A"); el resto de palabras vacías, no.
        if word in _JOURNAL_STOP and not (abbreviated and len(word) == 1):
            continue
        out.append(word)
    return out


def _abbreviates(abbr: str, word: str) -> bool:
    """`abbr` abrevia `word` según ISO 4: misma inicial y letras en el mismo orden (Sci/Science, Natl/National)."""
    if not abbr or not word or abbr[0] != word[0]:
        return False
    rest = iter(word)
    return all(ch in rest for ch in abbr)


def _align(abbr: list[str], full: list[str]) -> bool:
    memo: dict[tuple[int, int], bool] = {}

    def go(i: int, j: int) -> bool:
        if (i, j) in memo:
            return memo[(i, j)]
        if i == len(abbr):
            result = j == len(full)
        else:
            result = (j < len(full) and _abbreviates(abbr[i], full[j]) and go(i + 1, j + 1)) or \
                     (len(abbr[i]) == 1 and abbr[i] in _JOURNAL_STOP and go(i + 1, j))
        memo[(i, j)] = result
        return result
    return go(0, 0)


_ACRONYM_LEAD_RE = re.compile(r"^\s*([A-Z][A-Z0-9&]{2,})\s*[,:–—-]\s*(\S.*)$")


def _journal_forms(name: str | None) -> list[str]:
    """El nombre y, si empieza por una sigla seguida de coma o dos puntos ("ITEA, Información Técnica Económica
    Agraria"), la sigla y el resto por separado."""
    forms = [name or ""]
    match = _ACRONYM_LEAD_RE.match(name or "")
    if match:
        forms += [match.group(1), match.group(2)]
    return forms


def journal_matches(cited: str | None, full: str | None) -> bool:
    """¿Nombra `cited` la misma revista que `full`? Acepta el nombre completo, la abreviatura ISO 4 ("J Dairy
    Sci", "J. Dairy Sci.") o la sigla ("JDS"), en cualquiera de los dos sentidos. Un título de una sola palabra
    ("Animal", "Animals", "Nutrients") debe coincidir exactamente, porque ISO 4 no lo abrevia."""
    return any(_journal_match(c, f) for c in _journal_forms(cited) for f in _journal_forms(full))


def _journal_match(cited: str, full: str) -> bool:
    for x, y in ((cited, full), (full, cited)):
        short, long_ = _journal_words(x or "", abbreviated=True), _journal_words(y or "", abbreviated=False)
        if not short or not long_:
            continue
        if [w for w in short if not (len(w) == 1 and w in _JOURNAL_STOP)] == long_:
            return True
        if len(long_) == 1:
            continue
        if len(short) == 1 and len(short[0]) >= 3 and short[0] == "".join(w[0] for w in long_):
            return True
        if _align(short, long_):
            return True
    return False


def _journal_variants(journals) -> list[str]:
    out: list[str] = []
    for name in journals or []:
        name = _clean_meta(name)
        if not name:
            continue
        for variant in (name, name.split(":")[0], re.split(r"\s[-–—]\s", name)[0]):
            variant = variant.strip()
            if variant and variant not in out:
                out.append(variant)
    return out


def _is_non_journal(pub_types) -> bool:
    return any(re.sub(r"[\s_-]", "", str(t).lower()) in _NON_JOURNAL_TYPES for t in pub_types or [])


# ============================================================================ comparación de la cita
@dataclass
class CitationCheck:
    title_ok: bool | None = None          # None: el registro no trae título (no se puede comparar)
    title_absent: bool = False            # el registro tiene título y la cita no lo trae (es discrepancia)
    title_score: float | None = None
    title_missing: list[str] = field(default_factory=list)
    title_extra: list[str] = field(default_factory=list)
    title_in_citation: str | None = None
    text_before_title: list[str] = field(default_factory=list)   # palabras ajenas entre autores/año y título
    year_in_citation: int | None = None
    author_in_citation: str | None = None
    author_ok: bool | None = None
    journal_in_citation: str | None = None
    journal_ok: bool | None = None
    notes: list[str] = field(default_factory=list)
    described: bool = False               # la cita trae algo más que identificadores (autores, título...)


def _assign_segments(text: str, toks: list[_Tok]) -> None:
    cuts: list[int] = []
    for match in _BOUNDARY_RE.finditer(text):
        cuts.extend((match.start(), match.end()))
    cuts.sort()
    for tok in toks:
        tok.seg = bisect.bisect_right(cuts, tok.start)


def _trim_span(span: list[_Tok], real: set[str], authors: set[str], journals: list[str],
               lead: bool, tail: bool) -> list[_Tok]:
    """Quita del principio los nombres de autor del registro y del final la revista del registro (citas sin
    puntos entre autores, título y revista). Nunca quita palabras del título real."""
    toks = list(span)
    if lead:
        while toks and toks[0].norm not in real and (
                _name_norm(toks[0].norm) in authors or _is_initial(toks[0].raw) or not _is_sig(toks[0].norm)
                or toks[0].norm in _NOISE):
            toks.pop(0)
    if tail:
        while toks and toks[-1].norm not in real and not _is_qualifier(toks) and (
                _has_digit(toks[-1].norm) or not _is_sig(toks[-1].norm) or toks[-1].norm in _NOISE):
            toks.pop()
        for size in range(min(10, len(toks) - 1), 0, -1):
            tail_text = " ".join(t.raw for t in toks[-size:])
            if any(journal_matches(tail_text, j) for j in journals):
                toks = toks[:-size]
                break
    return toks


def _best_title(text: str, toks: list[_Tok], variants: list[list[str]], authors: set[str],
                journals: list[str]):
    """Tramo de la cita (frases consecutivas) que mejor coincide con alguna variante del título real.
    Devuelve (comparación, primera palabra, última palabra) o None."""
    segs = sorted({t.seg for t in toks})
    spans = []
    for a in range(len(segs)):
        for b in range(a, min(len(segs), a + 6)):
            span = [t for t in toks if segs[a] <= t.seg <= segs[b]]
            sig = {t.norm for t in span if _is_sig(t.norm)}
            if not sig:
                continue
            quick = max(2 * len(set(v) & sig) / (len(set(v)) + len(sig)) for v in variants)
            spans.append((quick, span))
    if not spans:
        return None
    span = max(spans, key=lambda item: (item[0], -len(item[1])))[1]
    real_all = {w for v in variants for w in v}
    chosen = None
    # Primero el tramo recortado: a igual coincidencia gana (un año o una revista pegados al final del título no
    # quedan dentro de él, donde no se comprobarían). El recorte nunca quita palabras del título real.
    for lead in (True, False):
        for tail in (True, False):
            cut = _trim_span(span, real_all, authors, journals, lead, tail)
            if not cut:
                continue
            region = text[cut[0].start:cut[-1].end]
            for cited in (region, _strip_title_prefix(region)):
                cited_tokens = _title_tokens(cited)
                for variant in variants:
                    cmp = _compare(variant, cited_tokens)
                    key = (cmp.ok, cmp.score)
                    if chosen is None or key > chosen[0]:
                        chosen = (key, cmp, cut)
    if chosen is None:
        return None
    return chosen[1], chosen[2][0], chosen[2][-1]


def _authorish(tok: _Tok, author_words: set[str], acronyms: set[str]) -> bool:
    """¿Puede ser parte de la lista de autores? Nombre del registro, inicial, partícula, sigla, "et al", "and"."""
    word = tok.norm
    return (_is_initial(tok.raw) or len(word) < 2 or word in _PARTICLES or word in _NOISE or word in _STOPWORDS
            or word in _LEAD_CONJ or _name_norm(word) in author_words or word in acronyms)


def _free_words(toks: list[_Tok], authors: set[str], journal_words: set[str]) -> set[str]:
    """Palabras de la cita que no son autores, revista, cifras ni fórmulas de cita: si hay varias, la cita
    trae un título."""
    out = set()
    for tok in toks:
        word = tok.norm
        if not _is_sig(word) or word in _NOISE or _name_norm(word) in authors or _is_initial(tok.raw) \
                or _has_digit(word) or word in _MONTHS:
            continue
        if word in journal_words or any(_abbreviates(word, w) for w in journal_words):
            continue
        out.add(word)
    return out


_YEAR_TOKEN_RE = re.compile(r"(?:1[6-9]\d{2}|20\d{2})[a-z]?")
# Palabras de autores colectivos que el registro puede no listar ("on behalf of the XYZ Study Group").
_GROUP_WORDS = frozenset({"behalf", "group", "groups", "study", "consortium", "investigators", "collaborators",
                          "collaboration", "team", "network", "committee", "working", "panel", "members"})


def _text_before_title(toks: list[_Tok], title_start: int, author_words: set[str], acronyms: set[str],
                       has_authors: bool, given: set[int] = frozenset()) -> list[str]:
    """Palabras ajenas antes del título: un título inventado delante del real ("Autor. 2020. Título falso.
    Título real. Revista"). Entre el año y el título no puede haber texto (basta con dos palabras); en la lista de
    autores se admiten nombres que el registro escribe de otra forma (uno suelto entre iniciales) y los nombres de
    pila completos de autores que el registro da con iniciales (`given`, ver `_given_names`), pero no dos o más
    palabras seguidas que no sean autores del registro. Sin autores en el registro solo se mira tras el año."""
    pre = [t for t in toks if t.end <= title_start]

    def explained(idx: int) -> bool:
        tok = pre[idx]
        return (idx in given or _authorish(tok, author_words, acronyms) or _has_digit(tok.norm)
                or tok.norm in _MONTHS or tok.norm in _TITLE_PREFIX_WORDS or tok.norm in _GROUP_WORDS)

    flagged: list[str] = []
    years = [i for i, t in enumerate(pre) if _YEAR_TOKEN_RE.fullmatch(t.raw.lower())]
    block = list(range(len(pre)))
    if years:
        after = [pre[i].raw for i in range(years[-1] + 1, len(pre)) if not explained(i)]
        if len(after) >= 2:
            flagged += after
        block = block[:years[-1]]
    if has_authors:
        run: list[str] = []
        for idx in block + [None]:
            if idx is not None and pre[idx].norm in _STOPWORDS and not _is_initial(pre[idx].raw):
                continue              # "of", "in", "and" no cortan ni cuentan
            if idx is None or explained(idx):
                if len(run) >= 2:
                    flagged += run
                run = []
            else:
                run.append(pre[idx].raw)
    return list(dict.fromkeys(flagged))


def _runs_after(text: str, toks: list[_Tok], start: int) -> list[list[_Tok]]:
    """Tramos de palabras desde `start`, cortados por cifras (volumen, páginas, año), identificadores, '(', '[' y
    ';'. El primero con contenido debe ser la revista."""
    runs: list[list[_Tok]] = []
    cur: list[_Tok] = []
    prev_end = start
    for tok in toks:
        if tok.start < start:
            continue
        if cur and re.search(r"[(\[;]", text[prev_end:tok.start]):
            runs.append(cur)
            cur = []
        prev_end = tok.end
        if _has_digit(tok.norm) or tok.norm in _JOURNAL_END:
            if cur:
                runs.append(cur)
            cur = []
            continue
        cur.append(tok)
    if cur:
        runs.append(cur)
    return runs


def _prefixes(text: str, run: list[_Tok]) -> list[list[_Tok]]:
    """Prefijos del tramo que acaban en coma, dos puntos o fin de frase ("Journal of Dairy Science. Elsevier")."""
    return [run[:k] for k in range(1, len(run) + 1)
            if k == len(run) or re.search(r"[,:]|[.?!]\s", text[run[k - 1].end:run[k].start] + " ")]


def _journal_like(run: list[_Tok]) -> bool:
    return any(_is_sig(t.norm) and t.norm not in _NOT_A_JOURNAL and t.norm not in _NOISE
               and t.norm not in _MONTHS and t.norm not in _JOURNAL_LEAD_NOISE for t in run)


def _run_matches(text: str, run: list[_Tok], journal_names: list[str]) -> bool:
    for prefix in _prefixes(text, run):
        cited = " ".join(t.raw for t in prefix)
        if any(journal_matches(cited, name) for name in journal_names):
            return True
    return False


def _check_journal(text: str, toks: list[_Tok], start: int, journal_names: list[str],
                   skip_authors: tuple[set[str], set[str]] | None = None) -> tuple[bool | None, str | None]:
    """Compara con la revista del registro el primer tramo con contenido que sigue a `start` (tras el título, o
    tras los autores si la cita no trae título). No vale poner antes la revista verdadera en otro sitio: lo que
    ocupa el lugar de la revista ("Animal 14:1-10", "Comment. Animal", "103:1-10. Animal") es lo que se compara.
    Con `skip_authors` (cita sin título) se admite que el tramo empiece por nombres de autor o iniciales."""
    for run in _runs_after(text, toks, start):
        if skip_authors is not None and all(_authorish(t, *skip_authors) for t in run):
            continue
        if not _journal_like(run):
            continue
        starts = [0]
        lead = 0
        while lead < len(run) and (run[lead].norm in _NOT_A_JOURNAL or run[lead].norm in _JOURNAL_LEAD_NOISE
                                   or run[lead].norm in _MONTHS or run[lead].norm in _NOISE):
            lead += 1
            starts.append(lead)
        if skip_authors is not None:
            k = 0
            while k < len(run) - 1 and _authorish(run[k], *skip_authors):
                k += 1
                starts.append(k)
        cited = " ".join(t.raw for t in run)
        for s in dict.fromkeys(starts):
            if s < len(run) and _run_matches(text, run[s:], journal_names):
                return True, " ".join(t.raw for t in run[s:])
        return False, cited
    return None, None


def compare_citation(citation_text: str | None, *, titles=(), authors=(), families=None, journals=(),
                     pub_types=(), author_order: str | None = None) -> CitationCheck:
    """Compara el texto de una cita con los metadatos del trabajo que identifica su DOI/PMID.

    `titles`: título real y variantes (con subtítulo, de otra fuente); `authors` y `families`: autores en el
    orden del registro y, si se conocen, sus apellidos; `author_order="given_family"` si los nombres vienen como
    "Nombre Apellido" sin apellido aparte (OpenAlex); `journals`: nombres de la revista (completo y abreviado).
    """
    check = CitationCheck()
    text = _clean_citation(citation_text)
    toks = _tokens_of(text)
    check.described = any(_is_sig(t.norm) and not _has_digit(t.norm) and t.norm not in _NOISE for t in toks)
    if not toks:
        return check
    _assign_segments(text, toks)
    families = list(families or [])
    authors = [a for a in (_clean_meta(x) for x in authors or []) if a]
    first_words: set[str] = set()
    acronyms: set[str] = set()
    for i, name in enumerate(authors):
        if i == 0:
            first_words = _author_candidates(name, families[0] if families else None, author_order)
        acronym = _acronym(name)
        if acronym:
            acronyms.add(acronym)
    author_words = _all_author_words(authors) | {
        w for i, f in enumerate(families) if f and i < len(authors) for w in _author_candidates(authors[i], f)}
    parts = [_author_parts(name, families[i] if i < len(families) else None, author_order)
             for i, name in enumerate(authors)]
    journal_names = _journal_variants(journals)
    journal_words = {w for j in journal_names for w in _journal_words(j, abbreviated=False)}
    variants: list[list[str]] = []
    for title in titles or []:
        for form in (title, _strip_title_prefix(_clean_meta(title))):
            words = _title_tokens(form)
            if words and words not in variants:
                variants.append(words)

    # Título: si el registro lo tiene, la cita debe traerlo (formato del informe: Autores. Año. Título. Revista).
    # Sin él no hay forma de saber qué trabajo se cita: "Pérez J. 2020. J Dairy Sci" vale para cualquier trabajo del
    # mismo primer autor, año y revista, así que es discrepancia, no "sin comprobar".
    region = None
    title_unlocated = False
    if variants:
        found = _best_title(text, toks, variants, author_words, journal_names)
        if found is not None and found[0].score >= 0.5:
            cmp, first, last = found
            region = (first.start, last.end)
            check.title_ok, check.title_score = cmp.ok, round(cmp.score, 3)
            check.title_missing, check.title_extra = cmp.missing, cmp.extra
            check.title_in_citation = text[first.start:last.end]
            check.text_before_title = _text_before_title(toks, first.start, author_words, acronyms, bool(authors),
                                                         _given_names(text, toks, parts))
            if check.text_before_title:
                check.title_ok = False
                check.notes.append("La cita trae, antes del título, texto que no es de los autores del registro ni el "
                                   f"año («{' '.join(check.text_before_title[:8])}»): no se sabe qué trabajo cita "
                                   "(¿un título añadido?).")
        elif len(_free_words(toks, author_words, journal_words)) >= 2:
            check.title_ok, check.title_score = False, round(found[0].score, 3) if found else 0.0
            title_unlocated = True
            check.notes.append("La cita trae un título que no se parece al del trabajo identificado.")
        else:
            check.title_ok, check.title_absent = False, True
    else:
        check.notes.append("El registro no trae título con el que comparar: solo se han comprobado el primer autor, "
                           "el año y la revista.")

    # Año (sin contar el propio título: "... after the 2015 milk quota" no es el año de la cita)
    check.year_in_citation = _year_from_clean(text, region)

    # Primer autor: el primero de la cita debe ser el primero del registro
    lead = _lead_tokens(text, toks)
    if lead and authors:
        check.author_in_citation = " ".join(t.raw for t in lead)
        names = {_name_norm(t.norm) for t in lead
                 if len(t.norm) >= 2 and not _is_initial(t.raw) and t.norm not in _PARTICLES}
        names = names or {_name_norm(t.norm) for t in lead}
        acronym = _acronym(authors[0])
        by_acronym = acronym is not None and any(
            t.norm == acronym and t.raw.isupper() and len(t.raw) >= 3 for t in lead)
        check.author_ok = bool(names & first_words) or by_acronym

    # Revista (solo artículos de revista; un libro, un capítulo o un conjunto de datos no tienen). Se mira tras el
    # título; tras los autores, solo si el registro no tiene título con el que situarla (sin título en la cita, la
    # entrada ya es discrepancia y el tramo tras los autores puede ser otro coautor).
    if journal_names and not _is_non_journal(pub_types) and not title_unlocated and not check.title_absent:
        if region is not None:
            check.journal_ok, check.journal_in_citation = _check_journal(text, toks, region[1], journal_names)
        elif lead and not variants:
            check.journal_ok, check.journal_in_citation = _check_journal(
                text, toks, lead[-1].end, journal_names, skip_authors=(author_words, acronyms))
    return check


# ============================================================================ verificación
@dataclass
class Verification:
    identifier: str
    kind: str                       # doi | pmid
    status: str                     # verificada | no_encontrada | existe_sin_metadatos | error_red | offline
    title: str | None = None
    year: int | None = None
    journal: str | None = None
    authors: list[str] | None = None
    retracted: bool | None = None   # None = desconocido (la verificación no es válida)
    retraction_checked_via: list[str] = field(default_factory=list)
    source: str | None = None       # crossref | openalex | datacite | pubmed
    registration_agency: str | None = None
    license: str | None = None
    title_ok: bool | None = None
    title_score: float | None = None
    year_in_citation: int | None = None
    author_in_citation: str | None = None
    author_ok: bool | None = None
    journal_in_citation: str | None = None
    journal_ok: bool | None = None
    mismatch_reasons: list[str] = field(default_factory=list)
    message: str = ""
    record: Record | None = field(default=None, repr=False, compare=False)   # registro verificado (para el alta)

    @property
    def year_mismatch(self) -> bool:
        return bool(self.year and self.year_in_citation and abs(self.year - self.year_in_citation) > 1)

    @property
    def mismatch(self) -> bool:
        return (self.title_ok is False or self.author_ok is False or self.journal_ok is False
                or self.year_mismatch)

    @property
    def ok(self) -> bool:
        return self.status == "verificada" and self.retracted is False and not self.mismatch

    def to_dict(self) -> dict:
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "record"}
        data.update(ok=self.ok, discrepancia=self.mismatch)
        return data


def _add_message(result: Verification, text: str) -> None:
    result.message = (result.message + " " if result.message else "") + text


def _apply_citation(result: Verification, rec: Record | None, citation_text: str | None,
                    titles=(), journals=()) -> Verification:
    if citation_text and result.status == "verificada" and rec is not None:
        families = (rec.extra or {}).get("families")
        order = "given_family" if rec.source == "openalex" and not families else None
        check = compare_citation(citation_text, titles=titles or [rec.title], authors=rec.authors,
                                 families=families, journals=journals or [rec.journal], pub_types=rec.pub_types,
                                 author_order=order)
        result.title_ok, result.title_score = check.title_ok, check.title_score
        result.year_in_citation = check.year_in_citation
        result.author_in_citation, result.author_ok = check.author_in_citation, check.author_ok
        result.journal_in_citation, result.journal_ok = check.journal_in_citation, check.journal_ok
        reasons = []
        if not check.described:
            # Solo el identificador: no hay nada que comparar, y así se esquivaría la comprobación.
            result.title_ok = False
            reasons.append("la entrada solo trae el identificador: escribe autores, año, título y revista para poder "
                           "comprobar que corresponde a la cita")
        elif check.title_absent:
            reasons.append("la cita no trae el título del trabajo (formato: Autores. Año. Título. Revista "
                           "Vol:páginas): sin él no se puede comprobar que el identificador sea el del trabajo citado")
        elif check.title_ok is False:
            detail = [] if (check.text_before_title and not check.title_missing and not check.title_extra) else \
                [f"similitud {check.title_score:.2f}" if check.title_score is not None else "no encontrado"]
            if check.title_missing:
                detail.append("faltan: " + ", ".join(check.title_missing[:6]))
            if check.title_extra:
                detail.append("sobran: " + ", ".join(check.title_extra[:6]))
            if check.text_before_title:
                detail.append("texto ajeno antes del título: " + " ".join(check.text_before_title[:8]))
            reasons.append(f"título ({'; '.join(detail)})")
        if result.year_mismatch:
            reasons.append(f"año (cita {result.year_in_citation}, real {result.year})")
        if check.author_ok is False:
            reasons.append(f"primer autor (cita «{check.author_in_citation}», registro «{rec.authors[0]}»)")
        if check.journal_ok is False:
            reasons.append(f"revista (cita «{check.journal_in_citation}», registro «{rec.journal}»)")
        result.mismatch_reasons = reasons
        if reasons:
            first = rec.authors[0] if rec.authors else "sin autores"
            _add_message(result, f"El identificador existe pero NO corresponde a la cita ({'; '.join(reasons)}); "
                                 f"trabajo real: {first}, {result.year}, «{result.title}», {result.journal or 's. r.'}.")
        for note in check.notes:
            _add_message(result, note)
    if result.retracted:
        _add_message(result, "Trabajo RETRACTADO.")
    elif result.retracted is None and result.status == "verificada":
        _add_message(result, "No se pudo comprobar si está retractado (verificación incompleta).")
    return result


def _pubmed_by_doi(client, doi: str) -> Record | None:
    """Registro de PubMed con ese DOI (búsqueda `<doi>[doi]` y resumen), o None si no se sabe. Se descarta si
    PubMed le asocia otro DOI: el PMID sería de otro trabajo. Si PubMed no declara el DOI, quien llama comprueba
    que el título sea el del trabajo (`_consistent`)."""
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
    if not records or (records[0].doi and records[0].doi != normalize_doi(doi)):
        return None
    return records[0]


def translated_title(title: str | None) -> bool:
    """¿Es un título traducido por PubMed? PubMed da en inglés y entre corchetes el título de los artículos escritos
    en otros idiomas ("[Effect of ...]")."""
    text = (title or "").strip().rstrip(".").strip()
    return len(text) > 2 and text.startswith("[") and text.endswith("]")


def same_first_author(a: str | None, b: str | None, family_a: str | None = None) -> bool:
    """¿Es el mismo primer autor? Algún apellido en común entre los dos nombres (cualquier formato)."""
    return bool(a and b and _author_candidates(a, family_a) & _author_candidates(b))


def translation_of(title: str | None, other_title: str | None, *, year=None, other_year=None, first_author=None,
                   other_first_author=None, family=None) -> bool:
    """¿Puede `title` ser la traducción de PubMed ("[...]") del título `other_title` de un registro que comparte el
    DOI? Solo si coinciden el primer autor y el año (±1). Un título distinto sin corchetes no es una traducción: es
    otro trabajo (PubMed puede asociar por error un DOI a otro PMID)."""
    return bool(translated_title(title) and other_title and year and other_year and abs(year - other_year) <= 1
                and same_first_author(other_first_author, first_author, family))


def _consistent(rec: Record, other: Record, doi: str) -> bool:
    """¿Es `other` (otra fuente consultada por el mismo DOI) el mismo trabajo que `rec`? Si declara otro DOI, no.
    Con otro título solo si es PubMed, declara ese mismo DOI y su título es una traducción ("[...]", ver
    `translation_of`) con el mismo primer autor y año (±1)."""
    norm = normalize_doi(doi)
    if other.doi and other.doi != norm:
        return False
    if not rec.title or not other.title or titles_same_work(rec.title, other.title):
        return True
    if other.doi != norm or other.source != "pubmed" or not rec.authors or not other.authors:
        return False
    families = (rec.extra or {}).get("families") or [None]
    return translation_of(other.title, rec.title, year=other.year, other_year=rec.year,
                          first_author=other.authors[0], other_first_author=rec.authors[0], family=families[0])


def _pubmed_confirms(client, rec: Record, doi: str, pmid: str, notes: list[str]) -> Record | None:
    """Registro de PubMed del PMID que OpenAlex asocia al DOI, solo si PubMed confirma que es el mismo trabajo (declara
    ese DOI, o no declara ninguno y el título es el mismo; ver `_consistent`). OpenAlex puede mezclar obras: un PMID
    sin confirmar no se asocia."""
    try:
        found = pubmed.summaries(client, [pmid])
    except NetworkError as exc:
        notes.append(f"no se pudo comprobar en PubMed el PMID {pmid} que da OpenAlex "
                     f"({_redact(str(exc), getattr(client, 'cfg', None))[:120]}): no se asocia")
        return None
    cand = found[0] if found else None
    if cand is None or not cand.title or not _consistent(rec, cand, doi):
        notes.append(f"PubMed no confirma que el PMID {pmid} que da OpenAlex sea este trabajo: no se asocia")
        return None
    return cand


def _copy(rec: Record) -> Record:
    data = rec.to_dict()
    data["extra"] = dict(rec.extra or {})
    return Record(**data)


def _existence_fallback(client, doi: str, notes: list[str]) -> tuple[Record | None, Verification | None]:
    """Registro de DOI (doi.org) y DataCite para un DOI que Crossref y OpenAlex no dan. Devuelve el registro de
    DataCite o una verificación terminada (inexistente, pendiente o sin red)."""
    cfg = getattr(client, "cfg", None)
    try:
        exists = doiorg.handle_exists(client, doi)
    except OfflineError as exc:
        return None, Verification(doi, "doi", "offline", message=_redact(str(exc), cfg))
    except NetworkError as exc:
        return None, Verification(doi, "doi", "error_red", message=_redact(
            f"{'; '.join(notes)}; no se pudo consultar el registro de DOI (doi.org) para saber si existe: {exc}",
            cfg))
    if not exists:
        return None, Verification(doi, "doi", "no_encontrada", message=(
            f"DOI inexistente: no está registrado en doi.org ({'; '.join(notes)})."))
    agency = None
    try:
        agency = doiorg.registration_agency(client, doi)
    except NetworkError as exc:
        notes.append(f"no se pudo saber la agencia de registro ({_redact(str(exc), cfg)})")
    if agency and agency.strip().lower() == "datacite":
        try:
            return doiorg.datacite_work(client, doi), None
        except NotFoundError:
            notes.append("DataCite no lo devuelve")
        except OfflineError as exc:
            return None, Verification(doi, "doi", "offline", registration_agency=agency,
                                      message=_redact(str(exc), cfg))
        except NetworkError as exc:
            return None, Verification(doi, "doi", "error_red", registration_agency=agency, message=_redact(
                f"El DOI existe (agencia DataCite), pero no se pudo consultar DataCite: {exc}", cfg))
    # Un DOI de otra agencia (mEDRA, JaLC, OP...) puede estar en PubMed: sus metadatos sí son comparables.
    record = _pubmed_by_doi(client, doi)
    if record is not None and record.doi == normalize_doi(doi):    # sin otra fuente, PubMed debe declarar el DOI
        record.extra = dict(record.extra or {}, registration_agency=agency)
        notes.append(f"metadatos de PubMed (PMID {record.pmid}); agencia de registro: {agency or 'desconocida'}")
        return record, None
    return None, Verification(doi, "doi", "existe_sin_metadatos", registration_agency=agency, message=(
        f"El DOI está registrado en doi.org (agencia: {agency or 'desconocida'}), pero ninguna fuente consultada da "
        f"metadatos comparables ({'; '.join(notes)}): no se puede comprobar que corresponda a la cita ni si está "
        "retractado. Queda PENDIENTE, no verificado."))


def verify_doi(client, doi: str, citation_text: str | None = None) -> Verification:
    cfg = getattr(client, "cfg", None)
    doi = clean_doi(doi)
    notes: list[str] = []
    rec = oa_rec = pm_rec = None
    try:
        rec = crossref.work(client, doi)
    except NotFoundError:
        notes.append("Crossref no lo tiene")
    except OfflineError as exc:
        return Verification(doi, "doi", "offline", message=_redact(str(exc), cfg))
    except NetworkError as exc:
        return Verification(doi, "doi", "error_red", message=_redact(str(exc), cfg))
    if rec is None:
        try:
            rec = oa_rec = openalex.work_by_doi(client, doi)
        except NotFoundError:
            notes.append("OpenAlex no lo tiene")
        except OfflineError as exc:
            return Verification(doi, "doi", "offline", message=_redact(str(exc), cfg))
        except NetworkError as exc:  # sin clave o sin red: no impide preguntar al registro de DOI
            reason = (f"falta {openalex.KEY_ENV}" if str(exc).startswith(openalex.KEY_REQUIRED_MESSAGE[:40])
                      else _redact(str(exc), cfg)[:160])
            notes.append(f"OpenAlex no consultado ({reason})")
    if rec is None:
        rec, finished = _existence_fallback(client, doi, notes)
        if finished is not None:
            return finished
        if rec.source == "pubmed":
            pm_rec = rec

    checked = [rec.source] if rec.retracted is not None else []
    retracted = rec.retracted
    if retracted is not True and rec.source not in ("openalex", "pubmed"):
        # Crossref y DataCite solo marcan la retractación en algunos registros: se confirma con otras fuentes.
        try:
            oa_rec = openalex.work_by_doi(client, doi)
        except NetworkError:
            oa_rec = None
        if oa_rec is not None and not _consistent(rec, oa_rec, doi):
            # OpenAlex puede mezclar obras: no se toman su PMID, sus identificadores ni su "no retractado"; un
            # "retractado" sí cuenta (prudencia).
            notes.append(f"OpenAlex da para este DOI otro título («{(oa_rec.title or '')[:80]}»): no se toman su "
                         "PMID ni sus identificadores")
            if oa_rec.retracted:
                checked.append("openalex")
                retracted = True
            oa_rec = None
        if oa_rec is not None and oa_rec.retracted is not None:
            checked.append("openalex")
            retracted = bool(oa_rec.retracted)
    if retracted is not True and pm_rec is None:
        pm_rec = _pubmed_by_doi(client, doi)
        if pm_rec is not None and not _consistent(rec, pm_rec, doi):
            notes.append(f"PubMed asocia el DOI al PMID {pm_rec.pmid}, de un trabajo con otro título: no se usa")
            pm_rec = None
        if pm_rec is not None and pm_rec.retracted is not None:
            checked.append("pubmed")
            retracted = bool(pm_rec.retracted)

    # PMID: solo el que confirma PubMed (búsqueda por el DOI o, para el que da OpenAlex, el resumen de ese PMID).
    # OpenAlex puede asociar a un DOI el PMID de otro trabajo: sin confirmar, no se asocia (lo usaría el registro
    # para unir esta fuente con la fila de ese PMID).
    oa_pmid = oa_rec.pmid if oa_rec is not None else None
    pmid = pm_rec.pmid if pm_rec is not None else None
    oa_confirmed = False
    if oa_pmid and pmid and oa_pmid != pmid:
        notes.append(f"OpenAlex ({oa_pmid}) y PubMed ({pmid}) dan PMID distintos para este DOI: no se asocia ninguno")
        pmid = None
    elif oa_pmid and not pmid:
        cand = _pubmed_confirms(client, rec, doi, oa_pmid, notes)
        if cand is not None:
            pmid, pm_rec, oa_confirmed = oa_pmid, cand, True
            if retracted is not True and cand.retracted is not None:
                checked.append("pubmed")
                retracted = bool(cand.retracted)
    else:
        oa_confirmed = bool(oa_pmid)

    # Registro verificado para el alta: metadatos de la fuente principal y, de las otras consultas por el mismo
    # DOI, el PMID confirmado, el PMCID, el identificador de OpenAlex y la licencia.
    full = _copy(rec)
    full.doi = full.doi or normalize_doi(doi)
    full.pmid = pmid
    full.pmcid = pm_rec.pmcid if pm_rec is not None else None
    if not full.pmcid and oa_rec is not None and (oa_confirmed or not oa_pmid):
        full.pmcid = oa_rec.pmcid
    if oa_rec is not None and oa_rec is not rec:
        full.openalex_id = full.openalex_id or oa_rec.openalex_id
    license_ = (rec.extra or {}).get("license") or ((oa_rec.extra or {}).get("license") if oa_rec else None)
    full.extra["license"] = license_
    full.retracted = retracted
    agency = {"crossref": "Crossref", "datacite": "DataCite"}.get(rec.source) or (rec.extra or {}).get(
        "registration_agency")
    result = Verification(doi, "doi", "verificada", title=rec.title, year=rec.year, journal=rec.journal,
                          authors=rec.authors[:5], retracted=retracted, retraction_checked_via=checked,
                          source=rec.source, registration_agency=agency, license=license_, record=full)
    extra = rec.extra or {}
    titles = [rec.title] + [f"{rec.title}: {s}" for s in extra.get("subtitles") or [] if rec.title]
    titles += list(extra.get("alternative_titles") or [])
    journals = [rec.journal] + list(extra.get("container_titles") or []) + list(extra.get("short_container_titles") or [])
    for other in (oa_rec, pm_rec):
        if other is not None and other is not rec:
            titles.append(other.title)
            journals.append(other.journal)
    titles = list(dict.fromkeys(_clean_meta(t) for t in titles if t and _clean_meta(t)))
    full.extra["title_variants"] = titles     # para comprobar fusiones de registros (`db.upsert_source`)
    full.extra["verified_by"] = "doi"         # identificador que avala esta verificación (el PMID es asociado)
    if notes:
        _add_message(result, f"Metadatos de {rec.source} ({'; '.join(notes)}).")
    return _apply_citation(result, full, citation_text, titles, [j for j in journals if j])


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
    full = _copy(rec)
    full.retracted = bool(rec.retracted)
    full.extra["title_variants"] = [rec.title] if rec.title else []
    full.extra["verified_by"] = "pmid"        # el DOI que declara PubMed es un dato asociado
    result = Verification(pmid, "pmid", "verificada", title=rec.title, year=rec.year, journal=rec.journal,
                          authors=rec.authors[:5], retracted=bool(rec.retracted),
                          retraction_checked_via=["pubmed"], source="pubmed", record=full)
    return _apply_citation(result, full, citation_text, [rec.title], [rec.journal])


def verify_entry(client, entry_text: str) -> list[Verification]:
    """Verifica todos los identificadores de una entrada bibliográfica (también los repetidos o dos DOI distintos:
    uno inventado junto a uno real no pasa por el real)."""
    ids = extract_identifiers(entry_text)
    results = [verify_doi(client, d, entry_text) for d in ids["doi"]]
    results += [verify_pmid(client, p, entry_text) for p in ids["pmid"]]
    return results
