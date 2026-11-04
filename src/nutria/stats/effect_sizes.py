"""Tamaños de efecto y conversión de medidas de dispersión habituales en ciencia animal.

En nutrición animal es frecuente que los artículos publiquen medias por tratamiento y un
único "SEM" combinado (error estándar de la media de mínimos cuadrados del ANOVA), no la
DE de cada grupo. Las conversiones aquí asumen replicación equilibrada; si no lo es,
regístralo en la extracción y valora un análisis de sensibilidad.
"""

from __future__ import annotations

import math

from scipy import stats

DISPERSION_TYPES = ("SD", "SE", "SEM_combinado", "SED", "IC95")


def sd_from_dispersion(value: float, n: float, kind: str, n_other: float | None = None) -> float:
    """Convierte una medida de dispersión en DE.

    - SD: ya es la DE.
    - SE: error estándar de la media de ese grupo -> DE = SE·√n.
    - SEM_combinado: SEM común de las medias de tratamiento (ANOVA, n réplicas por tratamiento)
      -> DE combinada = SEM·√n.
    - SED: error estándar de la diferencia entre dos medias -> DE combinada = SED / √(1/n1 + 1/n2).
    - IC95: semiamplitud del IC 95 % de la media del grupo -> DE = semiamplitud / t(0,975; n-1) · √n.
    """
    if value is None or n is None:
        raise ValueError("Faltan dispersión o n")
    if value < 0 or n <= 0:
        raise ValueError("Dispersión y n deben ser positivas")
    if kind == "SD":
        return float(value)
    if kind in ("SE", "SEM_combinado"):
        return float(value) * math.sqrt(n)
    if kind == "SED":
        other = n_other if n_other else n
        return float(value) / math.sqrt(1.0 / n + 1.0 / other)
    if kind == "IC95":
        if n < 2:
            raise ValueError("IC95 requiere n >= 2")
        return float(value) / stats.t.ppf(0.975, n - 1) * math.sqrt(n)
    raise ValueError(f"Tipo de dispersión desconocido: {kind}. Opciones: {', '.join(DISPERSION_TYPES)}")


def mean_difference(m_t: float, m_c: float, sd_t: float, sd_c: float, n_t: float, n_c: float) -> tuple[float, float]:
    """Diferencia de medias en unidades naturales (preferible si todos los estudios usan la misma unidad)."""
    return m_t - m_c, sd_t ** 2 / n_t + sd_c ** 2 / n_c


def hedges_g(m_t: float, m_c: float, sd_t: float, sd_c: float, n_t: float, n_c: float) -> tuple[float, float]:
    """Diferencia de medias estandarizada con corrección de sesgo para muestras pequeñas."""
    df = n_t + n_c - 2
    if df <= 0:
        raise ValueError("Se necesitan al menos 3 observaciones en total")
    sp = math.sqrt(((n_t - 1) * sd_t ** 2 + (n_c - 1) * sd_c ** 2) / df)
    if sp == 0:
        raise ValueError("DE combinada nula")
    d = (m_t - m_c) / sp
    j = 1 - 3 / (4 * df - 1)
    g = j * d
    var = (n_t + n_c) / (n_t * n_c) + g ** 2 / (2 * (n_t + n_c))
    return g, var


def log_ratio_of_means(m_t: float, m_c: float, sd_t: float, sd_c: float, n_t: float, n_c: float) -> tuple[float, float]:
    """Logaritmo de la razón de medias (efecto relativo); requiere medias positivas."""
    if m_t <= 0 or m_c <= 0:
        raise ValueError("El cociente de medias requiere medias positivas")
    return math.log(m_t / m_c), sd_t ** 2 / (n_t * m_t ** 2) + sd_c ** 2 / (n_c * m_c ** 2)


MEASURES = {"MD": mean_difference, "SMD": hedges_g, "ROM": log_ratio_of_means}


def effect_from_summary(measure: str, m_t: float, m_c: float, n_t: float, n_c: float, kind: str,
                        disp_t: float | None = None, disp_c: float | None = None) -> tuple[float, float]:
    """Calcula (yi, vi) a partir de medias, n y dispersión del tipo indicado.

    Si solo se dispone de un SEM combinado o un SED, pásalo como `disp_t` (o `disp_c`).
    """
    if measure not in MEASURES:
        raise ValueError(f"Medida desconocida: {measure}. Opciones: {', '.join(MEASURES)}")
    if kind in ("SEM_combinado", "SED"):
        common = disp_t if disp_t is not None else disp_c
        if kind == "SED":
            sd = sd_from_dispersion(common, n_t, "SED", n_c)
            sd_t = sd_c = sd
        else:
            n_ref = (n_t + n_c) / 2
            sd_t = sd_c = sd_from_dispersion(common, n_ref, "SEM_combinado")
    else:
        if disp_t is None or disp_c is None:
            raise ValueError(f"Con tipo {kind} se necesitan dispersiones de ambos grupos")
        sd_t = sd_from_dispersion(disp_t, n_t, kind)
        sd_c = sd_from_dispersion(disp_c, n_c, kind)
    return MEASURES[measure](m_t, m_c, sd_t, sd_c, n_t, n_c)
