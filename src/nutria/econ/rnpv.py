"""Perspectiva de la empresa: valor actual neto ajustado por riesgo (rNPV) del desarrollo.

Cada fase (computacional, in vitro, in vivo, dossier regulatorio...) tiene coste, duración y
probabilidad de éxito condicionada a haber llegado a ella. Los flujos comerciales solo se obtienen si
todas las fases tienen éxito.

evaluate() hace dos simulaciones de Monte Carlo con los mismos parámetros muestreados:
- incertidumbre de los parámetros: en cada simulación se calcula el rNPV (valor esperado del VPN sobre el
  riesgo de las fases) con los parámetros muestreados. p_rnpv_positivo_incertidumbre_parametros mide la
  confianza en que ese valor esperado sea positivo; no es la probabilidad de que el proyecto gane dinero.
- simulación de resultados: en cada simulación se muestrea además el éxito de cada fase (Bernoulli en
  secuencia). El VPN realizado descuenta los costes de las fases alcanzadas y suma los flujos comerciales
  solo si todas tienen éxito. Su media es vpn_esperado y p_vpn_realizado_positivo es la probabilidad de que
  el proyecto termine con VPN positivo.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .dist import Dist, parse_dist


@dataclass
class Phase:
    name: str
    cost: Dist
    years: Dist
    p_success: Dist


@dataclass
class Project:
    name: str
    discount: float
    phases: list[Phase]
    peak_sales: Dist
    years_to_peak: float
    years_on_market: int
    gross_margin: Dist
    commercial_cost: Dist
    assumptions: list[str] = field(default_factory=list)


def load_project(path: Path) -> Project:
    data = tomllib.loads(Path(path).read_text("utf-8"))
    proj = data.get("proyecto", {})
    market = data.get("mercado", {})
    phases = [Phase(f["nombre"], parse_dist(f["coste_eur"]), parse_dist(f["duracion_anios"]),
                    parse_dist(f["prob_exito"])) for f in data.get("fase", [])]
    if not phases:
        raise ValueError("El proyecto necesita al menos una [[fase]]")
    return Project(
        name=proj.get("nombre", Path(path).stem), discount=float(proj.get("tasa_descuento", 0.10)),
        phases=phases, peak_sales=parse_dist(market["ventas_pico_eur_anio"]),
        years_to_peak=float(market.get("anios_hasta_pico", 3)),
        years_on_market=int(market.get("anios_comercializacion", 10)),
        gross_margin=parse_dist(market.get("margen_bruto", 0.4)),
        commercial_cost=parse_dist(market.get("costes_comerciales_pct", 0.15)),
        assumptions=list(data.get("supuestos", {}).get("lista", [])),
    )


def _commercial_npv(project: Project, values: dict, t):
    """VPN de los flujos comerciales si se llega al mercado en el instante t (años); admite arrays."""
    r = project.discount
    margin = values["margin"] - values["commercial"]
    total = 0.0
    for year in range(project.years_on_market):
        ramp = min(1.0, (year + 1) / project.years_to_peak) if project.years_to_peak > 0 else 1.0
        total = total + values["peak"] * ramp * margin / (1 + r) ** (t + year + 0.5)
    return total


def _rnpv(project: Project, values: dict):
    """rNPV (valor esperado sobre el riesgo de las fases) con unos parámetros dados; admite escalares o arrays.
    Devuelve (rNPV, probabilidad acumulada de éxito, años hasta el mercado)."""
    r = project.discount
    t = 0.0
    p_cum = 1.0
    npv = 0.0
    for i, _phase in enumerate(project.phases):
        cost, years, p = values[f"c{i}"], values[f"y{i}"], np.clip(values[f"p{i}"], 0.0, 1.0)
        npv = npv - p_cum * cost / (1 + r) ** (t + years / 2)
        t = t + years
        p_cum = p_cum * p
    npv = npv + p_cum * _commercial_npv(project, values, t)
    return npv, p_cum, t


def _realized_npv(project: Project, values: dict, u: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """VPN realizado: la fase i tiene éxito si u[:, i] < p_i (Bernoulli en secuencia). Se pagan las fases
    alcanzadas (también la que fracasa) y los flujos comerciales solo entran si todas tienen éxito.
    Devuelve (VPN realizado, llega al mercado)."""
    r = project.discount
    n = u.shape[0]
    t = np.zeros(n)
    reached = np.ones(n, dtype=bool)
    npv = np.zeros(n)
    for i, _phase in enumerate(project.phases):
        cost, years, p = values[f"c{i}"], values[f"y{i}"], np.clip(values[f"p{i}"], 0.0, 1.0)
        npv = npv - np.where(reached, cost / (1 + r) ** (t + years / 2), 0.0)
        t = t + years
        reached = reached & (u[:, i] < p)
    npv = npv + np.where(reached, _commercial_npv(project, values, t), 0.0)
    return npv, reached


def _central_values(project: Project) -> dict:
    v = {"peak": project.peak_sales.central(), "margin": project.gross_margin.central(),
         "commercial": project.commercial_cost.central()}
    for i, ph in enumerate(project.phases):
        v.update({f"c{i}": ph.cost.central(), f"y{i}": ph.years.central(), f"p{i}": ph.p_success.central()})
    return v


def simulate(project: Project, n: int = 10000, seed: int = 42) -> dict:
    """Monte Carlo con los mismos parámetros muestreados para las dos métricas.

    Devuelve arrays de longitud n: 'rnpv' (valor esperado con los parámetros de cada simulación),
    'vpn_realizado' (además, éxito o fracaso de cada fase muestreado en secuencia) y 'llega_al_mercado'
    (todas las fases con éxito)."""
    if int(n) < 1:
        raise ValueError("El número de simulaciones debe ser >= 1")
    rng = np.random.default_rng(seed)
    samples = {"peak": project.peak_sales.sample(rng, n), "margin": project.gross_margin.sample(rng, n),
               "commercial": project.commercial_cost.sample(rng, n)}
    for i, ph in enumerate(project.phases):
        samples[f"c{i}"] = ph.cost.sample(rng, n)
        samples[f"y{i}"] = ph.years.sample(rng, n)
        samples[f"p{i}"] = ph.p_success.sample(rng, n)
    rnpv_sims = np.asarray(_rnpv(project, samples)[0], dtype=float)
    u = rng.random((n, len(project.phases)))
    realized, market = _realized_npv(project, samples, u)
    return {"rnpv": rnpv_sims, "vpn_realizado": realized, "llega_al_mercado": market}


def evaluate(project: Project, n: int = 10000, seed: int = 42) -> dict:
    npv, p_success, ttm = _rnpv(project, _central_values(project))
    sims = simulate(project, n, seed)
    rn, realized = sims["rnpv"], sims["vpn_realizado"]
    return {
        "rnpv_central": float(npv), "prob_exito_acumulada": float(p_success), "anios_hasta_mercado": float(ttm),
        # Incertidumbre de los parámetros: distribución del rNPV (un valor esperado por simulación).
        "rnpv_p05": float(np.percentile(rn, 5)), "rnpv_p50": float(np.percentile(rn, 50)),
        "rnpv_p95": float(np.percentile(rn, 95)),
        "p_rnpv_positivo_incertidumbre_parametros": float(np.mean(rn > 0)),
        # Simulación de resultados: VPN que la empresa obtendría en cada simulación.
        "vpn_esperado": float(np.mean(realized)),
        "vpn_realizado_p05": float(np.percentile(realized, 5)),
        "vpn_realizado_p50": float(np.percentile(realized, 50)),
        "vpn_realizado_p95": float(np.percentile(realized, 95)),
        "p_vpn_realizado_positivo": float(np.mean(realized > 0)),
        "p_llega_al_mercado_simulada": float(np.mean(sims["llega_al_mercado"])),
        "simulaciones": n, "semilla": seed,
    }


def report(project: Project, n: int = 10000, seed: int = 42) -> str:
    res = evaluate(project, n, seed)
    lines = [f"Proyecto: {project.name} (tasa de descuento {project.discount:.0%})",
             "Probabilidad acumulada de éxito técnico-regulatorio (valores centrales): "
             f"{res['prob_exito_acumulada']:.2f}",
             f"Años hasta el mercado (central): {res['anios_hasta_mercado']:.1f}",
             f"rNPV central: {res['rnpv_central']:,.0f} € (valor esperado del VPN con el riesgo de cada fase y "
             "los valores centrales de los parámetros)",
             "",
             f"1) Incertidumbre de los parámetros ({n} simulaciones, semilla {seed}): rNPV P50 "
             f"{res['rnpv_p50']:,.0f} € [P5 {res['rnpv_p05']:,.0f}; P95 {res['rnpv_p95']:,.0f}]; "
             f"P(rNPV > 0) = {res['p_rnpv_positivo_incertidumbre_parametros']:.2f}",
             "   Mide la confianza en que el valor esperado (rNPV) sea positivo si los parámetros toman otros "
             "valores plausibles. No es la probabilidad de que el proyecto gane dinero.",
             "",
             "2) Simulación de resultados (mismas simulaciones; éxito o fracaso de cada fase muestreado en "
             f"secuencia): VPN esperado {res['vpn_esperado']:,.0f} €; VPN realizado P50 "
             f"{res['vpn_realizado_p50']:,.0f} € [P5 {res['vpn_realizado_p05']:,.0f}; "
             f"P95 {res['vpn_realizado_p95']:,.0f}]; P(VPN realizado > 0) = {res['p_vpn_realizado_positivo']:.2f}; "
             f"fracción que llega al mercado = {res['p_llega_al_mercado_simulada']:.2f}",
             "   Mide lo que la empresa obtendría de verdad: si una fase fracasa se pierden los costes ya "
             "incurridos y no hay ventas. P(VPN realizado > 0) es la probabilidad de terminar con VPN positivo; "
             "con costes positivos no puede superar la fracción que llega al mercado. Con probabilidades de "
             "éxito bajas lo más probable es perder lo invertido aunque el rNPV sea positivo.",
             "   El VPN esperado es la media del VPN realizado y coincide con la media del rNPV salvo error de "
             "Monte Carlo."]
    if project.assumptions:
        lines += ["", "Supuestos declarados:"] + [f"- {a}" for a in project.assumptions]
    return "\n".join(lines)
