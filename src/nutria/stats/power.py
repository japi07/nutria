"""Potencia y tamaño muestral para diseñar ensayos (in vivo) con varianzas realistas.

La DE debe venir de datos (p. ej. la DE combinada de los estudios del metaanálisis o la
varianza residual de ensayos previos en la misma especie y etapa), no de supuestos.
"""

from __future__ import annotations

import math

from scipy import stats


def _power_two_sample(n: int, delta: float, sd: float, alpha: float) -> float:
    df = 2 * n - 2
    ncp = abs(delta) / (sd * math.sqrt(2.0 / n))
    tcrit = stats.t.ppf(1 - alpha / 2, df)
    return float(stats.nct.sf(tcrit, df, ncp) + stats.nct.cdf(-tcrit, df, ncp))


def n_parallel(delta: float, sd: float, alpha: float = 0.05, power: float = 0.80, max_n: int = 100000) -> dict:
    """Unidades experimentales por grupo en un diseño paralelo de dos grupos (t bilateral exacta)."""
    if delta == 0 or sd <= 0:
        raise ValueError("delta debe ser distinta de 0 y sd > 0")
    n = 2
    while n <= max_n:
        if _power_two_sample(n, delta, sd, alpha) >= power:
            return {"n_por_grupo": n, "n_total": 2 * n, "potencia": _power_two_sample(n, delta, sd, alpha)}
        n += 1
    raise ValueError("Tamaño muestral fuera de rango: revisa delta y sd")


def n_crossover(delta: float, sd_within: float, alpha: float = 0.05, power: float = 0.80,
                max_n: int = 100000) -> dict:
    """Animales totales en un cruzado AB/BA (o aproximación para cuadrado latino) usando la DE
    intraanimal. Supone ausencia de efecto residual (carry-over); con periodos cortos no es seguro."""
    if delta == 0 or sd_within <= 0:
        raise ValueError("delta debe ser distinta de 0 y sd_within > 0")
    n = 4
    while n <= max_n:
        df = n - 2
        ncp = abs(delta) / (sd_within * math.sqrt(2.0 / n))
        tcrit = stats.t.ppf(1 - alpha / 2, df)
        pw = float(stats.nct.sf(tcrit, df, ncp) + stats.nct.cdf(-tcrit, df, ncp))
        if pw >= power:
            return {"n_total": n, "potencia": pw,
                    "nota": "Aproximación: verifica con simulación del modelo mixto real (periodo, secuencia)."}
        n += 2
    raise ValueError("Tamaño muestral fuera de rango")


def design_effect(animals_per_pen: int, icc: float) -> float:
    """Efecto de diseño cuando se miden animales dentro de corrales pero el corral es la unidad
    experimental: DE = 1 + (m - 1)·ICC."""
    if animals_per_pen < 1 or not 0 <= icc <= 1:
        raise ValueError("m >= 1 y 0 <= ICC <= 1")
    return 1 + (animals_per_pen - 1) * icc


def n_proportions(p1: float, p2: float, alpha: float = 0.05, power: float = 0.80) -> dict:
    """Animales por grupo para comparar incidencias (p. ej. cetosis clínica). Aproximación normal."""
    if not (0 < p1 < 1 and 0 < p2 < 1) or p1 == p2:
        raise ValueError("Proporciones en (0, 1) y distintas")
    za = stats.norm.ppf(1 - alpha / 2)
    zb = stats.norm.ppf(power)
    pbar = (p1 + p2) / 2
    num = (za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    n = math.ceil(num / (p1 - p2) ** 2)
    return {"n_por_grupo": n, "n_total": 2 * n,
            "nota": "Las variables de salud requieren muestras grandes; considera la granja como unidad si el "
                    "tratamiento se aplica por lote."}


def detectable_difference(n_per_group: int, sd: float, alpha: float = 0.05, power: float = 0.80) -> float:
    """Diferencia mínima detectable con n por grupo (diseño paralelo)."""
    # Intervalo acotado de forma adaptativa: con no-centralidades enormes la t no central
    # pierde precisión numérica, así que no se parte de un extremo arbitrariamente alto.
    lo, hi = 0.0, sd
    for _ in range(40):
        if _power_two_sample(n_per_group, hi, sd, alpha) >= power:
            break
        lo, hi = hi, hi * 2
    for _ in range(100):
        mid = (lo + hi) / 2
        if _power_two_sample(n_per_group, mid, sd, alpha) >= power:
            hi = mid
        else:
            lo = mid
    return hi
