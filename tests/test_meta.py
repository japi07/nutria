"""Validación frente a los resultados publicados de metafor para dat.bcg (Viechtbauer 2010)."""
import pytest

from nutria.stats import meta
from nutria.stats.metareg import meta_regression


def test_fixed_effect(bcg):
    yi, vi, _ = bcg
    res = meta.fixed_effect(yi, vi)
    assert res.estimate == pytest.approx(-0.4303, abs=1e-4)
    assert res.se == pytest.approx(0.0405, abs=1e-4)
    assert res.Q == pytest.approx(152.2330, abs=1e-3)


@pytest.mark.parametrize("method,tau2,est", [("DL", 0.3088, -0.7141), ("REML", 0.3132, -0.7145)])
def test_random_effects(bcg, method, tau2, est):
    yi, vi, _ = bcg
    res = meta.random_effects(yi, vi, method=method)
    assert res.tau2 == pytest.approx(tau2, abs=1e-4)
    assert res.estimate == pytest.approx(est, abs=1e-4)


def test_reml_heterogeneity(bcg):
    yi, vi, _ = bcg
    res = meta.random_effects(yi, vi, method="REML")
    assert res.se == pytest.approx(0.1798, abs=1e-4)
    assert res.I2 == pytest.approx(92.22, abs=0.01)
    assert res.H2 == pytest.approx(12.86, abs=0.01)


def test_hksj(bcg):
    yi, vi, _ = bcg
    res = meta.random_effects(yi, vi, method="REML", hksj=True)
    assert res.se == pytest.approx(0.1808, abs=1e-4)
    assert res.ci_low == pytest.approx(-1.1084, abs=1e-4)
    assert res.ci_high == pytest.approx(-0.3206, abs=1e-4)


def test_paule_mandel_solves_generalized_q(bcg):
    import numpy as np
    yi, vi, _ = bcg
    t = meta.tau2_pm(yi, vi)
    w = 1 / (vi + t)
    mu = (w * yi).sum() / w.sum()
    assert float((w * (yi - mu) ** 2).sum()) == pytest.approx(len(yi) - 1, abs=1e-8)


def test_egger(bcg):
    yi, vi, _ = bcg
    eg = meta.egger_test(yi, vi)
    assert eg["t"] == pytest.approx(-1.4013, abs=1e-3)
    assert eg["p"] == pytest.approx(0.1887, abs=1e-3)


def test_meta_regression(bcg):
    yi, vi, ablat = bcg
    res = meta_regression(yi, vi, ablat, ["ablat"])
    assert res.coef[0] == pytest.approx(0.2515, abs=1e-3)
    assert res.coef[1] == pytest.approx(-0.0291, abs=1e-4)
    assert res.se[1] == pytest.approx(0.0072, abs=1e-4)
    assert res.QE == pytest.approx(30.7331, abs=1e-3)
    assert res.tau2 == pytest.approx(0.0764, abs=2e-4)
    assert res.R2 == pytest.approx(75.6, abs=0.2)


def test_leave_one_out_and_subgroups(bcg):
    yi, vi, ablat = bcg
    loo = meta.leave_one_out(yi, vi)
    assert len(loo) == len(yi)
    groups = ["alta" if a > 33 else "baja" for a in ablat]
    sg = meta.subgroups(yi, vi, groups)
    assert set(sg["subgrupos"]) == {"alta", "baja"}
    assert sg["p"] < 0.05


def test_input_validation():
    with pytest.raises(ValueError):
        meta.random_effects([0.1, 0.2], [0.01, 0.0])
