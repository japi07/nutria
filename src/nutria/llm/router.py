"""Llamadas a modelos de distintos proveedores para segunda opinión y evaluación de dominio.

Principios:
- Un modelo de lenguaje NO es una fuente: sus respuestas se registran como opinión y deben
  contrastarse con referencias verificadas.
- Cada llamada queda en la procedencia (proveedor, modelo, huella del prompt y de la respuesta).
- Los datos marcados como confidenciales solo se envían a modelos locales o con no retención.
"""

from __future__ import annotations

import json
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import requests

from ..provenance import log_event, sha256_text, utc_now

SAFE_POLICIES = {"local", "zero_retention"}


@dataclass
class ModelSpec:
    id: str
    provider: str
    model: str
    api_key_env: str = ""
    base_url: str = ""
    roles: list[str] = field(default_factory=list)
    data_policy: str = "standard"
    verified: str = ""
    active: bool = False

    def api_key(self, cfg) -> str | None:
        return cfg.secret(self.api_key_env) if self.api_key_env else None

    def usable(self, cfg) -> tuple[bool, str]:
        if not self.active:
            return False, "inactivo"
        if "RELLENAR" in self.model:
            return False, "falta el identificador exacto del modelo"
        if self.api_key_env and not self.api_key(cfg):
            return False, f"falta la variable de entorno {self.api_key_env}"
        return True, "ok"


def load_registry(cfg) -> list[ModelSpec]:
    custom = cfg.root / "config" / "modelos.toml"
    text = custom.read_text("utf-8") if custom.is_file() else \
        resources.files("nutria.data").joinpath("modelos.toml").read_text("utf-8")
    data = tomllib.loads(text)
    return [ModelSpec(id=m["id"], provider=m["proveedor"], model=m["modelo"], api_key_env=m.get("api_key_env", ""),
                      base_url=m.get("base_url", ""), roles=list(m.get("roles", [])),
                      data_policy=m.get("politica_datos", "standard"), verified=m.get("verificado", ""),
                      active=bool(m.get("activo", False))) for m in data.get("modelo", [])]


def _call(cfg, spec: ModelSpec, prompt: str, system: str | None, max_tokens: int,
          temperature: float | None) -> dict:
    key = spec.api_key(cfg)
    timeout = max(cfg.timeout, 120)
    if spec.provider == "anthropic":
        body = {"model": spec.model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        resp = requests.post((spec.base_url or "https://api.anthropic.com") + "/v1/messages", json=body,
                             headers={"x-api-key": key or "", "anthropic-version": "2023-06-01",
                                      "content-type": "application/json"}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return {"text": text, "usage": data.get("usage"), "model_reported": data.get("model")}
    if spec.provider == "openai_compatible":
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body = {"model": spec.model, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        resp = requests.post(spec.base_url.rstrip("/") + "/chat/completions", json=body, headers=headers,
                             timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return {"text": data["choices"][0]["message"].get("content") or "", "usage": data.get("usage"),
                "model_reported": data.get("model")}
    if spec.provider == "gemini":
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if temperature is not None:
            body["generationConfig"]["temperature"] = temperature
        base = spec.base_url or "https://generativelanguage.googleapis.com/v1beta"
        resp = requests.post(f"{base}/models/{spec.model}:generateContent", json=body,
                             headers={"x-goog-api-key": key or ""}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts", [])
        return {"text": "".join(p.get("text", "") for p in parts), "usage": data.get("usageMetadata"),
                "model_reported": data.get("modelVersion")}
    raise ValueError(f"Proveedor no soportado: {spec.provider}")


def ask(cfg, prompt: str, role: str | None = None, model_id: str | None = None, system: str | None = None,
        sensitivity: str = "normal", max_tokens: int = 2000, temperature: float | None = None) -> list[dict]:
    """Consulta a todos los modelos utilizables con ese rol (o a uno concreto)."""
    specs = load_registry(cfg)
    if model_id:
        specs = [s for s in specs if s.id == model_id]
    elif role:
        specs = [s for s in specs if role in s.roles]
    results = []
    for spec in specs:
        ok, why = spec.usable(cfg)
        if not ok:
            results.append({"modelo": spec.id, "omitido": why})
            continue
        if sensitivity == "confidencial" and spec.data_policy not in SAFE_POLICIES:
            results.append({"modelo": spec.id, "omitido": f"política de datos '{spec.data_policy}' no apta para "
                                                         "información confidencial"})
            continue
        try:
            out = _call(cfg, spec, prompt, system, max_tokens, temperature)
        except (requests.RequestException, KeyError, ValueError) as exc:
            results.append({"modelo": spec.id, "error": str(exc)[:300]})
            continue
        log_event(cfg, "llm", inputs={"modelo": spec.id, "proveedor": spec.provider, "id_modelo": spec.model,
                                      "prompt_sha256": sha256_text(prompt), "sensibilidad": sensitivity,
                                      "rol": role},
                  outputs={"respuesta_sha256": sha256_text(out["text"]), "uso": out.get("usage"),
                           "modelo_informado": out.get("model_reported")})
        results.append({"modelo": spec.id, "id_modelo": spec.model, "texto": out["text"],
                        "aviso": "Opinión de un modelo: no es evidencia. Contrasta con fuentes verificadas."})
    return results


# --------------------------------------------------------------------------- banco de pruebas de dominio
def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", text).strip()


def grade(item: dict, answer: str) -> bool:
    kind = item.get("tipo", "contiene")
    expected = item.get("respuesta_esperada")
    a = _norm(answer)
    if kind == "exacta":
        return a == _norm(expected)
    if kind == "contiene":
        return _norm(expected) in a
    if kind == "contiene_todos":
        return all(_norm(x) in a for x in expected)
    if kind == "sino":
        first = a.split(" ")[0].strip(".,;:") if a else ""
        return first == _norm(expected)
    if kind == "numerica":
        match = re.search(r"-?\d+(?:[.,]\d+)?", answer.replace("−", "-"))
        if not match:
            return False
        value = float(match.group(0).replace(",", "."))
        return abs(value - float(expected)) <= float(item.get("tolerancia", 0))
    raise ValueError(f"Tipo de corrección desconocido: {kind}")


def evaluate(cfg, eval_path: Path, role: str | None = None, model_id: str | None = None) -> dict:
    items = [json.loads(line) for line in Path(eval_path).read_text("utf-8").splitlines() if line.strip()]
    system = ("Responde de forma breve y directa. Si la pregunta pide una cifra, empieza por la cifra. "
              "Si pide SÍ o NO, empieza por SÍ o NO.")
    scores: dict[str, dict] = {}
    rows = []
    for item in items:
        for res in ask(cfg, item["pregunta"], role=role, model_id=model_id, system=system, max_tokens=400):
            if "texto" not in res:
                continue
            correct = grade(item, res["texto"])
            s = scores.setdefault(res["modelo"], {"aciertos": 0, "total": 0})
            s["total"] += 1
            s["aciertos"] += int(correct)
            rows.append({"item": item.get("id"), "modelo": res["modelo"], "correcto": correct,
                         "respuesta": res["texto"][:500]})
    out_dir = cfg.research_dir / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"evaluacion_{utc_now().replace(':', '').replace('+', '_')}.jsonl"
    out_file.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", "utf-8")
    for s in scores.values():
        s["exactitud"] = s["aciertos"] / s["total"] if s["total"] else None
    return {"resultados": scores, "detalle": str(out_file), "n_items": len(items)}
