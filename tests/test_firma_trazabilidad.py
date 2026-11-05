"""Firma humana, identidad de los actores, compuertas por rol y trazabilidad de la procedencia.

- D2-5, D5-8, D8-2, D3-2, D5-7 y los restos de D1-1 y D1-2: ningún analizador admite abreviaturas; la CLI y el
  registro rechazan toda firma con --agente, NUTRIA_AGENT, dentro de Claude Code (entorno propio, entorno inicial de
  los procesos antecesores o Claude Code entre ellos) o sin terminal de control; el hook de Bash normaliza la orden
  antes de buscar la opción, las variables de entorno, el registro, la procedencia y los sellos, y ata --agente al
  tipo de subagente.
- D1-2, D5-8 y D8-2: sin firma, cada compuerta la propone solo su rol propietario; "bloqueada", cualquiera; una
  propuesta no sustituye una decisión firmada; desbloquear exige firma y evidencia comprobable.
- D2-1: cada evaluador registra solo sus puntuaciones y el historial guarda el actor real.
- D3-11, D3-12 y D9-7: cadena de hashes, `nutria procedencia verificar`, líneas corruptas contadas, contexto, sesión,
  git y entorno en cada evento, decisiones solo de inserción, sobre entidades existentes y con evento.
- D1-11: autoría inventiva humana firmada. D7-7: hook de WebFetch y WebSearch.

Las pruebas que firman como una persona llaman a `main()` en el mismo proceso: el fixture automático de conftest.py
quita las señales de agente (también si pytest corre dentro de Claude Code).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from nutria import db, provenance
from nutria.cli import _firma_solo_de_personas, build_parser, main
from nutria.config import Config, load_config

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
EXAMPLES = ROOT / "ejemplos"
HOOKS = ROOT / "scripts" / "hooks"
SIGN = "--firma-humana"
EVID = ["--evidencia", "https://example.org/analisis-de-triage"]
linux_only = pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="requiere /proc (Linux)")
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git no está disponible")


# ============================================================================ utilidades
def _conn():
    return db.connect(load_config().db_path)


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text("utf-8"))


def _write_json(path: Path, data) -> str:
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return str(path)


def _new_opportunity(project: Path, **changes) -> str:
    data = _example("oportunidad_ejemplo.json")
    data.update(changes)
    n = len(db.list_opportunities(_conn())) + 1
    assert main(["oportunidad", "nueva", "--datos", _write_json(project / f"oportunidad_{n}.json", data)]) == 0
    return db.list_opportunities(_conn())[-1]["id"]


def _new_hypothesis(project: Path, opp_id: str) -> str:
    data = _example("hipotesis_ejemplo.json")
    data["oportunidad"] = opp_id
    assert main(["hipotesis", "nueva", "--datos", _write_json(project / "hipotesis.json", data)]) == 0
    return db.list_hypotheses(_conn())[-1]["id"]


def _scores_file(project: Path, rater: str, value: float, opp_id: str = "O-0001") -> str:
    data = _example("puntuacion_ejemplo.json")
    data.update(oportunidad=opp_id, evaluador=rater)
    for item in data["puntuaciones"]:
        item.update(valor=value, min=value, max=value)
    return _write_json(project / f"puntuacion_{rater}_{value}.json", data)


def _events(project: Path) -> list[dict]:
    text = (project / "research" / "procedencia.jsonl").read_text("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _code(argv) -> int | str | None:
    """Código de salida de la CLI, tanto si `main` lo devuelve como si sale con SystemExit."""
    try:
        return main(argv)
    except SystemExit as exc:
        return exc.code


def _all_parsers(parser: argparse.ArgumentParser) -> list[argparse.ArgumentParser]:
    found = [parser]
    for action in parser._actions:  # noqa: SLF001 - argparse no expone las subórdenes de otra forma
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            for sub in dict.fromkeys(action.choices.values()):
                found += _all_parsers(sub)
    return found


def _matches(parser: argparse.ArgumentParser, arg: str) -> list:
    result = parser._parse_optional(arg)  # noqa: SLF001
    if result is None:
        return []
    if isinstance(result, tuple):  # Python 3.11: una sola tupla
        result = [result]
    return [t for t in result if t[0] is not None]


# ============================================================================ sin abreviaturas (D2-5, D5-8, D8-2...)
def test_todos_los_analizadores_se_crean_sin_abreviaturas(monkeypatch):
    created = []
    original = argparse.ArgumentParser.__init__

    def spy(self, *args, **kwargs):
        original(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(argparse.ArgumentParser, "__init__", spy)
    build_parser()
    assert len(created) > 40    # el padre común, la raíz y todas las órdenes y subórdenes
    assert [p.prog for p in created if p.allow_abbrev] == []


def test_ninguna_abreviatura_de_una_opcion_larga_se_acepta_en_ninguna_orden():
    checked = 0
    for parser in _all_parsers(build_parser()):
        options = {s for action in parser._actions for s in action.option_strings}  # noqa: SLF001
        for option in sorted(s for s in options if s.startswith("--")):
            for end in range(3, len(option)):
                prefix = option[:end]
                if prefix in options:
                    continue
                for arg in (prefix, prefix + "=valor"):
                    assert _matches(parser, arg) == [], f"{parser.prog}: {arg} se acepta como {option}"
                    checked += 1
            assert _matches(parser, option)       # la opción completa sí
    assert checked > 1000


@pytest.mark.parametrize("argv", [
    ["puntuar", "compuerta", "O-0001", "seguridad", "superada", "--motivo", "x", "--firma", "Ana"],
    ["puntuar", "compuerta", "O-0001", "seguridad", "superada", "--motivo", "x", "--firma-h", "Ana"],
    ["puntuar", "compuerta", "O-0001", "seguridad", "superada", "--motivo", "x", "--fir=Ana"],
    ["puntuar", "compuerta", "O-0001", "seguridad", "superada", "--motivo", "x", "--f", "Ana"],
    ["decision", "registrar", "--decision", "d", "--motivo", "m", "--firma-hum", "Ana"],
    ["verificar", "manual", "https://www.ejemplo-inventado.org/documento-x", "--firma", "Ana"],
    ["oportunidad", "estado", "O-0001", "en_validacion", "--motivo", "x", "--firm", "Ana"],
    ["autoria", "registrar", "--entidad", "O-0001", "--persona", "Ana", "--aportacion", "a", "--firma", "Ana"],
    ["--age", "economia-mercado-pi", "estado"],
])
def test_las_abreviaturas_se_rechazan_al_analizar(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv)
    assert exc.value.code == 2


def test_la_opcion_completa_de_firma_sigue_analizandose():
    args = build_parser().parse_args(["decision", "registrar", "--decision", "d", "--motivo", "m", SIGN, "Ana"])
    assert args.firma_humana == "Ana"


# ============================================================================ la firma solo desde la terminal de una persona
FIRMADAS = [
    ["decision", "registrar", "--entidad", "O-0001", "--decision", "seleccionada M1", "--motivo", "m", SIGN, "Ana"],
    ["puntuar", "compuerta", "O-0001", "seguridad", "superada", "--motivo", "m", *EVID, SIGN, "Ana"],
    ["oportunidad", "estado", "O-0001", "priorizada", "--motivo", "m", SIGN, "Ana"],
    ["hipotesis", "estado", "H-0001", "promovida", "--motivo", "m", SIGN, "Ana"],
    ["verificar", "manual", "https://eur-lex.europa.eu/eli/reg/2009/767/oj", SIGN, "Ana"],
    ["autoria", "registrar", "--entidad", "O-0001", "--persona", "Ana", "--aportacion", "Combinación X + Y", SIGN, "Ana"],
]


@pytest.fixture
def registered(project):
    opp = _new_opportunity(project)
    _new_hypothesis(project, opp)
    return project


def _snapshot(project: Path):
    conn = _conn()
    return ([dict(r) for r in conn.execute("SELECT * FROM decisions")], db.list_gates(conn),
            [dict(r) for r in conn.execute("SELECT * FROM manual_verifications")],
            db.list_inventive_contributions(conn), [o["status"] for o in db.list_opportunities(conn)],
            (project / "research" / "procedencia.jsonl").read_text("utf-8"))


@pytest.mark.parametrize("senal", ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "AI_AGENT", "NUTRIA_AGENT", "--agente",
                                   "antecesor"])
@pytest.mark.parametrize("argv", FIRMADAS, ids=lambda a: "-".join(a[:2]))
def test_la_firma_se_rechaza_en_contexto_de_agente_sin_tocar_nada(registered, monkeypatch, capsys, argv, senal):
    before = _snapshot(registered)
    if senal == "--agente":
        argv = ["--agente", "director-cientifico", *argv]
    elif senal == "antecesor":   # p. ej. `env -i nutria ...` lanzado desde la shell de Claude Code
        monkeypatch.setattr(provenance, "ancestor_markers", lambda *a, **k: ["CLAUDECODE (proceso 4242)"])
    else:
        monkeypatch.setenv(senal, "1")
    capsys.readouterr()
    assert _code(argv) == 3
    err = capsys.readouterr().err
    assert "Firma humana rechazada" in err and "terminal de una persona" in err
    assert _snapshot(registered) == before


def test_una_persona_firma_desde_su_terminal(registered):
    for argv in FIRMADAS:
        assert _code(argv) == 0, argv
    conn = _conn()
    assert db.get_opportunity(conn, "O-0001")["status"] == "priorizada"
    assert db.get_manual_verification(conn, "https://eur-lex.europa.eu/eli/reg/2009/767/oj")["signed_by"] == "Ana"
    assert all(d["context"] == "terminal" for d in db.list_decisions(conn))


def test_la_comprobacion_cubre_cualquier_destino_de_firma(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    for ns in (argparse.Namespace(agente=None, firma_humana="Ana"),
               argparse.Namespace(agente=None, firma_humana=""),        # firma vacía: también es un intento
               argparse.Namespace(agente=None, firma_revisor="Ana"),    # opciones de firma futuras
               argparse.Namespace(agente=None, firmar=True)):
        with pytest.raises(SystemExit) as exc:
            _firma_solo_de_personas(ns)
        assert exc.value.code == 3
    _firma_solo_de_personas(argparse.Namespace(agente="x", firma_humana=None, firmar=False))   # sin firma: nada


def test_la_cli_real_rechaza_la_firma_de_un_agente(registered):
    """Reproducción de la reauditoría como proceso aparte: abreviatura (código 2) y opción completa (código 3)."""
    env = {**os.environ, "PYTHONPATH": str(SRC), "CLAUDECODE": "1", "NUTRIA_OFFLINE": "1"}
    env.pop("NUTRIA_HOME", None)
    base = [sys.executable, "-m", "nutria", "--agente", "economia-mercado-pi", "puntuar", "compuerta", "O-0001",
            "seguridad", "superada", "--motivo", "x", *EVID]
    for extra, expected in ((["--firma", "Ana"], 2), ([SIGN, "Ana"], 3), ([SIGN + "=Ana"], 3)):
        proc = subprocess.run([*base, *extra], cwd=registered, env=env, capture_output=True, text=True, timeout=60)
        assert proc.returncode == expected, proc.stderr
    assert db.list_gates(_conn()) == []


@linux_only
def test_los_antecesores_delatan_a_claude_code_aunque_se_vacie_el_entorno(tmp_path):
    probe = tmp_path / "sonda.py"
    probe.write_text(f"import json, os, sys\nsys.path.insert(0, {str(SRC)!r})\n"
                     "from nutria.provenance import ancestor_markers\n"
                     "print(json.dumps({'ppid': os.getppid(), 'marcas': ancestor_markers(), "
                     "'entorno': 'CLAUDECODE' in os.environ}))\n", "utf-8")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CLAUDECODE": "1"}
    proc = subprocess.run(["sh", "-c", f'env -i PATH="$PATH" "{sys.executable}" "{probe}"; true'], env=env,
                          capture_output=True, text=True, timeout=60)
    data = json.loads(proc.stdout)
    assert data["entorno"] is False
    assert f"CLAUDECODE (proceso {data['ppid']})" in data["marcas"]


def _fake_proc(root: Path, pid: int, ppid: int, comm: str, cmdline: list[str], environ: dict, tty: int = 0) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "stat").write_text(f"{pid} ({comm}) S {ppid} {pid} {pid} {tty} -1 4194560", "utf-8")
    (d / "comm").write_text(comm + "\n", "utf-8")
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in cmdline) + b"\0")
    (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in environ.items()) + b"\0")


def test_marcas_de_los_antecesores_con_un_proc_simulado(tmp_path):
    proc = tmp_path / "proc"
    _fake_proc(proc, 300, 200, "python3", ["python3", "-m", "nutria"], {"PATH": "/bin"})
    _fake_proc(proc, 200, 100, "bash", ["/bin/bash", "-c", "x"], {"PATH": "/bin", "CLAUDE_CODE_ENTRYPOINT": "cli"})
    _fake_proc(proc, 100, 1, "claude", ["/opt/claude-code/bin/claude"], {"PATH": "/bin"})
    assert provenance.ancestor_markers(proc, pid=300) == ["CLAUDE_CODE_ENTRYPOINT (proceso 200)",
                                                          "Claude Code (proceso 100)"]
    node = tmp_path / "proc_node"
    _fake_proc(node, 30, 20, "python3", ["python3"], {})
    _fake_proc(node, 20, 1, "node", ["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"], {})
    assert provenance.ancestor_markers(node, pid=30) == ["Claude Code (proceso 20)"]
    human = tmp_path / "proc_persona"
    _fake_proc(human, 30, 20, "python3", ["python3", "-m", "nutria"], {"PATH": "/bin"})
    _fake_proc(human, 20, 1, "zsh", ["-zsh"], {"TERM": "xterm-256color", "CLAUDE": "no-es-una-marca"})
    assert provenance.ancestor_markers(human, pid=30) == []
    assert provenance.ancestor_markers(tmp_path / "sin_proc", pid=30) == []


def test_el_rechazo_sale_con_codigo_3_y_su_texto_explica_el_motivo(registered, capsys):
    """El texto de la salida (str) es el motivo: otras órdenes con firma (p. ej. `informe revisar`) lo heredan."""
    with pytest.raises(SystemExit) as exc:
        main(["--agente", "director-cientifico", *FIRMADAS[0]])
    assert exc.value.code == 3
    assert "no la registra un agente" in str(exc.value) and "--agente" in str(exc.value)
    capsys.readouterr()
    with pytest.raises(SystemExit):                       # con --json, el error sale como JSON por stdout
        main(["--json", "--agente", "director-cientifico", *FIRMADAS[0]])
    payload = json.loads(capsys.readouterr().out)
    assert (payload["codigo"], payload["tipo"]) == (3, "registro") and "Firma humana rechazada" in payload["error"]


@linux_only
def test_borrar_la_variable_dentro_del_proceso_no_basta(registered):
    """El entorno inicial (/proc/<pid>/environ) conserva la marca aunque el proceso borre la variable antes de firmar."""
    script = registered / "firma_desde_dentro.py"
    script.write_text(textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(SRC)!r})
        os.environ.pop("CLAUDECODE")
        print(os.getpid(), flush=True)
        from nutria.cli import main
        sys.exit(main({FIRMADAS[0]!r}))
    """), "utf-8")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CLAUDECODE": "1", "NUTRIA_OFFLINE": "1"}
    proc = subprocess.run([sys.executable, str(script)], cwd=registered, env=env, capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 3, proc.stderr
    assert f"CLAUDECODE (proceso {proc.stdout.strip()})" in proc.stderr
    assert [d for d in db.list_decisions(_conn()) if d["decision"] == "seleccionada M1"] == []


def test_el_registro_tambien_rechaza_la_firma_sin_pasar_por_la_cli(registered, monkeypatch):
    """Un programa que llame directamente a las funciones del registro tampoco firma desde Claude Code."""
    before = _snapshot(registered)
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    conn = _conn()
    gates = list(db.GATE_OWNERS)
    intentos = [
        lambda: db.set_gate(conn, "O-0001", "seguridad", "superada", "m", gates, human_signoff="Ana"),
        lambda: db.add_decision(conn, "O-0001", "seleccionada", "m", human_signoff="Ana"),
        lambda: db.add_manual_verification(conn, "https://eur-lex.europa.eu/eli/reg/2009/767/oj", "Ana"),
        lambda: db.set_opportunity_status(conn, "O-0001", "priorizada", "m", human_signoff="Ana", gate_ids=gates),
        lambda: db.set_hypothesis_status(conn, "H-0001", "promovida", "m", human_signoff="Ana"),
        lambda: db.add_inventive_contribution(conn, "O-0001", "Ana", "X + Y", "Ana"),
    ]
    for intento in intentos:
        with pytest.raises(db.RegistryError, match="Firma humana rechazada"):
            intento()
    assert _snapshot(registered) == before
    db.add_decision(conn, "O-0001", "propuesta", "m", actor="director-cientifico")      # sin firma: se admite


@linux_only
def test_un_programa_que_llama_al_registro_desde_claude_code_no_firma(registered):
    script = registered / "firma_directa.py"
    script.write_text(textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(SRC)!r})
        for var in list(os.environ):
            if var.startswith("CLAUDE"):
                os.environ.pop(var)
        from nutria import db
        from nutria.config import load_config
        db.add_decision(db.connect(load_config().db_path), "O-0001", "seleccionada M1", "m", human_signoff="Ana")
    """), "utf-8")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"}
    proc = subprocess.run([sys.executable, str(script)], cwd=registered, env=env, capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode != 0 and "Firma humana rechazada" in proc.stderr
    assert [d for d in db.list_decisions(_conn()) if d["human_signoff"]] == []


@pytest.mark.parametrize("tty,expected", [(0, 3), (34816, 0)])
def test_sin_terminal_de_control_en_la_cadena_no_se_firma(registered, monkeypatch, capsys, tty, expected):
    """Un proceso desvinculado de Claude Code (huérfano, adoptado por init) y sin terminal no firma, aunque no tenga
    ninguna variable de Claude Code; con terminal de control (la de una persona), sí."""
    proc = registered / "proc"
    _fake_proc(proc, os.getpid(), 1, "python3", ["python3", "-m", "nutria"], {"PATH": "/usr/bin"}, tty=tty)
    monkeypatch.setattr(provenance, "PROC_ROOT", str(proc))
    assert provenance.interactive_terminal() is bool(tty)
    capsys.readouterr()
    assert _code(FIRMADAS[0]) == expected
    if not tty:
        assert "sin terminal de control" in capsys.readouterr().err


# ============================================================================ hook de Bash
def _hook(script: str, payload: dict, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(HOOKS / script)], input=json.dumps(payload), capture_output=True, text=True,
                          timeout=60, env=env)


def _bash(command: str) -> dict:
    return {"session_id": "s", "transcript_path": "/home/u/.claude/projects/p/s.jsonl", "cwd": "/home/u/nutria",
            "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command, "description": "orden de prueba"}}


BLOQUEADAS = [
    # firma humana: opción completa, abreviaturas y formas ofuscadas
    "nutria puntuar compuerta O-0001 seguridad superada --motivo x --firma-humana Ana",
    "nutria decision registrar --entidad O-0001 --decision d --motivo m --firma Ana",
    "nutria verificar manual https://x.org --firma-h Ana",
    "nutria verificar manual https://x.org --fir Ana",
    "nutria verificar manual https://x.org --firm Ana",
    "nutria verificar manual https://x.org --firma-humana=Ana",
    "nutria verificar manual https://x.org --fir=Ana",
    'nutria verificar manual https://x.org "--firma-""humana" Ana',
    "nutria verificar manual https://x.org '--firma'-humana Ana",
    "nutria verificar manual https://x.org --fir\\ma-humana Ana",
    "nutria verificar manual https://x.org --fir$()ma-humana Ana",
    "nutria verificar manual https://x.org --fir${X}ma-humana Ana",
    "nutria verificar manual https://x.org --fi${X}rma-humana Ana",
    "nutria verificar manual https://x.org --fi$X''rma-humana Ana",
    "nutria verificar manual https://x.org $'--firma-humana' Ana",
    "nutria verificar manual https://x.org `echo --fir`ma-humana Ana",
    "nutria x \\\n --firma-humana Ana",
    "nutria verificar manual https://x.org $'\\x2d\\x2dfirma-humana' Ana",           # secuencias de escape
    "nutria verificar manual https://x.org \"$(printf '\\055\\055firma-humana')\" Ana",
    "nutria verificar manual https://x.org $'\\u002d\\u002dfirma-humana' Ana",
    "nutria verificar manual https://x.org $(printf %s -- -fir)ma-humana Ana",       # concatenación de un guion
    "nutria verificar manual https://x.org -$(echo -firma_humana) Ana",
    # entorno con el que la CLI reconoce a los agentes
    "env -u CLAUDECODE nutria decision registrar --decision d --motivo m",
    "unset CLAUDECODE; nutria estado",
    "CLAUDE_CODE_ENTRYPOINT= nutria estado",
    "AI_AGENT= nutria estado",
    "env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION nutria estado",
    "env -i PATH=/usr/bin nutria estado",
    "/usr/bin/env -i nutria estado",
    "env - nutria estado",
    "env --ignore-environment nutria estado",
    "env -u NUTRIA_AGENT nutria estado",
    "unset NUTRIA_AGENT",
    "unset -v NUTRIA_AGENT",
    "NUTRIA_AGENT=evaluador-1 nutria puntuar registrar --datos f.json",
    "export NUTRIA_AGENT=x",
    "python3 -c \"import os; os.environ.pop('NUTRIA_AGENT')\"",
    "exec -c nutria estado",
    "sudo nutria estado",
    # procesos desvinculados de la sesión de Claude Code (los antecesores dejarían de delatarlos)
    "setsid -f nutria estado",
    "nohup nutria estado &",
    "nutria estado & disown",
    "python3 -c \"import os, subprocess; os.fork()\"",
    "python3 -c \"import subprocess; subprocess.Popen(['nutria'], start_new_session=True)\"",
    # registro por fuera de la CLI
    "sqlite3 research/nutria.sqlite \"UPDATE gates SET human_signoff='Ana'\"",
    "python3 -c \"import sqlite3; sqlite3.connect('research/nutria.sqlite').execute('DELETE FROM decisions')\"",
    "cp /tmp/x.sqlite research/nutria.sqlite",
    "rm research/nutria.sqlite",
    "cat /tmp/x > research/nutria.sqlite",
    "python3 -c \"from nutria import db; db.set_gate()\"",
    "python3 -c \"from nutria.db import set_gate\"",
    "python3 -c \"import nutria.provenance as p; p.log_event()\"",
    "echo '{}' >> research/procedencia.jsonl",
    "sed -i 1d research/procedencia.jsonl",
    "printf x | tee -a research/procedencia.jsonl",
    "git checkout -- research/procedencia.jsonl",
    "git checkout HEAD~3 -- research/",
    "git -C /home/u/nutria restore --source HEAD~1 research/nutria.sqlite",
    "git stash push research/",
    "python3 -m nutria estado; python3 -c \"open('research/procedencia.jsonl', 'w')\"",
    # sellos
    "cat ~/.config/nutria/clave_sello",
    "cat ~/.config/nutria/*",
    "echo $NUTRIA_CLAVE_SELLO",
    "python3 -c \"from nutria import sello; print(sello.sign({}))\"",
    "python3 -c \"import nutria.sello\"",
    "python3 -c \"from nutria.reports.check import write_stamp\"",
    "python3 -c \"from nutria import cli, sello\"",
]
PERMITIDAS = [
    "python -m pytest -q",
    "PYTHONPATH=src python3 -m pytest tests/test_registry_cli.py -q",
    "nutria informe comprobar research/informes/O-0001.md --online --sellar",
    "nutria --agente compuestos-toxicologia puntuar compuerta O-0001 seguridad bloqueada --motivo 'señal'",
    "nutria --agente compuestos-toxicologia puntuar compuerta O-0001 seguridad superada --motivo x --evidencia A-0001",
    "grep -n firma src/nutria/cli.py",
    "grep -rn human_signoff src/nutria/db.py",
    "git log --first-parent --oneline",
    "nutria procedencia --ultimas 30",
    "nutria procedencia verificar",
    "tail -n 5 research/procedencia.jsonl",
    "ls -la research/nutria.sqlite",
    "git add research/nutria.sqlite research/procedencia.jsonl",
    "nutria informe sello research/informes/O-0001.md",
    "echo $NUTRIA_AGENT",
    "nutria --agente evaluador-1 puntuar registrar --datos f.json",
    "env LC_ALL=C sort -i x.txt",
    "grep -i rumen notas.md",
    "nutria decision registrar --entidad O-0001 --decision 'propuesta de descarte' --motivo m",
    "nutria autoria listar --entidad O-0001",
    "git status && git diff --stat",
    "git log --oneline -- research/",
    "git commit -m 'reset de las notas de research'",
    "PYTHONPATH=src python3 -m nutria estado && ls -la research/nutria.sqlite",
    "grep -rn firma-humana docs/ CLAUDE.md",
    "IFS=$'\\n'; for f in *.md; do wc -l \"$f\"; done",
    "sed -n 's/\\x41/B/p' notas.txt",
    "nutria --agente extractor-datos-A extraccion depositar --datos research/datos/x/extraccion_y_A.json",
]


@pytest.mark.parametrize("command", BLOQUEADAS)
def test_el_hook_de_bash_bloquea(command):
    res = _hook("guard_bash.sh", _bash(command))
    assert res.returncode == 2, command
    assert "Bloqueado por el hook de NUTRIA" in res.stderr


@pytest.mark.parametrize("command", PERMITIDAS)
def test_el_hook_de_bash_deja_pasar_las_ordenes_legitimas(command):
    res = _hook("guard_bash.sh", _bash(command))
    assert res.returncode == 0, res.stderr


def test_el_hook_de_bash_solo_analiza_la_orden_y_sin_ella_falla_en_cerrado():
    payload = _bash("nutria --agente regulacion-seguridad puntuar compuerta O-0001 regulatoria superada --motivo m")
    payload["tool_input"]["description"] = "Registrar la propuesta sin --firma-humana (env -u CLAUDECODE no hace falta)"
    assert _hook("guard_bash.sh", payload).returncode == 0
    del payload["tool_input"]["command"]
    payload["tool_input"]["cmd"] = "nutria verificar manual https://x.org --firma Ana"
    assert _hook("guard_bash.sh", payload).returncode == 2


def _subagente(agent_type: str, command: str) -> dict:
    """Entrada del hook dentro de un subagente: Claude Code añade agent_id y agent_type."""
    return {**_bash(command), "agent_id": "a1b2c3", "agent_type": agent_type}


@pytest.mark.parametrize("agent_type,command", [
    # suplantar a otro rol del proyecto (también desde un subagente genérico) o a un evaluador
    ("economia-mercado-pi", "nutria --agente compuestos-toxicologia puntuar compuerta O-0001 seguridad superada --motivo x"),
    ("general-purpose", "nutria --agente regulacion-seguridad puntuar compuerta O-0001 regulatoria superada --motivo x"),
    ("critico-red-team", "nutria --agente evaluador-1 puntuar registrar --datos research/oportunidades/p.json"),
    ("critico-red-team", "nutria --agente=EvAlUaDoR-2 puntuar registrar --datos p.json"),
    ("director-cientifico", "nutria puntuar ranking --agente \"evaluador-3\""),
    # el evaluador independiente: siempre --agente evaluador-<n>, sin módulos de Python ni la base de datos
    ("evaluador-independiente", "nutria oportunidad ver O-0001 --json"),
    ("evaluador-independiente", "PYTHONPATH=src python3 -m nutria --json oportunidad ver O-0001"),
    ("evaluador-independiente", "nutria --agente director-cientifico puntuar ranking"),
    ("evaluador-independiente", "python3 -c \"from nutria.cli import main; main(['puntuar', 'ranking'])\""),
    ("evaluador-independiente", "strings research/nutria.sqlite | grep evaluador"),
])
def test_el_hook_ata_la_identidad_al_tipo_de_subagente(agent_type, command):
    res = _hook("guard_bash.sh", _subagente(agent_type, command))
    assert res.returncode == 2, (agent_type, command)
    assert "Bloqueado por el hook de NUTRIA" in res.stderr


@pytest.mark.parametrize("agent_type,command", [
    ("compuestos-toxicologia", "nutria --agente compuestos-toxicologia puntuar compuerta O-0001 seguridad superada --motivo x"),
    ("extractor-datos", "nutria --agente extractor-datos-A extraccion depositar --datos a.json"),
    ("general-purpose", "nutria --agente orquestador --json oportunidad ver O-0001 --sin-puntuaciones"),
    ("critico-red-team", "nutria oportunidad ver O-0001 --sin-puntuaciones --json"),     # sin --agente: sin privilegios
    ("evaluador-independiente", "nutria --agente evaluador-2 --json oportunidad ver O-0001 --sin-puntuaciones"),
    ("evaluador-independiente", "sha256sum research/oportunidades/O-0001_puntuacion_evaluador-2.json"),
    ("evaluador-independiente", "nutria --agente evaluador-2 --json puntuar registrar --datos "
                                "research/oportunidades/O-0001_puntuacion_evaluador-2.json"),
])
def test_el_hook_deja_a_cada_subagente_usar_su_rol(agent_type, command):
    res = _hook("guard_bash.sh", _subagente(agent_type, command))
    assert res.returncode == 0, res.stderr


def test_sin_tipo_de_subagente_el_hook_no_comprueba_la_identidad():
    """En el hilo principal no hay agent_type: --agente es una declaración (residuo declarado)."""
    command = "nutria --agente compuestos-toxicologia puntuar compuerta O-0001 seguridad superada --motivo x"
    assert _hook("guard_bash.sh", _bash(command)).returncode == 0


@pytest.mark.parametrize("path,blocked", [
    ("/home/u/nutria/research/procedencia.jsonl", True),
    ("/home/u/nutria/research/nutria.sqlite", True),
    ("/home/u/nutria/research/nutria.sqlite-wal", True),
    ("/home/u/nutria/research/informes/O-0001.verificacion.json", True),
    ("/root/.config/nutria/clave_sello", True),
    ("/root/.config/nutria/otra", True),
    ("/home/u/nutria/research/informes/O-0001.md", False),
    ("/home/u/nutria/docs/procedencia.md", False),
    ("/home/u/nutria/tests/test_firma_trazabilidad.py", False),
])
def test_el_hook_de_escritura_protege_registro_procedencia_y_sellos(path, blocked):
    payload = {"tool_name": "Write", "tool_input": {"file_path": path, "content": "x"}}
    assert _hook("guard_write.sh", payload).returncode == (2 if blocked else 0)


# ============================================================================ compuertas por rol propietario (D1-2, D8-2)
def test_sin_firma_cada_compuerta_la_propone_solo_su_rol_propietario(project, capsys):
    _new_opportunity(project)
    for gate, owner in db.GATE_OWNERS.items():
        other = next(o for o in db.GATE_OWNERS.values() if o != owner)
        assert _code(["--agente", other, "puntuar", "compuerta", "O-0001", gate, "superada", "--motivo", "m",
                      *EVID]) == 3
        assert _code(["--agente", other, "puntuar", "compuerta", "O-0001", gate, "no_resuelta", "--motivo", "m"]) == 3
        assert _code(["puntuar", "compuerta", "O-0001", gate, "no_resuelta", "--motivo", "m"]) == 3
        assert _code(["--agente", owner, "puntuar", "compuerta", "O-0001", gate, "superada", "--motivo", "m",
                      *EVID]) == 0
    gates = {g["gate"]: g for g in db.list_gates(_conn())}
    assert {g: row["decided_by"] for g, row in gates.items()} == db.GATE_OWNERS
    assert set(db.effective_gates(_conn(), "O-0001", list(db.GATE_OWNERS)).values()) == {"no_resuelta"}
    err = capsys.readouterr().err
    assert "corresponde a compuestos-toxicologia" in err and "identifícate con --agente" in err


def test_cualquier_agente_bloquea_y_solo_una_persona_desbloquea_con_evidencia(project):
    _new_opportunity(project)
    assert _code(["--agente", "critico-red-team", "puntuar", "compuerta", "O-0001", "seguridad", "bloqueada",
                  "--motivo", "señal de toxicidad"]) == 0
    assert db.effective_gates(_conn(), "O-0001", ["seguridad"])["seguridad"] == "bloqueada"
    for agent in ("compuestos-toxicologia", "sostenibilidad-bienestar"):
        assert _code(["--agente", agent, "puntuar", "compuerta", "O-0001", "seguridad", "superada", "--motivo", "y",
                      *EVID]) == 3
    assert _code(["puntuar", "compuerta", "O-0001", "seguridad", "no_resuelta", "--motivo", "revisado", SIGN,
                  "Ana"]) == 3                                     # desbloquear exige evidencia
    assert _code(["puntuar", "compuerta", "O-0001", "seguridad", "no_resuelta", "--motivo", "revisado", *EVID, SIGN,
                  "Ana"]) == 0
    assert db.effective_gates(_conn(), "O-0001", ["seguridad"])["seguridad"] == "no_resuelta"


def test_nutria_agent_identifica_al_rol_propietario(project, monkeypatch):
    _new_opportunity(project)
    monkeypatch.setenv("NUTRIA_AGENT", "fisiologia-bioquimica")
    assert _code(["puntuar", "compuerta", "O-0001", "destino_digestivo", "superada", "--motivo", "m", *EVID]) == 0
    assert _code(["puntuar", "compuerta", "O-0001", "seguridad", "superada", "--motivo", "m", *EVID]) == 3


def test_la_evidencia_citada_debe_poder_comprobarse(project):
    _new_opportunity(project)
    base = ["--agente", "compuestos-toxicologia", "puntuar", "compuerta", "O-0001", "seguridad", "superada",
            "--motivo", "m"]
    assert _code([*base, "--evidencia", ""]) == 3
    assert _code([*base, "--evidencia", "A-0099"]) == 3                         # ID del registro inexistente
    assert _code([*base, "--evidencia", "doi:10.1234/no.registrada"]) == 3
    assert _code([*base, "--evidencia", "research/revisiones/no-existe.md"]) == 3
    assert _code([*base, "--evidencia", "análisis de triage"]) == 3              # texto libre: no se puede comprobar
    assert db.list_gates(_conn()) == []
    (project / "research" / "revisiones" / "O-0001_seguridad.md").write_text("análisis", "utf-8")
    source = db.upsert_source(_conn(), {"doi": "10.1234/evidencia", "title": "Fuente", "year": 2020, "verified": True,
                                        "verified_via": "prueba", "retracted": False})
    evidence = ["research/revisiones/O-0001_seguridad.md", source, "https://eur-lex.europa.eu/eli/reg/2009/767/oj"]
    assert _code([*base, "--evidencia", *evidence]) == 0
    assert db.list_gates(_conn())[0]["evidence"] == evidence
    assert _code(base) == 0                  # una propuesta sin evidencia se admite (no cuenta hasta que se firme)


def test_bloquear_nunca_falla_por_la_evidencia_y_desbloquear_la_exige(project):
    _new_opportunity(project)
    assert _code(["--agente", "critico-red-team", "puntuar", "compuerta", "O-0001", "seguridad", "bloqueada",
                  "--motivo", "señal de hepatotoxicidad", "--evidencia", "nota sin formato", "A-0099"]) == 0
    assert db.effective_gates(_conn(), "O-0001", ["seguridad"])["seguridad"] == "bloqueada"
    unblock = ["puntuar", "compuerta", "O-0001", "seguridad", "no_resuelta", "--motivo", "revisado", SIGN, "Ana"]
    assert _code(unblock) == 3                                               # sin evidencia
    assert _code([*unblock, "--evidencia"]) == 3                             # lista de evidencia vacía
    assert _code([*unblock, "--evidencia", "A-0099"]) == 3                   # evidencia inexistente
    assert db.effective_gates(_conn(), "O-0001", ["seguridad"])["seguridad"] == "bloqueada"
    assert _code([*unblock, *EVID]) == 0
    assert db.effective_gates(_conn(), "O-0001", ["seguridad"])["seguridad"] == "no_resuelta"


def test_una_propuesta_sin_firma_no_sustituye_la_decision_firmada_salvo_bloqueada(project, capsys):
    _new_opportunity(project)
    assert _code(["puntuar", "compuerta", "O-0001", "etica", "superada", "--motivo", "ok", *EVID, SIGN, "Ana"]) == 0
    assert db.effective_gates(_conn(), "O-0001", ["etica"])["etica"] == "superada"
    capsys.readouterr()
    for state, extra in (("superada", EVID), ("no_resuelta", [])):     # ni siquiera el rol propietario
        assert _code(["--agente", "sostenibilidad-bienestar", "puntuar", "compuerta", "O-0001", "etica", state,
                      "--motivo", "nuevo análisis", *extra]) == 3
    assert "ya la decidió y firmó Ana" in capsys.readouterr().err
    gate = db.list_gates(_conn(), "O-0001")[0]
    assert (gate["status"], gate["human_signoff"]) == ("superada", "Ana")
    # la alarma 'bloqueada' sí actúa de inmediato, la proponga quien la proponga, y queda dicho a quién sustituye
    assert _code(["--agente", "critico-red-team", "puntuar", "compuerta", "O-0001", "etica", "bloqueada",
                  "--motivo", "conflicto de bienestar no evaluado"]) == 0
    assert db.effective_gates(_conn(), "O-0001", ["etica"])["etica"] == "bloqueada"
    assert "sustituye la decisión firmada por Ana" in db.list_decisions(_conn(), "O-0001")[-1]["decision"]


# ============================================================================ puntuaciones (D2-1)
def test_solo_el_propio_evaluador_registra_y_sustituye_sus_puntuaciones(project, monkeypatch, capsys):
    _new_opportunity(project)
    first = _scores_file(project, "evaluador-1", 0.25)
    assert _code(["puntuar", "registrar", "--datos", first]) == 3                 # sin identificarse
    assert _code(["--agente", "critico-red-team", "puntuar", "registrar", "--datos", first]) == 3
    assert _code(["--agente", "director-cientifico", "puntuar", "registrar", "--datos", first]) == 3
    assert _code(["--agente", "evaluador-2", "puntuar", "registrar", "--datos", first]) == 3    # suplanta a otro
    assert db.list_scores(_conn(), "O-0001") == []
    assert _code(["--agente", "evaluador-1", "puntuar", "registrar", "--datos", first]) == 0
    overwrite = _scores_file(project, "evaluador-1", 1.0)
    assert _code(["--agente", "evaluador-2", "puntuar", "registrar", "--datos", overwrite]) == 3
    assert {s["value"] for s in db.list_scores(_conn(), "O-0001")} == {0.25}
    monkeypatch.setenv("NUTRIA_AGENT", "evaluador-1")      # el reintento del propio evaluador sí sustituye
    assert _code(["puntuar", "registrar", "--datos", overwrite]) == 0
    assert {s["value"] for s in db.list_scores(_conn(), "O-0001")} == {1.0}
    history = [dict(r) for r in _conn().execute("SELECT * FROM scores_history")]
    assert len(history) == 9 and all(h["replaced_by"] == "evaluador-1" and h["value"] == 0.25 for h in history)
    assert "nadie registra ni sobrescribe" in capsys.readouterr().err


def test_un_evaluador_identificado_por_nutria_agent_tampoco_ve_puntuaciones(project, monkeypatch, capsys):
    _new_opportunity(project)
    assert _code(["--agente", "evaluador-1", "puntuar", "registrar", "--datos",
                  _scores_file(project, "evaluador-1", 0.5)]) == 0
    monkeypatch.setenv("NUTRIA_AGENT", "evaluador-2")
    capsys.readouterr()
    assert _code(["--json", "oportunidad", "ver", "O-0001"]) == 0
    assert "puntuaciones" not in json.loads(capsys.readouterr().out)
    assert _code(["procedencia"]) not in (0, None)


# ============================================================================ decisiones (D3-11, D3-12)
def test_una_decision_exige_que_la_entidad_exista_y_deja_evento(project):
    _new_opportunity(project)
    _new_hypothesis(project, "O-0001")
    n = len(db.list_decisions(_conn()))
    agent = ["--agente", "director-cientifico", "decision", "registrar"]
    assert _code([*agent, "--entidad", "O-9999", "--decision", "seleccionada", "--motivo", "m"]) == 3
    assert _code([*agent, "--entidad", "", "--decision", "seleccionada", "--motivo", "m"]) == 3
    assert _code([*agent, "--entidad", "O-0001", "--decision", " ", "--motivo", "m"]) == 3
    assert len(db.list_decisions(_conn())) == n
    assert _code([*agent, "--entidad", "O-0001", "--decision", "propuesta de descarte", "--motivo", "m"]) == 0
    assert _code([*agent, "--entidad", "H-0001", "--decision", "revisar", "--motivo", "m"]) == 0
    assert _code([*agent, "--decision", "propuesta top-3 M1", "--motivo", "m"]) == 0     # decisión general
    events = [e for e in _events(project) if e["action"] == "decision"]
    assert [e["inputs"]["entidad"] for e in events] == ["O-0001", "H-0001", None]
    assert all(e["actor"] == "director-cientifico" and e["outputs"]["firmada"] is False for e in events)
    assert db.list_decisions(_conn(), "O-0001")[-1]["context"] == "terminal"


def test_decisiones_historial_y_autoria_son_solo_de_insercion(project):
    _new_opportunity(project)
    for value in (0.25, 0.5):
        assert _code(["--agente", "evaluador-1", "puntuar", "registrar", "--datos",
                      _scores_file(project, "evaluador-1", value)]) == 0
    assert _code(["autoria", "registrar", "--entidad", "O-0001", "--persona", "Ana", "--aportacion", "X + Y",
                  SIGN, "Ana"]) == 0
    conn = _conn()
    for sql in ("UPDATE decisions SET rationale = 'x'", "UPDATE decisions SET human_signoff = 'Dra. Inventada'",
                "DELETE FROM decisions", "UPDATE scores_history SET value = 1", "DELETE FROM scores_history",
                "UPDATE inventive_contributions SET person = 'otra'", "DELETE FROM inventive_contributions"):
        with pytest.raises(sqlite3.IntegrityError, match="solo de inserción"):
            conn.execute(sql)


def test_las_bases_de_datos_existentes_se_migran(tmp_path):
    path = tmp_path / "anterior.sqlite"
    old = sqlite3.connect(path)
    old.executescript("CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, entity_id TEXT, "
                      "decision TEXT NOT NULL, rationale TEXT NOT NULL, decided_by TEXT, human_signoff TEXT);"
                      "INSERT INTO decisions (ts, decision, rationale) VALUES ('2026-01-01', 'd', 'r');")
    old.commit()
    old.close()
    conn = db.connect(path)
    assert "context" in {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
    triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert {"decisions_sin_update", "decisions_sin_delete", "scores_history_sin_delete"} <= triggers
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM decisions")


# ============================================================================ procedencia (D3-11, D3-12, D9-7)
def test_cada_evento_lleva_cadena_contexto_git_y_entorno(project):
    _new_opportunity(project)
    assert _code(["--agente", "director-cientifico", "decision", "registrar", "--entidad", "O-0001", "--decision", "d",
                  "--motivo", "m"]) == 0
    events = _events(project)
    assert events[0]["prev"] == provenance.GENESIS
    assert all(b["prev"] == a["hash"] for a, b in zip(events, events[1:]))
    for ev in events:
        assert ev["hash"] == provenance.event_hash(ev)
        assert ev["contexto"] == "terminal"
        assert set(ev["git"]) == {"commit", "cambios_sin_confirmar"}
        assert ev["entorno"]["python"] == sys.version.split()[0] and "numpy" in ev["entorno"]
    assert _code(["procedencia", "verificar"]) == 0


def test_el_contexto_de_claude_code_queda_en_eventos_y_decisiones(project, monkeypatch):
    _new_opportunity(project)
    assert "sesion_claude_code" not in _events(project)[-1]
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "0f3c-sesion-de-prueba")
    monkeypatch.setattr(provenance, "_PROCESS_CACHE", {})
    assert _code(["--agente", "director-cientifico", "decision", "registrar", "--entidad", "O-0001", "--decision", "d",
                  "--motivo", "m"]) == 0
    event = _events(project)[-1]
    assert event["contexto"] == "claude-code"
    assert event["sesion_claude_code"] == "0f3c-sesion-de-prueba"   # enlaza con la transcripción (agente y modelo)
    assert db.list_decisions(_conn(), "O-0001")[-1]["context"] == "claude-code"


def test_estados_y_compuertas_dejan_evento(project):
    _new_opportunity(project)
    _new_hypothesis(project, "O-0001")
    assert _code(["--agente", "fisiologia-bioquimica", "hipotesis", "estado", "H-0001", "refutada", "--motivo",
                  "degradación ruminal completa"]) == 0
    assert _code(["--agente", "compuestos-toxicologia", "puntuar", "compuerta", "O-0001", "seguridad", "bloqueada",
                  "--motivo", "señal"]) == 0
    events = _events(project)
    estado = next(e for e in events if e["action"] == "cambio_estado")
    assert estado["inputs"] == {"entidad": "H-0001", "estado": "refutada", "motivo": "degradación ruminal completa"}
    gate = next(e for e in events if e["action"] == "compuerta")
    assert gate["actor"] == "compuestos-toxicologia" and gate["outputs"]["efectiva"] == "bloqueada"


def _seed_events(project: Path, n: int = 4) -> Path:
    _new_opportunity(project)
    for i in range(n):
        assert _code(["--agente", "director-cientifico", "decision", "registrar", "--entidad", "O-0001", "--decision",
                      f"decisión {i}", "--motivo", "m"]) == 0
    return project / "research" / "procedencia.jsonl"


ATAQUES = {
    "alterar": "alterados",
    "recalcular_su_huella": "cadena_rota",
    "borrar": "cadena_rota",
    "reordenar": "cadena_rota",
    "insertar_corrupta": "lineas_corruptas",
    "anadir_sin_cadena": "sin_cadena_tras_inicio",
}


@pytest.mark.parametrize("ataque", list(ATAQUES))
def test_verificar_detecta_alteraciones_de_la_procedencia(project, capsys, ataque):
    path = _seed_events(project)
    lines = path.read_text("utf-8").splitlines()
    forged = json.loads(lines[2])
    forged.update(actor="humano", action="revision_humana")
    if ataque == "alterar":
        lines[2] = json.dumps(forged, ensure_ascii=False, sort_keys=True)
    elif ataque == "recalcular_su_huella":
        forged["hash"] = provenance.event_hash(forged)
        lines[2] = json.dumps(forged, ensure_ascii=False, sort_keys=True)
    elif ataque == "borrar":
        del lines[2]
    elif ataque == "reordenar":
        lines[2], lines[3] = lines[3], lines[2]
    elif ataque == "insertar_corrupta":
        lines.insert(2, '{"ts": "2026-01-01T00:00:00+00:00", "actor": "humano"')
    else:   # el evento forjado de la auditoría, con el formato de las versiones anteriores
        lines.append(json.dumps({"ts": "2026-01-01T00:00:00+00:00", "actor": "humano", "action": "revision_humana",
                                 "inputs": {}, "outputs": {}, "nutria": "0.1.0"}))
    path.write_text("\n".join(lines) + "\n", "utf-8")
    res = provenance.verify_chain(load_config())
    assert not res["integra"] and res[ATAQUES[ataque]]
    capsys.readouterr()
    assert _code(["procedencia", "verificar"]) == 1
    assert "NO íntegra" in capsys.readouterr().out
    assert _code(["--json", "procedencia", "verificar"]) == 1
    assert json.loads(capsys.readouterr().out)["integra"] is False


def test_las_lineas_corruptas_se_cuentan_e_informan(project, capsys):
    path = _seed_events(project, 1)
    with open(path, "ab") as fh:
        fh.write(b'{"ts": "escritura interrumpida')          # sin salto de línea final
    n_lines = len(path.read_text("utf-8").splitlines())
    with pytest.warns(provenance.ProcedenciaCorruptaWarning):
        events = provenance.read_events(load_config())
    assert events.corruptas == [n_lines]
    capsys.readouterr()
    assert _code(["procedencia"]) == 0
    assert "línea(s) corrupta(s)" in capsys.readouterr().err
    # el siguiente evento empieza en una línea nueva y se encadena con el último evento válido
    assert _code(["--agente", "director-cientifico", "decision", "registrar", "--decision", "d", "--motivo", "m"]) == 0
    assert json.loads(path.read_text("utf-8").splitlines()[-1])["action"] == "decision"
    res = provenance.verify_chain(load_config())
    assert res["lineas_corruptas"] == [n_lines] and not res["cadena_rota"] and not res["alterados"]


def test_los_eventos_anteriores_a_la_cadena_quedan_anclados(tmp_path):
    cfg = Config(root=tmp_path)
    legacy = [{"ts": "2026-01-01T00:00:00+00:00", "actor": "cli", "action": "init", "inputs": {}, "outputs": {},
               "nutria": "0.1.0"},
              {"ts": "2026-01-02T00:00:00+00:00", "actor": "director-cientifico", "action": "busqueda",
               "inputs": {"consulta": "rumen"}, "outputs": {}, "nutria": "0.1.0"}]
    cfg.provenance_path.parent.mkdir(parents=True)
    cfg.provenance_path.write_text("".join(json.dumps(e) + "\n" for e in legacy), "utf-8")
    prefix = cfg.provenance_path.read_bytes()
    res = provenance.verify_chain(cfg, use_git=False)
    assert not res["integra"] and "sin cadena" in res["problemas"][0]
    event = provenance.log_event(cfg, "prueba")
    assert event["prev"] == hashlib.sha256(prefix).hexdigest()
    res = provenance.verify_chain(cfg, use_git=False)
    assert res["integra"] and res["anteriores_a_la_cadena"] == 2 and res["avisos"]
    tampered = cfg.provenance_path.read_text("utf-8").replace('"actor": "cli"', '"actor": "humano"', 1)
    cfg.provenance_path.write_text(tampered, "utf-8")
    res = provenance.verify_chain(cfg, use_git=False)
    assert not res["integra"] and res["cadena_rota"] == [3]


def test_las_escrituras_concurrentes_mantienen_la_cadena(tmp_path):
    script = tmp_path / "escribe.py"
    script.write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(SRC)!r})
        from pathlib import Path
        from nutria.config import Config
        from nutria.provenance import log_event
        cfg = Config(root=Path({str(tmp_path)!r}))
        for i in range(30):
            log_event(cfg, "concurrente", inputs={{"i": i, "proceso": sys.argv[1]}})
    """), "utf-8")
    procs = [subprocess.Popen([sys.executable, str(script), str(k)]) for k in range(4)]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0, 0]
    res = provenance.verify_chain(Config(root=tmp_path), use_git=False)
    assert res["integra"] and res["encadenados"] == 120


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", "-c", "user.email=prueba@example.org", "-c", "user.name=Prueba", *args], cwd=cwd,
                         check=True, capture_output=True, text=True, timeout=60)
    return out.stdout.strip()


@requires_git
def test_verificar_compara_con_la_version_confirmada_en_git(project):
    _seed_events(project, 1)
    path = project / "research" / "procedencia.jsonl"
    _git(project, "init", "-q")
    _git(project, "add", "research/procedencia.jsonl")
    _git(project, "commit", "-q", "-m", "procedencia")
    assert _code(["--agente", "director-cientifico", "decision", "registrar", "--decision", "d", "--motivo", "m"]) == 0
    res = provenance.verify_chain(load_config())
    assert res["integra"] and res["git"]["estado"] == "amplia_lo_confirmado" and res["git"]["eventos_nuevos"] == 1
    # quien reescribe todo el fichero con una cadena recalculada no rompe la cadena, pero sí la versión confirmada
    other = Config(root=project / "otra")
    provenance.log_event(other, "init")
    provenance.log_event(other, "revision_humana", actor="humano")
    shutil.copyfile(other.provenance_path, path)
    res = provenance.verify_chain(load_config())
    assert not res["alterados"] and not res["cadena_rota"]
    assert not res["integra"] and res["git"]["estado"] == "reescrito"


@requires_git
def test_el_estado_de_git_marca_cambios_sin_confirmar_fuera_de_research(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "research").mkdir()
    (tmp_path / "src" / "codigo.py").write_text("x = 1\n", "utf-8")
    (tmp_path / "research" / "datos.txt").write_text("a\n", "utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "inicial")
    commit = _git(tmp_path, "rev-parse", "HEAD")

    def state():
        monkeypatch.setattr(provenance, "_PROCESS_CACHE", {})
        return provenance.git_state(tmp_path)

    assert state() == {"commit": commit, "cambios_sin_confirmar": False}
    (tmp_path / "research" / "datos.txt").write_text("b\n", "utf-8")        # los datos de nutria no cuentan
    assert state()["cambios_sin_confirmar"] is False
    (tmp_path / "src" / "codigo.py").write_text("x = 2\n", "utf-8")
    assert state()["cambios_sin_confirmar"] is True


def test_log_event_admite_parametros_y_huellas_de_entrada_y_salida(tmp_path):
    cfg = Config(root=tmp_path)
    data = tmp_path / "datos.csv"
    data.write_text("estudio,yi,vi\nA,1,0.1\n", "utf-8")
    result = provenance.sha256_json({"estimacion": 1.0})
    event = provenance.log_event(cfg, "metaanalisis", inputs={"csv": str(data)},
                                 params={"metodo": "REML", "api_key": "secreto"},
                                 input_files=[data, tmp_path / "falta.csv"], output_hashes={"resultado": result})
    assert event["parametros"] == {"metodo": "REML", "api_key": "***"}
    assert event["huellas"] == {"entrada": {str(data): provenance.sha256_file(data), str(tmp_path / "falta.csv"): None},
                                "salida": {"resultado": result}}
    assert provenance.verify_chain(cfg, use_git=False)["integra"]


# ============================================================================ autoría inventiva (D1-11)
def test_la_autoria_inventiva_exige_firma_humana_y_queda_registrada(project, monkeypatch, capsys):
    _new_opportunity(project)
    contribution = "Combinar el compuesto X protegido con Y en el preparto"
    base = ["autoria", "registrar", "--entidad", "O-0001", "--persona", "Ana Pérez", "--aportacion", contribution]
    assert _code(base) == 3                                                     # sin firma
    assert _code(["autoria", "registrar", "--entidad", "O-9999", "--persona", "Ana Pérez", "--aportacion",
                  contribution, SIGN, "Ana Pérez"]) == 3                        # entidad inexistente
    assert _code([*base, SIGN, "Ana Pérez"]) == 0
    row = db.list_inventive_contributions(_conn(), "O-0001")[0]
    assert (row["person"], row["contribution"], row["signed_by"], row["context"]) == (
        "Ana Pérez", contribution, "Ana Pérez", "terminal")
    assert db.list_decisions(_conn(), "O-0001")[-1]["human_signoff"] == "Ana Pérez"
    event = [e for e in _events(project) if e["action"] == "autoria"][-1]
    assert contribution not in json.dumps(event, ensure_ascii=False)          # en la procedencia, solo la huella
    assert event["huellas"]["entrada"]["aportacion"] == provenance.sha256_text(contribution)
    capsys.readouterr()
    assert _code(["--json", "autoria", "listar", "--entidad", "O-0001"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["contribution"] == contribution
    monkeypatch.setenv("CLAUDECODE", "1")
    assert _code([*base, SIGN, "Ana Pérez"]) == 3
    assert len(db.list_inventive_contributions(_conn())) == 1


# ============================================================================ hook de WebFetch y WebSearch (D7-7)
def _web(tool: str, tool_input: dict, response) -> dict:
    return {"session_id": "sesion-1", "transcript_path": "/x", "cwd": "/x", "hook_event_name": "PostToolUse",
            "tool_name": tool, "tool_input": tool_input, "tool_response": response, "tool_use_id": "toolu_01"}


def _run_web_hook(project_dir: Path, payload: dict, **env) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if k not in ("NUTRIA_HOME", "NUTRIA_PROCEDENCIA_WEB")}
    full.update(CLAUDE_PROJECT_DIR=str(project_dir), **env)
    return subprocess.run(["sh", str(HOOKS / "registrar_web.sh")], input=json.dumps(payload), capture_output=True,
                          text=True, timeout=60, env=full)


def test_el_hook_web_registra_la_url_y_la_huella_sin_el_contenido(tmp_path):
    url = "https://eur-lex.europa.eu/eli/reg/2009/767/oj"
    page = "Texto íntegro de la página consultada"
    payload = _web("WebFetch", {"url": url, "prompt": "¿Qué dice el artículo 13?"},
                   {"result": page, "code": 200, "durationMs": 812})
    res = _run_web_hook(tmp_path, payload)
    assert res.returncode == 0 and res.stdout.strip() == ""
    cfg = Config(root=tmp_path)
    event = provenance.read_events(cfg)[-1]
    assert event["action"] == "web_fetch" and event["actor"] == "claude-code"
    assert event["inputs"]["url"] == url and event["inputs"]["sesion"] == "sesion-1"
    assert event["inputs"]["id_llamada"] == "toolu_01"
    assert event["outputs"]["sha256_respuesta"] == provenance.sha256_json({"result": page, "code": 200})
    text = cfg.provenance_path.read_text("utf-8")
    assert page not in text and "artículo 13" not in text
    payload["tool_response"]["durationMs"] = 15                     # misma respuesta, otra duración: misma huella
    assert _run_web_hook(tmp_path, payload).returncode == 0
    first, second = provenance.read_events(cfg)[-2:]
    assert first["outputs"]["sha256_respuesta"] == second["outputs"]["sha256_respuesta"]
    assert provenance.verify_chain(cfg, use_git=False)["integra"]


def test_el_hook_web_registra_las_busquedas(tmp_path):
    query = "rumen protected choline transition cows"
    results = {"query": query, "results": [{"title": "Título A", "url": "https://a.org"},
                                           {"title": "Título B", "url": "https://b.org"}], "durationSeconds": 1.2}
    assert _run_web_hook(tmp_path, _web("WebSearch", {"query": query, "allowed_domains": ["efsa.europa.eu"]},
                                        results)).returncode == 0
    cfg = Config(root=tmp_path)
    event = provenance.read_events(cfg)[-1]
    assert event["action"] == "web_search" and event["inputs"]["consulta"] == query
    assert event["inputs"]["dominios_permitidos"] == ["efsa.europa.eu"] and event["outputs"]["resultados"] == 2
    assert "Título A" not in cfg.provenance_path.read_text("utf-8")


@pytest.mark.parametrize("modo", ["hash", "HASH", "sí"])     # un valor no reconocido falla en cerrado (huellas)
def test_el_hook_web_en_modo_hash_solo_guarda_dominio_y_huellas(tmp_path, modo):
    url = "https://patents.google.com/?q=rumen-protected+choline+carnitine"
    query = "rumen-protected choline carnitine dairy"
    assert _run_web_hook(tmp_path, _web("WebFetch", {"url": url, "prompt": "p"}, {"result": "r"}),
                         NUTRIA_PROCEDENCIA_WEB=modo).returncode == 0
    assert _run_web_hook(tmp_path, _web("WebSearch", {"query": query}, {"results": []}),
                         NUTRIA_PROCEDENCIA_WEB=modo).returncode == 0
    cfg = Config(root=tmp_path)
    fetch, search = provenance.read_events(cfg)[-2:]
    assert fetch["inputs"]["dominio"] == "patents.google.com"
    assert fetch["inputs"]["sha256_url"] == provenance.sha256_text(url) and "url" not in fetch["inputs"]
    assert search["inputs"]["sha256_consulta"] == provenance.sha256_text(query) and "consulta" not in search["inputs"]
    assert "carnitine" not in cfg.provenance_path.read_text("utf-8")


def test_el_hook_web_nunca_bloquea(tmp_path):
    not_a_dir = tmp_path / "fichero"
    not_a_dir.write_text("no es un directorio", "utf-8")
    res = _run_web_hook(not_a_dir, _web("WebFetch", {"url": "https://a.org"}, {"result": "r"}))
    assert res.returncode == 0
    assert "no quedó registrada" in json.loads(res.stdout)["systemMessage"]
    res = _run_web_hook(tmp_path, _web("Bash", {"command": "ls"}, {}))
    assert res.returncode == 0 and not (tmp_path / "research" / "procedencia.jsonl").exists()


def test_la_configuracion_registra_la_web_y_protege_el_registro():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text("utf-8"))
    web = [g for g in settings["hooks"]["PostToolUse"] if g["matcher"] == "WebFetch|WebSearch"]
    assert web and "registrar_web.sh" in web[0]["hooks"][0]["command"]
    bash = [g for g in settings["hooks"]["PreToolUse"] if g["matcher"] == "Bash"]
    assert "guard_bash.sh" in bash[0]["hooks"][0]["command"]
    assert "Bash(sqlite3 *)" in settings["permissions"]["deny"]
