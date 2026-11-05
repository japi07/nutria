"""Distribuciones de entrada declarativas para los modelos económicos.

Formatos admitidos en TOML:
    valor = 12.5                                         # fijo, sin fuente
    valor = { valor = 12.5, fuente = "[PUB] doi:10.xxxx/yyyy" }   # fijo con fuente registrada
    valor = { dist = "pert", min = 0.2, moda = 0.8, max = 1.5 }
    valor = { dist = "triangular", min = ..., moda = ..., max = ... }
    valor = { dist = "normal", media = ..., de = ... }
    valor = { dist = "lognormal", media_log = ..., de_log = ... }
    valor = { dist = "uniforme", min = ..., max = ... }
    valor = { dist = "fijo", valor = ... }

Cualquier tabla admite además "fuente" (de dónde sale el valor: "[PUB] doi:10.xxxx/yyyy" o "[PUB] A-0003" con el
identificador de una fuente o afirmación registrada, "[DER] nutria meta ...", "[SUP] ...") y "nota". Una clave
desconocida es un error: así una errata (p. ej. "fuentes") no se pierde en silencio.

Valor central de una entrada (`Dist.central`), con UN estadístico para todas las entradas de un modelo:
- "moda" (por defecto): la moda declarada (pert, triangular), la media de la normal (su moda), exp(media_log −
  de_log²) en la lognormal y, en la uniforme, que no tiene moda única, el punto medio;
- "mediana": la mediana de cada entrada.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np
from scipy import stats

# Etiquetas de CLAUDE.md que identifican el origen de un valor.
ETIQUETAS_FUENTE = ("[PUB]", "[DER]", "[SUP]", "[DESC]", "[HIP]")
# Estadístico del valor central (el mismo para todas las entradas de un modelo).
CENTRAL_STATS = ("moda", "mediana")
CENTRAL_TEXT = {
    "moda": "moda de cada entrada (la declarada en pert y triangular, la media en la normal, exp(media_log − "
            "de_log²) en la lognormal y el punto medio en la uniforme, que no tiene moda única)",
    "mediana": "mediana de cada entrada",
}
_COMMON_KEYS = {"fuente", "nota"}
_KEYS = {
    "fijo": {"dist", "valor"}, "pert": {"dist", "min", "moda", "max"}, "triangular": {"dist", "min", "moda", "max"},
    "normal": {"dist", "media", "de"}, "lognormal": {"dist", "media_log", "de_log"}, "uniforme": {"dist", "min", "max"},
}
_ALIASES = {"fixed": "fijo"}


@dataclass
class Dist:
    kind: str
    frozen: object | None = None
    fixed: float | None = None
    declared_mode: float | None = None
    source: str | None = None
    note: str = ""
    params: dict | None = None

    @property
    def stochastic(self) -> bool:
        return self.fixed is None

    def sample(self, rng: np.random.Generator, n: int) -> np.ndarray:
        if self.fixed is not None:
            return np.full(n, self.fixed)
        return self.frozen.rvs(size=n, random_state=rng)

    def ppf(self, u: np.ndarray) -> np.ndarray:
        """Inversa de la función de distribución (vectorizada); se usa en la cópula gaussiana."""
        if self.fixed is not None:
            return np.full(np.shape(u), self.fixed, dtype=float)
        return np.asarray(self.frozen.ppf(u), dtype=float)

    def quantile(self, q: float) -> float:
        if self.fixed is not None:
            return self.fixed
        return float(self.frozen.ppf(q))

    def mode(self) -> float:
        """Moda: la declarada (pert, triangular), la media de la normal, exp(media_log − de_log²) en la lognormal y
        el punto medio en la uniforme (densidad constante: no tiene moda única)."""
        if self.fixed is not None:
            return self.fixed
        if self.declared_mode is not None:
            return self.declared_mode
        if self.kind == "lognormal":
            p = self.params or {}
            return float(math.exp(p["media_log"] - p["de_log"] ** 2))
        return float(self.frozen.median())

    def median(self) -> float:
        if self.fixed is not None:
            return self.fixed
        return float(self.frozen.median())

    def central(self, stat: str = "moda") -> float:
        """Valor central con el estadístico `stat` ("moda" o "mediana"), el mismo para todas las entradas."""
        check_central(stat)
        return self.mode() if stat == "moda" else self.median()

    def describe(self) -> str:
        if self.fixed is not None:
            return f"{self.fixed:g} (fijo)"
        p = self.params or {}
        return f"{self.kind}(" + ", ".join(f"{k} {v:g}" for k, v in p.items()) + ")"


def check_central(stat: str) -> None:
    if stat not in CENTRAL_STATS:
        raise ValueError(f"Estadístico central no válido: {stat!r}. Opciones: {', '.join(CENTRAL_STATS)}")


def _number(spec: dict, key: str, kind: str) -> float:
    if key not in spec:
        raise ValueError(f"{kind}: falta '{key}' en {spec!r}")
    value = spec[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{kind}: '{key}' debe ser un número ({spec!r})")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{kind}: '{key}' no es finito ({spec!r})")
    return value


def parse_dist(spec) -> Dist:
    if isinstance(spec, bool):
        raise ValueError(f"Especificación de distribución no válida: {spec!r}")
    if isinstance(spec, (int, float)):
        if not math.isfinite(float(spec)):
            raise ValueError(f"Valor no finito: {spec!r}")
        return Dist("fijo", fixed=float(spec))
    if not isinstance(spec, dict):
        raise ValueError(f"Especificación de distribución no válida: {spec!r}")
    source = spec.get("fuente")
    if source is not None and (not isinstance(source, str) or not source.strip()):
        raise ValueError(f"'fuente' debe ser un texto no vacío ({spec!r})")
    source = source.strip() if source else None
    note = str(spec.get("nota", "") or "")
    if "dist" not in spec:
        if "valor" not in spec:
            raise ValueError(f"Especificación de distribución no válida (falta 'dist' o 'valor'): {spec!r}")
        kind = "fijo"
    else:
        kind = str(spec["dist"]).lower()
        kind = _ALIASES.get(kind, kind)
    if kind not in _KEYS:
        raise ValueError(f"Distribución desconocida: {kind}. Opciones: {', '.join(_KEYS)}")
    unknown = set(spec) - _KEYS[kind] - _COMMON_KEYS
    if unknown:
        raise ValueError(f"{kind}: claves desconocidas {sorted(unknown)} en {spec!r}. Admitidas: "
                         f"{sorted((_KEYS[kind] | _COMMON_KEYS) - {'dist'})}")
    if kind == "fijo":
        return Dist("fijo", fixed=_number(spec, "valor", kind), source=source, note=note)
    if kind in ("pert", "triangular"):
        lo, mode, hi = (_number(spec, k, kind) for k in ("min", "moda", "max"))
        if not lo <= mode <= hi or lo == hi:
            raise ValueError(f"{kind}: se requiere min <= moda <= max y min < max ({spec})")
        if kind == "triangular":
            frozen = stats.triang((mode - lo) / (hi - lo), loc=lo, scale=hi - lo)
        else:
            a = 1 + 4 * (mode - lo) / (hi - lo)
            b = 1 + 4 * (hi - mode) / (hi - lo)
            frozen = stats.beta(a, b, loc=lo, scale=hi - lo)
        return Dist(kind, frozen=frozen, declared_mode=mode, source=source, note=note,
                    params={"min": lo, "moda": mode, "max": hi})
    if kind == "normal":
        mean, sd = _number(spec, "media", kind), _number(spec, "de", kind)
        if sd <= 0:
            raise ValueError(f"normal: 'de' debe ser > 0 ({spec})")
        return Dist(kind, frozen=stats.norm(mean, sd), declared_mode=mean, source=source, note=note,
                    params={"media": mean, "de": sd})
    if kind == "lognormal":
        mu, sigma = _number(spec, "media_log", kind), _number(spec, "de_log", kind)
        if sigma <= 0:
            raise ValueError(f"lognormal: 'de_log' debe ser > 0 ({spec})")
        return Dist(kind, frozen=stats.lognorm(sigma, scale=np.exp(mu)), source=source, note=note,
                    params={"media_log": mu, "de_log": sigma})
    lo, hi = _number(spec, "min", kind), _number(spec, "max", kind)
    if not lo < hi:
        raise ValueError(f"uniforme: se requiere min < max ({spec})")
    return Dist(kind, frozen=stats.uniform(lo, hi - lo), source=source, note=note, params={"min": lo, "max": hi})


def source_has_label(source: str | None) -> bool:
    """La fuente declara su origen con una etiqueta de CLAUDE.md ([PUB], [DER], [SUP], [DESC] o [HIP])."""
    return bool(source) and any(tag in source for tag in ETIQUETAS_FUENTE)


def source_label(source: str | None, declared: bool = True) -> str:
    """Etiqueta principal de una fuente (la primera que aparece en el texto), "sin etiqueta", "sin fuente" o, para
    una entrada que no se declaró, "valor por defecto"."""
    if not source:
        return "sin fuente" if declared else "valor por defecto"
    found = [(source.find(tag), tag) for tag in ETIQUETAS_FUENTE if tag in source]
    return min(found)[1] if found else "sin etiqueta"


# Etiquetas que no apoyan un valor en datos: supuestos, desconocidos, hipótesis o sin fuente.
_WEAK_LABELS = ("[SUP]", "[DESC]", "[HIP]", "sin fuente", "sin etiqueta", "valor por defecto")


def sources_summary(inputs: list[dict], tornado: list[dict], top: int = 5) -> dict:
    """Resumen de las fuentes de un modelo: recuento por etiqueta y, de las `top` entradas que más mueven el
    resultado en el tornado, las que son supuestos ([SUP], [DESC], [HIP]), no tienen fuente o usan un valor por
    defecto. `inputs`: filas de input_table (entrada, fuente, declarada); `tornado`: filas de sensitivity (entrada,
    amplitud), ya ordenadas."""
    labels = {r["entrada"]: source_label(r["fuente"], r["declarada"]) for r in inputs}
    counts: dict[str, int] = {}
    for label in labels.values():
        counts[label] = counts.get(label, 0) + 1
    drivers = [r["entrada"] for r in tornado if r.get("amplitud")][:top]
    weak = [f"{name} ({labels.get(name, 'sin fuente')})" for name in drivers
            if labels.get(name, "sin fuente") in _WEAK_LABELS]
    return {"recuento": counts, "entradas": len(labels), "principales": drivers, "principales_sin_datos": weak}


def sources_summary_line(summary: dict, what: str) -> str:
    order = [*ETIQUETAS_FUENTE, "sin etiqueta", "sin fuente", "valor por defecto"]
    counts = "; ".join(f"{label} {summary['recuento'][label]}" for label in order if label in summary["recuento"])
    line = f"Resumen de fuentes ({summary['entradas']} entradas): {counts}."
    if summary["principales_sin_datos"]:
        line += (f" De las {len(summary['principales'])} entradas que más mueven {what} (tornado), "
                 f"{len(summary['principales_sin_datos'])} no se apoyan en datos: "
                 + ", ".join(summary["principales_sin_datos"])
                 + ". El resultado depende sobre todo de supuestos: acótalos con fuentes antes de puntuar.")
    elif summary["principales"]:
        line += f" Las {len(summary['principales'])} entradas que más mueven {what} (tornado) tienen fuente."
    return line


# Identificadores del registro que una fuente [PUB] puede citar: fuentes (doi:, pmid:, openalex:) y afirmaciones (A-).
_ID_RE = re.compile(r"(?i)(?<![\w/.-])(doi:\s*10\.\d{4,9}/\S+|pmid:\s*\d+|openalex:\s*W\d+|A-\d{4,})")


def _trim_id(token: str) -> str:
    """Quita la puntuación final que no forma parte del identificador (paréntesis o corchetes sin abrir, '.', ',')."""
    while token:
        last = token[-1]
        if last in ".,;:":
            token = token[:-1]
        elif last == ")" and token.count(")") > token.count("("):
            token = token[:-1]
        elif last == "]" and token.count("]") > token.count("["):
            token = token[:-1]
        else:
            break
    return token


def source_ids(source: str | None) -> list[str]:
    """Identificadores de registro citados en una fuente, normalizados: 'doi:<doi en minúsculas>', 'pmid:<n>',
    'openalex:W<n>' (fuentes registradas) o 'A-<n>' (afirmaciones registradas)."""
    out: list[str] = []
    for match in _ID_RE.finditer(source or ""):
        token = _trim_id(re.sub(r"\s+", "", match.group(1)))
        prefix, _, rest = token.partition(":")
        if rest:
            prefix = prefix.lower()
            rest = {"doi": rest.lower(), "openalex": rest.upper()}.get(prefix, rest)
            token = f"{prefix}:{rest}"
        else:
            token = token.upper()
        if token not in out:
            out.append(token)
    return out


def gaussian_copula(names: list[str], pairs: list[tuple[str, str, float]] | None = None,
                    matrix: list[list[float]] | None = None) -> np.ndarray:
    """Matriz de correlación de una cópula gaussiana a partir de pares (a, b, rho) o de una matriz completa.

    Comprueba que sea simétrica, con diagonal 1, |rho| <= 1 y definida positiva (Cholesky); si no, ValueError."""
    k = len(names)
    if matrix is not None:
        m = np.asarray(matrix, dtype=float)
        if m.shape != (k, k):
            raise ValueError(f"La matriz de correlación debe ser {k}x{k} (una fila por variable); es {m.shape}")
    else:
        m = np.eye(k)
        index = {name: i for i, name in enumerate(names)}
        seen: set[frozenset] = set()
        for a, b, rho in pairs or []:
            i, j = index[a], index[b]
            if i == j:
                raise ValueError(f"Correlación de '{a}' consigo misma")
            if frozenset((i, j)) in seen:
                raise ValueError(f"Correlación entre '{a}' y '{b}' declarada dos veces")
            seen.add(frozenset((i, j)))
            m[i, j] = m[j, i] = float(rho)
    if not np.all(np.isfinite(m)):
        raise ValueError("La matriz de correlación tiene valores no finitos")
    if not np.allclose(m, m.T, atol=1e-12):
        raise ValueError("La matriz de correlación no es simétrica")
    if not np.allclose(np.diag(m), 1.0, atol=1e-12):
        raise ValueError("La diagonal de la matriz de correlación debe ser 1")
    if np.any(np.abs(m) > 1.0 + 1e-12):
        raise ValueError("Las correlaciones deben estar entre -1 y 1")
    try:
        np.linalg.cholesky(m)
    except np.linalg.LinAlgError:
        raise ValueError("La matriz de correlación no es definida positiva: las correlaciones declaradas son "
                         "incompatibles entre sí (revisa los signos y magnitudes)") from None
    if float(np.min(np.linalg.eigvalsh(m))) <= 1e-10:
        raise ValueError("La matriz de correlación es singular o casi singular (no es definida positiva)")
    return m


def copula_uniforms(rng: np.random.Generator, corr: np.ndarray, n: int) -> np.ndarray:
    """Uniformes correlacionadas (n x k) con una cópula gaussiana de matriz `corr`."""
    chol = np.linalg.cholesky(corr)
    z = rng.standard_normal((n, corr.shape[0])) @ chol.T
    u = stats.norm.cdf(z)
    return np.clip(u, 1e-12, 1 - 1e-12)
