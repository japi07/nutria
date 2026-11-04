import pytest

from nutria.stats import power


def test_parallel_classic_values():
    assert power.n_parallel(0.5, 1.0)["n_por_grupo"] == 64
    assert power.n_parallel(1.0, 1.0)["n_por_grupo"] == 17


def test_crossover_needs_fewer_animals():
    par = power.n_parallel(1.0, 2.0)["n_total"]
    cross = power.n_crossover(1.0, 2.0)["n_total"]
    assert cross < par


def test_proportions_and_design_effect():
    assert power.n_proportions(0.3, 0.2)["n_por_grupo"] == 294
    assert power.design_effect(10, 0.1) == pytest.approx(1.9)
    mdd = power.detectable_difference(64, 1.0)
    assert mdd == pytest.approx(0.5, abs=0.01)
