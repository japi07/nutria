"""Bancos de evaluación de modelos (evals/) y su corrección automática (D4-10, D5-14 y D5-16).

- D5-14: `grade` ya no da por buena una respuesta que contiene la esperada negada («No es el 2020/354 sino el
  2019/962»), contradicha («Sí... bueno, no») o junto a una alternativa excluida. Los casos son los de la
  auditoría y la reauditoría.
- D4-10: el banco de dominio tiene preguntas de fisiología y nutrición de rumiantes; cada pregunta lleva una fuente
  comprobable (un documento de docs/ o de .claude/ que existe, una norma de docs/REGULACION_UE.md, un DOI citado en
  docs/, un cálculo [DER] o una orden `nutria` que existe) o el estado "pendiente_validacion". Las respuestas [DER]
  se recalculan aquí con las propias herramientas.
- D5-16: casos de clasificación regulatoria con respuesta esperada; cada uno cita normas que figuran en
  docs/REGULACION_UE.md, y su ruta y su grupo funcional son los del registro.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from nutria.cli import build_parser, main
from nutria.llm.router import PENDIENTE_VALIDACION, grade

ROOT = Path(__file__).resolve().parents[1]
DOMINIO = ROOT / "evals" / "dominio_semilla.jsonl"
CLASIFICACION = ROOT / "evals" / "clasificacion_regulatoria.jsonl"
REGULACION = (ROOT / "docs" / "REGULACION_UE.md").read_text("utf-8")
TIPOS = {"exacta", "contiene", "contiene_todos", "sino", "numerica"}
AREAS = {"regulacion", "estadistica", "fisiologia", "nutricion", "destino_digestivo"}


def _banco(ruta: Path) -> list[dict]:
    return [json.loads(linea) for linea in ruta.read_text("utf-8").splitlines() if linea.strip()]


def _menciona(texto: str, numero: str) -> bool:
    return re.search(r"(?<![\d/])" + re.escape(numero) + r"(?![\d])", texto) is not None


# ============================================================================ D5-14: corrección de las respuestas
CASOS_INCORRECTOS = [
    # (pregunta, respuesta errónea que la corrección por subcadena daba por buena)
    ({"tipo": "contiene", "respuesta_esperada": "zootecnic"}, "No es un aditivo zootécnico: pertenece a los tecnológicos"),
    ({"tipo": "contiene", "respuesta_esperada": "2020/354"}, "No es el 2020/354 sino el 2019/962"),
    ({"tipo": "contiene", "respuesta_esperada": "humano", "excluye": ["pienso"]},
     "No regula los alimentos para consumo humano: regula los piensos"),
    ({"tipo": "contiene", "respuesta_esperada": "humano", "excluye": ["pienso"]}, "Humano y también los piensos"),
    ({"tipo": "contiene", "respuesta_esperada": "2019/962"}, "No fue el 2019/962, sino el 1831/2003"),
    ({"tipo": "contiene", "respuesta_esperada": "2019/962"}, "El Reglamento (UE) 2019/9620"),
    ({"tipo": "contiene", "respuesta_esperada": "8856"}, "No es la 8856; es la 5274"),
    ({"tipo": "contiene", "respuesta_esperada": "8856"}, "Artículo e18856"),
    ({"tipo": "contiene", "respuesta_esperada": "biohidrogenacion"}, "No es la biohidrogenación, sino la lipólisis"),
    ({"tipo": "contiene", "respuesta_esperada": "biohidrogenacion"}, "La lipólisis, en lugar de la biohidrogenación"),
    ({"tipo": "contiene", "respuesta_esperada": "zootecnic"}, "Zootécnico. Bueno, no: no es zootécnico"),
    ({"tipo": "sino", "respuesta_esperada": "si"}, "Sí... bueno, no"),
    ({"tipo": "sino", "respuesta_esperada": "no"}, "Sí... bueno, no"),
    ({"tipo": "sino", "respuesta_esperada": "no"}, "No. Bueno, sí."),
    ({"tipo": "numerica", "respuesta_esperada": 294, "tolerancia": 3}, "No son 294 sino 300"),
    ({"tipo": "contiene_todos", "respuesta_esperada": ["lisina", "metionina"]}, "Lisina, no metionina"),
    ({"tipo": "contiene_todos", "respuesta_esperada": ["hemicelulosa", "celulosa", "lignina"]},
     "Hemicelulosa y lignina"),
]

CASOS_CORRECTOS = [
    ({"tipo": "contiene", "respuesta_esperada": "2020/354"}, "No es el 2019/962 sino el 2020/354"),
    ({"tipo": "contiene", "respuesta_esperada": "2020/354"}, "Reglamento (UE) nº 2020/354"),
    ({"tipo": "contiene", "respuesta_esperada": "2020/354"}, "Regulation (EU) No 2020/354"),
    ({"tipo": "contiene", "respuesta_esperada": "zootecnic"}, "A los zootécnicos, no a los tecnológicos"),
    ({"tipo": "contiene", "respuesta_esperada": "zootecnic"}, "No son tecnológicos ni nutricionales sino zootécnicos"),
    ({"tipo": "contiene", "respuesta_esperada": "humano", "excluye": ["pienso"]},
     "Humano: los nuevos alimentos para consumo humano, no los piensos"),
    ({"tipo": "contiene", "respuesta_esperada": "biohidrogenacion"}, "Biohidrogenación ruminal"),
    ({"tipo": "contiene", "respuesta_esperada": "rumen"}, "El RUMEN degrada la proteína"),
    ({"tipo": "sino", "respuesta_esperada": "si"}, "Sí, siempre que no alegue prevenir enfermedades"),
    ({"tipo": "sino", "respuesta_esperada": "no"}, "No, no puede alegarlo"),
    ({"tipo": "sino", "respuesta_esperada": "no"}, "No, porque se degrada"),
    ({"tipo": "numerica", "respuesta_esperada": 2.771, "tolerancia": 0.02}, "2,77 kg/d"),
    ({"tipo": "numerica", "respuesta_esperada": -0.5, "tolerancia": 0.01}, "−0,5"),
    ({"tipo": "contiene_todos", "respuesta_esperada": ["hemicelulosa", "celulosa", "lignina"]},
     "Hemicelulosa, celulosa y lignina"),
]


@pytest.mark.parametrize(("item", "respuesta"), CASOS_INCORRECTOS)
def test_grade_no_da_por_buena_una_respuesta_negada_contradicha_o_con_una_alternativa_excluida(item, respuesta):
    assert grade(item, respuesta) is False


@pytest.mark.parametrize(("item", "respuesta"), CASOS_CORRECTOS)
def test_grade_acepta_la_respuesta_correcta_aunque_lleve_negaciones_de_otras_cosas(item, respuesta):
    assert grade(item, respuesta) is True


# ============================================================================ D4-10: banco de dominio
def _fuente_comprobable(item: dict) -> str | None:
    """Motivo por el que la fuente no es comprobable, o None si lo es."""
    fuente = item.get("fuente", "")
    if "[DER]" in fuente:
        return None
    for ruta in re.findall(r"((?:docs|\.claude)/[\w./-]+\.md)", fuente):
        if (ROOT / ruta).is_file():
            return None
        return f"{ruta} no existe"
    numeros = re.findall(r"\d{1,4}/\d{1,4}(?:/(?:CE|UE))?", fuente)
    if numeros and all(_menciona(REGULACION, n) for n in numeros):
        return None
    dois = re.findall(r"10\.\d{4,9}/\S+", fuente)
    textos = "".join(p.read_text("utf-8") for p in (ROOT / "docs").glob("*.md"))
    if dois and all(d.rstrip(".,;)") in textos for d in dois):
        return None
    return f"fuente no comprobable: {fuente!r}"


def test_banco_de_dominio_bien_formado_con_fuente_o_pendiente_de_validacion():
    items = _banco(DOMINIO)
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids))
    problemas = []
    for item in items:
        assert {"id", "pregunta", "respuesta_esperada", "tipo", "fuente", "area"} <= set(item), item["id"]
        assert item["tipo"] in TIPOS and item["area"] in AREAS, item["id"]
        if item.get("estado") == PENDIENTE_VALIDACION:
            assert item["fuente"].startswith("pendiente"), item["id"]
            continue
        assert "estado" not in item, item["id"]
        motivo = _fuente_comprobable(item)
        if motivo:
            problemas.append(f"{item['id']}: {motivo}")
    assert problemas == []
    por_area = {a: [i for i in items if i["area"] == a] for a in AREAS}
    assert len(por_area["fisiologia"]) >= 8 and len(por_area["nutricion"]) >= 8


def test_banco_de_dominio_cita_ordenes_nutria_que_existen():
    parser = build_parser()
    for item in _banco(DOMINIO):
        for orden in re.findall(r"nutria [^\[`]+?(?= \[DER\]|`|$)", item["fuente"]):
            try:
                parser.parse_args(orden.split()[1:])
            except SystemExit:
                pytest.fail(f"{item['id']}: orden no válida: {orden}")


def test_respuestas_der_del_banco_recalculadas_con_las_herramientas(project, capsys):
    items = {i["id"]: i for i in _banco(DOMINIO)}

    def json_de(argv):
        assert main(["--json", *argv]) == 0
        return json.loads(capsys.readouterr().out)

    dosis = json_de(["dosis", "convertir", "50", "--desde", "g_animal_dia", "--ingestion-ms", "20", "--portador",
                     "concentrado", "--base", "tal_cual", "--ingestion-pienso", "8"])["dosis"]
    assert dosis["mg_kg_ms"] == pytest.approx(items["nut-02"]["respuesta_esperada"])
    assert dosis["g_t"] == pytest.approx(items["nut-03"]["respuesta_esperada"])
    escape = json_de(["destino", "ruminal", "--fraccion-escape", "0.4", "--dosis", "50",
                      "--digestibilidad-intestinal", "0.8"])["resultados"]
    assert escape["dosis_absorbible"] == pytest.approx(items["des-05"]["respuesta_esperada"])
    mitad = json_de(["destino", "ruminal", "--a", "0", "--b", "1", "--kd", "0.05", "--kp", "0.05"])["resultados"]
    assert mitad["degradabilidad_efectiva"] == pytest.approx(items["des-04"]["respuesta_esperada"])
    lento = json_de(["destino", "ruminal", "--a", "0.1", "--b", "0.8", "--kd", "0.08", "--kp", "0.04"])["resultados"]
    rapido = json_de(["destino", "ruminal", "--a", "0.1", "--b", "0.8", "--kd", "0.08", "--kp", "0.08"])["resultados"]
    assert rapido["degradabilidad_efectiva"] < lento["degradabilidad_efectiva"]
    assert grade(items["des-03"], "Disminuye") and not grade(items["des-03"], "Aumenta")
    assert items["nut-01"]["respuesta_esperada"] == (40 + 300) - (120 + 60)
    assert items["nut-04"]["respuesta_esperada"] == pytest.approx(8 * 0.88)


# ============================================================================ D5-16: casos de clasificación
def _esquema_oportunidad() -> dict:
    return json.loads((ROOT / "src" / "nutria" / "data" / "schemas" / "oportunidad.schema.json").read_text("utf-8"))


def test_casos_de_clasificacion_citan_normas_de_regulacion_ue_y_rutas_del_registro():
    casos = _banco(CLASIFICACION)
    props = _esquema_oportunidad()["properties"]
    rutas = set(props["ruta_regulatoria_preliminar"]["enum"])
    grupos = set(props["grupo_funcional"]["enum"])
    parrafos = re.split(r"\n\s*\n", REGULACION)
    for caso in casos:
        assert caso["tipo"] == "contiene" and caso["area"] == "regulacion", caso["id"]
        esperada = caso["respuesta_esperada"]
        assert esperada in rutas or esperada == "bloqueada", caso["id"]
        assert esperada not in caso["excluye"], caso["id"]
        if "grupo_funcional" in caso:
            assert caso["grupo_funcional"] in grupos, caso["id"]
            assert esperada.startswith("aditivo_") and caso["grupo_funcional"].startswith(esperada[len("aditivo_"):])
        assert caso["norma"], caso["id"]
        for norma in caso["norma"]:
            numero = re.search(r"\d{1,4}/\d{1,4}(?:/(?:CE|UE))?", norma).group(0)
            assert _menciona(REGULACION, numero), f"{caso['id']}: {norma} no figura en docs/REGULACION_UE.md"
            for articulo in re.findall(r"art\. \d+|anexo [IVX]+", norma):
                assert any(_menciona(p, numero) and articulo in p for p in parrafos), (caso["id"], norma, articulo)
        assert f"paso {caso['paso_arbol']}" in caso["fuente"] and 1 <= caso["paso_arbol"] <= 7, caso["id"]
        # el banco funciona con la corrección: la respuesta esperada es correcta y cualquier alternativa, no
        assert grade(caso, esperada), caso["id"]
        assert not grade(caso, caso["excluye"][0]), caso["id"]
        assert not grade(caso, f"{esperada} o {caso['excluye'][0]}"), caso["id"]


def test_casos_de_clasificacion_cubren_las_ramas_del_arbol():
    tipos = {c["tipo_caso"] for c in _banco(CLASIFICACION)}
    for tipo in ("pienso dietético con alegación permitida", "medicamento por alegación", "medicamento por función",
                 "aditivo zootécnico", "aditivo tecnológico", "materia prima", "biocida", "sustancia prohibida"):
        assert tipo in tipos, tipo
