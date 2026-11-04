#!/bin/sh
# Hook PreToolUse (Bash) de Claude Code.
# Solo una persona firma: bloquea a los agentes cualquier orden con --firma-humana y cualquier acceso
# a la clave de los sellos de verificación. La persona firma ejecutando la orden en su propia terminal.
payload=$(cat)
case "$payload" in
  *--firma-humana*)
    echo "Bloqueado por el hook de NUTRIA: --firma-humana solo puede usarlo una persona, ejecutando la orden en su propia terminal. Registra la decisión como propuesta (sin firma) y pide a la persona responsable que la firme." >&2
    exit 2 ;;
  *clave_sello*|*NUTRIA_CLAVE_SELLO*)
    echo "Bloqueado por el hook de NUTRIA: la clave de los sellos de verificación no es accesible para los agentes." >&2
    exit 2 ;;
esac
exit 0
