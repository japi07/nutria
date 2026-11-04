"""Firma HMAC de los sellos de verificación.

La clave vive FUERA del repositorio (~/.config/nutria/clave_sello, o NUTRIA_CLAVE_SELLO), así que un
sello escrito a mano o copiado de otro equipo no valida. No es una protección criptográfica frente a
quien tenga acceso a la clave (los hooks impiden a los agentes leerla o escribir sellos con las
herramientas de edición), sino una barrera contra sellos falsificados o desactualizados.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path


def key_path() -> Path:
    custom = os.environ.get("NUTRIA_CLAVE_SELLO")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".config" / "nutria" / "clave_sello"


def load_key(create: bool = True) -> bytes | None:
    path = key_path()
    if path.is_file():
        return path.read_text("utf-8").strip().encode("utf-8")
    if not create:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32)
    path.write_text(value, "utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - sistemas sin permisos POSIX
        pass
    return value.encode("utf-8")


def _canonical(data: dict) -> bytes:
    payload = {k: v for k, v in data.items() if k != "firma"}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign(data: dict) -> str:
    key = load_key(create=True)
    return hmac.new(key, _canonical(data), hashlib.sha256).hexdigest()


def verify(data: dict) -> bool:
    key = load_key(create=False)
    if key is None or not isinstance(data.get("firma"), str):
        return False
    expected = hmac.new(key, _canonical(data), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, data["firma"])
