"""Distribuciones de entrada declarativas para los modelos económicos.

Formatos admitidos en TOML:
    valor = 12.5                                         # fijo
    valor = { dist = "pert", min = 0.2, moda = 0.8, max = 1.5 }
    valor = { dist = "triangular", min = ..., moda = ..., max = ... }
    valor = { dist = "normal", media = ..., de = ... }
    valor = { dist = "lognormal", media_log = ..., de_log = ... }
    valor = { dist = "uniforme", min = ..., max = ... }
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass
class Dist:
    kind: str
    frozen: object | None = None
    fixed: float | None = None
    declared_mode: float | None = None

    def sample(self, rng: np.random.Generator, n: int) -> np.ndarray:
        if self.fixed is not None:
            return np.full(n, self.fixed)
        return self.frozen.rvs(size=n, random_state=rng)

    def quantile(self, q: float) -> float:
        if self.fixed is not None:
            return self.fixed
        return float(self.frozen.ppf(q))

    def central(self) -> float:
        if self.fixed is not None:
            return self.fixed
        if self.declared_mode is not None:
            return self.declared_mode
        return float(self.frozen.median())


def parse_dist(spec) -> Dist:
    if isinstance(spec, (int, float)):
        return Dist("fijo", fixed=float(spec))
    if not isinstance(spec, dict) or "dist" not in spec:
        raise ValueError(f"Especificación de distribución no válida: {spec!r}")
    kind = str(spec["dist"]).lower()
    if kind in ("fijo", "fixed"):
        return Dist("fijo", fixed=float(spec["valor"]))
    if kind in ("pert", "triangular"):
        lo, mode, hi = float(spec["min"]), float(spec["moda"]), float(spec["max"])
        if not lo <= mode <= hi or lo == hi:
            raise ValueError(f"{kind}: se requiere min <= moda <= max y min < max ({spec})")
        if kind == "triangular":
            frozen = stats.triang((mode - lo) / (hi - lo), loc=lo, scale=hi - lo)
        else:
            a = 1 + 4 * (mode - lo) / (hi - lo)
            b = 1 + 4 * (hi - mode) / (hi - lo)
            frozen = stats.beta(a, b, loc=lo, scale=hi - lo)
        return Dist(kind, frozen=frozen, declared_mode=mode)
    if kind == "normal":
        return Dist(kind, frozen=stats.norm(float(spec["media"]), float(spec["de"])),
                    declared_mode=float(spec["media"]))
    if kind == "lognormal":
        mu, sigma = float(spec["media_log"]), float(spec["de_log"])
        return Dist(kind, frozen=stats.lognorm(sigma, scale=np.exp(mu)))
    if kind == "uniforme":
        lo, hi = float(spec["min"]), float(spec["max"])
        return Dist(kind, frozen=stats.uniform(lo, hi - lo))
    raise ValueError(f"Distribución desconocida: {kind}")
