"""Registro de procedencia append-only (JSONL).

Cada consulta a una fuente externa, cálculo, llamada a un modelo o decisión queda
registrada con marca de tiempo UTC, actor, entradas (sin secretos) y huella SHA-256 de
las salidas. El fichero `research/procedencia.jsonl` se versiona con git.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

try:  # bloqueo de fichero en POSIX; en Windows se escribe sin bloqueo
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

_SENSITIVE_KEYS = {
    "api_key", "apikey", "key", "token", "email", "mailto", "x-api-key",
    "authorization", "password", "secret",
}


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


def current_actor(explicit: str | None = None) -> str:
    return explicit or os.environ.get("NUTRIA_AGENT") or "cli"


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


def git_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=3
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


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


def log_event(cfg, action: str, inputs: dict | None = None, outputs: dict | None = None,
              actor: str | None = None) -> dict:
    """Añade un evento al registro de procedencia y lo devuelve."""
    event = {
        "ts": utc_now(),
        "actor": current_actor(actor or getattr(cfg, "actor", None)),
        "action": action,
        "inputs": scrub(inputs or {}),
        "outputs": scrub(outputs or {}),
        "nutria": __version__,
    }
    path: Path = cfg.provenance_path
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
    with open(path, "a", encoding="utf-8") as fh:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(line + "\n")
            fh.flush()
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    return event


def read_events(cfg, limit: int | None = None, action: str | None = None) -> list[dict]:
    path: Path = cfg.provenance_path
    if not path.is_file():
        return []
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if action and ev.get("action") != action:
                continue
            events.append(ev)
    return events[-limit:] if limit else events
