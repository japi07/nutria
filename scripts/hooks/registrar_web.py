#!/usr/bin/env python3
"""Hook PostToolUse (WebFetch|WebSearch) de Claude Code: deja constancia de cada consulta web de los agentes.

Añade a research/procedencia.jsonl, dentro de la cadena de hashes, un evento `web_fetch` o `web_search` con la fecha,
la sesión, el id de la llamada y el SHA-256 de la respuesta; nunca su contenido.

Las consultas web pueden revelar una idea confidencial (regla 11) y el fichero se versiona, así que por defecto
(modo "hash") solo se guardan el dominio y el SHA-256 de la URL (WebFetch) o el SHA-256 de la consulta (WebSearch).
Con NUTRIA_PROCEDENCIA_WEB=claro se guardan la URL y la consulta en claro; cualquier otro valor se trata como "hash"
(falla en cerrado). La URL se redacta siempre antes de guardarla o de calcular su huella: sin credenciales
(usuario:clave@), sin parámetros sensibles (api_key, key, token, email, mailto...), sin fragmento, sin direcciones de
correo y sin los secretos configurados (`nutria.http.redact`); la consulta en claro, también (`redact_text`, que
quita además un correo suelto: "contacto ana@example.org"). La pregunta que el agente hace
sobre la página (prompt de WebFetch) se guarda como huella. Límites: la huella de una URL o de una consulta corta se
puede confirmar adivinándola (no es un cifrado), y el hook solo decide qué queda registrado: la consulta llega igual
al servicio externo.

Nunca bloquea: si algo falla, avisa con `systemMessage` y deja seguir la sesión. La raíz del proyecto es
CLAUDE_PROJECT_DIR (la define Claude Code) o, en su defecto, la del repositorio que contiene este hook.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

HOOK_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOOK_ROOT / "src"))

# Campos de la respuesta que cambian en cada llamada y no forman parte del contenido.
VOLATILE = {"durationMs", "durationSeconds", "duration_ms", "duration", "elapsed"}
# Parámetros de URL que nunca se guardan (además de los de nutria.http.SENSITIVE_PARAMS).
SENSITIVE_PARAMS = {"api_key", "apikey", "key", "token", "access_token", "auth", "password", "passwd", "secret",
                    "signature", "sig", "email", "mailto", "session", "sessionid", "x-api-key"}
MASK = "***"


def web_mode(value: str | None) -> str:
    """'hash' (por defecto: solo dominio y huellas) o 'claro' (URL y consulta en claro, solo si se pide)."""
    return "claro" if (value or "").strip().lower() == "claro" else "hash"


# Dirección de correo suelta (también codificada en una URL: ana%40example.org).
_EMAIL_RE = re.compile(r"[\w.+-]+(@|%40)[\w-]+(\.[\w-]+)+", re.IGNORECASE)


def redact_text(text: str, cfg=None) -> str:
    """Texto sin los secretos que reconoce `nutria.http.redact` (valores configurados, pares clave=valor sensibles,
    tokens Bearer y direcciones mailto:; si no se puede importar, sin ellos) ni direcciones de correo sueltas."""
    try:
        from nutria.http import redact
        text = redact(text, cfg)
    except Exception:  # noqa: BLE001 - sin requests u otra dependencia: se aplica solo la limpieza local
        pass
    return _EMAIL_RE.sub(f"{MASK}@{MASK}", text)


def redact_url(url: str, cfg=None) -> str:
    """URL sin credenciales, parámetros sensibles, correos ni secretos configurados (`redact_text`)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return MASK
    netloc = parts.netloc
    if "@" in netloc:                                     # https://usuario:clave@host/ (antes de buscar correos)
        netloc = f"{MASK}@{netloc.rsplit('@', 1)[1]}"
    try:
        parts = urlsplit(redact_text(urlunsplit((parts.scheme, netloc, parts.path, parts.query, "")), cfg))
    except ValueError:
        return MASK
    kept = []
    for piece in re.split(r"[&;]", parts.query) if parts.query else []:
        name = piece.split("=", 1)[0].strip().lower()
        kept.append(f"{piece.split('=', 1)[0]}={MASK}" if name in SENSITIVE_PARAMS else piece)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), ""))   # sin fragmento (#token=...)


def _response_digest(response) -> tuple[str, int | None]:
    """SHA-256 de la respuesta (sin los campos de duración) y número de resultados si es una búsqueda."""
    from nutria.provenance import sha256_json, sha256_text
    if isinstance(response, str):
        return sha256_text(response), None
    if isinstance(response, dict):
        content = {k: v for k, v in response.items() if k not in VOLATILE}
        results = content.get("results")
        return sha256_json(content), (len(results) if isinstance(results, list) else None)
    return sha256_json(response), None


def build_event(payload: dict, mode: str, cfg=None) -> tuple[str, dict, dict]:
    from nutria.provenance import sha256_text
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    digest, n_results = _response_digest(payload.get("tool_response"))
    outputs = {"sha256_respuesta": digest}
    if n_results is not None:
        outputs["resultados"] = n_results
    inputs = {"herramienta": tool, "sesion": payload.get("session_id"), "id_llamada": payload.get("tool_use_id"),
              "modo": mode}
    if tool == "WebFetch":
        url = redact_url(str(tool_input.get("url") or ""), cfg)
        if mode == "hash":
            try:
                host = urlsplit(url).hostname
            except ValueError:
                host = None
            inputs.update(dominio=host, sha256_url=sha256_text(url))
        else:
            inputs["url"] = url
        if tool_input.get("prompt"):
            inputs["sha256_prompt"] = sha256_text(str(tool_input["prompt"]))
        return "web_fetch", inputs, outputs
    query = str(tool_input.get("query") or "")
    if mode == "hash":
        inputs["sha256_consulta"] = sha256_text(query)
    else:   # en claro, sin los secretos que pudiera llevar (p. ej. un correo de contacto o una clave pegada)
        inputs["consulta"] = redact_text(query, cfg)
    for key, name in (("allowed_domains", "dominios_permitidos"), ("blocked_domains", "dominios_excluidos")):
        if tool_input.get(key):
            inputs[name] = tool_input[key]
    return "web_search", inputs, outputs


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        if payload.get("tool_name") not in ("WebFetch", "WebSearch"):
            return
        from nutria.config import load_config
        from nutria.provenance import log_event
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or HOOK_ROOT)
        cfg = load_config(root)
        mode = web_mode(os.environ.get("NUTRIA_PROCEDENCIA_WEB"))
        action, inputs, outputs = build_event(payload, mode, cfg)
        agent = payload.get("agent_type") or payload.get("subagent_type")
        log_event(cfg, action, inputs=inputs, outputs=outputs,
                  actor=f"claude-code:{agent}" if agent else "claude-code")
    except Exception as exc:  # noqa: BLE001 - nunca rompe la sesión, pero avisa
        print(json.dumps({"systemMessage": "NUTRIA: la consulta web no quedó registrada en la procedencia "
                                           f"({exc.__class__.__name__}: {exc})"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
