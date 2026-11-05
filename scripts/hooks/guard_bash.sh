#!/bin/sh
# Hook PreToolUse (Bash) de Claude Code: barrera de texto frente a los atajos de los agentes.
#
# La barrera de fondo está en la CLI: ningún analizador admite abreviaturas de las opciones, y la firma humana se
# rechaza con --agente, con NUTRIA_AGENT, dentro de Claude Code (sus variables de entorno en el proceso o en el entorno
# inicial de un antecesor, o el propio Claude Code entre los antecesores) o sin terminal de control. Este hook bloquea
# además, antes de ejecutar la orden (solo se analiza tool_input.command; si no se encuentra, toda la entrada):
#  1. la opción --firma-humana y cualquier prefijo suyo desde --fir, aunque se escriba con comillas, barras
#     invertidas, acentos graves o sustituciones vacías ("--firma-""humana", --fir\ma, --fir$()ma), y su
#     construcción con secuencias de escape ($'\x2d...', printf '\055...');
#  2. tocar las variables con las que la CLI reconoce a los agentes (CLAUDECODE, CLAUDE_CODE_*, AI_AGENT; unset,
#     env -u, export o asignaciones de NUTRIA_AGENT), vaciar el entorno (env -i, env -, exec -c), cambiar de usuario
#     (sudo, doas, runuser, setpriv), desvincular procesos (setsid, nohup, disown, os.fork...) o abrir
#     pseudoterminales (script, unbuffer, socat, el módulo pty de Python);
#  3. escribir el registro por fuera de la CLI: sqlite3, .connect(, copias, borrados, redirecciones o restauraciones
#     con git de research/ (nutria.sqlite, procedencia.jsonl); importar nutria.db o nutria.provenance;
#  4. la clave de los sellos (clave_sello, NUTRIA_CLAVE_SELLO, ~/.config/nutria) y el módulo de sellos
#     (nutria.sello, sello.sign, write_stamp, from nutria import sello);
#  5. identidad, dentro de un subagente (campo agent_type que Claude Code añade a la entrada del hook): --agente no
#     puede ser el nombre de otro rol del proyecto (.claude/agents/<rol>.md) ni un evaluador-<n>, salvo para el
#     propio evaluador-independiente, que se identifica siempre con --agente evaluador-<n>, no importa nutria desde
#     Python y no lee la base de datos del registro.
# Compara texto: no detiene a un proceso del mismo usuario decidido a falsificar (ver README).
payload=$(cat)
una_linea=$(printf '%s' "$payload" | tr '\n\r\t' '   ')
# tool_input.command tal como va en el JSON. Una cadena JSON no puede contener comillas sin escapar, así que el texto
# de la orden no puede imitar la clave "command" ni la clave "agent_type".
orden_json=$(printf '%s' "$una_linea" | grep -oE '"command"[[:space:]]*:[[:space:]]*"([^"\\]|\\.)*"' | head -n 1)
if [ -n "$orden_json" ]; then
  base=$(printf '%s' "$orden_json" | sed -e 's/^"command"[[:space:]]*:[[:space:]]*"//' -e 's/"$//' -e 's/\\[ntr]/ /g')
else  # falla en cerrado: sin campo command se analiza toda la entrada
  base=$(printf '%s' "$una_linea" | sed 's/\\[ntr]/ /g')
fi
# Variantes normalizadas: sin comillas, barras invertidas ni acentos graves; además, sin $ ( ) { } (--fir$()ma) y sin
# las expansiones ${X}, $(...) y $X, quitadas antes que las comillas para respetar dónde acaba el nombre de la
# variable (--fi${X}rma, --fi$X''rma).
plano=$(printf '%s' "$base" | tr -d '\\"'"'"'`')
compacto=$(printf '%s' "$plano" | tr -d '${}()')
sin_expansiones=$(printf '%s' "$base" | sed -e 's/\$[{][^}]*[}]//g' -e 's/\$([^)]*)//g' \
  -e 's/\$[A-Za-z_][A-Za-z0-9_]*//g' | tr -d '\\"'"'"'`')

bloquea() {
  printf '%s\n' "Bloqueado por el hook de NUTRIA: $1" >&2
  exit 2
}
contiene() {  # $1: texto; $2: expresión regular extendida
  printf '%s' "$1" | grep -Eq -e "$2"
}
en_alguna_variante() {  # $1: expresión regular extendida
  contiene "$plano" "$1" || contiene "$compacto" "$1" || contiene "$sin_expansiones" "$1"
}
# Delimitador antes y después de una orden: inicio o fin, separador, barra de una ruta (/usr/bin/env) o carácter que
# no forma parte de un nombre.
ini='(^|[^[:alnum:]_.-])'
fin='([^[:alnum:]_.-]|$)'
# Mención de nutria como orden o módulo (no como parte de una ruta: src/nutria/, research/nutria.sqlite).
menciona_nutria='(^|[^[:alnum:]_./-])nutria([^[:alnum:]_./-]|$)|-m[[:space:]]*nutria|(from|import)[[:space:]]+nutria'

# 1. Firma humana (opción completa o abreviada, o construida con secuencias de escape)
mensaje_firma="la firma humana (--firma-humana) solo puede usarla una persona, ejecutando la orden en su propia terminal; tampoco se admiten sus abreviaturas ni construirla por partes. Registra la acción sin firma (queda como propuesta) y pide a la persona responsable que la firme."
if en_alguna_variante '--firma|--firm([^[:alnum:]_]|$)|--fir([^[:alnum:]_]|$)|-firma[-_]?h'; then
  bloquea "$mensaje_firma"
fi
escapes='\\\\(x[0-9A-Fa-f]|u[0-9A-Fa-f]|U[0-9A-Fa-f]|0?[0-7][0-7][0-7]?)'
if contiene "$base" "$escapes" && { contiene "$compacto" '[Ff][Ii][Rr][Mm]|fir' || contiene "$plano" "$menciona_nutria"; }; then
  bloquea "no se construyen argumentos de nutria con secuencias de escape (\$'\\x..', printf '\\0..'): escribe el texto literal. $mensaje_firma"
fi

# 2. Variables con las que la CLI reconoce a los agentes, entorno vacío, cambio de usuario y procesos desvinculados
if contiene "$plano" 'CLAUDECODE|CLAUDE_CODE_[A-Z_]+|AI_AGENT'; then
  bloquea "las variables de entorno de Claude Code (CLAUDECODE, CLAUDE_CODE_*, AI_AGENT) no se tocan desde Bash: la CLI las usa para rechazar las firmas de los agentes. Para buscarlas en el código, usa la herramienta Grep."
fi
if contiene "$plano" 'NUTRIA_AGENT[[:space:]]*=|(unset|export|declare|typeset|readonly|local|-u|--unset)[[:space:]=]+([^[:space:];&|]+[[:space:]]+)*NUTRIA_AGENT|(environ|putenv|unsetenv|delete|ENV)[^;&|]*NUTRIA_AGENT'; then
  bloquea "NUTRIA_AGENT no se cambia ni se borra desde Bash: identifícate con --agente <rol>."
fi
if contiene "$plano" "${ini}env([[:space:]]+(-u[[:space:]]+[^[:space:]]+|-[^[:space:]i]*|--[^[:space:]]+|[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*))*[[:space:]]+(-[[:alnum:]]*i[[:alnum:]]*|--ignore-environment|-)${fin}|${ini}exec[[:space:]]+-[[:alnum:]]*c|${ini}(sudo|doas|runuser|setpriv)${fin}"; then
  bloquea "no se vacía el entorno (env -i, env -, exec -c) ni se cambia de usuario desde Bash: la CLI usa el entorno de Claude Code para rechazar las firmas de los agentes."
fi
if contiene "$plano" "${ini}(setsid|nohup|disown|daemonize|systemd-run|crontab|start-stop-daemon)${fin}|os\.(setsid|fork|daemon)|start_new_session|setpgrp"; then
  bloquea "no se desvinculan procesos de la sesión de Claude Code (setsid, nohup, disown, systemd-run, os.fork...): la CLI usa los procesos antecesores para reconocer a los agentes."
fi
if contiene "$plano" "${ini}script[[:space:]]+-|${ini}(unbuffer|socat)${fin}|${ini}import[[:space:]]+pty|pty\.(spawn|fork|openpty)|openpty|forkpty|posix_openpt|/dev/ptmx"; then
  bloquea "no se abren pseudoterminales desde Bash (script, unbuffer, socat, pty): la firma humana exige la terminal de una persona."
fi

# 3. Registro (base de datos y procedencia): solo a través de la CLI. `python -m nutria` es la CLI, no un acceso
# directo: se descarta antes de buscar los verbos que escriben.
sin_cli=$(printf '%s' "$plano" | sed -E 's/python[0-9.]*([[:space:]]+-[A-Za-z]+)*[[:space:]]+-m[[:space:]]*nutria/nutria/g')
verbos="${ini}(cp|mv|rm|dd|ln|rsync|install|truncate|shred|tee|sed|awk|perl|python[0-9.]*|node|ruby|checkout|restore|reset|stash|clean|revert)${fin}"
if contiene "$plano" 'nutria\.sqlite'; then
  if contiene "$sin_cli" "sqlite3|\.connect\(|>+[[:space:]]*[^&[:space:]]*nutria\.sqlite|$verbos"; then
    bloquea "el registro (research/nutria.sqlite) solo se lee y se escribe con la CLI nutria (las reglas de firma, compuertas y puntuaciones están en ella)."
  fi
fi
if contiene "$plano" 'nutria\.db([^[:alnum:]_]|$)|nutria\.provenance|from nutria import [^;&|]*(db|provenance)'; then
  bloquea "los módulos del registro y de la procedencia (nutria.db, nutria.provenance) no se usan directamente desde Bash: usa la CLI nutria."
fi
if contiene "$plano" 'procedencia\.jsonl'; then
  if contiene "$sin_cli" ">+[[:space:]]*[^&[:space:]]*procedencia\.jsonl|$verbos"; then
    bloquea "la procedencia (research/procedencia.jsonl) solo la escribe nutria (cadena de hashes); para leerla, usa nutria procedencia o nutria procedencia verificar."
  fi
fi
git_opciones='([[:space:]]+(-[Cc][[:space:]]+[^[:space:]]+|--(git-dir|work-tree)[[:space:]]+[^[:space:]]+|-[^[:space:]]+))*'
if contiene "$plano" "${ini}git${git_opciones}[[:space:]]+(checkout|restore|reset|stash|clean|rm|mv|revert|filter-branch|filter-repo)${fin}[^;&|]*${ini}(research(/|${fin})|nutria\.sqlite|procedencia\.jsonl)"; then
  bloquea "no se restaura ni se borra research/ con git desde Bash: el registro y la procedencia solo cambian a través de nutria (lo que se registra no se borra). Si hace falta, que lo haga una persona."
fi

# 4. Sellos de verificación
if contiene "$plano" 'clave_sello|NUTRIA_CLAVE_SELLO|\.config/nutria|CONFIG_HOME[^[:space:]]*/nutria'; then
  bloquea "la clave de los sellos de verificación no es accesible para los agentes."
fi
if contiene "$plano" 'nutria\.sello|sello\.(sign|load_key|key_path|_canonical)|write_stamp|from nutria import [^;&|]*sello|(import|__import__|import_module)[^;&|]*sello'; then
  bloquea "los sellos de verificación solo los genera 'nutria informe comprobar <informe> --online --sellar'; el módulo de sellos no se usa directamente."
fi

# 5. Identidad dentro de un subagente (agent_type lo pone Claude Code; no lo puede escribir el agente)
tipo=$(printf '%s' "$una_linea" | grep -oE '"agent_type"[[:space:]]*:[[:space:]]*"[^"\\]*"' | head -n 1 \
  | sed -e 's/^"agent_type"[[:space:]]*:[[:space:]]*"//' -e 's/"$//')
case "$tipo" in
  ''|*[!A-Za-z0-9_-]*) tipo='' ;;
esac
if [ -n "$tipo" ]; then
  agentes="$(dirname "$0")/../../.claude/agents"
  valores=$(printf '%s' "$plano" | grep -oE -- '--agente(=|[[:space:]]+)[^[:space:];&|(){}]+' \
    | sed -E 's/^--agente(=|[[:space:]]+)//')
  for valor in $valores; do
    minusculas=$(printf '%s' "$valor" | tr 'A-Z' 'a-z')
    case "$minusculas" in
      evaluador*) es_evaluador=1 ;;
      *) es_evaluador=0 ;;
    esac
    if [ "$tipo" = "evaluador-independiente" ]; then
      if [ "$es_evaluador" = 0 ]; then
        bloquea "un evaluador independiente se identifica siempre con --agente evaluador-<n>, no con '$valor' (regla 9)."
      fi
    elif [ "$es_evaluador" = 1 ]; then
      bloquea "--agente '$valor': solo los evaluadores independientes se identifican como evaluador-<n> (regla 9); este subagente es '$tipo'."
    else
      case "$valor" in
        *[!a-z0-9-]*) ;;
        *)
          if [ "$valor" != "$tipo" ] && [ -f "$agentes/$valor.md" ]; then
            bloquea "--agente '$valor' es otro rol del proyecto: este subagente es '$tipo' y se identifica con --agente $tipo (o una variante suya, como $tipo-A). Las compuertas, las puntuaciones y la procedencia dependen del rol."
          fi
          ;;
      esac
    fi
  done
  if [ "$tipo" = "evaluador-independiente" ]; then
    if contiene "$plano" 'nutria\.sqlite'; then
      bloquea "un evaluador independiente no lee la base de datos del registro directamente (regla 9): usa nutria --agente evaluador-<n> oportunidad ver <ID> --sin-puntuaciones."
    fi
    if contiene "$plano" '(from|import)[[:space:]]+nutria|nutria\.(cli|__main__)|runpy|import_module'; then
      bloquea "un evaluador independiente usa la CLI con --agente evaluador-<n>, no los módulos de nutria desde Python (regla 9)."
    fi
    if contiene "$plano" "$menciona_nutria" && [ -z "$valores" ]; then
      bloquea "un evaluador independiente se identifica en cada orden nutria: nutria --agente evaluador-<n> ... (regla 9; así la CLI le oculta las puntuaciones ajenas)."
    fi
  fi
fi
exit 0
