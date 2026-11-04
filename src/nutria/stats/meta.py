"""Metaanálisis de efectos fijos y aleatorios (DL, REML, Paule-Mandel), con ajuste de
Hartung-Knapp-Sidik-Jonkman, heterogeneidad, intervalo de predicción, prueba de Egger,
análisis de influencia (uno fuera) y subgrupos.

Validado contra el conjunto clásico BCG (metafor::dat.bcg); ver tests/test_meta.py.
Para efectos dependientes (varios tratamientos frente a un control compartido, varias
variables por estudio) usa un modelo multinivel o varianza robusta: `nutria meta exportar-r`
genera el script de metafor (rma.mv + robust) para ese caso.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy import optimize, stats


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
            f"Heterogeneidad: tau² = {self.tau2:.4f} (tau = {self.tau:.4f}); Q({self.Q_df}) = {self.Q:.3f}, "
            f"p = {self.Q_p:.4g}; I² = {self.I2:.1f} %; H² = {self.H2:.2f}",
        ]
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


def random_effects(yi, vi, method: str = "REML", hksj: bool = False, level: float = 0.95) -> MetaResult:
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
    est = float(np.sum(w * y) / np.sum(w))
    se = math.sqrt(1.0 / np.sum(w))
    notes: list[str] = []
    alpha = 1 - level
    if hksj and k > 1:
        q = float(np.sum(w * (y - est) ** 2) / (k - 1))
        se = math.sqrt(q / np.sum(w))
        crit = stats.t.ppf(1 - alpha / 2, k - 1)
        statistic = est / se
        p_value = float(2 * stats.t.sf(abs(statistic), k - 1))
        test = f"t (HKSJ, gl = {k - 1})"
        if q < 1:
            notes.append("HKSJ con factor de escala < 1: el IC puede ser más estrecho que el de Wald; "
                         "valora la corrección ad hoc max(1, q) como sensibilidad.")
    else:
        crit = stats.norm.ppf(1 - alpha / 2)
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
    )


def egger_test(yi, vi) -> dict:
    """Prueba de regresión de Egger (asimetría del embudo). Potencia baja si k < 10."""
    y, v = _arrays(yi, vi)
    k = y.size
    if k < 3:
        raise ValueError("La prueba de Egger necesita k >= 3")
    se = np.sqrt(v)
    z = y / se
    precision = 1.0 / se
    X = np.column_stack([np.ones(k), precision])
    beta, *_ = np.linalg.lstsq(X, z, rcond=None)
    resid = z - X @ beta
    df = k - 2
    sigma2 = float(resid @ resid) / df if df > 0 else float("nan")
    cov = sigma2 * np.linalg.inv(X.T @ X)
    se_b0 = math.sqrt(cov[0, 0])
    t_val = beta[0] / se_b0 if se_b0 > 0 else float("nan")
    p = float(2 * stats.t.sf(abs(t_val), df)) if df > 0 else float("nan")
    out = {"intercepto": float(beta[0]), "ee": se_b0, "t": float(t_val), "gl": df, "p": p}
    if k < 10:
        out["nota"] = "k < 10: la prueba tiene poca potencia; no descartes sesgo de publicación por un p alto."
    return out


def leave_one_out(yi, vi, labels: list[str] | None = None, method: str = "REML", hksj: bool = False) -> list[dict]:
    y, v = _arrays(yi, vi)
    k = y.size
    if k < 3:
        raise ValueError("El análisis uno-fuera necesita k >= 3")
    labels = labels or [str(i + 1) for i in range(k)]
    rows = []
    for i in range(k):
        mask = np.arange(k) != i
        res = random_effects(y[mask], v[mask], method=method, hksj=hksj)
        rows.append({"excluido": labels[i], "estimacion": res.estimate, "ci_low": res.ci_low,
                     "ci_high": res.ci_high, "p": res.p_value, "tau2": res.tau2, "I2": res.I2})
    return rows


def subgroups(yi, vi, groups: list[str], method: str = "REML", hksj: bool = False) -> dict:
    """Efecto por subgrupo (tau² propio en cada uno) y prueba Q entre subgrupos."""
    y, v = _arrays(yi, vi)
    if len(groups) != y.size:
        raise ValueError("groups debe tener la misma longitud que yi")
    per_group = {}
    for g in dict.fromkeys(groups):
        mask = np.array([x == g for x in groups])
        if mask.sum() == 1:
            per_group[g] = fixed_effect(y[mask], v[mask])
        else:
            per_group[g] = random_effects(y[mask], v[mask], method=method, hksj=hksj)
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
