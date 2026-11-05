#!/bin/sh
# Hook PreToolUse (Write|Edit|MultiEdit) de Claude Code.
# Los sellos de verificación solo los escribe nutria; la clave de los sellos no se edita. El registro
# (research/nutria.sqlite) y la procedencia (research/procedencia.jsonl, con cadena de hashes) solo los escribe la
# CLI nutria, y .git (reflog, referencias, configuración y hooks) no se edita. Solo se inspecciona el campo file_path (un documento que mencione estos ficheros no se bloquea), sin
# distinguir mayúsculas: en macOS y Windows, research/PROCEDENCIA.JSONL es el mismo fichero.
payload=$(cat)
target=$(printf '%s' "$payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n 1 | tr 'A-Z' 'a-z')
case "$target" in
  *.verificacion.json\")
    echo "Bloqueado por el hook de NUTRIA: los sellos de verificación solo los genera 'nutria informe comprobar <informe> --online --sellar'." >&2
    exit 2 ;;
  *clave_sello\"|*/.config/nutria/*)
    echo "Bloqueado por el hook de NUTRIA: la clave de los sellos y la configuración de nutria no pueden editarse desde Claude Code." >&2
    exit 2 ;;
  */.git/*|*\".git/*)
    echo "Bloqueado por el hook de NUTRIA: el repositorio git (.git: reflog, referencias, configuración y hooks) no se edita desde Claude Code; nutria procedencia verificar usa el reflog para detectar la historia reescrita." >&2
    exit 2 ;;
  *procedencia.jsonl\")
    echo "Bloqueado por el hook de NUTRIA: la procedencia (research/procedencia.jsonl) solo la escribe nutria, que mantiene su cadena de hashes." >&2
    exit 2 ;;
  *.sqlite\"|*.sqlite-wal\"|*.sqlite-shm\"|*.sqlite-journal\")
    echo "Bloqueado por el hook de NUTRIA: el registro (research/nutria.sqlite) solo lo escribe la CLI nutria." >&2
    exit 2 ;;
esac
exit 0
