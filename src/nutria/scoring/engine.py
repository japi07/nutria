"""Motor de priorización.

Mejoras respecto a una suma ponderada simple:
- puntuaciones ancladas (0-0.25-0.5-0.75-1) con rango de incertidumbre por evaluador;
- varios evaluadores independientes y detección de desacuerdos;
- compuertas que bloquean con independencia de la puntuación;
- dos ejes (valor frente a viabilidad/confianza) para no premiar ideas novedosas sin evidencia
  ni ideas sólidas sin interés comercial;
- Monte Carlo sobre puntuaciones y pesos: probabilidad de quedar entre las k primeras y rango
  mediano, para saber si el ranking es robusto o depende de los pesos elegidos;
- orden determinista con cada perfil de pesos y aviso automático si el orden o el top-k cambian entre perfiles;
- dentro de CONDICIONAL, subgrupos por el estado de las compuertas: solo pendientes de firma, alguna sin resolver y
  alguna CRÍTICA sin resolver (seguridad, regulatoria, destino_digestivo), en ese orden y sea cual sea la puntuación,
  con aviso si el top-k para planes incluye compuertas críticas sin resolver;
- anclas en los datos: un valor fuera de las anclas de la rúbrica exige una justificación explícita
  (validate_anchors) y el ranking lo señala.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import numpy as np

GATE_ORDER = {"bloqueada": 0, "no_resuelta": 1, "superada": 2}
# Compuertas críticas por defecto (si la rúbrica no marca ninguna con `critica = true`).
CRITICAL_GATES_DEFAULT = ("seguridad", "regulatoria", "destino_digestivo")
DEFAULT_ANCHORS = (0.0, 0.25, 0.5, 0.75, 1.0)
ANCHOR_TOLERANCE = 1e-9
# Subgrupos de CONDICIONAL (se ordenan en este orden).
TIER_LABELS = {0: "solo pendientes de firma humana", 1: "compuertas sin resolver",
               2: "compuertas CRÍTICAS sin resolver"}


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
    critical: bool = False


@dataclass
class MaturityLevel:
    id: str
    meaning: str
    note: str = ""


@dataclass
class Rubric:
    version: str
    criteria: list[Criterion]
    gates: list[Gate]
    profiles: dict[str, dict]
    maturity: list[MaturityLevel] = field(default_factory=list)

    @property
    def criterion_ids(self) -> set[str]:
        return {c.id for c in self.criteria}

    @property
    def gate_ids(self) -> set[str]:
        return {g.id for g in self.gates}

    @property
    def critical_gate_ids(self) -> set[str]:
        """Compuertas críticas: las marcadas `critica = true`; si ninguna lo está, seguridad, regulatoria y
        destino_digestivo (fallo en cerrado para rúbricas propias sin la marca)."""
        marked = {g.id for g in self.gates if g.critical}
        return marked or (set(CRITICAL_GATES_DEFAULT) & self.gate_ids)

    def anchor_values(self, criterion: str) -> tuple[float, ...]:
        """Valores admitidos sin justificación: las claves de las anclas del criterio (0, 0.25, 0.5, 0.75 y 1)."""
        for c in self.criteria:
            if c.id == criterion and c.anchors:
                return tuple(sorted(float(k) for k in c.anchors))
        return DEFAULT_ANCHORS

    def fingerprint(self) -> str:
        """Huella SHA-256 de lo que determina el ranking: criterios, ejes, anclas, compuertas (y cuáles son críticas)
        y perfiles de pesos."""
        import hashlib
        import json
        payload = {"version": self.version,
                   "criterios": [[c.id, c.axis, sorted(c.anchors)] for c in self.criteria],
                   "compuertas": [[g.id, g.id in self.critical_gate_ids] for g in self.gates],
                   "perfiles": {k: v.get("pesos", {}) for k, v in self.profiles.items()}}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

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
        anchors = ", ".join(f"{a:g}" for a in DEFAULT_ANCHORS)
        lines = [f"# Rúbrica de priorización (v{self.version})", "",
                 "Puntuaciones como fracción del máximo con rango de incertidumbre `[min, max]`. "
                 "La puntuación es orientativa y no demuestra eficacia; las compuertas bloquean "
                 "con independencia de la puntuación.", "",
                 f"**Anclas en los datos**: el `valor` de cada criterio es una de sus anclas ({anchors}). Un valor "
                 "intermedio solo se admite con una justificación explícita en `justificacion_entre_anclas` (al "
                 "menos 10 caracteres): sin ella, `nutria puntuar registrar` rechaza el fichero entero y no registra "
                 "nada; con ella, avisa y añade la justificación a la del criterio. El ranking señala los valores "
                 "entre anclas. `min` y `max` expresan la incertidumbre y pueden tomar cualquier valor entre 0 y 1.",
                 "", "## Pesos por perfil", "",
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
        critical = ", ".join(f"`{g.id}`" for g in self.gates if g.id in self.critical_gate_ids)
        lines += ["## Compuertas (bloqueo independiente de la puntuación)", "",
                  "Estados: `superada`, `no_resuelta` (candidato condicional: no puede avanzar a validación "
                  "experimental), `bloqueada` (fuera del ranking).", "",
                  f"Compuertas **críticas**: {critical}. Dentro de CONDICIONAL, el ranking forma tres subgrupos que "
                  "se ordenan siempre en este orden, sea cual sea la puntuación: (1) solo pendientes de firma humana "
                  "(todas las compuertas propuestas como `superada`); (2) alguna compuerta no crítica sin resolver "
                  "(sin propuesta o propuesta `no_resuelta`); (3) alguna compuerta crítica sin resolver. Si el top-k "
                  "para planes incluye una candidata del subgrupo (3), el ranking lo avisa.", ""]
        lines += [f"- **{g.name}** (`{g.id}`{', crítica' if g.id in self.critical_gate_ids else ''}): bloquea si "
                  f"{g.blocks_if[0].lower() + g.blocks_if[1:]}" for g in self.gates]
        if self.maturity:
            lines += ["", "## Escala de madurez del candidato (EMC)", "", "| Nivel | Significado |", "|---|---|"]
            lines += [f"| {m.id} | {m.meaning} |" for m in self.maturity]
            notes = [f"- **{m.id}**: {m.note}" for m in self.maturity if m.note]
            if notes:
                lines += [""] + notes
        return "\n".join(lines) + "\n"


def load_rubric(path: Path | None = None) -> Rubric:
    if path and path.is_file():
        data = tomllib.loads(path.read_text("utf-8"))
    else:
        data = tomllib.loads(resources.files("nutria.data").joinpath("rubrica.toml").read_text("utf-8"))
    criteria = [Criterion(c["id"], c["nombre"], c["eje"], c.get("descripcion", ""), c.get("anclas", {}))
                for c in data["criterio"]]
    for c in criteria:
        for key in c.anchors:
            try:
                float(key)
            except ValueError:
                raise ValueError(f"Rúbrica: el ancla '{key}' de '{c.id}' no es un número") from None
    gates = [Gate(g["id"], g["nombre"], g.get("bloquea_si", ""), bool(g.get("critica", False)))
             for g in data.get("compuerta", [])]
    maturity = [MaturityLevel(m["nivel"], m["significado"], m.get("nota", "")) for m in data.get("madurez", [])]
    return Rubric(str(data.get("version", "?")), criteria, gates, data.get("perfiles", {}), maturity)


def validate_anchors(rubric: Rubric, items: list[dict], warn=None) -> list[dict]:
    """Anclas en los datos (D8-11): cada `valor` debe ser un ancla de su criterio (0, 0.25, 0.5, 0.75, 1).

    Un valor intermedio exige `justificacion_entre_anclas` (texto de al menos 10 caracteres). Sin ella se rechaza el
    fichero entero con ValueError, antes de registrar nada; con ella se acepta, se avisa con `warn(mensaje)` y la
    justificación se añade a `justificacion` (así queda en el registro). Devuelve copias de las puntuaciones."""
    errors, out = [], []
    for item in items:
        crit = item.get("criterio")
        anchors = rubric.anchor_values(crit)
        new = dict(item)
        value = float(item["valor"])
        if not any(abs(value - a) <= ANCHOR_TOLERANCE for a in anchors):
            lower = max((a for a in anchors if a < value), default=anchors[0])
            upper = min((a for a in anchors if a > value), default=anchors[-1])
            raw = item.get("justificacion_entre_anclas")
            reason = raw.strip() if isinstance(raw, str) else ""
            if len(reason) < 10:
                errors.append(f"{crit}: valor {value:g} fuera de las anclas ({', '.join(f'{a:g}' for a in anchors)}); "
                              f"usa el ancla más cercana ({lower:g} o {upper:g}) o justifica el valor intermedio en "
                              "'justificacion_entre_anclas'")
                continue
            new["justificacion"] = (f"{str(item.get('justificacion', '')).rstrip()} [Valor entre anclas "
                                    f"{lower:g} y {upper:g}: {reason}]").strip()
            if warn is not None:
                warn(f"Aviso: {crit}: valor {value:g} entre las anclas {lower:g} y {upper:g}; se registra con la "
                     "justificación explícita y el ranking lo señalará.")
        out.append(new)
    if errors:
        raise ValueError("Puntuaciones fuera de las anclas de la rúbrica (no se ha registrado nada): "
                         + "; ".join(errors))
    return out


@dataclass
class RankRow:
    opportunity_id: str
    title: str
    status: str                      # APTA | CONDICIONAL | INSUFICIENTE | INCOMPLETA | BLOQUEADA
    composite: float | None = None   # puntuación determinista (medianas, pesos nominales)
    p05: float | None = None
    p50: float | None = None
    p95: float | None = None
    p_top: float | None = None       # probabilidad de quedar entre los k primeros DENTRO de su grupo
    median_rank: float | None = None
    value_axis: float | None = None
    feasibility_axis: float | None = None
    quadrant: str | None = None
    raters: int = 0
    ranks_by_profile: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    gate_tier: int | None = None     # CONDICIONAL: 0 solo firma, 1 sin resolver, 2 crítica sin resolver
    gates_awaiting_signature: list[str] = field(default_factory=list)
    gates_unresolved: list[str] = field(default_factory=list)
    critical_unresolved: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


STATUS_ORDER = {"APTA": 0, "CONDICIONAL": 1, "INSUFICIENTE": 2, "INCOMPLETA": 3, "BLOQUEADA": 4}


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


def _monte_carlo(group: list[tuple[RankRow, list]], w_nominal: np.ndarray, n_sim: int, rng: np.random.Generator,
                 alpha: float, top_k: int) -> None:
    if not group or n_sim <= 0:
        return
    n_opp, n_crit = len(group), len(w_nominal)
    if alpha and alpha > 0:
        conc = np.maximum(alpha * w_nominal / w_nominal.sum(), 1e-6)
        w = rng.dirichlet(conc, size=n_sim) * 100.0
    else:
        w = np.tile(w_nominal, (n_sim, 1))
    composites = np.zeros((n_sim, n_opp))
    for j, (_row, crits) in enumerate(group):
        for ci in range(n_crit):
            triples = np.array(crits[ci])                       # (n_evaluadores, 3)
            pick = rng.integers(0, len(triples), size=n_sim)
            lo, mode, hi = triples[pick, 0], triples[pick, 1], triples[pick, 2]
            composites[:, j] += w[:, ci] * _triangular_inverse(rng.random(n_sim), lo, mode, hi)
    order = np.argsort(-composites, axis=1)
    ranks = np.empty_like(order)
    ranks[np.arange(n_sim)[:, None], order] = np.arange(1, n_opp + 1)[None, :]
    for j, (row, _c) in enumerate(group):
        row.p05, row.p50, row.p95 = (float(x) for x in np.percentile(composites[:, j], [5, 50, 95]))
        row.p_top = float(np.mean(ranks[:, j] <= top_k))
        row.median_rank = float(np.median(ranks[:, j]))


def _classify_gates(row: RankRow, gate_states: dict[str, str], proposals: dict | None, critical: set[str]) -> None:
    """Compuertas no superadas (efectivas) separadas en pendientes de firma (propuesta `superada` sin firmar) y sin
    resolver (sin propuesta o propuesta `no_resuelta`). Sin propuestas conocidas, todo lo no superado cuenta como sin
    resolver (fallo en cerrado)."""
    pending = [g for g, st in gate_states.items() if st not in ("superada", "bloqueada")]
    row.gates_awaiting_signature = [g for g in pending if proposals and proposals.get(g) == "superada"]
    row.gates_unresolved = [g for g in pending if g not in row.gates_awaiting_signature]
    row.critical_unresolved = [g for g in row.gates_unresolved if g in critical]


def _tier(row: RankRow) -> int:
    if row.critical_unresolved:
        return 2
    return 1 if row.gates_unresolved else 0


def _order_key(row: RankRow) -> tuple:
    return STATUS_ORDER[row.status], row.gate_tier or 0


def rank(opportunities: list[dict], rubric: Rubric, profile: str = "recomendado", n_sim: int = 5000,
         seed: int = 42, alpha: float = 50.0, top_k: int = 3, disagreement: float = 0.5,
         min_raters: int = 3) -> list[RankRow]:
    """`opportunities`: [{"id", "title", "scores": [{rater, criterion, value, low, high}],
    "gates": {id: estado_efectivo}, "gate_proposals": {id: estado_registrado_sin_firma} (opcional),
    "status": estado_de_registro}]

    Estados: APTA (todas las compuertas superadas y firmadas), CONDICIONAL (alguna sin resolver o sin
    firmar), INSUFICIENTE (menos de `min_raters` evaluadores en algún criterio), INCOMPLETA (criterios
    sin puntuar) y BLOQUEADA (compuerta bloqueada u oportunidad bloqueada o descartada).
    Dentro de CONDICIONAL hay tres subgrupos (`gate_tier`) que se ordenan siempre así, sea cual sea la puntuación:
    0 = solo pendientes de firma (todas propuestas `superada`); 1 = alguna compuerta sin resolver; 2 = alguna
    compuerta CRÍTICA sin resolver (seguridad, regulatoria, destino_digestivo). Sin `gate_proposals`, toda compuerta
    no superada cuenta como sin resolver (fallo en cerrado).
    APTA y cada subgrupo de CONDICIONAL se simulan por separado: P(top-k) se refiere al propio grupo, de modo que
    una CONDICIONAL nunca adelanta a una APTA ni una candidata de un subgrupo a otra de un subgrupo anterior. `alpha`
    es la concentración de la Dirichlet sobre los pesos (menor = más perturbación; 0 = pesos fijos). Con semilla
    fija el resultado es reproducible. Los avisos globales (top-k con compuertas críticas, orden que cambia con los
    pesos) los da `ranking_warnings`.
    """
    weights = rubric.weights(profile)
    crit_ids = [c.id for c in rubric.criteria]
    axis = {c.id: c.axis for c in rubric.criteria}
    critical = rubric.critical_gate_ids
    w_nominal = np.array([weights[c] for c in crit_ids])
    other_profiles = {name: rubric.weights(name) for name in rubric.profiles}
    rows: list[RankRow] = []
    groups: dict[tuple, list] = {("APTA", 0): [], ("CONDICIONAL", 0): [], ("CONDICIONAL", 1): [],
                                 ("CONDICIONAL", 2): []}
    medians_by_row: dict[str, dict[str, float]] = {}

    for opp in opportunities:
        row = RankRow(opp["id"], opp.get("title", ""), status="APTA")
        by_crit: dict[str, list[tuple[float, float, float]]] = {c: [] for c in crit_ids}
        raters_by_crit: dict[str, set] = {c: set() for c in crit_ids}
        raters = set()
        off_anchor: dict[str, dict[str, list[str]]] = {}
        for s in opp.get("scores", []):
            if s["criterion"] not in by_crit:
                continue
            v = float(s["value"])
            lo = float(s["low"]) if s.get("low") is not None else v
            hi = float(s["high"]) if s.get("high") is not None else v
            by_crit[s["criterion"]].append((lo, v, hi))
            raters_by_crit[s["criterion"]].add(s["rater"])
            raters.add(s["rater"])
            if not any(abs(v - a) <= ANCHOR_TOLERANCE for a in rubric.anchor_values(s["criterion"])):
                off_anchor.setdefault(s["criterion"], {}).setdefault(f"{v:g}", []).append(str(s["rater"]))
        row.raters = len(raters)
        gates = opp.get("gates", {})
        gate_states = {g.id: gates.get(g.id, "no_resuelta") for g in rubric.gates}
        _classify_gates(row, gate_states, opp.get("gate_proposals"), critical)
        blocked = [g for g, st in gate_states.items() if st == "bloqueada"]
        if opp.get("status") in ("bloqueada", "descartada"):
            row.status = "BLOQUEADA"
            row.flags.append(f"Oportunidad en estado '{opp.get('status')}'")
        if blocked:
            row.status = "BLOQUEADA"
            row.flags.append("Compuertas bloqueadas: " + ", ".join(blocked))
        elif (row.gates_unresolved or row.gates_awaiting_signature) and row.status != "BLOQUEADA":
            row.status = "CONDICIONAL"
            row.gate_tier = _tier(row)
            if row.critical_unresolved:
                row.flags.append("COMPUERTAS CRÍTICAS SIN RESOLVER: " + ", ".join(row.critical_unresolved)
                                 + " (incertidumbre crítica: va detrás de las CONDICIONALES sin compuertas críticas "
                                   "pendientes, sea cual sea su puntuación)")
            other = [g for g in row.gates_unresolved if g not in row.critical_unresolved]
            if other:
                row.flags.append("Compuertas sin resolver (sin propuesta o propuesta no_resuelta): " + ", ".join(other))
            if row.gates_awaiting_signature:
                row.flags.append("Compuertas propuestas como superadas, pendientes de firma humana: "
                                 + ", ".join(row.gates_awaiting_signature))
        if off_anchor:
            row.flags.append("Valores entre anclas (exigen justificación explícita al registrar): " + "; ".join(
                f"{c} " + ", ".join(f"{v} ({', '.join(sorted(r))})" for v, r in vals.items())
                for c, vals in off_anchor.items()))
        missing = [c for c in crit_ids if not by_crit[c]]
        if missing:
            row.flags.append("Sin puntuar: " + ", ".join(missing))
            if row.status != "BLOQUEADA":
                row.status = "INCOMPLETA"
            rows.append(row)
            continue
        few = [c for c in crit_ids if len(raters_by_crit[c]) < min_raters]
        if few:
            row.flags.append(f"Menos de {min_raters} evaluadores independientes en: " + ", ".join(few))
            if row.status != "BLOQUEADA":
                row.status = "INSUFICIENTE"
        medians = {c: float(np.median([t[1] for t in by_crit[c]])) for c in crit_ids}
        medians_by_row[row.opportunity_id] = medians
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
        rows.append(row)
        key = (row.status, row.gate_tier or 0)
        if key in groups:
            groups[key].append((row, [by_crit[c] for c in crit_ids]))
    for row in rows:
        if row.status not in ("CONDICIONAL",):
            row.gate_tier = None

    # Orden determinista con cada perfil de pesos (robustez frente a la elección de pesos), respetando grupos.
    scored = [r for r in rows if r.opportunity_id in medians_by_row and r.status in ("APTA", "CONDICIONAL")]
    for name, wts in other_profiles.items():
        totals = sorted(((sum(wts[c] * medians_by_row[r.opportunity_id][c] for c in crit_ids), r) for r in scored),
                        key=lambda t: (*_order_key(t[1]), -t[0]))
        for position, (_total, r) in enumerate(totals, start=1):
            r.ranks_by_profile[name] = position

    rng = np.random.default_rng(seed)
    for key in groups:
        _monte_carlo(groups[key], w_nominal, n_sim, rng, alpha, top_k)

    def sort_key(r: RankRow):
        return (*_order_key(r), -(r.p_top or 0), -(r.p50 or r.composite or 0))

    ordered = sorted(rows, key=sort_key)
    for r in ordered:
        if len(set(r.ranks_by_profile.values())) > 1:
            r.flags.append("La posición cambia con el perfil de pesos ("
                           + ", ".join(f"{p} {pos}" for p, pos in r.ranks_by_profile.items())
                           + "): el orden depende de los pesos elegidos")
    tops = _top_sets(ordered, top_k, profile)
    for r in ordered:
        inside = [name for name, t in tops.items() if r.opportunity_id in t]
        outside = [name for name, t in tops.items() if r.opportunity_id not in t]
        if inside and outside:
            r.flags.append(f"Dentro del top-{top_k} con {', '.join(inside)} y fuera con {', '.join(outside)}: que entre "
                           "en la propuesta depende de los pesos o de la incertidumbre de las puntuaciones")
    for r in [r for r in ordered if r.status in ("APTA", "CONDICIONAL")][:top_k]:
        if r.critical_unresolved:
            r.flags.append(f"En el top-{top_k} para planes con compuertas críticas sin resolver: "
                           + ", ".join(r.critical_unresolved))
    return ordered


def _profile_orders(rows: list[RankRow]) -> dict[str, list[str]]:
    ranked = [r for r in rows if r.status in ("APTA", "CONDICIONAL") and r.ranks_by_profile]
    profiles = sorted({p for r in ranked for p in r.ranks_by_profile})
    return {p: [r.opportunity_id for r in sorted(ranked, key=lambda r: r.ranks_by_profile.get(p, 10 ** 9))]
            for p in profiles}


def _join(ids: list[str], limit: int = 12) -> str:
    return " > ".join(ids[:limit]) + (" > ..." if len(ids) > limit else "")


def _mc_label(profile: str | None) -> str:
    return f"Monte Carlo ({profile})" if profile else "Monte Carlo"


def _top_sets(rows: list[RankRow], top_k: int, profile: str | None = None) -> dict[str, set[str]]:
    """Conjunto del top-k con el orden determinista de cada perfil de pesos y con el orden Monte Carlo (filas en el
    orden devuelto por rank)."""
    tops = {p: set(o[:top_k]) for p, o in _profile_orders(rows).items()}
    if tops:
        ranked = [r for r in rows if r.status in ("APTA", "CONDICIONAL")]
        tops[_mc_label(profile)] = {r.opportunity_id for r in ranked[:top_k]}
    return tops


def ranking_warnings(rows: list[RankRow], top_k: int = 3, profile: str | None = None) -> list[str]:
    """Avisos globales del ranking (D8-1 y D8-4): top-k para planes con compuertas críticas sin resolver; orden o
    conjunto del top-k que cambian entre perfiles de pesos; y top-k del Monte Carlo (incertidumbre de las puntuaciones
    y pesos perturbados) distinto del orden determinista del perfil elegido (`profile`; sin él, de todos)."""
    warnings = []
    ranked = [r for r in rows if r.status in ("APTA", "CONDICIONAL")]
    top = ranked[:top_k]
    crit = [r for r in top if r.critical_unresolved]
    if crit:
        warnings.append(f"AVISO: el top-{top_k} para planes incluye candidatas con compuertas CRÍTICAS sin resolver: "
                        + "; ".join(f"{r.opportunity_id} ({', '.join(r.critical_unresolved)})" for r in crit)
                        + ". Pide antes la propuesta del especialista o declara la incertidumbre crítica en la "
                          "propuesta; la selección la firma una persona.")
    orders = _profile_orders(rows)
    if len({tuple(o) for o in orders.values()}) > 1:
        warnings.append("AVISO: el orden cambia según el perfil de pesos ("
                        + "; ".join(f"{p}: {_join(o)}" for p, o in orders.items())
                        + "): el ranking depende de los pesos elegidos; no lo presentes como robusto.")
    tops = {p: o[:top_k] for p, o in orders.items()}
    if len({frozenset(t) for t in tops.values()}) > 1:
        warnings.append(f"AVISO: el conjunto del top-{top_k} cambia según el perfil de pesos ("
                        + "; ".join(f"{p}: {', '.join(t) or '-'}" for p, t in tops.items())
                        + "): compara los perfiles antes de proponer candidatas.")
    mc = [r.opportunity_id for r in top]
    reference = {profile: tops[profile]} if profile in tops else tops
    if reference and all(frozenset(mc) != frozenset(t) for t in reference.values()):
        warnings.append(f"AVISO: el top-{top_k} del Monte Carlo ({', '.join(mc) or '-'}), con la incertidumbre de las "
                        "puntuaciones y los pesos perturbados, no coincide con el orden determinista "
                        + ("del perfil " + profile + f" ({', '.join(tops[profile]) or '-'})" if profile in tops
                           else "de ningún perfil")
                        + f": el top-{top_k} no es robusto.")
    return warnings


def format_table(rows: list[RankRow], top_k: int = 3, profile: str | None = None) -> str:
    profiles = sorted({p for r in rows for p in r.ranks_by_profile})
    header = (f"{'Oportunidad':<10} {'Estado':<12} {'Punt.':>6} {'P50 [P5-P95]':>20} {f'P(top{top_k})':>9} "
              f"{'Rango':>6} {'Valor':>6} {'Viab.':>6}  " + " ".join(f"{p[:5]:>5}" for p in profiles) + "  Cuadrante")
    lines = []
    warnings = ranking_warnings(rows, top_k, profile)
    if warnings:
        lines += ["AVISOS:"] + [f"- {w}" for w in warnings] + [""]
    lines += [header, "-" * len(header)]
    current = None
    for r in rows:
        group = (r.status, r.gate_tier)
        if group != current:
            current = group
            label = r.status if r.gate_tier is None else f"{r.status}: {TIER_LABELS[r.gate_tier]}"
            lines.append(f"[{label}]")
        if r.composite is None:
            lines.append(f"{r.opportunity_id:<10} {r.status:<12} {'-':>6} {'-':>20} {'-':>9} "
                         f"{'-':>6} {'-':>6} {'-':>6}")
        else:
            interval = f"{r.p50:.1f} [{r.p05:.1f}-{r.p95:.1f}]" if r.p50 is not None else "-"
            ptop = f"{r.p_top:.2f}" if r.p_top is not None else "-"
            mrank = f"{r.median_rank:.0f}" if r.median_rank is not None else "-"
            prof = " ".join(f"{r.ranks_by_profile.get(p, '-'):>5}" for p in profiles)
            lines.append(f"{r.opportunity_id:<10} {r.status:<12} {r.composite:>6.1f} {interval:>20} {ptop:>9} "
                         f"{mrank:>6} {r.value_axis:>6.1f} {r.feasibility_axis:>6.1f}  {prof}  {r.quadrant}")
        if r.title:
            lines.append(f"{'':<10} {r.title}")
        for flag in r.flags:
            lines.append(f"{'':<10} ! {flag}")
    lines.append("")
    lines.append("Punt. = puntuación con los valores modales y los pesos nominales. P50 [P5-P95] = distribución "
                 "Monte Carlo con los rangos de incertidumbre y pesos perturbados (difiere de Punt. si los rangos son "
                 "asimétricos). P(top-k) y Rango se calculan dentro de cada grupo (APTA y cada subgrupo de "
                 "CONDICIONAL). Las columnas de perfiles dan la posición con cada perfil de pesos; si el orden o el "
                 "top-k cambian entre perfiles, se avisa arriba.")
    lines.append("La puntuación es orientativa y no demuestra eficacia. Las APTA tienen todas las compuertas superadas "
                 "y firmadas por una persona; las CONDICIONALES no pueden pasar a validación experimental y se ordenan "
                 "en tres subgrupos, sea cual sea su puntuación: solo pendientes de firma, con compuertas sin resolver "
                 "y con compuertas CRÍTICAS (seguridad, regulatoria, destino digestivo) sin resolver; las "
                 "INSUFICIENTES necesitan más evaluadores independientes; las BLOQUEADAS no compiten.")
    return "\n".join(lines)
