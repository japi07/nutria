"""Perspectiva de la empresa: valor actual neto ajustado por riesgo (rNPV) del desarrollo.

Cada fase (computacional, in vitro, in vivo, dossier regulatorio...) tiene coste, duración y
probabilidad de éxito condicionada a haber llegado a ella. Los flujos comerciales solo se obtienen si
todas las fases tienen éxito.

Ventas ([mercado]), de una sola de estas dos formas:
- construidas (recomendado): censo_direccionable × adopcion_pico × ciclos_por_animal_anio × dias_por_ciclo ×
  dosis_g_dia / 1000 × precio_eur_kg × (1 − margen_distribucion). precio_eur_kg es el precio que paga el ganadero
  (el del modelo del ganadero), dosis_g_dia y dias_por_ciclo los mismos que en ese modelo y margen_distribucion la
  parte del precio que se queda la distribución. `nutria econ coherencia` (o `econ desarrollador --ganadero`)
  comprueba que precio, dosis y días coinciden con el modelo del ganadero;
- ventas_pico_eur_anio como dato: no puede comprobarse frente al modelo del ganadero (la comprobación de coherencia
  falla y da los animales tratados que implicaría).

Flujos comerciales por año de comercialización k = 0, 1, ...: ventas pico × min(1, (k + 1)/anios_hasta_pico) ×
(1 − erosion_precio_anual)^k × (margen_bruto − costes_comerciales_pct), menos impuesto_beneficios sobre el beneficio
positivo, descontados a mitad de año durante anios_comercializacion (admite fracciones de año); además,
inversion_lanzamiento_eur al llegar al mercado, coste_renovacion_eur en el año anio_renovacion si el producto sigue
en el mercado (renovación de la autorización) y, opcionalmente, un valor terminal al acabar la comercialización
(multiplo_valor_terminal × beneficio del último año; por defecto 0: los flujos acaban con anios_comercializacion).
Todas las entradas admiten distribuciones, también la tasa de descuento, los años hasta el pico y los de
comercialización. Los costes de desarrollo no se deducen de los impuestos (simplificación conservadora).

evaluate() hace dos simulaciones de Monte Carlo con los mismos parámetros muestreados:
- incertidumbre de los parámetros: en cada simulación se calcula el rNPV (valor esperado del VPN sobre el
  riesgo de las fases) con los parámetros muestreados. p_rnpv_positivo_incertidumbre_parametros mide la
  confianza en que ese valor esperado sea positivo; no es la probabilidad de que el proyecto gane dinero.
- simulación de resultados: en cada simulación se muestrea además el éxito de cada fase (Bernoulli en
  secuencia). El VPN realizado descuenta los costes de las fases alcanzadas y suma los flujos comerciales
  solo si todas tienen éxito. Su media es vpn_esperado; se informa de su distribución (percentiles y desglose por
  fase de fracaso), de P(VPN realizado > 0), de P(VPN realizado < 0) y de P(pérdida > umbral), con un umbral
  configurable ([proyecto].umbral_perdida_eur o --umbral-perdida; por defecto, el 50 % del coste central de
  desarrollo sin descontar).
sensitivity() da el tornado del rNPV central: cada entrada con incertidumbre en su P10 y su P90 con las demás en su
valor central (y la tasa de descuento ±2 puntos si es fija).

Valores centrales: el mismo estadístico para todas las entradas (`Project.central`: "moda" por defecto o "mediana";
ver nutria.econ.dist). La probabilidad de éxito de cada fase debe estar en [0, 1] en todas las simulaciones (no se
recorta en silencio).
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .dist import (CENTRAL_TEXT, Dist, check_central, parse_dist, source_has_label, sources_summary,
                   sources_summary_line)

_SECTIONS = {"proyecto", "fase", "mercado", "supuestos"}
_PROYECTO_KEYS = {"nombre", "tasa_descuento", "umbral_perdida_eur", "nota"}
_FASE_KEYS = {"nombre", "coste_eur", "duracion_anios", "prob_exito", "nota"}
_BUILT = ("censo_direccionable", "adopcion_pico", "dias_por_ciclo", "dosis_g_dia", "precio_eur_kg")
# Entradas de [mercado]: (descripción, valor por defecto o None si es obligatoria cuando se usa).
MARKET = {
    "ventas_pico_eur_anio": ("ventas pico (€/año; dato)", None),
    "censo_direccionable": ("censo direccionable (animales)", None),
    "adopcion_pico": ("adopción en el pico (fracción del censo)", None),
    "ciclos_por_animal_anio": ("ciclos de uso por animal y año", 1.0),
    "dias_por_ciclo": ("días de uso por ciclo", None),
    "dosis_g_dia": ("dosis (g/animal/día)", None),
    "precio_eur_kg": ("precio que paga el ganadero (€/kg)", None),
    "margen_distribucion": ("margen de la distribución (fracción del precio)", 0.0),
    "anios_hasta_pico": ("años hasta el pico de ventas", 3.0),
    "anios_comercializacion": ("años de comercialización", 10.0),
    "margen_bruto": ("margen bruto (fracción de las ventas)", 0.4),
    "costes_comerciales_pct": ("costes comerciales (fracción de las ventas)", 0.15),
    "inversion_lanzamiento_eur": ("inversión de lanzamiento (€, al llegar al mercado)", 0.0),
    "impuesto_beneficios": ("impuesto sobre beneficios (fracción)", 0.0),
    "erosion_precio_anual": ("erosión anual del precio (fracción)", 0.0),
    "coste_renovacion_eur": ("coste de renovación de la autorización (€)", 0.0),
    "anio_renovacion": ("año de la renovación (desde la llegada al mercado)", None),
    "multiplo_valor_terminal": ("valor terminal (múltiplo del beneficio del último año)", 0.0),
}
# Valores por defecto que no son neutros: con --exigir-fuentes deben declararse con fuente.
_ASSUMED_DEFAULTS = ("anios_hasta_pico", "anios_comercializacion", "margen_bruto", "costes_comerciales_pct")
# Rango admitido de cada entrada (mín, máx, máx incluido): se comprueba en todas las simulaciones.
_RANGES = {
    "censo_direccionable": (0.0, math.inf, True), "adopcion_pico": (0.0, 1.0, True),
    "ciclos_por_animal_anio": (0.0, math.inf, True), "dias_por_ciclo": (0.0, math.inf, True),
    "dosis_g_dia": (0.0, math.inf, True), "precio_eur_kg": (0.0, math.inf, True),
    "margen_distribucion": (0.0, 1.0, False), "anios_hasta_pico": (0.0, math.inf, True),
    "anios_comercializacion": (0.0, 200.0, True), "margen_bruto": (0.0, 1.0, True),
    "costes_comerciales_pct": (0.0, 1.0, True), "inversion_lanzamiento_eur": (0.0, math.inf, True),
    "impuesto_beneficios": (0.0, 1.0, False), "erosion_precio_anual": (0.0, 1.0, False),
    "coste_renovacion_eur": (0.0, math.inf, True), "anio_renovacion": (0.0, math.inf, True),
    "ventas_pico_eur_anio": (0.0, math.inf, True), "multiplo_valor_terminal": (0.0, math.inf, True),
}
_PCTS = (5, 10, 25, 50, 75, 90, 95)
DEFAULT_LOSS_FRACTION = 0.5


@dataclass
class Phase:
    name: str
    cost: Dist
    years: Dist
    p_success: Dist


@dataclass
class Project:
    name: str
    discount: Dist
    phases: list[Phase]
    market: dict[str, Dist]
    built_sales: bool
    assumptions: list[str] = field(default_factory=list)
    loss_threshold: float | None = None
    species: str = ""
    declared: set[str] = field(default_factory=set)
    central: str = "moda"  # estadístico central de TODAS las entradas: "moda" o "mediana"

    @property
    def peak_sales(self) -> Dist | None:
        return self.market.get("ventas_pico_eur_anio")


def _check_keys(where: str, table, allowed: set[str]) -> None:
    if not isinstance(table, dict):
        raise ValueError(f"{where} debe ser una tabla")
    unknown = set(table) - allowed
    if unknown:
        raise ValueError(f"{where}: claves desconocidas {sorted(unknown)}. Admitidas: {sorted(allowed)}")


def load_project(path: Path, central: str = "moda") -> Project:
    from .farm import assumptions_list

    check_central(central)
    data = tomllib.loads(Path(path).read_text("utf-8"))
    unknown = set(data) - _SECTIONS
    if unknown:
        raise ValueError(f"Secciones desconocidas en el modelo de la empresa: {sorted(unknown)}. Admitidas: "
                         f"{sorted(_SECTIONS)}")
    proj = data.get("proyecto", {})
    market = data.get("mercado", {})
    _check_keys("[proyecto]", proj, _PROYECTO_KEYS)
    _check_keys("[mercado]", market, set(MARKET) | {"especie", "nota"})
    _check_keys("[supuestos]", data.get("supuestos", {}), {"lista"})
    declared: set[str] = set()
    phases = []
    for i, f in enumerate(data.get("fase", []), 1):
        _check_keys(f"[[fase]] n.º {i}", f, _FASE_KEYS)
        for key in ("nombre", "coste_eur", "duracion_anios", "prob_exito"):
            if key not in f:
                raise ValueError(f"[[fase]] n.º {i}: falta '{key}'")
        phases.append(Phase(f["nombre"], parse_dist(f["coste_eur"]), parse_dist(f["duracion_anios"]),
                            parse_dist(f["prob_exito"])))
        declared.update({f"fase[{i}].coste_eur", f"fase[{i}].duracion_anios", f"fase[{i}].prob_exito"})
    if not phases:
        raise ValueError("El proyecto necesita al menos una [[fase]]")
    built = [k for k in _BUILT if k in market]
    if "ventas_pico_eur_anio" in market and built:
        raise ValueError("[mercado]: declara las ventas de una sola forma: ventas_pico_eur_anio (dato) o construidas "
                         f"({' × '.join(_BUILT)}); ahora hay ambas ({', '.join(built)})")
    if "ventas_pico_eur_anio" not in market and len(built) < len(_BUILT):
        missing = [k for k in _BUILT if k not in market]
        raise ValueError("[mercado]: las ventas construidas necesitan " + ", ".join(_BUILT) + "; faltan "
                         + ", ".join(missing) + " (o declara ventas_pico_eur_anio como dato)")
    built_sales = "ventas_pico_eur_anio" not in market
    if ("coste_renovacion_eur" in market) != ("anio_renovacion" in market):
        raise ValueError("[mercado]: coste_renovacion_eur y anio_renovacion van juntos")
    if not built_sales:
        for key in ("ciclos_por_animal_anio", "margen_distribucion"):
            if key in market:
                raise ValueError(f"[mercado].{key} solo se usa con las ventas construidas")
    values: dict[str, Dist] = {}
    for key, (_label, default) in MARKET.items():
        if key in market:
            values[key] = parse_dist(market[key])
            declared.add(f"mercado.{key}")
        elif default is not None:
            if key in ("ciclos_por_animal_anio", "margen_distribucion") and not built_sales:
                continue
            values[key] = parse_dist(default)
    if "tasa_descuento" in proj:
        declared.add("proyecto.tasa_descuento")
    threshold = proj.get("umbral_perdida_eur")
    if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                                  or not math.isfinite(threshold) or not threshold >= 0):
        raise ValueError("[proyecto].umbral_perdida_eur debe ser un número finito >= 0 (€)")
    return Project(
        name=proj.get("nombre", Path(path).stem), discount=parse_dist(proj.get("tasa_descuento", 0.10)),
        phases=phases, market=values, built_sales=built_sales, assumptions=assumptions_list(data),
        loss_threshold=None if threshold is None else float(threshold), species=str(market.get("especie", "")),
        declared=declared, central=central,
    )


# ---------------------------------------------------------------------------------------------- entradas
def inputs(project: Project) -> list[tuple[str, str, Dist]]:
    out = [("proyecto.tasa_descuento", "tasa de descuento", project.discount)]
    for i, ph in enumerate(project.phases, 1):
        out += [(f"fase[{i}].coste_eur", f"coste de '{ph.name}' (€)", ph.cost),
                (f"fase[{i}].duracion_anios", f"duración de '{ph.name}' (años)", ph.years),
                (f"fase[{i}].prob_exito", f"probabilidad de éxito de '{ph.name}'", ph.p_success)]
    for key, (label, _default) in MARKET.items():
        if key in project.market:
            out.append((f"mercado.{key}", label, project.market[key]))
    return out


def input_table(project: Project) -> list[dict]:
    return [{"entrada": key, "descripcion": label, "distribucion": d.describe(), "central": d.central(project.central),
             "estadistico_central": project.central, "p10": d.quantile(0.10), "p90": d.quantile(0.90),
             "fuente": d.source, "declarada": key in project.declared} for key, label, d in inputs(project)]


def declared_sources(project: Project) -> list[tuple[str, str]]:
    """(dónde, fuente) de cada fuente declarada (para comprobarlas en el registro)."""
    return [(r["entrada"], r["fuente"]) for r in input_table(project) if r["fuente"]]


def sources_overview(project: Project) -> dict:
    """Recuento de fuentes por etiqueta y entradas sin datos ([SUP], [DESC], sin fuente o por defecto) entre las que
    más mueven el rNPV en el tornado. El modelo no trae valores de referencia de plazo ni de coste de la autorización:
    si son [SUP], aparecen aquí cuando pesan en el resultado."""
    return sources_summary(input_table(project), sensitivity(project))


def missing_sources(project: Project) -> list[str]:
    """Entradas declaradas sin fuente etiquetada y valores por defecto no neutros sin declarar (la tasa de descuento,
    los años, los márgenes y, con ventas construidas, los ciclos por año y el margen de la distribución)."""
    assumed = {"proyecto.tasa_descuento", *(f"mercado.{k}" for k in _ASSUMED_DEFAULTS)}
    if project.built_sales:
        assumed |= {"mercado.ciclos_por_animal_anio", "mercado.margen_distribucion"}
    out = []
    for row in input_table(project):
        if row["declarada"] and not source_has_label(row["fuente"]):
            out.append(row["entrada"])
        elif not row["declarada"] and row["entrada"] in assumed:
            out.append(row["entrada"] + " (valor por defecto sin declarar)")
    return out


# ---------------------------------------------------------------------------------------------- cálculo
def _central_values(project: Project) -> dict:
    return {key: d.central(project.central) for key, _label, d in inputs(project)}


def _sample(project: Project, rng: np.random.Generator, n: int) -> dict:
    return {key: d.sample(rng, n) for key, _label, d in inputs(project)}


def _check_ranges(project: Project, v: dict) -> None:
    for key, (lo, hi, hi_closed) in _RANGES.items():
        name = f"mercado.{key}"
        if name not in v:
            continue
        x = np.atleast_1d(np.asarray(v[name], dtype=float))
        bad = ~np.isfinite(x) | (x < lo) | ((x > hi) if hi_closed else (x >= hi))
        if np.any(bad):
            interval = f"[{lo:g}, {hi:g}{']' if hi_closed else ')'}"
            raise ValueError(f"{name} fuera de {interval} en {int(bad.sum())} de {x.size} valores: usa una "
                             "distribución acotada (pert, triangular, uniforme)")
    r = np.atleast_1d(np.asarray(v["proyecto.tasa_descuento"], dtype=float))
    if np.any(~np.isfinite(r) | (r < 0) | (r >= 1)):
        raise ValueError("La tasa de descuento debe estar en [0, 1)")
    for i, _ph in enumerate(project.phases, 1):
        for part in ("coste_eur", "duracion_anios"):
            x = np.atleast_1d(np.asarray(v[f"fase[{i}].{part}"], dtype=float))
            if np.any(~np.isfinite(x) | (x < 0)):
                raise ValueError(f"fase[{i}].{part} debe ser >= 0 en todas las simulaciones")
        p = np.atleast_1d(np.asarray(v[f"fase[{i}].prob_exito"], dtype=float))
        bad = ~np.isfinite(p) | (p < 0) | (p > 1)
        if np.any(bad):
            raise ValueError(f"fase[{i}].prob_exito fuera de [0, 1] en {int(bad.sum())} de {p.size} valores: usa una "
                             "distribución acotada (pert, triangular, uniforme) en lugar de recortarla")


def peak_sales(project: Project, v: dict):
    """Ventas pico (€/año): construidas (censo × adopción × ciclos × días × dosis × precio × (1 − margen de la
    distribución)) o el dato declarado."""
    if project.built_sales:
        return (v["mercado.censo_direccionable"] * v["mercado.adopcion_pico"] * v["mercado.ciclos_por_animal_anio"]
                * v["mercado.dias_por_ciclo"] * v["mercado.dosis_g_dia"] / 1000.0 * v["mercado.precio_eur_kg"]
                * (1.0 - v["mercado.margen_distribucion"]))
    return v["mercado.ventas_pico_eur_anio"]


def _commercial_npv(project: Project, v: dict, t):
    """VPN de los flujos comerciales si se llega al mercado en el instante t (años); admite arrays."""
    r = v["proyecto.tasa_descuento"]
    peak = peak_sales(project, v)
    margin = v["mercado.margen_bruto"] - v["mercado.costes_comerciales_pct"]
    ytp = np.asarray(v["mercado.anios_hasta_pico"], dtype=float)
    yom = np.asarray(v["mercado.anios_comercializacion"], dtype=float)
    erosion, tax = v["mercado.erosion_precio_anual"], v["mercado.impuesto_beneficios"]
    total = 0.0
    for year in range(int(math.ceil(float(np.max(yom))))):
        fraction = np.clip(yom - year, 0.0, 1.0)
        ramp = np.where(ytp > 0, np.minimum(1.0, (year + 1) / np.where(ytp > 0, ytp, 1.0)), 1.0)
        profit = peak * ramp * (1.0 - erosion) ** year * margin
        profit = profit - tax * np.maximum(profit, 0.0)
        total = total + profit * fraction / (1 + r) ** (t + year + 0.5)
    # Valor terminal: múltiplo del beneficio (tras impuestos) del último año, al final de la comercialización.
    last = np.maximum(np.ceil(yom) - 1, 0)
    ramp = np.where(ytp > 0, np.minimum(1.0, (last + 1) / np.where(ytp > 0, ytp, 1.0)), 1.0)
    profit = peak * ramp * (1.0 - erosion) ** last * margin
    profit = profit - tax * np.maximum(profit, 0.0)
    total = total + np.where(yom > 0, v["mercado.multiplo_valor_terminal"] * profit / (1 + r) ** (t + yom), 0.0)
    total = total - v["mercado.inversion_lanzamiento_eur"] / (1 + r) ** t
    if "mercado.anio_renovacion" in v:
        ry = v["mercado.anio_renovacion"]
        total = total - np.where(yom > ry, v["mercado.coste_renovacion_eur"] / (1 + r) ** (t + ry), 0.0)
    return total


def _rnpv(project: Project, v: dict):
    """rNPV (valor esperado sobre el riesgo de las fases) con unos parámetros dados; admite escalares o arrays.
    Devuelve (rNPV, probabilidad acumulada de éxito, años hasta el mercado)."""
    r = v["proyecto.tasa_descuento"]
    t = 0.0
    p_cum = 1.0
    npv = 0.0
    for i, _phase in enumerate(project.phases, 1):
        cost, years = v[f"fase[{i}].coste_eur"], v[f"fase[{i}].duracion_anios"]
        p = np.clip(v[f"fase[{i}].prob_exito"], 0.0, 1.0)
        npv = npv - p_cum * cost / (1 + r) ** (t + years / 2)
        t = t + years
        p_cum = p_cum * p
    npv = npv + p_cum * _commercial_npv(project, v, t)
    return npv, p_cum, t


def _realized_npv(project: Project, v: dict, u: np.ndarray):
    """VPN realizado: la fase i tiene éxito si u[:, i] < p_i (Bernoulli en secuencia). Se pagan las fases
    alcanzadas (también la que fracasa) y los flujos comerciales solo entran si todas tienen éxito.
    Devuelve (VPN realizado, llega al mercado, índice de la fase que fracasa o n.º de fases si llega)."""
    r = v["proyecto.tasa_descuento"]
    n = u.shape[0]
    t = np.zeros(n)
    reached = np.ones(n, dtype=bool)
    npv = np.zeros(n)
    stage = np.full(n, len(project.phases))
    for i, _phase in enumerate(project.phases):
        cost, years = v[f"fase[{i + 1}].coste_eur"], v[f"fase[{i + 1}].duracion_anios"]
        p = np.clip(v[f"fase[{i + 1}].prob_exito"], 0.0, 1.0)
        npv = npv - np.where(reached, cost / (1 + r) ** (t + years / 2), 0.0)
        t = t + years
        success = u[:, i] < p
        stage = np.where(reached & ~success, i, stage)
        reached = reached & success
    npv = npv + np.where(reached, _commercial_npv(project, v, t), 0.0)
    return npv, reached, stage


# Versión del orden de muestreo del Monte Carlo de la empresa. Con la misma semilla y el mismo TOML, otra versión da
# otras cifras (dentro del error de Monte Carlo): la salida y la procedencia la guardan junto a la semilla.
# 1: hasta 7a3279f. 2: muestreo con correlaciones entre entradas (orden de las entradas de `inputs`) y después las
#    fases (revisión final de P6: las cifras de la versión 1 no se reproducen con la 2).
SAMPLING_VERSION = 2


def simulate(project: Project, n: int = 10000, seed: int = 42) -> dict:
    """Monte Carlo con los mismos parámetros muestreados para las dos métricas.

    Devuelve arrays de longitud n: 'rnpv' (valor esperado con los parámetros de cada simulación),
    'vpn_realizado' (además, éxito o fracaso de cada fase muestreado en secuencia), 'llega_al_mercado'
    (todas las fases con éxito), 'fase_fracaso' (índice de la fase que fracasa; n.º de fases si llega) y
    'ventas_pico'."""
    if int(n) < 1:
        raise ValueError("El número de simulaciones debe ser >= 1")
    rng = np.random.default_rng(seed)
    samples = _sample(project, rng, n)
    _check_ranges(project, samples)
    rnpv_sims = np.asarray(_rnpv(project, samples)[0], dtype=float)
    u = rng.random((n, len(project.phases)))
    realized, market, stage = _realized_npv(project, samples, u)
    return {"rnpv": rnpv_sims, "vpn_realizado": realized, "llega_al_mercado": market, "fase_fracaso": stage,
            "ventas_pico": np.asarray(peak_sales(project, samples), dtype=float) * np.ones(n)}


def development_cost_central(project: Project) -> float:
    return float(sum(ph.cost.central(project.central) for ph in project.phases))


def evaluate(project: Project, n: int = 10000, seed: int = 42, loss_threshold: float | None = None) -> dict:
    central = _central_values(project)
    _check_ranges(project, central)
    npv, p_success, ttm = _rnpv(project, central)
    sims = simulate(project, n, seed)
    rn, realized, stage = sims["rnpv"], sims["vpn_realizado"], sims["fase_fracaso"]
    if loss_threshold is None:
        loss_threshold = project.loss_threshold
    if loss_threshold is not None:
        if not (math.isfinite(loss_threshold) and loss_threshold >= 0):
            raise ValueError("El umbral de pérdida debe ser un número finito >= 0 (€)")
        origin = "declarado"
    else:
        loss_threshold = DEFAULT_LOSS_FRACTION * development_cost_central(project)
        origin = (f"por defecto: {DEFAULT_LOSS_FRACTION:.0%} del coste central de desarrollo sin descontar "
                  f"({development_cost_central(project):,.0f} €)")
    outcomes = []
    for i, ph in enumerate(project.phases):
        mask = stage == i
        outcomes.append({"resultado": f"fracasa en '{ph.name}'", "probabilidad": float(np.mean(mask)),
                         "vpn_medio": float(np.mean(realized[mask])) if mask.any() else None})
    mask = stage == len(project.phases)
    market_row = {"resultado": "llega al mercado", "probabilidad": float(np.mean(mask)),
                  "vpn_medio": float(np.mean(realized[mask])) if mask.any() else None}
    if mask.any():
        market_row.update({f"p{q:02d}": float(np.percentile(realized[mask], q)) for q in (5, 50, 95)})
    outcomes.append(market_row)
    return {
        "rnpv_central": float(npv), "prob_exito_acumulada": float(p_success), "anios_hasta_mercado": float(ttm),
        "ventas_pico_central": float(peak_sales(project, central)), "ventas_construidas": project.built_sales,
        "coste_desarrollo_central": development_cost_central(project), "estadistico_central": project.central,
        # Incertidumbre de los parámetros: distribución del rNPV (un valor esperado por simulación).
        "rnpv_p05": float(np.percentile(rn, 5)), "rnpv_p50": float(np.percentile(rn, 50)),
        "rnpv_p95": float(np.percentile(rn, 95)),
        "p_rnpv_positivo_incertidumbre_parametros": float(np.mean(rn > 0)),
        # Simulación de resultados: VPN que la empresa obtendría en cada simulación.
        "vpn_esperado": float(np.mean(realized)),
        "vpn_realizado_p05": float(np.percentile(realized, 5)),
        "vpn_realizado_p50": float(np.percentile(realized, 50)),
        "vpn_realizado_p95": float(np.percentile(realized, 95)),
        "vpn_realizado_percentiles": {f"p{q:02d}": float(np.percentile(realized, q)) for q in _PCTS},
        "p_vpn_realizado_positivo": float(np.mean(realized > 0)),
        "p_vpn_realizado_negativo": float(np.mean(realized < 0)),
        "umbral_perdida_eur": float(loss_threshold), "umbral_perdida_origen": origin,
        "p_perdida_mayor_umbral": float(np.mean(realized < -loss_threshold)),
        "resultados_por_fase": outcomes,
        "p_llega_al_mercado_simulada": float(np.mean(sims["llega_al_mercado"])),
        "simulaciones": n, "semilla": seed, "version_muestreo": SAMPLING_VERSION,
    }


def sensitivity(project: Project) -> list[dict]:
    """Tornado del rNPV central: cada entrada con incertidumbre en su P10 y su P90 con las demás en su valor central;
    si la tasa de descuento es fija, se evalúa además con ±2 puntos."""
    base = _central_values(project)
    rows = []
    for key, label, d in inputs(project):
        if d.stochastic:
            lo, hi = d.quantile(0.10), d.quantile(0.90)
            kind = "P10-P90"
        elif key == "proyecto.tasa_descuento":
            lo, hi = max(0.0, d.central(project.central) - 0.02), d.central(project.central) + 0.02
            kind = "±2 puntos"
        else:
            continue
        try:
            values = []
            for x in (lo, hi):
                v = {**base, key: x}
                _check_ranges(project, v)
                values.append(float(_rnpv(project, v)[0]))
        except ValueError as exc:
            rows.append({"entrada": key, "descripcion": label, "variacion": kind, "bajo": lo, "alto": hi,
                         "rnpv_bajo": None, "rnpv_alto": None, "amplitud": None, "error": str(exc)})
            continue
        rows.append({"entrada": key, "descripcion": label, "variacion": kind, "bajo": lo, "alto": hi,
                     "rnpv_bajo": values[0], "rnpv_alto": values[1], "amplitud": abs(values[1] - values[0])})
    rows.sort(key=lambda r: -(r["amplitud"] if r["amplitud"] is not None else math.inf))
    return rows


# ---------------------------------------------------------------------------------------------- coherencia
def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


# Tolerancia mínima al comparar P5 y P95: 1 % si se calculan con la inversa de la distribución; 2 % si el P5-P95 del
# ganadero sale de una simulación (dosis convertida con ingestiones inciertas), por el error de Monte Carlo.
RANGE_TOL = 0.01
RANGE_TOL_SIMULATED = 0.02
# Tolerancia relativa máxima de `econ coherencia --tolerancia`: con más, la comprobación dejaría de comprobar (con 2,
# un precio, una dosis o unos días el doble que los del ganadero salían «COHERENTE»).
MAX_RTOL = 0.05
_COHERENCE_SIMS = 20000
_COHERENCE_SEED = 20240501


def _difference(farm: float, proj: float) -> str:
    """Diferencia de la empresa respecto al valor del ganadero, en % de este (o absoluta si el del ganadero es 0)."""
    if farm == 0:
        return f"diferencia {proj - farm:+.6g} (el valor del ganadero es 0)"
    return f"diferencia {100 * (proj - farm) / abs(farm):+.2f} % del valor del ganadero"


def _differs(farm: float, proj: float, rtol: float) -> bool:
    return abs(proj - farm) > rtol * abs(farm) if farm != 0 else abs(proj - farm) > 1e-12


def _compare(name: str, farm_c: float, proj_c: float, farm_q, proj_q, rtol: float,
             range_tol: float = RANGE_TOL) -> dict:
    item = {"magnitud": name, "ganadero": farm_c, "empresa": proj_c,
            "ganadero_p05_p95": farm_q, "empresa_p05_p95": proj_q,
            "diferencia_pct": (100 * (proj_c - farm_c) / abs(farm_c)) if farm_c != 0 else None}
    if _differs(farm_c, proj_c, rtol):
        item["estado"] = "difiere"
        item["detalle"] = (f"valor central {farm_c:.6g} en el modelo del ganadero y {proj_c:.6g} en el de la empresa "
                           f"({_difference(farm_c, proj_c)})")
        return item
    central = ("mismo valor central" if proj_c == farm_c else
               f"valor central {farm_c:.6g} (ganadero) y {proj_c:.6g} (empresa), {_difference(farm_c, proj_c)}, "
               f"dentro de la tolerancia")
    if farm_q is not None and proj_q is not None and any(
            _differs(a, b, max(rtol, range_tol)) for a, b in zip(farm_q, proj_q, strict=True)):
        item["estado"] = "difiere"
        item["detalle"] = (f"{central} pero distinta incertidumbre: P5-P95 {farm_q[0]:.6g}-{farm_q[1]:.6g} "
                           f"(ganadero) frente a {proj_q[0]:.6g}-{proj_q[1]:.6g} (empresa)")
    else:
        item["estado"] = "coherente"
        item["detalle"] = central + ("" if farm_q is None or proj_q is None else
                                     (" y mismo P5-P95" if tuple(farm_q) == tuple(proj_q) else
                                      f" y P5-P95 {farm_q[0]:.6g}-{farm_q[1]:.6g} (ganadero) y "
                                      f"{proj_q[0]:.6g}-{proj_q[1]:.6g} (empresa), dentro de la tolerancia"))
    return item


def coherence(model, project: Project, rtol: float = 1e-3) -> dict:
    """Compara el precio, la dosis (g/animal/día) y los días de uso del modelo del ganadero (`farm.FarmModel`) con los
    del modelo de la empresa: valor central (con el mismo estadístico en los dos modelos) y P5-P95. Si la dosis del
    ganadero está en mg/kg de MS o en g/t, se convierte a g/animal/día con sus ingestiones: el valor central con los
    valores centrales y el P5-P95 con una simulación. Con ventas_pico_eur_anio como dato no es comprobable: falla y da
    los animales-ciclo tratados al año que implicaría con el precio, la dosis y los días del ganadero."""
    from . import farm  # import local: farm no depende de rnpv

    if not (math.isfinite(rtol) and 0 <= rtol <= MAX_RTOL):
        raise ValueError(f"La tolerancia debe ser un número finito entre 0 y {MAX_RTOL:g} ({100 * MAX_RTOL:g} %): con más, "
                         "la comprobación de coherencia dejaría de comprobar")
    if model.central != project.central:
        raise ValueError(f"Los dos modelos deben usar el mismo estadístico central ({model.central} frente a "
                         f"{project.central})")
    stat = project.central
    dose = farm.dose_summary(model)
    farm_days = model.cost_days if model.cost_days is not None else model.days
    farm_price = model.price_eur_kg
    items = []
    if not project.built_sales:
        per_animal = farm_price.central(stat) * dose["g_animal_dia"] / 1000.0 * farm_days.central(stat)
        implied = project.peak_sales.central(stat) / per_animal if per_animal > 0 else None
        items.append({"magnitud": "ventas", "estado": "no_comprobable", "ganadero": None, "empresa": None,
                      "detalle": "ventas_pico_eur_anio es un dato de entrada: no puede comprobarse que use el precio, "
                                 "la dosis y los días del ganadero. Constrúyelas con censo_direccionable × "
                                 "adopcion_pico × ciclos_por_animal_anio × dias_por_ciclo × dosis_g_dia × "
                                 "precio_eur_kg."
                                 + (f" Con los valores centrales del ganadero ({per_animal:.4g} € por animal y ciclo) "
                                    f"equivalen a {implied:,.0f} animales-ciclo tratados al año, a precio de ganadero "
                                    "y sin margen de la distribución." if implied is not None else
                                    " El coste del producto por animal y ciclo del ganadero no es positivo: no se "
                                    "pueden deducir los animales tratados."),
                      "animales_ciclo_implicitos": implied})
    else:
        m = project.market

        def q(d: Dist) -> tuple[float, float]:
            return (d.quantile(0.05), d.quantile(0.95))

        items.append(_compare("precio que paga el ganadero (€/kg)", farm_price.central(stat),
                              m["precio_eur_kg"].central(stat), q(farm_price), q(m["precio_eur_kg"]), rtol))
        if model.dose_basis == "g_animal_dia":
            dose_item = _compare("dosis (g/animal/día)", dose["g_animal_dia"], m["dosis_g_dia"].central(stat),
                                 q(model.dose), q(m["dosis_g_dia"]), rtol)
        else:
            farm_dose_q = farm.dose_g_day_quantiles(model, (5, 95), _COHERENCE_SIMS, _COHERENCE_SEED)
            dose_item = _compare("dosis (g/animal/día)", dose["g_animal_dia"], m["dosis_g_dia"].central(stat),
                                 farm_dose_q, q(m["dosis_g_dia"]), rtol, RANGE_TOL_SIMULATED)
            dose_item["detalle"] += (f" (la dosis del ganadero está en {model.dose_basis} y se convierte a "
                                     "g/animal/día con sus ingestiones: el valor central con los valores centrales y "
                                     f"el P5-P95 con {_COHERENCE_SIMS} simulaciones, con una tolerancia de "
                                     f"{100 * max(rtol, RANGE_TOL_SIMULATED):g} %)")
        items.append(dose_item)
        items.append(_compare("días de uso por ciclo", farm_days.central(stat), m["dias_por_ciclo"].central(stat),
                              q(farm_days), q(m["dias_por_ciclo"]), rtol))
    if project.species and model.species and _norm(project.species) != _norm(model.species):
        items.append({"magnitud": "especie", "estado": "difiere", "ganadero": model.species,
                      "empresa": project.species,
                      "detalle": f"'{model.species}' en el modelo del ganadero y '{project.species}' en el de la "
                                 "empresa"})
    return {"coherente": all(i["estado"] == "coherente" for i in items), "comparaciones": items,
            "tolerancia_relativa": rtol, "estadistico_central": stat}


def coherence_text(res: dict) -> str:
    head = ("Coherencia ganadero-empresa: COHERENTE (precio, dosis y días coinciden)" if res["coherente"] else
            "AVISO DE COHERENCIA ganadero-empresa: los dos modelos no usan el mismo precio, dosis y días (o no puede "
            "comprobarse); los criterios rentabilidad_ganadero y atractivo_empresa se apoyarían en supuestos "
            "incompatibles")
    lines = [head, f"(tolerancia relativa {res['tolerancia_relativa']:g}; valores centrales: "
                   f"{res.get('estadistico_central', 'moda')})"]
    for item in res["comparaciones"]:
        lines.append(f"- {item['magnitud']}: {item['estado'].upper()}: {item['detalle']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------- informe
def _sources_lines(project: Project) -> list[str]:
    lines = ["Entradas y fuentes:"]
    for r in input_table(project):
        origin = r["fuente"] if r["fuente"] else ("SIN FUENTE (decláralo o márcalo [SUP])" if r["declarada"]
                                                   else "valor por defecto (no declarado)")
        lines.append(f"- {r['entrada']}: {r['distribucion']} | {origin}")
    missing = missing_sources(project)
    if missing:
        lines.append(f"AVISO: {len(missing)} entradas sin fuente etiquetada ([PUB], [DER], [SUP], [DESC]) o con valor "
                     "por defecto no neutro sin declarar: " + ", ".join(missing) + ". `--exigir-fuentes` las rechaza.")
    lines.append(sources_summary_line(sources_overview(project), "el rNPV"))
    lines.append("El modelo no trae valores de referencia de coste ni de plazo de las fases (incluida la "
                 "autorización): cada uno va con su fuente o como [SUP].")
    return lines


def _sensitivity_lines(project: Project, top: int = 10) -> list[str]:
    rows = sensitivity(project)
    lines = ["3) Sensibilidad (tornado) del rNPV central: cada entrada en su P10 y su P90 (la tasa de descuento fija, "
             "±2 puntos) con las demás en su valor central",
             f"   {'Entrada':<40}{'Bajo':>12}{'Alto':>12}{'rNPV bajo':>14}{'rNPV alto':>14}"]
    for r in rows[:top]:
        if r["amplitud"] is None:
            lines.append(f"   {r['entrada']:<40}{r['bajo']:>12.4g}{r['alto']:>12.4g}  no evaluable: {r['error']}")
            continue
        lines.append(f"   {r['entrada']:<40}{r['bajo']:>12.4g}{r['alto']:>12.4g}{r['rnpv_bajo']:>14,.0f}"
                     f"{r['rnpv_alto']:>14,.0f}")
    if len(rows) > top:
        lines.append(f"   ... y {len(rows) - top} entradas más con menor amplitud (salida JSON: 'sensibilidad')")
    return lines


def report(project: Project, n: int = 10000, seed: int = 42, loss_threshold: float | None = None) -> str:
    res = evaluate(project, n, seed, loss_threshold)
    r = project.discount
    rate = (f"{r.central(project.central):.0%}" if r.fixed is not None
            else f"{r.central(project.central):.0%} central, {r.kind}")
    if project.built_sales:
        sales = ("Ventas pico (central): {:,.0f} €/año, construidas: censo direccionable × adopción en el pico × "
                 "ciclos por animal y año × días por ciclo × dosis × precio del ganadero × (1 − margen de la "
                 "distribución). Comprueba la coherencia con el modelo del ganadero: `nutria econ coherencia`."
                 ).format(res["ventas_pico_central"])
    else:
        sales = ("Ventas pico (central): {:,.0f} €/año como DATO de entrada: no puede comprobarse que usen el precio, "
                 "la dosis y los días del modelo del ganadero; constrúyelas (censo × adopción × dosis × días × "
                 "precio) y usa `nutria econ coherencia`.").format(res["ventas_pico_central"])
    pct = res["vpn_realizado_percentiles"]
    lines = [f"Proyecto: {project.name} (tasa de descuento {rate})",
             "Probabilidad acumulada de éxito técnico-regulatorio (valores centrales): "
             f"{res['prob_exito_acumulada']:.2f}",
             f"Años hasta el mercado (central): {res['anios_hasta_mercado']:.1f}",
             sales,
             f"rNPV central: {res['rnpv_central']:,.0f} € (valor esperado del VPN con el riesgo de cada fase y "
             f"los valores centrales de los parámetros: {CENTRAL_TEXT[project.central]})",
             "",
             f"1) Incertidumbre de los parámetros ({n} simulaciones, semilla {seed}, versión del muestreo "
             f"{res.get('version_muestreo', SAMPLING_VERSION)}): rNPV P50 "
             f"{res['rnpv_p50']:,.0f} € [P5 {res['rnpv_p05']:,.0f}; P95 {res['rnpv_p95']:,.0f}]; "
             f"P(rNPV > 0) = {res['p_rnpv_positivo_incertidumbre_parametros']:.2f}",
             "   Mide la confianza en que el valor esperado (rNPV) sea positivo si los parámetros toman otros "
             "valores plausibles. No es la probabilidad de que el proyecto gane dinero: para eso, mira la "
             "simulación de resultados (2).",
             "",
             "2) Simulación de resultados (mismas simulaciones; éxito o fracaso de cada fase muestreado en "
             f"secuencia): VPN esperado {res['vpn_esperado']:,.0f} €; VPN realizado P50 "
             f"{res['vpn_realizado_p50']:,.0f} € [P5 {res['vpn_realizado_p05']:,.0f}; "
             f"P95 {res['vpn_realizado_p95']:,.0f}]; P(VPN realizado > 0) = {res['p_vpn_realizado_positivo']:.2f}; "
             f"fracción que llega al mercado = {res['p_llega_al_mercado_simulada']:.2f}",
             "   Distribución del VPN realizado (incluye los fracasos): " + "; ".join(
                 f"P{q[1:]} {v:,.0f} €" for q, v in pct.items()),
             f"   P(VPN realizado < 0) = {res['p_vpn_realizado_negativo']:.2f}; P(pérdida > "
             f"{res['umbral_perdida_eur']:,.0f} €) = {res['p_perdida_mayor_umbral']:.2f} (umbral "
             f"{res['umbral_perdida_origen']}; cámbialo con [proyecto].umbral_perdida_eur o --umbral-perdida)",
             "   Por resultado: " + "; ".join(
                 f"{o['resultado']}: p = {o['probabilidad']:.2f}"
                 + (f", VPN medio {o['vpn_medio']:,.0f} €" if o["vpn_medio"] is not None else "")
                 for o in res["resultados_por_fase"]),
             "   Mide lo que la empresa obtendría de verdad: si una fase fracasa se pierden los costes ya "
             "incurridos y no hay ventas. P(VPN realizado > 0) es la probabilidad de terminar con VPN positivo; "
             "con costes positivos no puede superar la fracción que llega al mercado. Con probabilidades de "
             "éxito bajas lo más probable es perder lo invertido aunque el rNPV sea positivo.",
             "   El VPN esperado es la media del VPN realizado y coincide con la media del rNPV salvo error de "
             "Monte Carlo.",
             ""]
    lines += _sensitivity_lines(project)
    if project.assumptions:
        lines += ["", "Supuestos declarados:"] + [f"- {a}" for a in project.assumptions]
    lines += [""] + _sources_lines(project)
    return "\n".join(lines)
