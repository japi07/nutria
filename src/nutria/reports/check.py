"""Comprobación de informes y sello de verificación.

Comprobaciones estructurales (sin red): secciones obligatorias, referencias [Rn] definidas y
usadas, identificadores en la bibliografía y etiquetas de evidencia.
Comprobaciones en línea (con red): cada DOI/PMID existe, corresponde a la cita y no está retractado.
El sello (`<informe>.verificacion.json`) guarda la huella SHA-256 del fichero: cualquier edición
posterior lo invalida y obliga a volver a verificar.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import __version__
from ..provenance import sha256_file, utc_now
from ..verify.citations import extract_identifiers, verify_entry

SECTION_TITLES = {
    1: "Título", 2: "Problema", 3: "Estado actual", 4: "Necesidad no resuelta", 5: "Hipótesis principal",
    6: "Hipótesis alternativas", 7: "Mecanismo", 8: "Candidatos", 9: "Evidencia", 10: "Favorables/desfavorables",
    11: "Estadística", 12: "Simulaciones", 13: "Limitaciones", 14: "Seguridad y regulación",
    15: "Formulación", 16: "Comparación comercial", 17: "Economía", 18: "Validación experimental",
    19: "Conclusión", 20: "Bibliografía",
}
LABELS = ("[PUB]", "[DER]", "[HIP]", "[SUP]", "[DESC]")
REF_RE = re.compile(r"\[(R\d+)\]")
ENTRY_RE = re.compile(r"^\s*(?:[-*]\s*|\d+\.\s*)?\[(R\d+)\]\s*(.+)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
UNIT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:mg|g|kg|ppm|%|mmol|µmol|UI|IU)(?:/(?:kg|d|día|dia|t|animal))?\b",
                     re.IGNORECASE)


@dataclass
class Issue:
    severity: str          # error | aviso
    message: str
    line: int | None = None

    def __str__(self) -> str:
        where = f"línea {self.line}: " if self.line else ""
        return f"[{self.severity.upper()}] {where}{self.message}"


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.DOTALL)


def split_bibliography(text: str) -> tuple[list[str], dict[str, tuple[int, str]], int | None]:
    """Devuelve (líneas del cuerpo, {Rn: (línea, entrada)}, línea de inicio de la bibliografía)."""
    lines = text.splitlines()
    start = None
    level = None
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and re.search(r"bibliograf|referencias", m.group(2), re.IGNORECASE):
            start, level = i, len(m.group(1))
            break
    if start is None:
        return lines, {}, None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = HEADING_RE.match(lines[j])
        if m and len(m.group(1)) <= level:
            end = j
            break
    entries: dict[str, tuple[int, str]] = {}
    for j in range(start + 1, end):
        m = ENTRY_RE.match(lines[j])
        if m:
            entries[m.group(1)] = (j + 1, m.group(2).strip())
    body = lines[:start] + [""] * (end - start) + lines[end:]
    return body, entries, start + 1


def check_report(text: str, require_sections: bool = True) -> list[Issue]:
    issues: list[Issue] = []
    clean = _strip_comments(text)
    if require_sections:
        for number, name in SECTION_TITLES.items():
            if not re.search(rf"^##\s*{number}\.\s", clean, re.MULTILINE):
                issues.append(Issue("error", f"Falta la sección {number} ({name})"))
    body, entries, bib_line = split_bibliography(clean)
    if bib_line is None:
        issues.append(Issue("error", "No hay sección de bibliografía"))
    used: dict[str, int] = {}
    unlabeled_numeric = 0
    for i, raw_line in enumerate(body, start=1):
        if raw_line.lstrip().startswith(("#", "|---", ">")):
            continue
        line = re.sub(r"`[^`]*`", "", raw_line)  # las etiquetas citadas como término (en código) no cuentan
        refs = REF_RE.findall(line)
        for ref in refs:
            used.setdefault(ref, i)
        if "[PUB]" in line and not refs:
            issues.append(Issue("error", "Afirmación [PUB] sin referencia [Rn]", i))
        if "[DER]" in line and not re.search(r"c[aá]lculo|nutria |script|anexo c|\[R\d+\]", raw_line, re.IGNORECASE):
            issues.append(Issue("aviso", "[DER] sin indicar el cálculo u orden usada", i))
        if UNIT_RE.search(line) and not any(label in line for label in LABELS) and not refs:
            unlabeled_numeric += 1
            if unlabeled_numeric <= 10:
                issues.append(Issue("aviso", "Cifra con unidades sin etiqueta de evidencia ni referencia", i))
    for ref, line_no in used.items():
        if ref not in entries:
            issues.append(Issue("error", f"La referencia {ref} se cita pero no está en la bibliografía", line_no))
    for ref, (line_no, entry) in entries.items():
        if ref not in used:
            issues.append(Issue("aviso", f"{ref} está en la bibliografía pero no se cita", line_no))
        ids = extract_identifiers(entry)
        if not ids["doi"] and not ids["pmid"]:
            if re.search(r"https?://", entry):
                issues.append(Issue("aviso", f"{ref} solo tiene URL: requiere verificación manual", line_no))
            else:
                issues.append(Issue("error", f"{ref} sin DOI, PMID ni URL verificable", line_no))
    if unlabeled_numeric > 10:
        issues.append(Issue("aviso", f"{unlabeled_numeric} cifras con unidades sin etiqueta en total"))
    return issues


def check_report_online(client, text: str) -> tuple[list[Issue], list[dict]]:
    issues: list[Issue] = []
    details: list[dict] = []
    _body, entries, _ = split_bibliography(_strip_comments(text))
    for ref, (line_no, entry) in entries.items():
        for result in verify_entry(client, entry):
            row = result.to_dict()
            row["ref"] = ref
            details.append(row)
            if result.status == "no_encontrada":
                issues.append(Issue("error", f"{ref}: {result.identifier} no existe ({result.message})", line_no))
            elif result.status in ("error_red", "offline"):
                issues.append(Issue("aviso", f"{ref}: no se pudo verificar {result.identifier} ({result.message})",
                                    line_no))
            elif result.retracted:
                issues.append(Issue("error", f"{ref}: {result.identifier} está RETRACTADO", line_no))
            elif result.mismatch:
                issues.append(Issue("error", f"{ref}: {result.message}", line_no))
    return issues, details


def stamp_path(report: Path) -> Path:
    return report.with_name(report.name + ".verificacion.json")


def write_stamp(report: Path, issues: list[Issue], online: bool, details: list[dict] | None = None) -> dict:
    data = {
        "informe": report.name, "sha256": sha256_file(report), "fecha": utc_now(), "en_linea": online,
        "errores": sum(1 for i in issues if i.severity == "error"),
        "avisos": sum(1 for i in issues if i.severity == "aviso"),
        "incidencias": [asdict(i) for i in issues], "referencias": details or [], "nutria": __version__,
    }
    stamp_path(report).write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return data


def stamp_status(report: Path) -> tuple[bool, str]:
    path = stamp_path(report)
    if not path.is_file():
        return False, "Sin sello de verificación: ejecuta `nutria informe comprobar --online --sellar`"
    data = json.loads(path.read_text("utf-8"))
    if data.get("sha256") != sha256_file(report):
        return False, "El informe ha cambiado después del sello: vuelve a verificar"
    if not data.get("en_linea"):
        return False, "El sello no incluye verificación en línea de las referencias"
    if data.get("errores", 1) > 0:
        return False, f"El sello registra {data['errores']} error(es)"
    return True, f"Sello válido ({data.get('fecha')}, {data.get('avisos', 0)} aviso(s))"
