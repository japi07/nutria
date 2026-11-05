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
#     env -u, export o asignaciones de NUTRIA_AGENT); vaciar el entorno (env -i, env -, --ignore-env..., también tras
#     otras opciones como -C dir; env -S; exec -c, también tras -a nombre; os.execve, env={} en Python); cambiar de
#     usuario (sudo, su, doas, runuser, setpriv, pkexec); desvincular procesos (setsid, nohup, disown, crontab,
#     os.fork...) o darles una terminal propia (tmux, screen, dtach, abduco, zellij; expect, script, también desde una
#     lista de Python, unbuffer, socat, ssh -t, su -P, pty importado de cualquier forma);
#  3. escribir el registro por fuera de la CLI: sqlite3, .connect(, copias, borrados o redirecciones sobre
#     nutria.sqlite o procedencia.jsonl (sin distinguir mayúsculas), también con comodines que puedan cubrirlos
#     (rm research/*.jsonl, truncate research/proc*, find research -name '*.sqlite' -delete, git restore '*.sqlite');
#     borrar research/ entera (también con find ... -delete o comodines); importar nutria.db o nutria.provenance; las
#     órdenes de git que descartan cambios sin confirmar (stash, clean, reset --hard, checkout/restore de "." o con -f,
#     también con abreviaturas: reset --ha, checkout --forc, switch --disc; checkout-index, read-tree) o que
#     restauran, borran o mueven el registro (también con los pathspecs :/research o :!x); y lo que borraría el reflog
#     con el que `nutria procedencia verificar` detecta la historia reescrita (reflog expire|delete, update-ref,
#     gc.reflogExpire, borrar .git o .git/logs);
#  4. la clave de los sellos (clave_sello, NUTRIA_CLAVE_SELLO, ~/.config/nutria, también con ./ o // en la ruta, con
#     comodines en cualquier componente (~/.con*/nut*/*), entrando con cd en .config o recorriéndola con grep -r, find
#     o tar) y el módulo de sellos (nutria.sello, sello.sign, write_stamp, from nutria import sello);
#  5. identidad, dentro de un subagente (campo agent_type que Claude Code añade a la entrada del hook): cada orden nutria
#     lleva --agente con un nombre literal (no una variable, una sustitución ni un comodín) y se ejecuta directamente
#     (no desde Python, xargs ni eval); --agente no puede ser el de otro rol del proyecto (.claude/agents/<rol>.md) ni
#     un evaluador-<n>, salvo para el propio evaluador-independiente, que se identifica siempre con --agente
#     evaluador-<n>, no importa nutria desde Python y no lee la base de datos del registro; no se menciona
#     NUTRIA_AGENT ni se exportan variables con nombres construidos (set -a, export $V=..., printf -v $V); git, solo
#     para leer, añadir y confirmar (sin --amend).
# Una orden simple de solo lectura (grep, cat, head, tail, wc, ls, stat, file, diff, sha256sum sin ; & | < > $ ni
# acentos graves) solo se comprueba frente a la clave de los sellos y, en un evaluador, la base de datos: buscar o leer
# un texto no ejecuta nada.
# Compara texto: no detiene a un proceso del mismo usuario decidido a falsificar, y sus listas no son exhaustivas (hay
# más formas de vaciar el entorno, obtener una terminal o desvincular un proceso de las que reconoce). Lo eluden un
# programa escrito antes en un fichero y los nombres construidos con variables, concatenación o base64 (T=tm; ${T}ux
# ...; una opción leída de un fichero); en la sesión principal (sin agent_type) no se comprueba el rol declarado. Ver
# README.
set -f   # la orden se trocea en palabras: sus comodines no se expanden aquí
payload=$(cat)
una_linea=$(printf '%s' "$payload" | tr '\n\r\t' '   ')
# tool_input.command tal como va en el JSON. Una cadena JSON no puede contener comillas sin escapar, así que el texto
# de la orden no puede imitar las claves "command", "cwd" ni "agent_type".
orden_json=$(printf '%s' "$una_linea" | grep -oE '"command"[[:space:]]*:[[:space:]]*"([^"\\]|\\.)*"' | head -n 1)
# Escapes de JSON: la continuación de línea (barra y salto) y \t, \r son espacios; un salto de línea separa órdenes (;).
if [ -n "$orden_json" ]; then
  base=$(printf '%s' "$orden_json" | sed -e 's/^"command"[[:space:]]*:[[:space:]]*"//' -e 's/"$//' \
    -e 's/\\\\\\n/ /g' -e 's/\\n/;/g' -e 's/\\[tr]/ /g')
else  # falla en cerrado: sin campo command se analiza toda la entrada
  base=$(printf '%s' "$una_linea" | sed -e 's/\\\\\\n/ /g' -e 's/\\n/;/g' -e 's/\\[tr]/ /g')
fi
cwd=$(printf '%s' "$una_linea" | grep -oE '"cwd"[[:space:]]*:[[:space:]]*"([^"\\]|\\.)*"' | head -n 1 \
  | sed -e 's/^"cwd"[[:space:]]*:[[:space:]]*"//' -e 's/"$//')
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
nl='
'

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
# Patrón de la shell (* ? [..] [!..] {a,b}) como expresión regular extendida.
patron_a_er() {
  printf '%s' "$1" | sed -e 's/[.^$+()|\\]/\\&/g' -e 's/\[!/[^/g' -e 's/\*/.*/g' -e 's/?/./g' \
    -e ':a' -e 's/\({[^{}]*\),/\1|/' -e 'ta' -e 's/{/(/g' -e 's/}/)/g'
}
cubre() {  # $1: patrón de la shell; resto: nombres. Verdadero si el patrón cubre alguno (si no se entiende, también)
  er=$(patron_a_er "$1")
  shift
  printf '%s\n' "$@" | grep -Eiqx -e "$er"
  [ $? -ne 1 ]
}
es_comodin() {
  case "$1" in
    *[*?]*|*\[*\]*|*\{*,*\}*) return 0 ;;
  esac
  return 1
}
# Delimitador antes y después de una orden: inicio o fin, separador, barra de una ruta (/usr/bin/env) o carácter que
# no forma parte de un nombre.
ini='(^|[^[:alnum:]_.-])'
fin='([^[:alnum:]_.-]|$)'
# Posición de orden: inicio de una orden simple, de un grupo o de una sustitución, o tras then, do, exec, time...
posicion='(^|[;&|(){}!`]|\$\(|(then|do|else|elif|exec|time|command|nohup|nice|xargs|env)[[:space:]])[[:space:]]*'
# screen y expect también son palabras de las búsquedas bibliográficas: solo cuentan con sus opciones de uso
# desatendido (screen -dm, expect -c, expect fichero.exp, ... | expect, expect < fichero).
multiplexor_ingles='(^|[^[:alnum:]_.-])screen[[:space:]]([^;&|]*[[:space:]])?-[[:alnum:]]*[dDm]|(^|[^[:alnum:]_.-])expect[[:space:]]+(-[bcdDfinN]?([[:space:]]|$)|[^[:space:];&|]+\.exp([[:space:]]|$))|\|[[:space:]]*expect([[:space:]]|$)|(^|[^[:alnum:]_.-])expect[[:space:]]*<'
# Mención de nutria como orden o módulo (no como parte de una ruta: src/nutria/, research/nutria.sqlite).
menciona_nutria='(^|[^[:alnum:]_./-])nutria([^[:alnum:]_./-]|$)|-m[[:space:]]*nutria|(from|import)[[:space:]]+nutria'
nombres_registro='nutria.sqlite nutria.sqlite-wal nutria.sqlite-shm nutria.sqlite-journal procedencia.jsonl'
# Opciones de env antes de la orden que ejecuta (con su argumento, si lo llevan) y asignaciones VAR=valor.
env_opciones='([[:space:]]+(-[uCS][[:space:]]*[^-[:space:]][^[:space:]]*|--(unset|chdir|split-string|default-signal|ignore-signal|block-signal)(=[^[:space:]]*|[[:space:]]+[^-[:space:]][^[:space:]]*)?|-[^[:space:]i]*|--[^[:space:]i][^[:space:]]*|[A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*))*'
git_opciones='([[:space:]]+(-[c][[:space:]]+[^[:space:]]+|--(git-dir|work-tree)[[:space:]]+[^[:space:]]+|-[^[:space:]]+))*'
git_orden="${ini}git${git_opciones}[[:space:]]+"

# Orden simple de solo lectura: no puede firmar, escribir ni ejecutar otra orden.
solo_lectura=0
if contiene "$sin_comillas" '^[[:space:]]*(grep|egrep|fgrep|cat|head|tail|wc|ls|stat|file|diff|sha256sum|md5sum)[[:space:]]' \
  && ! contiene "$sin_comillas" '[;&|<>`$]'; then
  solo_lectura=1
fi
# Una orden enorme no se analiza (el tiempo del hook crece con ella y, si se agota, Claude Code la ejecuta igualmente).
if [ "${#base}" -gt 100000 ]; then
  bloquea "la orden es demasiado larga para comprobarla ($(printf '%s' "${#base}") caracteres): divídela o escribe un script con nombres explícitos."
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
#    terminales propias (listas no exhaustivas: ver la cabecera)
if [ "$solo_lectura" = 0 ]; then
  if contiene "$plano" 'CLAUDECODE|CLAUDE_CODE_[A-Z_]+|AI_AGENT'; then
    bloquea "las variables de entorno de Claude Code (CLAUDECODE, CLAUDE_CODE_*, AI_AGENT) no se tocan desde Bash: la CLI las usa para rechazar las firmas de los agentes. Para buscarlas en el código, usa la herramienta Grep o un grep simple."
  fi
  if contiene "$plano" 'NUTRIA_AGENT[[:space:]]*=|(unset|export|declare|typeset|readonly|local|-u|--unset)[[:space:]=]+([^[:space:];&|]+[[:space:]]+)*NUTRIA_AGENT|(environ|putenv|unsetenv|delete|ENV)[^;&|]*NUTRIA_AGENT'; then
    bloquea "NUTRIA_AGENT no se cambia ni se borra desde Bash: identifícate con --agente <rol>."
  fi
  if contiene "$plano" "${ini}env${env_opciones}[[:space:]]+(-[[:alnum:]]*i[[:alnum:]]*|--i[[:alnum:]-]*|-|-[[:alnum:]]*S|--sp[[:alnum:]-]*)${fin}|${ini}exec([[:space:]]+(-a[[:space:]]*[^[:space:]]+|-[[:alnum:]]+))*[[:space:]]+-[[:alnum:]]*c|os\.(execve|execle|execlpe|execvpe|spawnve|spawnvpe)|posix_spawn|env[[:space:]]*=[[:space:]]*(\{[[:space:]]*\}|dict\([[:space:]]*\))"; then
    bloquea "no se lanzan órdenes con el entorno vacío desde Bash (env -i y sus variantes, env -S, exec -c, os.execve, env={}): la CLI usa el entorno de Claude Code para rechazar las firmas de los agentes."
  fi
  if contiene "$plano" "${ini}(sudo|doas|runuser|setpriv|pkexec|ksu|machinectl)${fin}|${posicion}([^[:space:]]*/)?su([[:space:]]+([A-Za-z_][A-Za-z0-9_-]*[[:space:]]+)?-|[[:space:]]*$)|/(usr/)?s?bin/su([^[:alnum:]_.-]|$)" \
    || contiene "$base" "[[(,][[:space:]]*['\"]su['\"]"; then
    bloquea "no se cambia de usuario desde Bash (sudo, su, doas, runuser, setpriv, pkexec): su -P y similares dan además una terminal propia, y la firma humana exige la de una persona."
  fi
  sin_crontab_l=$(printf '%s' "$plano" | sed -E 's/crontab[[:space:]]+-l([[:space:]]*([;&|]|$))/crontab-l\1/g')
  if contiene "$sin_crontab_l" "${ini}(setsid|nohup|disown|daemonize|systemd-run|crontab|start-stop-daemon)${fin}|os\.(setsid|fork|daemon)|start_new_session|setpgrp"; then
    bloquea "no se desvinculan procesos de la sesión de Claude Code (setsid, nohup, disown, systemd-run, crontab, os.fork...): la CLI usa los procesos antecesores para reconocer a los agentes."
  fi
  if contiene "$plano" "${ini}(tmux|dtach|abduco|zellij|byobu|tmate|unbuffer|socat)${fin}|${ini}script([[:space:]]+|[[:space:]]*,[[:space:]]*)-|/(usr/)?bin/script([^[:alnum:]_.-]|$)|${ini}import[[:space:]]+pty|from[[:space:]]+pty[[:space:]]|(import|__import__|import_module|require)[^;&|]*[^[:alnum:]_]pty([^[:alnum:]_]|$)|pty\.(spawn|fork|openpty)|openpty|forkpty|posix_openpt|/dev/ptmx|IO::Pty|PTY\.spawn|node-pty" \
    || contiene "$base" "[[(,][[:space:]]*['\"]script['\"]" \
    || contiene "$plano" "$multiplexor_ingles" \
    || contiene "$plano" "${ini}ssh[[:space:]]([^;&|]*[[:space:]])?-[[:alnum:]]*t|${ini}ssh[[:space:]][^;&|]*RequestTTY"; then
    bloquea "no se lanzan órdenes con una terminal propia desde Bash (tmux, screen, dtach, expect, script, unbuffer, socat, ssh -t, su -P, pty): la firma humana exige la terminal de una persona."
  fi
fi

# 3. Registro (base de datos y procedencia): solo a través de la CLI. `python -m nutria` es la CLI y `python -m pytest`
#    ejecuta las pruebas, no un acceso directo: se descartan antes de buscar los verbos que escriben.
mensaje_registro="el registro (research/nutria.sqlite) y la procedencia (research/procedencia.jsonl) solo cambian a través de la CLI nutria (las reglas de firma, compuertas, puntuaciones y la cadena de hashes están en ella); tampoco se borran o sobrescriben con comodines que puedan cubrirlos ni borrando research/ entera. Para leer la procedencia, usa nutria procedencia o un cat/tail simple; si de verdad hace falta tocarlos, que lo haga una persona."
if [ "$solo_lectura" = 0 ]; then
  sin_cli=$(printf '%s' "$minusculas" | sed -E 's/python[0-9.]*([[:space:]]+-[a-z]+)*[[:space:]]+-m[[:space:]]*(nutria|pytest)/\2/g')
  verbos="${ini}(cp|mv|rm|dd|ln|rsync|install|truncate|shred|tee|sed|awk|perl|python[0-9.]*|node|ruby|checkout|restore|reset|stash|clean|revert)${fin}"
  if contiene "$minusculas" 'nutria\.sqlite'; then
    if contiene "$sin_cli" "sqlite3|\.connect\(|>+[[:space:]]*[^&[:space:]]*nutria\.sqlite|$verbos"; then
      bloquea "$mensaje_registro"
    fi
  fi
  if contiene "$plano" 'nutria\.db([^[:alnum:]_]|$)|nutria\.provenance|from nutria import [^;&|]*(db|provenance)'; then
    bloquea "los módulos del registro y de la procedencia (nutria.db, nutria.provenance) no se usan directamente desde Bash: usa la CLI nutria."
  fi
  if contiene "$minusculas" 'procedencia\.jsonl'; then
    if contiene "$sin_cli" ">+[[:space:]]*[^&[:space:]]*procedencia\.jsonl|$verbos"; then
      bloquea "$mensaje_registro"
    fi
  fi
  if por_segmento "$minusculas" "${ini}(rm|mv|shred|truncate|rsync|unlink|rmdir)${fin}.*[[:space:]=](\./)?research/?(\*|\.)?([[:space:]]|$)"; then
    bloquea "$mensaje_registro"
  fi

  # 3b. Comodines y find: órdenes que borran, sobrescriben o mueven ficheros (también git que restaura o descarta y
  #     find con -delete o -exec rm...) no pueden llevar patrones que cubran el registro o research/ entera. Un patrón
  #     sin directorio cuenta si la orden cambia de directorio (cd), si el directorio de trabajo es research/ (o el
  #     proyecto, para research/ entera) o, en git y find, siempre (sus patrones recorren subdirectorios).
  cambia_dir=0
  contiene "$minusculas" "${ini}(cd|pushd)${fin}" && cambia_dir=1
  en_research=0
  case "$cwd" in */research|*/research/) en_research=1 ;; esac
  proyecto_aqui=0
  if [ -n "$cwd" ] && [ -d "$cwd/research" ]; then proyecto_aqui=1; fi
  # cp, install, ln y rsync solo escriben en su destino (la última palabra); las demás, en todos sus ficheros.
  destruye_destino="${ini}(cp|install|ln|rsync)${fin}"
  destruye="${ini}(rm|unlink|shred|truncate|mv|dd|tee|srm|wipe|python[0-9.]*|node|ruby|perl)${fin}|${ini}sed${fin}[^;&|]*[[:space:]]-[[:alnum:]]*i|${ini}find${fin}[^;&|]*[[:space:]]-(delete|ok|okdir)${fin}|${ini}find${fin}[^;&|]*[[:space:]]-exec(dir)?[[:space:]]+([^[:space:]]*/)?(rm|unlink|shred|truncate|mv|cp|dd|tee|sed|perl|python[0-9.]*)${fin}"
  git_destruye="${git_orden}(checkout|restore|reset|rm|mv|clean|stash|checkout-index|read-tree|update-index)${fin}"
  sin_cli_plano=$(printf '%s' "$plano" | sed -E 's/[Pp][Yy][Tt][Hh][Oo][Nn][0-9.]*([[:space:]]+-[A-Za-z]+)*[[:space:]]+-m[[:space:]]*([Nn][Uu][Tt][Rr][Ii][Aa]|[Pp][Yy][Tt][Ee][Ss][Tt])/\2/g')
  revisa_patron() {  # $1: palabra con comodines; $2: 1 si un patrón sin directorio cuenta siempre (git, find)
    p=$1
    case "$p" in *\]*) ;; \[*) p=${p#\[} ;; esac       # restos de una lista de Python: ['rm','research/*.jsonl']
    case "$p" in *\[*) ;; *\]) p=${p%\]} ;; esac
    while [ "${p%/}" != "$p" ]; do p=${p%/}; done
    [ -n "$p" ] || return 0
    case "$p" in */*) dir=${p%/*}; nombre=${p##*/} ;; *) dir=''; nombre=$p ;; esac
    case "$dir" in "~") dir=$HOME ;; "~/"*) dir="$HOME/${dir#\~/}" ;; esac
    if [ -n "$dir" ] && [ "$dir" != "." ]; then
      if cubre "${dir##*/}" research && cubre "$nombre" $nombres_registro; then bloquea "$mensaje_registro"; fi
      if cubre "$nombre" research && { [ -d "$dir/research" ] || [ "$dir" = "$cwd" ]; }; then bloquea "$mensaje_registro"; fi
      return 0
    fi
    if cubre "$nombre" $nombres_registro && { [ "$2" = 1 ] || [ "$en_research" = 1 ] || [ "$cambia_dir" = 1 ]; }; then
      bloquea "$mensaje_registro"
    fi
    if cubre "$nombre" research && { [ "$2" = 1 ] || [ "$proyecto_aqui" = 1 ] || [ "$cambia_dir" = 1 ]; }; then
      bloquea "$mensaje_registro"
    fi
  }
  revisa_segmento() {  # $1: orden simple
    es_git=0
    es_find=0
    palabras=$(printf '%s' "$1" | tr '()<>=,' '      ')
    if printf '%s' "$1" | grep -Eiq -e "$git_destruye"; then
      es_git=1
    elif ! printf '%s' "$1" | grep -Eiq -e "$destruye"; then
      printf '%s' "$1" | grep -Eiq -e "$destruye_destino" || return 0
      palabras=$(printf '%s\n' $palabras | tail -n 1)
    fi
    printf '%s' "$1" | grep -Eiq -e "${ini}find${fin}" && es_find=1
    n_patrones=0
    for palabra in $palabras; do
      es_comodin "$palabra" || continue
      n_patrones=$((n_patrones + 1))
      [ "$n_patrones" -le 50 ] || bloquea "demasiados patrones con comodines en una orden que borra o mueve ficheros: nombra los ficheros explícitamente."
      if [ "$es_git" = 1 ] || [ "$es_find" = 1 ]; then revisa_patron "$palabra" 1; else revisa_patron "$palabra" 0; fi
    done
    # find que borra sin filtro de nombre (o con uno negado) desde research/, desde el proyecto o desde la raíz
    if [ "$es_find" = 1 ] && { ! printf '%s' "$1" | grep -Eiq -e '[[:space:]]-(i?name|i?path|i?wholename)[[:space:]]' \
      || printf '%s' "$1" | grep -Eiq -e '[[:space:]](-not|!)[[:space:]]'; }; then
      rutas=$(printf '%s' "$1" | sed -E -e 's/^.*(^|[^[:alnum:]_.-])[Ff][Ii][Nn][Dd][[:space:]]+//' \
        -e 's/^(-[HLP][[:space:]]+)+//' -e 's/(^|[[:space:]]+)[-(!].*$//')
      [ -n "$rutas" ] || rutas=.
      for ruta in $rutas; do
        while [ "${ruta%/}" != "$ruta" ] && [ "$ruta" != "/" ]; do ruta=${ruta%/}; done
        ruta=${ruta#./}
        case "$ruta" in
          research|*/research|"~"|"\$HOME"|/) bloquea "$mensaje_registro" ;;
          .|"") if [ "$proyecto_aqui" = 1 ] || [ "$en_research" = 1 ] || [ "$cambia_dir" = 1 ]; then bloquea "$mensaje_registro"; fi ;;
        esac
        if es_comodin "$ruta" && cubre "${ruta##*/}" research; then bloquea "$mensaje_registro"; fi
      done
    fi
    return 0
  }
  # Solo las órdenes simples que borran, sobrescriben o mueven (un único grep sobre todas); con muchas, se bloquea:
  # el tiempo del hook crece con ellas y, si se agota, Claude Code ejecuta la orden igualmente.
  destructivas=$(printf '%s\n' "$sin_cli_plano" | tr ';&|' '\n\n\n' \
    | grep -Ei -e "$git_destruye" -e "$destruye" -e "$destruye_destino")
  if [ "$(printf '%s\n' "$destructivas" | grep -c .)" -gt 50 ]; then
    bloquea "demasiadas órdenes que borran, sobrescriben o mueven ficheros en una sola llamada: divídela o escribe un script con nombres explícitos."
  fi
  IFS_antes=$IFS
  IFS=$nl
  set -- $destructivas
  IFS=$IFS_antes
  for segmento in "$@"; do
    revisa_segmento "$segmento"
  done
  # un intérprete que borra o escribe ficheros elegidos con glob: su código puede llevar ; dentro de las comillas
  # (python -c "...; ...")
  if contiene "$minusculas" "${ini}(python[0-9.]*|node|ruby|perl)${fin}" && contiene "$minusculas" 'glob|fnmatch' \
    && contiene "$minusculas" 'os\.(remove|unlink|rename|replace|truncate|rmdir)|shutil\.|unlink|rmtree|write_(text|bytes)|truncate|open\('; then
    revisa_segmento "$sin_cli_plano"
  fi
  # destino de una redirección (bash expande un comodín si cubre un único fichero)
  destinos=$(printf '%s' "$plano" | grep -oE '>[>|]?[[:space:]]*[^[:space:];&|<>()]*[][*?{][^[:space:];&|<>()]*' \
    | sed -E 's/^>[>|]?[[:space:]]*//' | head -n 21)
  if [ "$(printf '%s\n' "$destinos" | grep -c .)" -gt 20 ]; then
    bloquea "demasiadas redirecciones a patrones con comodines: escribe los nombres de los ficheros."
  fi
  for destino in $destinos; do
    es_comodin "$destino" && revisa_patron "$destino" 0
  done

  # 3c. git: pathspecs mágicos (:/x y :(top)x son x; :!x, :^x y :(exclude)x cubren todo lo demás, como '.') y
  #     abreviaturas de las opciones largas, que git acepta (reset --ha es --hard)
  git_norm=$(printf '%s' "$minusculas" | sed -E -e 's/([[:space:]=]):(\([^)]*exclude[^)]*\)|[!^])[^[:space:]]*/\1./g' \
    -e 's/([[:space:]=]):\([^)]*\)/\1/g' -e 's/([[:space:]=]):\/([[:space:]]|$)/\1.\2/g' -e 's/([[:space:]=]):\/+/\1/g')
  sin_stash_lectura=$(printf '%s' "$git_norm" | sed -E 's/stash([[:space:]]+)(list|show)/stash-lectura\1/g')
  if por_segmento "$sin_stash_lectura" "${git_orden}(stash|clean|filter-branch|filter-repo|checkout-index|read-tree)([[:space:]]|$)" \
    || por_segmento "$git_norm" "${git_orden}reset([[:space:]].*)?[[:space:]]--(h|ha|har|hard|me|mer|merg|merge|k|ke|kee|keep)([=[:space:]]|$)" \
    || por_segmento "$git_norm" "${git_orden}(checkout|restore|switch)([[:space:]].*)?[[:space:]](\.|\./|\*|-[[:alnum:]]*[fw][[:alnum:]]*|--(f|fo|for|forc|force|di[a-z-]*|w|wo|wor|work|workt|worktr|worktre|worktree))([=[:space:]]|$)"; then
    bloquea "no se descartan cambios sin confirmar con git desde Bash (stash, clean, reset --hard, checkout/restore de '.' o con -f, también con abreviaturas o pathspecs como :/ y :!x): se perderían el registro y la procedencia aún no confirmados. Si hace falta, que lo haga una persona."
  fi
  if por_segmento "$git_norm" "${git_orden}(checkout|restore|reset|revert)([[:space:]].*)?[[:space:]=](\./)?(research(/|[[:space:]]|$)|[^[:space:]]*(nutria\.sqlite|procedencia\.jsonl))" \
    || por_segmento "$git_norm" "${git_orden}(rm|mv)([[:space:]].*)?[[:space:]=](\./)?(research/?(\*|\.)?([[:space:]]|$)|[^[:space:]]*(nutria\.sqlite|procedencia\.jsonl))"; then
    bloquea "no se restaura, borra ni mueve el registro con git desde Bash: el registro y la procedencia solo cambian a través de nutria (lo que se registra no se borra). Si hace falta, que lo haga una persona."
  fi
  if por_segmento "$minusculas" "${git_orden}(reflog[[:space:]]+(expire|delete)|update-ref)([[:space:]]|$)" \
    || contiene "$minusculas" 'gc\.reflogexpire|core\.logallrefupdates' \
    || por_segmento "$minusculas" "${ini}(rm|mv|shred|truncate|unlink|rsync|rmdir)${fin}.*[[:space:]=](\./)?\.git(/(logs|refs|packed-refs)[^[:space:]]*)?/?([[:space:]]|$)"; then
    bloquea "no se borra ni se reescribe el reflog de git (ni .git) desde Bash: \`nutria procedencia verificar\` lo usa para detectar la historia reescrita tras quitar eventos ya confirmados. Si hace falta, que lo haga una persona."
  fi
fi

# 4. Sellos de verificación (la clave, también en las órdenes de solo lectura; también con ./ o // en la ruta, con
#    comodines en cualquier componente, entrando con cd en .config o recorriéndola entera)
mensaje_clave="la clave de los sellos de verificación no es accesible para los agentes."
if contiene "$minusculas" 'clave_sello|nutria_clave_sello|(\.config|config_home[^[:space:]/]*)/[./]*nutria|(\.config|config_home[^[:space:]/]*)/[^[:space:];&|]*[*?[]' \
  || contiene "$minusculas" "${ini}(cd|pushd)[[:space:]]+[^;&|]*(\.config|config_home)" \
  || contiene "$minusculas" "${ini}(grep|egrep|fgrep|rg|ag|ack|find|tar|zip|7z|rsync|scp|cp|du)${fin}[^;&|]*(\.config|config_home[^[:space:]/]*)/?([[:space:];&|)]|$)"; then
  bloquea "$mensaje_clave"
fi
busca_ficheros=0
contiene "$minusculas" "${ini}(find|locate|plocate|mlocate|fd|fdfind)${fin}" && busca_ficheros=1
comodines=$(printf '%s' "$minusculas" | tr '()<>=,;&|\t' '          ' | tr ' ' '\n' | grep -E '[*?]|\[.*\]|\{.*,.*\}' | sort -u)
if [ "$(printf '%s\n' "$comodines" | grep -c .)" -gt 100 ]; then
  bloquea "demasiados patrones con comodines en una sola orden: escribe los nombres de los ficheros."
fi
for palabra in $comodines; do
  es_comodin "$palabra" || continue
  if [ "$busca_ficheros" = 1 ] && cubre "${palabra##*/}" clave_sello; then bloquea "$mensaje_clave"; fi
  case "$palabra" in */*) ;; *) continue ;; esac
  anterior=''
  resto=$palabra
  while :; do
    componente=${resto%%/*}
    case "$anterior" in   # un comodín de la shell solo cubre un nombre con punto inicial si el patrón empieza por él
      .*|\[.*) if cubre "$anterior" .config && cubre "$componente" nutria; then bloquea "$mensaje_clave"; fi ;;
    esac
    anterior=$componente
    case "$resto" in */*) resto=${resto#*/} ;; *) break ;; esac
  done
done
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
  # La identidad va en --agente: NUTRIA_AGENT no se usa, ni se exportan variables con nombres construidos (así no se
  # fija NUTRIA_AGENT sin escribirlo: set -a; read ...; export $V=...; printf -v $V ...).
  if en_alguna_variante 'NUTRIA_AGENT'; then
    bloquea "dentro de un subagente, la identidad va en --agente $tipo: NUTRIA_AGENT no se usa ni se menciona en las órdenes."
  fi
  if contiene "$minusculas" "${ini}set[[:space:]]+(-[[:alnum:]]*a|[-+]o[[:space:]]+allexport)|allexport" \
    || contiene "$sin_comillas" "${ini}(export|declare|typeset|readonly|local)([[:space:]]+-[[:alnum:]]+)*[[:space:]]+[^[:space:]=]*[\$\`][^[:space:]]*=|${ini}(printf[[:space:]]+-v|read|mapfile|readarray)([[:space:]]+-[[:alpha:]]+)*([[:space:]]+[A-Za-z_][A-Za-z0-9_]*)*[[:space:]]+[^[:space:]<]*[\$\`]"; then
    bloquea "dentro de un subagente no se exportan variables con set -a ni con nombres construidos (export \$V=..., read \$V, printf -v \$V): la identidad va en --agente $tipo."
  fi
  # git, solo para leer, añadir y confirmar: un subagente no reescribe la historia ni mueve referencias.
  for sub in $(printf '%s\n' "$minusculas" | tr ';&|()`' '\n\n\n\n\n\n' | grep -oE "${git_orden}[a-z][a-z-]*" | sed -E 's/.*[[:space:]]//'); do
    case "$sub" in
      status|log|diff|show|blame|annotate|ls-files|ls-tree|rev-parse|rev-list|grep|describe|shortlog|cat-file|whatchanged|name-rev|merge-base|version|help|add|commit) ;;
      *) bloquea "dentro de un subagente, git solo se usa para leer (status, log, diff, show...), añadir y confirmar; 'git $sub' no (lo hace una persona)." ;;
    esac
  done
  if por_segmento "$minusculas" "${git_orden}commit([[:space:]].*)?[[:space:]]--(am|ame|amen|amend|fi|fix|fixu|fixup|sq|squ|squa|squas|squash)([=[:space:]]|$)"; then
    bloquea "dentro de un subagente no se reescriben commits (git commit --amend, --fixup, --squash): lo hace una persona."
  fi
  # nutria se ejecuta directamente, no a través de un intérprete o de otra orden que construya sus argumentos: así el
  # rol declarado está a la vista (python -c "subprocess.run([... '--agente', R ...])", xargs ... {}, eval).
  if contiene "$plano" "$menciona_nutria" \
    && contiene "$plano" "${ini}(xargs|eval|parallel)${fin}|subprocess|os\.(system|popen|exec[a-z]*|spawn[a-z]*)|(from|import)[[:space:]]+nutria|nutria\.(cli|__main__)|runpy|import_module"; then
    bloquea "dentro de un subagente, nutria se ejecuta directamente (nutria --agente $tipo ...), no a través de Python, xargs ni eval: así el rol declarado con --agente se puede comprobar."
  fi
  # Cada orden simple que menciona nutria lleva su --agente (también dentro de $(...), subshells y bucles).
  if printf '%s\n' "$sin_comillas" | tr ';&|()`' '\n\n\n\n\n\n' | grep -E -e "$menciona_nutria" | grep -Evq -e '--agente(=|[[:space:]])'; then
    if [ "$tipo" = "evaluador-independiente" ]; then
      bloquea "un evaluador independiente se identifica en cada orden nutria: nutria --agente evaluador-<n> ... (regla 9; así la CLI le oculta las puntuaciones ajenas)."
    fi
    bloquea "dentro de un subagente, cada orden nutria lleva --agente $tipo (o una variante suya, como $tipo-A), escrito literalmente: la CLI atribuye la acción con él."
  fi
  # Valor de cada --agente: con = o tras espacios o comas (también en una lista de Python), hasta el siguiente espacio,
  # coma, paréntesis o corchete de cierre o separador de órdenes. Un valor que no sea un nombre literal se bloquea
  # ($R, ${R}, $(...), `...`, {} de xargs, comodines). Los valores repetidos se comprueban una vez.
  valores=$(printf '%s' "$sin_comillas" | grep -oE -- '--agente(=|[[:space:],]+)[^][:space:];&|,)]*' \
    | sed -E 's/^--agente(=|[[:space:],]+)//' | sort -u)
  if [ "$(printf '%s\n' "$valores" | grep -c .)" -gt 20 ]; then
    bloquea "demasiados valores distintos de --agente en una sola orden: dentro de un subagente, cada orden nutria lleva --agente $tipo."
  fi
  for valor in $valores; do
    case "$valor" in
      *[!A-Za-z0-9_-]*)
        bloquea "--agente '$valor': dentro de un subagente, --agente lleva el nombre literal del rol (sin variables, sustituciones ni comodines); este subagente es '$tipo'." ;;
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
  fi
fi
exit 0
