#!/bin/sh
# Ejecuta un hook escrito en Python con el intérprete del proyecto (.venv) si existe, o con python3/python.
# Si no hay intérprete y el fichero afectado está en research/, bloquea (falla cerrado).
dir=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$dir/../.." && pwd)
script="$dir/$1"
payload=$(cat)
for py in "$root/.venv/bin/python" "$root/.venv/Scripts/python.exe" python3 python; do
  if [ -x "$py" ] || command -v "$py" >/dev/null 2>&1; then
    printf '%s' "$payload" | "$py" "$script"
    exit $?
  fi
done
case "$payload" in
  *research/*|*research\\\\*)
    printf '%s\n' '{"decision": "block", "reason": "Hook de verificación de NUTRIA no operativo: no se encontró Python. Instala el proyecto (pip install -e .) o crea el entorno .venv."}' ;;
esac
exit 0
