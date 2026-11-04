"""Metaanálisis de efectos fijos y aleatorios (DL, REML, Paule-Mandel), con ajuste de
Hartung-Knapp-Sidik-Jonkman (HKSJ) estándar o modificado (max(1, q)), heterogeneidad,
intervalo de predicción, prueba de Egger (con evaluabilidad del sesgo de publicación),
análisis de influencia (uno fuera), subgrupos y agregación de efectos dependientes.

Validado contra el conjunto clásico BCG (metafor::dat.bcg); ver tests/test_meta.py.

Efectos dependientes (varios tratamientos frente a un control compartido, varias variables o
momentos por estudio): `detect_dependence` avisa de los clústeres con más de una fila y
`aggregate_dependent` los resume en un efecto por clúster suponiendo una correlación común ρ.
Es la opción sencilla; con datos suficientes es preferible un modelo multinivel con varianza
robusta: `nutria meta exportar-r` genera el script de metafor (rma.mv + robust) para ese caso.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy import optimize, stats

#: Valores admitidos para el argumento `ajuste` de random_effects, leave_one_out y subgroups.
AJUSTES = ("ninguno", "hksj", "hksj-mod")

# q (factor de escala de HKSJ) por debajo de este valor se trata como nulo: efectos idénticos.
_Q_ZERO_TOL = 1e-10
# Variación relativa mínima de la precisión (1/√vi) para que la regresión de Egger esté identificada.
_EGGER_MIN_REL_SPREAD = 1e-6


@dataclass
class MetaResult:
    model: str
    method: str
    k: int
    estimate: float
    se: float
    ci_low: float
    ci_high: float
    statistic: float
    p_value: float
    test: str
    tau2: float
    Q: float
    Q_df: int
    Q_p: float
    I2: float
    H2: float
    pi_low: float | None = None
    pi_high: float | None = None
    level: float = 0.95
    weights: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ajuste: str = "ninguno"
    hksj_q: float | None = None
    ci_mod_low: float | None = None
    ci_mod_high: float | None = None

    @property
    def tau(self) -> float:
        return math.sqrt(self.tau2)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["tau"] = self.tau
        return data

    def summary(self) -> str:
        pct = int(round(self.level * 100))
        lines = [
            f"Modelo: {self.model} ({self.method}), k = {self.k}, prueba {self.test}",
            f"Estimación = {self.estimate:.4f} (EE {self.se:.4f}); IC{pct} [{self.ci_low:.4f}; {self.ci_high:.4f}]; "
            f"estadístico = {self.statistic:.3f}; p = {self.p_value:.4g}",
        ]
        if self.ci_mod_low is not None:
            lines.append(f"IC{pct} HKSJ modificado (max(1, q), q = {self.hksj_q:.4g}): "
                         f"[{self.ci_mod_low:.4f}; {self.ci_mod_high:.4f}]")
        lines.append(
            f"Heterogeneidad: tau² = {self.tau2:.4f} (tau = {self.tau:.4f}); Q({self.Q_df}) = {self.Q:.3f}, "
            f"p = {self.Q_p:.4g}; I² = {self.I2:.1f} %; H² = {self.H2:.2f}")
        if self.pi_low is not None:
            lines.append(f"Intervalo de predicción {pct} %: [{self.pi_low:.4f}; {self.pi_high:.4f}]")
        lines += [f"Nota: {n}" for n in self.notes]
        return "\n".join(lines)


def _arrays(yi, vi) -> tuple[np.ndarray, np.ndarray]:
    y = np.asarray(yi, dtype=float)
    v = np.asarray(vi, dtype=float)
    if y.ndim != 1 or y.shape != v.shape or y.size == 0:
        raise ValueError("yi y vi deben ser vectores de igual longitud (k >= 1)")
    if not (np.all(np.isfinite(y)) and np.all(np.isfinite(v))):
        raise ValueError("yi y vi deben ser finitos")
    if np.any(v <= 0):
        raise ValueError("Todas las varianzas vi deben ser > 0")
    return y, v


def _resolve_ajuste(ajuste, hksj: bool | None = None) -> str:
    """Normaliza el ajuste pedido. Acepta booleanos por compatibilidad con el antiguo `hksj`
    (también en posición: `random_effects(yi, vi, "REML", True)`)."""
    if ajuste is None or ajuste is False:
        name = "ninguno"
    elif ajuste is True:
        name = "hksj"
    elif isinstance(ajuste, str):
        name = ajuste.strip().lower().replace("_", "-")
    else:
        raise ValueError(f"Ajuste no válido: {ajuste!r}. Opciones: {', '.join(AJUSTES)}")
    if name not in AJUSTES:
        raise ValueError(f"Ajuste desconocido: {ajuste!r}. Opciones: {', '.join(AJUSTES)}")
    if hksj and name == "ninguno":
        name = "hksj"
    return name


def _q_statistic(y: np.ndarray, v: np.ndarray) -> float:
    w = 1.0 / v
    mu = np.sum(w * y) / np.sum(w)
    return float(np.sum(w * (y - mu) ** 2))


def _typical_within_variance(v: np.ndarray, X: np.ndarray | None = None) -> float:
    """Varianza intraestudio "típica" (Higgins y Thompson), generalizada a meta-regresión."""
    w = 1.0 / v
    k = v.size
    if X is None:
        denom = np.sum(w) ** 2 - np.sum(w ** 2)
        return float((k - 1) * np.sum(w) / denom) if denom > 0 else float("nan")
    W = np.diag(w)
    P = W - W @ X @ np.linalg.solve(X.T @ W @ X, X.T @ W)
    p = X.shape[1]
    return float((k - p) / np.trace(P))


# --------------------------------------------------------------------------- estimadores de tau²
def tau2_dl(yi, vi, X: np.ndarray | None = None) -> float:
    y, v = _arrays(yi, vi)
    k = y.size
    w = 1.0 / v
    if X is None:
        X = np.ones((k, 1))
    p = X.shape[1]
    if k <= p:
        return 0.0
    W = np.diag(w)
    P = W - W @ X @ np.linalg.solve(X.T @ W @ X, X.T @ W)
    Q = float(y @ P @ y)
    return max(0.0, (Q - (k - p)) / float(np.trace(P)))


def _reml_negloglik(tau2: float, y: np.ndarray, v: np.ndarray, X: np.ndarray) -> float:
    w = 1.0 / (v + tau2)
    XtWX = X.T @ (w[:, None] * X)
    beta = np.linalg.solve(XtWX, X.T @ (w * y))
    resid = y - X @ beta
    sign, logdet = np.linalg.slogdet(XtWX)
    return 0.5 * (np.sum(np.log(v + tau2)) + logdet + np.sum(w * resid ** 2))


def tau2_reml(yi, vi, X: np.ndarray | None = None) -> float:
    y, v = _arrays(yi, vi)
    if X is None:
        X = np.ones((y.size, 1))
    if y.size <= X.shape[1]:
        return 0.0
    upper = 10.0 * max(float(np.var(y)), float(np.max(v)), 1e-8) + 1.0
    res = optimize.minimize_scalar(_reml_negloglik, bounds=(0.0, upper), args=(y, v, X),
                                   method="bounded", options={"xatol": 1e-12, "maxiter": 2000})
    tau2 = float(res.x)
    if _reml_negloglik(0.0, y, v, X) <= res.fun:
        tau2 = 0.0
    # Refinamiento con iteraciones de punto fijo (convergencia a la solución de la ecuación REML)
    for _ in range(200):
        w = 1.0 / (v + tau2)
        W = np.diag(w)
        P = W - W @ X @ np.linalg.solve(X.T @ W @ X, X.T @ W)
        Py = P @ y
        num = float(Py @ Py) - float(np.trace(P))
        den = float(np.trace(P @ P))
        new = max(0.0, tau2 + num / den)
        if abs(new - tau2) < 1e-12:
            tau2 = new
            break
        tau2 = new
    return tau2


def tau2_pm(yi, vi) -> float:
    y, v = _arrays(yi, vi)
    k = y.size
    if k < 2:
        return 0.0

    def excess(t: float) -> float:
        w = 1.0 / (v + t)
        mu = np.sum(w * y) / np.sum(w)
        return float(np.sum(w * (y - mu) ** 2) - (k - 1))

    if excess(0.0) <= 0:
        return 0.0
    hi = max(float(np.var(y)), 1e-6)
    while excess(hi) > 0:
        hi *= 2.0
        if hi > 1e12:
            raise RuntimeError("Paule-Mandel no converge")
    return float(optimize.brentq(excess, 0.0, hi, xtol=1e-12))


# --------------------------------------------------------------------------- modelos
def fixed_effect(yi, vi, level: float = 0.95) -> MetaResult:
    y, v = _arrays(yi, vi)
    k = y.size
    w = 1.0 / v
    est = float(np.sum(w * y) / np.sum(w))
    se = math.sqrt(1.0 / np.sum(w))
    z = est / se
    crit = stats.norm.ppf(1 - (1 - level) / 2)
    Q = _q_statistic(y, v)
    df = k - 1
    notes = []
    if k > 1:
        notes.append("Efecto fijo: asume un único efecto verdadero; rara vez es plausible entre dietas y granjas.")
    return MetaResult(
        model="FE", method="IV", k=k, estimate=est, se=se, ci_low=est - crit * se, ci_high=est + crit * se,
        statistic=z, p_value=float(2 * stats.norm.sf(abs(z))), test="z", tau2=0.0, Q=Q, Q_df=df,
        Q_p=float(stats.chi2.sf(Q, df)) if df > 0 else float("nan"),
        I2=max(0.0, (Q - df) / Q) * 100 if Q > 0 and df > 0 else 0.0,
        H2=Q / df if df > 0 else float("nan"), level=level,
        weights=list(100 * w / np.sum(w)), notes=notes,
    )


def random_effects(yi, vi, method: str = "REML", ajuste: str = "ninguno", level: float = 0.95,
                   *, hksj: bool | None = None) -> MetaResult:
    """Modelo de efectos aleatorios con tau² por REML, DL o PM.

    `ajuste` fija la inferencia sobre el efecto medio:

    - "ninguno": prueba z de Wald, EE = √(1/Σw) con w = 1/(vi + tau²).
    - "hksj": Hartung-Knapp-Sidik-Jonkman, EE = √(q/Σw) con q = Σw(yi - μ)²/(k - 1) y t con k - 1 gl.
      Si q < 1, el IC puede ser más estrecho que el de Wald: se calcula además el IC modificado
      con max(1, q) y se guarda en `ci_mod_low`/`ci_mod_high` (con nota). Si q = 0 (efectos
      idénticos), el resultado principal usa max(1, q) = 1 para no dividir por un EE nulo.
    - "hksj-mod": HKSJ modificado, siempre con max(1, q).

    `hksj` se conserva por compatibilidad: True equivale a ajuste="hksj" (si `ajuste` no pide ya
    un ajuste HKSJ); False no cambia `ajuste`. Un booleano en la posición de `ajuste` se interpreta igual.
    """
    ajuste = _resolve_ajuste(ajuste, hksj)
    y, v = _arrays(yi, vi)
    k = y.size
    method = method.upper()
    if method == "DL":
        tau2 = tau2_dl(y, v)
    elif method == "REML":
        tau2 = tau2_reml(y, v)
    elif method == "PM":
        tau2 = tau2_pm(y, v)
    else:
        raise ValueError("Método de tau² desconocido: usa REML, DL o PM")
    w = 1.0 / (v + tau2)
    sw = float(np.sum(w))
    est = float(np.sum(w * y) / sw)
    se = math.sqrt(1.0 / sw)
    notes: list[str] = []
    alpha = 1 - level
    q: float | None = None
    ci_mod_low = ci_mod_high = None
    if ajuste != "ninguno" and k > 1:
        q = float(np.sum(w * (y - est) ** 2) / (k - 1))
        crit = float(stats.t.ppf(1 - alpha / 2, k - 1))
        q_is_zero = q < _Q_ZERO_TOL
        if ajuste == "hksj-mod":
            factor = max(1.0, q)
            test = f"t (HKSJ modificado max(1, q), gl = {k - 1})"
            notes.append(f"HKSJ modificado: factor de escala max(1, q) = {factor:.4g} (q = {q:.4g}).")
        elif q_is_zero:
            factor = 1.0
            test = f"t (HKSJ con max(1, q) porque q = 0, gl = {k - 1})"
            notes.append("HKSJ con q = 0 (efectos idénticos): para no dividir por un EE nulo se usa "
                         "max(1, q) = 1, es decir, el IC del HKSJ modificado.")
        else:
            factor = q
            test = f"t (HKSJ, gl = {k - 1})"
        se = math.sqrt(factor / sw)
        statistic = est / se
        p_value = float(2 * stats.t.sf(abs(statistic), k - 1))
        if ajuste == "hksj" and q < 1:
            se_mod = math.sqrt(1.0 / sw)  # max(1, q) = 1
            ci_mod_low, ci_mod_high = est - crit * se_mod, est + crit * se_mod
            if not q_is_zero:
                notes.append(f"HKSJ con factor de escala q = {q:.4g} < 1: el IC puede ser más estrecho que el de "
                             "Wald; se da también el IC del HKSJ modificado max(1, q) (ci_mod_low, ci_mod_high) "
                             "como análisis de sensibilidad.")
    else:
        if ajuste != "ninguno":
            notes.append(f"Con k = 1 no se aplica el ajuste {ajuste} (0 gl): se usa la prueba z.")
        crit = float(stats.norm.ppf(1 - alpha / 2))
        statistic = est / se
        p_value = float(2 * stats.norm.sf(abs(statistic)))
        test = "z"
    Q = _q_statistic(y, v)
    df = k - 1
    s2 = _typical_within_variance(v) if k > 1 else float("nan")
    I2 = 100 * tau2 / (tau2 + s2) if k > 1 and (tau2 + s2) > 0 else 0.0
    H2 = (tau2 + s2) / s2 if k > 1 and s2 > 0 else float("nan")
    pi_low = pi_high = None
    if k >= 3:
        t_pi = stats.t.ppf(1 - alpha / 2, k - 2)
        half = t_pi * math.sqrt(tau2 + se ** 2)
        pi_low, pi_high = est - half, est + half
    else:
        notes.append("Con k < 3 no se calcula intervalo de predicción.")
    if k < 5:
        notes.append(f"k = {k}: la estimación de tau² es muy imprecisa; interpreta la heterogeneidad con cautela.")
    return MetaResult(
        model="RE", method=method, k=k, estimate=est, se=se, ci_low=est - crit * se, ci_high=est + crit * se,
        statistic=statistic, p_value=p_value, test=test, tau2=tau2, Q=Q, Q_df=df,
        Q_p=float(stats.chi2.sf(Q, df)) if df > 0 else float("nan"), I2=I2, H2=H2,
        pi_low=pi_low, pi_high=pi_high, level=level, weights=list(100 * w / np.sum(w)), notes=notes,
        ajuste=ajuste, hksj_q=q, ci_mod_low=ci_mod_low, ci_mod_high=ci_mod_high,
    )


# --------------------------------------------------------------------------- sesgo de publicación
def egger_test(yi, vi) -> dict:
    """Prueba de regresión de Egger (asimetría del embudo): regresión de yi/√vi sobre 1/√vi.

    Devuelve {"estimable": False, "motivo": ...} en lugar de cifras sin sentido cuando la prueba
    no está identificada: k < 3, precisión (1/√vi) (casi) constante (todas las vi prácticamente
    iguales, con lo que el intercepto y la pendiente son colineales) o residuos nulos. Si es
    estimable devuelve "estimable": True con intercepto, ee, t, gl y p. Potencia baja si k < 10:
    para decidir si el sesgo de publicación es evaluable usa `publication_bias`.
    """
    y, v = _arrays(yi, vi)
    k = y.size
    if k < 3:
        return {"estimable": False, "k": k,
                "motivo": f"k = {k}: la prueba de Egger necesita al menos 3 estudios."}
    se = np.sqrt(v)
    precision = 1.0 / se
    rel_spread = float((precision.max() - precision.min()) / precision.mean())
    if rel_spread < _EGGER_MIN_REL_SPREAD:
        return {"estimable": False, "k": k,
                "motivo": "La precisión (1/√vi) es (casi) constante porque todas las vi son prácticamente "
                          "iguales: el intercepto de Egger es colineal con la pendiente y la prueba no es "
                          "estimable (no indica ausencia de sesgo)."}
    z = y / se
    X = np.column_stack([np.ones(k), precision])
    beta, *_ = np.linalg.lstsq(X, z, rcond=None)
    resid = z - X @ beta
    df = k - 2
    sigma2 = float(resid @ resid) / df
    if sigma2 <= (np.finfo(float).eps * max(1.0, float(np.max(np.abs(z))))) ** 2:
        return {"estimable": False, "k": k,
                "motivo": "Los efectos estandarizados caen exactamente sobre una recta (residuos nulos, p. ej. "
                          "todos los yi = 0): el error estándar del intercepto no es estimable."}
    cov = sigma2 * np.linalg.inv(X.T @ X)
    se_b0 = math.sqrt(cov[0, 0])
    t_val = float(beta[0] / se_b0)
    p = float(2 * stats.t.sf(abs(t_val), df))
    out = {"estimable": True, "k": k, "intercepto": float(beta[0]), "ee": se_b0, "t": t_val, "gl": df, "p": p}
    if k < 10:
        out["nota"] = "k < 10: la prueba tiene poca potencia; no descartes sesgo de publicación por un p alto."
    return out


def publication_bias(yi, vi, k_min: int = 10) -> dict:
    """Evaluabilidad del sesgo de publicación: solo ejecuta la prueba de Egger si k >= k_min.

    Con k < k_min devuelve {"evaluable": False, "motivo": "k < 10: ..."} (con el k_min usado), sin
    cifras: con tan pocos estudios la prueba y el embudo no permiten evaluar el sesgo. Si Egger no
    es estimable (p. ej. vi iguales), también devuelve "evaluable": False con su motivo.
    """
    y, v = _arrays(yi, vi)
    k = y.size
    try:
        valid = not isinstance(k_min, bool) and int(k_min) == k_min and k_min >= 3
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError(f"k_min debe ser un entero >= 3 (la prueba de Egger necesita al menos 3 estudios): {k_min!r}")
    k_min = int(k_min)
    if k < k_min:
        return {"evaluable": False, "k": k, "k_min": k_min,
                "motivo": f"k < {k_min}: con {k} estudio(s) la prueba de Egger y el embudo tienen muy poca "
                          "potencia; el sesgo de publicación se declara no evaluable (no significa que no exista)."}
    eg = egger_test(y, v)
    if not eg["estimable"]:
        return {"evaluable": False, "k": k, "k_min": k_min, "motivo": eg["motivo"], "egger": eg}
    return {"evaluable": True, "k": k, "k_min": k_min, "egger": eg,
            "nota": "La prueba de Egger detecta asimetría del embudo, que puede deberse a sesgo de publicación "
                    "o a otras causas (heterogeneidad entre estudios pequeños y grandes, azar; con SMD, vi "
                    "depende del propio g y puede crear asimetría artificial); un p alto no demuestra "
                    "ausencia de sesgo."}


# --------------------------------------------------------------------------- sensibilidad y subgrupos
def leave_one_out(yi, vi, labels: list[str] | None = None, method: str = "REML", ajuste: str = "ninguno",
                  *, hksj: bool | None = None) -> list[dict]:
    """Repite el modelo de efectos aleatorios quitando cada fila. `ajuste` y `hksj` como en random_effects.

    Cada fila se trata como un estudio: si hay filas dependientes (mismo estudio), agrégalas antes
    con `aggregate_dependent` para que «sin X» quite el estudio entero.
    """
    ajuste = _resolve_ajuste(ajuste, hksj)
    y, v = _arrays(yi, vi)
    k = y.size
    if k < 3:
        raise ValueError("El análisis uno-fuera necesita k >= 3")
    labels = labels or [str(i + 1) for i in range(k)]
    if len(labels) != k:
        raise ValueError("labels debe tener la misma longitud que yi")
    rows = []
    for i in range(k):
        mask = np.arange(k) != i
        res = random_effects(y[mask], v[mask], method=method, ajuste=ajuste)
        rows.append({"excluido": labels[i], "estimacion": res.estimate, "ci_low": res.ci_low,
                     "ci_high": res.ci_high, "p": res.p_value, "tau2": res.tau2, "I2": res.I2,
                     "ci_mod_low": res.ci_mod_low, "ci_mod_high": res.ci_mod_high})
    return rows


def subgroups(yi, vi, groups: list[str], method: str = "REML", ajuste: str = "ninguno",
              *, hksj: bool | None = None) -> dict:
    """Efecto por subgrupo (tau² propio en cada uno) y prueba Q entre subgrupos.

    `ajuste` y `hksj` como en random_effects; los subgrupos con un solo estudio usan efecto fijo.
    """
    ajuste = _resolve_ajuste(ajuste, hksj)
    y, v = _arrays(yi, vi)
    if len(groups) != y.size:
        raise ValueError("groups debe tener la misma longitud que yi")
    per_group = {}
    for g in dict.fromkeys(groups):
        mask = np.array([x == g for x in groups])
        if mask.sum() == 1:
            per_group[g] = fixed_effect(y[mask], v[mask])
        else:
            per_group[g] = random_effects(y[mask], v[mask], method=method, ajuste=ajuste)
    est = np.array([r.estimate for r in per_group.values()])
    var = np.array([r.se ** 2 for r in per_group.values()])
    if est.size < 2:
        return {"subgrupos": {g: r.to_dict() for g, r in per_group.items()}, "Q_entre": None}
    w = 1.0 / var
    mu = np.sum(w * est) / np.sum(w)
    q_between = float(np.sum(w * (est - mu) ** 2))
    df = est.size - 1
    return {"subgrupos": {g: r.to_dict() for g, r in per_group.items()},
            "Q_entre": q_between, "gl": df, "p": float(stats.chi2.sf(q_between, df)),
            "nota": "Comparación observacional entre estudios: no es aleatorizada y puede estar confundida."}


# --------------------------------------------------------------------------- efectos dependientes
def _cluster_key(label):
    """Identificador de clúster normalizado (sin espacios sobrantes); None si falta."""
    if label is None:
        return None
    if isinstance(label, float) and math.isnan(label):
        return None
    if isinstance(label, str):
        label = label.strip()
        return label or None
    return label


def detect_dependence(etiquetas) -> dict:
    """Clústeres (p. ej. estudios) que aparecen en más de una fila: {etiqueta: [índices de fila, base 0]}.

    Varias filas del mismo estudio (varios tratamientos frente a un control compartido, varias
    variables o momentos) no son independientes: si el resultado no está vacío, agrégalas con
    `aggregate_dependent` o usa un modelo multinivel. Las etiquetas vacías o None no se agrupan.
    """
    groups: dict = {}
    for i, label in enumerate(etiquetas):
        key = _cluster_key(label)
        if key is not None:
            groups.setdefault(key, []).append(i)
    return {key: idx for key, idx in groups.items() if len(idx) > 1}


def aggregate_dependent(yi, vi, clusters, rho: float = 0.5) -> tuple[list[float], list[float], list]:
    """Resume los efectos dependientes en un efecto por clúster (p. ej. por estudio).

    Para un clúster con m efectos: yi_agg = media simple de sus yi y
    vi_agg = (1/m²)·(Σ vi + Σ_{i≠j} ρ·√(vi·vj)), varianza de la media de m efectos con una
    correlación común ρ entre cada par (Borenstein, Hedges, Higgins y Rothstein, Introduction to
    Meta-Analysis, 2009; capítulo: pendiente de verificar). Los clústeres de una sola fila quedan igual.

    Supuesto de correlación: ρ es la correlación entre los errores de muestreo de los efectos de
    un mismo clúster y se supone igual para todos los pares y todos los clústeres. Casi nunca se
    publica: es un supuesto de trabajo que conviene declarar y someter a análisis de sensibilidad
    con varios valores. ρ = 0 trata las filas como independientes (subestima la varianza si hay
    dependencia, p. ej. con un control compartido); ρ = 1 es el caso más conservador. Promediar
    pierde la información intraestudio (p. ej. la respuesta a la dosis dentro de cada estudio).

    Devuelve (yi_agg, vi_agg, etiquetas) en el orden de primera aparición de cada clúster.
    """
    y, v = _arrays(yi, vi)
    labels = list(clusters)
    if len(labels) != y.size:
        raise ValueError("clusters debe tener la misma longitud que yi")
    try:
        rho = float(rho)
    except (TypeError, ValueError):
        raise ValueError(f"rho debe ser un número entre 0 y 1: {rho!r}") from None
    if not (math.isfinite(rho) and 0.0 <= rho <= 1.0):
        raise ValueError(f"rho debe estar entre 0 y 1 (correlación supuesta entre efectos del mismo clúster): {rho}")
    keys = [_cluster_key(c) for c in labels]
    missing = [i + 1 for i, key in enumerate(keys) if key is None]
    if missing:
        raise ValueError("Falta el identificador de clúster (estudio) en la(s) fila(s) "
                         + ", ".join(map(str, missing)))
    order = list(dict.fromkeys(keys))
    y_agg: list[float] = []
    v_agg: list[float] = []
    for key in order:
        idx = [i for i, k in enumerate(keys) if k == key]
        yy, vv = y[idx], v[idx]
        m = len(idx)
        if m == 1:
            y_agg.append(float(yy[0]))
            v_agg.append(float(vv[0]))
            continue
        sd = np.sqrt(vv)
        cross = float(np.sum(np.outer(sd, sd)[~np.eye(m, dtype=bool)]))  # Σ_{i≠j} √(vi·vj)
        y_agg.append(float(np.mean(yy)))
        v_agg.append((float(np.sum(vv)) + rho * cross) / m ** 2)
    return y_agg, v_agg, order


# --------------------------------------------------------------------------- IC de la heterogeneidad (D6-9)
def _generalized_q(tau2: float, y: np.ndarray, v: np.ndarray) -> float:
    w = 1.0 / (v + tau2)
    mu = np.sum(w * y) / np.sum(w)
    return float(np.sum(w * (y - mu) ** 2))


def tau2_confint(yi, vi, level: float = 0.95) -> dict:
    """IC de tau² por el método Q-profile (Viechtbauer 2007, Stat Med 26:37-52) y los IC de tau, I² y H²
    que se derivan de él (I² = tau²/(tau² + s²), con s² la varianza intraestudio típica). Es el método
    por defecto de metafor::confint para modelos de efectos aleatorios.

    Q(tau²) = Σ w_i(tau²)·(y_i - μ̂(tau²))² decrece con tau²: el límite inferior es el tau² con
    Q = χ²(k-1; 1-α/2) (0 si Q(0) es menor) y el superior el tau² con Q = χ²(k-1; α/2).
    """
    y, v = _arrays(yi, vi)
    k = y.size
    if k < 2:
        raise ValueError("El IC de tau² necesita k >= 2")
    alpha = 1 - level
    q_upper = float(stats.chi2.ppf(1 - alpha / 2, k - 1))   # define el límite inferior de tau²
    q_lower = float(stats.chi2.ppf(alpha / 2, k - 1))       # define el límite superior de tau²

    def solve(target: float) -> float:
        if _generalized_q(0.0, y, v) <= target:
            return 0.0
        hi = max(float(np.var(y)), float(np.max(v)), 1e-8)
        while _generalized_q(hi, y, v) > target:
            hi *= 2.0
            if hi > 1e12:
                raise RuntimeError("El IC Q-profile de tau² no converge")
        return float(optimize.brentq(lambda t: _generalized_q(t, y, v) - target, 0.0, hi, xtol=1e-12))

    lo, hi = solve(q_upper), solve(q_lower)
    s2 = _typical_within_variance(v)
    def i2(t):
        return 100 * t / (t + s2)
    return {
        "metodo": "Q-profile", "nivel": level,
        "tau2": [lo, hi], "tau": [math.sqrt(lo), math.sqrt(hi)],
        "I2": [i2(lo), i2(hi)], "H2": [(lo + s2) / s2, (hi + s2) / s2],
        "nota": "Con pocos estudios el IC de tau² es muy ancho: la heterogeneidad apenas está acotada.",
    }
