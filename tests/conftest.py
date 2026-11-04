import csv
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bcg():
    """Conjunto de validación dat.bcg (metafor), recalculado desde los recuentos originales."""
    rows = list(csv.DictReader(open(ROOT / "ejemplos" / "validacion_bcg.csv", encoding="utf-8")))
    yi, vi, ablat = [], [], []
    for r in rows:
        a, b, c, d = (int(r[k]) for k in ("tpos", "tneg", "cpos", "cneg"))
        yi.append(np.log((a / (a + b)) / (c / (c + d))))
        vi.append(1 / a - 1 / (a + b) + 1 / c - 1 / (c + d))
        ablat.append(float(r["ablat"]))
    return np.array(yi), np.array(vi), np.array(ablat)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Proyecto nutria inicializado en un directorio temporal, sin red."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NUTRIA_HOME", raising=False)
    monkeypatch.setenv("NUTRIA_OFFLINE", "1")
    from nutria.cli import main
    assert main(["init"]) == 0
    return tmp_path
