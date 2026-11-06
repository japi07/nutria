"""Llamadas a modelos de distintos proveedores para segunda opinión y evaluación de dominio.

Principios:
- Un modelo de lenguaje NO es una fuente: sus respuestas se registran como opinión y deben
  contrastarse con referencias verificadas.
- Cada llamada queda en la procedencia (proveedor, modelo, huella del prompt y de la respuesta).
- Los datos marcados como confidenciales solo se envían a modelos locales o con no retención.
- En modo offline (`--offline`) no se hace ninguna petición, ni siquiera a modelos locales.
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


OFFLINE_REASON = "modo offline: no se hace ninguna petición a modelos"


def _call(cfg, spec: ModelSpec, prompt: str, system: str | None, max_tokens: int,
          temperature: float | None) -> dict:
    if getattr(cfg, "offline", False):  # defensa en profundidad: `ask` ya lo comprueba
        raise ValueError(OFFLINE_REASON)
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


SYSTEM_BY_ROLE = {
    "critico": (
        "Actúas como revisor crítico independiente (red team) de un documento de I+D en nutrición animal. "
        "Tu tarea es intentar refutarlo, no resumirlo ni mejorarlo. Señala: afirmaciones sin respaldo o con "
        "respaldo insuficiente; extrapolaciones entre especies o desde estudios in vitro o in silico; confusión "
        "entre asociación y causalidad; errores de diseño, de unidad experimental o de estadística; destino "
        "digestivo no considerado (degradación ruminal, biohidrogenación); riesgos de seguridad y regulatorios "
        "(un pienso no puede alegar que previene, trata o cura enfermedades); explicaciones alternativas. "
        "No inventes referencias ni datos: si mencionas un trabajo, indica que hay que verificarlo. Para cada "
        "objeción da: la afirmación concreta, el problema, la gravedad (alta, media o baja) y qué dato o análisis "
        "la resolvería. Responde en español."
    ),
}


def ask(cfg, prompt: str, role: str | None = None, model_id: str | None = None, system: str | None = None,
        sensitivity: str = "confidencial", max_tokens: int = 2000, temperature: float | None = None) -> list[dict]:
    """Consulta a todos los modelos utilizables con ese rol (o a uno concreto).

    Falla cerrado: por defecto la información se trata como confidencial y solo se envía a modelos
    locales o con acuerdo de no retención; para enviarla a otros hay que pedir sensitivity="normal".
    """
    if system is None and role in SYSTEM_BY_ROLE:
        system = SYSTEM_BY_ROLE[role]
    specs = load_registry(cfg)
    if model_id:
        specs = [s for s in specs if s.id == model_id]
    elif role:
        specs = [s for s in specs if role in s.roles]
    if sensitivity not in ("normal", "confidencial"):
        raise ValueError(f"Sensibilidad desconocida: {sensitivity!r} (usa 'normal' o 'confidencial')")
    offline = bool(getattr(cfg, "offline", False))
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
        if offline:
            results.append({"modelo": spec.id, "omitido": OFFLINE_REASON})
            continue
        try:
            out = _call(cfg, spec, prompt, system, max_tokens, temperature)
        except (requests.RequestException, KeyError, ValueError) as exc:
            from ..http import redact
            results.append({"modelo": spec.id, "error": redact(str(exc), cfg)[:300]})
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
    # «nº», «n.º» y «N°» (número) no son la negación «no»: se escriben «num» antes de quitar los diacríticos, que
    # convertirían «º» en «o»; el signo menos tipográfico pasa a guion para no perder el signo de una cifra.
    text = re.sub(r"(?i)\bn\.?\s*[º°]", "num ", str(text)).replace("−", "-")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", text).strip()


# Corrección de las respuestas (D5-14). Una subcadena no basta: «No es un aditivo zootécnico» contiene «zootécnico».
# - Una mención está NEGADA si va dentro del alcance de una negación (no, ni, nunca, jamás, tampoco, en lugar de,
#   en vez de; not, never, neither, nor, instead of, rather than), que llega hasta el siguiente signo de puntuación
#   o hasta una conjunción adversativa (sino, pero, aunque, mientras; but, whereas, although): en «No es el 2020/354
#   sino el 2019/962», el 2020/354 está negado y el 2019/962 no. «No» seguido de una cifra («No 1831/2003») es la
#   abreviatura de número, no una negación, y «No,» al principio es la respuesta a la pregunta, no niega lo que sigue.
# - «contiene»: la respuesta esperada debe aparecer sin negar y no puede aparecer también negada (contradicción); las
#   palabras de «excluye» (las alternativas incorrectas) no pueden aparecer sin negar.
# - «sino»: la primera palabra es SÍ o NO, y no puede seguirle otra respuesta suelta contraria («Sí... bueno, no»).
# - Las menciones empiezan en el inicio de una palabra; una esperada que acaba en cifra no puede seguir con otra cifra
#   («2019/9620» no es «2019/962»), y una que acaba en letra admite terminaciones («zootecnic» → «zootécnicos»).
# Es una heurística léxica: no entiende la frase. Falla en cerrado ante las negaciones que reconoce (una respuesta
# correcta con una doble negación puede contar como incorrecta), no ante las que no reconoce.
_NEGACIONES = frozenset({"no", "ni", "nunca", "jamas", "tampoco", "not", "never", "neither", "nor"})
_NEGACIONES_DE_FRASE = (("en", "lugar", "de"), ("en", "vez", "de"), ("instead", "of"), ("rather", "than"))
_ADVERSATIVAS = frozenset({"sino", "pero", "aunque", "mientras", "but", "whereas", "although"})
_TOKEN_RE = re.compile(r"[a-z0-9]+|[.,;:!?()\[\]¿¡\"]")
_PUNTUACION = frozenset(".,;:!?()[]¿¡\"")


def _tramos_negados(a: str) -> list[tuple[int, int]]:
    """Tramos [inicio, fin) del texto normalizado que quedan dentro del alcance de una negación."""
    tokens = [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(a)]
    tramos: list[tuple[int, int]] = []
    abierto: int | None = None
    for i, (tok, ini, fin) in enumerate(tokens):
        siguiente = tokens[i + 1][0] if i + 1 < len(tokens) else ""
        if tok in _PUNTUACION or tok in _ADVERSATIVAS:
            if abierto is not None:
                tramos.append((abierto, ini))
                abierto = None
            continue
        frase = next((f for f in _NEGACIONES_DE_FRASE
                      if tuple(t for t, _, _ in tokens[i:i + len(f)]) == f), None)
        if frase is not None or tok in _NEGACIONES:
            if tok == "no" and (siguiente[:1].isdigit() or (i == 0 and siguiente in (",", ".", ":", ";", ""))):
                continue
            if abierto is None:
                abierto = tokens[i + len(frase) - 1][2] if frase else fin
    if abierto is not None:
        tramos.append((abierto, len(a)))
    return tramos


def _menciones(a: str, esperado: str) -> list[int]:
    x = _norm(esperado)
    if not x:
        return []
    patron = r"(?<![a-z0-9])" + re.escape(x) + (r"(?![0-9])" if x[-1].isdigit() else "")
    return [m.start() for m in re.finditer(patron, a)]


def _afirmado(a: str, esperado: str, tramos: list[tuple[int, int]]) -> bool:
    """La respuesta menciona `esperado` sin negarlo y no lo niega en ninguna otra parte."""
    menciones = _menciones(a, esperado)
    negadas = [p for p in menciones if any(i <= p < f for i, f in tramos)]
    return bool(menciones) and not negadas


def _menciona_sin_negar(a: str, termino: str, tramos: list[tuple[int, int]]) -> bool:
    return any(not any(i <= p < f for i, f in tramos) for p in _menciones(a, termino))


def grade(item: dict, answer: str) -> bool:
    kind = item.get("tipo", "contiene")
    expected = item.get("respuesta_esperada")
    a = _norm(answer)
    tramos = _tramos_negados(a)
    excluded = item.get("excluye") or []
    if kind == "exacta":
        return a == _norm(expected)
    if kind in ("contiene", "contiene_todos"):
        expected_list = [expected] if kind == "contiene" else list(expected)
        return (all(_afirmado(a, x, tramos) for x in expected_list)
                and not any(_menciona_sin_negar(a, x, tramos) for x in excluded))
    if kind == "sino":
        words = re.findall(r"[a-z0-9]+|[.,;:!?]", a)
        if not words or words[0] != _norm(expected):
            return False
        opuesta = {"si": "no", "no": "si"}.get(words[0])
        # otra respuesta suelta, contraria a la primera, más adelante: «Sí... bueno, no», «No. Bueno, sí.»
        return not any(w == opuesta and (k + 1 == len(words) or words[k + 1] in ".,;:!?")
                       for k, w in enumerate(words[1:], start=1))
    if kind == "numerica":
        match = re.search(r"-?\d+(?:[.,]\d+)?", a)
        if not match or any(i <= match.start() < f for i, f in tramos):
            return False
        value = float(match.group(0).replace(",", "."))
        return abs(value - float(expected)) <= float(item.get("tolerancia", 0))
    raise ValueError(f"Tipo de corrección desconocido: {kind}")


PENDIENTE_VALIDACION = "pendiente_validacion"


def evaluate(cfg, eval_path: Path, role: str | None = None, model_id: str | None = None) -> dict:
    items = [json.loads(line) for line in Path(eval_path).read_text("utf-8").splitlines() if line.strip()]
    if getattr(cfg, "offline", False):
        return {"resultados": {}, "detalle": None, "n_items": len(items), "aviso": OFFLINE_REASON}
    system = ("Responde de forma breve y directa. Si la pregunta pide una cifra, empieza por la cifra. "
              "Si pide SÍ o NO, empieza por SÍ o NO.")
    scores: dict[str, dict] = {}
    validated: dict[str, dict] = {}        # solo preguntas validadas (sin estado "pendiente_validacion")
    by_area: dict[str, dict[str, dict]] = {}
    errors: dict[str, int] = {}
    rows = []
    for item in items:
        pending = item.get("estado") == PENDIENTE_VALIDACION
        area = item.get("area") or "sin_area"
        # El banco de preguntas no contiene información confidencial.
        for res in ask(cfg, item["pregunta"], role=role, model_id=model_id, system=system, max_tokens=400,
                       sensitivity="normal"):
            if "error" in res:   # un fallo no se descarta en silencio: se cuenta por modelo (la orden sale con 1)
                errors[res["modelo"]] = errors.get(res["modelo"], 0) + 1
            if "texto" not in res:
                continue
            correct = grade(item, res["texto"])
            buckets = [scores.setdefault(res["modelo"], {"aciertos": 0, "total": 0}),
                       by_area.setdefault(res["modelo"], {}).setdefault(area, {"aciertos": 0, "total": 0})]
            if not pending:
                buckets.append(validated.setdefault(res["modelo"], {"aciertos": 0, "total": 0}))
            for s in buckets:
                s["total"] += 1
                s["aciertos"] += int(correct)
            rows.append({"item": item.get("id"), "area": area, "pendiente_validacion": pending,
                         "modelo": res["modelo"], "correcto": correct, "respuesta": res["texto"][:500]})
    out_dir = cfg.research_dir / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"evaluacion_{utc_now().replace(':', '').replace('+', '_')}.jsonl"
    out_file.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", "utf-8")
    for s in [*scores.values(), *validated.values(), *(a for m in by_area.values() for a in m.values())]:
        s["exactitud"] = s["aciertos"] / s["total"] if s["total"] else None
    # «resultados» mezcla todas las preguntas; para elegir modelo cuenta «solo_validadas»: las preguntas con estado
    # "pendiente_validacion" (respuesta esperada aún sin revisar por una persona experta) se corrigen, pero aparte.
    out = {"resultados": scores, "solo_validadas": validated, "por_area": by_area, "detalle": str(out_file),
           "n_items": len(items),
           "n_pendientes_validacion": sum(1 for i in items if i.get("estado") == PENDIENTE_VALIDACION)}
    if errors:
        out["errores"] = errors
    return out
