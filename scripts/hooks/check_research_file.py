#!/usr/bin/env python3
"""Hook PostToolUse de Claude Code: valida lo que se escribe en research/.

- research/informes/*.md: estructura (20 secciones), referencias [Rn] definidas, identificadores
  en la bibliografía y [PUB] con referencia. Si el encabezado dice `estado: revisado` o `final`,
  exige un sello de verificación válido.
- research/oportunidades/*.json y research/hipotesis/*.json: validación contra el esquema.

Si hay errores devuelve {"decision": "block", "reason": ...} para que Claude los corrija.
Nunca falla de forma ruidosa: si no puede analizar la entrada, no hace nada.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    sys.exit(0)


def check_report(path: Path) -> None:
    from nutria.reports.check import check_report, stamp_status
    text = path.read_text("utf-8")
    errors = [str(i) for i in check_report(text) if i.severity == "error"]
    status = re.search(r"^estado:\s*(\S+)", text, re.MULTILINE)
    if status and status.group(1).lower() in ("revisado", "final"):
        ok, msg = stamp_status(path)
        if not ok:
            errors.append(f"[ERROR] El informe se marca '{status.group(1)}' pero {msg}")
    if errors:
        block(f"{path.name}: {len(errors)} error(es) de verificación. Corrígelos (no inventes referencias; usa [DESC] "
              "si falta un dato):\n" + "\n".join(errors[:25]))


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
    items = data if isinstance(data, list) else [data]
    problems = []
    for item in items:
        for err in jsonschema.Draft202012Validator(schema).iter_errors(item):
            problems.append(f"{'/'.join(map(str, err.path)) or '(raíz)'}: {err.message}")
    if problems:
        block(f"{path.name} no cumple el esquema '{kind}':\n" + "\n".join(problems[:15]))


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        file_path = Path(payload.get("tool_input", {}).get("file_path", ""))
    except Exception:  # noqa: BLE001 - entrada inesperada: no bloquear
        return
    if not file_path.is_file():
        return
    try:
        rel = file_path.resolve().relative_to(ROOT)
    except ValueError:
        return
    parts = rel.parts
    try:
        if len(parts) >= 3 and parts[0] == "research" and parts[1] == "informes" and rel.suffix == ".md":
            check_report(file_path)
        elif len(parts) >= 3 and parts[0] == "research" and parts[1] in ("oportunidades", "hipotesis") \
                and rel.suffix == ".json":
            check_json(file_path)
    except ImportError:
        return


if __name__ == "__main__":
    main()
