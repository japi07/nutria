"""Perspectiva de la empresa: valor actual neto ajustado por riesgo (rNPV) del desarrollo.

Cada fase (computacional, in vitro, in vivo, dossier regulatorio...) tiene coste, duración y
probabilidad de éxito. Los flujos comerciales solo se obtienen si todas las fases tienen éxito.
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


def _rnpv(project: Project, values: dict) -> tuple[float, float, float]:
    r = project.discount
    t = 0.0
    p_cum = 1.0
    npv = 0.0
    for i, _phase in enumerate(project.phases):
        cost, years, p = values[f"c{i}"], values[f"y{i}"], min(max(values[f"p{i}"], 0.0), 1.0)
        npv -= p_cum * cost / (1 + r) ** (t + years / 2)
        t += years
        p_cum *= p
    margin = values["margin"] - values["commercial"]
    for year in range(project.years_on_market):
        ramp = min(1.0, (year + 1) / project.years_to_peak) if project.years_to_peak > 0 else 1.0
        npv += p_cum * values["peak"] * ramp * margin / (1 + r) ** (t + year + 0.5)
    return npv, p_cum, t


def _central_values(project: Project) -> dict:
    v = {"peak": project.peak_sales.central(), "margin": project.gross_margin.central(),
         "commercial": project.commercial_cost.central()}
    for i, ph in enumerate(project.phases):
        v.update({f"c{i}": ph.cost.central(), f"y{i}": ph.years.central(), f"p{i}": ph.p_success.central()})
    return v


def evaluate(project: Project, n: int = 10000, seed: int = 42) -> dict:
    npv, p_success, ttm = _rnpv(project, _central_values(project))
    rng = np.random.default_rng(seed)
    samples = {"peak": project.peak_sales.sample(rng, n), "margin": project.gross_margin.sample(rng, n),
               "commercial": project.commercial_cost.sample(rng, n)}
    for i, ph in enumerate(project.phases):
        samples[f"c{i}"] = ph.cost.sample(rng, n)
        samples[f"y{i}"] = ph.years.sample(rng, n)
        samples[f"p{i}"] = ph.p_success.sample(rng, n)
    sims = np.array([_rnpv(project, {k: float(v[j]) for k, v in samples.items()})[0] for j in range(n)])
    return {
        "rnpv_central": npv, "prob_exito_acumulada": p_success, "anios_hasta_mercado": ttm,
        "rnpv_p05": float(np.percentile(sims, 5)), "rnpv_p50": float(np.percentile(sims, 50)),
        "rnpv_p95": float(np.percentile(sims, 95)), "p_rnpv_positivo": float(np.mean(sims > 0)),
        "simulaciones": n, "semilla": seed,
    }


def report(project: Project, n: int = 10000, seed: int = 42) -> str:
    res = evaluate(project, n, seed)
    lines = [f"Proyecto: {project.name} (tasa de descuento {project.discount:.0%})",
             f"Probabilidad acumulada de éxito técnico-regulatorio: {res['prob_exito_acumulada']:.2f}",
             f"Años hasta el mercado (central): {res['anios_hasta_mercado']:.1f}",
             f"rNPV central: {res['rnpv_central']:,.0f} €",
             f"rNPV Monte Carlo: P50 {res['rnpv_p50']:,.0f} € [P5 {res['rnpv_p05']:,.0f}; P95 {res['rnpv_p95']:,.0f}]; "
             f"P(rNPV > 0) = {res['p_rnpv_positivo']:.2f}"]
    if project.assumptions:
        lines += ["", "Supuestos declarados:"] + [f"- {a}" for a in project.assumptions]
    return "\n".join(lines)
