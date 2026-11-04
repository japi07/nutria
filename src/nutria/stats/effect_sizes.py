"""Tamaños de efecto y conversión de medidas de dispersión habituales en ciencia animal.

En nutrición animal es frecuente que los artículos publiquen medias por tratamiento y un
único "SEM" combinado (error estándar de la media de mínimos cuadrados del ANOVA), no la
DE de cada grupo. Las conversiones aquí asumen replicación equilibrada; si no lo es,
regístralo en la extracción y valora un análisis de sensibilidad.

El tipo de dispersión nunca se supone: `effect_from_summary` exige `tipo_disp` y falla con un
mensaje que nombra el campo si falta cualquier media, n o dispersión.

Variables binarias (incidencias de salud o reproducción): `effect_from_counts` calcula el log
del riesgo relativo (RR), el log de la odds ratio (OR) o la diferencia de riesgos (RD) a partir
de los recuentos de animales (o unidades experimentales) con evento y totales por grupo.
"""

from __future__ import annotations

import math

from scipy import stats

DISPERSION_TYPES = ("SD", "SE", "SEM_combinado", "SED", "IC95")

#: Corrección de continuidad que se suma a las cuatro celdas de la tabla 2×2 si alguna es cero (RR y OR).
CONTINUITY_CORRECTION = 0.5


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


def _is_missing(value) -> bool:
    """None, cadena vacía o NaN cuentan como dato ausente."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def _as_number(value, field_name: str, prefix: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{prefix}{field_name} no es numérico: {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{prefix}{field_name} debe ser finito: {value!r}")
    return number


def effect_from_summary(measure: str, m_t: float, m_c: float, n_t: float, n_c: float, kind: str,
                        disp_t: float | None = None, disp_c: float | None = None,
                        *, label: str | None = None) -> tuple[float, float]:
    """Calcula (yi, vi) a partir de medias, n y dispersión del tipo indicado.

    `kind` (columna `tipo_disp`) es obligatorio: SD, SE, SEM_combinado, SED o IC95, tal como lo
    publica el artículo; no hay valor por defecto, porque tratar un SEM como DE subestima la
    varianza. Si falta cualquier media (media_trt, media_ctrl), n (n_trt, n_ctrl) o dispersión
    (disp_trt, disp_ctrl) se lanza ValueError nombrando el campo. Si solo se dispone de un SEM
    combinado o un SED, pásalo como `disp_t` (o `disp_c`); si se dan ambos, deben coincidir.
    `label` (p. ej. el estudio) se antepone a los mensajes de error.
    """
    prefix = f"{label}: " if label not in (None, "") else ""
    if measure not in MEASURES:
        raise ValueError(f"{prefix}Medida desconocida: {measure}. Opciones: {', '.join(MEASURES)}")
    if _is_missing(kind):
        raise ValueError(f"{prefix}Falta el tipo de dispersión (tipo_disp): indica {', '.join(DISPERSION_TYPES)} "
                         "tal como lo publica el artículo; no se supone SD por defecto.")
    kind = str(kind).strip()
    if kind not in DISPERSION_TYPES:
        raise ValueError(f"{prefix}Tipo de dispersión desconocido (tipo_disp): {kind}. "
                         f"Opciones: {', '.join(DISPERSION_TYPES)}")
    missing = [name for name, value in (("media_trt", m_t), ("media_ctrl", m_c), ("n_trt", n_t), ("n_ctrl", n_c))
               if _is_missing(value)]
    common = kind in ("SEM_combinado", "SED")
    if common:
        if _is_missing(disp_t) and _is_missing(disp_c):
            missing.append(f"disp_trt o disp_ctrl (dispersión común, tipo_disp={kind})")
    else:
        missing += [name for name, value in (("disp_trt", disp_t), ("disp_ctrl", disp_c)) if _is_missing(value)]
    if missing:
        raise ValueError(f"{prefix}Faltan datos para calcular el efecto {measure} (tipo_disp={kind}): "
                         + ", ".join(missing))
    m_t = _as_number(m_t, "media_trt", prefix)
    m_c = _as_number(m_c, "media_ctrl", prefix)
    n_t = _as_number(n_t, "n_trt", prefix)
    n_c = _as_number(n_c, "n_ctrl", prefix)
    for name, n in (("n_trt", n_t), ("n_ctrl", n_c)):
        if n <= 0:
            raise ValueError(f"{prefix}{name} debe ser > 0: {n}")
    if common:
        values = [_as_number(d, name, prefix) for name, d in (("disp_trt", disp_t), ("disp_ctrl", disp_c))
                  if not _is_missing(d)]
        if len(values) == 2 and not math.isclose(values[0], values[1], rel_tol=1e-9, abs_tol=0.0):
            raise ValueError(f"{prefix}Con tipo_disp={kind} la dispersión es común a ambos grupos, pero disp_trt "
                             f"({values[0]}) y disp_ctrl ({values[1]}) no coinciden: revisa la extracción.")
        shared = values[0]
        if kind == "SED":
            sd_t = sd_c = sd_from_dispersion(shared, n_t, "SED", n_c)
        else:
            sd_t = sd_c = sd_from_dispersion(shared, (n_t + n_c) / 2, "SEM_combinado")
    else:
        sd_t = sd_from_dispersion(_as_number(disp_t, "disp_trt", prefix), n_t, kind)
        sd_c = sd_from_dispersion(_as_number(disp_c, "disp_ctrl", prefix), n_c, kind)
    return MEASURES[measure](m_t, m_c, sd_t, sd_c, n_t, n_c)


# --------------------------------------------------------------------------- variables binarias
def _validate_counts(ev_t, n_t, ev_c, n_c) -> tuple[int, int, int, int]:
    """Comprueba que los recuentos sean enteros >= 0, n > 0 y eventos <= n."""
    fields = (("ev_t", "eventos en el grupo tratado", ev_t), ("n_t", "total del grupo tratado", n_t),
              ("ev_c", "eventos en el grupo control", ev_c), ("n_c", "total del grupo control", n_c))
    counts = []
    for name, description, value in fields:
        if _is_missing(value) or isinstance(value, bool):
            raise ValueError(f"Falta {name} ({description}) o no es un recuento: {value!r}")
        number = _as_number(value, f"{name} ({description})", "")
        if number < 0:
            raise ValueError(f"{name} ({description}) no puede ser negativo: {value!r}")
        if abs(number - round(number)) > 1e-9:
            raise ValueError(f"{name} ({description}) debe ser un número entero de animales o unidades "
                             f"experimentales: {value!r}. Si viene de un porcentaje, reconstruye el recuento.")
        counts.append(int(round(number)))
    a, n1, c, n2 = counts
    if n1 == 0 or n2 == 0:
        raise ValueError("n_t y n_c (totales por grupo) deben ser > 0")
    if a > n1:
        raise ValueError(f"ev_t ({a}) no puede superar n_t ({n1})")
    if c > n2:
        raise ValueError(f"ev_c ({c}) no puede superar n_c ({n2})")
    return a, n1, c, n2


def _cells_2x2(ev_t, n_t, ev_c, n_c) -> tuple[float, float, float, float]:
    """Celdas (a, b, c, d) = (eventos y no eventos del tratado, eventos y no eventos del control),
    con corrección de continuidad 0,5 en las cuatro si alguna es cero."""
    a, n1, c, n2 = _validate_counts(ev_t, n_t, ev_c, n_c)
    b, d = n1 - a, n2 - c
    if 0 in (a, b, c, d):
        h = CONTINUITY_CORRECTION
        return a + h, b + h, c + h, d + h
    return a, b, c, d


def log_risk_ratio(ev_t, n_t, ev_c, n_c) -> tuple[float, float]:
    """Log del riesgo relativo (tratado frente a control) y su varianza: 1/a - 1/n1 + 1/c - 1/n2.

    Si alguna celda de la tabla 2×2 es cero se suma 0,5 a las cuatro. Los estudios sin eventos
    en ningún grupo (o con evento en todos los animales) aportan muy poca información: decide de
    antemano si se excluyen y regístralo.
    """
    a, b, c, d = _cells_2x2(ev_t, n_t, ev_c, n_c)
    n1, n2 = a + b, c + d
    return math.log((a / n1) / (c / n2)), 1 / a - 1 / n1 + 1 / c - 1 / n2


def log_odds_ratio(ev_t, n_t, ev_c, n_c) -> tuple[float, float]:
    """Log de la odds ratio (tratado frente a control) y su varianza: 1/a + 1/b + 1/c + 1/d.

    Si alguna celda de la tabla 2×2 es cero se suma 0,5 a las cuatro.
    """
    a, b, c, d = _cells_2x2(ev_t, n_t, ev_c, n_c)
    return math.log((a * d) / (b * c)), 1 / a + 1 / b + 1 / c + 1 / d


def risk_difference(ev_t, n_t, ev_c, n_c) -> tuple[float, float]:
    """Diferencia de riesgos p_t - p_c y su varianza p_t(1 - p_t)/n_t + p_c(1 - p_c)/n_c (sin corrección).

    Lanza ValueError si la varianza es nula (ambos grupos con 0 % o 100 % de eventos), porque ese
    estudio recibiría un peso infinito.
    """
    a, n1, c, n2 = _validate_counts(ev_t, n_t, ev_c, n_c)
    p1, p2 = a / n1, c / n2
    var = p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2
    if var <= 0:
        raise ValueError("Diferencia de riesgos con varianza nula (ambos grupos con 0 % o 100 % de eventos): "
                         "la fórmula habitual no permite ponderar este estudio; usa RR u OR con corrección "
                         "de continuidad o decide su exclusión y regístrala.")
    return p1 - p2, var


BINARY_MEASURES = {"RR": log_risk_ratio, "OR": log_odds_ratio, "RD": risk_difference}


def effect_from_counts(measure: str, ev_t, n_t, ev_c, n_c, *, label: str | None = None) -> tuple[float, float]:
    """(yi, vi) desde recuentos: measure en {"RR", "OR", "RD"}. RR y OR se dan en escala logarítmica
    (yi = log RR o log OR); exponencia la estimación final y su IC para informar.
    `label` (p. ej. el estudio) se antepone a los mensajes de error."""
    prefix = f"{label}: " if label not in (None, "") else ""
    key = str(measure).strip().upper() if measure is not None else ""
    if key not in BINARY_MEASURES:
        raise ValueError(f"{prefix}Medida binaria desconocida: {measure}. Opciones: {', '.join(BINARY_MEASURES)}")
    try:
        return BINARY_MEASURES[key](ev_t, n_t, ev_c, n_c)
    except ValueError as exc:
        if not prefix:
            raise
        raise ValueError(f"{prefix}{exc}") from None
