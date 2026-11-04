"""Perspectiva del ganadero: presupuesto parcial por animal (beneficio frente a coste del producto).

Escenarios coherentes:
- conservador: beneficios en su percentil 10 y costes en su percentil 90;
- intermedio: valores modales (o medianas);
- optimista: beneficios en P90 y costes en P10.
El Monte Carlo propaga la incertidumbre de todas las entradas (supone independencia entre ellas;
si hay correlaciones relevantes, p. ej. precio de la leche y respuesta, documéntalo).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .dist import Dist, parse_dist


@dataclass
class Benefit:
    name: str
    response: Dist
    unit_value: Dist
    per_day: bool = True
    note: str = ""


@dataclass
class FarmModel:
    name: str
    species: str
    unit: str
    days: Dist
    dose_g_day: Dist
    price_eur_kg: Dist
    other_eur_day: Dist
    fixed_eur_cycle: Dist
    benefits: list[Benefit] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)


def load_farm_model(path: Path) -> FarmModel:
    data = tomllib.loads(Path(path).read_text("utf-8"))
    esc = data.get("escenario", {})
    coste = data.get("coste", {})
    benefits = [Benefit(b["nombre"], parse_dist(b["respuesta"]), parse_dist(b["valor_unitario"]),
                        bool(b.get("por_dia", True)), b.get("nota", "")) for b in data.get("beneficio", [])]
    if not benefits:
        raise ValueError("El modelo necesita al menos un [[beneficio]]")
    return FarmModel(
        name=esc.get("nombre", Path(path).stem), species=esc.get("especie", ""), unit=esc.get("unidad", "animal"),
        days=parse_dist(esc.get("dias", 1)), dose_g_day=parse_dist(coste.get("dosis_g_dia", 0)),
        price_eur_kg=parse_dist(coste.get("precio_eur_kg", 0)), other_eur_day=parse_dist(coste.get("otros_eur_dia", 0)),
        fixed_eur_cycle=parse_dist(coste.get("fijo_eur_ciclo", 0)), benefits=benefits,
        assumptions=list(data.get("supuestos", {}).get("lista", [])),
    )


def _evaluate(model: FarmModel, v: dict) -> dict:
    days = v["days"]
    cost = (v["dose"] / 1000.0 * v["price"] + v["other"]) * days + v["fixed"]
    benefit = 0.0
    parts = {}
    for i, b in enumerate(model.benefits):
        value = v[f"resp{i}"] * v[f"val{i}"] * (days if b.per_day else 1.0)
        parts[b.name] = value
        benefit = benefit + value
    net = benefit - cost
    roi = np.divide(benefit, cost, out=np.full_like(np.asarray(benefit, dtype=float), np.nan),
                    where=np.asarray(cost) > 0) if isinstance(benefit, np.ndarray) else (
        benefit / cost if cost > 0 else float("nan"))
    return {"beneficio": benefit, "coste": cost, "neto": net, "roi": roi, "componentes": parts}


def _values(model: FarmModel, q_benefit: float | None, q_cost: float | None) -> dict:
    def pick(d: Dist, q):
        return d.central() if q is None else d.quantile(q)
    v = {"days": model.days.central(), "dose": pick(model.dose_g_day, q_cost),
         "price": pick(model.price_eur_kg, q_cost), "other": pick(model.other_eur_day, q_cost),
         "fixed": pick(model.fixed_eur_cycle, q_cost)}
    for i, b in enumerate(model.benefits):
        v[f"resp{i}"] = pick(b.response, q_benefit)
        v[f"val{i}"] = pick(b.unit_value, q_benefit)
    return v


def scenarios(model: FarmModel) -> dict:
    out = {}
    for name, qb, qc in (("conservador", 0.10, 0.90), ("intermedio", None, None), ("optimista", 0.90, 0.10)):
        res = _evaluate(model, _values(model, qb, qc))
        out[name] = {k: (float(x) if not isinstance(x, dict) else {n: float(y) for n, y in x.items()})
                     for k, x in res.items()}
    return out


def monte_carlo(model: FarmModel, n: int = 10000, seed: int = 42) -> dict:
    rng = np.random.default_rng(seed)
    v = {"days": model.days.sample(rng, n), "dose": model.dose_g_day.sample(rng, n),
         "price": model.price_eur_kg.sample(rng, n), "other": model.other_eur_day.sample(rng, n),
         "fixed": model.fixed_eur_cycle.sample(rng, n)}
    for i, b in enumerate(model.benefits):
        v[f"resp{i}"] = b.response.sample(rng, n)
        v[f"val{i}"] = b.unit_value.sample(rng, n)
    res = _evaluate(model, v)
    net, roi, cost = res["neto"], res["roi"], res["coste"]

    def pct(x):
        x = x[np.isfinite(x)]
        return {"p05": float(np.percentile(x, 5)), "p50": float(np.percentile(x, 50)),
                "p95": float(np.percentile(x, 95))} if x.size else None

    first = model.benefits[0]
    days_c = model.days.central()
    unit_days = first.unit_value.central() * (days_c if first.per_day else 1.0)
    breakeven = float(np.median(cost)) / unit_days if unit_days > 0 else float("nan")
    return {
        "simulaciones": n, "semilla": seed,
        "neto_por_animal": pct(net), "roi": pct(roi), "coste_por_animal": pct(cost),
        "p_neto_positivo": float(np.mean(net > 0)),
        "respuesta_umbral": {"beneficio": first.name, "valor": breakeven,
                             "nota": "Respuesta mínima en el primer beneficio para cubrir el coste mediano "
                                     "(con el resto de beneficios a cero)."},
    }


def report(model: FarmModel, n: int = 10000, seed: int = 42) -> str:
    sc = scenarios(model)
    mc = monte_carlo(model, n, seed)
    lines = [f"Modelo del ganadero: {model.name} ({model.species}, por {model.unit})", "",
             f"{'Escenario':<13}{'Beneficio €':>13}{'Coste €':>11}{'Neto €':>11}{'ROI':>8}"]
    for name, r in sc.items():
        lines.append(f"{name:<13}{r['beneficio']:>13.2f}{r['coste']:>11.2f}{r['neto']:>11.2f}{r['roi']:>8.2f}")
    neto = mc["neto_por_animal"]
    lines += ["", f"Monte Carlo ({n} simulaciones, semilla {seed}): neto P50 {neto['p50']:.2f} € "
                  f"[P5 {neto['p05']:.2f}; P95 {neto['p95']:.2f}]; P(neto > 0) = {mc['p_neto_positivo']:.2f}",
              f"Respuesta umbral en '{mc['respuesta_umbral']['beneficio']}': {mc['respuesta_umbral']['valor']:.3f} "
              "unidades/animal/día (o por ciclo)"]
    if model.assumptions:
        lines += ["", "Supuestos declarados:"] + [f"- {a}" for a in model.assumptions]
    lines += ["", "Las entradas deben proceder de fuentes registradas o marcarse como [SUP]; el modelo no "
                  "sustituye a un ensayo de campo."]
    return "\n".join(lines)
