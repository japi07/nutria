import pytest

from nutria.scoring.engine import load_rubric, rank

RUBRIC = load_rubric()
CRITS = [c.id for c in RUBRIC.criteria]
ALL_PASS = {g.id: "superada" for g in RUBRIC.gates}
RATERS = ("a", "b", "c")


def opp(oid, value, gates=None, raters=RATERS, spread=0.1, status="propuesta"):
    scores = [{"rater": r, "criterion": c, "value": value, "low": max(0, value - spread),
               "high": min(1, value + spread)} for r in raters for c in CRITS]
    return {"id": oid, "title": oid, "scores": scores, "gates": dict(ALL_PASS) if gates is None else gates,
            "status": status}


def test_rubric_has_nine_criteria_and_six_gates():
    assert len(CRITS) == 9 and "rentabilidad_ganadero" in CRITS and "atractivo_empresa" in CRITS
    assert {g.id for g in RUBRIC.gates} == {"seguridad", "regulatoria", "analitica", "propiedad_intelectual",
                                            "destino_digestivo", "etica"}


def test_profiles_sum_100_and_cover_criteria():
    for profile in RUBRIC.profiles:
        w = RUBRIC.weights(profile)
        assert sum(w.values()) == pytest.approx(100)
        assert set(w) == set(CRITS)


def test_original_profile_keeps_prompt_weights_with_split_economics():
    w = RUBRIC.profiles["original"]["pesos"]
    assert w == {"evidencia": 20, "originalidad": 15, "plausibilidad": 15, "mejora_productiva": 15,
                 "seguridad": 10, "viabilidad_tecnologica": 10, "viabilidad_regulatoria": 5,
                 "rentabilidad_ganadero": 5, "atractivo_empresa": 5}


def test_conditional_never_outranks_apta_and_blocked_is_excluded():
    pending = dict(ALL_PASS, analitica="no_resuelta")
    blocked = dict(ALL_PASS, seguridad="bloqueada")
    opps = [opp("O-0001", 0.4), opp("O-0002", 0.95, gates=pending), opp("O-0003", 0.99, gates=blocked),
            opp("O-0004", 0.6), opp("O-0005", 0.9, status="bloqueada")]
    rows = rank(opps, RUBRIC, n_sim=2000, seed=1, top_k=1)
    order = [r.opportunity_id for r in rows]
    status = {r.opportunity_id: r.status for r in rows}
    assert order[:2] == ["O-0004", "O-0001"]          # las APTA primero, aunque puntúen menos
    assert status["O-0002"] == "CONDICIONAL" and order[2] == "O-0002"
    assert status["O-0003"] == "BLOQUEADA" and status["O-0005"] == "BLOQUEADA"
    apta = [r for r in rows if r.status == "APTA"]
    assert sum(r.p_top for r in apta) == pytest.approx(1.0)
    again = rank(opps, RUBRIC, n_sim=2000, seed=1, top_k=1)
    assert [r.p50 for r in again] == [r.p50 for r in rows]


def test_missing_gates_count_as_unresolved():
    rows = rank([opp("O-0001", 0.9, gates={})], RUBRIC, n_sim=100)
    assert rows[0].status == "CONDICIONAL"


def test_minimum_raters_and_disagreement():
    few = opp("O-0001", 0.5, raters=("a", "b"))
    split = opp("O-0002", 0.5)
    split["scores"] = [dict(s, value=0.0, low=0.0, high=0.0) if s["rater"] == "a" and s["criterion"] == "seguridad"
                       else s for s in split["scores"]]
    split["scores"] = [dict(s, value=1.0, low=1.0, high=1.0) if s["rater"] == "b" and s["criterion"] == "seguridad"
                       else s for s in split["scores"]]
    partial = {"id": "O-0003", "title": "x", "gates": ALL_PASS, "status": "propuesta",
               "scores": [{"rater": "a", "criterion": "evidencia", "value": 0.5}]}
    rows = {r.opportunity_id: r for r in rank([few, split, partial], RUBRIC, n_sim=100)}
    assert rows["O-0001"].status == "INSUFICIENTE"
    assert any("Desacuerdo" in f and "seguridad" in f for f in rows["O-0002"].flags)
    assert rows["O-0003"].status == "INCOMPLETA"


def test_ranks_by_profile_are_reported():
    rows = rank([opp("O-0001", 0.5), opp("O-0002", 0.7)], RUBRIC, n_sim=200)
    for r in rows:
        assert set(r.ranks_by_profile) == set(RUBRIC.profiles)


def test_rubric_markdown_mentions_gates():
    md = RUBRIC.to_markdown()
    assert "Compuertas" in md and "destino_digestivo" in md
