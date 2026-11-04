import math
from pathlib import Path

import numpy as np
import pytest

from nutria.econ import farm, rnpv
from nutria.econ.dist import parse_dist

EXAMPLES = Path(__file__).resolve().parents[1] / "ejemplos"


def _farm(tmp_path, text: str) -> farm.FarmModel:
    f = tmp_path / "m.toml"
    f.write_text(text, "utf-8")
    return farm.load_farm_model(f)


def _project(tmp_path, text: str) -> rnpv.Project:
    f = tmp_path / "p.toml"
    f.write_text(text, "utf-8")
    return rnpv.load_project(f)


# ---------------------------------------------------------------------------------------------- ganadero
def test_farm_deterministic_arithmetic(tmp_path):
    model = _farm(tmp_path, """
[escenario]
dias = 10
[coste]
dosis_g_dia = 100
precio_eur_kg = 5
[[beneficio]]
nombre = "leche"
respuesta = 1.0
valor_unitario = 0.4
""")
    sc = farm.scenarios(model)
    c = sc["valores_centrales"]
    assert c["coste"] == pytest.approx(0.1 * 5 * 10)
    assert c["beneficio"] == pytest.approx(4.0)
    assert c["roi"] == pytest.approx(4.0 / 5.0)
    # Con entradas fijas, todos los percentiles del neto coinciden con el neto central.
    for name in ("conservador", "intermedio", "optimista"):
        assert sc[name]["neto"] == pytest.approx(-1.0)


def test_farm_example_monte_carlo_is_reproducible():
    model = farm.load_farm_model(EXAMPLES / "econ_ganadero_ILUSTRATIVO.toml")
    a = farm.monte_carlo(model, 2000, seed=7)
    b = farm.monte_carlo(model, 2000, seed=7)
    assert a == b
    assert farm.scenarios(model, 2000, seed=7) == farm.scenarios(model, 2000, seed=7)
    sc = farm.scenarios(model)
    assert sc["conservador"]["neto"] <= sc["intermedio"]["neto"] <= sc["optimista"]["neto"]


@pytest.mark.parametrize("n,seed", [(10000, 42), (3000, 7)])
def test_farm_scenarios_equal_monte_carlo_percentiles(n, seed):
    """D8-5: los escenarios son P10, P50 y P90 del neto del mismo Monte Carlo."""
    model = farm.load_farm_model(EXAMPLES / "econ_ganadero_ILUSTRATIVO.toml")
    sc = farm.scenarios(model, n=n, seed=seed)
    neto = farm.monte_carlo(model, n, seed)["neto_por_animal"]
    assert sc["conservador"]["neto"] <= sc["intermedio"]["neto"] <= sc["optimista"]["neto"]
    assert sc["conservador"]["neto"] == neto["p10"]
    assert sc["intermedio"]["neto"] == neto["p50"]
    assert sc["optimista"]["neto"] == neto["p90"]
    assert [sc[k]["percentil"] for k in ("conservador", "intermedio", "optimista")] == [10, 50, 90]
    assert set(sc) == {"conservador", "intermedio", "optimista", "valores_centrales"}


def test_farm_scenarios_match_hand_computed_percentiles(tmp_path):
    """Neto = U(0, 2) - 0,5 por ciclo: P10 = -0,3; P50 = 0,5; P90 = 1,3."""
    model = _farm(tmp_path, """
[coste]
fijo_eur_ciclo = 0.5
[[beneficio]]
nombre = "b"
respuesta = { dist = "uniforme", min = 0.0, max = 2.0 }
valor_unitario = 1.0
por_dia = false
""")
    sc = farm.scenarios(model, n=20000, seed=1)
    assert sc["conservador"]["neto"] == pytest.approx(-0.3, abs=0.03)
    assert sc["intermedio"]["neto"] == pytest.approx(0.5, abs=0.03)
    assert sc["optimista"]["neto"] == pytest.approx(1.3, abs=0.03)
    # Valores centrales: la uniforme no declara moda y se usa su mediana (1,0).
    assert sc["valores_centrales"]["neto"] == pytest.approx(0.5)


def test_farm_scenarios_are_not_combinations_of_input_percentiles(tmp_path):
    """Dos beneficios U(0, 1) independientes: el P10 de su suma es sqrt(0,2) = 0,447 (CDF s²/2), no la suma
    de sus P10 (0,2) que daba el método antiguo; simétricamente, P90 = 2 - sqrt(0,2) y no 1,8."""
    model = _farm(tmp_path, """
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
""")
    sc = farm.scenarios(model, n=20000, seed=3)
    assert sc["conservador"]["neto"] == pytest.approx(math.sqrt(0.2) - 0.1, abs=0.025)
    assert sc["optimista"]["neto"] == pytest.approx(2 - math.sqrt(0.2) - 0.1, abs=0.025)
    old_conservative = (0.1 + 0.1) - 0.1  # método antiguo: P10 + P10 - coste = 0,1
    old_optimistic = (0.9 + 0.9) - 0.1    # método antiguo: P90 + P90 - coste = 1,7
    assert sc["conservador"]["neto"] - old_conservative > 0.2
    assert old_optimistic - sc["optimista"]["neto"] > 0.2


def test_farm_benefit_and_cost_days(tmp_path):
    """D4-8: el coste usa [coste].dias; cada beneficio, sus días o los del escenario."""
    model = _farm(tmp_path, """
[escenario]
dias = 30
[coste]
dias = 42
dosis_g_dia = 50
precio_eur_kg = 5
[[beneficio]]
nombre = "leche"
respuesta = 1.0
valor_unitario = 0.4
dias = 21
[[beneficio]]
nombre = "otro por día"
respuesta = 0.1
valor_unitario = 1.0
[[beneficio]]
nombre = "por ciclo"
respuesta = 0.01
valor_unitario = 250
por_dia = false
""")
    c = farm.scenarios(model, n=100)["valores_centrales"]
    assert c["coste"] == pytest.approx(0.05 * 5 * 42)                  # 10,5 €
    assert c["componentes"]["leche"] == pytest.approx(1.0 * 0.4 * 21)  # 8,4 €
    assert c["componentes"]["otro por día"] == pytest.approx(0.1 * 1.0 * 30)  # días del escenario: 3,0 €
    assert c["componentes"]["por ciclo"] == pytest.approx(2.5)
    assert c["neto"] == pytest.approx(8.4 + 3.0 + 2.5 - 10.5)
    mc = farm.monte_carlo(model, 100)
    assert mc["neto_por_animal"]["p50"] == pytest.approx(3.4)
    # Respuesta umbral con los días del primer beneficio (21), no los del escenario.
    assert mc["respuesta_umbral"]["valor"] == pytest.approx(10.5 / (0.4 * 21))


def test_farm_cost_days_default_to_scenario_days(tmp_path):
    model = _farm(tmp_path, """
[escenario]
dias = 30
[coste]
dosis_g_dia = 50
precio_eur_kg = 5
[[beneficio]]
nombre = "leche"
respuesta = 1.0
valor_unitario = 0.4
""")
    c = farm.scenarios(model, n=100)["valores_centrales"]
    assert c["coste"] == pytest.approx(0.05 * 5 * 30)
    assert c["beneficio"] == pytest.approx(0.4 * 30)


def test_farm_benefit_days_as_distribution(tmp_path):
    """Días del beneficio ~ U(10, 30) y 1 €/día: neto = días - 1 → P10 = 11, P50 = 19, P90 = 27."""
    model = _farm(tmp_path, """
[escenario]
dias = 1000
[coste]
fijo_eur_ciclo = 1.0
[[beneficio]]
nombre = "b"
respuesta = 1.0
valor_unitario = 1.0
dias = { dist = "uniforme", min = 10, max = 30 }
""")
    sc = farm.scenarios(model, n=20000, seed=5)
    assert sc["conservador"]["neto"] == pytest.approx(11.0, abs=0.3)
    assert sc["intermedio"]["neto"] == pytest.approx(19.0, abs=0.3)
    assert sc["optimista"]["neto"] == pytest.approx(27.0, abs=0.3)


def test_farm_undeclared_days_share_the_scenario_draw(tmp_path):
    """Coste y beneficio sin días propios usan la misma muestra de [escenario].dias: 1 €/día de coste y
    1 €/día de beneficio dan neto 0 en todas las simulaciones (con muestras independientes no sería así)."""
    model = _farm(tmp_path, """
[escenario]
dias = { dist = "uniforme", min = 10, max = 30 }
[coste]
otros_eur_dia = 1.0
[[beneficio]]
nombre = "b"
respuesta = 1.0
valor_unitario = 1.0
""")
    neto = farm.monte_carlo(model, 5000, seed=2)["neto_por_animal"]
    assert neto["p05"] == pytest.approx(0.0, abs=1e-12)
    assert neto["p95"] == pytest.approx(0.0, abs=1e-12)


def test_farm_days_in_per_cycle_benefit_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="por_dia"):
        _farm(tmp_path, """
[[beneficio]]
nombre = "cetosis"
respuesta = 0.01
valor_unitario = 250
por_dia = false
dias = 21
""")


def test_farm_example_uses_transition_periods():
    """Ejemplo ILUSTRATIVO: coste 42 días y leche solo 21 días posparto."""
    model = farm.load_farm_model(EXAMPLES / "econ_ganadero_ILUSTRATIVO.toml")
    assert model.cost_days is not None and model.cost_days.central() == 42
    milk, ketosis = model.benefits
    assert milk.per_day and milk.days is not None and milk.days.central() == 21
    assert not ketosis.per_day and ketosis.days is None
    c = farm.scenarios(model, n=200)["valores_centrales"]
    assert c["coste"] == pytest.approx(0.050 * 5.0 * 42)
    assert c["componentes"][milk.name] == pytest.approx(0.5 * 0.42 * 21)
    assert any("ILUSTRATIVO" in a for a in model.assumptions)
    text = (EXAMPLES / "econ_ganadero_ILUSTRATIVO.toml").read_text("utf-8")
    assert "NO son datos" in text


def test_farm_report_explains_scenarios():
    model = farm.load_farm_model(EXAMPLES / "econ_ganadero_ILUSTRATIVO.toml")
    text = farm.report(model, 2000, 3)
    sc = farm.scenarios(model, 2000, 3)
    assert "percentiles del beneficio neto simulado" in text
    assert "Valores centrales" in text
    assert "no una combinación de percentiles de las entradas" in text
    assert "Periodos: coste 42 días" in text and "21 días" in text and "por ciclo" in text
    assert f"{sc['conservador']['neto']:.2f}" in text and f"{sc['optimista']['neto']:.2f}" in text
    # Columna que usan las anclas de la rúbrica (neto positivo en P10, P50 o P90).
    assert "¿Neto > 0?" in text
    for name in ("conservador", "intermedio", "optimista"):
        row = next(line for line in text.splitlines() if line.startswith(name))
        assert row.rstrip().endswith("sí" if sc[name]["neto"] > 0 else "no")


def test_farm_rejects_zero_simulations():
    model = farm.load_farm_model(EXAMPLES / "econ_ganadero_ILUSTRATIVO.toml")
    with pytest.raises(ValueError):
        farm.scenarios(model, n=0)


# ---------------------------------------------------------------------------------------------- empresa
_ONE_PHASE = """
[proyecto]
tasa_descuento = 0.0
[[fase]]
nombre = "unica"
coste_eur = 100
duracion_anios = 1
prob_exito = {prob}
[mercado]
ventas_pico_eur_anio = 1000
anios_hasta_pico = 1
anios_comercializacion = 2
margen_bruto = 0.5
costes_comerciales_pct = 0.1
"""


def test_rnpv_deterministic(tmp_path):
    res = rnpv.evaluate(_project(tmp_path, _ONE_PHASE.format(prob="0.5")), n=50)
    # -100 + 0.5 * 2 años * 1000 * (0.5 - 0.1) = 300
    assert res["rnpv_central"] == pytest.approx(300.0)
    assert res["prob_exito_acumulada"] == pytest.approx(0.5)
    # D8-7: la métrica antigua se renombra; con parámetros fijos el rNPV es siempre 300 > 0.
    assert "p_rnpv_positivo" not in res
    assert res["p_rnpv_positivo_incertidumbre_parametros"] == 1.0


def test_rnpv_outcome_simulation_two_phases(tmp_path):
    """Dos fases de 100 € con p = 0,5 y flujos comerciales de 800 € (tasa 0):
    fracasa la 1.ª (p 0,5) → -100; fracasa la 2.ª (0,25) → -200; ambas con éxito (0,25) → 600.
    rNPV = -100 - 0,5·100 + 0,25·800 = 50 > 0, pero P(VPN realizado > 0) = 0,25."""
    project = _project(tmp_path, """
[proyecto]
tasa_descuento = 0.0
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
""")
    res = rnpv.evaluate(project, n=8000, seed=1)
    assert res["rnpv_central"] == pytest.approx(50.0)
    assert res["prob_exito_acumulada"] == pytest.approx(0.25)
    assert res["p_rnpv_positivo_incertidumbre_parametros"] == 1.0
    assert res["p_vpn_realizado_positivo"] == pytest.approx(0.25, abs=0.03)
    assert res["p_llega_al_mercado_simulada"] == pytest.approx(0.25, abs=0.03)
    assert res["vpn_esperado"] == pytest.approx(50.0, abs=20.0)
    assert res["vpn_realizado_p05"] == pytest.approx(-200.0)
    assert res["vpn_realizado_p50"] == pytest.approx(-100.0)
    assert res["vpn_realizado_p95"] == pytest.approx(600.0)
    sims = rnpv.simulate(project, n=8000, seed=1)
    values, counts = np.unique(np.round(sims["vpn_realizado"], 9), return_counts=True)
    assert values.tolist() == [-200.0, -100.0, 600.0]
    assert counts / counts.sum() == pytest.approx([0.25, 0.5, 0.25], abs=0.03)
    # Solo los que llegan al mercado suman flujos comerciales.
    assert np.allclose(sims["vpn_realizado"][sims["llega_al_mercado"]], 600.0)
    assert np.all(sims["vpn_realizado"][~sims["llega_al_mercado"]] < 0)


def test_rnpv_expected_value_with_uncertain_probability(tmp_path):
    """p ~ U(0,4; 0,6): E[VPN realizado] = -100 + E[p]·800 = 300 = media del rNPV; P(VPN realizado > 0) = E[p]."""
    project = _project(tmp_path, _ONE_PHASE.format(prob='{ dist = "uniforme", min = 0.4, max = 0.6 }'))
    res = rnpv.evaluate(project, n=20000, seed=11)
    assert res["vpn_esperado"] == pytest.approx(300.0, abs=15.0)
    assert res["p_vpn_realizado_positivo"] == pytest.approx(0.5, abs=0.02)
    # El rNPV mínimo es -100 + 0,4·800 = 220 > 0: la métrica antigua da 1 aunque se pierda la mitad de las veces.
    assert res["p_rnpv_positivo_incertidumbre_parametros"] == 1.0
    sims = rnpv.simulate(project, n=20000, seed=11)
    assert float(np.mean(sims["rnpv"])) == pytest.approx(300.0, abs=2.0)


def test_rnpv_realized_matches_rnpv_when_success_is_certain(tmp_path):
    """Con p = 1 el VPN realizado es el rNPV: -100/1,1^0,5 + 400/1,1^1,5 (coste a mitad de fase y flujos a
    mitad de año)."""
    project = _project(tmp_path, """
[proyecto]
tasa_descuento = 0.10
[[fase]]
nombre = "unica"
coste_eur = 100
duracion_anios = 1
prob_exito = 1.0
[mercado]
ventas_pico_eur_anio = 1000
anios_hasta_pico = 1
anios_comercializacion = 1
margen_bruto = 0.5
costes_comerciales_pct = 0.1
""")
    expected = -100 / 1.1 ** 0.5 + 400 / 1.1 ** 1.5
    res = rnpv.evaluate(project, n=500, seed=4)
    assert res["rnpv_central"] == pytest.approx(expected)
    assert res["vpn_esperado"] == pytest.approx(expected)
    assert res["vpn_realizado_p05"] == pytest.approx(expected)
    assert res["p_vpn_realizado_positivo"] == 1.0


def test_rnpv_example_outcomes_are_consistent():
    project = rnpv.load_project(EXAMPLES / "econ_desarrollador_ILUSTRATIVO.toml")
    a = rnpv.evaluate(project, 3000, seed=9)
    assert a == rnpv.evaluate(project, 3000, seed=9)
    # Con costes positivos, un VPN realizado positivo exige llegar al mercado.
    assert a["p_vpn_realizado_positivo"] <= a["p_llega_al_mercado_simulada"]
    assert a["vpn_realizado_p05"] <= a["vpn_realizado_p50"] <= a["vpn_realizado_p95"]
    assert a["rnpv_p05"] <= a["rnpv_p50"] <= a["rnpv_p95"]
    for key in ("rnpv_central", "vpn_esperado", "p_vpn_realizado_positivo",
                "p_rnpv_positivo_incertidumbre_parametros"):
        assert math.isfinite(a[key])


def test_rnpv_report_explains_both_metrics():
    project = rnpv.load_project(EXAMPLES / "econ_desarrollador_ILUSTRATIVO.toml")
    text = rnpv.report(project, 2000, 5)
    assert "rNPV central" in text
    assert "Incertidumbre de los parámetros" in text
    assert "No es la probabilidad de que el proyecto gane dinero" in text
    assert "Simulación de resultados" in text
    assert "P(VPN realizado > 0)" in text
    assert "VPN esperado" in text


def test_rnpv_rejects_zero_simulations():
    project = rnpv.load_project(EXAMPLES / "econ_desarrollador_ILUSTRATIVO.toml")
    with pytest.raises(ValueError):
        rnpv.simulate(project, n=0)


# ---------------------------------------------------------------------------------------------- distribuciones
def test_dist_validation():
    with pytest.raises(ValueError):
        parse_dist({"dist": "pert", "min": 2, "moda": 1, "max": 3})
    assert parse_dist(3).central() == 3
