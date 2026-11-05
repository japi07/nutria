"""Cliente HTTP común: caché en disco con integridad, límites de uso por host, reintentos y procedencia.

Todas las fuentes externas pasan por aquí.

- **Límites de uso** (`MIN_INTERVAL`, `MAX_CONCURRENCY`): intervalo mínimo entre peticiones y número
  máximo de peticiones simultáneas por host, compartidos entre procesos en POSIX (ficheros de bloqueo en
  `cache_dir/ratelimit`). Los valores se tomaron de las políticas que cada servicio publica en su
  documentación y NO se han comprobado contra los servicios reales (la red del entorno de desarrollo
  bloqueaba esos dominios).
- **Caché con integridad**: cada copia guarda su clave y la huella SHA-256 de su cuerpo. Al leerla se exige
  que la huella coincida con el cuerpo, que la clave corresponda a la petición y que la procedencia registre
  esa respuesta (evento `http` con la misma clave y huella, o una importación con `nutria cache importar`).
  La fecha de la copia es la más antigua entre la de ese registro y la del propio fichero: una copia nunca
  parece más reciente de lo que dicen ambos. Una copia que no cumple se descarta con aviso (`avisos`, evento
  `http_cache_descartada`): con red se vuelve a pedir; sin red, falla.
- **Sin red** (`offline`): solo se sirven copias válidas con una antigüedad máxima (`cache_max_offline_days`,
  180 días por defecto; `--antiguedad-maxima`). Cada copia servida, con o sin red, queda en `cache_hits`
  (fecha y antigüedad, que las órdenes muestran en su salida) y en la procedencia (`http_cache`).
- **Procedencia** (`research/procedencia.jsonl`): respuestas de la red (`http`, con huella y clave de caché),
  copias servidas (`http_cache`), copias descartadas (`http_cache_descartada`), peticiones fallidas
  (`http_error`) y comprobaciones de `nutria doctor` (`http_sonda`).
- **Confidencialidad** (`confidential=True`): la procedencia guarda solo la huella SHA-256 de la URL y de
  los parámetros, y no el texto de los errores. La consulta viaja igualmente al servicio externo.

Seguridad: ningún mensaje de `NetworkError`, evento de procedencia ni fichero de caché contiene claves de API,
tokens ni el correo de contacto (`redact`), y la clave de caché no depende de ellos (`cache_key`).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, quote_plus, unquote_plus, urlparse

import requests

from . import __version__
from .config import DEFAULT_MAX_OFFLINE_DAYS
from .provenance import log_event, sha256_file, sha256_text, utc_now

try:  # bloqueo entre procesos en POSIX; en Windows el límite se aplica solo dentro del proceso
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

# Espera breve entre intentos de conseguir turno (no es una espera de la política del servicio).
_poll_sleep = time.sleep


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
_URL_RE = re.compile(r"(?i)\bhttps?://([^/\s\"'<>?#]+)[^\s\"'<>]*")


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


def hide_urls(text) -> str:
    """Sustituye cada URL del texto por su host (`https://host/…`): para consultas confidenciales."""
    return _URL_RE.sub(lambda m: f"https://{m.group(1)}/…", str(text or ""))


# ============================================================================ excepciones
class NetworkError(RuntimeError):
    """Fallo de red, host bloqueado por la política de red o respuesta de error del servicio.

    El mensaje se redacta al crear la excepción (`redact`): nunca lleva claves, tokens ni el correo.
    `motivo` clasifica el fallo: red (sin más detalle), bloqueado (proxy o política de red), conexion (sin
    respuesta), servicio (error del servicio, HTTP 5xx, tras los reintentos), peticion (el servicio rechaza la
    petición, HTTP 4xx), credenciales (clave ausente o rechazada, HTTP 401/403), limite (límite de uso: HTTP 429 o
    sin turno libre), respuesta (respuesta no válida), offline o no_encontrado (HTTP 404). `status` guarda el código
    HTTP si lo hubo.
    """

    motivo = "red"

    def __init__(self, message: object = "", *, cfg=None, status: int | None = None, motivo: str | None = None):
        super().__init__(redact(message, cfg))
        self.status = status
        if motivo:
            self.motivo = motivo


class NotFoundError(NetworkError):
    """El recurso no existe en la fuente (HTTP 404).

    Hereda de `NetworkError` solo por compatibilidad (el código que captura `NetworkError` sigue viendo los
    404), pero no es un fallo de red: la CLI lo presenta como «No encontrado», con su propio código de
    salida, nunca como «Error de red».
    """

    motivo = "no_encontrado"


class OfflineError(NetworkError):
    """Modo offline activo y la petición no puede servirse desde la caché (o exige datos en línea)."""

    motivo = "offline"


# ============================================================================ caché
CACHE_FORMAT = 2
EXPORT_FORMAT = "nutria-cache/1"
_FUTURE_TOLERANCE_S = 300.0
_HEX64_RE = re.compile(r"[0-9a-f]{64}")


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


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _parse_ts(value) -> float | None:
    """Marca de tiempo ISO 8601 (sin zona = UTC) en segundos desde la época, o None si no se entiende."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


# ============================================================================ límites de uso
# Intervalo mínimo entre peticiones (segundos) según las políticas publicadas de cada servicio. Se aplica al
# host y a sus subdominios (p. ej. version-12-0.string-db.org comparte el de string-db.org). Valores tomados
# de la documentación de cada servicio, sin comprobar contra los servicios reales.
MIN_INTERVAL = {
    "eutils.ncbi.nlm.nih.gov": 0.34,   # 3 peticiones/s sin clave; 10/s con NCBI_API_KEY
    "api.crossref.org": 0.20,          # pool público: 5 peticiones/s (con correo de contacto, pool «polite»: 10/s)
    "api.openalex.org": 0.12,
    "www.ebi.ac.uk": 0.10,
    "pubchem.ncbi.nlm.nih.gov": 0.21,  # máximo 5 peticiones/s
    "rest.uniprot.org": 0.10,
    "string-db.org": 1.0,              # STRING pide 1 s entre llamadas
    "reactome.org": 0.10,
    "api.lens.org": 0.20,
    "eur-lex.europa.eu": 1.0,          # páginas web sin límite publicado: valor prudente
    "doi.org": 0.20,                   # sin límite publicado comprobado: valor prudente
    "api.datacite.org": 0.20,          # ídem
}
# Con correo de contacto (cfg.email), Crossref atiende en su pool «polite», con límites más amplios.
POLITE_INTERVAL = {"api.crossref.org": 0.10}
# Peticiones simultáneas máximas por host, contadas entre procesos (Crossref publica concurrencia 1 en el pool
# público y 3 en el «polite»; política vigente desde el 1-12-2025 según la auditoría, no comprobada en vivo).
MAX_CONCURRENCY = {"api.crossref.org": 1}
POLITE_CONCURRENCY = {"api.crossref.org": 3}
# Espera máxima por un turno libre antes de rendirse (segundos; como mínimo 4 veces el timeout configurado).
CONCURRENCY_WAIT_S = 120.0

RETRY_STATUS = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 4
_MISS = object()

# Cabeceras de respuesta que se conservan (versión de la base de datos que respondió).
KEEP_HEADERS = ("x-uniprot-release", "x-uniprot-release-date")


def _policy_key(host: str, table: dict) -> str | None:
    """Entrada de `table` que corresponde a `host` (el propio host o un dominio del que es subdominio)."""
    host = (host or "").lower().rstrip(".")
    if host in table:
        return host
    for key in table:
        if host.endswith("." + key):
            return key
    return None


def _safe_name(host: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", host) or "_"


def ratelimit_path(cfg, host: str) -> Path:
    """Fichero de bloqueo compartido por todos los procesos que consultan `host`."""
    return Path(cfg.cache_dir) / "ratelimit" / f"{_safe_name(host)}.lock"


def _headers_digest(headers) -> str:
    """Huella SHA-256 de las cabeceras conservadas (las que la procedencia registra junto al cuerpo)."""
    clean = {str(k).lower(): str(v) for k, v in (headers or {}).items()} if isinstance(headers, dict) else {}
    return sha256_text(json.dumps(clean, sort_keys=True))


def _validation_problem(validate, value) -> str | None:
    """Problema que `validate(value)` encuentra en una respuesta (None si no hay validación o si la supera). Una
    validación que falla con una excepción cuenta como problema (falla en cerrado)."""
    if validate is None:
        return None
    try:
        problem = validate(value)
    except Exception as exc:  # noqa: BLE001 - la validación nunca deja pasar una respuesta que no entiende
        return f"{type(exc).__name__}: {exc}"
    return str(problem) if problem else None


def _kept_headers(headers) -> dict:
    out: dict[str, str] = {}
    try:
        items = list(headers.items())
    except AttributeError:
        return out
    for name, value in items:
        if str(name).lower() in KEEP_HEADERS:
            out[str(name).lower()] = str(value)
    return out


class HttpClient:
    """Cliente con caché íntegra, límites de uso por host (entre procesos), reintentos y procedencia.

    `fresh=True` no lee nunca la caché (sí la actualiza): úsalo cuando el resultado deba reflejar el estado
    actual de la fuente, como en la verificación de referencias. En modo offline, un cliente `fresh` lanza
    `OfflineError` en lugar de servir una copia. `confidential=True` deja en la procedencia solo huellas de
    la URL y de los parámetros. Ciérralo con `close()` (o úsalo con `with`).
    """

    def __init__(self, cfg, session: requests.Session | None = None, fresh: bool = False,
                 confidential: bool = False):
        self.cfg = cfg
        self.fresh = bool(fresh)
        self.confidential = bool(confidential)
        self._owns_session = session is None
        self.session = session or requests.Session()
        agent = f"nutria/{__version__} (investigacion en nutricion animal)"
        if cfg.email:
            agent += f" mailto:{cfg.email}"
        self.session.headers.update({"User-Agent": agent, "Accept": "application/json"})
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._thread_slots: dict[str, threading.BoundedSemaphore] = {}
        self._confirmed: dict[str, list[tuple[float, str, str]]] | None = None
        self._confirmed_offset = 0
        # Lo que las órdenes muestran al usuario: copias servidas desde la caché y avisos.
        self.cache_hits: list[dict] = []
        self.avisos: list[str] = []
        self.response_headers: dict[str, dict] = {}

    # ------------------------------------------------------------------ ciclo de vida
    def close(self) -> None:
        """Cierra la sesión de requests si la creó este cliente."""
        if self._owns_session:
            self.session.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ límites de uso
    def _interval(self, host: str) -> float:
        key = _policy_key(host, MIN_INTERVAL)
        if key is None:
            return 0.0
        interval = MIN_INTERVAL[key]
        if key == "eutils.ncbi.nlm.nih.gov" and self.cfg.secret("NCBI_API_KEY"):
            interval = 0.11
        if key in POLITE_INTERVAL and self.cfg.email:
            interval = POLITE_INTERVAL[key]
        return interval

    def _concurrency(self, host: str) -> int:
        """Peticiones simultáneas permitidas a `host` (0 = sin límite)."""
        key = _policy_key(host, MAX_CONCURRENCY)
        if key is None:
            return 0
        if key in POLITE_CONCURRENCY and self.cfg.email:
            return POLITE_CONCURRENCY[key]
        return MAX_CONCURRENCY[key]

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
        key = _policy_key(host, MIN_INTERVAL) or host
        with self._host_lock(key):
            if fcntl is not None:
                try:
                    self._wait_shared(key, interval)
                    return
                except OSError:
                    pass
            delay = self._last.get(key, 0.0) + interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last[key] = time.monotonic()

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

    @contextmanager
    def _slot(self, host: str):
        """Turno para una petición a `host` si el servicio limita la concurrencia (`MAX_CONCURRENCY`).

        En POSIX los turnos son ficheros `cache_dir/ratelimit/<host>.turno<i>.lock` bloqueados con flock:
        cuentan entre procesos y entre hilos, y se liberan solos si el proceso termina. Sin `fcntl` o si el
        directorio no es accesible, se usa un semáforo dentro del proceso.
        """
        limit = self._concurrency(host)
        if limit <= 0:
            yield
            return
        key = _policy_key(host, MAX_CONCURRENCY) or host
        fd = self._acquire_slot_file(key, limit) if fcntl is not None else None
        if fd is not None:
            try:
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
            return
        with self._lock:
            semaphore = self._thread_slots.get(key)
            if semaphore is None:
                semaphore = self._thread_slots[key] = threading.BoundedSemaphore(limit)
        with semaphore:
            yield

    def _acquire_slot_file(self, key: str, limit: int) -> int | None:
        """Descriptor de un fichero de turno bloqueado, o None si los turnos no pueden usarse en disco."""
        directory = Path(self.cfg.cache_dir) / "ratelimit"
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        patience = max(CONCURRENCY_WAIT_S, 4 * float(getattr(self.cfg, "timeout", 0) or 0))
        deadline = time.monotonic() + patience
        while True:
            for index in range(limit):
                try:
                    fd = os.open(directory / f"{_safe_name(key)}.turno{index}.lock", os.O_RDWR | os.O_CREAT, 0o644)
                except OSError:
                    return None
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return fd
                except BlockingIOError:
                    os.close(fd)
                except OSError:
                    os.close(fd)
                    return None
            if time.monotonic() >= deadline:
                raise NetworkError(f"Sin turno libre para {key} tras {patience:.0f} s: el servicio admite {limit} "
                                   "petición(es) simultánea(s) y otros procesos las ocupan", cfg=self.cfg,
                                   motivo="limite")
            _poll_sleep(0.02)

    # ------------------------------------------------------------------ trazas y errores
    def _trace(self, method: str, url: str, params: dict) -> dict:
        if self.confidential:
            trace = {"method": method, "host": urlparse(url).hostname or "",
                     "url_sha256": sha256_text(strip_query_params(url, CACHE_EXCLUDED_PARAMS)),
                     "params_sha256": sha256_text(json.dumps(_cache_params(params), sort_keys=True, default=str)),
                     "confidencial": True}
        else:
            trace = {"method": method, "url": redact(url, self.cfg), "params": params}
        if self.fresh:
            trace["fresh"] = True
        return trace

    def _shown_url(self, url: str) -> str:
        """URL que puede mostrarse en avisos y mensajes (solo el host si la consulta es confidencial)."""
        return hide_urls(url) if self.confidential else redact(url, self.cfg)

    def _failure(self, cls: type[NetworkError], message: str, trace: dict, status: int | None = None,
                 attempts: int = 0, motivo: str | None = None) -> NetworkError:
        """Crea la excepción (con el mensaje redactado) y deja el fallo en la procedencia."""
        exc = cls(message, cfg=self.cfg, status=status, motivo=motivo)
        shown = f"{cls.__name__} (texto omitido: consulta confidencial)" if self.confidential else str(exc)
        try:
            log_event(self.cfg, "http_error", inputs=trace,
                      outputs={"error": shown, "type": cls.__name__, "status": status, "attempts": attempts,
                               "motivo": exc.motivo})
        except OSError:  # la procedencia no debe ocultar el error original
            pass
        return exc

    def _describe(self, exc: Exception) -> str:
        """Tipo y texto de una excepción de `requests`, redactado antes de recortarlo."""
        text = redact(exc, self.cfg)
        if self.confidential:
            text = hide_urls(text)
        return f"{type(exc).__name__}: {text[:300]}"

    @staticmethod
    def _retry_after(resp, default: float) -> float:
        value = str((resp.headers or {}).get("Retry-After", "") or "").strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            return default

    # ------------------------------------------------------------------ caché
    def _cache_file(self, key: str) -> Path:
        return Path(self.cfg.cache_dir) / key[:2] / f"{key}.json"

    @staticmethod
    def _decode(body: str, expect: str):
        return json.loads(body) if expect == "json" else body

    def _max_offline_days(self) -> float:
        value = getattr(self.cfg, "cache_max_offline_days", None)
        return float(DEFAULT_MAX_OFFLINE_DAYS if value is None else value)

    def _load_confirmations(self) -> None:
        """Índice clave de caché -> [(fecha, sha256, origen)] con las respuestas que registra la procedencia:
        eventos `http` (respuestas de la red) e importaciones (`cache_importar`). Se lee de forma incremental:
        solo las líneas añadidas desde la última lectura (todo el fichero si se ha acortado o sustituido)."""
        if self._confirmed is None:
            self._confirmed, self._confirmed_offset = {}, 0
        path = Path(self.cfg.provenance_path)
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size < self._confirmed_offset:
            self._confirmed, self._confirmed_offset = {}, 0
        if size == self._confirmed_offset:
            return
        try:
            handle = open(path, "rb")
        except OSError:
            return
        with handle:
            handle.seek(self._confirmed_offset)
            chunk = handle.read()
        complete = chunk.rfind(b"\n") + 1          # una línea a medio escribir se leerá la próxima vez
        self._confirmed_offset += complete
        for raw in chunk[:complete].splitlines():
            if b'"clave_cache"' not in raw and b'"cache_importar"' not in raw:
                continue
            try:
                event = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            if not isinstance(event, dict) or not isinstance(event.get("outputs"), dict):
                continue
            outputs = event["outputs"]
            if event.get("action") == "http":
                self._index_add(self._confirmed, outputs.get("clave_cache"), outputs.get("sha256"), event.get("ts"),
                                "red", outputs.get("cabeceras"))
            elif event.get("action") == "cache_importar":
                for item in outputs.get("entradas") or []:
                    if isinstance(item, dict):
                        self._index_add(self._confirmed, item.get("clave"), item.get("sha256"), item.get("fecha"),
                                        "importada", item.get("cabeceras"))

    @staticmethod
    def _index_add(index: dict, key, sha, when, origin: str, headers=None) -> None:
        moment = _parse_ts(when) if not isinstance(when, (int, float)) else float(when)
        if isinstance(key, str) and isinstance(sha, str) and moment is not None:
            index.setdefault(key, []).append((moment, sha, origin, _headers_digest(headers)))

    def _latest_confirmation(self, key: str, sha: str, headers_digest: str) -> tuple[float, str] | None:
        limit = time.time() + _FUTURE_TOLERANCE_S   # una fecha futura no confirma nada
        found = [(moment, origin) for moment, stored, origin, digest in (self._confirmed or {}).get(key, ())
                 if stored == sha and digest == headers_digest and moment <= limit]
        return max(found) if found else None

    def _confirmation(self, key: str, sha: str, headers_digest: str) -> tuple[float, str] | None:
        """Fecha (y origen) más reciente en que la procedencia registra esta respuesta (cuerpo y cabeceras
        conservadas), o None. Lee antes lo que se haya añadido a la procedencia (también lo que registren otras
        órdenes)."""
        self._load_confirmations()
        return self._latest_confirmation(key, sha, headers_digest)

    def _remember(self, key: str, sha: str, moment: float, origin: str = "red", headers=None) -> None:
        # Sin efecto real: el evento ya está en la procedencia y la próxima lectura incremental lo incorpora;
        # se conserva para que la confirmación no dependa de volver a leer el fichero.
        if self._confirmed is not None:
            self._confirmed.setdefault(key, []).append((moment, sha, origin, _headers_digest(headers)))

    def read_cached(self, cache_file: Path, key: str) -> tuple[dict | None, tuple[float, str] | None, str | None]:
        """Lee y valida una copia: (registro, (fecha, origen), None) si es válida; (None, None, motivo) si no
        lo es; (None, None, None) si no hay copia."""
        if not cache_file.is_file():
            return None, None, None
        try:
            record = json.loads(cache_file.read_text("utf-8"))
        except (OSError, ValueError):
            return None, None, "fichero ilegible"
        if not isinstance(record, dict) or not isinstance(record.get("body"), str):
            return None, None, "formato desconocido"
        stored = record.get("sha256")
        if not stored:
            return None, None, "sin huella SHA-256 (copia de una versión anterior de nutria o manipulada)"
        actual = sha256_text(record["body"])
        if stored != actual:
            return None, None, (f"la huella SHA-256 no coincide con el contenido (guardada {str(stored)[:12]}…, "
                                f"real {actual[:12]}…): copia alterada o dañada")
        if record.get("clave") != key:
            return None, None, "la clave guardada no corresponde a esta petición"
        try:
            written = float(record.get("ts"))
        except (TypeError, ValueError):
            return None, None, "la copia no tiene una fecha válida"
        headers = record.get("cabeceras") or {}
        if not isinstance(headers, dict):
            return None, None, "cabeceras con formato no válido"
        confirmed = self._confirmation(key, actual, _headers_digest(headers))
        if confirmed is None:
            return None, None, ("la procedencia no registra esta respuesta (ni un evento `http` con la misma clave, "
                                "huella y cabeceras ni una importación con `nutria cache importar`)")
        # La más antigua de las dos fechas: ni el fichero ni la procedencia pueden rejuvenecer una copia.
        return record, (min(confirmed[0], written), confirmed[1]), None

    def _discard(self, key: str, trace: dict, url: str, problem: str) -> None:
        self.avisos.append(f"Copia de la caché descartada ({self._shown_url(url)}): {problem}")
        try:
            log_event(self.cfg, "http_cache_descartada", inputs=trace,
                      outputs={"motivo": problem, "clave_cache": key})
        except OSError:
            pass

    def _from_cache(self, cache_file: Path, key: str, ttl: float, expect: str, trace: dict, url: str,
                    validate=None):
        """(valor, None) con una copia válida (y evento `http_cache`), o (_MISS, motivo) si no hay copia
        utilizable; el motivo explica por qué se descartó la que había (None si no había ninguna)."""
        record, confirmed, problem = self.read_cached(cache_file, key)
        if record is None:
            if problem:
                self._discard(key, trace, url, problem)
            return _MISS, problem
        moment, origin = confirmed
        age = max(0.0, time.time() - moment)
        if not self.cfg.offline:
            if age >= ttl:
                return _MISS, None
        elif age > self._max_offline_days() * 86400:
            problem = (f"la copia es del {_iso(moment)} ({age / 86400:.0f} días), más antigua que el máximo de "
                       f"{self._max_offline_days():g} días admitido sin red (amplíalo con --antiguedad-maxima DIAS "
                       "solo si te sirve una copia tan antigua)")
            self._discard(key, trace, url, problem)
            return _MISS, problem
        body = record["body"]
        try:
            value = self._decode(body, expect)
        except ValueError:
            problem = "el contenido no es JSON válido"
            self._discard(key, trace, url, problem)
            return _MISS, problem
        problem = _validation_problem(validate, value)
        if problem:
            problem = f"el contenido no es una respuesta válida de la fuente ({problem})"
            self._discard(key, trace, url, problem)
            return _MISS, problem
        hit = {"fecha": _iso(moment), "antiguedad_dias": round(age / 86400, 3), "sha256": record["sha256"],
               "bytes": len(body.encode("utf-8")), "clave_cache": key, "origen": origin}
        log_event(self.cfg, "http_cache", inputs=trace,
                  outputs={"cached_at": hit["fecha"], "age_days": hit["antiguedad_dias"], "sha256": hit["sha256"],
                           "bytes": hit["bytes"], "clave_cache": key, "origen": origin})
        self.cache_hits.append({"url": self._shown_url(url), **hit})
        headers = record.get("cabeceras")
        self.response_headers[urlparse(url).hostname or ""] = dict(headers) if isinstance(headers, dict) else {}
        return value, None

    @staticmethod
    def _was_confidential(cache_file: Path) -> bool:
        try:
            return bool(json.loads(cache_file.read_text("utf-8")).get("confidencial"))
        except (OSError, ValueError, AttributeError):
            return False

    def write_cache(self, cache_file: Path, key: str, method: str, url: str, params: dict, json_body, data,
                    body: str, headers: dict | None, moment: float, confidential: bool | None = None) -> None:
        """Escribe la copia (fichero temporal y renombrado atómico). `confidencial` marca las consultas confidenciales
        (no se exportan salvo petición expresa); la marca no se pierde aunque la misma consulta se repita sin ella."""
        confidential = self.confidential if confidential is None else bool(confidential)
        confidential = confidential or self._was_confidential(cache_file)
        record = {"formato": CACHE_FORMAT, "clave": key, "ts": moment, "fecha": _iso(moment), "method": method,
                  "url": redact(url, self.cfg), "params": _cache_params(params), "json": json_body, "data": data,
                  "sha256": sha256_text(body), "bytes": len(body.encode("utf-8")), "cabeceras": headers or {},
                  "confidencial": confidential, "body": body}
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_name(f"{cache_file.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(record, default=str), "utf-8")
        os.replace(tmp, cache_file)

    # ------------------------------------------------------------------ petición
    def request(self, method: str, url: str, *, params: dict | None = None, json_body=None,
                data: str | None = None, headers: dict | None = None, use_cache: bool = True,
                expect: str = "json", ttl_days: float | None = None, timeout: float | None = None,
                max_attempts: int | None = None, validate=None):
        """Petición con caché, límites de uso, reintentos y procedencia.

        `validate(valor)` (opcional) devuelve None si la respuesta es la que se esperaba de la fuente o un texto
        con el problema (p. ej. una página de verificación anti-robots con HTTP 200): una respuesta así no se
        guarda en la caché y se lanza `NetworkError` (motivo "respuesta"), y una copia de la caché que no la
        supera se descarta con aviso, como una copia alterada.
        """
        method = method.upper()
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = cache_key(method, url, params, json_body, data)
        cache_file = self._cache_file(key)
        ttl = (self.cfg.cache_days if ttl_days is None else ttl_days) * 86400
        trace = self._trace(method, url, params)
        attempts = max(1, int(max_attempts or MAX_ATTEMPTS))

        if self.cfg.offline and self.fresh:
            raise self._failure(OfflineError, "Modo offline: esta consulta exige una respuesta en línea "
                                              f"(no se usa la caché) y no hay red: {self._shown_url(url)}", trace)
        problem = None
        if use_cache and not self.fresh:
            value, problem = self._from_cache(cache_file, key, ttl, expect, trace, url, validate)
            if value is not _MISS:
                return value
        if self.cfg.offline:
            if problem:
                raise self._failure(OfflineError, f"Modo offline: la copia en caché de {self._shown_url(url)} no se "
                                                  f"puede usar: {problem}. Sin red no hay forma de sustituirla.", trace)
            raise self._failure(OfflineError, f"Modo offline: no hay copia en caché de {self._shown_url(url)}", trace)

        host = urlparse(url).hostname or ""
        backoff = 1.0
        last_error = ""
        last_status: int | None = None
        for attempt in range(1, attempts + 1):
            resp, error = None, None
            try:
                with self._slot(host):
                    self._wait(host)
                    try:
                        resp = self.session.request(method, url, params=params, json=json_body, data=data,
                                                    headers=headers, timeout=timeout or self.cfg.timeout)
                    except requests.exceptions.RequestException as exc:
                        error = exc
            except NetworkError as exc:  # sin turno libre (límite de concurrencia del servicio)
                raise self._failure(NetworkError, str(exc), trace, attempts=attempt, motivo=exc.motivo) from None
            if isinstance(error, requests.exceptions.ProxyError):
                # `from None`: la excepción original lleva la URL con sus parámetros sin redactar.
                raise self._failure(NetworkError, f"El proxy o la política de red bloquea {host}: permite ese "
                                                  "dominio en la red (lista en docs/FUENTES.md; compruébalo con "
                                                  "`nutria doctor`). Sin red no se puede consultar ni verificar nada "
                                                  f"nuevo. ({self._describe(error)})",
                                    trace, attempts=attempt, motivo="bloqueado") from None
            if error is not None:
                last_error, last_status = self._describe(error), None
                if attempt < attempts:
                    time.sleep(backoff)
                    backoff *= 2
                continue
            status = resp.status_code
            if status == 404:
                raise self._failure(NotFoundError, f"El recurso no existe en {host} (HTTP 404): "
                                                   f"{self._shown_url(str(resp.url))}", trace,
                                    status=status, attempts=attempt)
            if status in RETRY_STATUS:
                last_error, last_status = f"HTTP {status} en {host}", status
                if attempt < attempts:
                    time.sleep(min(self._retry_after(resp, backoff), 30.0))
                    backoff *= 2
                continue
            if status >= 400:
                # Se redacta el cuerpo completo antes de recortarlo: un recorte nunca deja medio secreto.
                detail = redact(resp.text[:5000], self.cfg)[:300]
                motivo = "credenciales" if status in (401, 403) else ("peticion" if status < 500 else "servicio")
                raise self._failure(NetworkError, f"HTTP {status} en {self._shown_url(str(resp.url))}: {detail}",
                                    trace, status=status, attempts=attempt, motivo=motivo)
            body = resp.text
            try:
                value = self._decode(body, expect)
            except ValueError as exc:
                raise self._failure(NetworkError, f"Respuesta no válida de {host} (se esperaba JSON): {exc}",
                                    trace, status=status, attempts=attempt, motivo="respuesta") from None
            invalid = _validation_problem(validate, value)
            if invalid:   # no se guarda: una página de error no debe servirse después desde la caché
                raise self._failure(NetworkError, f"Respuesta no válida de {host} (HTTP {status}): {invalid}",
                                    trace, status=status, attempts=attempt, motivo="respuesta")
            moment = time.time()
            sha = sha256_text(body)
            kept = _kept_headers(resp.headers)
            self.response_headers[host] = kept     # también vacío: la versión es la de la última respuesta
            outputs = {"status": status, "sha256": sha, "bytes": len(body.encode("utf-8")), "clave_cache": key}
            if kept:
                outputs["cabeceras"] = kept
            # Primero la procedencia y después la copia: una copia sin registro nunca se sirve.
            log_event(self.cfg, "http", inputs=trace, outputs=outputs)
            if use_cache:
                try:
                    self.write_cache(cache_file, key, method, url, params, json_body, data, body, kept, moment)
                    self._remember(key, sha, moment, "red", kept)
                except OSError as exc:  # la respuesta es válida aunque no pueda guardarse
                    self.avisos.append(f"No se pudo guardar la copia en la caché ({type(exc).__name__}: {exc})")
            return value
        raise self._failure(NetworkError, f"Sin respuesta válida de {host} tras {attempts} intento(s): "
                                          f"{last_error}", trace, status=last_status, attempts=attempts,
                            motivo=("limite" if last_status == 429 else "servicio") if last_status else "conexion")

    def get_json(self, url: str, **kw):
        return self.request("GET", url, expect="json", **kw)

    def get_text(self, url: str, **kw):
        return self.request("GET", url, expect="text", **kw)

    def post_json(self, url: str, json_body, **kw):
        return self.request("POST", url, json_body=json_body, expect="json", **kw)

    # ------------------------------------------------------------------ comprobación (doctor)
    def probe(self, url: str, *, method: str = "GET", params: dict | None = None, json_body=None,
              headers: dict | None = None, timeout: float = 15.0, record: bool = True) -> dict:
        """Una sola petición, sin caché ni reintentos y sin excepción por respuestas HTTP de error (la usa
        `nutria doctor`). En modo offline no hace ninguna petición. Respeta los límites de uso y, con `record`,
        deja un evento `http_sonda`. Devuelve {"estado": ok|http|bloqueado|sin_conexion|no_comprobado, ...}."""
        method = method.upper()
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if self.cfg.offline:
            return {"estado": "no_comprobado", "detalle": "modo offline: no se hace ninguna petición"}
        trace = self._trace(method, url, params)
        host = urlparse(url).hostname or ""
        try:
            with self._slot(host):
                self._wait(host)
                resp = self.session.request(method, url, params=params, json=json_body, headers=headers,
                                            timeout=timeout)
        except requests.exceptions.ProxyError as exc:
            result = {"estado": "bloqueado", "detalle": self._describe(exc)}
        except requests.exceptions.RequestException as exc:
            result = {"estado": "sin_conexion", "detalle": self._describe(exc)}
        except NetworkError as exc:
            result = {"estado": "sin_conexion", "detalle": str(exc)}
        else:
            status = int(resp.status_code)
            result = {"estado": "ok" if status < 400 else "http", "status": status,
                      "cabeceras": _kept_headers(resp.headers)}
            text = resp.text or ""
            if status >= 400:
                result["detalle"] = redact(text[:2000], self.cfg)[:200]
            result["texto"] = text[:20000]
        if record:
            try:
                log_event(self.cfg, "http_sonda", inputs=trace,
                          outputs={k: v for k, v in result.items() if k != "texto"})
            except OSError:
                pass
        return result


# ============================================================================ exportar e importar la caché
def iter_cache_files(cfg):
    """Ficheros de la caché (`cache_dir/<2 hex>/<clave>.json`), en orden estable."""
    root = Path(cfg.cache_dir)
    if not root.is_dir():
        return
    for sub in sorted(root.iterdir()):
        if sub.is_dir() and re.fullmatch(r"[0-9a-f]{2}", sub.name):
            yield from sorted(p for p in sub.glob("*.json") if _HEX64_RE.fullmatch(p.stem))


def inspect_cache(client: HttpClient) -> dict:
    """Clasifica las copias de la caché: válidas (íntegras y registradas en la procedencia) y descartables."""
    valid, confidential, invalid = 0, 0, {}
    oldest, newest = None, None
    for path in iter_cache_files(client.cfg):
        record, confirmed, problem = client.read_cached(path, path.stem)
        if record is None:
            invalid[problem or "desconocido"] = invalid.get(problem or "desconocido", 0) + 1
            continue
        valid += 1
        confidential += bool(record.get("confidencial"))
        oldest = confirmed[0] if oldest is None else min(oldest, confirmed[0])
        newest = confirmed[0] if newest is None else max(newest, confirmed[0])
    return {"validas": valid, "confidenciales": confidential, "descartables": invalid,
            "mas_antigua": _iso(oldest) if oldest else None, "mas_reciente": _iso(newest) if newest else None}


def export_cache(client: HttpClient, destination: Path, include_confidential: bool = False) -> dict:
    """Escribe en `destination` las copias válidas, con su petición, huella, cabeceras y fecha, para reproducir las
    mismas consultas sin red en otra máquina (`import_cache`). No se exportan las copias no válidas ni las que no
    podrían importarse, ni (salvo `include_confidential`) las de consultas confidenciales."""
    entries, skipped, confidential_left = [], {}, 0

    def skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    for path in iter_cache_files(client.cfg):
        record, confirmed, problem = client.read_cached(path, path.stem)
        if record is None:
            skip(problem or "desconocido")
            continue
        if record.get("confidencial") and not include_confidential:
            confidential_left += 1
            continue
        entry = {"clave": path.stem, "method": record.get("method"), "url": record.get("url"),
                 "params": record.get("params") or {}, "json": record.get("json"), "data": record.get("data"),
                 "sha256": record["sha256"], "bytes": len(record["body"].encode("utf-8")),
                 "fecha": _iso(confirmed[0]), "origen": confirmed[1], "cabeceras": record.get("cabeceras") or {},
                 "confidencial": bool(record.get("confidencial")), "body": record["body"]}
        problem = _import_problem(entry)
        if problem:
            skip(f"no se podría importar: {problem}")
            continue
        entries.append(entry)
    bundle = {"formato": EXPORT_FORMAT, "exportado": utc_now(), "nutria": __version__, "entradas": entries}
    destination = Path(destination)
    if destination.is_dir():
        raise IsADirectoryError(21, "el destino de la exportación es un directorio", str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f"{destination.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(bundle, ensure_ascii=False, default=str), "utf-8")
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)   # tras un fallo no queda un fichero temporal a medias
    return {"fichero": str(destination), "entradas": len(entries), "no_exportadas": skipped,
            "confidenciales_omitidas": confidential_left,
            "confidenciales_incluidas": sum(1 for e in entries if e["confidencial"]), "sha256": sha256_file(destination)}


def _import_problem(item, exported_at: float | None = None) -> str | None:
    """Motivo por el que una entrada de una exportación no puede importarse, o None si es válida. Con
    `exported_at` (la fecha de la exportación), una copia posterior a la exportación tampoco es válida."""
    if not isinstance(item, dict):
        return "no es un objeto"
    for name, kind in (("clave", str), ("method", str), ("url", str), ("sha256", str), ("fecha", str), ("body", str)):
        if not isinstance(item.get(name), kind):
            return f"falta '{name}' o no es texto"
    if not isinstance(item.get("params", {}), dict):
        return "'params' no es un objeto"
    if not isinstance(item.get("cabeceras") or {}, dict):
        return "'cabeceras' no es un objeto"
    if not _HEX64_RE.fullmatch(item["clave"]) or not _HEX64_RE.fullmatch(item["sha256"]):
        return "clave o huella con formato no válido"
    if not re.match(r"(?i)https?://", item["url"]):
        return "URL no válida"
    if sha256_text(item["body"]) != item["sha256"]:
        return "la huella SHA-256 no coincide con el contenido"
    if cache_key(item["method"], item["url"], item.get("params") or {}, item.get("json"), item.get("data")) != item["clave"]:
        return "la clave no corresponde a la petición declarada"
    moment = _parse_ts(item["fecha"])
    if moment is None:
        return "fecha no válida"
    if moment > time.time() + _FUTURE_TOLERANCE_S:
        return "fecha futura"
    if exported_at is not None and moment > exported_at + _FUTURE_TOLERANCE_S:
        return "fecha posterior a la de la exportación"
    return None


def import_cache(client: HttpClient, source: Path, expected_sha256: str | None = None) -> dict:
    """Importa una exportación de `export_cache`: comprueba cada entrada (huella, clave y fecha: ni futura ni
    posterior a la exportación), registra la importación en la procedencia (con la huella del fichero) y escribe las
    copias, que conservan su fecha original. No sustituye una copia local válida igual o más reciente. Con
    `expected_sha256` (la huella que mostró `cache exportar`), un fichero distinto no se importa.

    Las fechas las declara el fichero: nutria comprueba que cuadran entre sí, no cuándo se obtuvo cada respuesta
    (una exportación fabricada puede declarar fechas recientes y saltarse así la antigüedad máxima sin red). Por eso
    la importación devuelve el intervalo de fechas declaradas y las copias se marcan «importada» al servirse."""
    source = Path(source)
    raw = source.read_bytes()
    if expected_sha256 is not None:
        wanted = str(expected_sha256).strip().lower()
        actual = sha256_text(raw)
        if not _HEX64_RE.fullmatch(wanted):
            raise ValueError(f"--sha256 no es una huella SHA-256 (64 caracteres hexadecimales): {expected_sha256!r}")
        if actual != wanted:
            raise ValueError(f"{source}: su huella SHA-256 ({actual}) no es la esperada ({wanted}); el fichero no es "
                             "el que se exportó o se ha alterado: no se importa nada")
    try:
        bundle = json.loads(raw.decode("utf-8"))
    except ValueError as exc:   # también UnicodeDecodeError
        raise ValueError(f"{source} no es JSON válido: {exc}") from None
    if not isinstance(bundle, dict) or bundle.get("formato") != EXPORT_FORMAT:
        raise ValueError(f"{source} no es una exportación de la caché de nutria (formato '{EXPORT_FORMAT}')")
    items = bundle.get("entradas")
    if not isinstance(items, list):
        raise ValueError(f"{source}: 'entradas' debe ser una lista")
    exported_at = _parse_ts(bundle.get("exportado"))
    if exported_at is None:
        raise ValueError(f"{source}: la exportación no indica una fecha de exportación válida ('exportado'): no se "
                         "importa nada")
    now = time.time()
    if exported_at > now + _FUTURE_TOLERANCE_S:
        raise ValueError(f"{source}: la fecha de exportación ('exportado': {_iso(exported_at)}) es posterior a la hora "
                         f"actual de esta máquina ({_iso(now)}) en más de {_FUTURE_TOLERANCE_S:.0f} s: el reloj de una "
                         "de las dos máquinas está desajustado o el fichero se ha alterado; no se importa nada (ajusta "
                         "el reloj y vuelve a exportar)")
    accepted, rejected, kept_local = [], [], 0
    for index, item in enumerate(items):
        problem = _import_problem(item, exported_at)
        if problem:
            rejected.append({"indice": index, "motivo": problem})
            continue
        local, confirmed, _ = client.read_cached(client._cache_file(item["clave"]), item["clave"])
        if local is not None and confirmed[0] >= _parse_ts(item["fecha"]):
            kept_local += 1
            continue
        # Solo las cabeceras que conserva el cliente (versión de la base de datos), en minúsculas: lo mismo que se
        # registra en la procedencia y se escribe en la copia, para que su huella coincida al leerla.
        accepted.append({**item, "cabeceras": _kept_headers(item.get("cabeceras") or {})})
    log_event(client.cfg, "cache_importar",
              inputs={"fichero": str(source), "sha256": sha256_text(raw), "exportado": bundle.get("exportado"),
                      "nutria_origen": bundle.get("nutria")},
              outputs={"entradas": [{"clave": i["clave"], "sha256": i["sha256"], "fecha": i["fecha"],
                                     "cabeceras": i["cabeceras"]} for i in accepted],
                       "rechazadas": len(rejected), "conservadas_locales": kept_local})
    for item in accepted:
        moment = _parse_ts(item["fecha"])
        headers = item["cabeceras"]
        client.write_cache(client._cache_file(item["clave"]), item["clave"], item["method"].upper(), item["url"],
                           item.get("params") or {}, item.get("json"), item.get("data"), item["body"], headers,
                           moment, confidential=bool(item.get("confidencial")))
        client._remember(item["clave"], item["sha256"], moment, "importada", headers)
    moments = sorted(_parse_ts(item["fecha"]) for item in accepted)
    return {"fichero": str(source), "importadas": len(accepted), "rechazadas": rejected,
            "conservadas_locales": kept_local, "exportado": _iso(exported_at),
            "fechas_declaradas": {"desde": _iso(moments[0]), "hasta": _iso(moments[-1])} if moments else None}
