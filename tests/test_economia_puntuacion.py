"""Pruebas del paquete de economía, conversión de dosis, ranking y rúbrica.

Cada prueba fija una corrección de la reauditoría: D1-10, D4-2, D4-11, D8-1, D8-4, D8-7, D8-11, D8-12, D8-13, D8-14 y
la parte de D9-7 del ranking (procedencia reproducible). Las oportunidades y puntuaciones se construyen a partir de
`ejemplos/*.json`; los modelos económicos, de `ejemplos/econ_*.toml` o de fragmentos TOML mínimos cuando la prueba
comprueba una cuenta exacta.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pytest
from scipy import stats

from nutria import db, dosis
from nutria.cli import main
from nutria.config import load_config
from nutria.econ import farm, rnpv
from nutria.econ.dist import gaussian_copula, parse_dist
from nutria.econ.dist import source_ids as dist_source_ids
from nutria.scoring.engine import load_rubric, rank, ranking_warnings, validate_anchors

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "ejemplos"
FARM_EXAMPLE = EXAMPLES / "econ_ganadero_ILUSTRATIVO.toml"
COMPANY_EXAMPLE = EXAMPLES / "econ_desarrollador_ILUSTRATIVO.toml"
SIGN = "--firma-humana"
GATES = ["seguridad", "regulatoria", "analitica", "propiedad_intelectual", "destino_digestivo", "etica"]
OWNER = {"seguridad": "compuestos-toxicologia", "regulatoria": "regulacion-seguridad",
         "analitica": "desarrollo-industrial", "propiedad_intelectual": "economia-mercado-pi",
         "destino_digestivo": "fisiologia-bioquimica", "etica": "sostenibilidad-bienestar"}
RUBRIC = load_rubric()
CRITS = [c.id for c in RUBRIC.criteria]


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, "utf-8")
    return path


def _farm(tmp_path: Path, text: str) -> farm.FarmModel:
    return farm.load_farm_model(_write(tmp_path, "ganadero.toml", text))


def _project(tmp_path: Path, text: str) -> rnpv.Project:
    return rnpv.load_project(_write(tmp_path, "empresa.toml", text))


def _exit_code(argv: list[str]) -> int:
    """Código de salida de la CLI, tanto si `main` lo devuelve como si argparse sale con SystemExit (opción no
    válida)."""
    try:
        return main(argv)
    except SystemExit as exc:
        return int(exc.code)


# ============================================================================================ D4-2: dosis
def test_dosis_tres_bases_y_vuelta():
    """50 g/vaca/día con 20 kg MS y 8 kg de concentrado tal cual: 2500 mg/kg MS y 6250 g/t; y de vuelta."""
    res = dosis.convertir(50, "g_animal_dia", ingestion_ms=20, portador="concentrado", base_pienso="tal_cual",
                          ingestion_pienso=8, precio_eur_kg=5)
    assert res["dosis"] == pytest.approx({"g_animal_dia": 50, "mg_kg_ms": 2500, "g_t": 6250})
    assert res["coste"]["eur_animal_dia"] == pytest.approx(0.25)
    assert res["coste"]["eur_t_pienso"] == pytest.approx(31.25)          # 6250 g/t × 5 €/kg / 1000
    for valor, desde in ((2500, "mg_kg_ms"), (6250, "g_t")):
        back = dosis.convertir(valor, desde, ingestion_ms=20, portador="concentrado", base_pienso="tal_cual",
                               ingestion_pienso=8)
        assert back["dosis"]["g_animal_dia"] == pytest.approx(50)
    assert "[DER]" in dosis.texto_conversion(res)


def test_dosis_racion_completa():
    # En base MS, g/t de ración completa = mg/kg de MS.
    ms = dosis.convertir(50, "g_animal_dia", ingestion_ms=20, portador="racion_completa", base_pienso="ms")
    assert ms["dosis"]["g_t"] == pytest.approx(ms["dosis"]["mg_kg_ms"]) == pytest.approx(2500)
    # Tal cual con 50 % de MS: 40 kg tal cual → 1250 g/t.
    tc = dosis.convertir(50, "g_animal_dia", ingestion_ms=20, portador="racion_completa", base_pienso="tal_cual",
                         ms_pienso=0.5)
    assert tc["ingestion_portador_kg_dia"] == pytest.approx(40)
    assert tc["dosis"]["g_t"] == pytest.approx(1250)


def test_dosis_sin_ingestion_da_desc_y_no_inventa():
    res = dosis.convertir(50, "g_animal_dia")
    assert res["dosis"]["mg_kg_ms"] is None and res["dosis"]["g_t"] is None
    assert "ingestión de MS" in res["faltan"]["mg_kg_ms"]
    assert "[DESC]" in dosis.texto_conversion(res)


@pytest.mark.parametrize("kwargs, message", [
    ({"valor": 4000, "desde": "g_t"}, "pienso portador"),
    ({"valor": 2500, "desde": "mg_kg_ms"}, "ingestión de MS"),
    ({"valor": 50, "desde": "g_animal_dia", "ingestion_ms": 20, "portador": "racion_completa", "base_pienso": "ms",
      "ingestion_pienso": 25}, "difieren"),
    ({"valor": 50, "desde": "g_animal_dia", "ingestion_ms": 20, "portador": "concentrado", "base_pienso": "ms",
      "ingestion_pienso": 25}, "supera"),
    ({"valor": 50, "desde": "g_animal_dia", "ingestion_ms": 20, "portador": "racion_completa",
      "base_pienso": "tal_cual", "ms_pienso": 1.2}, "ms_pienso"),
    ({"valor": 50, "desde": "g_animal_dia", "ingestion_ms": -3}, "> 0"),
    ({"valor": -1, "desde": "g_animal_dia"}, ">= 0"),
    ({"valor": 50, "desde": "g_animal_dia", "portador": "concentrado"}, "a la vez"),
])
def test_dosis_falla_en_cerrado(kwargs, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        dosis.convertir(**kwargs)


def test_cli_dosis_convertir_traza_der(project, capsys):
    assert main(["--json", "--agente", "nutricion-formulacion", "dosis", "convertir", "2500", "--desde", "mg_kg_ms",
                 "--ingestion-ms", "20", "--portador", "concentrado", "--base", "tal_cual",
                 "--ingestion-pienso", "8", "--precio-eur-kg", "5"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dosis"]["g_animal_dia"] == pytest.approx(50) and out["dosis"]["g_t"] == pytest.approx(6250)
    assert out["coste"]["eur_t_pienso"] == pytest.approx(31.25)
    events = [json.loads(x) for x in (project / "research" / "procedencia.jsonl").read_text("utf-8").splitlines()]
    event = [e for e in events if e["action"] == "dosis_convertir"][-1]
    assert event["actor"] == "nutricion-formulacion" and event["inputs"]["base"] == "mg_kg_ms"
    assert main(["dosis", "convertir", "50", "--desde", "g_animal_dia"]) == 0
    assert "[DESC]" in capsys.readouterr().out
    assert main(["dosis", "convertir", "4000", "--desde", "g_t"]) == 2       # g/t sin portador: error, no supuesto


# ============================================================================ D4-2 y D8-13: dosis en el ganadero
_FARM_BASE = """
[escenario]
dias = 10
[coste]
{dosis}
precio_eur_kg = 5
{racion}
[[beneficio]]
nombre = "leche"
respuesta = 1.0
valor_unitario = 0.4
"""


def test_ganadero_admite_dosis_en_cualquier_base(tmp_path):
    racion = ('[racion]\ningestion_ms_kg_dia = 20\nportador = "concentrado"\nbase = "tal_cual"\n'
              'ingestion_pienso_kg_dia = 8')
    costs = {}
    for dose in ("dosis_g_dia = 50", "dosis_mg_kg_ms = 2500", "dosis_g_t = 6250"):
        model = _farm(tmp_path, _FARM_BASE.format(dosis=dose, racion=racion))
        c = farm.scenarios(model, n=50)["valores_centrales"]
        costs[dose] = c["coste"]
        summary = farm.dose_summary(model)
        assert summary["g_animal_dia"] == pytest.approx(50)
        assert summary["mg_kg_ms"] == pytest.approx(2500) and summary["g_t"] == pytest.approx(6250)
        assert summary["coste_producto_eur_t"] == pytest.approx(31.25)  # D8-13: coste por tonelada de pienso
    assert all(v == pytest.approx(0.05 * 5 * 10) for v in costs.values())


@pytest.mark.parametrize("dose, racion, message", [
    ("dosis_g_t = 4000", "", "exige [racion].portador"),
    ("dosis_mg_kg_ms = 2500", "", "ingestion_ms_kg_dia"),
    ("dosis_g_dia = 50\ndosis_mg_kg_ms = 2500", "", "una sola base"),
    ("dosis_g_t = 4000", '[racion]\nportador = "concentrado"\nbase = "tal_cual"', "ingestión del pienso"),
    ("dosis_g_dia = 50", '[racion]\nportador = "pienso"\nbase = "ms"', "portador no válido"),
    ("dosis_g_dia = 50", '[racion]\ningestion_ms_kg_dia = 20\nportador = "racion_completa"\nbase = "ms"\n'
                         'ingestion_pienso_kg_dia = 20', "redundantes"),
    ("dosis_g_dia = 50\nprecio_eur_k = 5", "", "claves desconocidas"),
])
def test_ganadero_dosis_incompleta_o_ambigua_falla(tmp_path, dose, racion, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        _farm(tmp_path, _FARM_BASE.format(dosis=dose, racion=racion))


def test_ganadero_ingestion_incierta_se_propaga(tmp_path):
    """Dosis en mg/kg MS con la ingestión de MS incierta: el coste por animal varía y la ingestión aparece en el
    tornado; con una ingestión que puede ser negativa (normal) el modelo falla en lugar de dar un coste absurdo."""
    racion = '[racion]\ningestion_ms_kg_dia = { dist = "uniforme", min = 15, max = 25 }'
    model = _farm(tmp_path, _FARM_BASE.format(dosis="dosis_mg_kg_ms = 2500", racion=racion))
    mc = farm.monte_carlo(model, 4000, seed=3)
    assert mc["coste_por_animal"]["p05"] < mc["coste_por_animal"]["p95"]
    assert mc["dosis_g_animal_dia"]["p50"] == pytest.approx(50, rel=0.02)
    tornado = {r["entrada"]: r for r in farm.sensitivity(model)}
    assert tornado["racion.ingestion_ms_kg_dia"]["amplitud"] > 0
    bad = '[racion]\ningestion_ms_kg_dia = { dist = "normal", media = 1, de = 5 }'
    model = _farm(tmp_path, _FARM_BASE.format(dosis="dosis_mg_kg_ms = 2500", racion=bad))
    with pytest.raises(ValueError, match="ingestión de MS"):
        farm.monte_carlo(model, 2000, seed=1)


def test_ejemplo_ganadero_da_coste_por_tonelada_y_fuentes():
    model = farm.load_farm_model(FARM_EXAMPLE)
    summary = farm.dose_summary(model)
    assert summary["g_t"] == pytest.approx(50 / 4.0 * 1000)                # 50 g/d en 4 kg de concentrado
    assert summary["coste_producto_eur_t"] == pytest.approx(12500 * 5.0 / 1000)
    assert farm.missing_sources(model) == []                               # cada entrada declara su fuente
    text = farm.report(model, 2000, 1)
    assert "por tonelada de concentrado (tal cual)" in text and "€/t" in text
    assert "Entradas y fuentes:" in text and "[SUP] ILUSTRATIVO" in text


# ============================================================================================ D8-12
def test_respuesta_umbral_coherente_y_declarada():
    """Coste central (moda: 0,05 × 5 × 42 = 10,5 €) frente al valor central (0,42 €/kg × 21 días), no la mediana del
    coste simulado (≈ 10,76 €); y la versión con la cetosis en su valor central (0,01 × 250 = 2,5 €)."""
    model = farm.load_farm_model(FARM_EXAMPLE)
    mc = farm.monte_carlo(model, 5000, seed=11)
    t = mc["respuesta_umbral"]
    assert t["beneficio"].startswith("leche")
    assert t["valor"] == pytest.approx(10.5 / (0.42 * 21))
    assert t["valor_con_otros_centrales"] == pytest.approx((10.5 - 2.5) / (0.42 * 21))
    assert mc["coste_por_animal"]["p50"] > 10.6                              # la mediana difiere de la moda
    assert len(mc["respuestas_umbral"]) == 2
    text = farm.report(model, 2000, 3)
    line = next(x for x in text.splitlines() if x.startswith("- 'leche"))
    assert "EXCLUYE los demás beneficios" in line and "con los demás en su valor central" in line
    assert f"{10.5 / (0.42 * 21):.3f}" in line and f"{8.0 / (0.42 * 21):.3f}" in line
    assert "valores centrales coherentes" in text


# ============================================================================================ D8-13
_TWO_UNIFORMS = """
[coste]
fijo_eur_ciclo = 0.1
[[beneficio]]
nombre = "b1"
respuesta = { dist = "uniforme", min = 0.0, max = 1.0 }
valor_unitario = 1.0
por_dia = false
[[beneficio]]
nombre = "b2"
respuesta = { dist = "uniforme", min = 0.0, max = 1.0 }
valor_unitario = 1.0
por_dia = false
"""


def test_correlaciones_con_copula_gaussiana(tmp_path):
    indep = _farm(tmp_path, _TWO_UNIFORMS)
    corr = _farm(tmp_path, _TWO_UNIFORMS + """
[[correlacion]]
entre = ["beneficio[1].respuesta", "beneficio[2].respuesta"]
rho = 0.8
fuente = "[SUP] prueba"
""")
    draws = farm._draw(corr, 20000, 5)
    rho_s = stats.spearmanr(draws["beneficio[1].respuesta"], draws["beneficio[2].respuesta"])[0]
    assert rho_s == pytest.approx(6 / math.pi * math.asin(0.4), abs=0.02)   # correlación de rangos de la cópula
    # Las marginales no cambian (uniformes en [0, 1]).
    assert np.min(draws["beneficio[1].respuesta"]) >= 0 and np.max(draws["beneficio[1].respuesta"]) <= 1
    # Con correlación positiva la suma es más dispersa: P10 más bajo que con independencia.
    p10 = {name: farm.scenarios(m, 20000, 5)["conservador"]["neto"] for name, m in (("corr", corr), ("indep", indep))}
    assert p10["corr"] < p10["indep"]
    mc = farm.monte_carlo(corr, 2000, 1)
    assert mc["correlaciones"]["entradas"] == ["beneficio[1].respuesta", "beneficio[2].respuesta"]
    assert "cópula gaussiana" in farm.report(corr, 2000, 1)
    # Forma matricial equivalente.
    matrix = _farm(tmp_path, _TWO_UNIFORMS + """
[correlaciones]
variables = ["beneficio[1].respuesta", "beneficio[2].respuesta"]
matriz = [[1.0, 0.8], [0.8, 1.0]]
""")
    assert np.allclose(matrix.corr_matrix, corr.corr_matrix)


@pytest.mark.parametrize("extra, message", [
    ('[[correlacion]]\nentre = ["beneficio[1].respuesta", "coste.fijo_eur_ciclo"]\nrho = 0.5', "valor fijo"),
    ('[[correlacion]]\nentre = ["beneficio[1].respuesta", "inventada"]\nrho = 0.5', "desconocida"),
    ('[correlaciones]\nvariables = ["beneficio[1].respuesta", "beneficio[2].respuesta"]\n'
     'matriz = [[1.0, 0.5], [0.4, 1.0]]', "simétrica"),
    ('[correlaciones]\nvariables = ["beneficio[1].respuesta", "beneficio[2].respuesta"]\n'
     'matriz = [[1.0, 1.5], [1.5, 1.0]]', "entre -1 y 1"),
])
def test_correlaciones_no_validas_fallan(tmp_path, extra, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        _farm(tmp_path, _TWO_UNIFORMS + extra)


def test_matriz_no_definida_positiva_falla():
    names = ["a", "b", "c"]
    with pytest.raises(ValueError, match="definida positiva"):
        gaussian_copula(names, pairs=[("a", "b", 0.9), ("a", "c", 0.9), ("b", "c", -0.9)])
    assert gaussian_copula(names, pairs=[("a", "b", 0.5)]).shape == (3, 3)


def test_tornado_ordena_por_amplitud(tmp_path):
    """Neto = respuesta × valor × 10 días − 5: con U(0, 2) y U(0, 1) los P10/P90 son conocidos."""
    model = _farm(tmp_path, """
[escenario]
dias = 10
[coste]
fijo_eur_ciclo = 5
[[beneficio]]
nombre = "b"
respuesta = { dist = "uniforme", min = 0.0, max = 2.0 }
valor_unitario = { dist = "uniforme", min = 0.0, max = 1.0 }
""")
    rows = farm.sensitivity(model)
    assert [r["entrada"] for r in rows] == ["beneficio[1].respuesta", "beneficio[1].valor_unitario"]
    first, second = rows
    # Respuesta en P10 (0,2) y P90 (1,8) con el valor en su mediana (0,5): neto 1 − 5 y 9 − 5.
    assert (first["neto_p10"], first["neto_p90"]) == pytest.approx((-4.0, 4.0))
    assert first["amplitud"] == pytest.approx(8.0) and second["amplitud"] == pytest.approx(8.0)
    text = farm.report(model, 1000, 1)
    assert "Sensibilidad (tornado)" in text


def test_fuentes_y_exigir_fuentes(tmp_path, project, capsys):
    plain = _write(tmp_path, "sin_fuentes.toml", _FARM_BASE.format(dosis="dosis_g_dia = 50", racion=""))
    model = farm.load_farm_model(plain)
    assert set(farm.missing_sources(model)) == {"escenario.dias", "coste.dosis_g_dia", "coste.precio_eur_kg",
                                                "beneficio[1].respuesta", "beneficio[1].valor_unitario"}
    assert "SIN FUENTE" in farm.report(model, 200, 1)
    assert main(["econ", "ganadero", str(plain), "--sim", "200", "--exigir-fuentes"]) == 2
    assert "--exigir-fuentes" in capsys.readouterr().err
    assert main(["econ", "ganadero", str(FARM_EXAMPLE), "--sim", "200", "--exigir-fuentes"]) == 0
    assert main(["econ", "desarrollador", str(COMPANY_EXAMPLE), "--sim", "200", "--exigir-fuentes"]) == 0
    # Una fuente sin etiqueta no cuenta.
    with_untagged = _write(tmp_path, "sin_etiqueta.toml", FARM_EXAMPLE.read_text("utf-8").replace(
        'fuente = "[SUP] ILUSTRATIVO: precio al ganadero"', 'fuente = "catálogo"'))
    assert farm.missing_sources(farm.load_farm_model(with_untagged)) == ["coste.precio_eur_kg"]
    with pytest.raises(ValueError, match="claves desconocidas"):
        parse_dist({"dist": "pert", "min": 1, "moda": 2, "max": 3, "fuentes": "[SUP] errata"})
    # Días del escenario por defecto (1 día) usados sin declararlos y correlación sin fuente: también cuentan.
    no_days = _farm(tmp_path, '[coste]\ndosis_g_dia = { valor = 50, fuente = "[SUP] x" }\n'
                              'precio_eur_kg = { valor = 5, fuente = "[SUP] x" }\n[[beneficio]]\nnombre = "b"\n'
                              'respuesta = { dist = "uniforme", min = 0, max = 1, fuente = "[SUP] x" }\n'
                              'valor_unitario = { dist = "uniforme", min = 0, max = 1, fuente = "[SUP] x" }\n'
                              '[[correlacion]]\nentre = ["beneficio[1].respuesta", "beneficio[1].valor_unitario"]\n'
                              'rho = 0.3\n')
    assert farm.missing_sources(no_days) == ["escenario.dias (valor por defecto de 1 día sin declarar)",
                                             "correlación beneficio[1].respuesta ~ beneficio[1].valor_unitario"]


def test_distribuciones_con_fuente_y_validacion():
    fixed = parse_dist({"valor": 3, "fuente": "[SUP] prueba"})
    assert (fixed.fixed, fixed.source, fixed.describe()) == (3.0, "[SUP] prueba", "3 (fijo)")
    pert = parse_dist({"dist": "pert", "min": 1, "moda": 2, "max": 4, "fuente": "[PUB] R1"})
    assert pert.central() == 2 and pert.source == "[PUB] R1" and pert.stochastic
    assert np.allclose(pert.ppf(np.array([0.1, 0.9])), [pert.quantile(0.1), pert.quantile(0.9)])
    for bad in (True, {"dist": "normal", "media": 1, "de": 0}, {"dist": "lognormal", "media_log": 0, "de_log": -1},
                {"dist": "uniforme", "min": 2, "max": 1}, {"valor": "50"}, {"valor": 1, "fuente": ""},
                {"dist": "gamma", "forma": 2}, {"fuente": "[SUP] sin valor"}, float("nan")):
        with pytest.raises(ValueError):
            parse_dist(bad)


_ALTERNATIVE = """
[alternativa]
nombre = "producto Y"
fuente = "[SUP] prueba"
[alternativa.coste]
dosis_g_dia = {dosis}
precio_eur_kg = {precio}
[alternativa.respuesta]
leche = {respuesta}
"""


def test_alternativa_en_las_mismas_simulaciones(tmp_path):
    base = _FARM_BASE.format(dosis="dosis_g_dia = 50", racion="").replace(
        "respuesta = 1.0", 'respuesta = { dist = "uniforme", min = 0.5, max = 1.5 }').replace(
        "valor_unitario = 0.4", 'valor_unitario = { dist = "uniforme", min = 0.3, max = 0.5 }')
    alone = _farm(tmp_path, base)
    same = _farm(tmp_path, base + _ALTERNATIVE.format(dosis=50, precio=5, respuesta='{ dist = "uniforme", '
                                                                                  'min = 0.5, max = 1.5 }'))
    cheaper = _farm(tmp_path, base + _ALTERNATIVE.format(dosis=50, precio=3, respuesta=0.0))
    # Añadir una alternativa no cambia las simulaciones del producto.
    a, b = farm.scenarios(alone, 3000, 2), farm.scenarios(same, 3000, 2)
    assert [a[k]["neto"] for k in ("conservador", "intermedio", "optimista")] == \
        [b[k]["neto"] for k in ("conservador", "intermedio", "optimista")]
    res = farm._simulate(cheaper, 3000, 2)
    # Respuesta nula de la alternativa: diferencia = neto del producto + coste de la alternativa (0,05 × 3 × 10).
    assert np.allclose(res["diferencia_frente_alternativa"], res["neto"] + 1.5)
    mc = farm.monte_carlo(same, 3000, 2)
    assert mc["alternativa"]["nombre"] == "producto Y"
    assert 0.2 < mc["alternativa"]["p_producto_mejor"] < 0.8
    text = farm.report(cheaper, 1000, 1)
    assert "Comparación con la alternativa 'producto Y'" in text and "P(producto > alternativa)" in text
    nothing = _farm(tmp_path, base + '[alternativa]\nnombre = "no hacer nada"\nno_hacer_nada = true\n')
    sims = farm._simulate(nothing, 500, 1)
    assert np.allclose(sims["diferencia_frente_alternativa"], sims["neto"])
    with pytest.raises(ValueError, match="falta la respuesta"):
        _farm(tmp_path, base + '[alternativa]\nnombre = "Y"\n[alternativa.coste]\ndosis_g_dia = 1\n'
                               'precio_eur_kg = 1\n[alternativa.respuesta]\n')


# ============================================================================ D1-10 y D8-14: ventas y coherencia
_COMPANY = """
[proyecto]
tasa_descuento = 0.0
[[fase]]
nombre = "unica"
coste_eur = 100
duracion_anios = 1
prob_exito = 1.0
[mercado]
{mercado}
anios_hasta_pico = 1
margen_bruto = 0.5
costes_comerciales_pct = 0.1
"""
_BUILT = """censo_direccionable = 1000
adopcion_pico = 0.1
ciclos_por_animal_anio = 1
dias_por_ciclo = 10
dosis_g_dia = 50
precio_eur_kg = 5
margen_distribucion = 0.2"""


def test_ventas_construidas_censo_adopcion_dosis_dias_precio(tmp_path):
    project = _project(tmp_path, _COMPANY.format(mercado=_BUILT + "\nanios_comercializacion = 1"))
    # 1000 × 0,1 × 1 × 10 días × 0,05 kg × 5 €/kg × 0,8 = 200 €/año.
    res = rnpv.evaluate(project, n=50)
    assert res["ventas_construidas"] and res["ventas_pico_central"] == pytest.approx(200.0)
    assert res["rnpv_central"] == pytest.approx(-100 + 200 * 0.4)
    with pytest.raises(ValueError, match="una sola forma"):
        _project(tmp_path, _COMPANY.format(mercado=_BUILT + "\nventas_pico_eur_anio = 1000"))
    with pytest.raises(ValueError, match="faltan"):
        _project(tmp_path, _COMPANY.format(mercado="censo_direccionable = 1000\nadopcion_pico = 0.1"))
    with pytest.raises(ValueError, match="fuera de"):
        rnpv.evaluate(_project(tmp_path, _COMPANY.format(mercado=_BUILT.replace("adopcion_pico = 0.1",
                                                                                  "adopcion_pico = 1.5"))), n=10)


def test_anios_como_distribucion_y_supuestos_estructurales(tmp_path):
    base = "ventas_pico_eur_anio = 1000\n"
    # 1,5 años de comercialización: el segundo año cuenta la mitad (tasa 0): 400 + 200.
    half = _project(tmp_path, _COMPANY.format(mercado=base + "anios_comercializacion = 1.5"))
    assert rnpv.evaluate(half, n=10)["rnpv_central"] == pytest.approx(-100 + 400 + 200)
    # Impuesto del 25 %, erosión del 10 % anual, lanzamiento 50 y renovación de 30 en el año 1 (2 años en mercado).
    structural = ("anios_comercializacion = 2\nimpuesto_beneficios = 0.25\nerosion_precio_anual = 0.1\n"
                  "inversion_lanzamiento_eur = 50\ncoste_renovacion_eur = 30\nanio_renovacion = 1")
    full = _project(tmp_path, _COMPANY.format(mercado=base + structural))
    expected = -100 + 400 * 0.75 + 400 * 0.9 * 0.75 - 50 - 30
    assert rnpv.evaluate(full, n=10)["rnpv_central"] == pytest.approx(expected)
    # Valor terminal: 3 × beneficio del último año (400 × 0,9 × 0,75 = 270) al final de la comercialización.
    terminal = _project(tmp_path, _COMPANY.format(mercado=base + structural + "\nmultiplo_valor_terminal = 3"))
    assert rnpv.evaluate(terminal, n=10)["rnpv_central"] == pytest.approx(expected + 3 * 270)
    # Años inciertos: el rNPV varía entre simulaciones y entran en el tornado.
    dist = _project(tmp_path, _COMPANY.format(mercado=base + 'anios_comercializacion = { dist = "uniforme", min = 1, '
                                                             'max = 3 }').replace(
        "anios_hasta_pico = 1", 'anios_hasta_pico = { dist = "triangular", min = 1, moda = 2, max = 4 }'))
    res = rnpv.evaluate(dist, n=4000, seed=1)
    assert res["rnpv_p05"] < res["rnpv_p95"]
    names = {r["entrada"] for r in rnpv.sensitivity(dist)}
    assert {"mercado.anios_comercializacion", "mercado.anios_hasta_pico", "proyecto.tasa_descuento"} <= names
    with pytest.raises(ValueError, match="van juntos"):
        _project(tmp_path, _COMPANY.format(mercado=base + "coste_renovacion_eur = 30"))


def test_coherencia_de_los_ejemplos_y_discrepancias(tmp_path, project, capsys):
    model = farm.load_farm_model(FARM_EXAMPLE)
    company = rnpv.load_project(COMPANY_EXAMPLE)
    ok = rnpv.coherence(model, company)
    assert ok["coherente"], ok
    assert {i["magnitud"] for i in ok["comparaciones"]} == {"precio que paga el ganadero (€/kg)",
                                                            "dosis (g/animal/día)", "días de uso por ciclo"}
    assert main(["econ", "coherencia", str(FARM_EXAMPLE), str(COMPANY_EXAMPLE)]) == 0
    text = COMPANY_EXAMPLE.read_text("utf-8")
    variants = {
        "precio": text.replace('precio_eur_kg = { dist = "pert", min = 4.0, moda = 5.0, max = 7.0',
                               'precio_eur_kg = { dist = "pert", min = 4.0, moda = 6.0, max = 7.0'),
        "incertidumbre": text.replace('precio_eur_kg = { dist = "pert", min = 4.0, moda = 5.0, max = 7.0',
                                      'precio_eur_kg = { dist = "pert", min = 4.5, moda = 5.0, max = 6.0'),
        "dosis": text.replace("dosis_g_dia = { valor = 50", "dosis_g_dia = { valor = 40"),
        "dias": text.replace("dias_por_ciclo = { valor = 42", "dias_por_ciclo = { valor = 60"),
        "especie": text.replace('especie = "vacuno lechero"', 'especie = "vacuno de carne"'),
    }
    for name, variant in variants.items():
        path = _write(tmp_path, f"empresa_{name}.toml", variant)
        res = rnpv.coherence(model, rnpv.load_project(path))
        assert not res["coherente"], name
        assert any(i["estado"] == "difiere" for i in res["comparaciones"]), name
        capsys.readouterr()
        assert main(["econ", "coherencia", str(FARM_EXAMPLE), str(path)]) == 1, name
        assert "AVISO DE COHERENCIA" in capsys.readouterr().out
    # La opción --ganadero de econ desarrollador hace la misma comprobación (salida 1 si no coinciden).
    assert main(["econ", "desarrollador", str(_write(tmp_path, "e.toml", variants["precio"])), "--sim", "200",
                 "--ganadero", str(FARM_EXAMPLE)]) == 1
    assert "AVISO DE COHERENCIA" in capsys.readouterr().out
    assert main(["econ", "desarrollador", str(COMPANY_EXAMPLE), "--sim", "200", "--ganadero", str(FARM_EXAMPLE)]) == 0


def test_coherencia_con_dosis_convertida_y_ventas_como_dato(tmp_path):
    # Dosis del ganadero en mg/kg de MS: 2500 × 20 kg MS / 1000 = 50 g/día, la de la empresa.
    farm_text = FARM_EXAMPLE.read_text("utf-8").replace(
        'dosis_g_dia = { valor = 50, fuente = "[SUP] ILUSTRATIVO" }',
        'dosis_mg_kg_ms = { valor = 2500, fuente = "[SUP] prueba" }').replace(
        'ingestion_ms_kg_dia = { dist = "pert", min = 11.0, moda = 13.0, max = 15.0,',
        'ingestion_ms_kg_dia = { dist = "pert", min = 18.0, moda = 20.0, max = 22.0,')
    model = farm.load_farm_model(_write(tmp_path, "g.toml", farm_text))
    res = rnpv.coherence(model, rnpv.load_project(COMPANY_EXAMPLE))
    dose = next(i for i in res["comparaciones"] if i["magnitud"].startswith("dosis"))
    # Mismo valor central (50 g/día), pero la dosis del ganadero varía con la ingestión (2,5 × pert(18, 20, 22)) y la
    # empresa la declara fija: el P5-P95 simulado no coincide y se avisa.
    assert dose["ganadero"] == pytest.approx(50) and dose["estado"] == "difiere"
    assert "distinta incertidumbre" in dose["detalle"] and "simulaciones" in dose["detalle"]
    # Con la misma distribución en g/animal/día (pert(45, 50, 55)) es coherente.
    company = _write(tmp_path, "e_pert.toml", COMPANY_EXAMPLE.read_text("utf-8").replace(
        "dosis_g_dia = { valor = 50", 'dosis_g_dia = { dist = "pert", min = 45, moda = 50, max = 55'))
    dose = next(i for i in rnpv.coherence(model, rnpv.load_project(company))["comparaciones"]
                if i["magnitud"].startswith("dosis"))
    assert dose["estado"] == "coherente", dose
    # Una dosis de la empresa mucho más incierta con el mismo valor central tampoco pasa.
    wide = _write(tmp_path, "e_wide.toml", COMPANY_EXAMPLE.read_text("utf-8").replace(
        "dosis_g_dia = { valor = 50", 'dosis_g_dia = { dist = "pert", min = 10, moda = 50, max = 200'))
    dose = next(i for i in rnpv.coherence(model, rnpv.load_project(wide))["comparaciones"]
                if i["magnitud"].startswith("dosis"))
    assert dose["estado"] == "difiere"
    # Ventas pico como dato: no comprobable (falla) y da los animales-ciclo implícitos.
    as_data = _project(tmp_path, _COMPANY.format(mercado="ventas_pico_eur_anio = 105000\nanios_comercializacion = 2"))
    res = rnpv.coherence(farm.load_farm_model(FARM_EXAMPLE), as_data)
    assert not res["coherente"]
    item = res["comparaciones"][0]
    assert item["estado"] == "no_comprobable"
    assert item["animales_ciclo_implicitos"] == pytest.approx(105000 / (5.0 * 0.05 * 42))


# ============================================================================================ D8-7
_TWO_PHASES = """
[proyecto]
tasa_descuento = 0.0
{umbral}
[[fase]]
nombre = "A"
coste_eur = 100
duracion_anios = 1
prob_exito = 0.5
[[fase]]
nombre = "B"
coste_eur = 100
duracion_anios = 1
prob_exito = 0.5
[mercado]
ventas_pico_eur_anio = 1000
anios_hasta_pico = 1
anios_comercializacion = 2
margen_bruto = 0.5
costes_comerciales_pct = 0.1
"""


def test_probabilidad_de_perdida_con_umbral_configurable(tmp_path):
    """Resultados: −100 (p 0,5), −200 (p 0,25) y 600 (p 0,25)."""
    default = rnpv.evaluate(_project(tmp_path, _TWO_PHASES.format(umbral="")), n=20000, seed=3)
    assert default["umbral_perdida_eur"] == pytest.approx(100.0)          # 50 % de 200 € de desarrollo
    assert "por defecto" in default["umbral_perdida_origen"]
    assert default["p_perdida_mayor_umbral"] == pytest.approx(0.25, abs=0.02)
    assert default["p_vpn_realizado_negativo"] == pytest.approx(0.75, abs=0.02)
    declared = rnpv.evaluate(_project(tmp_path, _TWO_PHASES.format(umbral="umbral_perdida_eur = 50")), n=20000,
                             seed=3)
    assert declared["p_perdida_mayor_umbral"] == pytest.approx(0.75, abs=0.02)
    override = rnpv.evaluate(_project(tmp_path, _TWO_PHASES.format(umbral="umbral_perdida_eur = 50")), n=20000,
                             seed=3, loss_threshold=500)
    assert override["p_perdida_mayor_umbral"] == 0.0
    by_outcome = {o["resultado"]: o for o in default["resultados_por_fase"]}
    assert by_outcome["fracasa en 'A'"]["vpn_medio"] == pytest.approx(-100)
    assert by_outcome["fracasa en 'B'"]["vpn_medio"] == pytest.approx(-200)
    assert by_outcome["llega al mercado"]["vpn_medio"] == pytest.approx(600)
    assert by_outcome["llega al mercado"]["probabilidad"] == pytest.approx(0.25, abs=0.02)
    pct = default["vpn_realizado_percentiles"]
    assert pct["p05"] == pytest.approx(-200) and pct["p95"] == pytest.approx(600)


def test_informe_empresa_explica_las_dos_metricas_y_la_perdida(tmp_path, project, capsys):
    text = rnpv.report(rnpv.load_project(COMPANY_EXAMPLE), 2000, 5)
    for fragment in ("No es la probabilidad de que el proyecto gane dinero", "Distribución del VPN realizado",
                     "P(VPN realizado < 0)", "P(pérdida > 1,000,000 €)", "Por resultado:", "llega al mercado",
                     "Sensibilidad (tornado)", "Ventas pico (central)"):
        assert fragment in text, fragment
    assert main(["--json", "econ", "desarrollador", str(COMPANY_EXAMPLE), "--sim", "500",
                 "--umbral-perdida", "2000000"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["umbral_perdida_eur"] == 2000000 and out["umbral_perdida_origen"] == "declarado"
    assert out["sensibilidad"] and out["entradas"] and out["coherencia"] is None
    assert main(["econ", "desarrollador", str(COMPANY_EXAMPLE), "--sim", "200"]) == 0
    assert "no se ha comprobado la coherencia con el modelo del ganadero" in capsys.readouterr().out
    assert main(["econ", "desarrollador", str(COMPANY_EXAMPLE), "--sim", "200", "--umbral-perdida", "-1"]) == 2


@pytest.mark.parametrize("which, text, message", [
    ("ganadero", "[notas]\ntexto = 'x'\n", "Secciones desconocidas"),
    ("ganadero", "", None),
    ("empresa", "tasa = 0.05", "claves desconocidas"),
    ("empresa", "[notas]\ntexto = 'x'", "Secciones desconocidas"),
])
def test_modelos_rechazan_secciones_y_claves_desconocidas(tmp_path, which, text, message):
    if which == "ganadero":
        base = _FARM_BASE.format(dosis="dosis_g_dia = 50", racion="")
        if message is None:  # por_dia debe ser booleano y los días no pueden ser negativos
            with pytest.raises(ValueError, match="true o false"):
                _farm(tmp_path, base.replace('nombre = "leche"', 'nombre = "leche"\npor_dia = "no"'))
            model = _farm(tmp_path, base.replace("dias = 10", 'dias = { dist = "normal", media = 1, de = 5 }'))
            with pytest.raises(ValueError, match="escenario.dias debe ser >= 0"):
                farm.monte_carlo(model, 2000, 1)
            return
        with pytest.raises(ValueError, match=message):
            _farm(tmp_path, base + text)
    else:
        with pytest.raises(ValueError, match=message):
            _project(tmp_path, _TWO_PHASES.format(umbral=text) if text.startswith("tasa")
                     else _TWO_PHASES.format(umbral="") + text)


def test_skill_y_agente_explican_la_economia():
    skill = " ".join((ROOT / ".claude" / "skills" / "evaluacion-economica" / "SKILL.md").read_text("utf-8").split())
    agent = " ".join((ROOT / ".claude" / "agents" / "economia-mercado-pi.md").read_text("utf-8").split())
    for fragment in ("/ 1000", "por tonelada", "nutria dosis convertir", "P(pérdida > umbral)", "VPN realizado",
                     "no es la probabilidad de que el proyecto gane dinero", "nutria econ coherencia",
                     "--exigir-fuentes", "tornado", "cópula gaussiana", "[alternativa]", "EXCLUYE"):
        assert fragment in skill, fragment
    assert "dosis (g/t) × precio;" not in skill                           # fórmula antigua sin /1000
    for fragment in ("P(pérdida > umbral)", "VPN realizado", "nutria econ coherencia", "censo direccionable"):
        assert fragment in agent, fragment


# ============================================================================================ D8-11
def _score_items(value=0.5) -> list[dict]:
    base = json.loads((EXAMPLES / "puntuacion_ejemplo.json").read_text("utf-8"))
    for item in base["puntuaciones"]:
        item.update(valor=value, min=0.0, max=1.0)
    return base["puntuaciones"]


def test_validate_anchors():
    items = _score_items(0.5)
    assert validate_anchors(RUBRIC, items) == items
    off = _score_items(0.37)
    with pytest.raises(ValueError, match=r"0\.37 fuera de las anclas.*0\.25 o 0\.5"):
        validate_anchors(RUBRIC, off)
    off[0]["justificacion_entre_anclas"] = "breve"                         # demasiado corta: no vale
    with pytest.raises(ValueError):
        validate_anchors(RUBRIC, off)
    warnings = []
    for item in off:
        item["justificacion_entre_anclas"] = "Dos ensayos concordantes y uno discrepante: entre 0,25 y 0,5."
    out = validate_anchors(RUBRIC, off, warn=warnings.append)
    assert len(warnings) == len(off) and all("entre las anclas 0.25 y 0.5" in w for w in warnings)
    assert all("[Valor entre anclas 0.25 y 0.5: Dos ensayos" in o["justificacion"] for o in out)
    assert validate_anchors(RUBRIC, _score_items(0.5 + 1e-12)) is not None  # tolerancia numérica


def _register_opportunity(title: str) -> None:
    data = json.loads((EXAMPLES / "oportunidad_ejemplo.json").read_text("utf-8"))
    data["titulo"] = title
    path = Path.cwd() / f"op_{abs(hash(title))}.json"
    path.write_text(json.dumps(data), "utf-8")
    assert main(["oportunidad", "nueva", "--datos", str(path)]) == 0


def _register_scores(opp: str, rater: str, value: float, extra: dict | None = None) -> int:
    data = json.loads((EXAMPLES / "puntuacion_ejemplo.json").read_text("utf-8"))
    data["oportunidad"], data["evaluador"] = opp, rater
    for item in data["puntuaciones"]:
        item.update(valor=value, min=max(0.0, value - 0.25), max=min(1.0, value + 0.25), **(extra or {}))
    path = Path.cwd() / f"{opp}_{rater}.json"
    path.write_text(json.dumps(data), "utf-8")
    return main(["--agente", rater, "puntuar", "registrar", "--datos", str(path)])


def test_cli_registrar_rechaza_valores_fuera_de_ancla(project, capsys):
    _register_opportunity("Oportunidad de prueba de anclas")
    assert _register_scores("O-0001", "evaluador-1", 0.37) == 2
    assert "fuera de las anclas" in capsys.readouterr().err
    conn = db.connect(load_config().db_path)
    assert db.list_scores(conn, "O-0001") == []                            # no se registró nada
    assert _register_scores("O-0001", "evaluador-1", 0.37, {
        "justificacion_entre_anclas": "Evidencia intermedia entre dos anclas, justificada."}) == 0
    assert "entre las anclas" in capsys.readouterr().err
    stored = db.list_scores(conn, "O-0001")
    assert len(stored) == 9 and all("Valor entre anclas" in s["rationale"] for s in stored)
    assert _register_scores("O-0001", "evaluador-2", 0.5) == 0


def test_ranking_senala_valores_entre_anclas():
    opps = [_engine_opp("O-0001", 0.6, dict.fromkeys(RUBRIC.gate_ids, "superada"))]
    row = rank(opps, RUBRIC, n_sim=50)[0]
    assert any(f.startswith("Valores entre anclas") and "evidencia 0.6" in f for f in row.flags)


def test_rubrica_documenta_las_anclas():
    md = RUBRIC.to_markdown()
    assert "justificacion_entre_anclas" in md and "rechaza el fichero entero" in md
    skill = (ROOT / ".claude" / "skills" / "puntuacion" / "SKILL.md").read_text("utf-8")
    assert "justificacion_entre_anclas" in skill


# ============================================================================================ D8-1
def _engine_opp(oid, value, gates, proposals=None, raters=("e1", "e2", "e3")):
    scores = [{"rater": r, "criterion": c, "value": value, "low": max(0, value - 0.25), "high": min(1, value + 0.25)}
              for r in raters for c in CRITS]
    opp = {"id": oid, "title": oid, "scores": scores, "gates": gates, "status": "en_evaluacion"}
    if proposals is not None:
        opp["gate_proposals"] = proposals
    return opp


def test_condicionales_por_subgrupos_sea_cual_sea_la_puntuacion():
    unsigned = dict.fromkeys(GATES, "no_resuelta")                        # nada firmado
    all_proposed = dict.fromkeys(GATES, "superada")
    opps = [
        _engine_opp("O-0001", 1.0, unsigned, {**all_proposed, "seguridad": "no_resuelta"}),  # crítica sin resolver
        _engine_opp("O-0002", 0.25, unsigned, all_proposed),                                   # solo falta la firma
        _engine_opp("O-0003", 0.75, unsigned, {**all_proposed, "etica": "no_resuelta"}),       # no crítica
        _engine_opp("O-0004", 0.75, unsigned, {}),                                             # sin propuestas
        _engine_opp("O-0005", 0.0, dict.fromkeys(GATES, "superada")),                          # APTA
    ]
    rows = rank(opps, RUBRIC, n_sim=500, top_k=3)
    assert [r.opportunity_id for r in rows] == ["O-0005", "O-0002", "O-0003", "O-0001", "O-0004"]
    assert {r.opportunity_id: r.gate_tier for r in rows} == {"O-0005": None, "O-0002": 0, "O-0003": 1, "O-0001": 2,
                                                              "O-0004": 2}
    by_id = {r.opportunity_id: r for r in rows}
    assert by_id["O-0001"].critical_unresolved == ["seguridad"]
    assert by_id["O-0002"].gates_awaiting_signature == GATES and by_id["O-0002"].gates_unresolved == []
    assert any(f.startswith("COMPUERTAS CRÍTICAS SIN RESOLVER: seguridad") for f in by_id["O-0001"].flags)
    # Sin propuestas conocidas: todo lo no superado cuenta como sin resolver (fallo en cerrado).
    assert set(by_id["O-0004"].critical_unresolved) == {"seguridad", "regulatoria", "destino_digestivo"}
    # El top-3 (O-0005, O-0002, O-0003) no tiene compuertas críticas sin resolver y el orden no depende de los pesos.
    assert ranking_warnings(rows, 3) == []
    # Con el top-4 entra O-0001 y se avisa.
    assert any("O-0001 (seguridad)" in w for w in ranking_warnings(rank(opps, RUBRIC, n_sim=500, top_k=4), 4))


def test_aviso_si_el_top_k_incluye_compuertas_criticas():
    unsigned = dict.fromkeys(GATES, "no_resuelta")
    all_proposed = dict.fromkeys(GATES, "superada")
    opps = [_engine_opp("O-0001", 1.0, unsigned, {**all_proposed, "destino_digestivo": "no_resuelta"}),
            _engine_opp("O-0002", 0.5, unsigned, all_proposed)]
    rows = rank(opps, RUBRIC, n_sim=200, top_k=2)
    warnings = ranking_warnings(rows, 2)
    assert any("top-2 para planes incluye candidatas con compuertas CRÍTICAS" in w and "O-0001 (destino_digestivo)"
               in w for w in warnings)
    assert any("En el top-2 para planes con compuertas críticas" in f for f in rows[1].flags)
    assert not any("CRÍTICAS" in w for w in ranking_warnings(rank(opps, RUBRIC, n_sim=200, top_k=1), 1))


def test_cli_ranking_con_propuestas_reales(project, capsys):
    """Caso del escéptico: O-0001 con seguridad no_resuelta y puntuación alta frente a O-0002 con todas las
    compuertas propuestas superadas y puntuación menor: O-0002 va primera y el top-2 avisa de O-0001."""
    _register_opportunity("Alta con seguridad sin resolver")
    _register_opportunity("Baja con todo propuesto")
    for rater in ("evaluador-1", "evaluador-2", "evaluador-3"):
        assert _register_scores("O-0001", rater, 0.75) == 0
        assert _register_scores("O-0002", rater, 0.5) == 0
    for opp in ("O-0001", "O-0002"):
        for gate in GATES:
            state = "no_resuelta" if (opp, gate) == ("O-0001", "seguridad") else "superada"
            assert main(["--agente", OWNER[gate], "puntuar", "compuerta", opp, gate, state, "--motivo",
                         "incertidumbre crítica" if state == "no_resuelta" else "propuesta"]) == 0
    capsys.readouterr()
    assert main(["--json", "--agente", "director-cientifico", "puntuar", "ranking", "--sim", "300", "--top",
                 "2"]) == 0
    captured = capsys.readouterr()
    rows = json.loads(captured.out)
    assert [r["opportunity_id"] for r in rows] == ["O-0002", "O-0001"]
    assert [r["status"] for r in rows] == ["CONDICIONAL", "CONDICIONAL"]
    assert [r["gate_tier"] for r in rows] == [0, 2]
    assert "compuertas CRÍTICAS sin resolver" in captured.err
    assert main(["--agente", "director-cientifico", "puntuar", "ranking", "--sim", "300", "--top", "2"]) == 0
    text = capsys.readouterr().out
    assert text.index("[CONDICIONAL: solo pendientes de firma humana]") < text.index(
        "[CONDICIONAL: compuertas CRÍTICAS sin resolver]")
    assert "AVISOS:" in text
    # La firma humana de todas las compuertas de O-0002 la convierte en APTA; O-0001 sigue detrás.
    for gate in GATES:
        assert main(["puntuar", "compuerta", "O-0002", gate, "superada", "--motivo", "revisado", SIGN, "Ana"]) == 0
    capsys.readouterr()
    assert main(["--json", "puntuar", "ranking", "--sim", "300"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["opportunity_id"], r["status"]) for r in rows] == [("O-0002", "APTA"), ("O-0001", "CONDICIONAL")]


# ============================================================================================ D8-4
def _profile_case():
    base = dict.fromkeys(CRITS, 0.5)
    a = dict(base, plausibilidad=1.0, originalidad=1.0, viabilidad_regulatoria=0.25, rentabilidad_ganadero=0.25,
             atractivo_empresa=0.25)
    b = dict(base, plausibilidad=0.25, originalidad=0.25, viabilidad_regulatoria=1.0, rentabilidad_ganadero=1.0,
             atractivo_empresa=1.0)
    out = []
    for oid, vals in (("O-A", a), ("O-B", b)):
        opp = _engine_opp(oid, 0.5, dict.fromkeys(RUBRIC.gate_ids, "superada"))
        opp["scores"] = [dict(s, value=vals[s["criterion"]], low=vals[s["criterion"]], high=vals[s["criterion"]])
                         for s in opp["scores"]]
        out.append(opp)
    return out


def test_aviso_si_el_orden_cambia_entre_perfiles():
    rows = rank(_profile_case(), RUBRIC, n_sim=2000, top_k=1)
    assert {r.opportunity_id: r.ranks_by_profile["original"] for r in rows} == {"O-A": 1, "O-B": 2}
    assert {r.opportunity_id: r.ranks_by_profile["recomendado"] for r in rows} == {"O-A": 2, "O-B": 1}
    warnings = ranking_warnings(rows, 1)
    assert any(w.startswith("AVISO: el orden cambia según el perfil de pesos") for w in warnings)
    assert any(w.startswith("AVISO: el conjunto del top-1 cambia") for w in warnings)
    assert all(any("La posición cambia con el perfil de pesos" in f for f in r.flags) for r in rows)
    from nutria.scoring.engine import format_table
    assert "AVISOS:" in format_table(rows, 1)
    # Sin dependencia de los pesos no hay aviso.
    robust = rank([_engine_opp("O-1", 0.75, dict.fromkeys(RUBRIC.gate_ids, "superada")),
                   _engine_opp("O-2", 0.25, dict.fromkeys(RUBRIC.gate_ids, "superada"))], RUBRIC, n_sim=500)
    assert ranking_warnings(robust, 3) == []


# ============================================================================================ D9-7 (ranking)
def test_ranking_registra_procedencia_reproducible(project, capsys):
    _register_opportunity("Procedencia del ranking")
    for rater in ("evaluador-1", "evaluador-2", "evaluador-3"):
        assert _register_scores("O-0001", rater, 0.5) == 0
    for _ in range(2):
        assert main(["--agente", "director-cientifico", "puntuar", "ranking", "--sim", "400", "--semilla", "7",
                     "--alfa", "30", "--perfil", "original"]) == 0
    events = [json.loads(x) for x in (project / "research" / "procedencia.jsonl").read_text("utf-8").splitlines()]
    first, second = [e for e in events if e["action"] == "ranking"]
    inputs = first["inputs"]
    assert (inputs["semilla"], inputs["sim"], inputs["alfa"], inputs["perfil"]) == (7, 400, 30.0, "original")
    for key in ("sha256_puntuaciones", "sha256_entrada", "sha256_rubrica"):
        assert re.fullmatch(r"[0-9a-f]{64}", inputs[key]), key
    assert inputs["n_puntuaciones"] == 27 and inputs["rubrica_version"] == RUBRIC.version
    assert first["outputs"]["orden"] == [["O-0001", "CONDICIONAL", 2]]
    assert first["outputs"]["sha256_resultado"] == second["outputs"]["sha256_resultado"]
    assert first["inputs"] == second["inputs"]
    assert '"value"' not in json.dumps(first)                              # sin valores de puntuación en la traza
    # Otra puntuación cambia la huella.
    assert _register_scores("O-0001", "evaluador-1", 0.75) == 0
    assert main(["--agente", "director-cientifico", "puntuar", "ranking", "--sim", "400", "--semilla", "7"]) == 0
    events = [json.loads(x) for x in (project / "research" / "procedencia.jsonl").read_text("utf-8").splitlines()]
    last = [e for e in events if e["action"] == "ranking"][-1]
    assert last["inputs"]["sha256_puntuaciones"] != inputs["sha256_puntuaciones"]


# ============================================================================================ D4-11
def test_escala_de_madurez_no_acepta_analogos_sin_destino_digestivo():
    levels = {m.id: m for m in RUBRIC.maturity}
    assert list(levels) == [f"EMC-{i}" for i in range(7)]
    emc2 = levels["EMC-2"].meaning + " " + levels["EMC-2"].note
    assert "misma forma química y protección" in emc2 and "destino digestivo" in emc2
    assert "análogo cercano" not in emc2
    evidencia = next(c for c in RUBRIC.criteria if c.id == "evidencia")
    assert "análogo" in evidencia.description and "destino digestivo" in evidencia.description
    assert RUBRIC.critical_gate_ids == {"seguridad", "regulatoria", "destino_digestivo"}


def test_docs_rubrica_es_la_salida_de_la_rubrica():
    """docs/RUBRICA.md es la salida de `nutria puntuar rubrica --markdown`: no puede desincronizarse."""
    doc = (ROOT / "docs" / "RUBRICA.md").read_text("utf-8")
    assert doc.rstrip("\n") == RUBRIC.to_markdown().rstrip("\n")
    assert "## Escala de madurez del candidato (EMC)" in doc and "justificacion_entre_anclas" in doc


# ============================================================================ D8-12: un solo estadístico central
def test_estadistico_central_moda_o_mediana_para_todas_las_entradas():
    lognormal = parse_dist({"dist": "lognormal", "media_log": 0.0, "de_log": 0.5})
    assert lognormal.mode() == pytest.approx(math.exp(-0.25)) and lognormal.median() == pytest.approx(1.0)
    uniform = parse_dist({"dist": "uniforme", "min": 2, "max": 4})
    assert uniform.mode() == uniform.median() == pytest.approx(3.0)       # sin moda única: punto medio
    pert = parse_dist({"dist": "pert", "min": 5, "moda": 6, "max": 10})
    assert pert.central() == pert.mode() == 6 and pert.central("mediana") == pytest.approx(pert.frozen.median())
    normal = parse_dist({"dist": "normal", "media": 3, "de": 1})
    assert normal.central("moda") == normal.central("mediana") == pytest.approx(3)
    with pytest.raises(ValueError, match="Estadístico central"):
        pert.central("media")


_MIXED = """
[escenario]
dias = { valor = 1, fuente = "[SUP] prueba" }
[coste]
fijo_eur_ciclo = { dist = "pert", min = 5, moda = 6, max = 10, fuente = "[SUP] prueba" }
[[beneficio]]
nombre = "b"
respuesta = { valor = 1, fuente = "[SUP] prueba" }
valor_unitario = { dist = "lognormal", media_log = 0.0, de_log = 0.5, fuente = "[SUP] prueba" }
por_dia = false
"""


def test_respuesta_umbral_no_mezcla_moda_y_mediana(tmp_path, project, capsys):
    """Coste pert (moda 6) y valor unitario lognormal (moda e^-0,25, mediana 1): la respuesta umbral usa moda con moda
    o mediana con mediana, nunca la moda de uno con la mediana del otro."""
    path = _write(tmp_path, "mixto.toml", _MIXED)
    by_mode = farm.breakeven(farm.load_farm_model(path))[0]
    assert by_mode["valor"] == pytest.approx(6 / math.exp(-0.25)) and by_mode["estadistico_central"] == "moda"
    median_model = farm.load_farm_model(path, central="mediana")
    by_median = farm.breakeven(median_model)[0]
    assert by_median["valor"] == pytest.approx(median_model.fixed_eur_cycle.median() / 1.0)
    assert by_median["valor"] != pytest.approx(6 / 1.0)                    # la mezcla antigua
    assert main(["--json", "econ", "ganadero", str(path), "--sim", "300", "--valor-central", "mediana"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["estadistico_central"] == "mediana"
    assert out["monte_carlo"]["respuesta_umbral"]["valor"] == pytest.approx(by_median["valor"])
    assert all(r["estadistico_central"] == "mediana" for r in out["entradas"])
    assert main(["econ", "ganadero", str(path), "--sim", "300"]) == 0
    text = capsys.readouterr().out
    assert "Respuesta umbral (valores centrales coherentes: coste, valor unitario y días con la moda de cada entrada" \
        in text and "exp(media_log − de_log²) en la lognormal" in text
    assert _exit_code(["econ", "ganadero", str(path), "--valor-central", "media"]) == 2


def test_coherencia_con_el_mismo_estadistico(tmp_path):
    model = farm.load_farm_model(FARM_EXAMPLE, central="mediana")
    with pytest.raises(ValueError, match="mismo estadístico central"):
        rnpv.coherence(model, rnpv.load_project(COMPANY_EXAMPLE))
    res = rnpv.coherence(model, rnpv.load_project(COMPANY_EXAMPLE, central="mediana"))
    assert res["coherente"] and res["estadistico_central"] == "mediana"
    with pytest.raises(ValueError, match="finito"):
        rnpv.coherence(farm.load_farm_model(FARM_EXAMPLE), rnpv.load_project(COMPANY_EXAMPLE), rtol=float("nan"))


# ============================================================================ D8-13: coste, supuestos y correlaciones
@pytest.mark.parametrize("coste, message", [
    ("dosis_g_dia = 50", "falta precio_eur_kg"),
    ("precio_eur_kg = 5", "falta la dosis"),
    ("dias = 10", "necesita un coste"),
])
def test_coste_incompleto_falla(tmp_path, coste, message):
    text = _FARM_BASE.format(dosis="dosis_g_dia = 50", racion="").replace("dosis_g_dia = 50\nprecio_eur_kg = 5",
                                                                          coste)
    with pytest.raises(ValueError, match=re.escape(message)):
        _farm(tmp_path, text)


def test_supuestos_y_pares_de_correlacion_repetidos(tmp_path):
    base = _FARM_BASE.format(dosis="dosis_g_dia = 50", racion="")
    with pytest.raises(ValueError, match="claves desconocidas"):
        _farm(tmp_path, base + '[supuestos]\nlistas = ["x"]\n')
    with pytest.raises(ValueError, match="lista de textos"):
        _farm(tmp_path, base + '[supuestos]\nlista = "un texto"\n')
    with pytest.raises(ValueError, match="lista de textos"):
        _project(tmp_path, _TWO_PHASES.format(umbral="") + '[supuestos]\nlista = "un texto"\n')
    pair = '[[correlacion]]\nentre = ["beneficio[1].respuesta", "beneficio[2].respuesta"]\nrho = {rho}\n'
    with pytest.raises(ValueError, match="dos veces"):
        _farm(tmp_path, _TWO_UNIFORMS + pair.format(rho=0.0) + pair.format(rho=0.5))
    with pytest.raises(ValueError, match="dos veces"):
        gaussian_copula(["a", "b"], pairs=[("a", "b", 0.0), ("b", "a", 0.3)])


def test_valores_no_finitos_fallan(tmp_path, project, capsys):
    with pytest.raises(ValueError, match="finito"):
        dosis.convertir(50, "g_animal_dia", precio_eur_kg=float("inf"))
    assert main(["dosis", "convertir", "50", "--desde", "g_animal_dia", "--precio-eur-kg", "inf"]) == 2
    assert main(["econ", "desarrollador", str(COMPANY_EXAMPLE), "--sim", "100", "--umbral-perdida", "inf"]) == 2
    with pytest.raises(ValueError, match="finito"):
        rnpv.evaluate(_project(tmp_path, _TWO_PHASES.format(umbral="umbral_perdida_eur = inf")), n=10)
    assert main(["econ", "coherencia", str(FARM_EXAMPLE), str(COMPANY_EXAMPLE), "--tolerancia", "nan"]) == 2


# ============================================================================ D8-14: probabilidad de éxito acotada
@pytest.mark.parametrize("prob", ['{ dist = "normal", media = 0.5, de = 0.3 }', "1.2", "-0.1"])
def test_probabilidad_de_exito_fuera_de_0_1_falla_en_vez_de_recortarse(tmp_path, prob):
    text = _TWO_PHASES.format(umbral="").replace("prob_exito = 0.5", f"prob_exito = {prob}", 1)
    with pytest.raises(ValueError, match=r"prob_exito fuera de \[0, 1\]"):
        rnpv.evaluate(_project(tmp_path, text), n=2000, seed=1)


# ============================================================================ D8-13: fuentes rastreables
def test_identificadores_de_fuente():
    ids = dist_source_ids("[PUB] doi:10.3168/JDS.S0022-0302(03)73713-6 (tabla 2); pmid: 12345, openalex:w42 y A-0003.")
    assert ids == ["doi:10.3168/jds.s0022-0302(03)73713-6", "pmid:12345", "openalex:W42", "A-0003"]
    assert dist_source_ids("[PUB] (doi:10.1000/abc).") == ["doi:10.1000/abc"]
    assert dist_source_ids("[PUB] R3") == [] and dist_source_ids(None) == []


def _farm_with_sources(tmp_path, price_source: str, response_source: str = "[SUP] prueba") -> Path:
    text = FARM_EXAMPLE.read_text("utf-8").replace(
        'fuente = "[SUP] ILUSTRATIVO: precio al ganadero"', f'fuente = "{price_source}"').replace(
        'respuesta = { dist = "pert", min = -0.5, moda = 0.5, max = 1.5, fuente = "[SUP] ILUSTRATIVO" }',
        f'respuesta = {{ dist = "pert", min = -0.5, moda = 0.5, max = 1.5, fuente = "{response_source}" }}')
    return _write(tmp_path, "fuentes.toml", text)


@pytest.mark.parametrize("price_source, response_source, message", [
    ("[PUB] R3", "[SUP] prueba", "sin el identificador de una fuente registrada"),
    ("[PUB] doi:10.1000/no-registrada", "[SUP] prueba", "no está registrada"),
    ("[PUB] A-0099", "[SUP] prueba", "A-0099 no está registrada"),
    ("[SUP] prueba", "[DER] cálculo propio", "[DER] sin la orden nutria"),
])
def test_exigir_fuentes_rastrea_pub_y_der_en_el_registro(tmp_path, project, capsys, price_source, response_source,
                                                         message):
    path = _farm_with_sources(tmp_path, price_source, response_source)
    assert main(["econ", "ganadero", str(path), "--sim", "200", "--exigir-fuentes"]) == 2
    assert message in capsys.readouterr().err
    # Sin --exigir-fuentes se evalúa, pero el informe avisa y el JSON lo lista.
    assert main(["--json", "econ", "ganadero", str(path), "--sim", "200"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert any(message in p for p in out["fuentes_sin_rastrear"])
    assert main(["econ", "ganadero", str(path), "--sim", "200"]) == 0
    assert "AVISO: fuentes sin rastrear en el registro" in capsys.readouterr().out


def test_exigir_fuentes_acepta_fuentes_y_afirmaciones_registradas(tmp_path, project, capsys, monkeypatch):
    """La comprobación usa la política del registro (registrada, verificada y no retractada); aquí se sustituye por
    una que solo acepta doi:10.1000/ok para comprobar qué identificadores se consultan."""
    asked = []

    def usable(conn, source_id, purpose):
        asked.append(source_id)
        if source_id != "doi:10.1000/ok":
            raise db.RegistryError(f"La fuente {source_id} está RETRACTADA")
        return {"id": source_id}

    monkeypatch.setattr(db, "_require_usable_source", usable)
    monkeypatch.setattr(db, "list_claims", lambda conn, entity_id=None: [
        {"id": "A-0001", "label": "PUB", "source_ids": ["doi:10.1000/ok"]},
        {"id": "A-0002", "label": "HIP", "source_ids": []},
        {"id": "A-0003", "label": "PUB", "source_ids": ["doi:10.1000/retractada"]}])
    ok = _farm_with_sources(tmp_path, "[PUB] doi:10.1000/OK (tabla 3)", "[DER] nutria meta analizar leche.csv")
    assert main(["econ", "ganadero", str(ok), "--sim", "200", "--exigir-fuentes"]) == 0
    assert asked == ["doi:10.1000/ok"]
    claim = _farm_with_sources(tmp_path, "[PUB] A-0001")
    assert main(["econ", "ganadero", str(claim), "--sim", "200", "--exigir-fuentes"]) == 0
    capsys.readouterr()
    for source, message in (("[PUB] A-0002", "es [HIP], no [PUB]"), ("[PUB] A-0003", "RETRACTADA"),
                            ("[PUB] doi:10.1000/otra", "RETRACTADA")):
        path = _farm_with_sources(tmp_path, source)
        assert main(["econ", "ganadero", str(path), "--sim", "200", "--exigir-fuentes"]) == 2, source
        assert message in capsys.readouterr().err, source
    # La empresa también: una fuente [PUB] sin identificador en el modelo rNPV.
    company = _write(tmp_path, "empresa_pub.toml", COMPANY_EXAMPLE.read_text("utf-8").replace(
        'fuente = "[SUP] ILUSTRATIVO: tasa de descuento de la empresa"', 'fuente = "[PUB] informe sectorial"'))
    assert main(["econ", "desarrollador", str(company), "--sim", "200", "--exigir-fuentes"]) == 2
    assert "proyecto.tasa_descuento: [PUB] sin el identificador" in capsys.readouterr().err


def test_fuentes_pub_sin_registro_del_proyecto(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NUTRIA_OFFLINE", "1")
    path = _farm_with_sources(tmp_path, "[PUB] doi:10.1000/ok")
    assert main(["econ", "ganadero", str(path), "--sim", "200", "--exigir-fuentes"]) == 2
    assert "no existe el registro del proyecto" in capsys.readouterr().err


# ============================================================================ D8-1: compuertas críticas fijas
def test_una_rubrica_propia_no_quita_las_compuertas_criticas(tmp_path):
    text = (ROOT / "src" / "nutria" / "data" / "rubrica.toml").read_text("utf-8")
    custom = text.replace("critica = true\n", "").replace('id = "etica"\n', 'id = "etica"\ncritica = true\n')
    path = _write(tmp_path, "rubrica.toml", custom)
    rubric = load_rubric(path)
    assert rubric.critical_gate_ids == {"seguridad", "regulatoria", "destino_digestivo", "etica"}


# ============================================================================ D8-4: Monte Carlo frente a perfiles
def test_aviso_si_el_top_k_del_monte_carlo_no_coincide_con_el_determinista():
    """O-2 gana con los valores modales en todos los perfiles (0,5 en todo y 0,75 en evidencia), pero O-1 tiene todos
    los rangos asimétricos hacia arriba (0,5 [0,5-1]) y gana en el Monte Carlo."""
    gates = dict.fromkeys(RUBRIC.gate_ids, "superada")
    o1 = _engine_opp("O-1", 0.5, gates)
    o1["scores"] = [dict(s, low=0.5, high=1.0) for s in o1["scores"]]
    o2 = _engine_opp("O-2", 0.5, gates)
    o2["scores"] = [dict(s, value=0.75 if s["criterion"] == "evidencia" else 0.5, low=None, high=None)
                    for s in o2["scores"]]
    rows = rank([o1, o2], RUBRIC, n_sim=3000, top_k=1)
    assert [r.opportunity_id for r in rows] == ["O-1", "O-2"]
    assert all(r.ranks_by_profile == {"original": 2, "recomendado": 2, "uniforme": 2} for r in rows
               if r.opportunity_id == "O-1")
    warnings = ranking_warnings(rows, 1, "recomendado")
    assert any("top-1 del Monte Carlo (O-1)" in w and "del perfil recomendado (O-2)" in w for w in warnings)
    assert not any("según el perfil de pesos" in w for w in warnings)        # los perfiles coinciden entre sí
    flags = {r.opportunity_id: r.flags for r in rows}
    assert any("Dentro del top-1 con Monte Carlo (recomendado) y fuera con original, recomendado, uniforme" in f
               for f in flags["O-1"])
    assert any("Dentro del top-1 con original, recomendado, uniforme y fuera con Monte Carlo" in f
               for f in flags["O-2"])


# ============================================================================ D4-2: entradas de la ración sin usar
@pytest.mark.parametrize("kwargs, message", [
    ({"ingestion_pienso": 5}, "exigen declarar el pienso portador"),
    ({"ms_pienso": 0.88}, "exigen declarar el pienso portador"),
    ({"ingestion_ms": 20, "portador": "concentrado", "base_pienso": "ms", "ingestion_pienso": 5, "ms_pienso": 0.88},
     "solo se usa con el pienso en base tal_cual"),
])
def test_dosis_rechaza_entradas_que_no_se_usarian(kwargs, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        dosis.convertir(50, "g_animal_dia", **kwargs)


def test_dosis_deduce_la_ingestion_de_ms_de_la_racion_completa():
    """La ración completa es toda la ración: 40 kg tal cual al 50 % de MS son 20 kg de MS (no es un supuesto)."""
    tc = dosis.convertir(50, "g_animal_dia", portador="racion_completa", base_pienso="tal_cual", ingestion_pienso=40,
                         ms_pienso=0.5)
    assert tc["ingestion_ms_deducida_kg_dia"] == pytest.approx(20)
    assert tc["dosis"]["mg_kg_ms"] == pytest.approx(2500) and tc["faltan"] == {}
    assert "deducida de la ración completa" in dosis.texto_conversion(tc)
    ms = dosis.convertir(2500, "mg_kg_ms", portador="racion_completa", base_pienso="ms", ingestion_pienso=20)
    assert ms["dosis"]["g_animal_dia"] == pytest.approx(50) and ms["dosis"]["g_t"] == pytest.approx(2500)
    # Con la ingestión de MS declarada no se deduce nada; con el concentrado no hay deducción posible.
    assert dosis.convertir(50, "g_animal_dia", ingestion_ms=20)["ingestion_ms_deducida_kg_dia"] is None
    conc = dosis.convertir(50, "g_animal_dia", portador="concentrado", base_pienso="tal_cual", ingestion_pienso=8)
    assert conc["dosis"]["mg_kg_ms"] is None and "ingestión de MS" in conc["faltan"]["mg_kg_ms"]


def test_ganadero_racion_completa_sin_ingestion_de_ms_y_ms_pienso_huerfano(tmp_path):
    racion = ('[racion]\nportador = "racion_completa"\nbase = "tal_cual"\ningestion_pienso_kg_dia = 40\n'
              'ms_pienso = 0.5')
    model = _farm(tmp_path, _FARM_BASE.format(dosis="dosis_mg_kg_ms = 2500", racion=racion))
    summary = farm.dose_summary(model)
    assert summary["g_animal_dia"] == pytest.approx(50) and summary["g_t"] == pytest.approx(1250)
    assert summary["mg_kg_ms"] == pytest.approx(2500)
    for bad, message in (('[racion]\nms_pienso = 0.5', "exige declarar el pienso portador"),
                         ('[racion]\nportador = "concentrado"\nbase = "ms"\ningestion_pienso_kg_dia = 5\n'
                          'ms_pienso = 0.9', "solo se usa con base")):
        with pytest.raises(ValueError, match=re.escape(message)):
            _farm(tmp_path, _FARM_BASE.format(dosis="dosis_g_dia = 50", racion=bad))


# ============================================================================ D8-1: P(top-k) en el ranking completo
def test_p_top_es_global_y_respeta_el_orden_de_los_grupos():
    """Dos CONDICIONALES solo pendientes de firma van delante de una con una compuerta crítica sin resolver: con k = 2
    esta no puede entrar en el top-2 (P = 0) aunque puntúe más, y su rango mediano es el 3."""
    unsigned = dict.fromkeys(GATES, "no_resuelta")
    proposed = dict.fromkeys(GATES, "superada")
    opps = [_engine_opp("O-0001", 1.0, unsigned, {**proposed, "seguridad": "no_resuelta"}),
            _engine_opp("O-0002", 0.5, unsigned, proposed),
            _engine_opp("O-0003", 0.25, unsigned, proposed)]
    rows = {r.opportunity_id: r for r in rank(opps, RUBRIC, n_sim=500, top_k=2)}
    assert rows["O-0001"].p_top == 0.0 and rows["O-0001"].median_rank == 3
    assert rows["O-0001"].p_top_in_group == 1.0                           # sola en su grupo
    assert rows["O-0002"].p_top == rows["O-0003"].p_top == 1.0
    assert sorted(r.median_rank for r in rows.values()) == [1, 2, 3]
    # Con k = 3 entra (y se avisa de la compuerta crítica).
    rows3 = rank(opps, RUBRIC, n_sim=500, top_k=3)
    assert next(r for r in rows3 if r.opportunity_id == "O-0001").p_top == 1.0
    assert any("compuertas CRÍTICAS sin resolver" in w for w in ranking_warnings(rows3, 3))


# ============================================================================ D8-1 y D8-4: opciones del ranking
@pytest.mark.parametrize("extra, message", [
    (["--min-evaluadores", "1"], "al menos 3 evaluadores"),
    (["--min-evaluadores", "2"], "al menos 3 evaluadores"),
    (["--top", "0"], "--top y --sim deben ser >= 1"),
    (["--sim", "0"], "--top y --sim deben ser >= 1"),
    (["--alfa", "-1"], "--alfa un número finito >= 0"),
    (["--alfa", "inf"], "--alfa un número finito >= 0"),
    (["--alfa", "nan"], "--alfa un número finito >= 0"),
])
def test_ranking_rechaza_opciones_que_eluden_las_reglas(project, capsys, extra, message):
    _register_opportunity("Opciones del ranking")
    assert main(["--agente", "director-cientifico", "puntuar", "ranking", *extra]) == 2
    assert message in capsys.readouterr().err
    assert main(["--agente", "director-cientifico", "puntuar", "ranking", "--sim", "50", "--min-evaluadores", "4"]) == 0


# ============================================================================ D8-13: [DER] rastreable
@pytest.mark.parametrize("source, traceable", [
    ("[DER] según el script", False),
    ("[DER] cálculo propio", False),
    ("[DER] nutria", False),
    ("[DER] nutria meta analizar research/datos/O-0001/leche.csv", True),
    ("[DER] `nutria destino ruminal --a 0.2 --b 0.7 --kd 0.05 --kp 0.06`", True),
    ("[DER] script research/datos/O-0001/precio.py", True),
    ("[DER] Rscript research/datos/O-0001/ajuste.R", True),
])
def test_der_exige_la_orden_o_el_fichero_del_script(tmp_path, project, capsys, source, traceable):
    path = _farm_with_sources(tmp_path, "[SUP] prueba", source)
    assert main(["econ", "ganadero", str(path), "--sim", "200", "--exigir-fuentes"]) == (0 if traceable else 2)
    assert ("[DER] sin la orden nutria o el script" in capsys.readouterr().err) is not traceable


# ============================================================================ D8-13 y D8-14: supuestos que dominan
def test_resumen_de_fuentes_senala_los_supuestos_que_dominan_el_resultado(tmp_path, project, capsys):
    from nutria.econ.dist import source_label
    assert source_label("[SUP] basado en [PUB] doi:10.1/x") == "[SUP]" and source_label(None) == "sin fuente"
    assert source_label(None, declared=False) == "valor por defecto" and source_label("catálogo") == "sin etiqueta"
    example = farm.sources_overview(farm.load_farm_model(FARM_EXAMPLE))
    assert example["recuento"] == {"[SUP]": 13} and example["principales"][0] == "beneficio[1].respuesta"
    assert "beneficio[1].respuesta ([SUP])" in example["principales_sin_datos"]
    # Con la respuesta en leche (la que más pesa) respaldada por un cálculo [DER], deja de señalarse.
    der = _farm_with_sources(tmp_path, "[SUP] prueba", "[DER] nutria meta analizar research/datos/O-0001/leche.csv")
    overview = farm.sources_overview(farm.load_farm_model(der))
    assert overview["recuento"]["[DER]"] == 1
    assert not any(x.startswith("beneficio[1].respuesta") for x in overview["principales_sin_datos"])
    company = rnpv.sources_overview(rnpv.load_project(COMPANY_EXAMPLE))
    assert company["recuento"] == {"[SUP]": 26, "valor por defecto": 1} and len(company["principales_sin_datos"]) == 5
    text = rnpv.report(rnpv.load_project(COMPANY_EXAMPLE), 500, 1)
    assert "no se apoyan en datos" in text and "no trae valores de referencia de coste ni de plazo" in text
    assert main(["--json", "econ", "ganadero", str(FARM_EXAMPLE), "--sim", "200"]) == 0
    assert json.loads(capsys.readouterr().out)["resumen_fuentes"]["entradas"] == 13


# ======================================================================= D8-1: una rúbrica propia no quita compuertas
def test_rubrica_propia_no_puede_quitar_compuertas_ni_usar_pesos_negativos(tmp_path, project, capsys):
    text = (ROOT / "src" / "nutria" / "data" / "rubrica.toml").read_text("utf-8")
    start = text.index('[[compuerta]]\nid = "seguridad"')
    without_safety = text[:start] + text[text.index("[[compuerta]]", start + 1):]
    with pytest.raises(ValueError, match="faltan las compuertas obligatorias seguridad"):
        load_rubric(_write(tmp_path, "sin_seguridad.toml", without_safety))
    # En un proyecto, config/rubrica.toml sin la compuerta de seguridad hace fallar el ranking (no la ignora).
    (project / "config").mkdir(exist_ok=True)
    (project / "config" / "rubrica.toml").write_text(without_safety, "utf-8")
    assert main(["--agente", "director-cientifico", "puntuar", "ranking", "--sim", "50"]) == 2
    assert "compuertas obligatorias" in capsys.readouterr().err
    negative = text.replace("pesos = { evidencia = 20,", "pesos = { evidencia = -20,", 1)
    with pytest.raises(ValueError, match="números finitos >= 0"):
        load_rubric(_write(tmp_path, "negativo.toml", negative)).weights("recomendado")
