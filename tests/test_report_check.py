import json

import pytest

from nutria.reports import check as rc
from nutria.reports.check import check_report, stamp_status, write_stamp

SECTIONS = "\n".join(f"## {n}. Sección {n}\n" for n in range(1, 20))


@pytest.fixture(autouse=True)
def stamp_key(tmp_path, monkeypatch):
    monkeypatch.setenv("NUTRIA_CLAVE_SELLO", str(tmp_path / "clave"))


def make(body: str, bib: str, estado: str = "borrador", humano: str = "null", anexo: str = "") -> str:
    return (f"---\nestado: {estado}\nrevisado_por_humano: {humano}\n---\n# Informe\n{SECTIONS}\n{body}\n"
            f"## 20. Bibliografía científica verificable\n{bib}\n## Anexo A. Tabla\n{anexo}\n")


ANEXO_OK = "| Ref | Afirmación | Respaldo | Motivo |\n|---|---|---|---|\n| R1 | dato | respalda | leído |\n"


def test_detects_unreferenced_pub_and_missing_entries():
    text = make("El efecto fue positivo [PUB].\nSegún [R2] [PUB] hubo cambios.\n",
                "[R1] Autor. 2020. Título. DOI: 10.1234/abc\n")
    msgs = [str(i) for i in check_report(text)]
    assert any("[PUB] sin referencia" in m for m in msgs)
    assert any("R2 se cita pero no está" in m for m in msgs)
    assert any("R1 está en la bibliografía pero no se cita" in m for m in msgs)


def test_author_year_citation_without_reference_is_error():
    # D3-4
    text = make("La colina mejora la producción (Smith et al., 2019).\n", "[R1] A. 2020. T. PMID: 12345678\n")
    assert any("autor-año" in str(i) for i in check_report(text) if i.severity == "error")


def test_entry_without_identifier_is_error_and_missing_section():
    text = "# x\n## 1. Título\n" + "[PUB] dato [R1]\n## 20. Bibliografía\n[R1] Autor. 2020. Sin identificador.\n"
    errors = [str(i) for i in check_report(text) if i.severity == "error"]
    assert any("sin DOI, PMID ni URL" in e for e in errors)
    assert any("Falta la sección 2" in e for e in errors)


def test_strict_mode_requires_support_and_human_review():
    # D3-3 / D3-6
    text = make("La respuesta fue de 1,2 kg/d [PUB] [R1].\n", "[R1] Autor. 2020. Título. PMID: 12345678\n",
                estado='"Final"')
    errors = [str(i) for i in check_report(text) if i.severity == "error"]
    assert any("respaldo de R1" in e for e in errors)
    assert any("revisión humana" in e for e in errors)
    ok = make("La respuesta fue de 1,2 kg/d [PUB] [R1].\n", "[R1] Autor. 2020. Título. PMID: 12345678\n",
              estado="final", humano="Ana Pérez", anexo=ANEXO_OK)
    assert [i for i in check_report(ok) if i.severity == "error"] == []
    refuted = ok.replace("| respalda |", "| no_respalda |")
    assert any("NO respalda" in str(i) for i in check_report(refuted))


def _stamp(report, text, complete=True, structure=True, errors=None):
    summary = {"total": 1, "verificadas": 1 if complete else 0, "pendientes": 0 if complete else 1,
               "con_error": 0, "completa": complete}
    return write_stamp(report, text, errors or [], online=True, details=[], summary=summary,
                       structure_checked=structure)


def test_stamp_requires_complete_online_verification(tmp_path):
    report = tmp_path / "informes" / "inf.md"
    report.parent.mkdir()
    text = make("[PUB] dato [R1]", "[R1] A. 2020. T. DOI: 10.1234/x", estado="final", humano="Ana", anexo=ANEXO_OK)
    report.write_text(text, "utf-8")
    _stamp(report, text, complete=False)          # p. ej. sin red: referencias no verificadas
    ok, msg = stamp_status(report)
    assert not ok and "incompleta" in msg
    _stamp(report, text, complete=True)
    assert stamp_status(report)[0]
    _stamp(report, text, complete=True, structure=False)   # `verificar --sellar` sin estructura
    assert not stamp_status(report)[0]


def test_stamp_invalidated_by_edit_forgery_and_corruption(tmp_path):
    report = tmp_path / "inf.md"
    text = make("[PUB] dato [R1]", "[R1] A. 2020. T. DOI: 10.1234/x", anexo=ANEXO_OK)
    report.write_text(text, "utf-8")
    _stamp(report, text)
    assert stamp_status(report)[0]
    report.write_text(text + "\nCambio", "utf-8")
    ok, msg = stamp_status(report)
    assert not ok and "cambiado" in msg
    report.write_text(text, "utf-8")
    forged = {"sha256": rc.sha256_text(text), "en_linea": True, "verificacion_completa": True, "errores": 0}
    rc.stamp_path(report).write_text(json.dumps(forged), "utf-8")          # sello escrito a mano (H5)
    assert not stamp_status(report)[0]
    rc.stamp_path(report).write_text("{corrupto", "utf-8")                   # sello corrupto (H1)
    assert not stamp_status(report)[0]


def test_stamp_covers_checked_text_not_later_edits(tmp_path):
    # D9-6: el sello guarda la huella del texto comprobado; si el fichero cambia durante la verificación, no vale
    report = tmp_path / "inf.md"
    checked = make("[PUB] dato [R1]", "[R1] A. 2020. T. DOI: 10.1234/x", anexo=ANEXO_OK)
    report.write_text(checked + "\n[R9] inventada", "utf-8")
    _stamp(report, checked)
    assert not stamp_status(report)[0]
