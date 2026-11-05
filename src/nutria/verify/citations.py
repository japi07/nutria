"""Verificación de referencias bibliográficas (falla cerrado).

Comprueba lo que un LLM suele fallar:

1. Que el identificador existe. Un DOI se busca en Crossref y OpenAlex y, si ninguno lo conoce, en el registro
   de DOI (doi.org: API de handles y agencia de registro) y, si es de DataCite, en su API. Solo se declara
   inexistente si doi.org no lo tiene registrado. Un DOI registrado del que ninguna fuente da metadatos
   comparables (p. ej. de mEDRA o JaLC) queda PENDIENTE (`existe_sin_metadatos`), nunca verificado. Un PMID se
   busca en PubMed.
2. Que corresponde al trabajo citado (`compare_citation`). Es una heurística, no una garantía:
   - primer autor: el primer nombre de la cita debe ser el PRIMER autor del registro, no uno cualquiera;
   - año: el de la cita (sin contar identificadores, volumen, páginas, fechas de consulta ni el propio
     título) debe estar a ±1 año del real;
   - título: si la cita lo trae, se compara de forma simétrica (palabras significativas normalizadas en los
     dos sentidos; ver `TITLE_MIN`), sin admitir sustituciones de palabras;
   - revista: si la cita la trae, debe ser la del registro, con tolerancia a abreviaturas ISO 4
     ("J Dairy Sci" = "Journal of Dairy Science").
3. Que el trabajo no está retractado, con una cadena de comprobación (Crossref, OpenAlex, PubMed). Si ninguna
   fuente permite saberlo, la retractación queda como DESCONOCIDA y la verificación no es válida.

No comprueba si la fuente respalda la afirmación concreta: eso se registra en la tabla de respaldo del Anexo A
del informe (agente `verificador-citas`), que la comprobación del informe exige.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from dataclasses import dataclass, field, fields
from difflib import SequenceMatcher
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
_TRAILING = ".,;:'\"!?*_"                                       # puntuación de la frase que sigue al DOI
_CLOSERS = {")": "(", "]": "[", ">": "<", "}": "{"}


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
    text = _ESCAPED_MD_RE.sub(r"\1", text or "")
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
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:short\s+communication|technical\s+note|invited\s+review|symposium\s+review|"
    r"graduate\s+student\s+literature\s+review|review(?:\s+article)?|mini[\s-]?review|research\s+note|"
    r"research\s+article|original\s+article|brief\s+(?:report|communication)|rapid\s+communication|"
    r"letter(?:\s+to\s+the\s+editor)?|editorial|retracted(?:\s+article)?|retraction|withdrawn|"
    r"expression\s+of\s+concern)\s*[:.\-–—]\s*", re.IGNORECASE)
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


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").lower()
    text = "".join(_FOLD.get(ch, ch) for ch in text)
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


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
    """Texto de la cita sin marcador [Rn], identificadores, URL ni anotaciones ("[Review]", "(in Spanish)")."""
    text = unicodedata.normalize("NFC", text or "")
    text = _ESCAPED_MD_RE.sub(r"\1", text)
    text = _MARKER_RE.sub("", text, count=1)
    text = _strip_identifiers(text)
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
# siempre es discrepancia. Las variantes de ortografía (fibre/fiber), plurales y compuestos (rumen-protected /
# rumenprotected) se emparejan antes y no cuentan como sustitución. El umbral anterior (0,6, asimétrico) dejaba
# pasar un título distinto del mismo tema con el 73 % de palabras comunes.
TITLE_MIN = 0.9


def _strip_title_prefix(text: str) -> str:
    return _TITLE_PREFIX_RE.sub("", text or "", count=1)


def _title_tokens(text: str | None) -> list[str]:
    return [t.norm for t in _tokens_of(text or "") if _is_sig(t.norm)]


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
    def ok(self) -> bool:
        """Mismo título: ≥ TITLE_MIN en los dos sentidos y sin sustituciones."""
        return (self.recall >= TITLE_MIN and self.precision >= TITLE_MIN
                and not (self.missing and self.extra))

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


def _spelling_variant(a: str, b: str) -> bool:
    if a[0] != b[0]:
        return False
    for x, y in ((a, b), (b, a)):
        if x in (y + "s", y + "es"):
            return True
    return min(len(a), len(b)) >= 3 and SequenceMatcher(None, a, b).ratio() >= 0.8


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
def _author_candidates(name: str | None, family: str | None = None) -> set[str]:
    """Formas normalizadas del apellido de un autor del registro (Crossref "Apellido Nombre", PubMed
    "Apellido AB", OpenAlex "Nombre Apellido"); con el apellido explícito (`family`) solo se usa ese."""
    def pick(tokens: list[_Tok]) -> set[str]:
        return {t.norm for t in tokens if len(t.norm) >= 2 and not _is_initial(t.raw)
                and t.norm not in _PARTICLES and t.norm not in _STOPWORDS}
    if family:
        found = pick(_tokens_of(family))
        if found:
            return found
    tokens = _tokens_of(name or "")
    return pick(tokens) or {t.norm for t in tokens if len(t.norm) >= 2}


def _acronym(name: str | None) -> str | None:
    """Sigla de un autor institucional ("European Food Safety Authority" -> "efsa")."""
    words = [t.norm for t in _tokens_of(name or "") if _is_sig(t.norm) and t.raw[:1].isupper()]
    return "".join(w[0] for w in words) if len(words) >= 3 else None


def _lead_tokens(text: str, toks: list[_Tok]) -> list[_Tok]:
    """Primer autor de la cita: palabras del principio hasta la primera coma, punto y coma, '&', paréntesis,
    cifra, conjunción ("and", "y", "et al."), palabra en minúscula (salvo partículas) o fin de frase tras una
    palabra que no es una inicial."""
    lead: list[_Tok] = []
    prev_end = 0
    for tok in toks:
        gap = text[prev_end:tok.start]
        if lead:
            if re.search(r"[,;&(\[:]", gap):
                break
            if re.search(r"[.?!]\s", gap + " ") and not _is_initial(lead[-1].raw):
                break
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
_JOURNAL_END = frozenset({"doi", "pmid", "pmcid", "http", "https", "www", "epub", "vol", "volume", "pp",
                          "pages", "page", "suppl", "issue", "available", "disponible", "accessed", "retrieved"})
_JOURNAL_LEAD_NOISE = frozenset({"in", "en"})
# Notas que pueden seguir al título y no son una revista ("Review.", "Erratum in: ...").
_NOT_A_JOURNAL = frozenset({"review", "erratum", "corrigendum", "comment", "published", "abstract", "editorial",
                            "letter", "retracted", "retraction", "preprint", "spanish", "english"})
_NON_JOURNAL_TYPES = frozenset({
    "bookchapter", "book", "monograph", "editedbook", "referencebook", "referenceentry", "bookpart", "booksection",
    "bookset", "bookseries", "proceedingsarticle", "proceedings", "conferencepaper", "conferenceproceeding",
    "report", "reportseries", "dataset", "postedcontent", "preprint", "dissertation", "thesis", "standard",
    "component", "peerreview", "paratext", "software", "collection", "image", "audiovisual", "model",
    "workflow", "other"})


def _journal_words(text: str, abbreviated: bool) -> list[str]:
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text or "")
    out = []
    for tok in _tokens_of(text):
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


def journal_matches(cited: str | None, full: str | None) -> bool:
    """¿Nombra `cited` la misma revista que `full`? Acepta el nombre completo, la abreviatura ISO 4 ("J Dairy
    Sci", "J. Dairy Sci.") o la sigla ("JDS"), en cualquiera de los dos sentidos. Un título de una sola palabra
    ("Animal", "Animals", "Nutrients") debe coincidir exactamente, porque ISO 4 no lo abrevia."""
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
    title_ok: bool | None = None          # None: la cita no trae título (o el registro no lo tiene)
    title_score: float | None = None
    title_missing: list[str] = field(default_factory=list)
    title_extra: list[str] = field(default_factory=list)
    title_in_citation: str | None = None
    year_in_citation: int | None = None
    author_in_citation: str | None = None
    author_ok: bool | None = None
    journal_in_citation: str | None = None
    journal_ok: bool | None = None
    notes: list[str] = field(default_factory=list)


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
                toks[0].norm in authors or _is_initial(toks[0].raw) or not _is_sig(toks[0].norm)
                or toks[0].norm in _NOISE):
            toks.pop(0)
    if tail:
        while toks and toks[-1].norm not in real and (
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
    Devuelve (comparación, primera palabra, última palabra, parecido del mejor tramo ajeno a ese) o None. El
    último valor sirve para detectar una cita con dos títulos parecidos al real (uno de ellos, añadido)."""
    segs = sorted({t.seg for t in toks})
    spans = []
    for a in range(len(segs)):
        for b in range(a, min(len(segs), a + 6)):
            span = [t for t in toks if segs[a] <= t.seg <= segs[b]]
            sig = {t.norm for t in span if _is_sig(t.norm)}
            if not sig:
                continue
            quick = max(2 * len(set(v) & sig) / (len(set(v)) + len(sig)) for v in variants)
            spans.append((quick, a, b, span))
    if not spans:
        return None
    best = max(spans, key=lambda item: (item[0], -len(item[3])))
    _, first_seg, last_seg, span = best
    other = max((q for q, a, b, _ in spans if b < first_seg or a > last_seg), default=0.0)
    real_all = {w for v in variants for w in v}
    chosen = None
    for lead in (False, True):
        for tail in (False, True):
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
    return chosen[1], chosen[2][0], chosen[2][-1], other


def _free_words(toks: list[_Tok], authors: set[str], journal_words: set[str]) -> set[str]:
    """Palabras de la cita que no son autores, revista, cifras ni fórmulas de cita: si hay varias, la cita
    trae un título."""
    out = set()
    for tok in toks:
        word = tok.norm
        if not _is_sig(word) or word in _NOISE or word in authors or _is_initial(tok.raw) or _has_digit(word):
            continue
        if word in journal_words or any(_abbreviates(word, w) for w in journal_words):
            continue
        out.add(word)
    return out


def _journal_candidate(text: str, toks: list[_Tok], start: int) -> list[_Tok]:
    """Palabras que siguen al título (o al año) hasta el volumen, las páginas o un paréntesis. Se salta un año
    inicial ("Título. 2020. Revista 14:1-10")."""
    cand: list[_Tok] = []
    prev_end = start
    skipped_year = False
    for tok in toks:
        if tok.start < start:
            continue
        gap = text[prev_end:tok.start]
        if cand and re.search(r"[(\[;]", gap):
            break
        if not cand and not skipped_year and YEAR_RE.fullmatch(tok.raw):
            skipped_year, prev_end = True, tok.end
            continue
        if _has_digit(tok.norm) or tok.norm in _JOURNAL_END or (not cand and tok.norm in _NOT_A_JOURNAL):
            break
        prev_end = tok.end
        if not cand and tok.norm in _JOURNAL_LEAD_NOISE:
            continue
        cand.append(tok)
        if len(cand) >= 14:
            break
    return cand


def compare_citation(citation_text: str | None, *, titles=(), authors=(), families=None, journals=(),
                     pub_types=()) -> CitationCheck:
    """Compara el texto de una cita con los metadatos del trabajo que identifica su DOI/PMID.

    `titles`: título real y variantes (con subtítulo, de otra fuente); `authors` y `families`: autores en el
    orden del registro y, si se conocen, sus apellidos; `journals`: nombres de la revista (completo y abreviado).
    """
    check = CitationCheck()
    text = _clean_citation(citation_text)
    toks = _tokens_of(text)
    if not toks:
        return check
    _assign_segments(text, toks)
    families = list(families or [])
    authors = [a for a in (authors or []) if a]
    author_words: set[str] = set()
    first_words: set[str] = set()
    for i, name in enumerate(authors):
        words = _author_candidates(name, families[i] if i < len(families) else None)
        author_words |= words
        if i == 0:
            first_words = words
    journal_names = _journal_variants(journals)
    journal_words = {w for j in journal_names for w in _journal_words(j, abbreviated=False)}
    variants: list[list[str]] = []
    for title in titles or []:
        for form in (title, _strip_title_prefix(title or "")):
            words = _title_tokens(form)
            if words and words not in variants:
                variants.append(words)

    # Título
    region = None
    title_unlocated = False
    if variants:
        found = _best_title(text, toks, variants, author_words, journal_names)
        if found is not None and found[0].score >= 0.5:
            cmp, first, last, other = found
            region = (first.start, last.end)
            check.title_ok, check.title_score = cmp.ok, round(cmp.score, 3)
            check.title_missing, check.title_extra = cmp.missing, cmp.extra
            check.title_in_citation = text[first.start:last.end]
            if other >= 0.5:
                check.title_ok = False
                check.notes.append("La cita trae otro texto parecido al título además del título: no se sabe qué "
                                   "trabajo cita (¿un título añadido?).")
        elif len(_free_words(toks, author_words, journal_words)) >= 2:
            check.title_ok, check.title_score = False, round(found[0].score, 3) if found else 0.0
            title_unlocated = True
            check.notes.append("La cita trae un título que no se parece al del trabajo identificado.")
        else:
            check.notes.append("La cita no trae el título: la correspondencia del título no se ha comprobado.")

    # Año (sin contar el propio título: "... after the 2015 milk quota" no es el año de la cita)
    check.year_in_citation = _year_from_clean(text, region)

    # Primer autor: el primero de la cita debe ser el primero del registro
    lead = _lead_tokens(text, toks)
    if lead and authors:
        check.author_in_citation = " ".join(t.raw for t in lead)
        names = {t.norm for t in lead if len(t.norm) >= 2 and not _is_initial(t.raw) and t.norm not in _PARTICLES}
        names = names or {t.norm for t in lead}
        acronym = _acronym(authors[0])
        by_acronym = acronym is not None and any(
            t.norm == acronym and t.raw.isupper() and len(t.raw) >= 3 for t in lead)
        check.author_ok = bool(names & first_words) or by_acronym

    # Revista (solo artículos de revista; un libro, un capítulo o un conjunto de datos no tienen)
    if journal_names and not _is_non_journal(pub_types) and not title_unlocated:
        if region is not None:
            start = region[1]
        else:
            match = YEAR_RE.search(_year_text_for_start(text))
            start = match.end() if match else None
        if start is not None:
            cand = _journal_candidate(text, toks, start)
            if cand:
                check.journal_in_citation = " ".join(t.raw for t in cand)
                prefixes = [cand[:k] for k in range(1, len(cand) + 1)
                            if k == len(cand) or cand[k].seg != cand[k - 1].seg]
                check.journal_ok = any(journal_matches(" ".join(t.raw for t in prefix), name)
                                       for prefix in prefixes for name in journal_names)
    return check


def _year_text_for_start(text: str) -> str:
    """El texto con volumen, páginas y fechas de consulta tapados (mismas posiciones) para localizar el año."""
    def blank(match: re.Match) -> str:
        return " " * (match.end() - match.start())
    for rx in (_ACCESS_RE, VOLUME_PAGES_RE, PAGES_RE):
        text = rx.sub(blank, text)
    return text


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
        check = compare_citation(citation_text, titles=titles or [rec.title], authors=rec.authors,
                                 families=(rec.extra or {}).get("families"),
                                 journals=journals or [rec.journal], pub_types=rec.pub_types)
        result.title_ok, result.title_score = check.title_ok, check.title_score
        result.year_in_citation = check.year_in_citation
        result.author_in_citation, result.author_ok = check.author_in_citation, check.author_ok
        result.journal_in_citation, result.journal_ok = check.journal_in_citation, check.journal_ok
        reasons = []
        if check.title_ok is False:
            detail = [f"similitud {check.title_score:.2f}" if check.title_score is not None else "no encontrado"]
            if check.title_missing:
                detail.append("faltan: " + ", ".join(check.title_missing[:6]))
            if check.title_extra:
                detail.append("sobran: " + ", ".join(check.title_extra[:6]))
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


def _pubmed_by_doi(client, doi: str) -> tuple[bool | None, str | None]:
    """(retractado según PubMed, PMID) del registro de PubMed con ese DOI; (None, None) si no se sabe."""
    finder = getattr(pubmed, "pmid_for_doi", None)
    if finder is None:
        return None, None
    try:
        pmid = finder(client, doi)
        if not pmid:
            return None, None
        records = pubmed.summaries(client, [pmid])
    except NetworkError:
        return None, None
    if not records:
        return None, None
    return records[0].retracted, str(pmid)


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
    return None, Verification(doi, "doi", "existe_sin_metadatos", registration_agency=agency, message=(
        f"El DOI está registrado en doi.org (agencia: {agency or 'desconocida'}), pero ninguna fuente consultada da "
        f"metadatos comparables ({'; '.join(notes)}): no se puede comprobar que corresponda a la cita ni si está "
        "retractado. Queda PENDIENTE, no verificado."))


def verify_doi(client, doi: str, citation_text: str | None = None) -> Verification:
    cfg = getattr(client, "cfg", None)
    doi = clean_doi(doi)
    notes: list[str] = []
    rec = oa_rec = None
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

    checked = [rec.source] if rec.retracted is not None else []
    retracted = rec.retracted
    pubmed_pmid = None
    if retracted is not True and rec.source != "openalex":
        # Crossref y DataCite solo marcan la retractación en algunos registros: se confirma con otras fuentes.
        try:
            oa_rec = openalex.work_by_doi(client, doi)
        except NetworkError:
            oa_rec = None
        if oa_rec is not None and oa_rec.retracted is not None:
            checked.append("openalex")
            retracted = bool(oa_rec.retracted)
    if retracted is not True:
        value, pubmed_pmid = _pubmed_by_doi(client, doi)
        if value is not None:
            checked.append("pubmed")
            retracted = bool(value)

    # Registro verificado para el alta: metadatos de la fuente principal y, de las otras consultas por el mismo
    # DOI, el PMID (solo si no hay dos distintos), el PMCID, el identificador de OpenAlex y la licencia.
    full = _copy(rec)
    pmids = {p for p in (rec.pmid, oa_rec.pmid if oa_rec else None, pubmed_pmid) if p}
    full.pmid = pmids.pop() if len(pmids) == 1 else None
    if len(pmids) > 1:
        notes.append("las fuentes dan PMID distintos para este DOI: no se asocia ninguno")
    if oa_rec is not None:
        full.pmcid = full.pmcid or oa_rec.pmcid
        full.openalex_id = full.openalex_id or oa_rec.openalex_id
    license_ = (rec.extra or {}).get("license") or ((oa_rec.extra or {}).get("license") if oa_rec else None)
    full.extra["license"] = license_
    full.retracted = retracted
    agency = "Crossref" if rec.source == "crossref" else ("DataCite" if rec.source == "datacite" else None)
    result = Verification(doi, "doi", "verificada", title=rec.title, year=rec.year, journal=rec.journal,
                          authors=rec.authors[:5], retracted=retracted, retraction_checked_via=checked,
                          source=rec.source, registration_agency=agency, license=license_, record=full)
    extra = rec.extra or {}
    titles = [rec.title] + [f"{rec.title}: {s}" for s in extra.get("subtitles") or [] if rec.title]
    titles += list(extra.get("alternative_titles") or [])
    journals = [rec.journal] + list(extra.get("container_titles") or []) + list(extra.get("short_container_titles") or [])
    if oa_rec is not None and oa_rec is not rec:
        titles.append(oa_rec.title)
        journals.append(oa_rec.journal)
    if rec.source == "datacite" and notes:
        _add_message(result, f"Metadatos de DataCite ({'; '.join(notes)}).")
    return _apply_citation(result, full, citation_text, [t for t in titles if t], [j for j in journals if j])


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
    result = Verification(pmid, "pmid", "verificada", title=rec.title, year=rec.year, journal=rec.journal,
                          authors=rec.authors[:5], retracted=bool(rec.retracted),
                          retraction_checked_via=["pubmed"], source="pubmed", record=full)
    return _apply_citation(result, full, citation_text, [rec.title], [rec.journal])


def verify_entry(client, entry_text: str) -> list[Verification]:
    """Verifica todos los identificadores de una entrada bibliográfica."""
    ids = extract_identifiers(entry_text)
    results = [verify_doi(client, d, entry_text) for d in ids["doi"]]
    results += [verify_pmid(client, p, entry_text) for p in ids["pmid"]]
    return results
