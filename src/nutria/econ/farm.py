"""Perspectiva del ganadero: presupuesto parcial por animal (beneficio frente a coste del producto).

Periodos (días):
- el coste usa [coste].dias si existe y, si no, [escenario].dias;
- cada [[beneficio]] por día usa su propio "dias" (número o distribución) si lo declara y, si no,
  [escenario].dias. Así, un producto de transición puede pagarse 42 días y contar la leche solo los días
  posparto. Un beneficio por ciclo (por_dia = false) no admite "dias".

Escenarios coherentes: percentiles del beneficio neto simulado por Monte Carlo
- conservador: P10; intermedio: P50; optimista: P90;
más una fila de valores centrales (moda declarada de cada entrada; mediana si la distribución no declara
moda) como referencia determinista. Combinar percentiles de las entradas (p. ej., P10 de todos los
beneficios con P90 de todos los costes) no da, en general, el percentil del resultado: suele dar un caso
bastante más extremo y de probabilidad desconocida.

El Monte Carlo propaga la incertidumbre de todas las entradas (supone independencia entre ellas;
si hay correlaciones relevantes, p. ej. precio de la leche y respuesta, documéntalo).
ROI = beneficio/coste: ROI > 1 equivale a beneficio neto positivo.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .dist import Dist, parse_dist

# Escenario -> percentil del beneficio neto simulado.
SCENARIO_PERCENTILES = (("conservador", 10), ("intermedio", 50), ("optimista", 90))
_PERCENTILES = (5, 10, 50, 90, 95)


@dataclass
class Benefit:
    name: str
    response: Dist
    unit_value: Dist
    per_day: bool = True
    note: str = ""
    days: Dist | None = None  # None: usa [escenario].dias


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
    cost_days: Dist | None = None  # None: usa [escenario].dias


def _parse_benefit(b: dict) -> Benefit:
    name = b["nombre"]
    per_day = bool(b.get("por_dia", True))
    if "dias" in b and not per_day:
        raise ValueError(f"El beneficio '{name}' declara 'dias' pero por_dia = false: los días no se usarían. "
                         "Elimina 'dias' o usa por_dia = true.")
    days = parse_dist(b["dias"]) if "dias" in b else None
    return Benefit(name, parse_dist(b["respuesta"]), parse_dist(b["valor_unitario"]), per_day,
                   b.get("nota", ""), days)


def load_farm_model(path: Path) -> FarmModel:
    data = tomllib.loads(Path(path).read_text("utf-8"))
    esc = data.get("escenario", {})
    coste = data.get("coste", {})
    benefits = [_parse_benefit(b) for b in data.get("beneficio", [])]
    if not benefits:
        raise ValueError("El modelo necesita al menos un [[beneficio]]")
    return FarmModel(
        name=esc.get("nombre", Path(path).stem), species=esc.get("especie", ""), unit=esc.get("unidad", "animal"),
        days=parse_dist(esc.get("dias", 1)), dose_g_day=parse_dist(coste.get("dosis_g_dia", 0)),
        price_eur_kg=parse_dist(coste.get("precio_eur_kg", 0)), other_eur_day=parse_dist(coste.get("otros_eur_dia", 0)),
        fixed_eur_cycle=parse_dist(coste.get("fijo_eur_ciclo", 0)), benefits=benefits,
        assumptions=list(data.get("supuestos", {}).get("lista", [])),
        cost_days=parse_dist(coste["dias"]) if "dias" in coste else None,
    )


def _evaluate(model: FarmModel, v: dict) -> dict:
    cost = (v["dose"] / 1000.0 * v["price"] + v["other"]) * v["cost_days"] + v["fixed"]
    benefit = 0.0
    parts = {}
    for i, b in enumerate(model.benefits):
        value = v[f"resp{i}"] * v[f"val{i}"] * (v[f"days{i}"] if b.per_day else 1.0)
        parts[b.name] = value
        benefit = benefit + value
    net = benefit - cost
    roi = np.divide(benefit, cost, out=np.full_like(np.asarray(benefit, dtype=float), np.nan),
                    where=np.asarray(cost) > 0) if isinstance(benefit, np.ndarray) else (
        benefit / cost if cost > 0 else float("nan"))
    return {"beneficio": benefit, "coste": cost, "neto": net, "roi": roi, "componentes": parts}


def _central_values(model: FarmModel) -> dict:
    """Valor central de cada entrada: moda declarada (pert, triangular, normal) o mediana (resto)."""
    v = {"days": model.days.central(), "dose": model.dose_g_day.central(), "price": model.price_eur_kg.central(),
         "other": model.other_eur_day.central(), "fixed": model.fixed_eur_cycle.central()}
    v["cost_days"] = model.cost_days.central() if model.cost_days is not None else v["days"]
    for i, b in enumerate(model.benefits):
        v[f"resp{i}"] = b.response.central()
        v[f"val{i}"] = b.unit_value.central()
        v[f"days{i}"] = b.days.central() if b.days is not None else v["days"]
    return v


def _simulate(model: FarmModel, n: int, seed: int) -> dict:
    """Una sola simulación compartida por scenarios() y monte_carlo(): con la misma (n, semilla) dan los mismos
    percentiles. Los días sin declarar reutilizan la muestra de [escenario].dias (mismo animal y periodo)."""
    if int(n) < 1:
        raise ValueError("El número de simulaciones debe ser >= 1")
    rng = np.random.default_rng(seed)
    v = {"days": model.days.sample(rng, n), "dose": model.dose_g_day.sample(rng, n),
         "price": model.price_eur_kg.sample(rng, n), "other": model.other_eur_day.sample(rng, n),
         "fixed": model.fixed_eur_cycle.sample(rng, n)}
    v["cost_days"] = model.cost_days.sample(rng, n) if model.cost_days is not None else v["days"]
    for i, b in enumerate(model.benefits):
        v[f"resp{i}"] = b.response.sample(rng, n)
        v[f"val{i}"] = b.unit_value.sample(rng, n)
        v[f"days{i}"] = b.days.sample(rng, n) if b.days is not None else v["days"]
    return _evaluate(model, v)


def _pct(x: np.ndarray) -> dict | None:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if not x.size:
        return None
    return {f"p{q:02d}": float(np.percentile(x, q)) for q in _PERCENTILES}


def scenarios(model: FarmModel, n: int = 10000, seed: int = 42) -> dict:
    """Escenarios coherentes: P10, P50 y P90 del beneficio neto simulado (conservador, intermedio, optimista)
    y la fila 'valores_centrales' (evaluación determinista con la moda de cada entrada)."""
    net = _pct(_simulate(model, n, seed)["neto"])
    if net is None:
        raise ValueError("El beneficio neto simulado no tiene valores finitos: revisa las entradas")
    out = {}
    for name, q in SCENARIO_PERCENTILES:
        out[name] = {"percentil": q, "neto": net[f"p{q:02d}"],
                     "definicion": f"P{q} del beneficio neto simulado ({n} simulaciones, semilla {seed})"}
    res = _evaluate(model, _central_values(model))
    central = {k: float(x) for k, x in res.items() if k != "componentes"}
    central["componentes"] = {name: float(y) for name, y in res["componentes"].items()}
    central["definicion"] = ("Evaluación determinista con la moda declarada de cada entrada (mediana si la "
                             "distribución no declara moda); no es un percentil del neto")
    out["valores_centrales"] = central
    return out


def _days_for(model: FarmModel, b: Benefit) -> Dist:
    return b.days if b.days is not None else model.days


def monte_carlo(model: FarmModel, n: int = 10000, seed: int = 42) -> dict:
    res = _simulate(model, n, seed)
    net, roi, cost = res["neto"], res["roi"], res["coste"]
    first = model.benefits[0]
    days_c = _days_for(model, first).central()
    unit_days = first.unit_value.central() * (days_c if first.per_day else 1.0)
    breakeven = float(np.median(cost)) / unit_days if unit_days > 0 else float("nan")
    return {
        "simulaciones": n, "semilla": seed,
        "neto_por_animal": _pct(net), "beneficio_por_animal": _pct(res["beneficio"]),
        "coste_por_animal": _pct(cost), "roi": _pct(roi),
        "p_neto_positivo": float(np.mean(net > 0)),
        "respuesta_umbral": {"beneficio": first.name, "valor": breakeven,
                             "nota": "Respuesta mínima en el primer beneficio para cubrir el coste mediano "
                                     "(con el resto de beneficios a cero y los días centrales de ese beneficio)."},
    }


def _days_label(d: Dist) -> str:
    text = f"{d.central():g} días"
    return text if d.fixed is not None else f"{text} (valor central; distribución {d.kind})"


def _periods_line(model: FarmModel) -> str:
    parts = ["coste " + _days_label(model.cost_days if model.cost_days is not None else model.days)]
    for b in model.benefits:
        parts.append(f"'{b.name}' " + (_days_label(_days_for(model, b)) if b.per_day else "por ciclo"))
    return "Periodos: " + "; ".join(parts)


def report(model: FarmModel, n: int = 10000, seed: int = 42) -> str:
    sc = scenarios(model, n, seed)
    mc = monte_carlo(model, n, seed)
    unit = model.unit
    lines = [f"Modelo del ganadero: {model.name} ({model.species}, por {unit})", _periods_line(model), "",
             f"Escenarios coherentes: percentiles del beneficio neto simulado ({n} simulaciones, semilla {seed})",
             f"{'Escenario':<14}{'Percentil':>10}{f'Neto €/{unit}':>16}{'¿Neto > 0?':>12}"]
    for name, q in SCENARIO_PERCENTILES:
        value = sc[name]["neto"]
        lines.append(f"{name:<14}{f'P{q}':>10}{value:>16.2f}{'sí' if value > 0 else 'no':>12}")
    c = sc["valores_centrales"]
    lines += ["", "Valores centrales (moda declarada de cada entrada; mediana si no la declara): "
                  f"beneficio {c['beneficio']:.2f} €; coste {c['coste']:.2f} €; neto {c['neto']:.2f} €; "
                  f"ROI (beneficio/coste) {c['roi']:.2f}"]
    neto, roi = mc["neto_por_animal"], mc["roi"]
    lines += ["", f"Monte Carlo: P(neto > 0) = {mc['p_neto_positivo']:.2f} (fracción de simulaciones con ROI > 1); "
                  f"neto P5 {neto['p05']:.2f} €, P95 {neto['p95']:.2f} €"]
    if roi is not None:
        lines.append(f"ROI (beneficio/coste): P10 {roi['p10']:.2f}; P50 {roi['p50']:.2f}; P90 {roi['p90']:.2f}")
    lines += [f"Respuesta umbral en '{mc['respuesta_umbral']['beneficio']}': {mc['respuesta_umbral']['valor']:.3f} "
              "unidades/animal/día (o por ciclo)",
              "", "Cómo leer los escenarios: cada uno es un percentil del neto simulado, no una combinación de "
                  "percentiles de las entradas. Los valores centrales son una referencia determinista y pueden "
                  "diferir del P50."]
    if model.assumptions:
        lines += ["", "Supuestos declarados:"] + [f"- {a}" for a in model.assumptions]
    lines += ["", "Las entradas deben proceder de fuentes registradas o marcarse como [SUP]; el modelo no "
                  "sustituye a un ensayo de campo."]
    return "\n".join(lines)
