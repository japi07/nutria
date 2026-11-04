"""Comprobación de informes y sello de verificación (falla cerrado).

Comprobaciones estructurales (sin red): secciones obligatorias, referencias [Rn] definidas y usadas,
identificadores en la bibliografía, etiquetas de evidencia, citas autor-año sin referencia verificable
y, en informes revisados o finales, tabla de respaldo del Anexo A y revisión humana.

Comprobaciones en línea (con red, sin caché): cada DOI/PMID existe, corresponde a la cita (título,
año y primer autor), y su estado de retractación es conocido y negativo. Las referencias que solo
tienen URL valen únicamente si una persona las registró con `nutria verificar manual`.

El sello (`<informe>.verificacion.json`) guarda la huella SHA-256 del texto EXACTO que se comprobó y
va firmado con HMAC (ver `nutria.sello`). Solo es válido si la verificación en línea fue completa
(ninguna referencia sin comprobar), no hubo errores, la estructura se comprobó entera y el informe
no ha cambiado desde entonces.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
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
LABELS = ("[PUB]", "[DER]", "[HIP]", "[SUP]", "[DESC]")
STRICT_STATUSES = ("revisado", "final")
SUPPORT_OK = ("respalda", "parcial")
SUPPORT_VALUES = ("respalda", "parcial", "no_respalda", "no_verificable")
REF_RE = re.compile(r"\[(R\d+)\]")
ENTRY_RE = re.compile(r"^\s*(?:[-*]\s*|\d+\.\s*)?\[(R\d+)\]\s*(.+)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
STATUS_RE = re.compile(r"^estado\s*:\s*[\"']?([A-Za-zÁÉÍÓÚáéíóú_]+)[\"']?\s*$", re.IGNORECASE | re.MULTILINE)
HUMAN_RE = re.compile(r"^revisado_por_humano\s*:\s*(.*)$", re.IGNORECASE | re.MULTILINE)
UNIT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:mg|g|kg|ppm|%|mmol|µmol|UI|IU)(?:/(?:kg|d|día|dia|t|animal))?\b",
                     re.IGNORECASE)
_NAME = r"[A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚáéíóúñü'\-]+"
AUTHOR_YEAR_RES = (
    re.compile(rf"\(\s*{_NAME}(?:\s+(?:et\s+al\.?|y|and|&)\s*(?:{_NAME})?)?,?\s+(?:19|20)\d{{2}}[a-z]?\s*[;)]"),
    re.compile(rf"\b{_NAME}\s+(?:et\s+al\.?|y\s+{_NAME}|and\s+{_NAME})\s*\(\s*(?:19|20)\d{{2}}[a-z]?\s*\)"),
)


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


def report_status(text: str) -> str | None:
    match = STATUS_RE.search(text or "")
    return match.group(1).lower() if match else None


def human_reviewer(text: str) -> str | None:
    match = HUMAN_RE.search(text or "")
    if not match:
        return None
    value = match.group(1).strip().strip("\"'")
    return None if value.lower() in ("", "null", "none", "no", "pendiente") else value


def _section(lines: list[str], pattern: str) -> tuple[int | None, int | None]:
    start = level = None
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and re.search(pattern, m.group(2), re.IGNORECASE):
            start, level = i, len(m.group(1))
            break
    if start is None:
        return None, None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = HEADING_RE.match(lines[j])
        if m and len(m.group(1)) <= level:
            end = j
            break
    return start, end


def split_bibliography(text: str) -> tuple[list[str], dict[str, tuple[int, str]], int | None]:
    """Devuelve (líneas del cuerpo, {Rn: (línea, entrada)}, línea de inicio de la bibliografía)."""
    lines = text.splitlines()
    start, end = _section(lines, r"bibliograf|referencias")
    if start is None:
        return lines, {}, None
    entries: dict[str, tuple[int, str]] = {}
    for j in range(start + 1, end):
        m = ENTRY_RE.match(lines[j])
        if m:
            entries[m.group(1)] = (j + 1, m.group(2).strip())
    body = lines[:start] + [""] * (end - start) + lines[end:]
    return body, entries, start + 1


def support_table(text: str) -> dict[str, str]:
    """Lee la tabla de respaldo del Anexo A: {Rn: respalda|parcial|no_respalda|no_verificable}."""
    lines = _strip_comments(text).splitlines()
    start, end = _section(lines, r"anexo\s+a")
    if start is None:
        return {}
    header_idx = None
    out: dict[str, str] = {}
    for j in range(start + 1, end):
        line = lines[j].strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header_idx is None:
            lowered = [c.lower() for c in cells]
            if "respaldo" in lowered:
                header_idx = lowered.index("respaldo")
            continue
        if set(line.replace("|", "").strip()) <= set("-: "):
            continue
        refs = REF_RE.findall(cells[0]) or REF_RE.findall("[" + cells[0].strip("[]") + "]")
        if refs and header_idx < len(cells):
            value = cells[header_idx].lower().replace(" ", "_")
            for ref in refs:
                out[ref] = value
    return out


def check_report(text: str, require_sections: bool = True, strict: bool | None = None) -> list[Issue]:
    """`strict` (por defecto: estado revisado o final) exige respaldo comprobado y revisión humana."""
    issues: list[Issue] = []
    clean = _strip_comments(text)
    status = report_status(clean)
    if strict is None:
        strict = status in STRICT_STATUSES
    if require_sections:
        for number, name in SECTION_TITLES.items():
            if not re.search(rf"^##\s*{number}\.\s", clean, re.MULTILINE):
                issues.append(Issue("error", f"Falta la sección {number} ({name})"))
    body, entries, bib_line = split_bibliography(clean)
    if bib_line is None:
        issues.append(Issue("error", "No hay sección de bibliografía"))
    used: dict[str, int] = {}
    pub_refs: dict[str, int] = {}
    unlabeled_numeric = 0
    for i, raw_line in enumerate(body, start=1):
        if raw_line.lstrip().startswith(("#", "|---")):
            continue
        line = re.sub(r"`[^`]*`", "", raw_line)  # las etiquetas citadas como término (en código) no cuentan
        refs = REF_RE.findall(line)
        for ref in refs:
            used.setdefault(ref, i)
        if "[PUB]" in line:
            if not refs:
                issues.append(Issue("error", "Afirmación [PUB] sin referencia [Rn]", i))
            for ref in refs:
                pub_refs.setdefault(ref, i)
        if "[DER]" in line and not re.search(r"c[aá]lculo|nutria |script|anexo c|\[R\d+\]", raw_line, re.IGNORECASE):
            issues.append(Issue("aviso", "[DER] sin indicar el cálculo u orden usada", i))
        if not refs and any(rx.search(line) for rx in AUTHOR_YEAR_RES):
            issues.append(Issue("error", "Cita autor-año sin referencia [Rn] verificable", i))
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
                issues.append(Issue("aviso", f"{ref} solo tiene URL: necesita verificación manual firmada", line_no))
            else:
                issues.append(Issue("error", f"{ref} sin DOI, PMID ni URL verificable", line_no))
    if unlabeled_numeric > 10:
        issues.append(Issue("aviso", f"{unlabeled_numeric} cifras con unidades sin etiqueta en total"))
    support = support_table(clean)
    for ref, value in support.items():
        if value not in SUPPORT_VALUES:
            issues.append(Issue("error", f"Anexo A: respaldo '{value}' no válido para {ref} "
                                         f"(usa {', '.join(SUPPORT_VALUES)})"))
        elif value == "no_respalda":
            issues.append(Issue("error", f"{ref} NO respalda la afirmación que se le atribuye (Anexo A)"))
    for ref, line_no in pub_refs.items():
        value = support.get(ref)
        if value in SUPPORT_OK:
            continue
        if value == "no_verificable":
            issues.append(Issue("error" if strict else "aviso",
                                f"Respaldo de {ref} no verificable: no puede sostener una afirmación [PUB]", line_no))
        elif value is None:
            issues.append(Issue("error" if strict else "aviso",
                                f"Sin comprobación de respaldo de {ref} en la tabla del Anexo A", line_no))
    if strict:
        if not human_reviewer(clean):
            issues.append(Issue("error", f"Estado '{status}' sin revisión humana (revisado_por_humano vacío)"))
        if require_sections and not entries:
            issues.append(Issue("error", f"Un informe en estado '{status}' necesita referencias verificables"))
    return issues


def check_report_online(client, text: str, conn=None) -> tuple[list[Issue], list[dict], dict]:
    """Verifica en línea cada entrada. Devuelve (incidencias, detalle por referencia, resumen).

    Falla cerrado: lo que no se pueda comprobar (sin red, retractación desconocida, URL sin verificación
    manual firmada) cuenta como pendiente y deja la verificación incompleta.
    """
    issues: list[Issue] = []
    details: list[dict] = []
    summary = {"total": 0, "verificadas": 0, "pendientes": 0, "con_error": 0}
    _body, entries, _ = split_bibliography(_strip_comments(text))
    for ref, (line_no, entry) in entries.items():
        summary["total"] += 1
        ids = extract_identifiers(entry)
        if not ids["doi"] and not ids["pmid"]:
            urls = re.findall(r"https?://[^\s)>\]]+", entry)
            manual = None
            if conn is not None:
                from .. import db
                for url in urls:
                    manual = db.get_manual_verification(conn, url.rstrip(".,;"))
                    if manual:
                        break
            if manual:
                summary["verificadas"] += 1
                details.append({"ref": ref, "identifier": urls[0] if urls else None, "kind": "url",
                                "status": "verificada_manual", "firmado_por": manual["signed_by"],
                                "fecha": manual["signed_at"]})
            else:
                summary["pendientes"] += 1
                details.append({"ref": ref, "identifier": urls[0] if urls else None, "kind": "url",
                                "status": "pendiente_manual"})
                issues.append(Issue("aviso", f"{ref}: referencia con URL sin verificación manual firmada "
                                             "(`nutria verificar manual <URL> --firma-humana NOMBRE`)", line_no))
            continue
        results = verify_entry(client, entry)
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
        if ref_ok and results:
            summary["verificadas"] += 1
    summary["completa"] = summary["pendientes"] == 0 and summary["con_error"] == 0
    return issues, details, summary


def stamp_path(report: Path) -> Path:
    return report.with_name(report.name + ".verificacion.json")


def _bibliography_ids(text: str) -> list[str]:
    _body, entries, _ = split_bibliography(_strip_comments(text))
    ids = []
    for _ref, (_line, entry) in sorted(entries.items()):
        found = extract_identifiers(entry)
        ids.extend(f"doi:{d}" for d in found["doi"])
        ids.extend(f"pmid:{p}" for p in found["pmid"])
        if not found["doi"] and not found["pmid"]:
            ids.extend(f"url:{u.rstrip('.,;')}" for u in re.findall(r"https?://[^\s)>\]]+", entry))
    return sorted(ids)


def write_stamp(report: Path, checked_text: str, issues: list[Issue], online: bool, details: list[dict] | None,
                summary: dict | None, structure_checked: bool) -> dict:
    data = {
        "informe": report.name,
        "sha256": sha256_text(checked_text),
        "fecha": utc_now(),
        "en_linea": online,
        "verificacion_completa": bool(online and summary and summary.get("completa")),
        "estructura_completa": structure_checked,
        "estado_informe": report_status(checked_text),
        "revisado_por_humano": human_reviewer(checked_text),
        "referencias": _bibliography_ids(checked_text),
        "resumen": summary or {},
        "errores": sum(1 for i in issues if i.severity == "error"),
        "avisos": sum(1 for i in issues if i.severity == "aviso"),
        "incidencias": [asdict(i) for i in issues],
        "detalle": details or [],
        "nutria": __version__,
    }
    data["firma"] = sello.sign(data)
    stamp_path(report).write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return data


def stamp_status(report: Path) -> tuple[bool, str]:
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
    current = report.read_text("utf-8")
    if data.get("sha256") != sha256_text(current) or data.get("sha256") != sha256_file(report):
        return False, "El informe ha cambiado después del sello: vuelve a verificar"
    if not data.get("en_linea"):
        return False, "El sello no incluye verificación en línea de las referencias"
    if not data.get("verificacion_completa"):
        resumen = data.get("resumen") or {}
        return False, (f"Verificación incompleta: {resumen.get('pendientes', '?')} referencia(s) sin comprobar y "
                       f"{resumen.get('con_error', '?')} con error")
    if not data.get("estructura_completa") and report.parent.name == "informes":
        return False, "El sello no comprobó la estructura completa del informe (usa `nutria informe comprobar`)"
    if data.get("errores", 1) > 0:
        return False, f"El sello registra {data['errores']} error(es)"
    if data.get("referencias") != _bibliography_ids(current):
        return False, "Las referencias del informe no coinciden con las del sello"
    rerun = [i for i in check_report(current, require_sections=bool(data.get("estructura_completa")))
             if i.severity == "error"]
    if rerun:
        return False, f"El informe tiene {len(rerun)} error(es) estructurales: vuelve a verificar"
    return True, (f"Sello válido ({data.get('fecha')}; {data.get('resumen', {}).get('verificadas', 0)} referencia(s) "
                  f"verificadas; {data.get('avisos', 0)} aviso(s))")
