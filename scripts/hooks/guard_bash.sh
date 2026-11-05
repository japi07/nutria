#!/bin/sh
# Hook PreToolUse (Bash) de Claude Code: barrera de texto frente a los atajos de los agentes.
#
# La barrera de fondo está en la CLI: ningún analizador admite abreviaturas de las opciones, y la firma humana se
# rechaza con --agente, con NUTRIA_AGENT, dentro de Claude Code (sus variables de entorno en el proceso o en el entorno
# inicial de un antecesor, o el propio Claude Code entre los antecesores) o sin terminal de control. Este hook bloquea
# además, antes de ejecutar la orden (solo se analiza tool_input.command; si no se encuentra, toda la entrada):
#  1. la opción --firma-humana y cualquier prefijo suyo desde --fir, aunque se escriba con comillas, barras
#     invertidas, acentos graves o sustituciones vacías ("--firma-""humana", --fir\ma, --fir$()ma); el texto
#     firma-humana o firma_humana en cualquier parte; las opciones construidas con variables o sustituciones
#     (--$A-humana, --`...`) y las secuencias de escape de caracteres imprimibles ($'\x2d...', printf '\055...');
#  2. tocar las variables con las que la CLI reconoce a los agentes (CLAUDECODE, CLAUDE_CODE_*, AI_AGENT; unset,
#     env -u, export o asignaciones de NUTRIA_AGENT), vaciar el entorno (env -i, env -, exec -c), cambiar de usuario
#     (sudo, doas, runuser, setpriv), desvincular procesos (setsid, nohup, disown, crontab, os.fork...) o darles una
#     terminal propia (multiplexores tmux, screen, dtach, abduco, zellij; expect, script, unbuffer, socat, ssh -t, pty);
#  3. escribir el registro por fuera de la CLI: sqlite3, .connect(, copias, borrados o redirecciones sobre
#     nutria.sqlite o procedencia.jsonl (sin distinguir mayúsculas), borrar research/ entera, importar nutria.db o
#     nutria.provenance, y las órdenes de git que descartan cambios sin confirmar (stash, clean, reset --hard,
#     checkout/restore de "." o con -f) o que restauran, borran o mueven el registro;
#  4. la clave de los sellos (clave_sello, NUTRIA_CLAVE_SELLO, ~/.config/nutria) y el módulo de sellos
#     (nutria.sello, sello.sign, write_stamp, from nutria import sello);
#  5. identidad, dentro de un subagente (campo agent_type que Claude Code añade a la entrada del hook): --agente lleva
#     un nombre literal (no una variable ni una sustitución) y no puede ser el de otro rol del proyecto
#     (.claude/agents/<rol>.md) ni un evaluador-<n>, salvo para el propio evaluador-independiente, que se identifica
#     siempre con --agente evaluador-<n>, no importa nutria desde Python y no lee la base de datos del registro.
# Una orden simple de solo lectura (grep, cat, head, tail, wc, ls, stat, file, diff, sha256sum sin ; & | < > $ ni
# acentos graves) solo se comprueba frente a la clave de los sellos y, en un evaluador, la base de datos: buscar o leer
# un texto no ejecuta nada.
# Compara texto: no detiene a un proceso del mismo usuario decidido a falsificar (un programa escrito en un fichero,
# una orden construida con variables o codificada); ver README.
payload=$(cat)
una_linea=$(printf '%s' "$payload" | tr '\n\r\t' '   ')
# tool_input.command tal como va en el JSON. Una cadena JSON no puede contener comillas sin escapar, así que el texto
# de la orden no puede imitar la clave "command" ni la clave "agent_type".
orden_json=$(printf '%s' "$una_linea" | grep -oE '"command"[[:space:]]*:[[:space:]]*"([^"\\]|\\.)*"' | head -n 1)
# Escapes de JSON: la continuación de línea (barra y salto) y \t, \r son espacios; un salto de línea separa órdenes (;).
if [ -n "$orden_json" ]; then
  base=$(printf '%s' "$orden_json" | sed -e 's/^"command"[[:space:]]*:[[:space:]]*"//' -e 's/"$//' \
    -e 's/\\\\\\n/ /g' -e 's/\\n/;/g' -e 's/\\[tr]/ /g')
else  # falla en cerrado: sin campo command se analiza toda la entrada
  base=$(printf '%s' "$una_linea" | sed -e 's/\\\\\\n/ /g' -e 's/\\n/;/g' -e 's/\\[tr]/ /g')
fi
# Variantes normalizadas: sin comillas, barras invertidas ni acentos graves; además, sin $ ( ) { } (--fir$()ma) y sin
# las expansiones ${X}, $(...) y $X, quitadas antes que las comillas para respetar dónde acaba el nombre de la
# variable (--fi${X}rma, --fi$X''rma); sin comillas ni barras pero con $ y acentos graves (para ver lo que se
# construye con variables); y en minúsculas (nombres de ficheros en sistemas que no distinguen mayúsculas).
plano=$(printf '%s' "$base" | tr -d '\\"'"'"'`')
compacto=$(printf '%s' "$plano" | tr -d '${}()')
sin_expansiones=$(printf '%s' "$base" | sed -e 's/\$[{][^}]*[}]//g' -e 's/\$([^)]*)//g' \
  -e 's/\$[A-Za-z_][A-Za-z0-9_]*//g' | tr -d '\\"'"'"'`')
sin_comillas=$(printf '%s' "$base" | tr -d '\\"'"'")
minusculas=$(printf '%s' "$plano" | tr 'A-Z' 'a-z')

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
por_segmento() {  # $1: texto; $2: expresión regular extendida que debe cumplirse dentro de una misma orden simple
  printf '%s\n' "$1" | tr ';&|' '\n\n\n' | grep -Eq -e "$2"
}
# Delimitador antes y después de una orden: inicio o fin, separador, barra de una ruta (/usr/bin/env) o carácter que
# no forma parte de un nombre.
ini='(^|[^[:alnum:]_.-])'
fin='([^[:alnum:]_.-]|$)'
# Posición de orden: inicio, separador o sustitución, tras asignaciones y envoltorios (env, sudo, exec, xargs...).
posicion_de_orden='(^|[;&|({`]|\$\()[[:space:]]*(([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*|sudo|env|exec|command|builtin|nice|nohup|time|xargs|eval|-[^[:space:]]+)[[:space:]]+)*([^[:space:];&|]*/)?'
# Mención de nutria como orden o módulo (no como parte de una ruta: src/nutria/, research/nutria.sqlite).
menciona_nutria='(^|[^[:alnum:]_./-])nutria([^[:alnum:]_./-]|$)|-m[[:space:]]*nutria|(from|import)[[:space:]]+nutria'

# Orden simple de solo lectura: no puede firmar, escribir ni ejecutar otra orden.
solo_lectura=0
if contiene "$sin_comillas" '^[[:space:]]*(grep|egrep|fgrep|cat|head|tail|wc|ls|stat|file|diff|sha256sum|md5sum)[[:space:]]' \
  && ! contiene "$sin_comillas" '[;&|<>`$]'; then
  solo_lectura=1
fi

# 1. Firma humana (opción completa o abreviada, construida con variables o con secuencias de escape)
mensaje_firma="la firma humana (--firma-humana) solo puede usarla una persona, ejecutando la orden en su propia terminal; tampoco se admiten sus abreviaturas ni construirla por partes (si solo la mencionas en un texto, escribe 'la firma de la persona'). Registra la acción sin firma (queda como propuesta) y pide a la persona responsable que la firme."
if [ "$solo_lectura" = 0 ]; then
  if en_alguna_variante '--firma|--firm([^[:alnum:]_]|$)|--fir([^[:alnum:]_]|$)|(^|[^[:alnum:]_.-])-firma[-_]?h|[Ff][Ii][Rr][Mm][Aa][-_][Hh][Uu][Mm][Aa][Nn][Aa]'; then
    bloquea "$mensaje_firma"
  fi
  if contiene "$sin_comillas" '(^|[^[:alnum:]_-])--[[:alnum:]_-]*[$`]'; then
    bloquea "las opciones de nutria no se construyen con variables ni sustituciones (--\$X, --\`...\`): escribe la opción literal. $mensaje_firma"
  fi
  escapes='\\\\(x[2-7][0-9A-Fa-f]|[uU][0-9A-Fa-f]|0?(0[4-7][0-7]|1[0-7][0-7]|[4-7][0-7]))'
  if contiene "$base" "$escapes" && { contiene "$compacto" '[Ff][Ii][Rr][Mm]|fir' || contiene "$plano" "$menciona_nutria"; }; then
    bloquea "no se construyen argumentos de nutria con secuencias de escape de caracteres imprimibles (\$'\\x..', printf '\\0..'): escribe el texto literal. $mensaje_firma"
  fi
fi

# 2. Variables con las que la CLI reconoce a los agentes, entorno vacío, cambio de usuario, procesos desvinculados y
#    terminales propias
if [ "$solo_lectura" = 0 ]; then
  if contiene "$plano" 'CLAUDECODE|CLAUDE_CODE_[A-Z_]+|AI_AGENT'; then
    bloquea "las variables de entorno de Claude Code (CLAUDECODE, CLAUDE_CODE_*, AI_AGENT) no se tocan desde Bash: la CLI las usa para rechazar las firmas de los agentes. Para buscarlas en el código, usa la herramienta Grep o un grep simple."
  fi
  if contiene "$plano" 'NUTRIA_AGENT[[:space:]]*=|(unset|export|declare|typeset|readonly|local|-u|--unset)[[:space:]=]+([^[:space:];&|]+[[:space:]]+)*NUTRIA_AGENT|(environ|putenv|unsetenv|delete|ENV)[^;&|]*NUTRIA_AGENT'; then
    bloquea "NUTRIA_AGENT no se cambia ni se borra desde Bash: identifícate con --agente <rol>."
  fi
  if contiene "$plano" "${ini}env([[:space:]]+(-u[[:space:]]+[^[:space:]]+|-[^[:space:]i]*|--[^[:space:]]+|[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*))*[[:space:]]+(-[[:alnum:]]*i[[:alnum:]]*|--ignore-environment|-)${fin}|${ini}exec[[:space:]]+-[[:alnum:]]*c|${ini}(sudo|doas|runuser|setpriv)${fin}"; then
    bloquea "no se vacía el entorno (env -i, env -, exec -c) ni se cambia de usuario desde Bash: la CLI usa el entorno de Claude Code para rechazar las firmas de los agentes."
  fi
  sin_crontab_l=$(printf '%s' "$plano" | sed -E 's/crontab[[:space:]]+-l([[:space:]]*([;&|]|$))/crontab-l\1/g')
  if contiene "$sin_crontab_l" "${ini}(setsid|nohup|disown|daemonize|systemd-run|crontab|start-stop-daemon)${fin}|os\.(setsid|fork|daemon)|start_new_session|setpgrp"; then
    bloquea "no se desvinculan procesos de la sesión de Claude Code (setsid, nohup, disown, systemd-run, crontab, os.fork...): la CLI usa los procesos antecesores para reconocer a los agentes."
  fi
  if contiene "$plano" "${ini}(tmux|dtach|abduco|zellij|byobu|tmate|unbuffer|socat)${fin}|${ini}script[[:space:]]+-|${ini}import[[:space:]]+pty|pty\.(spawn|fork|openpty)|openpty|forkpty|posix_openpt|/dev/ptmx|IO::Pty|PTY\.spawn|node-pty" \
    || contiene "$sin_comillas" "${posicion_de_orden}(screen|expect)([[:space:]]|$)" \
    || contiene "$plano" "${ini}ssh[[:space:]]([^;&|]*[[:space:]])?-[[:alnum:]]*t|${ini}ssh[[:space:]][^;&|]*RequestTTY"; then
    bloquea "no se lanzan órdenes con una terminal propia desde Bash (tmux, screen, dtach, expect, script, unbuffer, socat, ssh -t, pty): la firma humana exige la terminal de una persona."
  fi
fi

# 3. Registro (base de datos y procedencia): solo a través de la CLI. `python -m nutria` es la CLI y `python -m pytest`
#    ejecuta las pruebas, no un acceso directo: se descartan antes de buscar los verbos que escriben.
if [ "$solo_lectura" = 0 ]; then
  sin_cli=$(printf '%s' "$minusculas" | sed -E 's/python[0-9.]*([[:space:]]+-[a-z]+)*[[:space:]]+-m[[:space:]]*(nutria|pytest)/\2/g')
  verbos="${ini}(cp|mv|rm|dd|ln|rsync|install|truncate|shred|tee|sed|awk|perl|python[0-9.]*|node|ruby|checkout|restore|reset|stash|clean|revert)${fin}"
  if contiene "$minusculas" 'nutria\.sqlite'; then
    if contiene "$sin_cli" "sqlite3|\.connect\(|>+[[:space:]]*[^&[:space:]]*nutria\.sqlite|$verbos"; then
      bloquea "el registro (research/nutria.sqlite) solo se lee y se escribe con la CLI nutria (las reglas de firma, compuertas y puntuaciones están en ella)."
    fi
  fi
  if contiene "$plano" 'nutria\.db([^[:alnum:]_]|$)|nutria\.provenance|from nutria import [^;&|]*(db|provenance)'; then
    bloquea "los módulos del registro y de la procedencia (nutria.db, nutria.provenance) no se usan directamente desde Bash: usa la CLI nutria."
  fi
  if contiene "$minusculas" 'procedencia\.jsonl'; then
    if contiene "$sin_cli" ">+[[:space:]]*[^&[:space:]]*procedencia\.jsonl|$verbos"; then
      bloquea "la procedencia (research/procedencia.jsonl) solo la escribe nutria (cadena de hashes); para leerla, usa nutria procedencia, nutria procedencia verificar o un cat/tail simple."
    fi
  fi
  if por_segmento "$minusculas" "${ini}(rm|mv|shred|truncate|rsync|unlink|rmdir)${fin}.*[[:space:]=](\./)?research/?(\*|\.)?([[:space:]]|$)"; then
    bloquea "research/ no se borra ni se mueve entera desde Bash: el registro y la procedencia solo cambian a través de nutria."
  fi
  git_opciones='([[:space:]]+(-[c][[:space:]]+[^[:space:]]+|--(git-dir|work-tree)[[:space:]]+[^[:space:]]+|-[^[:space:]]+))*'
  git_orden="${ini}git${git_opciones}[[:space:]]+"
  sin_stash_lectura=$(printf '%s' "$minusculas" | sed -E 's/stash([[:space:]]+)(list|show)/stash-lectura\1/g')
  if por_segmento "$sin_stash_lectura" "${git_orden}(stash|clean|filter-branch|filter-repo)([[:space:]]|$)" \
    || por_segmento "$minusculas" "${git_orden}reset([[:space:]].*)?[[:space:]]--(hard|merge|keep)([[:space:]]|$)" \
    || por_segmento "$minusculas" "${git_orden}(checkout|restore|switch)([[:space:]].*)?[[:space:]](\.|\./|:/|:/:|\*|-f|--force|--discard-changes|--worktree|-w)([[:space:]]|$)"; then
    bloquea "no se descartan cambios sin confirmar con git desde Bash (stash, clean, reset --hard, checkout/restore de '.' o con -f): se perderían el registro y la procedencia aún no confirmados. Si hace falta, que lo haga una persona."
  fi
  if por_segmento "$minusculas" "${git_orden}(checkout|restore|reset|revert)([[:space:]].*)?[[:space:]=](\./)?(research(/|[[:space:]]|$)|[^[:space:]]*(nutria\.sqlite|procedencia\.jsonl))" \
    || por_segmento "$minusculas" "${git_orden}(rm|mv)([[:space:]].*)?[[:space:]=](\./)?(research/?(\*|\.)?([[:space:]]|$)|[^[:space:]]*(nutria\.sqlite|procedencia\.jsonl))"; then
    bloquea "no se restaura, borra ni mueve el registro con git desde Bash: el registro y la procedencia solo cambian a través de nutria (lo que se registra no se borra). Si hace falta, que lo haga una persona."
  fi
fi

# 4. Sellos de verificación (la clave, también en las órdenes de solo lectura)
if contiene "$minusculas" 'clave_sello|nutria_clave_sello|\.config/nutria|config_home[^[:space:]]*/nutria'; then
  bloquea "la clave de los sellos de verificación no es accesible para los agentes."
fi
if [ "$solo_lectura" = 0 ] && contiene "$plano" 'nutria\.sello|sello\.(sign|load_key|key_path|_canonical)|write_stamp|from nutria import [^;&|]*sello|(import|__import__|import_module)[^;&|]*sello'; then
  bloquea "los sellos de verificación solo los genera 'nutria informe comprobar <informe> --online --sellar'; el módulo de sellos no se usa directamente."
fi

# 5. Identidad dentro de un subagente (agent_type lo pone Claude Code; no lo puede escribir el agente)
tipo=$(printf '%s' "$una_linea" | grep -oE '"agent_type"[[:space:]]*:[[:space:]]*"[^"\\]*"' | head -n 1 \
  | sed -e 's/^"agent_type"[[:space:]]*:[[:space:]]*"//' -e 's/"$//')
case "$tipo" in
  ''|*[!A-Za-z0-9_-]*) tipo='' ;;
esac
if [ -n "$tipo" ]; then
  if [ "$tipo" = "evaluador-independiente" ] && contiene "$minusculas" 'nutria\.sqlite'; then
    bloquea "un evaluador independiente no lee la base de datos del registro directamente (regla 9): usa nutria --agente evaluador-<n> oportunidad ver <ID> --sin-puntuaciones."
  fi
  if [ "$solo_lectura" = 1 ]; then
    exit 0
  fi
  agentes="$(dirname "$0")/../../.claude/agents"
  valores=$(printf '%s' "$sin_comillas" | grep -oE -- '--agente(=|[[:space:]]+)[^[:space:];&|(){}]+' \
    | sed -E 's/^--agente(=|[[:space:]]+)//')
  for valor in $valores; do
    case "$valor" in
      *[!A-Za-z0-9_-]*)
        bloquea "--agente '$valor': dentro de un subagente, --agente lleva el nombre literal del rol (sin variables ni sustituciones); este subagente es '$tipo'." ;;
    esac
    minusculas_valor=$(printf '%s' "$valor" | tr 'A-Z' 'a-z')
    case "$minusculas_valor" in
      evaluador*) es_evaluador=1 ;;
      *) es_evaluador=0 ;;
    esac
    if [ "$tipo" = "evaluador-independiente" ]; then
      if [ "$es_evaluador" = 0 ]; then
        bloquea "un evaluador independiente se identifica siempre con --agente evaluador-<n>, no con '$valor' (regla 9)."
      fi
    elif [ "$es_evaluador" = 1 ]; then
      bloquea "--agente '$valor': solo los evaluadores independientes se identifican como evaluador-<n> (regla 9); este subagente es '$tipo'."
    elif [ "$minusculas_valor" != "$tipo" ] && [ -f "$agentes/$minusculas_valor.md" ]; then
      bloquea "--agente '$valor' es otro rol del proyecto: este subagente es '$tipo' y se identifica con --agente $tipo (o una variante suya, como $tipo-A). Las compuertas, las puntuaciones y la procedencia dependen del rol."
    fi
  done
  if [ "$tipo" = "evaluador-independiente" ]; then
    if contiene "$plano" '(from|import)[[:space:]]+nutria|nutria\.(cli|__main__)|runpy|import_module'; then
      bloquea "un evaluador independiente usa la CLI con --agente evaluador-<n>, no los módulos de nutria desde Python (regla 9)."
    fi
    if contiene "$plano" "$menciona_nutria" && [ -z "$valores" ]; then
      bloquea "un evaluador independiente se identifica en cada orden nutria: nutria --agente evaluador-<n> ... (regla 9; así la CLI le oculta las puntuaciones ajenas)."
    fi
  fi
fi
exit 0
