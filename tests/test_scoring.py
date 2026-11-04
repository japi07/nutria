import pytest

from nutria.scoring.engine import load_rubric, rank

RUBRIC = load_rubric()
CRITS = [c.id for c in RUBRIC.criteria]
ALL_PASS = {g.id: "superada" for g in RUBRIC.gates}


def opp(oid, value, gates=None, raters=("a", "b"), spread=0.1):
    scores = [{"rater": r, "criterion": c, "value": value, "low": max(0, value - spread),
               "high": min(1, value + spread)} for r in raters for c in CRITS]
    return {"id": oid, "title": oid, "scores": scores, "gates": gates or dict(ALL_PASS)}


def test_profiles_sum_100_and_cover_criteria():
    for profile in RUBRIC.profiles:
        w = RUBRIC.weights(profile)
        assert sum(w.values()) == pytest.approx(100)
        assert set(w) == set(CRITS)


def test_original_profile_matches_prompt_weights():
    w = RUBRIC.profiles["original"]["pesos"]
    assert w == {"evidencia": 20, "originalidad": 15, "plausibilidad": 15, "mejora_productiva": 15,
                 "seguridad": 10, "viabilidad_tecnologica": 10, "viabilidad_regulatoria": 5,
                 "potencial_economico": 10}


def test_ranking_order_gates_and_reproducibility():
    blocked = dict(ALL_PASS, seguridad="bloqueada")
    pending = dict(ALL_PASS, analitica="no_resuelta")
    opps = [opp("O-0001", 0.5), opp("O-0002", 0.8), opp("O-0003", 0.95, gates=blocked),
            opp("O-0004", 0.3, gates=pending)]
    rows = rank(opps, RUBRIC, n_sim=2000, seed=1, top_k=1)
    assert rows[0].opportunity_id == "O-0002"
    status = {r.opportunity_id: r.status for r in rows}
    assert status["O-0003"] == "BLOQUEADA" and rows[-1].opportunity_id == "O-0003"
    assert status["O-0004"] == "CONDICIONAL"
    eligible = [r for r in rows if r.status != "BLOQUEADA"]
    assert sum(r.p_top for r in eligible) == pytest.approx(1.0)
    again = rank(opps, RUBRIC, n_sim=2000, seed=1, top_k=1)
    assert [r.p50 for r in again] == [r.p50 for r in rows]


def test_incomplete_and_disagreement_flags():
    partial = {"id": "O-0009", "title": "x", "gates": ALL_PASS,
               "scores": [{"rater": "a", "criterion": "evidencia", "value": 0.5}]}
    split = opp("O-0010", 0.5)
    split["scores"] = [dict(s, value=0.0, low=0.0, high=0.0) if s["rater"] == "a" and s["criterion"] == "seguridad"
                       else s for s in split["scores"]]
    split["scores"] = [dict(s, value=1.0, low=1.0, high=1.0) if s["rater"] == "b" and s["criterion"] == "seguridad"
                       else s for s in split["scores"]]
    rows = {r.opportunity_id: r for r in rank([partial, split], RUBRIC, n_sim=100)}
    assert rows["O-0009"].status == "INCOMPLETA"
    assert any("Desacuerdo" in f and "seguridad" in f for f in rows["O-0010"].flags)


def test_rubric_markdown_mentions_gates():
    md = RUBRIC.to_markdown()
    assert "Compuertas" in md and "evidencia" in md
