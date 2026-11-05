"""Registro y esquemas: doble extracción verificable (D2-12), contexto biológico en los esquemas (D4-5), dieta basal
con PDR y exportación por especie y etapa (D4-6), periodo de transición e incidencias (D4-7) y clasificación
regulatoria (D5-12).

Los registros se construyen a partir de las plantillas de `ejemplos/` (si otro cambio endurece los esquemas, se
actualizan las plantillas y estas pruebas siguen valiendo).
"""
import copy
import csv
import json
from pathlib import Path

import pytest

from nutria import db
from nutria.cli import _validate, main
from nutria.config import load_config
from nutria.provenance import sha256_file

EXAMPLES = Path(__file__).resolve().parents[1] / "ejemplos"
SIGN = "--firma-humana"
GATES = ["seguridad", "regulatoria", "analitica", "propiedad_intelectual", "destino_digestivo", "etica"]
REG = ["--agente", "regulacion-seguridad"]   # rol del triage regulatorio
LECHE = "producción de leche"
CETOSIS = "cetosis subclínica"
MOTIVO = "Tabla 3, fila 2, releída en la fuente"
# Valores vacíos, de relleno o genéricos que no pueden ocupar el lugar de una especie o una etapa.
GENERICOS = ["", "   ", "-", "--", "?", "n/a", "N/A", "nd", "xxx", "desconocido", "no_consta", "animales", "Animales",
             "varios", "Varias especies", "todas", "TODAS", "todos los animales", "cualquiera", "general", "ganado"]


# ------------------------------------------------------------------ utilidades
def _example(name: str):
    return json.loads((EXAMPLES / f"{name}.json").read_text("utf-8"))


def _conn():
    return db.connect(load_config().db_path)


def _source(n: int = 1, retracted: bool = False) -> str:
    return db.upsert_source(_conn(), {"doi": f"10.1234/p7.{n}", "title": f"Fuente de prueba {n}", "year": 2021,
                                      "verified": True, "verified_via": "prueba", "retracted": retracted})


def _records(source: str, **changes) -> list[dict]:
    """Los dos registros de ejemplos/extraccion_ejemplo.json (variable continua e incidencia) con la fuente dada."""
    recs = _example("extraccion_ejemplo")
    for r in recs:
        r.update(fuente=source, **changes)
    return recs


def _write(path: Path, data, indent: int = 2) -> str:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=indent), "utf-8")
    return str(path)


def _code(argv: list[str]):
    """Código de salida de la CLI, también cuando termina con SystemExit (mensaje de error)."""
    try:
        return main(argv)
    except SystemExit as exc:
        return exc.code


def _deposit(project: Path, name: str, records: list[dict], actor: str, indent: int = 2) -> str:
    path = _write(project / name, records, indent)
    assert main(["--agente", actor, "extraccion", "depositar", "--datos", path]) == 0
    return path


def _double(project: Path, tag: str, records: list[dict]) -> str:
    """Doble extracción concordante registrada por el sistema: dos copias de extractores distintos y comparación."""
    a = _deposit(project, f"{tag}_A.json", records, f"extractor-{tag}-A", indent=1)
    b = _deposit(project, f"{tag}_B.json", records, f"extractor-{tag}-B", indent=2)
    assert main(["extraccion", "comparar", a, b, "--registrar"]) == 0
    return db.list_extraction_comparisons(_conn())[-1]["id"]


def _export_json(capsys, *extra) -> dict:
    capsys.readouterr()
    assert main(["--json", "extraccion", "exportar", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def _csv_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ================================================================== D2-12 doble extracción verificable
def test_agregar_no_puede_declarar_el_estado_de_doble_extraccion(project, capsys):
    source = _source()
    base = _records(source)[0]
    for changes in ({"estado_verificacion": "doble_concordante"}, {"estado_verificacion": "resuelta"},
                    {"estado_verificacion": "discrepancia"}, {"verificado_por": "Ana"}):
        assert main(["extraccion", "agregar", "--datos", _write(project / "e.json", dict(base, **changes))]) == 3
        assert "fija el sistema" in capsys.readouterr().err
    assert db.list_extractions(_conn()) == []
    with pytest.raises(db.RegistryError):  # también por la API del registro
        db.add_extraction(_conn(), dict(base, estado_verificacion="doble_concordante"))
    assert main(["extraccion", "agregar", "--datos", _write(project / "e.json", base)]) == 0
    assert [r["check_status"] for r in db.list_extractions(_conn())] == ["simple"]


def test_doble_concordante_declarada_sin_comparacion_no_llega_al_metaanalisis(project, capsys):
    """Reauditoría de D2-12: extracciones nunca comparadas con el estado 'doble_concordante' (de un registro anterior o
    escritas en la base de datos) ya no pasan a la exportación de análisis ni cuentan como verificadas."""
    sources = [_source(i) for i in range(1, 6)]
    conn = _conn()
    conn.execute("DROP TRIGGER extractions_estado_doble_alta")   # registro creado antes de esta protección
    for i, source in enumerate(sources, 1):
        conn.execute("INSERT INTO extractions (source_id, species, physiological_stage, intervention, outcome, "
                     "outcome_unit, n_ctrl, n_trt, mean_ctrl, mean_trt, dispersion_type, disp_ctrl, disp_trt, location, "
                     "check_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (source, "vacuno lechero", "periodo de transición", "X", LECHE, "kg/d", 12, 12, 30.0, 31.0 + i,
                      "SEM_combinado", 0.5, 0.5, "Tabla 2", "doble_concordante" if i < 5 else "resuelta"))
    csv_path = project / "leche.csv"
    with pytest.raises(SystemExit, match="sin_comparacion_registrada"):
        main(["extraccion", "exportar", "--variable", LECHE, "--csv", str(csv_path)])
    capsys.readouterr()
    assert main(["--json", "estado"]) == 0
    assert json.loads(capsys.readouterr().out)["extracciones_doble_verificadas"] == 0
    assert main(["extraccion", "listar", "--variable", LECHE]) == 0
    assert "SIN comparación registrada" in capsys.readouterr().out
    # solo como exploración, y marcadas como no verificadas
    out = _export_json(capsys, "--variable", LECHE, "--csv", str(csv_path), "--incluir-sin-conciliar")
    assert out["filas"] == 5 and any("sin doble extracción verificada" in a for a in out["avisos"])
    assert {r["doble_extraccion_verificada"] for r in _csv_rows(csv_path)} == {"no"}


def test_el_estado_de_doble_extraccion_no_se_escribe_en_la_base_de_datos(project, capsys):
    """Ni un INSERT ni un UPDATE directos fijan el estado; las copias y las comparaciones no se modifican."""
    import sqlite3
    comp = _double(project, "x", _records(_source()))
    conn = _conn()
    simple = _records(_source(2))[0]
    assert main(["extraccion", "agregar", "--datos", _write(project / "s.json", simple)]) == 0
    (sid,) = [r["id"] for r in db.list_extractions(_conn()) if r["check_status"] == "simple"]
    ataques = [
        ("INSERT INTO extractions (source_id, species, intervention, outcome, location, check_status) "
         "VALUES ('doi:10.1234/p7.2', 'vacuno lechero', 'X', 'leche', 'Tabla 1', 'doble_concordante')", ()),
        ("UPDATE extractions SET check_status = 'doble_concordante', comparison_id = ? WHERE id = ?", (comp, sid)),
        ("UPDATE extractions SET comparison_id = ? WHERE id = ?", (comp, sid)),
        ("UPDATE extraction_copies SET records = '[]'", ()),
        ("DELETE FROM extraction_copies", ()),
        ("DELETE FROM extraction_comparisons", ()),
        ("UPDATE extraction_comparisons SET result = 'concordante'", ()),
        ("UPDATE extraction_comparisons SET conciliation = '{}'", ()),     # concordante: nada que conciliar
    ]
    for sql, params in ataques:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql, params)
    assert len(db.verified_extraction_ids(_conn())) == 2
    conn.execute("UPDATE extractions SET notes = 'nota' WHERE id = ?", (sid,))   # lo demás sí se puede anotar


def test_doble_extraccion_concordante_la_registra_el_sistema(project, capsys):
    source = _source()
    recs = _records(source)
    a = _deposit(project, "x_A.json", recs, "extractor-datos-A", indent=1)
    b = _deposit(project, "x_B.json", recs, "extractor-datos-B", indent=2)  # mismo contenido, otra copia
    capsys.readouterr()
    assert main(["--json", "--agente", "extractor-datos", "extraccion", "comparar", a, b, "--registrar"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["resultado"] == "concordante" and len(res["ids"]) == 2
    rows = db.list_extractions(_conn())
    assert {r["check_status"] for r in rows} == {"doble_concordante"}
    assert {r["comparison_id"] for r in rows} == {res["id"]}
    assert rows[0]["extractor"] == "extractor-datos-A | extractor-datos-B"
    assert rows[0]["checked_by"] == "extractor-datos"
    comp = db.list_extraction_comparisons(_conn())[0]
    assert (comp["sha256_a"], comp["sha256_b"]) == (sha256_file(Path(a)), sha256_file(Path(b)))
    assert (comp["actor_a"], comp["actor_b"]) == ("extractor-datos-A", "extractor-datos-B")
    assert db.list_decisions(_conn(), res["id"])[0]["decision"] == "doble extracción comparada: concordante"
    csv_path = project / "leche.csv"
    assert main(["extraccion", "exportar", "--variable", LECHE, "--csv", str(csv_path)]) == 0
    (row,) = _csv_rows(csv_path)
    assert row["id_doble_extraccion"] == res["id"] and row["doble_extraccion_verificada"] == "si"
    # una copia solo entra en una comparación registrada: repetirla no duplica los registros
    assert main(["extraccion", "comparar", a, b, "--registrar"]) == 3
    assert "ya se usó" in capsys.readouterr().err
    assert len(db.list_extractions(_conn())) == 2


@pytest.mark.parametrize("caso", ["mismo_fichero", "sin_depositar", "mismo_extractor", "editada_tras_depositar",
                                  "sin_extractor"])
def test_doble_extraccion_exige_dos_copias_independientes_y_depositadas(project, capsys, monkeypatch, caso):
    monkeypatch.delenv("NUTRIA_AGENT", raising=False)
    source = _source()
    recs = _records(source)
    if caso == "sin_extractor":  # sin --agente ni NUTRIA_AGENT no hay a quién atribuir la copia
        assert main(["extraccion", "depositar", "--datos", _write(project / "x_A.json", recs)]) == 3
        assert db.list_extraction_comparisons(_conn()) == []
        return
    a = _deposit(project, "x_A.json", recs, "extractor-datos-A", indent=1)
    if caso == "mismo_fichero":
        b = a
    elif caso == "sin_depositar":
        b = _write(project / "x_B.json", recs)
    elif caso == "mismo_extractor":
        b = _deposit(project, "x_B.json", recs, "extractor-datos-A", indent=2)
    else:  # la copia B, con una discrepancia, se edita después de depositarla para igualarla a A
        distinta = copy.deepcopy(recs)
        distinta[0]["media_tratamiento"] = 30.0
        b = _deposit(project, "x_B.json", distinta, "extractor-datos-B")
        _write(project / "x_B.json", recs, indent=2)
    assert _code(["extraccion", "comparar", a, b, "--registrar"]) not in (0, 1)
    assert db.list_extractions(_conn()) == []
    assert db.list_extraction_comparisons(_conn()) == []


def test_conciliacion_registrada_con_resolucion_de_cada_discrepancia(project, capsys):
    source = _source()
    recs_a = _records(source)
    recs_b = copy.deepcopy(recs_a)
    recs_b[0]["media_tratamiento"] = 37.0           # B leyó mal la media
    recs_b[0]["dosis_base"] = "g/animal y día"      # misma base, otra redacción
    recs_b[1]["eventos_tratamiento"] = 5            # discrepancia en el registro de incidencia
    a = _deposit(project, "x_A.json", recs_a, "extractor-datos-A")
    b = _deposit(project, "x_B.json", recs_b, "extractor-datos-B")
    capsys.readouterr()
    assert main(["--json", "extraccion", "comparar", a, b, "--registrar"]) == 1
    res = json.loads(capsys.readouterr().out)
    ce = res["id"]
    assert res["resultado"] == "con_discrepancias" and res["ids"] == [] and len(res["discrepancias"]) == 3
    assert db.list_extractions(_conn()) == []

    def conciliar(entradas, no_resueltos=(), valida=False):
        path = _write(project / "conciliacion.json", {"conciliacion": entradas, "no_resueltos": list(no_resueltos)})
        code = _code(["--agente", "extractor-datos", "extraccion", "conciliar", ce, "--datos", path])
        assert (code == 0) is valida, capsys.readouterr().err
        if not valida:
            assert len(db.list_extractions(_conn())) == (1 if conciliada else 0)  # rechazada: no registra nada

    def res_(campo, origen, **extra):
        return {"campo": campo, "origen_valor": origen, "motivo": MOTIVO, **extra}

    conciliada = False
    media_a, dosis_a = res_("media_tratamiento", "A"), res_("dosis_base", "A")
    pendiente = [{"a": 2, "b": 2, "motivo": "La figura 2 no permite leer los eventos"}]
    conciliar([{"a": 1, "b": 1, "resoluciones": [media_a]}], pendiente)                      # falta una resolución
    conciliar([{"a": 1, "b": 1, "resoluciones": [media_a, dosis_a]}])                        # registro 2 sin declarar
    conciliar([{"a": 1, "b": 1, "resoluciones": [dosis_a, res_("media_tratamiento", "fuente", valor=36.7)]}],
              pendiente)                                                                     # promedio de A y B
    conciliar([{"a": 1, "b": 1, "resoluciones": [dosis_a, res_("media_tratamiento", "fuente", valor=36.4)]}],
              pendiente)                                                                     # 'fuente' = valor de A
    conciliar([{"a": 1, "b": 1, "resoluciones": [media_a, dosis_a, res_("especie", "A")]}], pendiente)  # innecesaria
    conciliar([{"a": 1, "b": 1, "resoluciones": [media_a, dosis_a]}, {"a": 1, "b": 2}])      # A1 dos veces
    conciliar([{"a": 1, "b": 1, "resoluciones": [media_a, dosis_a]}, {"a": 2, "b": 2}])      # incidencia sin resolver
    conciliar([{"a": 1, "b": 1, "resoluciones": [{"campo": "media_tratamiento", "origen_valor": "A"}, dosis_a]}],
              pendiente)                                                                     # resolución sin motivo
    # válida: el registro 1 se resuelve; el 2 se declara no resuelto y no se registra
    conciliar([{"a": 1, "b": 1, "resoluciones": [media_a, dosis_a]}], pendiente, valida=True)
    conciliada = True
    (row,) = db.list_extractions(_conn())
    assert row["check_status"] == "resuelta" and row["comparison_id"] == ce
    assert row["mean_trt"] == 36.4 and row["dose_basis"] == "g/animal/d" and (row["pair_a"], row["pair_b"]) == (1, 1)
    comp = db.list_extraction_comparisons(_conn())[0]
    assert comp["conciliated_by"] == "extractor-datos" and comp["conciliation"]["no_resueltos"] == pendiente
    assert comp["conciliation"]["sha256"] == sha256_file(project / "conciliacion.json")
    assert [d["decision"] for d in db.list_decisions(_conn(), ce)] == ["doble extracción comparada: con_discrepancias",
                                                                       "doble extracción conciliada"]
    out = _export_json(capsys, "--variable", LECHE, "--csv", str(project / "leche.csv"))
    assert out["filas"] == 1 and out["excluidas"] == {}
    conciliar([{"a": 1, "b": 1, "resoluciones": [media_a, dosis_a]}], pendiente)            # ya conciliada


def test_conciliacion_valor_releido_y_promedio_confirmado(project, capsys):
    source = _source()
    recs_a = _records(source)
    recs_b = copy.deepcopy(recs_a)
    recs_b[0]["media_tratamiento"] = 37.0
    recs_b[1]["eventos_tratamiento"] = 5
    a = _deposit(project, "x_A.json", recs_a, "extractor-datos-A")
    b = _deposit(project, "x_B.json", recs_b, "extractor-datos-B")
    assert main(["extraccion", "comparar", a, b, "--registrar"]) == 1
    entradas = [{"a": 1, "b": 1, "resoluciones": [{"campo": "media_tratamiento", "origen_valor": "fuente", "valor": 36.7,
                                                   "confirmado_en_fuente": True, "motivo": MOTIVO}]},
                {"a": 2, "b": 2, "resoluciones": [{"campo": "eventos_tratamiento", "origen_valor": "fuente",
                                                   "valor": 3, "motivo": "Tabla 4: 3 casos; ninguna copia acertó"}]}]
    path = _write(project / "conciliacion.json", {"conciliacion": entradas})
    assert main(["extraccion", "conciliar", "CE-0001", "--datos", path]) == 0
    rows = db.list_extractions(_conn())
    assert [(r["mean_trt"], r["ev_trt"], r["check_status"]) for r in rows] == [(36.7, None, "resuelta"),
                                                                              (None, 3, "resuelta")]


def test_conciliacion_de_copias_con_distinto_numero_de_registros(project, capsys):
    source = _source()
    recs_a = _records(source)
    a = _deposit(project, "x_A.json", recs_a, "extractor-datos-A")
    b = _deposit(project, "x_B.json", [recs_a[1]], "extractor-datos-B")  # B no extrajo el primer registro
    assert main(["extraccion", "comparar", a, b, "--registrar"]) == 1
    path = _write(project / "c.json", {"conciliacion": [{"a": 2, "b": 1}]})
    assert main(["extraccion", "conciliar", "CE-0001", "--datos", path]) == 3      # A1 sin declarar
    path = _write(project / "c.json", {"conciliacion": [{"a": 2, "b": 1}],
                                       "no_resueltos": [{"a": 1, "motivo": "Solo está en la copia A: se repetirá"}]})
    assert main(["extraccion", "conciliar", "CE-0001", "--datos", path]) == 0
    (row,) = db.list_extractions(_conn())
    assert (row["outcome"], row["check_status"], row["pair_a"], row["pair_b"]) == (CETOSIS, "doble_concordante", 2, 1)


def test_una_copia_con_discrepancias_no_se_blanquea_con_una_tercera(project, capsys):
    source = _source()
    recs = _records(source)
    distinta = copy.deepcopy(recs)
    distinta[0]["media_tratamiento"] = 30.0
    a = _deposit(project, "x_A.json", recs, "extractor-datos-A")
    b = _deposit(project, "x_B.json", distinta, "extractor-datos-B")
    assert main(["extraccion", "comparar", a, b, "--registrar"]) == 1
    c = _deposit(project, "x_C.json", recs, "extractor-datos-C", indent=4)   # igual que A, de otro extractor
    assert main(["extraccion", "comparar", a, c, "--registrar"]) == 3
    assert "CE-0001" in capsys.readouterr().err
    assert db.list_extractions(_conn()) == []
    capsys.readouterr()
    assert main(["--json", "extraccion", "comparaciones", "--pendientes"]) == 0
    assert [c["id"] for c in json.loads(capsys.readouterr().out)] == ["CE-0001"]


def test_copias_corregidas_tras_ver_discrepancias_no_sustituyen_a_la_conciliacion(project, capsys):
    """Ver las discrepancias, igualar las copias y volver a depositarlas no da 'doble_concordante': la comparación
    con discrepancias se concilia (o se cierra declarando lo no resuelto) antes de comparar otras copias."""
    source = _source()
    recs = _records(source)
    distinta = copy.deepcopy(recs)
    distinta[0]["media_tratamiento"] = 30.0
    a = _deposit(project, "x_A.json", recs, "extractor-datos-A")
    b = _deposit(project, "x_B.json", distinta, "extractor-datos-B")
    assert main(["extraccion", "comparar", a, b, "--registrar"]) == 1
    a2 = _deposit(project, "x_A2.json", recs, "extractor-datos-A", indent=3)
    b2 = _deposit(project, "x_B2.json", recs, "extractor-datos-B", indent=4)      # B igualada a A
    capsys.readouterr()
    assert main(["extraccion", "comparar", a2, b2, "--registrar"]) == 3
    assert "CE-0001 con discrepancias sin conciliar" in capsys.readouterr().err
    assert db.list_extractions(_conn()) == [] and len(db.list_extraction_comparisons(_conn())) == 1
    # quien extrajo una de las copias no concilia su propia discrepancia
    cierre = _write(project / "cierre.json", {"conciliacion": [], "no_resueltos": [
        {"a": 1, "b": 1, "motivo": "Se repite la doble extracción completa de la fuente"},
        {"a": 2, "b": 2, "motivo": "Se repite la doble extracción completa de la fuente"}]})
    assert main(["--agente", "extractor-datos-A", "extraccion", "conciliar", "CE-0001", "--datos", cierre]) == 3
    assert "no quien extrajo" in capsys.readouterr().err
    # cerrarla con su motivo (sin registrar nada) permite una doble extracción nueva, que sí se registra
    assert main(["--agente", "extractor-datos", "extraccion", "conciliar", "CE-0001", "--datos", cierre]) == 0
    assert db.list_extractions(_conn()) == []
    assert main(["extraccion", "comparar", a2, b2, "--registrar"]) == 0
    assert {r["check_status"] for r in db.list_extractions(_conn())} == {"doble_concordante"}


def test_diseno_factorial_no_se_toma_por_un_dato_repetido(project, capsys):
    """Dos registros que solo difieren en la dieta basal (o en otro campo de contexto) son datos distintos."""
    source = _source()
    alta, baja = _records(source)[0], _records(source)[0]
    alta.update(dieta_basal="TMR con 60 % de forraje", forraje_pct_ms=60.0, media_tratamiento=36.0)
    baja.update(dieta_basal="TMR con 40 % de forraje", forraje_pct_ms=40.0, media_tratamiento=37.5)
    _double(project, "fact", [alta, baja])
    assert len(db.list_extractions(_conn())) == 2
    out = _export_json(capsys, "--variable", LECHE, "--csv", str(project / "f.csv"))
    assert any("varias comparaciones de la misma variable" in a and "--agregar-estudio" in a for a in out["avisos"])
    # el mismo dato repetido en la copia sí se rechaza
    otra = _source(2)
    rec = _records(otra)[0]
    a = _deposit(project, "rep_A.json", [rec, dict(rec)], "extractor-rep-A")
    b = _deposit(project, "rep_B.json", [rec, dict(rec)], "extractor-rep-B")
    capsys.readouterr()
    assert main(["extraccion", "comparar", a, b, "--registrar"]) == 3
    assert "no se registra dos veces" in capsys.readouterr().err


def test_los_mismos_datos_no_se_registran_dos_veces_con_copias_reformateadas(project, capsys):
    source = _source()
    recs = _records(source)
    _double(project, "uno", recs)
    a = _deposit(project, "otra_A.json", recs, "extractor-otra-A", indent=4)   # mismos datos, otros ficheros
    b = _deposit(project, "otra_B.json", recs, "extractor-otra-B", indent=3)
    capsys.readouterr()
    assert main(["extraccion", "comparar", a, b, "--registrar"]) == 3
    assert "no se registra dos veces" in capsys.readouterr().err
    assert len(db.list_extractions(_conn())) == 2 and len(db.list_extraction_comparisons(_conn())) == 1


def test_copia_con_fuente_retractada_no_se_deposita(project, capsys):
    source = _source(retracted=True)
    path = _write(project / "x_A.json", _records(source))
    assert main(["--agente", "extractor-datos-A", "extraccion", "depositar", "--datos", path]) == 3
    assert "RETRACTADA" in capsys.readouterr().err


# ================================================================== D4-5 contexto biológico en los esquemas
def test_ejemplos_cumplen_los_esquemas():
    for kind, name in (("oportunidad", "oportunidad_ejemplo"), ("hipotesis", "hipotesis_ejemplo"),
                       ("afirmacion", "afirmacion_ejemplo"), ("conciliacion", "conciliacion_ejemplo")):
        _validate(kind, _example(name))
    for record in _example("extraccion_ejemplo"):
        _validate("extraccion", record)


@pytest.mark.parametrize("valor", GENERICOS)
@pytest.mark.parametrize("kind", ["oportunidad", "hipotesis"])
@pytest.mark.parametrize("campo", ["especie", "etapa"])
def test_especie_y_etapa_concretas_en_oportunidades_e_hipotesis(kind, campo, valor):
    with pytest.raises(SystemExit):
        _validate(kind, dict(_example(f"{kind}_ejemplo"), **{campo: valor}))


@pytest.mark.parametrize("kind", ["oportunidad", "hipotesis"])
def test_especies_y_etapas_concretas_se_aceptan(kind):
    base = _example(f"{kind}_ejemplo")
    for especie, etapa in (("ovino", "toda la lactación"), ("rumiantes", "cebo"), ("Bos taurus", "destete"),
                           ("aves (pollos de engorde)", "fase de arranque")):
        _validate(kind, dict(base, especie=especie, etapa=etapa))


@pytest.mark.parametrize("valor", [
    "anim​ales", "ani­males", "ㅤ", "ｔｏｄａｓ", "𝐚𝐧𝐢𝐦𝐚𝐥𝐞𝐬", "аnimales",      # invisibles, anchos, homoglifos
    "a n i m a l e s", "t o d a s", "anim ales", "todaslasetapas", "Todas-las-etapas", "animales_de_granja",
    "universal", "indefinida", "sin restricción", "independiente de la etapa", "multietapa", "multi-especie",
    "todo el ciclo productivo", "ciclo completo", "cualquier fase del ciclo", "animales de producción", "livestock",
    "food-producing animals", "whole cycle", "especie diana", "n/a, desconocido", "No Consta."])
@pytest.mark.parametrize("campo", ["especie", "etapa"])
def test_variantes_de_valores_genericos_rechazadas(campo, valor):
    with pytest.raises(SystemExit, match="JSON no válido"):
        _validate("oportunidad", dict(_example("oportunidad_ejemplo"), **{campo: valor}))


def test_valores_concretos_no_se_confunden_con_genericos():
    base = _example("oportunidad_ejemplo")
    for etapa in ("fase de producción", "fase de crecimiento", "en ayunas", "cría", "adultos", "toda la lactación",
                  "periodo seco", "inicio de lactación (0 a 21 DEL)"):
        _validate("oportunidad", dict(base, etapa=etapa))
    for especie in ("ganado lechero", "líquido ruminal bovino", "Oncorhynchus mykiss", "salmón atlántico", "ocas",
                    "toros", "monogástricos", "ratas de laboratorio"):
        _validate("oportunidad", dict(base, especie=especie))
    _validate("oportunidad", dict(base, raza="La Mancha"))
    for especie in ("animales adultos", "hembras reproductoras", "crías", "modelo animal", "healthy adults"):
        with pytest.raises(SystemExit, match="no nombra la especie"):   # describen a los animales, no la especie
            _validate("oportunidad", dict(base, especie=especie))


def test_validacion_de_textos_en_tiempo_lineal():
    """Los textos se comprueban sin expresiones regulares con retroceso: un valor hostil no bloquea la CLI."""
    import time
    base = _example("oportunidad_ejemplo")
    inicio = time.perf_counter()
    for valor in ("s/d " * 2000 + "q", "n/a " * 2000 + "q", "de " * 3000 + "zzz", "animales de producción " * 500 + "q",
                  "todaslasetapas" * 700 + "q", "x" * 50000 + "y", "a " * 20000):
        for campo in ("especie", "etapa", "raza"):
            try:
                _validate("oportunidad", dict(base, **{campo: valor}))
            except SystemExit:
                pass
        try:
            _validate("extraccion", dict(_example("extraccion_ejemplo")[0], intervencion=valor))
        except SystemExit:
            pass
    assert time.perf_counter() - inicio < 5


def test_oportunidad_raza_y_estado_fisiologico_con_no_consta_explicito():
    base = _example("oportunidad_ejemplo")
    _validate("oportunidad", dict(base, raza="no_consta", estado_fisiologico="no_consta"))
    _validate("oportunidad", dict(base, raza="Holstein", estado_fisiologico="multíparas sanas en el preparto"))
    _validate("oportunidad", {k: v for k, v in base.items() if k not in ("raza", "estado_fisiologico")})
    for campo in ("raza", "estado_fisiologico"):
        for valor in ("", "-", "todas", "No consta", "n/a"):
            with pytest.raises(SystemExit):
                _validate("oportunidad", dict(base, **{campo: valor}))


def test_oportunidad_generica_rechazada_por_la_cli(project):
    path = _write(project / "o.json", dict(_example("oportunidad_ejemplo"), especie="animales", etapa="todas"))
    with pytest.raises(SystemExit, match="genérico"):
        main(["oportunidad", "nueva", "--datos", path])
    assert db.list_opportunities(_conn()) == []


def _claim(source: str) -> dict:
    claim = _example("afirmacion_ejemplo")
    claim.pop("entidad")
    claim["fuentes"] = [source]
    return claim


def test_afirmacion_pub_exige_contexto_biologico(project, capsys):
    source = _source()
    base = _claim(source)
    assert main(["afirmacion", "agregar", "--datos", _write(project / "a.json", base)]) == 0
    assert db.list_claims(_conn())[0]["context"]["especie"] == "vacuno lechero"
    invalidas = [{k: v for k, v in base.items() if k != "contexto"},
                 {k: v for k, v in base.items() if k != "transferibilidad"},
                 dict(base, especie="vacuno lechero")]                                  # fuera del contexto
    for campo in db.PUB_CONTEXT_REQUIRED:
        claim = copy.deepcopy(base)
        del claim["contexto"][campo]
        invalidas.append(claim)
    sin_etapa = copy.deepcopy(base)
    del sin_etapa["contexto"]["etapa"], sin_etapa["contexto"]["estado_fisiologico"]
    invalidas.append(sin_etapa)
    for campo, valor in (("especie", "animales"), ("especie", ""), ("diseno", "-"), ("unidad_experimental", "n/a"),
                         ("dosis_base", "no_consta"), ("dosis_unidad", "no_consta"), ("n", 0), ("forma_producto", "")):
        claim = copy.deepcopy(base)
        claim["contexto"][campo] = valor
        invalidas.append(claim)
    nombrada = copy.deepcopy(base)
    nombrada["contexto"]["especie"] = "no_consta"                                       # especie_diana sin especie
    invalidas.append(nombrada)
    for claim in invalidas:
        with pytest.raises(SystemExit):
            main(["afirmacion", "agregar", "--datos", _write(project / "a.json", claim)])
    # lo que la fuente no informa se declara con 'no_consta' explícito
    declarada = copy.deepcopy(base)
    declarada["transferibilidad"] = "in_vitro"
    declarada["contexto"] = {k: "no_consta" for k in (*db.PUB_CONTEXT_REQUIRED, "etapa")}
    assert main(["afirmacion", "agregar", "--datos", _write(project / "a.json", declarada)]) == 0
    # también por la API del registro, y una afirmación [HIP] no necesita contexto
    with pytest.raises(db.RegistryError):
        db.add_claim(_conn(), {k: v for k, v in base.items() if k != "contexto"})
    hip = {"texto": "X podría reducir el BHB en el posparto", "etiqueta": "HIP"}
    assert main(["afirmacion", "agregar", "--datos", _write(project / "h.json", hip)]) == 0


@pytest.mark.parametrize("campo", ["especie", "estado_fisiologico", "dieta_basal", "forma_producto", "dosis_base",
                                   "dosis_unidad", "intervencion", "variable", "unidad", "ubicacion", "raza", "sexo",
                                   "paridad", "comparador", "experimento"])
@pytest.mark.parametrize("valor", ["", "  ", "-", "n/a", "xxx", "No consta", "​"])
def test_extraccion_rechaza_vacios_y_relleno_en_campos_obligatorios(campo, valor):
    with pytest.raises(SystemExit):
        _validate("extraccion", dict(_example("extraccion_ejemplo")[0], **{campo: valor}))


def test_extraccion_acepta_unidades_con_signos():
    base = _example("extraccion_ejemplo")[0]
    for unidad in ("%", "% de la MS", "‰", "µg/kg MS", "μmol/L", "kg/d"):
        _validate("extraccion", dict(base, unidad=unidad))


def test_extraccion_no_consta_coherente_y_sin_campos_desconocidos():
    base = _example("extraccion_ejemplo")[0]
    with pytest.raises(SystemExit, match="no_consta"):
        _validate("extraccion", dict(base, dieta_basal=None))
    _validate("extraccion", dict(base, dieta_basal=None, no_consta=["dieta_basal"]))
    for changes in ({"no_consta": ["dosis"]},                       # declarado ausente pero con valor
                    {"no_consta": ["especie"]},                     # la especie nunca puede faltar
                    {"especie": "animales"}, {"estado_fisiologico": "todos"},
                    {"pdr_pct_pb_": 60},                            # campo desconocido: no se pierde en silencio
                    {"dosis": -1}, {"forraje_pct_ms": 120}):
        with pytest.raises(SystemExit):
            _validate("extraccion", dict(base, **changes))


# ================================================================== D4-6 PDR, especie y etapa en la exportación
def test_pdr_en_el_esquema_y_en_la_exportacion(project, capsys):
    base = _example("extraccion_ejemplo")[0]
    for changes in ({"pdr_pct_ms": 17.0}, {"pdr_pct_pb": 120}, {"pdr_pct_ms": -1}):
        with pytest.raises(SystemExit):
            _validate("extraccion", dict(base, **changes))
    _double(project, "e1", _records(_source()))
    out = _export_json(capsys, "--variable", LECHE, "--csv", str(project / "l.csv"))
    (row,) = _csv_rows(project / "l.csv")
    assert (float(row["pdr_pct_pb"]), float(row["pdr_pct_ms"]), float(row["pb_pct_ms"])) == (62.0, 10.2, 16.5)
    assert row["moderadores_no_informados"] == "dcad_meq_kg_ms"
    assert out["moderadores_no_informados"] == {row["estudio"]: ["dcad_meq_kg_ms"]}


def _three_species(project):
    _double(project, "vac", _records(_source(1)))
    _double(project, "ovi", _records(_source(2), especie="ovino", estado_fisiologico="lactación temprana"))
    _double(project, "bov", _records(_source(3), especie="bovino de carne", estado_fisiologico="cebo"))


def test_exportar_filtra_por_especie_y_etapa_y_no_mezcla_especies(project, capsys):
    _three_species(project)
    csv_path = str(project / "l.csv")
    with pytest.raises(SystemExit, match="mezcla especies"):
        main(["extraccion", "exportar", "--variable", LECHE, "--csv", csv_path])
    out = _export_json(capsys, "--variable", LECHE, "--especie", "ovino", "--csv", csv_path)
    assert out["especies"] == {"ovino": 1}                     # 'ovino' no selecciona 'bovino de carne'
    out = _export_json(capsys, "--variable", LECHE, "--especie", "Vacuno Lechero", "--etapa", "transicion",
                       "--csv", csv_path)
    assert out["especies"] == {"vacuno lechero": 1} and len(out["fuera_de_filtro"]) == 2
    (aviso,) = [a for a in out["avisos"] if "dejan fuera" in a]       # lo que el filtro deja fuera, a la vista
    assert "ovino" in aviso and "bovino de carne" in aviso
    assert not any("mezcla" in a or "combinan" in a for a in out["avisos"])
    assert _code(["extraccion", "exportar", "--variable", LECHE, "--etapa", "transición", "--especie", "ovino",
                  "--csv", csv_path]) not in (0, None)          # sin resultados
    out = _export_json(capsys, "--variable", LECHE, "--mezclar-especies", "--csv", csv_path)
    assert out["filas"] == 3 and len(out["especies"]) == 3
    assert any("especies distintas" in a for a in out["avisos"])
    assert any("estados fisiológicos distintos" in a for a in out["avisos"])
    capsys.readouterr()
    assert main(["extraccion", "listar", "--especie", "bovino"]) == 0
    assert all("bovino de carne" in line for line in capsys.readouterr().out.splitlines())


def test_meta_regresion_de_casos_completos(project, capsys):
    forraje = [45.0, 50.0, 55.0, 60.0, None]
    for i, f in enumerate(forraje, 1):
        recs = _records(_source(i), forraje_pct_ms=f)
        recs[0]["media_tratamiento"] = 35.2 + 0.4 * i
        if f is None:
            recs = [{k: v for k, v in r.items() if k != "forraje_pct_ms"} for r in recs]
        _double(project, f"s{i}", recs)
    csv_path = project / "l.csv"
    out = _export_json(capsys, "--variable", LECHE, "--csv", str(csv_path))
    sin_forraje = [r["estudio"] for r in _csv_rows(csv_path) if "forraje_pct_ms" in r["moderadores_no_informados"]]
    assert sin_forraje == ["doi:10.1234/p7.5"]
    assert "forraje_pct_ms" in out["moderadores_no_informados"]["doi:10.1234/p7.5"]
    out = _export_json(capsys, "--variable", LECHE, "--casos-completos", "forraje_pct_ms", "--csv", str(csv_path))
    assert out["filas"] == 4 and out["excluidas_por_casos_completos"] == {
        "E9": {"estudio": "doi:10.1234/p7.5", "faltan": ["forraje_pct_ms"]}}
    capsys.readouterr()
    assert main(["--json", "meta", "analizar", str(csv_path), "--moderador", "forraje_pct_ms"]) == 0
    assert "error" not in json.loads(capsys.readouterr().out)["meta_regresion"]
    with pytest.raises(SystemExit, match="moderadores desconocidos"):
        main(["extraccion", "exportar", "--variable", LECHE, "--casos-completos", "forraje", "--csv", str(csv_path)])


# ================================================================== D4-7 periodo de transición e incidencias
def test_incidencia_exige_ventana_respecto_al_hito_y_definicion_de_caso():
    inc = _example("extraccion_ejemplo")[1]
    _validate("extraccion", inc)
    for campo in ("ventana_inicio_dias", "ventana_fin_dias", "definicion_caso", "hito_referencia",
                  "eventos_tratamiento"):
        with pytest.raises(SystemExit):
            _validate("extraccion", {k: v for k, v in inc.items() if k != campo})
    for changes in ({"definicion_caso": ""}, {"definicion_caso": "-"}, {"definicion_caso": "Cetosis  Subclínica"},
                    {"ventana_inicio_dias": 20}, {"eventos_tratamiento": 25},
                    {"n_control": None, "no_consta": ["media_control", "media_tratamiento", "tipo_dispersion",
                                                      "n_control"]}):
        with pytest.raises(SystemExit):
            _validate("extraccion", dict(inc, **changes))
    continua = _example("extraccion_ejemplo")[0]
    for changes in ({"suplementacion_inicio_dias": 30}, {"hito_referencia": ""}):
        with pytest.raises(SystemExit):
            _validate("extraccion", dict(continua, **changes))
    with pytest.raises(SystemExit):                            # una ventana sin hito no se puede interpretar
        _validate("extraccion", {k: v for k, v in continua.items() if k not in (
            "hito_referencia", "suplementacion_inicio_dias", "suplementacion_fin_dias")})


def test_exportacion_avisa_si_se_mezclan_definiciones_o_ventanas(project, capsys):
    _double(project, "c1", _records(_source(1)))
    _double(project, "c2", _records(_source(2), definicion_caso="BHB en sangre ≥ 1,4 mmol/L", ventana_inicio_dias=1,
                                    ventana_fin_dias=21))
    csv_path = project / "c.csv"
    out = _export_json(capsys, "--variable", CETOSIS, "--csv", str(csv_path))
    assert any("mezcla 2 definiciones de caso" in a for a in out["avisos"])
    assert any("mezcla 2 ventanas" in a for a in out["avisos"])
    assert {r["definicion_caso"] for r in _csv_rows(csv_path)} == {
        "BHB en sangre ≥ 1,2 mmol/L en al menos un muestreo (ejemplo de formato)", "BHB en sangre ≥ 1,4 mmol/L"}
    assert {(r["hito_referencia"], r["ventana_inicio_dias"]) for r in _csv_rows(csv_path)} == {("parto", "3.0"),
                                                                                             ("parto", "1.0")}
    capsys.readouterr()
    assert main(["--json", "meta", "analizar", str(csv_path), "--medida", "RR"]) == 0


def test_exportacion_sin_aviso_si_la_definicion_y_la_ventana_coinciden(project, capsys):
    _double(project, "c1", _records(_source(1)))
    _double(project, "c2", _records(_source(2)))
    out = _export_json(capsys, "--variable", CETOSIS, "--csv", str(project / "c.csv"))
    assert not any("definiciones" in a or "ventanas" in a or "umbral" in a for a in out["avisos"])
    assert out["incidencia"][CETOSIS]["sin_definicion_o_ventana"] == []


def test_exportacion_avisa_de_definiciones_sin_umbral(project, capsys):
    _double(project, "c1", _records(_source(1), definicion_caso="diagnóstico de cetosis por el veterinario"))
    out = _export_json(capsys, "--variable", CETOSIS, "--csv", str(project / "c.csv"))
    assert any("sin umbral numérico" in a for a in out["avisos"])


# ================================================================== D5-12 clasificación regulatoria
@pytest.mark.parametrize("ruta", ["materia_prima", "aditivo_tecnologico", "aditivo_sensorial", "aditivo_nutricional",
                                  "aditivo_zootecnico", "pienso_dietetico", "fuera_de_alcance_medicamento",
                                  "fuera_de_alcance_biocida", "indeterminada", "aditivo_coccidiostatico_histomonostato",
                                  "pienso_complementario", "pienso_compuesto", "premezcla"])
def test_rutas_regulatorias_completas(ruta):
    _validate("oportunidad", dict(_example("oportunidad_ejemplo"), ruta_regulatoria_preliminar=ruta))


def test_grupo_funcional_coherente_con_la_ruta_y_situacion():
    base = _example("oportunidad_ejemplo")
    for ruta, grupo in (("aditivo_zootecnico", "zootecnico.estabilizadores_condiciones_fisiologicas"),
                        ("aditivo_nutricional", "nutricional.aminoacidos"),
                        ("aditivo_tecnologico", "tecnologico.otros_tecnologicos"),
                        ("aditivo_sensorial", "sensorial.aromatizantes"),
                        ("aditivo_coccidiostatico_histomonostato", "coccidiostatico_histomonostato"),
                        ("premezcla", "nutricional.vitaminas"), ("pienso_complementario", "zootecnico.digestibilidad")):
        _validate("oportunidad", dict(base, ruta_regulatoria_preliminar=ruta, grupo_funcional=grupo, situacion="nuevo"))
    for ruta, grupo in (("aditivo_nutricional", "zootecnico.digestibilidad"), ("aditivo_zootecnico", "sensorial.colorantes"),
                        ("aditivo_coccidiostatico_histomonostato", "zootecnico.otros_zootecnicos"),
                        ("materia_prima", "nutricional.vitaminas"), ("indeterminada", "zootecnico.digestibilidad"),
                        ("fuera_de_alcance_medicamento", "zootecnico.digestibilidad"),
                        ("aditivo_zootecnico", "zootecnico.inventado")):
        with pytest.raises(SystemExit):
            _validate("oportunidad", dict(base, ruta_regulatoria_preliminar=ruta, grupo_funcional=grupo))
    with pytest.raises(SystemExit):
        _validate("oportunidad", dict(base, situacion="en_tramite"))


def test_clasificar_registra_la_decision_del_triage_con_su_actor(project, capsys):
    assert main(["oportunidad", "nueva", "--datos", str(EXAMPLES / "oportunidad_ejemplo.json")]) == 0
    assert main(["--agente", "regulacion-seguridad", "oportunidad", "clasificar", "O-0001", "--ruta",
                 "aditivo_zootecnico", "--grupo-funcional", "zootecnico.estabilizadores_condiciones_fisiologicas",
                 "--situacion", "nuevo", "--motivo", "Triage: estabilizador de las condiciones fisiológicas"]) == 0
    opp = db.get_opportunity(_conn(), "O-0001")
    assert (opp["regulatory_route"], opp["functional_group"], opp["regulatory_situation"], opp["classified_by"]) == (
        "aditivo_zootecnico", "zootecnico.estabilizadores_condiciones_fisiologicas", "nuevo", "regulacion-seguridad")
    assert opp["data"]["ruta_regulatoria_preliminar"] == "aditivo_zootecnico"
    assert opp["data"]["grupo_funcional"] == "zootecnico.estabilizadores_condiciones_fisiologicas"
    last = db.list_decisions(_conn(), "O-0001")[-1]
    assert last["decision"].startswith("clasificación regulatoria: indeterminada")
    assert last["decided_by"] == "regulacion-seguridad" and last["rationale"].startswith("Triage")
    # errores: grupo incoherente, oportunidad inexistente, motivo vacío, ruta desconocida, evaluador
    assert main([*REG, "oportunidad", "clasificar", "O-0001", "--ruta", "aditivo_nutricional", "--grupo-funcional",
                 "zootecnico.digestibilidad", "--motivo", "triage"]) == 3
    assert main([*REG, "oportunidad", "clasificar", "O-0099", "--ruta", "premezcla", "--motivo", "triage"]) == 3
    assert main([*REG, "oportunidad", "clasificar", "O-0001", "--ruta", "premezcla", "--motivo", "  "]) == 3
    assert _code([*REG, "oportunidad", "clasificar", "O-0001", "--ruta", "fuera_de_alcance_medicamentos",
                  "--motivo", "x"]) == 2
    with pytest.raises(SystemExit, match="evaluadores"):
        main(["--agente", "evaluador-1", "oportunidad", "clasificar", "O-0001", "--ruta", "premezcla", "--motivo", "x"])
    assert db.get_opportunity(_conn(), "O-0001")["regulatory_route"] == "aditivo_zootecnico"


def test_clasificar_fuera_de_alcance_bloquea_y_salir_exige_firma(project, capsys):
    assert main(["oportunidad", "nueva", "--datos", str(EXAMPLES / "oportunidad_ejemplo.json")]) == 0
    assert main(["puntuar", "compuerta", "O-0001", "regulatoria", "superada", "--motivo", "ok", SIGN, "Ana"]) == 0
    assert db.effective_gates(_conn(), "O-0001", GATES)["regulatoria"] == "superada"
    assert main(["--agente", "regulacion-seguridad", "oportunidad", "clasificar", "O-0001", "--ruta",
                 "fuera_de_alcance_medicamento", "--motivo", "Acción farmacológica: medicamento por función"]) == 0
    assert db.effective_gates(_conn(), "O-0001", GATES)["regulatoria"] == "bloqueada"
    (gate,) = [g for g in db.list_gates(_conn(), "O-0001") if g["gate"] == "regulatoria"]
    assert (gate["status"], gate["decided_by"], gate["human_signoff"]) == ("bloqueada", "registro", None)
    assert "compuerta regulatoria -> bloqueada (automática)" in [d["decision"] for d in db.list_decisions(_conn(), "O-0001")]
    assert main(["--agente", "economia-mercado-pi", "puntuar", "compuerta", "O-0001", "regulatoria", "superada",
                 "--motivo", "x"]) == 3
    # salir de la ruta fuera de alcance exige una persona; la compuerta sigue bloqueada hasta que la revise
    assert main(["--agente", "regulacion-seguridad", "oportunidad", "clasificar", "O-0001", "--ruta",
                 "aditivo_zootecnico", "--motivo", "rediseño"]) == 3
    assert db.get_opportunity(_conn(), "O-0001")["regulatory_route"] == "fuera_de_alcance_medicamento"
    assert main(["oportunidad", "clasificar", "O-0001", "--ruta", "aditivo_zootecnico", "--motivo",
                 "Rediseño revisado: dosis y alegación nutricionales", SIGN, "Ana"]) == 0
    assert db.effective_gates(_conn(), "O-0001", GATES)["regulatoria"] == "bloqueada"
    assert db.list_decisions(_conn(), "O-0001")[-1]["human_signoff"] == "Ana"


def test_reclasificar_reabre_la_compuerta_regulatoria_superada(project, capsys):
    assert main(["oportunidad", "nueva", "--datos", str(EXAMPLES / "oportunidad_ejemplo.json")]) == 0
    assert main([*REG, "oportunidad", "clasificar", "O-0001", "--ruta", "pienso_complementario", "--motivo",
                 "triage"]) == 0
    assert main(["puntuar", "compuerta", "O-0001", "regulatoria", "superada", "--motivo", "ok", SIGN, "Ana"]) == 0
    # repetir la misma clasificación no cambia nada
    assert main([*REG, "oportunidad", "clasificar", "O-0001", "--ruta", "pienso_complementario", "--motivo",
                 "revisado"]) == 0
    assert db.effective_gates(_conn(), "O-0001", GATES)["regulatoria"] == "superada"
    # otra clasificación invalida la decisión anterior
    assert main(["--agente", "regulacion-seguridad", "oportunidad", "clasificar", "O-0001", "--ruta",
                 "aditivo_zootecnico", "--grupo-funcional", "zootecnico.otros_zootecnicos", "--motivo",
                 "Es un aditivo zootécnico nuevo"]) == 0
    (gate,) = [g for g in db.list_gates(_conn(), "O-0001") if g["gate"] == "regulatoria"]
    assert (gate["status"], gate["human_signoff"]) == ("no_resuelta", None)
    assert db.list_decisions(_conn(), "O-0001")[-1]["decision"].startswith("compuerta regulatoria -> no_resuelta")


def test_clasificar_respeta_la_alegacion_de_finalidad_nutricional(project):
    opp = _example("oportunidad_ejemplo")
    opp["alegacion_prevista"] = {"texto": "Reducción del riesgo de fiebre de la leche",
                                 "tipo": "finalidad_nutricional_especifica"}
    opp["ruta_regulatoria_preliminar"] = "pienso_dietetico"
    assert main(["oportunidad", "nueva", "--datos", _write(project / "o.json", opp)]) == 0
    assert main([*REG, "oportunidad", "clasificar", "O-0001", "--ruta", "aditivo_zootecnico", "--motivo", "triage"]) == 3
    assert db.get_opportunity(_conn(), "O-0001")["regulatory_route"] == "pienso_dietetico"


def test_clasificar_sin_firma_solo_el_rol_del_triage(project, capsys, monkeypatch):
    """Sin firma humana, la clasificación la hace regulacion-seguridad; cualquiera puede proponer una ruta
    fuera_de_alcance_* (bloquea, como 'bloqueada'); una persona clasifica con su firma."""
    assert main(["oportunidad", "nueva", "--datos", str(EXAMPLES / "oportunidad_ejemplo.json")]) == 0
    for quien in ([], ["--agente", "director-cientifico"], ["--agente", "regulacion-seguridad-falso"]):
        capsys.readouterr()
        assert main([*quien, "oportunidad", "clasificar", "O-0001", "--ruta", "materia_prima", "--motivo",
                     "triage"]) == 3
        assert "corresponde a regulacion-seguridad" in capsys.readouterr().err
    assert db.get_opportunity(_conn(), "O-0001")["regulatory_route"] == "indeterminada"
    monkeypatch.setenv("NUTRIA_AGENT", "regulacion-seguridad")
    assert main(["oportunidad", "clasificar", "O-0001", "--ruta", "materia_prima", "--situacion", "catalogado",
                 "--motivo", "Triage: materia prima del Catálogo"]) == 0
    monkeypatch.delenv("NUTRIA_AGENT")
    assert main(["--agente", "director-cientifico", "oportunidad", "clasificar", "O-0001", "--ruta",
                 "fuera_de_alcance_biocida", "--motivo", "Se presenta como desinfectante del pezón"]) == 0
    assert db.effective_gates(_conn(), "O-0001", GATES)["regulatoria"] == "bloqueada"
    assert main(["oportunidad", "clasificar", "O-0001", "--ruta", "materia_prima", "--motivo",
                 "Rediseño revisado: sin alegación biocida", SIGN, "Ana"]) == 0
    opp = db.get_opportunity(_conn(), "O-0001")
    assert (opp["regulatory_route"], opp["regulatory_situation"]) == ("materia_prima", None)
