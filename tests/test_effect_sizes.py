import math

import pytest

from nutria.stats import effect_sizes as es


def test_sd_conversions():
    assert es.sd_from_dispersion(2.0, 10, "SD") == 2.0
    assert es.sd_from_dispersion(0.8, 12, "SEM_combinado") == pytest.approx(0.8 * math.sqrt(12))
    # SED = DE·sqrt(1/n1 + 1/n2)
    assert es.sd_from_dispersion(1.0, 8, "SED", 8) == pytest.approx(1.0 / math.sqrt(2 / 8))
    with pytest.raises(ValueError):
        es.sd_from_dispersion(1.0, 8, "desconocido")


def test_mean_difference_with_pooled_sem_equals_two_sem_squared():
    yi, vi = es.effect_from_summary("MD", 31.0, 30.0, 12, 12, "SEM_combinado", 0.5)
    assert yi == pytest.approx(1.0)
    assert vi == pytest.approx(2 * 0.5 ** 2)


def test_hedges_g_and_rom():
    g, v = es.hedges_g(11, 10, 2, 2, 20, 20)
    assert g == pytest.approx(0.5 * (1 - 3 / (4 * 38 - 1)))
    assert v > 0
    lr, _ = es.log_ratio_of_means(11, 10, 2, 2, 20, 20)
    assert lr == pytest.approx(math.log(1.1))
    with pytest.raises(ValueError):
        es.log_ratio_of_means(-1, 10, 2, 2, 20, 20)
