"""Cliente HTTP común: caché en disco, límites de frecuencia por host, reintentos y procedencia.

Todas las fuentes externas pasan por aquí. Así el sistema respeta las políticas de uso
de cada API, puede trabajar sin red con lo ya consultado (`offline`) y deja constancia
en `research/procedencia.jsonl` de cada consulta: respuestas de la red (`http`), copias
servidas desde la caché (`http_cache`, con la fecha de la copia y su antigüedad en días)
y peticiones fallidas (`http_error`).

Seguridad: ningún mensaje de `NetworkError`, evento de procedencia ni fichero de caché
contiene claves de API, tokens ni el correo de contacto (`redact`), y la clave de caché
no depende de ellos (`cache_key`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, quote_plus, unquote_plus, urlparse

import requests

from . import __version__
from .provenance import log_event, sha256_text

try:  # bloqueo entre procesos en POSIX; en Windows el límite se aplica solo dentro del proceso
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


# ============================================================================ redacción de secretos
# Variables de entorno cuyos valores nunca deben aparecer en mensajes, procedencia ni caché.
SECRET_ENV_VARS = (
    "NCBI_API_KEY", "OPENALEX_API_KEY", "LENS_API_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
    "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "S2_API_KEY",
)
# Parámetros de URL que se eliminan de cualquier URL al redactar (sin distinguir mayúsculas).
SENSITIVE_PARAMS = frozenset({"api_key", "apikey", "key", "token", "email", "mailto"})
# Parámetros que no forman parte de la clave de caché: secretos e identidad de quien consulta.
CACHE_EXCLUDED_PARAMS = SENSITIVE_PARAMS | {"tool", "caller_identity"}
MASK = "***"

_QUERY_RE = re.compile(r"\?([^\s\"'<>#]*)")          # cadena de consulta, en URL absoluta o relativa
_QUERY_SEP_RE = re.compile(r"(&amp;|&|;)")
_QUERY_TRAILING = ").,;:]}!"                          # puntuación del texto que sigue a una URL
_PAIR_RE = re.compile(r"(?i)(?<![\w-])(api_key|apikey|key|token|email|mailto)=[^&\s\"'<>#;]*")
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")
_MAILTO_RE = re.compile(r"(?i)\bmailto:[^\s\"'<>&;)]+")


def _param_name(part: str) -> str:
    return unquote_plus(part.split("=", 1)[0]).strip().lower()


def _filter_query(query: str, names: frozenset[str]) -> str:
    """Quita de una cadena de consulta (sin '?') los parámetros cuyo nombre está en `names`,
    conservando intacto el resto (orden, codificación y separadores)."""
    tokens = _QUERY_SEP_RE.split(query)  # [parte, separador, parte, separador, parte...]
    kept: list[str] = []
    for index in range(0, len(tokens), 2):
        part = tokens[index]
        if part and _param_name(part) in names:
            continue
        if kept:
            kept.append(tokens[index - 1])
        kept.append(part)
    return "".join(kept)


def strip_query_params(text: str, names: frozenset[str] = SENSITIVE_PARAMS) -> str:
    """Elimina los parámetros `names` de todas las cadenas de consulta (`?a=1&b=2`) del texto."""
    def repl(match: re.Match) -> str:
        original = match.group(1)
        core = original.rstrip(_QUERY_TRAILING)
        tail = original[len(core):]
        query = _filter_query(core, names)
        if query == core:
            return match.group(0)
        return (f"?{query}" if query else "") + tail
    return _QUERY_RE.sub(repl, text)


def _secret_values(cfg=None) -> list[str]:
    """Valores secretos configurados (y sus formas codificadas en URL), del más largo al más corto."""
    getter = getattr(cfg, "secret", None)
    if not callable(getter):
        def getter(name: str) -> str | None:
            return (os.environ.get(name) or "").strip() or None
    values = [getter(name) for name in SECRET_ENV_VARS]
    values.append(str(getattr(cfg, "email", "") or "").strip())
    forms: set[str] = set()
    for value in values:
        if value:
            forms.update({value, quote(value, safe=""), quote_plus(value)})
    return sorted(forms, key=len, reverse=True)


def redact(text, cfg=None) -> str:
    """Devuelve `text` sin secretos.

    1. Sustituye por *** cualquier valor secreto configurado (variables de `SECRET_ENV_VARS` y
       `cfg.email`), también en su forma codificada en URL.
    2. Elimina de cualquier URL, absoluta o relativa, los parámetros api_key, apikey, key, token,
       email y mailto.
    3. Enmascara los pares `clave=valor` sensibles que queden fuera de una URL, los tokens
       `Bearer` y las direcciones `mailto:`.
    Sin `cfg` se redactan igualmente los secretos del entorno (no el correo de contacto).
    """
    if text is None:
        return ""
    out = str(text)
    if not out:
        return out
    secrets = _secret_values(cfg)
    if secrets:
        out = re.sub("|".join(re.escape(s) for s in secrets), MASK, out, flags=re.IGNORECASE)
    out = strip_query_params(out, SENSITIVE_PARAMS)
    out = _PAIR_RE.sub(lambda m: f"{m.group(1)}={MASK}", out)
    out = _BEARER_RE.sub(lambda m: f"{m.group(1)} {MASK}", out)
    return _MAILTO_RE.sub(f"mailto:{MASK}", out)


# ============================================================================ excepciones
class NetworkError(RuntimeError):
    """Fallo de red, host bloqueado por la política de red o respuesta de error.

    El mensaje se redacta al crear la excepción (`redact`): nunca lleva claves, tokens ni el correo.
    """

    def __init__(self, message: object = "", *, cfg=None):
        super().__init__(redact(message, cfg))


class NotFoundError(NetworkError):
    """El recurso no existe (HTTP 404)."""


class OfflineError(NetworkError):
    """Modo offline activo y la petición no puede servirse desde la caché (o exige datos en línea)."""


# ============================================================================ caché
def cache_key(method: str, url: str, params: dict | None = None, json_body=None, data=None) -> str:
    """Huella SHA-256 de una petición. No incluye `CACHE_EXCLUDED_PARAMS` (claves, tokens, correo ni
    identificación de la herramienta), ni en `params` ni en la propia URL: la misma consulta comparte
    copia con o sin clave, y la clave no deja rastro en el nombre del fichero."""
    clean = {k: v for k, v in (params or {}).items()
             if v is not None and str(k).lower() not in CACHE_EXCLUDED_PARAMS}
    material = json.dumps({"m": method.upper(), "u": strip_query_params(url, CACHE_EXCLUDED_PARAMS),
                           "p": clean, "j": json_body, "d": data}, sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cache_params(params: dict) -> dict:
    return {k: v for k, v in params.items() if str(k).lower() not in CACHE_EXCLUDED_PARAMS}


# ============================================================================ límite de frecuencia
# Intervalo mínimo entre peticiones (segundos) según las políticas publicadas de cada servicio.
MIN_INTERVAL = {
    "eutils.ncbi.nlm.nih.gov": 0.34,   # 3 peticiones/s sin clave; 10/s con NCBI_API_KEY
    "api.crossref.org": 0.05,
    "api.openalex.org": 0.12,
    "www.ebi.ac.uk": 0.10,
    "pubchem.ncbi.nlm.nih.gov": 0.21,  # máximo 5 peticiones/s
    "rest.uniprot.org": 0.10,
    "string-db.org": 1.0,              # STRING pide 1 s entre llamadas
    "reactome.org": 0.10,
    "api.lens.org": 0.20,
}

RETRY_STATUS = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 4
_MISS = object()


def ratelimit_path(cfg, host: str) -> Path:
    """Fichero de bloqueo compartido por todos los procesos que consultan `host`."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", host) or "_"
    return Path(cfg.cache_dir) / "ratelimit" / f"{safe}.lock"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class HttpClient:
    """Cliente con caché, límite de frecuencia por host (entre procesos), reintentos y procedencia.

    `fresh=True` no lee nunca la caché (sí la actualiza): úsalo cuando el resultado deba reflejar
    el estado actual de la fuente, como en la verificación de referencias. En modo offline, un
    cliente `fresh` lanza `OfflineError` en lugar de servir una copia.
    """

    def __init__(self, cfg, session: requests.Session | None = None, fresh: bool = False):
        self.cfg = cfg
        self.fresh = bool(fresh)
        self.session = session or requests.Session()
        agent = f"nutria/{__version__} (investigacion en nutricion animal)"
        if cfg.email:
            agent += f" mailto:{cfg.email}"
        self.session.headers.update({"User-Agent": agent, "Accept": "application/json"})
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------ límite de frecuencia
    def _interval(self, host: str) -> float:
        interval = MIN_INTERVAL.get(host, 0.0)
        if host == "eutils.ncbi.nlm.nih.gov" and self.cfg.secret("NCBI_API_KEY"):
            interval = 0.11
        return interval

    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            lock = self._host_locks.get(host)
            if lock is None:
                lock = self._host_locks[host] = threading.Lock()
            return lock

    def _wait(self, host: str) -> None:
        """Espera lo necesario para respetar el intervalo mínimo de `host`.

        En POSIX el estado se comparte entre procesos: un fichero de bloqueo por host
        (`cfg.cache_dir/ratelimit/<host>.lock`) guarda la marca de tiempo de la última llamada y
        cada proceso espera el intervalo con el bloqueo tomado. Sin `fcntl` (Windows) o si el
        directorio no es accesible, el límite se aplica solo dentro del proceso.
        """
        interval = self._interval(host)
        if interval <= 0 or not host:
            return
        with self._host_lock(host):
            if fcntl is not None:
                try:
                    self._wait_shared(host, interval)
                    return
                except OSError:
                    pass
            delay = self._last.get(host, 0.0) + interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()

    def _wait_shared(self, host: str, interval: float) -> None:
        path = ratelimit_path(self.cfg, host)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        with os.fdopen(fd, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.seek(0)
                try:
                    last = float(fh.read().strip() or 0.0)
                except ValueError:  # contenido ilegible (también texto no UTF-8): se ignora
                    last = 0.0
                # Si el reloj retrocedió, nunca se espera más de un intervalo.
                delay = min(last + interval - time.time(), interval)
                if delay > 0:
                    time.sleep(delay)
                fh.seek(0)
                fh.truncate()
                fh.write(f"{time.time():.6f}\n")
                fh.flush()
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # ------------------------------------------------------------------ caché
    def _cache_file(self, key: str) -> Path:
        return Path(self.cfg.cache_dir) / key[:2] / f"{key}.json"

    @staticmethod
    def _decode(body: str, expect: str):
        return json.loads(body) if expect == "json" else body

    def _from_cache(self, cache_file: Path, ttl: float, expect: str, trace: dict):
        """Valor de la copia en caché (y evento `http_cache`), o `_MISS` si no hay copia válida."""
        if not cache_file.is_file():
            return _MISS
        try:
            cached = json.loads(cache_file.read_text("utf-8"))
            ts = float(cached["ts"])
            body = cached["body"]
            age = max(0.0, time.time() - ts)
            if not (self.cfg.offline or age < ttl):
                return _MISS
            value = self._decode(body, expect)
        except (OSError, ValueError, KeyError, TypeError):
            return _MISS
        log_event(self.cfg, "http_cache", inputs=trace,
                  outputs={"cached_at": _iso(ts), "age_days": round(age / 86400, 3),
                           "sha256": sha256_text(body), "bytes": len(body)})
        return value

    def _write_cache(self, cache_file: Path, method: str, url: str, params: dict, body: str) -> None:
        record = {"ts": time.time(), "method": method, "url": redact(url, self.cfg),
                  "params": _cache_params(params), "body": body}
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_name(f"{cache_file.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(record, default=str), "utf-8")
        os.replace(tmp, cache_file)

    # ------------------------------------------------------------------ errores
    def _failure(self, cls: type[NetworkError], message: str, trace: dict, status: int | None = None,
                 attempts: int = 0) -> NetworkError:
        """Crea la excepción (con el mensaje redactado) y deja el fallo en la procedencia."""
        exc = cls(message, cfg=self.cfg)
        try:
            log_event(self.cfg, "http_error", inputs=trace,
                      outputs={"error": str(exc), "type": cls.__name__, "status": status, "attempts": attempts})
        except OSError:  # la procedencia no debe ocultar el error original
            pass
        return exc

    def _describe(self, exc: Exception) -> str:
        """Tipo y texto de una excepción de `requests`, redactado antes de recortarlo."""
        return f"{type(exc).__name__}: {redact(exc, self.cfg)[:300]}"

    @staticmethod
    def _retry_after(resp, default: float) -> float:
        value = str((resp.headers or {}).get("Retry-After", "") or "").strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            return default

    # ------------------------------------------------------------------ petición
    def request(self, method: str, url: str, *, params: dict | None = None, json_body=None,
                data: str | None = None, headers: dict | None = None, use_cache: bool = True,
                expect: str = "json", ttl_days: float | None = None):
        method = method.upper()
        params = {k: v for k, v in (params or {}).items() if v is not None}
        cache_file = self._cache_file(cache_key(method, url, params, json_body, data))
        ttl = (self.cfg.cache_days if ttl_days is None else ttl_days) * 86400
        trace: dict = {"method": method, "url": redact(url, self.cfg), "params": params}
        if self.fresh:
            trace["fresh"] = True

        if self.cfg.offline and self.fresh:
            raise self._failure(OfflineError, "Modo offline: esta consulta exige una respuesta en línea "
                                              f"(no se usa la caché) y no hay red: {url}", trace)
        if use_cache and not self.fresh:
            value = self._from_cache(cache_file, ttl, expect, trace)
            if value is not _MISS:
                return value
        if self.cfg.offline:
            raise self._failure(OfflineError, f"Modo offline: no hay copia en caché de {url}", trace)

        host = urlparse(url).hostname or ""
        backoff = 1.0
        last_error = ""
        last_status: int | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait(host)
            try:
                resp = self.session.request(method, url, params=params, json=json_body, data=data,
                                            headers=headers, timeout=self.cfg.timeout)
            except requests.exceptions.ProxyError as exc:
                # `from None`: la excepción original lleva la URL con sus parámetros sin redactar.
                raise self._failure(NetworkError, f"El proxy o la política de red bloquea {host}. Permite ese "
                                                  f"dominio o usa --offline. ({self._describe(exc)})",
                                    trace, attempts=attempt) from None
            except requests.exceptions.RequestException as exc:
                last_error, last_status = self._describe(exc), None
                if attempt < MAX_ATTEMPTS:
                    time.sleep(backoff)
                    backoff *= 2
                continue
            status = resp.status_code
            if status == 404:
                raise self._failure(NotFoundError, f"No encontrado (404): {resp.url}", trace,
                                    status=status, attempts=attempt)
            if status in RETRY_STATUS:
                last_error, last_status = f"HTTP {status} en {host}", status
                if attempt < MAX_ATTEMPTS:
                    time.sleep(min(self._retry_after(resp, backoff), 30.0))
                    backoff *= 2
                continue
            if status >= 400:
                # Se redacta el cuerpo completo antes de recortarlo: un recorte nunca deja medio secreto.
                detail = redact(resp.text[:5000], self.cfg)[:300]
                raise self._failure(NetworkError, f"HTTP {status} en {resp.url}: {detail}", trace,
                                    status=status, attempts=attempt)
            body = resp.text
            try:
                value = self._decode(body, expect)
            except ValueError as exc:
                raise self._failure(NetworkError, f"Respuesta no válida de {host} (se esperaba JSON): {exc}",
                                    trace, status=status, attempts=attempt) from None
            if use_cache:
                self._write_cache(cache_file, method, url, params, body)
            log_event(self.cfg, "http", inputs=trace,
                      outputs={"status": status, "sha256": sha256_text(body), "bytes": len(body)})
            return value
        raise self._failure(NetworkError, f"Sin respuesta válida de {host} tras {MAX_ATTEMPTS} intentos: "
                                          f"{last_error}", trace, status=last_status, attempts=MAX_ATTEMPTS)

    def get_json(self, url: str, **kw):
        return self.request("GET", url, expect="json", **kw)

    def get_text(self, url: str, **kw):
        return self.request("GET", url, expect="text", **kw)

    def post_json(self, url: str, json_body, **kw):
        return self.request("POST", url, json_body=json_body, expect="json", **kw)
