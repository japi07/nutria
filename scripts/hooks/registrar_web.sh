#!/bin/sh
# Hook PostToolUse (WebFetch|WebSearch) de Claude Code: registra cada consulta web en research/procedencia.jsonl
# con scripts/hooks/registrar_web.py (URL o consulta, fecha y SHA-256 de la respuesta; con
# NUTRIA_PROCEDENCIA_WEB=hash, solo el dominio y huellas). Nunca bloquea: si no hay Python o el registro falla,
# avisa con systemMessage y sale con 0.
dir=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$dir/../.." && pwd)
payload=$(cat)
for py in "$root/.venv/bin/python" "$root/.venv/Scripts/python.exe" python3 python; do
  if [ -x "$py" ] || command -v "$py" >/dev/null 2>&1; then
    if printf '%s' "$payload" | "$py" "$dir/registrar_web.py"; then
      exit 0
    fi
    printf '%s\n' '{"systemMessage": "NUTRIA: falló scripts/hooks/registrar_web.py; la consulta web no quedó registrada en la procedencia."}'
    exit 0
  fi
done
printf '%s\n' '{"systemMessage": "NUTRIA: no se encontró Python; la consulta web no quedó registrada en la procedencia."}'
exit 0
