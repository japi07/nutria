"""Motor de priorización.

Mejoras respecto a una suma ponderada simple:
- puntuaciones ancladas (0-0.25-0.5-0.75-1) con rango de incertidumbre por evaluador;
- varios evaluadores independientes y detección de desacuerdos;
- compuertas que bloquean con independencia de la puntuación;
- dos ejes (valor frente a viabilidad/confianza) para no premiar ideas novedosas sin evidencia
  ni ideas sólidas sin interés comercial;
- Monte Carlo sobre puntuaciones y pesos: probabilidad de quedar entre las k primeras y rango
  mediano, para saber si el ranking es robusto o depende de los pesos elegidos.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import numpy as np

GATE_ORDER = {"bloqueada": 0, "no_resuelta": 1, "superada": 2}


@dataclass
class Criterion:
    id: str
    name: str
    axis: str
    description: str
    anchors: dict[str, str]


@dataclass
class Gate:
    id: str
    name: str
    blocks_if: str


@dataclass
class Rubric:
    version: str
    criteria: list[Criterion]
    gates: list[Gate]
    profiles: dict[str, dict]

    @property
    def criterion_ids(self) -> set[str]:
        return {c.id for c in self.criteria}

    @property
    def gate_ids(self) -> set[str]:
        return {g.id for g in self.gates}

    def weights(self, profile: str) -> dict[str, float]:
        if profile not in self.profiles:
            raise ValueError(f"Perfil desconocido: {profile}. Opciones: {', '.join(self.profiles)}")
        raw = self.profiles[profile]["pesos"]
        missing = self.criterion_ids - set(raw)
        extra = set(raw) - self.criterion_ids
        if missing or extra:
            raise ValueError(f"Perfil {profile} incoherente. Faltan: {missing or '-'}; sobran: {extra or '-'}")
        total = sum(raw.values())
        return {k: 100.0 * float(v) / total for k, v in raw.items()}

    def to_markdown(self) -> str:
        lines = [f"# Rúbrica de priorización (v{self.version})", "",
                 "Puntuaciones como fracción del máximo con rango de incertidumbre `[min, max]`. "
                 "La puntuación es orientativa y no demuestra eficacia; las compuertas bloquean "
                 "con independencia de la puntuación.", "", "## Pesos por perfil", "",
                 "| Criterio | Eje | " + " | ".join(self.profiles) + " |",
                 "|---|---|" + "---|" * len(self.profiles)]
        for c in self.criteria:
            row = [f"{c.name} (`{c.id}`)", c.axis]
            row += [f"{self.profiles[p]['pesos'][c.id]:g}" for p in self.profiles]
            lines.append("| " + " | ".join(row) + " |")
        lines += ["", "## Anclas de puntuación", ""]
        for c in self.criteria:
            lines += [f"### {c.name} (`{c.id}`, eje {c.axis})", "", c.description, ""]
            lines += [f"- **{k}**: {v}" for k, v in c.anchors.items()]
            lines.append("")
        lines += ["## Compuertas (bloqueo independiente de la puntuación)", "",
                  "Estados: `superada`, `no_resuelta` (candidato condicional: no puede avanzar a validación "
                  "experimental), `bloqueada` (fuera del ranking).", ""]
        lines += [f"- **{g.name}** (`{g.id}`): bloquea si {g.blocks_if[0].lower() + g.blocks_if[1:]}"
                  for g in self.gates]
        return "\n".join(lines) + "\n"


def load_rubric(path: Path | None = None) -> Rubric:
    if path and path.is_file():
        data = tomllib.loads(path.read_text("utf-8"))
    else:
        data = tomllib.loads(resources.files("nutria.data").joinpath("rubrica.toml").read_text("utf-8"))
    criteria = [Criterion(c["id"], c["nombre"], c["eje"], c.get("descripcion", ""), c.get("anclas", {}))
                for c in data["criterio"]]
    gates = [Gate(g["id"], g["nombre"], g.get("bloquea_si", "")) for g in data.get("compuerta", [])]
    return Rubric(str(data.get("version", "?")), criteria, gates, data.get("perfiles", {}))


@dataclass
class RankRow:
    opportunity_id: str
    title: str
    status: str                      # APTA | CONDICIONAL | BLOQUEADA | INCOMPLETA
    composite: float | None = None   # puntuación determinista (medianas, pesos nominales)
    p05: float | None = None
    p50: float | None = None
    p95: float | None = None
    p_top: float | None = None
    median_rank: float | None = None
    value_axis: float | None = None
    feasibility_axis: float | None = None
    quadrant: str | None = None
    raters: int = 0
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _quadrant(value: float, feasibility: float, threshold: float = 50.0) -> str:
    if value >= threshold and feasibility >= threshold:
        return "Prioridad"
    if value >= threshold:
        return "Apuesta (alto valor, baja viabilidad/confianza)"
    if feasibility >= threshold:
        return "Mejora incremental"
    return "Baja prioridad"


def _triangular_inverse(u: np.ndarray, a: np.ndarray, c: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Inversa de la CDF triangular, tolerante a a == b (devuelve la constante)."""
    width = b - a
    safe = np.where(width > 0, width, 1.0)
    fc = np.where(width > 0, (c - a) / safe, 0.5)
    left = a + np.sqrt(np.clip(u * width * (c - a), 0, None))
    right = b - np.sqrt(np.clip((1 - u) * width * (b - c), 0, None))
    out = np.where(u < fc, left, right)
    return np.where(width > 0, out, a)


def rank(opportunities: list[dict], rubric: Rubric, profile: str = "recomendado", n_sim: int = 5000,
         seed: int = 42, alpha: float = 200.0, top_k: int = 3, disagreement: float = 0.5) -> list[RankRow]:
    """`opportunities`: [{"id", "title", "scores": [{rater, criterion, value, low, high}], "gates": {id: estado}}]

    `alpha` es la concentración de la Dirichlet sobre los pesos (mayor = pesos más estables;
    0 = pesos fijos). Con semilla fija el resultado es reproducible.
    """
    weights = rubric.weights(profile)
    crit_ids = [c.id for c in rubric.criteria]
    axis = {c.id: c.axis for c in rubric.criteria}
    w_nominal = np.array([weights[c] for c in crit_ids])
    rows: list[RankRow] = []
    eligible: list[tuple[RankRow, list[list[tuple[float, float, float]]]]] = []

    for opp in opportunities:
        row = RankRow(opp["id"], opp.get("title", ""), status="APTA")
        by_crit: dict[str, list[tuple[float, float, float]]] = {c: [] for c in crit_ids}
        raters = set()
        for s in opp.get("scores", []):
            if s["criterion"] not in by_crit:
                continue
            v = float(s["value"])
            lo = float(s["low"]) if s.get("low") is not None else v
            hi = float(s["high"]) if s.get("high") is not None else v
            by_crit[s["criterion"]].append((lo, v, hi))
            raters.add(s["rater"])
        row.raters = len(raters)
        gates = opp.get("gates", {})
        gate_states = [gates.get(g.id, "no_resuelta") for g in rubric.gates]
        if "bloqueada" in gate_states:
            row.status = "BLOQUEADA"
            row.flags.append("Compuertas bloqueadas: " + ", ".join(
                g.id for g in rubric.gates if gates.get(g.id) == "bloqueada"))
        elif "no_resuelta" in gate_states:
            row.status = "CONDICIONAL"
            row.flags.append("Compuertas sin resolver: " + ", ".join(
                g.id for g in rubric.gates if gates.get(g.id, "no_resuelta") == "no_resuelta"))
        missing = [c for c in crit_ids if not by_crit[c]]
        if missing:
            row.flags.append("Sin puntuar: " + ", ".join(missing))
            if row.status != "BLOQUEADA":
                row.status = "INCOMPLETA"
            rows.append(row)
            continue
        medians = {c: float(np.median([t[1] for t in by_crit[c]])) for c in crit_ids}
        row.composite = float(sum(weights[c] * medians[c] for c in crit_ids))
        value_ids = [c for c in crit_ids if axis[c] == "valor"]
        feas_ids = [c for c in crit_ids if axis[c] != "valor"]
        row.value_axis = 100 * sum(weights[c] * medians[c] for c in value_ids) / sum(weights[c] for c in value_ids)
        row.feasibility_axis = 100 * sum(weights[c] * medians[c] for c in feas_ids) / sum(weights[c] for c in feas_ids)
        row.quadrant = _quadrant(row.value_axis, row.feasibility_axis)
        for c in crit_ids:
            vals = [t[1] for t in by_crit[c]]
            if len(vals) > 1 and max(vals) - min(vals) >= disagreement:
                row.flags.append(f"Desacuerdo entre evaluadores en '{c}' ({min(vals):.2f}-{max(vals):.2f}): discutir")
        if row.raters < 2:
            row.flags.append("Un solo evaluador: puntuación no independiente")
        rows.append(row)
        if row.status != "BLOQUEADA":
            eligible.append((row, [by_crit[c] for c in crit_ids]))

    if eligible and n_sim > 0:
        rng = np.random.default_rng(seed)
        n_opp, n_crit = len(eligible), len(crit_ids)
        if alpha and alpha > 0:
            conc = np.maximum(alpha * w_nominal / w_nominal.sum(), 1e-6)
            w = rng.dirichlet(conc, size=n_sim) * 100.0
        else:
            w = np.tile(w_nominal, (n_sim, 1))
        composites = np.zeros((n_sim, n_opp))
        for j, (_row, crits) in enumerate(eligible):
            for ci in range(n_crit):
                triples = np.array(crits[ci])                       # (n_raters, 3)
                pick = rng.integers(0, len(triples), size=n_sim)
                lo, mode, hi = triples[pick, 0], triples[pick, 1], triples[pick, 2]
                samples = _triangular_inverse(rng.random(n_sim), lo, mode, hi)
                composites[:, j] += w[:, ci] * samples
        order = np.argsort(-composites, axis=1)
        ranks = np.empty_like(order)
        rows_idx = np.arange(n_sim)[:, None]
        ranks[rows_idx, order] = np.arange(1, n_opp + 1)[None, :]
        for j, (row, _c) in enumerate(eligible):
            row.p05, row.p50, row.p95 = (float(x) for x in np.percentile(composites[:, j], [5, 50, 95]))
            row.p_top = float(np.mean(ranks[:, j] <= top_k))
            row.median_rank = float(np.median(ranks[:, j]))

    def sort_key(r: RankRow):
        status_order = {"APTA": 0, "CONDICIONAL": 1, "INCOMPLETA": 2, "BLOQUEADA": 3}[r.status]
        return (status_order if r.status in ("INCOMPLETA", "BLOQUEADA") else 0,
                -(r.p_top or 0), -(r.p50 or r.composite or 0))

    return sorted(rows, key=sort_key)


def format_table(rows: list[RankRow], top_k: int = 3) -> str:
    header = (f"{'Oportunidad':<10} {'Estado':<12} {'Punt.':>6} {'P50 [P5-P95]':>20} {f'P(top{top_k})':>9} "
              f"{'Rango':>6} {'Valor':>6} {'Viab.':>6}  Cuadrante")
    lines = [header, "-" * len(header)]
    for r in rows:
        if r.composite is None:
            lines.append(f"{r.opportunity_id:<10} {r.status:<12} {'-':>6} {'-':>20} {'-':>9} {'-':>6} {'-':>6} {'-':>6}")
        else:
            interval = f"{r.p50:.1f} [{r.p05:.1f}-{r.p95:.1f}]" if r.p50 is not None else "-"
            ptop = f"{r.p_top:.2f}" if r.p_top is not None else "-"
            mrank = f"{r.median_rank:.0f}" if r.median_rank is not None else "-"
            lines.append(f"{r.opportunity_id:<10} {r.status:<12} {r.composite:>6.1f} {interval:>20} {ptop:>9} "
                         f"{mrank:>6} {r.value_axis:>6.1f} {r.feasibility_axis:>6.1f}  {r.quadrant}")
        if r.title:
            lines.append(f"{'':<10} {r.title}")
        for flag in r.flags:
            lines.append(f"{'':<10} ! {flag}")
    lines.append("")
    lines.append("Punt. = puntuación con los valores modales y los pesos nominales. P50 [P5-P95] = distribución Monte "
                 "Carlo con los rangos de incertidumbre y pesos perturbados (difiere de Punt. si los rangos son asimétricos). "
                 "Valor y Viab. = ejes de 0 a 100.")
    lines.append("La puntuación es orientativa y no demuestra eficacia. Las BLOQUEADAS no compiten; las "
                 "CONDICIONALES no pueden pasar a validación experimental hasta resolver sus compuertas.")
    return "\n".join(lines)
