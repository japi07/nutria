"""Pruebas del paquete de orquestación: CLAUDE.md, .claude/agents, .claude/skills y .claude/workflows.

- Comprobaciones estáticas frente al contrato de diseño (compuertas propuestas, 9 criterios, evaluadores ciegos,
  verificación manual solo por personas, Anexo A, hechos regulatorios verificados).
- Ejecución de los workflows con agentes simulados (`tests/orquestacion_harness.cjs`; requiere node). El arnés
  desactiva Date.now(), Math.random() y new Date() sin argumentos, como el entorno real de los workflows.
"""
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLAUDE_MD = ROOT / "CLAUDE.md"
AGENTS_DIR = ROOT / ".claude" / "agents"
SKILLS_DIR = ROOT / ".claude" / "skills"
WORKFLOWS_DIR = ROOT / ".claude" / "workflows"
WORKFLOWS = sorted(WORKFLOWS_DIR.glob("*.js"))
HARNESS = Path(__file__).with_name("orquestacion_harness.cjs")
NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node no está disponible")

CRITERIOS = ["evidencia", "plausibilidad", "mejora_productiva", "originalidad", "seguridad",
             "viabilidad_tecnologica", "viabilidad_regulatoria", "rentabilidad_ganadero", "atractivo_empresa"]
COMPUERTA_DE = {"compuestos-toxicologia": "seguridad", "regulacion-seguridad": "regulatoria",
                "desarrollo-industrial": "analitica", "economia-mercado-pi": "propiedad_intelectual",
                "sostenibilidad-bienestar": "etica", "fisiologia-bioquimica": "destino_digestivo"}
HERRAMIENTAS = {"Read", "Grep", "Glob", "Bash", "Write", "Edit", "MultiEdit", "WebSearch", "WebFetch", "NotebookEdit"}
MODELOS = {"opus", "sonnet", "haiku", "inherit"}
# Orden de comprobación de sintaxis indicada para los workflows.
SYNTAX_CHECK = ('const fs=require("fs");const s=fs.readFileSync(process.argv[1],"utf8")'
                '.replace(/^export const meta/m,"const meta");new (Object.getPrototypeOf(async function(){})'
                '.constructor)("args","agent","parallel","pipeline","phase","log","budget","workflow",s)')


# ------------------------------------------------------------------ utilidades
def _text(path: Path) -> str:
    return path.read_text("utf-8")


def _norm(text: str) -> str:
    """Texto con los espacios y saltos de línea normalizados (las frases pueden partirse entre líneas)."""
    return " ".join(text.split())


def _frontmatter(path: Path) -> tuple[dict, str]:
    text = _text(path)
    assert text.startswith("---\n"), f"{path.name}: falta el frontmatter"
    end = text.index("\n---\n", 4)
    meta = {}
    for line in text[4:end].splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, text[end + 5:]


def _agent(name: str) -> tuple[dict, str]:
    return _frontmatter(AGENTS_DIR / f"{name}.md")


def _skill(name: str) -> str:
    return _text(SKILLS_DIR / name / "SKILL.md")


def _package_files() -> list[Path]:
    return [CLAUDE_MD, *sorted(AGENTS_DIR.glob("*.md")), *sorted(SKILLS_DIR.glob("*/SKILL.md")), *WORKFLOWS]


def _meta_block(js: str) -> str:
    start = js.index("export const meta")
    return js[start:js.index("\n}\n", start)]


def _rubric() -> dict:
    return tomllib.loads(_text(ROOT / "src" / "nutria" / "data" / "rubrica.toml"))


def run_workflow(name, args=None, reglas=(), presupuesto_total=None, coste_por_agente=0):
    escenario = {"args": args, "reglas": list(reglas), "presupuesto_total": presupuesto_total,
                 "coste_por_agente": coste_por_agente}
    proc = subprocess.run([NODE, str(HARNESS), str(WORKFLOWS_DIR / f"{name}.js")], input=json.dumps(escenario),
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["error"] is None, out["error"]
    return out


def labels(out) -> list[str]:
    return [c["label"] for c in out["llamadas"]]


def call(out, label: str) -> dict:
    return next(c for c in out["llamadas"] if c["label"] == label)


# ------------------------------------------------------------------ agentes
@pytest.mark.parametrize("path", sorted(AGENTS_DIR.glob("*.md")), ids=lambda p: p.stem)
def test_agent_frontmatter_is_valid(path):
    meta, body = _frontmatter(path)
    assert meta.get("name") == path.stem
    assert len(meta.get("description", "")) > 40
    tools = {t.strip() for t in meta.get("tools", "").split(",") if t.strip()}
    assert tools and tools <= HERRAMIENTAS, tools - HERRAMIENTAS
    assert meta.get("model") in MODELOS
    assert body.strip()


def test_evaluador_independiente_is_blind_and_read_only():  # D1-3, D2-1, D2-2, D2-3
    meta, body = _agent("evaluador-independiente")
    assert [t.strip() for t in meta["tools"].split(",")] == ["Read", "Grep", "Glob", "Bash"]
    assert meta["model"] == "inherit"
    text = _norm(body)
    assert "oportunidad ver <ID> --sin-puntuaciones --json" in text
    assert "Nunca ejecutes `nutria puntuar ranking`" in text
    assert "research/oportunidades/*_puntuacion_*.json" in text
    assert "docs/RUBRICA.md" in text
    assert "nutria --agente <id> puntuar registrar" in text
    for criterio in CRITERIOS:
        assert f"`{criterio}`" in text


def test_director_never_scores_nor_reads_ranking_before_opining():  # D2-1, D2-2, D2-3
    meta, body = _agent("director-cientifico")
    text = _norm(body)
    assert "**Nunca puntúas**" in text
    assert "puntuar ranking" not in text
    assert "puntuar registrar" not in text
    assert "oportunidad ver <ID> --sin-puntuaciones --json" in text
    assert "Nunca puntúa" in meta["description"]


def test_director_plans_include_validation_checklist():  # D1-12
    text = _norm(_agent("director-cientifico")[1])
    for fragment in ("de animales donantes (procedimiento autorizado) o de matadero", "Directiva 2010/63/UE",
                     "Real Decreto 53/2013", "La técnica in situ es un ensayo in vivo", "EFSA J 2024;22(7):e8856",
                     "10.2903/j.efsa.2024.8856", "1-12-2024", "(BPL)", "varianzas reales", "Registro previo del protocolo",
                     "no comprometa la confidencialidad", "Reglamento (UE) 2019/1381", "art. 32b"):
        assert fragment in text, fragment


@pytest.mark.parametrize("agente,compuerta", sorted(COMPUERTA_DE.items()))
def test_each_gate_is_proposed_by_its_owner_without_signature(agente, compuerta):  # D5-10
    _, body = _agent(agente)
    text = _norm(body)
    assert f"puntuar compuerta <ID> {compuerta} " in text
    assert "sin `--firma-humana`" in text
    for otro, otra in COMPUERTA_DE.items():
        if otro != agente:
            assert f"puntuar compuerta <ID> {otra} " not in text, f"{agente} registra la compuerta {otra}"


def test_agents_only_propose_gates_and_never_sign():
    for path in AGENTS_DIR.glob("*.md"):
        text = _text(path)
        assert "Qué decides" not in text, path.name
        assert not re.search(r"\bdecid\w* (las |la )?compuertas?\b", text, re.I), path.name
        for line in text.splitlines():
            if "--firma-humana" in line:
                assert re.search(r"\b(sin|nunca|solo|personas?|no)\b", line, re.I), f"{path.name}: {line}"


def test_regulacion_seguridad_applies_verified_facts():  # D1-6, D5-1..D5-6, D5-11
    meta, body = _agent("regulacion-seguridad")
    text = _norm(body)
    assert text.index("2020/354") < text.index("2019/6")
    for fragment in ("art. 4", "**alegación**", "**y también por función**", "farmacológica, inmunológica o metabólica",
                     "anexo I del Reglamento (CE) 1831/2003", "vitaminas, compuestos de oligoelementos, aminoácidos y urea",
                     "art. 6 y anexo III", "Directiva 96/22/CE", "2003/74/CE", "2008/97/CE", "restricciones de EET",
                     "promotores del crecimiento", "verificar artículo", "art. 13", "art. 32a", "art. 32b",
                     "10.2903/j.efsa.2024.8856", "2024/2899", "estabilizadores de las condiciones fisiológicas",
                     "otros aditivos tecnológicos", "puntuar compuerta <ID> regulatoria"):
        assert fragment in text, fragment
    assert "compuestos-toxicologia" in text  # la compuerta seguridad la propone toxicología
    assert "decide" not in meta["description"]


def test_fisiologia_proposes_digestive_fate_with_nutria_destino():  # D5-10, D4-7
    text = _norm(_agent("fisiologia-bioquimica")[1])
    assert "destino ruminal --a <a> --b <b> --kd <kd> --kp <kp>" in text
    assert "destino ruminal --fraccion-escape <F>" in text
    assert "DE = a + b·kd/(kd + kp)" in text
    assert "10.1017/S0021859600063048" in text
    assert "McDonald (1981, J Agric Sci 96:251-252)" in text
    for item in ("caída de la ingestión preparto", "balance energético negativo", "movilización lipídica",
                 "(AGNE)", "cetogénesis", "(BHB)", "lipidosis hepática", "hipocalcemia", "DCAD = (Na + K) − (Cl + S)",
                 "inflamación y disfunción inmunitaria", "estrés oxidativo"):
        assert item in text, item
    assert "No uses umbrales numéricos" in text


def test_nutricion_formulacion_uses_official_software_not_memory():  # D4-2
    text = _norm(_agent("nutricion-formulacion")[1])
    assert "`nutria` no implementa sistemas de racionamiento" in text
    assert "software del modelo NASEM 2021" in text
    assert "registra la versión y todas las entradas" in text
    assert "Nunca calcules ecuaciones de requerimientos de memoria" in text


def test_economia_runs_farm_model_before_scoring():  # D8-8
    meta, body = _agent("economia-mercado-pi")
    text = _norm(body)
    assert "Antes de que se puntúe la oportunidad" in text
    assert "econ ganadero research/datos/<ID>/econ_ganadero.toml" in text
    assert "econ desarrollador" in text
    assert "`rentabilidad_ganadero`" in text and "`atractivo_empresa`" in text
    assert "No puntúas" in text
    assert "ROI > 1" in text


def test_verificador_fills_annex_a_and_never_registers_manual_checks():  # D3-3
    meta, body = _agent("verificador-citas")
    assert "Edit" in {t.strip() for t in meta["tools"].split(",")}  # rellena la tabla del Anexo A
    text = _norm(body)
    assert "| Ref | Afirmación | Respaldo | Motivo |" in text
    for valor in ("`respalda`", "`parcial`", "`no_respalda`", "`no_verificable`"):
        assert valor in text
    assert "**No registras verificaciones manuales**" in text
    assert "(solo personas)" in text
    assert "verificar <fichero.md>" in text


def test_extractor_double_extraction_files_and_comparison():
    text = _norm(_agent("extractor-datos")[1])
    assert "extraccion_<fuente>_A.json" in text and "_B.json" in text
    assert "extraccion comparar <A.json> <B.json>" in text
    assert "**No la registres**" in text


def test_estadistico_uses_contract_options():
    text = _norm(_agent("estadistico")[1])
    for fragment in ("--ajuste hksj-mod", "--agregar-estudio --rho 0.5", "meta stpierre <csv> --respuesta <Y>",
                     "--pendiente-aleatoria", "meta validar-predictivo"):
        assert fragment in text, fragment


# ------------------------------------------------------------------ CLAUDE.md
def test_claude_md_roles_and_contract():  # D2-11
    text = _norm(_text(CLAUDE_MD))
    assert "La **sesión principal** es el **director científico y orquestador**" in text
    assert "**Un subagente sigue su propio rol**" in text
    for compuerta in COMPUERTA_DE.values():
        assert f"`{compuerta}`" in text
    for criterio in CRITERIOS:
        assert f"`{criterio}`" in text
    for fragment in ("**Los agentes solo proponen compuertas**", "una `superada` solo cuenta cuando la firma una persona",
                     "salir de `bloqueada` exige firma humana", "`fuera_de_alcance_*` bloquean automáticamente",
                     "`evaluador-independiente`", "El director nunca puntúa",
                     "nutria verificar manual <URL> --firma-humana NOMBRE", "verificación en línea **completa**",
                     "| Ref | Afirmación | Respaldo | Motivo |", "cuenta de Anthropic del usuario",
                     "sensibilidad por defecto es `confidencial`", "proveedores adicionales",
                     "APTA", "CONDICIONAL", "BLOQUEADA", "INCOMPLETA", "INSUFICIENTE", "Alfa de Dirichlet por defecto 50",
                     "nutria destino ruminal", "nutria extraccion comparar A.json B.json", "--ajuste hksj|hksj-mod|ninguno",
                     "nutria meta validar-predictivo", "ROI > 1"):
        assert fragment in text, fragment


def test_claude_md_weights_match_rubric_profiles():
    rubric = _rubric()
    assert {c["id"] for c in rubric["criterio"]} == set(CRITERIOS)
    text = _norm(_text(CLAUDE_MD))
    for perfil in ("recomendado", "original"):
        pesos = rubric["perfiles"][perfil]["pesos"]
        assert f"perfil {perfil}: " + ", ".join(f"{pesos[c]:g}" for c in CRITERIOS) in text, perfil


def test_claude_md_and_workflow_descriptions_do_not_claim_determinism():  # D2-13
    assert not re.search(r"determinist|reproducib", _text(CLAUDE_MD), re.I)
    for path in WORKFLOWS:
        assert not re.search(r"determinist|reproducib", _meta_block(_text(path)), re.I), path.name


def test_scoring_documents_list_exactly_the_rubric_criteria():
    ids = {c["id"] for c in _rubric()["criterio"]}
    for path in (SKILLS_DIR / "puntuacion" / "SKILL.md", AGENTS_DIR / "evaluador-independiente.md"):
        text = _text(path)
        for criterio in ids:
            assert f"`{criterio}`" in text, (path.name, criterio)
    for path in _package_files():
        assert "potencial_economico" not in _text(path), path.name
        assert not re.search(r"--hksj\b", _text(path)), path.name


# ------------------------------------------------------------------ skills
def test_triage_regulatorio_order_and_verified_facts():  # D1-6, D5-1..D5-6, D5-11
    text = _norm(_skill("triage-regulatorio"))
    arbol = text[text.index("## Árbol de decisión"):text.index("## Seguridad")]
    assert arbol.index("2020/354") < arbol.index("2019/6"), "primero pienso dietético, después medicamento"
    for fragment in ("art. 4", "por **alegación o presentación**", "por **función**",
                     "farmacológica, inmunológica o metabólica", "Recomendación 2011/25/UE",
                     "anexo I del Reglamento (CE) 1831/2003", "vitaminas, compuestos de oligoelementos, aminoácidos y urea",
                     "767/2009, art. 6 y anexo III", "semillas tratadas con fitosanitarios tras la cosecha",
                     "Directiva 96/22/CE", "2003/74/CE", "2008/97/CE", "restricciones de EET",
                     "antibióticos como promotores del crecimiento", "verificar artículo", "2019/1381", "art. 32a",
                     "art. 32b", "27-03-2021", "EFSA J 2024;22(7):e8856", "6-6-2024", "1-12-2024",
                     "10.2903/j.efsa.2018.5274", "2024/2899", "(Na + K) − (Cl + S)", "13.1", "13.2", "13.3.a",
                     "estabilizadores de las condiciones fisiológicas", "otros aditivos tecnológicos"):
        assert fragment in text, fragment
    assert "decidir las compuertas" not in text
    assert "puntuar compuerta <ID> regulatoria" in text


def test_puntuacion_skill_uses_blind_independent_evaluators():
    text = _norm(_skill("puntuacion"))
    for fragment in ("9 criterios", "`evaluador-independiente`", "--sin-puntuaciones --json",
                     "nunca ejecuta `nutria puntuar ranking`", "identificador distinto", "modelos distintos",
                     "INSUFICIENTE (menos de 3 evaluadores distintos por criterio)", "--min-evaluadores 3"):
        assert fragment in text, fragment


def test_informe_skill_requires_annex_a_complete_verification_and_human_review():
    text = _norm(_skill("informe"))
    for fragment in ("| Ref | Afirmación | Respaldo | Motivo |", "`respalda`, `parcial`, `no_respalda` o `no_verificable`",
                     "`verificador-citas` rellena", "verificación en línea **completa**", "`revisado_por_humano`",
                     "nutria verificar manual <URL> --firma-humana NOMBRE", "ningún agente lo rellena"):
        assert fragment in text, fragment


def test_metaanalisis_skill_uses_contract_options():
    text = _norm(_skill("metaanalisis"))
    for fragment in ("--medida MD|SMD|ROM|RR|OR|RD", "--metodo REML|DL|PM|FE", "--ajuste hksj|hksj-mod|ninguno",
                     "--agregar-estudio --rho 0.5", "--subgrupo diseno", "meta stpierre", "--pendiente-aleatoria",
                     "meta validar-predictivo", "extraccion comparar A.json B.json", "extraccion exportar --variable"):
        assert fragment in text, fragment


def test_estado_skill_lists_unsigned_gates():
    text = _norm(_skill("estado"))
    assert "**compuertas sin firmar**" in text
    assert "`destino_digestivo`" in text
    assert "INSUFICIENTES" in text
    assert "verificación manual" in text


def test_oportunidad_skill_starts_with_quick_discard():
    text = _norm(_skill("oportunidad"))
    assert text.index("**Descarte rápido") < text.index("**Evidencia (F4)**")
    for fragment in ("nutria destino ruminal", "nutria econ ganadero", "**detente**", "extraccion comparar",
                     "tres `evaluador-independiente`"):
        assert fragment in text, fragment


def test_mision1_skill_matches_workflow_phases():  # D2-6, D1-5, D1-9
    text = _norm(_skill("mision-1"))
    for fragment in ('{"fase": "mapa"}', '"fase": "oportunidades", "problemas"', '"fase": "planes", "seleccion"',
                     "**conserva las soluciones actuales de cada problema**", "nutria verificar research/revisiones/M1a_mapa_problemas.md",
                     "nutria verificar research/revisiones/M1_ranking.md", "nutria verificar research/planes/<ID>.md",
                     "**tres `evaluador-independiente`**", "9 criterios", "`destino_digestivo`"):
        assert fragment in text, fragment
    assert "8 criterios" not in text


def test_red_team_skill_quorum_and_cross_family():
    text = _norm(_skill("red-team"))
    for fragment in ("`sin_quorum`", "`sostenida` solo con 2 o más `sostenida` y ninguna `refutada`",
                     "nutria llm modelos --json", "nutria llm preguntar --rol critico --archivo <fichero>",
                     "sensibilidad por defecto es `confidencial`"):
        assert fragment in text, fragment


# ------------------------------------------------------------------ workflows: estática
@requires_node
@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_workflow_syntax(path):
    proc = subprocess.run([NODE, "-e", SYNTAX_CHECK, str(path)], capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_workflow_avoids_nondeterministic_apis(path):
    assert not re.search(r"Date\.now\s*\(|Math\.random\s*\(|new\s+Date\s*\(\s*\)", _text(path))


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.stem)
def test_workflow_counts_failed_agents(path):  # D2-12, D9-11
    text = _text(path)
    assert "const fallos = []" in text and "fallos: resumenFallos()" in text
    assert len(re.findall(r"\bagent\(", text)) == 1, "todas las llamadas deben pasar por lanzar()"


# ------------------------------------------------------------------ workflows: comportamiento
MINIMOS = {
    "mision-1-auto": None,
    "evaluar-oportunidad-auto": "O-0003",
    "red-team-auto": "research/informes/O-0003.md",
    "comparar-sistemas": {"sistemas": [{"nombre": "nutria", "ruta": "nutria"}]},
}


@requires_node
@pytest.mark.parametrize("nombre", sorted(MINIMOS))
def test_workflow_logs_agent_estimate(nombre):  # D10-3
    out = run_workflow(nombre, MINIMOS[nombre])
    assert any("Estimación" in m for m in out["logs"]), out["logs"]
    assert out["resultado"]["fallos"]["total"] == 0


def _opp(titulo):
    return {"titulo": titulo, "problema": "P1", "especie": "vacuno lechero", "etapa": "transición", "mecanismo": "m",
            "candidatos": ["c"], "tipo_producto": "aditivo", "evidencia_resumen": "e", "diferenciacion": "d",
            "lagunas": ["l"], "hipotesis": ["h"]}


@requires_node
def test_mision1_default_phase_is_map_and_stops():  # D2-6, D1-5, D1-9
    reglas = [{"label": "M1a:consolidacion", "respuesta": {
        "prioritarios": [{"id": "P1", "titulo": "Lipidosis hepática", "especie": "vacuno lechero", "etapa": "transición",
                          "justificacion": "j", "mecanismos": ["movilización lipídica"], "soluciones_actuales": "SOLUCIONES-P1"}],
        "archivo": "research/revisiones/M1a_mapa_problemas.md"}}]
    out = run_workflow("mision-1-auto", None, reglas)
    res = out["resultado"]
    assert res["fase"] == "mapa"
    assert out["fases"] == ["Mapa de problemas"]
    assert labels(out)[-1] == "verificacion:M1a"
    assert not any(l.startswith(("M1b", "registro", "triage", "puntuacion", "ranking", "plan")) for l in labels(out))
    verificacion = call(out, "verificacion:M1a")
    assert verificacion["agentType"] == "verificador-citas"
    assert "nutria --agente verificador-citas verificar research/revisiones/M1a_mapa_problemas.md" in verificacion["prompt"]
    consolidacion = call(out, "M1a:consolidacion")
    assert "soluciones_actuales" in consolidacion["schema"]["properties"]["prioritarios"]["items"]["required"]
    assert res["problemas"][0]["soluciones_actuales"] == "SOLUCIONES-P1"
    assert '"fase": "oportunidades"' in res["siguiente"]


@requires_node
@pytest.mark.parametrize("args,mensaje", [
    ({"fase": "oportunidades"}, "args.problemas"),
    ({"fase": "oportunidades", "problemas": []}, "args.problemas"),
    ({"fase": "planes"}, "args.seleccion"),
    ({"fase": "planes", "seleccion": ["O-1", "O-0002"]}, "O-1"),
    ({"fase": "todo"}, "Fase no válida"),
])
def test_mision1_phases_require_human_inputs(args, mensaje):  # D2-6
    out = run_workflow("mision-1-auto", args)
    assert out["llamadas"] == []
    assert mensaje in out["resultado"]["error"]


@requires_node
def test_mision1_opportunities_phase_runs_until_ranking():  # D2-6, D1-9, D5-10, D8-8, D2-1..D2-3, D1-5
    problemas = [{"id": "P1", "titulo": "Lipidosis hepática en el periodo de transición", "especie": "vacuno lechero",
                  "etapa": "transición", "mecanismos": ["movilización lipídica"], "soluciones_actuales": "SOLUCIONES-P1"}]
    novedad = {"veredicto": "nueva", "literatura": "l", "patentes": "p", "productos": "x"}
    reglas = [
        {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp("Oportunidad A"), _opp("Oportunidad B"), _opp("Oportunidad C")]}},
        {"label": "M1b:novedad:P1.3", "respuesta": {**novedad, "veredicto": "existente"}},
        {"labelPrefix": "M1b:novedad", "respuesta": novedad},
        {"label": "registro:1", "respuesta": {"id": "O-0001"}},
        {"label": "registro:2", "respuesta": {"id": "O-0002"}},
        {"label": "puntuacion:evaluador-3:O-0001", "respuesta": None},
        {"label": "ranking", "respuesta": {"top": [{"id": "O-0001", "titulo": "A", "motivo": "m"}],
                                           "archivo": "research/revisiones/M1_ranking.md", "resumen": "r"}},
    ]
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": problemas, "n_oportunidades": 2}, reglas)
    ls = labels(out)
    assert "SOLUCIONES-P1" in call(out, "M1b:gen:P1")["prompt"]
    assert not any(":r1" in l for l in ls), "no hacen falta rondas extra"
    assert not any(l.startswith(("M1a", "plan:", "critica-plan")) for l in ls)
    for opp_id in ("O-0001", "O-0002"):
        for agente, compuerta in COMPUERTA_DE.items():
            triage = call(out, f"triage:{compuerta}:{opp_id}")
            assert triage["agentType"] == agente
            assert f"puntuar compuerta {opp_id} {compuerta}" in triage["prompt"]
            assert "sin --firma-humana" in triage["prompt"]
        assert call(out, f"critica:{opp_id}")["agentType"] == "critico-red-team"
    econ = call(out, "triage:propiedad_intelectual:O-0001")["prompt"]
    assert "nutria econ ganadero" in econ and "research/datos/O-0001/" in econ
    puntuaciones = [c for c in out["llamadas"] if c["label"].startswith("puntuacion:")]
    assert {c["agentType"] for c in puntuaciones} == {"evaluador-independiente"}
    assert {c["label"]: c["model"] for c in puntuaciones if c["label"].endswith("O-0002")} == {
        "puntuacion:evaluador-1:O-0002": "opus", "puntuacion:evaluador-2:O-0002": "sonnet",
        "puntuacion:evaluador-3:O-0002": "fable"}
    assert call(out, "puntuacion:evaluador-3:O-0001:reintento")["model"] == "opus"
    for c in puntuaciones:
        assert "--sin-puntuaciones --json" in c["prompt"] and "No ejecutes nutria puntuar ranking" in c["prompt"]
    assert ls.index("ranking") < ls.index("verificacion:ranking")
    verificacion = call(out, "verificacion:ranking")
    assert verificacion["agentType"] == "verificador-citas"
    assert "verificar research/revisiones/M1_ranking.md" in verificacion["prompt"]
    res = out["resultado"]
    assert [o["id"] for o in res["oportunidades"]] == ["O-0001", "O-0002"]
    assert res["no_registradas"] == [{"titulo": "Oportunidad C", "motivo": "existente sin diferenciación"}]
    assert res["fallos"]["total"] == 1 and res["fallos"]["detalle"][0]["agente"] == "puntuacion:evaluador-3:O-0001"
    ev3 = next(e for e in res["oportunidades"][0]["evaluadores"] if e["id"] == "evaluador-3")
    assert ev3 == {"id": "evaluador-3", "modelo": "opus", "reintento": True, "registrado": True}
    assert '"fase": "planes"' in res["siguiente"]


@requires_node
def test_mision1_extra_rounds_respect_budget():  # D10-3
    reglas = [{"labelPrefix": "M1b:gen", "respuesta": {"oportunidades": []}}]
    args = {"fase": "oportunidades", "problemas": ["Problema sin oportunidades"]}
    sin_limite = run_workflow("mision-1-auto", args, reglas)
    assert [l for l in labels(sin_limite) if l.startswith("M1b:gen")] == ["M1b:gen:P1", "M1b:gen:P1:r1", "M1b:gen:P1:r2"]
    ajustado = run_workflow("mision-1-auto", args, reglas, presupuesto_total=150000, coste_por_agente=60000)
    assert [l for l in labels(ajustado) if l.startswith("M1b:gen")] == ["M1b:gen:P1"]
    assert any("Rondas extra omitidas" in m for m in ajustado["logs"])


@requires_node
def test_mision1_plans_phase_includes_validation_checklist_and_citation_check():  # D2-6, D1-12, D1-5
    reglas = []
    for opp_id in ("O-0003", "O-0007"):
        plan = {"archivo": f"research/planes/{opp_id}.md", "resumen": "r", "seleccion_firmada": True}
        reglas += [{"label": f"plan:{opp_id}", "respuesta": plan}, {"label": f"revision-plan:{opp_id}", "respuesta": plan}]
    reglas.append({"label": "critica-plan:O-0007", "respuesta": None})
    out = run_workflow("mision-1-auto", {"fase": "planes", "seleccion": ["O-0003", "O-0007", "O-0003"]}, reglas)
    ls = labels(out)
    assert not any(l.startswith(("M1a", "M1b", "registro", "triage", "puntuacion", "ranking")) for l in ls)
    assert [l for l in ls if l.startswith("plan:")] == ["plan:O-0003", "plan:O-0007"]
    prompt = _norm(call(out, "plan:O-0003")["prompt"])
    for fragment in ("donantes con procedimiento autorizado o de matadero", "Directiva 2010/63/UE", "Real Decreto 53/2013",
                     "la técnica in situ es in vivo", "EFSA J 2024;22(7):e8856", "BPL", "varianzas reales",
                     "registro previo del protocolo", "Reglamento (UE) 2019/1381, art. 32b", "--sin-puntuaciones"):
        assert fragment in prompt, fragment
    assert ls.index("revision-plan:O-0003") < ls.index("verificacion:plan:O-0003")
    assert "verificar research/planes/O-0003.md" in call(out, "verificacion:plan:O-0003")["prompt"]
    # sin crítica no hay revisión, pero el plan se verifica igualmente y el fallo se informa
    assert "revision-plan:O-0007" not in ls and "verificacion:plan:O-0007" in ls
    res = out["resultado"]
    assert [p["critica"] for p in res["planes"]] == ["sostenida", "sin crítica (fallo de agente)"]
    assert res["fallos"]["total"] == 1


@requires_node
@pytest.mark.parametrize("analisis,agente", [("destino_digestivo", "fisiologia-bioquimica"),
                                             ("modelo_ganadero", "economia-mercado-pi")])
def test_evaluar_stops_when_quick_discard_proposes_blocked(analisis, agente):  # D1-7
    reglas = [{"label": f"descarte:{analisis}", "respuesta": {"analisis": analisis, "estado": "bloqueada",
                                                             "motivo": "MOTIVO-BLOQUEO", "resumen": "r"}}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)
    assert {c["label"]: c["agentType"] for c in out["llamadas"]} == {
        "descarte:regulatoria": "regulacion-seguridad", "descarte:seguridad": "compuestos-toxicologia",
        "descarte:destino_digestivo": "fisiologia-bioquimica", "descarte:modelo_ganadero": "economia-mercado-pi"}
    assert out["fases"] == ["Descarte rápido"]
    res = out["resultado"]
    assert res["detenida_en"] == "Descarte rápido"
    assert res["bloqueos"] == [{"analisis": analisis, "agente": agente, "motivo": "MOTIVO-BLOQUEO", "archivo": None}]
    assert "nutria destino ruminal" in call(out, "descarte:destino_digestivo")["prompt"]
    assert "nutria econ ganadero research/datos/O-0003/econ_ganadero.toml" in call(out, "descarte:modelo_ganadero")["prompt"]
    assert "puntuar compuerta O-0003 regulatoria" in call(out, "descarte:regulatoria")["prompt"]


@requires_node
def test_evaluar_full_flow_double_extraction_meta_and_blind_scoring():  # D2-7, D2-8, D2-12, D9-11, D2-2, D2-3, D3-3
    estudios = [{"fuente": f"doi:10.1000/e{i}", "especie": "vacuno lechero", "tipo_estudio": "ensayo controlado",
                 "datos_cuantitativos": True, "comparable": True} for i in range(1, 5)]
    estudios.append({"fuente": "pmid:123", "especie": "rata", "tipo_estudio": "in vivo", "datos_cuantitativos": True,
                     "comparable": False})
    reglas = [
        {"label": "plan:O-0003", "respuesta": {"peco": "PECO", "variables": ["leche_kg_d"], "preguntas": [
            {"agente": "investigador-bibliografico", "pregunta": "novedad", "entregable": "e"},
            {"agente": "estadistico", "pregunta": "potencia", "entregable": "e"},
            {"agente": "nutricion-formulacion", "pregunta": "dieta basal", "entregable": "e"}]}},
        {"label": "bibliografia:O-0003", "respuesta": {"archivo": "research/revisiones/O-0003_busqueda.md", "estudios": estudios}},
        {"label": "extraccion:B:doi:10.1000/e4", "respuesta": None},
        {"labelPrefix": "conciliacion:", "respuesta": {"discrepancias": 0, "registrada": True,
                                                       "estado_verificacion": "doble_concordante"}},
        {"label": "puntuacion:evaluador-2", "lanzar": "error terminal simulado"},
        {"label": "informe:O-0003", "respuesta": {"archivo": "research/informes/O-0003.md", "decision_propuesta": "continuar"}},
    ]
    out = run_workflow("evaluar-oportunidad-auto", {"id": "O-0003"}, reglas)
    ls = labels(out)
    assert out["fases"] == ["Descarte rápido", "Plan", "Evidencia", "Especialistas", "Síntesis e informe", "Puntuación"]
    for i in range(1, 5):
        assert f"extraccion:A:doi:10.1000/e{i}" in ls and f"extraccion:B:doi:10.1000/e{i}" in ls
    assert not any("pmid:123" in l for l in ls)
    extraccion_a = call(out, "extraccion:A:doi:10.1000/e1")
    assert extraccion_a["agentType"] == "extractor-datos"
    assert "research/datos/O-0003/extraccion_doi_10.1000_e1_A.json" in extraccion_a["prompt"]
    assert "NO la registres" in extraccion_a["prompt"]
    assert ("extraccion comparar research/datos/O-0003/extraccion_doi_10.1000_e1_A.json "
            "research/datos/O-0003/extraccion_doi_10.1000_e1_B.json") in call(out, "conciliacion:doi:10.1000/e1")["prompt"]
    assert "conciliacion:doi:10.1000/e4" not in ls  # sin copia B no se concilia ni se registra
    assert call(out, "metaanalisis:O-0003")["agentType"] == "estadistico"  # 3 estudios comparables
    enum = call(out, "plan:O-0003")["schema"]["properties"]["preguntas"]["items"]["properties"]["agente"]["enum"]
    assert {"investigador-bibliografico", "estadistico"} <= set(enum)
    especialistas = [c for c in out["llamadas"] if c["phase"] == "Especialistas"]
    assert {c["agentType"] for c in especialistas} == {
        "investigador-bibliografico", "estadistico", "nutricion-formulacion", "desarrollo-industrial",
        "economia-mercado-pi", "sostenibilidad-bienestar"}
    industrial = next(c for c in especialistas if c["agentType"] == "desarrollo-industrial")
    assert "puntuar compuerta O-0003 analitica" in industrial["prompt"]
    verificacion = call(out, "verificacion-informe:O-0003")
    assert verificacion["agentType"] == "verificador-citas"
    assert "| Ref | Afirmación | Respaldo | Motivo |" in verificacion["prompt"]
    assert "informe comprobar research/informes/O-0003.md --online --sellar" in verificacion["prompt"]
    puntuaciones = [c for c in out["llamadas"] if c["phase"] == "Puntuación"]
    assert {c["agentType"] for c in puntuaciones} == {"evaluador-independiente"}
    assert {c["label"]: c["model"] for c in puntuaciones} == {
        "puntuacion:evaluador-1": "opus", "puntuacion:evaluador-2": "sonnet", "puntuacion:evaluador-3": "fable",
        "puntuacion:evaluador-2:reintento": "opus"}
    for c in puntuaciones:
        assert "--sin-puntuaciones --json" in c["prompt"] and "No ejecutes nutria puntuar ranking" in c["prompt"]
    res = out["resultado"]
    assert res["fallos"]["total"] == 2
    assert {f["agente"] for f in res["fallos"]["detalle"]} == {"extraccion:B:doi:10.1000/e4", "puntuacion:evaluador-2"}
    assert res["evidencia"]["doble_extraccion_registrada"] == 3
    assert [s["fuente"] for s in res["evidencia"]["sin_registrar"]] == ["doi:10.1000/e4"]
    assert next(e for e in res["evaluadores"] if e["id"] == "evaluador-2") == {
        "id": "evaluador-2", "modelo": "opus", "reintento": True, "registrado": True, "fallo": None}
    assert res["evaluadores_registrados"] == 3


@requires_node
def test_evaluar_skips_meta_analysis_and_logs_study_limit():
    estudios = [{"fuente": f"doi:10.1000/e{i}", "especie": "vacuno lechero", "tipo_estudio": "ensayo controlado",
                 "datos_cuantitativos": True, "comparable": True} for i in range(1, 5)]
    reglas = [{"label": "bibliografia:O-0003", "respuesta": {"archivo": "a.md", "estudios": estudios}}]
    out = run_workflow("evaluar-oportunidad-auto", {"id": "O-0003", "max_estudios": 2}, reglas)
    assert len([l for l in labels(out) if l.startswith("extraccion:A:")]) == 2
    assert "metaanalisis:O-0003" not in labels(out)
    assert any("Límite de 2 estudios" in m for m in out["logs"])
    assert any("Metaanálisis no lanzado" in m for m in out["logs"])
    assert out["resultado"]["evidencia"]["omitidos_por_limite"] == ["doi:10.1000/e3", "doi:10.1000/e4"]


@requires_node
def test_evaluar_retry_skipped_when_budget_is_low():  # D10-3
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", [{"label": "puntuacion:evaluador-3", "respuesta": None}],
                       presupuesto_total=1000, coste_por_agente=1)
    assert not any(l.endswith(":reintento") for l in labels(out))
    ev3 = next(e for e in out["resultado"]["evaluadores"] if e["id"] == "evaluador-3")
    assert ev3["registrado"] is False and "presupuesto" in ev3["fallo"]


@requires_node
@pytest.mark.parametrize("args", [None, "O-3", {"id": "X-0001"}])
def test_evaluar_requires_valid_id(args):
    out = run_workflow("evaluar-oportunidad-auto", args)
    assert out["llamadas"] == [] and "error" in out["resultado"]


CASOS_AGREGACION = {  # afirmación: ((fuentes, biologia, viabilidad), resultado esperado); None = agente sin resultado
    "A1": (("sostenida", "sostenida", "sostenida"), "sostenida"),
    "A2": (("refutada", "refutada", "sostenida"), "refutada"),
    "A3": (("no_verificable", "no_verificable", "sostenida"), "no_verificable"),
    "A4": (("refutada", "sostenida", "sostenida"), "debilitada"),
    "A5": (("debilitada", "sostenida", "sostenida"), "debilitada"),
    "A6": (("sostenida", "no_verificable", None), "no_verificable"),
    "A7": (("sostenida", None, None), "sin_quorum"),
    "A8": (("sostenida", "sostenida", None), "sostenida"),
    "A9": (("refutada", "no_verificable", "no_verificable"), "no_verificable"),
    "A10": (("refutada", "refutada", "no_verificable"), "refutada"),
    "A11": ((None, None, None), "sin_quorum"),
}


@requires_node
def test_red_team_aggregation_with_quorum():  # D2-9, D2-10, D9-11
    afirmaciones = [{"id": k, "texto": f"afirmación {k}"} for k in CASOS_AGREGACION]
    reglas = [{"label": "extraccion", "respuesta": {"afirmaciones": afirmaciones}}]
    for k, (votos, _) in CASOS_AGREGACION.items():
        for enfoque, voto in zip(("fuentes", "biologia", "viabilidad"), votos):
            reglas.append({"label": f"{k}:{enfoque}", "respuesta": None if voto is None else {"veredicto": voto, "motivo": "m"}})
    out = run_workflow("red-team-auto", "research/informes/O-0003.md", reglas)
    res = out["resultado"]
    assert {r["id"]: r["resultado"] for r in res["resultados"]} == {k: v for k, (_, v) in CASOS_AGREGACION.items()}
    assert res["sin_quorum"] == ["A7", "A11"]
    assert res["refutadas"] == 2
    assert res["fallos"]["total"] == sum(v is None for votos, _ in CASOS_AGREGACION.values() for v in votos)
    refutador = call(out, "A1:fuentes")
    assert refutador["schema"]["properties"]["veredicto"]["enum"] == ["sostenida", "debilitada", "refutada", "no_verificable"]
    assert 'nunca "sostenida"' in refutador["prompt"]
    assert "refutada=true" not in refutador["prompt"]


@requires_node
def test_red_team_cross_family_step_respects_sensitivity():  # D2-14
    out = run_workflow("red-team-auto", "research/informes/O-0003.md")
    segunda = call(out, "otra-familia")
    assert "llm modelos --json" in segunda["prompt"]
    assert "llm preguntar --rol critico --archivo research/informes/O-0003.md --sensibilidad confidencial" in segunda["prompt"]
    assert "Segunda opinión de otra familia" in call(out, "consolidacion")["prompt"]
    normal = run_workflow("red-team-auto", {"archivo": "research/planes/O-0003.md", "sensibilidad": "normal"})
    assert "--sensibilidad normal" in call(normal, "otra-familia")["prompt"]
    invalida = run_workflow("red-team-auto", {"archivo": "x.md", "sensibilidad": "publica"})
    assert invalida["llamadas"] == [] and "Sensibilidad no válida" in invalida["resultado"]["error"]


@requires_node
def test_red_team_skips_cross_family_step_when_budget_is_low():  # D10-3
    out = run_workflow("red-team-auto", "research/informes/O-0003.md", presupuesto_total=50000)
    assert "otra-familia" not in labels(out)
    assert out["resultado"]["otra_familia"]["ejecutado"] is False
    assert any("omitida por presupuesto" in m for m in out["logs"])


@requires_node
def test_comparar_sistemas_counts_and_reports_failed_agents():
    reglas = [
        {"label": "eval:D3", "respuesta": None},
        {"label": "eval:D4", "lanzar": "error terminal simulado"},
        {"labelPrefix": "verif:", "veces": 1, "respuesta": None},
    ]
    out = run_workflow("comparar-sistemas", {"sistemas": [{"nombre": "nutria", "ruta": "nutria"}]}, reglas)
    res = out["resultado"]
    assert res["fallos"]["total"] == 3
    assert {"eval:D3", "eval:D4"} <= {f["agente"] for f in res["fallos"]["detalle"]}
    assert [d.split(" ")[0] for d in res["dimensiones_sin_evaluar"]] == ["D3", "D4"]
    assert len(res["sin_verificar"]) == 1
    assert "AGENTES FALLIDOS" in call(out, "sintesis")["prompt"]
