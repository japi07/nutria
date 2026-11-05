"""Comprobación de informes y sello de verificación (falla en cerrado).

Sin red (`check_report`):
- Estado: se lee del front matter YAML (primer bloque `---` del documento; lo que va dentro de comentarios HTML
  no cuenta, tampoco antes del front matter). Admite comillas y comentarios `#` de YAML. Un estado ilegible,
  duplicado, desconocido o fuera del front matter es un error y se trata como estricto; en un informe (cualquier
  documento bajo una carpeta `informes`, también en subcarpetas) el estado es obligatorio. Lo leen igual
  `check_report`, `stamp_status` y el hook (`status_info`).
- Estructura: las 20 secciones numeradas (sin duplicados), marcadores `[POR COMPLETAR]` y secciones vacías (error
  en revisado o final, aviso en borrador), destino digestivo en la sección 7 (informes de rumiantes o de especie no
  declarada) y, en la sección 14, la alegación prevista, la ruta regulatoria y el estado de las compuertas.
- Etiquetas: se detectan sin distinguir mayúsculas (`[pub]` cuenta como `[PUB]`, con aviso de formato), también en
  encabezados, citas, tablas y bloques de código; una etiqueta escrita en código solo se ignora si el código no
  contiene nada más (mención del término). Cada segmento que empieza en una `[PUB]` necesita su propio `[Rn]`
  antes de la etiqueta siguiente (los `[Rn]` escritos antes de la primera etiqueta cuentan para ella). Las cifras
  con unidades (%, L/d, kg/d, mg/kg MS, g/t, Mcal, MJ, mmol/L, ppm...) sin etiqueta ni referencia son error en
  revisado o final y aviso en borrador; las citas autor-año sin `[Rn]` son error.
- Bibliografía: la sección 20 o, si no existe, la última sección de nivel 2 titulada Bibliografía o Referencias.
  Una entrada puede ocupar varias líneas (hasta la siguiente entrada, una línea en blanco o un encabezado). Un
  `[Rn]` duplicado es error; una entrada `[Rn]` con identificador fuera de la bibliografía no se verifica: aviso
  (error en estricto).
- Anexo A, por afirmación: cada línea `[PUB]` con `[Rn]` necesita una fila de ese `[Rn]` cuya columna Afirmación
  reproduzca la afirmación y cuyo Respaldo sea `respalda` o `parcial`. Coincidencia: tras normalizar (minúsculas,
  sin acentos, sin etiquetas ni `[Rn]`, sin formato ni puntuación), el texto de la fila debe aparecer literalmente,
  con al menos 4 palabras (o el párrafo entero si es más corto), en el párrafo de la afirmación y solapar la línea
  de la `[PUB]`; `...` o `…` separan fragmentos que deben aparecer en orden. Una fila `no_respalda` del mismo par
  (Rn, afirmación) es error aunque otra diga `respalda`, y las filas con valores distintos para el mismo par
  también.
- Registro (si se pasa `conn`): las referencias con DOI o PMID de las líneas `[PUB]` deben ser fuentes registradas
  y verificadas, con la retractación comprobada (error en estricto, aviso en borrador; una fuente retractada es
  siempre error). En revisado o final, `revisado_por_humano` debe coincidir con una revisión registrada por una
  persona (`nutria informe revisar`) para el contenido exacto del informe: si el informe cambia, deja de valer.

Con red (`check_report_online`, sin caché): cada DOI/PMID existe, corresponde a la cita (título, año y primer
autor) y su retractación es conocida y negativa; las referencias con solo URL valen únicamente si una persona las
registró con `nutria verificar manual`. `check_support_quotes`: si la columna Motivo del Anexo A cita texto literal
entre comillas, se busca en el resumen de la fuente (PubMed u OpenAlex); si no aparece o no hay resumen, aviso. El
texto completo de las fuentes no se comprueba.

No se comprueba: que cada fuente diga lo que se le atribuye (lo juzga `verificador-citas` en el Anexo A y lo revisa
una persona) ni la calidad del razonamiento.

El sello (`<informe>.verificacion.json`) guarda la huella SHA-256 del texto EXACTO comprobado y el detalle de la
verificación de cada referencia, y va firmado con HMAC (`nutria.sello`). Solo lo escribe `write_stamp` a partir del
resultado de `verify_report`. `stamp_status` vuelve a pasar `check_report` sobre el texto actual: el sello solo es
válido si la verificación en línea fue completa y coherente con el detalle, no hubo errores, la estructura se
comprobó entera (en informes) y el informe no ha cambiado.
"""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import tomllib
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import __version__
from .. import sello
from ..provenance import sha256_file, sha256_text, utc_now
from ..verify.citations import extract_identifiers, verify_entry

SECTION_TITLES = {
    1: "Título", 2: "Problema", 3: "Estado actual", 4: "Necesidad no resuelta", 5: "Hipótesis principal",
    6: "Hipótesis alternativas", 7: "Mecanismo", 8: "Candidatos", 9: "Evidencia", 10: "Favorables/desfavorables",
    11: "Estadística", 12: "Simulaciones", 13: "Limitaciones", 14: "Seguridad y regulación",
    15: "Formulación", 16: "Comparación comercial", 17: "Economía", 18: "Validación experimental",
    19: "Conclusión", 20: "Bibliografía",
}
LABEL_NAMES = ("PUB", "DER", "HIP", "SUP", "DESC")
LABELS = tuple(f"[{name}]" for name in LABEL_NAMES)
KNOWN_STATUSES = ("borrador", "revisado", "final")
STRICT_STATUSES = ("revisado", "final")
SUPPORT_OK = ("respalda", "parcial")
SUPPORT_VALUES = ("respalda", "parcial", "no_respalda", "no_verificable")
PENDING_MARK = "[POR COMPLETAR]"
DEFAULT_GATES = ("seguridad", "regulatoria", "analitica", "propiedad_intelectual", "destino_digestivo", "etica")
MIN_ROW_WORDS = 4
NO_REVIEWER = ("", "null", "none", "no", "pendiente", "~", "-", "false")
REVIEW_CODE = "revision_humana"

REF_RE = re.compile(r"\[(R\d+)\]")
LABEL_RE = re.compile(r"(?i:\[\s*(pub|der|hip|sup|desc)\s*\])|[({]\s*(PUB|DER|HIP|SUP|DESC)\s*[)}]")
BAD_LABEL_RE = re.compile(r"(?i:\[\s*(?:pub|der|hip|sup|desc)\b[^\]\n]*[^\]\s][^\]\n]*\])")
GROUPED_REFS_RE = re.compile(r"\[\s*R\d+\s*(?:[,;]|\by\b|-|–)\s*R?\d+[^\]\n]*\]")
LOWER_REF_RE = re.compile(r"\[(r\d+)\]")
PENDING_RE = re.compile(r"\[\s*por\s*completar", re.IGNORECASE)
ENTRY_RE = re.compile(r"^\s{0,3}(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*|__)?\[(R\d+)\](?:\*\*|__)?\s*[:.]?\s*(.*)$")
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
LIST_ITEM_RE = re.compile(r"^\s{0,3}(?:>\s*)*(?:[-*+]|\d+[.)])\s+")
TERM_SPAN_RE = re.compile(r"(?i)^\[?\s*(?:pub|der|hip|sup|desc|rn|r\d+|por\s+completar)\s*\]?$")
ID_HINT_RE = re.compile(r"\b10\.\d{4,9}/|\bPMID:?\s*\d{5,9}\b|https?://", re.IGNORECASE)
QUOTE_RE = re.compile(r"«([^»]+)»|\"([^\"]+)\"|“([^”]+)”")

_NUM = r"(?<![\w.,/])[-+±−]?\d+(?:[.,]\d+)*"
_WORD_UNITS = ("puntos porcentuales", "por ciento", "ppm", "ppb", "pp", "mEq", "mmol", "μmol", "umol", "nmol", "mol",
               "mg", "μg", "ug", "ng", "kg", "g", "t", "Mcal", "kcal", "cal", "MJ", "kJ", "mL", "dL", "L", "UI", "IU",
               "UFC", "CFU", "EUR", "USD")
_SYMBOL_UNITS = ("%", "‰", "°C", "€")
UNIT_RE = re.compile(
    _NUM + r"\s?(?:(?:" + "|".join(re.escape(u) for u in sorted(_WORD_UNITS, key=len, reverse=True))
    + r")(?![^\W\d_])|(?:" + "|".join(re.escape(u) for u in _SYMBOL_UNITS) + "))",
    re.IGNORECASE)
_NAME = r"[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚáéíóúñü'\-]+"
AUTHOR_YEAR_RES = (
    re.compile(rf"\(\s*{_NAME}(?:\s+(?:et\s+al\.?|y|and|&)\s*(?:{_NAME})?)?,?\s+(?:19|20)\d{{2}}[a-z]?\s*[;)]"),
    re.compile(rf"\b{_NAME}\s+(?:et\s+al\.?|y\s+{_NAME}|and\s+{_NAME})\s*\(\s*(?:19|20)\d{{2}}[a-z]?\s*\)"),
)
_RUMINANT_RE = re.compile(
    r"vacun|bovin|vaca|ternero|ternera|novill|buey|toro|ovin|oveja|cordero|caprin|cabra|cabrit|bufal|rumiant|"
    r"ciervo|camel|llama|alpaca|cattle|\bcows?\b|dairy|beef|calf|calves|heifer|steer|\bbulls?\b|sheep|\bewes?\b|"
    r"lamb|goat|buffalo|ruminant|deer", re.IGNORECASE)
_NON_RUMINANT_RE = re.compile(
    r"porcin|cerdo|lechon|cerda|pollo|broiler|gallina|ponedora|\baves?\b|pavo|conejo|\bpez\b|peces|acuicult|salmon|"
    r"trucha|tilapia|caball|equin|perro|gato|\bpigs?\b|swine|\bsows?\b|piglet|poultry|chicken|\bhens?\b|turkey|"
    r"rabbit|\bfish|trout|horse|equine|\bdogs?\b|\bcats?\b", re.IGNORECASE)


@dataclass
class Issue:
    severity: str          # error | aviso
    message: str
    line: int | None = None
    code: str | None = None

    def __str__(self) -> str:
        where = f"línea {self.line}: " if self.line else ""
        return f"[{self.severity.upper()}] {where}{self.message}"


class StampError(RuntimeError):
    """No se puede escribir el sello (el informe cambió durante la comprobación o el resultado no es coherente)."""


# ============================================================================ texto visible y estructura
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
# Letras cirílicas y griegas que se confunden con latinas (para que «[РUB]» o «еstado» no escapen a la comprobación).
_CONFUSABLES = str.maketrans("аеорсухіѕјԁһАВЕКМНОРСТХІЅЈΑΒΕΗΙΚΜΝΟΡΤΧΥΖοα",
                             "aeopcyxisjdhABEKMHOPCTXISJABEHIKMNOPTXYZoa")


def _visible(line: str) -> str:
    """Aproxima el texto que ve quien lee el Markdown, para comprobarlo: NFKC, sin caracteres de formato invisibles
    (anchura cero, marcas de dirección), letras cirílicas o griegas que imitan a las latinas, entidades HTML
    decodificadas y sin escapes con barra invertida."""
    line = unicodedata.normalize("NFKC", line)
    if any(unicodedata.category(ch) == "Cf" for ch in line):
        line = "".join(ch for ch in line if unicodedata.category(ch) != "Cf")
    line = line.translate(_CONFUSABLES)
    if "&" in line:
        line = re.sub(r"[\r\n]", " ", html.unescape(line))
    if "\\" in line:
        line = _ESCAPE_RE.sub(r"\1", line)
    return line


def _plain(text: str) -> str:
    """Minúsculas, sin acentos y con '_' como espacio (comparación de títulos y nombres)."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text.lower().replace("_", " ")).strip()


def _code_spans(line: str) -> list[tuple[int, int]]:
    spans, i = [], 0
    while True:
        m = re.compile(r"`+").search(line, i)
        if not m:
            return spans
        close = re.compile(r"(?<!`)" + m.group(0) + r"(?!`)").search(line, m.end())
        if not close:
            i = m.end()
            continue
        spans.append((m.start(), close.end()))
        i = close.end()


def _strip_inline_comments(text: str, keep_unclosed: bool = False) -> tuple[str, bool]:
    """Quita los comentarios HTML completos de una línea (fuera de código). Devuelve (texto, queda_abierto)."""
    spans = _code_spans(text)
    out, pos = [], 0
    while True:
        start = text.find("<!--", pos)
        while start != -1 and any(a <= start < b for a, b in spans):
            start = text.find("<!--", start + 1)
        if start == -1:
            out.append(text[pos:])
            return "".join(out), False
        out.append(text[pos:start])
        end = text.find("-->", start + 2)
        if end == -1:
            if keep_unclosed:
                out.append(text[start:])
                return "".join(out), False
            return "".join(out), True
        pos = end + 3


def _opens_fence(line: str) -> str | None:
    m = FENCE_RE.match(line)
    if not m or (m.group(1)[0] == "`" and "`" in m.group(2)):
        return None
    return m.group(1)


def _closes_fence(line: str, fence: str) -> bool:
    m = FENCE_RE.match(line)
    return bool(m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip())


def _strip_comments(lines: list[str]) -> tuple[list[str], int | None]:
    """Quita los comentarios HTML fuera de bloques de código, conservando el número de líneas. Un comentario sin
    cerrar no se quita (su contenido se comprueba) y se devuelve su línea para informar del error."""
    out: list[str] = []
    fence = None
    in_comment = False
    opened_at = None
    for idx, line in enumerate(lines):
        if in_comment:
            end = line.find("-->")
            if end == -1:
                out.append("")
                continue
            in_comment = False
            rest, opened = _strip_inline_comments(line[end + 3:])
            out.append(rest)
            if opened:
                in_comment, opened_at = True, idx
            continue
        if fence is not None:
            if _closes_fence(line, fence):
                fence = None
            out.append(line)
            continue
        opening = _opens_fence(line)
        if opening:
            fence = opening
            out.append(line)
            continue
        rest, opened = _strip_inline_comments(line)
        out.append(rest)
        if opened:
            in_comment, opened_at = True, idx
    if in_comment and opened_at is not None:
        out = out[:opened_at] + [_strip_inline_comments(line, keep_unclosed=True)[0] for line in lines[opened_at:]]
        return out, opened_at
    return out, None


@dataclass
class _Doc:
    lines: list[str]                          # texto visible, sin comentarios (mismo número de líneas)
    fence: list[bool]                         # línea dentro de un bloque de código cercado
    headings: list[tuple[int, int, str]]      # (índice, nivel, título)
    fm_start: int | None = None               # delimitador inicial del front matter
    fm_end: int | None = None                 # delimitador final (None si no se cerró)
    issues: list[Issue] = field(default_factory=list)

    @property
    def fm_lines(self) -> range:
        if self.fm_start is None or self.fm_end is None:
            return range(0)
        return range(self.fm_start + 1, self.fm_end)

    def in_front_matter(self, idx: int) -> bool:
        if self.fm_start is None:
            return False
        return self.fm_start <= idx <= (self.fm_end if self.fm_end is not None else self.fm_start)

    def heading_at(self, idx: int) -> tuple[int, int, str] | None:
        return next((h for h in self.headings if h[0] == idx), None)

    def section_end(self, start: int, level: int) -> int:
        return next((i for i, lvl, _ in self.headings if i > start and lvl <= level), len(self.lines))


def _normalize_newlines(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return text[1:] if text.startswith("\ufeff") else text


def _parse(text: str) -> _Doc:
    raw = _normalize_newlines(text).split("\n")
    stripped, unclosed = _strip_comments(raw)
    lines = [_visible(line) for line in stripped]
    doc = _Doc(lines=lines, fence=[False] * len(lines), headings=[])
    if unclosed is not None:
        doc.issues.append(Issue("error", "Comentario HTML sin cerrar: su contenido no se ve al renderizar el "
                                         "documento (ciérralo con -->); se comprueba como texto", unclosed + 1))
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is not None and lines[first].rstrip() == "---":
        doc.fm_start = first
        doc.fm_end = next((j for j in range(first + 1, len(lines)) if lines[j].rstrip() in ("---", "...")), None)
        if doc.fm_end is None:
            doc.issues.append(Issue("error", "El front matter (bloque --- del principio) no está cerrado", first + 1))
    fence = None
    for i, line in enumerate(lines):
        if doc.in_front_matter(i):
            continue
        if fence is not None:
            doc.fence[i] = True
            if _closes_fence(line, fence):
                fence = None
            continue
        opening = _opens_fence(line)
        if opening:
            fence = opening
            doc.fence[i] = True
            continue
        m = HEADING_RE.match(line)
        if m:
            title = re.sub(r"[ \t]+#+[ \t]*$", "", m.group(2) or "").strip()
            doc.headings.append((i, len(m.group(1)), title))
    return doc


# ============================================================================ front matter y estado
_FM_KEY_RE = re.compile(r"""^(\s*)(?:-\s+)?(?:"([^"]*)"|'([^']*)'|([^\s:#'"\-?][^:]*?))[ \t]*:(?:[ \t]+(.*)|[ \t]*)$""")


def _yaml_value(rest: str | None) -> tuple[str | None, str | None]:
    """Valor escalar simple de YAML: (valor, problema). Admite texto simple, comillas y comentarios '#'."""
    rest = (rest or "").strip()
    if not rest or rest.startswith("#"):
        return None, None
    if rest[0] == "'":
        m = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", rest)
        return (m.group(1).replace("''", "'"), None) if m else (None, "comillas simples sin cerrar o con texto detrás")
    if rest[0] == '"':
        m = re.fullmatch(r'"([^"\\]*)"\s*(?:#.*)?', rest)
        return (m.group(1), None) if m else (None, "comillas dobles con escapes, sin cerrar o con texto detrás")
    if rest[0] in "[{&*!|>%@`" or rest == "-" or rest.startswith("- "):
        return None, f"valor YAML no admitido ('{rest[:12]}'): usa texto simple"
    return re.split(r"\s+#", rest, maxsplit=1)[0].strip(), None


def _fm_fields(doc: _Doc) -> tuple[dict[str, list[tuple[int, str | None, str | None]]], list[int]]:
    """{clave en minúsculas: [(línea, valor, problema)]} y las líneas que no se pudieron leer como clave: valor."""
    fields: dict[str, list[tuple[int, str | None, str | None]]] = {}
    unread: list[int] = []
    rng = list(doc.fm_lines)
    for pos, i in enumerate(rng):
        line = doc.lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _FM_KEY_RE.match(line)
        if not m:
            unread.append(i)
            continue
        indent = len(m.group(1))
        key = (m.group(2) if m.group(2) is not None else m.group(3) if m.group(3) is not None else m.group(4) or "")
        value, problem = _yaml_value(m.group(5))
        if problem is None:
            # Una línea siguiente más sangrada que no es otra clave continúa el valor (YAML lo uniría): ilegible.
            nxt = next((j for j in rng[pos + 1:] if doc.lines[j].strip() and not doc.lines[j].lstrip().startswith("#")),
                       None)
            if nxt is not None:
                nm = _FM_KEY_RE.match(doc.lines[nxt])
                nxt_indent = len(doc.lines[nxt]) - len(doc.lines[nxt].lstrip())
                if nxt_indent > indent and (nm is None or doc.lines[nxt].lstrip().startswith("-")):
                    problem = "valor en varias líneas: escríbelo en una sola línea"
        fields.setdefault(key.strip().lower(), []).append((i, value, problem))
    return fields, unread


@dataclass
class StatusInfo:
    value: str | None          # borrador | revisado | final | None (sin estado) o el valor desconocido leído
    strict: bool               # revisado o final, o estado ilegible (falla en cerrado)
    problem: str | None = None
    line: int | None = None


def _status_in_body(doc: _Doc) -> StatusInfo | None:
    """Un 'estado: revisado|final' fuera del front matter: falla en cerrado (estricto y error)."""
    for i, line in enumerate(doc.lines):
        if doc.fence[i] or doc.in_front_matter(i):
            continue
        m = re.match(r"^\s*(?:[-*>]\s*)*(?:\*\*|__)?estado(?:\*\*|__)?\s*:\s*(?:\*\*|__)?[\"']?(revisado|final)\b", line,
                     re.IGNORECASE)
        if m:
            return StatusInfo(m.group(1).lower(), True, "El estado debe ir en el front matter (bloque --- al principio "
                                                        "del documento), no en el cuerpo", i + 1)
    return None


def _status_of(doc: _Doc) -> StatusInfo:
    if doc.fm_start is None:
        return _status_in_body(doc) or StatusInfo(None, False)
    if doc.fm_end is None:
        return StatusInfo(None, True, "Front matter sin cerrar: no se puede leer el estado", doc.fm_start + 1)
    fields, unread = _fm_fields(doc)
    for i in unread:
        if re.search(r"estado", doc.lines[i], re.IGNORECASE):
            return StatusInfo(None, True, f"Línea del front matter ilegible con 'estado': «{doc.lines[i].strip()}»",
                              i + 1)
    for key, entries_ in fields.items():
        if key != "estado" and re.search(r"(?<![a-z0-9_])estado(?![a-z0-9_])", key):
            return StatusInfo(None, True, f"Clave del front matter ilegible con 'estado': «{key}»", entries_[0][0] + 1)
    entries = fields.get("estado", [])
    if not entries:
        return _status_in_body(doc) or StatusInfo(None, False)
    for line, _value, problem in entries:
        if problem:
            return StatusInfo(None, True, f"Estado ilegible en el front matter: {problem}", line + 1)
    values = [(line, (value or "").strip().lower()) for line, value, _ in entries]
    distinct = {v for _, v in values}
    if len(distinct) > 1:
        strictest = next((s for s in ("final", "revisado") if s in distinct), sorted(distinct)[0])
        return StatusInfo(strictest, True, "Estado duplicado en el front matter con valores distintos: "
                          + ", ".join(sorted(v or "(vacío)" for v in distinct)), values[1][0] + 1)
    line, value = values[0]
    if not value:
        return StatusInfo(None, False)
    if value not in KNOWN_STATUSES:
        return StatusInfo(value, True, f"Estado «{value}» no reconocido (usa borrador, revisado o final)", line + 1)
    return StatusInfo(value, value in STRICT_STATUSES, None, line + 1)


def status_info(text: str) -> StatusInfo:
    """Estado del documento tal como lo leen la comprobación, el sello y el hook."""
    return _status_of(_parse(text))


def report_status(text: str) -> str | None:
    """Valor del estado (compatibilidad); usa `status_info` para saber si es ilegible."""
    return status_info(text).value


def _reviewer_of(doc: _Doc) -> tuple[str | None, str | None]:
    fields, unread = _fm_fields(doc)
    for i in unread:
        if re.search(r"revisado", doc.lines[i], re.IGNORECASE):
            return None, f"Línea del front matter ilegible con 'revisado_por_humano': «{doc.lines[i].strip()}»"
    entries = fields.get("revisado_por_humano", [])
    problems = [p for _, _, p in entries if p]
    if problems:
        return None, f"revisado_por_humano ilegible: {problems[0]}"
    names = {(v or "").strip() for _, v, _ in entries if (v or "").strip().lower() not in NO_REVIEWER}
    if len(names) > 1:
        return None, "revisado_por_humano duplicado con valores distintos: " + ", ".join(sorted(names))
    return (names.pop() if names else None), None


def human_reviewer(text: str) -> str | None:
    return _reviewer_of(_parse(text))[0]


def front_matter_value(text: str, key: str) -> str | None:
    fields, _ = _fm_fields(_parse(text))
    values = [v for _, v, p in fields.get(key.lower(), []) if v and not p]
    return values[0] if values else None


def _yaml_scalar(value: str) -> str:
    text = " ".join(str(value).split())
    return "'" + text.replace("'", "''") + "'"


def set_front_matter_fields(text: str, updates: dict[str, str]) -> str:
    """Cambia (o añade) claves de primer nivel del front matter conservando el resto del texto."""
    newline = "\r\n" if "\r\n" in text else "\n"
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    norm = _normalize_newlines(text)
    lines = norm.split("\n")
    doc = _parse(norm)
    if doc.fm_start is None or doc.fm_end is None:
        raise ValueError("El documento no tiene front matter (bloque --- al principio) donde registrar el campo")
    fields, _ = _fm_fields(doc)
    for key, value in updates.items():
        entries = fields.get(key.lower(), [])
        if len(entries) > 1:
            raise ValueError(f"La clave '{key}' está duplicada en el front matter: corrígelo antes")
        new_line = f"{key}: {_yaml_scalar(value)}"
        if entries:
            lines[entries[0][0]] = new_line
        else:
            lines.insert(doc.fm_end, new_line)
            doc = _parse("\n".join(lines))
            fields, _ = _fm_fields(doc)
    return bom + newline.join(lines)


def review_digest(text: str) -> str:
    """Huella del contenido revisado por una persona: SHA-256 del texto normalizado (saltos de línea, espacios
    finales, NFC) con el valor de `revisado_por_humano` del front matter vaciado."""
    norm = _normalize_newlines(text)
    lines = norm.split("\n")
    doc = _parse(norm)
    fields, _ = _fm_fields(doc)
    for i, _value, _problem in fields.get("revisado_por_humano", []):
        lines[i] = "revisado_por_humano:"
    body = "\n".join(line.rstrip() for line in lines).strip("\n") + "\n"
    return sha256_text(unicodedata.normalize("NFC", body))


# ============================================================================ rutas y registro
def _path_variants(path: Path) -> list[Path]:
    path = Path(path)
    out = [Path(os.path.abspath(path))]
    try:
        out.append(path.resolve())
    except OSError:
        pass
    return out


def is_report_path(path: Path | str) -> bool:
    """Un informe es cualquier documento bajo una carpeta `informes` (research/informes y sus subcarpetas). Se
    comprueba con la ruta absoluta y con la resuelta, así que no depende de cómo se escriba la ruta."""
    for variant in _path_variants(Path(path)):
        if any(part.lower() == "informes" for part in variant.parts[:-1]):
            return True
    return False


def project_root(path: Path | str) -> Path | None:
    """Carpeta del proyecto (la que contiene nutria.toml) que contiene `path`, sin mirar NUTRIA_HOME."""
    for variant in _path_variants(Path(path)):
        for candidate in variant.parents:
            if (candidate / "nutria.toml").is_file():
                return candidate
    return None


def registry_path_for(path: Path | str) -> Path | None:
    root = project_root(path)
    if root is not None:
        rel = "research/nutria.sqlite"
        try:
            with open(root / "nutria.toml", "rb") as fh:
                rel = tomllib.load(fh).get("rutas", {}).get("base_datos", rel)
        except (OSError, tomllib.TOMLDecodeError):
            pass
        return root / rel
    for variant in _path_variants(Path(path)):
        for candidate in variant.parents:
            if candidate.name.lower() == "research":
                return candidate / "nutria.sqlite"
    return None


def open_registry_for(path: Path | str):
    """Conexión al registro del proyecto del documento, o None si no hay registro."""
    db_path = registry_path_for(path)
    if db_path is None or not db_path.is_file():
        return None
    from .. import db
    try:
        return db.connect(db_path)
    except sqlite3.Error:
        return None


def report_key(path: Path | str) -> str:
    """Identificador estable del informe en el registro: ruta relativa a la raíz del proyecto."""
    resolved = Path(path).resolve()
    root = project_root(resolved)
    if root is not None:
        try:
            return resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return resolved.as_posix()


# ============================================================================ bibliografía
@dataclass
class BibEntry:
    ref: str
    line: int          # 1-based
    text: str


@dataclass
class _Bib:
    start: int
    end: int
    entries: list[BibEntry]
    stray: list[int]
    entry_lines: set[int] = field(default_factory=set)     # índices de las líneas que forman las entradas


def _bibliography(doc: _Doc) -> _Bib | None:
    level2 = [(i, title) for i, lvl, title in doc.headings if lvl == 2]
    numbered = [i for i, title in level2 if re.match(r"20\.(\s|$)", title)]
    named = [i for i, title in level2 if re.match(r"(\d+\.\s*)?(bibliograf|referencias?\b)", _plain(title))]
    if numbered:
        start = numbered[-1]
    elif named:
        start = named[-1]
    else:
        return None
    end = doc.section_end(start, 2)
    entries: list[BibEntry] = []
    stray: list[int] = []
    entry_lines: set[int] = set()
    current = None
    for i in range(start + 1, end):
        line = doc.lines[i]
        if doc.fence[i] or not line.strip() or doc.heading_at(i):
            current = None
            if doc.fence[i] and ID_HINT_RE.search(line):
                stray.append(i + 1)
            continue
        m = ENTRY_RE.match(line)
        if m:
            current = BibEntry(m.group(1), i + 1, m.group(2).strip())
            entries.append(current)
            entry_lines.add(i)
        elif current is not None:
            current.text = f"{current.text} {line.strip()}".strip()
            entry_lines.add(i)
        elif ID_HINT_RE.search(line):
            stray.append(i + 1)
    return _Bib(start, end, entries, stray, entry_lines)


def bibliography_entries(text: str) -> list[BibEntry]:
    """Todas las entradas de la bibliografía, incluidas las duplicadas."""
    bib = _bibliography(_parse(text))
    return bib.entries if bib else []


def _entry_urls(entry_text: str) -> list[str]:
    return [u.rstrip(".,;") for u in re.findall(r"https?://[^\s)>\]]+", entry_text)]


def _bibliography_ids(text: str) -> list[str]:
    ids = []
    for entry in bibliography_entries(text):
        found = extract_identifiers(entry.text)
        ids.extend(f"doi:{d}" for d in found["doi"])
        ids.extend(f"pmid:{p}" for p in found["pmid"])
        if not found["doi"] and not found["pmid"]:
            ids.extend(f"url:{u}" for u in _entry_urls(entry.text))
    return sorted(ids)


# ============================================================================ líneas, etiquetas y párrafos
def _drop_term_spans(line: str) -> str:
    """Quita las marcas de código; el código que solo contiene una etiqueta o un [Rn] (mención del término) se
    ignora, el resto se comprueba como texto."""
    def repl(m: re.Match) -> str:
        inner = m.group(2).strip()
        return " " if TERM_SPAN_RE.match(inner) else f" {inner} "
    return re.sub(r"(`+)(.+?)\1", repl, line)


@dataclass
class _Line:
    text: str
    labels: list[tuple[int, str, str]]   # (posición, nombre canónico, texto original)
    refs: list[tuple[int, str]]


def _scan(line: str) -> _Line:
    text = _drop_term_spans(line)
    # El énfasis y las etiquetas HTML no deben partir una etiqueta: «[P**UB**]» o «[P<b>UB</b>]» se ven como [PUB].
    text = re.sub(r"</?[A-Za-z][^>]*>", "", text)
    text = re.sub(r"[*_~]", "", text)
    labels = [(m.start(), (m.group(1) or m.group(2)).upper(), m.group(0)) for m in LABEL_RE.finditer(text)]
    refs = [(m.start(), m.group(1)) for m in REF_RE.finditer(text)]
    return _Line(text, labels, refs)


def _norm_words(text: str) -> list[str]:
    text = REF_RE.sub(" ", text)
    text = LABEL_RE.sub(" ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z0-9]+", text)


def _paragraphs(doc: _Doc, body: set[int]) -> list[list[int]]:
    paras: list[list[int]] = []
    current: list[int] = []

    def flush() -> None:
        if current:
            paras.append(current.copy())
            current.clear()

    for i, line in enumerate(doc.lines):
        stripped = line.strip()
        if i not in body or not stripped:
            flush()
            continue
        if doc.heading_at(i) or doc.fence[i] or stripped.lstrip(">").strip().startswith("|"):
            flush()
            paras.append([i])
            continue
        if LIST_ITEM_RE.match(line):
            flush()
        current.append(i)
    flush()
    return paras


# ============================================================================ Anexo A
@dataclass
class SupportRow:
    line: int            # 1-based
    refs: list[str]
    claim: str
    value: str
    raw_value: str
    reason: str


def _cells(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", inner)]


def _support_value(raw: str) -> str:
    value = re.sub(r"[*`~]", "", _plain(raw)).strip(" .;:,")
    return re.sub(r"[\s\-]+", "_", value)


def _annex_ranges(doc: _Doc, pattern: str) -> list[tuple[int, int]]:
    return [(i, doc.section_end(i, lvl)) for i, lvl, title in doc.headings if re.match(pattern, _plain(title))]


def _support_rows(doc: _Doc) -> tuple[list[SupportRow], list[Issue], bool, bool]:
    """(filas, incidencias, hay cabecera con Respaldo, hay columna Afirmación)."""
    rows: list[SupportRow] = []
    issues: list[Issue] = []
    header = claim_col = False
    for start, end in _annex_ranges(doc, r"anexo\s+a\b"):
        cols = None
        for i in range(start + 1, end):
            stripped = doc.lines[i].strip()
            if doc.fence[i] or not stripped.startswith("|"):
                continue
            cells = _cells(stripped)
            if all(re.fullmatch(r":?-+:?", c) or not c for c in cells):
                continue
            low = [_plain(c) for c in cells]
            if "respaldo" in low:
                cols = {"ref": next((k for k, c in enumerate(low) if c in ("ref", "refs", "referencia", "referencias",
                                                                           "rn")), 0),
                        "claim": next((k for k, c in enumerate(low) if c.startswith("afirmacion")), None),
                        "value": low.index("respaldo"),
                        "reason": next((k for k, c in enumerate(low) if c.startswith("motivo")), None)}
                header = True
                claim_col = claim_col or cols["claim"] is not None
                continue
            if cols is None:
                issues.append(Issue("aviso", "Fila del Anexo A antes de la cabecera | Ref | Afirmación | Respaldo | "
                                             "Motivo |: no se tiene en cuenta", i + 1))
                continue

            def cell(key: str) -> str:
                k = cols.get(key)
                return cells[k] if k is not None and k < len(cells) else ""
            refs = list(dict.fromkeys(re.findall(r"\bR\d+\b", cell("ref"))))
            if not refs:
                issues.append(Issue("aviso", "Fila del Anexo A sin referencia [Rn] reconocible", i + 1))
                continue
            rows.append(SupportRow(i + 1, refs, cell("claim"), _support_value(cell("value")), cell("value"),
                                   cell("reason")))
    return rows, issues, header, claim_col


def _fragments(claim: str) -> list[list[str]]:
    parts = re.split(r"\[\s*(?:\.\.\.|…)\s*\]|\(\s*(?:\.\.\.|…)\s*\)|\.\.\.|…", claim)
    return [w for w in (_norm_words(p) for p in parts) if w]


def _find(words: list[str], frag: list[str], start: int) -> list[int]:
    n = len(frag)
    return [k for k in range(start, len(words) - n + 1) if words[k:k + n] == frag]


def _row_covers(claim: str, para_words: list[str], owners: list[int], target: int) -> bool:
    """La fila cubre la afirmación si su texto aparece en el párrafo (fragmentos en orden) y solapa la línea."""
    frags = _fragments(claim)
    total = sum(len(f) for f in frags)
    if not frags or total < min(MIN_ROW_WORDS, len(para_words)):
        return False
    for first in _find(para_words, frags[0], 0):
        covered = list(range(first, first + len(frags[0])))
        pos, ok = first + len(frags[0]), True
        for frag in frags[1:]:
            hits = _find(para_words, frag, pos)
            if not hits:
                ok = False
                break
            covered += list(range(hits[0], hits[0] + len(frag)))
            pos = hits[0] + len(frag)
        if ok and any(owners[k] == target for k in covered):
            return True
    return False


# ============================================================================ comprobación sin red
def _lookup_source(conn, kind: str, value: str):
    column = "doi" if kind == "doi" else "pmid"
    try:
        if column == "doi":
            return conn.execute("SELECT * FROM sources WHERE lower(doi) = ?", (value.lower(),)).fetchone()
        return conn.execute("SELECT * FROM sources WHERE pmid = ?", (str(value),)).fetchone()
    except sqlite3.Error:
        return "error"


def _registry_issues(conn, ref: str, entries: list[BibEntry], line: int, strict: bool) -> list[Issue]:
    out = []
    sev = "error" if strict else "aviso"
    for entry in entries:
        ids = extract_identifiers(entry.text)
        for kind, values in (("doi", ids["doi"]), ("pmid", ids["pmid"])):
            for value in values:
                row = _lookup_source(conn, kind, value)
                label = f"{ref}: {kind.upper()} {value}"
                if row == "error":
                    out.append(Issue(sev, f"{label}: no se pudo consultar el registro de fuentes", line))
                elif row is None:
                    out.append(Issue(sev, f"{label} se usa en una afirmación [PUB] pero no está registrado como fuente "
                                          f"verificada (`nutria fuente agregar --{kind} {value}`)", line))
                elif row["retracted"] == 1:
                    out.append(Issue("error", f"{label} está RETRACTADO según el registro de fuentes", line))
                elif not row["verified"]:
                    out.append(Issue(sev, f"{label} está registrado pero sin verificar (`nutria verificar --pendientes`)",
                                     line))
                elif row["retracted"] is None:
                    out.append(Issue(sev, f"{label}: el registro no tiene comprobada la retractación "
                                          "(`nutria verificar --pendientes`)", line))
    return out


def _review_issues(text: str, doc: _Doc, status: str | None, conn, path) -> list[Issue]:
    reviewer, problem = _reviewer_of(doc)
    label = status or "estricto"
    how = "una persona debe revisarlo y registrarlo con `nutria informe revisar <informe> --firma-humana NOMBRE`"
    if problem:
        return [Issue("error", problem, None, REVIEW_CODE)]
    if not reviewer:
        return [Issue("error", f"Estado '{label}' sin revisión humana (revisado_por_humano vacío): {how}", None,
                      REVIEW_CODE)]
    if conn is None:
        return [Issue("error", "No se puede comprobar la revisión humana sin el registro del proyecto "
                               "(research/nutria.sqlite): revisado_por_humano no basta por sí solo", None, REVIEW_CODE)]
    from .. import db
    digest = review_digest(text)
    rows = db.report_reviews(conn, digest=digest)
    if any(r["reviewer"].strip().casefold() == reviewer.casefold() for r in rows):
        return []
    if rows:
        names = ", ".join(sorted({r["reviewer"] for r in rows}))
        return [Issue("error", f"revisado_por_humano dice «{reviewer}», pero la revisión registrada para este "
                               f"contenido es de {names}", None, REVIEW_CODE)]
    prior = db.report_reviews(conn, report=report_key(path)) if path is not None else []
    if prior:
        last = prior[-1]
        return [Issue("error", f"El informe cambió después de la revisión humana de {last['reviewer']} "
                               f"({last['reviewed_at']}): esa revisión ya no vale; {how}", None, REVIEW_CODE)]
    return [Issue("error", f"revisado_por_humano «{reviewer}» no corresponde a ninguna revisión registrada de este "
                           f"contenido: {how}", None, REVIEW_CODE)]


def _species(doc: _Doc) -> str | None:
    fields, _ = _fm_fields(doc)
    values = [v for _, v, p in fields.get("especie", []) if v and not p and v.lower() not in NO_REVIEWER]
    return values[0] if values else None


def is_ruminant(species: str | None) -> bool:
    """Falla en cerrado: sin especie declarada, o si no es claramente de otra especie, se trata como rumiante."""
    if not species:
        return True
    plain = _plain(species)
    if _RUMINANT_RE.search(plain):
        return True
    return not _NON_RUMINANT_RE.search(plain)


def _mentions_gate(gate: str, plain_line: str) -> bool:
    return re.search(rf"\b{re.escape(_plain(gate))}\b", plain_line) is not None


def _pending_and_empty(doc: _Doc, numbered: dict[int, list[int]], require_sections: bool
                       ) -> tuple[dict[str, int], list[tuple[int, int]]]:
    """({apartado con [POR COMPLETAR]: primera línea}, [(sección obligatoria vacía, línea)])."""
    titles = {i: (lvl, title) for i, lvl, title in doc.headings}
    current = "(antes de la primera sección)"
    pending: dict[str, int] = {}
    for i, line in enumerate(doc.lines):
        if i in titles and titles[i][0] <= 2:
            current = titles[i][1]
        if doc.in_front_matter(i):
            continue
        if PENDING_RE.search(_drop_term_spans(line)):
            pending.setdefault(current, i + 1)
    empty = []
    if require_sections:
        for number, idxs in sorted(numbered.items()):
            if number in SECTION_TITLES and idxs:
                start = idxs[0]
                if not any(doc.lines[j].strip() for j in range(start + 1, doc.section_end(start, 2))):
                    empty.append((number, start + 1))
    return pending, empty


def _numbered_sections(doc: _Doc) -> dict[int, list[int]]:
    numbered: dict[int, list[int]] = {}
    for i, lvl, title in doc.headings:
        m = re.match(r"(\d+)\.(?:\s|$)", title)
        if lvl == 2 and m:
            numbered.setdefault(int(m.group(1)), []).append(i)
    return numbered


def check_report(text: str, require_sections: bool = True, strict: bool | None = None, *, conn=None,
                 path: Path | str | None = None, gate_ids=None) -> list[Issue]:
    """Comprueba un documento. `strict` (por defecto: estado revisado o final, o ilegible) exige respaldo por
    afirmación, revisión humana registrada, ningún marcador [POR COMPLETAR] y cifras etiquetadas. `conn` (registro
    del proyecto) activa las comprobaciones de fuentes y de revisión humana. `path` indica si es un informe."""
    doc = _parse(text)
    issues: list[Issue] = list(doc.issues)
    is_report = bool(require_sections) or (path is not None and is_report_path(path))
    st = _status_of(doc)
    if st.problem:
        issues.append(Issue("error", st.problem, st.line))
    elif is_report and st.value is None:
        issues.append(Issue("error", "El informe no declara su estado en el front matter (estado: borrador, revisado "
                                     "o final)", (doc.fm_start + 1) if doc.fm_start is not None else None))
    if strict is None:
        strict = st.strict or (is_report and st.value is None)
    sev_strict = "error" if strict else "aviso"
    gate_ids = list(gate_ids) if gate_ids else list(DEFAULT_GATES)

    # ---- secciones numeradas
    numbered = _numbered_sections(doc)
    if require_sections:
        for number, name in SECTION_TITLES.items():
            found = numbered.get(number, [])
            if not found:
                issues.append(Issue("error", f"Falta la sección {number} ({name})"))
            elif len(found) > 1:
                issues.append(Issue("error", f"Sección {number} duplicada (líneas "
                                             f"{', '.join(str(i + 1) for i in found)})", found[1] + 1))

    # ---- bibliografía y zonas que no son cuerpo
    bib = _bibliography(doc)
    if bib is None:
        issues.append(Issue("error", "No hay sección de bibliografía (sección 20, o «## Bibliografía» o "
                                     "«## Referencias»)"))
    entries = bib.entries if bib else []
    annex_a = _annex_ranges(doc, r"anexo\s+a\b")
    records = _annex_ranges(doc, r"anexo\s+[bc]\b")
    # Fuera del cuerpo solo quedan el front matter, las entradas de la bibliografía y las filas de la tabla del
    # Anexo A: cualquier otro texto de esas secciones se comprueba como el resto del informe.
    excluded: set[int] = set()
    if doc.fm_start is not None:
        fm_range = range(doc.fm_start, (doc.fm_end if doc.fm_end is not None else doc.fm_start) + 1)
        excluded |= set(fm_range)
        for i in fm_range:
            if LABEL_RE.search(_scan(doc.lines[i]).text):
                issues.append(Issue("error", "Etiqueta de evidencia en el front matter: las afirmaciones van en el "
                                             "cuerpo del informe", i + 1))
    if bib:
        excluded |= {bib.start} | bib.entry_lines
        for entry in entries:
            if LABEL_RE.search(_scan(entry.text).text):
                issues.append(Issue("error", f"Etiqueta de evidencia dentro de la entrada {entry.ref} de la bibliografía: "
                                             "las afirmaciones van en el cuerpo, separadas de las referencias",
                                    entry.line))
    for start, end in annex_a:
        excluded |= {start} | {j for j in range(start + 1, end)
                               if not doc.fence[j] and doc.lines[j].strip().startswith("|")}
    body = {i for i in range(len(doc.lines)) if i not in excluded}
    no_numbers = {i for i, lvl, _ in doc.headings if lvl == 1}
    for start, end in records:
        no_numbers |= set(range(start, end))
    for i in numbered.get(1, [])[:1]:
        no_numbers |= set(range(i, doc.section_end(i, 2)))

    # ---- cuerpo: etiquetas, referencias, cifras
    used: dict[str, int] = {}
    pub_lines: list[tuple[int, list[str]]] = []
    unlabeled: list[int] = []
    for i in sorted(body):
        line = doc.lines[i]
        if not line.strip():
            continue
        scan = _scan(line)
        for _pos, ref in scan.refs:
            used.setdefault(ref, i + 1)
        for m in BAD_LABEL_RE.finditer(scan.text):
            issues.append(Issue("error", f"Etiqueta con formato no válido «{m.group(0)}»: escribe la etiqueta y cada "
                                         "referencia por separado, p. ej. [PUB] [R1]", i + 1))
        for m in GROUPED_REFS_RE.finditer(scan.text):
            issues.append(Issue("error", f"Referencias agrupadas «{m.group(0)}»: escribe cada una entre corchetes "
                                         "([R1] [R2])", i + 1))
        for m in LOWER_REF_RE.finditer(scan.text):
            issues.append(Issue("error", f"Referencia «{m.group(0)}» en minúsculas: escribe [{m.group(1).upper()}]",
                                i + 1))
        for _pos, name, raw in scan.labels:
            if raw != f"[{name}]":
                issues.append(Issue("aviso", f"Etiqueta «{raw}» con formato no canónico: cuenta como [{name}]; "
                                             f"escribe [{name}]", i + 1))
        pub_refs: list[str] = []
        if scan.labels:
            first_pos = scan.labels[0][0]
            prefix = [r for p, r in scan.refs if p < first_pos]
            for k, (pos, name, _raw) in enumerate(scan.labels):
                end = scan.labels[k + 1][0] if k + 1 < len(scan.labels) else len(scan.text)
                seg = [r for p, r in scan.refs if pos <= p < end] + (prefix if k == 0 else [])
                if name == "PUB":
                    if not seg:
                        issues.append(Issue("error", "Afirmación [PUB] sin referencia [Rn] (cada [PUB] necesita su "
                                                     "propio [Rn] antes de la etiqueta siguiente)", i + 1))
                    pub_refs += seg
        if pub_refs:
            pub_lines.append((i, list(dict.fromkeys(pub_refs))))
        if any(name == "DER" for _p, name, _r in scan.labels) and not re.search(
                r"c[aá]lculo|nutria |script|anexo c|\[R\d+\]", scan.text, re.IGNORECASE):
            issues.append(Issue("aviso", "[DER] sin indicar el cálculo u orden usada", i + 1))
        if not scan.refs and any(rx.search(scan.text) for rx in AUTHOR_YEAR_RES):
            issues.append(Issue("error", "Cita autor-año sin referencia [Rn] verificable", i + 1))
        if i not in no_numbers and not scan.labels and not scan.refs and UNIT_RE.search(scan.text):
            unlabeled.append(i + 1)
        m = ENTRY_RE.match(line)
        if m and ID_HINT_RE.search(m.group(2)):
            issues.append(Issue(sev_strict, f"Entrada bibliográfica [{m.group(1)}] fuera de la bibliografía: no se "
                                            "verifica; muévela a la sección 20", i + 1))
    for n, line_no in enumerate(unlabeled):
        if n < 10:
            issues.append(Issue(sev_strict, "Cifra con unidades sin etiqueta de evidencia ni referencia", line_no))
    if len(unlabeled) > 10:
        issues.append(Issue(sev_strict, f"{len(unlabeled)} cifras con unidades sin etiqueta en total"))

    # ---- bibliografía: referencias, duplicados, identificadores
    by_ref: dict[str, list[BibEntry]] = {}
    for entry in entries:
        by_ref.setdefault(entry.ref, []).append(entry)
    for ref, line_no in used.items():
        if ref not in by_ref:
            issues.append(Issue("error", f"La referencia {ref} se cita pero no está en la bibliografía", line_no))
    seen_ids: dict[str, str] = {}
    for ref, group in by_ref.items():
        if len(group) > 1:
            issues.append(Issue("error", f"{ref} está duplicada en la bibliografía (líneas "
                                         f"{', '.join(str(e.line) for e in group)}): cada [Rn] debe aparecer una vez",
                                group[1].line))
        if ref not in used:
            issues.append(Issue("aviso", f"{ref} está en la bibliografía pero no se cita", group[0].line))
        for entry in group:
            ids = extract_identifiers(entry.text)
            if not ids["doi"] and not ids["pmid"]:
                if _entry_urls(entry.text):
                    issues.append(Issue("aviso", f"{ref} solo tiene URL: necesita verificación manual firmada",
                                        entry.line))
                else:
                    issues.append(Issue("error", f"{ref} sin DOI, PMID ni URL verificable", entry.line))
            for ident in [f"doi:{d}" for d in ids["doi"]] + [f"pmid:{p}" for p in ids["pmid"]]:
                other = seen_ids.setdefault(ident, ref)
                if other != ref:
                    issues.append(Issue("aviso", f"{ref} y {other} citan el mismo trabajo ({ident})", entry.line))
    for line_no in (bib.stray if bib else []):
        issues.append(Issue(sev_strict, "Identificador en la bibliografía fuera de una entrada [Rn]: no se verifica",
                            line_no))

    # ---- Anexo A: respaldo por afirmación
    rows, row_issues, header, claim_col = _support_rows(doc)
    issues += row_issues
    if rows and not header:
        issues.append(Issue(sev_strict, "La tabla del Anexo A no tiene la cabecera | Ref | Afirmación | Respaldo | "
                                        "Motivo |"))
    if header and not claim_col:
        issues.append(Issue(sev_strict, "La tabla del Anexo A no tiene la columna Afirmación: el respaldo se "
                                        "comprueba por afirmación, no por referencia"))
    for row in rows:
        if row.value not in SUPPORT_VALUES:
            issues.append(Issue("error", f"Anexo A: respaldo '{row.raw_value}' no válido para {', '.join(row.refs)} "
                                         f"(usa {', '.join(SUPPORT_VALUES)})", row.line))
    paras = _paragraphs(doc, body)
    para_of = {i: p for p in paras for i in p}
    words_cache: dict[int, tuple[list[str], list[int]]] = {}
    used_rows: set[int] = set()
    for i, refs in pub_lines:
        para = para_of.get(i, [i])
        key = para[0]
        if key not in words_cache:
            words, owners = [], []
            for j in para:
                w = _norm_words(_drop_term_spans(doc.lines[j]))
                words += w
                owners += [j] * len(w)
            words_cache[key] = (words, owners)
        words, owners = words_cache[key]
        target = i
        if i not in owners:
            before = [j for j in para if j < i and j in owners]
            after = [j for j in para if j > i and j in owners]
            target = before[-1] if before else (after[0] if after else i)
        for ref in refs:
            matching = [r for r in rows if ref in r.refs and _row_covers(r.claim, words, owners, target)]
            for r in matching:
                used_rows.add(r.line)
            values = {r.value for r in matching if r.value in SUPPORT_VALUES}
            where = ", ".join(str(r.line) for r in matching)
            if "no_respalda" in values:
                issues.append(Issue("error", f"{ref} NO respalda la afirmación de la línea {i + 1} (Anexo A, fila "
                                             f"{where})", i + 1))
            elif len(values) > 1:
                issues.append(Issue("error", f"Filas contradictorias del Anexo A ({where}) para {ref} y la afirmación "
                                             f"de la línea {i + 1}: {', '.join(sorted(values))}", i + 1))
            elif values == {"no_verificable"}:
                issues.append(Issue(sev_strict, f"Respaldo de {ref} no verificable: no puede sostener la afirmación "
                                                f"[PUB] de la línea {i + 1}", i + 1))
            elif not values:
                issues.append(Issue(sev_strict, f"Sin comprobación de respaldo de {ref} para la afirmación de la línea "
                                                f"{i + 1}: el Anexo A necesita una fila de {ref} cuya columna "
                                                "Afirmación reproduzca esa afirmación", i + 1))
    for r in rows:
        if r.line in used_rows or r.value not in SUPPORT_VALUES:
            continue
        sev = sev_strict if r.value in ("no_respalda", "no_verificable") else "aviso"
        issues.append(Issue(sev, f"Fila del Anexo A ({', '.join(r.refs)}, {r.value}) que no corresponde a ninguna "
                                 "afirmación [PUB] actual con esa referencia: si la afirmación se reformuló, comprueba "
                                 "la nueva versión", r.line))

    # ---- registro de fuentes
    if conn is not None:
        first_line: dict[str, int] = {}
        for i, refs in pub_lines:
            for ref in refs:
                first_line.setdefault(ref, i + 1)
        for ref, line_no in first_line.items():
            issues += _registry_issues(conn, ref, by_ref.get(ref, []), line_no, strict)

    # ---- secciones con contenido obligatorio
    if require_sections and numbered.get(14):
        start = numbered[14][0]
        content = [doc.lines[j] for j in range(start + 1, doc.section_end(start, 2))]
        joined = _plain("\n".join(content))
        if not re.search(r"alegacion", joined):
            issues.append(Issue(sev_strict, "La sección 14 no recoge la alegación prevista (Reglamento (CE) 767/2009, "
                                            "art. 13)", start + 1))
        if not re.search(r"(ruta|via|clasificacion) (regulatoria|de autorizacion)", joined):
            issues.append(Issue(sev_strict, "La sección 14 no recoge la ruta o clasificación regulatoria", start + 1))
        missing = [g for g in gate_ids
                   if not any(_mentions_gate(g, _plain(c))
                              and re.search(r"\b(superada|bloqueada|no resuelta)\b", _plain(c)) for c in content)]
        if missing:
            issues.append(Issue(sev_strict, "La sección 14 no recoge el estado (superada, bloqueada o no_resuelta) de "
                                            f"las compuertas: {', '.join(missing)}", start + 1))
    if require_sections and numbered.get(7) and is_ruminant(_species(doc)):
        start = numbered[7][0]
        content = [doc.lines[j] for j in range(start + 1, doc.section_end(start, 2))]
        labeled = [_plain(c) for c in content if _scan(c).labels]
        ok = ("destino digestivo" in _plain("\n".join(content))
              and any(re.search(r"rumin|rumen|escape|bypass|by pass|biohidrogen|protegid|proteccion", c) for c in labeled)
              and any(re.search(r"intestin|absor|posrumin|duoden|digestibilidad", c) for c in labeled))
        if not ok:
            issues.append(Issue(sev_strict, "La sección 7 no trata el destino digestivo (regla 5): en rumiantes, "
                                            "degradación o escape ruminal y digestibilidad o absorción intestinal, con "
                                            "etiqueta ([PUB] [Rn], [DER] con el cálculo o [DESC] con cómo obtenerlo)",
                                start + 1))

    # ---- marcadores y secciones vacías
    pending, empty = _pending_and_empty(doc, numbered, require_sections)
    if strict:
        for title, line_no in pending.items():
            issues.append(Issue("error", f"Contenido por completar ({PENDING_MARK}) en «{title}»", line_no))
        for number, line_no in empty:
            issues.append(Issue("error", f"La sección {number} ({SECTION_TITLES[number]}) está vacía", line_no))
    elif pending or empty:
        parts = []
        if pending:
            parts.append(f"{len(pending)} apartado(s) con {PENDING_MARK}")
        if empty:
            parts.append("secciones vacías: " + ", ".join(str(n) for n, _ in empty))
        issues.append(Issue("aviso", "Informe por completar: " + "; ".join(parts) + " (en revisado o final es un error)"))

    # ---- modo estricto: revisión humana y referencias
    if strict:
        issues += _review_issues(text, doc, st.value, conn, path)
        if is_report and not entries:
            issues.append(Issue("error", f"Un informe en estado '{st.value or 'estricto'}' necesita referencias "
                                         "verificables"))
        if is_report and not _species(doc):
            issues.append(Issue("aviso", "El front matter no declara la especie: el destino digestivo se exige como en "
                                         "rumiantes"))
    return issues


def pending_summary(text: str, require_sections: bool = True) -> tuple[int, list[int]]:
    """(número de apartados con [POR COMPLETAR], secciones obligatorias vacías)."""
    doc = _parse(text)
    pending, empty = _pending_and_empty(doc, _numbered_sections(doc), require_sections)
    return len(pending), [n for n, _ in empty]


# ============================================================================ comprobación en línea
def check_report_online(client, text: str, conn=None) -> tuple[list[Issue], list[dict], dict]:
    """Verifica en línea cada entrada (también las duplicadas). Devuelve (incidencias, detalle, resumen).

    Falla en cerrado: lo que no se pueda comprobar (sin red, retractación desconocida, URL sin verificación manual
    firmada) cuenta como pendiente y deja la verificación incompleta.
    """
    issues: list[Issue] = []
    details: list[dict] = []
    summary = {"total": 0, "verificadas": 0, "pendientes": 0, "con_error": 0}
    for entry in bibliography_entries(text):
        ref, line_no = entry.ref, entry.line
        summary["total"] += 1
        ids = extract_identifiers(entry.text)
        if not ids["doi"] and not ids["pmid"]:
            urls = _entry_urls(entry.text)
            manual = None
            if conn is not None:
                from .. import db
                for url in urls:
                    try:
                        manual = db.get_manual_verification(conn, url)
                    except sqlite3.Error:
                        manual = None
                    if manual:
                        break
            if manual:
                summary["verificadas"] += 1
                details.append({"ref": ref, "identifier": manual["url"], "kind": "url", "status": "verificada_manual",
                                "firmado_por": manual["signed_by"], "fecha": manual["signed_at"]})
            else:
                summary["pendientes"] += 1
                details.append({"ref": ref, "identifier": urls[0] if urls else None, "kind": "url",
                                "status": "pendiente_manual"})
                issues.append(Issue("aviso", f"{ref}: referencia con URL sin verificación manual firmada "
                                             "(`nutria verificar manual <URL> --firma-humana NOMBRE`, solo personas)",
                                    line_no))
            continue
        results = verify_entry(client, entry.text)
        ref_ok = True
        for result in results:
            row = result.to_dict()
            row["ref"] = ref
            details.append(row)
            if result.status == "no_encontrada":
                ref_ok = False
                summary["con_error"] += 1
                issues.append(Issue("error", f"{ref}: {result.identifier} no existe ({result.message})", line_no))
            elif result.status in ("error_red", "offline"):
                ref_ok = False
                summary["pendientes"] += 1
                issues.append(Issue("aviso", f"{ref}: {result.identifier} NO verificado ({result.message})", line_no))
            elif result.status != "verificada":
                ref_ok = False
                summary["pendientes"] += 1
                issues.append(Issue("aviso", f"{ref}: {result.identifier} en estado '{result.status}'", line_no))
            elif result.retracted:
                ref_ok = False
                summary["con_error"] += 1
                issues.append(Issue("error", f"{ref}: {result.identifier} está RETRACTADO", line_no))
            elif result.mismatch:
                ref_ok = False
                summary["con_error"] += 1
                issues.append(Issue("error", f"{ref}: {result.message}", line_no))
            elif result.retracted is None:
                ref_ok = False
                summary["pendientes"] += 1
                issues.append(Issue("aviso", f"{ref}: no se pudo comprobar si {result.identifier} está retractado",
                                    line_no))
        if not results:
            ref_ok = False
            summary["pendientes"] += 1
        if ref_ok:
            summary["verificadas"] += 1
    summary["completa"] = summary["pendientes"] == 0 and summary["con_error"] == 0
    return issues, details, summary


def fetch_abstract(client, entry_text: str) -> tuple[str | None, str]:
    """Resumen de la fuente (PubMed por PMID o por DOI; si no, OpenAlex). Devuelve (resumen, origen o motivo)."""
    from ..http import NetworkError, redact
    from ..sources import openalex, pubmed
    cfg = getattr(client, "cfg", None)
    ids = extract_identifiers(entry_text)
    problems = []
    pmids = list(ids["pmid"])
    try:
        if not pmids:
            for doi in ids["doi"]:
                pmid = pubmed.pmid_for_doi(client, doi)
                if pmid:
                    pmids.append(pmid)
                    break
        if pmids:
            info = pubmed.abstracts(client, pmids[:1]).get(pmids[0]) or {}
            if info.get("abstract"):
                return info["abstract"], "PubMed"
    except NetworkError as exc:
        problems.append(f"PubMed: {redact(str(exc), cfg)}")
    except Exception as exc:  # noqa: BLE001 - respuesta inesperada: se informa y se sigue
        problems.append(f"PubMed: {exc.__class__.__name__}")
    for doi in ids["doi"]:
        try:
            record = openalex.work_by_doi(client, doi)
            if record.abstract:
                return record.abstract, "OpenAlex"
        except NetworkError as exc:
            problems.append(f"OpenAlex: {redact(str(exc), cfg)}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"OpenAlex: {exc.__class__.__name__}")
    return None, "; ".join(problems) or "sin resumen en PubMed ni en OpenAlex"


def check_support_quotes(client, text: str) -> tuple[list[Issue], list[dict]]:
    """Compara las citas literales entre comillas de la columna Motivo del Anexo A con el resumen de la fuente.
    Solo el resumen: el texto completo no se comprueba."""
    doc = _parse(text)
    rows, _issues, _h, _c = _support_rows(doc)
    entries: dict[str, list[BibEntry]] = {}
    for entry in bibliography_entries(text):
        entries.setdefault(entry.ref, []).append(entry)
    cache: dict[str, tuple[str | None, str]] = {}
    issues: list[Issue] = []
    details: list[dict] = []
    for row in rows:
        quotes = [q for m in QUOTE_RE.finditer(row.reason) for q in m.groups() if q]
        quotes = [q for q in quotes if len(_norm_words(q)) >= 2]
        for ref in row.refs:
            for quote in quotes:
                if ref not in cache:
                    found = [fetch_abstract(client, e.text) for e in entries.get(ref, [])]
                    good = [f for f in found if f[0]]
                    cache[ref] = good[0] if good else (None, "; ".join(f[1] for f in found) or "sin entrada")
                abstract, origin = cache[ref]
                short = quote if len(quote) <= 80 else quote[:77] + "..."
                if abstract is None:
                    issues.append(Issue("aviso", f"{ref}: no se pudo comprobar la cita «{short}» del Anexo A: resumen no "
                                                 f"disponible ({origin}); el texto completo no se comprueba", row.line))
                    details.append({"ref": ref, "cita": quote, "estado": "sin_resumen"})
                    continue
                words, needle = _norm_words(abstract), _norm_words(quote)
                hit = bool(_find(words, needle, 0))
                details.append({"ref": ref, "cita": quote, "estado": "en_resumen" if hit else "no_en_resumen",
                                "origen": origin})
                if not hit:
                    issues.append(Issue("aviso", f"{ref}: la cita «{short}» del Anexo A no aparece en el resumen de la "
                                                 f"fuente ({origin}); solo se comprueba el resumen, no el texto "
                                                 "completo: indica la página o la tabla", row.line))
    return issues, details


# ============================================================================ verificación y sello
@dataclass
class ReportCheck:
    path: Path
    text: str
    sha256: str
    issues: list[Issue]
    details: list[dict]
    summary: dict | None
    online: bool
    structure: bool
    quotes: list[dict] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


def verify_report(path: Path | str, client=None, conn=None, require_sections: bool = True,
                  gate_ids=None) -> ReportCheck:
    """Comprueba un documento: sin red siempre; en línea si se pasa `client` (sin caché)."""
    path = Path(path)
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    issues = check_report(text, require_sections=require_sections, conn=conn, path=path, gate_ids=gate_ids)
    details: list[dict] = []
    summary = None
    quotes: list[dict] = []
    if client is not None:
        more, details, summary = check_report_online(client, text, conn)
        issues += more
        quote_issues, quotes = check_support_quotes(client, text)
        issues += quote_issues
    return ReportCheck(path, text, sha256_text(raw), issues, details, summary, client is not None, require_sections,
                       quotes)


def stamp_path(report: Path) -> Path:
    report = Path(report)
    return report.with_name(report.name + ".verificacion.json")


def write_stamp(check: ReportCheck) -> dict:
    """Escribe el sello firmado del resultado de `verify_report` (nunca de datos sueltos)."""
    if not isinstance(check, ReportCheck):
        raise StampError("El sello solo se escribe a partir del resultado de verify_report")
    if sha256_text(check.text) != check.sha256 or sha256_file(check.path) != check.sha256:
        raise StampError("El informe cambió durante la verificación: no se sella. Vuelve a ejecutar la comprobación.")
    info = status_info(check.text)
    data = {
        "formato": 2,
        "informe": check.path.name,
        "sha256": check.sha256,
        "fecha": utc_now(),
        "en_linea": check.online,
        "verificacion_completa": bool(check.online and check.summary and check.summary.get("completa")),
        "estructura_completa": check.structure,
        "estado_informe": info.value,
        "revisado_por_humano": human_reviewer(check.text),
        "huella_revision": review_digest(check.text),
        "referencias": _bibliography_ids(check.text),
        "resumen": check.summary or {},
        "errores": len(check.errors),
        "avisos": len(check.issues) - len(check.errors),
        "incidencias": [asdict(i) for i in check.issues],
        "detalle": check.details or [],
        "citas_anexo_a": check.quotes,
        "no_comprobado": "que cada fuente respalde la afirmación (Anexo A, juicio de verificador-citas revisado por "
                         "una persona) y el texto completo de las fuentes",
        "nutria": __version__,
    }
    data["firma"] = sello.sign(data)
    stamp_path(check.path).write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return data


def _detail_gaps(text: str, details) -> list[str]:
    """Referencias de la bibliografía actual sin una verificación correcta en el detalle del sello."""
    ok: set[tuple[str, str]] = set()
    manual: set[str] = set()
    for row in details if isinstance(details, list) else []:
        if not isinstance(row, dict):
            continue
        if (row.get("status") == "verificada" and row.get("retracted") is False and not row.get("discrepancia")
                and row.get("ok", True)):
            ok.add((str(row.get("ref")), str(row.get("identifier")).lower()))
        elif row.get("status") == "verificada_manual":
            manual.add(str(row.get("ref")))
    gaps = []
    for entry in bibliography_entries(text):
        ids = extract_identifiers(entry.text)
        if not ids["doi"] and not ids["pmid"]:
            if entry.ref not in manual:
                gaps.append(entry.ref)
            continue
        for ident in ids["doi"] + ids["pmid"]:
            if (entry.ref, ident.lower()) not in ok:
                gaps.append(f"{entry.ref} ({ident})")
    return gaps


def stamp_status(report: Path | str, conn=None) -> tuple[bool, str]:
    report = Path(report)
    path = stamp_path(report)
    if not path.is_file():
        return False, "Sin sello de verificación: ejecuta `nutria informe comprobar <informe> --online --sellar`"
    try:
        data = json.loads(path.read_text("utf-8"))
    except (ValueError, OSError) as exc:
        return False, f"Sello ilegible o corrupto ({exc.__class__.__name__}): vuelve a verificar"
    if not isinstance(data, dict) or not sello.verify(data):
        return False, "Sello sin firma válida (escrito a mano, alterado o creado en otro equipo): vuelve a verificar"
    if not report.is_file():
        return False, "El informe no existe"
    raw = report.read_bytes()
    try:
        current = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False, "El informe no está en UTF-8: no se puede comprobar"
    if data.get("sha256") != sha256_text(raw):
        return False, "El informe ha cambiado después del sello: vuelve a verificar"
    if not data.get("en_linea"):
        return False, "El sello no incluye verificación en línea de las referencias"
    if not data.get("verificacion_completa"):
        resumen = data.get("resumen") or {}
        return False, (f"Verificación incompleta: {resumen.get('pendientes', '?')} referencia(s) sin comprobar y "
                       f"{resumen.get('con_error', '?')} con error")
    report_doc = is_report_path(report)
    if report_doc and not data.get("estructura_completa"):
        return False, ("El sello no comprobó la estructura completa del informe (usa `nutria informe comprobar "
                       "<informe> --online --sellar`)")
    if data.get("errores", 1) != 0:
        return False, f"El sello registra {data.get('errores')} error(es)"
    if data.get("referencias") != _bibliography_ids(current):
        return False, "Las referencias del informe no coinciden con las del sello"
    gaps = _detail_gaps(current, data.get("detalle"))
    if gaps:
        return False, f"El sello no contiene una verificación correcta de: {', '.join(gaps[:5])}: vuelve a verificar"
    own = conn is None
    if own:
        conn = open_registry_for(report)
    try:
        structure = report_doc or bool(data.get("estructura_completa"))
        rerun = [i for i in check_report(current, require_sections=structure, conn=conn, path=report)
                 if i.severity == "error"]
    finally:
        if own and conn is not None:
            conn.close()
    if rerun:
        return False, (f"El informe tiene {len(rerun)} error(es) al volver a comprobarlo (p. ej.: {rerun[0].message}): "
                       "corrígelo y vuelve a sellar")
    info = status_info(current)
    total = (data.get("resumen") or {}).get("total", 0)
    refs = ("no había referencias que verificar" if not total else
            f"{(data.get('resumen') or {}).get('verificadas', 0)} de {total} referencia(s) verificadas en línea "
            "(existencia, correspondencia con la cita y retractación)")
    parts = [f"estado '{info.value or 'sin estado'}'", refs,
             "estructura de informe comprobada" if structure else "documento sin estructura de informe"]
    if structure and not info.strict:
        n_pending, empty = pending_summary(current)
        if n_pending or empty:
            parts.append(f"borrador por completar ({n_pending} apartado(s) con {PENDING_MARK}"
                         + (f", {len(empty)} sección(es) vacías" if empty else "") + ")")
    if info.strict:
        parts.append(f"revisión humana registrada de {human_reviewer(current)}")
    return True, (f"Sello válido ({data.get('fecha')}): " + "; ".join(parts) + f"; {data.get('avisos', 0)} aviso(s). "
                  "No certifica que las fuentes respalden las afirmaciones (Anexo A, revisado por una persona)")
