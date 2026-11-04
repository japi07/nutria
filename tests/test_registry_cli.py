import json
from pathlib import Path

import pytest

from nutria import db
from nutria.cli import main
from nutria.config import load_config

EXAMPLES = Path(__file__).resolve().parents[1] / "ejemplos"
GATES = ["seguridad", "regulatoria", "analitica", "propiedad_intelectual", "destino_digestivo", "etica"]
SIGN = "--firma-humana"


def _conn():
    return db.connect(load_config().db_path)


def _verified_source(doi="10.1234/prueba.registro", retracted=False):
    return db.upsert_source(_conn(), {"doi": doi, "title": "Fuente de prueba", "year": 2020, "verified": True,
                                      "verified_via": "prueba", "retracted": retracted})


def _three_raters(project):
    base = json.loads((EXAMPLES / "puntuacion_ejemplo.json").read_text("utf-8"))
    for rater in ("evaluador-a", "evaluador-b", "evaluador-c"):
        base["evaluador"] = rater
        (project / f"{rater}.json").write_text(json.dumps(base), "utf-8")
        assert main(["puntuar", "registrar", "--datos", str(project / f"{rater}.json")]) == 0


def test_gates_need_human_signature_and_ranking_respects_it(project, capsys):
    assert main(["oportunidad", "nueva", "--datos", str(EXAMPLES / "oportunidad_ejemplo.json")]) == 0
    _three_raters(project)
    # propuestas de agentes (sin firma): no cuentan como superadas
    for gate in GATES:
        assert main(["--agente", "regulacion-seguridad", "puntuar", "compuerta", "O-0001", gate, "superada",
                     "--motivo", "propuesta"]) == 0
    capsys.readouterr()
    assert main(["--json", "puntuar", "ranking", "--sim", "300"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "CONDICIONAL"
    # una persona firma: pasa a APTA
    for gate in GATES:
        assert main(["puntuar", "compuerta", "O-0001", gate, "superada", "--motivo", "revisado", SIGN, "Ana"]) == 0
    capsys.readouterr()
    assert main(["--json", "puntuar", "ranking", "--sim", "300"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "APTA"
    # un agente bloquea: actúa de inmediato; desbloquear sin firma está prohibido
    assert main(["--agente", "compuestos-toxicologia", "puntuar", "compuerta", "O-0001", "seguridad", "bloqueada",
                 "--motivo", "señal de toxicidad"]) == 0
    assert main(["--agente", "sostenibilidad-bienestar", "puntuar", "compuerta", "O-0001", "seguridad", "superada",
                 "--motivo", "y"]) == 3
    capsys.readouterr()
    assert main(["--json", "puntuar", "ranking", "--sim", "300"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "BLOQUEADA"


def test_status_transitions_require_signature_and_gates(project):
    assert main(["oportunidad", "nueva", "--datos", str(EXAMPLES / "oportunidad_ejemplo.json")]) == 0
    assert main(["oportunidad", "estado", "O-0001", "en_validacion", "--motivo", "x"]) == 3
    assert main(["oportunidad", "estado", "O-0001", "en_validacion", "--motivo", "x", SIGN, "Ana"]) == 3
    for gate in GATES:
        main(["puntuar", "compuerta", "O-0001", gate, "superada", "--motivo", "ok", SIGN, "Ana"])
    assert main(["oportunidad", "estado", "O-0001", "en_validacion", "--motivo", "x", SIGN, "Ana"]) == 0


def test_out_of_scope_route_blocks_automatically(project):
    data = json.loads((EXAMPLES / "oportunidad_ejemplo.json").read_text("utf-8"))
    data["ruta_regulatoria_preliminar"] = "fuera_de_alcance_medicamento"
    (project / "med.json").write_text(json.dumps(data), "utf-8")
    assert main(["oportunidad", "nueva", "--datos", str(project / "med.json")]) == 0
    gates = db.effective_gates(_conn(), "O-0001", GATES)
    assert gates["regulatoria"] == "bloqueada"
    assert main(["--agente", "economia-mercado-pi", "puntuar", "compuerta", "O-0001", "regulatoria", "superada",
                 "--motivo", "x"]) == 3


def test_hypothesis_lifecycle_and_report(project):
    assert main(["oportunidad", "nueva", "--datos", str(EXAMPLES / "oportunidad_ejemplo.json")]) == 0
    assert main(["hipotesis", "nueva", "--datos", str(EXAMPLES / "hipotesis_ejemplo.json")]) == 0
    assert main(["hipotesis", "estado", "H-0001", "refutada", "--motivo", "Degradación ruminal completa"]) == 0
    assert db.list_decisions(_conn(), "H-0001")[-1]["decision"] == "estado -> refutada"
    assert main(["informe", "nuevo", "O-0001"]) == 0
    report = project / "research" / "informes" / "O-0001.md"
    assert main(["informe", "comprobar", str(report)]) == 0
    assert (project / "research" / "procedencia.jsonl").is_file()


def test_pub_claim_requires_verified_source_quote_and_location(project):
    f = project / "a.json"
    f.write_text(json.dumps({"texto": "Afirmación publicada", "etiqueta": "PUB", "fuentes": ["doi:10.1/nope"]}), "utf-8")
    with pytest.raises(SystemExit):       # el esquema exige cita textual y ubicación
        main(["afirmacion", "agregar", "--datos", str(f)])
    unverified = db.upsert_source(_conn(), {"doi": "10.1234/sin.verificar", "title": "t"})
    claim = {"texto": "Afirmación publicada", "etiqueta": "PUB", "fuentes": [unverified],
             "cita_textual": "texto literal de la fuente", "ubicacion": "Tabla 2"}
    f.write_text(json.dumps(claim), "utf-8")
    assert main(["afirmacion", "agregar", "--datos", str(f)]) == 3
    claim["fuentes"] = [_verified_source()]
    f.write_text(json.dumps(claim), "utf-8")
    assert main(["afirmacion", "agregar", "--datos", str(f)]) == 0
    claim["fuentes"] = [_verified_source("10.1234/retractada", retracted=True)]
    f.write_text(json.dumps(claim), "utf-8")
    assert main(["afirmacion", "agregar", "--datos", str(f)]) == 3


def test_extraction_batch_export_and_double_extraction(project, capsys):
    source = _verified_source()
    base = {"fuente": source, "especie": "vacuno lechero", "estado_fisiologico": "inicio de lactación",
            "dieta_basal": None, "diseno": "paralelo", "unidad_experimental": "animal", "intervencion": "X",
            "dosis": 50, "dosis_unidad": "g", "dosis_base": "g/animal/d", "forma_producto": "protegido",
            "variable": "leche", "unidad": "kg/d", "n_control": 12, "n_tratamiento": 12, "media_control": 30.0,
            "media_tratamiento": 31.0, "tipo_dispersion": "SEM_combinado", "dispersion_tratamiento": 0.5,
            "ubicacion": "Tabla 2"}
    (project / "e.json").write_text(json.dumps(base), "utf-8")
    with pytest.raises(SystemExit):       # dieta_basal a null sin declararla en no_consta
        main(["extraccion", "agregar", "--datos", str(project / "e.json")])
    base["no_consta"] = ["dieta_basal"]
    bad = dict(base, fuente="doi:10.1234/no.registrada")
    (project / "lote.json").write_text(json.dumps([base, bad]), "utf-8")
    assert main(["extraccion", "agregar", "--datos", str(project / "lote.json")]) == 3
    assert db.list_extractions(_conn()) == []          # lote atómico: no queda nada a medias
    (project / "e.json").write_text(json.dumps(base), "utf-8")
    assert main(["extraccion", "agregar", "--datos", str(project / "e.json")]) == 0
    csv_path = project / "leche.csv"
    assert main(["extraccion", "exportar", "--variable", "leche", "--csv", str(csv_path)]) == 0
    header = csv_path.read_text("utf-8").splitlines()[0].split(",")
    assert {"estudio", "comparacion", "media_trt", "media_ctrl", "disp_trt", "tipo_disp", "n_trt"} <= set(header)
    other = dict(base, media_tratamiento=31.5)
    (project / "b.json").write_text(json.dumps(other), "utf-8")
    capsys.readouterr()
    assert main(["extraccion", "comparar", str(project / "e.json"), str(project / "b.json")]) == 1
    assert "media_tratamiento" in capsys.readouterr().out


def test_invalid_json_is_rejected(project):
    bad = project / "bad.json"
    bad.write_text(json.dumps({"titulo": "x"}), "utf-8")
    with pytest.raises(SystemExit):
        main(["oportunidad", "nueva", "--datos", str(bad)])
