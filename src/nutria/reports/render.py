"""Genera el esqueleto de un informe a partir del registro (oportunidad, hipótesis, compuertas, decisiones)."""

from __future__ import annotations

from datetime import date
from importlib import resources

from .. import db


def _bullets(items) -> str:
    if not items:
        return "[DESC] Pendiente."
    if isinstance(items, str):
        return items
    return "\n".join(f"- {x}" for x in items)


def _claim_text(claim) -> str:
    if not claim:
        return ("[DESC] Pendiente: define la alegación prevista (art. 13.1 funcional; art. 13.2 solo piensos "
                "dietéticos) y comprueba que no alega prevenir, tratar o curar enfermedades (art. 13.3).")
    text = f"{claim.get('texto', '')} (tipo: {claim.get('tipo', 'indeterminado')})"
    if claim.get("fundamento"):
        text += f". Fundamento previsto: {claim['fundamento']}"
    return text


def new_report(conn, opp_id: str) -> str:
    opp = db.get_opportunity(conn, opp_id)
    if opp is None:
        raise db.RegistryError(f"No existe la oportunidad {opp_id}")
    data = opp.get("data") or {}
    hyps = db.list_hypotheses(conn, opportunity_id=opp_id)
    hyp_text = "\n".join(
        f"- **{h['id']}** ({h['status']}): {h['title']} [HIP]" for h in hyps
    ) or "[DESC] Sin hipótesis registradas."
    gates = db.list_gates(conn, opp_id)
    gate_text = ", ".join(f"{g['gate']}={g['status']}" for g in gates) or "sin evaluar"
    decisions = db.list_decisions(conn, opp_id)
    dec_text = "\n".join(
        f"- {d['ts']} | {d['decision']} | {d['rationale']} | {d['decided_by']}"
        + (f" | firma: {d['human_signoff']}" if d.get("human_signoff") else "")
        for d in decisions
    ) or "- Sin decisiones registradas."
    template = resources.files("nutria.reports").joinpath("plantilla_informe.md").read_text("utf-8")
    return template.format(
        opp_id=opp_id, fecha=date.today().isoformat(), titulo=opp["title"],
        problema=data.get("problema") or opp.get("problem") or "[DESC] Pendiente.",
        hipotesis=hyp_text, candidatos=_bullets(data.get("candidatos")), compuertas=gate_text,
        decisiones=dec_text, alegacion=_claim_text(data.get("alegacion_prevista")),
    )
