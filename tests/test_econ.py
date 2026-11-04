from pathlib import Path

import pytest

from nutria.econ import farm, rnpv
from nutria.econ.dist import parse_dist

EXAMPLES = Path(__file__).resolve().parents[1] / "ejemplos"


def test_farm_deterministic_arithmetic(tmp_path):
    f = tmp_path / "m.toml"
    f.write_text("""
[escenario]
dias = 10
[coste]
dosis_g_dia = 100
precio_eur_kg = 5
[[beneficio]]
nombre = "leche"
respuesta = 1.0
valor_unitario = 0.4
""", "utf-8")
    sc = farm.scenarios(farm.load_farm_model(f))["intermedio"]
    assert sc["coste"] == pytest.approx(0.1 * 5 * 10)
    assert sc["beneficio"] == pytest.approx(4.0)
    assert sc["roi"] == pytest.approx(4.0 / 5.0)


def test_farm_example_monte_carlo_is_reproducible():
    model = farm.load_farm_model(EXAMPLES / "econ_ganadero_ILUSTRATIVO.toml")
    a = farm.monte_carlo(model, 2000, seed=7)
    b = farm.monte_carlo(model, 2000, seed=7)
    assert a == b
    sc = farm.scenarios(model)
    assert sc["conservador"]["neto"] <= sc["intermedio"]["neto"] <= sc["optimista"]["neto"]


def test_rnpv_deterministic(tmp_path):
    f = tmp_path / "p.toml"
    f.write_text("""
[proyecto]
tasa_descuento = 0.0
[[fase]]
nombre = "unica"
coste_eur = 100
duracion_anios = 1
prob_exito = 0.5
[mercado]
ventas_pico_eur_anio = 1000
anios_hasta_pico = 1
anios_comercializacion = 2
margen_bruto = 0.5
costes_comerciales_pct = 0.1
""", "utf-8")
    res = rnpv.evaluate(rnpv.load_project(f), n=50)
    # -100 + 0.5 * 2 años * 1000 * (0.5 - 0.1) = 300
    assert res["rnpv_central"] == pytest.approx(300.0)
    assert res["prob_exito_acumulada"] == pytest.approx(0.5)


def test_dist_validation():
    with pytest.raises(ValueError):
        parse_dist({"dist": "pert", "min": 2, "moda": 1, "max": 3})
    assert parse_dist(3).central() == 3
