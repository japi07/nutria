#!/usr/bin/env python3
"""Demostración de extremo a extremo SIN RED de las herramientas `nutria`, en un proyecto temporal.

Uso (desde la raíz del repositorio, con el entorno del proyecto activado):

    python scripts/demo_sin_red.py              # crea un proyecto en el directorio temporal del sistema
    python scripts/demo_sin_red.py --dir /ruta  # o en un directorio nuevo o vacío
    python scripts/demo_sin_red.py --limpiar    # y lo borra al terminar

Recorre, con los ficheros de `ejemplos/` y con `--offline` en todas las órdenes: `init` y `doctor`, alta de una
oportunidad y una hipótesis, destino ruminal y conversión de dosis, doble extracción (depositar las copias A y B,
comparar con registro y conciliar), exportación para el análisis, metaanálisis del conjunto de validación BCG,
economía del ganadero y coherencia con la empresa, tres puntuaciones y el ranking, esqueleto de informe y su
comprobación, estado y verificación de la procedencia. Cada paso tiene su código de salida esperado; si alguno no
coincide, la demostración termina con código 1.

LO QUE ES SIMULADO (y nada de esto es evidencia):
- Los ficheros de `ejemplos/` son EJEMPLOS DE FORMATO con valores ilustrativos, salvo `validacion_bcg.csv` (el
  conjunto clásico BCG con el que se valida el metaanálisis frente a metafor).
- La doble extracción necesita una fuente registrada, verificada y con la retractación comprobada, y eso solo lo hace
  `nutria fuente agregar --doi <DOI>` consultando Crossref, OpenAlex y PubMed (con red). Sin red, la demostración
  registra con la API del registro, y SOLO en el proyecto temporal, una fuente de ejemplo con un DOI que no existe
  (prefijo 10.0000) marcada `verified_via = "simulacion_demo_sin_red"`, y deja en la procedencia el evento
  `demo_fuente_simulada`. Nunca hagas esto en un proyecto real: allí el registro solo se escribe con la CLI.
- Las puntuaciones son las del ejemplo de formato, registradas con tres identidades de evaluador para mostrar el
  ranking; no son la puntuación ciega de tres evaluadores independientes.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EJEMPLOS = REPO / "ejemplos"
DOI_SIMULADO = "10.0000/ejemplo-de-formato"   # el de ejemplos/extraccion_ejemplo.json; el prefijo 10.0000 no existe


def _entorno() -> dict:
    """Entorno de las órdenes: el código de este repositorio y sin variables que cambien el proyecto o el actor."""
    env = {k: v for k, v in os.environ.items() if k not in ("NUTRIA_HOME", "NUTRIA_AGENT")}
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(REPO / "src"), env.get("PYTHONPATH", "")) if p)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class Demo:
    def __init__(self, proyecto: Path, lineas: int = 12):
        self.proyecto = proyecto
        self.lineas = lineas
        self.fallos: list[str] = []
        self.paso = 0

    def titulo(self, texto: str) -> None:
        self.paso += 1
        print(f"\n[{self.paso}] {texto}", flush=True)

    def nutria(self, *args: str, esperado: int = 0, agente: str | None = None, nota: str = "") -> str:
        orden = ["nutria", "--offline"] + (["--agente", agente] if agente else []) + list(args)
        print("$ " + " ".join(a if " " not in a else repr(a) for a in orden), flush=True)
        proc = subprocess.run([sys.executable, "-m", *orden], cwd=self.proyecto, env=_entorno(),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        salida = (proc.stdout + proc.stderr).rstrip()
        lineas = salida.splitlines()
        for linea in lineas[: self.lineas]:
            print("  " + linea)
        if len(lineas) > self.lineas:
            print(f"  ... ({len(lineas) - self.lineas} líneas más)")
        estado = "como se esperaba" if proc.returncode == esperado else f"SE ESPERABA {esperado}"
        print(f"  -> código {proc.returncode} ({estado}){' — ' + nota if nota else ''}", flush=True)
        if proc.returncode != esperado:
            self.fallos.append(f"paso {self.paso}: {' '.join(orden)} -> {proc.returncode} (esperado {esperado})")
        return proc.stdout


def _fuente_simulada(proyecto: Path) -> str:
    """Registra en el proyecto temporal la fuente de ejemplo como verificada (SIMULACIÓN; ver el docstring)."""
    sys.path.insert(0, str(REPO / "src"))
    os.environ.pop("NUTRIA_HOME", None)
    from nutria import db
    from nutria.config import load_config
    from nutria.provenance import log_event

    cfg = load_config(proyecto)
    if cfg.root.resolve() != proyecto.resolve():
        raise SystemExit(f"El proyecto temporal no es la raíz de la configuración ({cfg.root}): no se toca nada")
    conn = db.connect(cfg.db_path)
    try:
        fuente = db.upsert_source(conn, {
            "doi": DOI_SIMULADO, "year": 2026, "verified": True, "verified_via": "simulacion_demo_sin_red",
            "retracted": False,
            "title": "EJEMPLO DE FORMATO - fuente simulada para la demostración sin red (no es una publicación)"},
            actor="demo_sin_red")
    finally:
        conn.close()
    log_event(cfg, "demo_fuente_simulada", inputs={"doi": DOI_SIMULADO},
              outputs={"fuente": fuente, "aviso": "verificación SIMULADA sin red; solo en el proyecto de la demostración"},
              actor="demo_sin_red")
    return fuente


def _escribir(ruta: Path, datos) -> Path:
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), "utf-8")
    return ruta


def ejecutar(proyecto: Path) -> int:
    d = Demo(proyecto)
    print(f"Proyecto temporal de la demostración: {proyecto}")
    print("Todas las órdenes van con --offline: no se hace ninguna petición de red.")

    d.titulo("Proyecto nuevo: nutria.toml, research/nutria.sqlite y research/procedencia.jsonl")
    d.nutria("init")
    d.titulo("Comprobación del entorno sin red (dice qué comprobaría con red)")
    d.nutria("doctor", nota="con red, comprueba también las fuentes; sale con 1 si falla alguna esencial")

    d.titulo("Alta de una oportunidad y de una hipótesis (ejemplos de formato)")
    d.nutria("oportunidad", "nueva", "--datos", str(EJEMPLOS / "oportunidad_ejemplo.json"), agente="director-cientifico")
    d.nutria("hipotesis", "nueva", "--datos", str(EJEMPLOS / "hipotesis_ejemplo.json"), agente="director-cientifico")

    d.titulo("Destino ruminal (Ørskov y McDonald, 1979) con valores ILUSTRATIVOS y conversión de dosis")
    d.nutria("destino", "ruminal", "--a", "0.1", "--b", "0.8", "--kd", "0.08", "--kp", "0.06", "--dosis", "50",
             "--unidad-dosis", "g/animal/d", "--digestibilidad-intestinal", "0.8", agente="fisiologia-bioquimica")
    d.nutria("dosis", "convertir", "50", "--desde", "g_animal_dia", "--ingestion-ms", "20", "--portador",
             "concentrado", "--base", "tal_cual", "--ingestion-pienso", "8", "--precio-eur-kg", "5",
             agente="nutricion-formulacion")

    d.titulo(f"SIMULACIÓN: fuente de ejemplo {DOI_SIMULADO} registrada como verificada (en uso real: "
             "`nutria fuente agregar --doi <DOI>`, con red)")
    fuente = _fuente_simulada(proyecto)
    print(f"  fuente {fuente} (verified_via = simulacion_demo_sin_red; evento demo_fuente_simulada en la procedencia)")
    d.nutria("fuente", "ver", fuente)

    d.titulo("Doble extracción: cada extractor deposita su copia; un tercero compara con registro y concilia")
    # Discrepancias como las que resuelve ejemplos/conciliacion_ejemplo.json: la copia B toma el SEM de la columna
    # contigua como media; ninguna copia da la duración que dice la fuente (42 días); los eventos del segundo
    # registro no coinciden y quedan sin resolver.
    copia_a = json.loads((EJEMPLOS / "extraccion_ejemplo.json").read_text("utf-8"))
    copia_a[0]["duracion_dias"] = 21
    copia_b = json.loads((EJEMPLOS / "extraccion_ejemplo.json").read_text("utf-8"))
    copia_b[0]["media_tratamiento"] = 0.6
    copia_b[0]["duracion_dias"] = 40
    copia_b[1]["eventos_tratamiento"] = 5
    datos = proyecto / "research" / "datos" / "demo"
    datos.mkdir(parents=True, exist_ok=True)
    a = _escribir(datos / "extraccion_demo_A.json", copia_a)
    b = _escribir(datos / "extraccion_demo_B.json", copia_b)
    d.nutria("extraccion", "depositar", "--datos", str(a), agente="extractor-datos-A")
    d.nutria("extraccion", "depositar", "--datos", str(b), agente="extractor-datos-B")
    d.nutria("extraccion", "comparar", str(a), str(b), "--registrar", agente="extractor-datos-conciliador",
             esperado=1, nota="1 = comparación registrada CON discrepancias (CE-0001)")
    d.nutria("extraccion", "conciliar", "CE-0001", "--datos", str(EJEMPLOS / "conciliacion_ejemplo.json"),
             agente="extractor-datos-conciliador",
             nota="el registro 1 se resuelve releyendo la fuente; el 2 queda sin resolver y no cuenta")
    d.nutria("extraccion", "comparaciones")
    d.nutria("extraccion", "exportar", "--variable", "producción de leche", "--especie", "vacuno lechero", "--csv",
             str(datos / "leche.csv"), agente="estadistico",
             nota="solo exporta las filas con doble extracción verificada")

    d.titulo("Metaanálisis del conjunto de validación BCG (REML con HKSJ modificado por defecto) con un moderador")
    d.nutria("meta", "analizar", str(EJEMPLOS / "validacion_bcg.csv"), "--moderador", "ablat", agente="estadistico")

    d.titulo("Economía del ganadero y coherencia con el modelo de la empresa (modelos ILUSTRATIVOS)")
    d.nutria("econ", "ganadero", str(EJEMPLOS / "econ_ganadero_ILUSTRATIVO.toml"), "--sim", "2000",
             agente="economia-mercado-pi")
    d.nutria("econ", "coherencia", str(EJEMPLOS / "econ_ganadero_ILUSTRATIVO.toml"),
             str(EJEMPLOS / "econ_desarrollador_ILUSTRATIVO.toml"), agente="economia-mercado-pi")

    d.titulo("Tres puntuaciones (las del ejemplo de formato) y el ranking: sin compuertas firmadas queda CONDICIONAL")
    plantilla = json.loads((EJEMPLOS / "puntuacion_ejemplo.json").read_text("utf-8"))
    for n in (1, 2, 3):
        p = dict(plantilla, evaluador=f"evaluador-{n}")
        d.nutria("puntuar", "registrar", "--datos", str(_escribir(proyecto / f"puntuacion_{n}.json", p)),
                 agente=f"evaluador-{n}")
    d.nutria("puntuar", "ranking", "--sim", "1000", agente="director-cientifico")

    d.titulo("Esqueleto de informe y su comprobación sin red (es un borrador con [POR COMPLETAR])")
    d.nutria("informe", "nuevo", "O-0001", agente="director-cientifico")
    d.nutria("informe", "comprobar", "research/informes/O-0001.md", agente="verificador-citas",
             nota="sin --online no se verifican referencias y no hay sello")

    d.titulo("Estado del registro y cadena de la procedencia")
    d.nutria("estado")
    d.nutria("procedencia", "verificar")

    print()
    if d.fallos:
        print("La demostración NO ha ido como se esperaba:")
        for f in d.fallos:
            print("  - " + f)
        return 1
    print(f"Demostración completa: {d.paso} pasos con los códigos de salida esperados.")
    print("Nada de lo registrado es evidencia: son ejemplos de formato y una fuente simulada.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, help="directorio del proyecto temporal (nuevo o vacío)")
    parser.add_argument("--limpiar", action="store_true", help="borra el proyecto temporal al terminar")
    args = parser.parse_args(argv)
    proyecto = args.dir.resolve() if args.dir else Path(tempfile.mkdtemp(prefix="nutria_demo_"))
    proyecto.mkdir(parents=True, exist_ok=True)
    if any(proyecto.iterdir()):   # antes del try: un directorio con contenido no se usa ni se borra
        print(f"{proyecto} no está vacío: indica un directorio nuevo o vacío con --dir", file=sys.stderr)
        return 2
    try:
        return ejecutar(proyecto)
    finally:
        if args.limpiar:
            shutil.rmtree(proyecto, ignore_errors=True)
        else:
            print(f"Proyecto de la demostración: {proyecto} (bórralo cuando quieras)")


if __name__ == "__main__":
    sys.exit(main())
