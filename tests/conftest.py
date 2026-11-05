import csv
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bcg():
    """Conjunto de validación dat.bcg (metafor), recalculado desde los recuentos originales."""
    with open(ROOT / "ejemplos" / "validacion_bcg.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    yi, vi, ablat = [], [], []
    for r in rows:
        a, b, c, d = (int(r[k]) for k in ("tpos", "tneg", "cpos", "cneg"))
        yi.append(np.log((a / (a + b)) / (c / (c + d))))
        vi.append(1 / a - 1 / (a + b) + 1 / c - 1 / (c + d))
        ablat.append(float(r["ablat"]))
    return np.array(yi), np.array(vi), np.array(ablat)


@pytest.fixture(autouse=True)
def _entorno_de_persona(monkeypatch, tmp_path_factory):
    """Cada prueba se ejecuta como en la terminal de una persona, se lance pytest donde se lance (también dentro de
    Claude Code): sin las variables de entorno de Claude Code ni NUTRIA_AGENT y sin recorrer los procesos antecesores
    reales (PROC_ROOT apunta a un directorio vacío, como en un sistema sin /proc).

    La CLI rechaza la firma humana en cuanto ve alguna de esas señales. Las pruebas de la vía de los agentes las
    activan explícitamente (monkeypatch.setenv, --agente o un /proc simulado). Un subproceso no hereda PROC_ROOT: si
    firma, ve los procesos reales y, lanzado desde Claude Code, se rechaza; por eso las pruebas que firman como una
    persona llaman a `main()` en el mismo proceso."""
    from nutria import provenance
    for var in (*provenance.AGENT_ENV_VARS, "NUTRIA_AGENT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(provenance, "PROC_ROOT", str(tmp_path_factory.mktemp("sin_proc") / "proc"))
    monkeypatch.setattr(provenance, "_PROCESS_CACHE", {})


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Proyecto nutria inicializado en un directorio temporal, sin red."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NUTRIA_HOME", raising=False)
    monkeypatch.setenv("NUTRIA_OFFLINE", "1")
    from nutria.cli import main
    assert main(["init"]) == 0
    return tmp_path
