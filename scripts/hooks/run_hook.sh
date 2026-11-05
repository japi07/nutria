#!/bin/sh
# Ejecuta un hook escrito en Python con el intérprete del proyecto (.venv) si existe, o con python3/python.
# Falla en cerrado: si un intérprete no existe o sale con un código distinto de 0 (p. ej. 1 por SyntaxError en un
# Python antiguo, 127 si no se encuentra, 9009 del alias de la Microsoft Store en Windows), prueba el siguiente; si
# ninguno funciona y el fichero afectado está en research/, devuelve la decisión "block".
case "$0" in
  */*) here=${0%/*} ;;
  *) here=. ;;
esac
dir=$(cd "$here" && pwd)
root=$(cd "$dir/../.." && pwd)
script="$dir/$1"
payload=$(cat)
tried=""
for py in "$root/.venv/bin/python" "$root/.venv/Scripts/python.exe" python3 python; do
  case "$py" in
    /*) [ -x "$py" ] || continue ;;
    *) command -v "$py" >/dev/null 2>&1 || continue ;;
  esac
  out=$(printf '%s' "$payload" | "$py" "$script" 2>/dev/null)
  rc=$?
  if [ "$rc" -eq 0 ]; then
    [ -n "$out" ] && printf '%s\n' "$out"
    exit 0
  fi
  tried="$tried ${py##*/}=$rc"
done
target=$(printf '%s' "$payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n 1)
[ -n "$target" ] || target=$payload
case "$target" in
  *research/*|*research\\*)
    printf '{"decision": "block", "reason": "Hook de verificación de NUTRIA no operativo: ningún intérprete de Python funcionó (%s). Instala el proyecto (pip install -e .) en .venv o activa el entorno antes de abrir Claude Code."}\n' "${tried:- ninguno encontrado}" ;;
esac
exit 0
