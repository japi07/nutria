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

Diseños cruzados (crossover y cuadrado latino): cada animal recibe el tratamiento y el control,
así que las dos medias no son independientes. `effect_from_summary(..., diseno=...)` usa entonces
las fórmulas de grupos emparejados de Borenstein, Hedges, Higgins y Rothstein (2009, Introduction
to Meta-Analysis, cap. 4), con n = número de animales y r = correlación intrasujeto (entre las dos
observaciones del mismo animal). La SMD de un diseño cruzado no es calculable sin r (ni la
estimación, si la dispersión es residual, ni su varianza): se exige. Tratar un cruzado como
paralelo sobrestima la varianza de la diferencia de medias si r > 0 (conservador; Elbourne et al.
2002, Int J Epidemiol 31:140-149), pero da una SMD en otra escala (DE intraanimal) que no se puede
combinar con la de los diseños paralelos. `design_note` describe el tratamiento aplicado a cada fila.
"""

from __future__ import annotations

import math
import re
import unicodedata

from scipy import stats

DISPERSION_TYPES = ("SD", "SE", "SEM_combinado", "SED", "IC95")
#: Dispersiones que, en un diseño cruzado, proceden de la varianza residual (intraanimal) del modelo.
RESIDUAL_DISPERSIONS = ("SEM_combinado", "SED")

#: Diseños de la extracción (esquema extraccion.schema.json) y los que miden ambos tratamientos en el mismo animal.
DESIGNS = ("paralelo", "crossover", "cuadrado_latino", "factorial", "bloques", "otro")
PAIRED_DESIGNS = ("crossover", "cuadrado_latino")
_DESIGN_ALIASES = {"parallel": "paralelo", "cross_over": "crossover", "cruzado": "crossover",
                   "diseno_cruzado": "crossover", "latin_square": "cuadrado_latino",
                   "cuadrado_latino_replicado": "cuadrado_latino", "factorial_paralelo": "factorial",
                   "bloques_completos": "bloques", "blocks": "bloques", "other": "otro"}
#: Unidades experimentales de la extracción.
UNITS = ("animal", "corral", "jaula", "tanque", "granja", "otro")
_UNIT_ALIASES = {"pen": "corral", "cage": "jaula", "tank": "tanque", "farm": "granja", "rebano": "granja",
                 "herd": "granja", "vaca": "animal", "cow": "animal", "other": "otro"}

#: Corrección de continuidad que se suma a las cuatro celdas de la tabla 2×2 si alguna es cero (RR y OR).
CONTINUITY_CORRECTION = 0.5


def _token(value) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[\s\-]+", "_", text.strip().lower())


def normalize_design(value) -> str | None:
    """Diseño normalizado (paralelo, crossover, cuadrado_latino, factorial, bloques u otro) o None si falta.

    Acepta mayúsculas, acentos, espacios y algunos sinónimos ("cuadrado latino", "latin square",
    "cross-over", "cruzado"). Un valor desconocido lanza ValueError: el diseño cambia el cálculo del
    efecto, así que no se adivina."""
    if _is_missing(value):
        return None
    key = _token(value)
    key = _DESIGN_ALIASES.get(key, key)
    if key not in DESIGNS:
        raise ValueError(f"Diseño desconocido (diseno): {value!r}. Opciones: {', '.join(DESIGNS)}")
    return key


def normalize_unit(value) -> str | None:
    """Unidad experimental normalizada (animal, corral, jaula, tanque, granja, otro), el texto original
    en minúsculas si no se reconoce, o None si falta."""
    if _is_missing(value):
        return None
    key = _token(value)
    return _UNIT_ALIASES.get(key, key)


def _check_rho(rho, prefix: str = "") -> float | None:
    if _is_missing(rho):
        return None
    try:
        value = float(rho)
    except (TypeError, ValueError):
        raise ValueError(f"{prefix}La correlación intrasujeto (rho_intrasujeto) no es numérica: {rho!r}") from None
    if not (math.isfinite(value) and 0.0 <= value < 1.0):
        raise ValueError(f"{prefix}La correlación intrasujeto (rho_intrasujeto) debe estar en [0, 1): {rho!r}")
    return value


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
    """Diferencia de medias estandarizada con corrección de sesgo para muestras pequeñas (g de Hedges),
    grupos independientes (diseño paralelo), según Borenstein et al. (2009, cap. 4):

    d = (m_t - m_c)/S_p, con S_p la DE combinada; V_d = (n_t + n_c)/(n_t·n_c) + d²/(2·(n_t + n_c));
    J = 1 - 3/(4·gl - 1), gl = n_t + n_c - 2; g = J·d; V_g = J²·V_d.

    La corrección J se aplica a la estimación y a su varianza, igual que en `paired_hedges_g`, para que
    las filas de diseños paralelos y cruzados usen la misma convención. (metafor::escalc usa por defecto
    otra aproximación de muestras grandes, 1/n_t + 1/n_c + g²/(2·(n_t + n_c)), algo mayor; el script de
    `nutria meta exportar-r` usa los vi de nutria, así que no hay discrepancia.)
    """
    df = n_t + n_c - 2
    if df <= 0:
        raise ValueError("Se necesitan al menos 3 observaciones en total")
    sp = math.sqrt(((n_t - 1) * sd_t ** 2 + (n_c - 1) * sd_c ** 2) / df)
    if sp == 0:
        raise ValueError("DE combinada nula")
    d = (m_t - m_c) / sp
    j = 1 - 3 / (4 * df - 1)
    v_d = (n_t + n_c) / (n_t * n_c) + d ** 2 / (2 * (n_t + n_c))
    return j * d, j ** 2 * v_d


def paired_mean_difference(m_t: float, m_c: float, sd_t: float, sd_c: float, n: float,
                           r: float | None) -> tuple[float, float]:
    """Diferencia de medias de grupos emparejados (Borenstein et al. 2009, cap. 4): n animales que reciben
    ambos tratamientos, DE por tratamiento sd_t y sd_c (entre animales) y correlación intrasujeto r.

    vi = S_dif²/n, con S_dif² = sd_t² + sd_c² - 2·r·sd_t·sd_c. Si r es None se usa r = 0, que trata los
    grupos como independientes y sobrestima la varianza cuando r > 0 (conservador; Elbourne et al. 2002).
    """
    rho = 0.0 if r is None else r
    var = (sd_t ** 2 + sd_c ** 2 - 2 * rho * sd_t * sd_c) / n
    if not var > 0:
        raise ValueError("Varianza no positiva en la diferencia de medias emparejada: revisa las DE y r")
    return m_t - m_c, var


def paired_hedges_g(m_t: float, m_c: float, s_within: float, n: float, r: float) -> tuple[float, float]:
    """SMD de grupos emparejados (Borenstein et al. 2009, cap. 4): d = (m_t - m_c)/S_within, con S_within
    la DE entre animales (la misma escala que la SMD de un diseño paralelo);
    V_d = (1/n + d²/(2n))·2·(1 - r); J = 1 - 3/(4·gl - 1), gl = n - 1; g = J·d; V_g = J²·V_d.
    n es el número de animales (pares) y r la correlación intrasujeto."""
    if n < 3:
        raise ValueError("La SMD de un diseño cruzado necesita al menos 3 animales")
    if not s_within > 0:
        raise ValueError("DE entre animales nula")
    d = (m_t - m_c) / s_within
    v_d = (1.0 / n + d ** 2 / (2.0 * n)) * 2.0 * (1.0 - r)
    j = 1 - 3 / (4 * (n - 1) - 1)
    return j * d, j ** 2 * v_d


def paired_log_ratio_of_means(m_t: float, m_c: float, sd_t: float, sd_c: float, n: float,
                              r: float | None) -> tuple[float, float]:
    """Log de la razón de medias de grupos emparejados (método delta; Lajeunesse 2011, Ecology 92:2049-2055):
    vi = sd_t²/(n·m_t²) + sd_c²/(n·m_c²) - 2·r·sd_t·sd_c/(n·m_t·m_c). Con r None se usa r = 0
    (grupos independientes; conservador si r > 0)."""
    if m_t <= 0 or m_c <= 0:
        raise ValueError("El cociente de medias requiere medias positivas")
    rho = 0.0 if r is None else r
    var = (sd_t ** 2 / (n * m_t ** 2) + sd_c ** 2 / (n * m_c ** 2)
           - 2 * rho * sd_t * sd_c / (n * m_t * m_c))
    if not var > 0:
        raise ValueError("Varianza no positiva en el cociente de medias emparejado: revisa las DE y r")
    return math.log(m_t / m_c), var


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
                        *, label: str | None = None, diseno: str | None = None,
                        rho_intrasujeto: float | None = None) -> tuple[float, float]:
    """Calcula (yi, vi) a partir de medias, n y dispersión del tipo indicado.

    `kind` (columna `tipo_disp`) es obligatorio: SD, SE, SEM_combinado, SED o IC95, tal como lo
    publica el artículo; no hay valor por defecto, porque tratar un SEM como DE subestima la
    varianza. Si falta cualquier media (media_trt, media_ctrl), n (n_trt, n_ctrl) o dispersión
    (disp_trt, disp_ctrl) se lanza ValueError nombrando el campo. Si solo se dispone de un SEM
    combinado o un SED, pásalo como `disp_t` (o `disp_c`); si se dan ambos, deben coincidir.
    `label` (p. ej. el estudio) se antepone a los mensajes de error.

    `diseno` (columna `diseno`; None = paralelo): con "crossover" o "cuadrado_latino" se aplican las
    fórmulas de grupos emparejados (ver `_paired_effect`), con n = número de animales (el menor de
    n_trt y n_ctrl si difieren) y `rho_intrasujeto` = correlación intrasujeto r en [0, 1). La SMD de
    un diseño cruzado exige r (ValueError si falta); MD y ROM lo usan si se da y, si no, siguen siendo
    calculables (ver `design_note`). En los demás diseños, `rho_intrasujeto` no se usa.
    """
    prefix = f"{label}: " if label not in (None, "") else ""
    try:
        design = normalize_design(diseno)
    except ValueError as exc:
        raise ValueError(f"{prefix}{exc}") from None
    rho = _check_rho(rho_intrasujeto, prefix)
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
    shared = None
    if common:
        values = [_as_number(d, name, prefix) for name, d in (("disp_trt", disp_t), ("disp_ctrl", disp_c))
                  if not _is_missing(d)]
        if len(values) == 2 and not math.isclose(values[0], values[1], rel_tol=1e-9, abs_tol=0.0):
            raise ValueError(f"{prefix}Con tipo_disp={kind} la dispersión es común a ambos grupos, pero disp_trt "
                             f"({values[0]}) y disp_ctrl ({values[1]}) no coinciden: revisa la extracción.")
        shared = values[0]
    if design in PAIRED_DESIGNS:
        try:
            return _paired_effect(measure, m_t, m_c, min(n_t, n_c), kind, shared,
                                  None if common else _as_number(disp_t, "disp_trt", prefix),
                                  None if common else _as_number(disp_c, "disp_ctrl", prefix), rho, design)
        except ValueError as exc:
            raise ValueError(f"{prefix}{exc}") from None
    if common:
        if kind == "SED":
            sd_t = sd_c = sd_from_dispersion(shared, n_t, "SED", n_c)
        else:
            sd_t = sd_c = sd_from_dispersion(shared, (n_t + n_c) / 2, "SEM_combinado")
    else:
        sd_t = sd_from_dispersion(_as_number(disp_t, "disp_trt", prefix), n_t, kind)
        sd_c = sd_from_dispersion(_as_number(disp_c, "disp_ctrl", prefix), n_c, kind)
    return MEASURES[measure](m_t, m_c, sd_t, sd_c, n_t, n_c)


def _paired_effect(measure: str, m_t: float, m_c: float, n: float, kind: str, shared: float | None,
                   disp_t: float | None, disp_c: float | None, r: float | None, design: str) -> tuple[float, float]:
    """(yi, vi) de un diseño cruzado (crossover o cuadrado latino) con n animales que reciben ambos tratamientos.

    Dispersión residual (SEM_combinado o SED): se interpreta como procedente de la varianza residual
    (intraanimal) del modelo con el animal como efecto, la que se publica en crossovers y cuadrados
    latinos: DE intraanimal s_w = SEM·√n (SEM_combinado) o SED·√(n/2) (SED), y la DE de la diferencia
    dentro de animal es S_dif = √2·s_w.
      - MD: vi = S_dif²/n = 2·s_w²/n (= 2·SEM² o SED²); no necesita r.
      - SMD: S_within = S_dif/√(2(1 - r)) = s_w/√(1 - r) (Borenstein et al. 2009, cap. 4); necesita r.
      - ROM: vi = s_w²/n·(1/m_t² + 1/m_c²) + [r/(1 - r)]·s_w²/n·(1/m_t - 1/m_c)² (método delta con la
        varianza entre animales r/(1 - r)·s_w²; Lajeunesse 2011). Sin r se omite el segundo término, que
        es de segundo orden (despreciable salvo con efectos relativos grandes).
    Dispersión por grupo (SD, SE o IC95: DE entre animales de cada tratamiento):
      - MD y ROM: fórmulas emparejadas con r (`paired_mean_difference`, `paired_log_ratio_of_means`); sin r,
        grupos independientes (conservador si r > 0; Elbourne et al. 2002).
      - SMD: S_within = DE combinada √((sd_t² + sd_c²)/2); la varianza necesita r.
    """
    label = "cuadrado latino" if design == "cuadrado_latino" else "crossover"
    if measure == "SMD" and r is None:
        raise ValueError(f"La SMD de un diseño cruzado ({label}) necesita la correlación intrasujeto r "
                         "(Borenstein et al. 2009, cap. 4; Elbourne et al. 2002): sin ella ni la escala ni la "
                         "varianza son comparables con las de un diseño paralelo. Indícala con --rho-intrasujeto "
                         "o en la columna rho_intrasujeto (de la publicación o como supuesto [SUP] con análisis "
                         "de sensibilidad), usa --medida MD o analiza este diseño por separado.")
    if kind in RESIDUAL_DISPERSIONS:
        s_w = shared * math.sqrt(n) if kind == "SEM_combinado" else shared * math.sqrt(n / 2.0)
        if not s_w > 0:
            raise ValueError("Dispersión residual nula en un diseño cruzado")
        if measure == "MD":
            return m_t - m_c, 2.0 * s_w ** 2 / n
        if measure == "SMD":
            return paired_hedges_g(m_t, m_c, s_w / math.sqrt(1.0 - r), n, r)
        if m_t <= 0 or m_c <= 0:
            raise ValueError("El cociente de medias requiere medias positivas")
        var = s_w ** 2 / n * (1.0 / m_t ** 2 + 1.0 / m_c ** 2)
        if r is not None:
            var += r / (1.0 - r) * s_w ** 2 / n * (1.0 / m_t - 1.0 / m_c) ** 2
        return math.log(m_t / m_c), var
    sd_t = sd_from_dispersion(disp_t, n, kind)
    sd_c = sd_from_dispersion(disp_c, n, kind)
    if measure == "MD":
        return paired_mean_difference(m_t, m_c, sd_t, sd_c, n, r)
    if measure == "SMD":
        return paired_hedges_g(m_t, m_c, math.sqrt((sd_t ** 2 + sd_c ** 2) / 2.0), n, r)
    return paired_log_ratio_of_means(m_t, m_c, sd_t, sd_c, n, r)


def design_note(measure: str, kind: str | None, diseno: str | None,
                rho_intrasujeto: float | None = None) -> tuple[str, str] | None:
    """(nivel, texto) que explica cómo se trató una fila de diseño cruzado; None para los demás diseños.

    nivel es "aviso" cuando el resultado depende de algo que nutria no puede comprobar o se aparta de la
    fórmula emparejada, y "nota" cuando se aplicó la fórmula estándar. Pensado para agrupar las filas con
    el mismo tratamiento en un único mensaje. `kind` None o vacío indica yi y vi precalculados."""
    design = normalize_design(diseno)
    if design not in PAIRED_DESIGNS:
        return None
    if _is_missing(kind):
        return ("aviso", "Diseño cruzado con yi y vi precalculados: se usan tal cual; comprueba que se calcularon "
                         "con las fórmulas de grupos emparejados (correlación intrasujeto).")
    kind = str(kind).strip()
    rho = _check_rho(rho_intrasujeto)
    base = "Diseño cruzado (crossover o cuadrado latino), n = animales con ambos tratamientos"
    if measure == "SMD":
        return ("nota", f"{base}: SMD de grupos emparejados con r = {rho:g} (Borenstein et al. 2009, cap. 4), "
                        "estandarizada por la DE entre animales para que sea comparable con la de diseños "
                        "paralelos.")
    if kind in RESIDUAL_DISPERSIONS:
        text = (f"{base}: el {kind} se interpreta como residual (intraanimal), del modelo con el animal como "
                "efecto; vi = 2·SEM² o SED², la varianza de la diferencia dentro de animal. Si el artículo "
                "publica un SEM que incluye la varianza entre animales, vi queda sobrestimada (conservador).")
        if measure == "ROM" and rho is None:
            text += (" ROM sin r: se omite el término de la varianza entre animales (de segundo orden); "
                     "indica --rho-intrasujeto para incluirlo.")
        return ("nota", text)
    if rho is None:
        return ("aviso", f"{base}, DE por tratamiento y sin correlación intrasujeto: vi se calcula como si los "
                         "grupos fueran independientes, lo que la sobrestima si r > 0 (conservador; Elbourne et al. "
                         "2002). Indica --rho-intrasujeto (o la columna rho_intrasujeto) para la fórmula de grupos "
                         "emparejados.")
    return ("nota", f"{base}: fórmula de grupos emparejados con r = {rho:g} (Borenstein et al. 2009, cap. 4).")


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
