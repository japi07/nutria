"""Pruebas sin red de los clientes de fuentes, la caché, el modo sin red, `doctor`, los errores y los códigos de
salida.

Cubren D7-4 (límites de Crossref entre hilos y procesos), D7-7 (procedencia de `buscar`: identificadores y total),
D7-10 y D9-8 (caché sin red, fecha de cada copia, antigüedad máxima, exportar e importar con huella), D3-16
(envenenamiento de la caché: cuerpo, huella, clave, cabeceras, fechas, importación y respuestas que no superan la
validación), D7-11 (dianas por símbolo de gen y estrategias independientes), D7-12 y D9-12 (clientes de STRING,
Reactome, Lens, PubChem, ChEMBL, UniProt, EUR-Lex y router de modelos con sus tres proveedores; recursos cerrados;
fichero de restricciones), D7-13 (paginación de PubMed, Europe PMC y OpenAlex; avisos de las API), D7-14, D10-12 y
D9-16 (no encontrado, errores etiquetados por su causa, doctor con y sin red y su comprobación del run_hook.sh
instalado, GHS, router sin red, init con NUTRIA_HOME), D7-15 (versiones de las bases), D10-8 y D9-4 (errores en
JSON, tabla única de códigos, «no existe» coherente, OSError), D5-15 (EUR-Lex) y las partes de D1-11
(confidencialidad de consultas) y D10-1 (ningún mensaje recomienda --offline).
Toda la red está simulada: no prueban que los servicios reales respondan así (eso es tests/test_vivo.py).
Las pruebas que tocan órdenes de otros paquetes (fuente, verificar, oportunidad...) solo comprueban el contrato de
errores y códigos, no sus mensajes.
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import pytest
import requests

from nutria import cli, http
from nutria.cli import main
from nutria.config import Config, load_config
from nutria.http import HttpClient, NetworkError, NotFoundError, OfflineError, cache_key
from nutria.provenance import read_events
from nutria.sources import chembl, europepmc, eurlex, patents, pubchem, pubmed, reactome, stringdb, uniprot
from nutria.sources.base import collect_pages

pytestmark = pytest.mark.filterwarnings("error::ResourceWarning")

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "ejemplos"
REAL_SLEEP = time.sleep
SECRET = "clave-SECRETA-0123456789"


# ============================================================================ red simulada
class Resp:
    def __init__(self, status=200, body="{}", headers=None, url=""):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.headers = headers or {}
        self.url = url


class Router:
    """Red simulada: cada ruta (expresión regular sobre la URL completa) devuelve (estado, cuerpo[, cabeceras]),
    una excepción o el resultado de una función. Lo que no tiene ruta está «bloqueado por el proxy»."""

    def __init__(self):
        self.routes = []
        self.calls = []

    def add(self, pattern, response):
        self.routes.insert(0, (re.compile(pattern), response))

    def request(self, method, url, params=None, json=None, data=None, headers=None, timeout=None, **kw):
        full = url + ("?" + urlencode(params, doseq=True) if params else "")
        self.calls.append({"method": method, "url": full, "params": dict(params or {}), "json": json, "data": data,
                           "headers": dict(headers or {})})
        for rx, response in self.routes:
            if rx.search(full):
                res = response(method, full) if callable(response) else response
                if isinstance(res, Exception):
                    raise res
                return Resp(res[0], res[1], res[2] if len(res) > 2 else None, full)
        raise requests.exceptions.ProxyError(f"Unable to connect to proxy: blocked {full}")


class Session:
    """Sesión inyectable en HttpClient (no posee recursos)."""

    def __init__(self, router):
        self.router = router
        self.headers = {}

    def request(self, method, url, **kw):
        return self.router.request(method, url, **kw)


@pytest.fixture
def router(monkeypatch):
    net = Router()
    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, **kw: net.request(method, url, **kw))
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)
    return net


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for name in http.SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)
    return Config(root=tmp_path, cache_dir=tmp_path / "cache", db_path=tmp_path / "research" / "nutria.sqlite")


@pytest.fixture
def proj(tmp_path, monkeypatch, router):
    """Proyecto inicializado CON red simulada (sin NUTRIA_OFFLINE, a diferencia de `project`)."""
    for name in (*http.SECRET_ENV_VARS, "NUTRIA_HOME", "NUTRIA_OFFLINE", "NUTRIA_AGENT", "NUTRIA_DEPURAR"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "proyecto"
    root.mkdir()
    monkeypatch.chdir(root)
    assert main(["init"]) == 0
    return root


def run(capsys, argv):
    """Ejecuta la CLI: (código, stdout, stderr). Un SystemExit cuenta como su código."""
    capsys.readouterr()
    try:
        code = main(argv)
    except SystemExit as exc:
        code = exc.code
    out, err = capsys.readouterr()
    return code, out, err


def events(root, action=None):
    path = Path(root) / "research" / "procedencia.jsonl"
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()] if path.is_file() else []
    return [e for e in rows if action is None or e["action"] == action]


def age_provenance(provenance: Path, days: float):
    """Envejece `days` días las respuestas registradas (la fecha de una copia es la de su evento `http`)."""
    lines = []
    for line in provenance.read_text("utf-8").splitlines():
        event = json.loads(line)
        if event["action"] in ("http", "cache_importar"):
            event["ts"] = (datetime.fromisoformat(event["ts"]) - timedelta(days=days)).isoformat(timespec="seconds")
        lines.append(json.dumps(event, ensure_ascii=False))
    provenance.write_text("\n".join(lines) + "\n", "utf-8")


def cache_files(cache_dir):
    return sorted(p for p in Path(cache_dir).rglob("*.json") if "ratelimit" not in p.parts)


def esummary(pmid, doi=None, title="Rumen-protected choline in dairy cows"):
    ids = [{"idtype": "pubmed", "value": pmid}] + ([{"idtype": "doi", "value": doi}] if doi else [])
    return {"result": {"uids": [pmid], pmid: {"uid": pmid, "title": title, "pubdate": "2019",
                                               "fulljournalname": "Journal of Dairy Science",
                                               "authors": [{"name": "Smith J", "authtype": "Author"}],
                                               "articleids": ids, "pubtype": ["Journal Article"]}}}


def pubmed_routes(net, pmid="111", doi="10.1000/abc"):
    net.add(r"esearch\.fcgi", (200, {"esearchresult": {"count": "1", "idlist": [pmid]}}))
    net.add(r"esummary\.fcgi", (200, esummary(pmid, doi)))


URL = "https://api.example.org/works"


# ============================================================================ caché: integridad (D3-16, D3-9)
def test_cache_entry_keeps_key_hash_and_is_confirmed_by_provenance(cfg):
    net = Router()
    net.add(r"api\.example\.org", (200, '{"n": 1}'))
    assert HttpClient(cfg, session=Session(net)).get_json(URL, params={"q": "rumen"}) == {"n": 1}
    (stored,) = cache_files(cfg.cache_dir)
    record = json.loads(stored.read_text("utf-8"))
    key = cache_key("GET", URL, {"q": "rumen"})
    assert record["formato"] == http.CACHE_FORMAT and record["clave"] == key == stored.stem
    assert record["sha256"] == hashlib.sha256(b'{"n": 1}').hexdigest()
    (event,) = read_events(cfg, action="http")
    assert event["outputs"]["clave_cache"] == key and event["outputs"]["sha256"] == record["sha256"]
    client = HttpClient(cfg, session=Session(net))
    assert client.get_json(URL, params={"q": "rumen"}) == {"n": 1} and len(net.calls) == 1
    assert client.cache_hits[0]["sha256"] == record["sha256"] and client.cache_hits[0]["origen"] == "red"


def _poison(cfg, recompute_hash: bool):
    for path in cache_files(cfg.cache_dir):
        record = json.loads(path.read_text("utf-8"))
        record["body"] = record["body"].replace("verdad", "VENENO")
        if recompute_hash:
            record["sha256"] = hashlib.sha256(record["body"].encode()).hexdigest()
        path.write_text(json.dumps(record), "utf-8")


@pytest.mark.parametrize("recompute_hash, reason", [(False, "huella SHA-256 no coincide"),
                                                    (True, "procedencia no registra")])
def test_poisoned_cache_entry_is_discarded_with_warning(cfg, recompute_hash, reason):
    net = Router()
    net.add(r"api\.example\.org", (200, '{"dato": "verdad"}'))
    HttpClient(cfg, session=Session(net)).get_json(URL)
    _poison(cfg, recompute_hash)
    client = HttpClient(cfg, session=Session(net))
    assert client.get_json(URL) == {"dato": "verdad"}            # con red: se descarta y se vuelve a pedir
    assert len(net.calls) == 2 and reason in client.avisos[0]
    assert reason in read_events(cfg, action="http_cache_descartada")[-1]["outputs"]["motivo"]
    _poison(cfg, recompute_hash)
    cfg.offline = True
    with pytest.raises(OfflineError, match=reason):               # sin red: falla, nunca sirve el veneno
        HttpClient(cfg, session=Session(net)).get_json(URL)
    assert len(net.calls) == 2


def test_planted_entry_under_another_key_and_legacy_entry_are_rejected(cfg):
    net = Router()
    net.add(r"api\.example\.org", (200, '{"dato": "verdad"}'))
    HttpClient(cfg, session=Session(net)).get_json(URL, params={"q": "a"})
    (valid,) = cache_files(cfg.cache_dir)
    other = cache_key("GET", URL, {"q": "b"})
    target = cfg.cache_dir / other[:2] / f"{other}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(valid.read_text("utf-8"), "utf-8")         # copia válida plantada en otra clave
    cfg.offline = True
    with pytest.raises(OfflineError, match="clave guardada no corresponde"):
        HttpClient(cfg, session=Session(net)).get_json(URL, params={"q": "b"})
    legacy = {"ts": time.time(), "method": "GET", "url": URL, "params": {"q": "b"}, "body": '{"dato": 1}'}
    target.write_text(json.dumps(legacy), "utf-8")               # formato anterior, sin huella
    with pytest.raises(OfflineError, match="sin huella SHA-256"):
        HttpClient(cfg, session=Session(net)).get_json(URL, params={"q": "b"})


def test_copy_age_is_the_oldest_of_the_file_and_provenance_dates(cfg):
    net = Router()
    net.add(r"api\.example\.org", (200, '{"a": 1}'))
    HttpClient(cfg, session=Session(net)).get_json(URL)
    age_provenance(cfg.provenance_path, 300)
    for path in cache_files(cfg.cache_dir):                      # el fichero dice «recién descargada»
        record = json.loads(path.read_text("utf-8"))
        record["ts"] = time.time()
        path.write_text(json.dumps(record), "utf-8")
    cfg.offline = True
    with pytest.raises(OfflineError, match="300 días"):
        HttpClient(cfg, session=Session(net)).get_json(URL)
    age_provenance(cfg.provenance_path, -300)                    # procedencia «de hoy», fichero de hace 400 días
    for path in cache_files(cfg.cache_dir):
        record = json.loads(path.read_text("utf-8"))
        record["ts"] = time.time() - 400 * 86400
        path.write_text(json.dumps(record), "utf-8")
    with pytest.raises(OfflineError, match="400 días"):
        HttpClient(cfg, session=Session(net)).get_json(URL)
    for path in cache_files(cfg.cache_dir):
        record = json.loads(path.read_text("utf-8"))
        record["ts"] = "ayer"
        path.write_text(json.dumps(record), "utf-8")
    with pytest.raises(OfflineError, match="fecha válida"):
        HttpClient(cfg, session=Session(net)).get_json(URL)


def test_confirmation_with_a_future_date_does_not_count(cfg):
    net = Router()
    net.add(r"api\.example\.org", (200, '{"a": 1}'))
    HttpClient(cfg, session=Session(net)).get_json(URL)
    age_provenance(cfg.provenance_path, -30)                     # evento «del futuro»
    cfg.offline = True
    with pytest.raises(OfflineError, match="procedencia no registra"):
        HttpClient(cfg, session=Session(net)).get_json(URL)


def test_confirmation_written_by_another_process_is_seen(cfg):
    net = Router()
    net.add(r"api\.example\.org", (200, '{"a": 1}'))
    reader = HttpClient(cfg, session=Session(net))
    with pytest.raises(NetworkError):
        reader.get_json("https://bloqueado.example.org/x")       # carga el índice (vacío)
    HttpClient(cfg, session=Session(net)).get_json(URL)          # «otra orden» registra la respuesta
    assert reader.get_json(URL) == {"a": 1} and len(net.calls) == 2


def test_tampered_cached_headers_are_discarded(cfg):
    # D3-16, variante: la versión de UniProt viaja en las cabeceras de la copia; cambiarla (sin tocar el cuerpo)
    # no debe colar una versión falsa
    net = Router()
    net.add(r"rest\.uniprot\.org", (200, {"results": []}, {"X-UniProt-Release": "2025_04"}))
    uniprot.search(HttpClient(cfg, session=Session(net)), "gene:PPARA")
    (path,) = cache_files(cfg.cache_dir)
    record = json.loads(path.read_text("utf-8"))
    assert record["cabeceras"] == {"x-uniprot-release": "2025_04"}
    record["cabeceras"]["x-uniprot-release"] = "2099_01"
    path.write_text(json.dumps(record), "utf-8")
    cfg.offline = True
    with pytest.raises(OfflineError, match="procedencia no registra"):
        uniprot.search(HttpClient(cfg, session=Session(net)), "gene:PPARA")
    record["cabeceras"] = "x"
    path.write_text(json.dumps(record), "utf-8")
    with pytest.raises(OfflineError, match="cabeceras con formato no válido"):
        uniprot.search(HttpClient(cfg, session=Session(net)), "gene:PPARA")


def test_confidential_mark_is_sticky_and_export_skips_entries_that_could_not_be_imported(cfg, tmp_path):
    net = Router()
    net.add(r"api\.example\.org", (200, '{"a": 1}'))
    HttpClient(cfg, session=Session(net), confidential=True).get_json(URL, params={"q": "idea"}, use_cache=True)
    HttpClient(cfg, session=Session(net), fresh=True).get_json(URL, params={"q": "idea"})   # misma consulta, normal
    (path,) = cache_files(cfg.cache_dir)
    assert json.loads(path.read_text("utf-8"))["confidencial"] is True
    res = http.export_cache(HttpClient(cfg, session=Session(net)), tmp_path / "a.json")
    assert res["entradas"] == 0 and res["confidenciales_omitidas"] == 1
    HttpClient(cfg, session=Session(net)).get_json(URL, params={"q": "otra"})
    for path in cache_files(cfg.cache_dir):                       # metadatos de la petición alterados
        record = json.loads(path.read_text("utf-8"))
        record["url"] = "https://api.example.org/otra-ruta"
        path.write_text(json.dumps(record), "utf-8")
    res = http.export_cache(HttpClient(cfg, session=Session(net)), tmp_path / "b.json", include_confidential=True)
    assert res["entradas"] == 0
    assert res["no_exportadas"] == {"no se podría importar: la clave no corresponde a la petición declarada": 2}


# ============================================================================ sin red: fecha y antigüedad (D7-10)
def test_offline_cli_reports_copy_dates_and_enforces_the_maximum_age(proj, router, capsys, monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "k1")
    pubmed_routes(router)
    assert run(capsys, ["buscar", "pubmed", "rumen choline"])[0] == 0
    monkeypatch.delenv("NCBI_API_KEY")                           # la caché no depende de la clave (D9-8)
    code, out, _ = run(capsys, ["--offline", "buscar", "pubmed", "rumen choline"])
    assert code == 0 and "Modo offline: datos de la caché" in out and "copia del " in out
    age_provenance(proj / "research" / "procedencia.jsonl", 400)
    code, out, err = run(capsys, ["--offline", "buscar", "pubmed", "rumen choline"])
    assert code == cli.SALIDA_RED and "más antigua que el máximo de 180 días" in err and "--antiguedad-maxima" in err
    code, out, _ = run(capsys, ["--offline", "--antiguedad-maxima", "500", "--json", "buscar", "pubmed",
                                "rumen choline"])
    data = json.loads(out)
    assert code == 0 and data["cache"] and all(round(c["antiguedad_dias"]) == 400 for c in data["cache"])
    assert all(c["fecha"].startswith(str(datetime.now(timezone.utc).year - 1)) or c["fecha"] for c in data["cache"])
    hits = events(proj, "http_cache")
    assert hits and round(hits[-1]["outputs"]["age_days"]) == 400
    assert run(capsys, ["--offline", "--antiguedad-maxima", "-3", "buscar", "pubmed", "x"])[0] == cli.SALIDA_ENTRADA
    (proj / "nutria.toml").write_text((proj / "nutria.toml").read_text("utf-8").replace(
        "antiguedad_maxima_offline_dias = 180", "antiguedad_maxima_offline_dias = 1000"), "utf-8")
    assert load_config().cache_max_offline_days == 1000
    assert run(capsys, ["--offline", "buscar", "pubmed", "rumen choline"])[0] == 0


# ============================================================================ exportar e importar la caché (D9-8)
def _fill_cache(router, capsys, monkeypatch):
    pubmed_routes(router)
    assert run(capsys, ["buscar", "pubmed", "rumen choline"])[0] == 0
    router.add(r"reactome\.org/AnalysisService", (200, {"summary": {"token": "T1"}, "pathways": [
        {"stId": "R-HSA-1", "name": "Fatty acid metabolism", "entities": {"fdr": 0.01, "pValue": 0.001,
                                                                           "found": 2, "total": 50}}]}))
    router.add(r"reactome\.org/ContentService/data/database/version", (200, "88"))
    assert run(capsys, ["rutas", "analizar", "PPARA", "CPT1A"])[0] == 0
    monkeypatch.setenv("LENS_API_TOKEN", SECRET)
    router.add(r"api\.lens\.org", (200, {"total": 1, "data": [{"lens_id": "L-1", "jurisdiction": "EP"}]}))
    assert run(capsys, ["patentes", "choline carnitine", "--lens"])[0] == 0
    monkeypatch.delenv("LENS_API_TOKEN")


def test_cache_export_and_import_reproduce_queries_offline_on_another_machine(proj, router, capsys, monkeypatch,
                                                                              tmp_path):
    _fill_cache(router, capsys, monkeypatch)
    bundle = tmp_path / "cache.json"
    code, out, _ = run(capsys, ["cache", "exportar", str(bundle)])
    assert code == 0 and "en claro" in out and "No exportadas 1 copias de consultas confidenciales" in out
    data = json.loads(bundle.read_text("utf-8"))
    assert data["formato"] == http.EXPORT_FORMAT and len(data["entradas"]) == 4      # sin la consulta de patentes
    assert "choline carnitine" not in bundle.read_text("utf-8")
    assert run(capsys, ["cache", "exportar", str(bundle)])[0] == cli.SALIDA_ENTRADA      # no sobrescribe
    code, out, _ = run(capsys, ["cache", "exportar", str(bundle), "--forzar", "--incluir-confidenciales"])
    assert code == 0 and "1 consulta(s) CONFIDENCIAL(ES)" in out
    data = json.loads(bundle.read_text("utf-8"))
    assert len(data["entradas"]) == 5 and sum(e["confidencial"] for e in data["entradas"]) == 1
    assert SECRET not in bundle.read_text("utf-8")
    assert {e["method"] for e in data["entradas"]} == {"GET", "POST"}
    assert events(proj, "cache_exportar")[-1]["outputs"]["entradas"] == 5
    other = tmp_path / "otra_maquina"
    other.mkdir()
    monkeypatch.chdir(other)
    assert main(["init"]) == 0
    age_provenance(proj / "research" / "procedencia.jsonl", 0)
    code, out, _ = run(capsys, ["cache", "importar", str(bundle)])
    assert code == 0 and "Importadas 5" in out
    assert f"Fechas que declara la exportación (del {data['exportado']}): de " in out
    assert "no puede comprobar cuándo se obtuvo cada respuesta" in out
    imported = events(other, "cache_importar")[-1]
    assert len(imported["outputs"]["entradas"]) == 5 and imported["inputs"]["sha256"]
    code, out, _ = run(capsys, ["--offline", "--json", "buscar", "pubmed", "rumen choline"])
    result = json.loads(out)
    assert code == 0 and result["total"] == 1 and {c["origen"] for c in result["cache"]} == {"importada"}
    original = {e["sha256"]: e["fecha"] for e in data["entradas"]}
    assert all(original[c["sha256"]] == c["fecha"] for c in result["cache"])  # conservan fecha y huella
    assert run(capsys, ["--offline", "rutas", "analizar", "PPARA", "CPT1A"])[0] == 0
    code, out, _ = run(capsys, ["cache", "importar", str(bundle)])           # repetir: se conservan las locales
    assert code == 0 and "5 copias locales" in out
    code, out, _ = run(capsys, ["--json", "cache", "comprobar"])
    assert code == 0 and json.loads(out)["validas"] == 5 and json.loads(out)["confidenciales"] == 1
    again = tmp_path / "de_nuevo.json"                     # la marca de confidencialidad viaja con la copia
    assert run(capsys, ["cache", "exportar", str(again)])[0] == 0
    assert len(json.loads(again.read_text("utf-8"))["entradas"]) == 4


@pytest.mark.parametrize("change, reason", [
    (lambda e: e.update(body=e["body"] + " "), "huella SHA-256 no coincide"),
    (lambda e: e.update(url=e["url"] + "/otra"), "clave no corresponde"),
    (lambda e: e.update(fecha="2999-01-01T00:00:00+00:00"), "fecha futura"),
    (lambda e: e.update(fecha="ayer"), "fecha no válida"),
    (lambda e: e.pop("sha256"), "falta 'sha256'"),
])
def test_cache_import_rejects_altered_entries(proj, router, capsys, monkeypatch, tmp_path, change, reason):
    pubmed_routes(router)
    assert run(capsys, ["buscar", "pubmed", "rumen choline"])[0] == 0
    bundle = tmp_path / "cache.json"
    assert run(capsys, ["cache", "exportar", str(bundle)])[0] == 0
    data = json.loads(bundle.read_text("utf-8"))
    change(data["entradas"][0])
    bundle.write_text(json.dumps(data), "utf-8")
    for path in cache_files(proj / ".cache"):
        path.unlink()
    code, out, _ = run(capsys, ["cache", "importar", str(bundle)])
    assert code == cli.SALIDA_RESULTADO and "RECHAZADA entrada 0" in out and reason in out
    assert "Importadas 1" in out


def test_cache_import_checks_declared_dates_against_the_export_date(proj, router, capsys, tmp_path):
    # D9-8: las fechas de una exportación las declara el fichero; al menos tienen que cuadrar con su fecha de
    # exportación (una copia no puede ser posterior), y sin fecha de exportación no se importa nada.
    pubmed_routes(router)
    assert run(capsys, ["buscar", "pubmed", "rumen choline"])[0] == 0
    bundle = tmp_path / "cache.json"
    assert run(capsys, ["cache", "exportar", str(bundle)])[0] == 0
    data = json.loads(bundle.read_text("utf-8"))
    data["exportado"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    bundle.write_text(json.dumps(data), "utf-8")
    for path in cache_files(proj / ".cache"):
        path.unlink()
    code, out, _ = run(capsys, ["--json", "cache", "importar", str(bundle)])
    res = json.loads(out)
    assert code == cli.SALIDA_RESULTADO and res["importadas"] == 0
    assert {r["motivo"] for r in res["rechazadas"]} == {"fecha posterior a la de la exportación"}
    for value in (None, "ayer"):
        data["exportado"] = value
        bundle.write_text(json.dumps(data), "utf-8")
        code, out, _ = run(capsys, ["--json", "cache", "importar", str(bundle)])
        assert code == cli.SALIDA_ENTRADA and "no indica una fecha de exportación válida" in json.loads(out)["error"]
    # Una fecha válida pero futura (reloj desajustado o fichero alterado) se explica como tal, no como «no válida».
    for future in ("2999-01-01T00:00:00+00:00", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()):
        data["exportado"] = future
        bundle.write_text(json.dumps(data), "utf-8")
        code, out, err = run(capsys, ["--json", "cache", "importar", str(bundle)])
        message = json.loads(out)["error"]
        assert code == cli.SALIDA_ENTRADA and "es posterior a la hora actual de esta máquina" in message
        assert "reloj" in message and "no indica una fecha" not in message and message in err
    assert not cache_files(proj / ".cache")


def test_cache_import_rejects_a_file_that_is_not_an_export(proj, capsys, tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({"formato": "otro", "entradas": []}), "utf-8")
    code, out, _ = run(capsys, ["--json", "cache", "importar", str(bad)])
    assert code == cli.SALIDA_ENTRADA and json.loads(out)["tipo"] == "entrada"


def test_cache_check_lists_invalid_copies(proj, router, capsys):
    pubmed_routes(router)
    assert run(capsys, ["buscar", "pubmed", "rumen choline"])[0] == 0
    path = cache_files(proj / ".cache")[0]
    record = json.loads(path.read_text("utf-8"))
    record["body"] += " "
    path.write_text(json.dumps(record), "utf-8")
    code, out, _ = run(capsys, ["cache", "comprobar"])
    assert code == cli.SALIDA_RESULTADO and "Copias válidas: 1" in out and "huella SHA-256 no coincide" in out


# ============================================================================ límites de uso (D7-4)
def test_crossref_limits_follow_the_public_and_polite_pools(cfg):
    client = HttpClient(cfg, session=Session(Router()))
    assert client._interval("api.crossref.org") == pytest.approx(0.2)        # 5 peticiones/s
    assert client._concurrency("api.crossref.org") == 1
    cfg.email = "persona@example.org"
    assert client._interval("api.crossref.org") == pytest.approx(0.1)        # pool «polite»: 10/s
    assert client._concurrency("api.crossref.org") == 3
    assert client._interval("version-12-0.string-db.org") == pytest.approx(1.0)  # subdominio de STRING
    assert client._concurrency("api.openalex.org") == 0
    assert "no comprobada en vivo" in Path(http.__file__).read_text("utf-8")


def _max_overlap(spans):
    points = sorted([(start, 1) for start, _ in spans] + [(end, -1) for _, end in spans], key=lambda p: (p[0], p[1]))
    active = peak = 0
    for _, delta in points:
        active += delta
        peak = max(peak, active)
    return peak


@pytest.mark.parametrize("email, threads, peak", [("", 4, 1), ("persona@example.org", 6, 3)])
def test_crossref_concurrency_is_limited_between_threads(cfg, email, threads, peak):
    cfg.email = email
    spans, lock = [], threading.Lock()

    class Slow(Session):
        def request(self, method, url, **kw):
            start = time.monotonic()
            REAL_SLEEP(0.15)
            with lock:
                spans.append((start, time.monotonic()))
            return Resp(200, '{"message": {}}', url=url)

    barrier = threading.Barrier(threads)

    def work(i):
        client = HttpClient(cfg, session=Slow(Router()))
        barrier.wait(30)                       # todos a la vez: el máximo de solapamiento lo fija el límite
        client.get_json("https://api.crossref.org/works", params={"i": i}, use_cache=False)

    workers = [threading.Thread(target=work, args=(i,)) for i in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(60)
    assert len(spans) == threads and _max_overlap(spans) == peak


_CROSSREF_WORKER = """
import json, sys, time
from pathlib import Path
from nutria.config import Config
from nutria.http import HttpClient
class R:
    def __init__(s, url): s.status_code = 200; s.text = '{"message": {}}'; s.headers = {}; s.url = url
class S:
    headers = {}
    def request(s, method, url, **kw):
        start = time.time(); time.sleep(0.05); print(json.dumps([start, time.time()]), flush=True); return R(url)
root, start = Path(sys.argv[1]), float(sys.argv[2])
while time.time() < start:
    time.sleep(0.002)
client = HttpClient(Config(root=root, cache_dir=root / "cache"), session=S())
for i in range(3):
    client.get_json("https://api.crossref.org/works", params={"i": i, "p": sys.argv[3]}, use_cache=False)
"""


def test_crossref_concurrency_and_rate_are_limited_between_processes(tmp_path):
    if http.fcntl is None:
        pytest.skip("sin fcntl (Windows): los límites solo se aplican dentro del proceso")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT / "src"), os.environ.get("PYTHONPATH")]))}
    start = time.time() + 1.0
    procs = [subprocess.Popen([sys.executable, "-c", _CROSSREF_WORKER, str(tmp_path), repr(start), str(k)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) for k in range(3)]
    spans = []
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err
        spans += [tuple(json.loads(line)) for line in out.splitlines() if line.strip()]
    spans.sort()
    assert len(spans) == 9 and _max_overlap(spans) == 1                    # concurrencia 1 entre procesos
    starts = [s for s, _ in spans]
    assert min(b - a for a, b in zip(starts, starts[1:])) >= 0.19          # 5 peticiones/s como máximo


def test_busy_slot_fails_closed_and_slot_is_released_after_errors(cfg, monkeypatch):
    if http.fcntl is None:
        pytest.skip("sin fcntl")
    import fcntl
    monkeypatch.setattr(http, "CONCURRENCY_WAIT_S", 0.2)
    cfg.timeout = 0.01
    net = Router()
    net.add(r"crossref", requests.exceptions.ConnectionError("caída"))
    with pytest.raises(NetworkError):
        HttpClient(cfg, session=Session(net)).get_json("https://api.crossref.org/works", max_attempts=1)
    slot = cfg.cache_dir / "ratelimit" / "api.crossref.org.turno0.lock"
    with open(slot, "a+") as held:                                          # el turno quedó libre
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(NetworkError, match="Sin turno libre") as info:  # otro proceso lo ocupa
            HttpClient(cfg, session=Session(net)).get_json("https://api.crossref.org/works", params={"x": 1})
        assert info.value.motivo == "limite"
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
    assert read_events(cfg, action="http_error")[-1]["outputs"]["motivo"] == "limite"


# ============================================================================ PubMed, Europe PMC, OpenAlex (D7-13)
class FakeClient:
    """Cliente sin red para los módulos de fuentes: `handler(url, params, kw)` devuelve la respuesta."""

    def __init__(self, cfg, handler):
        self.cfg = cfg
        self.handler = handler
        self.calls = []
        self.response_headers = {}

    def get_json(self, url, params=None, **kw):
        self.calls.append((url, dict(params or {})))
        result = self.handler(url, dict(params or {}), kw)
        if isinstance(result, Exception):
            raise result
        return result

    get_text = get_json

    def request(self, method, url, params=None, **kw):
        return self.get_json(url, params=params, **kw)


def test_pubmed_paginates_with_retstart_and_batches_summaries(cfg, monkeypatch):
    monkeypatch.setattr(pubmed, "ESEARCH_PAGE", 3)
    monkeypatch.setattr(pubmed, "ESUMMARY_BATCH", 4)
    ids = [str(100 + i) for i in range(10)]

    def handler(url, params, kw):
        if url.endswith("esearch.fcgi"):
            start = int(params.get("retstart", 0))
            return {"esearchresult": {"count": "10", "idlist": ids[start:start + int(params["retmax"])]}}
        return {"result": {"uids": params["id"].split(","),
                           **{i: {"uid": i, "title": f"T{i}", "articleids": [], "pubtype": []}
                              for i in params["id"].split(",")}}}

    client = FakeClient(cfg, handler)
    res = pubmed.search_records(client, "rumen", retmax=7)
    searches = [p for u, p in client.calls if u.endswith("esearch.fcgi")]
    assert [(p.get("retstart", 0), p["retmax"]) for p in searches] == [(0, 3), (3, 3), (6, 1)]
    assert [len(p["id"].split(",")) for u, p in client.calls if u.endswith("esummary.fcgi")] == [4, 3]
    assert [r.pmid for r in res["records"]] == ids[:7] and res["paginas"] == 3 and not res["incompleto"]


def test_pubmed_reports_errorlist_warninglist_and_error(cfg):
    result = {"count": "0", "idlist": [], "errorlist": {"phrasesnotfound": ["zzqq rumen"], "fieldsnotfound": []},
              "warninglist": {"phrasesignored": ["the"], "outputmessages": ["No items found."]}}
    res = pubmed.search_records(FakeClient(cfg, lambda u, p, k: {"esearchresult": result}), '"zzqq rumen"')
    assert res["avisos"] == ["PubMed (error, frases no encontradas): zzqq rumen",
                             "PubMed (aviso, frases ignoradas): the", "PubMed (aviso, mensajes): No items found."]
    assert not res["incompleto"]
    res = pubmed.search_records(FakeClient(cfg, lambda u, p, k: {"esearchresult": {"ERROR": "Invalid query"}}), "(")
    assert res["incompleto"] and res["avisos"] == ["PubMed (error): Invalid query"]


def test_pubmed_partial_failure_is_flagged_and_first_failure_propagates(cfg, monkeypatch):
    monkeypatch.setattr(pubmed, "ESEARCH_PAGE", 2)

    def handler(url, params, kw):
        if url.endswith("esearch.fcgi"):
            if int(params.get("retstart", 0)) >= 2:
                return NetworkError("HTTP 503 en eutils")
            return {"esearchresult": {"count": "5", "idlist": ["1", "2"]}}
        return {"result": {"uids": []}}

    res = pubmed.search_records(FakeClient(cfg, handler), "rumen", retmax=5)
    assert res["incompleto"] and "INCOMPLETOS" in res["avisos"][0] and res["paginas"] == 1
    with pytest.raises(NetworkError):
        pubmed.search_records(FakeClient(cfg, lambda u, p, k: NetworkError("caída")), "rumen")


def test_europepmc_paginates_with_cursor_mark(cfg, monkeypatch):
    monkeypatch.setattr(europepmc, "PAGE_MAX", 2)
    pages = {"*": ("C2", ["1", "2"]), "C2": ("C3", ["3", "4"]), "C3": ("C3", ["5"])}

    def handler(url, params, kw):
        following, hits = pages[params["cursorMark"]]
        return {"hitCount": 5, "nextCursorMark": following,
                "resultList": {"result": [{"id": h, "source": "MED", "title": f"t{h}"} for h in hits]}}

    client = FakeClient(cfg, handler)
    res = europepmc.search_all(client, "rumen", max_results=10)
    assert [r.extra["epmc_id"] for r in res["records"]] == ["1", "2", "3", "4", "5"]
    assert [p["cursorMark"] for _, p in client.calls] == ["*", "C2", "C3"] and res["next_cursor"] is None
    res = europepmc.search_all(FakeClient(cfg, handler), "rumen", max_results=3)
    assert len(res["records"]) == 3 and res["next_cursor"] == "C3" and res["count"] == 5


def test_collect_pages_stops_on_repeated_cursor_and_empty_page():
    calls = []

    def page(cursor, size):
        calls.append((cursor, size))
        return {"count": 100, "records": [cursor or "a"], "next": "x"}

    res = collect_pages(page, 10, 5, "Prueba")
    assert calls == [(None, 5), ("x", 5)] and len(res["records"]) == 2       # «x» se repite: fin


def test_buscar_cli_paginates_openalex_with_cursor_and_logs_identifiers(proj, router, capsys, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", SECRET)
    monkeypatch.setattr(cli, "OPENALEX_PAGE", 2)

    def openalex(method, url):
        cursor = re.search(r"cursor=([^&]+)", url).group(1)
        following = {"%2A": "K2", "K2": "K3", "K3": None}[cursor]
        n = {"%2A": 0, "K2": 2, "K3": 4}[cursor]
        works = [{"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/10.1000/w{i}", "display_name": f"T{i}",
                  "ids": {"pmid": f"https://pubmed.ncbi.nlm.nih.gov/{900 + i}"}} for i in range(n, min(n + 2, 5))]
        return 200, {"meta": {"count": 5, "next_cursor": following}, "results": works}

    router.add(r"api\.openalex\.org/works", openalex)
    code, out, _ = run(capsys, ["--json", "buscar", "openalex", "rumen", "--max", "5"])
    data = json.loads(out)
    assert code == 0 and data["devueltos"] == 5 and data["paginas"] == 3 and data["total"] == 5
    assert not any(re.search(r"[?&]page=", c["url"]) for c in router.calls)
    event = events(proj, "busqueda")[-1]
    assert event["outputs"]["total"] == 5 and event["inputs"]["consulta"] == "rumen"
    assert event["outputs"]["identificadores"][0] == {"doi": "10.1000/w0", "pmid": "900", "openalex": "W0"}
    assert SECRET not in (proj / "research" / "procedencia.jsonl").read_text("utf-8")


def test_buscar_cli_shows_api_warnings_and_flags_incomplete_results(proj, router, capsys, monkeypatch):
    router.add(r"esearch\.fcgi", (200, {"esearchresult": {"count": "0", "idlist": [],
                                                           "errorlist": {"phrasesnotfound": ["zzqq rumen"]},
                                                           "warninglist": {"outputmessages": ["No items found."]}}}))
    code, out, _ = run(capsys, ["buscar", "pubmed", '"zzqq rumen"'])
    assert code == 0 and "AVISO: PubMed (error, frases no encontradas): zzqq rumen" in out
    assert events(proj, "busqueda")[-1]["outputs"]["avisos"] == [
        "PubMed (error, frases no encontradas): zzqq rumen", "PubMed (aviso, mensajes): No items found."]
    monkeypatch.setattr(pubmed, "ESEARCH_PAGE", 1)

    def esearch(method, url):
        start = int(re.search(r"retstart=(\d+)", url).group(1)) if "retstart=" in url else 0
        if start:
            return requests.exceptions.ProxyError("bloqueado")
        return 200, {"esearchresult": {"count": "3", "idlist": ["1"]}}

    router.add(r"esearch\.fcgi", esearch)
    router.add(r"esummary\.fcgi", (200, esummary("1")))
    code, out, _ = run(capsys, ["buscar", "pubmed", "rumen", "--max", "3"])
    assert code == cli.SALIDA_RESULTADO and "RESULTADOS INCOMPLETOS" in out
    assert events(proj, "busqueda")[-1]["outputs"]["incompleto"] is True


# ============================================================================ confidencialidad (D1-11)
def test_patent_queries_are_hashed_in_provenance_unless_registered(proj, router, capsys, monkeypatch):
    query = "rumen-protected choline AND carnitine combinacion-secreta"
    code, out, _ = run(capsys, ["patentes", query])
    trace = (proj / "research" / "procedencia.jsonl").read_text("utf-8")
    assert code == 0 and query not in trace and "combinacion-secreta" not in trace
    event = events(proj, "busqueda_patentes")[-1]
    assert event["inputs"]["consulta_sha256"] == hashlib.sha256(query.encode()).hexdigest()
    monkeypatch.setenv("LENS_API_TOKEN", SECRET)
    router.add(r"api\.lens\.org", (200, {"total": 1, "data": [{"lens_id": "L-1"}]}))
    assert run(capsys, ["patentes", query, "--lens"])[0] == 0
    trace = (proj / "research" / "procedencia.jsonl").read_text("utf-8")
    assert "combinacion-secreta" not in trace and SECRET not in trace and "L-1" not in trace
    assert router.calls[-1]["json"]["query"]["query_string"]["query"] == query      # la consulta sí viaja
    assert run(capsys, ["patentes", query, "--registrar-consulta"])[0] == 0
    assert events(proj, "busqueda_patentes")[-1]["inputs"]["consulta"] == query


def test_confidential_search_keeps_only_hashes_in_provenance(proj, router, capsys):
    query = "combinacion-secreta AND carnitine"
    router.add(r"esearch\.fcgi", (200, {"esearchresult": {"count": "1", "idlist": ["111"],
                                                           "errorlist": {"phrasesnotfound": ["combinacion-secreta"]}}}))
    router.add(r"esummary\.fcgi", (200, esummary("111", "10.1000/abc")))
    code, out, _ = run(capsys, ["buscar", "pubmed", query, "--sensibilidad", "confidencial"])
    assert code == 0 and "viajado igualmente al servicio externo" in out
    trace = (proj / "research" / "procedencia.jsonl").read_text("utf-8")
    assert "combinacion-secreta" not in trace and "10.1000/abc" not in trace
    event = events(proj, "busqueda")[-1]
    assert event["inputs"]["consulta_sha256"] == hashlib.sha256(query.encode()).hexdigest()
    assert event["outputs"]["avisos_api"] == 1 and "identificadores_sha256" in event["outputs"]
    assert all(e["inputs"].get("confidencial") for e in events(proj) if e["action"].startswith("http"))
    router.add(r"esearch\.fcgi", (500, '{"error": "combinacion-secreta"}'))
    code, _, err = run(capsys, ["buscar", "pubmed", query + " otra", "--sensibilidad", "confidencial"])
    assert code == cli.SALIDA_RED
    assert "combinacion-secreta" not in (proj / "research" / "procedencia.jsonl").read_text("utf-8")


def test_confidential_queries_of_molecules_targets_proteins_networks_and_pathways(proj, router, capsys, tmp_path):
    # D1-11 (misma causa que buscar): molecula, diana, proteina, red y rutas admiten --sensibilidad confidencial y
    # entonces la procedencia versionada guarda solo huellas de la consulta y de lo que devolvió.
    secret = "SECRETX9"
    router.add(r"pubchem.*/compound/name/", (200, {"PropertyTable": {"Properties": [{"CID": 777001}]}}))
    router.add(r"pug_view/data/compound/777001/", (404, "{}"))

    def target(method, url):
        if "target_synonym__icontains" in url:   # un error cuyo cuerpo repite la consulta
            return 400, json.dumps({"error": f"filtro rechazado: {secret}"})
        return 200, {"targets": [dict(BOVINE_PPARA, target_chembl_id="CHEMBL777002")]}

    router.add(r"chembl/api/data/target", target)
    router.add(r"chembl/api/data/status\.json", (200, {"chembl_db_version": "ChEMBL_35"}))
    router.add(r"rest\.uniprot\.org", (200, {"results": [{"primaryAccession": "Q777003", "genes": [
        {"geneName": {"value": secret}}]}]}, {"X-UniProt-Release": "2025_04"}))
    router.add(r"string-db\.org/api/json/version", (200, [{"string_version": "12.0"}]))
    router.add(r"string-db\.org/api/json/network", (200, [{"preferredName_A": secret, "preferredName_B": "RXRA",
                                                            "score": 0.9}]))
    router.add(r"ContentService/data/database/version", (200, "94"))
    router.add(r"ContentService/search/query", (200, {"results": [{"entries": [{"stId": "R-BTA-777004",
                                                                                 "name": secret}]}]}))
    orders = {"molecula": ["molecula", "pubchem", secret, "--peligros"],
              "diana": ["diana", secret, "--organismo", "Bos taurus"],
              "proteina": ["proteina", f"gene:{secret}"],
              "red_string": ["red", secret, "RXRA"],
              "rutas": ["rutas", "buscar", secret]}
    for action, argv in orders.items():
        code, out, _ = run(capsys, ["--json", *argv, "--sensibilidad", "confidencial"])
        assert code in (cli.SALIDA_OK, cli.SALIDA_RESULTADO), (argv, out)
        data = json.loads(out)
        assert any("viajado igualmente al servicio externo" in n for n in data["notas"]), action
        event = events(proj, action)[-1]
        assert event["inputs"]["sensibilidad"] == "confidencial" and event["inputs"]["consulta_sha256"], action
    trace = (proj / "research" / "procedencia.jsonl").read_text("utf-8")
    for token in (secret, "777001", "CHEMBL777002", "Q777003", "R-BTA-777004"):
        assert token not in trace, token
    assert all(e["inputs"].get("confidencial") for e in events(proj) if e["action"].startswith("http"))
    diana = events(proj, "diana")[-1]["outputs"]
    assert diana["identificadores_sha256"] and {"sinonimo_o_simbolo": True}.items() <= {
        t["estrategia"]: t["error"] for t in diana["estrategias"]}.items()
    assert events(proj, "red_string")[-1]["inputs"]["puntuacion_min"] == 700          # lo que no revela, en claro
    bundle = tmp_path / "exportacion.json"
    code, out, _ = run(capsys, ["--json", "cache", "exportar", str(bundle)])
    assert code == 0 and json.loads(out)["entradas"] == 0 and secret not in bundle.read_text("utf-8")
    # Sin la opción, todo sigue en claro (comportamiento por defecto).
    assert run(capsys, ["rutas", "buscar", secret])[0] == 0
    assert events(proj, "rutas")[-1]["inputs"] == {"accion": "buscar", "terminos": [secret], "sensibilidad": "normal"}


# ============================================================================ ChEMBL (D7-11)
def _chembl_handler(by_synonym=None, by_accession=None, by_name=None):
    def handler(url, params, kw):
        if url.endswith("status.json"):
            return {"chembl_db_version": "ChEMBL_35", "chembl_release_date": "2025-01-01"}
        if "target_synonym__icontains" in params:
            return {"targets": by_synonym or []}
        if "target_components__accession" in params:
            return {"targets": by_accession or []}
        return {"targets": by_name or []}
    return handler


def _component(accession, *synonyms):
    return [{"accession": accession, "target_component_synonyms": [
        {"component_synonym": name, "syn_type": "GENE_SYMBOL"} for name in synonyms]}]


BOVINE_PPARA = {"target_chembl_id": "CHEMBL9", "pref_name": "Peroxisome proliferator-activated receptor alpha",
                "organism": "Bos taurus", "target_type": "SINGLE PROTEIN",
                "target_components": _component("Q07869", "PPARA", "NR1C1")}


def test_chembl_target_by_gene_symbol_is_searched_by_synonym_on_the_server(cfg):
    # D7-11: una consulta por símbolo de gen («PPARA») no coincide con pref_name (nombre completo): se busca
    # también por sinónimo o símbolo, con el organismo filtrado en el servidor
    client = FakeClient(cfg, _chembl_handler(by_synonym=[BOVINE_PPARA]))
    tried = []
    rows = chembl.target_search(client, "PPARA", organism="Bos taurus", tried=tried)
    assert [(r["target_chembl_id"], r["coincidencia"], r["exacta"]) for r in rows] == [
        ("CHEMBL9", "sinonimo_o_simbolo", True)]
    assert [c[1] for c in client.calls] == [
        {"pref_name__icontains": "PPARA", "organism__iexact": "Bos taurus", "limit": 20},
        {"target_synonym__icontains": "PPARA", "organism__iexact": "Bos taurus", "limit": 20}]
    assert tried == [{"estrategia": "nombre_preferido", "n": 0, "truncada": False},
                     {"estrategia": "sinonimo_o_simbolo", "n": 1, "truncada": False}]
    client = FakeClient(cfg, _chembl_handler(by_accession=[BOVINE_PPARA]))
    rows = chembl.target_search(client, "q07869", organism="Bos taurus")
    assert rows[0]["coincidencia"] == "accesion_uniprot" and rows[0]["exacta"]
    assert client.calls[1][1] == {"target_components__accession": "Q07869", "organism__iexact": "Bos taurus",
                                  "limit": 20}
    assert chembl.target_search(FakeClient(cfg, _chembl_handler()), "PPARA", organism="Homo sapiens") == []
    assert chembl.release(FakeClient(cfg, _chembl_handler())) == {"version": "ChEMBL_35",
                                                                  "fecha_publicacion": "2025-01-01"}


def test_chembl_name_hits_do_not_hide_the_gene_symbol_match(cfg):
    # Variante: pref_name__icontains=CAT devuelve dianas que solo contienen «cat» en el nombre; la diana cuyo
    # símbolo de gen es CAT (catalasa) no debe quedar oculta ni detrás de ellas, aunque se pida un máximo pequeño
    unrelated = [{"target_chembl_id": f"CHEMBL{100 + i}", "pref_name": name, "organism": "Bos taurus",
                  "target_type": "SINGLE PROTEIN", "target_components": _component(f"P0000{i}", f"GEN{i}")}
                 for i, name in enumerate(["Cathepsin B", "Catechol O-methyltransferase"])]
    catalase = {"target_chembl_id": "CHEMBL7", "pref_name": "Catalase", "organism": "Bos taurus",
                "target_type": "SINGLE PROTEIN", "target_components": _component("P00432", "CAT")}
    client = FakeClient(cfg, _chembl_handler(by_name=unrelated + [catalase], by_synonym=[catalase]))
    tried = []
    rows = chembl.target_search(client, "cat", organism="Bos taurus", limit=2, tried=tried)
    assert [r["target_chembl_id"] for r in rows] == ["CHEMBL7", "CHEMBL100"]
    assert rows[0]["exacta"] and rows[0]["coincidencias"] == ["nombre_preferido", "sinonimo_o_simbolo"]
    assert tried[0] == {"estrategia": "nombre_preferido", "n": 3, "truncada": True}


def test_chembl_molecules_mechanisms_and_activities_parse(cfg):
    def handler(url, params, kw):
        if "molecule/search" in url:
            return {"molecules": [{"molecule_chembl_id": "CHEMBL25", "pref_name": "ASPIRIN", "max_phase": 4,
                                   "molecule_properties": {"full_molformula": "C9H8O4", "full_mwt": "180.16"},
                                   "molecule_structures": {"canonical_smiles": "CC(=O)Oc1ccccc1C(=O)O"}}]}
        if "mechanism" in url:
            return {"mechanisms": [{"mechanism_of_action": "COX inhibitor", "target_chembl_id": "CHEMBL2094253",
                                    "action_type": "INHIBITOR"}]}
        return {"activities": [{"molecule_chembl_id": "CHEMBL25", "standard_type": "IC50", "standard_value": "5"}]}

    client = FakeClient(cfg, handler)
    (mol,) = chembl.molecule_search(client, "aspirin")
    assert mol["chembl_id"] == "CHEMBL25" and mol["formula"] == "C9H8O4" and mol["smiles"].startswith("CC")
    assert chembl.mechanisms(client, "CHEMBL25")[0]["accion"] == "INHIBITOR"
    assert chembl.activities(client, molecule_chembl_id="CHEMBL25")[0]["valor"] == "5"
    with pytest.raises(ValueError):
        chembl.activities(client)


def test_diana_cli_reports_strategy_version_and_no_false_negative_claim(proj, router, capsys):
    def target(method, url):
        if "target_synonym__icontains=PPARA" in url:
            return 200, {"targets": [BOVINE_PPARA]}
        return 200, {"targets": []}

    router.add(r"chembl/api/data/target", target)
    router.add(r"chembl/api/data/status\.json", (200, {"chembl_db_version": "ChEMBL_35"}))
    code, out, _ = run(capsys, ["--json", "diana", "PPARA", "--organismo", "Bos taurus"])
    data = json.loads(out)
    assert code == 0 and data["resultados"][0]["target_chembl_id"] == "CHEMBL9"
    assert data["fuente"]["version"] == "ChEMBL_35"
    event = events(proj, "diana")[-1]
    assert event["outputs"]["identificadores"] == ["CHEMBL9"] and event["outputs"]["fuente"]["version"] == "ChEMBL_35"
    code, out, _ = run(capsys, ["diana", "XYZ1", "--organismo", "Bos taurus"])
    assert code == 0 and "No demuestra que la diana no exista" in out
    assert "nombre preferido, sinónimo o símbolo de gen" in out
    router.add(r"chembl/api/data/target", (200, {"targets": [dict(BOVINE_PPARA, target_chembl_id=f"CHEMBL{i}")
                                                               for i in range(3)]}))
    code, out, _ = run(capsys, ["diana", "receptor", "--organismo", "Bos taurus", "--max", "3"])
    assert code == 0 and "llegó al máximo (3)" in out
    assert run(capsys, ["diana", "PPARA", "--max", "0"])[0] == cli.SALIDA_ENTRADA


# ============================================================================ PubChem (D7-14)
def test_pubchem_ghs_states_are_explicit(cfg):
    def by_status(status, body=None):
        def handler(url, params, kw):
            if status == 404:
                return NotFoundError("404")
            if status >= 500:
                return NetworkError(f"HTTP {status}")
            return body
        return FakeClient(cfg, handler)

    assert pubchem.ghs_classification(by_status(404), 1)["estado"] == pubchem.GHS_SIN_CLASIFICACION
    section = {"Record": {"Section": [{"TOCHeading": "GHS Classification", "Information": [
        {"Name": "GHS Hazard Statements", "Value": {"StringWithMarkup": [
            {"String": "Not Classified"}]}}]}]}}
    without = pubchem.ghs_classification(by_status(200, {"Record": {"Section": [{"TOCHeading": "GHS"}]}}), 1)
    assert without["estado"] == pubchem.GHS_SIN_FRASES and without["frases_H"] == []
    hazards = {"Record": {"Information": [{"Name": "Hazard Statements", "Value": {"StringWithMarkup": [
        {"String": "H302: Harmful if swallowed"}]}}]}}
    with_h = pubchem.ghs_classification(by_status(200, hazards), 1)
    assert with_h["estado"] == pubchem.GHS_CON_FRASES and with_h["frases_H"] == ["H302: Harmful if swallowed"]
    error = pubchem.ghs_classification(by_status(503), 1)
    assert error["estado"] == pubchem.GHS_ERROR and "503" in error["detalle"]
    # «Not Classified» no es una frase H: clasificado sin frases H, con la notificación a la vista.
    not_classified = pubchem.ghs_classification(by_status(200, section), 1)
    assert not_classified["estado"] == pubchem.GHS_SIN_FRASES and not_classified["frases_H"] == []
    assert not_classified["no_clasificado"] == ["Not Classified"] and "Not Classified" in not_classified["detalle"]


def _ghs_record(*informations):
    """Respuesta de PUG View con la forma real de la sección «GHS Classification» (Safety and Hazards > Hazards
    Identification > GHS Classification), con una o varias referencias."""
    info = []
    for ref, (name, strings) in enumerate(informations, start=1):
        info.append({"ReferenceNumber": ref, "Name": name,
                     "Value": {"StringWithMarkup": [{"String": text} for text in strings]}})
    return {"Record": {"RecordType": "CID", "RecordNumber": 1, "Section": [
        {"TOCHeading": "Safety and Hazards", "Section": [{"TOCHeading": "Hazards Identification", "Section": [
            {"TOCHeading": "GHS Classification", "Information": info}]}]}]}}


# D7-14: en la lista «GHS Hazard Statements» PubChem mezcla frases H con textos que no lo son.
GHS_NOT_MEETING = "Reported as not meeting GHS hazard criteria by 1197 of 1201 companies. For more detailed " \
                  "information, please visit ECHA C&L website"
GHS_OLD_FORMAT = ["Reported as not meeting GHS hazard criteria by 4 of 1203 companies. For more detailed information, "
                  "please visit ECHA C&L website",
                  "Of the 31 notification(s) provided by 1199 of 1203 companies with hazard statement code(s):",
                  "H302 (88.6%): Harmful if swallowed [Warning Acute toxicity, oral]",
                  "H300+H310+H330 (12%): Fatal if swallowed, in contact with skin or if inhaled [Danger]",
                  "H360FD (10.2%): May damage fertility. May damage the unborn child [Danger Reproductive toxicity]",
                  "EUH066: Repeated exposure may cause skin dryness or cracking",
                  "Information may vary between notifications depending on impurities, additives, and other factors. "
                  "The percentage value in parenthesis indicates the notified classification ratio from companies "
                  "that provide hazard codes. Only hazard classifications with ratios higher than 10% are shown"]


@pytest.mark.parametrize("informations, estado, frases, no_clasificado", [
    ([("GHS Hazard Statements", ["Not Classified"]), ("ECHA C&L Notifications Summary", [GHS_NOT_MEETING])],
     pubchem.GHS_SIN_FRASES, [], ["Not Classified", GHS_NOT_MEETING]),
    ([("GHS Hazard Statements", [GHS_NOT_MEETING])], pubchem.GHS_SIN_FRASES, [], [GHS_NOT_MEETING]),
    ([("GHS Hazard Statements", ["The substance does not meet the criteria for classification"])],
     pubchem.GHS_SIN_FRASES, [], ["The substance does not meet the criteria for classification"]),
    ([("Signal", ["Warning"]), ("GHS Hazard Statements", GHS_OLD_FORMAT)], pubchem.GHS_CON_FRASES,
     GHS_OLD_FORMAT[2:6], GHS_OLD_FORMAT[:1]),
    # Dos referencias que discrepan: una notifica H315 y otra «Not Classified».
    ([("GHS Hazard Statements", ["H315 (100%): Causes skin irritation [Warning Skin corrosion/irritation]"]),
      ("GHS Hazard Statements", ["Not Classified"])],
     pubchem.GHS_CON_FRASES, ["H315 (100%): Causes skin irritation [Warning Skin corrosion/irritation]"],
     ["Not Classified"]),
    ([("GHS Hazard Statements", ["Hazard statements not available", "H3020 typo", "Harmful"])],
     pubchem.GHS_SIN_FRASES, [], []),
])
def test_pubchem_only_coded_statements_are_hazard_statements(cfg, informations, estado, frases, no_clasificado):
    body = _ghs_record(*informations)
    result = pubchem.ghs_classification(FakeClient(cfg, lambda url, params, kw: body), 1)
    assert result["estado"] == estado
    assert result["frases_H"] == frases and result["no_clasificado"] == no_clasificado
    assert not set(result["frases_H"]) & set(result["otras"])
    assert all(pubchem.is_hazard_statement(text) for text in result["frases_H"])
    assert pubchem.ghs_hazards(FakeClient(cfg, lambda url, params, kw: body), 1) == frases
    if estado == pubchem.GHS_CON_FRASES and no_clasificado:
        assert "algunas notificaciones declaran que no se clasifica" in result["detalle"]
    if estado == pubchem.GHS_SIN_FRASES:
        assert "no equivale a «sin peligro»" in result["detalle"]


def test_molecula_cli_does_not_present_not_classified_as_a_hazard_statement(proj, router, capsys):
    router.add(r"compound/name/glucose", (200, {"PropertyTable": {"Properties": [{"CID": 5793}]}}))
    router.add(r"pug_view/data/compound/5793/", (200, _ghs_record(
        ("GHS Hazard Statements", ["Not Classified"]), ("ECHA C&L Notifications Summary", [GHS_NOT_MEETING]))))
    code, out, _ = run(capsys, ["molecula", "pubchem", "glucose", "--peligros"])
    assert code == cli.SALIDA_OK
    line = next(text for text in out.splitlines() if text.startswith("GHS CID 5793"))
    assert line.startswith("GHS CID 5793: clasificado_sin_frases_H —") and "con_frases_H" not in line
    assert "notificado como «Not Classified»" in line
    code, out, _ = run(capsys, ["--json", "molecula", "pubchem", "glucose", "--peligros"])
    ghs = json.loads(out)["resultados"][0]
    assert ghs["GHS_frases_H"] == [] and ghs["GHS"]["no_clasificado"] == ["Not Classified", GHS_NOT_MEETING]
    assert events(proj, "molecula")[-1]["outputs"]["ghs"] == {"5793": "clasificado_sin_frases_H"}


def test_pubchem_compound_falls_back_to_isomeric_smiles_and_lists_synonyms(cfg):
    def handler(url, params, kw):
        if ",SMILES/JSON" in url:
            return NetworkError("HTTP 400 en pubchem: propiedad desconocida")
        if "IsomericSMILES" in url:
            return {"PropertyTable": {"Properties": [{"CID": 2244, "IsomericSMILES": "CC(=O)O"}]}}
        return {"InformationList": {"Information": [{"Synonym": ["aspirin", "acetylsalicylic acid"]}]}}

    client = FakeClient(cfg, handler)
    assert pubchem.compound_by_name(client, "aspirin") == [{"CID": 2244, "SMILES": "CC(=O)O"}]
    assert pubchem.synonyms(client, 2244, limit=1) == ["aspirin"]


def test_molecula_cli_distinguishes_ghs_states_and_not_found(proj, router, capsys):
    router.add(r"compound/name/aspirin", (200, {"PropertyTable": {"Properties": [
        {"CID": c} for c in (2244, 2245, 2246, 2247)]}}))

    def view(method, url):
        if "/2244/" in url:
            return 404, '{"Fault": {}}'
        if "/2245/" in url:
            return 200, {"Record": {"Section": [{"TOCHeading": "GHS Classification"}]}}
        return 500, "{}"

    router.add(r"pug_view/data/compound/", view)
    code, out, _ = run(capsys, ["--json", "molecula", "pubchem", "aspirin", "--peligros"])
    data = json.loads(out)
    assert code == cli.SALIDA_RESULTADO and data["incompleto"]
    assert [c["GHS"]["estado"] for c in data["resultados"]] == [
        "sin_clasificacion", "clasificado_sin_frases_H", "error", "no_consultado"]
    assert events(proj, "molecula")[-1]["outputs"]["ghs"]["2246"] == "error"
    router.add(r"compound/name/inexistente", (404, '{"Fault": {"Code": "PUGREST.NotFound"}}'))
    code, out, err = run(capsys, ["molecula", "pubchem", "inexistente"])
    assert code == cli.SALIDA_NO_ENCONTRADO and err.startswith("No encontrado:") and "Error de red" not in err
    code, out, _ = run(capsys, ["--json", "molecula", "pubchem", "inexistente"])
    assert json.loads(out)["tipo"] == "no_encontrado" and json.loads(out)["codigo"] == 6


# ============================================================================ UniProt, STRING, Reactome (D7-12, D7-15)
def test_uniprot_query_parse_and_release_from_headers(cfg):
    entry = {"primaryAccession": "Q07869", "uniProtkbId": "PPARA_BOVIN", "entryType": "UniProtKB reviewed",
             "genes": [{"geneName": {"value": "PPARA"}}], "organism": {"scientificName": "Bos taurus",
                                                                       "taxonId": 9913}}
    net = Router()
    net.add(r"rest\.uniprot\.org", (200, {"results": [entry]}, {"X-UniProt-Release": "2025_04",
                                                                 "X-UniProt-Release-Date": "15-Oct-2025"}))
    client = HttpClient(cfg, session=Session(net))
    (row,) = uniprot.search(client, "gene:PPARA", taxon=9913, reviewed_only=True)
    assert row["accesion"] == "Q07869" and row["revisada"] and row["taxon"] == 9913
    assert net.calls[0]["params"]["query"] == "(gene:PPARA) AND (organism_id:9913) AND (reviewed:true)"
    assert uniprot.release(client) == {"version": "2025_04", "fecha_publicacion": "15-Oct-2025"}
    cached = HttpClient(cfg, session=Session(net))                     # la versión viaja también en la caché
    uniprot.search(cached, "gene:PPARA", taxon=9913, reviewed_only=True)
    assert len(net.calls) == 1 and uniprot.release(cached)["version"] == "2025_04"
    assert uniprot.release(FakeClient(cfg, lambda *a: {})) == {}
    net.add(r"rest\.uniprot\.org", (200, {"results": []}))              # respuesta sin la cabecera de versión
    uniprot.search(cached, "gene:OTRO")
    assert uniprot.release(cached) == {}                                 # nunca la versión de una respuesta anterior


def test_string_uses_versioned_address_and_reports_version(cfg):
    assert stringdb.base_url(cfg) == "https://version-12-0.string-db.org" and stringdb.is_versioned(cfg)

    def handler(url, params, kw):
        if url.endswith("/json/version"):
            return [{"string_version": "12.0", "stable_address": "https://version-12-0.string-db.org"}]
        if url.endswith("/json/network"):
            return [{"preferredName_A": "PPARA", "preferredName_B": "RXRA", "score": 0.9, "escore": 0.5}]
        return [{"category": "Process", "term": "GO:1", "fdr": 0.01}, {"category": "Process", "term": "GO:2",
                                                                         "fdr": 0.2}, {"term": "GO:3", "fdr": None}]

    client = FakeClient(cfg, handler)
    assert stringdb.version(client) == {"version": "12.0", "direccion_estable": "https://version-12-0.string-db.org"}
    assert stringdb.network(client, ["PPARA", " RXRA "], 9913)[0]["experimental"] == 0.5
    assert [r["termino"] for r in stringdb.enrichment(client, ["PPARA"], 9913)] == ["GO:1"]
    assert all(u.startswith("https://version-12-0.string-db.org/api/") for u, _ in client.calls)
    assert client.calls[1][1]["identifiers"] == "PPARA\rRXRA" and client.calls[1][1]["caller_identity"] == "nutria"
    cfg.string_url = "https://string-db.org"
    assert not stringdb.is_versioned(cfg) and stringdb.api_url(cfg) == "https://string-db.org/api"


def test_red_cli_records_string_version_and_address(proj, router, capsys):
    router.add(r"version-12-0\.string-db\.org/api/json/version", (200, [{"string_version": "12.0"}]))
    router.add(r"version-12-0\.string-db\.org/api/json/network", (200, []))
    code, out, _ = run(capsys, ["--json", "red", "PPARA", "CPT1A"])
    data = json.loads(out)
    assert code == 0 and data["fuente"].items() >= {"nombre": "STRING", "url": "https://version-12-0.string-db.org",
                                                    "version": "12.0"}.items()
    assert data["fuente"]["version_consultada"].startswith(datetime.now(timezone.utc).date().isoformat()[:4])
    assert events(proj, "red_string")[-1]["outputs"]["fuente"]["version"] == "12.0"
    (proj / "nutria.toml").write_text((proj / "nutria.toml").read_text("utf-8").replace(
        "https://version-12-0.string-db.org", "https://string-db.org"), "utf-8")
    router.add(r"//string-db\.org/api/json/", (200, []))
    code, out, _ = run(capsys, ["red", "PPARA"])
    assert code == 0 and "no es versionada" in out and "no informó de su versión" in out


def test_reactome_search_analysis_and_version(cfg):
    def handler(url, params, kw):
        if url.endswith("/data/database/version"):
            return "88\n"
        if "search/query" in url:
            return {"results": [{"entries": [{"stId": "R-BTA-1", "name": "<b>Fatty</b> acid", "species": ["Bos taurus"],
                                              "exactType": "Pathway"}]}]}
        return {"summary": {"token": "T"}, "identifiersNotFound": 1,
                "pathways": [{"stId": "R-HSA-1", "name": "FA", "entities": {"fdr": 0.01, "found": 2}}]}

    client = FakeClient(cfg, handler)
    assert reactome.version(client) == {"version": "88"}
    assert reactome.search(client, "fatty acid")[0] == {"id": "R-BTA-1", "nombre": "Fatty acid",
                                                        "especie": ["Bos taurus"], "tipo": "Pathway"}
    result = reactome.analyze_genes(client, ["PPARA", " CPT1A "])
    assert result["token"] == "T" and result["rutas"][0]["encontradas"] == 2
    garbage = reactome.version(FakeClient(cfg, lambda *a: "<html>error</html>" * 5))
    assert "version" not in garbage and garbage["version_no_reconocida"].startswith("<html>")
    assert reactome.version(FakeClient(cfg, lambda *a: "")) == {}


# D7-15: una respuesta que no es una versión nunca se registra como versión (ni en las órdenes ni en doctor).
@pytest.mark.parametrize("module, parse, good, bad", [
    (reactome, reactome.parse_version, "94", ["{}", "<html>Error 500</html>", "94; DROP", "version"]),
    (stringdb, stringdb.parse_version, [{"string_version": "12.0"}], [{"string_version": "{}"}, ["x"],
                                                                      [{"string_version": {"a": 1}}]]),
    (chembl, chembl.parse_release, {"chembl_db_version": "ChEMBL_35", "chembl_release_date": "2024-12-01"},
     [{"chembl_db_version": "<html>"}, {"chembl_db_version": "35"}, "texto"]),
    (uniprot, uniprot.parse_release, {"X-UniProt-Release": "2025_04", "X-UniProt-Release-Date": "11-June-2025"},
     [{"X-UniProt-Release": "{}"}, {"x-uniprot-release": "<html>"}]),
])
def test_versions_are_checked_against_their_expected_form(module, parse, good, bad):
    assert parse(good).get("version")
    for value in bad:
        found = parse(value)
        assert "version" not in found and found.get("version_no_reconocida"), value
    assert parse(None) == {} and parse("") == {}


def test_a_response_that_is_not_a_version_is_neither_cached_nor_recorded(proj, router, capsys):
    router.add(r"ContentService/data/database/version", (200, "{}"))
    router.add(r"ContentService/search/query", (200, {"results": []}))
    code, out, _ = run(capsys, ["--json", "rutas", "buscar", "fatty", "acid"])
    data = json.loads(out)
    assert code == 0 and "version" not in data["fuente"]
    assert any("Reactome respondió sin una versión reconocible" in a for a in data["avisos"])
    assert "version" not in events(proj, "rutas")[-1]["outputs"]["fuente"]
    router.add(r"ContentService/data/database/version", (200, "94"))      # no se guardó: se vuelve a pedir
    code, out, _ = run(capsys, ["--json", "rutas", "buscar", "fatty", "acid"])
    assert code == 0 and json.loads(out)["fuente"]["version"] == "94"
    assert sum("database/version" in c["url"] for c in router.calls) == 2


def test_version_from_another_date_than_cached_results_is_flagged(proj, router, capsys):
    # La versión se pide aparte (TTL de 1 día): si los resultados salen de una copia de la caché de otra fecha, la
    # versión puede no ser la suya y se avisa.
    router.add(r"ContentService/data/database/version", (200, "94"))
    router.add(r"ContentService/search/query", (200, {"results": []}))
    assert run(capsys, ["rutas", "buscar", "fatty", "acid"])[0] == 0
    code, out, _ = run(capsys, ["--json", "rutas", "buscar", "fatty", "acid"])     # todo de la caché, misma fecha
    assert code == 0 and not any("otra versión" in a for a in json.loads(out)["avisos"])
    age_provenance(proj / "research" / "procedencia.jsonl", 10)                   # resultados y versión: 10 días
    code, out, _ = run(capsys, ["--json", "rutas", "buscar", "fatty", "acid"])     # versión nueva (TTL 1 día)
    data = json.loads(out)
    assert code == 0 and data["fuente"]["version"] == "94"
    assert data["fuente"]["version_consultada"][:10] == datetime.now(timezone.utc).date().isoformat()
    aviso = next(a for a in data["avisos"] if "otra versión de Reactome" in a)
    assert "1 respuesta(s)" in aviso
    assert events(proj, "rutas")[-1]["outputs"]["fuente"]["version_consultada"] == data["fuente"]["version_consultada"]


def test_rutas_cli_records_reactome_version(proj, router, capsys):
    router.add(r"ContentService/data/database/version", (200, "88"))
    router.add(r"ContentService/search/query", (200, {"results": []}))
    code, out, _ = run(capsys, ["--json", "rutas", "buscar", "fatty", "acid"])
    assert code == 0 and json.loads(out)["fuente"]["version"] == "88"
    fuente = events(proj, "rutas")[-1]["outputs"]["fuente"]
    assert fuente["nombre"] == "Reactome" and fuente["version"] == "88" and set(fuente) == {
        "nombre", "version", "version_consultada"}


# ============================================================================ Lens (D7-12)
def test_lens_client_posts_with_token_parses_and_never_stores_the_token(cfg, monkeypatch):
    with pytest.raises(NetworkError, match="LENS_API_TOKEN") as info:
        patents.lens_search(FakeClient(cfg, lambda *a: {}), "x")
    assert info.value.motivo == "credenciales"
    monkeypatch.setenv("LENS_API_TOKEN", SECRET)
    net = Router()
    net.add(r"api\.lens\.org", (200, {"total": 7, "data": [{
        "lens_id": "001-002", "jurisdiction": "EP", "doc_number": "1234567", "kind": "A1",
        "date_published": "2020-01-01", "biblio": {"invention_title": [{"text": "Feed additive"}],
                                                   "parties": {"applicants": [{"extracted_name": {"value": "ACME"}}]}},
        "legal_status": {"patent_status": "PENDING"}}]}))
    meta = {}
    (row,) = patents.lens_search(HttpClient(cfg, session=Session(net)), "choline", size=5, meta=meta)
    assert row == {"lens_id": "001-002", "jurisdiccion": "EP", "numero": "1234567", "tipo": "A1",
                   "fecha_publicacion": "2020-01-01", "titulo": "Feed additive", "solicitantes": ["ACME"],
                   "estado": "PENDING"} and meta["total"] == 7
    call = net.calls[0]
    assert call["method"] == "POST" and call["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert call["json"] == {"query": {"query_string": {"query": "choline"}}, "size": 5}
    for path in [cfg.provenance_path, *cache_files(cfg.cache_dir)]:
        assert SECRET not in path.read_text("utf-8")
    net.add(r"api\.lens\.org", (401, '{"error": "token no válido"}'))
    with pytest.raises(NetworkError) as info:
        patents.lens_search(HttpClient(cfg, session=Session(net)), "otra")
    assert info.value.motivo == "credenciales" and info.value.status == 401
    links = patents.search_links("choline AND carnitine")
    assert links["espacenet"].endswith("q=choline%20AND%20carnitine")


# ============================================================================ EUR-Lex (D5-15)
EURLEX_PAGE = """<html><head><title>EUR-Lex - 32009R0767 - ES</title>
<meta about="http://data.europa.eu/eli/reg/2009/767/oj" typeof="eli:LegalResource"/>
<meta about="http://data.europa.eu/eli/reg/2009/767/oj/eng" property="eli:title" content="Regulation (EC) No 767/2009" lang="en"/>
<meta about="http://data.europa.eu/eli/reg/2009/767/oj/spa" property="eli:title" content="Reglamento (CE) n.&#186; 767/2009 sobre la comercializaci&#243;n de los piensos" lang="es"/>
<meta about="http://data.europa.eu/eli/reg/2009/767/oj" property="eli:date_document" content="2009-07-13" datatype="xsd:date"/>
<meta about="http://data.europa.eu/eli/reg/2009/767/oj" property="eli:date_publication" content="2009-09-01"/>
<meta about="http://data.europa.eu/eli/reg/2009/767/oj" property="eli:in_force" resource="http://data.europa.eu/eli/ontology#InForce-inForce"/>
</head><body>
<a href="./?uri=CELEX:02009R0767-20170101">2017</a> <a href="./?uri=CELEX%3A02009R0767-20181226">2018</a>
<a href="./?uri=CELEX:02011R0068-20220101">otro acto</a></body></html>"""


def test_eurlex_parses_eli_metadata_and_latest_consolidated_version():
    doc = eurlex.parse_page(EURLEX_PAGE, "32009R0767", "ES")
    assert doc["titulo"] == "Reglamento (CE) n.º 767/2009 sobre la comercialización de los piensos"
    assert doc["fecha_documento"] == "2009-07-13" and doc["fecha_publicacion"] == "2009-09-01"
    assert doc["eli"] == "http://data.europa.eu/eli/reg/2009/767/oj" and doc["en_vigor"] is True
    assert doc["ultima_consolidada"] == {"celex": "02009R0767-20181226", "fecha": "2018-12-26",
                                         "url": "https://eur-lex.europa.eu/legal-content/ES/TXT/?uri=CELEX:02009R0767-20181226"}
    assert doc["versiones_consolidadas"] == 2 and doc["avisos"] == []
    bare = eurlex.parse_page('<meta about="http://data.europa.eu/eli/reg/2009/767/oj" typeof="eli:LegalResource">'
                             "<title>Página</title>", "32009R0767")
    assert bare["titulo"] == "Página" and bare["ultima_consolidada"] is None and bare["fecha_documento"] is None
    assert any("versiones consolidadas" in a for a in bare["avisos"]) and any("eli:title" in a for a in bare["avisos"])
    with pytest.raises(NetworkError, match="sin metadatos reconocibles"):
        eurlex.parse_page("<html><title>Please verify you are human</title></html>", "32009R0767")
    for bad in ("767/2009", "32009R767", "", "CELEX:3200"):
        with pytest.raises(ValueError):
            eurlex.normalize_celex(bad)
    assert eurlex.normalize_celex(" celex:32009r0767 ") == "32009R0767"


def test_normativa_cli_fetches_caches_and_logs(proj, router, capsys):
    router.add(r"eur-lex\.europa\.eu/legal-content/ES/ALL/\?uri=CELEX(:|%3A)32009R0767", (200, EURLEX_PAGE))
    code, out, _ = run(capsys, ["normativa", "eurlex", "32009R0767"])
    assert code == 0 and "02009R0767-20181226 (2018-12-26)" in out and "ELI: http://data.europa.eu/eli/reg/2009/767/oj" in out
    event = events(proj, "normativa")[-1]
    assert event["inputs"]["celex"] == "32009R0767" and event["outputs"]["fecha_documento"] == "2009-07-13"
    code, out, _ = run(capsys, ["--offline", "--json", "normativa", "eurlex", "32009R0767"])
    data = json.loads(out)
    assert code == 0 and data["cache"] and data["fecha_copia"] and len(router.calls) == 1
    assert run(capsys, ["normativa", "eurlex", "767/2009"])[0] == cli.SALIDA_ENTRADA
    router.add(r"eur-lex", (200, "<html><title>Captcha</title></html>"))
    assert run(capsys, ["normativa", "eurlex", "32019R0006"])[0] == cli.SALIDA_RED


# ============================================================================ router de modelos (D9-16, D9-12)
MODELS = """
[[modelo]]
id = "local1"
proveedor = "openai_compatible"
modelo = "modelo-local"
base_url = "http://localhost:11434/v1"
roles = ["critico"]
politica_datos = "local"
activo = true

[[modelo]]
id = "externo"
proveedor = "openai_compatible"
modelo = "modelo-externo"
base_url = "https://api.example.org/v1"
api_key_env = "OPENAI_API_KEY"
roles = ["critico"]
politica_datos = "standard"
activo = true
"""


@pytest.fixture
def llm(tmp_path, monkeypatch):
    from nutria.llm import router as llm_router
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "modelos.toml").write_text(MODELS, "utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        if "example.org" in url and json["messages"][-1]["content"] == "falla":
            raise requests.exceptions.ConnectionError(f"fallo con {SECRET}")
        resp = Resp(200, {"choices": [{"message": {"content": "opinión"}}], "model": "m"}, url=url)
        resp.raise_for_status = lambda: None
        resp.json = lambda: {"choices": [{"message": {"content": "opinión"}}], "model": "m"}
        return resp

    monkeypatch.setattr(llm_router.requests, "post", post)
    return llm_router, Config(root=tmp_path, cache_dir=tmp_path / "cache"), calls


def test_llm_router_makes_no_request_offline(llm, tmp_path):
    llm_router, config, calls = llm
    config.offline = True
    results = llm_router.ask(config, "documento", role="critico", sensitivity="normal")
    assert calls == [] and all("modo offline" in r.get("omitido", "") for r in results) and len(results) == 2
    bank = tmp_path / "banco.jsonl"
    bank.write_text(json.dumps({"id": "q1", "pregunta": "¿2+2?", "respuesta_esperada": "4", "tipo": "contiene"}) + "\n",
                    "utf-8")
    result = llm_router.evaluate(config, bank, role="critico")
    assert calls == [] and result["aviso"].startswith("modo offline") and not (tmp_path / "research").exists()


def test_llm_router_applies_the_data_policy(llm):
    llm_router, config, calls = llm
    results = llm_router.ask(config, "idea confidencial", role="critico")
    assert calls == ["http://localhost:11434/v1/chat/completions"]
    assert {r["modelo"]: ("texto" in r) for r in results} == {"local1": True, "externo": False}
    assert "no apta para información confidencial" in results[1]["omitido"]
    results = llm_router.ask(config, "falla", role="critico", sensitivity="normal")
    assert SECRET not in json.dumps(results) and "error" in results[1]
    with pytest.raises(ValueError):
        llm_router.ask(config, "x", role="critico", sensitivity="publica")


def test_llm_grade_and_evaluate_with_simulated_models(llm, tmp_path):
    # D7-12: el banco de pruebas de dominio (grade/evaluate) con los modelos simulados.
    llm_router, config, calls = llm
    assert llm_router.grade({"tipo": "exacta", "respuesta_esperada": "Sí"}, "  si ")
    assert llm_router.grade({"tipo": "contiene", "respuesta_esperada": "rumen"}, "El RUMEN degrada la proteína")
    assert llm_router.grade({"tipo": "contiene_todos", "respuesta_esperada": ["a", "b"]}, "a y b")
    assert not llm_router.grade({"tipo": "contiene_todos", "respuesta_esperada": ["a", "z"]}, "a y b")
    assert llm_router.grade({"tipo": "sino", "respuesta_esperada": "no"}, "No, porque se degrada")
    assert llm_router.grade({"tipo": "numerica", "respuesta_esperada": 4, "tolerancia": 0.5}, "Unos 4,2 kg/d")
    assert not llm_router.grade({"tipo": "numerica", "respuesta_esperada": 4}, "sin cifra")
    with pytest.raises(ValueError):
        llm_router.grade({"tipo": "desconocido", "respuesta_esperada": "x"}, "x")
    bank = tmp_path / "banco.jsonl"
    bank.write_text("\n".join(json.dumps(item) for item in (
        {"id": "q1", "pregunta": "¿Qué opinas?", "respuesta_esperada": "opinion", "tipo": "contiene"},
        {"id": "q2", "pregunta": "¿2+2?", "respuesta_esperada": "4", "tipo": "numerica"})) + "\n", "utf-8")
    result = llm_router.evaluate(config, bank, role="critico")
    assert result["n_items"] == 2 and len(calls) == 4            # banco no confidencial: los dos modelos
    assert result["resultados"]["local1"] == {"aciertos": 1, "total": 2, "exactitud": 0.5}
    rows = [json.loads(line) for line in Path(result["detalle"]).read_text("utf-8").splitlines()]
    assert {(r["item"], r["modelo"], r["correcto"]) for r in rows} == {
        ("q1", "local1", True), ("q1", "externo", True), ("q2", "local1", False), ("q2", "externo", False)}


def test_llm_cli_offline_omits_models(proj, router, capsys, monkeypatch):
    (proj / "config").mkdir(exist_ok=True)
    (proj / "config" / "modelos.toml").write_text(MODELS, "utf-8")
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("petición en modo offline"))
    code, out, _ = run(capsys, ["--offline", "llm", "preguntar", "--rol", "critico", "--prompt", "hola"])
    # Sin ninguna respuesta (todos omitidos) el resultado es negativo: código 1, no 0 (D9-16, tabla de D10-8).
    assert code == cli.SALIDA_RESULTADO and "modo offline" in out and not router.calls
    sent = []

    def post(url, json=None, headers=None, timeout=None):
        sent.append(url)
        resp = Resp(200, {"choices": [{"message": {"content": "opinión"}}]}, url=url)
        resp.raise_for_status = lambda: None
        resp.json = lambda: {"choices": [{"message": {"content": "opinión"}}]}
        return resp

    monkeypatch.setattr(requests, "post", post)
    code, out, _ = run(capsys, ["llm", "preguntar", "--rol", "critico", "--prompt", "hola"])
    assert code == cli.SALIDA_OK and "opinión" in out and sent == ["http://localhost:11434/v1/chat/completions"]


# ============================================================================ doctor (D7-14, D10-12, D9-16)
def _doctor_ok_routes(net):
    net.add(r".", (200, "ok"))
    net.add(r"string-db\.org/api/json/version", (200, [{"string_version": "12.0"}]))
    net.add(r"ContentService/data/database/version", (200, "88"))
    net.add(r"chembl/api/data/status\.json", (200, {"chembl_db_version": "ChEMBL_35"}))
    net.add(r"rest\.uniprot\.org", (200, {"results": []}, {"X-UniProt-Release": "2025_04"}))


def test_doctor_offline_makes_no_request_and_says_what_it_would_check(proj, router, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_doctor_r", lambda: {"estado": "no_instalado"})
    code, out, _ = run(capsys, ["--offline", "doctor"])
    assert code == 0 and not router.calls and "MODO OFFLINE" in out and "https://api.crossref.org" in out
    code, out, _ = run(capsys, ["--offline", "--json", "doctor"])
    report = json.loads(out)
    assert {r["estado"] for r in report["red"].values()} <= {"no_comprobado", "sin_clave", "sin_token"}
    assert report["resultado"] == "ok" and not router.calls


def test_doctor_fails_when_essential_sources_are_blocked(proj, router, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_doctor_r", lambda: {"estado": "no_instalado"})
    code, out, _ = run(capsys, ["--json", "doctor"])
    report = json.loads(out)
    assert code == cli.SALIDA_RESULTADO
    assert set(report["fallos_esenciales"]) == {"PubMed (NCBI E-utilities)", "Europe PMC", "Crossref"}
    assert report["red"]["Crossref"]["estado"] == "bloqueado"
    assert events(proj, "doctor")[-1]["outputs"]["fallos_esenciales"] == report["fallos_esenciales"]


def test_doctor_checks_keys_lens_webfetch_and_records_versions(proj, router, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_doctor_r", lambda: {"estado": "no_instalado"})
    monkeypatch.setenv("OPENALEX_API_KEY", SECRET)
    monkeypatch.setenv("LENS_API_TOKEN", "tok-" + SECRET)
    _doctor_ok_routes(router)
    router.add(r"www\.ebi\.ac\.uk/chembl", (503, "caído"))                      # fuente no esencial caída
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text(json.dumps(
        {"permissions": {"allow": ["WebFetch(domain:eur-lex.europa.eu)"]}}), "utf-8")
    code, out, _ = run(capsys, ["--json", "doctor"])
    report = json.loads(out)
    assert code == 0 and report["resultado"] == "ok" and report["red"]["ChEMBL"]["estado"] == "http"
    assert report["versiones"] == {"UniProt": "2025_04", "STRING": "12.0", "Reactome": "88"}
    hosts = {re.match(r"https?://([^/?]+)", c["url"]).group(1) for c in router.calls}
    assert {"api.lens.org", "doi.org", "api.datacite.org", "eur-lex.europa.eu", "www.feedipedia.org",
            "version-12-0.string-db.org"} <= hosts
    openalex = [c for c in router.calls if "api.openalex.org" in c["url"]]
    assert openalex and openalex[0]["params"]["api_key"] == SECRET
    lens = [c for c in router.calls if "api.lens.org" in c["url"]][0]
    assert lens["method"] == "POST" and lens["headers"]["Authorization"].endswith(SECRET)
    assert report["webfetch"]["eur-lex.europa.eu"]["permitido_en_settings"] is True
    assert report["webfetch"]["doi.org"]["permitido_en_settings"] is False
    assert events(proj, "doctor")[-1]["outputs"]["versiones"]["STRING"] == "12.0"
    assert SECRET not in (proj / "research" / "procedencia.jsonl").read_text("utf-8")
    # D7-15: un cuerpo que no es una versión no se registra como versión de Reactome
    router.add(r"ContentService/data/database/version", (200, "{}"))
    code, out, _ = run(capsys, ["--json", "doctor"])
    report = json.loads(out)
    assert "Reactome" not in report["versiones"] and report["versiones_no_reconocidas"] == {"Reactome": "{}"}
    assert events(proj, "doctor")[-1]["outputs"]["versiones_no_reconocidas"] == {"Reactome": "{}"}
    code, out, _ = run(capsys, ["doctor"])
    assert "Reactome respondió sin una versión reconocible («{}»)" in out


MARKER = '{"doctor_hook": "ok", "python": "venv", "nutria": "/ruta/nutria/__init__.py"}'
# run_hook.sh de la versión que prueba el siguiente intérprete cuando uno falla (la del paquete de informes)
FALLTHROUGH_RUNNER = """#!/bin/sh
dir=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$dir/../.." && pwd)
payload=$(cat)
for py in "$root/.venv/bin/python" python3 python; do
  case "$py" in /*) [ -x "$py" ] || continue ;; *) command -v "$py" >/dev/null 2>&1 || continue ;; esac
  out=$(printf '%s' "$payload" | "$py" "$dir/$1" 2>/dev/null) && { printf '%s\\n' "$out"; exit 0; }
done
exit 0
"""


def _hermetic_python(proj, monkeypatch):
    """python3 del PATH = el intérprete de las pruebas, y <proyecto>/src = el src del repositorio: la sonda del hook
    no depende de cómo esté instalado nutria en la máquina."""
    bin_dir = proj / "bin_pruebas"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', "utf-8")   # conserva su venv
    (bin_dir / "python3").chmod(0o755)
    (proj / "src").symlink_to(ROOT / "src", target_is_directory=True)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


def _fake_venv(proj, script):
    venv = proj / ".venv" / "bin"
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "python").write_text(script, "utf-8")
    (venv / "python").chmod(0o755)
    return venv / "python"


def test_doctor_checks_the_hook_interpreter(proj, router, capsys, monkeypatch):
    # D10-12: doctor ejecuta una copia del propio run_hook.sh con una sonda en lugar del hook
    monkeypatch.setattr(cli, "_doctor_r", lambda: {"estado": "no_instalado"})
    _doctor_ok_routes(router)
    _hermetic_python(proj, monkeypatch)
    (proj / "scripts" / "hooks").mkdir(parents=True)
    (proj / "scripts" / "hooks" / "run_hook.sh").write_text((ROOT / "scripts" / "hooks" / "run_hook.sh").read_text(
        "utf-8"), "utf-8")
    _fake_venv(proj, "#!/bin/sh\necho 'ModuleNotFoundError: No module named jsonschema' >&2\nexit 3\n")
    code, out, _ = run(capsys, ["doctor"])
    runner_text = (ROOT / "scripts" / "hooks" / "run_hook.sh").read_text("utf-8")
    if "exit $?" in runner_text:      # run_hook.sh que usa el primer intérprete que encuentra: el hook falla
        assert code == cli.SALIDA_RESULTADO and "sin_dependencias" in out and "intérprete del hook" in out
    else:                             # run_hook.sh que prueba el siguiente: usa python3 y avisa
        assert code == 0 and "usa otro intérprete" in out
    seen = proj / "argumentos.txt"
    _fake_venv(proj, f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{seen}"\necho \'{MARKER}\'\n')
    code, out, _ = run(capsys, ["--json", "doctor"])
    hook = json.loads(out)["hook"]
    assert code == 0 and hook["estado"] == "ok" and hook["interprete"] == "venv"
    # la sonda directa antepone <raíz>/src a sys.path e importa lo que usa check_research_file.py; la copia de
    # run_hook.sh ejecuta la sonda como un hook (un fichero de scripts/hooks)
    text = seen.read_text("utf-8")
    args = text.splitlines()
    assert args[0] == "-c" and str(proj / "src") in args
    assert "sys.path.insert(0, sys.argv[1])" in text and "nutria.reports.check" in text and "jsonschema" in text
    assert any(a.endswith("doctor_probe.py") for a in args)
    (proj / ".venv" / "bin" / "python").unlink()                            # sin .venv ni Python: falla cerrado
    monkeypatch.setattr("shutil.which", lambda name: None)
    code, out, _ = run(capsys, ["--json", "doctor"])
    assert code == cli.SALIDA_RESULTADO and json.loads(out)["hook"]["estado"] == "no_encontrado"


def test_doctor_hook_check_follows_the_installed_runner(proj, router, capsys, monkeypatch):
    # Con un run_hook.sh que prueba el siguiente intérprete, una .venv rota no impide el hook si python3 sirve
    monkeypatch.setattr(cli, "_doctor_r", lambda: {"estado": "no_instalado"})
    _doctor_ok_routes(router)
    _hermetic_python(proj, monkeypatch)
    (proj / "scripts" / "hooks").mkdir(parents=True)
    (proj / "scripts" / "hooks" / "run_hook.sh").write_text(FALLTHROUGH_RUNNER, "utf-8")
    broken = _fake_venv(proj, "#!/bin/sh\necho 'ModuleNotFoundError: No module named jsonschema' >&2\nexit 3\n")
    code, out, _ = run(capsys, ["--json", "doctor"])
    hook = json.loads(out)["hook"]
    assert code == 0 and hook["estado"] == "ok" and hook["interprete"] != str(broken)
    assert "usa otro intérprete" in hook["detalle"] and str(broken) in hook["detalle"]
    assert [c["ok"] for c in hook["candidatos"]][:2] == [False, True]


def test_doctor_fails_without_project(tmp_path, router, capsys, monkeypatch):
    monkeypatch.delenv("NUTRIA_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_doctor_r", lambda: {"estado": "no_instalado"})
    code, out, _ = run(capsys, ["--offline", "--json", "doctor"])
    assert code == cli.SALIDA_RESULTADO and json.loads(out)["fallos_esenciales"] == ["nutria.toml", "base de datos"]
    code, out, _ = run(capsys, ["--json", "doctor"])                       # con red (bloqueada) tampoco
    assert code == cli.SALIDA_RESULTADO and router.calls
    assert not (tmp_path / "research").exists()                             # fuera de un proyecto no deja procedencia


def test_doctor_reads_the_webfetch_domains_from_docs_fuentes(tmp_path):
    # doctor comprueba los dominios que declara docs/FUENTES.md (no una copia que pueda desfasarse). Si esta prueba
    # falla tras editar FUENTES.md, mantén la frase «Para lectura con WebFetch: `dominio`, ... Comprueba con».
    domains, origin = cli._webfetch_dominios(ROOT)
    assert origin == "docs/FUENTES.md", "doctor no reconoce la lista de dominios de WebFetch de docs/FUENTES.md"
    assert "eur-lex.europa.eu" in domains and all("." in d and " " not in d for d in domains)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "FUENTES.md").write_text(
        "Para lectura con WebFetch: `eur-lex.europa.eu`,\n`joint-research-centre.ec.europa.eu`. Comprueba con algo.",
        "utf-8")
    assert cli._webfetch_dominios(tmp_path) == (["eur-lex.europa.eu", "joint-research-centre.ec.europa.eu"],
                                                 "docs/FUENTES.md")
    (tmp_path / "docs" / "FUENTES.md").write_text("Sin la frase esperada.", "utf-8")
    assert cli._webfetch_dominios(tmp_path) == (list(cli.WEBFETCH_DOMINIOS), "lista interna")
    assert cli._webfetch_dominios(tmp_path / "no_existe") == (list(cli.WEBFETCH_DOMINIOS), "lista interna")


def test_doctor_probes_the_domains_listed_in_the_project_docs(proj, router, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_doctor_r", lambda: {"estado": "no_instalado"})
    _doctor_ok_routes(router)
    (proj / "docs").mkdir()
    (proj / "docs" / "FUENTES.md").write_text("Para lectura con WebFetch: `www.boe.es`. Comprueba con doctor.", "utf-8")
    code, out, _ = run(capsys, ["--json", "doctor"])
    report = json.loads(out)
    assert code == 0 and list(report["webfetch"]) == ["www.boe.es"] and report["webfetch_origen"] == "docs/FUENTES.md"
    assert any(c["url"].startswith("https://www.boe.es/") for c in router.calls)
    code, out, _ = run(capsys, ["--offline", "doctor"])
    assert code == 0 and "WebFetch www.boe.es" in out and "la red NO se ha comprobado" in out


# ============================================================================ errores, códigos y recursos (D10-8, D9-4, D9-16)
def test_exit_codes_are_defined_once_and_documented_in_help(capsys):
    codes = {v for k, v in vars(cli).items() if k.startswith("SALIDA_")}
    assert codes == set(cli.CODIGOS_SALIDA) and len(codes) == len([k for k in vars(cli) if k.startswith("SALIDA_")])
    assert len({tipo for tipo, _ in cli.CODIGOS_SALIDA.values()}) == len(codes)
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for code in codes:
        assert re.search(rf"^\s+{code}\s", out, re.MULTILINE), code


def _assert_json_error(out, err, code, tipo):
    payload = json.loads(out)
    assert set(payload) >= {"error", "codigo", "tipo"} and payload["codigo"] == code and payload["tipo"] == tipo
    assert "Traceback" not in err
    # Con --json, el texto del error sale también por stderr (lo que lee una persona, y lo que leen las pruebas de
    # otros paquetes escritas antes de la salida en JSON); stdout solo lleva el objeto JSON.
    assert payload["error"] in err
    return payload


def test_errors_with_json_are_structured_and_codes_are_coherent(proj, router, capsys, monkeypatch):
    cases = [
        (["--json", "meta", "analizar", "noexiste.csv"], cli.SALIDA_ENTRADA, "entrada"),
        (["--json", "buscar", "pubmed", "x"], cli.SALIDA_RED, "red"),
        (["--json", "--offline", "buscar", "pubmed", "x"], cli.SALIDA_RED, "red"),
        (["--json", "buscar", "pubmed"], cli.SALIDA_ENTRADA, "entrada"),                       # argparse
        (["--json", "cache", "importar", "noexiste.json"], cli.SALIDA_ENTRADA, "entrada"),
        (["--json", "normativa", "eurlex", "767/2009"], cli.SALIDA_ENTRADA, "entrada"),
    ]
    for argv, code, tipo in cases:
        got, out, err = run(capsys, argv)
        assert got == code, argv
        _assert_json_error(out, err, code, tipo)
    assert _assert_json_error(*run(capsys, ["--json", "--offline", "buscar", "pubmed", "x"])[1:], 4, "red")["motivo"] \
        == "offline"
    # Una orden que se niega (rol, permisos...) da siempre un error JSON con un código de la tabla, sin traza.
    got, out, err = run(capsys, ["--json", "--agente", "evaluador-1", "procedencia"])
    payload = _assert_json_error(out, err, got, cli.CODIGOS_SALIDA[got][0])
    assert got in (cli.SALIDA_ENTRADA, cli.SALIDA_REGISTRO) and payload["error"]
    opp = json.loads((EXAMPLES / "oportunidad_ejemplo.json").read_text("utf-8"))
    (proj / "o.json").write_text(json.dumps(opp), "utf-8")
    assert run(capsys, ["oportunidad", "nueva", "--datos", "o.json"])[0] == 0
    got, out, err = run(capsys, ["--json", "oportunidad", "estado", "O-0001", "en_validacion", "--motivo", "x"])
    _assert_json_error(out, err, cli.SALIDA_REGISTRO, "registro")


def test_a_missing_registry_identifier_has_the_same_code_in_every_order(proj, capsys):
    # D10-8: «un recurso inexistente es 1 o 2 según la orden»; ahora un identificador que no existe en el registro es
    # siempre el código 3, lo detecte la orden (SystemExit con mensaje) o la base de datos (RegistryError)
    for argv in (["oportunidad", "ver", "O-9999"], ["hipotesis", "ver", "H-9999"],
                 ["oportunidad", "estado", "O-9999", "descartada", "--motivo", "x"],
                 ["puntuar", "compuerta", "O-9999", "seguridad", "bloqueada", "--motivo", "x"]):
        got, out, err = run(capsys, ["--json", *argv])
        assert got == cli.SALIDA_REGISTRO, argv
        assert "O-9999" in _assert_json_error(out, err, cli.SALIDA_REGISTRO, "registro")["error"] or "H-9999" in out
    code, out, err = run(capsys, ["oportunidad", "ver", "O-9999"])
    assert code == cli.SALIDA_REGISTRO and err.startswith("Error de registro: No existe O-9999")
    # quien llama a main() desde Python sigue recibiendo SystemExit con el mensaje de la orden, y con el código
    with pytest.raises(SystemExit, match="No existe O-9999") as info:
        main(["oportunidad", "ver", "O-9999"])
    assert info.value.code == cli.SALIDA_REGISTRO
    with pytest.raises(SystemExit, match="Indica --doi o --pmid") as info:
        main(["fuente", "agregar"])
    assert info.value.code == cli.SALIDA_ENTRADA
    # un proyecto sin base de datos es una condición previa (2), aunque el mensaje empiece por «No existe»
    (proj / "research" / "nutria.sqlite").unlink()
    code, out, err = run(capsys, ["--json", "oportunidad", "ver", "O-0001"])
    assert code == cli.SALIDA_ENTRADA and "nutria init" in _assert_json_error(out, err, 2, "entrada")["error"]


@pytest.mark.parametrize("argv", [["meta", "analizar", "{d}"], ["oportunidad", "nueva", "--datos", "{d}"],
                                  ["extraccion", "agregar", "--datos", "{d}"], ["econ", "ganadero", "{d}"],
                                  ["informe", "comprobar", "{d}"], ["afirmacion", "agregar", "--datos", "{f}/x.json"]])
def test_oserror_inputs_give_a_message_and_the_input_code(proj, capsys, argv):
    # D9-4 (b): un directorio o una ruta imposible dan un mensaje y el código de entrada, sin traza (las órdenes son
    # de otros paquetes: pueden comprobar la ruta antes y dar su propio mensaje, pero nunca una traza)
    (proj / "fichero.txt").write_text("x", "utf-8")
    argv = [a.format(d=str(proj / "research"), f=str(proj / "fichero.txt")) for a in argv]
    code, out, err = run(capsys, argv)
    assert code == cli.SALIDA_ENTRADA and "Traceback" not in err and err.strip()
    code, out, err = run(capsys, ["--json", *argv])
    assert _assert_json_error(out, err, cli.SALIDA_ENTRADA, "entrada")["error"]


@pytest.mark.parametrize("argv, kind", [(["cache", "importar", "{d}"], "es un directorio"),
                                        (["cache", "importar", "{f}/x.json"], "no es un directorio"),
                                        (["cache", "exportar", "{d}", "--forzar"], "es un directorio")])
def test_oserror_in_own_orders_names_the_path_and_the_cause(proj, capsys, argv, kind):
    (proj / "fichero.txt").write_text("x", "utf-8")
    argv = [a.format(d=str(proj / "research"), f=str(proj / "fichero.txt")) for a in argv]
    code, out, err = run(capsys, argv)
    assert code == cli.SALIDA_ENTRADA and "Traceback" not in err
    assert err.startswith("Error: No se puede usar") and f"({kind})" in err
    assert not list(proj.glob("research.*.tmp"))                    # sin ficheros temporales a medias


def test_permission_error_is_an_input_error(proj, capsys, monkeypatch):
    def denied(path):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(cli, "_load_json", denied)
    code, _, err = run(capsys, ["oportunidad", "nueva", "--datos", "secreto.json"])
    assert code == cli.SALIDA_ENTRADA and "(sin permiso)" in err and "Traceback" not in err


def test_internal_errors_have_their_own_code_and_no_traceback(proj, capsys, monkeypatch):
    def broken(args, cfg):
        raise AttributeError("'str' object has no attribute 'get'")

    monkeypatch.setattr(cli, "cmd_estado", broken)
    parser_build = cli.build_parser

    def build():
        parser = parser_build()
        for action in parser._actions:  # noqa: SLF001
            if isinstance(action, __import__("argparse")._SubParsersAction):  # noqa: SLF001
                action.choices["estado"].set_defaults(func=broken)
        return parser

    monkeypatch.setattr(cli, "build_parser", build)
    code, out, err = run(capsys, ["estado"])
    assert code == cli.SALIDA_INTERNO and err.startswith("Error interno:") and "Traceback" not in err
    code, out, err = run(capsys, ["--json", "estado"])
    _assert_json_error(out, err, cli.SALIDA_INTERNO, "interno")
    monkeypatch.setenv("NUTRIA_DEPURAR", "1")
    code, out, err = run(capsys, ["estado"])
    assert code == cli.SALIDA_INTERNO and "Traceback" in err


def test_keyboard_interrupt_has_its_own_code(proj, capsys, monkeypatch):
    def interrupted(args, cfg):
        raise KeyboardInterrupt

    parser_build = cli.build_parser

    def build():
        parser = parser_build()
        for action in parser._actions:  # noqa: SLF001
            if isinstance(action, __import__("argparse")._SubParsersAction):  # noqa: SLF001
                action.choices["estado"].set_defaults(func=interrupted)
        return parser

    monkeypatch.setattr(cli, "build_parser", build)
    assert run(capsys, ["estado"])[0] == cli.SALIDA_INTERRUMPIDA


def _verification(identifier, status, ok):
    """Verification real (de nutria.verify.citations): «ok» exige verificada, retractación comprobada y sin
    discrepancia; con ok=False y estado verificada se simula una retractación desconocida."""
    from nutria.verify import citations
    return citations.Verification(identifier=identifier, kind="doi", status=status, title="T", message="m",
                                  retracted=False if ok else None)


@pytest.mark.parametrize("status, ok, code", [("error_red", False, 4), ("offline", False, 4), ("no_encontrada", False, 6),
                                              ("verificada", False, 1), ("verificada", True, 0)])
def test_verification_exit_codes_match_the_table(proj, capsys, monkeypatch, status, ok, code):
    from nutria.verify import citations
    monkeypatch.setattr(citations, "verify_doi", lambda client, doi, *a, **k: _verification(doi, status, ok))
    assert run(capsys, ["verificar", "--doi", "10.1/x"])[0] == code
    if status != "verificada":
        got, out, err = run(capsys, ["--json", "fuente", "agregar", "--doi", "10.1/x"])
        assert got == code and json.loads(out)["codigo"] == code


@pytest.mark.parametrize("statuses, code", [
    (["verificada", "verificada"], cli.SALIDA_OK),
    (["verificada", "no_encontrada"], cli.SALIDA_RESULTADO),
    (["no_encontrada", "error_red"], cli.SALIDA_RED),
    (["offline", "verificada"], cli.SALIDA_RED),
])
def test_pending_verification_batch_never_exits_zero_on_failures(proj, capsys, monkeypatch, statuses, code):
    # D10-8: `verificar --pendientes` salía con 0 aunque la red fallara o alguna fuente no se verificase
    from nutria import db
    from nutria.verify import citations
    conn = db.connect(proj / "research" / "nutria.sqlite")
    try:
        for i in range(2):
            db.upsert_source(conn, {"doi": f"10.1000/p{i}", "title": f"T{i}", "verified": False}, "prueba")
    finally:
        conn.close()
    answers = dict(zip(["10.1000/p0", "10.1000/p1"], statuses))
    # «verificada» con retractación desconocida: sin metadatos que registrar, el lote solo cuenta el estado
    monkeypatch.setattr(citations, "verify_doi",
                        lambda client, doi, *a, **k: _verification(doi, answers[doi], answers[doi] == "verificada"))
    got, out, _ = run(capsys, ["--json", "verificar", "--pendientes"])
    assert got == code and [o["estado"] for o in json.loads(out)] == statuses


def test_main_closes_http_sessions_and_database_connections(proj, router, capsys, monkeypatch):
    closed = []
    original = HttpClient.close
    monkeypatch.setattr(HttpClient, "close", lambda self: (closed.append(self), original(self)))
    pubmed_routes(router)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert run(capsys, ["buscar", "pubmed", "rumen", "--registrar"])[0] == 0
        assert run(capsys, ["estado"])[0] == 0
        gc.collect()
    assert len(closed) == 1 and not cli._RECURSOS  # noqa: SLF001
    assert not [w for w in caught if issubclass(w.category, ResourceWarning)]


def test_init_respects_nutria_home(tmp_path, capsys, monkeypatch):
    work = tmp_path / "trabajo"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("NUTRIA_HOME", "../nh_home")
    code, out, _ = run(capsys, ["--json", "init"])
    home = (work / "../nh_home").resolve()
    data = json.loads(out)
    assert code == 0 and data["nutria_home"] and Path(data["raiz"]) == home
    assert (home / "nutria.toml").is_file() and (home / "research" / "nutria.sqlite").is_file()
    assert not (work / "nutria.toml").exists() and not (work / "research").exists()
    assert run(capsys, ["estado"])[0] == 0
    assert load_config().db_path == home / "research" / "nutria.sqlite"      # la misma raíz que las demás órdenes
    (tmp_path / "fichero").write_text("x", "utf-8")
    monkeypatch.setenv("NUTRIA_HOME", str(tmp_path / "fichero"))
    code, out, err = run(capsys, ["init"])
    assert code == cli.SALIDA_ENTRADA and "no es un directorio" in err and "Traceback" not in err


def test_network_error_messages_never_recommend_offline(cfg):
    from nutria.sources import openalex
    net = Router()
    with pytest.raises(NetworkError) as info:
        HttpClient(cfg, session=Session(net)).get_json(URL)
    assert info.value.motivo == "bloqueado" and "--offline" not in str(info.value)
    assert "nutria doctor" in str(info.value)
    assert "--offline" not in openalex.KEY_REQUIRED_MESSAGE


def test_http_client_closes_only_its_own_session_and_survives_an_unwritable_cache(cfg):
    owned = HttpClient(cfg)
    closed = []
    owned.session.close = lambda: closed.append(True)
    with owned:
        pass
    injected = Session(Router())
    injected.close = lambda: pytest.fail("no debe cerrar una sesión ajena")
    HttpClient(cfg, session=injected).close()
    assert closed == [True]
    cfg.cache_dir.parent.mkdir(parents=True, exist_ok=True)
    cfg.cache_dir.write_text("no es un directorio", "utf-8")        # la caché no se puede escribir
    net = Router()
    net.add(r"api\.example\.org", (200, '{"a": 1}'))
    client = HttpClient(cfg, session=Session(net))
    assert client.get_json(URL) == {"a": 1} and "No se pudo guardar la copia" in client.avisos[0]


def test_constraints_pin_every_project_dependency_within_pyproject_bounds():
    import tomllib
    from packaging.requirements import Requirement
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    wanted = [Requirement(r) for r in project["dependencies"]]
    wanted += [Requirement(r) for extra in project["optional-dependencies"].values() for r in extra]
    pins = {}
    for line in (ROOT / "requirements" / "constraints.txt").read_text("utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            name, version = line.split("==")
            pins[re.sub(r"[-_.]+", "-", name).lower()] = version.strip()
    for req in wanted:
        key = re.sub(r"[-_.]+", "-", req.name).lower()
        assert key in pins, req.name
        assert req.specifier.contains(pins[key], prereleases=True), (req, pins[key])
    # Ninguna versión preliminar (pueden desaparecer de PyPI y no reproducir el entorno).
    from packaging.version import Version
    assert not [f"{name}=={version}" for name, version in pins.items() if Version(version).is_prerelease]


# ============================================================================ validación de respuestas (D5-15, D3-16)
CAPTCHA = "<html><head><title>Please verify you are human</title></head><body>challenge</body></html>"


def test_a_response_that_fails_validation_is_neither_cached_nor_served(cfg):
    net = Router()
    net.add(r"api\.example\.org", (200, CAPTCHA))

    def check(page):
        return None if "<meta" in page else "sin metadatos"

    with pytest.raises(NetworkError, match="sin metadatos") as info:
        HttpClient(cfg, session=Session(net)).get_text(URL, validate=check)
    assert info.value.motivo == "respuesta" and not cache_files(cfg.cache_dir)
    assert read_events(cfg, action="http_error")[-1]["outputs"]["motivo"] == "respuesta"
    assert not read_events(cfg, action="http")                     # nada que confirme una copia
    # una copia ya guardada sin validar (p. ej. por otra versión) se descarta al validarla y se vuelve a pedir
    HttpClient(cfg, session=Session(net)).get_text(URL)
    net.add(r"api\.example\.org", (200, '<meta property="eli:title" content="T">'))
    client = HttpClient(cfg, session=Session(net))
    assert client.get_text(URL, validate=check).startswith("<meta") and len(net.calls) == 3
    assert "no es una respuesta válida de la fuente" in client.avisos[0]
    # una validación que falla con una excepción cuenta como problema (falla en cerrado)
    with pytest.raises(NetworkError, match="ZeroDivisionError"):
        HttpClient(cfg, session=Session(net), fresh=True).get_text(URL, validate=lambda page: 1 / 0)


def test_eurlex_challenge_page_is_not_cached_and_the_next_query_gets_the_real_page(proj, router, capsys):
    router.add(r"eur-lex\.europa\.eu", (200, CAPTCHA))
    code, out, err = run(capsys, ["normativa", "eurlex", "32009R0767"])
    assert code == cli.SALIDA_RED and "sin metadatos reconocibles" in err
    assert not cache_files(proj / ".cache")
    router.add(r"eur-lex\.europa\.eu", (200, EURLEX_PAGE))         # minutos después, la página real
    code, out, _ = run(capsys, ["--json", "normativa", "eurlex", "32009R0767"])
    assert code == 0 and json.loads(out)["fecha_documento"] == "2009-07-13" and not json.loads(out)["cache"]
    assert len(cache_files(proj / ".cache")) == 1
    router.add(r"eur-lex\.europa\.eu", (200, CAPTCHA))             # la copia válida se sigue sirviendo
    code, out, _ = run(capsys, ["--json", "normativa", "eurlex", "32009R0767"])
    assert code == 0 and json.loads(out)["cache"]


# ============================================================================ exportar e importar: comprobaciones
def test_cache_import_checks_the_expected_hash_before_importing(proj, router, capsys, monkeypatch, tmp_path):
    pubmed_routes(router)
    assert run(capsys, ["buscar", "pubmed", "rumen choline"])[0] == 0
    bundle = tmp_path / "cache.json"
    code, out, _ = run(capsys, ["--json", "cache", "exportar", str(bundle)])
    digest = json.loads(out)["sha256"]
    assert code == 0 and digest == hashlib.sha256(bundle.read_bytes()).hexdigest()
    for path in cache_files(proj / ".cache"):
        path.unlink()
    imports = len(events(proj, "cache_importar"))
    code, out, err = run(capsys, ["cache", "importar", str(bundle), "--sha256", "0" * 64])
    assert code == cli.SALIDA_ENTRADA and "no es la esperada" in err and not cache_files(proj / ".cache")
    assert len(events(proj, "cache_importar")) == imports                # nada importado ni registrado
    assert run(capsys, ["cache", "importar", str(bundle), "--sha256", "abc"])[0] == cli.SALIDA_ENTRADA
    code, out, _ = run(capsys, ["cache", "importar", str(bundle), "--sha256", digest.upper()])
    assert code == 0 and "Importadas 2" in out and "sin --sha256" not in out
    assert events(proj, "cache_importar")[-1]["inputs"]["sha256"] == digest
    code, out, _ = run(capsys, ["cache", "importar", str(bundle)])
    assert code == 0 and "sin --sha256 no se comprueba" in out


def test_confidential_search_with_registration_warns_that_sources_stay_in_the_registry(proj, router, capsys):
    pubmed_routes(router)
    code, out, _ = run(capsys, ["buscar", "pubmed", "idea secreta", "--sensibilidad", "confidencial", "--registrar"])
    assert code == 0 and "quedan en claro en el registro" in out
    assert "idea secreta" not in (proj / "research" / "procedencia.jsonl").read_text("utf-8")


# ============================================================================ router de modelos: proveedores (D9-12)
@pytest.mark.parametrize("provider, base, expected_url", [
    ("anthropic", None, "https://api.anthropic.com/v1/messages"),
    ("openai_compatible", "http://localhost:8000/v1/", "http://localhost:8000/v1/chat/completions"),
    ("gemini", None, "https://generativelanguage.googleapis.com/v1beta/models/m1:generateContent"),
])
def test_llm_router_builds_each_provider_request_and_parses_the_answer(tmp_path, monkeypatch, provider, base,
                                                                      expected_url):
    from nutria.llm import router as llm_router
    monkeypatch.setenv("CLAVE_PRUEBA", SECRET)
    answers = {"anthropic": {"content": [{"type": "text", "text": "hola"}], "usage": {"in": 1}, "model": "m1-r"},
               "openai_compatible": {"choices": [{"message": {"content": "hola"}}], "usage": {"in": 1},
                                     "model": "m1-r"},
               "gemini": {"candidates": [{"content": {"parts": [{"text": "ho"}, {"text": "la"}]}}],
                          "usageMetadata": {"in": 1}, "modelVersion": "m1-r"}}
    sent = {}

    def post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, body=json, headers=headers, timeout=timeout)
        resp = Resp(200, answers[provider], url=url)
        resp.raise_for_status = lambda: None
        resp.json = lambda: answers[provider]
        return resp

    monkeypatch.setattr(llm_router.requests, "post", post)
    spec = llm_router.ModelSpec(id="x", provider=provider, model="m1", base_url=base, api_key_env="CLAVE_PRUEBA",
                                roles=["critico"], data_policy="local", verified="", active=True)
    config = Config(root=tmp_path, cache_dir=tmp_path / "cache")
    out = llm_router._call(config, spec, "pregunta", "sistema", 50, 0.2)   # noqa: SLF001
    assert out["text"] == "hola" and out["model_reported"] == "m1-r" and sent["url"] == expected_url
    assert sent["timeout"] >= 120 and json.dumps(sent["body"]).count("pregunta") == 1
    assert SECRET in json.dumps(sent["headers"])                     # la clave solo viaja en la cabecera
    assert "sistema" in json.dumps(sent["body"])
    config.offline = True
    with pytest.raises(ValueError, match="modo offline"):
        llm_router._call(config, spec, "pregunta", None, 50, None)    # noqa: SLF001


# ============================================================================ PubMed: resúmenes por lotes (D7-12, D7-13)
EFETCH = """<PubmedArticleSet>{}</PubmedArticleSet>"""
ARTICLE = """<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article><Abstract>
<AbstractText Label="OBJECTIVE">Objetivo {pmid}.</AbstractText><AbstractText>Resultado.</AbstractText></Abstract>
<PublicationTypeList><PublicationType>Journal Article</PublicationType></PublicationTypeList></Article>
<MeshHeadingList><MeshHeading><DescriptorName>Rumen</DescriptorName></MeshHeading></MeshHeadingList>
</MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1000/{pmid}</ArticleId></ArticleIdList>
</PubmedData></PubmedArticle>"""


def test_pubmed_abstracts_are_fetched_in_batches_and_a_failure_flags_the_result(cfg, monkeypatch):
    monkeypatch.setattr(pubmed, "ESUMMARY_BATCH", 2)
    ids = ["11", "12", "13"]

    def handler(url, params, kw):
        if url.endswith("esearch.fcgi"):
            return {"esearchresult": {"count": "3", "idlist": ids}}
        if url.endswith("esummary.fcgi"):
            return {"result": {"uids": params["id"].split(","),
                               **{i: {"uid": i, "title": f"T{i}", "articleids": [], "pubtype": []}
                                  for i in params["id"].split(",")}}}
        return EFETCH.format("".join(ARTICLE.format(pmid=i) for i in params["id"].split(",")))

    client = FakeClient(cfg, handler)
    res = pubmed.search_records(client, "rumen", retmax=3, with_abstracts=True)
    assert [len(p["id"].split(",")) for u, p in client.calls if u.endswith("efetch.fcgi")] == [2, 1]
    assert res["records"][0].abstract == "OBJECTIVE: Objetivo 11.\nResultado." and res["records"][2].extra["mesh"] == [
        "Rumen"]
    assert pubmed.parse_efetch(EFETCH.format(ARTICLE.format(pmid="5")))["5"]["doi"] == "10.1000/5"

    def failing(url, params, kw):
        return NetworkError("HTTP 503 en efetch") if url.endswith("efetch.fcgi") else handler(url, params, kw)

    res = pubmed.search_records(FakeClient(cfg, failing), "rumen", retmax=3, with_abstracts=True)
    assert res["incompleto"] and any("no se pudieron descargar los resúmenes" in a for a in res["avisos"])


def test_buscar_europepmc_pages_and_logs_identifiers_of_records_without_doi(proj, router, capsys, monkeypatch):
    # D7-7 y D7-13: con Europe PMC, cursorMark hasta --max; un preprint sin DOI ni PMID queda identificado igualmente
    monkeypatch.setattr(europepmc, "PAGE_MAX", 2)
    pages = {"%2A": ("C2", [{"id": "PPR1", "source": "PPR", "title": "Preprint"},
                            {"id": "123", "source": "MED", "pmid": "123", "doi": "10.1000/x", "title": "Art"}]),
             "C2": ("C3", [{"id": "PPR2", "source": "PPR", "title": "Otro"}])}

    def epmc(method, url):
        following, hits = pages[re.search(r"cursorMark=([^&]+)", url).group(1)]
        return 200, {"hitCount": 9, "nextCursorMark": following, "resultList": {"result": hits}}

    router.add(r"europepmc/webservices/rest/search", epmc)
    code, out, _ = run(capsys, ["buscar", "europepmc", "rumen", "--max", "3"])
    assert code == 0 and "(2 páginas)" in out and "Hay más resultados" in out
    code, out, _ = run(capsys, ["--json", "buscar", "europepmc", "rumen", "--max", "3"])   # desde la caché
    assert code == 0 and json.loads(out)["siguiente_cursor"] == "C3"                      # para seguir después
    event = events(proj, "busqueda")[-1]
    assert event["outputs"]["paginas"] == 2 and event["outputs"]["total"] == 9
    assert event["outputs"]["identificadores"] == [{"europepmc": "PPR/PPR1"},
                                                   {"doi": "10.1000/x", "pmid": "123", "europepmc": "MED/123"},
                                                   {"europepmc": "PPR/PPR2"}]


def test_network_errors_are_labelled_by_their_cause(proj, router, capsys, monkeypatch):
    # D7-14: el código es el de red (4), pero el mensaje dice qué falló: credencial, respuesta o red
    code, out, err = run(capsys, ["patentes", "x", "--lens"])                      # sin LENS_API_TOKEN
    assert code == cli.SALIDA_RED and err.startswith("Error de credenciales:") and "LENS_API_TOKEN" in err
    monkeypatch.setenv("LENS_API_TOKEN", SECRET)
    router.add(r"api\.lens\.org", (403, '{"error": "token caducado"}'))
    code, out, err = run(capsys, ["--json", "patentes", "x", "--lens"])
    payload = _assert_json_error(out, err, cli.SALIDA_RED, "red")
    assert payload["motivo"] == "credenciales" and SECRET not in out + err
    router.add(r"eur-lex\.europa\.eu", (200, "<html>anti-robots</html>"))
    code, out, err = run(capsys, ["normativa", "eurlex", "32009R0767"])
    assert code == cli.SALIDA_RED and err.startswith("Respuesta no válida del servicio:")
    code, out, err = run(capsys, ["buscar", "pubmed", "x"])                          # bloqueado por el proxy
    assert code == cli.SALIDA_RED and err.startswith("Error de red:")
    # Una petición rechazada (HTTP 400), un error del servicio (5xx tras los reintentos), un límite de uso (429) o la
    # falta de la clave de OpenAlex tampoco son «errores de red» (el código sigue siendo el de red).
    for status, prefix, motivo in ((400, "Petición rechazada por el servicio:", "peticion"),
                                   (503, "Error del servicio:", "servicio"),
                                   (429, "Límite de uso del servicio:", "limite")):
        router.add(r"eutils\.ncbi\.nlm\.nih\.gov", (status, '{"error": "x"}'))
        code, out, err = run(capsys, ["buscar", "pubmed", f"consulta {status}"])
        assert code == cli.SALIDA_RED and err.startswith(prefix), (status, err)
        code, out, err = run(capsys, ["--json", "buscar", "pubmed", f"consulta {status}"])
        assert _assert_json_error(out, err, cli.SALIDA_RED, "red")["motivo"] == motivo
    code, out, err = run(capsys, ["buscar", "openalex", "rumen"])                     # sin OPENALEX_API_KEY
    assert code == cli.SALIDA_RED and err.startswith("Error de credenciales:") and "OPENALEX_API_KEY" in err
    assert not [c for c in router.calls if "openalex" in c["url"]]                     # ni siquiera se intenta


def test_a_closed_pipe_has_its_own_code_and_no_traceback(tmp_path):
    # D10-8: quien lee la salida la cierra antes de que termine (p. ej. `| head`): código 141 (128 + SIGPIPE), de la
    # tabla, sin traza ni «Exception ignored» al cerrar el intérprete.
    script = tmp_path / "salida_larga.py"
    script.write_text(
        "import sys\n"
        "from nutria import cli\n"
        "def larga(args, cfg):\n"
        "    for i in range(200000):\n"
        "        print('linea de salida', i)\n"
        "    return 0\n"
        "cli.cmd_estado = larga\n"
        "raise SystemExit(cli.main(['estado']))\n", "utf-8")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "NUTRIA_HOME": str(tmp_path)}
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                            cwd=tmp_path)
    proc.stdout.read(64)
    proc.stdout.close()
    err = proc.stderr.read().decode("utf-8", "replace")
    proc.stderr.close()
    assert proc.wait(timeout=60) == cli.SALIDA_TUBERIA == 141
    assert "Traceback" not in err and "Exception ignored" not in err and "BrokenPipe" not in err
    assert cli.CODIGOS_SALIDA[cli.SALIDA_TUBERIA][0] == "tuberia"


def test_a_failing_chembl_strategy_does_not_hide_the_others(proj, router, capsys):
    # Si ChEMBL rechaza un filtro (p. ej. HTTP 400 en la búsqueda por sinónimo), las demás estrategias siguen y el
    # resultado se marca incompleto; si fallan todas, error de red
    def target(method, url):
        if "target_synonym__icontains" in url:
            return 400, '{"error": "filtro no admitido"}'
        return 200, {"targets": [BOVINE_PPARA]}

    router.add(r"chembl/api/data/target", target)
    router.add(r"chembl/api/data/status\.json", (200, {"chembl_db_version": "ChEMBL_35"}))
    code, out, _ = run(capsys, ["--json", "diana", "Peroxisome", "--organismo", "Bos taurus"])
    data = json.loads(out)
    assert code == cli.SALIDA_RESULTADO and data["incompleto"] and data["resultados"][0]["target_chembl_id"] == "CHEMBL9"
    assert any("sinónimo o símbolo de gen falló" in a for a in data["avisos"])
    assert events(proj, "diana")[-1]["outputs"]["incompleto"] is True
    router.add(r"chembl/api/data/target", (400, '{"error": "x"}'))                 # otra consulta: sin copias
    assert run(capsys, ["diana", "Catalase", "--organismo", "Bos taurus"])[0] == cli.SALIDA_RED
