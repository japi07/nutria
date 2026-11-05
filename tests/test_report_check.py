import json
from pathlib import Path

import pytest

from nutria import db
from nutria.reports import check as rc
from nutria.reports.check import check_report, review_digest, stamp_status, verify_report, write_stamp
from nutria.verify.citations import Verification, extract_identifiers

GATES = ("seguridad", "regulatoria", "analitica", "propiedad_intelectual", "destino_digestivo", "etica")
SECTION_TEXT = {
    7: ("Destino digestivo en vacuno: escape ruminal [DESC] (se medirá in vitro) y digestibilidad intestinal [DESC] "
        "(se medirá in situ).\n"),
    14: ("Ruta regulatoria preliminar: aditivo_zootecnico [SUP].\n\n| Compuerta | Estado |\n|---|---|\n"
         + "\n".join(f"| {g} | no_resuelta |" for g in GATES) + "\n\nAlegación prevista: ninguna.\n"),
}
SECTIONS = "\n".join(f"## {n}. Sección {n}\n" + SECTION_TEXT.get(n, "Texto [HIP].\n") for n in range(1, 20))
CLAIM = "La respuesta fue de 1,2 kg/d en vacas lecheras [PUB] [R1]."
ANEXO_OK = ("| Ref | Afirmación | Respaldo | Motivo |\n|---|---|---|---|\n"
            "| R1 | La respuesta fue de 1,2 kg/d en vacas lecheras | respalda | Tabla 2 |\n")
BIB = "[R1] Autor. 2020. Título. DOI: 10.1234/x\n"
EXAMPLES = Path(__file__).resolve().parents[1] / "ejemplos"


@pytest.fixture(autouse=True)
def stamp_key(tmp_path, monkeypatch):
    monkeypatch.setenv("NUTRIA_CLAVE_SELLO", str(tmp_path / "clave"))


def make(body: str, bib: str, estado: str = "borrador", humano: str = "null", anexo: str = "") -> str:
    return (f"---\noportunidad: O-0001\nestado: {estado}\nespecie: vacuno lechero\nrevisado_por_humano: {humano}\n"
            "---\n# Informe\n"
            f"{SECTIONS}\n{body}\n## 20. Bibliografía científica verificable\n{bib}\n## Anexo A. Tabla\n{anexo}\n")


def _errors(text: str, **kw) -> list[str]:
    return [str(i) for i in check_report(text, **kw) if i.severity == "error"]


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


def test_strict_mode_requires_support_and_human_review(tmp_path):
    # D3-3 / D3-6: respaldo por afirmación y revisión humana registrada para el contenido exacto
    text = make(CLAIM, BIB, estado='"Final"')
    errors = _errors(text)
    assert any("respaldo de R1" in e for e in errors)
    assert any("revisión humana" in e for e in errors)
    conn = db.connect(tmp_path / "registro.sqlite")
    db.upsert_source(conn, {"doi": "10.1234/x", "title": "t", "verified": True, "retracted": False})
    opportunity = json.loads((EXAMPLES / "oportunidad_ejemplo.json").read_text("utf-8"))
    assert db.add_opportunity(conn, opportunity) == "O-0001"       # sus compuertas, sin evaluar: no_resuelta
    ok = make(CLAIM, BIB, estado="final", humano="Ana Pérez", anexo=ANEXO_OK)
    assert any("no corresponde a ninguna revisión registrada" in e for e in _errors(ok, conn=conn))
    db.add_report_review(conn, "inf.md", review_digest(ok), "Ana Pérez", "final")
    assert _errors(ok, conn=conn) == []
    refuted = ok.replace("| respalda |", "| no_respalda |")
    assert any("NO respalda" in e for e in _errors(refuted, conn=conn))


def _verify(report, monkeypatch, complete=True, structure=True):
    def fake_verify_entry(client, entry):
        return [Verification(d, "doi", "verificada" if complete else "error_red", title="t", year=2020,
                             retracted=False if complete else None) for d in extract_identifiers(entry)["doi"]]
    monkeypatch.setattr(rc, "verify_entry", fake_verify_entry)
    monkeypatch.setattr(rc, "fetch_abstract", lambda client, entry: (None, "sin red"))
    return verify_report(report, client=object(), require_sections=structure)


def test_stamp_requires_complete_online_verification(tmp_path, monkeypatch):
    report = tmp_path / "informes" / "inf.md"
    report.parent.mkdir()
    report.write_text(make(CLAIM, BIB, anexo=ANEXO_OK), "utf-8")
    write_stamp(_verify(report, monkeypatch, complete=False))        # p. ej. sin red: referencias no verificadas
    ok, msg = stamp_status(report)
    assert not ok and "incompleta" in msg
    write_stamp(_verify(report, monkeypatch, complete=True))
    assert stamp_status(report)[0]
    write_stamp(_verify(report, monkeypatch, structure=False))       # sin estructura (antes, `verificar --sellar`)
    assert not stamp_status(report)[0]


def test_stamp_invalidated_by_edit_forgery_and_corruption(tmp_path, monkeypatch):
    report = tmp_path / "inf.md"
    text = make(CLAIM, BIB, anexo=ANEXO_OK)
    report.write_text(text, "utf-8")
    write_stamp(_verify(report, monkeypatch))
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


def test_stamp_covers_checked_text_not_later_edits(tmp_path, monkeypatch):
    # D9-6: el sello guarda la huella del texto comprobado; si el fichero cambia durante la verificación, no se sella
    report = tmp_path / "inf.md"
    checked = make(CLAIM, BIB, anexo=ANEXO_OK)
    report.write_text(checked, "utf-8")
    result = _verify(report, monkeypatch)
    report.write_text(checked + "\n[R9] inventada", "utf-8")
    with pytest.raises(rc.StampError):
        write_stamp(result)
    assert not stamp_status(report)[0]


def test_section_14_requires_intended_claim():
    # D5-11: la sección 14 debe recoger la alegación prevista (Reglamento (CE) 767/2009, art. 13)
    no_claim = SECTIONS.replace("Alegación prevista: ninguna.\n", "")
    draft = make("", "").replace(SECTIONS, no_claim)
    issues = [i for i in check_report(draft) if "alegación prevista" in i.message]
    assert issues and issues[0].severity == "aviso"
    final = make("", "", estado="final", humano="Ana").replace(SECTIONS, no_claim)
    issues = [i for i in check_report(final) if "alegación prevista" in i.message]
    assert issues and issues[0].severity == "error"
    assert not [i for i in check_report(make("", "")) if "alegación prevista" in i.message]
