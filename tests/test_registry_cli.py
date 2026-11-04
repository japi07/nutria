import json
from pathlib import Path

import pytest

from nutria import db
from nutria.cli import main
from nutria.config import load_config

EXAMPLES = Path(__file__).resolve().parents[1] / "ejemplos"


def test_full_offline_flow(project, capsys):
    assert main(["oportunidad", "nueva", "--datos", str(EXAMPLES / "oportunidad_ejemplo.json")]) == 0
    assert main(["hipotesis", "nueva", "--datos", str(EXAMPLES / "hipotesis_ejemplo.json")]) == 0
    assert main(["puntuar", "registrar", "--datos", str(EXAMPLES / "puntuacion_ejemplo.json")]) == 0
    second = json.loads((EXAMPLES / "puntuacion_ejemplo.json").read_text("utf-8"))
    second["evaluador"] = "evaluador-industrial"
    (project / "p2.json").write_text(json.dumps(second), "utf-8")
    assert main(["puntuar", "registrar", "--datos", str(project / "p2.json")]) == 0
    assert main(["puntuar", "compuerta", "O-0001", "seguridad", "no_resuelta", "--motivo", "Datos parciales"]) == 0
    capsys.readouterr()
    assert main(["--json", "puntuar", "ranking", "--sim", "500"]) == 0
    ranking = json.loads(capsys.readouterr().out)
    assert ranking[0]["opportunity_id"] == "O-0001" and ranking[0]["status"] == "CONDICIONAL"
    # cambiar estado exige motivo y queda en el registro de decisiones
    assert main(["hipotesis", "estado", "H-0001", "refutada", "--motivo", "Degradación ruminal completa"]) == 0
    conn = db.connect(load_config().db_path)
    decisions = db.list_decisions(conn, "H-0001")
    assert decisions[-1]["decision"] == "estado -> refutada"
    # informe: esqueleto y comprobación estructural sin red
    assert main(["informe", "nuevo", "O-0001"]) == 0
    report = project / "research" / "informes" / "O-0001.md"
    assert report.is_file()
    assert main(["informe", "comprobar", str(report)]) == 0
    assert (project / "research" / "procedencia.jsonl").is_file()


def test_invalid_json_is_rejected(project):
    bad = project / "bad.json"
    bad.write_text(json.dumps({"titulo": "x"}), "utf-8")
    with pytest.raises(SystemExit):
        main(["oportunidad", "nueva", "--datos", str(bad)])


def test_pub_claim_requires_registered_source(project):
    f = project / "a.json"
    f.write_text(json.dumps({"texto": "Afirmación publicada", "etiqueta": "PUB", "fuentes": ["doi:10.1/nope"]}), "utf-8")
    assert main(["afirmacion", "agregar", "--datos", str(f)]) == 3
