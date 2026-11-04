"""Pruebas del cliente HTTP con una sesión falsa (sin red): caché, `fresh`, offline, reintentos, errores,
redacción de secretos, clave de caché, procedencia y límite de frecuencia entre procesos."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, urlencode

import pytest
import requests

from nutria import http
from nutria.config import Config
from nutria.http import HttpClient, NetworkError, NotFoundError, OfflineError, cache_key, redact
from nutria.provenance import read_events

ROOT = Path(__file__).resolve().parents[1]
URL = "https://api.example.org/works"          # host sin intervalo mínimo: las pruebas no esperan
SECRET = "clave-SECRETA-0123456789"
EMAIL = "persona@example.org"


class FakeResponse:
    def __init__(self, status_code=200, text="{}", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.url = URL


class FakeSession:
    """Sesión sin red: devuelve (o lanza) los elementos programados y registra cada llamada."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def request(self, method, url, params=None, json=None, data=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": dict(params or {}), "json": json,
                           "data": data})
        item = self.responses.pop(0) if self.responses else FakeResponse()
        if isinstance(item, Exception):
            raise item
        item.url = url + ("?" + urlencode(params) if params else "")  # como requests: URL final
        return item


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for name in http.SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)
    return Config(root=tmp_path, cache_dir=tmp_path / "cache", db_path=tmp_path / "research" / "nutria.sqlite")


def cache_files(cfg):
    return sorted(p for p in Path(cfg.cache_dir).rglob("*.json"))


def age_cache(cfg, days):
    """Envejece todas las copias de la caché `days` días."""
    for path in cache_files(cfg):
        record = json.loads(path.read_text("utf-8"))
        record["ts"] -= days * 86400
        path.write_text(json.dumps(record), "utf-8")


# ============================================================================ caché, fresh y offline
def test_cache_write_then_read_logs_http_cache(cfg):
    session = FakeSession(FakeResponse(text='{"n": 1}'))
    client = HttpClient(cfg, session=session)
    assert client.get_json(URL, params={"q": "rumen"}) == {"n": 1}
    assert client.get_json(URL, params={"q": "rumen"}) == {"n": 1}
    assert len(session.calls) == 1 and len(cache_files(cfg)) == 1
    assert [e["action"] for e in read_events(cfg)] == ["http", "http_cache"]


def test_cache_hit_records_copy_date_and_age(cfg):
    HttpClient(cfg, session=FakeSession(FakeResponse(text='{"n": 1}'))).get_json(URL)
    age_cache(cfg, 2)
    session = FakeSession()
    assert HttpClient(cfg, session=session).get_json(URL) == {"n": 1} and not session.calls
    hit = read_events(cfg, action="http_cache")[-1]
    assert hit["outputs"]["age_days"] == pytest.approx(2.0, abs=0.01)
    copy_date = datetime.fromisoformat(hit["outputs"]["cached_at"])
    assert abs(datetime.now(timezone.utc) - timedelta(days=2) - copy_date) < timedelta(minutes=1)
    assert hit["inputs"]["url"] == URL and hit["outputs"]["bytes"] == len('{"n": 1}')


def test_expired_copy_goes_back_to_network(cfg):
    HttpClient(cfg, session=FakeSession(FakeResponse(text='{"v": 1}'))).get_json(URL)
    age_cache(cfg, 2)
    session = FakeSession(FakeResponse(text='{"v": 2}'))
    assert HttpClient(cfg, session=session).get_json(URL, ttl_days=1) == {"v": 2}
    assert len(session.calls) == 1


def test_fresh_never_reads_cache_but_writes_it(cfg):
    HttpClient(cfg, session=FakeSession(FakeResponse(text='{"v": "vieja"}'))).get_json(URL)
    fresh_session = FakeSession(FakeResponse(text='{"v": "nueva"}'))
    fresh = HttpClient(cfg, session=fresh_session, fresh=True)
    assert fresh.fresh is True
    assert fresh.get_json(URL) == {"v": "nueva"} and len(fresh_session.calls) == 1
    assert not read_events(cfg, action="http_cache")
    assert read_events(cfg, action="http")[-1]["inputs"]["fresh"] is True
    normal_session = FakeSession()
    assert HttpClient(cfg, session=normal_session).get_json(URL) == {"v": "nueva"}  # copia actualizada
    assert not normal_session.calls


def test_offline_serves_cache_even_if_expired_and_fails_without_copy(cfg):
    HttpClient(cfg, session=FakeSession(FakeResponse(text='{"a": 1}'))).get_json(URL)
    age_cache(cfg, 400)
    cfg.offline = True
    session = FakeSession()
    assert HttpClient(cfg, session=session).get_json(URL) == {"a": 1}
    with pytest.raises(OfflineError):
        HttpClient(cfg, session=session).get_json(URL, params={"q": "otra"})
    assert not session.calls
    error = read_events(cfg, action="http_error")[-1]
    assert error["outputs"]["type"] == "OfflineError" and error["inputs"]["params"] == {"q": "otra"}


def test_offline_with_fresh_raises_even_with_copy(cfg):
    HttpClient(cfg, session=FakeSession(FakeResponse(text='{"a": 1}'))).get_json(URL)
    cfg.offline = True
    session = FakeSession()
    with pytest.raises(OfflineError) as info:
        HttpClient(cfg, session=session, fresh=True).get_json(URL)
    assert "en línea" in str(info.value) and not session.calls


# ============================================================================ reintentos y errores
def test_retry_after_503_then_success(cfg, monkeypatch):
    sleeps = []
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    session = FakeSession(FakeResponse(503), FakeResponse(429, headers={"Retry-After": "2"}),
                          FakeResponse(text='{"ok": true}'))
    assert HttpClient(cfg, session=session).get_json(URL) == {"ok": True}
    assert len(session.calls) == 3 and sleeps == [1.0, 2.0]


def test_retries_exhausted_without_sleeping_after_last_attempt(cfg, monkeypatch):
    sleeps = []
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    session = FakeSession(*(FakeResponse(503) for _ in range(http.MAX_ATTEMPTS)))
    with pytest.raises(NetworkError) as info:
        HttpClient(cfg, session=session).get_json(URL)
    assert "HTTP 503" in str(info.value) and len(session.calls) == http.MAX_ATTEMPTS
    assert sleeps == [1.0, 2.0, 4.0]
    error = read_events(cfg, action="http_error")[-1]
    assert error["outputs"]["status"] == 503 and error["outputs"]["attempts"] == http.MAX_ATTEMPTS
    assert not cache_files(cfg)


def test_404_raises_not_found_and_is_not_cached(cfg):
    session = FakeSession(FakeResponse(404), FakeResponse(404))
    client = HttpClient(cfg, session=session)
    for _ in range(2):
        with pytest.raises(NotFoundError) as info:
            client.get_json(URL, params={"q": "x"})
        assert isinstance(info.value, NetworkError) and "404" in str(info.value)
    assert len(session.calls) == 2 and not cache_files(cfg)
    error = read_events(cfg, action="http_error")[-1]
    assert error["outputs"]["type"] == "NotFoundError" and error["outputs"]["status"] == 404


def test_invalid_json_is_a_network_error_and_not_cached(cfg):
    session = FakeSession(FakeResponse(text="<html>mantenimiento</html>"))
    with pytest.raises(NetworkError, match="se esperaba JSON"):
        HttpClient(cfg, session=session).get_json(URL)
    assert not cache_files(cfg)


# ============================================================================ redacción de secretos
def test_redact_removes_sensitive_params_and_configured_secrets(monkeypatch):
    for name in http.SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NCBI_API_KEY", "ncbi-123456")
    monkeypatch.setenv("S2_API_KEY", "s2-abcdef")
    cfg = SimpleNamespace(email=EMAIL)
    text = ("GET https://eutils.ncbi.nlm.nih.gov/esearch.fcgi?db=pubmed&api_key=ncbi-123456&email=persona%40"
            "example.org&tool=nutria y /works?mailto=persona@example.org&search=x; cuerpo: s2-abcdef; "
            "Authorization: Bearer tok.en-123 ¿seguro? token=zzz")
    assert redact(text, cfg) == (
        "GET https://eutils.ncbi.nlm.nih.gov/esearch.fcgi?db=pubmed&tool=nutria y /works?search=x; "
        "cuerpo: ***; Authorization: Bearer *** ¿seguro? token=***")
    assert redact(None) == "" and redact("¿Sin URL? Nada que quitar.") == "¿Sin URL? Nada que quitar."
    assert redact("Ver (https://x.org/y?KEY=1&apikey=2).") == "Ver (https://x.org/y)."
    assert redact(redact(text, cfg), cfg) == redact(text, cfg)


def test_redact_masks_every_configured_secret_variable(monkeypatch):
    values = {name: f"valor-{i}-{name.lower()}" for i, name in enumerate(http.SECRET_ENV_VARS)}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    out = redact(" | ".join(values.values()) + f" | {EMAIL} | {quote(EMAIL, safe='')}",
                 SimpleNamespace(email=EMAIL))
    assert not any(value in out for value in values.values())
    assert EMAIL not in out and quote(EMAIL, safe="") not in out


def test_http_errors_are_redacted_in_message_and_provenance(cfg, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", SECRET)
    cfg.email = EMAIL
    body = f'{{"error": "la clave {SECRET} de {EMAIL} no vale"}}'
    session = FakeSession(FakeResponse(403, text=body))
    with pytest.raises(NetworkError) as info:
        HttpClient(cfg, session=session).get_json(URL, params={"search": "rumen", "api_key": SECRET,
                                                                "mailto": EMAIL})
    message = str(info.value)
    assert "HTTP 403" in message and "search=rumen" in message and "no vale" in message
    for leaked in (SECRET, EMAIL, quote(EMAIL, safe=""), "api_key=", "mailto="):
        assert leaked not in message
    provenance = cfg.provenance_path.read_text("utf-8")
    assert SECRET not in provenance and EMAIL not in provenance


def test_connection_and_proxy_errors_are_redacted_without_cause(cfg, monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", SECRET)
    cfg.email = EMAIL
    leaky = (f"HTTPSConnectionPool(host='api.example.org', port=443): Max retries exceeded with url: "
             f"/works?search=rumen&api_key={SECRET}&mailto={quote(EMAIL, safe='')} (Caused by X)")
    session = FakeSession(*(requests.exceptions.ConnectionError(leaky) for _ in range(http.MAX_ATTEMPTS)))
    with pytest.raises(NetworkError) as info:
        HttpClient(cfg, session=session).get_json(URL)
    assert "ConnectionError" in str(info.value) and "search=rumen" in str(info.value)
    assert SECRET not in str(info.value) and "mailto" not in str(info.value)
    session = FakeSession(requests.exceptions.ProxyError(leaky))
    with pytest.raises(NetworkError) as info:
        HttpClient(cfg, session=session).get_json(URL)
    assert "proxy" in str(info.value) and SECRET not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert SECRET not in cfg.provenance_path.read_text("utf-8")


def test_network_error_redacts_on_creation(monkeypatch):
    monkeypatch.setenv("LENS_API_TOKEN", SECRET)
    exc = NetworkError(f"fallo en https://api.lens.org/x?token={SECRET}&q=1 con Bearer {SECRET}")
    assert SECRET not in str(exc) and "?q=1" in str(exc)
    assert SECRET not in str(NotFoundError(f"no existe {SECRET}"))
    assert EMAIL not in str(OfflineError(f"sin copia de {EMAIL}", cfg=SimpleNamespace(email=EMAIL)))


# ============================================================================ clave de caché
def test_cache_key_does_not_depend_on_secrets_or_identity():
    base = cache_key("GET", URL, {"q": "x"})
    assert base == cache_key("GET", URL, {"q": "x", "api_key": "A", "mailto": "a@b.org", "tool": "nutria"})
    assert base == cache_key("get", URL, {"q": "x", "API_KEY": "B", "email": "c@d.org", "token": "t",
                                          "key": "k", "apikey": "z", "caller_identity": "otro", "nada": None})
    assert cache_key("GET", URL + "?q=x") == cache_key("GET", URL + "?api_key=A&q=x&mailto=a%40b.org")
    assert base != cache_key("GET", URL, {"q": "y"})
    assert base != cache_key("POST", URL, {"q": "x"})


def test_cache_is_shared_across_keys_and_stores_no_secret(cfg, monkeypatch):
    cfg.email = EMAIL
    monkeypatch.setenv("NCBI_API_KEY", SECRET)
    first = FakeSession(FakeResponse(text='{"ids": [1]}'))
    HttpClient(cfg, session=first).get_json(URL, params={"term": "rumen", "api_key": SECRET, "email": EMAIL,
                                                         "tool": "nutria"})
    assert first.calls[0]["params"]["api_key"] == SECRET  # la clave sí viaja en la petición
    monkeypatch.setenv("NCBI_API_KEY", "otra-clave-distinta")
    second = FakeSession()
    assert HttpClient(cfg, session=second).get_json(
        URL, params={"term": "rumen", "api_key": "otra-clave-distinta", "tool": "otra"}) == {"ids": [1]}
    assert not second.calls
    (stored,) = cache_files(cfg)
    content = stored.read_text("utf-8")
    assert SECRET not in stored.name and SECRET not in content and EMAIL not in content
    assert json.loads(content)["params"] == {"term": "rumen"}


# ============================================================================ límite de frecuencia
def test_ratelimit_state_is_shared_through_the_lock_file(cfg, monkeypatch):
    if http.fcntl is None:
        pytest.skip("sin fcntl (Windows): el límite solo se aplica dentro del proceso")
    monkeypatch.setitem(http.MIN_INTERVAL, "limite.example", 0.5)
    lock = cfg.cache_dir / "ratelimit" / "limite.example.lock"
    assert http.ratelimit_path(cfg, "limite.example") == lock
    lock.parent.mkdir(parents=True)
    previous = time.time()
    lock.write_text(f"{previous:.6f}")              # otro proceso acaba de llamar a ese host
    sleeps = []
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    HttpClient(cfg, session=FakeSession())._wait("limite.example")
    assert len(sleeps) == 1 and 0.4 < sleeps[0] <= 0.5
    assert float(lock.read_text()) >= previous
    lock.write_text(f"{time.time() + 3600:.6f}")    # reloj adelantado: nunca más de un intervalo
    HttpClient(cfg, session=FakeSession())._wait("limite.example")
    assert sleeps[-1] <= 0.5
    lock.write_bytes(b"\xff\xfe basura")            # fichero dañado: se ignora y se reescribe
    HttpClient(cfg, session=FakeSession())._wait("limite.example")
    assert float(lock.read_text()) > 0


def test_ratelimit_without_fcntl_applies_within_the_process(cfg, monkeypatch):
    monkeypatch.setattr(http, "fcntl", None)
    monkeypatch.setitem(http.MIN_INTERVAL, "limite.example", 0.5)
    sleeps = []
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    client = HttpClient(cfg, session=FakeSession())
    client._wait("limite.example")
    client._wait("limite.example")
    assert len(sleeps) == 1 and sleeps[0] > 0.4
    assert not (cfg.cache_dir / "ratelimit").exists()


_RATE_SCRIPT = """
import sys, time
from pathlib import Path
from nutria import http
from nutria.config import Config
cache, start, n = Path(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])
http.MIN_INTERVAL["limite.example"] = 0.1
client = http.HttpClient(Config(root=cache, cache_dir=cache))
while time.time() < start:
    time.sleep(0.005)
t0 = time.time()
for _ in range(n):
    client._wait("limite.example")
print(t0, time.time())
"""


def test_ratelimit_is_respected_between_processes(tmp_path):
    if http.fcntl is None:
        pytest.skip("sin fcntl (Windows): el límite solo se aplica dentro del proceso")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT / "src"),
                                                                     os.environ.get("PYTHONPATH")]))}
    start = time.time() + 1.0
    procs = [subprocess.Popen([sys.executable, "-c", _RATE_SCRIPT, str(tmp_path), repr(start), "3"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
             for _ in range(2)]
    spans = []
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err
        spans.append(tuple(map(float, out.split())))
    # 6 llamadas en serie (2 procesos x 3): al menos 5 intervalos de 0,1 s entre la primera y la última.
    assert max(end for _, end in spans) - min(begin for begin, _ in spans) >= 0.45
