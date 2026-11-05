"""Comprobación de informes, etiquetas, Anexo A, revisión humana, sello y hook de verificación.

Cubre los hallazgos D3-3, D3-4, D3-5, D3-6, D3-10, D3-13, D3-16, D4-3, D4-5, D5-11, D9-10, D10-1 y D10-10 de la
auditoría. Los informes se construyen con `nutria informe nuevo` a partir de los ejemplos de ejemplos/ y se rellenan
como lo haría un agente; la red se simula (las pruebas no salen a Internet).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nutria import db
from nutria.cli import main
from nutria.config import load_config
from nutria.reports import check as chk
from nutria.reports import render
from nutria.verify.citations import Verification, extract_identifiers

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "ejemplos"
HOOKS = ROOT / "scripts" / "hooks"
SIGN = "--firma-humana"
GOOD, GOOD2 = "10.1234/colina.2020", "10.1234/leche.2019"
FAKE = "10.9999/doi.inventado.2021.001"
CLAIM_A = "La colina protegida aumentó la producción de leche en vacas en transición 1,2 kg/d [PUB] [R1]."
CLAIM_B = "La misma dosis redujo el BHB plasmático en el posparto temprano [PUB] [R2]."
BIB = (f"[R1] Autor A, Autor B. 2020. Colina protegida en vacas en transición. J Dairy Sci 103:1-10. DOI: {GOOD}\n"
       f"[R2] Autor C. 2019. BHB y colina en el posparto. Animal 13:5-9. DOI: {GOOD2}")
ANNEX = ("| R1 | La colina protegida aumentó la producción de leche en vacas en transición 1,2 kg/d | respalda | Tabla 2 |\n"
         "| R2 | La misma dosis redujo el BHB plasmático en el posparto temprano | parcial | otra dosis |")
ANNEX_HEADER = "| Ref | Afirmación | Respaldo | Motivo |\n|---|---|---|---|\n"


# ============================================================================ utilidades
@pytest.fixture(autouse=True)
def stamp_key(tmp_path, monkeypatch):
    monkeypatch.setenv("NUTRIA_CLAVE_SELLO", str(tmp_path / "clave_de_prueba"))


@pytest.fixture(autouse=True)
def human_terminal(monkeypatch):
    """Las pruebas simulan a la persona en su terminal: sin variables de contexto de agente."""
    for name in ("NUTRIA_AGENT", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        monkeypatch.delenv(name, raising=False)


def fake_verify_entry(client, entry):
    """Red simulada: los DOI 10.9999/* no existen; el resto existe, corresponde a la cita y no está retractado."""
    out = []
    ids = extract_identifiers(entry)
    for doi in ids["doi"]:
        if doi.startswith("10.9999/"):
            out.append(Verification(doi, "doi", "no_encontrada", message="404 simulado"))
        else:
            out.append(Verification(doi, "doi", "verificada", title="t", year=2020, retracted=False,
                                    retraction_checked_via=["crossref"]))
    for pmid in ids["pmid"]:
        out.append(Verification(pmid, "pmid", "verificada", title="t", year=2020, retracted=False,
                                retraction_checked_via=["pubmed"]))
    return out


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(chk, "verify_entry", fake_verify_entry)
    monkeypatch.setattr(chk, "fetch_abstract", lambda client, entry: (None, "sin red en las pruebas"))


def _opportunity(project: Path, **changes) -> None:
    data = json.loads((EXAMPLES / "oportunidad_ejemplo.json").read_text("utf-8"))
    data.update(changes)
    path = project / "oportunidad.json"
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    assert main(["oportunidad", "nueva", "--datos", str(path)]) == 0


def _skeleton(project: Path, opp: str = "O-0001", out: Path | None = None) -> Path:
    args = ["informe", "nuevo", opp] + (["--salida", str(out)] if out else [])
    assert main(args) == 0
    return out or project / "research" / "informes" / f"{opp}.md"


def fill(text: str, evidence: str = CLAIM_A + "\n" + CLAIM_B, bib: str = BIB, annex: str = ANNEX) -> str:
    """Rellena los [POR COMPLETAR] del esqueleto como lo haría un agente."""
    out, section = [], ""
    for line in text.split("\n"):
        if line.startswith("## "):
            section = line
        if "[POR COMPLETAR]" in line and not line.startswith(">"):
            if section.startswith("## 9."):
                out.append(evidence)
            elif section.startswith("## 20."):
                out.append(bib)
            elif "biohidrogenación" in line:
                out.append("- Transformación o biohidrogenación ruminal del compuesto: [DESC] sin datos; se medirá con "
                           "incubaciones ruminales in vitro.")
            elif "Alegación prevista" in line:
                out.append("Alegación prevista (Reglamento (CE) 767/2009, art. 13): ninguna (sin alegación).")
            else:
                out.append("Contenido redactado de la sección [HIP].")
            continue
        out.append(line)
    return "\n".join(out).replace(ANNEX_HEADER, ANNEX_HEADER + annex + ("\n" if annex else ""), 1)


def _conn():
    return db.connect(load_config().db_path)


def _register(*dois, verified=True, retracted=False):
    conn = _conn()
    for doi in dois:
        db.upsert_source(conn, {"doi": doi, "title": "Fuente de prueba", "year": 2020, "verified": verified,
                                "verified_via": "prueba", "retracted": retracted})
    conn.close()


def _filled_report(project: Path, opp: str = "O-0001", **opp_changes) -> Path:
    _opportunity(project, **opp_changes)
    path = _skeleton(project, opp)
    path.write_text(fill(path.read_text("utf-8")), "utf-8")
    _register(GOOD, GOOD2)
    return path


def _final_report(project: Path, reviewer: str = "Ana Pérez") -> Path:
    path = _filled_report(project)
    assert main(["informe", "revisar", str(path), SIGN, reviewer, "--estado", "final"]) == 0
    return path


def _errors(text: str, **kw) -> list[str]:
    return [str(i) for i in chk.check_report(text, **kw) if i.severity == "error"]


def _out(capsys) -> str:
    return capsys.readouterr().out


# ============================================================================ flujo completo
def test_full_flow_human_review_then_valid_stamp(project, online, capsys):
    path = _final_report(project)
    assert "revisado_por_humano: 'Ana Pérez'" in path.read_text("utf-8")
    assert "estado: 'final'" in path.read_text("utf-8")
    capsys.readouterr()
    assert main(["informe", "comprobar", str(path), "--online", "--sellar"]) == 0
    out = _out(capsys)
    assert "Sello guardado: Sello válido" in out
    assert "No se comprueba" in out and "texto completo" in out
    assert main(["informe", "sello", str(path)]) == 0
    msg = _out(capsys)
    assert "2 de 2 referencia(s) verificadas en línea" in msg and "revisión humana registrada de Ana Pérez" in msg
    assert "No certifica que las fuentes respalden" in msg


def test_review_is_bound_to_exact_content(project, online, capsys):
    # (3) revisado_por_humano autodeclarado ya no basta; si el informe cambia, la revisión deja de valer.
    path = _final_report(project)
    assert main(["informe", "comprobar", str(path), "--online", "--sellar"]) == 0
    text = path.read_text("utf-8")
    path.write_text(text.replace("1,2 kg/d", "2,4 kg/d").replace("producción de leche en vacas en transición 1,2",
                                                                 "producción de leche en vacas en transición 2,4"),
                    "utf-8")
    errors = _errors(path.read_text("utf-8"), conn=_conn(), path=path)
    assert any("cambió después de la revisión humana de Ana Pérez" in e for e in errors)
    assert main(["informe", "sello", str(path)]) == 1


def test_agent_written_reviewer_is_rejected(project):
    path = _filled_report(project)
    text = path.read_text("utf-8").replace("estado: borrador", "estado: final")
    text = text.replace("revisado_por_humano: null", "revisado_por_humano: Dra. Prueba")
    path.write_text(text, "utf-8")
    errors = _errors(text, conn=_conn(), path=path)
    assert any("no corresponde a ninguna revisión registrada" in e for e in errors)
    # sin registro, error claro en estricto
    assert any("sin el registro del proyecto" in e for e in _errors(text, path=path))


def test_review_name_must_match_registered_reviewer(project):
    path = _final_report(project, reviewer="Ana Pérez")
    text = path.read_text("utf-8").replace("revisado_por_humano: 'Ana Pérez'", "revisado_por_humano: 'Luis Gil'")
    assert any("la revisión registrada para este contenido es de Ana Pérez" in e
               for e in _errors(text, conn=_conn(), path=path))


def _rejected(argv: list[str]) -> bool:
    """La orden se rechaza (con SystemExit o con un código distinto de 0: la comprobación central de la firma en
    contexto de agente puede hacerlo antes que la propia orden)."""
    try:
        rc = main(argv)
    except SystemExit as exc:
        return exc.code not in (0, None)
    return rc != 0


def test_review_command_rejects_agents_missing_signature_and_errors(project, monkeypatch):
    path = _filled_report(project)
    before = path.read_bytes()
    with pytest.raises(SystemExit, match=f"exige {SIGN}"):
        main(["informe", "revisar", str(path)])
    assert _rejected(["--agente", "director-cientifico", "informe", "revisar", str(path), SIGN, "Ana"])
    for name, value in (("NUTRIA_AGENT", "verificador-citas"), ("CLAUDECODE", "1"), ("CLAUDE_CODE_ENTRYPOINT", "cli")):
        with monkeypatch.context() as m:
            m.setenv(name, value)
            assert _rejected(["informe", "revisar", str(path), SIGN, "Ana"]), name
    assert path.read_bytes() == before and db.report_reviews(_conn()) == []
    # un informe final con errores (fuente sin registrar) no se da por revisado
    conn = _conn()
    conn.execute("DELETE FROM sources")
    conn.close()
    with pytest.raises(SystemExit, match="No se registra la revisión"):
        main(["informe", "revisar", str(path), SIGN, "Ana", "--estado", "final"])
    assert db.report_reviews(_conn()) == []


def test_reviews_are_insert_only(project):
    path = _final_report(project)
    conn = _conn()
    assert [r["reviewer"] for r in db.report_reviews(conn, report="research/informes/O-0001.md")] == ["Ana Pérez"]
    for sql in ("UPDATE report_reviews SET reviewer = 'Otra'", "DELETE FROM report_reviews"):
        with pytest.raises(Exception, match="no se (modifican|borran)"):
            conn.execute(sql)
    with pytest.raises(db.RegistryError):
        db.add_report_review(conn, "x.md", "no-es-una-huella", "Ana")
    assert path.is_file()
    conn.close()


def test_review_command_rejects_abbreviated_signature_option(project, capsys):
    # La orden no acepta abreviaturas (--firma, --firma-h): el hook de Bash solo reconoce la opción completa.
    path = _filled_report(project)
    for abbreviation in ("--firma", "--firma-h", "--fir"):
        with pytest.raises(SystemExit) as exc:
            main(["informe", "revisar", str(path), abbreviation, "Ana"])
        assert exc.value.code == 2
    assert db.report_reviews(_conn()) == []


def test_review_keeps_crlf_line_endings(project):
    path = _filled_report(project)
    path.write_bytes(path.read_text("utf-8").replace("\n", "\r\n").encode("utf-8"))
    assert main(["informe", "revisar", str(path), SIGN, "Ana Pérez"]) == 0
    raw = path.read_bytes()
    assert b"revisado_por_humano: 'Ana P\xc3\xa9rez'\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert not [e for e in _errors(raw.decode("utf-8"), conn=_conn(), path=path, strict=True)
                if "revisión" in e]


def test_review_outside_a_project_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NUTRIA_HOME", raising=False)
    doc = tmp_path / "suelto.md"
    doc.write_text("---\nestado: borrador\nrevisado_por_humano: null\n---\n# x\n", "utf-8")
    with pytest.raises(SystemExit, match="No hay registro del proyecto"):
        main(["informe", "revisar", str(doc), SIGN, "Ana"])


# ============================================================================ D3-5, D3-6, D10-10: estado
@pytest.mark.parametrize("front, expected", [
    ("estado: final  # pendiente de firma", "final"),
    ("estado: 'final' # sellado", "final"),
    ('estado: "Revisado"', "revisado"),
    ("Estado: FINAL", "final"),
    ("estado: borrador", "borrador"),
])
def test_status_read_from_yaml_front_matter(front, expected):
    info = chk.status_info(f"---\n{front}\n---\n# x\n")
    assert info.value == expected and info.problem is None
    assert info.strict == (expected in ("revisado", "final"))


def test_html_comment_before_front_matter_is_ignored():
    info = chk.status_info("<!--\nestado: borrador\n-->\n---\nestado: final\n---\n# x\n")
    assert info.value == "final" and info.strict


@pytest.mark.parametrize("front", [
    "estado: borrador\n  final",           # continuación: YAML uniría las líneas
    'estado: "fin\\x61l"',                 # escapes
    "estado: !!str final",                 # etiqueta YAML
    "estado: [final]",
    "estado: aprobado",                    # desconocido
    "estado: borrador\nestado: final",     # duplicado
    "estado:final",                        # ilegible
])
def test_unreadable_status_fails_closed(front):
    info = chk.status_info(f"---\n{front}\n---\n# x\n")
    assert info.problem and info.strict


def test_status_outside_front_matter_and_missing_status_in_reports(tmp_path):
    assert chk.status_info("# x\nestado: final\n").strict
    report = tmp_path / "research" / "informes" / "sub" / "E.md"
    errors = _errors("# Informe sin front matter\n## 20. Bibliografía\n", require_sections=False, path=report)
    assert any("no declara su estado" in e for e in errors)


def test_report_paths_are_resolved(tmp_path, monkeypatch):
    sub = tmp_path / "research" / "informes" / "sub"
    sub.mkdir(parents=True)
    assert chk.is_report_path(sub / "E.md")
    monkeypatch.chdir(tmp_path / "research" / "informes")
    assert chk.is_report_path(Path("O-0009.md")) and chk.is_report_path(Path("./sub/../O-0009.md"))
    assert not chk.is_report_path(tmp_path / "research" / "planes" / "P.md")


def test_report_paths_are_relative_to_the_project_root(tmp_path, monkeypatch):
    # Una carpeta «informes» por encima del proyecto (p. ej. ~/informes/nutria) no convierte los planes en informes.
    base = tmp_path / "informes" / "proyecto"
    base.mkdir(parents=True)
    monkeypatch.chdir(base)
    monkeypatch.delenv("NUTRIA_HOME", raising=False)
    assert main(["init"]) == 0
    plan = base / "research" / "planes" / "P.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    assert not chk.is_report_path(plan)
    assert chk.is_report_path(base / "research" / "informes" / "sub" / "E.md")
    assert chk.is_report_path(base / "Research" / "Informes" / "E.md")
    assert not chk.is_report_path(base / "informes" / "E.md")              # dentro del proyecto, fuera de research
    link = base / "research" / "planes" / "enlace.md"                       # un enlace a un informe sigue siéndolo
    target = base / "research" / "informes" / "O-0001.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", "utf-8")
    link.symlink_to(target)
    assert chk.is_report_path(link)


# ============================================================================ D3-4: etiquetas y cifras
def _draft(body: str, bib: str = "[R1] Autor A. 2020. Título del trabajo. DOI: 10.1234/abc") -> str:
    return (f"---\nestado: borrador\n---\n# Nota\n\n{body}\n\n## Referencias\n\n{bib}\n")


@pytest.mark.parametrize("body", [
    "### Reducción de la cetosis a la mitad [PUB]",            # encabezado
    "La colina reduce la cetosis [pub].",                      # minúsculas
    "La colina reduce la cetosis [P​UB].",                # carácter invisible
    "La colina reduce la cetosis &#91;PUB&#93;.",              # entidades HTML
    "La colina reduce la cetosis \\[PUB\\].",                  # escapes de Markdown
    "> La colina reduce la cetosis [PUB].",                    # cita
    "`La colina reduce la cetosis [PUB]`",                     # código con contenido
    "La colina reduce la cetosis (PUB).",
])
def test_pub_without_reference_is_detected_everywhere(body):
    assert any("[PUB] sin referencia" in e for e in _errors(_draft(body), require_sections=False))


def test_label_only_code_span_is_a_term_mention():
    assert not _errors(_draft("Las etiquetas válidas son `[PUB]`, `[DER]` y `[Rn]`. Dato [HIP] citado [R1]."),
                       require_sections=False)


def test_lowercase_label_warns_about_format():
    issues = chk.check_report(_draft("La colina reduce la cetosis [pub] [R1]."), require_sections=False)
    assert any("formato no canónico" in str(i) and i.severity == "aviso" for i in issues)


def test_each_pub_segment_needs_its_own_reference():
    errors = _errors(_draft("A reduce la cetosis [PUB] y B la acidosis [PUB] y C la leche [PUB] [R1]."),
                     require_sections=False)
    assert sum("[PUB] sin referencia" in e for e in errors) == 2
    assert not any("[PUB] sin referencia" in e for e in
                   _errors(_draft("Según [R1] [PUB] hubo cambios en la leche."), require_sections=False))


def test_a_reference_counts_only_within_its_sentence():
    for body in ("La colina aumentó la leche en vacas [PUB]. Otros estudios [R1] sugieren lo mismo [HIP].",
                 "La colina aumentó la leche [R1]. La cetosis bajó en vacas [PUB]."):
        assert any("[PUB] sin referencia" in e for e in _errors(_draft(body), require_sections=False)), body
    for body in ("La colina aumentó la leche en vacas [PUB] (Fig. 2; p. 23) [R1].",
                 "Según D. Sauvant [R1] [PUB] la respuesta fue lineal."):
        assert not any("[PUB] sin referencia" in e for e in _errors(_draft(body), require_sections=False)), body


@pytest.mark.parametrize("bad", ["[PUB, R1]", "[R1, R2]", "[r1]"])
def test_malformed_labels_and_references_are_errors(bad):
    assert _errors(_draft(f"La colina reduce la cetosis [PUB] [R1] {bad}."), require_sections=False)


@pytest.mark.parametrize("text", ["un 45 % de los casos", "un 30%.", "3,1 L/d", "12 kg/d", "20 g/d", "5 mg/kg MS",
                                  "200 g/t", "1,6 Mcal", "12 MJ", "2,1 mmol/L", "50 ppm", "0,30 €/d", "38,5 °C",
                                  "5 puntos porcentuales", "4 UFC"])
def test_units_regex_matches_nutrition_units(text):
    assert chk.UNIT_RE.search(text)


@pytest.mark.parametrize("text", ["3 grupos", "[R1]", "DOI 10.1234/x", "21 días", "PMID: 12345678", "H-0001",
                                  "## 14. Seguridad"])
def test_units_regex_ignores_non_quantities(text):
    assert not chk.UNIT_RE.search(text)


def test_unlabeled_figures_error_in_strict_and_warning_in_draft():
    body = "La producción subió un 45 % y 3,1 L/d con la colina."
    draft = chk.check_report(_draft(body), require_sections=False)
    assert any("Cifra con unidades" in str(i) and i.severity == "aviso" for i in draft)
    strict = chk.check_report(_draft(body), require_sections=False, strict=True)
    assert any("Cifra con unidades" in str(i) and i.severity == "error" for i in strict)


def _strict_errors(body: str) -> list[str]:
    return _errors(_draft(body), require_sections=False, strict=True)


@pytest.mark.parametrize("body", [
    "La dosis fue de 15 g/d. El efecto sobre la leche fue positivo [HIP].",     # otra frase de la línea
    "La leche subió 3,1 L/d en el grupo B. Contexto del ensayo [SUP].",
    "La leche subió 3,1 L/d!\nContexto del ensayo [SUP].",
])
def test_a_label_only_covers_its_own_sentence(body):
    assert any("Cifra con unidades" in e for e in _strict_errors(body))


@pytest.mark.parametrize("body", [
    "La colina aumentó la producción de leche en 1,2 kg/d en vacas\nHolstein multíparas [HIP].",   # frase partida
    "La leche subió 1,2 kg/d; la grasa, 0,1 % [HIP].",
    "Según D. Sauvant la respuesta fue de 1,2 kg/d [HIP].",
    "La respuesta fue de 1,2 kg/d (p. ej. en multíparas) [HIP].",
])
def test_sentence_split_has_no_false_positives(body):
    assert not any("Cifra con unidades" in e for e in _strict_errors(body))


def test_references_need_a_label_too():
    # Una afirmación atribuida a [Rn] sin etiqueta eludiría el Anexo A y el registro de fuentes.
    for body in ("La colina aumentó la leche en vacas lecheras [R1].",
                 "| Estudio | Dosis |\n|---|---|\n| [R1] | 50 g/d |",
                 "La colina aumentó la leche [R1]. Contexto [HIP]."):
        assert any("Referencia [Rn] sin etiqueta" in e for e in _strict_errors(body)), body
    draft = chk.check_report(_draft("La colina aumentó la leche [R1]."), require_sections=False)
    assert any("Referencia [Rn] sin etiqueta" in str(i) and i.severity == "aviso" for i in draft)
    for body in ("Según [R1] [PUB] hubo cambios en la leche.", "Hipótesis basada en [R1] [HIP].",
                 "Calculado con datos de [R1] [DER] (nutria destino ruminal)."):
        assert not any("Referencia [Rn] sin etiqueta" in e for e in _strict_errors(body)), body


def test_only_real_annexes_and_the_title_line_are_exempt(project):
    # Un «#### Anexo A» o un «### Anexo C» en mitad del informe no exime sus tablas ni su texto, y en la sección 1
    # solo queda exenta la línea que repite el título.
    path = _filled_report(project)
    text = path.read_text("utf-8").replace("estado: borrador", "estado: final")
    fake = {"#### Anexo A de la sección\n\n| Dato | Valor |\n|---|---|\n| leche | 3,1 L/d |\n": "## 10.",
            "### Anexo C. Datos\n\nLa leche subió 3,1 L/d y la cetosis bajó un 45 %.\n": "## 10.",
            "La leche subió 3,1 L/d.\n": "\n## 2."}
    for inject, before in fake.items():
        errors = _errors(text.replace(before, "\n" + inject + "\n" + before, 1), conn=_conn(), path=path)
        assert any("Cifra con unidades" in e for e in errors), inject
    titled = _filled_report(project, "O-0002", titulo="Colina protegida a 15 g/d en el periodo de transición")
    text = titled.read_text("utf-8")
    assert "\n\nColina protegida a 15 g/d en el periodo de transición\n" in text
    assert not any("Cifra con unidades" in str(i) for i in chk.check_report(text, conn=_conn(), path=titled))


# ============================================================================ D3-13: bibliografía
def test_duplicate_reference_is_error_and_both_entries_are_verified(online):
    text = _draft("La colina reduce la cetosis [HIP] [R1].",
                  bib=f"[R1] Inventado A. 2020. Ensayo falso. DOI: {FAKE}\n"
                      "[R1] Sauvant D. 2008. Meta-analyses in animal nutrition. DOI: 10.1017/s1751731108002280")
    assert any("duplicada en la bibliografía" in e for e in _errors(text, require_sections=False))
    issues, details, summary = chk.check_report_online(None, text)
    assert {d["identifier"] for d in details} == {FAKE, "10.1017/s1751731108002280"}
    assert not summary["completa"] and any("no existe" in str(i) for i in issues)


def test_bibliography_is_section_20_not_first_heading_with_the_word():
    text = ("---\nestado: borrador\n---\n# x\n## 3. Estado actual (referencias clave)\n"
            "[R1] Real. 2008. DOI: 10.1017/s1751731108002280\nDato [HIP] [R1].\n"
            f"## 20. Bibliografía\n[R1] Inventado. 2021. DOI: {FAKE}\n")
    assert [e.text for e in chk.bibliography_entries(text)] == [f"Inventado. 2021. DOI: {FAKE}"]
    issues = chk.check_report(text, require_sections=False)
    assert any("fuera de la bibliografía" in str(i) for i in issues)
    assert any("fuera de la bibliografía" in str(i) and i.severity == "error"
               for i in chk.check_report(text, require_sections=False, strict=True))


def test_multiline_entries():
    text = _draft("Dato [HIP] [R1].", bib="[R1] Autor A, Autor B, Autor C. 2020. Un título largo que\n"
                                         "  continúa en la línea siguiente. Revista 1:2-3.\n  DOI: 10.1234/abc")
    entries = chk.bibliography_entries(text)
    assert len(entries) == 1 and "10.1234/abc" in entries[0].text
    assert not _errors(text, require_sections=False)


# ============================================================================ D3-3: Anexo A por afirmación
def _strict_doc(body: str, annex: str, bib: str = BIB) -> str:
    return (f"---\nestado: final\n---\n# Nota\n\n{body}\n\n## Referencias\n\n{bib}\n\n## Anexo A. Respaldo\n\n"
            f"{ANNEX_HEADER}{annex}\n")


def test_no_respalda_row_is_not_masked_by_a_later_respalda_row():
    body = "La colina reduce la cetosis clínica en vacas [PUB] [R1].\nLa colina aumenta la leche en vacas [PUB] [R1]."
    annex = ("| R1 | La colina reduce la cetosis clínica en vacas | no_respalda | no lo dice |\n"
             "| R1 | La colina aumenta la leche en vacas | respalda | Tabla 2 |")
    errors = _errors(_strict_doc(body, annex), require_sections=False)
    assert any("R1 NO respalda la afirmación de la línea 6" in e for e in errors)
    assert not any("línea 7" in e and "respaldo" in e.lower() for e in errors)


def test_one_row_per_claim_not_per_reference():
    body = "La colina reduce la cetosis clínica en vacas [PUB] [R1].\nLa colina aumenta la leche en vacas [PUB] [R1]."
    errors = _errors(_strict_doc(body, "| R1 | La colina aumenta la leche en vacas | respalda | Tabla 2 |"),
                     require_sections=False)
    assert any("Sin comprobación de respaldo de R1 para la afirmación de la línea 6" in e for e in errors)


def test_contradictory_rows_and_orphan_refutations():
    body = "La colina aumenta la leche en vacas [PUB] [R1]."
    rows = ("| R1 | La colina aumenta la leche en vacas | respalda | a |\n"
            "| R1 | La colina aumenta la leche en vacas | parcial | b |")
    assert any("Filas contradictorias" in e for e in _errors(_strict_doc(body, rows), require_sections=False))
    orphan = ("| R1 | La colina aumenta la leche en vacas | respalda | a |\n"
              "| R1 | Una afirmación que ya no está en el texto | no_respalda | c |")
    assert any("no corresponde a ninguna afirmación" in e for e in
               _errors(_strict_doc(body, orphan), require_sections=False))


def test_row_matching_is_normalized_supports_ellipsis_and_wrapped_lines():
    body = ("La suplementación con colina protegida aumentó la producción de leche corregida por energía\n"
            "en vacas Holstein de primer parto (1,2 kg/d) [PUB] [R1].")
    rows = ("| R1 | la suplementacion con colina protegida ... en vacas Holstein de primer parto (1,2 kg/d) | "
            "respalda | Tabla 2 |")
    assert not _errors(_strict_doc(body, rows), require_sections=False, strict=False)
    short = "| R1 | colina | respalda | x |"     # fragmento demasiado corto: no cuenta
    assert any("Sin comprobación de respaldo" in str(i) for i in
               chk.check_report(_strict_doc(body, short), require_sections=False, strict=False))


def _annex_errors(body: str, annex: str) -> list[str]:
    return [e for e in _errors(_strict_doc(body, annex), require_sections=False)
            if "revisión humana" not in e and "no se cita" not in e]


def test_a_generic_row_only_counts_for_the_claim_it_reproduces_best():
    body = ("La suplementación con colina protegida aumentó la producción de leche [PUB] [R1].\n"
            "La suplementación con colina protegida redujo la cetosis [PUB] [R1].")
    errors = _annex_errors(body, "| R1 | La suplementación con colina protegida | respalda | x |")
    assert any("Sin comprobación de respaldo de R1 para la afirmación de la línea 6" in e for e in errors)
    assert not any("línea 7" in e for e in errors)


def test_a_row_that_fits_two_claims_equally_counts_for_none_unless_negative():
    body = ("La colina protegida en vacas aumentó la leche [PUB] [R1].\n"
            "La colina protegida en vacas redujo la cetosis [PUB] [R1].")
    errors = _annex_errors(body, "| R1 | La colina protegida en vacas | respalda | x |")
    assert any("reproduce por igual varias afirmaciones distintas" in e for e in errors)
    assert sum("Sin comprobación de respaldo de R1" in e for e in errors) == 2
    rows = ("| R1 | La colina protegida en vacas | no_respalda | x |\n"
            "| R1 | La colina protegida en vacas aumentó la leche | respalda | y |\n"
            "| R1 | La colina protegida en vacas redujo la cetosis | respalda | z |")
    errors = _annex_errors(body, rows)
    assert sum("R1 NO respalda" in e for e in errors) == 2              # la negativa cuenta para las dos
    proper = rows.split("\n", 1)[1]
    assert _annex_errors(body, proper) == []


def test_repeated_claim_and_second_claim_of_a_sentence():
    body = ("La colina aumentó la leche en vacas [PUB] [R1].\n\n"
            "Resumen: la colina aumentó la leche en vacas [PUB] [R1].")
    assert _annex_errors(body, "| R1 | La colina aumentó la leche en vacas | respalda | y |") == []
    body = "La colina aumentó la leche [PUB] [R1] y redujo la cetosis [PUB] [R1]."
    rows = "| R1 | La colina aumentó la leche | respalda | y |\n| R1 | La colina ... redujo la cetosis | respalda | z |"
    assert _annex_errors(body, rows) == []
    errors = _annex_errors(body, rows.replace("La colina ... redujo", "La colina redujo"))
    assert any("«redujo la cetosis»" in e for e in errors)        # el mensaje dice qué texto reproducir


def test_rows_separated_from_the_table_or_before_its_header_are_not_ignored():
    body = "La colina aumentó la leche en vacas [PUB] [R1]."
    annex = ("| R1 | La colina aumentó la leche en vacas | respalda | y |\n\n"
             "| R1 | La colina aumentó la leche en vacas | no_respalda | z |")
    assert any("R1 NO respalda" in e for e in _annex_errors(body, annex))
    doc = _strict_doc(body, "| R1 | La colina aumentó la leche en vacas | respalda | y |")
    doc = doc.replace(ANNEX_HEADER, "| R1 | La colina aumentó la leche en vacas | no_respalda | z |\n\n" + ANNEX_HEADER)
    errors = _errors(doc, require_sections=False)
    assert any("antes de la cabecera" in e and "no respalda" in e for e in errors)
    draft = doc.replace("estado: final", "estado: borrador")
    assert any("antes de la cabecera" in e for e in _errors(draft, require_sections=False))   # negativa: siempre


def test_annex_quotes_are_checked_against_the_abstract(monkeypatch):
    body = "La colina aumenta la leche en vacas [PUB] [R1]."
    rows = "| R1 | La colina aumenta la leche en vacas | respalda | El resumen dice «increased milk yield by 1.2 kg/d» |"
    text = _strict_doc(body, rows)
    monkeypatch.setattr(chk, "fetch_abstract",
                        lambda client, entry: ("Choline increased milk yield by 1.2 kg/d in dairy cows.", "PubMed"))
    issues, details = chk.check_support_quotes(None, text)
    assert not issues and details[0]["estado"] == "en_resumen"
    monkeypatch.setattr(chk, "fetch_abstract", lambda client, entry: ("Choline had no effect.", "PubMed"))
    issues, _ = chk.check_support_quotes(None, text)
    assert issues and "no aparece en el resumen" in issues[0].message and "texto completo" in issues[0].message
    monkeypatch.setattr(chk, "fetch_abstract", lambda client, entry: (None, "sin red"))
    issues, _ = chk.check_support_quotes(None, text)
    assert issues and "no se pudo comprobar la cita" in issues[0].message and issues[0].severity == "aviso"


def test_fetch_abstract_uses_pubmed_by_pmid():
    xml = ("<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345678</PMID><Article><Abstract>"
           "<AbstractText>Choline increased milk yield.</AbstractText></Abstract></Article></MedlineCitation>"
           "</PubmedArticle></PubmedArticleSet>")
    cfg = SimpleNamespace(tool="nutria", email="", offline=False, secret=lambda name: None)
    client = SimpleNamespace(cfg=cfg, get_text=lambda url, **kw: xml, get_json=lambda url, **kw: {})
    abstract, origin = chk.fetch_abstract(client, "[R1] A. 2020. T. PMID: 12345678")
    assert abstract == "Choline increased milk yield." and origin == "PubMed"


# ============================================================================ D3-10: registro de fuentes
def test_pub_references_must_be_registered_verified_sources(project):
    path = _filled_report(project)
    conn = _conn()
    conn.execute("DELETE FROM sources")
    draft = chk.check_report(path.read_text("utf-8"), conn=conn, path=path)
    assert any("no está registrada como fuente" in str(i) and i.severity == "aviso" for i in draft)
    final = path.read_text("utf-8").replace("estado: borrador", "estado: final")
    assert any("no está registrada como fuente" in e for e in _errors(final, conn=conn, path=path))
    conn.close()
    _register(GOOD, verified=False)
    _register(GOOD2, retracted=True)
    conn = _conn()
    messages = [str(i) for i in chk.check_report(path.read_text("utf-8"), conn=conn, path=path)]
    assert any("sin verificar" in m for m in messages)
    assert any("RETRACTADA" in m and m.startswith("[ERROR]") for m in messages)


def test_registered_source_is_found_by_any_identifier_and_must_agree(project):
    path = _filled_report(project)                       # GOOD registrado por DOI, sin PMID
    text = path.read_text("utf-8").replace(f"DOI: {GOOD}", f"DOI: {GOOD} | PMID: 12345678")
    conn = _conn()
    assert not any("registr" in str(i) for i in chk.check_report(text, conn=conn, path=path))
    conn.execute("UPDATE sources SET pmid = '99999999' WHERE doi = ?", (GOOD,))
    messages = [str(i) for i in chk.check_report(text, conn=conn, path=path)]
    assert any("el registro guarda PMID 99999999" in m for m in messages)
    db.upsert_source(conn, {"pmid": "12345678", "title": "Otro trabajo", "verified": True, "verified_via": "prueba",
                            "retracted": False})
    conn.execute("UPDATE sources SET pmid = NULL WHERE doi = ?", (GOOD,))
    messages = [str(i) for i in chk.check_report(text, conn=conn, path=path)]
    assert any("corresponden a fuentes distintas del registro" in m for m in messages)
    conn.close()


# ============================================================================ D10-1: esqueleto y mensajes
def test_skeleton_stamp_does_not_look_like_a_guarantee(project, online, capsys):
    # D10-1: el esqueleto de `informe nuevo` no obtiene un «Sello válido», tampoco sin red
    _opportunity(project)
    path = _skeleton(project)
    for prefix in ([], ["--offline"]):
        capsys.readouterr()
        assert main(prefix + ["informe", "comprobar", str(path), "--online", "--sellar"]) == 1
        out = _out(capsys)
        assert "no había referencias que verificar" in out and "Sello válido" not in out
        assert "Sello guardado, pero NO es válido: Documento por completar" in out
        assert "verificadas en línea" not in out
        assert main(["informe", "sello", str(path)]) == 1
        assert "borrador incompleto" in _out(capsys)
    # un documento completo sin referencias sí se sella, y el sello dice que no había nada que verificar
    plan = project / "research" / "planes" / "P.md"
    plan.write_text("---\nestado: borrador\n---\n# Plan\n\nPaso 1 [HIP].\n\n## Referencias\n\nNinguna.\n", "utf-8")
    capsys.readouterr()
    assert main(["verificar", str(plan), "--sellar"]) == 0
    assert "Sello válido" in _out(capsys)
    ok, msg = chk.stamp_status(plan)
    assert ok and "no había referencias que verificar" in msg and "documento sin estructura de informe" in msg
    final = path.read_text("utf-8").replace("estado: borrador", "estado: final")
    errors = _errors(final, conn=_conn(), path=path)
    assert any("[POR COMPLETAR]" in e for e in errors)
    assert any("necesita referencias verificables" in e for e in errors)


def test_without_network_there_is_no_valid_stamp(project, capsys):
    path = _final_report(project)
    capsys.readouterr()
    assert main(["informe", "comprobar", str(path), "--online", "--sellar"]) == 1   # NUTRIA_OFFLINE=1
    out = _out(capsys)
    assert "verificación INCOMPLETA" in out and "NO es válido" in out
    assert main(["informe", "sello", str(path)]) == 1


# ============================================================================ D3-6, D10-10: sello
def test_stamp_only_from_verify_report_and_cross_checked_with_details(project, online):
    path = _final_report(project)
    with pytest.raises(chk.StampError):
        chk.write_stamp({"sha256": "x"})
    result = chk.verify_report(path, client=object(), conn=_conn())
    assert not result.errors
    lazy = chk.ReportCheck(path, result.text, result.sha256, [], [], {"total": 2, "verificadas": 2, "pendientes": 0,
                                                                      "con_error": 0, "completa": True}, True, True)
    chk.write_stamp(lazy)                          # resultado fabricado sin detalle de verificación
    ok, msg = chk.stamp_status(path)
    assert not ok and "no contiene una verificación correcta" in msg
    chk.write_stamp(result)
    assert chk.stamp_status(path)[0]
    forged = json.loads(chk.stamp_path(path).read_text("utf-8"))
    forged["errores"] = 0
    forged["firma"] = "0" * 64
    chk.stamp_path(path).write_text(json.dumps(forged), "utf-8")
    assert not chk.stamp_status(path)[0]


def test_subdirectory_report_and_relative_paths_need_structure(project, online, monkeypatch, capsys):
    # D3-6 (escéptico) y D10-10: research/informes/sub/E.md y `informe sello O-0009.md` desde research/informes
    partial = ("---\nestado: borrador\n---\n# E\n\nDato [HIP] [R1].\n\n## 20. Bibliografía\n\n"
               "[R1] Sauvant D. 2008. Meta-analyses. DOI: 10.1017/s1751731108002280\n")
    sub = project / "research" / "informes" / "sub"
    sub.mkdir()
    report = sub / "E.md"
    report.write_text(partial, "utf-8")
    assert main(["informe", "comprobar", str(report), "--sin-secciones", "--online", "--sellar"]) == 1
    stamp = json.loads(chk.stamp_path(report).read_text("utf-8"))
    assert stamp["errores"] == 0 and stamp["estructura_completa"] is False      # sello sin estructura
    ok, msg = chk.stamp_status(report)
    assert not ok and "estructura completa" in msg
    other = project / "research" / "informes" / "O-0009.md"
    other.write_text(partial, "utf-8")
    assert main(["informe", "comprobar", str(other), "--sin-secciones", "--online", "--sellar"]) == 1
    monkeypatch.chdir(project / "research" / "informes")
    for relative in ("O-0009.md", "./O-0009.md", "sub/../O-0009.md"):
        capsys.readouterr()
        assert main(["informe", "sello", relative]) == 1
        assert "estructura completa" in _out(capsys)
    monkeypatch.chdir(project)
    assert main(["verificar", str(other), "--sellar"]) == 1          # en informes, verificar exige la estructura
    assert json.loads(chk.stamp_path(other).read_text("utf-8"))["estructura_completa"] is True
    plan = project / "research" / "planes" / "P.md"
    plan.write_text(partial, "utf-8")
    capsys.readouterr()
    assert main(["verificar", str(plan), "--sellar"]) == 0
    out = _out(capsys)
    assert "no vale para un informe" in out and "documento sin estructura de informe" in out


def test_url_only_reference_needs_signed_manual_verification(project, online):
    url = "https://eur-lex.europa.eu/eli/reg/2009/767/oj"
    plan = project / "research" / "planes" / "P.md"
    plan.write_text(f"---\nestado: borrador\n---\n# Plan\n\nNorma aplicable [SUP] [R1].\n\n## Referencias\n\n"
                    f"[R1] Reglamento (CE) 767/2009. URL: {url}\n", "utf-8")
    issues, details, summary = chk.check_report_online(None, plan.read_text("utf-8"), _conn())
    assert not summary["completa"] and details[0]["status"] == "pendiente_manual"
    assert main(["verificar", "manual", url, SIGN, "Ana Pérez"]) == 0           # la persona, en su terminal
    issues, details, summary = chk.check_report_online(None, plan.read_text("utf-8"), _conn())
    assert summary["completa"] and details[0]["status"] == "verificada_manual"
    assert main(["verificar", str(plan), "--sellar"]) == 0
    assert chk.stamp_status(plan)[0]


# ============================================================================ D4-3, D4-5, D5-11: esqueleto
def test_skeleton_records_biological_context_and_digestive_fate(project):
    _opportunity(project, destino_digestivo={"fraccion_escape_ruminal": 0.35, "digestibilidad_intestinal": 0.8,
                                             "fuente_o_calculo": "nutria destino ruminal (supuesto de trabajo)"})
    text = _skeleton(project).read_text("utf-8")
    assert "especie: 'vacuno lechero'" in text and "etapa: 'periodo de transición" in text
    assert "Especie diana: vacuno lechero; etapa productiva: periodo de transición" in text
    assert "[SUP] (contexto biológico de la oportunidad en el registro)." in text
    section7 = text.split("\n## 7.")[1].split("\n## 8.")[0]
    assert "<!--" not in section7.split("### 7.1")[1].split("-->")[1]          # contenido, no solo un comentario
    assert "Fracción que escapa a la degradación ruminal: 0,35 [SUP]" in section7
    assert "Fracción absorbible (escape ruminal × digestibilidad intestinal): 0,28 [DER]" in section7
    assert "Dosis absorbible: [DESC]" in section7


def test_skeleton_includes_breed_and_physiological_state_when_present(project):
    data = json.loads((EXAMPLES / "oportunidad_ejemplo.json").read_text("utf-8"))
    data.update(raza="Holstein", estado_fisiologico="vacas multíparas en preparto")
    conn = _conn()
    opp = db.add_opportunity(conn, data)
    text = render.new_report(conn, opp)
    assert "raza: 'Holstein'" in text and "; raza: Holstein; estado fisiológico: vacas multíparas en preparto [SUP]" in text


def test_digestive_fate_is_required_in_ruminant_reports(project):
    path = _filled_report(project)
    text = path.read_text("utf-8")
    assert not any("destino digestivo" in e for e in _errors(text, conn=_conn(), path=path))
    section7 = text.split("### 7.1")[1].split("## 8.")[0]
    gutted = text.replace(section7, ". Destino digestivo en la especie diana\n\nSin datos.\n\n")
    assert any("no trata el destino digestivo" in str(i) for i in chk.check_report(gutted, path=path))
    strict = gutted.replace("estado: borrador", "estado: final")
    assert any("no trata el destino digestivo" in e for e in _errors(strict, path=path))
    pig = gutted.replace("especie: 'vacuno lechero'", "especie: 'porcino de cebo'")
    assert not any("destino digestivo" in str(i) for i in chk.check_report(pig, path=path))


def test_front_matter_species_cannot_skip_the_digestive_fate(project):
    # Cambiar la especie del front matter no evita la exigencia del destino digestivo: manda también la del registro.
    path = _filled_report(project)
    text = path.read_text("utf-8")
    section7 = text.split("### 7.1")[1].split("## 8.")[0]
    pig = text.replace(section7, ". Destino digestivo en la especie diana\n\nSin datos.\n\n").replace(
        "especie: 'vacuno lechero'", "especie: 'porcino de cebo'").replace("estado: borrador", "estado: final")
    errors = _errors(pig, conn=_conn(), path=path)
    assert any("no trata el destino digestivo" in e for e in errors)
    assert any("La especie del front matter (porcino de cebo) no es la de la oportunidad O-0001" in e for e in errors)
    stage = text.replace("etapa: 'periodo de transición", "etapa: 'lactación media").replace("estado: borrador",
                                                                                            "estado: final")
    assert any("La etapa del front matter" in e for e in _errors(stage, conn=_conn(), path=path))


def test_pending_marker_in_code_is_still_pending():
    body = "`[POR COMPLETAR]` mecanismo en la especie diana [HIP]."
    assert any("[POR COMPLETAR]" in e for e in _errors(_draft(body), require_sections=False, strict=True))
    mention = "Lo que falta se marca con `[POR COMPLETAR]` en el borrador [HIP]."
    assert not any("[POR COMPLETAR]" in e for e in _errors(_draft(mention), require_sections=False, strict=True))


@pytest.mark.parametrize("citation", ["(Smith y col., 2019)", "Smith y col. (2019)", "(Smith et al. 2019; Jones, 2017)",
                                      "Smith y colaboradores (2019)", "(García y López, 2019)"])
def test_author_year_citations_in_spanish_forms(citation):
    assert any("autor-año" in e for e in _errors(_draft(f"La colina reduce la cetosis {citation} [HIP]."),
                                                 require_sections=False))


@pytest.mark.parametrize("text", ["3 kilos de pienso", "2,5 litros/d", "15 gramos/d", "0,30 euros por vaca",
                                  "5 toneladas"])
def test_spelled_out_units_count_as_figures(text):
    assert chk.UNIT_RE.search(text)


def test_section_14_has_route_and_effective_gate_states(project):
    _opportunity(project)
    assert main(["--agente", "regulacion-seguridad", "puntuar", "compuerta", "O-0001", "seguridad", "superada",
                 "--motivo", "propuesta"]) == 0
    assert main(["--agente", "compuestos-toxicologia", "puntuar", "compuerta", "O-0001", "etica", "bloqueada",
                 "--motivo", "propuesta"]) == 0
    text = _skeleton(project).read_text("utf-8")
    section14 = text.split("## 14.")[1].split("## 15.")[0]
    assert "Ruta regulatoria preliminar del registro: `indeterminada` [SUP]" in section14
    assert re.search(r"\| seguridad \| no_resuelta \| superada \(propuesta sin firmar", section14)
    assert re.search(r"\| etica \| bloqueada \|", section14)
    assert "13.2 solo piensos" not in text and "optimización de la nutrición" in section14
    no_gates = text.replace(section14, "\nAlegación prevista: ninguna.\n\n")
    issues = [str(i) for i in chk.check_report(no_gates)]
    assert any("ruta o clasificación regulatoria" in m for m in issues)
    assert any("estado (superada, bloqueada o no_resuelta) de las compuertas" in m for m in issues)


def test_section_14_gate_states_must_match_the_registry(project):
    # D5-11: la sección 14 no puede declarar un estado de compuerta distinto del efectivo, ni quedarse sin oportunidad
    path = _filled_report(project)
    text = path.read_text("utf-8")
    assert not any("compuerta" in str(i).lower() for i in chk.check_report(text, conn=_conn(), path=path))
    claimed = text.replace("Alegación prevista (Reglamento", "La compuerta seguridad está superada.\n\nAlegación "
                                                            "prevista (Reglamento", 1)
    final = claimed.replace("estado: borrador", "estado: final")
    assert any("dice «superada» para la compuerta seguridad" in e and "«no_resuelta»" in e
               for e in _errors(final, conn=_conn(), path=path))
    table = text.replace("| seguridad | no_resuelta |", "| seguridad | superada |").replace("estado: borrador",
                                                                                             "estado: final")
    assert any("compuerta seguridad" in e for e in _errors(table, conn=_conn(), path=path))
    unknown = final.replace("oportunidad: O-0001", "oportunidad: O-0099")
    assert any("O-0099 del front matter no está en el registro" in e for e in _errors(unknown, conn=_conn(), path=path))
    missing = re.sub(r"(?m)^oportunidad: .*\n", "", final)
    assert any("no declara la oportunidad" in e for e in _errors(missing, conn=_conn(), path=path))
    other_route = re.sub(r"(?m)^Ruta regulatoria preliminar del registro: .*$",
                         "Ruta regulatoria preliminar: `aditivo_zootecnico` [SUP].", final)
    assert any("no recoge la ruta regulatoria que el registro tiene para O-0001 («indeterminada»)" in e
               for e in _errors(other_route, conn=_conn(), path=path))


def test_project_rubric_gates_are_used_everywhere(project, online):
    # Si la rúbrica del proyecto define otras compuertas, la CLI, el sello y el hook exigen las mismas.
    from importlib import resources
    rubric = resources.files("nutria.data").joinpath("rubrica.toml").read_text("utf-8")
    head, *gates = rubric.split("[[compuerta]]")
    kept = [g for g in gates if 'id = "etica"' not in g]
    (project / "config").mkdir(exist_ok=True)
    (project / "config" / "rubrica.toml").write_text(head + "".join("[[compuerta]]" + g for g in kept), "utf-8")
    assert "etica" not in chk.gate_ids_for(project / "research" / "informes" / "x.md")
    path = _filled_report(project)
    assert "| etica |" not in path.read_text("utf-8")
    assert main(["informe", "revisar", str(path), SIGN, "Ana Pérez", "--estado", "final"]) == 0
    assert main(["informe", "comprobar", str(path), "--online", "--sellar"]) == 0
    assert chk.stamp_status(path)[0]


def test_out_of_scope_route_shows_blocked_regulatory_gate(project):
    _opportunity(project, ruta_regulatoria_preliminar="fuera_de_alcance_medicamento")
    text = _skeleton(project).read_text("utf-8")
    assert re.search(r"\| regulatoria \| bloqueada \|", text)
    assert "fuera del alcance de un pienso" in text


# ============================================================================ D3-5, D3-16: hook de verificación
def _hook_project(project: Path) -> Path:
    hooks = project / "scripts" / "hooks"
    hooks.mkdir(parents=True)
    for name in ("check_research_file.py", "run_hook.sh"):
        shutil.copy(HOOKS / name, hooks / name)
    return hooks


def _run_hook(hooks: Path, payload) -> dict | None:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    res = subprocess.run([sys.executable, str(hooks / "check_research_file.py")], input=raw, text=True,
                         capture_output=True, env=env, timeout=120)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout) if res.stdout.strip() else None


def _payload(path: Path) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": "..."}}


def test_hook_blocks_final_reports_without_valid_stamp_in_every_variant(project, tmp_path_factory):
    # D3-5 (escéptico): comentarios YAML, comillas, comentario HTML antes del front matter, extensión en
    # mayúsculas, subcarpetas y enlaces simbólicos. El informe está completo: lo único que falla es el estado.
    hooks = _hook_project(project)
    informes = project / "research" / "informes"
    base = _filled_report(project).read_text("utf-8")
    assert "estado: borrador" in base

    def variant(front: str, prefix: str = "") -> str:
        return prefix + base.replace("estado: borrador", front)

    control = informes / "control.md"
    control.write_text(base, "utf-8")
    assert _run_hook(hooks, _payload(control)) is None                       # en borrador pasa
    cases = {
        informes / "H2c.md": variant("estado: final  # pendiente de firma"),
        informes / "H2e.md": variant("estado: 'final' # sellado"),
        informes / "H7.MD": variant("estado: final"),
        informes / "H2d.md": variant("estado: final", prefix="<!--\nestado: borrador\n-->\n"),
        informes / "sub" / "E.md": variant("estado: final"),
        informes / "H8.md": variant("estado: final\nestado: borrador"),
        informes / "H9.md": variant('estado: "fin\\x61l"'),
    }
    for path, text in cases.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf-8")
        decision = _run_hook(hooks, _payload(path))
        assert decision and decision["decision"] == "block", path.name
        assert "Sin sello" in decision["reason"] or "Estado" in decision["reason"], path.name
    outside = tmp_path_factory.mktemp("fuera") / "real.md"
    outside.write_text(variant("estado: final"), "utf-8")
    link = informes / "enlace.md"
    link.symlink_to(outside)
    decision = _run_hook(hooks, _payload(link))
    assert decision and "Sin sello" in decision["reason"]


def test_hook_passes_skeleton_and_blocks_bad_input(project):
    hooks = _hook_project(project)
    _opportunity(project)
    path = _skeleton(project)
    assert _run_hook(hooks, _payload(path)) is None
    assert _run_hook(hooks, {"tool_name": "Write", "tool_input": {"content": "research/informes/x.md"}})["decision"] \
        == "block"
    assert _run_hook(hooks, "{no es json research/informes/x.md")["decision"] == "block"
    assert _run_hook(hooks, {"tool_name": "Write", "tool_input": {"content": "nada"}}) is None


def test_hook_validates_json_against_schema(project):
    hooks = _hook_project(project)
    good = project / "research" / "oportunidades" / "O-nueva.json"
    good.write_text((EXAMPLES / "oportunidad_ejemplo.json").read_text("utf-8"), "utf-8")
    assert _run_hook(hooks, _payload(good)) is None
    bad = project / "research" / "hipotesis" / "sub" / "H-mala.json"
    bad.parent.mkdir(parents=True)
    bad.write_text(json.dumps({"titulo": "x"}), "utf-8")
    assert _run_hook(hooks, _payload(bad))["decision"] == "block"


def test_hook_blocks_agent_written_reviewer_even_with_a_stamp(project, online):
    hooks = _hook_project(project)
    path = _filled_report(project)
    text = path.read_text("utf-8").replace("estado: borrador", "estado: final")
    path.write_text(text.replace("revisado_por_humano: null", "revisado_por_humano: Dra. Prueba"), "utf-8")
    main(["informe", "comprobar", str(path), "--online", "--sellar"])
    decision = _run_hook(hooks, _payload(path))
    assert decision["decision"] == "block" and "revisión registrada" in decision["reason"]


def test_hook_accepts_reviewed_and_sealed_report(project, online):
    hooks = _hook_project(project)
    path = _final_report(project)
    assert main(["informe", "comprobar", str(path), "--online", "--sellar"]) == 0
    assert _run_hook(hooks, _payload(path)) is None


# ============================================================================ D9-10: run_hook.sh falla en cerrado
def _fake_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    hooks = repo / "scripts" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy(HOOKS / "run_hook.sh", hooks / "run_hook.sh")
    (hooks / "stub.py").write_text('import sys\nsys.stdin.read()\nprint(\'{"decision": "block", "reason": "stub"}\')\n',
                                   "utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("cat", "grep", "head"):
        found = shutil.which(tool)
        assert found, tool
        (bindir / tool).symlink_to(found)
    return repo, bindir


def _fake_python(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n", "utf-8")
    path.chmod(0o755)


def _run_sh(repo: Path, bindir: Path, payload: str) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/sh", str(repo / "scripts" / "hooks" / "run_hook.sh"), "stub.py"], input=payload,
                          text=True, capture_output=True, env={"PATH": str(bindir)}, timeout=60)


RESEARCH_PAYLOAD = json.dumps({"tool_input": {"file_path": "/x/research/informes/O-0001.md"}})


def test_run_hook_tries_next_interpreter_when_one_fails(tmp_path):
    repo, bindir = _fake_repo(tmp_path)
    _fake_python(repo / ".venv" / "bin" / "python", "exit 127")
    _fake_python(bindir / "python3", "exit 9009")                       # alias de la Microsoft Store
    _fake_python(bindir / "python", f'exec "{sys.executable}" "$@"')
    res = _run_sh(repo, bindir, RESEARCH_PAYLOAD)
    assert res.returncode == 0 and json.loads(res.stdout) == {"decision": "block", "reason": "stub"}
    _fake_python(bindir / "python3", 'echo "SyntaxError: invalid syntax" >&2; exit 1')   # Python antiguo
    res = _run_sh(repo, bindir, RESEARCH_PAYLOAD)
    assert json.loads(res.stdout)["reason"] == "stub"


def test_run_hook_blocks_when_no_interpreter_works(tmp_path):
    repo, bindir = _fake_repo(tmp_path)
    _fake_python(repo / ".venv" / "bin" / "python", "exit 1")
    _fake_python(bindir / "python3", "exit 9009")
    res = _run_sh(repo, bindir, RESEARCH_PAYLOAD)
    decision = json.loads(res.stdout)
    assert res.returncode == 0 and decision["decision"] == "block" and "python3=" in decision["reason"]
    res = _run_sh(repo, bindir, json.dumps({"tool_input": {"file_path": "/x/docs/notas.md"}}))
    assert res.returncode == 0 and res.stdout.strip() == ""
    (repo / ".venv" / "bin" / "python").unlink()
    (bindir / "python3").unlink()
    res = _run_sh(repo, bindir, RESEARCH_PAYLOAD)
    assert json.loads(res.stdout)["decision"] == "block"
    upper = json.dumps({"tool_input": {"file_path": "/x/Research/Informes/O-0001.md"}})   # sin distinguir mayúsculas
    assert json.loads(_run_sh(repo, bindir, upper).stdout)["decision"] == "block"


def test_run_hook_with_the_real_hook_falls_back_and_blocks(project):
    # De principio a fin: run_hook.sh con check_research_file.py. Un python3 sin las dependencias del proyecto sale
    # con 3 (explica el bloqueo) y se prueba el siguiente; el informe final sin sello se bloquea.
    hooks = _hook_project(project)
    path = _filled_report(project)
    path.write_text(path.read_text("utf-8").replace("estado: borrador", "estado: final"), "utf-8")
    bindir = project / "bin"
    bindir.mkdir()
    for tool in ("cat", "grep", "head"):
        (bindir / tool).symlink_to(shutil.which(tool))
    _fake_python(bindir / "python3", f'exec "{sys.executable}" -I -S "$@"')       # aislado: sin nutria ni requests
    env = dict(os.environ, PATH=str(bindir), PYTHONPATH=str(ROOT / "src"))

    def run(payload: dict) -> dict:
        res = subprocess.run(["/bin/sh", str(hooks / "run_hook.sh"), "check_research_file.py"],
                             input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=120)
        assert res.returncode == 0, res.stderr
        return json.loads(res.stdout)

    only_broken = run(_payload(path))
    assert only_broken["decision"] == "block" and "no operativo" in only_broken["reason"]
    _fake_python(bindir / "python", f'exec "{sys.executable}" "$@"')
    decision = run(_payload(path))
    assert decision["decision"] == "block" and "Sin sello" in decision["reason"]
