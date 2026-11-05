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


_OPEN_CONNECTIONS: list = []


@pytest.fixture(autouse=True)
def close_connections():
    yield
    while _OPEN_CONNECTIONS:
        _OPEN_CONNECTIONS.pop().close()


def _conn():
    conn = db.connect(load_config().db_path)
    _OPEN_CONNECTIONS.append(conn)
    return conn


def _register(*dois, verified=True, retracted=False):
    conn = _conn()
    for doi in dois:
        db.upsert_source(conn, {"doi": doi, "title": "Fuente de prueba", "year": 2020, "verified": verified,
                                "verified_via": "prueba", "retracted": retracted})
    conn.close()


def _critique(project: Path, opp: str = "O-0001") -> str:
    """Revisión adversarial del crítico (como la que deja red-team-auto) y su ruta relativa."""
    rel = f"research/revisiones/informes_{opp}_redteam.md"
    target = project / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# Revisión adversarial de research/informes/{opp}.md\n\nSin objeciones bloqueantes [HIP].\n",
                      "utf-8")
    return rel


def _filled_report(project: Path, opp: str = "O-0001", **opp_changes) -> Path:
    _opportunity(project, **opp_changes)
    path = _skeleton(project, opp)
    text = fill(path.read_text("utf-8")).replace("revision_critica: null", f"revision_critica: {_critique(project, opp)}")
    path.write_text(text, "utf-8")
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


def test_revised_or_final_report_needs_the_critic_review(project):
    # D3-6: en revisado o final hace falta la revisión adversarial del crítico, enlazada y referida a este informe
    path = _filled_report(project)
    final = path.read_text("utf-8").replace("estado: borrador", "estado: final")
    assert not any("crítico" in e for e in _errors(final, conn=_conn(), path=path))
    missing = re.sub(r"(?m)^revision_critica: .*$", "revision_critica: null", final)
    assert any("Falta la revisión del crítico" in e for e in _errors(missing, conn=_conn(), path=path))
    outside = project / "research" / "planes" / "critica.md"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("Crítica de O-0001", "utf-8")
    elsewhere = re.sub(r"(?m)^revision_critica: .*$", "revision_critica: research/planes/critica.md", final)
    assert any("no es un documento de research/revisiones" in e for e in _errors(elsewhere, conn=_conn(), path=path))
    other = project / "research" / "revisiones" / "otra.md"
    other.write_text("Revisión adversarial de research/informes/O-0002.md", "utf-8")
    unrelated = re.sub(r"(?m)^revision_critica: .*$", "revision_critica: research/revisiones/otra.md", final)
    assert any("no menciona este informe" in e for e in _errors(unrelated, conn=_conn(), path=path))
    path.write_text(missing.replace("estado: final", "estado: borrador"), "utf-8")
    with pytest.raises(SystemExit, match="Falta la revisión del crítico"):     # la persona no puede saltársela
        main(["informe", "revisar", str(path), SIGN, "Ana", "--estado", "final"])


def test_reviews_are_insert_only(project):
    path = _final_report(project)
    conn = _conn()
    assert [r["reviewer"] for r in db.report_reviews(conn, report="research/informes/O-0001.md")] == ["Ana Pérez"]
    for sql in ("UPDATE report_reviews SET reviewer = 'Otra'", "DELETE FROM report_reviews"):
        with pytest.raises(Exception, match="no se (modifican|borran)"):
            conn.execute(sql)
    with pytest.raises(db.RegistryError):
        db.add_report_review(conn, "x.md", "no-es-una-huella", "Ana")
    digest = chk.review_digest(path.read_text("utf-8"))
    with pytest.raises(db.RegistryError, match="no la registra un agente"):          # un agente declarado
        db.add_report_review(conn, "x.md", digest, "Ana", actor="director-cientifico")
    with pytest.MonkeyPatch.context() as m:                                          # dentro de Claude Code
        m.setenv("CLAUDECODE", "1")
        with pytest.raises(db.RegistryError, match="no la registra un agente"):
            db.add_report_review(conn, "x.md", digest, "Ana")
    assert path.is_file()
    conn.close()


def test_review_command_rejects_abbreviated_signature_option(project, capsys):
    # La orden no acepta abreviaturas (--firma, --firma-h): el hook de Bash solo reconoce la opción completa.
    path = _filled_report(project)
    for abbreviation in ("--firma", "--firma-h", "--fir"):
        with pytest.raises(SystemExit) as exc:
            main(["informe", "revisar", str(path), abbreviation, "Ana"])
        assert exc.value.code == 2
        # tampoco en `verificar manual` (una URL dada por verificada sin la opción completa reabriría el sello)
        with pytest.raises(SystemExit) as exc:
            main(["verificar", "manual", "https://example.org/norma", abbreviation, "Ana"])
        assert exc.value.code == 2
    assert db.report_reviews(_conn()) == []
    assert db.get_manual_verification(_conn(), "https://example.org/norma") is None


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


@pytest.mark.parametrize("text", [
    "---\nestado: borrador\n---\n# x\n\n**Estado:** final\n",                       # el cuerpo dice otra cosa
    "---\nestado: borrador\n---\n# x\n\n- _Estado_: 'revisado' (pendiente de sello)\n",
    "---\n<!--\nestado: final\n-->\nestado: borrador\n---\n# x\n",                  # comentario HTML en el front matter
    "---\nestado: borrador <!--\n---\nestado: final\n-->\n---\n# x\n",
])
def test_ambiguous_status_fails_closed(text):
    info = chk.status_info(text)
    assert info.strict and info.problem
    assert any(info.problem in e for e in _errors(text, require_sections=False))


def test_body_mentions_of_the_word_estado_are_not_statuses():
    for line in ("Estado: final de la gestación (preparto).", "El estado: final del ensayo se decidirá después."):
        info = chk.status_info(f"---\nestado: borrador\n---\n# x\n\n{line}\n")
        assert info.value == "borrador" and not info.strict and info.problem is None, line


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
    "La colina reduce la cetosis [`PUB`].",                    # código dentro de los corchetes
    "La colina reduce la cetosis [P`UB`].",                    # código pegado a una letra
    "La colina reduce la cetosis [ṔUB].",                # acento combinado
    "La colina reduce la cetosis [Ṕ​UB].",               # letra con acento y anchura cero
])
def test_pub_without_reference_is_detected_everywhere(body):
    assert any("[PUB] sin referencia" in e for e in _errors(_draft(body), require_sections=False))


def test_label_only_code_span_is_a_term_mention():
    assert not _errors(_draft("Las etiquetas válidas son `[PUB]`, `[DER]` y `[Rn]`. Dato [HIP] citado [R1]."),
                       require_sections=False)
    # una cifra en código pegada a una palabra sigue siendo una cifra
    assert any("Cifra con unidades" in e for e in _errors(_draft("La dosis fue valor`15 g/d` en todas."),
                                                         require_sections=False, strict=True))


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
                                  "5 puntos porcentuales", "4 UFC", "2,5  kg/d", "2,5\nkg/d", "38,1 oC", "2,5 (kg/d)",
                                  "€15", "US$ 2", "3 cm", "2 log UFC/g", "1,2 UFL"])
def test_units_regex_matches_nutrition_units(text):
    assert chk.UNIT_RE.search(text)


@pytest.mark.parametrize("text", ["3 grupos", "[R1]", "DOI 10.1234/x", "21 días", "PMID: 12345678", "H-0001",
                                  "## 14. Seguridad", "grupo 2 (t de Student)", "en 2 o cuatro", "los 3 (tres)"])
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
            "La colina aumentó la leche en vacas [PUB] [R1].")
    assert _annex_errors(body, "| R1 | La colina aumentó la leche en vacas | respalda | y |") == []
    # «Resumen:» forma parte de la segunda afirmación («:» no cierra la frase): ya no es idéntica a la primera y
    # necesita su propia fila, copiada tal como aparece.
    body = ("La colina aumentó la leche en vacas [PUB] [R1].\n\n"
            "Resumen: la colina aumentó la leche en vacas [PUB] [R1].")
    row = "| R1 | La colina aumentó la leche en vacas | respalda | y |"
    assert any("línea 8" in e for e in _annex_errors(body, row))
    assert _annex_errors(body, row + "\n| R1 | Resumen: la colina aumentó la leche en vacas | respalda | y |") == []
    body = "La colina aumentó la leche [PUB] [R1] y redujo la cetosis [PUB] [R1]."
    rows = "| R1 | La colina aumentó la leche | respalda | y |\n| R1 | La colina ... redujo la cetosis | respalda | z |"
    assert _annex_errors(body, rows) == []
    errors = _annex_errors(body, rows.replace("La colina ... redujo", "La colina redujo"))
    assert any("«redujo la cetosis»" in e for e in errors)        # el mensaje dice qué texto reproducir


@pytest.mark.parametrize("body, row", [
    # «;» o «:» no cierran la afirmación: la parte sustantiva antes de ellos sigue siendo de la [PUB]
    ("La colina aumentó la leche 1,2 kg/d en vacas en transición; así [PUB] [R1].", "así"),
    ("La colina aumentó la leche 1,2 kg/d en vacas en transición: así lo publicaron [PUB] [R1].",
     "así lo publicaron"),
    # la etiqueta antes de la parte sustantiva no la deja fuera
    ("Según el ensayo de campo publicado [R1] [PUB], la colina aumentó la leche 1,2 kg/d en vacas en transición.",
     "Según el ensayo de campo publicado"),
    ("Según [R1] [PUB]: la colina aumentó la leche 1,2 kg/d en vacas en transición.", "Según"),
])
def test_claim_is_not_shrunk_by_punctuation_or_label_position(body, row):
    # D3-3: una fila que solo reproduce una muletilla no respalda la afirmación sustantiva de la frase
    errors = _annex_errors(body, f"| R1 | {row} | respalda | x |")
    assert any("Sin comprobación de respaldo de R1" in e for e in errors), errors
    whole = "| R1 | la colina aumentó la leche 1,2 kg/d en vacas en transición | respalda | x |"
    assert _annex_errors(body, whole) == []


def test_trailing_qualifiers_belong_to_the_last_claim_of_the_sentence():
    body = "La colina protegida aumentó la leche [PUB] [R1] en vacas multíparas Holstein durante la transición."
    errors = _annex_errors(body, "| R1 | La colina protegida aumentó la leche | respalda | x |")
    assert any("Sin comprobación de respaldo de R1" in e for e in errors)
    assert _annex_errors(body, "| R1 | La colina protegida aumentó la leche ... en vacas multíparas Holstein | "
                               "respalda | x |") == []
    # con la etiqueta delante, cada afirmación es lo que sigue hasta la etiqueta siguiente
    body = "[PUB] [R1] La colina protegida aumentó la leche en vacas y [HIP] la grasa también."
    assert _annex_errors(body, "| R1 | La colina protegida aumentó la leche en vacas | respalda | x |") == []


def test_negative_rows_without_a_claim_or_reference_are_errors_even_in_drafts():
    body = "La colina aumentó la leche en vacas [PUB] [R1]."
    rows = ("| R1 | La colina aumentó la leche en vacas | respalda | y |\n"
            "| R1 | La colina redujo la mortalidad de los terneros | no_respalda | no lo dice |\n"
            "| sin ref | La colina aumentó la leche en vacas | no_verificable | z |\n"
            "| R1 | | no_respalda | vacía |")
    draft = _strict_doc(body, rows).replace("estado: final", "estado: borrador")
    errors = _errors(draft, require_sections=False)
    assert any("no corresponde a ninguna afirmación" in e for e in errors)
    assert any("sin referencia [Rn] reconocible" in e for e in errors)
    assert any("la columna Afirmación está vacía" in e for e in errors)
    positive = _strict_doc(body, rows.split("\n")[0] + "\n| R1 | Otra afirmación que ya no está | respalda | w |")
    warnings = chk.check_report(positive.replace("estado: final", "estado: borrador"), require_sections=False)
    assert any("no corresponde a ninguna afirmación" in str(i) and i.severity == "aviso" for i in warnings)


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
    # una fila negativa escondida en un bloque de código o en una cita tampoco se pierde, ni en borrador
    negative = "| R1 | La colina aumentó la leche en vacas | no_respalda | z |"
    good = _strict_doc(body, "| R1 | La colina aumentó la leche en vacas | respalda | y |")
    for hidden in (f"\n```\n{negative}\n```\n", f"\n> {negative}\n"):
        draft = (good + hidden).replace("estado: final", "estado: borrador")
        assert any("dentro de un bloque de código o de una cita" in e for e in _errors(draft, require_sections=False))


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
    assert "sin revisión humana" in msg                       # un borrador sellado no parece un informe revisado
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


def test_skeleton_digestive_fate_of_non_ruminants_is_not_ruminal(project):
    _opportunity(project, especie="porcino", etapa="cebo (30 a 110 kg de peso vivo)")
    text = _skeleton(project).read_text("utf-8")
    section7 = text.split("### 7.1")[1].split("## 8.")[0].split("-->")[1]       # lo visible, sin el comentario
    assert "ruminal" not in section7 and "absorción intestinal a la dosis prevista: [DESC]" in section7
    assert "Compuerta `destino_digestivo`" in section7


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
    # cada compuerta, propuesta por el rol que la propone según CLAUDE.md
    assert main(["--agente", "compuestos-toxicologia", "puntuar", "compuerta", "O-0001", "seguridad", "superada",
                 "--motivo", "propuesta"]) == 0
    assert main(["--agente", "sostenibilidad-bienestar", "puntuar", "compuerta", "O-0001", "etica", "bloqueada",
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
    # el informe de O-0001 no puede declarar otra oportunidad (y tomar su especie, su ruta y sus compuertas)
    assert any("debe decir «oportunidad: O-0001»" in e for e in _errors(unknown, conn=_conn(), path=path))
    missing = re.sub(r"(?m)^oportunidad: .*\n", "", final)
    assert any("no declara la oportunidad" in e for e in _errors(missing, conn=_conn(), path=path))
    other_route = re.sub(r"(?m)^Ruta regulatoria preliminar del registro: .*$",
                         "Ruta regulatoria preliminar: `aditivo_zootecnico` [SUP].", final)
    assert any("declara la ruta regulatoria «aditivo_zootecnico», pero el registro tiene «indeterminada»" in e
               for e in _errors(other_route, conn=_conn(), path=path))
    no_route = re.sub(r"(?m)^Ruta regulatoria preliminar del registro: .*$", "Clasificación pendiente [DESC].", final)
    assert any("no recoge la ruta regulatoria que el registro tiene para O-0001 («indeterminada»" in e
               for e in _errors(no_route, conn=_conn(), path=path))


def test_section_14_gate_states_in_any_wording_are_checked_against_the_registry(project):
    # D5-11: sin tabla y sin la palabra «compuerta», «- seguridad: superada» también se contrasta con el registro
    path = _filled_report(project)
    text = path.read_text("utf-8").replace("estado: borrador", "estado: final")
    section14 = text.split("## 14.")[1].split("## 15.")[0]
    table = "\n".join(line for line in section14.split("\n") if line.startswith("|"))
    gates = chk.gate_ids_for(path)
    listed = text.replace(table, "\n".join(f"- {g}: superada" for g in gates))
    errors = _errors(listed, conn=_conn(), path=path)
    assert sum("dice «superada» para la compuerta" in e for e in errors) == len(gates)
    honest = text.replace(table, "\n".join(f"- {g}: no resuelta" for g in gates))
    assert not any("compuerta" in e for e in _errors(honest, conn=_conn(), path=path))
    # el estado de una compuerta no se toma de la mención de la siguiente
    mixed = text.replace(table, "Seguridad (pendiente de evaluar) y etica bloqueada; "
                         + "; ".join(f"{g} no resuelta" for g in gates if g not in ("seguridad", "etica")) + ".")
    errors = _errors(mixed, conn=_conn(), path=path)
    assert any("no recoge el estado" in e and "seguridad" in e for e in errors)
    assert any("dice «bloqueada» para la compuerta etica" in e for e in errors)
    assert not any("para la compuerta seguridad" in e for e in errors)


def test_unverifiable_identifiers_are_errors_in_reports_even_in_draft(project):
    # D3-13 / D10-1: una entrada con identificador fuera de la bibliografía no se verifica; en un informe en borrador
    # tampoco puede acabar en un «Sello válido» que diga que se verificaron todas las referencias
    path = _filled_report(project)
    text = path.read_text("utf-8")
    shadow = text.replace("## 10. Estudios favorables", f"[R1] Inventado. 2021. DOI: {FAKE}\n\n## 10. Estudios favorables")
    assert any("fuera de la bibliografía" in e for e in _errors(shadow, conn=_conn(), path=path))
    stray = text.replace(f"[R1] Autor A", f"DOI: {FAKE}\n[R1] Autor A")
    assert any("fuera de una entrada" in e for e in _errors(stray, conn=_conn(), path=path))
    plan = project / "research" / "planes" / "P.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    note = _draft("Dato [HIP] [R1].\n\n[R2] Otra. 2021. DOI: 10.1234/otra")
    assert any("fuera de la bibliografía" in str(i) and i.severity == "aviso"
               for i in chk.check_report(note, require_sections=False, path=plan))


def test_project_rubric_gates_are_used_everywhere(project, online):
    # Si la rúbrica del proyecto añade una compuerta, el esqueleto, la comprobación, el sello y el hook la exigen
    # igual; si la rúbrica no se puede leer, se exigen las seis compuertas por defecto (falla en cerrado).
    from importlib import resources
    rubric = resources.files("nutria.data").joinpath("rubrica.toml").read_text("utf-8")
    extra = ('\n[[compuerta]]\nid = "bioseguridad_granja"\nnombre = "Bioseguridad de la granja"\n'
             'bloquea_si = "Riesgo de introducir patógenos en la explotación."\n')
    (project / "config").mkdir(exist_ok=True)
    (project / "config" / "rubrica.toml").write_text(rubric + extra, "utf-8")
    report_path = project / "research" / "informes" / "x.md"
    assert "bioseguridad_granja" in chk.gate_ids_for(report_path)
    path = _filled_report(project)
    text = path.read_text("utf-8")
    assert "| bioseguridad_granja | no_resuelta |" in text
    without = text.replace("| bioseguridad_granja |", "| otra |").replace("estado: borrador", "estado: final")
    assert any("bioseguridad_granja" in e for e in _errors(without, conn=_conn(), path=path))
    assert main(["informe", "revisar", str(path), SIGN, "Ana Pérez", "--estado", "final"]) == 0
    assert main(["informe", "comprobar", str(path), "--online", "--sellar"]) == 0
    assert chk.stamp_status(path)[0]
    (project / "config" / "rubrica.toml").write_text("esto no es TOML [", "utf-8")
    assert chk.gate_ids_for(report_path) == list(chk.DEFAULT_GATES)


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


# ============================================================================ ronda 2: elusiones de la revisión
def _body_errors(body: str, rows: str = ANNEX, status: str = "final") -> list[str]:
    """Errores (sin los de la revisión humana, que exigen registro) de un documento con CLAIM_A, CLAIM_B y `body`."""
    text = _strict_doc(CLAIM_A + "\n" + CLAIM_B + "\n\n" + body, rows).replace("estado: final", f"estado: {status}")
    return [str(i) for i in chk.check_report(text, require_sections=False)
            if i.severity == "error" and i.code != chk.REVIEW_CODE]


def _body_issues(body: str, rows: str = ANNEX, status: str = "borrador") -> list[chk.Issue]:
    text = _strict_doc(CLAIM_A + "\n" + CLAIM_B + "\n\n" + body, rows).replace("estado: final", f"estado: {status}")
    return [i for i in chk.check_report(text, require_sections=False) if i.code != chk.REVIEW_CODE]


@pytest.mark.parametrize("body", [
    "En el ensayo de campo la producción aumentó 2,5\nkg/d en vacas multíparas.",            # salto de línea
    "En el ensayo de campo la producción aumentó 2,5  kg/d en vacas multíparas.",            # dos espacios
    "En el ensayo de campo la producción aumentó 2,5  kg/d en vacas multíparas.",       # espacio fino
    "En el ensayo de campo la producción aumentó 2,5&nbsp;kg/d en vacas multíparas.",        # entidad HTML
    "En el ensayo de campo la producción aumentó 2,5 (kg/d) en vacas multíparas.",           # unidad entre paréntesis
    "La temperatura rectal bajó a 38,1 ºC con la colina.",                                   # indicador ordinal
    "El margen subió €15 por vaca y año con la colina.",                                     # moneda delante
    "# Resultado del ensayo: +2,5 kg/d de leche",                                            # otro título de nivel 1
    "En el ensayo de campo la producción aumentó 2,5 кg/d en vacas multíparas.",       # «к» cirílica
])
def test_figures_split_spaced_or_disguised_need_a_label(body):
    # D3-4 (ronda 2): la cifra y su unidad se buscan en el párrafo entero, no línea a línea
    assert any("Cifra con unidades" in e for e in _body_errors(body)), body
    assert any("Cifra con unidades" in str(i) and i.severity == "aviso" for i in _body_issues(body)), body


@pytest.mark.parametrize("body", [
    "García (2018) demostró que la colina elimina la cetosis subclínica.",                   # narrativa, un autor
    "García (2018, p. 23) demostró que la colina elimina la cetosis subclínica.",            # con página
    "Smith J. M. (2019) demostró que la colina elimina la cetosis subclínica.",              # con iniciales
    "D'Angelo (2019) demostró que la colina elimina la cetosis subclínica.",                 # apóstrofo
    "García et al. 2018 demostraron que la colina elimina la cetosis subclínica.",           # sin paréntesis
    "Según García 2018, la colina elimina la cetosis subclínica.",                           # tras «según»
    "Como demostraron García et al.\n(2018), la colina elimina la cetosis subclínica.",      # partida en dos líneas
    CLAIM_A[:-1] + ". García et al. (2018) demostró que la colina elimina la cetosis.",      # [Rn] en otra frase
])
def test_author_year_citations_need_a_reference_in_the_same_sentence(body):
    assert any("Cita autor-año sin referencia" in e for e in _body_errors(body)), body
    assert any("Cita autor-año sin referencia" in e for e in _body_errors(body, status="borrador")), body


@pytest.mark.parametrize("body, expected", [
    ("| Estudio | Especie | n | Dosis (g/d) | Leche (kg/d) |\n|---|---|---|---|---|\n"
     "| García 2018 | vacuno lechero | 60 | 15 | +2,5 |\n| Pérez et al. 2017 | vacuno lechero | 48 | 20 | +3,1 |",
     ("Cita autor-año", "Cifra con unidades")),                                              # unidad en la cabecera
    ("| Variable | Control | Colina |\n|---|---|---|\n| Leche (kg/d) | 30,1 | 32,4 |", ("Cifra con unidades",)),
    ("| Variable | Valor | Unidad |\n|---|---|---|\n| Leche | 2,5 | kg/d |", ("Cifra con unidades",)),
    ("| Estudio | Leche | Grasa |\n|---|---|---|\n| | kg/d | % |\n| Ensayo de campo | 2,5 | 0,1 |",
     ("Cifra con unidades",)),                                                               # fila de unidades
    ("| Referencia | Resultado |\n|---|---|\n| García y Pérez 2018 | más leche |", ("Cita autor-año",)),
    ("Estudio | Leche (kg/d)\n--- | ---\nGarcía 2018 | 2,5", ("Cita autor-año", "Cifra con unidades")),   # sin barras
])
def test_tables_are_checked_by_their_structure(body, expected):
    errors = _body_errors(body)
    for what in expected:
        assert any(what in e for e in errors), (what, body)


@pytest.mark.parametrize("body, what", [
    ("La colina elimina la cetosis subclínica [3].", "Cita numérica «[3]»"),
    ("La colina elimina la cetosis subclínica (R1).", "Referencia «(R1)» mal escrita"),
])
def test_numeric_citations_and_parenthesized_references_are_errors(body, what):
    assert any(what in e for e in _body_errors(body, status="borrador"))


@pytest.mark.parametrize("body, row", [
    ("| Estudio | Especie | n | Dosis (g/d) | Leche (kg/d) |\n|---|---|---|---|---|\n"
     "| García 2018 [R1] | vacuno lechero | 60 | 15 | +1,2 [PUB] |", "García 2018 vacuno lechero 60 15 +1,2"),
    ("| Estudio | Semana 4 (kg/d) |\n|---|---|\n| Ensayo de campo [R1] | 1,2 [PUB] |", "Ensayo de campo 1,2"),
    ("| Estudio | Leche (kg/d) | Evidencia |\n|---|---|---|\n| García et al. 2017. Vacas Holstein. | 1,2 | [PUB] [R1] |",
     "García et al. 2017. Vacas Holstein. 1,2"),                                             # punto dentro de una celda
    ("Como demostraron García et al.\n(2018) [R1], la colina aumentó la leche [PUB].",
     "Como demostraron García et al. (2018), la colina aumentó la leche"),
    ("La colina protegida aumentó la producción de leche en vacas en transición [PUB]\n[R1].",
     "La colina protegida aumentó la producción de leche en vacas en transición"),         # [Rn] en la línea siguiente
    ("Los animales del Grupo A (2019) recibieron la dieta basal [SUP].", ""),
    ("La alegación se rige por el Reglamento (CE) 767/2009, art. 13, y el Reglamento (UE) 2019/6 [SUP].", ""),
    ("El proyecto se alinea con la Agenda 2030 y con Horizonte 2020 [SUP].", ""),
    ("Se usaron tres dietas; Holstein 2019; y Frisona [SUP].", ""),
    ("```\nx = datos[1]\nprint([2])\n```", ""),
    ("La colina es un nutriente esencial[^1] [SUP].\n\n[^1]: Nota del autor [SUP].", ""),
    ("Véase [la guía][1] [SUP].\n\n[1]: https://example.org", ""),
    ("El IC 95 % fue [1,2; 3,4] kg/d [DER] (cálculo: nutria meta analizar).", ""),
])
def test_legitimate_forms_are_not_flagged(body, row):
    rows = ANNEX + (f"\n| R1 | {row} | respalda | x |" if row else "")
    assert _body_errors(body, rows) == [], body


def test_reviewer_round1_evasions_block_review_and_stamp(project, online):
    # D3-4: las cinco formas de la revisión (cifra partida, dos espacios, cita narrativa, cita en una línea con [Rn]
    # de otra frase y tabla con la unidad en la cabecera) no llegan a un informe final revisado y sellado.
    path = _filled_report(project)
    base = path.read_text("utf-8")
    table = ("\n\n| Estudio | Especie | n | Dosis (g/d) | Leche (kg/d) |\n|---|---|---|---|---|\n"
             "| García 2018 | vacuno lechero | 60 | 15 | +2,5 |\n| Pérez et al. 2017 | vacuno lechero | 48 | 20 | +3,1 |\n")
    variants = {CLAIM_B + "\nEn el ensayo de campo la producción aumentó 2,5\nkg/d en vacas multíparas.": "Cifra",
                CLAIM_B + "\nEn el ensayo de campo la producción aumentó 2,5  kg/d en vacas multíparas.": "Cifra",
                CLAIM_B + "\nGarcía (2018) demostró que la colina elimina la cetosis subclínica.": "Cita autor-año",
                CLAIM_B + " García et al. (2018) demostró que la colina elimina la cetosis subclínica.": "Cita autor-año",
                CLAIM_B + table: "Cita autor-año"}
    for variant, what in variants.items():
        path.write_text(base.replace(CLAIM_B, variant, 1), "utf-8")
        with pytest.raises(SystemExit, match=f"No se registra la revisión(.|\n)*{what}"):
            main(["informe", "revisar", str(path), SIGN, "Ana Pérez", "--estado", "final"])
        assert db.report_reviews(_conn()) == []
        # escrito a mano como final, el sello no vale
        path.write_text(path.read_text("utf-8").replace("estado: borrador", "estado: final"), "utf-8")
        main(["informe", "comprobar", str(path), "--online", "--sellar"])
        assert not chk.stamp_status(path)[0], variant
        assert any(what in e for e in _errors(path.read_text("utf-8"), conn=_conn(), path=path)), variant


def test_support_tables_outside_annex_a_are_errors():
    # D3-3 (a): una valoración no_respalda en otra sección, bajo «## Anexo A1» o «## Annex A» no queda sin leer
    two = ("La colina aumenta la producción de leche en vacas lecheras [PUB] [R1]. "
           "La colina reduce la incidencia de cetosis en vacas lecheras [PUB] [R1].")
    rows = ("| R1 | La colina aumenta la producción de leche en vacas lecheras | respalda | x |\n"
            "| R1 | La colina reduce la incidencia de cetosis en vacas lecheras | respalda | x |")
    negative = "| R1 | La colina reduce la incidencia de cetosis en vacas lecheras | no_respalda | x |\n"
    ok = _strict_doc(two, rows)
    assert not [e for e in _errors(ok, require_sections=False) if "Anexo A" in e or "respaldo" in e]
    for where in ("## 13. Limitaciones", "## Anexo A1. Continuación", "## Annex A"):
        doc = ok + f"\n{where}\n\n{ANNEX_HEADER}{negative}"
        errors = _errors(doc.replace("estado: final", "estado: borrador"), require_sections=False)
        assert any("Tabla de respaldo (columna Respaldo) fuera del Anexo A" in e for e in errors), where
        assert any("valoración de respaldo negativa fuera del Anexo A" in e for e in errors), where
    headerless = ok.replace("## Referencias", f"{negative}\n## Referencias")
    assert any("valoración de respaldo negativa fuera del Anexo A" in e
               for e in _errors(headerless, require_sections=False))
    emphasized = ok.replace("| Ref | Afirmación | Respaldo | Motivo |", "| **Ref** | **Afirmación** | **Respaldo** | "
                                                                       "**Motivo** |")
    assert not [e for e in _errors(emphasized, require_sections=False) if "Anexo A" in e]
    # GFM admite filas sin «|» inicial dentro de una tabla: una negativa escrita así tampoco queda sin leer
    pipeless = ok.rstrip("\n") + "\nR1 | La colina reduce la incidencia de cetosis en vacas lecheras | no_respalda | x\n"
    assert any("R1 NO respalda" in e for e in _errors(pipeless.replace("estado: final", "estado: borrador"),
                                                       require_sections=False))


@pytest.mark.parametrize("body", [
    "La colina protegida aumenta la producción de leche en vacas lecheras. Así lo indica el ensayo [PUB] [R1].",
    "La colina protegida aumenta la producción de leche en vacas lecheras.\n\nAsí lo indica el ensayo [PUB] [R1].",
    "La colina protegida aumenta la producción de leche en vacas lecheras. Según el ensayo [PUB] [R1].",
    "La colina protegida aumenta la producción de leche en vacas lecheras. Es lo que muestra el ensayo [PUB] [R1].",
    "La colina protegida aumenta la producción de leche en vacas lecheras. Esto se observó en el ensayo [PUB] [R1].",
])
def test_pub_sentence_that_only_points_to_an_unlabeled_sentence(body):
    # D3-3 (b): la afirmación real quedaría sin etiqueta y la fila del Anexo A solo reproduciría la remisión
    row = body.replace("\n\n", " ").split(". ")[-1].replace(" [PUB] [R1].", "")
    rows = ANNEX + f"\n| R1 | {row} | respalda | x |"
    assert any("remite a la frase anterior" in e for e in _body_errors(body, rows)), body
    assert any("remite a la frase anterior" in str(i) and i.severity == "aviso" for i in _body_issues(body, rows))
    labeled = body.replace("vacas lecheras.", "vacas lecheras [HIP].", 1)
    assert not any("remite a la frase anterior" in e for e in _body_errors(labeled, rows)), labeled


@pytest.mark.parametrize("body, row", [
    (CLAIM_A + " Este efecto se mantuvo hasta la semana 12 en multíparas [PUB] [R1].",
     "Este efecto se mantuvo hasta la semana 12 en multíparas"),
    ("La colina es un nutriente. Como indica el ensayo, la colina protegida aumentó la producción de leche en vacas "
     "[PUB] [R1].", "Como indica el ensayo, la colina protegida aumentó la producción de leche en vacas"),
    ("La betaína es un osmolito. La colina protegida aumenta la producción de leche; así lo indica el ensayo [PUB] [R1].",
     "La colina protegida aumenta la producción de leche; así lo indica el ensayo"),
])
def test_substantive_or_chained_pub_sentences_are_not_anaphora(body, row):
    assert not any("remite a la frase anterior" in e
                   for e in _body_errors(body, ANNEX + f"\n| R1 | {row} | respalda | x |")), body


def test_section_14_route_is_the_first_declared_and_struck_text_does_not_count(project):
    # D5-11: «materia_prima; se descartó la de aditivo zootécnico» declara materia_prima, y «~~no_resuelta~~
    # superada» declara superada
    path = _filled_report(project, ruta_regulatoria_preliminar="aditivo_zootecnico")
    final = path.read_text("utf-8").replace("estado: borrador", "estado: final")
    assert not [e for e in _errors(final, conn=_conn(), path=path) if "ruta" in e or "compuerta" in e]
    route_line = re.search(r"(?m)^Ruta regulatoria preliminar del registro: .*$", final).group(0)
    mention = final.replace(route_line, "Ruta regulatoria preliminar: `materia_prima` [SUP]; se descartó la de aditivo "
                                        "zootécnico [SUP].")
    assert any("declara la ruta regulatoria «materia_prima», pero el registro tiene «aditivo_zootecnico»" in e
               for e in _errors(mention, conn=_conn(), path=path))
    struck_route = final.replace(route_line, "Ruta regulatoria preliminar: ~~`aditivo_zootecnico`~~ `materia_prima` "
                                             "[SUP].")
    assert any("declara la ruta regulatoria «materia_prima»" in e for e in _errors(struck_route, conn=_conn(), path=path))
    wrapped = final.replace(route_line, "Ruta regulatoria preliminar del registro:\n`aditivo_zootecnico` [SUP].")
    assert not [e for e in _errors(wrapped, conn=_conn(), path=path) if "ruta" in e]
    for struck in ("| seguridad | ~~no_resuelta~~ superada |", "| seguridad | <del>no_resuelta</del> superada |"):
        text = final.replace("| seguridad | no_resuelta |", struck)
        assert any("dice «superada» para la compuerta seguridad" in e for e in _errors(text, conn=_conn(), path=path))
    honest = final.replace("| seguridad | no_resuelta |", "| seguridad | ~~superada~~ no_resuelta |")
    assert not [e for e in _errors(honest, conn=_conn(), path=path) if "compuerta" in e]


@pytest.mark.parametrize("name", ["pendiente", "-", "no", "null", "~", "false", "X", "  "])
def test_review_rejects_placeholder_names(project, name):
    # Regresión de la ronda 1: un nombre de relleno no puede dejar una revisión imborrable que luego no cuenta
    path = _filled_report(project)
    before = path.read_bytes()
    with pytest.raises(SystemExit, match="exige|no es el nombre de una persona"):
        main(["informe", "revisar", str(path), SIGN, name, "--estado", "final"])
    assert path.read_bytes() == before and db.report_reviews(_conn()) == []
    with pytest.raises(db.RegistryError):
        db.add_report_review(_conn(), "x.md", chk.review_digest(before.decode("utf-8")), name)
    assert db.report_reviews(_conn()) == []


def test_many_claims_in_one_paragraph_are_checked_quickly():
    # Regresión de la ronda 1: el emparejamiento del Anexo A no crece con el cubo del número de afirmaciones
    import time
    n = 400
    body = "\n".join(f"La colina protegida aumentó la leche {i},2 kg/d en vacas del grupo {i} [PUB] [R1]."
                     for i in range(n))
    rows = "\n".join(f"| R1 | La colina protegida aumentó la leche {i},2 kg/d en vacas del grupo {i} | respalda | x |"
                     for i in range(n))
    started = time.perf_counter()
    errors = _errors(_strict_doc(body, rows), require_sections=False)
    assert time.perf_counter() - started < 20
    assert not [e for e in errors if "respaldo" in e or "Anexo A" in e]


# ============================================================================ ronda 3: revisión de la ronda 2
REDTEAM = """# Revisión adversarial de research/informes/O-0001.md

Fecha: 2026-11-05. Refutadores: fuentes, biología y viabilidad [SUP].

| Afirmación | Resultado | Motivos por enfoque | Acción |
|---|---|---|---|
| La colina protegida aumentó la producción de leche [PUB] [R1] | sostenida | fuentes: el resumen lo dice | ninguna |
| La misma dosis redujo el BHB plasmático [PUB] [R2] | no_verificable | fuentes: el resumen no da el BHB | pedir texto |

## Bibliografía

""" + BIB + "\n"
VERIFICATION_NOTE = """# Verificación de citas de research/informes/O-0001.md

| Ref | Afirmación | Respaldo | Motivo |
|---|---|---|---|
| R1 | La colina protegida aumentó la producción de leche en vacas en transición 1,2 kg/d | respalda | Tabla 2 |

## Bibliografía

""" + BIB + "\n"


def test_review_documents_keep_their_verdict_tables(project):
    # Regresión de la ronda 2: la crítica de red-team-auto (veredictos «no_verificable» con [Rn]) y una nota de
    # verificación con la columna Respaldo son documentos legítimos de research/revisiones; en ellos esas tablas no
    # son «tablas de respaldo fuera del Anexo A». Esa crítica es la que exige `revision_critica`.
    hooks = _hook_project(project)
    for name, text in (("O-0001_redteam.md", REDTEAM), ("O-0001_verificacion.md", VERIFICATION_NOTE)):
        path = project / "research" / "revisiones" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf-8")
        assert _errors(text, require_sections=False, conn=_conn(), path=path) == [], name
        assert _run_hook(hooks, _payload(path)) is None, name
    # en un informe, y en cualquier documento con un Anexo A, siguen siendo un error
    with_annex = REDTEAM + "\n## Anexo A. Respaldo\n\n" + ANNEX_HEADER
    assert any("valoración de respaldo negativa fuera del Anexo A" in e
               for e in _errors(with_annex, require_sections=False))
    report = project / "research" / "informes" / "nota.md"
    assert any("Tabla de respaldo (columna Respaldo) fuera del Anexo A" in e
               for e in _errors("---\nestado: borrador\n---\n" + VERIFICATION_NOTE, require_sections=False,
                                path=report))


@pytest.mark.parametrize("body, what", [
    # citas autor-año con apellidos compuestos, partículas, entradillas y formas que la ronda 2 no veía
    ("La colina reduce la cetosis subclínica (García Pérez, 2018).", "Cita autor-año"),
    ("La colina reduce la cetosis subclínica (Fernández García y López Sánchez, 2015).", "Cita autor-año"),
    ("La fibra no cambia con la colina (van Soest, 1994).", "Cita autor-año"),
    ("La fibra no cambia con la colina (Van Soest, 1994).", "Cita autor-año"),
    ("La colina reduce la cetosis subclínica (de Vries, 2018).", "Cita autor-año"),
    ("La colina reduce la cetosis subclínica (da Silva, 2017).", "Cita autor-año"),
    ("La colina reduce la cetosis subclínica (Le Roux, 2016).", "Cita autor-año"),
    ("El modelo mixto es el método de referencia (St. Pierre, 2001).", "Cita autor-año"),
    ("La colina reduce la cetosis subclínica (revisado por García, 2018).", "Cita autor-año"),
    ("La colina reduce la cetosis subclínica (citado en García, 2018).", "Cita autor-año"),
    ("La colina reduce la cetosis subclínica (tomado de la revisión de García, 2018).", "Cita autor-año"),
    ("García y otros (2018) demostraron que la colina reduce la cetosis subclínica.", "Cita autor-año"),
    ("La colina reduce la cetosis subclínica (Smith, Jones, & Brown, 2019).", "Cita autor-año"),
    ("La colina reduce la cetosis subclínica (García, en prensa).", "Cita autor-año"),
    ("García (com. pers.) observó que la colina reduce la cetosis subclínica.", "Cita autor-año"),
    ("La colina reduce la cetosis subclínica (García, 2018: 23).", "Cita autor-año"),
    ("En 2018, García demostró que la colina reduce la cetosis subclínica.", "Cita autor-año"),
    ("García demostró en 2018 que la colina reduce la cetosis subclínica.", "Cita autor-año"),
    ("Un ensayo publicado en 2018 por García mostró que la colina reduce la cetosis.", "Cita autor-año"),
    ("La EFSA publicó en 2020 que la colina es segura en vacas lecheras.", "Cita autor-año"),
    ("García y col. demostraron que la colina reduce la cetosis subclínica.", "Cita autor-año"),
    ("Otros trabajos:\n\n- Pérez M, López A. 2019. Betaína en vacas. Animal 13:5-9.", "Cita autor-año"),
    ("La colina reduce la cetosis[^1] [SUP].\n\n[^1]: García J, Pérez M. 2018. Colina. J Dairy Sci 101:1-10.",
     "Cita autor-año"),
    # identificadores y enlaces de una fuente en el cuerpo
    ("La colina reduce la cetosis subclínica (doi:10.9999/doi.inventado.2021.001).", "Identificador de una fuente"),
    ("La colina reduce la cetosis según [un ensayo](https://doi.org/10.9999/doi.inventado.2021.001).",
     "Identificador de una fuente"),
    ("La colina reduce la cetosis subclínica (PMID: 31234567).", "Identificador de una fuente"),
    ("La colina reduce la cetosis (https://pubmed.ncbi.nlm.nih.gov/31234567/).", "Identificador de una fuente"),
    ("La colina reduce la cetosis (https://www.revista.org/article/S0022-0302(20)30001-1/fulltext).",
     "Identificador de una fuente"),
    # citas numéricas y referencias mal escritas
    ("La colina reduce la cetosis subclínica [3, 4].", "Cita numérica «[3, 4]»"),
    ("La colina reduce la cetosis subclínica [1-3].", "Cita numérica «[1-3]»"),
    ("La colina reduce la cetosis subclínica [1,2].", "Cita numérica «[1,2]»"),
    ("La colina reduce la cetosis subclínica (ref. 3).", "Cita numérica «(ref. 3)»"),
    ("La colina reduce la cetosis subclínica (R1, R2).", "escribe [R1] [R2]"),
    ("La colina reduce la cetosis subclínica (R1-R3).", "escribe [R1] [R3]"),
    ("Según R1, la colina reduce la cetosis subclínica.", "sin corchetes"),
])
def test_round2_citation_forms_are_errors_even_in_draft(body, what, tmp_path):
    # D3-4 (ronda 2): formas naturales que pasaban también en la base y en la ronda 1. Son error en final y, en los
    # informes, también en borrador.
    assert any(what in e for e in _body_errors(body)), body
    report = tmp_path / "research" / "informes" / "nota.md"
    text = _strict_doc(CLAIM_A + "\n" + CLAIM_B + "\n\n" + body, ANNEX).replace("estado: final", "estado: borrador")
    report_errors = [str(i) for i in chk.check_report(text, require_sections=False, path=report)
                     if i.severity == "error" and i.code != chk.REVIEW_CODE]
    assert any(what in e for e in report_errors), body
    # En otros borradores (un plan, una crítica: aquí, un documento sin ruta) los identificadores, «según R1», las
    # listas o intervalos entre corchetes y los patrones débiles (verbo de cita, líneas con forma de referencia) son
    # aviso: pueden ser notas del equipo, escalas o rangos (fase de coordinación; el hook no bloquea esos borradores).
    issues = _body_issues(body, status="borrador")
    errors = [str(i) for i in issues if i.severity == "error"]
    if what in ("Identificador de una fuente", "sin corchetes") or body in _WEAK_IN_OTHER_DRAFTS:
        assert not any(what in e for e in errors), body
        assert any(what in str(i) and i.severity == "aviso" for i in issues), body
    else:
        assert any(what in e for e in errors), body


_WEAK_IN_OTHER_DRAFTS = {
    "En 2018, García demostró que la colina reduce la cetosis subclínica.",
    "García demostró en 2018 que la colina reduce la cetosis subclínica.",
    "Un ensayo publicado en 2018 por García mostró que la colina reduce la cetosis.",
    "La EFSA publicó en 2020 que la colina es segura en vacas lecheras.",
    "Otros trabajos:\n\n- Pérez M, López A. 2019. Betaína en vacas. Animal 13:5-9.",
    "La colina reduce la cetosis[^1] [SUP].\n\n[^1]: García J, Pérez M. 2018. Colina. J Dairy Sci 101:1-10.",
    "La colina reduce la cetosis subclínica [3, 4].",
    "La colina reduce la cetosis subclínica [1-3].",
    "La colina reduce la cetosis subclínica [1,2].",
}


@pytest.mark.parametrize("body, what", [
    ("La colina reduce la cetosis subclínica (1).", "Posible cita numérica «(1)»"),
    ("La colina reduce la cetosis subclínica (1, 2).", "Posible cita numérica «(1, 2)»"),
    ("La colina reduce la cetosis subclínica¹.", "Número volado «^1»"),
    ("La colina reduce la cetosis subclínica.²", "Número volado «^2»"),
    ("La colina reduce la cetosis subclínica<sup>1</sup>.", "Número volado «^1»"),
    # cifras: tablas, HTML, entidades, notación científica y unidades nuevas
    ("| Variable | Control | Colina |\n|---|---|---|\n| Leche, semana 4 (kg/d) | 30,1 | 32,4 |", "Cifra"),
    ("| Variable | Control | Colina |\n|---|---|---|\n| Leche (kg/d), semana 8 | 30,1 | 32,4 |", "Cifra"),
    ("| Variable | Efecto |\n|---|---|\n| Leche | +2,5 (kg/d) |", "Cifra"),
    ("| Variable | Efecto |\n|---|---|\n| Leche (por vaca y día, kg) | 2,5 |", "Cifra"),
    ("| Leche | +2,5 (kg/d) |", "Cifra"),
    ("<table><tr><td>Leche</td><td>2,5 kg/d</td></tr></table>", "Cifra"),
    ("La producción de leche aumentó:<br>2,5 kg/d en multíparas.", "Cifra"),
    ("La producción de leche aumentó 2,5&#8203;kg/d en multíparas.", "Cifra"),
    ("El producto aporta 1,2e6 UFC/g de levadura viva.", "Cifra"),
    ("El producto aporta 10⁶ UFC/g de levadura viva.", "Cifra"),
    ("Cada vaca necesita 2,5 m² de cama.", "Cifra"),
    ("La dosis fue de 50 µL por vaca.", "Cifra"),
    ("La leche aumentó 5,5 lb/d con la colina.", "Cifra"),
    ("El margen subió EUR 20 por vaca y año.", "Cifra"),
    ("El riesgo de cetosis bajó con la colina (OR = 0,45).", "Cifra"),
    ("El modelo explicó la respuesta a la colina (R² = 0,91).", "Cifra"),
])
def test_round2_figure_and_numeric_forms_are_errors_in_final(body, what):
    assert any(what in e for e in _body_errors(body)), body
    assert any(what in str(i) and i.severity == "aviso" for i in _body_issues(body)), body


@pytest.mark.parametrize("body", [
    "Los animales del Grupo A (2019) recibieron la dieta basal [SUP].",
    "El proyecto se alinea con los objetivos de sostenibilidad (Agenda 2030) [SUP].",
    "El proyecto se alinea con la política agraria común (PAC, 2023) [SUP].",
    "El censo se tomó de la estadística oficial (en España, 2019) [SUP].",
    "Se observó en 2019 un aumento de los costes de la ración [SUP].",
    "El IC 95 % fue [1,2; 3,4] kg/d [DER] (cálculo: nutria meta analizar).",
    "El intervalo fue [1,2-3,4] kg/d [DER] (cálculo: nutria meta analizar).",
    "Cada criterio se puntúa en una escala [1, 5] [SUP].",
    "La dosis exploratoria está en [2, 4] g/d [HIP].",
    "La degradabilidad efectiva se calcula con la ecuación (3).",
    "Se compararon la dieta (1) y la dieta (2).",
    "El número de ensayos incluidos fue doce (12).",
    "## Hallazgos confirmados (40)\n\nTexto de la sección [SUP].",
    "Se informa tau², I² y el intervalo de predicción [SUP].",
    "El coeficiente de determinación (R²) se calculó con el modelo mixto [DER] (cálculo: nutria meta stpierre).",
    "El calcio iónico (Ca²⁺) es un mensajero intracelular [SUP].",
    "La consulta usa `www.ebi.ac.uk` [SUP].",
    "La alegación sigue el [Reglamento (CE) 767/2009](https://eur-lex.europa.eu/eli/reg/2009/767/oj) [SUP].",
    "La colina es un nutriente esencial[^2] [SUP].\n\n[^2]: Datos de la campaña 2019 [SUP].",
    "Regiones del estudio [SUP]:\n\n- Galicia 2019: 120 explotaciones [SUP].\n- Asturias 2020: 80 explotaciones [SUP].",
    "Ensayos [SUP]:\n\n- Ensayo A 2019: dieta basal [SUP].\n- Tratamiento B 2020: colina [SUP].",
])
def test_round3_legitimate_forms_are_not_flagged(body):
    assert _body_errors(body) == [], body


def test_body_identifiers_are_errors_in_reports_and_other_links_warnings(tmp_path):
    report = tmp_path / "research" / "informes" / "nota.md"
    plan = tmp_path / "research" / "planes" / "plan.md"
    doi = "La colina reduce la cetosis (doi:10.9999/doi.inventado.2021.001) [SUP]."
    text = _draft(doi)
    assert any("Identificador de una fuente" in e for e in _errors(text, require_sections=False, path=report))
    plan_issues = chk.check_report(text, require_sections=False, path=plan)
    assert any("Identificador de una fuente" in str(i) and i.severity == "aviso" for i in plan_issues)
    in_code = _draft("```\nnutria fuente agregar --doi 10.1234/abc\n```")
    assert not any("Identificador" in e for e in _errors(in_code, require_sections=False, path=report))
    link = _draft("Los datos del censo están en https://www.ine.es/censo [SUP].")
    issues = chk.check_report(link, require_sections=False, path=report)
    assert any("Enlace en el cuerpo" in str(i) and i.severity == "aviso" for i in issues)
    assert not any("Enlace" in str(i) and i.severity == "error" for i in issues)


def test_round2_evasions_block_review_and_stamp(project, online, capsys):
    # D3-4 de punta a punta: con estas formas, `informe revisar` se niega y el sello no vale (antes, «Sello válido»
    # con «2 de 2 referencias verificadas» aunque el cuerpo citara un DOI inventado).
    path = _filled_report(project)
    base = path.read_text("utf-8")
    variants = {
        "(García Pérez, 2018)": "La colina reduce la cetosis subclínica (García Pérez, 2018).",
        "Identificador de una fuente": "Un ensayo posterior confirmó que la colina reduce a la mitad la cetosis "
                                       "(doi:10.9999/doi.inventado.2021.001).",
        "Cifra": "| Variable | Control | Colina |\n|---|---|---|\n| Leche, semana 4 (kg/d) | 30,1 | 32,4 |",
        "Cita numérica": "La colina elimina la cetosis subclínica [3, 4].",
        "mal escrita": "La colina elimina la cetosis subclínica (R1, R2).",
    }
    for what, extra in variants.items():
        what = "Cita autor-año" if what.startswith("(") else what
        path.write_text(base.replace(CLAIM_B, CLAIM_B + "\n\n" + extra, 1), "utf-8")
        with pytest.raises(SystemExit, match=f"No se registra la revisión(.|\n)*{re.escape(what)}"):
            main(["informe", "revisar", str(path), SIGN, "Ana Pérez", "--estado", "final"])
        assert db.report_reviews(_conn()) == []
        path.write_text(path.read_text("utf-8").replace("estado: borrador", "estado: final"), "utf-8")
        capsys.readouterr()
        main(["informe", "comprobar", str(path), "--online", "--sellar"])
        assert "Sello válido" not in _out(capsys), what
        assert not chk.stamp_status(path)[0], what


def test_claims_citing_a_source_need_their_annex_row_whatever_the_label(project):
    # D3-3 (residuo de la ronda 2): «... [SUP] [R1]» no elude el Anexo A ni el registro de fuentes
    body = "La colina aumenta la leche 2,5 kg/d en vacas lecheras multíparas [SUP] [R1]."
    assert any("Sin comprobación de respaldo de R1 para la afirmación [SUP]" in e for e in _body_errors(body))
    row = "\n| R1 | La colina aumenta la leche 2,5 kg/d en vacas lecheras multíparas | parcial | x |"
    assert not [e for e in _body_errors(body, ANNEX + row) if "respaldo" in e or "Anexo A" in e]
    negative = row.replace("| parcial |", "| no_respalda |")
    assert any("R1 NO respalda la afirmación [SUP]" in e for e in _body_errors(body, ANNEX + negative))
    path = _filled_report(project)
    conn = _conn()
    conn.execute("DELETE FROM sources WHERE doi = ?", (GOOD2,))
    text = path.read_text("utf-8").replace("estado: borrador", "estado: final").replace(
        CLAIM_B, "La fracción absorbible calculada con los datos del ensayo es 0,48 [DER] (cálculo: nutria destino "
                 "ruminal) [R2].", 1)
    assert any("R2" in e and "no está registrada como fuente" in e for e in _errors(text, conn=conn, path=path))


def test_annex_prose_with_a_pipe_is_checked_as_text():
    # D3-3 (regresión de la ronda 2): la prosa con una «|» en medio, separada de la tabla del Anexo A, no es una
    # fila: sus cifras y citas se comprueban; pegada a la tabla es una fila sin referencia (error en final)
    prose = "La colina duplicó la leche: +2,5 kg/d en vacas multíparas (García, 2018; de Vries, 2019) | nota."
    separated = _body_errors("", ANNEX + "\n\n" + prose)
    assert any("Cifra con unidades" in e for e in separated) and any("Cita autor-año" in e for e in separated)
    glued = _body_errors("", ANNEX + "\n" + prose)
    assert any("Fila del Anexo A sin referencia" in e for e in glued)
    assert any("Fila del Anexo A sin referencia" in str(i) and i.severity == "aviso"
               for i in _body_issues("", ANNEX + "\n" + prose))
    assert any("Valoración de respaldo negativa escrita como texto" in e
               for e in _body_errors("", ANNEX + "\n\nR1 no respalda la cetosis | pero sí la leche."))


@pytest.mark.parametrize("opening", ["Así,", "Por ello,", "Dicho esto,", "Tal como se esperaba,", "Esto es,",
                                     "Según el ensayo,", "Tal como indica el ensayo,", "Por ello", "Según este ensayo,",
                                     "Según este ensayo", "Como muestran estos datos,", "Según estos autores,"])
def test_anaphora_connectors_are_not_remissions(opening):
    # D3-3 (falsos positivos de la ronda 2): un conector inicial seguido de la afirmación completa no remite
    claim = f"{opening} la colina protegida aumentó la producción de leche en vacas en transición"
    body = f"Se revisaron doce ensayos controlados publicados entre 2010 y 2023. {claim} [PUB] [R1]."
    assert not any("remite a la frase anterior" in e
                   for e in _body_errors(body, ANNEX + f"\n| R1 | {claim} | respalda | x |")), opening


@pytest.mark.parametrize("body, row", [
    ("La colina aumenta la leche. Un ensayo con 60 vacas Holstein así lo indica [PUB] [R1].",
     "Un ensayo con 60 vacas Holstein así lo indica"),
    ("Se suplementó colina a 60 vacas. Esto, según el ensayo, aumentó la leche en multíparas [PUB] [R1].",
     "Esto, según el ensayo, aumentó la leche en multíparas"),
    ("La colina aumenta la leche. Así, así lo indica el ensayo [PUB] [R1].", "así lo indica el ensayo"),
    ("La colina aumenta la leche. Así lo indica el ensayo [SUP] [R1].", "Así lo indica el ensayo"),
    # una entradilla seguida de coma no oculta la remisión que viene detrás
    ("La colina aumenta la leche. Según el ensayo de campo, esto se confirmó en vacas lecheras multíparas [PUB] [R1].",
     "Según el ensayo de campo, esto se confirmó en vacas lecheras multíparas"),
    ("La colina aumenta la leche. Según este ensayo [PUB] [R1].", "Según este ensayo"),
])
def test_remissions_with_a_source_subject_or_any_cited_label(body, row):
    errors = _body_errors(body, ANNEX + f"\n| R1 | {row} | respalda | x |")
    assert any("remite a la frase anterior" in e for e in errors)


def test_section_14_single_tilde_and_hidden_text_do_not_count(project):
    # D5-11 (ronda 2): el tachado de una tilde (GFM) y los span tachados u ocultos no declaran nada
    path = _filled_report(project, ruta_regulatoria_preliminar="aditivo_zootecnico")
    final = path.read_text("utf-8").replace("estado: borrador", "estado: final")
    assert not [e for e in _errors(final, conn=_conn(), path=path) if "ruta" in e or "compuerta" in e]
    for hidden in ("~no_resuelta~ superada", '<span style="text-decoration: line-through">no_resuelta</span> superada',
                   "<span hidden>no_resuelta</span> superada", '<span style="color:white">no_resuelta</span> superada',
                   '<span style="display: none">no_resuelta</span> superada'):
        text = final.replace("| seguridad | no_resuelta |", f"| seguridad | {hidden} |")
        errors = _errors(text, conn=_conn(), path=path)
        assert any("dice «superada» para la compuerta seguridad" in e for e in errors), hidden
    route_line = re.search(r"(?m)^Ruta regulatoria preliminar del registro: .*$", final).group(0)
    struck = final.replace(route_line, "Ruta regulatoria preliminar: ~`aditivo_zootecnico`~ `materia_prima` [SUP].")
    assert any("declara la ruta regulatoria «materia_prima»" in e for e in _errors(struck, conn=_conn(), path=path))
    approx = final.replace("| seguridad | no_resuelta |", "| seguridad | no_resuelta (~2 semanas y ~3 días) |")
    assert not [e for e in _errors(approx, conn=_conn(), path=path) if "compuerta" in e]


def test_new_patterns_are_fast_on_pathological_input():
    import time
    cases = ["García J, " * 3000 + "2018.", "\n".join(f"[^{i}]: García J, Pérez M. 2018. T." for i in range(2000)),
             "- " + "de la " * 3000 + "García J. 2018.", "(" + " ".join(["García"] * 3000) + ", 2018).",
             "la cetosis (1). " * 3000, "transición¹ " * 3000, ", ".join(["Así"] * 3000) + ", la colina [PUB] [R1]."]
    for body in cases:
        started = time.perf_counter()
        chk.check_report(_draft(body), require_sections=False)
        assert time.perf_counter() - started < 10, body[:40]


# ============================================================================ ronda 3b: revisión del punto de control
@pytest.mark.parametrize("body", [
    # una línea con forma de referencia necesita un punto, una coma o un paréntesis entre los autores y el año
    "Explotaciones [SUP]:\n\n- Cooperativa A 2020: 50 vacas [SUP].\n- Finca B 2021: 80 vacas [SUP].",
    "La colina es un nutriente esencial[^1] [SUP].\n\n[^1]: Datos de Galicia 2019 [SUP].",
    # «en <año> por <nombre>» solo es una cita con un verbo de cita delante
    "El Reglamento (CE) 767/2009 fue adoptado en 2009 por el Parlamento Europeo y el Consejo [SUP].",
    # una unidad al cuadrado tras un paréntesis no es un número volado
    "La varianza entre estudios fue tau² = 0,4 (kg/d)² [DER] (cálculo: nutria meta analizar).",
    # varios números entre paréntesis en la línea: una enumeración (aviso, no error)
    "La cojera se puntuó como leve (1), moderada (2) o grave (3).",
])
def test_round3b_false_positives_are_not_errors(body):
    assert _body_errors(body) == [], body


@pytest.mark.parametrize("body, what", [
    ("Otros trabajos:\n\n- Pérez M, López A. 2019. Betaína en vacas. Animal 13:5-9.", "Cita autor-año"),
    ("Otros trabajos:\n\n- García J. 2018. Colina en vacas. J Dairy Sci 101:1-10.", "Cita autor-año"),
    ("Otros trabajos:\n\n- García, J. (2018) Colina en vacas. J Dairy Sci 101:1-10.", "Cita autor-año"),
    ("La colina reduce la cetosis[^1] [SUP].\n\n[^1]: García, 2018.", "Cita autor-año"),
    ("La colina reduce la cetosis[^1] [SUP].\n\n[^1]: García J, Pérez M. 2018. Colina. J Dairy Sci.", "Cita autor-año"),
    ("El método fue descrito en 1979 por Ørskov y McDonald para estimar la degradabilidad.", "Cita autor-año"),
    ("In 2018, Smith showed that choline reduces ketosis in dairy cows.", "Cita autor-año"),
    ("Smith reported in 2019 that choline reduces ketosis in dairy cows.", "Cita autor-año"),
    ("La colina reduce la cetosis subclínica (1).", "Posible cita numérica «(1)»"),
])
def test_round3b_citations_are_still_errors(body, what):
    assert any(what in e for e in _body_errors(body)), body


def test_round3b_parenthesized_enumeration_is_a_warning_and_a_single_number_an_error():
    body = "La cojera se puntuó como leve (1), moderada (2) o grave (3)."
    issues = _body_issues(body, status="final")
    assert any("Posible cita numérica «(3)»" in str(i) and i.severity == "aviso" for i in issues)
    assert any("Posible cita numérica «(1)»" in e for e in _body_errors("La colina reduce la cetosis (1)."))


def test_round3b_body_identifier_of_a_bibliography_entry_is_a_warning(tmp_path):
    # D3-4: el sello no miente si el identificador del cuerpo es el de una entrada de la bibliografía (que sí se
    # verifica); un identificador que no está en ella sigue siendo un error en los informes, aunque estén en borrador.
    report = tmp_path / "research" / "informes" / "nota.md"
    sici = "10.1016/S0022-0302(20)30001-1"
    bib = f"[R1] Autor A. 2020. Título del trabajo. DOI: 10.1234/abc\n[R2] Autor B. 2020. Otro título. DOI: {sici}"
    for body, ref in (("El texto completo (doi:10.1234/abc) no estaba disponible [SUP] [R1].", "R1"),
                      ("El diseño está en https://doi.org/10.1234/abc [SUP] [R1].", "R1"),
                      (f"El texto completo (doi:{sici}) no estaba disponible [SUP] [R2].", "R2")):
        issues = chk.check_report(_draft(body, bib), require_sections=False, path=report)
        assert not any("Identificador de una fuente" in str(i) and i.severity == "error" for i in issues), body
        assert any("Identificador de una fuente" in str(i) and f"es el de {ref} de la bibliografía" in str(i)
                   and i.severity == "aviso" for i in issues), body
    unknown = _draft("El texto completo (doi:10.9999/doi.inventado.2021.001) no estaba disponible [SUP] [R1].", bib)
    assert any("Identificador de una fuente" in e for e in _errors(unknown, require_sections=False, path=report))


def test_round3b_bibliography_entries_keep_superscripts_as_in_the_metadata():
    # El cuerpo lee «Ca²⁺» como «Ca^2+» (no es «(R2)» ni un número volado), pero el título de la entrada se compara
    # con Crossref y PubMed como antes: «Ca2+».
    bib = "[R1] Autor A. 2020. Ca²⁺ y colina en vacas en transición. J Dairy Sci 103:1-10. DOI: 10.1234/abc"
    text = _draft("La colina eleva el Ca²⁺ sérico en vacas en transición [SUP].", bib)
    entry = chk.bibliography_entries(text)[0]
    assert "Ca2+ y colina" in entry.text and "^" not in entry.text
    assert chk.bibliography_entries(text.replace("Ca²⁺ y", "Ca<sup>2+</sup> y"))[0].text.count("^") == 0
    assert _errors(text, require_sections=False) == []


def test_round3b_motive_quotes_with_superscripts_are_found_in_the_abstract(monkeypatch):
    monkeypatch.setattr(chk, "fetch_abstract",
                        lambda client, entry: ("Choline raised serum Ca²⁺ and milk yield in transition cows.",
                                               "PubMed"))
    claim = "La colina eleva el Ca²⁺ sérico en vacas en transición"
    text = _strict_doc(f"{claim} [PUB] [R1].", f"| R1 | {claim} | respalda | «raised serum Ca²⁺» |")
    issues, details = chk.check_support_quotes(None, text)
    assert issues == [] and [d["estado"] for d in details] == ["en_resumen"]


def test_round3b_stamp_counts_unverified_links_of_the_body(project, online, capsys):
    path = _filled_report(project)
    link = "Texto consolidado del reglamento en https://eur-lex.europa.eu/eli/reg/2009/767/oj [SUP]."
    path.write_text(path.read_text("utf-8").replace(CLAIM_B, CLAIM_B + "\n\n" + link, 1), "utf-8")
    assert main(["informe", "revisar", str(path), SIGN, "Ana Pérez", "--estado", "final"]) == 0
    assert main(["informe", "comprobar", str(path), "--online", "--sellar"]) == 0
    valid, message = chk.stamp_status(path)
    assert valid and "2 de 2 referencia(s) verificadas en línea" in message
    assert "1 enlace(s) del cuerpo sin verificar" in message


def test_round3b_registry_message_names_any_label(project):
    path = _filled_report(project)
    conn = _conn()
    conn.execute("DELETE FROM sources WHERE doi = ?", (GOOD2,))
    text = path.read_text("utf-8").replace(
        CLAIM_B, "La fracción absorbible calculada con los datos del ensayo es 0,48 [DER] (cálculo: nutria destino "
                 "ruminal) [R2].", 1)
    found = [e for e in _errors(text.replace("estado: borrador", "estado: final"), conn=conn, path=path)
             if "no está registrada como fuente" in e]
    assert found and all("[PUB]" not in e and "se cita en una afirmación del informe" in e for e in found)


def test_round3b_font_color_attribute_hides_text_in_section_14(project):
    path = _filled_report(project, ruta_regulatoria_preliminar="aditivo_zootecnico")
    final = path.read_text("utf-8").replace("estado: borrador", "estado: final")
    for hidden in ('<font color="white">no_resuelta</font> superada',
                   "<font color=#ffffff>no_resuelta</font> superada"):
        text = final.replace("| seguridad | no_resuelta |", f"| seguridad | {hidden} |")
        errors = _errors(text, conn=_conn(), path=path)
        assert any("dice «superada» para la compuerta seguridad" in e for e in errors), hidden


def test_round3b_bare_references_are_errors_in_reports_and_warnings_in_other_drafts(tmp_path, project):
    # «según R1» sin corchetes: error en los informes (también en borrador) y en revisado o final; en una crítica o una
    # nota de research/revisiones en borrador, aviso (hablan de las referencias del informe), y el hook no la bloquea.
    body = "Según R1, la dosis del ensayo fue otra [SUP]."
    report = tmp_path / "research" / "informes" / "nota.md"
    review = tmp_path / "research" / "revisiones" / "nota.md"
    assert any("sin corchetes" in e for e in _errors(_draft(body), require_sections=False, path=report))
    issues = chk.check_report(_draft(body), require_sections=False, path=review)
    assert any("sin corchetes" in str(i) and i.severity == "aviso" for i in issues)
    assert not any("sin corchetes" in str(i) and i.severity == "error" for i in issues)
    final = _draft(body).replace("estado: borrador", "estado: final")
    assert any("sin corchetes" in e for e in _errors(final, require_sections=False, path=review))
    hooks = _hook_project(project)
    note = project / "research" / "revisiones" / "O-0001_critica.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(_draft(body), "utf-8")
    assert _run_hook(hooks, _payload(note)) is None
    in_report = _filled_report(project)
    assert _run_hook(hooks, _payload(in_report)) is None                      # el informe en borrador pasa
    in_report.write_text(in_report.read_text("utf-8").replace(CLAIM_B, CLAIM_B + "\n\n" + body, 1), "utf-8")
    decision = _run_hook(hooks, _payload(in_report))
    assert decision["decision"] == "block" and "sin corchetes" in decision["reason"]


def test_round3b_negative_note_in_the_annex_is_a_warning_when_the_table_has_the_row():
    claim = "La misma dosis redujo el BHB plasmático en el posparto temprano"
    rows = ANNEX.replace(f"| R2 | {claim} | parcial | otra dosis |", f"| R2 | {claim} | no_verificable | sin resumen |")
    note = "\n\nR2 no verificable: no hay acceso al texto completo."
    issues = _body_issues("", rows + note, status="borrador")
    assert any("también escrita como texto" in str(i) and i.severity == "aviso" for i in issues)
    assert not any("escrita como texto" in str(i) and i.severity == "error" for i in issues)
    # sin la fila negativa en la tabla, la valoración escrita como texto es un error (no puede sustituir a la fila)
    assert any("Valoración de respaldo negativa escrita como texto" in e for e in _body_errors("", ANNEX + note))


# ============================================================================ fase de coordinación: D3-4 en cerrado
# Bloqueante de la revisión final de P2 (ronda 3): las listas de palabras que no son apellidos se aplicaban a todos los
# patrones de cita, también entre paréntesis y delante de «et al.», y citas con apellidos reales que coinciden con una
# palabra común pasaban. Ahora la cita se reconoce siempre y se clasifica después (`check._citation_kind`).
_COMMON_WORD_SURNAMES = [
    "La colina protegida reduce la cetosis subclínica (Han et al., 2020).",
    "La colina protegida reduce la cetosis subclínica (Han, 2020).",
    "La colina protegida reduce la cetosis subclínica (Han y Kim, 2020).",
    "Han et al. (2020) demostraron que la colina protegida reduce la cetosis subclínica.",
    "Han et al. demostraron que la colina protegida reduce la cetosis subclínica.",
    "Ley et al. (2006) demostraron que la microbiota determina la eficiencia energética.",
    "La microbiota ruminal determina la eficiencia energética de las vacas (Ley et al., 2006).",
    "La colina protegida reduce la cetosis subclínica (Son et al., 2018).",
    "La colina protegida reduce la cetosis subclínica (Raza et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Raza, 2019).",
    "La colina protegida reduce la cetosis subclínica (Toda et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Ha et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Tal et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Ni et al., 2020).",
    "La colina protegida reduce la cetosis subclínica (An et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Lo et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Hay et al., 2016).",
    "La colina protegida reduce la cetosis subclínica (Vaca et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Caso et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Campaña et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Granja et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Bajo et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Plan et al., 2016).",
    "La colina protegida reduce la cetosis subclínica (Bloque, 2016).",
    "La colina protegida reduce la cetosis subclínica (May et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Mayo et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (March y Pérez, 2017).",
    "La colina protegida reduce la cetosis subclínica (Abril et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (August et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Como, 2019).",
    "| Estudio | Especie | Efecto en leche |\n|---|---|---|\n| Han et al. 2020 | vacuno lechero | positivo |",
    "Otros trabajos:\n\n- Han X, Li Y. 2020. Choline in dairy cows. J Dairy Sci 103:1-10.",
]


@pytest.mark.parametrize("body", _COMMON_WORD_SURNAMES)
def test_coord_common_word_surnames_are_errors_in_final_and_warnings_in_draft(body, tmp_path):
    assert any("Cita autor-año" in e for e in _body_errors(body)), body
    assert any("Cita autor-año" in e for e in _body_errors(body, status="revisado")), body
    # en borrador (también en un informe), aviso que dice que en revisado o final será error
    report = tmp_path / "research" / "informes" / "nota.md"
    text = _strict_doc(CLAIM_A + "\n" + CLAIM_B + "\n\n" + body, ANNEX).replace("estado: final", "estado: borrador")
    issues = [i for i in chk.check_report(text, require_sections=False, path=report) if "Cita autor-año" in str(i)]
    assert issues and all(i.severity == "aviso" for i in issues), body
    assert any("en revisado o final es error" in str(i) for i in issues), body


@pytest.mark.parametrize("body", [
    "La colina protegida reduce la cetosis subclínica (Şahin et al., 2019).",
    "La colina protegida reduce la cetosis subclínica (Čermák, 2018).",
    "Łukaszewicz et al. (2020) demostraron que la colina protegida reduce la cetosis subclínica.",
    "La colina protegida reduce la cetosis subclínica (Šustala y Novák, 2017).",
    "La colina protegida reduce la cetosis subclínica (İbrahim, 2019).",
    "La colina protegida reduce la cetosis subclínica (Đorđević et al., 2016).",
    "La colina protegida reduce la cetosis subclínica (Ōta, 2018).",
    "La colina protegida reduce la cetosis subclínica (Śliwiński et al., 2002).",
    "La colina protegida reduce la cetosis subclínica (Kim y Han, 2020).",
    "La colina protegida reduce la cetosis subclínica (de los Santos, 2019).",
    "La colina protegida reduce la cetosis subclínica (De las Heras et al., 2019).",
    "Como demostraron García y Pérez en 2018, la colina protegida reduce la cetosis subclínica.",
    "García, 2018, demostró que la colina protegida reduce la cetosis subclínica.",
    "García 2018 demostró que la colina protegida reduce la cetosis subclínica.",
])
def test_coord_unicode_initials_particles_and_verb_order_are_citations(body, tmp_path):
    # iniciales fuera de Latin-1, «de los»/«de las» y el verbo de cita delante o el año sin «en»: error en final y,
    # en los informes, también en borrador
    assert any("Cita autor-año" in e for e in _body_errors(body)), body
    report = tmp_path / "research" / "informes" / "nota.md"
    text = _strict_doc(CLAIM_A + "\n" + CLAIM_B + "\n\n" + body, ANNEX).replace("estado: final", "estado: borrador")
    assert any("Cita autor-año" in str(i) and i.severity == "error"
               for i in chk.check_report(text, require_sections=False, path=report)), body


@pytest.mark.parametrize("body, what", [
    ("La colina protegida reduce la cetosis subclínica en vacas en transición [1,2,5-7].", "Cita numérica «[1,2,5-7]»"),
    ("La colina protegida reduce la cetosis subclínica en vacas en transición [2,4-6].", "Cita numérica «[2,4-6]»"),
    ("La colina protegida reduce la cetosis subclínica en vacas en transición (ver R1).", "«ver R1» sin corchetes"),
    ("La colina protegida reduce la cetosis subclínica en vacas en transición [@garcia2018].",
     "Cita con clave «[@garcia2018]»"),
    ("La colina protegida reduce la cetosis subclínica [véase @garcia2018, p. 3].", "Cita con clave"),
])
def test_coord_vancouver_lists_bare_ver_and_pandoc_keys_are_errors(body, what, tmp_path):
    assert any(what in e for e in _body_errors(body)), body
    report = tmp_path / "research" / "informes" / "nota.md"
    text = _strict_doc(CLAIM_A + "\n" + CLAIM_B + "\n\n" + body, ANNEX).replace("estado: final", "estado: borrador")
    assert any(what in str(i) and i.severity == "error"
               for i in chk.check_report(text, require_sections=False, path=report)), body


@pytest.mark.parametrize("body", [
    # una palabra que no puede ser un apellido en esa posición
    "El proyecto se alinea con los objetivos de sostenibilidad (Agenda 2030) [SUP].",
    "El proyecto se financia con el programa europeo (Horizonte 2020) [SUP].",
    "El proyecto se alinea con la política agraria común (PAC, 2023) [SUP].",
    "Los precios de la leche son los de la campaña anterior (Desde 2019) [SUP].",
    "El precio de referencia es el del último informe de mercado (May 2019) [SUP].",
    "Se observó en 2019 un aumento de los costes de la ración [SUP].",
    "Han observado en 2019 un aumento de los costes de la ración [SUP].",
    "Esto indica en 2020 un cambio de tendencia en los costes de la ración [SUP].",
    # el futuro no atribuye un resultado a nadie
    "En 2027, Ana Pérez evaluará la dosis con el equipo de campo [SUP].",
    # intervalos con decimales
    "El IC 95 % fue [1,2; 3,4] kg/d [DER] (cálculo: nutria meta analizar).",
    "El intervalo fue [1,2-3,4] kg/d [DER] (cálculo: nutria meta analizar).",
    # una dirección de correo entre corchetes no es una clave de pandoc
    "Contacto del equipo [ana@example.org] [SUP].",
])
def test_coord_words_that_cannot_be_surnames_are_not_flagged(body):
    assert not [e for e in _body_errors(body) if "Cita" in e or "Referencia" in e], body


@pytest.mark.parametrize("kind", ["planes", "revisiones"])
def test_coord_hook_warns_but_does_not_block_legitimate_drafts(project, kind):
    # Revisión final de P2 (no bloqueante): las reglas nuevas de «error siempre» bloqueaban borradores legítimos de
    # research/planes y research/revisiones. En borrador, aviso; en final, error.
    body = ("La condición corporal [1-5] se medirá cada semana en todas las vacas [SUP].\n\n"
            "Se tomarán muestras de sangre en las semanas [2, 4] tras el parto [SUP].\n\n"
            "El precio seguirá una distribución triangular [1, 2, 3] en la simulación [SUP].\n\n"
            "En 2026, Ana Pérez evaluó la oportunidad y propuso descartarla [SUP].\n\n"
            "En 2022, la Comisión propuso revisar el Reglamento (CE) 1831/2003 [SUP].\n\n"
            "Equipo [SUP]:\n\n- Pérez A., 2026: coordinación [SUP].\n- López M., 2026: estadística [SUP].\n\n"
            "La colina protegida reduce la cetosis subclínica (Han et al., 2020).")
    hooks = _hook_project(project)
    target = project / "research" / kind / "borrador.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_draft(body), "utf-8")
    assert _run_hook(hooks, _payload(target)) is None
    issues = chk.check_report(target.read_text("utf-8"), require_sections=False, path=target)
    assert not [i for i in issues if i.severity == "error"]
    assert sum("Cita numérica" in str(i) for i in issues) == 3
    assert sum("Cita autor-año" in str(i) for i in issues) >= 4
    final = _draft(body).replace("estado: borrador", "estado: final")
    errors = _errors(final, require_sections=False, path=target)
    assert sum("Cita numérica" in e for e in errors) == 3
    assert sum("Cita autor-año" in e for e in errors) >= 4


def test_coord_the_same_draft_in_a_report_is_still_blocked(project):
    hooks = _hook_project(project)
    target = project / "research" / "informes" / "nota.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_draft("En 2018, García demostró que la colina reduce la cetosis subclínica."), "utf-8")
    decision = _run_hook(hooks, _payload(target))
    assert decision and decision["decision"] == "block" and "Cita autor-año" in decision["reason"]


def test_coord_review_and_stamp_refuse_common_word_surnames_and_unicode_initials(project, online, capsys):
    # De punta a punta (p_e2e_r3.py del revisor): con estas citas sin [Rn], `informe revisar` se niega y el sello no
    # vale (antes: revisar=0, comprobar=0 y «Sello válido ... 2 de 2»).
    path = _filled_report(project)
    base = path.read_text("utf-8")
    for extra in ("La colina reduce la cetosis subclínica (Han et al., 2020).",
                  "Ley et al. (2006) demostraron que la microbiota determina la eficiencia energética.",
                  "La colina reduce la cetosis subclínica (Son et al., 2018).",
                  "La colina reduce la cetosis subclínica (Raza et al., 2019).",
                  "La colina reduce la cetosis subclínica (Şahin et al., 2019).",
                  "La colina reduce la cetosis subclínica [1,2,5-7]."):
        path.write_text(base.replace(CLAIM_B, CLAIM_B + "\n\n" + extra, 1), "utf-8")
        with pytest.raises(SystemExit, match="No se registra la revisión"):
            main(["informe", "revisar", str(path), SIGN, "Ana Pérez", "--estado", "final"])
        assert db.report_reviews(_conn()) == []
        path.write_text(path.read_text("utf-8").replace("estado: borrador", "estado: final"), "utf-8")
        capsys.readouterr()
        main(["informe", "comprobar", str(path), "--online", "--sellar"])
        assert "Sello válido" not in _out(capsys), extra
        assert not chk.stamp_status(path)[0], extra


def test_coord_identifiers_in_annexes_b_and_c_are_warnings_counted_in_the_stamp(project, online, capsys):
    # p_annexb_doi.py del revisor: un DOI o un enlace de PubMed inventados en los anexos B o C daban «Sello válido
    # ... 2 de 2 referencia(s) verificadas ... 0 aviso(s)». Ahora son aviso y el sello los cuenta.
    path = _filled_report(project)
    text = path.read_text("utf-8")
    text = text.replace("## Anexo B. Registro de decisiones", "## Anexo B. Registro de decisiones\n\nSe decidió seguir "
                        "porque un ensayo posterior lo confirmó (doi:10.9999/doi.inventado.2021.001).", 1)
    text = text.replace("## Anexo C. Procedencia y versiones", "## Anexo C. Procedencia y versiones\n\nConfirmado en "
                        "https://pubmed.ncbi.nlm.nih.gov/39999999/.", 1)
    path.write_text(text, "utf-8")
    issues = chk.check_report(text, require_sections=True, path=path)
    annex = [i for i in issues if "en el Anexo B o C" in str(i)]
    assert len(annex) == 2 and all(i.severity == "aviso" for i in annex)
    assert main(["informe", "revisar", str(path), SIGN, "Ana Pérez", "--estado", "final"]) == 0
    assert main(["informe", "comprobar", str(path), "--online", "--sellar"]) == 0
    valid, message = chk.stamp_status(path)
    assert valid and "2 identificador(es) en los anexos B o C sin verificar" in message
    assert "0 aviso(s)" not in message
