#!/usr/bin/env python3
"""Prepara una carpeta de trabajo neutral para comparar nutria con otro sistema construido desde el mismo encargo.

Uso:
    python scripts/preparar_comparacion.py --otro RUTA_DEL_OTRO_SISTEMA --trabajo CARPETA_NUEVA
        [--nutria RUTA_DE_NUTRIA] [--nombre-otro motor-ganadero] [--forzar]

Crea en CARPETA_NUEVA, sin tocar ninguno de los dos sistemas:
  - CLAUDE.md                          reglas de la comparación (Claude Code las da a la sesión y a cada subagente);
  - PROMPT.md                          el encargo de comparación, con las rutas ya puestas;
  - .claude/settings.json              permisos (Workflow, fuentes oficiales, originales en solo lectura) y entorno;
  - comparacion/args_dimensiones.json  argumentos del workflow de comparación por dimensiones.

Los textos salen de docs/PROMPT_COMPARACION.md, que es la única fuente. Después: abre Claude Code en CARPETA_NUEVA y
escribe «Lee PROMPT.md y ejecútalo». Solo usa la biblioteca estándar.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DOC = Path("docs") / "PROMPT_COMPARACION.md"
WORKFLOW = Path(".claude") / "workflows" / "comparar-sistemas.js"
SECCIONES = {"reglas": "## Reglas de la carpeta de trabajo", "prompt": "## El prompt",
             "args": "## Argumentos del workflow de comparación"}
DOMINIOS = ("eur-lex.europa.eu", "food.ec.europa.eu", "www.efsa.europa.eu", "pubmed.ncbi.nlm.nih.gov",
            "eutils.ncbi.nlm.nih.gov", "europepmc.org", "www.ebi.ac.uk", "api.crossref.org", "doi.org",
            "api.datacite.org", "www.metafor-project.org", "wviechtb.github.io")
NOMBRE_OTRO = "motor-ganadero"


class Fallo(Exception):
    """Entrada no válida: se informa y se sale con código 2."""


def bloque(texto: str, encabezado: str) -> str:
    """Primer bloque de código (``` ... ```) que sigue al encabezado dado."""
    inicio = texto.find(encabezado)
    if inicio < 0:
        raise Fallo(f"docs/PROMPT_COMPARACION.md no tiene la sección «{encabezado}»")
    m = re.search(r"```[a-z]*\n(.*?)\n```", texto[inicio:], flags=re.DOTALL)
    if not m:
        raise Fallo(f"la sección «{encabezado}» no tiene un bloque de código")
    return m.group(1)


def regla_ruta(ruta: Path) -> str:
    """Ruta absoluta en el formato de las reglas de permisos de Claude Code (//ruta; en Windows, //c/ruta)."""
    posix = ruta.as_posix()
    if re.match(r"^[A-Za-z]:/", posix):
        posix = "/" + posix[0].lower() + posix[2:]
    return "/" + posix


def dentro(hija: Path, madre: Path) -> bool:
    return hija == madre or madre in hija.parents


def preparar(nutria: Path, otro: Path, trabajo: Path, nombre_otro: str = NOMBRE_OTRO, forzar: bool = False) -> dict:
    nutria, otro, trabajo = nutria.resolve(), otro.resolve(), trabajo.resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", nombre_otro) or nombre_otro == "nutria":
        raise Fallo("--nombre-otro: usa letras, cifras, guiones o guiones bajos, y que no sea «nutria»")
    doc = nutria / DOC
    if not doc.is_file():
        raise Fallo(f"no encuentro {DOC.as_posix()} en {nutria}: ¿es la carpeta de nutria, en la rama correcciones-auditoria?")
    flujo = nutria / WORKFLOW
    if not flujo.is_file() or '"evaluadores"' not in flujo.read_text("utf-8"):
        raise Fallo(f"{WORKFLOW.as_posix()} de {nutria} no admite el argumento «evaluadores»: actualiza nutria "
                    "(git pull en la rama correcciones-auditoria)")
    if not otro.is_dir():
        raise Fallo(f"no existe la carpeta del otro sistema: {otro}")
    if otro == nutria:
        raise Fallo("--otro es la carpeta de nutria: indica la del otro sistema")
    for sistema, ruta in (("nutria", nutria), (nombre_otro, otro)):
        if dentro(trabajo, ruta):
            raise Fallo(f"la carpeta de trabajo ({trabajo}) no puede ser la de {sistema} ni estar dentro de ella: Claude "
                        "Code cargaría sus instrucciones, sus hooks y sus permisos, y ese sistema jugaría en casa")
    existentes = [p for p in (trabajo / "CLAUDE.md", trabajo / "PROMPT.md", trabajo / ".claude" / "settings.json")
                  if p.exists()]
    if existentes and not forzar:
        raise Fallo("la carpeta de trabajo ya tiene " + ", ".join(p.name for p in existentes) +
                    ": usa una carpeta nueva, o --forzar para sobrescribirlos")

    avisos = []
    anidado = ""
    if dentro(nutria, otro):
        anidado = (f"- Aviso: nutria está dentro de la carpeta de {nombre_otro}. Al copiar {nombre_otro}, excluye esa "
                   "subcarpeta, y dime si eso cambia algo de lo que evalúas.")
    elif dentro(otro, nutria):
        anidado = (f"- Aviso: {nombre_otro} está dentro de la carpeta de nutria. Al copiar nutria, excluye esa "
                   "subcarpeta, y dime si eso cambia algo de lo que evalúas.")
    if anidado:
        avisos.append("un sistema está dentro de la carpeta del otro: las reglas piden excluirlo al copiar")
    for madre in trabajo.parents:
        if (madre / "CLAUDE.md").exists() or (madre / ".claude").is_dir():
            avisos.append(f"hay CLAUDE.md o .claude/ en una carpeta superior ({madre}): Claude Code también los cargará; "
                          "si son de uno de los dos sistemas, elige otra carpeta de trabajo")
            break

    def sustituir(texto: str) -> str:
        texto = texto.replace("RUTA_NUTRIA", nutria.as_posix()).replace("RUTA_OTRO", otro.as_posix())
        texto = texto.replace("RUTA_TRABAJO", trabajo.as_posix())
        return texto.replace(NOMBRE_OTRO, nombre_otro) if nombre_otro != NOMBRE_OTRO else texto

    fuente = doc.read_text("utf-8")
    reglas, prompt = sustituir(bloque(fuente, SECCIONES["reglas"])), sustituir(bloque(fuente, SECCIONES["prompt"]))
    if anidado:
        # El aviso va al final de la lista de rutas, antes de las reglas.
        if "\n\nREGLAS\n" not in reglas:
            raise Fallo("el bloque de reglas de docs/PROMPT_COMPARACION.md no tiene la sección REGLAS")
        reglas = reglas.replace("\n\nREGLAS\n", f"\n{anidado}\n\nREGLAS\n", 1)
    try:
        args = json.loads(sustituir(bloque(fuente, SECCIONES["args"])))
    except json.JSONDecodeError as exc:
        raise Fallo(f"los argumentos del workflow de docs/PROMPT_COMPARACION.md no son JSON válido: {exc}") from exc
    for texto in (reglas, prompt, json.dumps(args)):
        resto = re.search(r"RUTA_[A-Z]+", texto)
        if resto:
            raise Fallo(f"queda un marcador sin sustituir: {resto.group(0)}")

    ajustes = {
        "permissions": {
            "allow": ["Workflow", *[f"WebFetch(domain:{d})" for d in DOMINIOS]],
            "deny": [f"Edit({regla_ruta(nutria)}/**)", f"Edit({regla_ruta(otro)}/**)",
                     "Read(~/.config/nutria/**)", "Edit(~/.config/nutria/**)", "Read(**/.env)", "Read(**/.env.*)"],
            "additionalDirectories": [nutria.as_posix(), otro.as_posix()],
        },
        "env": {"NUTRIA_HOME": "",
                "NUTRIA_CLAVE_SELLO": (trabajo / "comparacion" / "tmp" / "clave_sello_pruebas").as_posix()},
    }

    for carpeta in ("copias", "privado", "pruebas", "tmp"):
        (trabajo / "comparacion" / carpeta).mkdir(parents=True, exist_ok=True)
    (trabajo / ".claude").mkdir(exist_ok=True)
    (trabajo / "CLAUDE.md").write_text(reglas + "\n", "utf-8")
    (trabajo / "PROMPT.md").write_text(prompt + "\n", "utf-8")
    (trabajo / ".claude" / "settings.json").write_text(json.dumps(ajustes, ensure_ascii=False, indent=2) + "\n", "utf-8")
    (trabajo / "comparacion" / "args_dimensiones.json").write_text(
        json.dumps(args, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return {"trabajo": trabajo.as_posix(), "nutria": nutria.as_posix(), "otro": otro.as_posix(),
            "nombre_otro": nombre_otro, "avisos": avisos,
            "ficheros": ["CLAUDE.md", "PROMPT.md", ".claude/settings.json", "comparacion/args_dimensiones.json"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    ap.add_argument("--otro", required=True, type=Path, help="carpeta del otro sistema (p. ej. motor-ganadero)")
    ap.add_argument("--trabajo", required=True, type=Path,
                    help="carpeta de trabajo nueva, fuera de los dos sistemas (se crea si no existe)")
    ap.add_argument("--nutria", type=Path, default=Path(__file__).resolve().parent.parent,
                    help="carpeta de nutria (por defecto, la de este script)")
    ap.add_argument("--nombre-otro", default=NOMBRE_OTRO, help=f"nombre del otro sistema (por defecto, {NOMBRE_OTRO})")
    ap.add_argument("--forzar", action="store_true", help="sobrescribe CLAUDE.md, PROMPT.md y settings.json si existen")
    a = ap.parse_args(argv)
    try:
        r = preparar(a.nutria, a.otro, a.trabajo, a.nombre_otro, a.forzar)
    except Fallo as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Carpeta de trabajo preparada: {r['trabajo']}")
    print("  " + ", ".join(r["ficheros"]))
    print(f"  nutria: {r['nutria']}\n  {r['nombre_otro']}: {r['otro']}")
    for aviso in r["avisos"]:
        print(f"AVISO: {aviso}")
    print("\nSiguiente paso (en una terminal sin entorno virtual activado):")
    print(f"  cd \"{r['trabajo']}\"")
    print("  claude")
    print("y escribe: Lee PROMPT.md y ejecútalo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
