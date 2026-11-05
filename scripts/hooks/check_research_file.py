#!/usr/bin/env python3
"""Hook PostToolUse de Claude Code: valida lo que se escribe en research/ (falla en cerrado).

- research/informes/**/*.md (también en subcarpetas, sin distinguir mayúsculas en la extensión): estructura
  completa (20 secciones), referencias, etiquetas, Anexo A por afirmación y, si hay registro, fuentes registradas
  y verificadas. Si el documento está en estado revisado o final (o su estado no se puede leer), exige además la
  revisión humana registrada (`nutria informe revisar`) y un sello válido.
- research/planes/**/*.md y research/revisiones/**/*.md: lo mismo sin exigir las 20 secciones.
- research/oportunidades/**/*.json y research/hipotesis/**/*.json: validación contra su esquema.

El estado se lee con la misma función que usan `nutria informe comprobar` y `nutria informe sello`. La ruta se
comprueba sin resolver y resuelta (un enlace simbólico no saca el fichero de la comprobación). Si hay errores
devuelve {"decision": "block", "reason": ...}. Si el hook no puede funcionar (dependencias ausentes, fallo interno,
entrada sin ruta legible) y lo escrito puede estar en research/, también bloquea. Actúa después de escribir y solo
con Write, Edit y MultiEdit: no ve lo que se escribe con Bash ni con la CLI (compruébalo con `nutria informe sello`).
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
MD_SUFFIXES = (".md", ".markdown")


def block(reason: str, code: int = 0) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    sys.exit(code)


def _variants(raw_path: str) -> list[Path]:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path(os.environ.get("CLAUDE_PROJECT_DIR") or ROOT) / path
    out = [Path(os.path.abspath(path))]
    try:
        out.append(path.resolve())
    except OSError:
        pass
    return out


def _research_parts(paths: list[Path]) -> tuple[str, ...] | None:
    roots = {ROOT, Path(os.path.abspath(ROOT))}
    for path in paths:
        for root in roots:
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if len(rel.parts) >= 3 and rel.parts[0].lower() == "research":
                return tuple(p.lower() for p in rel.parts)
    return None


def check_markdown(path: Path, full: bool) -> None:
    from nutria.reports.check import check_report, open_registry_for, stamp_status, status_info
    text = path.read_text("utf-8")
    conn = open_registry_for(path)
    try:
        errors = [str(i) for i in check_report(text, require_sections=full, conn=conn, path=path)
                  if i.severity == "error"]
        info = status_info(text)
        if info.strict or (full and info.value is None):
            ok, msg = stamp_status(path, conn=conn)
            if not ok:
                errors.append(f"[ERROR] El documento se marca '{info.value or 'sin estado legible'}' pero: {msg}")
    finally:
        if conn is not None:
            conn.close()
    if errors:
        block(f"{path.name}: {len(errors)} error(es) de verificación. Corrígelos (no inventes referencias; usa "
              "[DESC] si falta un dato):\n" + "\n".join(errors[:25]))


def check_json(path: Path, kind: str) -> None:
    import jsonschema
    from importlib import resources
    if "_puntuacion_" in path.name:
        kind = "puntuacion"
    try:
        data = json.loads(path.read_text("utf-8"))
    except ValueError as exc:
        block(f"{path.name}: JSON no válido ({exc})")
    schema = json.loads(resources.files("nutria.data").joinpath(f"schemas/{kind}.schema.json").read_text("utf-8"))
    problems = []
    for item in (data if isinstance(data, list) else [data]):
        for err in jsonschema.Draft202012Validator(schema).iter_errors(item):
            problems.append(f"{'/'.join(map(str, err.path)) or '(raíz)'}: {err.message}")
    if problems:
        block(f"{path.name} no cumple el esquema '{kind}':\n" + "\n".join(problems[:15]))


def main() -> None:
    raw = sys.stdin.read()
    raw_path = None
    try:
        payload = json.loads(raw)
        tool_input = payload.get("tool_input") or {}
        raw_path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
    except Exception:  # noqa: BLE001 - entrada inesperada: se decide abajo
        raw_path = None
    if not isinstance(raw_path, str) or not raw_path.strip():
        if "research" in raw.lower():
            block("Hook de verificación de NUTRIA: la entrada no trae una ruta de fichero legible y puede afectar a "
                  "research/; no se puede validar. Revisa el fichero con `nutria informe comprobar`.")
        return
    paths = _variants(raw_path)
    parts = _research_parts(paths)
    if parts is None:
        return
    target = next((p for p in paths if p.is_file()), None)
    if target is None:
        return  # no se escribió nada
    kind, suffix = parts[1], target.suffix.lower()
    try:
        if kind == "informes" and suffix in MD_SUFFIXES:
            check_markdown(target, full=True)
        elif kind in ("planes", "revisiones") and suffix in MD_SUFFIXES:
            check_markdown(target, full=False)
        elif kind in ("oportunidades", "hipotesis") and suffix == ".json":
            check_json(target, "hipotesis" if kind == "hipotesis" else "oportunidad")
    except SystemExit:
        raise
    except ImportError as exc:
        # Con run_hook.sh (NUTRIA_HOOK_RUNNER=1), el código 3 le pide probar el siguiente intérprete (a este le faltan
        # dependencias); si ninguno funciona, run_hook.sh bloquea con este mismo mensaje. Ejecutado directamente,
        # bloquea con código 0 (Claude Code solo atiende la decisión "block" con código 0).
        block(f"Hook de verificación no operativo ({exc}). Instala el proyecto con `pip install -e .` en el entorno "
              "que usa Claude Code (scripts/hooks/run_hook.sh busca .venv).",
              3 if os.environ.get("NUTRIA_HOOK_RUNNER") == "1" else 0)
    except Exception as exc:  # noqa: BLE001 - falla en cerrado
        block(f"El hook de verificación falló al revisar {target.name}: {exc.__class__.__name__}: {exc}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - falla en cerrado también ante errores inesperados
        block(f"El hook de verificación falló: {exc.__class__.__name__}: {exc}")
