#!/usr/bin/env python3
"""Hook PostToolUse de Claude Code: valida lo que se escribe en research/ (falla cerrado).

- research/informes/*.md: estructura completa (20 secciones), referencias, etiquetas, citas autor-año,
  y, si el informe está en estado revisado o final, respaldo en el Anexo A, revisión humana y sello válido.
- research/planes/*.md y research/revisiones/*.md: referencias y etiquetas (sin exigir secciones).
- research/oportunidades/*.json y research/hipotesis/*.json: validación contra su esquema.

Si hay errores devuelve {"decision": "block", "reason": ...} para que Claude los corrija. Si el hook no
puede funcionar (dependencias ausentes, fallo interno) y el fichero está en research/, también bloquea.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    sys.exit(0)


def check_markdown(path: Path, full: bool) -> None:
    from nutria.reports.check import STRICT_STATUSES, check_report, report_status, stamp_status
    text = path.read_text("utf-8")
    errors = [str(i) for i in check_report(text, require_sections=full) if i.severity == "error"]
    status = report_status(text)
    if status in STRICT_STATUSES:
        ok, msg = stamp_status(path)
        if not ok:
            errors.append(f"[ERROR] El documento se marca '{status}' pero: {msg}")
    if errors:
        block(f"{path.name}: {len(errors)} error(es) de verificación. Corrígelos (no inventes referencias; usa "
              "[DESC] si falta un dato):\n" + "\n".join(errors[:25]))


def check_json(path: Path) -> None:
    import jsonschema
    if "_puntuacion_" in path.name:
        kind = "puntuacion"
    elif path.parent.name == "hipotesis":
        kind = "hipotesis"
    else:
        kind = "oportunidad"
    try:
        data = json.loads(path.read_text("utf-8"))
    except ValueError as exc:
        block(f"{path.name}: JSON no válido ({exc})")
    schema = json.loads((ROOT / "src" / "nutria" / "data" / "schemas" / f"{kind}.schema.json").read_text("utf-8"))
    problems = []
    for item in (data if isinstance(data, list) else [data]):
        for err in jsonschema.Draft202012Validator(schema).iter_errors(item):
            problems.append(f"{'/'.join(map(str, err.path)) or '(raíz)'}: {err.message}")
    if problems:
        block(f"{path.name} no cumple el esquema '{kind}':\n" + "\n".join(problems[:15]))


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        file_path = Path(payload.get("tool_input", {}).get("file_path", ""))
    except Exception:  # noqa: BLE001 - entrada inesperada: nada que validar
        return
    if not file_path.is_file():
        return
    try:
        rel = file_path.resolve().relative_to(ROOT)
    except ValueError:
        return
    parts = rel.parts
    if len(parts) < 3 or parts[0] != "research":
        return
    try:
        if parts[1] == "informes" and rel.suffix == ".md":
            check_markdown(file_path, full=True)
        elif parts[1] in ("planes", "revisiones") and rel.suffix == ".md":
            check_markdown(file_path, full=False)
        elif parts[1] in ("oportunidades", "hipotesis") and rel.suffix == ".json":
            check_json(file_path)
    except SystemExit:
        raise
    except ImportError as exc:
        block(f"Hook de verificación no operativo ({exc}). Instala el proyecto con `pip install -e .` en el entorno "
              "que usa Claude Code (scripts/hooks/run_hook.sh busca .venv).")
    except Exception as exc:  # noqa: BLE001 - falla cerrado
        block(f"El hook de verificación falló al revisar {file_path.name}: {exc.__class__.__name__}: {exc}")


if __name__ == "__main__":
    main()
