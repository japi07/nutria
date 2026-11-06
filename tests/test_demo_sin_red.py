"""La demostración de extremo a extremo sin red (scripts/demo_sin_red.py) funciona tal cual (D10-11).

Se ejecuta como la ejecutaría una persona, en un subproceso y en un proyecto temporal, con la red inutilizada (un
proxy que no existe): si algún paso intentara conectarse, fallaría con un código distinto del esperado.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "scripts" / "demo_sin_red.py"


def _huella(ruta: Path):
    return (ruta.stat().st_mtime_ns, ruta.stat().st_size) if ruta.exists() else None


def test_demo_sin_red_recorre_todos_los_pasos_con_los_codigos_esperados(tmp_path):
    proyecto = tmp_path / "demo"
    registro_repo = [ROOT / "research" / "nutria.sqlite", ROOT / "research" / "procedencia.jsonl"]
    antes = [_huella(r) for r in registro_repo]
    env = {k: v for k, v in os.environ.items() if k not in ("NUTRIA_HOME", "NUTRIA_AGENT")}
    env.update(HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", http_proxy="http://127.0.0.1:9",
               https_proxy="http://127.0.0.1:9", NO_PROXY="", no_proxy="")
    proc = subprocess.run([sys.executable, str(DEMO), "--dir", str(proyecto)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=600, check=False)
    salida = proc.stdout + proc.stderr
    assert proc.returncode == 0, salida[-4000:]
    assert "Demostración completa" in salida
    for fragmento in ("Registradas: O-0001", "Registradas: H-0001", "CE-0001", "CONDICIONAL",
                      "HKSJ modificado", "SIMULACIÓN", "Resultado: íntegra"):
        assert fragmento in salida, fragmento
    # Lo que deja: el registro y la procedencia del proyecto temporal, el esqueleto del informe y el CSV de análisis.
    for rel in ("nutria.toml", "research/nutria.sqlite", "research/procedencia.jsonl",
                "research/informes/O-0001.md", "research/datos/demo/leche.csv"):
        assert (proyecto / rel).is_file(), rel
    # La fuente simulada queda declarada como tal en la procedencia.
    assert "demo_fuente_simulada" in (proyecto / "research" / "procedencia.jsonl").read_text("utf-8")
    # El registro y la procedencia del repositorio (si existen) no se tocan.
    assert [_huella(r) for r in registro_repo] == antes


def test_demo_sin_red_no_usa_ni_borra_un_directorio_con_contenido(tmp_path):
    (tmp_path / "ocupado.txt").write_text("x", "utf-8")
    proc = subprocess.run([sys.executable, str(DEMO), "--dir", str(tmp_path), "--limpiar"], capture_output=True,
                          text=True, encoding="utf-8", timeout=120, check=False)
    assert proc.returncode == 2 and "no está vacío" in proc.stdout + proc.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ocupado.txt"]
