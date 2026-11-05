"""Pruebas de las correcciones del paquete de estadística (auditoría externa).

D6-7/D9-4c (HKSJ estándar y modificado), D6-6/D9-5 (Egger y sesgo de publicación),
D9-4 (rango de la meta-regresión), D6-2/D1-8 (efectos dependientes), D6-8 (medidas binarias),
D6-1/D1-4/D9-5 (tipo de dispersión obligatorio), D6-4/D4-9 (St-Pierre intra/entre) y
D6-5 (validación dejando un estudio fuera).
"""
import csv
import math
import warnings
from pathlib import Path

import numpy as np
import pytest
from scipy import stats

from nutria.stats import effect_sizes as es
from nutria.stats import meta
from nutria.stats.metareg import meta_regression
from nutria.stats.predictive import loso_cv

ROOT = Path(__file__).resolve().parents[1]

# k = 3 homogéneo (leche, kg/d) con SEM combinado, construido por la auditoría para D6-7: q < 1.
HOMOGENEOUS = [(32.0, 31.0, 12, 12, 0.60), (30.9, 30.0, 10, 10, 0.55), (33.1, 32.2, 15, 15, 0.70)]


def _homogeneous():
    pairs = [es.effect_from_summary("MD", mt, mc, nt, nc, "SEM_combinado", sem) for mt, mc, nt, nc, sem in HOMOGENEOUS]
    yi, vi = zip(*pairs)
    return np.array(yi), np.array(vi)


def _bcg_rows():
    with open(ROOT / "ejemplos" / "validacion_bcg.csv", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- D6-7 y D9-4c: HKSJ
def test_ajuste_hksj_equivale_al_argumento_hksj(bcg):
    yi, vi, _ = bcg
    nuevo = meta.random_effects(yi, vi, method="REML", ajuste="hksj")
    antiguo = meta.random_effects(yi, vi, method="REML", hksj=True)
    posicional = meta.random_effects(yi, vi, "REML", True)
    for res in (antiguo, posicional):
        assert res.ajuste == "hksj"
        assert (res.estimate, res.se, res.ci_low, res.ci_high, res.p_value) == \
               (nuevo.estimate, nuevo.se, nuevo.ci_low, nuevo.ci_high, nuevo.p_value)
    assert nuevo.se == pytest.approx(0.1808, abs=1e-4)  # metafor, test = "knha"
    assert nuevo.hksj_q == pytest.approx(1.0113, abs=1e-4)
    assert nuevo.ci_mod_low is None and nuevo.ci_mod_high is None  # q > 1: no hace falta
    sin = meta.random_effects(yi, vi, method="REML", hksj=False)
    assert sin.ajuste == "ninguno" and sin.test == "z" and sin.hksj_q is None


def test_hksj_mod_coincide_con_hksj_si_q_mayor_que_1(bcg):
    yi, vi, _ = bcg
    hk = meta.random_effects(yi, vi, ajuste="hksj")
    mod = meta.random_effects(yi, vi, ajuste="hksj-mod")
    assert mod.ajuste == "hksj-mod" and "modificado" in mod.test
    assert (mod.ci_low, mod.ci_high) == pytest.approx((hk.ci_low, hk.ci_high), abs=1e-12)


def test_hksj_con_q_menor_que_1_da_ic_modificado():
    yi, vi = _homogeneous()
    res = meta.random_effects(yi, vi, method="REML", ajuste="hksj")
    # Cálculo independiente: w = 1/(vi + tau²), q = Σw(y - μ)²/(k - 1), IC modificado con max(1, q)
    w = 1 / (vi + res.tau2)
    mu = float(np.sum(w * yi) / np.sum(w))
    q = float(np.sum(w * (yi - mu) ** 2) / (len(yi) - 1))
    crit = stats.t.ppf(0.975, len(yi) - 1)
    se_mod = math.sqrt(max(1.0, q) / np.sum(w))
    assert q < 1 and res.hksj_q == pytest.approx(q)
    assert res.ci_mod_low == pytest.approx(mu - crit * se_mod)
    assert res.ci_mod_high == pytest.approx(mu + crit * se_mod)
    assert (res.ci_mod_low, res.ci_mod_high) == pytest.approx((-1.2006, 3.0690), abs=1e-4)
    # El HKSJ estándar es más estrecho que Wald (el problema de D6-7); el modificado no
    wald = meta.random_effects(yi, vi, method="REML")
    assert res.ci_high - res.ci_low < wald.ci_high - wald.ci_low < res.ci_mod_high - res.ci_mod_low
    assert any("max(1, q)" in a for a in res.avisos)  # el IC estándar es más estrecho que Wald: aviso visible
    assert "AVISO:" in res.summary() and "HKSJ modificado" in res.summary()
    # "hksj-mod" usa max(1, q) en el resultado principal
    mod = meta.random_effects(yi, vi, method="REML", ajuste="hksj-mod")
    assert (mod.ci_low, mod.ci_high) == pytest.approx((res.ci_mod_low, res.ci_mod_high))
    assert mod.p_value == pytest.approx(0.2004, abs=1e-4)
    assert mod.ci_mod_low is None


@pytest.mark.parametrize("ajuste", ["hksj", "hksj-mod"])
def test_hksj_con_q_cero_no_divide_por_cero(ajuste):
    vi = np.array([0.1, 0.2, 0.3])
    res = meta.random_effects([0.37, 0.37, 0.37], vi, method="REML", ajuste=ajuste)
    assert res.estimate == pytest.approx(0.37)
    assert res.se == pytest.approx(math.sqrt(1 / np.sum(1 / vi)))  # max(1, q) = 1
    assert math.isfinite(res.p_value) and math.isfinite(res.ci_low)
    assert any("max(1, q)" in n for n in res.notes)
    # Uno fuera y subgrupos con efectos idénticos (antes, ZeroDivisionError sin capturar)
    assert len(meta.leave_one_out([1.0, 1.0, 2.0], [0.1, 0.2, 0.3], ajuste=ajuste)) == 3
    sg = meta.subgroups([1.0, 1.0, 0.5, 0.8], [0.1, 0.2, 0.1, 0.2], ["a", "a", "b", "b"], ajuste=ajuste)
    assert sg["subgrupos"]["a"]["ajuste"] == ajuste and math.isfinite(sg["p"])


def test_ajuste_desconocido_y_k_1():
    with pytest.raises(ValueError, match="Ajuste desconocido"):
        meta.random_effects([0.1, 0.2, 0.3], [0.01, 0.02, 0.03], ajuste="knha")
    uno = meta.random_effects([0.5], [0.04], ajuste="hksj")
    assert uno.test == "z" and any("k = 1" in n for n in uno.notes)


def test_uno_fuera_y_subgrupos_aceptan_ajuste(bcg):
    yi, vi, ablat = bcg
    loo = meta.leave_one_out(yi, vi, ajuste="hksj-mod")
    ref = meta.random_effects(yi[1:], vi[1:], ajuste="hksj-mod")
    assert loo[0]["ci_low"] == pytest.approx(ref.ci_low) and loo[0]["ci_high"] == pytest.approx(ref.ci_high)
    assert meta.leave_one_out(yi, vi, hksj=True)[0]["ci_low"] == \
        pytest.approx(meta.random_effects(yi[1:], vi[1:], ajuste="hksj").ci_low)
    groups = ["alta" if a > 33 else "baja" for a in ablat]
    sg = meta.subgroups(yi, vi, groups, ajuste="hksj")
    assert all(r["ajuste"] == "hksj" for r in sg["subgrupos"].values())
    assert meta.subgroups(yi, vi, groups, hksj=True)["Q_entre"] == pytest.approx(sg["Q_entre"])


# --------------------------------------------------------------------------- D6-6 y D9-5: Egger
def test_egger_con_vi_iguales_no_es_estimable():
    eg = meta.egger_test([0.1, 0.3, 0.2, 0.5, 0.4], [0.04] * 5)
    assert eg["estimable"] is False and "constante" in eg["motivo"]
    assert "p" not in eg and "intercepto" not in eg
    # vi "iguales" salvo error de redondeo
    eg2 = meta.egger_test([0.1, 0.3, 0.2, 0.5], [0.3, 0.1 + 0.2, 0.3, 0.30000000000000004])
    assert eg2["estimable"] is False


def test_egger_con_k_menor_que_3_no_es_estimable():
    eg = meta.egger_test([0.1, 0.3], [0.01, 0.02])
    assert eg == {"estimable": False, "k": 2, "motivo": eg["motivo"]} and "3" in eg["motivo"]


def test_egger_bcg_sigue_validado(bcg):
    yi, vi, _ = bcg
    eg = meta.egger_test(yi, vi)
    assert eg["estimable"] is True
    assert eg["t"] == pytest.approx(-1.4013, abs=1e-3) and eg["p"] == pytest.approx(0.1887, abs=1e-3)


def test_sesgo_de_publicacion_no_evaluable_con_k_menor_que_10():
    pb = meta.publication_bias([0.1, 0.3, 0.2, 0.5, 0.4], [0.04, 0.05, 0.06, 0.07, 0.08])
    assert pb["evaluable"] is False and pb["motivo"].startswith("k < 10:")
    assert "egger" not in pb
    pb5 = meta.publication_bias([0.1, 0.3, 0.2, 0.5, 0.4], [0.04, 0.05, 0.06, 0.07, 0.08], k_min=5)
    assert pb5["evaluable"] is True and pb5["egger"]["estimable"] is True


def test_sesgo_de_publicacion_bcg(bcg):
    yi, vi, _ = bcg
    pb = meta.publication_bias(yi, vi)
    assert pb["evaluable"] is True and pb["k"] == 13
    assert pb["egger"]["t"] == pytest.approx(-1.4013, abs=1e-3)
    assert meta.publication_bias(yi, vi, k_min=14)["motivo"].startswith("k < 14:")
    # Con k suficiente pero vi iguales, Egger no es estimable y el sesgo tampoco es evaluable
    flat = meta.publication_bias(np.linspace(-0.5, 0.5, 12), [0.05] * 12)
    assert flat["evaluable"] is False and "constante" in flat["motivo"]
    with pytest.raises(ValueError):
        meta.publication_bias(yi, vi, k_min=2)


# --------------------------------------------------------------------------- D9-4: rango en meta-regresión
def test_meta_regresion_rechaza_moderador_constante(bcg):
    yi, vi, ablat = bcg
    with pytest.raises(ValueError, match="Moderadores constantes o colineales: dieta"):
        meta_regression(yi, vi, np.column_stack([ablat, np.full(len(yi), 3.0)]), ["ablat", "dieta"])


def test_meta_regresion_rechaza_moderadores_colineales(bcg):
    yi, vi, ablat = bcg
    with pytest.raises(ValueError, match="Moderadores constantes o colineales: ablat_mg"):
        meta_regression(yi, vi, np.column_stack([ablat, 1000 * ablat + 5]), ["ablat", "ablat_mg"])


def test_meta_regresion_no_depende_de_la_escala(bcg):
    yi, vi, ablat = bcg
    base = meta_regression(yi, vi, ablat, ["ablat"])
    tiny = meta_regression(yi, vi, ablat * 1e-6, ["ablat"])
    assert tiny.coef[1] == pytest.approx(base.coef[1] * 1e6, rel=1e-6)


def test_meta_regresion_entradas_no_validas(bcg):
    yi, vi, ablat = bcg
    with pytest.raises(ValueError, match="finitos"):
        meta_regression(yi, vi, np.where(np.arange(len(yi)) == 0, np.nan, ablat))
    with pytest.raises(ValueError, match="nombre"):
        meta_regression(yi, vi, ablat, ["a", "b"])


def test_meta_regresion_ajuste_knapp_hartung(bcg):
    yi, vi, ablat = bcg
    z = meta_regression(yi, vi, ablat, ["ablat"])
    kh = meta_regression(yi, vi, ablat, ["ablat"], knha=True)
    assert kh.ajuste == "hksj" and z.ajuste == "ninguno"
    assert meta_regression(yi, vi, ablat, ["ablat"], ajuste="hksj").se == pytest.approx(kh.se)
    assert kh.se[1] == pytest.approx(z.se[1] * math.sqrt(kh.kh_s2))
    mod = meta_regression(yi, vi, ablat, ["ablat"], ajuste="hksj-mod")
    assert mod.se[1] == pytest.approx(z.se[1] * math.sqrt(max(1.0, kh.kh_s2)))
    # s² < 1: el aviso recomienda el modificado, que usa max(1, s²), y se dan su p y su IC
    yh, vh, dosis = [0.9, 1.0, 1.05, 1.1, 1.0], [0.3] * 5, [1, 2, 3, 4, 5]
    low = meta_regression(yh, vh, dosis, ["dosis"], knha=True)
    assert low.kh_s2 < 1 and any("hksj-mod" in a for a in low.avisos)
    assert low.p_values_mod[1] > low.p_values[1] and "AVISO:" in low.summary()
    low_mod = meta_regression(yh, vh, dosis, ["dosis"], ajuste="hksj-mod")
    assert low_mod.se[1] > low.se[1]


# --------------------------------------------------------------------------- D6-2 y D1-8: dependencia
def test_detect_dependence():
    labels = ["A", "A ", "B", "C", "C", "C", "", None]
    assert meta.detect_dependence(labels) == {"A": [0, 1], "C": [3, 4, 5]}
    assert meta.detect_dependence(["A", "B"]) == {}


def test_aggregate_dependent_formula():
    yi = [0.2, 0.4, 1.0, 0.1, 0.3, 0.5]
    vi = [0.04, 0.09, 0.05, 0.01, 0.04, 0.09]
    y, v, labels = meta.aggregate_dependent(yi, vi, ["A", "A", "B", "C", "C", "C"], rho=0.5)
    assert labels == ["A", "B", "C"]
    assert y == pytest.approx([0.3, 1.0, 0.3])
    # A: (0,04 + 0,09 + 2·0,5·0,2·0,3)/4 ; C: (Σvi + 0,5·2·(0,1·0,2 + 0,1·0,3 + 0,2·0,3))/9
    assert v[0] == pytest.approx((0.04 + 0.09 + 2 * 0.5 * 0.2 * 0.3) / 4)
    assert v[1] == 0.05
    assert v[2] == pytest.approx((0.14 + 0.5 * 2 * (0.02 + 0.03 + 0.06)) / 9)


def test_aggregate_dependent_casos_limite():
    yi, vi, cl = [0.2, 0.4], [0.04, 0.09], ["E1", "E1"]
    assert meta.aggregate_dependent(yi, vi, cl, rho=0)[1][0] == pytest.approx((0.04 + 0.09) / 4)
    assert meta.aggregate_dependent(yi, vi, cl, rho=1)[1][0] == pytest.approx(((0.2 + 0.3) / 2) ** 2)
    for rho in (-0.1, 1.5, float("nan"), "alto"):
        with pytest.raises(ValueError, match="rho"):
            meta.aggregate_dependent(yi, vi, cl, rho=rho)
    with pytest.raises(ValueError, match="misma longitud"):
        meta.aggregate_dependent(yi, vi, ["E1"])
    with pytest.raises(ValueError, match="identificador"):
        meta.aggregate_dependent(yi, vi, ["E1", " "])


def test_agregar_antes_del_metaanalisis():
    # 3 brazos de E1 frente a un control compartido, 2 de E2 y 1 de E3: k = 3 estudios, no 6 filas
    yi = [0.5, 0.6, 0.7, 0.2, 0.3, 0.4]
    vi = [0.02, 0.02, 0.03, 0.04, 0.04, 0.05]
    labels = ["E1", "E1", "E1", "E2", "E2", "E3"]
    assert set(meta.detect_dependence(labels)) == {"E1", "E2"}
    y, v, _ = meta.aggregate_dependent(yi, vi, labels)
    res = meta.random_effects(y, v, ajuste="hksj")
    assert res.k == 3 and math.isfinite(res.ci_low)
    # Tratar las 6 filas como independientes subestima el EE del efecto común
    assert meta.fixed_effect(y, v).se > meta.fixed_effect(yi, vi).se


# --------------------------------------------------------------------------- D6-8: medidas binarias
def test_log_rr_reproduce_exactamente_bcg():
    for r in _bcg_rows():
        tpos, tneg, cpos, cneg = (int(r[k]) for k in ("tpos", "tneg", "cpos", "cneg"))
        yi, vi = es.log_risk_ratio(tpos, tpos + tneg, cpos, cpos + cneg)
        assert yi == float(r["yi"]) and vi == float(r["vi"]), r["estudio"]
        assert es.effect_from_counts("RR", tpos, tpos + tneg, cpos, cpos + cneg) == (yi, vi)


def test_log_or_y_diferencia_de_riesgos():
    yi, vi = es.effect_from_counts("OR", 4, 123, 11, 139)  # Aronson 1948 (dat.bcg)
    assert yi == pytest.approx(math.log((4 * 128) / (119 * 11)))
    assert vi == pytest.approx(1 / 4 + 1 / 119 + 1 / 11 + 1 / 128)
    rd, vrd = es.effect_from_counts("rd", 3, 10, 1, 10)
    assert rd == pytest.approx(0.2) and vrd == pytest.approx(0.3 * 0.7 / 10 + 0.1 * 0.9 / 10)


def test_correccion_de_continuidad_con_celda_cero():
    # a = 0: se suma 0,5 a las cuatro celdas (a, b, c, d) = (0,5; 10,5; 3,5; 7,5)
    yi, vi = es.log_odds_ratio(0, 10, 3, 10)
    assert yi == pytest.approx(math.log(0.5 * 7.5 / (10.5 * 3.5)))
    assert vi == pytest.approx(1 / 0.5 + 1 / 10.5 + 1 / 3.5 + 1 / 7.5)
    yr, vr = es.log_risk_ratio(0, 10, 3, 10)
    assert yr == pytest.approx(math.log((0.5 / 11) / (3.5 / 11)))
    assert vr == pytest.approx(1 / 0.5 - 1 / 11 + 1 / 3.5 - 1 / 11)
    # Celda cero en "sin evento" (b = 0) también activa la corrección
    assert es.log_odds_ratio(10, 10, 3, 10)[0] == pytest.approx(math.log(10.5 * 7.5 / (0.5 * 3.5)))
    # Sin celdas cero no hay corrección
    assert es.log_odds_ratio(2, 10, 3, 10)[0] == pytest.approx(math.log(2 * 7 / (8 * 3)))


@pytest.mark.parametrize("args,match", [
    ((11, 10, 3, 10), "no puede superar"),
    ((-1, 10, 3, 10), "negativo"),
    ((2.5, 10, 3, 10), "entero"),
    ((0, 0, 3, 10), "> 0"),
    ((None, 10, 3, 10), "Falta ev_t"),
    ((2, 10, "tres", 10), "no es numérico"),
    ((True, 10, 3, 10), "recuento"),
])
def test_recuentos_no_validos(args, match):
    with pytest.raises(ValueError, match=match):
        es.log_risk_ratio(*args)


def test_diferencia_de_riesgos_sin_varianza_y_medida_desconocida():
    with pytest.raises(ValueError, match="varianza nula"):
        es.risk_difference(0, 10, 0, 12)
    with pytest.raises(ValueError, match="Medida binaria desconocida"):
        es.effect_from_counts("HR", 1, 10, 2, 10)
    with pytest.raises(ValueError, match="^Granja 3: ev_c"):
        es.effect_from_counts("OR", 1, 10, 12, 10, label="Granja 3")
    assert es.effect_from_counts("rr", 2.0, 10.0, 3, 10) == es.log_risk_ratio(2, 10, 3, 10)


# --------------------------------------------------------------------------- D6-1, D1-4, D9-5: dispersión
@pytest.mark.parametrize("kind", [None, "", "  "])
def test_falta_el_tipo_de_dispersion(kind):
    with pytest.raises(ValueError, match="tipo_disp"):
        es.effect_from_summary("MD", 31.0, 30.0, 12, 12, kind, 0.5, 0.5)


@pytest.mark.parametrize("missing,args", [
    ("media_trt", ("MD", None, 30.0, 12, 12, "SD", 1.0, 1.0)),
    ("media_ctrl", ("MD", 31.0, float("nan"), 12, 12, "SD", 1.0, 1.0)),
    ("n_trt", ("MD", 31.0, 30.0, "", 12, "SD", 1.0, 1.0)),
    ("n_ctrl", ("SMD", 31.0, 30.0, 12, None, "SE", 1.0, 1.0)),
    ("disp_trt", ("ROM", 31.0, 30.0, 12, 12, "IC95", None, 1.0)),
    ("disp_ctrl", ("MD", 31.0, 30.0, 12, 12, "SD", 1.0, None)),
    ("disp_trt o disp_ctrl", ("MD", 31.0, 30.0, 12, 12, "SEM_combinado", None, None)),
])
def test_falta_un_campo_y_se_nombra(missing, args):
    with pytest.raises(ValueError, match=missing):
        es.effect_from_summary(*args)


def test_effect_from_summary_mensajes_y_validacion():
    with pytest.raises(ValueError, match="^Smith 2010: .*media_trt, n_ctrl"):
        es.effect_from_summary("MD", None, 30.0, 12, None, "SD", 1.0, 1.0, label="Smith 2010")
    with pytest.raises(ValueError, match="desconocido"):
        es.effect_from_summary("MD", 31.0, 30.0, 12, 12, "DE", 1.0, 1.0)
    with pytest.raises(ValueError, match="no coinciden"):
        es.effect_from_summary("MD", 31.0, 30.0, 12, 12, "SEM_combinado", 0.5, 0.6)
    with pytest.raises(ValueError, match="media_trt no es numérico"):
        es.effect_from_summary("MD", "n.d.", 30.0, 12, 12, "SD", 1.0, 1.0)
    with pytest.raises(ValueError, match="n_ctrl debe ser > 0"):
        es.effect_from_summary("MD", 31.0, 30.0, 12, 0, "SD", 1.0, 1.0)
    # El SEM combinado puede ir en cualquiera de las dos columnas (o en ambas si coincide)
    ref = es.effect_from_summary("MD", 31.0, 30.0, 12, 12, "SEM_combinado", 0.5)
    assert es.effect_from_summary("MD", 31.0, 30.0, 12, 12, "SEM_combinado", None, 0.5) == ref
    assert es.effect_from_summary("MD", 31.0, 30.0, 12, 12, "SEM_combinado", 0.5, 0.5) == ref


def test_sem_combinado_no_se_trata_como_de():
    # Caso de D6-1: tratar el SEM como DE subestimaría vi en un factor n
    sem = es.effect_from_summary("MD", 31.0, 30.0, 12, 12, "SEM_combinado", 0.5)
    sd = es.effect_from_summary("MD", 31.0, 30.0, 12, 12, "SD", 0.5, 0.5)
    assert sem[1] == pytest.approx(12 * sd[1])


# --------------------------------------------------------------------------- D6-4 y D4-9: St-Pierre
def _dose_rows(n_studies=8, doses=4, within=2.0, between=-1.0, noise=0.05, seed=1, shift=2.0):
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(n_studies):
        base = shift * s
        intercept = rng.normal(0, 0.5)
        mean_s = base + (doses - 1) / 2
        for j in range(doses):
            dose = base + j
            y = 10 + intercept + within * (dose - mean_s) + between * mean_s + rng.normal(0, noise)
            rows.append({"estudio": f"S{s}", "dosis": str(dose), "leche": str(y)})
    return rows


def test_st_pierre_descompone_intra_y_entre():
    pytest.importorskip("statsmodels")
    from nutria.stats import mixed
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = mixed.st_pierre(_dose_rows(), "leche", "dosis", "estudio")
    assert res["descomponer"] is True
    assert set(res["efectos_fijos"]) == {"Intercept", "dosis_intra", "dosis_entre"}
    assert res["pendiente_intra"]["estimacion"] == pytest.approx(2.0, abs=0.05)
    assert res["pendiente_entre"]["estimacion"] == pytest.approx(-1.0, abs=0.1)
    assert res["diferencia_entre_intra"]["estimacion"] == pytest.approx(-3.0, abs=0.15)
    assert res["pendiente_intra"]["ic95"][0] < 2.0 < res["pendiente_intra"]["ic95"][1]
    notas = " ".join(res["notas"])
    assert "Wald z" in notas and "Kenward-Roger" in notas and "lme4/lmerTest" in notas
    assert "Mundlak" in notas


def test_st_pierre_sin_descomponer_y_casos_limite():
    pytest.importorskip("statsmodels")
    from nutria.stats import mixed
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        clasico = mixed.st_pierre(_dose_rows(), "leche", "dosis", "estudio", descomponer=False)
        assert "pendiente" in clasico and "pendiente_intra" not in clasico
        assert any("mezcla" in n for n in clasico["notas"])
        # Misma dosis media en todos los estudios: no hay información entre estudios
        igual = mixed.st_pierre(_dose_rows(shift=0.0), "leche", "dosis", "estudio")
        assert igual["pendiente_entre"] is None and igual["diferencia_entre_intra"] is None
        assert igual["pendiente_intra"]["estimacion"] == pytest.approx(2.0, abs=0.05)
    with pytest.raises(ValueError, match="más de un nivel de dosis"):
        mixed.st_pierre(_dose_rows(doses=1), "leche", "dosis", "estudio")
    rows = [dict(r, dosis_intra="1") for r in _dose_rows()]
    with pytest.raises(ValueError, match="ya existe"):
        mixed.st_pierre(rows, "leche", "dosis", "estudio")
    with pytest.raises(ValueError, match="Faltan columnas"):
        mixed.st_pierre(_dose_rows(), "leche", "dosis_mg", "estudio")


def test_st_pierre_pendiente_aleatoria_sobre_dosis_intra():
    pytest.importorskip("statsmodels")
    from nutria.stats import mixed
    rng = np.random.default_rng(7)
    rows = []
    for s in range(10):
        base, a, b = 1.5 * s, rng.normal(0, 1), 2 + rng.normal(0, 0.4)
        for j in range(4):
            d = base + j
            rows.append({"st": s, "d": d, "y": 10 + a + b * (d - base - 1.5) - (base + 1.5) + rng.normal(0, 0.2)})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = mixed.st_pierre(rows, "y", "d", "st", random_slope=True)
    assert np.array(res["varianza_estudio"]).shape == (2, 2)
    assert res["pendiente_intra"]["estimacion"] == pytest.approx(2.0, abs=0.4)


def test_st_pierre_docstring_corregido():
    from nutria.stats import mixed
    doc = mixed.__doc__
    assert "intra y entre" in doc and "Mundlak" in doc
    assert "eliminando el sesgo" not in doc


# --------------------------------------------------------------------------- D6-5: validación LOSO
def _linear_rows(slope=2.0, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for s, offset in zip("ABCDE", (0.0, 1.0, 2.5, 4.0, 5.5)):
        for j in range(3):
            x = offset + j
            rows.append({"estudio": s, "x": str(x), "y": str(1.0 + slope * x + rng.normal(0, noise))})
    return rows


def test_loso_con_relacion_exacta():
    cv = loso_cv(_linear_rows(), "y", ["x"], "estudio")
    assert cv["n_estudios"] == 5 and cv["n_observaciones"] == 15
    assert cv["rmse"] < 1e-9 and cv["mae"] < 1e-9
    assert cv["r2_fuera_estudio"] == pytest.approx(1.0)
    assert cv["coeficientes_modelo_completo"] == pytest.approx({"intercepto": 1.0, "x": 2.0})
    assert [p["estudio"] for p in cv["por_estudio"]] == list("ABCDE")


def test_loso_coincide_con_calculo_manual():
    rows = _linear_rows(noise=0.8, seed=3)
    cv = loso_cv(rows, "y", "x", "estudio")
    x = np.array([float(r["x"]) for r in rows])
    y = np.array([float(r["y"]) for r in rows])
    s = np.array([r["estudio"] for r in rows])
    sse = sse0 = 0.0
    errors = {}
    for st in "ABCDE":
        train, test = s != st, s == st
        beta = np.polyfit(x[train], y[train], 1)
        e = y[test] - np.polyval(beta, x[test])
        errors[st] = e
        sse += float(np.sum(e ** 2))
        sse0 += float(np.sum((y[test] - y[train].mean()) ** 2))
    for item in cv["por_estudio"]:
        assert item["errores"] == pytest.approx(list(errors[item["estudio"]]))
        assert item["rmse"] == pytest.approx(math.sqrt(np.mean(errors[item["estudio"]] ** 2)))
    assert cv["rmse"] == pytest.approx(math.sqrt(sse / len(y)))
    assert cv["r2_fuera_estudio"] == pytest.approx(1 - sse / sse0)


def test_loso_peor_que_la_media():
    # La relación se invierte de un estudio a otro: el modelo predice peor que la media
    rows = [{"s": s, "x": x, "y": x if sign > 0 else 1 - x}
            for s, sign in zip("ABCD", (1, -1, 1, -1)) for x in (0.0, 1.0)]
    cv = loso_cv(rows, "y", "x", "s")
    assert cv["r2_fuera_estudio"] == pytest.approx(-7 / 9)
    assert any("peor que la media" in n for n in cv["notas"])


def test_loso_validaciones():
    rows = _linear_rows()
    with pytest.raises(ValueError, match="al menos 3 estudios"):
        loso_cv([r for r in rows if r["estudio"] in "AB"], "y", ["x"], "estudio")
    with pytest.raises(ValueError, match="colineales"):
        loso_cv([dict(r, x2=str(2 * float(r["x"]))) for r in rows], "y", ["x", "x2"], "estudio")
    with pytest.raises(ValueError, match="Faltan columnas"):
        loso_cv(rows, "y", ["dosis"], "estudio")
    with pytest.raises(ValueError, match="predictor"):
        loso_cv(rows, "y", [], "estudio")
    sucias = rows + [{"estudio": "A", "x": "", "y": "3"}, {"estudio": "", "x": "1", "y": "3"},
                     {"estudio": "B", "x": "1", "y": "n.d."}]
    cv = loso_cv(sucias, "y", ["x"], "estudio")
    assert cv["filas_descartadas"] == 3 and cv["n_observaciones"] == 15
