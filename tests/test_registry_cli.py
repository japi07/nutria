import json
from pathlib import Path

import pytest

from nutria import db
from nutria.cli import main
from nutria.config import load_config

EXAMPLES = Path(__file__).resolve().parents[1] / "ejemplos"
GATES = ["seguridad", "regulatoria", "analitica", "propiedad_intelectual", "destino_digestivo", "etica"]
SIGN = "--firma-humana"


_ABIERTAS: list = []


def _conn():
    """Conexión al registro del proyecto de la prueba; se cierra al terminar la prueba (sin ResourceWarning)."""
    conn = db.connect(load_config().db_path)
    _ABIERTAS.append(conn)
    return conn


@pytest.fixture(autouse=True)
def _cerrar_conexiones():
    yield
    while _ABIERTAS:
        _ABIERTAS.pop().close()


def _verified_source(doi="10.1234/prueba.registro", retracted=False):
    return db.upsert_source(_conn(), {"doi": doi, "title": "Fuente de prueba", "year": 2020, "verified": True,
                                      "verified_via": "prueba", "retracted": retracted})


def _three_raters(project):
    base = json.loads((EXAMPLES / "puntuacion_ejemplo.json").read_text("utf-8"))
    for rater in ("evaluador-a", "evaluador-b", "evaluador-c"):
        base["evaluador"] = rater
        (project / f"{rater}.json").write_text(json.dumps(base), "utf-8")
        assert main(["--agente", rater, "puntuar", "registrar", "--datos", str(project / f"{rater}.json")]) == 0


def test_gates_need_human_signature_and_ranking_respects_it(project, capsys):
    assert main(["oportunidad", "nueva", "--datos", str(EXAMPLES / "oportunidad_ejemplo.json")]) == 0
    _three_raters(project)
    # propuestas de agentes (sin firma, cada una de su rol propietario): no cuentan como superadas
    for gate in GATES:
        assert main(["--agente", db.GATE_OWNERS[gate], "puntuar", "compuerta", "O-0001", gate, "superada",
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
    with pytest.raises(SystemExit):       # el esquema exige cita textual, ubicación y contexto biológico
        main(["afirmacion", "agregar", "--datos", str(f)])
    unverified = db.upsert_source(_conn(), {"doi": "10.1234/sin.verificar", "title": "t"})
    claim = json.loads((EXAMPLES / "afirmacion_ejemplo.json").read_text("utf-8"))
    claim.pop("entidad")
    claim["fuentes"] = [unverified]
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
    base = json.loads((EXAMPLES / "extraccion_ejemplo.json").read_text("utf-8"))[0]
    base.update(fuente=source, dieta_basal=None, variable="leche")
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
    with pytest.raises(SystemExit):       # solo hay una extracción simple: no pasa al análisis por defecto
        main(["extraccion", "exportar", "--variable", "leche", "--csv", str(csv_path)])
    assert main(["extraccion", "exportar", "--variable", "leche", "--csv", str(csv_path),
                 "--incluir-sin-conciliar"]) == 0
    header = csv_path.read_text("utf-8").splitlines()[0].split(",")
    assert {"estudio", "comparacion", "media_trt", "media_ctrl", "disp_trt", "tipo_disp", "n_trt"} <= set(header)
    # el estado de doble extracción no se declara: solo sale de una comparación registrada de dos copias
    conciliated = dict(base, estado_verificacion="doble_concordante", media_tratamiento=32.0)
    (project / "c.json").write_text(json.dumps(conciliated), "utf-8")
    assert main(["extraccion", "agregar", "--datos", str(project / "c.json")]) == 3
    copy = dict(base, media_tratamiento=32.0)
    for label in ("A", "B"):
        (project / f"c_{label}.json").write_text(json.dumps([copy], indent=1 if label == "A" else 2), "utf-8")
        assert main(["--agente", f"extractor-datos-{label}", "extraccion", "depositar", "--datos",
                     str(project / f"c_{label}.json")]) == 0
    assert main(["--agente", "orquestador", "extraccion", "comparar", str(project / "c_A.json"),
                 str(project / "c_B.json"), "--registrar"]) == 0
    capsys.readouterr()
    assert main(["--json", "extraccion", "exportar", "--variable", "leche", "--csv", str(csv_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["filas"] == 1 and out["excluidas"] == {"simple": 1}
    assert len(csv_path.read_text("utf-8").splitlines()) == 2      # cabecera + la fila conciliada
    other = dict(base, media_tratamiento=31.5)
    (project / "b.json").write_text(json.dumps(other), "utf-8")
    capsys.readouterr()
    # en un proyecto, las discrepancias solo se ven al registrar la comparación de dos copias depositadas
    assert main(["extraccion", "comparar", str(project / "e.json"), str(project / "b.json")]) == 3
    assert "media_tratamiento" not in capsys.readouterr().out


def test_invalid_json_is_rejected(project):
    bad = project / "bad.json"
    bad.write_text(json.dumps({"titulo": "x"}), "utf-8")
    with pytest.raises(SystemExit):
        main(["oportunidad", "nueva", "--datos", str(bad)])


def test_evaluators_cannot_see_other_scores_ranking_or_provenance(project, capsys):
    # Ceguera técnica de los evaluadores (regla 9): ni puntuaciones ajenas, ni ranking, ni procedencia.
    assert main(["oportunidad", "nueva", "--datos", str(EXAMPLES / "oportunidad_ejemplo.json")]) == 0
    _three_raters(project)
    capsys.readouterr()
    assert main(["--json", "--agente", "evaluador-2", "oportunidad", "ver", "O-0001"]) == 0
    assert "puntuaciones" not in json.loads(capsys.readouterr().out)
    assert main(["--json", "--agente", "director-cientifico", "oportunidad", "ver", "O-0001"]) == 0
    assert json.loads(capsys.readouterr().out)["puntuaciones"]
    with pytest.raises(SystemExit):
        main(["--agente", "evaluador-2", "puntuar", "ranking", "--sim", "100"])
    with pytest.raises(SystemExit):
        main(["--agente", "evaluador-2", "procedencia"])
    # la procedencia registra quién puntuó, pero no los valores
    trace = (project / "research" / "procedencia.jsonl").read_text("utf-8")
    events = [json.loads(line) for line in trace.splitlines() if '"puntuacion"' in line]
    assert events and all("valores" not in json.dumps(e["outputs"]) for e in events)


def test_intended_claim_schema(project):
    opp = json.loads((EXAMPLES / "oportunidad_ejemplo.json").read_text("utf-8"))
    opp["alegacion_prevista"] = {"texto": "Apoyo al equilibrio mineral en el preparto",
                                 "tipo": "finalidad_nutricional_especifica"}
    opp["ruta_regulatoria_preliminar"] = "aditivo_zootecnico"
    f = project / "o.json"
    f.write_text(json.dumps(opp), "utf-8")
    with pytest.raises(SystemExit):       # art. 13.2: solo piensos dietéticos
        main(["oportunidad", "nueva", "--datos", str(f)])
    opp["ruta_regulatoria_preliminar"] = "pienso_dietetico"
    f.write_text(json.dumps(opp), "utf-8")
    assert main(["oportunidad", "nueva", "--datos", str(f)]) == 0
