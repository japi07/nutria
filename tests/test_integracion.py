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
