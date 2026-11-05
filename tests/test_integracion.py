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
