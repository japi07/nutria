"""Meta-regresión de efectos mixtos (REML o DL) con prueba de Knapp-Hartung opcional
(estándar o modificada con max(1, s²)).

Útil para modelar respuesta a dosis, efecto de la dieta basal, etapa productiva o diseño
experimental como moderadores. Recuerda: la meta-regresión es observacional entre estudios
(sesgo ecológico, confusión) y necesita, como orientación, unos 10 estudios por moderador.
La matriz de diseño (intercepto + moderadores) debe ser de rango completo: los moderadores
constantes o colineales se rechazan con un ValueError que los nombra.

Moderadores categóricos (p. ej. diseno o unidad_experimental): `codificar_moderadores` construye
la matriz a partir de las filas de un CSV con codificación ficticia (una variable indicadora por
categoría salvo la de referencia, que se declara en el resultado). Los errores de los datos
(columna inexistente, celdas vacías, números mezclados con texto, referencia inexistente) lanzan
ValueError; un moderador categórico con una sola categoría lanza `ModeradorNoEstimable` (subclase
de ValueError), que la CLI informa como meta-regresión no estimable, igual que los colineales.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy import stats

from .meta import _Q_ZERO_TOL, _arrays, _resolve_ajuste, _typical_within_variance, tau2_dl, tau2_reml

# Tolerancia relativa (sobre columnas normalizadas) para decidir que una columna es redundante.
_RANK_RTOL = 1e-10


class ModeradorNoEstimable(ValueError):
    """El moderador no permite estimar ningún contraste (p. ej. una sola categoría): no es un error de los
    datos, sino una meta-regresión no estimable con ellos."""


def _celda_vacia(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and value.strip() == ""


def _numero(value) -> float | None:
    """Número finito o None si la celda no es numérica."""
    if isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


#: Categoría de referencia por defecto de los moderadores de diseño (si aparece en los datos).
REFERENCIAS_POR_DEFECTO = {"diseno": "paralelo", "unidad_experimental": "animal"}


def categoria_moderador(columna: str, value) -> str:
    """Categoría normalizada: diseno y unidad_experimental con sus normalizaciones; el resto, sin espacios."""
    if columna == "diseno":
        from .effect_sizes import normalize_design
        return normalize_design(value)
    if columna == "unidad_experimental":
        from .effect_sizes import normalize_unit
        return normalize_unit(value)
    return str(value).strip()


def categorias_de_columna(columna: str, valores: list) -> list[str]:
    """Categorías normalizadas de una columna (moderador categórico o subgrupo).

    Lanza ValueError si dos valores distintos solo difieren en mayúsculas, acentos o espacios (p. ej.
    "Alta" y "alta"): casi siempre es una errata que partiría una categoría en dos."""
    from .effect_sizes import _token
    categorias = [categoria_moderador(columna, v) for v in valores]
    vistos: dict[str, str] = {}
    for c in categorias:
        clave = _token(c)
        if clave in vistos and vistos[clave] != c:
            raise ValueError(f"La columna '{columna}' tiene categorías que solo difieren en mayúsculas, acentos o "
                             f"espacios ('{vistos[clave]}' y '{c}'): unifícalas en el CSV.")
        vistos.setdefault(clave, c)
    return categorias


def codificar_moderadores(rows: list[dict], columnas: list[str],
                          referencias: dict[str, str] | None = None) -> tuple[np.ndarray, list[str], dict]:
    """Matriz de moderadores (k × m, sin intercepto), nombres y descripción a partir de filas de un CSV.

    - Moderador numérico: todas sus celdas son números finitos; una columna.
    - Moderador categórico: ninguna celda es numérica, o la columna figura en `referencias` (útil para
      categorías con código numérico). Codificación ficticia: una variable indicadora `col[categoria]`
      por categoría distinta de la de referencia; cada coeficiente es la diferencia frente a la referencia.
      La referencia es la indicada en `referencias`; si no, "paralelo" en `diseno` y "animal" en
      `unidad_experimental` (si aparecen) y, en los demás casos, la primera categoría en orden alfabético
      (como factor() en R). Se declara en la descripción.

    Una celda vacía, una columna inexistente, una columna que mezcla números y texto (p. ej. una dosis con
    "n.d.": no se convierte en categórica sin decirlo; decláralo con `referencias` si lo es), categorías que
    solo difieren en mayúsculas o acentos, una referencia que no existe o un moderador categórico con una
    sola categoría lanzan ValueError con el nombre de la columna (y las filas, base 1).
    Devuelve (matriz, nombres, descripcion) con descripcion = {col: {"tipo": "numerico"} o
    {"tipo": "categorico", "referencia": ..., "categorias": [...], "indicadoras": [...]}}.
    """
    referencias = dict(referencias or {})
    columnas = list(columnas or [])
    if len(set(columnas)) != len(columnas):
        raise ValueError("Hay moderadores repetidos")
    sobrantes = [c for c in referencias if c not in columnas]
    if sobrantes:
        raise ValueError(f"--referencia nombra columnas que no son moderadores: {', '.join(sobrantes)}")
    presentes = set().union(*(r.keys() for r in rows)) if rows else set()
    bloques, nombres, descripcion = [], [], {}
    for col in columnas:
        if col not in presentes:
            raise ValueError(f"El moderador '{col}' no es una columna del CSV")
        valores = [r.get(col) for r in rows]
        vacias = [i + 1 for i, v in enumerate(valores) if _celda_vacia(v)]
        if vacias:
            raise ValueError(f"El moderador '{col}' tiene celdas vacías en la(s) fila(s) "
                             + ", ".join(map(str, vacias[:10])) + ("…" if len(vacias) > 10 else ""))
        numeros = [_numero(v) for v in valores]
        if col not in referencias and all(n is not None for n in numeros):
            bloques.append(np.asarray(numeros, dtype=float)[:, None])
            nombres.append(col)
            descripcion[col] = {"tipo": "numerico"}
            continue
        texto = [i + 1 for i, n in enumerate(numeros) if n is None]
        if col not in referencias and len(texto) < len(numeros):
            raise ValueError(f"El moderador '{col}' mezcla números y texto (texto en la(s) fila(s) "
                             + ", ".join(map(str, texto[:10])) + ("…" if len(texto) > 10 else "")
                             + "): corrige los datos (¿dosis no disponible, coma decimal sin --decimal?) o, si es "
                               f"categórico, decláralo con --referencia {col}=<categoría de referencia>.")
        categorias_fila = categorias_de_columna(col, valores)
        categorias = sorted(set(categorias_fila))
        if col in referencias:
            referencia = categoria_moderador(col, referencias[col])
            if referencia not in categorias:
                raise ValueError(f"La categoría de referencia '{referencias[col]}' no aparece en el moderador "
                                 f"'{col}' (categorías: {', '.join(categorias)})")
        elif REFERENCIAS_POR_DEFECTO.get(col) in categorias:
            referencia = REFERENCIAS_POR_DEFECTO[col]
        else:
            referencia = categorias[0]
        if len(categorias) < 2:
            raise ModeradorNoEstimable(f"El moderador categórico '{col}' solo tiene una categoría ({categorias[0]}): "
                                       "no hay contraste que estimar")
        otras = [c for c in categorias if c != referencia]
        indicadoras = [f"{col}[{c}]" for c in otras]
        bloques.append(np.column_stack([[1.0 if x == c else 0.0 for x in categorias_fila] for c in otras]))
        nombres += indicadoras
        descripcion[col] = {"tipo": "categorico", "referencia": referencia, "categorias": categorias,
                            "indicadoras": indicadoras}
    if not bloques:
        raise ValueError("Indica al menos un moderador")
    return np.column_stack(bloques), nombres, descripcion


@dataclass
class MetaRegResult:
    method: str
    test: str
    k: int
    names: list[str]
    coef: list[float]
    se: list[float]
    statistic: list[float]
    p_values: list[float]
    ci_low: list[float]
    ci_high: list[float]
    tau2: float
    I2_res: float
    R2: float | None
    QM: float
    QM_df: int
    QM_p: float
    QE: float
    QE_df: int
    QE_p: float
    level: float = 0.95
    notes: list[str] = field(default_factory=list)
    ajuste: str = "ninguno"
    kh_s2: float | None = None
    # Con ajuste "hksj" y s² < 1: p e IC de Knapp-Hartung modificado (max(1, s²)), como sensibilidad.
    p_values_mod: list[float] | None = None
    ci_low_mod: list[float] | None = None
    ci_high_mod: list[float] | None = None
    avisos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        pct = int(round(self.level * 100))
        lines = [f"AVISO: {a}" for a in self.avisos]
        lines += [f"Meta-regresión ({self.method}, prueba {self.test}), k = {self.k}",
                 f"tau² residual = {self.tau2:.4f}; I² residual = {self.I2_res:.1f} %"
                 + (f"; R² = {self.R2:.1f} %" if self.R2 is not None else ""),
                 f"QE({self.QE_df}) = {self.QE:.3f}, p = {self.QE_p:.4g} | QM({self.QM_df}) = {self.QM:.3f}, "
                 f"p = {self.QM_p:.4g}",
                 f"{'coeficiente':<22}{'estim.':>10}{'EE':>10}{'estad.':>9}{'p':>10}   IC{pct}"]
        for i, name in enumerate(self.names):
            lines.append(f"{name:<22}{self.coef[i]:>10.4f}{self.se[i]:>10.4f}{self.statistic[i]:>9.3f}"
                         f"{self.p_values[i]:>10.4g}   [{self.ci_low[i]:.4f}; {self.ci_high[i]:.4f}]")
        if self.p_values_mod is not None:
            lines.append(f"Knapp-Hartung modificado (max(1, s²), s² = {self.kh_s2:.4g}):")
            for i, name in enumerate(self.names):
                lines.append(f"{name:<22}{'':>29}{self.p_values_mod[i]:>10.4g}   "
                             f"[{self.ci_low_mod[i]:.4f}; {self.ci_high_mod[i]:.4f}]")
        lines += [f"Nota: {n}" for n in self.notes]
        return "\n".join(lines)


def _numeric_rank(A: np.ndarray, rtol: float = _RANK_RTOL) -> int:
    s = np.linalg.svd(A, compute_uv=False)
    if s.size == 0 or s[0] <= 0:
        return 0
    return int(np.sum(s > rtol * s[0]))


def _redundant_columns(X: np.ndarray, rtol: float = _RANK_RTOL) -> list[int]:
    """Índices de las columnas de X que no aumentan el rango: constantes (si hay intercepto),
    nulas o combinación lineal de las columnas anteriores.

    Las columnas se normalizan antes de calcular el rango numérico, para que la decisión no dependa
    de las unidades de cada moderador (g frente a mg, kg/d frente a g/d).
    """
    X = np.asarray(X, dtype=float)
    norms = np.linalg.norm(X, axis=0)
    Xn = X / np.where(norms > 0, norms, 1.0)
    redundant: list[int] = []
    previous = 0
    for j in range(X.shape[1]):
        current = _numeric_rank(Xn[:, : j + 1], rtol)
        if current <= previous:
            redundant.append(j)
        previous = current
    return redundant


def meta_regression(yi, vi, moderators: np.ndarray | list, names: list[str] | None = None,
                    method: str = "REML", knha: bool = False, level: float = 0.95,
                    *, ajuste: str | None = None) -> MetaRegResult:
    """`moderators` es la matriz k×m SIN intercepto (se añade automáticamente).

    Inferencia sobre los coeficientes:
    - ajuste="ninguno" (por defecto si knha=False): pruebas z.
    - ajuste="hksj" (equivale a knha=True): Knapp-Hartung, covarianza escalada por
      s² = Σw·e²/(k - p) y t con k - p gl; si s² es nulo se usa max(1, s²) para no dar EE nulos.
    - ajuste="hksj-mod": Knapp-Hartung modificado, siempre con max(1, s²).

    Lanza ValueError("Moderadores constantes o colineales: ...") si la matriz de diseño
    (intercepto + moderadores) no es de rango completo.
    """
    ajuste = _resolve_ajuste("ninguno" if ajuste is None else ajuste, knha)
    y, v = _arrays(yi, vi)
    k = y.size
    M = np.asarray(moderators, dtype=float)
    if M.ndim == 1:
        M = M[:, None]
    if M.ndim != 2 or M.shape[0] != k:
        raise ValueError("La matriz de moderadores debe tener k filas")
    if not np.all(np.isfinite(M)):
        raise ValueError("Los moderadores deben ser numéricos y finitos (hay valores ausentes o no numéricos)")
    if names is not None and len(names) != M.shape[1]:
        raise ValueError(f"Se dieron {len(names)} nombre(s) para {M.shape[1]} moderador(es)")
    X = np.column_stack([np.ones(k), M])
    p = X.shape[1]
    names = ["intercepto"] + (list(names) if names is not None else [f"mod{i + 1}" for i in range(M.shape[1])])
    if k <= p:
        raise ValueError(f"Se necesitan más estudios (k = {k}) que coeficientes (p = {p})")
    redundant = _redundant_columns(X)
    if redundant:
        parts = []
        for j in redundant:
            col = X[:, j]
            constant = float(np.ptp(col)) <= _RANK_RTOL * max(1.0, float(np.max(np.abs(col))))
            parts.append(f"{names[j]} (constante)" if constant
                         else f"{names[j]} (combinación lineal del intercepto o de otros moderadores)")
        raise ValueError(
            "Moderadores constantes o colineales: " + "; ".join(parts)
            + f". La matriz de diseño (intercepto + moderadores) tiene rango {p - len(redundant)} < {p}: "
              "elimina los moderadores constantes o redundantes (con variables indicadoras de una categoría, "
              "deja una como referencia).")
    method = method.upper()
    if method == "REML":
        tau2 = tau2_reml(y, v, X)
        tau2_null = tau2_reml(y, v)
    elif method == "DL":
        tau2 = tau2_dl(y, v, X)
        tau2_null = tau2_dl(y, v)
    else:
        raise ValueError("Método no válido: usa REML o DL")

    w = 1.0 / (v + tau2)
    XtWX = X.T @ (w[:, None] * X)
    cov = np.linalg.inv(XtWX)
    beta = cov @ (X.T @ (w * y))
    resid = y - X @ beta
    alpha = 1 - level
    notes: list[str] = []
    avisos: list[str] = []
    s2: float | None = None
    p_mod = ci_low_mod = ci_high_mod = None
    if ajuste != "ninguno":
        s2 = float(np.sum(w * resid ** 2) / (k - p))
        if ajuste == "hksj-mod":
            factor = max(1.0, s2)
            test = f"t (Knapp-Hartung modificado max(1, s²), gl = {k - p})"
            notes.append(f"Knapp-Hartung modificado: factor de escala max(1, s²) = {factor:.4g} (s² = {s2:.4g}).")
        elif s2 < _Q_ZERO_TOL:
            factor = 1.0
            test = f"t (Knapp-Hartung con max(1, s²) porque s² = 0, gl = {k - p})"
            notes.append("Knapp-Hartung con s² = 0 (residuos nulos): para no dar EE nulos se usa max(1, s²) = 1.")
        else:
            factor = s2
            test = f"t (Knapp-Hartung, gl = {k - p})"
            if s2 < 1:
                se_m = np.sqrt(np.diag(cov))  # max(1, s²) = 1
                crit_m = stats.t.ppf(1 - alpha / 2, k - p)
                p_mod = list(map(float, 2 * stats.t.sf(np.abs(beta / se_m), k - p)))
                ci_low_mod = list(map(float, beta - crit_m * se_m))
                ci_high_mod = list(map(float, beta + crit_m * se_m))
                avisos.append(f"Knapp-Hartung estándar con s² = {s2:.4g} < 1: los IC pueden ser más estrechos que "
                              "los de Wald. Se dan también la p y el IC del Knapp-Hartung modificado (max(1, s²)), "
                              "que es el ajuste por defecto (--ajuste hksj-mod).")
        cov = cov * factor
        se = np.sqrt(np.diag(cov))
        stat = beta / se
        pvals = 2 * stats.t.sf(np.abs(stat), k - p)
        crit = stats.t.ppf(1 - alpha / 2, k - p)
    else:
        se = np.sqrt(np.diag(cov))
        stat = beta / se
        pvals = 2 * stats.norm.sf(np.abs(stat))
        crit = stats.norm.ppf(1 - alpha / 2)
        test = "z"

    b = beta[1:]
    cov_b = cov[1:, 1:]
    QM = float(b @ np.linalg.solve(cov_b, b))
    QM_df = p - 1
    if ajuste != "ninguno":
        QM_p = float(stats.f.sf(QM / QM_df, QM_df, k - p))
        QM = QM / QM_df
        notes.append("Con Knapp-Hartung, QM se expresa como estadístico F.")
    else:
        QM_p = float(stats.chi2.sf(QM, QM_df))

    w0 = 1.0 / v
    beta_fe = np.linalg.solve(X.T @ (w0[:, None] * X), X.T @ (w0 * y))
    QE = float(np.sum(w0 * (y - X @ beta_fe) ** 2))
    QE_df = k - p
    s2_res = _typical_within_variance(v, X)
    I2_res = 100 * tau2 / (tau2 + s2_res) if (tau2 + s2_res) > 0 else 0.0
    R2 = max(0.0, (tau2_null - tau2) / tau2_null) * 100 if tau2_null > 0 else None
    if k < 10 * (p - 1):
        notes.append(f"k = {k} para {p - 1} moderador(es): riesgo de sobreajuste y de falsos positivos.")
    notes.append("Asociación entre estudios (observacional): no implica causalidad a nivel de animal.")
    return MetaRegResult(
        method=method, test=test, k=k, names=names, coef=list(map(float, beta)), se=list(map(float, se)),
        statistic=list(map(float, stat)), p_values=list(map(float, pvals)),
        ci_low=list(map(float, beta - crit * se)), ci_high=list(map(float, beta + crit * se)),
        tau2=tau2, I2_res=I2_res, R2=R2, QM=QM, QM_df=QM_df, QM_p=QM_p, QE=QE, QE_df=QE_df,
        QE_p=float(stats.chi2.sf(QE, QE_df)), level=level, notes=notes, ajuste=ajuste, kh_s2=s2,
        p_values_mod=p_mod, ci_low_mod=ci_low_mod, ci_high_mod=ci_high_mod, avisos=avisos,
    )


def predict(result: MetaRegResult, moderator_values: list[float]) -> dict:
    """Predicción puntual del efecto medio para unos valores de moderadores (sin IC)."""
    x = np.concatenate([[1.0], np.asarray(moderator_values, dtype=float)])
    if x.size != len(result.coef):
        raise ValueError("Número de valores de moderadores incorrecto")
    return {"prediccion": float(x @ np.asarray(result.coef)), "tau": math.sqrt(result.tau2)}
