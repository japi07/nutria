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


def _sin_agente(text: str) -> str:
    """Texto con las órdenes «nutria --agente <rol> ...» escritas sin la atribución, para buscar una orden concreta (en
    los subagentes el hook exige --agente en cada orden nutria: los agentes, las skills y los workflows la llevan)."""
    return re.sub(r"\bnutria --agente (?:<[^>]+>|\S+) ", "nutria ", text)


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
                     "promotores del crecimiento", "art. 11, apartado 2", "art. 13", "art. 32a", "art. 32b",
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
                     "antibióticos como promotores del crecimiento", "art. 11, apartado 2", "2019/1381", "art. 32a",
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
                     "meta validar-predictivo", "extraccion comparar <A> <B> --registrar", "extraccion exportar --variable"):
        assert fragment in text, fragment


def test_estado_skill_lists_unsigned_gates():
    text = _norm(_skill("estado"))
    assert "**compuertas sin firmar**" in text
    assert "`destino_digestivo`" in text
    assert "INSUFICIENTES" in text
    assert "verificación manual" in text


def test_oportunidad_skill_starts_with_quick_discard():
    text = _sin_agente(_norm(_skill("oportunidad")))
    assert text.index("**Descarte rápido") < text.index("**Evidencia (F4)**")
    for fragment in ("nutria destino ruminal", "nutria econ ganadero", "**detente**", "extraccion comparar",
                     "tres `evaluador-independiente`"):
        assert fragment in text, fragment


def test_mision1_skill_matches_workflow_phases():  # D2-6, D1-5, D1-9
    text = _sin_agente(_norm(_skill("mision-1")))
    for fragment in ('{"fase": "mapa"}', '"fase": "oportunidades", "problemas"', '"fase": "planes", "seleccion"',
                     "**conserva las soluciones actuales de cada problema**",
                     "nutria verificar research/revisiones/M1a_mapa_problemas_<fecha>.md",
                     "nutria verificar research/revisiones/M1_ranking_<fecha>.md", "nutria verificar research/planes/<ID>.md",
                     "**tres `evaluador-independiente`**", "9 criterios", "`destino_digestivo`"):
        assert fragment in text, fragment
    assert "8 criterios" not in text


def test_red_team_skill_quorum_and_cross_family():
    text = _sin_agente(_norm(_skill("red-team")))
    for fragment in ("`sin_quorum`", "`sostenida` solo con 2 o más `sostenida`, ninguna `refutada` y el enfoque de fuentes en `sostenida`",
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


def _opp(titulo, especie=None):
    """Oportunidad generada (M1b) construida a partir de los ejemplos de ejemplos/*.json."""
    base = _ejemplo("oportunidad_ejemplo")
    hip = _ejemplo("hipotesis_ejemplo")
    return {"titulo": titulo, "problema": "P1", "especie": especie or base["especie"], "etapa": base["etapa"],
            "mecanismo": base["mecanismo"], "candidatos": base["candidatos"], "tipo_producto": base["tipo_producto"],
            "evidencia_resumen": base["evidencia_resumen"], "diferenciacion": "d", "lagunas": base["lagunas"],
            "hipotesis": [{"enunciado": hip["titulo"], "efecto_minimo_relevante": hip["efecto_minimo_relevante"],
                           "criterio_falsacion": hip["criterio_falsacion"], "variables": hip["variables"]}]}


@requires_node
def test_mision1_default_phase_is_map_and_stops():  # D2-6, D1-5, D1-9, D2-15
    reglas = [{"label": "M1a:consolidacion", "respuesta": {
        "prioritarios": [{"id": "P1", "titulo": "Lipidosis hepática", "especie": "vacuno lechero", "etapa": "transición",
                          "justificacion": "j", "mecanismos": ["movilización lipídica"], "soluciones_actuales": "SOLUCIONES-P1"}],
        "archivo": "research/revisiones/M1a_mapa_problemas_2026-11-04.md"}}]
    out = run_workflow("mision-1-auto", {"fecha": "2026-11-04"}, reglas)
    res = out["resultado"]
    assert res["fase"] == "mapa" and res["fecha"] == "2026-11-04"
    assert out["fases"] == ["Preparación", "Mapa de problemas"]
    assert labels(out)[0] == "paso0" and labels(out)[-1] == "verificacion:M1a"
    assert not any(l.startswith(("M1b", "registro", "triage", "puntuacion", "ranking", "plan")) for l in labels(out))
    verificacion = call(out, "verificacion:M1a")
    assert verificacion["agentType"] == "verificador-citas"
    assert ("nutria --agente verificador-citas --json verificar research/revisiones/M1a_mapa_problemas_2026-11-04.md"
            in verificacion["prompt"])
    consolidacion = call(out, "M1a:consolidacion")
    assert "research/revisiones/M1a_mapa_problemas_2026-11-04.md" in consolidacion["prompt"]
    assert "soluciones_actuales" in consolidacion["schema"]["properties"]["prioritarios"]["items"]["required"]
    assert res["problemas"][0]["soluciones_actuales"] == "SOLUCIONES-P1"
    assert '"fase": "oportunidades"' in res["siguiente"] and "herramienta Workflow" in res["siguiente"]
    # la verificación la decide la salida literal de nutria: la respuesta mínima del arnés no es interpretable
    assert res["verificacion_citas"]["completa"] is False


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
def test_mision1_opportunities_phase_runs_until_ranking(project, capsys):  # D2-6, D1-9, D5-10, D8-8, D2-1..D2-3, D1-5, D2-18
    paso0 = _regla_paso0(project, capsys)  # el paso 0 ve el registro antes de las altas
    _registrar_titulos(project, capsys, "Oportunidad A", "Oportunidad B")  # lo que hacen los agentes de registro
    problemas = [{"id": "P1", "titulo": "Lipidosis hepática en el periodo de transición", "especie": "vacuno lechero",
                  "etapa": "transición", "mecanismos": ["movilización lipídica"], "soluciones_actuales": "SOLUCIONES-P1"}]
    novedad_lit = {"veredicto": "nueva", "literatura": "l"}
    novedad_mer = {"veredicto": "nueva", "productos": "x", "patentes": "p"}
    reglas = [
        paso0,
        {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp("Oportunidad A"), _opp("Oportunidad B"), _opp("Oportunidad C")]}},
        {"label": "M1b:novedad-mercado:P1.3", "respuesta": {**novedad_mer, "veredicto": "existente"}},
        {"labelPrefix": "M1b:novedad-literatura", "respuesta": novedad_lit},
        {"labelPrefix": "M1b:novedad-mercado", "respuesta": novedad_mer},
        {"label": "registro:1", "respuesta": {"id": "O-0001"}},
        {"label": "registro:2", "respuesta": {"id": "O-0002"}},
        {"label": "compuertas-registro:M1c:O-0001", "respuesta": _ver(capsys, "O-0001")},
        {"label": "compuertas-registro:M1c:O-0002", "respuesta": _ver(capsys, "O-0002")},
        {"label": "puntuacion:evaluador-3:O-0001", "respuesta": None},
        {"label": "ranking", "respuesta": {"top": [{"id": "O-0001", "titulo": "A", "motivo": "m"}],
                                           "archivo": "research/revisiones/M1_ranking_2026-11-04.md", "resumen": "r"}},
    ]
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": problemas, "n_oportunidades": 2,
                                         "fecha": "2026-11-04"}, reglas)
    ls = labels(out)
    assert ls[0] == "paso0"
    assert "SOLUCIONES-P1" in call(out, "M1b:gen:P1")["prompt"]
    assert not any(":r1" in l for l in ls), "no hacen falta rondas extra"
    assert not any(l.startswith(("M1a", "plan:", "critica-plan")) for l in ls)
    for j in (1, 2, 3):  # novedad: literatura (investigador-bibliografico) y productos y patentes (economia-mercado-pi)
        assert call(out, f"M1b:novedad-literatura:P1.{j}")["agentType"] == "investigador-bibliografico"
        assert call(out, f"M1b:novedad-mercado:P1.{j}")["agentType"] == "economia-mercado-pi"
    registro = call(out, "registro:1")
    assert registro["agentType"] == "fisiologia-bioquimica"  # lo registra quien lo generó, no el director
    assert "research/oportunidades/M1_2026-11-04_oportunidad-a.json" in registro["prompt"]
    assert "nutria --agente fisiologia-bioquimica oportunidad nueva" in registro["prompt"]
    for opp_id in ("O-0001", "O-0002"):
        for agente, compuerta in COMPUERTA_DE.items():
            triage = call(out, f"triage:{compuerta}:{opp_id}")
            assert triage["agentType"] == agente
            assert f"nutria --agente {agente} puntuar compuerta {opp_id} {compuerta}" in triage["prompt"]
            assert "sin --firma-humana" in triage["prompt"]
        assert call(out, f"critica:{opp_id}")["agentType"] == "critico-red-team"
        assert ls.index(f"compuertas-registro:M1c:{opp_id}") < ls.index(f"puntuacion:evaluador-1:{opp_id}")
    econ = call(out, "triage:propiedad_intelectual:O-0001")["prompt"]
    assert ("nutria --agente economia-mercado-pi econ ganadero research/datos/O-0001/econ_ganadero.toml --exigir-fuentes"
            in econ)
    puntuaciones = [c for c in out["llamadas"] if c["label"].startswith("puntuacion:")]
    assert {c["agentType"] for c in puntuaciones} == {"evaluador-independiente"}
    assert {c["label"]: c["model"] for c in puntuaciones if c["label"].endswith("O-0002")} == {
        "puntuacion:evaluador-1:O-0002": "opus", "puntuacion:evaluador-2:O-0002": "sonnet",
        "puntuacion:evaluador-3:O-0002": "fable"}
    assert call(out, "puntuacion:evaluador-3:O-0001:reintento")["model"] == "opus"
    for c in puntuaciones:
        assert "--sin-puntuaciones --json" in c["prompt"] and "No ejecutes nutria puntuar ranking" in c["prompt"]
    assert ls.index("ranking") < ls.index("verificacion:ranking")
    # fase de coordinación (peticiones de P6 y P7 a P8): el ranking respeta los subgrupos de CONDICIONAL y transcribe los
    # AVISOS; el triage regulatorio registra la clasificación y el workflow avisa si el registro sigue sin ruta
    ranking = call(out, "ranking")["prompt"]
    assert "solo pendientes de firma, sin resolver y CRÍTICAS sin resolver" in ranking and "bloque AVISOS" in ranking
    regulatoria = call(out, "triage:regulatoria:O-0001")["prompt"]
    assert "nutria --agente regulacion-seguridad oportunidad clasificar O-0001 --ruta <ruta>" in regulatoria
    assert "nutria --agente regulacion-seguridad normativa eurlex <CELEX>" in regulatoria
    for o in out["resultado"]["oportunidades"]:
        assert o["ruta_regulatoria"] == "indeterminada" and "no registró la clasificación" in o["aviso_clasificacion"]
    verificacion = call(out, "verificacion:ranking")
    assert verificacion["agentType"] == "verificador-citas"
    assert "verificar research/revisiones/M1_ranking_2026-11-04.md" in verificacion["prompt"]
    res = out["resultado"]
    assert [o["id"] for o in res["oportunidades"]] == ["O-0001", "O-0002"]
    assert res["no_registradas"] == [{"titulo": "Oportunidad C", "motivo": "existente sin diferenciación", "reformulacion": None}]
    assert res["fallos"]["total"] == 1 and res["fallos"]["detalle"][0]["agente"] == "puntuacion:evaluador-3:O-0001"
    ev3 = next(e for e in res["oportunidades"][0]["evaluadores"] if e["id"] == "evaluador-3")
    assert ev3 == {"id": "evaluador-3", "modelo": "opus", "reintento": True, "registrado": True}
    assert '"fase": "planes"' in res["siguiente"]


@requires_node
def test_mision1_extra_rounds_respect_budget(project, capsys):  # D10-3
    reglas = [_regla_paso0(project, capsys), {"labelPrefix": "M1b:gen", "respuesta": {"oportunidades": []}}]
    args = {"fase": "oportunidades", "problemas": ["Problema sin oportunidades"]}
    sin_limite = run_workflow("mision-1-auto", args, reglas)
    assert [l for l in labels(sin_limite) if l.startswith("M1b:gen")] == ["M1b:gen:P1", "M1b:gen:P1:r1", "M1b:gen:P1:r2"]
    ajustado = run_workflow("mision-1-auto", args, reglas, presupuesto_total=150000, coste_por_agente=60000)
    assert [l for l in labels(ajustado) if l.startswith("M1b:gen")] == ["M1b:gen:P1"]
    assert any("Rondas extra omitidas" in m for m in ajustado["logs"])


@requires_node
def test_mision1_plans_phase_includes_validation_checklist_and_citation_check(project, capsys):  # D2-6, D1-12, D1-5
    _registrar_oportunidades(project, capsys, 7)
    reglas = []
    for opp_id in ("O-0003", "O-0007"):
        plan = {"archivo": f"research/planes/{opp_id}.md", "resumen": "r", "seleccion_firmada": True}
        reglas += [{"label": f"plan:{opp_id}", "respuesta": plan}, {"label": f"revision-plan:{opp_id}", "respuesta": plan},
                   {"label": f"compuertas-registro:planes:{opp_id}", "respuesta": _ver(capsys, opp_id)}]
    reglas.append({"label": "critica-plan:O-0007", "respuesta": None})
    out = run_workflow("mision-1-auto", {"fase": "planes", "seleccion": ["O-0003", "O-0007", "O-0003"]}, reglas)
    ls = labels(out)
    assert not any(l.startswith(("M1a", "M1b", "registro", "triage", "puntuacion", "ranking")) for l in ls)
    assert [l for l in ls if l.startswith("plan:")] == ["plan:O-0003", "plan:O-0007"]
    for c in ("plan:O-0003", "critica-plan:O-0003"):  # el crítico recibe la misma lista de validación (D1-12)
        prompt = _norm(call(out, c)["prompt"])
        for fragment in FRAGMENTOS_VALIDACION:
            assert fragment in prompt, (c, fragment)
    assert "--sin-puntuaciones" in call(out, "plan:O-0003")["prompt"]
    assert ls.index("compuertas-registro:planes:O-0003") < ls.index("plan:O-0003")
    assert ls.index("revision-plan:O-0003") < ls.index("etica-plan:O-0003")
    assert ls.index("revision-plan:O-0003") < ls.index("verificacion:plan:O-0003")
    etica = call(out, "etica-plan:O-0003")
    assert etica["agentType"] == "sostenibilidad-bienestar"
    assert "nutria --agente sostenibilidad-bienestar --json puntuar compuerta O-0003 etica" in etica["prompt"]
    assert "sin --firma-humana" in etica["prompt"]
    assert "verificar research/planes/O-0003.md" in call(out, "verificacion:plan:O-0003")["prompt"]
    # sin crítica no hay revisión, pero el plan se verifica y su ética se repropone igualmente; el fallo se informa
    assert "revision-plan:O-0007" not in ls and "verificacion:plan:O-0007" in ls and "etica-plan:O-0007" in ls
    res = out["resultado"]
    assert [p["critica"] for p in res["planes"]] == ["sostenida", "sin crítica (fallo de agente)"]
    assert res["planes"][0]["etica"]["estado"] == "superada"
    assert res["fallos"]["total"] == 1


@requires_node
@pytest.mark.parametrize("analisis,agente", [("destino_digestivo", "fisiologia-bioquimica"),
                                             ("modelo_ganadero", "economia-mercado-pi")])
def test_evaluar_stops_when_quick_discard_proposes_blocked(analisis, agente, project, capsys):  # D1-7
    reglas = [_regla_registro(project, capsys, "O-0003"),
              {"label": f"descarte:{analisis}", "respuesta": {"analisis": analisis, "estado": "bloqueada",
                                                             "motivo": "MOTIVO-BLOQUEO", "resumen": "r"}}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)
    assert {c["label"]: c.get("agentType") for c in out["llamadas"]} == {
        "compuertas-registro:inicio": None,
        "descarte:regulatoria": "regulacion-seguridad", "descarte:seguridad": "compuestos-toxicologia",
        "descarte:destino_digestivo": "fisiologia-bioquimica", "descarte:modelo_ganadero": "economia-mercado-pi"}
    assert out["fases"] == ["Compuertas del registro", "Descarte rápido"]
    res = out["resultado"]
    assert res["detenida_en"] == "Descarte rápido"
    assert res["motivo"] == f'propuesta "bloqueada" en el descarte rápido: {analisis} ({agente})'
    assert res["bloqueos"] == [{"analisis": analisis, "agente": agente, "motivo": "MOTIVO-BLOQUEO",
                                "archivo": f"research/revisiones/O-0003_descarte_{analisis}.md"}]
    assert "nutria --agente fisiologia-bioquimica destino ruminal" in call(out, "descarte:destino_digestivo")["prompt"]
    assert ("nutria --agente economia-mercado-pi econ ganadero research/datos/O-0003/econ_ganadero.toml --exigir-fuentes"
            in call(out, "descarte:modelo_ganadero")["prompt"])
    regulatoria = call(out, "descarte:regulatoria")["prompt"]
    assert "nutria --agente regulacion-seguridad oportunidad clasificar O-0003 --ruta <ruta>" in regulatoria
    assert "nutria --agente regulacion-seguridad normativa eurlex <CELEX>" in regulatoria
    assert "nutria --agente regulacion-seguridad puntuar compuerta O-0003 regulatoria" in call(out, "descarte:regulatoria")["prompt"]


@requires_node
def test_evaluar_full_flow_double_extraction_meta_and_blind_scoring(project, capsys):  # D2-7, D2-8, D2-12, D9-11, D2-2, D2-3, D3-3
    estudios = [{"fuente": f"doi:10.1000/e{i}", "especie": "vacuno lechero", "tipo_estudio": "ensayo controlado",
                 "datos_cuantitativos": True, "comparable": True} for i in range(1, 5)]
    estudios.append({"fuente": "pmid:123", "especie": "rata", "tipo_estudio": "in vivo", "datos_cuantitativos": True,
                     "comparable": False})
    reglas = [
        _regla_registro(project, capsys, "O-0003"),
        {"label": "plan:O-0003", "respuesta": {"peco": "PECO", "variables": ["leche_kg_d"], "preguntas": [
            {"agente": "investigador-bibliografico", "pregunta": "novedad", "entregable": "e"},
            {"agente": "estadistico", "pregunta": "potencia", "entregable": "e"},
            {"agente": "nutricion-formulacion", "pregunta": "dieta basal", "entregable": "e"}]}},
        {"label": "bibliografia:O-0003", "respuesta": {"archivo": "research/revisiones/O-0003_busqueda.md", "estudios": estudios}},
        {"label": "extraccion:B:doi:10.1000/e4", "respuesta": None},
        # doble extracción con el registro: salidas literales de depositar, comparar --registrar y conciliar
        *_reglas_doble_extraccion(project, capsys, "O-0003", [f"doi:10.1000/e{i}" for i in range(1, 5)],
                                  con_discrepancias={"doi:10.1000/e2"}, sin_copia_b={"doi:10.1000/e4"}),
        {"label": "puntuacion:evaluador-2", "lanzar": "error terminal simulado"},
        *_reglas_puntuacion_emitida(project, capsys, "O-0003"),
        {"label": "informe:O-0003", "respuesta": {"archivo": "research/informes/O-0003.md", "decision_propuesta": "continuar"}},
        {"label": "sello:O-0003", "respuesta": SELLO_VALIDO},
    ]
    out = run_workflow("evaluar-oportunidad-auto", {"id": "O-0003"}, reglas)
    ls = labels(out)
    assert out["fases"] == ["Compuertas del registro", "Descarte rápido", "Plan", "Evidencia", "Especialistas", "Puntuación",
                            "Síntesis e informe", "Sello y registro"]
    for i in range(1, 5):
        assert f"extraccion:A:doi:10.1000/e{i}" in ls and f"extraccion:B:doi:10.1000/e{i}" in ls
    assert not any("pmid:123" in l for l in ls)
    extraccion_a = call(out, "extraccion:A:doi:10.1000/e1")
    assert extraccion_a["agentType"] == "extractor-datos"
    assert "research/datos/O-0003/extraccion_doi_10.1000_e1_A.json" in extraccion_a["prompt"]
    assert "NO la registres" in extraccion_a["prompt"]
    assert ("nutria --agente extractor-datos-A-O-0003-r1 --json extraccion depositar --datos "
            "research/datos/O-0003/extraccion_doi_10.1000_e1_A.json") in extraccion_a["prompt"]
    concilia = call(out, "conciliacion:doi:10.1000/e1")["prompt"]
    assert ("nutria --agente extractor-datos-C-O-0003-r1 --json extraccion comparar research/datos/O-0003/extraccion_doi_10.1000_e1_A.json "
            "research/datos/O-0003/extraccion_doi_10.1000_e1_B.json --registrar") in concilia
    assert "extraccion conciliar <CE-xxxx>" in concilia and "No uses nutria extraccion agregar" in concilia
    assert "conciliacion:doi:10.1000/e4" not in ls  # sin copia B no se concilia ni se registra
    meta = call(out, "metaanalisis:O-0003")
    assert meta["agentType"] == "estadistico"  # 3 estudios comparables
    assert 'nutria --agente estadistico extraccion exportar --variable "leche_kg_d" --especie "vacuno lechero"' in meta["prompt"]
    enum = call(out, "plan:O-0003")["schema"]["properties"]["preguntas"]["items"]["properties"]["agente"]["enum"]
    assert {"investigador-bibliografico", "estadistico"} <= set(enum)
    especialistas = [c for c in out["llamadas"] if c["phase"] == "Especialistas"]
    assert {c["agentType"] for c in especialistas} == {
        "investigador-bibliografico", "estadistico", "nutricion-formulacion", "desarrollo-industrial",
        "economia-mercado-pi", "sostenibilidad-bienestar"}
    industrial = next(c for c in especialistas if c["agentType"] == "desarrollo-industrial")
    assert "nutria --agente desarrollo-industrial puntuar compuerta O-0003 analitica" in industrial["prompt"]
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
    # quien genera no evalúa: los evaluadores puntúan antes de la síntesis y del informe
    assert max(ls.index(c["label"]) for c in puntuaciones) < ls.index("informe:O-0003")
    res = out["resultado"]
    assert res["fallos"]["total"] == 2
    assert {f["agente"] for f in res["fallos"]["detalle"]} == {"extraccion:B:doi:10.1000/e4", "puntuacion:evaluador-2"}
    assert res["evidencia"]["doble_extraccion_registrada"] == 3
    assert [s["fuente"] for s in res["evidencia"]["sin_registrar"]] == ["doi:10.1000/e4"]
    estados = {c["fuente"]: c["estado_verificacion"] for c in res["evidencia"]["comparaciones"]}
    assert estados == {"doi:10.1000/e1": "doble_concordante", "doi:10.1000/e2": "resuelta", "doi:10.1000/e3": "doble_concordante"}
    assert next(e for e in res["evaluadores"] if e["id"] == "evaluador-2") == {
        "id": "evaluador-2", "modelo": "opus", "reintento": True, "emitida": True, "registrado": True, "fallo": None,
        "alerta": None}
    assert res["evaluadores_registrados"] == 3 and res["puntuaciones_pendientes"] == []
    assert res["informe"]["sello_valido"] is True


@requires_node
def test_evaluar_skips_meta_analysis_and_logs_study_limit(project, capsys):
    estudios = [{"fuente": f"doi:10.1000/e{i}", "especie": "vacuno lechero", "tipo_estudio": "ensayo controlado",
                 "datos_cuantitativos": True, "comparable": True} for i in range(1, 5)]
    reglas = [_regla_registro(project, capsys, "O-0003"),
              {"label": "bibliografia:O-0003", "respuesta": {"archivo": "a.md", "estudios": estudios}}]
    out = run_workflow("evaluar-oportunidad-auto", {"id": "O-0003", "max_estudios": 2}, reglas)
    assert len([l for l in labels(out) if l.startswith("extraccion:A:")]) == 2
    assert "metaanalisis:O-0003" not in labels(out)
    assert any("Límite de 2 estudios" in m for m in out["logs"])
    assert any("Metaanálisis no lanzado" in m for m in out["logs"])
    assert out["resultado"]["evidencia"]["omitidos_por_limite"] == ["doi:10.1000/e3", "doi:10.1000/e4"]


@requires_node
def test_evaluar_retry_skipped_when_budget_is_low(project, capsys):  # D10-3
    reglas = [_regla_registro(project, capsys, "O-0003"), {"label": "puntuacion:evaluador-3", "respuesta": None}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas, presupuesto_total=1000, coste_por_agente=1)
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
    "A4": (("refutada", "sostenida", "sostenida"), "refutada"),        # D2-10: "fuentes" refutada decide
    "A5": (("debilitada", "sostenida", "sostenida"), "debilitada"),
    "A6": (("sostenida", "no_verificable", None), "no_verificable"),
    "A7": (("sostenida", None, None), "sin_quorum"),
    "A8": (("sostenida", "sostenida", None), "sostenida"),
    "A9": (("refutada", "no_verificable", "no_verificable"), "refutada"),  # D2-10: la fuente no existe o no dice eso
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
    assert res["refutadas"] == 4
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


# ====================================================================================================================
# P8: workflows, skills y agentes (independencia de los evaluadores, descarte temprano, idempotencia, topes y
# contenido regulatorio y fisiológico). Utilidades y pruebas de este bloque.
#
# Índice (defecto → pruebas test_p8_*):
#   D1-3, D2-2  evaluar_evaluadores_ciegos_antes_del_informe, evaluador_no_ve_las_decisiones_propuestas,
#               evaluar|mision1_la_evidencia_de_los_evaluadores_no_admite_rutas_ajenas,
#               evaluar_critica_y_verificacion_eligen_las_afirmaciones, red_team_ningun_paso_lo_hace_el_autor,
#               red_team_sin_autor_deducible_no_empieza, evaluar_detecta_registros_prematuros (ronda 2: decision
#               listar rechazada a evaluadores en evaluador_no_ve_las_decisiones_propuestas)
#   D1-7, D2-8  evaluar_se_detiene_si_el_registro_ya_tiene_una_compuerta_bloqueada, evaluar_ruta_fuera_de_alcance_*,
#               evaluar_falla_en_cerrado_*, evaluar_el_registro_manda_tras_el_descarte_rapido,
#               evaluar_se_detiene_antes_de_puntuar_*, evaluar_relee_el_registro_antes_de_puntuar,
#               estado_detenido_sin_compuertas_bloqueadas
#   D4-3        mision1_no_puntua_las_oportunidades_bloqueadas, mision1_sin_compuertas_legibles_no_puntua
#   D3-3        evaluar_sin_sello_valido_no_registra_puntuaciones, evaluar_interpreta_la_salida_real_de_informe_sello,
#               evaluar_no_registra_un_fichero_de_puntuacion_modificado, evaluar_modo_registrar_*,
#               evaluar_registro_sin_comprobacion_literal_del_sha256, evaluar_detecta_registros_prematuros,
#               mision1_firma_de_la_seleccion_segun_el_registro, mision1_firma_de_la_seleccion_orden_y_negacion
#   D1-12       mision1_planes_critica_con_la_lista_y_etica_repropuesta, mision1_etica_repropuesta_rechazada_por_la_cli,
#               lista_de_validacion_igual_en_director_critico_y_workflow
#   D2-10       red_team_regla_de_fuentes_en_todas_las_combinaciones, red_team_regla_de_fuentes_documentada_*
#   D2-15       mision1_paso0_en_cada_fase, mision1_deduplica_contra_el_registro, mision1_nombres_de_fichero_*,
#               mision1_repetir_la_fase_no_duplica_ni_sobrescribe, mision1_planes_conserva_la_version_anterior,
#               mision1_minimo_de_rumiantes, mision1_id_de_otra_oportunidad_no_se_puntua,
#               mision1_paso0_acepta_los_dos_formatos_de_doctor, mision1_paso0_estado_compacto_y_comprobado,
#               mision1_ls_falla_en_cerrado, mision1_ls_reconoce_cabeceras_absolutas_por_directorio
#   D2-16       red_team_salida_incluye_el_directorio_de_origen, red_team_revisar_plan_e_informe_no_se_pisan,
#               red_team_no_sobrescribe_una_revision_anterior
#   D2-17       version_de_claude_code_documentada
#   D2-18       mision1_novedad_por_el_rol_que_fija_la_skill, evaluar_pregunta_obligatoria_aunque_el_plan_traiga_la_suya
#   D10-3       evaluar_tope_de_preguntas_*, red_team_tope_*, mision1_n_oportunidades_es_un_tope, *_topes_no_validos_*,
#               mision1_estimacion_de_las_fases_mapa_y_planes, mision1_entradas_no_validas_no_se_descartan_en_silencio,
#               readme_cifras_de_agentes_coinciden_con_los_workflows
#   D10-9       ningun_mensaje_presenta_un_workflow_como_orden_con_barra, siguiente_de_evaluar_lanza_red_team_*
#   D4-4        adaptacion_de_la_microbiota_ruminal
#   D5-4        antibioticos_y_moduladores_ruminales_antimicrobianos
#   D5-13       omg_microorganismos_y_qps
#   compuertas  compuertas_propuestas_por_su_rol_propietario
# ====================================================================================================================
import hashlib  # noqa: E402

EJEMPLOS = ROOT / "ejemplos"
WORKFLOWS_INVESTIGACION = ("mision-1-auto", "evaluar-oportunidad-auto", "red-team-auto")
FRAGMENTOS_VALIDACION = ("donantes con procedimiento autorizado o de matadero", "Directiva 2010/63/UE", "Real Decreto 53/2013",
                         "la técnica in situ es in vivo", "EFSA J 2024;22(7):e8856", "BPL", "varianzas reales",
                         "registro previo del protocolo", "Reglamento (UE) 2019/1381, art. 32b", "incubaciones cortas en lote")
PROPIETARIO = {compuerta: agente for agente, compuerta in COMPUERTA_DE.items()}


def _ejemplo(nombre: str) -> dict:
    return json.loads((EJEMPLOS / f"{nombre}.json").read_text("utf-8"))


def _cli(capsys, *argv) -> tuple[int, str]:
    """Ejecuta la CLI real en el proyecto de prueba y devuelve (código de salida, salida estándar)."""
    from nutria.cli import main
    capsys.readouterr()
    try:
        code = main(list(argv))
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    return code, capsys.readouterr().out


def _salida(capsys, *argv) -> dict:
    """Respuesta de un agente que transcribe literalmente la salida de una orden nutria."""
    code, out = _cli(capsys, *argv)
    return {"orden": "nutria " + " ".join(argv), "codigo_salida": code, "salida": out}


def _registrar_oportunidades(project, capsys, n: int, especie: str | None = None) -> None:
    """Deja n oportunidades en el registro (a partir del ejemplo de ejemplos/), con títulos distintos."""
    code, out = _cli(capsys, "--json", "oportunidad", "listar")
    ya = len(json.loads(out)) if code == 0 else 0
    base = _ejemplo("oportunidad_ejemplo")
    nuevas = [{**base, "titulo": f"{base['titulo']} ({i})", **({"especie": especie} if especie else {})}
              for i in range(ya + 1, n + 1)]
    if not nuevas:
        return
    ruta = project / f"oportunidades_{ya + 1}_{n}.json"
    ruta.write_text(json.dumps(nuevas, ensure_ascii=False), "utf-8")
    code, out = _cli(capsys, "--agente", "fisiologia-bioquimica", "oportunidad", "nueva", "--datos", str(ruta))
    assert code == 0, out


def _registrar_titulos(project, capsys, *titulos: str) -> None:
    """Alta en el registro de oportunidades con estos títulos (lo que hace el agente de registro de la misión 1)."""
    for titulo in titulos:
        _registrar_oportunidad(project, capsys, titulo)


def _registrar_oportunidad(project, capsys, titulo: str, especie: str | None = None) -> str:
    datos = {**_ejemplo("oportunidad_ejemplo"), "titulo": titulo, **({"especie": especie} if especie else {})}
    ruta = project / f"oportunidad_{hashlib.sha256(titulo.encode()).hexdigest()[:8]}.json"
    ruta.write_text(json.dumps(datos, ensure_ascii=False), "utf-8")
    code, out = _cli(capsys, "--agente", "fisiologia-bioquimica", "--json", "oportunidad", "nueva", "--datos", str(ruta))
    assert code == 0, out
    return json.loads(out)["ids"][0]


def _ver(capsys, opp_id: str) -> dict:
    return _salida(capsys, "--agente", "orquestador", "--json", "oportunidad", "ver", opp_id, "--sin-puntuaciones")


def _bloquear(capsys, opp_id: str, compuerta: str, motivo: str = "MOTIVO-REGISTRO") -> None:
    code, out = _cli(capsys, "--agente", PROPIETARIO[compuerta], "puntuar", "compuerta", opp_id, compuerta, "bloqueada",
                     "--motivo", motivo)
    assert code == 0, out


def _regla_registro(project, capsys, opp_id: str, etiqueta: str = "compuertas-registro:") -> dict:
    """Regla del arnés: toda lectura de compuertas del registro devuelve la salida real de `oportunidad ver`."""
    _registrar_oportunidades(project, capsys, int(opp_id[2:]))
    return {"labelPrefix": etiqueta, "respuesta": _ver(capsys, opp_id)}


def _ls(project) -> str:
    """Salida de `ls -1` de los directorios que lee el paso 0 de la misión 1."""
    bloques = []
    for d in ("research/planes", "research/revisiones", "research/oportunidades", "research/hipotesis"):
        nombres = sorted(p.name for p in (project / d).iterdir()) if (project / d).is_dir() else []
        bloques.append(f"{d}:\n" + "".join(f"{n}\n" for n in nombres))
    return "\n".join(bloques)


DOCTOR_SIN_RED = {"orden": "nutria --agente orquestador --json doctor", "codigo_salida": 0, "salida": json.dumps({
    "version": "x", "red": {"PubMed (NCBI E-utilities)": "BLOQUEADO por proxy/política de red", "Crossref": "OK (200)"},
    "claves": {"NCBI_API_KEY": False, "OPENALEX_API_KEY": False, "LENS_API_TOKEN": False}})}


def _doctor_objetos(estados: dict[str, str], **extra) -> dict:
    """Salida de `nutria --json doctor` con el formato de objetos de la rama de clientes y red (P4): cada fuente es
    {"estado", "url", "esencial", "detalle"?}, más "offline", "fallos_esenciales" y "resultado"."""
    red = {}
    for nombre, estado in estados.items():
        red[nombre] = {"estado": estado, "url": f"https://{nombre.split()[0].lower()}.example/x", "esencial": nombre != "OpenAlex"}
        if estado == "bloqueado":
            red[nombre]["detalle"] = "ProxyError: Tunnel connection failed: 403 Forbidden"
    fallos = [n for n, e in estados.items() if red[n]["esencial"] and e not in ("ok", "no_comprobado")]
    datos = {"version": "0.1.0", "config": True, "base_datos": True, "offline": False,
             "claves": {"NCBI_API_KEY": False, "OPENALEX_API_KEY": False, "LENS_API_TOKEN": False}, "red": red,
             "webfetch": {"eur-lex.europa.eu": {"estado": "bloqueado", "permitido_en_settings": None}},
             "hook": {"estado": "ok", "esencial": True}, "r": {"estado": "no_instalado"},
             "fallos_esenciales": fallos, "resultado": "fallos_esenciales" if fallos else "ok", **extra}
    return {"orden": "nutria --agente orquestador --json doctor", "codigo_salida": 3 if fallos else 0, "salida": json.dumps(datos)}


def _regla_paso0(project, capsys, fecha: str = "2026-11-04", ficheros: str | None = None, doctor: dict | None = None) -> dict:
    return {"label": "paso0", "respuesta": {
        "doctor": doctor or DOCTOR_SIN_RED,
        "estado": _salida(capsys, "--agente", "orquestador", "estado"),
        "fecha": {"orden": "date -u +%F", "codigo_salida": 0, "salida": f"{fecha}\n"},
        "ficheros": {"orden": "ls -1 research/revisiones research/oportunidades research/hipotesis research/planes", "codigo_salida": 0,
                     "salida": _ls(project) if ficheros is None else ficheros}}}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256sum_c(project, sha: str, rel: str) -> tuple[int, str]:
    """`echo '<sha>  <fichero>' | sha256sum -c -` en el proyecto de prueba (como lo ejecuta el agente de registro)."""
    if shutil.which("sha256sum"):
        proc = subprocess.run(["sha256sum", "-c", "-"], input=f"{sha}  {rel}\n", cwd=project, capture_output=True, text=True,
                              check=False)
        return proc.returncode, proc.stdout
    ok = _sha256(project / rel) == sha
    return (0, f"{rel}: OK\n") if ok else (1, f"{rel}: FAILED\n")


def _registro_literal(project, capsys, ev: str, rel: str, sha: str) -> dict:
    """Salida literal de la orden de registro diferido: sha256sum -c y, solo si da OK, `nutria puntuar registrar`."""
    code, out = _sha256sum_c(project, sha, rel)
    orden = f"echo '{sha}  {rel}' | sha256sum -c - && nutria --agente {ev} --json puntuar registrar --datos {rel}"
    if code != 0:
        return {"orden": orden, "codigo_salida": code, "salida": out}
    registro = _salida(capsys, "--agente", ev, "--json", "puntuar", "registrar", "--datos", str(project / rel))
    return {"orden": orden, "codigo_salida": registro["codigo_salida"], "salida": out + registro["salida"]}


def _reglas_puntuacion_emitida(project, capsys, opp_id: str, evaluadores=(1, 2, 3)) -> list[dict]:
    """Reglas del arnés para la puntuación diferida: cada evaluador deja su JSON (del ejemplo) y devuelve su sha256;
    el registro posterior devuelve la salida literal real de sha256sum -c y de `puntuar registrar`."""
    reglas = []
    for n in evaluadores:
        ev = f"evaluador-{n}"
        datos = {**_ejemplo("puntuacion_ejemplo"), "oportunidad": opp_id, "evaluador": ev}
        ruta = project / "research" / "oportunidades" / f"{opp_id}_puntuacion_{ev}.json"
        ruta.write_text(json.dumps(datos, ensure_ascii=False), "utf-8")
        sha = _sha256(ruta)
        rel = f"research/oportunidades/{ruta.name}"
        registro = _registro_literal(project, capsys, ev, rel, sha)
        assert registro["codigo_salida"] == 0, registro
        reglas += [{"labelRegex": f"^puntuacion:{ev}(:reintento)?$", "respuesta": {"archivo": rel, "sha256": sha, "resumen": "r"}},
                   {"label": f"registro-puntuacion:{ev}", "respuesta": registro}]
    return reglas


def _comprobar(completa=True, errores=(), codigo=0, pendientes=0) -> dict:
    resumen = {"total": 2, "verificadas": 2 - pendientes, "pendientes": pendientes, "con_error": 0, "completa": completa}
    return {"orden": "nutria --agente verificador-citas --json informe comprobar research/informes/O-0003.md --online --sellar",
            "codigo_salida": codigo, "salida": json.dumps({
                "incidencias": [{"severity": "error", "message": m, "line": 1} for m in errores], "referencias": [],
                "resumen": resumen})}


def _sello(valido=True, codigo=0, mensaje="Sello válido") -> dict:
    return {"orden": "nutria --agente verificador-citas --json informe sello research/informes/O-0003.md",
            "codigo_salida": codigo, "salida": json.dumps({"valido": valido, "mensaje": mensaje})}


SELLO_VALIDO = {"comprobar": _comprobar(), "sello": _sello()}


def _maximo_logueado(out, patron=r"como máximo (\d+) agentes") -> int:
    valores = [int(m.group(1)) for msg in out["logs"] for m in [re.search(patron, msg)] if m]
    assert valores, out["logs"]
    return valores[0]


def _plan_evaluar(n_preguntas=3) -> dict:
    agentes = ["investigador-bibliografico", "estadistico", "nutricion-formulacion", "fisiologia-bioquimica",
               "compuestos-toxicologia", "bioinformatico", "regulacion-seguridad"]
    return {"peco": "PECO", "variables": ["leche_kg_d"], "preguntas": [
        {"agente": agentes[i % len(agentes)], "pregunta": f"pregunta {i + 1}", "entregable": "e"} for i in range(n_preguntas)]}


# ------------------------------------------------------------------ evaluar-oportunidad-auto: compuertas del registro
@requires_node
@pytest.mark.parametrize("compuerta", sorted(PROPIETARIO))
def test_p8_evaluar_se_detiene_si_el_registro_ya_tiene_una_compuerta_bloqueada(compuerta, project, capsys):  # D1-7, D2-8
    _registrar_oportunidades(project, capsys, 3)
    _bloquear(capsys, "O-0003", compuerta)
    reglas = [{"labelPrefix": "compuertas-registro:", "respuesta": _ver(capsys, "O-0003")},
              {"labelPrefix": "descarte:", "respuesta": {"analisis": "x", "estado": "no_resuelta", "motivo": "m", "resumen": "r"}}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)
    assert labels(out) == ["compuertas-registro:inicio"]  # nada más se gasta
    lectura = call(out, "compuertas-registro:inicio")
    assert "oportunidad ver O-0003 --sin-puntuaciones" in lectura["prompt"] and "--json" in lectura["prompt"]
    res = out["resultado"]
    assert res["detenida_en"] == "Compuertas del registro"
    assert [b["compuerta"] for b in res["bloqueos"]] == [compuerta]
    assert res["bloqueos"][0]["motivo"] == "MOTIVO-REGISTRO"
    assert "herramienta Workflow" in res["siguiente"]


@requires_node
def test_p8_evaluar_ruta_fuera_de_alcance_bloquea_aunque_no_haya_fila_de_compuerta(project, capsys):  # D1-7 (escéptico)
    _registrar_oportunidades(project, capsys, 3)
    salida = _ver(capsys, "O-0003")
    ver = json.loads(salida["salida"])
    ver["regulatory_route"] = "fuera_de_alcance_medicamento"
    ver["compuertas_efectivas"]["regulatoria"] = "bloqueada"
    ver["compuertas"] = []
    reglas = [{"labelPrefix": "compuertas-registro:", "respuesta": {**salida, "salida": json.dumps(ver)}},
              {"labelPrefix": "descarte:", "respuesta": {"analisis": "x", "estado": "no_resuelta", "motivo": "m", "resumen": "r"}}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)
    assert labels(out) == ["compuertas-registro:inicio"]
    assert out["resultado"]["bloqueos"][0]["motivo"] == "ruta regulatoria fuera_de_alcance_medicamento"


@requires_node
@pytest.mark.parametrize("respuesta", [
    None,  # el agente no devuelve nada
    {"orden": "nutria ...", "codigo_salida": 1, "salida": ""},
    {"orden": "nutria ...", "codigo_salida": 0, "salida": "compuertas: todas superadas"},  # resumen en vez de la salida literal
    {"orden": "nutria ...", "codigo_salida": 0, "salida": json.dumps({"id": "O-0003", "compuertas_efectivas": {"regulatoria": "superada"}})},
], ids=["sin-resultado", "codigo-1", "no-json", "incompleta"])
def test_p8_evaluar_falla_en_cerrado_si_no_puede_leer_el_registro(respuesta):  # D1-7
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", [{"labelPrefix": "compuertas-registro:", "respuesta": respuesta}])
    assert labels(out) == ["compuertas-registro:inicio"]
    assert out["resultado"]["detenida_en"] == "Compuertas del registro"
    assert "no se pudieron leer las compuertas" in out["resultado"]["motivo"]


@requires_node
@pytest.mark.parametrize("estado", ["bloqueada", "descartada"])
def test_p8_estado_detenido_sin_compuertas_bloqueadas(estado, project, capsys):  # D1-7, D4-3: el estado también detiene
    paso0_vacio = _regla_paso0(project, capsys)  # el paso 0 de la misión ve el registro antes del alta
    _registrar_oportunidades(project, capsys, 3)
    salida = _ver(capsys, "O-0003")
    vista = json.loads(salida["salida"])
    assert "bloqueada" not in vista["compuertas_efectivas"].values()
    vista["status"] = estado  # salir de ese estado exige firma humana
    detenida = {**salida, "salida": json.dumps(vista)}
    ev = run_workflow("evaluar-oportunidad-auto", "O-0003", [{"labelPrefix": "compuertas-registro:", "respuesta": detenida}])
    assert labels(ev) == ["compuertas-registro:inicio"]
    assert ev["resultado"]["detenida_en"] == "Compuertas del registro" and f'estado "{estado}"' in ev["resultado"]["motivo"]
    planes = run_workflow("mision-1-auto", {"fase": "planes", "seleccion": ["O-0003"]},
                          [_regla_paso0(project, capsys), {"label": "compuertas-registro:planes:O-0003", "respuesta": detenida}])
    assert "plan:O-0003" not in labels(planes)
    assert f'estado "{estado}": no se redacta el plan' in planes["resultado"]["planes"][0]["motivo"]
    titulo = vista["title"]
    ops = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": [_problema(1)], "n_oportunidades": 1},
                       [paso0_vacio, {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp(titulo)]}}, *_reglas_novedad(),
                        {"label": "registro:1", "respuesta": {"id": "O-0003"}}, {"labelPrefix": "compuertas-registro:", "respuesta": detenida},
                        {"labelPrefix": "triage:", "respuesta": {"compuerta": "x", "estado": "no_resuelta", "motivo": "m", "resumen": "r"}}])
    assert not any(l.startswith("puntuacion:") for l in labels(ops))
    assert f'estado "{estado}"' in ops["resultado"]["oportunidades"][0]["sin_puntuar"]


@requires_node
def test_p8_evaluar_falla_en_cerrado_si_la_salida_es_de_otra_oportunidad(project, capsys):  # D1-7
    _registrar_oportunidades(project, capsys, 3)
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", [{"labelPrefix": "compuertas-registro:", "respuesta": _ver(capsys, "O-0002")}])
    assert labels(out) == ["compuertas-registro:inicio"]
    assert "O-0002" in out["resultado"]["motivo"]


@requires_node
def test_p8_evaluar_el_registro_manda_tras_el_descarte_rapido(project, capsys):  # D1-7 (escéptico), D2-8
    _registrar_oportunidades(project, capsys, 3)
    limpio = _ver(capsys, "O-0003")
    _bloquear(capsys, "O-0003", "seguridad", "REGISTRADA-SIN-DECIRLO")
    reglas = [{"label": "compuertas-registro:inicio", "respuesta": limpio},
              {"label": "compuertas-registro:descarte", "respuesta": _ver(capsys, "O-0003")},
              {"labelPrefix": "descarte:", "respuesta": {"analisis": "x", "estado": "no_resuelta", "motivo": "m", "resumen": "r"}}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)
    ls = labels(out)
    assert ls[-1] == "compuertas-registro:descarte" and len(ls) == 6
    assert not any(l.startswith(("plan:", "bibliografia:", "puntuacion:", "informe:")) for l in ls)
    res = out["resultado"]
    assert res["detenida_en"] == "Descarte rápido"
    assert res["bloqueos"][0]["compuerta"] == "seguridad" and res["bloqueos"][0]["motivo"] == "REGISTRADA-SIN-DECIRLO"


@requires_node
def test_p8_evaluar_se_detiene_antes_de_puntuar_si_un_especialista_propone_bloqueada(project, capsys):  # D2-8
    reglas = [_regla_registro(project, capsys, "O-0003"), {"label": "plan:O-0003", "respuesta": _plan_evaluar(1)},
              {"labelPrefix": "desarrollo-industrial:", "respuesta": {
                  "archivo": "research/revisiones/O-0003_02_desarrollo-industrial.md", "resumen": "r", "afirmaciones": [],
                  "compuerta_propuesta": {"compuerta": "analitica", "estado": "bloqueada", "motivo": "SIN-METODO"}}},
              {"labelPrefix": "critica:", "respuesta": {"veredicto": "refutada", "debilidades": ["d"]}}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)
    ls = labels(out)
    assert not any(l.startswith(("puntuacion:", "informe:", "sello:", "registro-puntuacion:", "compuertas-registro:antes")) for l in ls)
    res = out["resultado"]
    assert res["detenida_en"] == "Especialistas"
    assert res["motivo"] == 'propuesta "bloqueada" de un especialista: analitica (desarrollo-industrial)'
    assert res["bloqueos"] == [{"agente": "desarrollo-industrial", "compuerta": "analitica", "motivo": "SIN-METODO",
                                "archivo": "research/revisiones/O-0003_02_desarrollo-industrial.md"}]


@requires_node
def test_p8_evaluar_relee_el_registro_antes_de_puntuar(project, capsys):  # D2-8
    _registrar_oportunidades(project, capsys, 3)
    limpio = _ver(capsys, "O-0003")
    _bloquear(capsys, "O-0003", "etica")
    reglas = [{"label": "compuertas-registro:inicio", "respuesta": limpio},
              {"label": "compuertas-registro:descarte", "respuesta": limpio},
              {"label": "compuertas-registro:antes-de-puntuar", "respuesta": _ver(capsys, "O-0003")},
              {"label": "plan:O-0003", "respuesta": _plan_evaluar(1)}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)
    ls = labels(out)
    assert set(ls[-2:]) == {"compuertas-registro:antes-de-puntuar", "procedencia:antes-de-puntuar"}  # en paralelo
    assert not any(l.startswith(("puntuacion:", "informe:", "sello:")) for l in ls)
    assert out["resultado"]["detenida_en"] == "Especialistas"
    assert [b["compuerta"] for b in out["resultado"]["bloqueos"]] == ["etica"]


# ------------------------------------------------------------------ evaluar-oportunidad-auto: independencia y sello
@requires_node
def test_p8_evaluar_evaluadores_ciegos_antes_del_informe(project, capsys):  # D1-3, D2-2
    reglas = [_regla_registro(project, capsys, "O-0003"), {"label": "plan:O-0003", "respuesta": _plan_evaluar(2)},
              *_reglas_puntuacion_emitida(project, capsys, "O-0003"),
              {"label": "informe:O-0003", "respuesta": {"archivo": "research/informes/O-0003.md", "decision_propuesta": "DECISION-DIRECTOR"}},
              {"label": "sello:O-0003", "respuesta": SELLO_VALIDO}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)
    ls = labels(out)
    evaluadores = [c for c in out["llamadas"] if c["label"].startswith("puntuacion:")]
    assert [c["label"] for c in evaluadores] == ["puntuacion:evaluador-1", "puntuacion:evaluador-2", "puntuacion:evaluador-3"]
    assert len({c["model"] for c in evaluadores}) == 3
    informe = ls.index("informe:O-0003")
    for c in evaluadores:
        ev = c["label"].split(":")[1]
        assert ls.index(c["label"]) < informe  # puntúan antes de la síntesis, el informe y la decisión
        assert c["agentType"] == "evaluador-independiente"
        assert f"nutria --agente {ev} oportunidad ver O-0003 --sin-puntuaciones --json" in c["prompt"]
        assert "research/informes/O-0003.md" not in c["prompt"] and "DECISION-DIRECTOR" not in c["prompt"]
        assert "decision_propuesta" not in c["prompt"]
        otros = [f"_puntuacion_evaluador-{n}" for n in (1, 2, 3) if f"evaluador-{n}" != ev]
        assert not any(o in c["prompt"] for o in otros)
        assert "NO ejecutes nutria puntuar registrar" in c["prompt"]
        assert "nutria decision listar" in c["prompt"] and "prevalecen sobre la skill puntuacion" in c["prompt"]
    assert ls.index("informe:O-0003") < ls.index("verificacion-informe:O-0003") < ls.index("sello:O-0003")
    # el director redacta después incorporando las puntuaciones; él no puntúa
    prompt_informe = call(out, "informe:O-0003")["prompt"]
    assert "research/oportunidades/O-0003_puntuacion_evaluador-1.json" in prompt_informe and "No puntúes" in prompt_informe
    registros = [c for c in out["llamadas"] if c["label"].startswith("registro-puntuacion:")]
    assert len(registros) == 3 and all(ls.index(c["label"]) > ls.index("sello:O-0003") for c in registros)
    for c in registros:
        ev = c["label"].split(":")[1]
        sha = _sha256(project / "research" / "oportunidades" / f"O-0003_puntuacion_{ev}.json")
        assert (f"echo '{sha}  research/oportunidades/O-0003_puntuacion_{ev}.json' | sha256sum -c - && "
                f"nutria --agente {ev} --json puntuar registrar --datos research/oportunidades/O-0003_puntuacion_{ev}.json") in c["prompt"]
    assert not any(c.get("agentType") == "director-cientifico" for c in evaluadores + registros)
    res = out["resultado"]
    assert res["evaluadores_registrados"] == 3 and res["informe"]["sello_valido"] is True
    assert res["informe"]["decision_propuesta"] == "DECISION-DIRECTOR"


@requires_node
def test_p8_evaluar_critica_y_verificacion_eligen_las_afirmaciones(project, capsys):  # D2-2
    reglas = [_regla_registro(project, capsys, "O-0003"), {"label": "plan:O-0003", "respuesta": _plan_evaluar(1)},
              {"labelPrefix": "investigador-bibliografico:", "respuesta": {
                  "archivo": "research/revisiones/O-0003_01_investigador-bibliografico.md", "resumen": "r",
                  "afirmaciones": [{"texto": "X", "etiqueta": "HIP"}]}}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)
    critica = call(out, "critica:investigador-bibliografico:4")  # las 3 preguntas obligatorias van primero
    assert critica["agentType"] == "critico-red-team"
    assert "Lee completo research/revisiones/O-0003_01_investigador-bibliografico.md" in critica["prompt"]
    assert "no te limites a las que el autor declara" in critica["prompt"]
    # sin afirmaciones [PUB] declaradas por el autor, las fuentes se verifican igualmente leyendo el fichero
    fuentes = call(out, "fuentes:investigador-bibliografico:4")
    assert fuentes["agentType"] == "verificador-citas"
    assert "TODAS las afirmaciones [PUB] de research/revisiones/O-0003_01_investigador-bibliografico.md" in fuentes["prompt"]


SELLOS_NO_VALIDOS = {
    "sello-no-valido": {"comprobar": _comprobar(), "sello": _sello(False, 1, "Sin sello")},
    "verificacion-incompleta": {"comprobar": _comprobar(False, codigo=1, pendientes=2), "sello": _sello()},
    "errores": {"comprobar": _comprobar(errores=("[R1] no tiene fila en el Anexo A",), codigo=1), "sello": _sello()},
    "incoherente": {"comprobar": _comprobar(), "sello": _sello(True, 1)},
    "declarado-por-el-llm": {"comprobar": {"orden": "x", "codigo_salida": 0, "salida": "Todo correcto"},
                             "sello": {"orden": "x", "codigo_salida": 0, "salida": "sello_valido: true"}},
    "sin-resultado": None,
    # cada comprobación de la salida de `informe comprobar`, por separado (las demás no la cubren)
    "solo-incompleta": {"comprobar": _comprobar(False, codigo=0, pendientes=2), "sello": _sello()},
    "solo-errores": {"comprobar": _comprobar(errores=("[R2] sin fila en el Anexo A",), codigo=0), "sello": _sello()},
    "solo-codigo": {"comprobar": _comprobar(codigo=1), "sello": _sello()},
}


@requires_node
@pytest.mark.parametrize("caso", sorted(SELLOS_NO_VALIDOS))
def test_p8_evaluar_sin_sello_valido_no_registra_puntuaciones(caso, project, capsys):  # D3-3
    reglas = [_regla_registro(project, capsys, "O-0003"), {"label": "plan:O-0003", "respuesta": _plan_evaluar(1)},
              *_reglas_puntuacion_emitida(project, capsys, "O-0003"),
              {"label": "informe:O-0003", "respuesta": {"archivo": "research/informes/O-0003.md", "decision_propuesta": "continuar"}},
              {"label": "sello:O-0003", "respuesta": SELLOS_NO_VALIDOS[caso]}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)
    sello = call(out, "sello:O-0003")
    assert "informe comprobar research/informes/O-0003.md --online --sellar" in sello["prompt"]
    assert "--json informe sello research/informes/O-0003.md" in sello["prompt"]
    assert not any(l.startswith("registro-puntuacion:") for l in labels(out))
    res = out["resultado"]
    assert res["informe"]["sello_valido"] is False and res["informe"]["motivo_sello"]
    assert res["evaluadores_registrados"] == 0
    assert [p["evaluador"] for p in res["puntuaciones_pendientes"]] == ["evaluador-1", "evaluador-2", "evaluador-3"]
    assert all(re.fullmatch(r"[0-9a-f]{64}", p["sha256"]) for p in res["puntuaciones_pendientes"])
    assert "registrar_puntuaciones" in res["siguiente"] and "herramienta Workflow" in res["siguiente"]
    assert "no se da por bueno" in res["siguiente"]


@requires_node
def test_p8_evaluar_interpreta_la_salida_real_de_informe_sello(project, capsys):  # D3-3
    (project / "research" / "informes" / "O-0003.md").write_text("---\nestado: borrador\n---\n# Informe\n", "utf-8")
    real = {"comprobar": _comprobar(), "sello": _salida(capsys, "--agente", "verificador-citas", "--json", "informe", "sello",
                                                         "research/informes/O-0003.md")}
    assert real["sello"]["codigo_salida"] != 0 and json.loads(real["sello"]["salida"])["valido"] is False
    reglas = [_regla_registro(project, capsys, "O-0003"), {"label": "plan:O-0003", "respuesta": _plan_evaluar(1)},
              *_reglas_puntuacion_emitida(project, capsys, "O-0003"), {"label": "sello:O-0003", "respuesta": real}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)
    assert not any(l.startswith("registro-puntuacion:") for l in labels(out))
    assert out["resultado"]["informe"]["sello_valido"] is False
    assert out["resultado"]["informe"]["motivo_sello"].startswith("nutria informe sello:")


@requires_node
def test_p8_evaluar_no_registra_un_fichero_de_puntuacion_modificado(project, capsys):  # D1-3, D3-3
    registro = _regla_registro(project, capsys, "O-0003")
    reglas_p = _reglas_puntuacion_emitida(project, capsys, "O-0003")
    # evaluador-1 modifica su fichero después de emitir la puntuación: sha256sum -c da FAILED y no se registra
    rel = "research/oportunidades/O-0003_puntuacion_evaluador-1.json"
    sha_emitido = reglas_p[0]["respuesta"]["sha256"]
    datos = json.loads((project / rel).read_text("utf-8"))
    datos["puntuaciones"][0]["valor"] = 1.0 if datos["puntuaciones"][0]["valor"] != 1.0 else 0.0
    (project / rel).write_text(json.dumps(datos), "utf-8")
    fallido = _registro_literal(project, capsys, "evaluador-1", rel, sha_emitido)
    assert fallido["codigo_salida"] != 0 and "FAILED" in fallido["salida"]
    reglas = [registro, {"label": "plan:O-0003", "respuesta": _plan_evaluar(1)},
              {"label": "registro-puntuacion:evaluador-1", "respuesta": fallido},
              *reglas_p, {"label": "sello:O-0003", "respuesta": SELLO_VALIDO}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)
    res = out["resultado"]
    ev1 = next(e for e in res["evaluadores"] if e["id"] == "evaluador-1")
    assert ev1["registrado"] is False and "sha256sum -c no confirma" in ev1["fallo"]
    assert res["evaluadores_registrados"] == 2
    assert [p["evaluador"] for p in res["puntuaciones_pendientes"]] == ["evaluador-1"]


ORDEN_PROCEDENCIA = ("--agente", "orquestador", "procedencia", "--accion", "puntuacion", "--ultimas", "20")


@requires_node
@pytest.mark.parametrize("sello_valido", [False, True], ids=["sin-sello", "con-sello"])
def test_p8_evaluar_detecta_registros_prematuros(sello_valido, project, capsys):  # D3-3, D1-3 (skill puntuacion de P6)
    registro = _regla_registro(project, capsys, "O-0003")
    reglas_p = _reglas_puntuacion_emitida(project, capsys, "O-0003")  # deja eventos "puntuacion" anteriores en la procedencia
    antes = _salida(capsys, *ORDEN_PROCEDENCIA)
    # evaluador-2 sigue el paso 5 de la skill en vez del prompt: registra su puntuación mientras puntúa (antes del sello)
    code, out = _cli(capsys, "--agente", "evaluador-2", "--json", "puntuar", "registrar", "--datos",
                     str(project / "research" / "oportunidades" / "O-0003_puntuacion_evaluador-2.json"))
    assert code == 0, out
    # entretanto, otra evaluación registra una puntuación de OTRA oportunidad: no es un registro prematuro de esta
    otra = project / "otra.json"
    otra.write_text(json.dumps({**_ejemplo("puntuacion_ejemplo"), "oportunidad": "O-0002", "evaluador": "evaluador-1"}), "utf-8")
    code, out = _cli(capsys, "--agente", "evaluador-1", "--json", "puntuar", "registrar", "--datos", str(otra))
    assert code == 0, out
    despues = _salida(capsys, *ORDEN_PROCEDENCIA)
    sello = SELLO_VALIDO if sello_valido else SELLOS_NO_VALIDOS["sello-no-valido"]
    base = [registro, {"label": "plan:O-0003", "respuesta": _plan_evaluar(1)}, *reglas_p, {"label": "sello:O-0003", "respuesta": sello}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", [*base, {"label": "procedencia:antes-de-puntuar", "respuesta": antes},
                                                               {"label": "procedencia:tras-puntuar", "respuesta": despues}])
    ls = labels(out)
    assert ls.index("procedencia:antes-de-puntuar") < min(ls.index(f"puntuacion:evaluador-{n}") for n in (1, 2, 3))
    assert ls.index("procedencia:tras-puntuar") > ls.index("verificacion-informe:O-0003")
    assert "nutria --agente orquestador procedencia --accion puntuacion --ultimas 20" in call(out, "procedencia:tras-puntuar")["prompt"]
    assert len(despues["salida"]) < 1000  # en texto: una línea corta por evento, sin valores
    assert call(out, "procedencia:tras-puntuar").get("agentType") is None  # agente mecánico, no un evaluador ni el director
    res = out["resultado"]
    assert res["registros_prematuros"]["comprobado"] is True and res["registros_prematuros"]["evaluadores"] == ["evaluador-2"]
    por_id = {e["id"]: e for e in res["evaluadores"]}
    assert "antes de comprobar el sello" in por_id["evaluador-2"]["alerta"] and por_id["evaluador-1"]["alerta"] is None
    assert ("una persona decide si se mantiene" in por_id["evaluador-2"]["alerta"]) is not sello_valido
    assert "Atención: evaluador-2 registró su puntuación antes de comprobar el sello" in res["siguiente"]
    assert any("registro diferido incumplido" in m for m in out["logs"])
    # sin registros nuevos no hay alerta; sin lecturas interpretables, consta que no se pudo comprobar
    limpio = run_workflow("evaluar-oportunidad-auto", "O-0003", [*base, {"labelPrefix": "procedencia:", "respuesta": despues}])
    assert limpio["resultado"]["registros_prematuros"] == {"comprobado": True, "evaluadores": [], "eventos": []}
    assert "antes de comprobar el sello" not in limpio["resultado"]["siguiente"]
    sin = run_workflow("evaluar-oportunidad-auto", "O-0003", base)
    assert sin["resultado"]["registros_prematuros"]["comprobado"] is False
    assert "no se pudo comprobar en la procedencia" in sin["resultado"]["registros_prematuros"]["motivo"]


@requires_node
def test_p8_evaluar_registro_sin_comprobacion_literal_del_sha256(project, capsys):  # D3-3: el agente no declara si coincide
    registro = _regla_registro(project, capsys, "O-0003")
    reglas_p = _reglas_puntuacion_emitida(project, capsys, "O-0003")
    solo_registro = _salida(capsys, "--agente", "evaluador-2", "--json", "puntuar", "registrar", "--datos",
                            str(project / "research" / "oportunidades" / "O-0003_puntuacion_evaluador-2.json"))
    declarado = {"orden": "sha256sum ...", "codigo_salida": 0, "salida": "coincide: true\n"}  # declaración, no la salida de sha256sum -c
    reglas = [registro, {"label": "plan:O-0003", "respuesta": _plan_evaluar(1)},
              {"label": "registro-puntuacion:evaluador-2", "respuesta": solo_registro},
              {"label": "registro-puntuacion:evaluador-3", "respuesta": declarado},
              *reglas_p, {"label": "sello:O-0003", "respuesta": SELLO_VALIDO}]
    res = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)["resultado"]
    por_id = {e["id"]: e for e in res["evaluadores"]}
    assert por_id["evaluador-1"]["registrado"] is True and por_id["evaluador-1"]["alerta"] is None
    # la salida completa pero con un código distinto de 0 no confirma el registro (queda pendiente)
    con_error = {**reglas_p[1]["respuesta"], "codigo_salida": 1}
    res_error = run_workflow("evaluar-oportunidad-auto", "O-0003", [registro, {"label": "plan:O-0003", "respuesta": _plan_evaluar(1)},
                                                                     {"label": "registro-puntuacion:evaluador-1", "respuesta": con_error},
                                                                     *reglas_p, {"label": "sello:O-0003", "respuesta": SELLO_VALIDO}])["resultado"]
    ev1 = next(e for e in res_error["evaluadores"] if e["id"] == "evaluador-1")
    assert ev1["registrado"] is False and "no confirma el registro (código 1)" in ev1["fallo"]
    assert [p["evaluador"] for p in res_error["puntuaciones_pendientes"]] == ["evaluador-1"]
    # registró sin la comprobación literal: consta como registrada (lo está), con alerta para revisarlo
    assert por_id["evaluador-2"]["registrado"] is True and "sin la comprobación literal del sha256" in por_id["evaluador-2"]["alerta"]
    # declarar que coincide no basta: sin "<fichero>: OK" ni registro confirmado, no consta registrada
    assert por_id["evaluador-3"]["registrado"] is False and "sha256sum -c no confirma" in por_id["evaluador-3"]["fallo"]


@requires_node
def test_p8_evaluar_modo_registrar_puntuaciones_pendientes(project, capsys):  # D3-3
    _registrar_oportunidades(project, capsys, 3)
    reglas_p = _reglas_puntuacion_emitida(project, capsys, "O-0003")
    pendientes = [{"evaluador": f"evaluador-{n}", "sha256": reglas_p[2 * (n - 1)]["respuesta"]["sha256"], "modelo": m}
                  for n, m in ((1, "opus"), (2, "sonnet"), (3, "fable"))]
    args = {"id": "O-0003", "registrar_puntuaciones": pendientes}
    out = run_workflow("evaluar-oportunidad-auto", args, [_regla_registro(project, capsys, "O-0003"), *reglas_p,
                                                          {"label": "sello:O-0003", "respuesta": SELLO_VALIDO}])
    assert labels(out) == ["compuertas-registro:inicio", "sello:O-0003", "registro-puntuacion:evaluador-1",
                           "registro-puntuacion:evaluador-2", "registro-puntuacion:evaluador-3"]
    assert [c["model"] for c in out["llamadas"][2:]] == ["opus", "sonnet", "fable"]
    assert out["resultado"]["evaluadores_registrados"] == 3 and out["resultado"]["puntuaciones_pendientes"] == []
    # sin sello válido no se registra nada y siguen pendientes
    sin = run_workflow("evaluar-oportunidad-auto", args, [_regla_registro(project, capsys, "O-0003"),
                                                          {"label": "sello:O-0003", "respuesta": SELLOS_NO_VALIDOS["sello-no-valido"]}])
    assert labels(sin) == ["compuertas-registro:inicio", "sello:O-0003"]
    assert len(sin["resultado"]["puntuaciones_pendientes"]) == 3
    # una lista mal formada no lanza nada
    for mala in ([], [{"evaluador": "evaluador-1", "sha256": "abc"}], [pendientes[0], pendientes[0]], "evaluador-1"):
        err = run_workflow("evaluar-oportunidad-auto", {"id": "O-0003", "registrar_puntuaciones": mala})
        assert err["llamadas"] == [] and "registrar_puntuaciones" in err["resultado"]["error"]


@requires_node
def test_p8_evaluar_modo_registrar_respeta_las_compuertas_bloqueadas(project, capsys):  # D1-7, D3-3
    _registrar_oportunidades(project, capsys, 3)
    _bloquear(capsys, "O-0003", "regulatoria")
    args = {"id": "O-0003", "registrar_puntuaciones": [{"evaluador": "evaluador-1", "sha256": "a" * 64}]}
    out = run_workflow("evaluar-oportunidad-auto", args, [{"labelPrefix": "compuertas-registro:", "respuesta": _ver(capsys, "O-0003")},
                                                          {"label": "sello:O-0003", "respuesta": SELLO_VALIDO}])
    assert labels(out) == ["compuertas-registro:inicio"]
    assert out["resultado"]["detenida_en"] == "Compuertas del registro"


# ------------------------------------------------------------------ evaluar-oportunidad-auto: topes y estimación
@requires_node
def test_p8_evaluar_tope_de_preguntas_y_estimacion_coherente(project, capsys):  # D10-3
    estudios = [{"fuente": f"doi:10.1000/e{i}", "especie": "vacuno lechero", "tipo_estudio": "ensayo", "datos_cuantitativos": True,
                 "comparable": True} for i in range(1, 41)]
    reglas = [_regla_registro(project, capsys, "O-0003"), {"label": "plan:O-0003", "respuesta": _plan_evaluar(25)},
              {"label": "bibliografia:O-0003", "respuesta": {"archivo": "b.md", "estudios": estudios}},
              {"labelPrefix": "conciliacion:", "respuesta": {"discrepancias": 0, "registrada": True, "estado_verificacion": "doble_concordante"}},
              {"labelRegex": r"^puntuacion:evaluador-\d$", "respuesta": None}]  # peor caso: todos los evaluadores se reintentan
    out = run_workflow("evaluar-oportunidad-auto", {"id": "O-0003", "max_estudios": 30, "max_preguntas": 15}, reglas)
    ls = labels(out)
    especialistas = [c for c in out["llamadas"] if c["phase"] == "Especialistas"]
    assert len(especialistas) == 15
    assert len([l for l in ls if l.startswith("extraccion:A:")]) == 30
    assert len(ls) <= _maximo_logueado(out)
    assert any("Límite de 15 preguntas" in m for m in out["logs"])
    assert len(out["resultado"]["preguntas_sin_lanzar"]) == 25 + 3 - 15
    # por defecto, 10 preguntas como máximo (incluidas las 3 de las compuertas)
    defecto = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas[:2])
    assert len([c for c in defecto["llamadas"] if c["phase"] == "Especialistas"]) == 10
    assert len(labels(defecto)) <= _maximo_logueado(defecto)
    assert call(defecto, "plan:O-0003")["schema"]["properties"]["preguntas"]["maxItems"] == 10


@requires_node
@pytest.mark.parametrize("args", [{"id": "O-0003", "max_preguntas": 2}, {"id": "O-0003", "max_preguntas": 16},
                                  {"id": "O-0003", "max_estudios": 0}, {"id": "O-0003", "max_estudios": "10"}])
def test_p8_evaluar_topes_no_validos_no_lanzan_nada(args):  # D10-3
    out = run_workflow("evaluar-oportunidad-auto", args)
    assert out["llamadas"] == [] and "debe ser un entero" in out["resultado"]["error"]


# ------------------------------------------------------------------ mensajes: workflows por su nombre, no con barra
def test_p8_ningun_mensaje_presenta_un_workflow_como_orden_con_barra():  # D10-9
    nombres = "|".join(re.escape(p.stem) for p in WORKFLOWS)
    patron = re.compile(rf"(?<![\w./-])/({nombres})\b")
    for path in [*WORKFLOWS, *sorted(SKILLS_DIR.glob("*/SKILL.md")), *sorted(AGENTS_DIR.glob("*.md"))]:
        assert not patron.search(_text(path)), f"{path.name}: {patron.search(_text(path)).group(0)}"


@requires_node
def test_p8_siguiente_de_evaluar_lanza_red_team_por_su_nombre(project, capsys):  # D10-9
    reglas = [_regla_registro(project, capsys, "O-0003"), {"label": "plan:O-0003", "respuesta": _plan_evaluar(1)},
              *_reglas_puntuacion_emitida(project, capsys, "O-0003"), {"label": "sello:O-0003", "respuesta": SELLO_VALIDO}]
    siguiente = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)["resultado"]["siguiente"]
    assert "red-team-auto por su nombre con la herramienta Workflow" in siguiente
    assert '{"archivo": "research/informes/O-0003.md"}' in siguiente
    assert not re.search(r"/red-team-auto", siguiente)


# ------------------------------------------------------------------ red-team-auto
@requires_node
@pytest.mark.parametrize("args,autor,extractor,refutadores", [
    ("research/informes/O-0003.md", "director-cientifico", "critico-red-team",
     {"fuentes": "verificador-citas", "biologia": "critico-red-team", "viabilidad": "critico-red-team"}),
    ("research/planes/O-0003.md", "director-cientifico", "critico-red-team",
     {"fuentes": "verificador-citas", "biologia": "critico-red-team", "viabilidad": "critico-red-team"}),
    ("research/revisiones/informes_O-0003_redteam.md", "critico-red-team", "verificador-citas",
     {"fuentes": "verificador-citas", "biologia": "fisiologia-bioquimica", "viabilidad": "regulacion-seguridad"}),
    ({"archivo": "research/revisiones/notas.md", "autor": "critico-red-team"}, "critico-red-team", "verificador-citas",
     {"fuentes": "verificador-citas", "biologia": "fisiologia-bioquimica", "viabilidad": "regulacion-seguridad"}),
    ({"archivo": "x.md", "autor": "verificador-citas"}, "verificador-citas", "critico-red-team",
     {"fuentes": "investigador-bibliografico", "biologia": "critico-red-team", "viabilidad": "critico-red-team"}),
    ("research/revisiones/O-0003_descarte_destino_digestivo.md", "fisiologia-bioquimica", "critico-red-team",
     {"fuentes": "verificador-citas", "biologia": "critico-red-team", "viabilidad": "critico-red-team"}),
])
def test_p8_red_team_ningun_paso_lo_hace_el_autor(args, autor, extractor, refutadores):  # D2-2
    reglas = [{"label": "extraccion", "respuesta": {"afirmaciones": [{"id": "A1", "texto": "t"}, {"id": "A2", "texto": "u"}]}}]
    out = run_workflow("red-team-auto", args, reglas)
    assert call(out, "extraccion")["agentType"] == extractor
    assert call(out, "consolidacion")["agentType"] == extractor
    for enfoque, rol in refutadores.items():
        assert call(out, f"A1:{enfoque}")["agentType"] == rol
    assert not any(c.get("agentType") == autor for c in out["llamadas"])
    assert out["resultado"]["autor"]["rol"] == autor
    assert f"nutria --agente {extractor} decision registrar" in call(out, "consolidacion")["prompt"]


@requires_node
def test_p8_red_team_autor_no_valido():  # D2-2
    out = run_workflow("red-team-auto", {"archivo": "x.md", "autor": "nadie"})
    assert out["llamadas"] == [] and '"autor" no válido' in out["resultado"]["error"]


VEREDICTOS_RT = ("sostenida", "debilitada", "refutada", "no_verificable", None)


@requires_node
def test_p8_red_team_regla_de_fuentes_en_todas_las_combinaciones():  # D2-10
    combinaciones = [(f, b, v) for f in VEREDICTOS_RT for b in VEREDICTOS_RT for v in VEREDICTOS_RT]
    resultados = {}
    for inicio in range(0, len(combinaciones), 25):
        bloque = combinaciones[inicio:inicio + 25]
        ids = [f"C{inicio + i + 1}" for i in range(len(bloque))]
        reglas = [{"label": "extraccion", "respuesta": {"afirmaciones": [{"id": i, "texto": i} for i in ids]}}]
        for i, votos in zip(ids, bloque):
            for enfoque, voto in zip(("fuentes", "biologia", "viabilidad"), votos):
                reglas.append({"label": f"{i}:{enfoque}", "respuesta": None if voto is None else {"veredicto": voto, "motivo": "m"}})
        out = run_workflow("red-team-auto", {"archivo": "research/informes/O-0003.md", "max_afirmaciones": 25}, reglas)
        for r in out["resultado"]["resultados"]:
            resultados[bloque[ids.index(r["id"])]] = r["resultado"]
    assert len(resultados) == len(combinaciones)
    for (f, b, v), res in resultados.items():
        validos = [x for x in (f, b, v) if x is not None]
        if len(validos) < 2:
            assert res == "sin_quorum", (f, b, v, res)
        elif f == "refutada":
            assert res == "refutada", (f, b, v, res)
        elif f != "sostenida":  # fuentes no_verificable, debilitada o sin resultado: nunca sostenida
            assert res in ("debilitada", "no_verificable", "refutada"), (f, b, v, res)
        if res == "sostenida":
            assert f == "sostenida" and "refutada" not in (b, v), (f, b, v, res)
    # casos señalados por el escéptico y por el diseño
    assert resultados[("no_verificable", "sostenida", "sostenida")] == "no_verificable"
    assert resultados[(None, "sostenida", "sostenida")] == "no_verificable"
    assert resultados[("no_verificable", "debilitada", "sostenida")] == "debilitada"
    assert resultados[("refutada", "sostenida", "sostenida")] == "refutada"
    assert resultados[("sostenida", "sostenida", "no_verificable")] == "sostenida"


def test_p8_red_team_regla_de_fuentes_documentada_en_script_y_skill():  # D2-10
    script = _norm(_text(WORKFLOWS_DIR / "red-team-auto.js"))
    assert 'si "fuentes" da "no_verificable" o no da resultado, la afirmación nunca sale "sostenida"' in script
    skill = _norm(_skill("red-team"))
    assert "si el enfoque de fuentes da `no_verificable` (p. ej. sin red) o no da resultado, la afirmación nunca sale `sostenida`" in skill


@requires_node
@pytest.mark.parametrize("args,destino", [
    ("research/planes/O-0001.md", "research/revisiones/planes_O-0001_redteam.md"),
    ("research/informes/O-0001.md", "research/revisiones/informes_O-0001_redteam.md"),
    ("/home/u/nutria/research/informes/O-0001.md", "research/revisiones/informes_O-0001_redteam.md"),
    ("research/informes/sub/O-0001.md", "research/revisiones/informes_sub_O-0001_redteam.md"),
    ({"archivo": "notas.md", "autor": "persona"}, "research/revisiones/notas_redteam.md"),
])
def test_p8_red_team_salida_incluye_el_directorio_de_origen(args, destino):  # D2-16
    out = run_workflow("red-team-auto", args)
    assert out["resultado"]["informe"] == destino
    assert f"Escribe {destino}" in call(out, "consolidacion")["prompt"]


@requires_node
def test_p8_red_team_revisar_plan_e_informe_no_se_pisan():  # D2-16 (reproducción del hallazgo)
    destinos = {run_workflow("red-team-auto", f"research/{d}/O-0001.md")["resultado"]["informe"] for d in ("planes", "informes")}
    assert destinos == {"research/revisiones/planes_O-0001_redteam.md", "research/revisiones/informes_O-0001_redteam.md"}


@requires_node
@pytest.mark.parametrize("archivo", ["notas.md", "research/revisiones/O-0003_verificacion.md",
                                     "research/oportunidades/M1_2026-11-04_colina.json"])
def test_p8_red_team_sin_autor_deducible_no_empieza(archivo):  # D2-2: falla en cerrado
    out = run_workflow("red-team-auto", archivo)
    assert out["llamadas"] == []
    assert '"autor"' in out["resultado"]["error"] and "quien genera no evalúa" in out["resultado"]["error"]
    con_autor = run_workflow("red-team-auto", {"archivo": archivo, "autor": "verificador-citas"})
    assert call(con_autor, "extraccion")["agentType"] == "critico-red-team"
    assert {c.get("agentType") for c in con_autor["llamadas"] if c["label"].endswith(":fuentes")} == {"investigador-bibliografico"}


@requires_node
def test_p8_red_team_tope_de_afirmaciones_y_estimacion():  # D10-3
    afirmaciones = [{"id": f"A{i}", "texto": f"t{i}"} for i in range(1, 41)]
    reglas = [{"label": "extraccion", "respuesta": {"afirmaciones": afirmaciones}}]
    out = run_workflow("red-team-auto", "research/informes/O-0003.md", reglas)
    refutaciones = [c for c in out["llamadas"] if c["phase"] == "Refutación"]
    assert len(refutaciones) == 15 * 3
    assert out["resultado"]["no_revisadas"] == [f"A{i}" for i in range(16, 41)]
    assert len(out["llamadas"]) <= _maximo_logueado(out)
    assert call(out, "extraccion")["schema"]["properties"]["afirmaciones"]["maxItems"] == 15
    treinta = run_workflow("red-team-auto", {"archivo": "research/informes/O-0003.md", "max_afirmaciones": 30}, reglas)
    assert len([c for c in treinta["llamadas"] if c["phase"] == "Refutación"]) == 90
    assert len(treinta["llamadas"]) <= _maximo_logueado(treinta)
    for malo in (0, 31, "15"):
        err = run_workflow("red-team-auto", {"archivo": "x.md", "max_afirmaciones": malo})
        assert err["llamadas"] == [] and "max_afirmaciones" in err["resultado"]["error"]


# ------------------------------------------------------------------ mision-1-auto: paso 0, idempotencia y topes
def _problema(i: int, especie: str = "vacuno lechero") -> dict:
    return {"id": f"P{i}", "titulo": f"Problema {i}", "especie": especie, "etapa": "transición", "mecanismos": ["m"],
            "soluciones_actuales": f"SOLUCIONES-P{i}"}


def _reglas_novedad(veredicto="nueva") -> list[dict]:
    return [{"labelPrefix": "M1b:novedad-literatura", "respuesta": {"veredicto": veredicto, "literatura": "l"}},
            {"labelPrefix": "M1b:novedad-mercado", "respuesta": {"veredicto": veredicto, "productos": "x", "patentes": "p"}}]


def _registro_secuencial(n: int) -> list[dict]:
    return [{"label": f"registro:{i}", "respuesta": {"id": f"O-{i:04d}"}} for i in range(1, n + 1)]


@requires_node
@pytest.mark.parametrize("args", [{"fase": "mapa"}, {"fase": "oportunidades", "problemas": ["Problema"]},
                                  {"fase": "planes", "seleccion": ["O-0001"]}], ids=["mapa", "oportunidades", "planes"])
def test_p8_mision1_paso0_en_cada_fase(args, project, capsys):  # D2-15
    out = run_workflow("mision-1-auto", args, [_regla_paso0(project, capsys)])
    paso0 = out["llamadas"][0]
    assert paso0["label"] == "paso0" and out["fases"][0] == "Preparación"
    for orden in ("nutria --agente orquestador --json doctor", "2) nutria --agente orquestador estado;", "date -u +%F",
                  "ls -1 research/revisiones research/oportunidades research/hipotesis research/planes"):
        assert orden in paso0["prompt"], orden
    assert "--json estado" not in paso0["prompt"]  # el registro, en texto: una línea por oportunidad (compacto)
    assert out["resultado"]["preparacion"]["fuentes_no_accesibles"] == ["PubMed (NCBI E-utilities): BLOQUEADO por proxy/política de red"]
    assert out["resultado"]["fecha"] == "2026-11-04"  # la del sistema (paso 0) si no se indica args.fecha


@requires_node
def test_p8_mision1_sin_registro_no_continua_la_fase_oportunidades():  # D2-15
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": ["Problema"]},
                       [{"label": "paso0", "respuesta": None}])
    assert labels(out) == ["paso0"]
    assert out["resultado"]["detenida_en"] == "Preparación"


@requires_node
@pytest.mark.parametrize("doctor,no_accesibles,aviso", [
    # formato de cadenas (esta rama)
    (DOCTOR_SIN_RED, ["PubMed (NCBI E-utilities): BLOQUEADO por proxy/política de red"], None),
    # formato de objetos (rama de clientes y red, P4): todas las fuentes responden
    (_doctor_objetos({"PubMed (NCBI E-utilities)": "ok", "Europe PMC": "ok", "Crossref": "ok"}), [], None),
    # formato de objetos con fuentes bloqueadas, sin clave y modo offline
    (_doctor_objetos({"PubMed (NCBI E-utilities)": "ok", "Crossref": "bloqueado", "OpenAlex": "sin_clave"}),
     ["Crossref: BLOQUEADO por proxy/política de red", "OpenAlex: sin clave (no se comprueba)"], "fallan comprobaciones esenciales (Crossref)"),
    (_doctor_objetos({"PubMed (NCBI E-utilities)": "no_comprobado"}, offline=True),
     ["PubMed (NCBI E-utilities): no comprobado (modo offline: solo la caché)"], "modo offline"),
], ids=["cadenas", "objetos-ok", "objetos-bloqueados", "objetos-offline"])
def test_p8_mision1_paso0_acepta_los_dos_formatos_de_doctor(doctor, no_accesibles, aviso, project, capsys):  # D2-15 (integración con P4)
    out = run_workflow("mision-1-auto", {"fase": "mapa"}, [_regla_paso0(project, capsys, doctor=doctor)])
    preparacion = out["resultado"]["preparacion"]
    assert preparacion["fuentes_no_accesibles"] == no_accesibles
    textos = [*out["logs"], *(c["prompt"] for c in out["llamadas"])]
    assert not any("[object Object]" in t for t in textos)
    mapa = call(out, "M1a:fisiologia-bioquimica")["prompt"]
    if no_accesibles:
        assert "Fuentes no accesibles según nutria doctor: " + "; ".join(no_accesibles) in mapa
    else:
        assert "Fuentes no accesibles" not in mapa
    if aviso:
        assert any(aviso in a for a in preparacion["avisos"]), preparacion["avisos"]


@requires_node
def test_p8_mision1_paso0_estado_compacto_y_comprobado(project, capsys):  # D2-15 (escala del paso 0)
    _registrar_oportunidades(project, capsys, 24)
    texto = _salida(capsys, "--agente", "orquestador", "estado")
    en_json = _salida(capsys, "--agente", "orquestador", "--json", "estado")
    assert len(texto["salida"]) * 10 < len(en_json["salida"])  # una línea por oportunidad frente a todo su "data"
    base = _ejemplo("oportunidad_ejemplo")["titulo"]
    gen = {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp(f"{base} (24)"), _opp("Oportunidad nueva")]}}
    args = {"fase": "oportunidades", "problemas": [_problema(1)], "n_oportunidades": 1}
    reglas = [_regla_paso0(project, capsys), gen, *_reglas_novedad(), {"label": "registro:1", "respuesta": {"id": "O-0025"}}]
    out = run_workflow("mision-1-auto", args, reglas)
    assert out["resultado"]["preparacion"]["oportunidades_registradas"] == 24
    assert {"titulo": f"{base} (24)", "motivo": "ya registrada (O-0024)"} in out["resultado"]["no_registradas"]
    # una salida recortada (falta la última línea) no se acepta: la fase no continúa (falla en cerrado)
    recortada = {**texto, "salida": "\n".join(texto["salida"].rstrip("\n").split("\n")[:-1]) + "\n"}
    errores = {}
    for caso, salida_mala in (("recortada", recortada), ("json", en_json), ("codigo", {**texto, "codigo_salida": 1})):
        paso0 = _regla_paso0(project, capsys)
        paso0["respuesta"]["estado"] = salida_mala
        detenida = run_workflow("mision-1-auto", args, [paso0, *reglas[1:]])
        assert labels(detenida) == ["paso0"], caso
        assert detenida["resultado"]["detenida_en"] == "Preparación", caso
        errores[caso] = detenida["resultado"]["error"]
    assert "resume 24 oportunidad(es) y lista 23: salida recortada o incompleta" in errores["recortada"]
    assert 'falta la línea "Oportunidades:"' in errores["json"]
    assert "terminó con código 1" in errores["codigo"]


@requires_node
@pytest.mark.parametrize("ficheros", [
    "/home/ana/nutria/research/oportunidades:\nM1_2026-11-04_oportunidad-a.json\n",  # cabecera con ruta absoluta
    "research/oportunidades:\nM1_2026-11-04_oportunidad-a.json  otro.json  tercero.json\n",  # nombres en columnas (sin -1)
    "research/otra-carpeta:\nM1_2026-11-04_oportunidad-a.json\n\nresearch/oportunidades:\nx.json\n",  # cabecera no reconocida
    "ls: cannot access 'research/planes': No such file or directory\nM1_2026-11-04_oportunidad-a.json\n",  # sin cabecera
], ids=["ruta-absoluta", "columnas", "cabecera-desconocida", "sin-cabecera"])
def test_p8_mision1_ls_falla_en_cerrado(ficheros, project, capsys):  # D2-15: no se elige un nombre que ya existe
    reglas = [_regla_paso0(project, capsys, ficheros=ficheros),
              {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp("Oportunidad A")]}}, *_reglas_novedad(), *_registro_secuencial(1)]
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": [_problema(1)], "n_oportunidades": 1}, reglas)
    assert "research/oportunidades/M1_2026-11-04_oportunidad-a-2.json" in call(out, "registro:1")["prompt"]


@requires_node
def test_p8_mision1_ls_reconoce_cabeceras_absolutas_por_directorio(project, capsys):  # D2-15
    # el nombre está en research/revisiones (cabecera absoluta reconocida): no ocupa el de research/oportunidades
    ficheros = "/home/ana/nutria/research/revisiones:\nM1_2026-11-04_oportunidad-a.json\n\n/home/ana/nutria/research/oportunidades:\nx.json\n"
    reglas = [_regla_paso0(project, capsys, ficheros=ficheros),
              {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp("Oportunidad A")]}}, *_reglas_novedad(), *_registro_secuencial(1)]
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": [_problema(1)], "n_oportunidades": 1}, reglas)
    assert "research/oportunidades/M1_2026-11-04_oportunidad-a.json " in call(out, "registro:1")["prompt"]


@requires_node
def test_p8_mision1_deduplica_contra_el_registro(project, capsys):  # D2-15
    registrada = _registrar_oportunidad(project, capsys, "Colina protegida en vacas en transición")
    gen = {"oportunidades": [_opp("COLINA  protegida en VACAS en transición."), _opp("Oportunidad nueva")]}
    reglas = [_regla_paso0(project, capsys), {"label": "M1b:gen:P1", "respuesta": gen}, *_reglas_novedad(),
              {"label": "registro:1", "respuesta": {"id": "O-0002"}}]
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": [_problema(1)], "n_oportunidades": 1}, reglas)
    ls = labels(out)
    assert "Colina protegida en vacas en transición" in call(out, "M1b:gen:P1")["prompt"]  # el generador la conoce
    assert "M1b:novedad-literatura:P1.1" not in ls and "M1b:novedad-mercado:P1.1" not in ls  # no se gasta en comprobarla
    assert "M1b:novedad-literatura:P1.2" in ls
    assert [l for l in ls if l.startswith("registro:")] == ["registro:1"]
    assert "Oportunidad nueva" in call(out, "registro:1")["prompt"]
    assert {"titulo": "COLINA  protegida en VACAS en transición.", "motivo": f"ya registrada ({registrada})"} in out["resultado"]["no_registradas"]


@requires_node
def test_p8_mision1_nombres_de_fichero_que_no_se_pisan(project, capsys):  # D2-15
    for d, nombre in (("revisiones", "M1a_mapa_problemas_2026-11-04.md"), ("revisiones", "M1_ranking_2026-11-04.md"),
                      ("oportunidades", "M1_2026-11-04_oportunidad-a.json"), ("hipotesis", "M1_2026-11-04_oportunidad-b_H1.json")):
        (project / "research" / d / nombre).write_text("{}", "utf-8")
    titulo_largo = "Una oportunidad con un título muy largo que comparte el principio"
    gen = {"oportunidades": [_opp("Oportunidad A"), _opp("Oportunidad B"), _opp(titulo_largo + " uno")]}
    gen2 = {"oportunidades": [_opp(titulo_largo + " dos")]}
    reglas = [_regla_paso0(project, capsys), {"label": "M1b:gen:P1", "respuesta": gen}, {"label": "M1b:gen:P2", "respuesta": gen2},
              *_reglas_novedad(), *_registro_secuencial(4)]
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": [_problema(1), _problema(2)], "n_oportunidades": 4}, reglas)
    prompts = {c["label"]: c["prompt"] for c in out["llamadas"]}
    assert "research/oportunidades/M1_2026-11-04_oportunidad-a-2.json" in prompts["registro:1"]
    assert "research/oportunidades/M1_2026-11-04_oportunidad-b-2.json" in prompts["registro:2"]
    base = "M1_2026-11-04_una-oportunidad-con-un-titulo-muy-largo-que"  # cortado en un límite de palabra
    assert f"research/oportunidades/{base}.json" in prompts["registro:3"]
    assert f"research/oportunidades/{base}-2.json" in prompts["registro:4"]
    assert f"research/hipotesis/{base}-2_H<n>.json" in prompts["registro:4"]
    assert "research/revisiones/M1_ranking_2026-11-04-2.md" in prompts["ranking"]
    mapa = run_workflow("mision-1-auto", {"fase": "mapa"}, [_regla_paso0(project, capsys)])
    assert "research/revisiones/M1a_mapa_problemas_2026-11-04-2.md" in call(mapa, "M1a:consolidacion")["prompt"]
    otra_fecha = run_workflow("mision-1-auto", {"fase": "mapa", "fecha": "2026-12-01"}, [_regla_paso0(project, capsys)])
    assert "research/revisiones/M1a_mapa_problemas_2026-12-01.md" in call(otra_fecha, "M1a:consolidacion")["prompt"]


@requires_node
def test_p8_mision1_n_oportunidades_es_un_tope(project, capsys):  # D10-3
    problemas = [_problema(i) for i in range(1, 9)]
    gen = [{"label": f"M1b:gen:P{i}", "respuesta": {"oportunidades": [_opp(f"P{i} oportunidad {j}") for j in range(1, 6)]}}
           for i in range(1, 9)]
    reglas = [_regla_paso0(project, capsys), *gen, *_reglas_novedad(), *_registro_secuencial(24),
              {"labelRegex": r"^puntuacion:evaluador-\d:O-\d{4}$", "respuesta": None}]  # peor caso: reintentos
    for n_ops in (5, 24):
        out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": problemas, "n_oportunidades": n_ops}, reglas)
        ls = labels(out)
        assert len([l for l in ls if l.startswith("registro:")]) == n_ops
        assert len(out["resultado"]["oportunidades"]) == n_ops
        assert len([l for l in ls if l.startswith("M1b:novedad-literatura")]) == 8 * 3  # como máximo 3 por generador
        assert len(ls) <= _maximo_logueado(out)
        fuera = [o for o in out["resultado"]["no_registradas"] if o["motivo"].startswith("fuera del tope")]
        assert len(fuera) == 24 - n_ops


@requires_node
@pytest.mark.parametrize("args,mensaje", [
    ({"fase": "oportunidades", "problemas": [f"P{i}" for i in range(9)]}, "el tope es 8"),
    ({"fase": "oportunidades", "problemas": [f"P{i}" for i in range(13)], "max_problemas": 12}, "el tope es 12"),
    ({"fase": "oportunidades", "problemas": ["P"], "max_problemas": 13}, '"max_problemas" debe ser un entero'),
    ({"fase": "oportunidades", "problemas": ["P"], "n_oportunidades": 25}, '"n_oportunidades" debe ser un entero'),
    ({"fase": "oportunidades", "problemas": ["P"], "n_oportunidades": "12"}, '"n_oportunidades" debe ser un entero'),
    ({"fase": "mapa", "fecha": "04/11/2026"}, 'con el formato AAAA-MM-DD'),
])
def test_p8_mision1_topes_no_validos_no_lanzan_nada(args, mensaje):  # D10-3
    out = run_workflow("mision-1-auto", args)
    assert out["llamadas"] == [] and mensaje in out["resultado"]["error"]


@requires_node
def test_p8_mision1_minimo_de_rumiantes(project, capsys):  # D2-15
    problemas = [_problema(1, "vacuno lechero"), _problema(2, "porcino")]
    reglas = [_regla_paso0(project, capsys),
              {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp("Vacuno 1")]}},
              {"label": "M1b:gen:P2", "respuesta": {"oportunidades": [_opp(f"Cerdo {j}", "porcino") for j in (1, 2, 3)]}},
              {"label": "M1b:gen:P1:r1", "respuesta": {"oportunidades": [_opp(f"Vacuno {j}") for j in (2, 3, 4)]}},
              *_reglas_novedad(), *_registro_secuencial(4)]
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": problemas, "n_oportunidades": 4}, reglas)
    ls = labels(out)
    # con 4 candidatas ya está el total, pero faltan rumiantes (1 de 3): ronda extra solo con el problema de rumiantes
    assert [l for l in ls if l.startswith("M1b:gen")] == ["M1b:gen:P1", "M1b:gen:P2", "M1b:gen:P1:r1"]
    assert "Faltan oportunidades en rumiantes" in call(out, "M1b:gen:P1:r1")["prompt"]
    res = out["resultado"]
    assert sorted(o["titulo"] for o in res["oportunidades"]) == ["Cerdo 1", "Vacuno 1", "Vacuno 2", "Vacuno 3"]
    assert res["minimo_mision"] == {"requisito": "al menos 10 oportunidades, al menos 6 en rumiantes", "oportunidades": 4,
                                    "rumiantes": 3, "cumple": False}
    assert any("no se cumple el mínimo de la misión" in m for m in out["logs"])
    # sin problemas de rumiantes lo avisa y no genera en vano
    solo_cerdo = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": [_problema(2, "porcino")], "n_oportunidades": 3},
                              [_regla_paso0(project, capsys), reglas[2], *_reglas_novedad(), *_registro_secuencial(3)])
    assert [l for l in labels(solo_cerdo) if l.startswith("M1b:gen")] == ["M1b:gen:P2"]
    assert any("ningún problema aprobado es de rumiantes" in m for m in solo_cerdo["logs"])


def test_p8_mision1_reconoce_especies_de_rumiantes():  # D2-15
    js = _text(WORKFLOWS_DIR / "mision-1-auto.js")
    patron = re.search(r"const RUMIANTES_RE = /(.+)/\n", js).group(1)
    proc = subprocess.run([NODE or "node", "-e", f"""
const norm = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').replace(/[^a-z0-9 ]/g, ' ').replace(/\\s+/g, ' ').trim()
const re = /{patron}/
const casos = JSON.parse(process.argv[1])
process.stdout.write(JSON.stringify(casos.map((c) => re.test(norm(c)))))
""", json.dumps(["vacuno lechero", "Vacas de leche", "ovino", "cabras lecheras", "terneros", "Bos taurus", "dairy cows",
                 "porcino", "lechones", "broiler", "riñón (kidney) de cerdo", "conejos"])],
                          capture_output=True, text=True, timeout=60, check=False) if NODE else None
    if proc is None:
        pytest.skip("node no está disponible")
    assert json.loads(proc.stdout) == [True] * 7 + [False] * 5


@requires_node
def test_p8_mision1_no_puntua_las_oportunidades_bloqueadas(project, capsys):  # D4-3, D2-8
    paso0 = _regla_paso0(project, capsys)
    _registrar_titulos(project, capsys, "Oportunidad A", "Oportunidad B", "Oportunidad C")
    _bloquear(capsys, "O-0002", "seguridad", "BLOQUEADA-EN-EL-REGISTRO")
    gen = {"oportunidades": [_opp("Oportunidad A"), _opp("Oportunidad B"), _opp("Oportunidad C")]}
    reglas = [paso0, {"label": "M1b:gen:P1", "respuesta": gen}, *_reglas_novedad(),
              *_registro_secuencial(3),
              {"label": "triage:regulatoria:O-0001", "respuesta": {"compuerta": "regulatoria", "estado": "bloqueada",
                                                                   "motivo": "MEDICAMENTO-POR-FUNCION", "resumen": "r"}},
              {"labelPrefix": "triage:", "respuesta": {"compuerta": "x", "estado": "no_resuelta", "motivo": "m", "resumen": "r"}},
              {"label": "compuertas-registro:M1c:O-0001", "respuesta": _ver(capsys, "O-0001")},
              {"label": "compuertas-registro:M1c:O-0002", "respuesta": _ver(capsys, "O-0002")},
              {"label": "compuertas-registro:M1c:O-0003", "respuesta": _ver(capsys, "O-0003")}]
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": [_problema(1)], "n_oportunidades": 3}, reglas)
    ls = labels(out)
    assert not any(l.startswith("puntuacion:") and ("O-0001" in l or "O-0002" in l) for l in ls)
    assert [l for l in ls if l.startswith("puntuacion:")] == [f"puntuacion:evaluador-{n}:O-0003" for n in (1, 2, 3)]
    res = out["resultado"]
    bloqueadas = {b["id"]: b["bloqueos"] for b in res["bloqueadas"]}
    assert set(bloqueadas) == {"O-0001", "O-0002"}
    assert bloqueadas["O-0001"][0]["motivo"] == "MEDICAMENTO-POR-FUNCION"
    assert bloqueadas["O-0002"][0] == {"compuerta": "seguridad", "motivo": "BLOQUEADA-EN-EL-REGISTRO",
                                       "propuesta_por": "compuestos-toxicologia", "origen": "registro"}
    assert "O-0001 (regulatoria)" in call(out, "ranking")["prompt"] and "O-0002 (seguridad)" in call(out, "ranking")["prompt"]
    por_id = {o["id"]: o for o in res["oportunidades"]}
    assert por_id["O-0001"]["bloqueada"] and por_id["O-0001"]["evaluadores"] == []
    assert not por_id["O-0003"]["bloqueada"] and len(por_id["O-0003"]["evaluadores"]) == 3


@requires_node
def test_p8_mision1_sin_compuertas_legibles_no_puntua(project, capsys):  # D4-3
    reglas = [_regla_paso0(project, capsys), {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp("Oportunidad A")]}},
              *_reglas_novedad(), *_registro_secuencial(1),
              {"label": "compuertas-registro:M1c:O-0001", "respuesta": {"orden": "x", "codigo_salida": 1, "salida": ""}}]
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": [_problema(1)], "n_oportunidades": 1}, reglas)
    assert not any(l.startswith("puntuacion:") for l in labels(out))
    assert "no se pudieron leer las compuertas del registro" in out["resultado"]["oportunidades"][0]["sin_puntuar"]


@requires_node
def test_p8_mision1_novedad_por_el_rol_que_fija_la_skill(project, capsys):  # D2-18
    reglas = [_regla_paso0(project, capsys), {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp("A"), _opp("B")]}},
              {"label": "M1b:novedad-mercado:P1.2", "respuesta": None}, *_reglas_novedad(), *_registro_secuencial(2)]
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": [_problema(1)], "n_oportunidades": 2}, reglas)
    literatura, mercado = call(out, "M1b:novedad-literatura:P1.1"), call(out, "M1b:novedad-mercado:P1.1")
    assert literatura["agentType"] == "investigador-bibliografico" and mercado["agentType"] == "economia-mercado-pi"
    assert ("PATENTES" in mercado["prompt"] and "nutria --agente economia-mercado-pi patentes" in mercado["prompt"]
            and "PRODUCTOS" in mercado["prompt"])
    assert "patentes \"<consulta>\"" not in literatura["prompt"]
    # las búsquedas de novedad revelan la combinación: confidenciales (solo la huella en la procedencia)
    assert ("nutria --agente investigador-bibliografico buscar pubmed|europepmc|openalex \"<consulta>\" --sensibilidad confidencial"
            in literatura["prompt"])
    assert set(mercado["schema"]["required"]) >= {"productos", "patentes"}
    assert {"titulo": "B", "motivo": "novedad sin comprobar en productos y patentes (fallo de agente)"} in out["resultado"]["no_registradas"]
    skill = _norm(_skill("mision-1"))
    assert "`investigador-bibliografico` (literatura) y `economia-mercado-pi` (productos y patentes)" in skill
    oportunidad = _norm(_skill("oportunidad"))
    assert "`economia-mercado-pi` (rNPV, novedad frente a productos y patentes" in oportunidad
    evaluar = _text(WORKFLOWS_DIR / "evaluar-oportunidad-auto.js")
    assert "la novedad frente a productos y patentes es tuya" in evaluar


@requires_node
def test_p8_mision1_planes_critica_con_la_lista_y_etica_repropuesta(project, capsys):  # D1-12
    _registrar_oportunidades(project, capsys, 2)
    _bloquear(capsys, "O-0002", "regulatoria")
    plan = {"archivo": "research/planes/O-0001.md", "resumen": "r", "seleccion_firmada": False}
    critica = {"veredicto": "debilitada", "debilidades": ["d"], "experimento_decisivo": "e",
               "lista_validacion": [{"punto": n, "estado": {2: "omite", 4: "cubre_mal"}.get(n, "cubre"), "motivo": "m"} for n in range(1, 9)]}
    reglas = [_regla_paso0(project, capsys), {"label": "compuertas-registro:planes:O-0001", "respuesta": _ver(capsys, "O-0001")},
              {"label": "compuertas-registro:planes:O-0002", "respuesta": _ver(capsys, "O-0002")},
              {"label": "plan:O-0001", "respuesta": plan}, {"label": "revision-plan:O-0001", "respuesta": plan},
              {"label": "critica-plan:O-0001", "respuesta": critica}]
    # lo que hace el agente de ética: registra la propuesta con la CLI real y devuelve su salida literal
    registro_etica = _salida(capsys, "--agente", "sostenibilidad-bienestar", "--json", "puntuar", "compuerta", "O-0001", "etica",
                             "bloqueada", "--motivo", "DONANTES-SIN-AUTORIZACION")
    reglas.append({"label": "etica-plan:O-0001", "respuesta": {"compuerta": "etica", "estado": "bloqueada", "motivo": "DONANTES-SIN-AUTORIZACION",
                                                              "resumen": "r", "registro": registro_etica}})
    out = run_workflow("mision-1-auto", {"fase": "planes", "seleccion": ["O-0001", "O-0002"]}, reglas)
    ls = labels(out)
    assert "plan:O-0002" not in ls  # compuerta bloqueada en el registro: no se redacta el plan
    critica_plan = call(out, "critica-plan:O-0001")
    assert critica_plan["agentType"] == "critico-red-team"
    for fragmento in FRAGMENTOS_VALIDACION:
        assert fragmento in _norm(critica_plan["prompt"]), fragmento
    assert critica_plan["schema"]["properties"]["lista_validacion"]["items"]["properties"]["estado"]["enum"] == ["cubre", "cubre_mal", "omite"]
    assert ls.index("revision-plan:O-0001") < ls.index("etica-plan:O-0001")
    etica = call(out, "etica-plan:O-0001")
    assert etica["agentType"] == "sostenibilidad-bienestar"
    assert "nutria --agente sostenibilidad-bienestar --json puntuar compuerta O-0001 etica" in etica["prompt"]
    assert "research/revisiones/O-0001_etica_plan_2026-11-04.md" in etica["prompt"]
    assert "registro" in etica["schema"]["required"]  # la salida literal de la orden, no solo el estado declarado
    res = {p["id"]: p for p in out["resultado"]["planes"]}
    assert res["O-0001"]["lista_validacion"] == {"omitidos": [2], "mal_cubiertos": [4], "sin_respuesta": [9]}
    assert res["O-0001"]["etica"] == {"estado": "bloqueada", "motivo": "DONANTES-SIN-AUTORIZACION", "registrada": True,
                                      "rechazo": None} and res["O-0001"]["bloqueada"]
    assert res["O-0002"]["plan"] is None and res["O-0002"]["bloqueada"]
    assert any("Compuerta ética repropuesta" in m for m in out["logs"])
    assert len(ls) <= _maximo_logueado(out, r"Estimación: (\d+) agentes")


@requires_node
def test_p8_mision1_etica_repropuesta_rechazada_por_la_cli(project, capsys):  # D1-12 (con la CLI del rol propietario, P1)
    _registrar_oportunidades(project, capsys, 1)
    plan = {"archivo": "research/planes/O-0001.md", "resumen": "r"}
    # una persona ya firmó la ética: la CLI rechaza la propuesta sin firma (código distinto de 0, mensaje por stderr)
    rechazada = {"orden": "nutria --agente sostenibilidad-bienestar --json puntuar compuerta O-0001 etica superada --motivo m",
                 "codigo_salida": 3, "salida": ""}
    reglas = [_regla_paso0(project, capsys), {"label": "compuertas-registro:planes:O-0001", "respuesta": _ver(capsys, "O-0001")},
              {"label": "plan:O-0001", "respuesta": plan}, {"label": "revision-plan:O-0001", "respuesta": plan},
              {"label": "etica-plan:O-0001", "respuesta": {"compuerta": "etica", "estado": "superada", "motivo": "m", "resumen": "r",
                                                            "registro": rechazada}}]
    out = run_workflow("mision-1-auto", {"fase": "planes", "seleccion": ["O-0001"]}, reglas)
    etica = out["resultado"]["planes"][0]["etica"]
    assert etica["estado"] == "superada" and etica["registrada"] is False and "código 3" in etica["rechazo"]
    assert any("Compuerta ética repropuesta de O-0001 sin registrar" in m for m in out["logs"])
    # sin la salida de la orden tampoco consta como registrada
    sin_salida = run_workflow("mision-1-auto", {"fase": "planes", "seleccion": ["O-0001"]}, reglas[:-1])
    assert sin_salida["resultado"]["planes"][0]["etica"]["registrada"] is False


@requires_node
def test_p8_mision1_estimacion_de_las_fases_mapa_y_planes(project, capsys):  # D10-3
    mapa = run_workflow("mision-1-auto", {"fase": "mapa"}, [_regla_paso0(project, capsys)])
    assert len(labels(mapa)) == _maximo_logueado(mapa, r"Estimación: (\d+) agentes")
    _registrar_oportunidades(project, capsys, 3)
    reglas = [_regla_paso0(project, capsys)] + [{"label": f"compuertas-registro:planes:O-000{i}", "respuesta": _ver(capsys, f"O-000{i}")}
                                                for i in (1, 2, 3)]
    planes = run_workflow("mision-1-auto", {"fase": "planes", "seleccion": ["O-0001", "O-0002", "O-0003"]}, reglas)
    assert len(labels(planes)) == _maximo_logueado(planes, r"Estimación: (\d+) agentes")


@requires_node
def test_p8_readme_cifras_de_agentes_coinciden_con_los_workflows():  # D10-3, indicación (10): README coherente
    fila = {f.split("|")[1].strip().strip("`"): f for f in _text(ROOT / "README.md").splitlines()
            if f.startswith("| `") and "Del orden de" in f}
    # evaluar-oportunidad-auto: máximo por defecto y agentes fijos, según su propio registro
    ev = run_workflow("evaluar-oportunidad-auto", "O-0003")
    fijos = int(re.search(r"si no se detiene antes, (\d+) fijos", " ".join(ev["logs"])).group(1))
    assert f"como máximo {_maximo_logueado(ev)} por defecto" in fila["evaluar-oportunidad-auto"]
    assert f"{fijos} fijos si no se detiene antes" in fila["evaluar-oportunidad-auto"]
    assert "3 registros de puntuación solo con sello válido" in fila["evaluar-oportunidad-auto"]
    # red-team-auto: por defecto y con el tope de 30 afirmaciones
    reglas = [{"label": "extraccion", "respuesta": {"afirmaciones": [{"id": f"A{i}", "texto": "t"} for i in range(1, 31)]}}]
    rt15 = _maximo_logueado(run_workflow("red-team-auto", "research/informes/O-0003.md", reglas))
    rt30 = _maximo_logueado(run_workflow("red-team-auto", {"archivo": "research/informes/O-0003.md", "max_afirmaciones": 30}, reglas))
    assert f"como máximo {rt15} por defecto y {rt30} con 30 afirmaciones" in fila["red-team-auto"]
    # mision-1-auto: mapa, planes y máximo de la fase oportunidades con 8 problemas y 12 oportunidades
    mapa = _maximo_logueado(run_workflow("mision-1-auto", {"fase": "mapa"}), r"Estimación: (\d+) agentes")
    assert f"**{mapa}** en la fase `mapa`" in fila["mision-1-auto"]
    planes = [_maximo_logueado(run_workflow("mision-1-auto", {"fase": "planes", "seleccion": [f"O-000{i}" for i in range(1, n + 1)]}),
                               r"Estimación: (\d+) agentes") for n in (1, 2)]
    assert f"**1 + {planes[1] - planes[0]} por oportunidad elegida**" in fila["mision-1-auto"] and planes[0] == 1 + planes[1] - planes[0]
    ops = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": [f"P{i}" for i in range(1, 9)], "n_oportunidades": 12})
    assert f"{_maximo_logueado(ops)} con 8 problemas y 12 oportunidades" in fila["mision-1-auto"]


# ------------------------------------------------------------------ compuertas: rol propietario y --agente
def test_p8_compuertas_propuestas_por_su_rol_propietario():  # D5-10, P1 (rol propietario)
    patron = re.compile(r"nutria (?:--agente (\S+) )?(?:--json )?puntuar compuerta (\S+) (\S+)")
    revisadas = 0
    for path in [*WORKFLOWS, *sorted(SKILLS_DIR.glob("*/SKILL.md")), *sorted(AGENTS_DIR.glob("*.md"))]:
        for linea in _text(path).splitlines():
            for m in patron.finditer(linea):
                agente, compuerta = m.group(1), m.group(3)
                if agente is None:  # solo la vía humana (firma en su terminal) va sin --agente
                    assert "--firma-humana NOMBRE" in linea and "terminal" in linea, f"{path.name}: {linea.strip()}"
                    continue
                revisadas += 1
                if compuerta in PROPIETARIO and not agente.startswith(("$", "<")):
                    assert agente == PROPIETARIO[compuerta], f"{path.name}: {compuerta} propuesta por {agente}"
    assert revisadas >= 10


# ------------------------------------------------------------------ contenido fisiológico y regulatorio
def test_p8_adaptacion_de_la_microbiota_ruminal():  # D4-4
    for texto in (_norm(_agent("fisiologia-bioquimica")[1]), _norm(_agent("critico-red-team")[1]), _norm(_skill("hipotesis"))):
        for fragmento in ("se adapta", "días o semanas `[SUP]`", "incubaciones cortas en lote (24-48 h `[SUP]`)", "RUSITEC", "in vivo",
                          "`[HIP]`"):
            assert fragmento in texto, fragmento
    fisio = _norm(_agent("fisiologia-bioquimica")[1])
    assert "No extrapoles" in fisio and "indica la duración de cada estudio" in fisio
    # el crítico del plan y los prompts de los workflows también lo comprueban
    assert "incubaciones cortas en lote" in _text(WORKFLOWS_DIR / "mision-1-auto.js")
    assert "adaptación de la microbiota ruminal" in _text(WORKFLOWS_DIR / "evaluar-oportunidad-auto.js")


def test_p8_antibioticos_y_moduladores_ruminales_antimicrobianos():  # D5-4
    textos = {"skill": _norm(_skill("triage-regulatorio")), "agente": _norm(_agent("regulacion-seguridad")[1]),
              "regulacion": _norm(_text(ROOT / "docs" / "REGULACION_UE.md"))}
    for nombre, texto in textos.items():
        for fragmento in ("art. 11, apartado 2", "CELEX 32003R1831", "coccidiostáticos e histomonóstatos",
                          "moduladores ruminales antimicrobianos", "ionóforos", "pendiente de verificación",
                          "no hay vía como pienso ni como aditivo"):
            assert fragmento in texto, (nombre, fragmento)
    assert "verificar artículo" not in textos["skill"] + textos["agente"]
    assert "ionóforos" in _norm(_agent("compuestos-toxicologia")[1])
    # precisión: la excepción de los coccidiostáticos (categoría propia de aditivos) no ampara el uso como modulador ruminal
    for nombre, texto in {**textos, "toxicologia": _norm(_agent("compuestos-toxicologia")[1])}.items():
        assert "coccidiostático o histomonóstato" in texto and "no ampara el uso como modulador ruminal" in texto, nombre


def test_p8_lista_de_validacion_igual_en_director_critico_y_workflow():  # D1-12: la misma lista de 9 puntos
    def seccion(texto: str, titulo: str) -> str:
        return texto.split(titulo, 1)[1].split("\n## ", 1)[0]
    secciones = {"director": seccion(_agent("director-cientifico")[1], "## Lista de validación"),
                 "critico": seccion(_agent("critico-red-team")[1], "## Planes: lista de validación")}
    js = _text(WORKFLOWS_DIR / "mision-1-auto.js")
    lista = js.split("const LISTA_VALIDACION = [", 1)[1].split("]\n", 1)[0]
    assert len(re.findall(r"^  '", lista, re.M)) == 9
    for nombre, cuerpo in secciones.items():
        assert [m.group(1) for m in re.finditer(r"^(\d+)\. ", cuerpo, re.M)] == [str(n) for n in range(1, 10)], nombre
        # mismo orden: in situ (3), FEEDAP (4), BPL (5), notificación a EFSA (8), duración de los ensayos (9)
        puntos = re.split(r"^\d+\. ", cuerpo, flags=re.M)
        assert "in situ" in puntos[3] and "FEEDAP" in puntos[4] and "BPL" in puntos[5] and "32b" in puntos[8], nombre
        assert "Duración de los ensayos de fermentación ruminal" in _norm(puntos[9]).replace("**", ""), nombre


def test_p8_omg_microorganismos_y_qps():  # D5-13
    textos = {"skill": _norm(_skill("triage-regulatorio")), "regulacion": _norm(_text(ROOT / "docs" / "REGULACION_UE.md"))}
    for nombre, texto in textos.items():
        for fragmento in ("32003R1829", "considerando 16", '"con" un OMG', '"a partir de"', "células viables",
                          "ADN recombinante", "QPS", "genes de resistencia", "toxigenicidad", "pendiente de verificación"):
            assert fragmento in texto, (nombre, fragmento)
    assert "Productos de fermentación con cepas modificadas | pendiente |" not in _text(ROOT / "docs" / "REGULACION_UE.md")


# ------------------------------------------------------------------ P8: independencia en la CLI y en los prompts
def test_p8_evaluador_no_ve_las_decisiones_propuestas(project, capsys):  # D1-3: la conclusión esperada
    _registrar_oportunidades(project, capsys, 1)
    code, _ = _cli(capsys, "--agente", "director-cientifico", "decision", "registrar", "--entidad", "O-0001",
                   "--decision", "propuesta: descartar", "--motivo", "CONCLUSION-ESPERADA")
    assert code == 0
    for argv in (("--agente", "evaluador-2"), ("--agente", "Evaluador-1")):
        code, out = _cli(capsys, *argv, "--json", "oportunidad", "ver", "O-0001", "--sin-puntuaciones")
        assert code == 0
        vista = json.loads(out)
        assert "decisiones" not in vista and "puntuaciones" not in vista
        assert "CONCLUSION-ESPERADA" not in out
        assert set(vista["compuertas_efectivas"]) == set(PROPIETARIO)  # las compuertas sí son evidencia
    # tampoco por `decision listar` (con o sin --entidad): la orden se rechaza para un evaluador
    for extra in (("--entidad", "O-0001"), ()):
        code, out = _cli(capsys, "--agente", "evaluador-3", "--json", "decision", "listar", *extra)
        assert code != 0 and "CONCLUSION-ESPERADA" not in out
    # el resto de roles (director, orquestador) sigue viendo las decisiones
    code, out = _cli(capsys, "--agente", "director-cientifico", "--json", "oportunidad", "ver", "O-0001", "--sin-puntuaciones")
    assert code == 0 and "CONCLUSION-ESPERADA" in out
    code, out = _cli(capsys, "--agente", "orquestador", "--json", "decision", "listar", "--entidad", "O-0001")
    assert code == 0 and "CONCLUSION-ESPERADA" in out
    # y los prompts de los evaluadores y su agente lo prohíben de forma explícita
    for texto in (_text(WORKFLOWS_DIR / "evaluar-oportunidad-auto.js"), _text(WORKFLOWS_DIR / "mision-1-auto.js"),
                  _agent("evaluador-independiente")[1]):
        assert "nutria decision listar" in _sin_agente(texto)


@requires_node
def test_p8_evaluar_pregunta_obligatoria_aunque_el_plan_traiga_la_suya(project, capsys):  # D2-18, D5-10
    plan = {"peco": "PECO", "variables": ["leche_kg_d"], "preguntas": [
        {"agente": "economia-mercado-pi", "pregunta": "PREGUNTA-DEL-PLAN", "entregable": "ENTREGABLE-DEL-PLAN"},
        {"agente": "sostenibilidad-bienestar", "pregunta": "huella", "entregable": "e"}]}
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", [_regla_registro(project, capsys, "O-0003"),
                                                               {"label": "plan:O-0003", "respuesta": plan}])
    especialistas = [c for c in out["llamadas"] if c["phase"] == "Especialistas"]
    assert [c["agentType"] for c in especialistas] == ["desarrollo-industrial", "economia-mercado-pi", "sostenibilidad-bienestar"]
    economia = _norm(especialistas[1]["prompt"])
    assert "PREGUNTA-DEL-PLAN" in economia and "ENTREGABLE-DEL-PLAN" in economia
    assert "la novedad frente a productos y patentes es tuya" in economia
    assert "nutria --agente economia-mercado-pi puntuar compuerta O-0003 propiedad_intelectual" in economia
    assert "nutria --agente sostenibilidad-bienestar puntuar compuerta O-0003 etica" in _norm(especialistas[2]["prompt"])


@requires_node
def test_p8_red_team_tope_menor_que_cinco_no_pide_imposibles():  # D10-3
    out = run_workflow("red-team-auto", {"archivo": "research/informes/O-0003.md", "max_afirmaciones": 3})
    assert "entre 3 y 3" in call(out, "extraccion")["prompt"]
    assert call(out, "extraccion")["schema"]["properties"]["afirmaciones"]["maxItems"] == 3


def test_p8_version_de_claude_code_documentada():  # D2-17
    for nombre in ("mision-1", "oportunidad"):
        texto = _norm(_skill(nombre))
        assert "2.1.318" in texto and "herramienta Workflow" in texto, nombre
    assert "2.1.42" in _norm(_skill("mision-1"))


# ------------------------------------------------------------------ P8: nada se sobrescribe entre ejecuciones
@requires_node
@pytest.mark.parametrize("declarada,esperada", [
    ("research/revisiones/informes_O-0001_redteam-2.md", "research/revisiones/informes_O-0001_redteam-2.md"),
    ("research/revisiones/otro_fichero.md", "research/revisiones/informes_O-0001_redteam.md"),  # ruta ajena: no se acepta
    (None, "research/revisiones/informes_O-0001_redteam.md"),
])
def test_p8_red_team_no_sobrescribe_una_revision_anterior(declarada, esperada):  # D2-16
    reglas = [{"label": "consolidacion", "respuesta": None if declarada is None else {"archivo": declarada}}]
    out = run_workflow("red-team-auto", "research/informes/O-0001.md", reglas)
    prompt = call(out, "consolidacion")["prompt"]
    assert "no la sobrescribas" in prompt and "research/revisiones/informes_O-0001_redteam-2.md" in prompt
    assert out["resultado"]["informe"] == esperada


@requires_node
def test_p8_mision1_planes_conserva_la_version_anterior(project, capsys):  # D2-15
    _registrar_oportunidades(project, capsys, 2)
    (project / "research" / "planes").mkdir(exist_ok=True)
    (project / "research" / "planes" / "O-0001.md").write_text("# plan anterior\n", "utf-8")
    (project / "research" / "planes" / "O-0001_anterior_2026-11-04.md").write_text("# otro\n", "utf-8")
    reglas = [_regla_paso0(project, capsys), *[{"label": f"compuertas-registro:planes:{i}", "respuesta": _ver(capsys, i)}
                                                for i in ("O-0001", "O-0002")]]
    out = run_workflow("mision-1-auto", {"fase": "planes", "seleccion": ["O-0001", "O-0002"]}, reglas)
    plan1 = _norm(call(out, "plan:O-0001")["prompt"])
    assert "Si research/planes/O-0001.md ya existe, no se pierde" in plan1
    assert "research/planes/O-0001_anterior_2026-11-04-2.md" in plan1  # el nombre libre según el paso 0
    assert "ya existe, no se pierde" not in _norm(call(out, "plan:O-0002")["prompt"])  # el paso 0 dice que no existe
    assert "research/revisiones/O-0001_etica_plan_2026-11-04.md" in call(out, "etica-plan:O-0001")["prompt"]


@requires_node
def test_p8_mision1_ficheros_por_directorio_y_sin_cabeceras(project, capsys):  # D2-15
    # el mismo nombre en otro directorio no ocupa el de research/oportunidades
    (project / "research" / "revisiones" / "M1_2026-11-04_oportunidad-a.json").write_text("{}", "utf-8")
    reglas = [_regla_paso0(project, capsys), {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp("Oportunidad A")]}},
              *_reglas_novedad(), *_registro_secuencial(1)]
    args = {"fase": "oportunidades", "problemas": [_problema(1)], "n_oportunidades": 1}
    out = run_workflow("mision-1-auto", args, reglas)
    assert "research/oportunidades/M1_2026-11-04_oportunidad-a.json " in call(out, "registro:1")["prompt"]
    # sin cabeceras reconocibles, cada nombre cuenta como ocupado en todos los directorios (falla en cerrado)
    sin_cabeceras = _regla_paso0(project, capsys, ficheros="M1_2026-11-04_oportunidad-a.json\n")
    out = run_workflow("mision-1-auto", args, [sin_cabeceras, *reglas[1:]])
    assert "research/oportunidades/M1_2026-11-04_oportunidad-a-2.json" in call(out, "registro:1")["prompt"]


@requires_node
def test_p8_evaluar_la_evidencia_de_los_evaluadores_no_admite_rutas_ajenas(project, capsys):  # D1-3, D2-2
    reglas = [_regla_registro(project, capsys, "O-0003"), {"label": "plan:O-0003", "respuesta": _plan_evaluar(1)},
              {"label": "descarte:seguridad", "respuesta": {"analisis": "seguridad", "estado": "no_resuelta", "motivo": "m", "resumen": "r",
                                                            "archivo": "research/informes/O-0003.md"}},
              {"labelPrefix": "desarrollo-industrial:", "respuesta": {"archivo": "research/revisiones/informes_O-0003_redteam.md",
                                                                       "resumen": "r", "afirmaciones": []}},
              {"labelPrefix": "economia-mercado-pi:", "respuesta": {"archivo": "research/datos/O-0003/../../planes/O-0003.md",
                                                                     "resumen": "r", "afirmaciones": []}},
              {"labelPrefix": "sostenibilidad-bienestar:", "respuesta": {"archivo": "research/revisiones/O-0003_huella.md",
                                                                          "resumen": "r", "afirmaciones": []}},
              {"label": "bibliografia:O-0003", "respuesta": {"archivo": "research/oportunidades/O-0003_puntuacion_evaluador-1.json", "estudios": []}}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)
    for n in (1, 2, 3):
        prompt = call(out, f"puntuacion:evaluador-{n}")["prompt"]
        for ajena in ("research/informes/O-0003.md", "redteam.md", "planes/O-0003.md", "_puntuacion_evaluador-1.json"):
            assert ajena not in prompt.split("No leas nada más")[0], ajena
        for esperada in ("research/revisiones/O-0003_descarte_seguridad.md", "research/revisiones/O-0003_01_desarrollo-industrial.md",
                         "research/revisiones/O-0003_02_economia-mercado-pi.md", "research/revisiones/O-0003_huella.md",
                         "research/revisiones/O-0003_busqueda.md"):
            assert esperada in prompt, esperada
    assert call(out, "critica:desarrollo-industrial:1")["prompt"].count("research/revisiones/O-0003_01_desarrollo-industrial.md") == 1
    rechazadas = [m.split(" declaró")[0] for m in out["logs"] if "fuera de las rutas de evidencia" in m]
    assert {"descarte:seguridad", "desarrollo-industrial:1", "economia-mercado-pi:2", "bibliografia:O-0003"} <= set(rechazadas)
    assert "sostenibilidad-bienestar:3" not in rechazadas  # su ruta es válida aunque no sea la esperada


@requires_node
def test_p8_mision1_la_evidencia_de_los_evaluadores_no_admite_rutas_ajenas(project, capsys):  # D1-3, D2-2
    reglas = [_regla_paso0(project, capsys), {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp("Oportunidad A")]}},
              *_reglas_novedad(), *_registro_secuencial(1),
              {"label": "triage:seguridad:O-0001", "respuesta": {"compuerta": "seguridad", "estado": "no_resuelta", "motivo": "m",
                                                                 "resumen": "r", "archivo": "research/revisiones/M1_ranking_2026-11-04.md"}},
              {"label": "triage:etica:O-0001", "respuesta": {"compuerta": "etica", "estado": "no_resuelta", "motivo": "m",
                                                             "resumen": "r", "archivo": "research/revisiones/O-0001_etica_detalle.md"}}]
    _registrar_titulos(project, capsys, "Oportunidad A")
    reglas.append({"labelPrefix": "compuertas-registro:", "respuesta": _ver(capsys, "O-0001")})
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": [_problema(1)], "n_oportunidades": 1}, reglas)
    prompt = call(out, "puntuacion:evaluador-1:O-0001")["prompt"]
    assert "M1_ranking" not in prompt
    assert "research/revisiones/O-0001_triage_seguridad.md" in prompt and "research/revisiones/O-0001_etica_detalle.md" in prompt


@requires_node
@pytest.mark.parametrize("args,mensaje", [
    ({"fase": "mapa", "fecha": "2026-13-45"}, '"fecha" debe ser una fecha válida'),
    ({"fase": "mapa", "fecha": "2026-02-29"}, '"fecha" debe ser una fecha válida'),  # 2026 no es bisiesto
    ({"fase": "oportunidades", "problemas": ["A", {"x": 1}, ""]}, "posiciones sin título (ni texto ni objeto con \"titulo\"): 2, 3"),
    ({"fase": "oportunidades", "problemas": [{"id": "P1", "titulo": "A"}, {"id": "P1", "titulo": "B"}]}, "ids repetidos: P1"),
])
def test_p8_mision1_entradas_no_validas_no_se_descartan_en_silencio(args, mensaje):  # D2-15, D10-3
    out = run_workflow("mision-1-auto", args)
    assert out["llamadas"] == [] and mensaje in out["resultado"]["error"]


@requires_node
def test_p8_mision1_fecha_bisiesta_valida(project, capsys):  # D2-15
    out = run_workflow("mision-1-auto", {"fase": "mapa", "fecha": "2028-02-29"}, [_regla_paso0(project, capsys)])
    assert out["resultado"]["fecha"] == "2028-02-29"


@requires_node
def test_p8_mision1_repetir_la_fase_no_duplica_ni_sobrescribe(project, capsys):  # D2-15 (reproducción del hallazgo)
    args = {"fase": "oportunidades", "problemas": [_problema(1)], "n_oportunidades": 2}
    gen = {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp("Oportunidad A")]}}
    primera = run_workflow("mision-1-auto", args, [_regla_paso0(project, capsys), gen, {"labelPrefix": "M1b:gen:P1:r", "respuesta": {"oportunidades": []}},
                                                   *_reglas_novedad(), *_registro_secuencial(1)])
    ruta_a = "research/oportunidades/M1_2026-11-04_oportunidad-a.json"
    assert ruta_a in call(primera, "registro:1")["prompt"]
    # lo que hizo el agente de registro en la primera ejecución: el JSON en disco y el alta en el registro
    datos = {**_ejemplo("oportunidad_ejemplo"), "titulo": "Oportunidad A"}
    (project / ruta_a).write_text(json.dumps(datos, ensure_ascii=False), "utf-8")
    code, out = _cli(capsys, "--agente", "fisiologia-bioquimica", "oportunidad", "nueva", "--datos", ruta_a)
    assert code == 0, out
    # segunda ejecución con el mismo generador y una oportunidad más
    gen2 = {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp("Oportunidad A"), _opp("Oportunidad B")]}}
    sin_mas = {"labelPrefix": "M1b:gen:P1:r", "respuesta": {"oportunidades": []}}  # las rondas extra no aportan nada
    segunda = run_workflow("mision-1-auto", args, [_regla_paso0(project, capsys), gen2, sin_mas, *_reglas_novedad(),
                                                   {"label": "registro:1", "respuesta": {"id": "O-0002"}}])
    ls = labels(segunda)
    assert [l for l in ls if l.startswith("registro:")] == ["registro:1"]
    assert "Oportunidad B" in call(segunda, "registro:1")["prompt"] and ruta_a not in call(segunda, "registro:1")["prompt"]
    assert "M1b:novedad-literatura:P1.1" not in ls  # la ya registrada no se vuelve a comprobar
    assert {"titulo": "Oportunidad A", "motivo": "ya registrada (O-0001)"} in segunda["resultado"]["no_registradas"]
    code, out = _cli(capsys, "--json", "oportunidad", "listar")
    assert [o["title"] for o in json.loads(out)].count("Oportunidad A") == 1


@requires_node
def test_p8_mision1_propuesta_top3_atribuida_y_por_oportunidad(project, capsys):  # trazabilidad (regla 10)
    reglas = [_regla_paso0(project, capsys), {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp("Oportunidad A")]}},
              *_reglas_novedad(), *_registro_secuencial(1)]
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": [_problema(1)], "n_oportunidades": 1}, reglas)
    ranking = _norm(call(out, "ranking")["prompt"])
    assert 'nutria --agente director-cientifico decision registrar --entidad <ID> --decision "propuesta top-3 M1"' in ranking
    assert "sin --firma-humana" in ranking


@requires_node
def test_p8_mision1_firma_de_la_seleccion_segun_el_registro(project, capsys):  # D3-3 (no fiarse de lo que declara un agente)
    _registrar_oportunidades(project, capsys, 2)
    firmada = _ver(capsys, "O-0001")
    vista = json.loads(firmada["salida"])
    vista["decisiones"] = [{"entity_id": "O-0001", "decision": "seleccionada M1", "rationale": "m", "decided_by": "persona",
                            "human_signoff": "Ana"},
                           {"entity_id": "O-0001", "decision": "seleccionada M1", "rationale": "propuesta", "decided_by": "x",
                            "human_signoff": None}]
    reglas = [_regla_paso0(project, capsys), {"label": "compuertas-registro:planes:O-0001", "respuesta": {**firmada, "salida": json.dumps(vista)}},
              {"label": "compuertas-registro:planes:O-0002", "respuesta": _ver(capsys, "O-0002")},
              # el agente del plan declara lo contrario de lo que dice el registro: no se le cree
              {"labelPrefix": "plan:", "respuesta": {"archivo": "research/planes/x.md", "resumen": "r", "seleccion_firmada": True}}]
    out = run_workflow("mision-1-auto", {"fase": "planes", "seleccion": ["O-0001", "O-0002"]}, reglas)
    assert "Según el registro, la selección de O-0001 tiene firma humana" in call(out, "plan:O-0001")["prompt"]
    assert "Según el registro, la selección de O-0002 NO tiene firma humana: el plan es provisional" in call(out, "plan:O-0002")["prompt"]
    res = {p["id"]: p for p in out["resultado"]["planes"]}
    assert res["O-0001"]["seleccion_firmada"] is True and res["O-0002"]["seleccion_firmada"] is False
    assert any("Selección sin firma humana en el registro: los planes de O-0002 son provisionales" in m for m in out["logs"])


def _decision(n: int, texto: str, firma: str | None) -> dict:
    return {"id": n, "ts": f"2026-11-0{n}T10:00:00+00:00", "entity_id": "O-0001", "decision": texto, "rationale": "m",
            "decided_by": "persona" if firma else "director-cientifico", "human_signoff": firma}


@requires_node
@pytest.mark.parametrize("decisiones,firmada", [
    ([_decision(1, "seleccionada M1", None)], False),  # una propuesta sin firma no es la selección
    ([_decision(1, "propuesta top-3 M1", None), _decision(2, "seleccionada M1", "Ana")], True),
    ([_decision(1, "no seleccionada M1 (se descarta)", "Ana")], False),
    ([_decision(1, "deseleccionada", "Ana")], False),
    ([_decision(1, "seleccionada M1", "Ana"), _decision(2, "selección retirada", "Ana")], False),  # la última manda
    # fase de coordinación: solo la frase canónica confirma; otra decisión firmada sobre la selección la revoca
    ([_decision(1, "seleccionada M1", "Ana"), _decision(2, "selección retirada", "Ana"), _decision(3, "seleccionada de nuevo", "Luis")], False),
    ([_decision(1, "seleccionada M1", "Ana"), _decision(2, "selección retirada", "Ana"), _decision(3, "Seleccionada M1.", "Luis")], True),
    ([_decision(2, "selección retirada", "Ana"), _decision(1, "seleccionada M1", "Ana")], False),  # por orden de registro (id)
    ([_decision(1, "seleccionada M1", "Ana"), _decision(2, "no seleccionada (propuesta)", None)], True),  # sin firma no anula
    ([_decision(1, "seleccionada M1", "Ana"), _decision(2, "Retirada de la selección", "Ana")], False),  # negación en cualquier parte
    ([_decision(1, "seleccionada M1", "Ana"), _decision(2, "revisada la dosis tras la selección", "Ana")], False),  # menciona la selección: revoca
    ([_decision(1, "seleccionada M1", "Ana"), _decision(2, "revisada la dosis", "Ana")], True),  # no es sobre la selección
    ([_decision(1, "propuesta: seleccionar O-0001 para planes", "Ana")], False),  # ambigua: no empieza por "seleccionada"
], ids=["sin-firma", "firmada", "negada", "deseleccionada", "retirada", "reseleccionada-sin-frase", "reseleccionada", "orden-por-id",
        "retirada-sin-firma", "retirada-de-la-seleccion", "menciona-la-seleccion", "otra-decision", "ambigua"])
def test_p8_mision1_firma_de_la_seleccion_orden_y_negacion(decisiones, firmada, project, capsys):  # D3-3 (selección firmada)
    _registrar_oportunidades(project, capsys, 1)
    salida = _ver(capsys, "O-0001")
    vista = json.loads(salida["salida"])
    vista["decisiones"] = decisiones
    reglas = [_regla_paso0(project, capsys), {"label": "compuertas-registro:planes:O-0001", "respuesta": {**salida, "salida": json.dumps(vista)}}]
    out = run_workflow("mision-1-auto", {"fase": "planes", "seleccion": ["O-0001"]}, reglas)
    assert out["resultado"]["planes"][0]["seleccion_firmada"] is firmada
    prompt = call(out, "plan:O-0001")["prompt"]
    assert ("la selección de O-0001 tiene firma humana" in prompt) is firmada
    assert ("la selección de O-0001 NO tiene firma humana: el plan es provisional" in prompt) is not firmada


@requires_node
def test_p8_mision1_id_de_otra_oportunidad_no_se_puntua(project, capsys):  # D2-15, D1-7: integridad del alta
    paso0 = _regla_paso0(project, capsys)
    _registrar_titulos(project, capsys, "Otra oportunidad ya registrada")  # O-0001
    reglas = [paso0, {"label": "M1b:gen:P1", "respuesta": {"oportunidades": [_opp("Oportunidad A")]}}, *_reglas_novedad(),
              {"label": "registro:1", "respuesta": {"id": "O-0001"}},  # el agente devuelve un ID ajeno
              {"labelPrefix": "compuertas-registro:", "respuesta": _ver(capsys, "O-0001")}]
    out = run_workflow("mision-1-auto", {"fase": "oportunidades", "problemas": [_problema(1)], "n_oportunidades": 1}, reglas)
    assert not any(l.startswith("puntuacion:") for l in labels(out))
    triage = call(out, "triage:regulatoria:O-0001")["prompt"]
    assert 'comprueba que el título registrado de O-0001 es "Oportunidad A"' in triage
    sin_puntuar = out["resultado"]["oportunidades"][0]["sin_puntuar"]
    assert "no corresponde a la oportunidad generada" in sin_puntuar and "Otra oportunidad ya registrada" in sin_puntuar



# ============================================================================ fase de coordinación: doble extracción
# Petición de P7 a P8 (nota 11 del director): la doble extracción del workflow usa las órdenes del registro (depositar,
# comparar --registrar y conciliar) con identidades por extractor, oportunidad y ejecución, y el workflow decide con
# las salidas literales de nutria (no con el booleano que declare el agente de conciliación).
def _reglas_doble_extraccion(project, capsys, opp_id: str, fuentes, con_discrepancias=(), sin_copia_b=(),
                             ejecucion: int = 1) -> list[dict]:
    """Reglas del arnés: cada extractor escribe su copia (del ejemplo de ejemplos/) y la deposita con la CLI real; el
    tercer agente compara con --registrar y, si hay discrepancias, concilia. Las respuestas son salidas literales."""
    import copy

    from nutria import db
    from nutria.config import load_config
    conn = db.connect(load_config().db_path)
    try:
        for fuente in fuentes:
            db.upsert_source(conn, {"doi": fuente.split(":", 1)[1], "title": f"Ensayo {fuente}", "year": 2020,
                                    "verified": True, "retracted": False, "verified_via": "prueba"})
    finally:
        conn.close()
    base = json.loads((EJEMPLOS / "extraccion_ejemplo.json").read_text("utf-8"))

    def slug(f):
        return re.sub(r"[^A-Za-z0-9._-]+", "_", f).strip("_")

    def ident(c):
        return f"extractor-datos-{c}-{opp_id}-r{ejecucion}"
    reglas = []
    for fuente in fuentes:
        recs = [dict(r, fuente=fuente, notas="registro de prueba") for r in base]
        rel = {c: f"research/datos/{opp_id}/extraccion_{slug(fuente)}_{c}.json" for c in "AB"}
        (project / rel["A"]).parent.mkdir(parents=True, exist_ok=True)
        (project / rel["A"]).write_text(json.dumps(recs, ensure_ascii=False), "utf-8")
        recs_b = copy.deepcopy(recs)
        if fuente in con_discrepancias:
            recs_b[0]["media_tratamiento"] = 30.0
        (project / rel["B"]).write_text(json.dumps(recs_b, ensure_ascii=False, indent=1), "utf-8")
        for c in "AB":
            if c == "B" and fuente in sin_copia_b:
                continue
            dep = _salida(capsys, "--agente", ident(c), "--json", "extraccion", "depositar", "--datos", rel[c])
            assert dep["codigo_salida"] == 0, dep
            reglas.append({"label": f"extraccion:{c}:{fuente}",
                           "respuesta": {"archivo": rel[c], "registros": len(recs), "deposito": dep}})
        if fuente in sin_copia_b:
            continue
        cmp = _salida(capsys, "--agente", ident("C"), "--json", "extraccion", "comparar", rel["A"], rel["B"], "--registrar")
        respuesta = {"comparar": cmp}
        if cmp["codigo_salida"] == 1:
            ce = json.loads(cmp["salida"])["id"]
            conc = {"conciliacion": [{"a": 1, "b": 1, "resoluciones": [{"campo": "media_tratamiento", "origen_valor": "A",
                                                                        "motivo": "Tabla 3, fila 2 (prueba)"}]},
                                     {"a": 2, "b": 2, "resoluciones": []}], "no_resueltos": []}
            ruta_c = f"research/datos/{opp_id}/conciliacion_{slug(fuente)}.json"
            (project / ruta_c).write_text(json.dumps(conc, ensure_ascii=False), "utf-8")
            respuesta["conciliar"] = _salida(capsys, "--agente", ident("C"), "--json", "extraccion", "conciliar", ce,
                                             "--datos", ruta_c)
            assert respuesta["conciliar"]["codigo_salida"] == 0, respuesta["conciliar"]
        reglas.append({"label": f"conciliacion:{fuente}", "respuesta": respuesta})
    return reglas


def _estudios(n: int) -> list[dict]:
    return [{"fuente": f"doi:10.1000/e{i}", "especie": "vacuno lechero", "tipo_estudio": "ensayo controlado",
             "datos_cuantitativos": True, "comparable": True} for i in range(1, n + 1)]


@requires_node
def test_coord_doble_extraccion_decide_con_las_salidas_literales(project, capsys):
    reglas = [_regla_registro(project, capsys, "O-0003"),
              {"label": "bibliografia:O-0003", "respuesta": {"archivo": "research/revisiones/O-0003_busqueda.md",
                                                             "estudios": _estudios(4)}},
              *_reglas_doble_extraccion(project, capsys, "O-0003", ["doi:10.1000/e1", "doi:10.1000/e2"],
                                        con_discrepancias={"doi:10.1000/e2"})]
    # e3: el agente de conciliación declara que todo fue bien, pero la comparación terminó con error (código 3)
    error = _salida(capsys, "--agente", "extractor-datos-C-O-0003-r1", "--json", "extraccion", "comparar",
                    "research/datos/O-0003/no_existe_A.json", "research/datos/O-0003/no_existe_B.json", "--registrar")
    assert error["codigo_salida"] != 0
    for copia in "AB":
        reglas.append({"label": f"extraccion:{copia}:doi:10.1000/e3", "respuesta": next(
            r["respuesta"] for r in reglas if r.get("label") == f"extraccion:{copia}:doi:10.1000/e1")})
    reglas.append({"label": "conciliacion:doi:10.1000/e3", "respuesta": {
        "comparar": error, "registrada": True, "estado_verificacion": "doble_concordante"}})
    # e4: la copia A la depositó otra identidad (no la de esta ejecución)
    otra = dict(next(r["respuesta"] for r in reglas if r.get("label") == "extraccion:A:doi:10.1000/e1"))
    otra["deposito"] = dict(otra["deposito"], salida=otra["deposito"]["salida"].replace(
        "extractor-datos-A-O-0003-r1", "extractor-datos-A-O-0003-r7"))
    reglas += [{"label": "extraccion:A:doi:10.1000/e4", "respuesta": otra},
               {"label": "extraccion:B:doi:10.1000/e4", "respuesta": next(
                   r["respuesta"] for r in reglas if r.get("label") == "extraccion:B:doi:10.1000/e1")}]
    out = run_workflow("evaluar-oportunidad-auto", {"id": "O-0003"}, reglas)
    ev = out["resultado"]["evidencia"]
    assert ev["doble_extraccion_registrada"] == 2
    motivos = {s["fuente"]: s["motivo"] for s in ev["sin_registrar"]}
    assert "no registró la comparación (código" in motivos["doi:10.1000/e3"]
    assert "depositada por" in motivos["doi:10.1000/e4"]
    assert "conciliacion:doi:10.1000/e4" not in labels(out)
    assert "metaanalisis:O-0003" not in labels(out)    # 2 < 3 comparables
    assert any("Metaanálisis no lanzado: 2 estudio(s)" in m for m in out["logs"])


@requires_node
def test_coord_discrepancias_sin_conciliacion_no_cuentan(project, capsys):
    reglas = [_regla_registro(project, capsys, "O-0003"),
              {"label": "bibliografia:O-0003", "respuesta": {"archivo": "research/revisiones/O-0003_busqueda.md",
                                                             "estudios": _estudios(1)}},
              *_reglas_doble_extraccion(project, capsys, "O-0003", ["doi:10.1000/e1"], con_discrepancias={"doi:10.1000/e1"})]
    conciliacion = next(r for r in reglas if r.get("label") == "conciliacion:doi:10.1000/e1")
    del conciliacion["respuesta"]["conciliar"]
    out = run_workflow("evaluar-oportunidad-auto", {"id": "O-0003"}, reglas)
    ev = out["resultado"]["evidencia"]
    assert ev["doble_extraccion_registrada"] == 0
    assert "sin conciliación registrada" in ev["sin_registrar"][0]["motivo"]


@requires_node
def test_coord_ejecucion_da_identidades_nuevas_a_los_extractores(project, capsys):
    reglas = [_regla_registro(project, capsys, "O-0003"),
              {"label": "bibliografia:O-0003", "respuesta": {"archivo": "a.md", "estudios": _estudios(1)}}]
    out = run_workflow("evaluar-oportunidad-auto", {"id": "O-0003", "ejecucion": 2}, reglas)
    assert "nutria --agente extractor-datos-B-O-0003-r2 --json extraccion depositar" in call(
        out, "extraccion:B:doi:10.1000/e1")["prompt"]
    assert run_workflow("evaluar-oportunidad-auto", {"id": "O-0003", "ejecucion": 0})["resultado"]["error"]



# ============================================================================ fase de coordinación: misión 1
@requires_node
@pytest.mark.parametrize("revocacion", ["Selección cancelada", "Selección denegada", "Selección aplazada", "Seleccionada: NO",
                                        "Selección: no procede", "Selección suspendida hasta nuevo aviso",
                                        "Selección invalidada por error", "deseleccionada", "Seleccionada M2"])
def test_coord_mision1_cualquier_otra_decision_firmada_revoca_la_seleccion(revocacion, project, capsys):
    # Revisión final de P8: la selección solo cuenta como firmada con la frase canónica que pide el workflow
    # («seleccionada M1»); cualquier otra decisión firmada posterior sobre la selección la revoca (falla en cerrado)
    _registrar_oportunidades(project, capsys, 1)
    base = _ver(capsys, "O-0001")
    vista = json.loads(base["salida"])
    vista["decisiones"] = [_decision(1, "seleccionada M1", "Ana"), _decision(2, revocacion, "Ana")]
    reglas = [_regla_paso0(project, capsys),
              {"label": "compuertas-registro:planes:O-0001", "respuesta": {**base, "salida": json.dumps(vista)}}]
    out = run_workflow("mision-1-auto", {"fase": "planes", "seleccion": ["O-0001"]}, reglas)
    assert out["resultado"]["planes"][0]["seleccion_firmada"] is False, revocacion
    assert "NO tiene firma humana" in call(out, "plan:O-0001")["prompt"]


# ============================================================================ fase de coordinación: mutaciones de P8
# Revisión final de P8: 9 mutaciones de la interpretación de salidas literales sobrevivían (R2, R4, R7, R8, R9, R18, R19,
# R25, R36). R25 (la negación «deselecci») ya no existe: la selección solo se confirma con la frase canónica.
@requires_node
def test_coord_registro_de_puntuacion_exige_la_linea_ok_de_su_fichero_y_su_evaluador(project, capsys):  # R2, R4
    registro = _regla_registro(project, capsys, "O-0003")
    reglas_p = _reglas_puntuacion_emitida(project, capsys, "O-0003")
    literal = next(r["respuesta"] for r in reglas_p if r.get("label") == "registro-puntuacion:evaluador-1")
    rel = "research/oportunidades/O-0003_puntuacion_evaluador-1.json"
    # R2: la línea «OK» es de otro fichero: el registro consta, pero sin la comprobación literal del sha256 (alerta)
    otra_linea = dict(literal, salida=literal["salida"].replace(f"{rel}: OK", "research/oportunidades/otro.json: OK"))
    # R4: el JSON de puntuar registrar es de otro evaluador: no consta como registro de evaluador-2
    lit2 = next(r["respuesta"] for r in reglas_p if r.get("label") == "registro-puntuacion:evaluador-2")
    otro_ev = dict(lit2, salida=lit2["salida"].replace('"evaluador": "evaluador-2"', '"evaluador": "evaluador-9"'))
    assert otro_ev["salida"] != lit2["salida"]
    reglas = [registro, {"label": "plan:O-0003", "respuesta": _plan_evaluar(1)},
              {"label": "registro-puntuacion:evaluador-1", "respuesta": otra_linea},
              {"label": "registro-puntuacion:evaluador-2", "respuesta": otro_ev},
              *reglas_p, {"label": "sello:O-0003", "respuesta": SELLO_VALIDO}]
    res = run_workflow("evaluar-oportunidad-auto", "O-0003", reglas)["resultado"]
    por_id = {e["id"]: e for e in res["evaluadores"]}
    assert por_id["evaluador-1"]["registrado"] is True
    assert "sin la comprobación literal del sha256" in por_id["evaluador-1"]["alerta"]
    assert por_id["evaluador-2"]["registrado"] is False and "no confirma el registro" in por_id["evaluador-2"]["fallo"]


@requires_node
@pytest.mark.parametrize("caso", ["linea-extrana", "vacia", "codigo"])
def test_coord_procedencia_no_interpretable_no_es_sin_eventos(caso, project, capsys):  # R7, R8, R9
    registro = _regla_registro(project, capsys, "O-0003")
    reglas_p = _reglas_puntuacion_emitida(project, capsys, "O-0003")
    buena = _salida(capsys, *ORDEN_PROCEDENCIA)
    mala = {"linea-extrana": dict(buena, salida=buena["salida"] + "Aviso: la cadena de la procedencia no es íntegra\n"),
            "vacia": dict(buena, salida=""),
            "codigo": dict(buena, codigo_salida=3)}[caso]
    base = [registro, {"label": "plan:O-0003", "respuesta": _plan_evaluar(1)}, *reglas_p,
            {"label": "sello:O-0003", "respuesta": SELLO_VALIDO}]
    out = run_workflow("evaluar-oportunidad-auto", "O-0003", [*base, {"label": "procedencia:antes-de-puntuar", "respuesta": buena},
                                                               {"label": "procedencia:tras-puntuar", "respuesta": mala}])
    prematuros = out["resultado"]["registros_prematuros"]
    assert prematuros["comprobado"] is False and "no se pudo comprobar en la procedencia" in prematuros["motivo"], caso


@requires_node
@pytest.mark.parametrize("caso", ["otro-estado", "codigo", "otra-oportunidad"])
def test_coord_etica_repropuesta_solo_consta_con_su_salida_literal(caso, project, capsys):  # R18, R19, R36
    _registrar_oportunidades(project, capsys, 2)
    plan = {"archivo": "research/planes/O-0001.md", "resumen": "r"}
    literal = _salida(capsys, "--agente", "sostenibilidad-bienestar", "--json", "puntuar", "compuerta", "O-0001", "etica",
                      "no_resuelta", "--motivo", "m")
    assert literal["codigo_salida"] == 0, literal
    otra = _salida(capsys, "--agente", "sostenibilidad-bienestar", "--json", "puntuar", "compuerta", "O-0002", "etica",
                   "no_resuelta", "--motivo", "m")
    registro = {"otro-estado": literal, "codigo": dict(literal, codigo_salida=1), "otra-oportunidad": otra}[caso]
    estado = "superada" if caso == "otro-estado" else "no_resuelta"
    reglas = [_regla_paso0(project, capsys), {"label": "compuertas-registro:planes:O-0001", "respuesta": _ver(capsys, "O-0001")},
              {"label": "plan:O-0001", "respuesta": plan}, {"label": "revision-plan:O-0001", "respuesta": plan},
              {"label": "etica-plan:O-0001", "respuesta": {"compuerta": "etica", "estado": estado, "motivo": "m", "resumen": "r",
                                                            "registro": registro}}]
    etica = run_workflow("mision-1-auto", {"fase": "planes", "seleccion": ["O-0001"]}, reglas)["resultado"]["planes"][0]["etica"]
    assert etica["registrada"] is False, caso
    # control: la salida literal de su propia orden sí consta
    reglas[-1]["respuesta"] = {"compuerta": "etica", "estado": "no_resuelta", "motivo": "m", "resumen": "r", "registro": literal}
    ok = run_workflow("mision-1-auto", {"fase": "planes", "seleccion": ["O-0001"]}, reglas)["resultado"]["planes"][0]["etica"]
    assert ok["registrada"] is True


@requires_node
def test_coord_red_team_segunda_opinion_sin_respuesta_no_es_fallo():
    # Petición de P4 a P8: `nutria llm preguntar` sale con 1 si ningún modelo respondió: «segunda opinión no disponible»
    out = run_workflow("red-team-auto", "research/informes/O-0003.md",
                       [{"label": "otra-familia", "respuesta": {"ejecutado": False, "motivo": "segunda opinión no disponible"}}])
    prompt = call(out, "otra-familia")["prompt"]
    assert "termina con código 1, ningún modelo respondió" in prompt and "segunda opinión no disponible" in prompt
    assert out["resultado"]["otra_familia"]["ejecutado"] is False
    assert not any(f["agente"] == "otra-familia" for f in out["resultado"]["fallos"]["detalle"])


def test_coord_ordenes_nutria_atribuidas_en_agentes_y_skills():
    # Petición de P1 a P8 (nota del director): dentro de un subagente el hook bloquea toda orden nutria sin --agente
    # literal. Los agentes y las skills escriben cada orden que piden ejecutar con su --agente; quedan sin él las
    # prohibiciones («no ejecutes nutria puntuar ranking») y las órdenes de una persona (con la firma humana).
    import argparse

    from nutria.cli import build_parser
    subs = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)).choices
    orden = re.compile(r"\bnutria\s+(?:(?:--json|--offline)\s+)*(" + "|".join(sorted(subs)) + r")\b")
    negacion = re.compile(r"(?i)\b(no|nunca|ni|sin|jamás)\b[^.;:()]{0,40}$")
    firma = "--firma" + "-humana"
    faltan = []
    for path in [*sorted(AGENTS_DIR.glob("*.md")), *sorted(SKILLS_DIR.glob("*/SKILL.md"))]:
        texto = _text(path)
        if texto.startswith("---"):
            texto = texto.split("---", 2)[2]          # sin el front matter
        for linea in texto.splitlines():
            if firma in linea:
                continue
            faltan += [f"{path.parent.name}/{path.name}: {m.group(0)}" for m in orden.finditer(linea)
                       if not negacion.search(linea[:m.start()])]
    assert not faltan, faltan[:10]


def test_coord_compuertas_de_los_workflows_coinciden_con_el_registro():
    # Petición de P1 a P8: el rol que propone cada compuerta en los workflows (COMPUERTA_DE de evaluar-oportunidad-auto,
    # TRIAGE de mision-1-auto) es el propietario que exige el registro (nutria.db.GATE_OWNERS)
    from nutria.db import GATE_OWNERS
    registro = {compuerta: dueno for compuerta, dueno in GATE_OWNERS.items()}
    evaluar = _text(WORKFLOWS_DIR / "evaluar-oportunidad-auto.js")
    bloque = evaluar[evaluar.index("const COMPUERTA_DE = {"):]
    bloque = bloque[:bloque.index("}")]
    compuerta_de = dict(re.findall(r"'([a-z-]+)': '([a-z_]+)'", bloque))
    assert {compuerta: agente for agente, compuerta in compuerta_de.items()} == registro
    mision = _text(WORKFLOWS_DIR / "mision-1-auto.js")
    triage = mision[mision.index("const TRIAGE = ["):]
    triage = triage[:triage.index("\n]\n")]
    pares = re.findall(r"agente: '([a-z-]+)', compuerta: '([a-z_]+)'", triage)
    assert {compuerta: agente for agente, compuerta in pares} == registro
    assert COMPUERTA_DE == {agente: compuerta for compuerta, agente in registro.items()}   # la copia de estas pruebas
