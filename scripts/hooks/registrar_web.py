#!/usr/bin/env python3
"""Hook PostToolUse (WebFetch|WebSearch) de Claude Code: deja constancia de cada consulta web de los agentes.

Añade a research/procedencia.jsonl, dentro de la cadena de hashes, un evento `web_fetch` o `web_search` con la URL
(WebFetch) o la consulta (WebSearch), la fecha, la sesión y el SHA-256 de la respuesta; nunca su contenido.

Las consultas web también pueden revelar una idea confidencial: con NUTRIA_PROCEDENCIA_WEB=hash solo se guardan el
dominio y el SHA-256 de la URL completa (o el SHA-256 de la consulta). Cualquier otro valor no vacío distinto de
"claro" se trata como "hash" (falla en cerrado). La pregunta que el agente hace sobre la página (prompt de WebFetch)
se guarda siempre como huella.

Nunca bloquea: si algo falla, avisa con `systemMessage` y deja seguir la sesión. La raíz del proyecto es
CLAUDE_PROJECT_DIR (la define Claude Code) o, en su defecto, la del repositorio que contiene este hook.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

HOOK_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOOK_ROOT / "src"))

# Campos de la respuesta que cambian en cada llamada y no forman parte del contenido.
VOLATILE = {"durationMs", "durationSeconds", "duration_ms", "duration", "elapsed"}


def web_mode(value: str | None) -> str:
    """'claro' (URL y consulta en claro, por defecto) o 'hash' (solo dominio y huellas)."""
    value = (value or "").strip().lower()
    return "claro" if value in ("", "claro") else "hash"


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


def build_event(payload: dict, mode: str) -> tuple[str, dict, dict]:
    from nutria.provenance import sha256_text
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    digest, n_results = _response_digest(payload.get("tool_response"))
    outputs = {"sha256_respuesta": digest}
    if n_results is not None:
        outputs["resultados"] = n_results
    inputs = {"herramienta": tool, "sesion": payload.get("session_id"), "id_llamada": payload.get("tool_use_id")}
    if tool == "WebFetch":
        url = str(tool_input.get("url") or "")
        if mode == "hash":
            inputs.update(dominio=urlsplit(url).hostname, sha256_url=sha256_text(url))
        else:
            inputs["url"] = url
        if tool_input.get("prompt"):
            inputs["sha256_prompt"] = sha256_text(str(tool_input["prompt"]))
        return "web_fetch", inputs, outputs
    query = str(tool_input.get("query") or "")
    if mode == "hash":
        inputs["sha256_consulta"] = sha256_text(query)
    else:
        inputs["consulta"] = query
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
        action, inputs, outputs = build_event(payload, mode)
        agent = payload.get("agent_type") or payload.get("subagent_type")
        log_event(cfg, action, inputs=inputs, outputs=outputs,
                  actor=f"claude-code:{agent}" if agent else "claude-code")
    except Exception as exc:  # noqa: BLE001 - nunca rompe la sesión, pero avisa
        print(json.dumps({"systemMessage": "NUTRIA: la consulta web no quedó registrada en la procedencia "
                                           f"({exc.__class__.__name__}: {exc})"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
