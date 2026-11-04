#!/bin/sh
# Hook PreToolUse (Write|Edit|MultiEdit) de Claude Code.
# Los sellos de verificación solo los escribe nutria; la clave de los sellos no se edita.
# Solo se inspecciona el campo file_path (un documento que mencione los sellos no se bloquea).
payload=$(cat)
target=$(printf '%s' "$payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n 1)
case "$target" in
  *.verificacion.json\")
    echo "Bloqueado por el hook de NUTRIA: los sellos de verificación solo los genera 'nutria informe comprobar <informe> --online --sellar'." >&2
    exit 2 ;;
  *clave_sello\")
    echo "Bloqueado por el hook de NUTRIA: la clave de los sellos no puede editarse desde Claude Code." >&2
    exit 2 ;;
esac
exit 0
