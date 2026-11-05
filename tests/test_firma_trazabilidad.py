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
- D1-11: autoría inventiva humana firmada. D7-7: hook de WebFetch y WebSearch (por defecto solo huellas; la URL,
  siempre redactada).
- Revisión de la ronda 1: sin git en las pruebas que no lo piden (test_http.py intermitente), antecesores con `ps`
  donde no hay /proc, opciones y --agente construidos con variables, multiplexores y ssh -t, git que descarta cambios,
  mayúsculas en los nombres del registro, falsos positivos del hook, INSERT OR REPLACE, evaluadores normalizados,
  evidencia (ficheros regulares del proyecto, no la propia oportunidad) y aviso de integridad en el listado; además,
  nutria solo directamente en los subagentes (no desde Python, xargs ni eval), la clave con comodines bajo .config,
  el rechazo de la firma también por stderr con --json y sin traza con los flujos cerrados, y la consulta web
  redactada en claro.
- Revisión de la ronda 2: `procedencia verificar` detecta el fichero confirmado que ha desaparecido y compara con
  todas las versiones confirmadas (historial de HEAD y reflog de la rama: commit --amend, reset --soft); el hook
  reconoce su -P, script desde Python, env -C dir -i, --ignore-env, exec -a x -c, pty importado, os.execve y env={},
  comodines y find sobre el registro, abreviaturas y pathspecs de git, el reflog, cd en .config y comodines en la ruta
  de la clave; en los subagentes, --agente en cada orden nutria, sin NUTRIA_AGENT ni set -a y git solo para leer;
  órdenes largas sin agotar el tiempo del hook; correos sueltos en modo claro; el registro no es evidencia; guiones
  Unicode en los evaluadores.

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
    with pytest.raises(SystemExit):        # con --json, el error sale como JSON por stdout y, como texto, por stderr
        main(["--json", "--agente", "director-cientifico", *FIRMADAS[0]])
    out = capsys.readouterr()
    payload = json.loads(out.out)
    assert (payload["codigo"], payload["tipo"]) == (3, "registro") and "Firma humana rechazada" in payload["error"]
    assert out.err.startswith("Error de registro: Firma humana rechazada")


def test_el_rechazo_con_los_flujos_cerrados_sigue_saliendo_con_codigo_3(registered, monkeypatch):
    """Quien lee la salida puede haberla cerrado (`| head`): el rechazo no da una traza, sale con el código 3."""
    import io
    cerrado = io.StringIO()
    cerrado.close()
    monkeypatch.setattr(sys, "stdout", cerrado)
    monkeypatch.setattr(sys, "stderr", cerrado)
    with pytest.raises(SystemExit) as exc:
        main(["--json", "--agente", "director-cientifico", *FIRMADAS[0]])
    assert exc.value.code == 3 and "no la registra un agente" in str(exc.value)


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


def _fake_ps(tmp_path: Path, rows: list[tuple]) -> tuple[str, ...]:
    """Orden que imprime una tabla de procesos como `ps -A -o pid=,ppid=,tty=,args=` (macOS: '??' sin terminal)."""
    script = tmp_path / "ps_simulado.py"
    table = "".join(f"{pid:>6} {ppid:>6} {tty:<8} {args}\n" for pid, ppid, tty, args in rows)
    script.write_text(f"import sys\nsys.stdout.write({table!r})\n", "utf-8")
    return (sys.executable, str(script))


def test_sin_proc_los_antecesores_se_recorren_con_ps(registered, tmp_path, monkeypatch, capsys):
    """Revisión: sin /proc (macOS) solo se miraba el entorno del propio proceso, así que `env -i nutria ... --firma-
    humana` firmaba. Ahora la cadena de antecesores sale de `ps`: Claude Code entre ellos (también instalado con npm,
    `node .../bin/claude`) y la terminal de control. `ps` no muestra el entorno inicial de otros procesos."""
    me = os.getpid()
    agente = [(me, 4300, "??", "python3 -m nutria decision registrar"), (4300, 4200, "??", "/bin/zsh -c nutria"),
              (4200, 4100, "ttys001", "node /usr/local/bin/claude --resume"), (4100, 1, "ttys001", "-zsh")]
    monkeypatch.setattr(provenance, "PS_COMMAND", _fake_ps(tmp_path, agente))
    assert provenance.ancestor_markers() == ["Claude Code (proceso 4200)"]
    capsys.readouterr()
    assert _code(FIRMADAS[0]) == 3
    assert "Claude Code (proceso 4200)" in capsys.readouterr().err
    persona = [(me, 4100, "ttys002", "python3 -m nutria decision registrar"), (4100, 4000, "ttys002", "-zsh"),
               (4000, 1, "??", "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal")]
    monkeypatch.setattr(provenance, "PS_COMMAND", _fake_ps(tmp_path, persona))
    assert provenance.ancestor_markers() == [] and provenance.interactive_terminal() is True
    assert _code(FIRMADAS[0]) == 0
    monkeypatch.setattr(provenance, "PS_COMMAND", _fake_ps(tmp_path, [(me, 1, "??", "python3 -m nutria")]))
    assert provenance.interactive_terminal() is False                    # huérfano sin terminal: no firma
    monkeypatch.setattr(provenance, "PS_COMMAND", None)
    assert provenance.interactive_terminal() is None and provenance.ancestor_markers() == []


def test_claude_code_instalado_con_npm_tambien_se_reconoce(tmp_path):
    proc = tmp_path / "proc"
    _fake_proc(proc, 30, 20, "python3", ["python3", "-m", "nutria"], {})
    _fake_proc(proc, 20, 1, "node", ["node", "/usr/local/bin/claude", "--continue"], {})
    assert provenance.ancestor_markers(proc, pid=30) == ["Claude Code (proceso 20)"]


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
    # opción construida con variables o sustituciones (revisión: $A en lugar de 'firma')
    "nutria decision registrar --decision d --motivo m --$A-humana Ana",
    'nutria decision registrar --decision d --motivo m "--${A}" Ana',
    "nutria decision registrar --decision d --motivo m --`printf fir`ma-humana Ana",
    "x=-; nutria decision registrar --decision d --motivo m $x$x\"firma-humana\" Ana",
    "E=env; A=firma; $E -i PATH=/usr/bin:/bin tmux -L x new-session -d \"nutria decision registrar --decision d "
    "--motivo m --$A-humana Ana\"",
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
    "crontab /tmp/tarea",
    "crontab -l; crontab -r",
    # terminales propias: multiplexores, expect, ssh -t (con el entorno vacío darían una terminal sin Claude Code)
    "tmux new-session -d 'nutria estado'",
    "tmux -L x send-keys -t 0 'nutria estado' Enter",
    "screen -dmS x nutria estado",
    "/usr/bin/screen -dm sh -c 'nutria estado'",
    "cd /tmp && screen -dm nutria estado",
    "screen -S x -d -m nutria estado",
    "expect guion.exp",
    "expect < guion.exp",
    "echo 'spawn nutria estado' | expect",
    "expect -c 'spawn nutria estado'",
    "dtach -n /tmp/s nutria estado",
    "ssh -tt localhost nutria estado",
    "ssh -o RequestTTY=force localhost nutria estado",
    # revisión de la ronda 2: terminal propia y entorno vacío con formas que el hook no reconocía aunque se escriban
    # literalmente (su -P, script desde una lista de Python, env con otras opciones delante, exec -a, pty importado)
    "su -P root -c 'nutria estado'",
    "/bin/su - root -c 'nutria estado'",
    "(python3 -c \"import os; os.execve('/bin/su', ['su','-P','-','root','-c','nutria estado'], {})\" &)",
    "python3 -c \"import subprocess; subprocess.run(['su', '--pty', 'root'])\"",
    "(python3 -c \"import os; os.execve('/usr/bin/script', ['script','-qc','nutria estado','/dev/null'], "
    "{'PATH':'/usr/bin:/bin'})\" &)",
    "python3 -c \"import subprocess; subprocess.run(['script', '-qc', 'nutria estado', '/dev/null'])\"",
    "python3 -c \"__import__('pty').spawn(['sh','-c','nutria estado'])\"",
    "python3 -c \"import importlib; importlib.import_module('pty').spawn('sh')\"",
    "python3 -c \"from pty import spawn; spawn('sh')\"",
    "env --ignore-env PATH=/usr/bin:/bin python3 -m nutria estado",
    "env --i PATH=/usr/bin nutria estado",
    "env -C /tmp -i PATH=/usr/bin:/bin python3 -m nutria estado",
    "env --chdir /tmp -i PATH=/usr/bin python3 -m nutria estado",
    "env --chdir=/tmp -vi nutria estado",
    "env -S '-i PATH=/usr/bin python3 -m nutria estado'",
    "exec -a x -c python3 -m nutria estado",
    "exec -l -a x -c nutria estado",
    "python3 -c \"import os; os.execve('/usr/bin/python3', ['python3','-m','nutria','estado'], {})\"",
    "python3 -c \"import subprocess; subprocess.run(['nutria', 'estado'], env={})\"",
    "pkexec nutria estado",
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
    "cp /tmp/x research/NUTRIA.sqlite",                       # mayúsculas: el mismo fichero en macOS y Windows
    "echo x >> research/PROCEDENCIA.JSONL",
    "rm -rf research",
    "rm -rf ./research/*",
    "rsync -a --delete /tmp/vacio/ research/",
    # git que descarta cambios sin confirmar (el registro y la procedencia) sin nombrar research/
    "git stash",
    "git stash push -u",
    "git stash drop",
    "git checkout .",
    "git checkout -- .",
    "git checkout -f main",
    "git reset --hard",
    "git reset --hard HEAD~1",
    "git restore .",
    "git restore :/",
    "git clean -fdx",
    "git -C /home/u/nutria switch --discard-changes main",
    "git restore research/",                                   # un solo espacio (antes no se detectaba)
    "git rm -r research",
    "git rm --cached research/nutria.sqlite",
    "git mv research/procedencia.jsonl /tmp/p.jsonl",
    # revisión de la ronda 2: comodines que cubren el registro, find que borra research/, pathspecs mágicos y
    # abreviaturas de las opciones de git (git las acepta: reset --ha es --hard)
    "rm -f research/*.sqlite research/*.jsonl",
    "rm -f research/nutria.sq*",
    "rm research/{nutria.sqlite,x}",
    "truncate -s 0 research/proc*.jsonl",
    "shred -u research/p?ocedencia.jsonl",
    "cp /dev/null research/*.jsonl",
    "rm -f */*.sqlite",
    ": > research/proc*.jsonl",
    "cd research && rm -f *.sqlite",
    "cd research; truncate -s 0 *",
    "python3 -c \"import glob, os; [os.remove(f) for f in glob.glob('research/*.sqlite')]\"",
    "find research -name '*.sqlite' -delete",
    "find . -name 'nutria.sq*' -exec rm {} +",
    "find research -mindepth 1 -delete",
    "find research/ -type f -delete",
    "find ./research -not -name '*.md' -delete",
    "find . -path '*/research/*' -delete",
    "git restore --source=HEAD~1 '*.sqlite'",
    "git checkout HEAD~2 -- ':/research'",
    "git restore -s HEAD~2 :/research",
    "git checkout HEAD -- ':(top)research'",
    "git checkout -- ':!src'",
    "git restore :/",
    "git checkout-index -a -f",
    "git read-tree -u --reset HEAD",
    "cd research && git checkout HEAD~3 -- nutria.sq*",
    "git reset --ha",
    "git reset --har HEAD~1",
    "git reset --h",
    "git reset --merg HEAD~1",
    "git checkout --forc main",
    "git checkout -qf main",
    "git switch --discard main",
    "git switch --force main",
    "git restore --wor src/",
    # lo que borraría el reflog con el que `procedencia verificar` detecta la historia reescrita
    "git reflog expire --expire=now --all",
    "git reflog delete HEAD@{1}",
    "git -c gc.reflogExpire=now -c gc.reflogExpireUnreachable=now gc --prune=now",
    "git config core.logAllRefUpdates false",
    "git update-ref refs/heads/main HEAD~3",
    "rm -rf .git/logs",
    "rm -rf .git",
    # falsos positivos aceptados (hay alternativa): el texto '--firma' dentro de un motivo y leer la procedencia con
    # Python (usa `nutria procedencia --json` o un cat/tail simple)
    "nutria --agente director-cientifico decision registrar --decision d --motivo 'falta --firma de la persona'",
    "python3 -c \"print(open('research/procedencia.jsonl').read())\"",
    # sellos
    "cat ~/.config/nutria/clave_sello",
    "cat ~/.config/nutria/*",
    "ls ~/.config/NUTRIA/",
    "cat ~/.config/*/*",                                      # comodines y rutas equivalentes (ronda 2)
    "cat ~/.config/n?tria/c*",
    "cat ~/.config/./nutria/k",
    "cat ~/.config//nutria/k",
    "cat \"${XDG_CONFIG_HOME:-$HOME/.config}\"/nut*/*",
    "cd ~/.config && cat nutria/*",                           # ronda 3: cd en .config y comodines en la ruta
    "pushd $HOME/.config; cat nutria/k",
    "cat ~/.con*/nut*/*",
    "cat ~/.c?nfig/n*/c*",
    "head -c 100 ~/.*/nutria/k",
    "grep -r HMAC ~/.config",
    "find ~/.config -type f",
    "tar czf /tmp/c.tgz ~/.config/",
    "find / -name 'clave*'",
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
    # falsos positivos de la revisión
    "git mv research/revisiones/a.md research/revisiones/b.md",
    "grep -rn sudo docs/",
    "grep -rn start_new_session src/",
    "crontab -l",
    "grep -rn CLAUDE_CODE_ENTRYPOINT src/",
    "PYTHONPATH=src python3 -m pytest -q tests/ && ls -la research/nutria.sqlite",
    "grep -n 'from nutria import' src/nutria/db.py",
    "printf '\\033[1m%s\\033[0m\\n' \"$(nutria estado)\"",
    "cat research/planes/plan-firma-humana.md",
    # otras órdenes legítimas
    "git stash list",
    "git stash show -p",
    "git checkout -b nueva-rama",
    "git checkout main",
    "git rm research/revisiones/obsoleta.md",
    "git restore --staged src/nutria/cli.py",
    "nutria buscar pubmed \"high-throughput screen of rumen modifiers: what to expect\" --max 25",
    "nutria buscar europepmc \"(screen OR screening) AND rumen; expect (cattle)\" --max 25",
    "tail -n 3 research/PROCEDENCIA.JSONL",
    "head -c 200 research/procedencia.jsonl",
    "cat research/nutria.sqlite | head -c 16",
    "nutria --agente desarrollo-industrial puntuar compuerta O-0001 analitica superada --motivo m --datos=$F",
    # ronda 3: lo que no debe confundirse con las reglas nuevas (su, script, env, exec, comodines, git, .config)
    "nutria --agente director-cientifico decision registrar --entidad O-0001 --decision 'ajustar su dosis' "
    "--motivo 'su efecto depende de la dieta basal'",
    "nutria buscar pubmed \"script-based screening of rumen modifiers\" --max 25",
    "nutria buscar pubmed \"(su OR 'sulfonylurea') AND rumen\" --max 25",
    "env LC_ALL=C grep -i rumen notas.md | head",
    "env PYTHONPATH=src python3 -i -c 'print(1)' </dev/null",
    "exec 3>&1; nutria estado",
    "rm -f /tmp/x/*.sqlite",
    "rm -rf build/* dist/*",
    "rm -f *.log",
    "cp research/revisiones/*.md /tmp/copia/",
    "ls research/*.jsonl > /tmp/lista.txt",
    "find . -name '*.pyc' -delete",
    "find research/datos/tmp -type f -delete",
    "find research -name '*.md' -newer x",
    "git restore --staged src/nutria/cli.py",
    "git checkout -- 'src/*.py'",
    "git log --all --oneline -- research/",
    "git reset --soft HEAD~1",                         # reescribe la historia sin descartar cambios: lo detecta verificar
    "git commit --amend -m 'mensaje corregido'",
    "git reflog -n 5",
    "ls -la ~/.config/",
    "cat ~/.config/git/config",
    "ls ~/.*rc",
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
    # el valor de --agente construido con variables o sustituciones (revisión)
    ("economia-mercado-pi", "R=compuestos-toxicologia; nutria --agente $R puntuar compuerta O-0001 seguridad superada "
                            "--motivo x"),
    ("economia-mercado-pi", "nutria --agente $(echo compuestos-toxicologia) puntuar compuerta O-0001 seguridad "
                            "superada --motivo x"),
    ("economia-mercado-pi", "nutria --agente=COMPUESTOS-TOXICOLOGIA puntuar compuerta O-0001 seguridad superada "
                            "--motivo x"),
    ("critico-red-team", "E=evaluador-1; nutria --agente $E puntuar registrar --datos p.json"),
    ("critico-red-team", "nutria --agente \"${E}\" puntuar registrar --datos p.json"),
    ("critico-red-team", "nutria --agente `echo evaluador-1` puntuar registrar --datos p.json"),
    # nutria a través de otra orden que construye sus argumentos (ronda 2): Python, xargs, eval
    ("economia-mercado-pi", "python3 -c \"import subprocess; R='compuestos-toxicologia'; subprocess.run(['nutria', "
                            "'--agente', R, 'puntuar', 'compuerta', 'O-0001', 'seguridad', 'superada', '--motivo', 'x'])\""),
    ("economia-mercado-pi", "python3 -c \"from nutria.cli import main; main(['--agente', 'economia-mercado-pi', "
                            "'estado'])\""),
    ("economia-mercado-pi", "echo compuestos-toxicologia | xargs -I{} nutria --agente {} puntuar compuerta O-0001 "
                            "seguridad superada --motivo x"),
    ("critico-red-team", "eval \"nutria --agente $(cat rol.txt) puntuar registrar --datos p.json\""),
    ("critico-red-team", "python3 -m nutria --agente evaluador-1, puntuar registrar --datos p.json"),
    ("economia-mercado-pi", "nutria --agente compuestos-toxicologia* puntuar compuerta O-0001 seguridad superada "
                            "--motivo x"),
    # el evaluador independiente: siempre --agente evaluador-<n>, sin módulos de Python ni la base de datos
    ("evaluador-independiente", "nutria oportunidad ver O-0001 --json"),
    ("evaluador-independiente", "PYTHONPATH=src python3 -m nutria --json oportunidad ver O-0001"),
    ("evaluador-independiente", "nutria --agente director-cientifico puntuar ranking"),
    ("evaluador-independiente", "python3 -c \"from nutria.cli import main; main(['puntuar', 'ranking'])\""),
    ("evaluador-independiente", "strings research/nutria.sqlite | grep evaluador"),
    # ronda 3: NUTRIA_AGENT fijado sin escribir `NUTRIA_AGENT=` (set -a, read, printf -v, export de un nombre
    # construido) o leído de un fichero: cada orden nutria de un subagente lleva --agente, que prevalece
    ("critico-red-team", "set -a; read NUTRIA_AGENT <<< evaluador-1; nutria puntuar registrar --datos p.json"),
    ("critico-red-team", "set -a; printf -v NUTRIA_AGENT evaluador-1; nutria puntuar registrar --datos p.json"),
    ("critico-red-team", "V=NUTRIA_AGENT; export $V=evaluador-1; nutria puntuar registrar --datos p.json"),
    ("critico-red-team", "V=NUTRIA_; export ${V}AGENT=evaluador-1; nutria --agente critico-red-team estado"),
    ("critico-red-team", "V=NUTRIA_; read ${V}AGENT <<< x; nutria --agente critico-red-team estado"),
    ("critico-red-team", "set -o allexport; . ./vars.sh; nutria --agente critico-red-team estado"),
    ("critico-red-team", "env \"$(printf NUTRIA_)AGENT=evaluador-1\" python3 -m nutria --agente critico-red-team estado"),
    ("critico-red-team", "f() { nutria \"$@\"; }; f puntuar registrar --datos p.json"),
    ("critico-red-team", ". ./vars.sh && nutria puntuar registrar --datos p.json"),
    ("critico-red-team", "nutria oportunidad ver O-0001 --sin-puntuaciones --json"),     # antes permitido: sin --agente
    ("critico-red-team", "echo \"$(nutria estado)\"; nutria --agente critico-red-team estado"),
    ("critico-red-team", "X=nutria; $X puntuar registrar --datos p.json"),
    # git: un subagente no reescribe la historia ni mueve referencias
    ("critico-red-team", "git commit --amend -a --no-edit"),
    ("critico-red-team", "git reset --soft HEAD~3"),
    ("critico-red-team", "git push --force origin main"),
    ("critico-red-team", "git -C /home/u/nutria rebase -i HEAD~3"),
    ("critico-red-team", "git checkout main"),
])
def test_el_hook_ata_la_identidad_al_tipo_de_subagente(agent_type, command):
    res = _hook("guard_bash.sh", _subagente(agent_type, command))
    assert res.returncode == 2, (agent_type, command)
    assert "Bloqueado por el hook de NUTRIA" in res.stderr


@pytest.mark.parametrize("agent_type,command", [
    ("compuestos-toxicologia", "nutria --agente compuestos-toxicologia puntuar compuerta O-0001 seguridad superada --motivo x"),
    ("extractor-datos", "nutria --agente extractor-datos-A extraccion depositar --datos a.json"),
    ("general-purpose", "nutria --agente orquestador --json oportunidad ver O-0001 --sin-puntuaciones"),
    ("critico-red-team", "nutria --agente critico-red-team oportunidad ver O-0001 --sin-puntuaciones --json"),
    ("critico-red-team", "git status && git log --oneline -5 && git diff --stat"),
    ("critico-red-team", "grep -rn colina research/revisiones/ | head -20"),
    ("critico-red-team", "nutria --agente critico-red-team buscar pubmed \"$(cat research/revisiones/q.txt)\" --max 25"),
    ("critico-red-team", "while read -r doi; do nutria --agente critico-red-team verificar --doi \"$doi\"; done "
                         "< research/revisiones/dois.txt"),
    ("evaluador-independiente", "nutria --agente evaluador-2 --json oportunidad ver O-0001 --sin-puntuaciones"),
    ("evaluador-independiente", "sha256sum research/oportunidades/O-0001_puntuacion_evaluador-2.json"),
    ("evaluador-independiente", "nutria --agente evaluador-2 --json puntuar registrar --datos "
                                "research/oportunidades/O-0001_puntuacion_evaluador-2.json"),
    ("director-cientifico", "for f in research/oportunidades/*.json; do nutria --agente director-cientifico "
                            "oportunidad nueva --datos \"$f\"; done"),
    ("fisiologia-bioquimica", "(cd research && nutria --agente fisiologia-bioquimica destino ruminal --a 0.2 --b 0.6 "
                              "--kd 0.08 --kp 0.06)"),
    ("investigador-bibliografico", "nutria --agente investigador-bibliografico buscar pubmed \"choline AND "
                                   "(dairy OR cattle)\" --max 25 --desde 2010"),
])
def test_el_hook_deja_a_cada_subagente_usar_su_rol(agent_type, command):
    res = _hook("guard_bash.sh", _subagente(agent_type, command))
    assert res.returncode == 0, res.stderr


def test_sin_tipo_de_subagente_el_hook_no_comprueba_la_identidad():
    """En el hilo principal no hay agent_type: --agente es una declaración (residuo declarado)."""
    command = "nutria --agente compuestos-toxicologia puntuar compuerta O-0001 seguridad superada --motivo x"
    assert _hook("guard_bash.sh", _bash(command)).returncode == 0


@pytest.mark.parametrize("command,where,blocked", [
    ("rm -rf r*", "proyecto", True),           # research/ entera, desde el directorio del proyecto
    ("rm -rf *", "proyecto", True),
    ("mv * /tmp/x/", "proyecto", True),
    ("find . -delete", "proyecto", True),
    ("rm -f *.sqlite", "research", True),      # el registro, desde research/
    ("rm -rf r*", "otro", False),               # en otro directorio, los mismos patrones no tocan el registro
    ("rm -rf *", "otro", False),
    ("rm -f *.sqlite", "otro", False),
    ("find . -delete", "otro", False),
    ("rm -rf {dir}/r*", "otro", True),          # con la ruta del proyecto delante
    ("python3 -c \"import glob, os; [os.remove(f) for f in glob.glob('*')]\"", "proyecto", True),
    ("python3 -c \"open('out.txt', 'w').write('a*b')\"", "proyecto", False),   # el * no es un patrón de ficheros
    ("cp -r research/* /tmp/copia/", "proyecto", False),                        # cp solo escribe en su destino
    ("cp /dev/null research/*.jsonl", "proyecto", True),
])
def test_los_comodines_cuentan_segun_el_directorio_de_trabajo(tmp_path, command, where, blocked):
    """Revisión (ronda 2): `rm -f research/*.jsonl`, `find research -mindepth 1 -delete`... pasaban el hook. Un patrón
    sin directorio solo puede cubrir el registro o research/ si se ejecuta en el proyecto o en research/ (cwd de la
    entrada del hook) o si la orden cambia de directorio."""
    project_dir = tmp_path / "proyecto"
    (project_dir / "research").mkdir(parents=True)
    other = tmp_path / "otro"
    other.mkdir()
    cwd = {"proyecto": project_dir, "research": project_dir / "research", "otro": other}[where]
    payload = _bash(command.format(dir=project_dir))
    payload["cwd"] = str(cwd)
    assert _hook("guard_bash.sh", payload).returncode == (2 if blocked else 0)


def test_el_hook_no_se_agota_con_ordenes_largas_o_repetitivas():
    """Revisión: cada --agente se comprobaba con procesos aparte; con miles, el hook superaba su tiempo máximo y
    Claude Code ejecutaba la orden. Los valores repetidos se comprueban una vez, más de 20 distintos se bloquean y una
    orden de más de 100 000 caracteres no se analiza: se bloquea."""
    import time
    repeated = "nutria --agente critico-red-team estado; " * 1500 + "R=compuestos-toxicologia; nutria --agente $R estado"
    start = time.monotonic()
    res = _hook("guard_bash.sh", _subagente("critico-red-team", repeated))
    assert res.returncode == 2 and "'$R'" in res.stderr
    assert time.monotonic() - start < 8
    many = "; ".join(f"nutria --agente critico-red-team-{i} estado" for i in range(25))
    res = _hook("guard_bash.sh", _subagente("critico-red-team", many))
    assert res.returncode == 2 and "demasiados valores" in res.stderr
    res = _hook("guard_bash.sh", _bash("echo " + "a" * 100001))
    assert res.returncode == 2 and "demasiado larga" in res.stderr
    globs = "rm -f " + " ".join(f"/tmp/x{i}/*.tmp" for i in range(60))
    res = _hook("guard_bash.sh", _bash(globs))
    assert res.returncode == 2 and "demasiados patrones" in res.stderr
    start = time.monotonic()
    res = _hook("guard_bash.sh", _bash("; ".join(f"rm -f /tmp/f{i}" for i in range(60))))
    assert res.returncode == 2 and "demasiadas órdenes que borran" in res.stderr
    res = _hook("guard_bash.sh", _bash("ls " + " ".join(f"/tmp/d{i}/*.x" for i in range(120))))
    assert res.returncode == 2 and "demasiados patrones" in res.stderr
    assert time.monotonic() - start < 8
    assert _hook("guard_bash.sh", _bash("; ".join(f"rm -f /tmp/f{i}" for i in range(40)))).returncode == 0


@pytest.mark.parametrize("path,blocked", [
    ("/home/u/nutria/research/procedencia.jsonl", True),
    ("/home/u/nutria/research/nutria.sqlite", True),
    ("/home/u/nutria/research/nutria.sqlite-wal", True),
    ("/home/u/nutria/research/informes/O-0001.verificacion.json", True),
    ("/root/.config/nutria/clave_sello", True),
    ("/root/.config/nutria/otra", True),
    ("/home/u/nutria/research/PROCEDENCIA.JSONL", True),          # mayúsculas (sistemas que no las distinguen)
    ("/home/u/nutria/research/nutria.SQLITE", True),
    ("/home/u/nutria/research/informes/O-0001.VERIFICACION.json", True),
    ("/Users/u/.config/NUTRIA/clave_sello", True),
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


def test_la_evidencia_es_un_fichero_regular_del_proyecto_y_no_la_propia_oportunidad(project, tmp_path_factory):
    """Revisión: se aceptaban '.', rutas de fuera del proyecto y la propia oportunidad como evidencia."""
    _new_opportunity(project)
    outside = tmp_path_factory.mktemp("fuera_del_proyecto") / "analisis.md"
    outside.write_text("análisis ajeno", "utf-8")
    link = project / "research" / "revisiones" / "enlace.md"
    link.symlink_to(outside)
    base = ["--agente", "compuestos-toxicologia", "puntuar", "compuerta", "O-0001", "seguridad", "superada",
            "--motivo", "m"]
    for item in (".", "research", "research/revisiones", str(outside), "research/revisiones/enlace.md",
                 f"research/../../{outside.parent.name}/analisis.md", "O-0001", "o-0001"):
        assert _code([*base, "--evidencia", item]) == 3, item
    assert db.list_gates(_conn()) == []
    with pytest.raises(db.RegistryError, match="propia compuerta"):        # también por la API del registro
        db.set_gate(_conn(), "O-0001", "seguridad", "superada", "m", list(db.GATE_OWNERS), evidence=["O-0001"],
                    actor="compuestos-toxicologia")
    (project / "research" / "revisiones" / "O-0001_seguridad.md").write_text("análisis", "utf-8")
    assert _code([*base, "--evidencia", "research/revisiones/O-0001_seguridad.md",
                  str(project / "research" / "revisiones" / "O-0001_seguridad.md")]) == 0
    # desbloquear: la propia oportunidad tampoco vale como evidencia
    assert _code(["--agente", "critico-red-team", "puntuar", "compuerta", "O-0001", "seguridad", "bloqueada",
                  "--motivo", "señal"]) == 0
    assert _code(["puntuar", "compuerta", "O-0001", "seguridad", "no_resuelta", "--motivo", "revisado",
                  "--evidencia", "O-0001", SIGN, "Ana"]) == 3
    assert db.effective_gates(_conn(), "O-0001", ["seguridad"])["seguridad"] == "bloqueada"


def test_el_registro_y_el_json_de_la_oportunidad_no_son_evidencia(project):
    """Revisión (ronda 2): `--evidencia research/nutria.sqlite` (el propio registro) desbloqueaba la compuerta de
    seguridad. Tampoco valen la procedencia ni los JSON de la propia oportunidad en research/oportunidades/."""
    _new_opportunity(project)
    opp_dir = project / "research" / "oportunidades"
    opp_dir.mkdir(parents=True, exist_ok=True)
    (opp_dir / "O-0001.json").write_text("{}", "utf-8")
    (opp_dir / "O-0001_puntuacion_evaluador-1.json").write_text("{}", "utf-8")
    (opp_dir / "O-0002.json").write_text("{}", "utf-8")
    assert _code(["--agente", "critico-red-team", "puntuar", "compuerta", "O-0001", "seguridad", "bloqueada",
                  "--motivo", "señal"]) == 0
    unblock = ["puntuar", "compuerta", "O-0001", "seguridad", "no_resuelta", "--motivo", "revisado", SIGN, "Ana",
               "--evidencia"]
    for item in ("research/nutria.sqlite", str(project / "research" / "nutria.sqlite"), "research/procedencia.jsonl",
                 "research/../research/nutria.sqlite", "research/oportunidades/O-0001.json",
                 "research/oportunidades/o-0001_puntuacion_evaluador-1.json"):
        assert _code([*unblock, item]) == 3, item
    assert db.effective_gates(_conn(), "O-0001", ["seguridad"])["seguridad"] == "bloqueada"
    assert _code([*unblock, "research/oportunidades/O-0002.json"]) == 0      # otra oportunidad sí puede citarse
    assert db.effective_gates(_conn(), "O-0001", ["seguridad"])["seguridad"] == "no_resuelta"


def test_el_agente_se_guarda_sin_espacios_alrededor(project):
    """Revisión: `--agente " desarrollo-industrial "` pasaba el control de rol pero quedaba con espacios."""
    _new_opportunity(project)
    assert _code(["--agente", " desarrollo-industrial ", "puntuar", "compuerta", "O-0001", "analitica", "superada",
                  "--motivo", "m"]) == 0
    assert db.list_gates(_conn(), "O-0001")[0]["decided_by"] == "desarrollo-industrial"
    assert db.list_decisions(_conn(), "O-0001")[-1]["decided_by"] == "desarrollo-industrial"
    assert _events(project)[-1]["actor"] == "desarrollo-industrial"


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


def test_las_variantes_de_un_evaluador_cuentan_como_uno(project, capsys):
    """Revisión: con espacios o mayúsculas, un mismo evaluador contaba varias veces para el mínimo de 3 (O-0002
    quedaba CONDICIONAL en lugar de INSUFICIENTE). El identificador se normaliza al comparar y al guardar."""
    _new_opportunity(project)
    data = _example("puntuacion_ejemplo.json")
    data["oportunidad"] = "O-0001"
    for i, (agent, rater) in enumerate((("evaluador-1", "evaluador-1"), ("evaluador-1", "evaluador-1 "),
                                        ("Evaluador-1", "Evaluador-1"), (" EVALUADOR-1", "evaluador-1"),
                                        ("evaluador‐1", "evaluador-1"),          # guion U+2010 (revisión)
                                        ("ｅvaluador−1", "Evaluador–1"))):  # NFKC, menos y semirraya
        data["evaluador"] = rater
        path = _write_json(project / f"variante_{i}.json", data)
        assert _code(["--agente", agent, "puntuar", "registrar", "--datos", path]) == 0
    conn = _conn()
    assert [r[0] for r in conn.execute("SELECT DISTINCT rater FROM scores")] == ["evaluador-1"]
    assert len(db.list_scores(conn, "O-0001")) == len(data["puntuaciones"])
    assert {r[0] for r in conn.execute("SELECT DISTINCT replaced_by FROM scores_history")} == {"evaluador-1"}
    capsys.readouterr()
    assert _code(["--json", "puntuar", "ranking", "--sim", "200", "--min-evaluadores", "3"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["status"] == "INSUFICIENTE"
    assert _code(["--agente", "Evaluador-2", "puntuar", "registrar", "--datos", path]) == 3   # sigue sin suplantar
    assert db.normalize_rater("  Evaluador-1 ") == "evaluador-1"


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


def test_insert_or_replace_tampoco_sustituye_lo_registrado(project):
    """Revisión: INSERT OR REPLACE borra la fila que choca sin disparar los triggers de DELETE y la sustituía."""
    _new_opportunity(project)
    for value in (0.25, 0.5):
        assert _code(["--agente", "evaluador-1", "puntuar", "registrar", "--datos",
                      _scores_file(project, "evaluador-1", value)]) == 0
    assert _code(["autoria", "registrar", "--entidad", "O-0001", "--persona", "Ana", "--aportacion", "X + Y",
                  SIGN, "Ana"]) == 0
    conn = _conn()
    tables = {"decisions": "(id, ts, decision, rationale) VALUES (1, 't', 'd', 'r')",
              "scores_history": "(id, opportunity_id, rater, criterion, replaced_at) VALUES (1, 'O-0001', 'x', 'c', 't')",
              "inventive_contributions": "(id, entity_id, person, contribution, signed_by, signed_at) "
                                         "VALUES (1, 'O-0001', 'Otra', 'Z', 'Otra', 't')"}
    before = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")] for t in tables}
    for table, values in tables.items():
        for verb in ("INSERT OR REPLACE INTO", "REPLACE INTO", "INSERT INTO"):
            with pytest.raises(sqlite3.IntegrityError, match="solo de inserción"):
                conn.execute(f"{verb} {table} {values}")
        with pytest.raises(sqlite3.IntegrityError, match="solo de inserción"):
            conn.execute(f"INSERT INTO {table} {values} ON CONFLICT(id) DO UPDATE SET id = excluded.id")
    assert {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")] for t in tables} == before
    db.add_decision(conn, "O-0001", "propuesta nueva", "m", actor="director-cientifico")    # insertar sí se puede
    assert db.list_decisions(conn)[-1]["decision"] == "propuesta nueva"


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
    assert {"decisions_sin_update", "decisions_sin_delete", "scores_history_sin_delete", "decisions_sin_reemplazo",
            "scores_history_sin_reemplazo", "inventive_contributions_sin_reemplazo"} <= triggers
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM decisions")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT OR REPLACE INTO decisions (id, ts, decision, rationale) VALUES (1, 't', 'd', 'r')")


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


def test_el_listado_avisa_si_la_procedencia_no_es_integra(project, capsys):
    """Revisión: un evento forjado con el formato antiguo aparecía sin aviso en `nutria procedencia`."""
    path = _seed_events(project, 2)
    capsys.readouterr()
    assert _code(["procedencia"]) == 0
    assert "no es íntegra" not in capsys.readouterr().err
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-01-01T00:00:00+00:00", "actor": "humano", "action": "revision_humana",
                             "inputs": {}, "outputs": {}, "nutria": "0.1.0"}) + "\n")
    assert _code(["procedencia"]) == 0
    out = capsys.readouterr()
    assert "revision_humana" in out.out                                  # se muestra...
    assert "no es íntegra" in out.err and "sin cadena" in out.err        # ...con el aviso
    assert _code(["--json", "procedencia"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)[-1]["action"] == "revision_humana" and "no es íntegra" in out.err


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
def test_verificar_compara_con_la_version_confirmada_en_git(project, git_real):
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


def _commit_all(project: Path, message: str, *extra: str) -> str:
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", message, *extra)
    return _git(project, "rev-parse", "HEAD")


def _git_project(project: Path, n: int = 2) -> Path:
    path = _seed_events(project, n)
    _git(project, "init", "-q")
    _commit_all(project, "procedencia")
    return path


@requires_git
@pytest.mark.parametrize("borrado", ["rm", "rm_y_commit"])
def test_verificar_detecta_la_procedencia_confirmada_borrada(project, git_real, capsys, borrado):
    """Revisión (ronda 2, bloqueante): `rm -f research/*.jsonl` y `procedencia verificar` daban «íntegra (vacía)»
    con código 0. También si el borrado se confirma: la versión anterior sigue en el historial de HEAD."""
    path = _git_project(project)
    path.unlink()
    if borrado == "rm_y_commit":
        _commit_all(project, "sin procedencia")
    res = provenance.verify_chain(load_config())
    assert not res["existe"] and not res["integra"] and res["git"]["estado"] == "borrado"
    assert "se ha borrado la historia confirmada" in res["problemas"][0]
    capsys.readouterr()
    assert _code(["procedencia", "verificar"]) == 1
    out = capsys.readouterr().out
    assert "NO íntegra" in out and "(vacía)" not in out and "(no existe)" in out
    assert _code(["--json", "procedencia", "verificar"]) == 1
    assert json.loads(capsys.readouterr().out)["git"]["estado"] == "borrado"


@requires_git
@pytest.mark.parametrize("reescritura", ["commit", "amend", "reset_soft"])
def test_verificar_compara_con_todas_las_versiones_confirmadas(project, git_real, reescritura):
    """Revisión: la referencia era solo HEAD. Truncar y confirmar (un commit nuevo, commit --amend o reset --soft
    a un commit anterior) daba «íntegra». Ahora el fichero debe ampliar cada versión del historial de HEAD y de las
    puntas anteriores de la rama (reflog)."""
    path = _git_project(project, 2)
    truncated = path.read_bytes()
    first = _git(project, "rev-parse", "HEAD")
    for i in range(2):
        assert _code(["--agente", "director-cientifico", "decision", "registrar", "--entidad", "O-0001",
                      "--decision", f"más {i}", "--motivo", "m"]) == 0
    _commit_all(project, "más eventos")
    assert provenance.verify_chain(load_config())["integra"]
    path.write_bytes(truncated)                                   # quita los eventos ya confirmados
    if reescritura == "commit":
        _commit_all(project, "truncada")
    elif reescritura == "amend":
        _commit_all(project, "truncada", "--amend")
    else:
        _git(project, "reset", "-q", "--soft", first)
    res = provenance.verify_chain(load_config())
    assert not res["alterados"] and not res["cadena_rota"]       # la cadena del fichero truncado es coherente
    assert not res["integra"] and res["git"]["estado"] == "reescrito"
    origins = {v["origen"] for v in res["git"]["no_ampliadas"]}
    if reescritura == "commit":
        assert origins == {"historial"}
    else:   # la versión con los eventos quitados ya no está en el historial de HEAD: solo en el reflog de la rama
        assert all(o.startswith("reflog de") for o in origins)
        assert any("reflog" in p for p in res["problemas"])


@requires_git
def test_verificar_acepta_la_historia_que_solo_anade_eventos(project, git_real):
    path = _git_project(project, 1)
    for i in range(3):
        assert _code(["--agente", "director-cientifico", "decision", "registrar", "--entidad", "O-0001",
                      "--decision", f"d{i}", "--motivo", "m"]) == 0
        _commit_all(project, f"c{i}", *(["--amend"] if i == 1 else []))   # un amend que solo añade eventos
    assert _code(["--agente", "director-cientifico", "decision", "registrar", "--entidad", "O-0001", "--decision",
                  "sin confirmar", "--motivo", "m"]) == 0
    res = provenance.verify_chain(load_config())
    assert res["integra"] and res["git"]["estado"] == "amplia_lo_confirmado"
    assert res["git"]["eventos_nuevos"] == 1 and res["git"]["versiones_comprobadas"] == 4
    # con finales de línea CRLF en la copia de trabajo (Windows) sigue ampliando lo confirmado
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    res = provenance.verify_chain(load_config())
    assert res["git"]["estado"] == "amplia_lo_confirmado"


@requires_git
def test_el_estado_de_git_marca_cambios_sin_confirmar_fuera_de_research(tmp_path, monkeypatch, git_real):
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


def test_las_pruebas_no_ejecutan_git_salvo_que_lo_pidan(tmp_path, monkeypatch):
    """Regresión de la revisión (test_http.py intermitente): `subprocess` con `timeout` espera con `time.sleep`, y
    test_http.py sustituye time.sleep para contar las esperas de los reintentos. Con un git lento en el PATH y
    time.sleep sustituido, registrar un evento no espera nada: el fixture automático no deja que la procedencia
    ejecute git (las pruebas de git piden `git_real`)."""
    slow = tmp_path / "bin"
    slow.mkdir()
    (slow / "git").write_text("#!/bin/sh\nexec 1>&- 2>&-\nsleep 0.3\nexit 128\n", "utf-8")
    (slow / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{slow}{os.pathsep}{os.environ.get('PATH', '')}")
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or pytest.fail("se ejecutó un proceso"))
    event = provenance.log_event(Config(root=tmp_path), "prueba")
    assert provenance.verify_chain(Config(root=tmp_path))["git"] == {"estado": "sin_referencia"}
    assert sleeps == [] and calls == []
    assert event["git"] == {"commit": None, "cambios_sin_confirmar": None}


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
    res = _run_web_hook(tmp_path, payload, NUTRIA_PROCEDENCIA_WEB="claro")
    assert res.returncode == 0 and res.stdout.strip() == ""
    cfg = Config(root=tmp_path)
    event = provenance.read_events(cfg)[-1]
    assert event["action"] == "web_fetch" and event["actor"] == "claude-code"
    assert event["inputs"]["url"] == url and event["inputs"]["sesion"] == "sesion-1"
    assert event["inputs"]["id_llamada"] == "toolu_01" and event["inputs"]["modo"] == "claro"
    assert event["outputs"]["sha256_respuesta"] == provenance.sha256_json({"result": page, "code": 200})
    text = cfg.provenance_path.read_text("utf-8")
    assert page not in text and "artículo 13" not in text
    payload["tool_response"]["durationMs"] = 15                     # misma respuesta, otra duración: misma huella
    assert _run_web_hook(tmp_path, payload, NUTRIA_PROCEDENCIA_WEB="claro").returncode == 0
    first, second = provenance.read_events(cfg)[-2:]
    assert first["outputs"]["sha256_respuesta"] == second["outputs"]["sha256_respuesta"]
    assert provenance.verify_chain(cfg, use_git=False)["integra"]


def test_el_hook_web_registra_las_busquedas(tmp_path):
    query = "rumen protected choline transition cows"
    results = {"query": query, "results": [{"title": "Título A", "url": "https://a.org"},
                                           {"title": "Título B", "url": "https://b.org"}], "durationSeconds": 1.2}
    assert _run_web_hook(tmp_path, _web("WebSearch", {"query": query, "allowed_domains": ["efsa.europa.eu"]},
                                        results), NUTRIA_PROCEDENCIA_WEB="claro").returncode == 0
    cfg = Config(root=tmp_path)
    event = provenance.read_events(cfg)[-1]
    assert event["action"] == "web_search" and event["inputs"]["consulta"] == query
    assert event["inputs"]["dominios_permitidos"] == ["efsa.europa.eu"] and event["outputs"]["resultados"] == 2
    assert "Título A" not in cfg.provenance_path.read_text("utf-8")


# Por defecto (sin la variable o vacía) solo huellas: las consultas pueden revelar una idea confidencial y la
# procedencia se versiona. Un valor no reconocido también falla en cerrado; solo "claro" guarda el texto.
@pytest.mark.parametrize("modo", [None, "", "hash", "HASH", "sí", "clear"])
def test_el_hook_web_en_modo_hash_solo_guarda_dominio_y_huellas(tmp_path, modo):
    url = "https://patents.google.com/?q=rumen-protected+choline+carnitine"
    query = "rumen-protected choline carnitine dairy"
    env = {} if modo is None else {"NUTRIA_PROCEDENCIA_WEB": modo}
    assert _run_web_hook(tmp_path, _web("WebFetch", {"url": url, "prompt": "p"}, {"result": "r"}), **env).returncode == 0
    assert _run_web_hook(tmp_path, _web("WebSearch", {"query": query}, {"results": []}), **env).returncode == 0
    cfg = Config(root=tmp_path)
    fetch, search = provenance.read_events(cfg)[-2:]
    assert fetch["inputs"]["dominio"] == "patents.google.com" and fetch["inputs"]["modo"] == "hash"
    assert fetch["inputs"]["sha256_url"] == provenance.sha256_text(url) and "url" not in fetch["inputs"]
    assert search["inputs"]["sha256_consulta"] == provenance.sha256_text(query) and "consulta" not in search["inputs"]
    assert "carnitine" not in cfg.provenance_path.read_text("utf-8")


@pytest.mark.parametrize("modo", ["claro", "hash"])
def test_el_hook_web_nunca_guarda_los_secretos_de_la_url(tmp_path, modo):
    """Revisión: con el modo en claro, la URL de WebFetch quedaba con su api_key en la procedencia versionada. Se
    redacta siempre (credenciales, parámetros sensibles, secretos configurados y fragmento), también antes de calcular
    su huella, para que un secreto adivinable no se pueda confirmar con ella."""
    secretos = ["SECRETO123", "T0K3N", "persona@example.org", "clave-de-usuario", "FRAGMENTO"]
    url = ("https://lector:clave-de-usuario@api.example.org/x?api_key=SECRETO123&q=colina&token=T0K3N"
           "&mailto=persona@example.org#access_token=FRAGMENTO")
    assert _run_web_hook(tmp_path, _web("WebFetch", {"url": url}, {"result": "r"}),
                         NUTRIA_PROCEDENCIA_WEB=modo).returncode == 0
    cfg = Config(root=tmp_path)
    text = cfg.provenance_path.read_text("utf-8")
    assert [s for s in secretos if s in text] == []
    event = provenance.read_events(cfg)[-1]
    if modo == "claro":
        assert event["inputs"]["url"].startswith("https://***@api.example.org/x?") and "q=colina" in event["inputs"]["url"]
    else:
        assert event["inputs"]["dominio"] == "api.example.org"
        assert event["inputs"]["sha256_url"] != provenance.sha256_text(url)


def test_el_hook_web_en_claro_tampoco_guarda_secretos_de_la_consulta(tmp_path):
    """En modo claro, la consulta de WebSearch también pasa por `nutria.http.redact` (pares clave=valor sensibles,
    tokens Bearer y direcciones mailto:)."""
    query = "choline dairy api_key=SECRETO123 Bearer T0K3N mailto:persona@example.org"
    assert _run_web_hook(tmp_path, _web("WebSearch", {"query": query}, {"results": []}),
                         NUTRIA_PROCEDENCIA_WEB="claro").returncode == 0
    cfg = Config(root=tmp_path)
    text = cfg.provenance_path.read_text("utf-8")
    assert [s for s in ("SECRETO123", "T0K3N", "persona@example.org") if s in text] == []
    assert provenance.read_events(cfg)[-1]["inputs"]["consulta"].startswith("choline dairy")
    # revisión (ronda 2): un correo suelto en la consulta quedaba en claro; también en la ruta de una URL
    query = "rumen-protected choline carnitine synergy contacto ana@example.org api_key=SECRETO4"
    assert _run_web_hook(tmp_path, _web("WebSearch", {"query": query}, {"results": []}),
                         NUTRIA_PROCEDENCIA_WEB="claro").returncode == 0
    url = "https://lector:clave@api.example.org/usuarios/luis.p%40example.org/perfil?q=rumen"
    assert _run_web_hook(tmp_path, _web("WebFetch", {"url": url}, {"result": "r"}),
                         NUTRIA_PROCEDENCIA_WEB="claro").returncode == 0
    text = cfg.provenance_path.read_text("utf-8")
    assert [s for s in ("ana@example.org", "SECRETO4", "luis.p", "lector", "clave@") if s in text] == []
    events = provenance.read_events(cfg)
    assert events[-2]["inputs"]["consulta"].startswith("rumen-protected choline carnitine synergy contacto ***@***")
    assert events[-1]["inputs"]["url"] == "https://***@api.example.org/usuarios/***@***/perfil?q=rumen"


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
