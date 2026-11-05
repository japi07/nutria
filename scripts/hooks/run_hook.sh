#!/bin/sh
# Ejecuta un hook escrito en Python con el intérprete del proyecto (.venv) si existe, o con python3/python.
# Falla en cerrado: si un intérprete no existe o sale con un código distinto de 0 (p. ej. 1 por SyntaxError en un
# Python antiguo, 127 si no se encuentra, 9009 del alias de la Microsoft Store en Windows, 3 si le faltan las
# dependencias del proyecto), prueba el siguiente; si ninguno funciona y el fichero afectado está en research/,
# devuelve la decisión "block" (con el motivo del último intérprete que lo explicó, si lo hay).
case "$0" in
  */*) here=${0%/*} ;;
  *) here=. ;;
esac
dir=$(cd "$here" && pwd)
root=$(cd "$dir/../.." && pwd)
script="$dir/$1"
payload=$(cat)
tried=""
last_block=""
NUTRIA_HOOK_RUNNER=1
export NUTRIA_HOOK_RUNNER
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
  case "$out" in
    *'"decision": "block"'*) last_block=$out ;;
  esac
done
target=$(printf '%s' "$payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n 1)
[ -n "$target" ] || target=$payload
# Sin distinguir mayúsculas (Research/ en sistemas de ficheros que no las distinguen), con / o con \ (que en el
# JSON de la entrada va escapado como \\).
case "$target" in
  *[Rr][Ee][Ss][Ee][Aa][Rr][Cc][Hh]/*|*[Rr][Ee][Ss][Ee][Aa][Rr][Cc][Hh]\\*)
    if [ -n "$last_block" ]; then
      printf '%s\n' "$last_block"
    else
      printf '{"decision": "block", "reason": "Hook de verificación de NUTRIA no operativo: ningún intérprete de Python funcionó (%s). Instala el proyecto (pip install -e .) en .venv o activa el entorno antes de abrir Claude Code."}\n' "${tried:- ninguno encontrado}"
    fi ;;
esac
exit 0
