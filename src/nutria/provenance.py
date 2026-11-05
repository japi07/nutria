"""Registro de procedencia append-only (JSONL) con cadena de hashes.

Cada consulta a una fuente externa, cálculo, llamada a un modelo o decisión queda registrada con marca de
tiempo UTC, actor, entradas (sin secretos), salidas y el contexto de ejecución. El fichero
`research/procedencia.jsonl` se versiona con git.

Campos de cada evento (claves ordenadas en el JSON):

- `ts`, `actor` (--agente, NUTRIA_AGENT o "cli"), `action`, `inputs`, `outputs` (sin secretos) y `nutria` (versión);
- `contexto`: "claude-code" si el proceso corre dentro de Claude Code (alguna variable de AGENT_ENV_VARS en el
  entorno o en el entorno inicial de un proceso antecesor, o el propio Claude Code entre los antecesores),
  "terminal" en otro caso. `human_terminal_problems` añade, para la firma humana, la falta de terminal de control
  (Linux). Dentro de Claude Code, `sesion_claude_code` enlaza el evento con la transcripción de la sesión (donde
  constan el agente y el modelo, que la CLI no puede conocer);
- `git`: {"commit", "cambios_sin_confirmar"} del proyecto (cambios fuera de research/, que es donde nutria escribe
  sus datos); `entorno`: versiones de Python y de las bibliotecas de cálculo. Se calculan una vez por proceso;
- opcionales: `parametros` y `huellas` ({"entrada": {nombre: sha256}, "salida": {...}}), ver `log_event`;
- `prev` y `hash`: la cadena.

Integridad. `hash` es el SHA-256 del JSON canónico del evento sin el campo `hash`; `prev` es el `hash` del evento
encadenado anterior. El primer evento encadenado de un fichero ancla todo lo anterior: su `prev` es el SHA-256 de
los bytes previos (el de la cadena vacía si el fichero no existía), así que los eventos de versiones anteriores,
sin cadena, quedan cubiertos. Las escrituras se serializan con un bloqueo de fichero (POSIX). `verify_chain`
(orden `nutria procedencia verificar`) detecta líneas corruptas, eventos alterados, la cadena rota, eventos sin
cadena añadidos después y, si el fichero está en git, la reescritura o el borrado de eventos ya confirmados.

Límites: una cadena de hashes no autentica. Quien pueda escribir el fichero puede recalcular la cola entera, añadir
eventos al final o truncar los que aún no estén confirmados en git; lo que detecta es la corrupción, las
alteraciones parciales y los borrados o inserciones intermedios. La cadena supone un único historial lineal: dos
ramas de git que añadan eventos por separado la rompen al fusionarse. Los hooks de Claude Code impiden a los agentes
escribir el fichero por fuera de nutria.
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
    return explicit or os.environ.get("NUTRIA_AGENT") or "cli"


def declared_agent(explicit: str | None = None) -> str:
    """Rol declarado de quien ejecuta la orden (--agente o, si no se indicó, NUTRIA_AGENT); "" si no declara ninguno.

    Es una declaración, no una identidad comprobada: el hook de Bash la contrasta con el tipo de subagente de Claude
    Code cuando lo hay (scripts/hooks/guard_bash.sh)."""
    return str(explicit or os.environ.get("NUTRIA_AGENT") or "").strip()


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
        return "claude-code" in script or ("anthropic" in script and script.rsplit("/", 1)[-1] in ("claude", "cli.js"))
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


def ancestor_markers(proc_root: Path | str | None = None, pid: int | None = None, max_depth: int = 64) -> list[str]:
    """Señales de Claude Code en el proceso y sus antecesores (Linux, vía /proc).

    Recorre la cadena de padres desde el propio proceso y devuelve, para cada uno, las variables de
    AGENT_ENV_VARS de su entorno inicial (el de /proc/<pid>/environ, que no cambia aunque el proceso las borre)
    y si es el propio Claude Code. Así se reconoce una orden lanzada por un agente aunque haya vaciado su
    entorno (`env -i`, `env -u`, `exec -c`). Sin /proc (macOS, Windows) devuelve [].
    """
    found: list[str] = []
    for proc in _proc_chain(proc_root, pid, max_depth) or ():
        for var in AGENT_ENV_VARS:
            if (b"\0" + var.encode() + b"=") in (b"\0" + proc["environ"]):
                found.append(f"{var} (proceso {proc['pid']})")
        if _is_claude_code_process(proc["comm"], proc["cmdline"]):
            found.append(f"Claude Code (proceso {proc['pid']})")
    return found


def interactive_terminal(proc_root: Path | str | None = None, pid: int | None = None) -> bool | None:
    """¿Tiene terminal de control el proceso o alguno de sus antecesores? None si no se puede saber (sin /proc).

    Una persona firma en su terminal; las órdenes Bash de Claude Code se ejecutan en una sesión propia sin
    terminal, y un proceso desvinculado de Claude Code (adoptado por init, p. ej. con un doble fork) tampoco
    tiene ninguna en su cadena.
    """
    chain = _proc_chain(proc_root, pid)
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
def git_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=3
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def git_state(root: Path | None) -> dict:
    """Commit del proyecto y si hay cambios sin confirmar fuera de research/ (una vez por proceso y raíz)."""
    if root is None:
        return {"commit": None, "cambios_sin_confirmar": None}
    key = ("git", str(root))
    if key not in _PROCESS_CACHE:
        commit = git_commit(root)
        dirty = None
        if commit is not None:
            try:  # GIT_OPTIONAL_LOCKS=0: `git status` no toma el bloqueo del índice (no molesta a otro git)
                out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no", "--", ".",
                                      ":(exclude)research"], cwd=root, capture_output=True, text=True, timeout=5,
                                     env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
                dirty = bool(out.stdout.strip()) if out.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                dirty = None
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


def _committed_version(path: Path, root: Path) -> tuple[str | None, bytes | None]:
    """Versión del fichero confirmada en git (HEAD), o (None, None) si no hay git o el fichero no está confirmado."""
    try:
        rel = Path(os.path.relpath(path, root)).as_posix()
    except ValueError:  # pragma: no cover - otra unidad en Windows
        return None, None
    if rel.startswith(".."):
        return None, None
    try:
        out = subprocess.run(["git", "show", f"HEAD:./{rel}"], cwd=root, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None, None
    if out.returncode != 0:
        return None, None
    return git_commit(root), out.stdout


def verify_chain(cfg, use_git: bool = True) -> dict:
    """Comprueba la integridad del registro de procedencia.

    Devuelve un dict con `integra` (bool), `problemas` (textos) y el detalle: eventos, encadenados,
    `anteriores_a_la_cadena` (eventos de versiones previas, anclados por el primer evento encadenado),
    `lineas_corruptas`, `corruptas_ancladas` (corruptas anteriores a la cadena y cubiertas por su ancla: aviso),
    `alterados` (el `hash` no corresponde al contenido), `cadena_rota` (`prev` no corresponde al evento
    anterior), `sin_cadena_tras_inicio` (eventos sin cadena añadidos después de empezar la cadena) y `git`.
    """
    path: Path = cfg.provenance_path
    res: dict[str, Any] = {
        "fichero": str(path), "existe": path.is_file(), "eventos": 0, "encadenados": 0,
        "anteriores_a_la_cadena": 0, "lineas_corruptas": [], "corruptas_ancladas": [], "alterados": [],
        "cadena_rota": [], "sin_cadena_tras_inicio": [], "git": None, "avisos": [], "problemas": [],
        "integra": True,
    }
    if not path.is_file():
        res["avisos"].append("No hay registro de procedencia todavía")
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
        commit, committed = _committed_version(path, root)
        if committed is None:
            res["git"] = {"estado": "sin_referencia"}
        else:
            current = data.replace(b"\r\n", b"\n")
            reference = committed.replace(b"\r\n", b"\n")
            if current.startswith(reference):
                res["git"] = {"estado": "amplia_lo_confirmado", "commit": commit,
                              "eventos_nuevos": current[len(reference):].count(b"\n")}
            else:
                res["git"] = {"estado": "reescrito", "commit": commit}
                p.append(f"el fichero no amplía la versión confirmada en git (commit {str(commit)[:12]}): se han "
                         "modificado o borrado eventos ya confirmados")
    res["integra"] = not p
    return res
