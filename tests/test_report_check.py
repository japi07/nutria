from nutria.reports.check import check_report, stamp_status, write_stamp

SECTIONS = "\n".join(f"## {n}. Sección {n}\n" for n in range(1, 20))


def make(body: str, bib: str) -> str:
    return f"# Informe\n{SECTIONS}\n{body}\n## 20. Bibliografía científica verificable\n{bib}\n"


def test_detects_unreferenced_pub_and_missing_entries():
    text = make("El efecto fue positivo [PUB].\nSegún [R2] [PUB] hubo cambios.\n",
                "[R1] Autor. 2020. Título. DOI: 10.1/abc\n")
    msgs = [str(i) for i in check_report(text)]
    assert any("[PUB] sin referencia" in m for m in msgs)
    assert any("R2 se cita pero no está" in m for m in msgs)
    assert any("R1 está en la bibliografía pero no se cita" in m for m in msgs)


def test_entry_without_identifier_is_error_and_missing_section():
    text = "# x\n## 1. Título\n" + "[PUB] dato [R1]\n## 20. Bibliografía\n[R1] Autor. 2020. Sin identificador.\n"
    errors = [str(i) for i in check_report(text) if i.severity == "error"]
    assert any("sin DOI, PMID ni URL" in e for e in errors)
    assert any("Falta la sección 2" in e for e in errors)


def test_clean_report_passes():
    text = make("La respuesta fue de 1,2 kg/d [PUB] [R1].\n", "[R1] Autor. 2020. Título. PMID: 12345678\n")
    assert [i for i in check_report(text) if i.severity == "error"] == []


def test_stamp_invalidated_by_edit(tmp_path):
    report = tmp_path / "inf.md"
    report.write_text(make("[PUB] dato [R1]", "[R1] A. 2020. T. DOI: 10.1/x"), "utf-8")
    write_stamp(report, [], online=True)
    assert stamp_status(report)[0]
    report.write_text(report.read_text("utf-8") + "\nCambio", "utf-8")
    ok, msg = stamp_status(report)
    assert not ok and "cambiado" in msg
