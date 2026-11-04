"""Cliente HTTP común: caché en disco, límites de frecuencia por host, reintentos y procedencia.

Todas las fuentes externas pasan por aquí. Así el sistema respeta las políticas de uso
de cada API, puede trabajar sin red con lo ya consultado (`offline`) y deja constancia
de cada consulta en `research/procedencia.jsonl`.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from urllib.parse import urlparse

import requests

from . import __version__
from .provenance import log_event, sha256_text


class NetworkError(RuntimeError):
    """Fallo de red, host bloqueado por la política de red o respuesta de error."""


class NotFoundError(NetworkError):
    """El recurso no existe (HTTP 404)."""


class OfflineError(NetworkError):
    """Modo offline activo y la petición no está en caché."""


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


class HttpClient:
    def __init__(self, cfg, session: requests.Session | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()
        agent = f"nutria/{__version__} (investigacion en nutricion animal)"
        if cfg.email:
            agent += f" mailto:{cfg.email}"
        self.session.headers.update({"User-Agent": agent, "Accept": "application/json"})
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ utilidades
    def _wait(self, host: str) -> None:
        interval = MIN_INTERVAL.get(host, 0.0)
        if host == "eutils.ncbi.nlm.nih.gov" and self.cfg.secret("NCBI_API_KEY"):
            interval = 0.11
        with self._lock:
            delay = self._last.get(host, 0.0) + interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()

    def _cache_file(self, key: str):
        return self.cfg.cache_dir / key[:2] / f"{key}.json"

    @staticmethod
    def _decode(body: str, expect: str):
        return json.loads(body) if expect == "json" else body

    # ------------------------------------------------------------------ petición
    def request(self, method: str, url: str, *, params: dict | None = None, json_body=None,
                data: str | None = None, headers: dict | None = None, use_cache: bool = True,
                expect: str = "json", ttl_days: float | None = None):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        material = json.dumps({"m": method, "u": url, "p": params, "j": json_body, "d": data},
                              sort_keys=True, default=str)
        key = hashlib.sha256(material.encode("utf-8")).hexdigest()
        cache_file = self._cache_file(key)
        ttl = (self.cfg.cache_days if ttl_days is None else ttl_days) * 86400

        if use_cache and cache_file.is_file():
            try:
                cached = json.loads(cache_file.read_text("utf-8"))
                if self.cfg.offline or time.time() - cached["ts"] < ttl:
                    return self._decode(cached["body"], expect)
            except (ValueError, KeyError):
                pass
        if self.cfg.offline:
            raise OfflineError(f"Modo offline: no hay copia en caché de {url}")

        host = urlparse(url).hostname or ""
        backoff = 1.0
        last_error: Exception | None = None
        for _attempt in range(4):
            self._wait(host)
            try:
                resp = self.session.request(method, url, params=params, json=json_body, data=data,
                                            headers=headers, timeout=self.cfg.timeout)
            except requests.exceptions.ProxyError as exc:
                raise NetworkError(
                    f"El proxy o la política de red bloquea {host}. Permite ese dominio o usa --offline."
                ) from exc
            except requests.exceptions.RequestException as exc:
                last_error = exc
                time.sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code == 404:
                raise NotFoundError(f"No encontrado (404): {resp.url}")
            if resp.status_code in (429, 500, 502, 503, 504):
                retry_after = resp.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else backoff
                last_error = NetworkError(f"HTTP {resp.status_code} en {host}")
                time.sleep(min(wait, 30.0))
                backoff *= 2
                continue
            if resp.status_code >= 400:
                raise NetworkError(f"HTTP {resp.status_code} en {resp.url}: {resp.text[:300]}")
            body = resp.text
            if use_cache:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps({"ts": time.time(), "url": url, "body": body}), "utf-8")
            log_event(self.cfg, "http",
                      inputs={"method": method, "url": url, "params": params},
                      outputs={"status": resp.status_code, "sha256": sha256_text(body), "bytes": len(body)})
            return self._decode(body, expect)
        raise NetworkError(f"Sin respuesta válida de {host} tras varios intentos: {last_error}")

    def get_json(self, url: str, **kw):
        return self.request("GET", url, expect="json", **kw)

    def get_text(self, url: str, **kw):
        return self.request("GET", url, expect="text", **kw)

    def post_json(self, url: str, json_body, **kw):
        return self.request("POST", url, json_body=json_body, expect="json", **kw)
