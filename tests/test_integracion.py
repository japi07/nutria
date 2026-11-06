"""Interacciones entre los paquetes de corrección que solo aparecen al fusionarlos.

Cada prueba fija un contrato que depende de dos paquetes a la vez (p. ej. la comprobación central de la firma de
P1 dentro del main() de P4); las pruebas de cada paquete siguen en su propio fichero.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SIGN = "--firma-humana"


def _env() -> dict:
    env = {**os.environ, "PYTHONPATH": str(SRC), "NUTRIA_OFFLINE": "1"}
    env.pop("NUTRIA_HOME", None)
    return env


def _nutria_sh(cwd: Path, argv: list[str], redirection: str = "") -> subprocess.CompletedProcess:
    """Ejecuta `python -m nutria` desde sh con una redirección de la shell (p. ej. `2>&-`, stderr cerrada)."""
    return subprocess.run(["sh", "-c", f'exec "$@" {redirection}', "sh", sys.executable, "-m", "nutria", *argv],
                          cwd=cwd, env=_env(), capture_output=True, text=True, timeout=120)


# ============================================================================ P1 + P4: firma y contrato de errores
@pytest.mark.parametrize("redirection", ["", "2>&-", "2>/dev/null"])
def test_rechazo_de_firma_con_json_deja_un_solo_objeto_en_stdout(tmp_path, redirection):
    """P1 rechaza la firma con señales de agente (código 3) con el contrato de errores de P4: con --json, stdout lleva
    solo el objeto {error, codigo, tipo} y el texto va por stderr. Con stderr cerrada (`2>&-`, sys.stderr None), el
    texto no puede acabar en stdout detrás del JSON (print(file=None) escribe en stdout)."""
    subprocess.run([sys.executable, "-m", "nutria", "init"], cwd=tmp_path, env=_env(), capture_output=True,
                   check=True, timeout=120)
    argv = ["--agente", "director-cientifico", "--json", "puntuar", "compuerta", "O-0001", "seguridad", "superada",
            "--motivo", "prueba de rechazo", SIGN, "Persona Inventada"]
    proc = _nutria_sh(tmp_path, argv, redirection)
    assert proc.returncode == 3, (proc.stdout, proc.stderr)
    payload = json.loads(proc.stdout)          # un único objeto JSON: nada más en stdout
    assert (payload["codigo"], payload["tipo"]) == (3, "registro")
    assert "Firma humana rechazada" in payload["error"] and "no la registra un agente" in payload["error"]
    if not redirection:
        assert proc.stderr.startswith("Error de registro: Firma humana rechazada")
    assert "Traceback" not in proc.stderr


def test_rechazo_de_firma_sin_json_no_escribe_en_stdout_aunque_stderr_este_cerrada(tmp_path):
    subprocess.run([sys.executable, "-m", "nutria", "init"], cwd=tmp_path, env=_env(), capture_output=True,
                   check=True, timeout=120)
    argv = ["--agente", "director-cientifico", "puntuar", "compuerta", "O-0001", "seguridad", "superada",
            "--motivo", "prueba de rechazo", SIGN, "Persona Inventada"]
    proc = _nutria_sh(tmp_path, argv, "2>&-")
    assert proc.returncode == 3 and proc.stdout == ""


# ============================================================================ P3 + P4: fuentes y códigos de salida
@pytest.fixture
def proyecto_con_red(tmp_path, monkeypatch):
    """Proyecto sin NUTRIA_OFFLINE; cualquier petición HTTP real falla (las pruebas sustituyen los clientes)."""
    import requests

    from nutria import http
    from nutria.cli import main
    for name in (*http.SECRET_ENV_VARS, "NUTRIA_HOME", "NUTRIA_OFFLINE", "NUTRIA_AGENT", "NUTRIA_DEPURAR"):
        monkeypatch.delenv(name, raising=False)

    def sin_red(self, method, url, **kwargs):
        raise requests.ConnectionError(f"red no disponible en las pruebas: {url}")

    monkeypatch.setattr(requests.Session, "request", sin_red)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)
    root = tmp_path / "proyecto"
    root.mkdir()
    monkeypatch.chdir(root)
    assert main(["init"]) == 0
    return root


def _run(capsys, argv):
    from nutria.cli import main
    capsys.readouterr()
    try:
        code = main(argv)
    except SystemExit as exc:
        code = exc.code
    out, err = capsys.readouterr()
    return code, out, err


def _fuentes(root: Path, filas: list[dict]) -> None:
    from nutria import db
    conn = db.connect(root / "research" / "nutria.sqlite")
    try:
        for fila in filas:
            db.upsert_source(conn, {"title": "Ensayo sin verificar", "verified": False, **fila}, "prueba")
    finally:
        conn.close()


def _verificacion(identifier, status, con_registro=True):
    from nutria.sources.base import Record
    from nutria.verify import citations
    record = Record(source="crossref", doi=identifier, title="Ensayo verificado") if con_registro else None
    return citations.Verification(identifier=identifier, kind="doi", status=status, title="Ensayo verificado",
                                  message="m", retracted=False if status == "verificada" else None, record=record)


@pytest.mark.parametrize("estados, codigo", [
    ({"10.1000/p0": "conflicto", "10.1000/p1": "error_red"}, 3),   # el conflicto del registro va antes que la red
    ({"10.1000/p0": "error_red", "10.1000/p1": "no_encontrada"}, 4),
    ({"10.1000/p0": "conflicto", "10.1000/p1": "verificada"}, 3),
    ({"10.1000/p0": "verificada", "10.1000/p1": "verificada"}, 0),
])
def test_verificar_pendientes_conflicto_del_registro_y_red_en_el_mismo_lote(proyecto_con_red, capsys, monkeypatch,
                                                                            estados, codigo):
    """P3 marca «conflicto» la fuente que choca con el registro (SALIDA_REGISTRO); P4 da al lote el código de la tabla.
    Orden acordado al fusionar (informe final de P4, dueño de la tabla): 3 por conflicto, después 4 por red, después
    1 si alguna no se verifica o no tiene identificador; 0 solo si todas se verifican."""
    from nutria import db
    from nutria.verify import citations
    _fuentes(proyecto_con_red, [{"doi": doi} for doi in estados])
    original = db.upsert_source

    def upsert(conn, record, actor=None, changes=None):
        if record.get("verified") and estados.get(record.get("doi")) == "conflicto":
            raise db.RegistryError(f"doi:{record['doi']} ya está verificada con el título de otro trabajo")
        return original(conn, record, actor, changes)

    monkeypatch.setattr(db, "upsert_source", upsert)
    monkeypatch.setattr(citations, "verify_doi", lambda client, doi, *a, **k: _verificacion(
        doi, "verificada" if estados[doi] == "conflicto" else estados[doi]))
    code, out, _ = _run(capsys, ["--json", "verificar", "--pendientes"])
    assert code == codigo
    assert sorted(o["estado"] for o in json.loads(out)) == sorted(estados.values())


def test_verificar_pendientes_fuente_sin_identificador_no_sale_con_0(proyecto_con_red, capsys, monkeypatch):
    """Una fuente sin DOI ni PMID (P3: «sin_identificador») no se ha verificado: el lote no puede salir con 0."""
    from nutria.verify import citations
    _fuentes(proyecto_con_red, [{"openalex_id": "W123456789"}, {"doi": "10.1000/p1"}])
    monkeypatch.setattr(citations, "verify_doi", lambda client, doi, *a, **k: _verificacion(doi, "verificada"))
    code, out, _ = _run(capsys, ["--json", "verificar", "--pendientes"])
    estados = {o["id"]: o["estado"] for o in json.loads(out)}
    assert "sin_identificador" in estados.values() and "verificada" in estados.values()
    assert code == 1


def test_fuente_agregar_verificada_sin_registro_no_sale_con_0(proyecto_con_red, capsys, monkeypatch):
    """P3 registra el registro de la propia verificación; si la verificación no lo trae, P4 da 1 (no 0) con el error
    en JSON por stdout y en texto por stderr."""
    from nutria.verify import citations
    monkeypatch.setattr(citations, "verify_doi",
                        lambda client, doi, *a, **k: _verificacion(doi, "verificada", con_registro=False))
    code, out, err = _run(capsys, ["--json", "fuente", "agregar", "--doi", "10.1000/x1"])
    assert code == 1 and json.loads(out)["codigo"] == 1 and "No se registra" in err


def test_buscar_registrar_con_conflicto_y_stderr_cerrada_deja_un_solo_json(proyecto_con_red, capsys, monkeypatch):
    """Un resultado que choca con el registro no se registra (P3) y la orden sale con 3; el aviso va por stderr con
    _imprimir (P4): con stderr cerrada no acaba en stdout detrás del objeto JSON de la búsqueda."""
    from nutria import db
    from nutria.sources import pubmed
    from nutria.sources.base import Record
    monkeypatch.setattr(pubmed, "search_records", lambda client, query, **kw: {
        "count": 2, "records": [Record(source="pubmed", pmid="111", title="Primer ensayo"),
                                Record(source="pubmed", pmid="222", title="Segundo ensayo")]})
    original = db.upsert_source

    def upsert(conn, record, actor=None, changes=None):
        if record.get("pmid") == "222":
            raise db.RegistryError("pmid:222 choca con otra fuente del registro")
        return original(conn, record, actor, changes)

    monkeypatch.setattr(db, "upsert_source", upsert)
    capsys.readouterr()
    saved, sys.stderr = sys.stderr, None        # stderr cerrada al arrancar (`2>&-`)
    try:
        code, out, _ = _run(capsys, ["--json", "buscar", "pubmed", "colina protegida", "--registrar"])
    finally:
        sys.stderr = saved
    data = json.loads(out)                      # un único objeto JSON
    assert code == 3 and len(data["registradas"]) == 1 and data["conflictos"][0]["registro"] == "pmid:222"


# ============================================================================ P1 + P6: puntuar registrar
def _oportunidad(project: Path) -> str:
    from nutria.cli import main
    path = project / "oportunidad.json"
    path.write_text((ROOT / "ejemplos" / "oportunidad_ejemplo.json").read_text("utf-8"), "utf-8")
    assert main(["oportunidad", "nueva", "--datos", str(path)]) == 0
    return "O-0001"


def _puntuaciones(project: Path, evaluador: str, **primera) -> Path:
    data = json.loads((ROOT / "ejemplos" / "puntuacion_ejemplo.json").read_text("utf-8"))
    data.update(oportunidad="O-0001", evaluador=evaluador)
    data["puntuaciones"][0].update(primera)
    path = project / f"puntuacion_{evaluador}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return path


def test_puntuar_registrar_aplica_las_anclas_de_p6_y_la_identidad_de_p1(project, capsys):
    from nutria import db
    from nutria.config import load_config
    _oportunidad(project)
    entre_anclas = {"valor": 0.6, "min": 0.5, "max": 0.75}
    # P6: un valor entre anclas sin justificación rechaza el fichero entero (código 2) y no registra nada
    sin_justificar = _puntuaciones(project, "evaluador-1", **entre_anclas)
    code, _, err = _run(capsys, ["--agente", "evaluador-1", "puntuar", "registrar", "--datos", str(sin_justificar)])
    assert code == 2 and "fuera de las anclas" in err
    conn = db.connect(load_config().db_path)
    try:
        assert db.list_scores(conn, "O-0001") == []
    finally:
        conn.close()
    # Con la justificación se registra; el aviso va solo por stderr (también con stderr cerrada) y el actor es el
    # evaluador declarado (P1)
    justificado = _puntuaciones(project, "evaluador-1", **entre_anclas,
                                justificacion_entre_anclas="dos ensayos con el mismo diseño discrepan")
    code, out, err = _run(capsys, ["--agente", "evaluador-1", "--json", "puntuar", "registrar", "--datos",
                                   str(justificado)])
    assert code == 0 and json.loads(out)["evaluador"] == "evaluador-1" and "entre las anclas" in err
    saved, sys.stderr = sys.stderr, None
    try:
        code, out, _ = _run(capsys, ["--agente", "evaluador-1", "--json", "puntuar", "registrar", "--datos",
                                     str(justificado)])
    finally:
        sys.stderr = saved
    assert code == 0 and json.loads(out)["criterios"] == 9          # un único objeto JSON en stdout
    eventos = [json.loads(line) for line in (project / "research" / "procedencia.jsonl").read_text("utf-8")
               .splitlines() if line.strip()]
    assert [e["actor"] for e in eventos if e["action"] == "puntuacion"] == ["evaluador-1", "evaluador-1"]
    # P1: un rol que no es evaluador no registra puntuaciones, aunque el fichero cumpla las anclas
    code, _, err = _run(capsys, ["--agente", "critico-red-team", "puntuar", "registrar", "--datos", str(justificado)])
    assert code == 3 and "Solo los evaluadores independientes" in err


# ============================================================================ P4 + P1/P6: avisos con stderr cerrada
def _sin_stderr(capsys, argv):
    """Ejecuta la CLI con stderr cerrada al arrancar (sys.stderr None, como con `2>&-`)."""
    saved, sys.stderr = sys.stderr, None
    try:
        return _run(capsys, argv)
    finally:
        sys.stderr = saved


def test_avisos_del_ranking_solo_por_stderr(project, capsys):
    """P6 avisa de las compuertas críticas sin resolver en el top-k; con --json el aviso va por stderr y stdout lleva
    solo la lista JSON del ranking, también con stderr cerrada (contrato de P4)."""
    _oportunidad(project)
    for n in (1, 2, 3):
        evaluador = f"evaluador-{n}"
        code, _, _ = _run(capsys, ["--agente", evaluador, "puntuar", "registrar", "--datos",
                                   str(_puntuaciones(project, evaluador))])
        assert code == 0
    argv = ["--agente", "director-cientifico", "--json", "puntuar", "ranking", "--sim", "200"]
    code, out, err = _run(capsys, argv)
    assert code == 0 and json.loads(out)[0]["opportunity_id"] == "O-0001" and "CRÍTICAS sin resolver" in err
    code, out, _ = _sin_stderr(capsys, argv)
    assert code == 0 and json.loads(out)[0]["opportunity_id"] == "O-0001"


def test_aviso_de_integridad_de_la_procedencia_solo_por_stderr(project, capsys):
    """P1 avisa en `procedencia` (listar) si la cadena no es íntegra; con --json stdout lleva solo la lista."""
    _oportunidad(project)
    path = project / "research" / "procedencia.jsonl"
    lines = path.read_text("utf-8").splitlines()
    evento = json.loads(lines[0])
    evento["actor"] = "otro-actor"              # evento alterado por fuera de nutria
    path.write_text("\n".join([json.dumps(evento, ensure_ascii=False), *lines[1:]]) + "\n", "utf-8")
    argv = ["--agente", "director-cientifico", "--json", "procedencia"]
    code, out, err = _run(capsys, argv)
    assert code == 0 and isinstance(json.loads(out), list) and "no es íntegra" in err
    code, out, _ = _sin_stderr(capsys, argv)
    assert code == 0 and isinstance(json.loads(out), list)


# ============================================================================ P1 + P7: clasificación regulatoria
def test_clasificar_con_firma_desde_un_agente_se_rechaza_tambien_por_la_api(project, monkeypatch):
    """P7 clasifica con firma humana llamando a `require_human_signature` de P1 si existe (globals().get): con las dos
    ramas fusionadas la comprobación está activa, y una firma con señales de agente no cambia la clasificación."""
    from nutria import db
    from nutria.config import load_config
    _oportunidad(project)
    conn = db.connect(load_config().db_path)
    try:
        antes = db.get_opportunity(conn, "O-0001")["regulatory_route"]
        monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
        with pytest.raises(db.RegistryError, match="Firma humana rechazada"):
            db.classify_opportunity(conn, "O-0001", "pienso_dietetico", "triage", human_signoff="Ana")
        assert db.get_opportunity(conn, "O-0001")["regulatory_route"] == antes
        # Sin firma, el rol propietario del triage sigue pudiendo clasificar (la comprobación no le afecta)
        db.classify_opportunity(conn, "O-0001", "materia_prima", "triage", actor=db.CLASSIFICATION_OWNER)
        assert db.get_opportunity(conn, "O-0001")["regulatory_route"] == "materia_prima"
    finally:
        conn.close()


# ============================================================================ P1 + P8: ceguera de los evaluadores
@pytest.mark.parametrize("identidad", ["agente", "variable"])
def test_ceguera_de_los_evaluadores_tambien_con_nutria_agent(project, capsys, monkeypatch, identidad):
    """P8 oculta a los evaluadores las decisiones propuestas (la conclusión esperada) y las puntuaciones ajenas; P1
    reconoce al evaluador por --agente o, sin él, por NUTRIA_AGENT. Con las dos ramas, un evaluador que solo declara la
    variable tampoco ve nada de eso."""
    _oportunidad(project)
    for n in (1, 2, 3):
        evaluador = f"evaluador-{n}"
        code, _, _ = _run(capsys, ["--agente", evaluador, "puntuar", "registrar", "--datos",
                                   str(_puntuaciones(project, evaluador))])
        assert code == 0
    code, _, _ = _run(capsys, ["--agente", "director-cientifico", "decision", "registrar", "--entidad", "O-0001",
                               "--decision", "propuesta: la mejor candidata del ranking", "--motivo", "m"])
    assert code == 0
    # Control: quien no es evaluador sí ve decisiones y puntuaciones
    code, out, _ = _run(capsys, ["--agente", "director-cientifico", "--json", "oportunidad", "ver", "O-0001"])
    fila = json.loads(out)
    assert code == 0 and fila["decisiones"] and fila["puntuaciones"]
    if identidad == "variable":
        monkeypatch.setenv("NUTRIA_AGENT", "evaluador-2")
        quien = []
    else:
        quien = ["--agente", "evaluador-2"]
    code, out, _ = _run(capsys, [*quien, "--json", "oportunidad", "ver", "O-0001"])
    fila = json.loads(out)
    assert code == 0 and "decisiones" not in fila and "puntuaciones" not in fila
    for argv in (["decision", "listar"], ["decision", "listar", "--entidad", "O-0001"], ["puntuar", "ranking"],
                 ["procedencia"]):
        code, out, err = _run(capsys, [*quien, *argv])
        assert code == 2 and "Los evaluadores independientes no pueden" in err, argv
        assert "propuesta: la mejor candidata" not in out


# ============================================================================ fase de coordinación
@pytest.mark.parametrize("identidad", ["agente", "variable"])
def test_coord_status_reason_no_llega_a_los_evaluadores(project, capsys, monkeypatch, identidad):
    """Revisión final de P8 (residuo de D1-3): el motivo del último cambio de estado de una oportunidad o de una
    hipótesis («seleccionada: mejor del ranking») anticipa la conclusión esperada; un evaluador no lo ve en oportunidad
    ver, oportunidad listar, hipotesis listar ni estado, se identifique con --agente o con NUTRIA_AGENT."""
    _oportunidad(project)
    code, _, _ = _run(capsys, ["--agente", "director-cientifico", "oportunidad", "estado", "O-0001", "en_evaluacion",
                               "--motivo", "MOTIVO-ESTADO-3: mejor del ranking"])
    assert code == 0
    hip = json.loads((ROOT / "ejemplos" / "hipotesis_ejemplo.json").read_text("utf-8"))
    hip = hip[0] if isinstance(hip, list) else hip
    hip["oportunidad"] = "O-0001"
    (project / "h.json").write_text(json.dumps(hip, ensure_ascii=False), "utf-8")
    code, out, err = _run(capsys, ["--agente", "director-cientifico", "--json", "hipotesis", "nueva", "--datos",
                                   str(project / "h.json")])
    assert code == 0, err
    hid = json.loads(out)["ids"][0]
    code, _, err = _run(capsys, ["--agente", "director-cientifico", "hipotesis", "estado", hid, "en_revision", "--motivo",
                                 "MOTIVO-ESTADO-3: la más prometedora"])
    assert code == 0, err
    code, out, _ = _run(capsys, ["--agente", "director-cientifico", "--json", "oportunidad", "ver", "O-0001"])
    assert code == 0 and json.loads(out)["status_reason"].startswith("MOTIVO-ESTADO-3")      # control
    if identidad == "variable":
        monkeypatch.setenv("NUTRIA_AGENT", "evaluador-1")
        quien = []
    else:
        quien = ["--agente", "evaluador-1"]
    for argv in (["--json", "oportunidad", "ver", "O-0001", "--sin-puntuaciones"], ["--json", "oportunidad", "ver", "O-0001"],
                 ["oportunidad", "ver", "O-0001"], ["--json", "oportunidad", "listar"], ["--json", "estado"],
                 ["--json", "hipotesis", "listar"]):
        code, out, _ = _run(capsys, [*quien, *argv])
        assert code == 0 and "MOTIVO-ESTADO-3" not in out and "status_reason" not in out, argv


# ============================================================================ fase de coordinación (2): P1 y P2
def _proc_con_claude(root: Path) -> Path:
    """/proc simulado: este proceso desciende de Claude Code (sin ninguna variable de entorno de Claude Code)."""
    proc = root / "proc"
    for pid, ppid, comm, cmdline in ((os.getpid(), 200, "python3", ["python3", "-m", "nutria"]),
                                      (200, 100, "bash", ["/bin/bash", "-c", "x"]),
                                      (100, 1, "claude", ["/opt/claude-code/bin/claude"])):
        d = proc / str(pid)
        d.mkdir(parents=True)
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} {pid} {pid} 34816 -1 4194560", "utf-8")
        (d / "comm").write_text(comm + "\n", "utf-8")
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in cmdline) + b"\0")
        (d / "environ").write_bytes(b"PATH=/bin\0")
    return proc


def test_coord_revision_de_informe_por_la_api_exige_la_terminal_de_una_persona(project, monkeypatch):
    """Petición de P1 a P2 (e) y de P2 a P1 (1): `db.add_report_review` guarda una firma humana y aplica la comprobación
    central (`require_human_signature`): antecesores y terminal de control, no solo las variables de este proceso. Un
    programa lanzado desde Claude Code que llame a la API sin esas variables no registra la revisión."""
    from nutria import db, provenance
    from nutria.config import load_config
    conn = db.connect(load_config().db_path)
    try:
        monkeypatch.setattr(provenance, "PROC_ROOT", str(_proc_con_claude(project)))
        monkeypatch.setattr(provenance, "_PROCESS_CACHE", {})
        with pytest.raises(db.RegistryError, match="Firma humana rechazada"):
            db.add_report_review(conn, "research/informes/O-0001.md", "a" * 64, "Ana Pérez", status="revisado")
        assert db.report_reviews(conn) == []
        # control: desde la terminal de una persona (sin /proc con Claude Code) sí se registra
        monkeypatch.setattr(provenance, "PROC_ROOT", str(project / "sin_proc"))
        monkeypatch.setattr(provenance, "_PROCESS_CACHE", {})
        db.add_report_review(conn, "research/informes/O-0001.md", "a" * 64, "Ana Pérez", status="revisado")
        assert [r["reviewer"] for r in db.report_reviews(conn)] == ["Ana Pérez"]
    finally:
        conn.close()


def test_coord_eventos_con_las_versiones_de_las_bibliotecas_de_calculo(project):
    """Petición de P5 a P1: cada evento lleva las versiones de numpy, scipy, statsmodels y pandas (St-Pierre usa el
    optimizador de statsmodels con pandas), si están instaladas."""
    from importlib import metadata

    from nutria import provenance
    from nutria.config import load_config
    ev = provenance.log_event(load_config(), "prueba", inputs={"x": 1})
    for dist in ("numpy", "scipy", "statsmodels", "pandas"):
        try:
            assert ev["entorno"][dist] == metadata.version(dist), dist
        except metadata.PackageNotFoundError:
            assert dist not in ev["entorno"]


# ============================================================================ fase de coordinación (2): hook de Bash
HOOKS = ROOT / "scripts" / "hooks"


def _hook_bash(command: str, agent_type: str | None = None, cwd: str = "/home/u/nutria") -> subprocess.CompletedProcess:
    payload = {"session_id": "s", "transcript_path": "/home/u/.claude/projects/p/s.jsonl", "cwd": cwd,
               "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_input": {"command": command, "description": "orden de prueba"}}
    if agent_type:
        payload.update(agent_id="a1", agent_type=agent_type)
    return subprocess.run(["sh", str(HOOKS / "guard_bash.sh")], input=json.dumps(payload), capture_output=True,
                          text=True, timeout=60)


_MUCHAS_REFERENCIAS = " ".join(f"[R{i}]" for i in range(1, 120))


@pytest.mark.parametrize("command", [
    # nutria como texto en órdenes que no ejecutan nada (revisión final de P1: falsos positivos de la ronda 3)
    "grep -rn nutria docs/ | head -5",
    "grep -c nutria README.md | wc -l",
    "git commit -m 'Registro de nutria (P1); varios cambios'",
    "git add -A && git commit -m \"nutria: órdenes atribuidas\"",
    "git log --oneline | grep nutria | head -3",
    # un comentario después de una orden bien atribuida
    "nutria --agente critico-red-team estado # nota para la persona",
    # más de 100 referencias [Rn] (palabras con corchetes) en un --motivo o en un mensaje de commit
    f"nutria --agente critico-red-team decision registrar --entidad O-0001 --decision d --motivo \"{_MUCHAS_REFERENCIAS}\"",
    f"git commit -F - <<'EOF'\nRevisión {_MUCHAS_REFERENCIAS}\nEOF",
])
def test_coord_hook_no_bloquea_ordenes_legitimas_de_un_subagente(command):
    res = _hook_bash(command, "critico-red-team")
    assert res.returncode == 0, (command, res.stderr)


@pytest.mark.parametrize("command", [
    # un comentario no aporta el --agente (revisión final de P1: el evaluador veía puntuaciones ajenas)
    "python3 -m nutria --json oportunidad ver O-0001 # --agente critico-red-team",
    "nutria puntuar registrar --datos p.json # --agente critico-red-team",
    "sh -c \"nutria estado\" # --agente critico-red-team",
    # nutria por su ruta
    "/usr/local/bin/nutria puntuar ranking",
    "./nutria estado",
    ".venv/bin/nutria --json estado",
    # la excepción de las órdenes que no ejecutan nada no vale si algo de la línea puede ejecutar nutria
    "grep -o 'nutria estado' notas.txt | sh",
    "echo nutria estado | sh",
    "git commit -m \"$(nutria estado)\"",
    "git commit -m x; nutria estado",
    "grep x f && nutria estado",
    "PATH=/tmp grep nutria x",
    "git -c diff.external=nutria diff",
    "awk 'BEGIN{system(\"nutria estado\")}'",
    "find . -maxdepth 0 -exec nutria estado ;",
    "cat <<'EOF' | sh\nnutria estado\nEOF",
])
def test_coord_hook_sigue_exigiendo_agente_a_nutria_ejecutable(command):
    res = _hook_bash(command, "critico-red-team")
    assert res.returncode == 2, (command, res.stdout)
    # un evaluador tampoco: su identidad va en cada orden nutria
    assert _hook_bash(command.replace("critico-red-team", "evaluador-2"), "evaluador-independiente").returncode == 2


@pytest.mark.parametrize("command", [
    # el directorio del proyecto se llama nutria: como argumento de otra orden no es una orden nutria (la primera
    # versión de la coordinación bloqueaba «cd /home/user/nutria && nutria --agente ... estado» en todo subagente)
    "cd /home/user/nutria && python3 -m pytest -q",
    "cd /home/user/nutria && nutria --agente critico-red-team estado",
    "python3 scripts/contar.py /home/user/nutria",
    "ls ~/nutria/research && cd ../nutria && pwd",
    "find /home/user/nutria -name '*.md' | head",
    "echo 'Revisión de nutria' >> notas.md",
    "git grep -n nutria -- docs",
    "/usr/local/bin/nutria --agente critico-red-team estado",
])
def test_coord3_hook_la_ruta_del_proyecto_no_es_una_orden_nutria(command):
    assert _hook_bash(command, "critico-red-team").returncode == 0, command


@pytest.mark.parametrize("command, agente", [
    # nutria por su ruta en posición de orden o seguida de una orden de nutria
    ("timeout 10 /usr/bin/nutria estado", "critico-red-team"),
    ("env -u FOO /usr/bin/nutria --json estado", "critico-red-team"),
    ("X=1 .venv/bin/nutria", "critico-red-team"),
    # git grep -O ejecuta una orden con los ficheros encontrados, pegada a la opción y fuera de la vista del hook
    ("git grep -Onutria foo", "critico-red-team"),
    ("git grep -O'nutria puntuar ranking #' foo", "evaluador-independiente"),
    ("git grep --open-files-in-pager='sh -c nutria' foo", "critico-red-team"),
    # lo que la shell toma por comentario pero ejecuta otro intérprete (campo privado #x de node en un documento en
    # línea) no esconde una orden nutria sin --agente
    ("node <<EOF\nclass A { #x = require('child_process').execSync('nutria puntuar ranking').toString(); }\nEOF",
     "evaluador-independiente"),
])
def test_coord3_hook_sigue_viendo_nutria_por_ruta_git_grep_y_comentarios_ajenos(command, agente):
    assert _hook_bash(command, agente).returncode == 2, command


def test_coord_hook_el_comentario_no_hace_pasar_por_evaluador():
    # Reproducción de la revisión final de P1 (r3b_comentario_e2e.py): el evaluador sin --agente real
    res = _hook_bash("python3 -m nutria --json oportunidad ver O-0001 # --agente evaluador-2", "evaluador-independiente")
    assert res.returncode == 2 and "se identifica en cada orden nutria" in res.stderr
    assert _hook_bash("nutria --agente evaluador-2 --json oportunidad ver O-0001 --sin-puntuaciones # leer",
                      "evaluador-independiente").returncode == 0


@pytest.mark.parametrize("command", [
    # borrados literales del registro y de la procedencia, coherentes con sus variantes con comodines
    "unlink research/nutria.sqlite",
    "find research -name nutria.sqlite -delete",
    "find . -name procedencia.jsonl -delete",
    "ex -sc '$d|x' research/procedencia.jsonl",
    "vim -es -c '$d' -c 'wq' research/procedencia.jsonl",
    "php -r 'unlink(\"research/nutria.sqlite\");'",
    "zip -m copia.zip research/nutria.sqlite",
    "tar --remove-files -cf copia.tar research/nutria.sqlite",
    "gio remove research/nutria.sqlite",
    # el reflog y el resto de .git (revisión final de P1: verificar daba «íntegra» tras vaciar el reflog)
    ": > .git/logs/refs/heads/main",
    "cp /dev/null .git/logs/refs/heads/main",
    "ex -sc '1d|x' .git/logs/HEAD",
    "printf '#!/bin/sh\\nnutria estado\\n' > .git/hooks/pre-commit",
])
def test_coord_hook_bloquea_borrados_literales_y_escrituras_en_git(command):
    for agente in (None, "critico-red-team"):
        res = _hook_bash(command, agente)
        assert res.returncode == 2, (command, agente)


@pytest.mark.parametrize("command", [
    "tar czf copia.tgz research/nutria.sqlite",
    "zip copia.zip research/nutria.sqlite",
    "find research -name nutria.sqlite",
    "cat .git/HEAD",
    "git log --oneline -3",
])
def test_coord_hook_deja_leer_y_copiar_el_registro_y_git(command):
    assert _hook_bash(command).returncode == 0, command


@pytest.mark.parametrize("ruta, bloqueada", [
    ("/home/u/nutria/.git/logs/refs/heads/main", True),
    ("/home/u/nutria/.git/hooks/pre-commit", True),
    ("/home/u/nutria/.git/config", True),
    (".git/refs/heads/main", True),
    ("/home/u/nutria/.github/workflows/ci.yml", False),
    ("/home/u/nutria/research/planes/plan.md", False),
])
def test_coord_guard_write_protege_git(ruta, bloqueada):
    payload = {"tool_name": "Write", "tool_input": {"file_path": ruta, "content": "x"}}
    res = subprocess.run(["sh", str(HOOKS / "guard_write.sh")], input=json.dumps(payload), capture_output=True,
                         text=True, timeout=60)
    assert res.returncode == (2 if bloqueada else 0), ruta


def test_coord_hook_evaluador_no_lee_el_registro_con_comodines():
    # Revisión final de P1 (6): «strings research/*.sqlite» daba 0 a un evaluador; con el nombre literal ya se bloqueaba
    for command in ("strings research/*.sqlite | grep -i evaluador", "cat research/*", "od -c research/nutria.sq*"):
        res = _hook_bash(command, "evaluador-independiente")
        assert res.returncode == 2 and "no lee la base de datos" in res.stderr, command
    assert _hook_bash("cat research/oportunidades/*.json | head", "critico-red-team").returncode == 0


# ============================================================================ fase de coordinación (2): evaluadores
def test_coord_normalize_rater_quita_los_caracteres_de_formato():
    # Revisión final de P1 (3): tres variantes invisibles de evaluador-1 contaban como tres evaluadores
    from nutria import db
    for variante in ("evaluador-1​", "evaluador​-1", "evaluador-1\xad", "﻿evaluador-1", "evaluador‍-1"):
        assert db.normalize_rater(variante) == "evaluador-1", repr(variante)


def test_coord_variantes_invisibles_de_un_evaluador_no_cuentan_como_tres(project, capsys):
    _oportunidad(project)
    for variante in ("evaluador-1​", "evaluador​-1", "evaluador-1\xad"):
        code, _, err = _run(capsys, ["--agente", variante, "puntuar", "registrar", "--datos",
                                     str(_puntuaciones(project, variante))])
        assert code == 0, err
    code, out, _ = _run(capsys, ["--agente", "orquestador", "--json", "puntuar", "ranking", "--min-evaluadores", "3",
                                 "--sim", "200"])
    fila = next(r for r in json.loads(out) if r["opportunity_id"] == "O-0001")
    assert fila["status"] == "INSUFICIENTE" and fila["raters"] == 1


def test_coord_ranking_cuenta_evaluadores_normalizados_en_registros_antiguos(project, capsys):
    # Petición de P1 a P6 (c): puntuaciones registradas antes de normalizar el evaluador («Evaluador-1» y
    # «evaluador-1 ») son de un solo evaluador; de cada criterio vale la última
    import sqlite3

    from nutria.config import load_config
    _oportunidad(project)
    conn = sqlite3.connect(load_config().db_path)
    try:
        for n, rater in enumerate(("Evaluador-1", "evaluador-1 ", "evaluador_1", "evaluador-2")):
            conn.execute("INSERT INTO scores (opportunity_id, rater, criterion, value, low, high, created_at) "
                         "VALUES ('O-0001', ?, 'evidencia', ?, 0, 1, ?)", (rater, 0.25 * (n + 1), f"2026-01-0{n + 1}"))
        conn.commit()
    finally:
        conn.close()
    code, out, _ = _run(capsys, ["--agente", "orquestador", "--json", "puntuar", "ranking", "--sim", "200"])
    fila = next(r for r in json.loads(out) if r["opportunity_id"] == "O-0001")
    assert fila["raters"] == 2


# ============================================================================ fase de coordinación (2): P7 + P1
def test_coord_regulatoria_superada_exige_la_clasificacion_del_triage(project, capsys):
    """Petición de P7 a P1: la compuerta regulatoria no se propone ni se firma 'superada' mientras la ruta del registro
    siga sin clasificar ('indeterminada'); 'bloqueada' y 'no_resuelta' sí. Tras `oportunidad clasificar`, se puede."""
    from nutria import db
    from nutria.config import load_config
    _oportunidad(project)
    code, _, err = _run(capsys, ["--agente", "regulacion-seguridad", "puntuar", "compuerta", "O-0001", "regulatoria",
                                 "superada", "--motivo", "propuesta"])
    assert code == 3 and "oportunidad clasificar O-0001" in err
    code, _, err = _run(capsys, ["puntuar", "compuerta", "O-0001", "regulatoria", "superada", "--motivo", "revisado",
                                 SIGN, "Ana"])
    assert code == 3 and "no tiene clasificación regulatoria" in err
    for estado in ("no_resuelta", "bloqueada"):
        code, _, err = _run(capsys, ["--agente", "regulacion-seguridad", "puntuar", "compuerta", "O-0001",
                                     "regulatoria", estado, "--motivo", "m"])
        assert code == 0, err
    code, _, err = _run(capsys, ["--agente", "regulacion-seguridad", "oportunidad", "clasificar", "O-0001", "--ruta",
                                 "materia_prima", "--motivo", "triage"])
    assert code == 0, err
    triage = project / "research" / "revisiones" / "O-0001_descarte_regulatoria.md"
    triage.parent.mkdir(parents=True, exist_ok=True)
    triage.write_text("# Triage regulatorio de O-0001\n\nMateria prima [SUP].\n", "utf-8")
    code, _, err = _run(capsys, ["puntuar", "compuerta", "O-0001", "regulatoria", "superada", "--motivo", "revisado",
                                 SIGN, "Ana", "--evidencia", str(triage)])
    assert code == 0, err
    conn = db.connect(load_config().db_path)
    try:
        assert db.CLASSIFICATION_OWNER == db.GATE_OWNERS["regulatoria"]
        assert db.effective_gates(conn, "O-0001", ["regulatoria"])["regulatoria"] == "superada"
    finally:
        conn.close()


# ============================================================================ fase de coordinación (2): P3 → P4
def test_coord_europepmc_lee_la_licencia_y_el_registro_la_guarda(project):
    """Petición de P3 a P4: el cliente de Europe PMC pone el campo 'license' del resultado en Record.extra['license'];
    db.upsert_source lo guarda (en una búsqueda, solo si la fila no tenía licencia)."""
    from dataclasses import asdict

    from nutria import db
    from nutria.config import load_config
    from nutria.sources import europepmc
    hit = {"id": "123", "source": "MED", "pmid": "123", "doi": "10.1234/epmc.1", "title": "Choline in cows",
           "pubYear": "2020", "license": "cc by"}
    rec = europepmc.parse(hit)
    assert rec.extra["license"] == "cc by"
    assert "license" not in europepmc.parse({**hit, "license": "  "}).extra
    conn = db.connect(load_config().db_path)
    try:
        db.upsert_source(conn, asdict(rec))
        assert db.get_source(conn, "doi:10.1234/epmc.1")["license"] == "cc by"
    finally:
        conn.close()


# ============================================================================ fase de coordinación (2): P4
@pytest.mark.skipif(not Path("/dev/full").exists(), reason="sin /dev/full")
@pytest.mark.parametrize("argv, codigo", [(["procedencia"], 2), (["--json", "oportunidad", "ver", "O-9999"], 3)])
def test_coord_error_de_escritura_en_stdout_tiene_su_codigo(tmp_path, argv, codigo):
    """Revisión final de P4: un error de E/S en stdout distinto de una tubería rota (disco lleno) salía con 120 y
    «Exception ignored ... OSError». Ahora: el código de la orden si ya era un error (3) y, si no, 2, sin traza."""
    subprocess.run([sys.executable, "-m", "nutria", "init"], cwd=tmp_path, env=_env(), capture_output=True,
                   check=True, timeout=120)
    env = _env()
    env.pop("PYTHONUNBUFFERED", None)
    res = subprocess.run(["sh", "-c", 'exec "$@" > /dev/full', "sh", sys.executable, "-m", "nutria", *argv],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert res.returncode == codigo, res.stderr
    assert "Exception ignored" not in res.stderr and "Traceback" not in res.stderr


# ============================================================================ fase de coordinación (2): P5 → P7
def test_coord_correlacion_intrasujeto_publicada_llega_al_csv_de_analisis(project, capsys):
    """Petición de P5 al paquete del registro: el campo opcional correlacion_intrasujeto (r publicada en un diseño
    cruzado, en [0, 1)) se registra con la doble extracción y se exporta como columna rho_intrasujeto, que meta
    analizar usa sin suponerla."""
    import csv

    from nutria import db
    from nutria.config import load_config
    conn = db.connect(load_config().db_path)
    try:
        db.upsert_source(conn, {"doi": "10.1000/cruzado", "title": "Ensayo cruzado", "year": 2020, "verified": True,
                                "retracted": False, "verified_via": "prueba"})
    finally:
        conn.close()
    recs = json.loads((ROOT / "ejemplos" / "extraccion_ejemplo.json").read_text("utf-8"))
    for r in recs:
        r.update(fuente="doi:10.1000/cruzado", diseno="crossover", correlacion_intrasujeto=0.6)
    for copia, indent in (("A", None), ("B", 1)):
        (project / f"x_{copia}.json").write_text(json.dumps(recs, ensure_ascii=False, indent=indent), "utf-8")
        code, _, err = _run(capsys, ["--agente", f"extractor-datos-{copia}", "extraccion", "depositar", "--datos",
                                     str(project / f"x_{copia}.json")])
        assert code == 0, err
    code, _, err = _run(capsys, ["--agente", "extractor-datos-C", "extraccion", "comparar", str(project / "x_A.json"),
                                 str(project / "x_B.json"), "--registrar"])
    assert code == 0, err
    variable = recs[0]["variable"]
    code, _, err = _run(capsys, ["--agente", "estadistico", "extraccion", "exportar", "--variable", variable, "--csv",
                                 str(project / "l.csv")])
    assert code in (0, 1), err
    with open(project / "l.csv", encoding="utf-8") as fh:
        filas = list(csv.DictReader(fh))
    assert filas and all(float(f["rho_intrasujeto"]) == 0.6 for f in filas)
    # fuera de [0, 1): el esquema lo rechaza (código 2)
    malo = [dict(recs[0], correlacion_intrasujeto=1.0)]
    (project / "malo.json").write_text(json.dumps(malo, ensure_ascii=False), "utf-8")
    code, _, err = _run(capsys, ["--agente", "extractor-datos-D", "extraccion", "depositar", "--datos",
                                 str(project / "malo.json")])
    assert code == 2 and "correlacion_intrasujeto" in err
