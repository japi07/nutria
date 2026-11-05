"""Registro de procedencia append-only (JSONL) con cadena de hashes.

Cada consulta a una fuente externa, cálculo, llamada a un modelo o decisión queda registrada con marca de
tiempo UTC, actor, entradas (sin secretos), salidas y el contexto de ejecución. El fichero
`research/procedencia.jsonl` se versiona con git.

Campos de cada evento (claves ordenadas en el JSON):

- `ts`, `actor` (--agente, NUTRIA_AGENT o "cli"), `action`, `inputs`, `outputs` (sin secretos) y `nutria` (versión);
- `contexto`: "claude-code" si el proceso corre dentro de Claude Code (alguna variable de AGENT_ENV_VARS en el
  entorno o en el entorno inicial de un proceso antecesor, o el propio Claude Code entre los antecesores; sin /proc,
  los antecesores se recorren con `ps`, que no muestra su entorno), "terminal" en otro caso.
  `human_terminal_problems` añade, para la firma humana, la falta de terminal de control. Dentro de Claude Code,
  `sesion_claude_code` enlaza el evento con la transcripción de la sesión (donde constan el agente y el modelo, que la
  CLI no puede conocer);
- `git`: {"commit", "cambios_sin_confirmar"} del proyecto (cambios fuera de research/, que es donde nutria escribe
  sus datos); `entorno`: versiones de Python y de las bibliotecas de cálculo. Se calculan una vez por proceso;
- opcionales: `parametros` y `huellas` ({"entrada": {nombre: sha256}, "salida": {...}}), ver `log_event`;
- `prev` y `hash`: la cadena.

Integridad. `hash` es el SHA-256 del JSON canónico del evento sin el campo `hash`; `prev` es el `hash` del evento
encadenado anterior. El primer evento encadenado de un fichero ancla todo lo anterior: su `prev` es el SHA-256 de
los bytes previos (el de la cadena vacía si el fichero no existía), así que los eventos de versiones anteriores,
sin cadena, quedan cubiertos. Las escrituras se serializan con un bloqueo de fichero (POSIX). `verify_chain`
(orden `nutria procedencia verificar`) detecta líneas corruptas, eventos alterados, la cadena rota, eventos sin
cadena añadidos después y, si el fichero está en git, la reescritura o el borrado de eventos ya confirmados: el
fichero (aunque haya desaparecido) debe ampliar cada versión confirmada en el historial de HEAD y en las puntas
anteriores de la rama actual (su reflog: lo que había antes de un commit --amend, un reset o un rebase).

Límites: una cadena de hashes no autentica. Quien pueda escribir el fichero puede recalcular la cola entera, añadir
eventos al final o truncar los que aún no estén confirmados en git; lo que detecta es la corrupción, las
alteraciones parciales y los borrados o inserciones intermedios. La referencia es el repositorio local: quien
reescriba la historia y además borre el reflog de la rama (git reflog expire, gc, borrar o renombrar la rama con
git branch -D/-M) o siga en otra rama elude la comparación; solo una copia fuera de su alcance (la rama remota) la
cubre. La cadena supone un único historial lineal: dos ramas de git que añadan eventos por separado la rompen al
fusionarse, y lo que una persona descarte a propósito (reset, rebase) se señala mientras siga en el reflog de la
rama. Los hooks de Claude Code dificultan a los agentes escribir el fichero o la historia de git por fuera de nutria,
pero son una barrera de texto: no ven un programa escrito en un fichero ni una orden construida de forma indirecta.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import __version__

try:  # bloqueo de fichero en POSIX; en Windows se escribe sin bloqueo
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

_SENSITIVE_KEYS = {
    "api_key", "apikey", "key", "token", "email", "mailto", "x-api-key",
    "authorization", "password", "secret",
}

# Variables con las que Claude Code (y otros agentes de IA) marcan los procesos que lanzan (las órdenes Bash de sus
# agentes las heredan).
AGENT_ENV_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID",
                  "AI_AGENT")
# Raíz de /proc (Linux) con la que se recorren los procesos antecesores. Las pruebas la sustituyen.
PROC_ROOT = "/proc"
# Donde no hay /proc (macOS, BSD), los antecesores se recorren con esta orden (tabla pid, ppid, terminal y orden de
# todos los procesos, sin recortar las líneas: -ww). None la desactiva (lo hacen las pruebas, que simulan una terminal
# de persona).
PS_COMMAND: tuple[str, ...] | None = ("ps", "-ww", "-A", "-o", "pid=,ppid=,tty=,args=")
# `prev` del primer evento de un fichero vacío: SHA-256 de la cadena vacía.
GENESIS = hashlib.sha256(b"").hexdigest()
_HEX64 = re.compile(r"[0-9a-f]{64}")
_CHUNK = 1 << 16
# Valores que se calculan una vez por proceso (contexto, estado de git, versiones). Las pruebas lo sustituyen.
_PROCESS_CACHE: dict = {}


class ProcedenciaCorruptaWarning(UserWarning):
    """Aviso de `read_events` cuando el fichero de procedencia tiene líneas que no son eventos JSON."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj: Any) -> bytes:
    """JSON canónico (claves ordenadas, sin espacios, UTF-8) con el que se calculan las huellas."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def sha256_json(obj: Any) -> str:
    """Huella SHA-256 del JSON canónico de `obj` (p. ej. el estado de entrada de un ranking)."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def event_hash(event: Mapping) -> str:
    """Huella de un evento: SHA-256 de su JSON canónico sin el campo `hash`."""
    return sha256_json({k: v for k, v in event.items() if k != "hash"})


def current_actor(explicit: str | None = None) -> str:
    """Quién ejecuta la acción: --agente, NUTRIA_AGENT o "cli" (sin espacios alrededor)."""
    return declared_agent(explicit) or "cli"


def declared_agent(explicit: str | None = None) -> str:
    """Rol declarado de quien ejecuta la orden (--agente o, si no se indicó, NUTRIA_AGENT); "" si no declara ninguno.

    Sin espacios alrededor (`--agente " desarrollo-industrial "` se guarda como desarrollo-industrial). Es una
    declaración, no una identidad comprobada: el hook de Bash la contrasta con el tipo de subagente de Claude Code
    cuando lo hay (scripts/hooks/guard_bash.sh)."""
    explicit = str(explicit).strip() if explicit is not None else ""
    return explicit or os.environ.get("NUTRIA_AGENT", "").strip()


def scrub(obj: Any) -> Any:
    """Elimina valores de claves sensibles de forma recursiva."""
    if isinstance(obj, dict):
        return {
            k: ("***" if str(k).lower() in _SENSITIVE_KEYS else scrub(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [scrub(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------- contexto de ejecución
def _is_claude_code_process(comm: str, cmdline: list[str]) -> bool:
    """¿Es el proceso Claude Code (binario `claude`, o node/bun/deno ejecutando el paquete de Claude Code)?"""
    if comm.strip() == "claude":
        return True
    if not cmdline:
        return False
    exe = cmdline[0].replace("\\", "/").rsplit("/", 1)[-1]
    if exe in ("claude", "claude.exe"):
        return True
    if exe.startswith(("node", "bun", "deno")) and len(cmdline) > 1:
        script = cmdline[1].replace("\\", "/")
        name = script.rsplit("/", 1)[-1]
        # instalación con npm: `node /usr/local/bin/claude` (enlace al cli.js del paquete @anthropic-ai/claude-code)
        return "claude-code" in script or name in ("claude", "claude.js") or ("anthropic" in script and name == "cli.js")
    return False


def _proc_chain(proc_root: Path | str | None = None, pid: int | None = None,
                max_depth: int = 64) -> list[dict] | None:
    """El proceso y sus antecesores según /proc (Linux), hasta init: [{pid, environ, comm, cmdline, tty}].

    `environ` es el entorno inicial (/proc/<pid>/environ no cambia aunque el proceso borre variables) y `tty`, el
    número de su terminal de control (0 si no tiene). Devuelve None si no hay /proc (macOS, Windows). Sin
    `proc_root`, usa PROC_ROOT.
    """
    root = Path(PROC_ROOT if proc_root is None else proc_root)
    current = pid if pid is not None else os.getpid()
    if not (root / str(current)).is_dir():
        return None
    chain: list[dict] = []
    seen: set[int] = set()
    for _ in range(max_depth):
        if current <= 1 or current in seen:
            break
        seen.add(current)
        base = root / str(current)
        info: dict[str, Any] = {"pid": current, "environ": b"", "comm": "", "cmdline": [], "tty": 0}
        try:
            info["environ"] = (base / "environ").read_bytes()
        except OSError:
            pass
        try:
            info["comm"] = (base / "comm").read_text("utf-8", "replace")
        except OSError:
            pass
        try:
            info["cmdline"] = [a for a in (base / "cmdline").read_bytes().decode("utf-8", "replace").split("\0") if a]
        except OSError:
            pass
        chain.append(info)
        try:  # campos tras "(comm)": estado, ppid, pgrp, sesión, tty_nr...
            fields = (base / "stat").read_text("utf-8", "replace").rsplit(")", 1)[1].split()
            info["tty"] = int(fields[4])
            current = int(fields[1])
        except (OSError, ValueError, IndexError):
            break
    return chain


def _ps_chain(pid: int | None = None, max_depth: int = 64) -> list[dict] | None:
    """Como `_proc_chain`, con la tabla de procesos de `ps` (PS_COMMAND) donde no hay /proc (macOS, BSD).

    `ps` no muestra el entorno inicial de los procesos (`environ` vacío): solo sirve para reconocer a Claude Code
    entre los antecesores y para saber si alguno tiene terminal de control. None si no hay `ps` o falla.
    """
    if not PS_COMMAND:
        return None
    try:
        out = subprocess.run(list(PS_COMMAND), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    table: dict[int, tuple[int, str, list[str]]] = {}
    for line in out.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 3 or not (parts[0].isdigit() and parts[1].isdigit()):
            continue
        table[int(parts[0])] = (int(parts[1]), parts[2], parts[3].split() if len(parts) > 3 else [])
    current = pid if pid is not None else os.getpid()
    if current not in table:
        return None
    chain: list[dict] = []
    seen: set[int] = set()
    for _ in range(max_depth):
        if current <= 1 or current in seen or current not in table:
            break
        seen.add(current)
        ppid, tty, argv = table[current]
        chain.append({"pid": current, "environ": b"", "cmdline": argv,
                      "comm": argv[0].replace("\\", "/").rsplit("/", 1)[-1] if argv else "",
                      "tty": 0 if tty.strip("?-") == "" else 1})   # "?" (Linux), "??" (macOS): sin terminal
        current = ppid
    return chain


def _process_chain(proc_root: Path | str | None = None, pid: int | None = None,
                   max_depth: int = 64) -> list[dict] | None:
    """Cadena de antecesores por /proc y, si no lo hay y no se pidió otra raíz, por `ps`."""
    chain = _proc_chain(proc_root, pid, max_depth)
    if chain is None and proc_root is None:
        chain = _ps_chain(pid, max_depth)
    return chain


def ancestor_markers(proc_root: Path | str | None = None, pid: int | None = None, max_depth: int = 64) -> list[str]:
    """Señales de Claude Code en el proceso y sus antecesores (vía /proc en Linux; vía `ps` donde no hay /proc).

    Recorre la cadena de padres desde el propio proceso y devuelve, para cada uno, las variables de
    AGENT_ENV_VARS de su entorno inicial (el de /proc/<pid>/environ, que no cambia aunque el proceso las borre)
    y si es el propio Claude Code. Así se reconoce una orden lanzada por un agente aunque haya vaciado su
    entorno (`env -i`, `env -u`, `exec -c`). Con `ps` solo se reconoce al propio Claude Code (no hay entorno
    inicial); sin /proc ni `ps` (Windows) devuelve [].
    """
    found: list[str] = []
    for proc in _process_chain(proc_root, pid, max_depth) or ():
        for var in AGENT_ENV_VARS:
            if (b"\0" + var.encode() + b"=") in (b"\0" + proc["environ"]):
                found.append(f"{var} (proceso {proc['pid']})")
        if _is_claude_code_process(proc["comm"], proc["cmdline"]):
            found.append(f"Claude Code (proceso {proc['pid']})")
    return found


def interactive_terminal(proc_root: Path | str | None = None, pid: int | None = None) -> bool | None:
    """¿Tiene terminal de control el proceso o alguno de sus antecesores? None si no se puede saber (sin /proc ni
    `ps`).

    Una persona firma en su terminal; las órdenes Bash de Claude Code se ejecutan en una sesión propia sin
    terminal, y un proceso desvinculado de Claude Code (adoptado por init, p. ej. con un doble fork) tampoco
    tiene ninguna en su cadena. Límite: un multiplexor (tmux, screen) o un programa con su propia pseudoterminal
    dan terminal a una orden lanzada desde Claude Code con el entorno vacío (el hook de Bash bloquea esas órdenes).
    """
    chain = _process_chain(proc_root, pid)
    if chain is None:
        return None
    return any(proc["tty"] for proc in chain)


def agent_markers(include_ancestors: bool = True) -> list[str]:
    """Señales de que el proceso lo lanzó Claude Code u otro agente de IA (entorno propio y antecesores)."""
    found = [var for var in AGENT_ENV_VARS if var in os.environ]
    if include_ancestors:
        found += ancestor_markers()
    return found


def human_terminal_problems() -> list[str]:
    """Motivos por los que este proceso no es la terminal de una persona (lista vacía si lo es).

    Son las señales de agente de `agent_markers` y, en Linux, la falta de terminal de control en toda la cadena de
    procesos (orden no interactiva o desvinculada). La CLI rechaza la firma humana si hay alguno.
    """
    problems = agent_markers()
    if interactive_terminal() is False:
        problems.append("sin terminal de control en el proceso ni en sus antecesores (orden no interactiva o "
                        "desvinculada de la sesión)")
    return problems


def signature_problems(actor: str | None = None) -> list[str]:
    """Motivos por los que este proceso no puede registrar una firma humana (lista vacía si puede).

    La firma humana (regla 12) solo se acepta desde la terminal de una persona: sin --agente (`actor`, si se pasa),
    sin NUTRIA_AGENT y sin las señales de `human_terminal_problems`. La comprueban la CLI, antes de tocar nada, y el
    registro, en cada función que guarda una firma, así que también se rechaza a un programa que llame a esas
    funciones sin pasar por la CLI desde Claude Code. Es una atestación sin autenticar (ver `nutria.cli`).
    """
    problems = []
    if actor is not None:
        problems.append("--agente")
    if "NUTRIA_AGENT" in os.environ:
        problems.append("NUTRIA_AGENT")
    return problems + human_terminal_problems()


def execution_context() -> str:
    """"claude-code" o "terminal"; se calcula una vez por proceso."""
    if "contexto" not in _PROCESS_CACHE:
        _PROCESS_CACHE["contexto"] = "claude-code" if agent_markers() else "terminal"
    return _PROCESS_CACHE["contexto"]


# ---------------------------------------------------------------------------- git y entorno
def _run_git(root: Path, args: list[str], timeout: float, env: Mapping[str, str] | None = None,
             input: bytes | None = None):
    """Ejecuta `git <args>` en `root` (salida en bytes; `input`, la entrada); None si git no está o no se pudo
    ejecutar a tiempo.

    Toda llamada a git de este módulo pasa por aquí: las pruebas lo sustituyen (tests/conftest.py) para no lanzar git
    salvo que lo pidan, porque `subprocess` con `timeout` espera con `time.sleep`, que otras pruebas sustituyen.
    """
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=timeout, input=input,
                              env=dict(env) if env is not None else None)
    except (OSError, subprocess.SubprocessError):
        return None


def git_commit(root: Path) -> str | None:
    out = _run_git(root, ["rev-parse", "HEAD"], 3)
    if out is None or out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", "replace").strip() or None


def git_state(root: Path | None) -> dict:
    """Commit del proyecto y si hay cambios sin confirmar fuera de research/ (una vez por proceso y raíz)."""
    if root is None:
        return {"commit": None, "cambios_sin_confirmar": None}
    key = ("git", str(root))
    if key not in _PROCESS_CACHE:
        commit = git_commit(root)
        dirty = None
        if commit is not None:   # GIT_OPTIONAL_LOCKS=0: `git status` no toma el bloqueo del índice (no molesta a otro git)
            out = _run_git(root, ["status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude)research"], 5,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
            dirty = bool(out.stdout.strip()) if out is not None and out.returncode == 0 else None
        _PROCESS_CACHE[key] = {"commit": commit, "cambios_sin_confirmar": dirty}
    return dict(_PROCESS_CACHE[key])


def runtime_versions() -> dict:
    """Versiones de Python y de las bibliotecas de las que dependen los resultados (una vez por proceso)."""
    if "entorno" not in _PROCESS_CACHE:
        from importlib import metadata
        versions = {"python": sys.version.split()[0]}
        for dist in ("numpy", "scipy", "statsmodels", "jsonschema", "requests"):
            try:
                versions[dist] = metadata.version(dist)
            except metadata.PackageNotFoundError:  # dependencia opcional ausente
                continue
        _PROCESS_CACHE["entorno"] = versions
    return dict(_PROCESS_CACHE["entorno"])


def environment_snapshot(root: Path) -> dict:
    versions = {"nutria": __version__, "python": sys.version.split()[0], "platform": platform.platform()}
    from importlib import metadata
    for dist in ("numpy", "scipy", "requests", "jsonschema", "statsmodels"):
        try:
            versions[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:  # dependencia opcional ausente
            continue
    versions["git_commit"] = git_commit(root)
    return versions


# ---------------------------------------------------------------------------- escritura
def _fingerprints(files: Iterable[str | Path] | None, hashes: Mapping[str, str] | None) -> dict:
    out: dict[str, str | None] = {}
    for f in files or ():
        path = Path(f)
        out[str(f)] = sha256_file(path) if path.is_file() else None
    for name, value in (hashes or {}).items():
        out[str(name)] = value
    return out


def _declared_hash(raw: bytes) -> str | None:
    raw = raw.strip()
    if not raw.startswith(b"{"):
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    value = obj.get("hash") if isinstance(obj, dict) else None
    return value if isinstance(value, str) and _HEX64.fullmatch(value) else None


def _chain_head(fh) -> tuple[str, bool]:
    """Huella a la que se encadena el próximo evento y si falta el salto de línea final del fichero.

    Es el `hash` del último evento encadenado; si no hay ninguno, el SHA-256 de todo el contenido previo (que así
    queda anclado). `fh` es el fichero abierto en modo binario de lectura y añadido, ya bloqueado.
    """
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    if size == 0:
        return GENESIS, False
    fh.seek(size - 1)
    needs_newline = fh.read(1) != b"\n"
    pos, tail = size, b""
    while pos > 0:
        step = min(_CHUNK, pos)
        pos -= step
        fh.seek(pos)
        tail = fh.read(step) + tail
        lines = tail.split(b"\n")
        complete = lines if pos == 0 else lines[1:]
        for raw in reversed(complete):
            found = _declared_hash(raw)
            if found:
                return found, needs_newline
        tail = lines[0] if pos > 0 else b""
    digest = hashlib.sha256()
    fh.seek(0)
    for chunk in iter(lambda: fh.read(_CHUNK), b""):
        digest.update(chunk)
    if needs_newline:
        digest.update(b"\n")
    return digest.hexdigest(), needs_newline


def log_event(cfg, action: str, inputs: dict | None = None, outputs: dict | None = None,
              actor: str | None = None, *, params: dict | None = None,
              input_files: Iterable[str | Path] | None = None, output_files: Iterable[str | Path] | None = None,
              input_hashes: Mapping[str, str] | None = None, output_hashes: Mapping[str, str] | None = None) -> dict:
    """Añade un evento encadenado al registro de procedencia y lo devuelve tal como quedó escrito.

    - `inputs`, `outputs`: datos del evento (se eliminan las claves sensibles: api_key, token, email...).
    - `actor`: quién ejecuta la acción; por defecto, `cfg.actor` (--agente), NUTRIA_AGENT o "cli".
    - `params`: parámetros que hacen falta para reproducir el resultado (método, semilla, perfil, rúbrica...);
      quedan en `parametros`.
    - `input_files`, `output_files`: rutas cuyo SHA-256 se calcula en el momento (None si no existen);
      quedan en `huellas.entrada` y `huellas.salida`, con la ruta como clave.
    - `input_hashes`, `output_hashes`: huellas ya calculadas, {nombre: sha256} (p. ej. `sha256_json(estado)` del
      estado de entrada de un ranking o de los IDs devueltos por una búsqueda); se añaden a las mismas secciones.

    Ejemplo: `log_event(cfg, "metaanalisis", inputs={...}, outputs={...}, params={"metodo": "REML"},
    input_files=[csv])`. El evento lleva además contexto, git, entorno, `prev` y `hash` (ver el módulo).
    """
    event: dict[str, Any] = {
        "ts": utc_now(),
        "actor": current_actor(actor or getattr(cfg, "actor", None)),
        "action": action,
        "inputs": scrub(inputs or {}),
        "outputs": scrub(outputs or {}),
        "nutria": __version__,
        "contexto": execution_context(),
    }
    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if session:   # enlaza el evento con la transcripción de Claude Code (agente, modelo e instrucciones)
        event["sesion_claude_code"] = session
    path: Path = cfg.provenance_path
    root = getattr(cfg, "root", None)
    event["git"] = git_state(Path(root) if root is not None else path.parent.parent)
    event["entorno"] = runtime_versions()
    if params:
        event["parametros"] = scrub(params)
    entrada, salida = _fingerprints(input_files, input_hashes), _fingerprints(output_files, output_hashes)
    if entrada or salida:
        event["huellas"] = {"entrada": entrada, "salida": salida}
    # Forma serializada (lo que se escribe y lo que se verifica después): Path, tuplas, etc. ya convertidos, y los
    # sustitutos sueltos de UTF-8 (argumentos con bytes no válidos) reemplazados, para que la línea sea UTF-8 válido.
    serialized = json.dumps(event, ensure_ascii=False, default=str).encode("utf-8", "replace").decode("utf-8")
    record = json.loads(serialized)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as fh:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            prev, needs_newline = _chain_head(fh)
            record["prev"] = prev
            record["hash"] = event_hash(record)
            line = json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
            fh.write((b"\n" if needs_newline else b"") + line)
            fh.flush()
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return record


# ---------------------------------------------------------------------------- lectura y verificación
class EventList(list):
    """Lista de eventos con el recuento de líneas que no se pudieron leer (`corruptas`: números de línea)."""

    def __init__(self, events=(), corruptas: list[int] | None = None):
        super().__init__(events)
        self.corruptas: list[int] = list(corruptas or [])


def read_events(cfg, limit: int | None = None, action: str | None = None) -> EventList:
    """Eventos del registro (los últimos `limit`, filtrados por `action`).

    Las líneas que no son un evento JSON no se ignoran en silencio: se cuentan en `.corruptas` (números de línea)
    y se avisa con `ProcedenciaCorruptaWarning`. Para comprobar la integridad, `verify_chain`.
    """
    path: Path = cfg.provenance_path
    if not path.is_file():
        return EventList()
    events, corrupt = [], []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                corrupt.append(lineno)
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                corrupt.append(lineno)
                continue
            if not isinstance(ev, dict):
                corrupt.append(lineno)
                continue
            if action and ev.get("action") != action:
                continue
            events.append(ev)
    if corrupt:
        warnings.warn(f"{len(corrupt)} línea(s) corrupta(s) en {path} (líneas {_lines_text(corrupt)}): "
                      "comprueba la integridad con `nutria procedencia verificar`",
                      ProcedenciaCorruptaWarning, stacklevel=2)
    return EventList(events[-limit:] if limit else events, corrupt)


def _lines_text(lines: list[int], limit: int = 10) -> str:
    text = ", ".join(map(str, lines[:limit]))
    return text + (f" y {len(lines) - limit} más" if len(lines) > limit else "")


def _git_lines(out) -> list[str]:
    if out is None or out.returncode != 0:
        return []
    return out.stdout.decode("utf-8", "replace").split()


def _committed_versions(path: Path, root: Path) -> dict | None:
    """Versiones del fichero confirmadas en git, sin leer su contenido: {"head": commit, "versiones": [(commit,
    origen, blob, tamaño)]}, con la de HEAD primero.

    Son la de HEAD, las de las puntas anteriores de la rama actual (su reflog: lo que había antes de un commit
    --amend, un reset o un rebase) y las del historial de HEAD. None si no hay git, ni commits, o el fichero está
    fuera del repositorio. Cuatro llamadas a git, sean cuantas sean las versiones.
    """
    try:
        rel = Path(os.path.relpath(path, root)).as_posix()
    except ValueError:  # pragma: no cover - otra unidad en Windows
        return None
    if rel.startswith(".."):
        return None
    head = _git_lines(_run_git(root, ["rev-parse", "--verify", "-q", "HEAD"], 10))
    if not head:
        return None
    history = _git_lines(_run_git(root, ["rev-list", "HEAD", "--", f"./{rel}"], 60))
    in_history = set(history)
    commits: dict[str, str] = {head[0]: "HEAD"}
    branch = _git_lines(_run_git(root, ["symbolic-ref", "-q", "HEAD"], 10))
    if branch:   # en HEAD separado no hay rama: su reflog mezclaría las de otras ramas
        name = branch[0].removeprefix("refs/heads/")
        for commit in _git_lines(_run_git(root, ["log", "-g", "--format=%H", branch[0], "--"], 60)):
            commits.setdefault(commit, "historial" if commit in in_history else f"reflog de {name}")
    for commit in history:
        commits.setdefault(commit, "historial")
    query = "".join(f"{commit}:./{rel}\n" for commit in commits).encode()
    out = _run_git(root, ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"], 60, input=query)
    if out is None or out.returncode != 0:
        return None
    versions = []
    for (commit, origin), line in zip(commits.items(), out.stdout.decode("utf-8", "replace").splitlines()):
        parts = line.split()   # "<blob> blob <tamaño>", o "<consulta> missing" si en ese commit no estaba el fichero
        if len(parts) == 3 and parts[1] == "blob" and parts[2].isdigit():
            versions.append((commit, origin, parts[0], int(parts[2])))
    return {"head": head[0], "versiones": versions}


def _git_blob_id(data, object_id_length: int) -> str:
    """Identificador de git del contenido `data` (SHA-1, o SHA-256 en repositorios con ese formato)."""
    digest = hashlib.new("sha1" if object_id_length == 40 else "sha256")
    digest.update(b"blob %d\0" % len(data))
    digest.update(data)
    return digest.hexdigest()


# Tope de bytes que `verify_chain` resume para comparar con las versiones confirmadas en git (las más recientes
# primero); lo que no cabe se señala como aviso.
GIT_VERIFY_BUDGET = 4 << 30


def _compare_with_git(data: bytes, exists: bool, path: Path, root: Path, problems: list, warnings_: list) -> dict:
    """Comprueba que el fichero actual amplía cada versión confirmada en git (ver `_committed_versions`).

    Una versión confirmada queda ampliada si es un prefijo del fichero actual; se comprueba con el identificador del
    objeto de git (el resumen del prefijo del mismo tamaño), sin leer las versiones. Los finales de línea CRLF de
    una copia de trabajo en Windows se comparan también normalizados.
    """
    found = _committed_versions(path, root)
    if found is None or not found["versiones"]:
        return {"estado": "sin_referencia"}
    normalized = data.replace(b"\r\n", b"\n")
    variants = [memoryview(normalized)] + ([memoryview(data)] if normalized != data else [])
    budget, verdicts, unchecked = GIT_VERIFY_BUDGET, {}, []
    not_extended: list[tuple[str, str]] = []
    for commit, origin, blob, size in found["versiones"]:
        if blob not in verdicts:
            verdict = False
            for variant in variants:
                if size > len(variant):
                    continue
                if size > budget:
                    verdict = None
                    break
                budget -= size
                if _git_blob_id(variant[:size], len(blob)) == blob:
                    verdict = True
                    break
            verdicts[blob] = verdict
        if verdicts[blob] is None:
            unchecked.append(commit)
        elif not verdicts[blob]:
            not_extended.append((commit, origin))
    head_version = found["versiones"][0] if found["versiones"][0][1] == "HEAD" else None
    result: dict[str, Any] = {"commit": found["head"],
                              "versiones_comprobadas": sum(v is not None for v in verdicts.values())}
    if unchecked:
        warnings_.append(f"{len(unchecked)} versión(es) confirmada(s) en git sin comparar (tope de lectura de "
                         f"{GIT_VERIFY_BUDGET >> 20} MiB): las más antiguas")
    if not not_extended:
        result["estado"] = "amplia_lo_confirmado"
        if head_version is not None:
            result["eventos_nuevos"] = normalized[head_version[3]:].count(b"\n")
        return result
    detail = "; ".join(f"commit {commit[:12]} ({origin})" for commit, origin in not_extended[:3])
    if len(not_extended) > 3:
        detail += f" y {len(not_extended) - 3} más"
    result["no_ampliadas"] = [{"commit": commit, "origen": origin} for commit, origin in not_extended]
    if not exists:
        result["estado"] = "borrado"
        problems.append(f"el fichero de procedencia no existe, pero git tiene confirmados eventos suyos ({detail}): "
                        "se ha borrado la historia confirmada")
    else:
        result["estado"] = "reescrito"
        problems.append(f"el fichero no amplía {len(not_extended)} versión(es) confirmada(s) en git ({detail}): se "
                        "han modificado o borrado eventos ya confirmados")
    if any(origin.startswith("reflog") for _, origin in not_extended):
        problems.append("alguna de esas versiones solo está en el reflog de la rama: la historia se reescribió "
                        "(commit --amend, reset o rebase) después de quitar eventos confirmados")
    return result


def verify_chain(cfg, use_git: bool = True) -> dict:
    """Comprueba la integridad del registro de procedencia.

    Devuelve un dict con `integra` (bool), `problemas` (textos) y el detalle: eventos, encadenados,
    `anteriores_a_la_cadena` (eventos de versiones previas, anclados por el primer evento encadenado),
    `lineas_corruptas`, `corruptas_ancladas` (corruptas anteriores a la cadena y cubiertas por su ancla: aviso),
    `alterados` (el `hash` no corresponde al contenido), `cadena_rota` (`prev` no corresponde al evento
    anterior), `sin_cadena_tras_inicio` (eventos sin cadena añadidos después de empezar la cadena) y `git`
    (con `use_git`, ver `_compare_with_git`: "amplia_lo_confirmado", "reescrito", "borrado" si el fichero ya no
    existe pero git tiene eventos suyos confirmados, o "sin_referencia").
    """
    path: Path = cfg.provenance_path
    res: dict[str, Any] = {
        "fichero": str(path), "existe": path.is_file(), "eventos": 0, "encadenados": 0,
        "anteriores_a_la_cadena": 0, "lineas_corruptas": [], "corruptas_ancladas": [], "alterados": [],
        "cadena_rota": [], "sin_cadena_tras_inicio": [], "git": None, "avisos": [], "problemas": [],
        "integra": True,
    }
    if not path.is_file():
        # Que el fichero no exista no lo hace íntegro: si git tiene eventos suyos confirmados, se ha borrado la
        # historia (revisión: `rm research/*.jsonl` daba «íntegra (vacía)»).
        if use_git:
            root = Path(getattr(cfg, "root", None) or path.parent.parent)
            res["git"] = _compare_with_git(b"", False, path, root, res["problemas"], res["avisos"])
        if not res["problemas"]:
            res["avisos"].append("No hay registro de procedencia todavía")
        res["integra"] = not res["problemas"]
        return res
    data = path.read_bytes()
    corrupt_before: list[int] = []
    offset, lineno, last, started = 0, 0, None, False
    while offset < len(data):
        end = data.find(b"\n", offset)
        nxt = len(data) if end == -1 else end + 1
        end = len(data) if end == -1 else end
        raw = data[offset:end].strip()
        lineno += 1
        obj = None
        if raw:
            try:
                obj = json.loads(raw)
            except ValueError:
                obj = None
        if not isinstance(obj, dict):
            (res["lineas_corruptas"] if started else corrupt_before).append(lineno)
            offset = nxt
            continue
        res["eventos"] += 1
        if "hash" not in obj and "prev" not in obj:
            if started:
                res["sin_cadena_tras_inicio"].append(lineno)
            else:
                res["anteriores_a_la_cadena"] += 1
            offset = nxt
            continue
        declared = obj.get("hash")
        computed = event_hash(obj)
        if declared != computed:
            res["alterados"].append(lineno)
        expected = last if started else hashlib.sha256(data[:offset]).hexdigest()
        if obj.get("prev") != expected:
            res["cadena_rota"].append(lineno)
        last = declared if isinstance(declared, str) else computed
        started = True
        res["encadenados"] += 1
        offset = nxt
    if started:
        res["corruptas_ancladas"] = corrupt_before
    else:
        res["lineas_corruptas"] = corrupt_before + res["lineas_corruptas"]

    p = res["problemas"]
    if res["lineas_corruptas"]:
        p.append(f"{len(res['lineas_corruptas'])} línea(s) corrupta(s): {_lines_text(res['lineas_corruptas'])}")
    if res["alterados"]:
        p.append(f"{len(res['alterados'])} evento(s) alterado(s) (la huella no corresponde al contenido): "
                 f"líneas {_lines_text(res['alterados'])}")
    if res["cadena_rota"]:
        p.append(f"cadena rota en {len(res['cadena_rota'])} punto(s) (falta, sobra o se reordenó algún evento): "
                 f"líneas {_lines_text(res['cadena_rota'])}")
    if res["sin_cadena_tras_inicio"]:
        p.append(f"{len(res['sin_cadena_tras_inicio'])} evento(s) sin cadena añadidos después de empezar la cadena: "
                 f"líneas {_lines_text(res['sin_cadena_tras_inicio'])}")
    if not started and res["eventos"]:
        p.append(f"{res['eventos']} evento(s) sin cadena de hashes (versiones anteriores de nutria): su integridad no "
                 "puede comprobarse hasta que el siguiente evento los ancle")
    if started and res["anteriores_a_la_cadena"]:
        res["avisos"].append(f"{res['anteriores_a_la_cadena']} evento(s) anteriores a la cadena, anclados por el "
                             "primer evento encadenado")
    if res["corruptas_ancladas"]:
        res["avisos"].append(f"{len(res['corruptas_ancladas'])} línea(s) corrupta(s) anteriores a la cadena y "
                             f"ancladas por ella: {_lines_text(res['corruptas_ancladas'])}")
    if use_git:
        root = Path(getattr(cfg, "root", None) or path.parent.parent)
        res["git"] = _compare_with_git(data, True, path, root, p, res["avisos"])
    res["integra"] = not p
    return res
