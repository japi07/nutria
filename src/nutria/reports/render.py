"""Genera el esqueleto de un informe a partir del registro: oportunidad (especie, etapa, raza y estado fisiológico
si constan), hipótesis, destino digestivo, ruta regulatoria, estado EFECTIVO de las compuertas, alegación prevista y
decisiones.

Lo que falta en el registro queda como `[POR COMPLETAR]` (contenido por redactar: en revisado o final es un error de
`nutria informe comprobar`) o como `[DESC]` con la forma de obtenerlo (dato desconocido).
"""

from __future__ import annotations

from datetime import date
from importlib import resources

from .. import db

MARK = "[POR COMPLETAR]"

_CLAIM_TYPES = {
    "funcional": ("funcional: art. 13.1 (objetiva, verificable por las autoridades, comprensible y con justificación "
                  "científica); puede referirse a la optimización de la nutrición o al apoyo o la protección de las "
                  "condiciones fisiológicas (art. 13.2)"),
    "finalidad_nutricional_especifica": ("finalidad nutricional específica: solo en piensos dietéticos, para un uso "
                                         "previsto del Reglamento (UE) 2020/354"),
    "ninguna": "sin alegación",
}


def _oneline(value) -> str:
    return " ".join(str(value).split())


def _cell(value) -> str:
    return _oneline(value).replace("|", "/") or "—"


def _yaml(value) -> str:
    return "'" + _oneline(value).replace("'", "''") + "'"


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _num(value: float) -> str:
    return f"{value:.3g}".replace(".", ",")


def _bullets(items, missing: str) -> str:
    if not items:
        return f"{MARK} {missing}"
    if isinstance(items, str):
        return items
    return "\n".join(f"- {_oneline(x)}" for x in items)


def _claim_text(claim) -> str:
    if not claim:
        return (f"{MARK} define la alegación prevista y su tipo: funcional (art. 13.1: objetiva, verificable y con "
                "justificación científica; puede referirse a la optimización de la nutrición o al apoyo o la "
                "protección de las condiciones fisiológicas, art. 13.2), finalidad nutricional específica (solo en "
                "piensos dietéticos, Reglamento (UE) 2020/354) o ninguna. Ningún pienso puede alegar que previene, "
                "trata o cura enfermedades (art. 13.3).")
    tipo = claim.get("tipo") or "indeterminado"
    text = f"{_oneline(claim.get('texto', ''))} (tipo {_CLAIM_TYPES.get(tipo, tipo)})"
    if claim.get("fundamento"):
        text += f". Fundamento previsto: {_oneline(claim['fundamento'])}"
    return text + ". No puede alegar que previene, trata o cura enfermedades (art. 13.3)."


def _gate_ids(gate_ids) -> list[str]:
    if gate_ids:
        return list(gate_ids)
    from ..scoring.engine import load_rubric
    return [g.id for g in load_rubric().gates]


def _gate_last(raw: dict | None) -> str:
    if raw is None:
        return "sin propuesta registrada"
    kind = "decisión firmada" if raw.get("human_signoff") else "propuesta sin firmar"
    return f"{raw['status']} ({kind}; {raw.get('decided_by') or 'n/d'}; {(raw.get('updated_at') or '')[:10]})"


def _gate_row(gate: str, raw: dict | None, effective: str) -> str:
    signed = "—" if raw is None else (raw.get("human_signoff") or "sin firma")
    return f"| {gate} | {effective} | {_cell(_gate_last(raw))} | {_cell(signed)} |"


def _gates_table(conn, opp_id: str, gate_ids: list[str]) -> tuple[str, dict, dict]:
    raw = {g["gate"]: g for g in db.list_gates(conn, opp_id)}
    effective = db.effective_gates(conn, opp_id, gate_ids)
    rows = ["| Compuerta | Estado efectivo | Último registro | Firma humana |", "|---|---|---|---|"]
    rows += [_gate_row(g, raw.get(g), effective.get(g, "no_resuelta")) for g in gate_ids]
    return "\n".join(rows), raw, effective


def _route_text(opp: dict, data: dict) -> str:
    route = opp.get("regulatory_route") or data.get("ruta_regulatoria_preliminar")
    if not route:
        return (f"Ruta regulatoria preliminar: {MARK} la oportunidad no tiene ruta en el registro; aplica el triage "
                "regulatorio (docs/REGULACION_UE.md).")
    note = ""
    if route in db.OUT_OF_SCOPE_ROUTES:
        note = "; fuera del alcance de un pienso: la compuerta regulatoria queda bloqueada automáticamente"
    elif route == "indeterminada":
        note = "; indeterminada: el triage regulatorio debe concretarla"
    return (f"Ruta regulatoria preliminar del registro: `{route}` [SUP] (propuesta, pendiente de confirmar en el "
            f"triage regulatorio{note}).")


def _destino_text(data: dict, gate_line: str) -> str:
    dd = data.get("destino_digestivo") or {}
    esc, di = dd.get("fraccion_escape_ruminal"), dd.get("digestibilidad_intestinal")
    source = _oneline(dd.get("fuente_o_calculo") or "")
    origin = f"fuente o cálculo declarado en el registro: {source}" if source else "sin fuente declarada en el registro"
    lines = []
    if _is_num(esc):
        lines.append(f"- Fracción que escapa a la degradación ruminal: {_num(esc)} [SUP] ({origin}; sustituye la "
                     "etiqueta por `[PUB]` con su `[Rn]` o por `[DER]` con el cálculo cuando proceda).")
    else:
        lines.append("- Fracción que escapa a la degradación ruminal: [DESC] sin dato en el registro; obtenla de la "
                     "degradabilidad publicada (fracciones a y b, kd) con `nutria destino ruminal --a A --b B --kd KD "
                     "--kp KP` o de ensayos in situ o in vitro.")
    if _is_num(di):
        lines.append(f"- Digestibilidad intestinal de la fracción que escapa: {_num(di)} [SUP] ({origin}).")
    else:
        lines.append("- Digestibilidad intestinal de la fracción que escapa: [DESC] sin dato en el registro; obtenla "
                     "de la literatura (in vivo, bolsas móviles o in vitro) y aplícala con "
                     "`nutria destino ruminal ... --digestibilidad-intestinal DI`.")
    dose = dd.get("dosis") if _is_num(dd.get("dosis")) else None
    unit = _oneline(dd.get("unidad_dosis") or dd.get("dosis_unidad") or "")
    if _is_num(esc) and _is_num(di):
        fraction = esc * di
        lines.append(f"- Fracción absorbible (escape ruminal × digestibilidad intestinal): {_num(fraction)} [DER] "
                     f"(cálculo: {_num(esc)} × {_num(di)}; `nutria destino ruminal --fraccion-escape {esc} "
                     f"--digestibilidad-intestinal {di}`).")
        if dose is not None:
            lines.append(f"- Dosis absorbible a la dosis prevista ({_num(dose)} {unit}): {_num(dose * fraction)} "
                         f"{unit} [DER] (cálculo: {_num(dose)} × {_num(fraction)}; `nutria destino ruminal "
                         f"--fraccion-escape {esc} --digestibilidad-intestinal {di} --dosis {dose}`).")
        else:
            lines.append("- Dosis absorbible: [DESC] falta la dosis prevista; calcúlala con `nutria destino ruminal "
                         "--fraccion-escape F --digestibilidad-intestinal DI --dosis D --unidad-dosis g/animal/d`.")
    else:
        lines.append("- Fracción y dosis absorbibles en el intestino: [DESC] dependen de los dos datos anteriores; "
                     "calcúlalas con `nutria destino ruminal --fraccion-escape F --digestibilidad-intestinal DI "
                     "--dosis D`.")
    lines.append(f"- Transformación o biohidrogenación ruminal del compuesto: {MARK} indica si el compuesto se "
                 "transforma en el rumen y en qué, con su etiqueta (`[PUB]` con `[Rn]`, o `[DESC]` y cómo obtenerlo).")
    if dd.get("comentario"):
        lines.append(f"- Comentario del registro: {_oneline(dd['comentario'])}")
    lines.append(gate_line)
    return "\n".join(lines)


def _context(opp: dict, data: dict) -> tuple[str, str]:
    """(líneas del front matter, texto visible) con el contexto biológico de la oportunidad: especie y etapa y, si
    constan en el registro, raza y estado fisiológico."""
    especie = data.get("especie") or opp.get("species")
    etapa = data.get("etapa") or opp.get("production_stage")
    extra = [("raza", "raza", data.get("raza")), ("estado_fisiologico", "estado fisiológico",
                                                    data.get("estado_fisiologico"))]
    fm = [f"especie: {_yaml(especie) if especie else 'null'}", f"etapa: {_yaml(etapa) if etapa else 'null'}"]

    def shown(value) -> str:
        text = _oneline(value).rstrip(". ")
        return "no consta" if text == "no_consta" else text
    parts = [f"Especie diana: {shown(especie) if especie else MARK + ' especie no registrada en la oportunidad'}",
             f"etapa productiva: {shown(etapa) if etapa else MARK + ' etapa no registrada en la oportunidad'}"]
    for key, label, value in extra:
        if value:
            fm.append(f"{key}: {_yaml(value)}")
            parts.append(f"{label}: {shown(value)}")
    # Una sola frase con su etiqueta: es el ámbito que fija la oportunidad (supuesto de trabajo hasta contrastarlo con
    # la evidencia), y así una cifra de la etapa (p. ej. un peso vivo) no queda sin etiquetar.
    return "\n".join(fm), "; ".join(parts) + " [SUP] (contexto biológico de la oportunidad en el registro)."


def new_report(conn, opp_id: str, gate_ids=None) -> str:
    opp = db.get_opportunity(conn, opp_id)
    if opp is None:
        raise db.RegistryError(f"No existe la oportunidad {opp_id}")
    data = opp.get("data") or {}
    hyps = db.list_hypotheses(conn, opportunity_id=opp_id)
    hyp_text = "\n".join(
        f"- **{h['id']}** ({h['status']}): {_oneline(h['title'])} [HIP]" for h in hyps
    ) or f"{MARK} sin hipótesis registradas para {opp_id}: regístralas con `nutria hipotesis nueva`."
    gates = _gate_ids(gate_ids)
    table, raw, effective = _gates_table(conn, opp_id, gates)
    gate_line = (f"- Compuerta `destino_digestivo`: {effective.get('destino_digestivo', 'no_resuelta')} (último "
                 f"registro: {_gate_last(raw.get('destino_digestivo'))}).")
    decisions = db.list_decisions(conn, opp_id)
    dec_text = "\n".join(
        f"- {d['ts']} | {_cell(d['decision'])} | {_cell(d['rationale'])} | {d['decided_by']}"
        + (f" | firma: {d['human_signoff']}" if d.get("human_signoff") else "")
        for d in decisions
    ) or "- Sin decisiones registradas."
    contexto_fm, contexto = _context(opp, data)
    template = resources.files("nutria.reports").joinpath("plantilla_informe.md").read_text("utf-8")
    return template.format(
        opp_id=opp_id, fecha=date.today().isoformat(), titulo=_oneline(opp["title"]), contexto_fm=contexto_fm,
        contexto=contexto,
        problema=data.get("problema") or opp.get("problem") or f"{MARK} describe el problema productivo o fisiológico.",
        hipotesis=hyp_text, candidatos=_bullets(data.get("candidatos"), "sin candidatos registrados."),
        destino=_destino_text(data, gate_line), ruta=_route_text(opp, data), compuertas=table,
        decisiones=dec_text, alegacion=_claim_text(data.get("alegacion_prevista")),
    )
