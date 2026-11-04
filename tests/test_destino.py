"""Destino ruminal (Ørskov y McDonald 1979): valores calculados a mano."""

import math

import pytest

from nutria import destino


# DE = a + b·kd/(kd + kp)
# (0,2; 0,6; 0,06; 0,04): kd/(kd+kp) = 0,6 → DE = 0,2 + 0,36 = 0,56; escape 0,44 = 0,2 (no degradable) + 0,24
# (0,3; 0,5; 0,08; 0,08): kd/(kd+kp) = 0,5 → DE = 0,3 + 0,25 = 0,55; escape 0,45 = 0,2 + 0,25
# (0;   1;   0,10; 0,05): kd/(kd+kp) = 2/3 → DE = 2/3; escape 1/3 = kp/(kd+kp)
CASES = [
    ((0.2, 0.6, 0.06, 0.04), 0.56, 0.44, 0.2, 0.24),
    ((0.3, 0.5, 0.08, 0.08), 0.55, 0.45, 0.2, 0.25),
    ((0.0, 1.0, 0.10, 0.05), 2 / 3, 1 / 3, 0.0, 1 / 3),
]


@pytest.mark.parametrize("params,de,escape,undegradable,b_escape", CASES)
def test_effective_degradability_and_escape(params, de, escape, undegradable, b_escape):
    assert destino.degradabilidad_efectiva(*params) == pytest.approx(de)
    assert destino.fraccion_escape(*params) == pytest.approx(escape)
    info = destino.informe_destino(*params)
    r = info["resultados"]
    assert r["degradabilidad_efectiva"] == pytest.approx(de)
    assert r["fraccion_escape"] == pytest.approx(escape)
    assert r["escape_fraccion_no_degradable"] == pytest.approx(undegradable)
    assert r["escape_fraccion_b"] == pytest.approx(b_escape)
    assert r["escape_fraccion_no_degradable"] + r["escape_fraccion_b"] == pytest.approx(r["fraccion_escape"])


def test_first_order_escape():
    # kp/(kd + kp): 0,05/0,15 = 1/3; 0,06/0,08 = 0,75
    assert destino.escape_primer_orden(0.10, 0.05) == pytest.approx(1 / 3)
    assert destino.escape_primer_orden(0.02, 0.06) == pytest.approx(0.75)
    # Caso a = 0, b = 1 del modelo general.
    assert destino.escape_primer_orden(0.02, 0.06) == pytest.approx(destino.fraccion_escape(0, 1, 0.02, 0.06))


def test_kinetic_limits():
    # kd >> kp: se degrada toda la fracción b (DE → a + b); kp >> kd: solo se degrada a (DE → a).
    assert destino.degradabilidad_efectiva(0.1, 0.5, 1e6, 1e-6) == pytest.approx(0.6)
    assert destino.degradabilidad_efectiva(0.1, 0.5, 1e-6, 1e6) == pytest.approx(0.1)
    # a + b = 1 exactamente es válido: sin fracción no degradable.
    assert destino.fraccion_escape(0.3, 0.7, 0.05, 0.05) == pytest.approx(0.35)


@pytest.mark.parametrize("params,match", [
    ((-0.1, 0.5, 0.05, 0.05), "a debe ser >= 0"),
    ((0.1, -0.5, 0.05, 0.05), "b debe ser >= 0"),
    ((30, 50, 0.05, 0.05), "porcentajes"),
    ((0.6, 0.5, 0.05, 0.05), "a \\+ b"),
    ((0.1, 0.5, 0.0, 0.05), "kd"),
    ((0.1, 0.5, -0.05, 0.05), "kd"),
    ((0.1, 0.5, 0.05, 0.0), "kp"),
    ((0.1, 0.5, 0.05, math.nan), "kp"),
    ((0.1, 0.5, math.inf, 0.05), "kd"),
    ((math.nan, 0.5, 0.05, 0.05), "a"),
    (("x", 0.5, 0.05, 0.05), "a"),
    ((True, 0.5, 0.05, 0.05), "booleano"),
])
def test_kinetics_validation(params, match):
    with pytest.raises(ValueError, match=match):
        destino.degradabilidad_efectiva(*params)
    with pytest.raises(ValueError):
        destino.fraccion_escape(*params)


@pytest.mark.parametrize("kd,kp", [(0.0, 0.05), (0.05, 0.0), (-1.0, 0.05), (0.05, math.inf)])
def test_first_order_validation(kd, kp):
    with pytest.raises(ValueError):
        destino.escape_primer_orden(kd, kp)


def test_post_ruminal_and_absorbable_dose():
    # 10 g × 0,44 = 4,4 g posruminales; × 0,5 de digestibilidad intestinal = 2,2 g absorbibles.
    assert destino.dosis_posruminal(10, 0.44) == pytest.approx(4.4)
    assert destino.dosis_absorbible(10, 0.44, 0.5) == pytest.approx(2.2)
    assert destino.dosis_posruminal(0, 0.44) == 0
    with pytest.raises(ValueError):
        destino.dosis_posruminal(-1, 0.44)
    with pytest.raises(ValueError, match="fraccion_escape"):
        destino.dosis_posruminal(10, 44)
    with pytest.raises(ValueError, match="digestibilidad_intestinal"):
        destino.dosis_absorbible(10, 0.44, 1.5)


def test_report_kinetic_mode():
    info = destino.informe_destino(0.2, 0.6, 0.06, 0.04, dosis=10, digestibilidad_intestinal=0.5,
                                   unidad_dosis="g/animal/día")
    r = info["resultados"]
    assert r["dosis_posruminal"] == pytest.approx(4.4)
    assert r["dosis_absorbible"] == pytest.approx(2.2)
    assert r["fraccion_absorbible"] == pytest.approx(0.22)
    assert r["unidad_dosis"] == "g/animal/día"
    assert info["etiqueta"] == "[DER]"
    assert info["entradas"] == {"a": 0.2, "b": 0.6, "kd_h-1": 0.06, "kp_h-1": 0.04, "dosis": 10.0,
                                "digestibilidad_intestinal": 0.5, "unidad_dosis": "g/animal/día"}
    assumptions = " ".join(info["supuestos"])
    assert "primer orden" in assumptions
    assert "kp constante" in assumptions
    assert "latencia" in assumptions and "McDonald (1981)" in assumptions
    refs = " ".join(info["referencias"])
    assert "10.1017/S0021859600063048" in refs and "J Agric Sci 92(2):499-503" in refs
    assert "96:251-252" in refs and "pendientes de verificar" in refs
    text = destino.texto_informe_destino(info)
    assert "0.56" in text and "4.4" in text and "2.2" in text and "h⁻¹" in text


def test_report_without_dose_or_digestibility_leaves_results_empty():
    info = destino.informe_destino(0.2, 0.6, 0.06, 0.04)
    r = info["resultados"]
    assert r["fraccion_escape"] == pytest.approx(0.44)
    assert r["dosis_posruminal"] is None
    assert r["dosis_absorbible"] is None
    assert r["fraccion_absorbible"] is None
    # Sin digestibilidad no hay dosis absorbible aunque haya dosis.
    r = destino.informe_destino(0.2, 0.6, 0.06, 0.04, dosis=10)["resultados"]
    assert r["dosis_posruminal"] == pytest.approx(4.4)
    assert r["dosis_absorbible"] is None
    assert "sin calcular" in destino.texto_informe_destino(destino.informe_destino(0.2, 0.6, 0.06, 0.04, dosis=10))


def test_report_declared_escape_mode():
    # 20 × 0,3 = 6; × 0,9 = 5,4
    info = destino.informe_destino(fraccion_escape=0.3, dosis=20, digestibilidad_intestinal=0.9)
    r = info["resultados"]
    assert r["degradabilidad_efectiva"] is None
    assert r["fraccion_escape"] == pytest.approx(0.3)
    assert r["dosis_posruminal"] == pytest.approx(6.0)
    assert r["dosis_absorbible"] == pytest.approx(5.4)
    assert info["referencias"] == []
    assert any("[SUP]" in s for s in info["supuestos"])
    assert "Fracción de escape declarada" in destino.texto_informe_destino(info)


@pytest.mark.parametrize("kwargs,match", [
    ({"a": 0.2, "b": 0.6, "kd": 0.06, "kp": 0.04, "fraccion_escape": 0.4}, "no ambas"),
    ({"a": 0.2, "b": 0.6, "kd": 0.06}, "kp"),
    ({"kd": 0.06, "kp": 0.04}, "a, b"),
    ({}, "cinética"),
    ({"fraccion_escape": 1.2}, "fraccion_escape"),
    ({"fraccion_escape": 0.3, "digestibilidad_intestinal": -0.1}, "digestibilidad_intestinal"),
    ({"fraccion_escape": 0.3, "dosis": -5}, "dosis"),
])
def test_report_input_errors(kwargs, match):
    with pytest.raises(ValueError, match=match):
        destino.informe_destino(**kwargs)
