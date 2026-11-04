"""Meta-regresión de efectos mixtos (REML o DL) con prueba de Knapp-Hartung opcional.

Útil para modelar respuesta a dosis, efecto de la dieta basal, etapa productiva o diseño
experimental como moderadores. Recuerda: la meta-regresión es observacional entre estudios
(sesgo ecológico, confusión) y necesita, como orientación, unos 10 estudios por moderador.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy import stats

from .meta import _arrays, _typical_within_variance, tau2_dl, tau2_reml


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

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        pct = int(round(self.level * 100))
        lines = [f"Meta-regresión ({self.method}, prueba {self.test}), k = {self.k}",
                 f"tau² residual = {self.tau2:.4f}; I² residual = {self.I2_res:.1f} %"
                 + (f"; R² = {self.R2:.1f} %" if self.R2 is not None else ""),
                 f"QE({self.QE_df}) = {self.QE:.3f}, p = {self.QE_p:.4g} | QM({self.QM_df}) = {self.QM:.3f}, "
                 f"p = {self.QM_p:.4g}",
                 f"{'coeficiente':<22}{'estim.':>10}{'EE':>10}{'estad.':>9}{'p':>10}   IC{pct}"]
        for i, name in enumerate(self.names):
            lines.append(f"{name:<22}{self.coef[i]:>10.4f}{self.se[i]:>10.4f}{self.statistic[i]:>9.3f}"
                         f"{self.p_values[i]:>10.4g}   [{self.ci_low[i]:.4f}; {self.ci_high[i]:.4f}]")
        lines += [f"Nota: {n}" for n in self.notes]
        return "\n".join(lines)


def meta_regression(yi, vi, moderators: np.ndarray | list, names: list[str] | None = None,
                    method: str = "REML", knha: bool = False, level: float = 0.95) -> MetaRegResult:
    """`moderators` es la matriz k×m SIN intercepto (se añade automáticamente)."""
    y, v = _arrays(yi, vi)
    k = y.size
    M = np.asarray(moderators, dtype=float)
    if M.ndim == 1:
        M = M[:, None]
    if M.shape[0] != k:
        raise ValueError("La matriz de moderadores debe tener k filas")
    X = np.column_stack([np.ones(k), M])
    p = X.shape[1]
    names = ["intercepto"] + (names or [f"mod{i + 1}" for i in range(M.shape[1])])
    if k <= p:
        raise ValueError(f"Se necesitan más estudios (k = {k}) que coeficientes (p = {p})")
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
    if knha:
        s2 = float(np.sum(w * resid ** 2) / (k - p))
        cov = cov * s2
        se = np.sqrt(np.diag(cov))
        stat = beta / se
        pvals = 2 * stats.t.sf(np.abs(stat), k - p)
        crit = stats.t.ppf(1 - alpha / 2, k - p)
        test = f"t (Knapp-Hartung, gl = {k - p})"
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
    if knha:
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
        QE_p=float(stats.chi2.sf(QE, QE_df)), level=level, notes=notes,
    )


def predict(result: MetaRegResult, moderator_values: list[float]) -> dict:
    """Predicción puntual del efecto medio para unos valores de moderadores (sin IC)."""
    x = np.concatenate([[1.0], np.asarray(moderator_values, dtype=float)])
    if x.size != len(result.coef):
        raise ValueError("Número de valores de moderadores incorrecto")
    return {"prediccion": float(x @ np.asarray(result.coef)), "tau": math.sqrt(result.tau2)}
