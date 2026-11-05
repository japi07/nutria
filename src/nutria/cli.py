"""Interfaz de línea de órdenes `nutria`: la API que usan los agentes y las personas.

Ejecuta `nutria --help` o `nutria <orden> --help`. Añade `--json` para salida legible por máquina
y `--agente <nombre>` (o la variable NUTRIA_AGENT) para atribuir la acción en la procedencia.

Las firmas humanas (`--firma-humana`) solo puede escribirlas una persona en su propia terminal: el hook
de Claude Code de este proyecto bloquea cualquier orden de un agente que las incluya.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from importlib import resources
from pathlib import Path

from . import __version__
from . import db as registry
from .config import CONFIG_FILENAME, DEFAULT_CONFIG_TEXT, load_config
from .http import HttpClient, NetworkError
from .provenance import environment_snapshot, log_event, read_events, sha256_file, sha256_text

SCHEMAS = ("oportunidad", "hipotesis", "extraccion", "afirmacion", "puntuacion")


# ============================================================================ utilidades
def _emit(args, data, text: str | None = None) -> None:
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print(text if text is not None else json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _es_evaluador(args) -> bool:
    """Los evaluadores independientes se identifican con --agente evaluador-<n> (regla 9 de CLAUDE.md)."""
    return str(getattr(args, "agente", None) or "").strip().lower().startswith("evaluador")


def _no_para_evaluadores(args, what: str) -> None:
    if _es_evaluador(args):
        raise SystemExit(f"Los evaluadores independientes no pueden {what}: puntúan a ciegas "
                         "(regla 9 de CLAUDE.md). Usa `oportunidad ver <ID> --sin-puntuaciones`.")


def _load_json(path: str):
    raw = sys.stdin.read() if path == "-" else Path(path).read_text("utf-8")
    return json.loads(raw)


def _schema(kind: str) -> dict:
    return json.loads(resources.files("nutria.data").joinpath(f"schemas/{kind}.schema.json").read_text("utf-8"))


def _validate(kind: str, data: dict) -> None:
    import jsonschema
    validator = jsonschema.Draft202012Validator(_schema(kind))
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(f"{'/'.join(map(str, e.path)) or '(raíz)'}: {e.message}" for e in errors[:8])
        raise SystemExit(f"JSON no válido para '{kind}': {detail}")
    if kind == "extraccion":
        declared = set(data.get("no_consta") or [])
        undeclared = [k for k in _schema("extraccion")["required"] if data.get(k) is None and k not in declared]
        if undeclared:
            raise SystemExit("Extracción: campos obligatorios a null sin declararlos en 'no_consta': "
                             + ", ".join(undeclared))


# Clientes HTTP y conexiones abiertos durante la orden en curso: main() los cierra al terminar.
_RECURSOS: list = []


def _conn(cfg):
    if not cfg.db_path.is_file():
        raise SystemExit(f"No existe la base de datos ({cfg.db_path}). Ejecuta `nutria init` en la raíz del proyecto.")
    conn = registry.connect(cfg.db_path)
    _RECURSOS.append(conn)
    return conn


def _client(cfg, fresh: bool = False, confidential: bool = False) -> HttpClient:
    """Cliente HTTP; con fresh=True no lee la caché (verificación de referencias); con confidential=True la
    procedencia guarda solo huellas de la consulta. main() lo cierra al terminar la orden."""
    client = HttpClient(cfg, fresh=fresh, confidential=confidential)
    _RECURSOS.append(client)
    return client


def _rubric(cfg):
    from .scoring.engine import load_rubric
    return load_rubric(cfg.root / "config" / "rubrica.toml")


def _records_text(records) -> str:
    lines = []
    for i, rec in enumerate(records, 1):
        lines.append(f"{i:>2}. {rec.citation()}")
        if rec.pub_types:
            lines.append(f"    tipo: {', '.join(rec.pub_types[:4])}" + (f" | citas: {rec.cited_by}" if rec.cited_by else ""))
        if rec.abstract:
            lines.append(f"    resumen: {rec.abstract[:400]}{'…' if len(rec.abstract) > 400 else ''}")
    return "\n".join(lines) or "(sin resultados)"


# ============================================================================ órdenes generales
def cmd_init(args, cfg) -> int:
    import os
    # Con NUTRIA_HOME, el proyecto se crea allí (como lo buscan las demás órdenes), no en el directorio actual.
    home = os.environ.get("NUTRIA_HOME", "").strip()
    root = Path(home).expanduser().resolve() if home else Path.cwd()
    root.mkdir(parents=True, exist_ok=True)
    conf = root / CONFIG_FILENAME
    if not conf.exists() or args.forzar:
        conf.write_text(DEFAULT_CONFIG_TEXT, "utf-8")
    cfg = load_config(root)
    for sub in ("oportunidades", "hipotesis", "informes", "planes", "revisiones", "datos", "evals"):
        (cfg.research_dir / sub).mkdir(parents=True, exist_ok=True)
    registry.init_db(cfg.db_path).close()
    log_event(cfg, "init", outputs={"entorno": environment_snapshot(cfg.root)})
    _emit(args, {"raiz": str(root), "configuracion": str(conf), "base_de_datos": str(cfg.db_path),
                 "nutria_home": bool(home)},
          f"Proyecto inicializado en {root}{' (NUTRIA_HOME)' if home else ''}\n  configuración: {conf}\n"
          f"  base de datos: {cfg.db_path}")
    return 0


# Fuentes que comprueba `nutria doctor`: (nombre, URL, esencial). Las esenciales son las que necesitan la búsqueda
# bibliográfica y la verificación de referencias (sin ellas no hay sello válido); si alguna falla, doctor sale con
# código 1. STRING usa la dirección configurada (URL None); OpenAlex exige clave y Lens (aparte), token. Las URL de
# doi.org, DataCite y EUR-Lex siguen la documentación de cada servicio y no se han probado contra ellos.
FUENTES_DOCTOR = (
    ("PubMed (NCBI E-utilities)", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?retmode=json", True),
    ("Europe PMC", "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=rumen&format=json&pageSize=1", True),
    ("Crossref", "https://api.crossref.org/works?rows=1&query=rumen", True),
    ("OpenAlex", "https://api.openalex.org/works?search=rumen&per-page=1", False),
    ("ChEMBL", "https://www.ebi.ac.uk/chembl/api/data/status.json", False),
    ("PubChem", "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/305/property/MolecularFormula/JSON", False),
    ("UniProt", "https://rest.uniprot.org/uniprotkb/search?query=gene:PPARA&size=1&format=json", False),
    ("STRING", None, False),
    ("Reactome", "https://reactome.org/ContentService/data/database/version", False),
    ("doi.org", "https://doi.org/api/handles/10.1000/182", False),
    ("DataCite", "https://api.datacite.org/dois?query=rumen&page%5Bsize%5D=1", False),
    ("EUR-Lex", "https://eur-lex.europa.eu/legal-content/ES/ALL/?uri=CELEX:32009R0767", False),
)
# Dominios de lectura con WebFetch que declara docs/FUENTES.md (los usan los agentes de Claude Code, no `nutria`).
WEBFETCH_DOMINIOS = ("eur-lex.europa.eu", "efsa.onlinelibrary.wiley.com", "food.ec.europa.eu", "europepmc.org",
                     "pubmed.ncbi.nlm.nih.gov", "www.feedipedia.org", "doi.org")
_ESTADOS_DOCTOR = {"ok": "OK", "http": "HTTP", "bloqueado": "BLOQUEADO por proxy/política de red",
                   "sin_conexion": "sin conexión", "no_comprobado": "no comprobado (modo offline)",
                   "sin_clave": "sin clave (no se comprueba)", "sin_token": "sin token (opcional; no se comprueba)"}


def _doctor_estado(res: dict) -> str:
    text = _ESTADOS_DOCTOR.get(res.get("estado"), res.get("estado"))
    if res.get("status") is not None:
        text = f"{text} ({res['status']})"
    return text


def _doctor_versiones(res: dict) -> dict:
    """Versión de la base de datos que respondió a la comprobación de doctor (si la expone)."""
    nombre, texto = res.get("nombre"), res.get("texto") or ""
    try:
        if nombre == "STRING":
            data = json.loads(texto)
            item = data[0] if isinstance(data, list) and data else {}
            return {"STRING": item.get("string_version")}
        if nombre == "ChEMBL":
            return {"ChEMBL": json.loads(texto).get("chembl_db_version")}
        if nombre == "Reactome":
            return {"Reactome": texto.strip()[:40] or None}
        if nombre == "UniProt":
            return {"UniProt": (res.get("cabeceras") or {}).get("x-uniprot-release")}
    except (ValueError, AttributeError):
        return {}
    return {}


def _doctor_hook(root: Path) -> dict:
    """Intérprete que usaría scripts/hooks/run_hook.sh (mismo orden de búsqueda) y si importa nutria y jsonschema."""
    import os
    import shutil
    import subprocess
    if not (root / "scripts" / "hooks" / "run_hook.sh").is_file():
        return {"estado": "no_aplica", "esencial": False,
                "detalle": "el proyecto no tiene los hooks de Claude Code (scripts/hooks/run_hook.sh)"}
    if not shutil.which("sh"):
        return {"estado": "no_encontrado", "esencial": True, "detalle": "no hay `sh` para ejecutar run_hook.sh"}
    interpreter = None
    for candidate in (root / ".venv" / "bin" / "python", root / ".venv" / "Scripts" / "python.exe", "python3", "python"):
        if isinstance(candidate, Path):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                interpreter = str(candidate)
                break
        elif shutil.which(candidate):
            interpreter = shutil.which(candidate)
            break
    if not interpreter:
        return {"estado": "no_encontrado", "esencial": True,
                "detalle": "run_hook.sh no encuentra Python: bloqueará las escrituras en research/ (falla cerrado)"}
    try:
        proc = subprocess.run([interpreter, "-c", "import nutria, jsonschema; print(nutria.__file__)"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"estado": "error", "esencial": True, "interprete": interpreter, "detalle": str(exc)[:300]}
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {"estado": "sin_dependencias", "esencial": True, "interprete": interpreter,
                "detalle": (lines[-1] if lines else f"código {proc.returncode}")[:300]
                + " — el hook bloqueará las escrituras en research/ que debía validar"}
    return {"estado": "ok", "esencial": True, "interprete": interpreter, "nutria": proc.stdout.strip()}


def _doctor_r() -> dict:
    """Rscript y los paquetes metafor y clubSandwich (opcionales: solo para `nutria meta exportar-r`)."""
    import shutil
    import subprocess
    rscript = shutil.which("Rscript")
    if not rscript:
        return {"estado": "no_instalado", "detalle": "opcional: solo hace falta para ejecutar el script de "
                                                     "`nutria meta exportar-r` (paquetes metafor y clubSandwich)"}
    out: dict = {"estado": "ok", "ruta": rscript}
    try:
        proc = subprocess.run([rscript, "--version"], capture_output=True, text=True, timeout=30)
        lines = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
        out["version"] = lines[0] if lines else None
        proc = subprocess.run([rscript, "-e", 'cat(sapply(c("metafor", "clubSandwich"), requireNamespace, '
                                              'quietly = TRUE))'], capture_output=True, text=True, timeout=120)
        flags = (proc.stdout or "").split()
        out["paquetes"] = ({"metafor": flags[0] == "TRUE", "clubSandwich": flags[1] == "TRUE"}
                           if len(flags) == 2 else None)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"estado": "error", "ruta": rscript, "detalle": str(exc)[:300]}
    return out


def cmd_doctor(args, cfg) -> int:
    from .sources import patents, stringdb
    client = _client(cfg)
    report = {"version": __version__, "raiz": str(cfg.root), "config": (cfg.root / CONFIG_FILENAME).is_file(),
              "base_datos": cfg.db_path.is_file(), "entorno": environment_snapshot(cfg.root), "offline": cfg.offline,
              "claves": {k: bool(cfg.secret(k)) for k in ("NCBI_API_KEY", "OPENALEX_API_KEY", "LENS_API_TOKEN")},
              "red": {}, "versiones": {}, "webfetch": {}}
    for nombre, url, esencial in FUENTES_DOCTOR:
        url = url or f"{stringdb.api_url(cfg)}/json/version"
        params = None
        if nombre == "OpenAlex":
            if not cfg.secret("OPENALEX_API_KEY"):
                report["red"][nombre] = {"estado": "sin_clave", "url": url, "esencial": esencial}
                continue
            params = {"api_key": cfg.secret("OPENALEX_API_KEY")}
        res = client.probe(url, params=params, headers={"Accept": "*/*"})
        res.update(nombre=nombre, url=url, esencial=esencial)
        report["versiones"].update({k: v for k, v in _doctor_versiones(res).items() if v})
        res.pop("texto", None)
        res.pop("nombre", None)
        report["red"][nombre] = res
    if cfg.secret("LENS_API_TOKEN"):
        res = client.probe(patents.LENS_URL, method="POST",
                           json_body={"query": {"query_string": {"query": "rumen"}}, "size": 1},
                           headers={"Authorization": f"Bearer {cfg.secret('LENS_API_TOKEN')}"})
        res.pop("texto", None)
        report["red"]["Lens"] = {**res, "url": patents.LENS_URL, "esencial": False}
    else:
        report["red"]["Lens"] = {"estado": "sin_token", "url": patents.LENS_URL, "esencial": False}
    allowed = None
    settings = cfg.root / ".claude" / "settings.json"
    if settings.is_file():
        try:
            allowed = set(json.loads(settings.read_text("utf-8")).get("permissions", {}).get("allow", []))
        except (OSError, ValueError, AttributeError):
            allowed = None
    for domain in WEBFETCH_DOMINIOS:
        res = client.probe(f"https://{domain}/", headers={"Accept": "*/*"})
        res.pop("texto", None)
        res["permitido_en_settings"] = None if allowed is None else f"WebFetch(domain:{domain})" in allowed
        report["webfetch"][domain] = res
    report["hook"] = _doctor_hook(cfg.root)
    report["r"] = _doctor_r()
    fallos = [] if report["config"] else ["nutria.toml"]
    if not report["base_datos"]:
        fallos.append("base de datos")
    fallos += [n for n, r in report["red"].items() if r.get("esencial") and r["estado"] not in ("ok", "no_comprobado")]
    if report["hook"].get("esencial") and report["hook"]["estado"] != "ok":
        fallos.append("intérprete del hook")
    report["fallos_esenciales"] = fallos
    report["resultado"] = "ok" if not fallos else "fallos_esenciales"
    log_event(cfg, "doctor", inputs={"offline": cfg.offline},
              outputs={"red": {n: r["estado"] for n, r in report["red"].items()}, "versiones": report["versiones"],
                       "webfetch": {d: r["estado"] for d, r in report["webfetch"].items()},
                       "hook": report["hook"]["estado"], "r": report["r"]["estado"], "fallos_esenciales": fallos},
              actor=args.agente)

    lines = [f"nutria {__version__} | raíz: {cfg.root}",
             f"  nutria.toml: {'sí' if report['config'] else 'NO (ejecuta nutria init)'} | "
             f"base de datos: {'sí' if report['base_datos'] else 'NO (ejecuta nutria init)'}",
             "  entorno: " + ", ".join(f"{k}={v}" for k, v in report["entorno"].items() if v),
             "  claves: " + ", ".join(f"{k}={'sí' if v else 'no'}" for k, v in report["claves"].items())]
    if cfg.offline:
        lines.append("  MODO OFFLINE: no se ha hecho ninguna petición de red. Con red se comprobaría:")
        lines += [f"    {n:<28} {r['url']}" for n, r in report["red"].items()]
        lines += [f"    WebFetch {d:<19} https://{d}/" for d in WEBFETCH_DOMINIOS]
    else:
        lines.append("  fuentes de nutria (esenciales marcadas con *):")
        lines += [f"  {'*' if r.get('esencial') else ' '} {n:<28} {_doctor_estado(r)}" for n, r in report["red"].items()]
        if report["versiones"]:
            lines.append("  versiones: " + " | ".join(f"{k} {v}" for k, v in report["versiones"].items()))
        lines.append("  dominios de WebFetch (agentes de Claude Code), accesibilidad desde esta máquina:")
        for domain, res in report["webfetch"].items():
            allowed_text = {None: "sin .claude/settings.json", True: "preautorizado", False: "NO preautorizado"}[
                res["permitido_en_settings"]]
            lines.append(f"    {domain:<30} {_doctor_estado(res)} | {allowed_text}")
    hook = report["hook"]
    lines.append(f"  intérprete del hook (scripts/hooks/run_hook.sh): {hook['estado']}"
                 + (f" ({hook['interprete']})" if hook.get("interprete") else "")
                 + (f": {hook['detalle']}" if hook.get("detalle") else ""))
    rinfo = report["r"]
    lines.append(f"  R (Rscript): {rinfo['estado']}" + (f" ({rinfo.get('version')})" if rinfo.get("version") else "")
                 + (f" | paquetes: {rinfo['paquetes']}" if rinfo.get("paquetes") else "")
                 + (f": {rinfo['detalle']}" if rinfo.get("detalle") else ""))
    if not report["claves"]["OPENALEX_API_KEY"]:
        lines.append("  OpenAlex exige una clave gratuita desde el 13-02-2026: https://openalex.org/settings/api")
    if not cfg.offline and any(r["estado"] != "ok" for r in report["red"].values()
                               if r["estado"] not in ("sin_clave", "sin_token")):
        lines.append("  Algunas fuentes no son accesibles: permite esos dominios en la red. Sin red, la verificación "
                     "de referencias queda incompleta y los sellos no son válidos.")
    lines.append("  Resultado: " + ("OK" if not fallos else "FALLAN comprobaciones esenciales: " + ", ".join(fallos)))
    _emit(args, report, "\n".join(lines))
    return SALIDA_RESULTADO if fallos else SALIDA_OK


def cmd_estado(args, cfg) -> int:
    conn = _conn(cfg)
    data = registry.summary(conn)
    opps = registry.list_opportunities(conn)
    lines = [f"Fuentes: {data['fuentes']} (verificadas {data['fuentes_verificadas']}, retractadas "
             f"{data['fuentes_retractadas']}) | Extracciones: {data['extracciones']} "
             f"(doble verificación {data['extracciones_doble_verificadas']}) | Decisiones: {data['decisiones']}",
             "Afirmaciones: " + (", ".join(f"{r['label']}={r['n']}" for r in data["afirmaciones"]) or "0"),
             "Hipótesis: " + (", ".join(f"{r['status']}={r['n']}" for r in data["hipotesis"]) or "0"),
             "Oportunidades: " + (", ".join(f"{r['status']}={r['n']}" for r in data["oportunidades"]) or "0"),
             f"Compuertas 'superada' pendientes de firma humana: {data['compuertas_sin_firmar']}"]
    for o in opps:
        lines.append(f"  {o['id']} [{o['status']}] {o['title']}")
    _emit(args, {**data, "lista_oportunidades": opps}, "\n".join(lines))
    return 0


# ============================================================================ literatura
OPENALEX_PAGE = 200   # resultados por página de OpenAlex (máximo que admite)


def _red_salida(client, avisos: list[str] | None = None) -> dict:
    """Copias de la caché servidas (con su fecha y antigüedad) y avisos, para la salida de una orden."""
    if client is None:
        return {"cache": [], "avisos": list(avisos or [])}
    return {"cache": list(client.cache_hits), "avisos": list(avisos or []) + list(client.avisos)}


def _red_lineas(cfg, client, avisos: list[str] | None = None, notas: list[str] | None = None) -> list[str]:
    """Líneas de texto con la fecha de cada copia servida desde la caché, los avisos y las notas."""
    lines = []
    hits = list(client.cache_hits) if client is not None else []
    if hits:
        lines.append("Modo offline: datos de la caché, no de la fuente actual:" if cfg.offline
                     else "Respuestas servidas desde la caché:")
        for hit in hits:
            origin = ", importada" if hit.get("origen") == "importada" else ""
            lines.append(f"  copia del {hit['fecha']} ({hit['antiguedad_dias']:.1f} días{origin}) {hit['url']}")
    lines += [f"AVISO: {a}" for a in _red_salida(client, avisos)["avisos"]]
    lines += [f"Nota: {n}" for n in notas or []]
    return lines


def _red_procedencia(client) -> list[dict]:
    """Copias de la caché para la procedencia: fecha, huella y origen (sin la URL, que puede llevar la consulta)."""
    if client is None:
        return []
    return [{"fecha": h["fecha"], "sha256": h["sha256"], "origen": h.get("origen")} for h in client.cache_hits]


def _version_fuente(func, client, nombre: str, avisos: list[str]) -> dict:
    """Versión de la base de datos consultada (`func(client)`), o {} con un aviso si no se pudo saber."""
    try:
        info = func(client) or {}
    except NetworkError as exc:
        avisos.append(f"No se pudo consultar la versión de {nombre}: {exc}")
        return {}
    if not info.get("version"):
        avisos.append(f"{nombre} no informó de su versión: el resultado no queda ligado a una versión concreta")
    return info


def cmd_buscar(args, cfg) -> int:
    from .sources import europepmc, openalex, pubmed
    from .sources.base import collect_pages, record_identifiers
    if args.max < 1:
        raise ValueError("--max debe ser un entero positivo")
    confidencial = getattr(args, "sensibilidad", "normal") == "confidencial"
    client = _client(cfg, confidential=confidencial)
    year_filter = (f" AND PUB_YEAR:[{args.desde or 1800} TO {args.hasta or 3000}]"
                   if (args.desde or args.hasta) else "")
    if args.fuente == "pubmed":
        res = pubmed.search_records(client, args.consulta, retmax=args.max, mindate=args.desde, maxdate=args.hasta,
                                    with_abstracts=args.resumenes)
    elif args.fuente == "europepmc":
        res = europepmc.search_all(client, args.consulta + year_filter, max_results=args.max)
    elif args.fuente == "efsa":
        res = europepmc.search_all(client, europepmc.efsa_query(f"({args.consulta}){year_filter}"),
                                   max_results=args.max)
    else:
        from_year = int(args.desde) if args.desde else None
        to_year = int(args.hasta) if args.hasta else None

        def page(cursor, size):
            found = openalex.search(client, args.consulta, per_page=size, cursor=cursor or "*",
                                    from_year=from_year, to_year=to_year)
            return {"count": found["count"], "records": found["records"], "next": found.get("next_cursor")}

        res = collect_pages(page, args.max, OPENALEX_PAGE, "OpenAlex")
    records = res["records"]
    api_avisos = list(res.get("avisos") or [])
    incompleto = bool(res.get("incompleto"))
    total = res.get("count") or 0
    registered = []
    if args.registrar and records:
        conn = _conn(cfg)
        with registry.transaction(conn):
            for rec in records:
                if rec.key:
                    registered.append(registry.upsert_source(conn, {**rec.to_dict(), "verified": False}, args.agente))
    identifiers = record_identifiers(records)
    inputs = {"fuente": args.fuente, "max": args.max, "desde": args.desde, "hasta": args.hasta,
              "sensibilidad": "confidencial" if confidencial else "normal"}
    outputs = {"total": total, "devueltos": len(records), "registrados": len(registered),
               "paginas": res.get("paginas"), "incompleto": incompleto, "cache": _red_procedencia(client)}
    if confidencial:
        # Ni la consulta ni lo que devolvió quedan en claro en el registro versionado.
        inputs["consulta_sha256"] = sha256_text(args.consulta)
        outputs["identificadores_sha256"] = sha256_text(json.dumps(identifiers, sort_keys=True))
        outputs["avisos_api"] = len(api_avisos)
    else:
        inputs["consulta"] = args.consulta
        outputs["identificadores"] = identifiers
        outputs["avisos"] = api_avisos
    log_event(cfg, "busqueda", inputs=inputs, outputs=outputs, actor=args.agente)
    notas = []
    if confidencial:
        notas.append("consulta confidencial: la procedencia guarda solo su huella SHA-256 (y la de los identificadores "
                     f"devueltos), pero la consulta ha viajado igualmente al servicio externo ({args.fuente})")
    header = f"{args.fuente}: {total} resultados totales; mostrando {len(records)}"
    if (res.get("paginas") or 0) > 1:
        header += f" ({res['paginas']} páginas)"
    if incompleto:
        header += "\nRESULTADOS INCOMPLETOS: una parte de la consulta falló (ver avisos)"
    elif total > len(records):
        header += f"\nHay más resultados: sube --max (ahora {args.max}) para recuperarlos"
    if res.get("query_translation"):
        header += f"\nTraducción de la consulta: {res['query_translation']}"
    if registered:
        header += (f"\nRegistradas {len(registered)} fuentes SIN verificar (no sirven para [PUB] hasta "
                   "`nutria verificar --pendientes`)")
    extra = _red_lineas(cfg, client, api_avisos, notas)
    _emit(args, {"total": total, "registros": [r.to_dict() for r in records], "registradas": registered,
                 "devueltos": len(records), "paginas": res.get("paginas"), "incompleto": incompleto,
                 **_red_salida(client, api_avisos), "notas": notas},
          header + "\n" + _records_text(records) + ("\n" + "\n".join(extra) if extra else ""))
    return SALIDA_RESULTADO if incompleto else SALIDA_OK


def cmd_fuente(args, cfg) -> int:
    from .http import NotFoundError
    from .verify.citations import verify_doi, verify_pmid
    conn = _conn(cfg)
    if args.accion == "listar":
        rows = registry.list_sources(conn, unverified_only=args.no_verificadas)
        text = "\n".join(f"{r['id']:<45} {'✓' if r['verified'] else '·'} "
                         f"{'RETRACTADO ' if r.get('retracted') == 1 else ('retractación? ' if r.get('retracted') is None else '')}"
                         f"{(r.get('title') or '')[:90]}" for r in rows) or "(sin fuentes)"
        _emit(args, rows, text)
        return 0
    if args.accion == "ver":
        row = registry.get_source(conn, args.id)
        if not row:
            raise SystemExit(f"No existe la fuente {args.id}")
        _emit(args, row)
        return 0
    # agregar: verifica contra la fuente autoritativa antes de registrar
    from .sources import crossref, openalex, pubmed
    client = _client(cfg, fresh=True)
    if args.doi:
        ver = verify_doi(client, args.doi)
        if ver.status != "verificada":
            raise ErrorOrden(f"No se registra: {ver.status} ({ver.message})", _salida_verificacion(ver))
        try:
            rec, via = crossref.work(client, ver.identifier).to_dict(), "crossref"
        except NotFoundError:
            rec, via = openalex.work_by_doi(client, ver.identifier).to_dict(), "openalex"
        rec["retracted"] = ver.retracted
    elif args.pmid:
        ver = verify_pmid(client, args.pmid)
        if ver.status != "verificada":
            raise ErrorOrden(f"No se registra: {ver.status} ({ver.message})", _salida_verificacion(ver))
        rec, via = pubmed.summaries(client, [args.pmid])[0].to_dict(), "pubmed"
        rec["retracted"] = ver.retracted
    else:
        raise SystemExit("Indica --doi o --pmid")
    rec.update(verified=True, verified_via=via, study_type=args.tipo_estudio, species=args.especie, notes=args.notas)
    source_id = registry.upsert_source(conn, rec, args.agente)
    log_event(cfg, "fuente_agregada", inputs={"doi": args.doi, "pmid": args.pmid},
              outputs={"id": source_id, "via": via, "retractado": rec.get("retracted")}, actor=args.agente)
    warn = ""
    if rec.get("retracted"):
        warn = "  ATENCIÓN: trabajo RETRACTADO"
    elif rec.get("retracted") is None:
        warn = "  ATENCIÓN: no se pudo comprobar la retractación; no servirá para [PUB] hasta comprobarla"
    _emit(args, {"id": source_id, "registro": rec}, f"Registrada y verificada: {source_id}{warn}\n  {rec.get('title')}")
    return 0


def _verification_text(res) -> str:
    retraction = {True: "RETRACTADO", False: "no retractado", None: "retractación DESCONOCIDA"}[res.retracted]
    text = f"{res.kind.upper()} {res.identifier}: {res.status} | {retraction}"
    if res.mismatch:
        text += " | DISCREPANCIA con la cita"
    if res.title:
        text += f"\n  {res.title} ({res.year}) {res.journal or ''}"
    if res.message:
        text += f"\n  {res.message}"
    return text


def cmd_verificar(args, cfg) -> int:
    from .verify.citations import verify_doi, verify_pmid
    targets = list(args.objetivos or [])
    if targets and targets[0] == "manual":
        if len(targets) != 2:
            raise SystemExit("Uso: nutria verificar manual <URL> --firma-humana NOMBRE [--nota TEXTO]")
        if not args.firma_humana:
            raise SystemExit("La verificación manual exige --firma-humana: solo una persona puede registrarla")
        conn = _conn(cfg)
        registry.add_manual_verification(conn, targets[1], args.firma_humana, args.nota)
        log_event(cfg, "verificacion_manual", inputs={"url": targets[1]}, outputs={"firma": args.firma_humana},
                  actor=args.agente)
        _emit(args, {"url": targets[1], "firma": args.firma_humana},
              f"Verificación manual registrada para {targets[1]} (firma: {args.firma_humana})")
        return 0
    client = _client(cfg, fresh=True)
    if args.doi or args.pmid:
        res = verify_doi(client, args.doi, args.cita) if args.doi else verify_pmid(client, args.pmid, args.cita)
        log_event(cfg, "verificacion", inputs={"doi": args.doi, "pmid": args.pmid},
                  outputs={"estado": res.status, "retractado": res.retracted, "discrepancia": res.mismatch},
                  actor=args.agente)
        _emit(args, res.to_dict(), _verification_text(res))
        return _salida_verificacion(res)
    if args.pendientes:
        conn = _conn(cfg)
        rows = registry.list_sources(conn, unverified_only=False)
        rows = [r for r in rows if not r.get("verified") or r.get("retracted") is None]
        out = []
        for r in rows:
            res = verify_doi(client, r["doi"]) if r.get("doi") else (verify_pmid(client, r["pmid"]) if r.get("pmid") else None)
            if res is None:
                continue
            if res.status == "verificada":
                registry.upsert_source(conn, {"doi": r.get("doi"), "pmid": r.get("pmid"), "verified": True,
                                              "verified_via": res.kind, "retracted": res.retracted}, args.agente)
            log_event(cfg, "verificacion_fuente", inputs={"id": r["id"]},
                      outputs={"estado": res.status, "retractado": res.retracted}, actor=args.agente)
            out.append({"id": r["id"], "estado": res.status, "retractado": res.retracted})
        _emit(args, out, "\n".join(f"{o['id']}: {o['estado']} | retractación "
                                   f"{ {True: 'SÍ', False: 'no', None: 'desconocida'}[o['retractado']] }" for o in out)
              or "No hay fuentes pendientes de verificar")
        return 0
    if not targets:
        raise SystemExit("Indica --doi, --pmid, --pendientes, un fichero .md o 'manual <URL>'")
    args.sin_secciones = True  # verificar comprueba referencias; la estructura de informe la exige `informe comprobar`
    return _check_report_file(args, cfg, Path(targets[0]), online=True)


# ============================================================================ moléculas, proteínas, redes
GHS_MAX_CID = 3   # CID de PubChem cuya clasificación GHS se consulta con --peligros


def _json_text(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _fuente_texto(fuente: dict) -> str:
    parts = [fuente.get("nombre", "")]
    if fuente.get("version"):
        parts.append(f"versión {fuente['version']}")
    if fuente.get("fecha_publicacion"):
        parts.append(f"publicada {fuente['fecha_publicacion']}")
    if fuente.get("url"):
        parts.append(fuente["url"])
    return "Fuente: " + ", ".join(p for p in parts if p)


def cmd_molecula(args, cfg) -> int:
    client = _client(cfg)
    avisos: list[str] = []
    incompleto = False
    if args.fuente == "pubchem":
        from .sources import pubchem
        data = pubchem.compound_by_name(client, args.nombre)
        fuente = {"nombre": "PubChem"}
        if args.peligros:
            for index, compound in enumerate(data):
                if index < GHS_MAX_CID:
                    ghs = pubchem.ghs_classification(client, compound["CID"])
                else:
                    ghs = pubchem.ghs_not_queried(f"solo se consulta la clasificación GHS de los {GHS_MAX_CID} "
                                                  "primeros CID")
                compound["GHS"] = ghs
                compound["GHS_frases_H"] = ghs["frases_H"]
                if ghs["estado"] == pubchem.GHS_ERROR:
                    incompleto = True
                    avisos.append(f"CID {compound.get('CID')}: {ghs['detalle']}")
        identifiers = [c.get("CID") for c in data]
    else:
        from .sources import chembl
        data = chembl.molecule_search(client, args.nombre, limit=args.max)
        if args.mecanismos:
            for m in data[:3]:
                m["mecanismos"] = chembl.mechanisms(client, m["chembl_id"])
        fuente = {"nombre": "ChEMBL", **_version_fuente(chembl.release, client, "ChEMBL", avisos)}
        identifiers = [m.get("chembl_id") for m in data]
    outputs = {"n": len(data), "identificadores": identifiers, "fuente": fuente, "incompleto": incompleto,
               "cache": _red_procedencia(client)}
    if args.fuente == "pubchem" and args.peligros:
        outputs["ghs"] = {str(c.get("CID")): c["GHS"]["estado"] for c in data}
    log_event(cfg, "molecula", inputs={"fuente": args.fuente, "nombre": args.nombre, "peligros": args.peligros,
                                       "mecanismos": args.mecanismos}, outputs=outputs, actor=args.agente)
    lines = [_json_text(data), _fuente_texto(fuente)]
    if args.fuente == "pubchem" and args.peligros:
        lines += [f"GHS CID {c.get('CID')}: {c['GHS']['estado']}"
                  + (f" ({', '.join(c['GHS']['frases_H'])})" if c["GHS"]["frases_H"] else "")
                  + f" — {c['GHS']['detalle']}" for c in data]
    lines += _red_lineas(cfg, client, avisos)
    _emit(args, {"resultados": data, "fuente": fuente, "incompleto": incompleto, **_red_salida(client, avisos)},
          "\n".join(lines))
    return SALIDA_RESULTADO if incompleto else SALIDA_OK


def cmd_diana(args, cfg) -> int:
    from .sources import chembl
    client = _client(cfg)
    avisos: list[str] = []
    tried: list[dict] = []
    targets = chembl.target_search(client, args.consulta, organism=args.organismo, limit=args.max, tried=tried)
    if args.organismo and not targets:
        avisos.append(f"ChEMBL no devolvió dianas de «{args.organismo.strip()}» para «{args.consulta}» ni por nombre "
                      "preferido, ni por número de acceso, ni por sinónimo o símbolo de gen. No demuestra que no exista "
                      "la diana en esa especie (ChEMBL tiene pocas dianas de especies ganaderas): busca el ortólogo con "
                      "`nutria proteina` (UniProt) y etiqueta la diana de otra especie como evidencia de otra especie")
    if args.actividades and targets:
        for t in targets[:2]:
            t["actividades"] = chembl.activities(client, target_chembl_id=t["target_chembl_id"], limit=25)
    fuente = {"nombre": "ChEMBL", **_version_fuente(chembl.release, client, "ChEMBL", avisos)}
    log_event(cfg, "diana", inputs={"consulta": args.consulta, "organismo": args.organismo},
              outputs={"n": len(targets), "identificadores": [t["target_chembl_id"] for t in targets],
                       "estrategias": tried, "fuente": fuente, "cache": _red_procedencia(client)}, actor=args.agente)
    lines = [_json_text(targets),
             "Estrategias: " + ", ".join(f"{t['estrategia']} ({t['n']})" for t in tried), _fuente_texto(fuente)]
    lines += _red_lineas(cfg, client, avisos)
    _emit(args, {"resultados": targets, "estrategias": tried, "fuente": fuente, **_red_salida(client, avisos)},
          "\n".join(lines))
    return SALIDA_OK


def cmd_proteina(args, cfg) -> int:
    from .sources import uniprot
    from .sources.base import taxon_id
    client = _client(cfg)
    avisos: list[str] = []
    rows = uniprot.search(client, args.consulta, taxon=taxon_id(args.especie) if args.especie else None,
                          size=args.max, reviewed_only=args.revisadas)
    fuente = {"nombre": "UniProtKB", **uniprot.release(client)}
    if not fuente.get("version"):
        avisos.append("UniProt no informó de su versión (cabecera X-UniProt-Release): el resultado no queda ligado a "
                      "una versión concreta")
    log_event(cfg, "proteina", inputs={"consulta": args.consulta, "especie": args.especie},
              outputs={"n": len(rows), "identificadores": [r["accesion"] for r in rows], "fuente": fuente,
                       "cache": _red_procedencia(client)}, actor=args.agente)
    text = "\n".join(f"{r['accesion']:<12} {','.join(r['genes'])[:20]:<20} {r['organismo'] or '':<18} "
                     f"{'rev' if r['revisada'] else 'no rev'}  {r['proteina'] or ''}" for r in rows) or "(sin resultados)"
    lines = [text, _fuente_texto(fuente)] + _red_lineas(cfg, client, avisos)
    _emit(args, {"resultados": rows, "fuente": fuente, **_red_salida(client, avisos)}, "\n".join(lines))
    return SALIDA_OK


def cmd_red(args, cfg) -> int:
    from .sources import stringdb
    from .sources.base import taxon_id
    client = _client(cfg)
    avisos: list[str] = []
    species = taxon_id(args.especie)
    if not stringdb.is_versioned(cfg):
        avisos.append(f"La dirección de STRING configurada ({stringdb.base_url(cfg)}) no es versionada: los resultados "
                      "pueden cambiar entre versiones (usa https://version-X-Y.string-db.org en nutria.toml)")
    data = {"interacciones": stringdb.network(client, args.genes, species, args.puntuacion_min)}
    if args.enriquecimiento:
        data["enriquecimiento"] = stringdb.enrichment(client, args.genes, species)
    fuente = {"nombre": "STRING", "url": stringdb.base_url(cfg),
              **_version_fuente(stringdb.version, client, "STRING", avisos)}
    log_event(cfg, "red_string", inputs={"genes": args.genes, "especie": species, "puntuacion_min": args.puntuacion_min},
              actor=args.agente,
              outputs={"interacciones": len(data["interacciones"]),
                       "enriquecimiento": len(data["enriquecimiento"]) if "enriquecimiento" in data else None,
                       "fuente": fuente, "cache": _red_procedencia(client)})
    lines = [_json_text(data), _fuente_texto(fuente)] + _red_lineas(cfg, client, avisos)
    _emit(args, {**data, "fuente": fuente, **_red_salida(client, avisos)}, "\n".join(lines))
    return SALIDA_OK


def cmd_rutas(args, cfg) -> int:
    from .sources import reactome
    client = _client(cfg)
    avisos: list[str] = []
    if args.accion == "buscar":
        data = reactome.search(client, " ".join(args.terminos), species=args.especie)
        identifiers = [r["id"] for r in data]
    else:
        data = reactome.analyze_genes(client, args.terminos)
        identifiers = [r["id"] for r in data["rutas"]]
        avisos.append("Análisis con proyección a humano: los genes de otras especies se asignan a ortólogos humanos "
                      "(aproximación)")
    fuente = {"nombre": "Reactome", **_version_fuente(reactome.version, client, "Reactome", avisos)}
    log_event(cfg, "rutas", inputs={"accion": args.accion, "terminos": args.terminos}, actor=args.agente,
              outputs={"n": len(identifiers), "identificadores": identifiers, "fuente": fuente,
                       "cache": _red_procedencia(client)})
    lines = [_json_text(data), _fuente_texto(fuente)] + _red_lineas(cfg, client, avisos)
    _emit(args, {"resultados": data, "fuente": fuente, **_red_salida(client, avisos)}, "\n".join(lines))
    return SALIDA_OK


def cmd_patentes(args, cfg) -> int:
    from .sources import patents
    registrar = bool(getattr(args, "registrar_consulta", False))
    client = None
    data: dict = {"enlaces": patents.search_links(args.consulta)}
    notas = ["los enlaces se generan aquí, sin enviar nada; ábrelos solo si la consulta puede salir del proyecto"]
    if args.lens:
        client = _client(cfg, confidential=not registrar)
        meta: dict = {}
        data["lens"] = patents.lens_search(client, args.consulta, size=args.max, meta=meta)
        data["lens_total"] = meta.get("total")
        notas.append("la consulta se ha enviado a la API de Lens (servicio externo): no protege una idea patentable")
    inputs: dict = {"lens": bool(args.lens), "max": args.max}
    lens_ids = [item.get("lens_id") for item in data.get("lens") or []]
    outputs: dict = {"n_lens": len(lens_ids) if args.lens else None, "cache": _red_procedencia(client)}
    if registrar:
        inputs["consulta"] = args.consulta
        outputs["lens_ids"] = lens_ids
    else:
        # Consulta de patentes: puede revelar una combinación inventiva; en el registro versionado solo su huella.
        inputs.update(consulta_sha256=sha256_text(args.consulta), confidencial=True)
        if lens_ids:
            outputs["lens_ids_sha256"] = sha256_text(json.dumps(lens_ids))
        notas.append("la procedencia guarda solo la huella SHA-256 de la consulta (usa --registrar-consulta para "
                     "guardarla en claro)")
    log_event(cfg, "busqueda_patentes", inputs=inputs, outputs=outputs, actor=args.agente)
    lines = [_json_text(data)] + _red_lineas(cfg, client, None, notas)
    _emit(args, {**data, "consulta_en_procedencia": "en claro" if registrar else "solo huella SHA-256",
                 **_red_salida(client), "notas": notas}, "\n".join(lines))
    return SALIDA_OK


def cmd_normativa(args, cfg) -> int:
    from .sources import eurlex
    client = _client(cfg)
    doc = eurlex.document(client, args.celex, language=args.idioma)
    avisos = list(doc.pop("avisos", []))
    consultado = client.cache_hits[-1]["fecha"] if client.cache_hits else None
    log_event(cfg, "normativa", inputs={"fuente": "eur-lex", "celex": doc["celex"], "idioma": doc["idioma"]},
              outputs={"titulo": doc["titulo"], "fecha_documento": doc["fecha_documento"], "eli": doc["eli"],
                       "en_vigor": doc["en_vigor"], "ultima_consolidada": doc["ultima_consolidada"],
                       "avisos": avisos, "cache": _red_procedencia(client)}, actor=args.agente)
    nota = ("metadatos leídos de la página de EUR-Lex, sin interpretar: compruébalos en el texto oficial antes de "
            "citarlos; no sustituyen la verificación manual de la URL (`nutria verificar manual`, solo personas)")
    vigor = {True: "en vigor", False: "NO en vigor", None: "no consta"}[doc["en_vigor"]]
    consolidated = doc["ultima_consolidada"]
    lines = [f"CELEX {doc['celex']} (EUR-Lex, {doc['idioma']})",
             f"  título: {doc['titulo'] or 'no consta'}",
             f"  fecha del documento: {doc['fecha_documento'] or 'no consta'} | publicación: "
             f"{doc['fecha_publicacion'] or 'no consta'} | vigencia: {vigor}",
             f"  ELI: {doc['eli'] or 'no consta'}",
             f"  URL: {doc['url']}",
             "  última versión consolidada: " + (f"{consolidated['celex']} ({consolidated['fecha']}) {consolidated['url']}"
                                                if consolidated else "no consta en la página")]
    lines += _red_lineas(cfg, client, avisos, [nota])
    _emit(args, {**doc, "fecha_copia": consultado, **_red_salida(client, avisos), "nota": nota}, "\n".join(lines))
    return SALIDA_OK


def cmd_cache(args, cfg) -> int:
    from .http import export_cache, import_cache, inspect_cache
    client = _client(cfg)
    if args.accion == "exportar":
        destination = Path(args.fichero)
        if destination.exists() and not args.forzar:
            raise ValueError(f"{destination} ya existe (usa --forzar para sobrescribirlo)")
        res = export_cache(client, destination)
        log_event(cfg, "cache_exportar", inputs={"fichero": str(destination)},
                  outputs={"entradas": res["entradas"], "sha256": res["sha256"], "no_exportadas": res["no_exportadas"]},
                  actor=args.agente)
        aviso = ("la exportación contiene las consultas y las respuestas en claro (también las de búsquedas "
                 "confidenciales y de patentes): trátala como un dato confidencial del proyecto")
        lines = [f"Exportadas {res['entradas']} copias a {destination} (sha256 {res['sha256']})"]
        if res["no_exportadas"]:
            lines.append("No exportadas (no válidas): " + "; ".join(f"{k}: {v}" for k, v in res["no_exportadas"].items()))
        lines.append(f"AVISO: {aviso}")
        _emit(args, {**res, "aviso": aviso}, "\n".join(lines))
        return SALIDA_OK
    if args.accion == "importar":
        res = import_cache(client, Path(args.fichero))
        lines = [f"Importadas {res['importadas']} copias de {res['fichero']} (conservan su fecha original; "
                 f"{res['conservadas_locales']} copias locales iguales o más recientes se mantienen)"]
        lines += [f"RECHAZADA entrada {r['indice']}: {r['motivo']}" for r in res["rechazadas"]]
        lines.append("Nota: una copia importada vale lo que valga su origen; la importación queda en la procedencia "
                     "con la huella del fichero, y la verificación de referencias nunca usa la caché")
        _emit(args, res, "\n".join(lines))
        return SALIDA_RESULTADO if res["rechazadas"] else SALIDA_OK
    res = inspect_cache(client)
    lines = [f"Copias válidas: {res['validas']}"
             + (f" (de {res['mas_antigua']} a {res['mas_reciente']})" if res["validas"] else "")]
    lines += [f"Descartables: {v} — {k}" for k, v in res["descartables"].items()]
    _emit(args, res, "\n".join(lines))
    return SALIDA_RESULTADO if res["descartables"] else SALIDA_OK


# ============================================================================ registro de evidencia
ANALYSIS_COLUMNS = [
    ("estudio", "source_id"), ("variable", "outcome"), ("unidad", "outcome_unit"), ("especie", "species"),
    ("estado_fisiologico", "physiological_stage"), ("diseno", "design"), ("unidad_experimental", "experimental_unit"),
    ("duracion_dias", "duration_days"), ("intervencion", "intervention"), ("dosis", "dose_value"),
    ("dosis_unidad", "dose_unit"), ("dosis_base", "dose_basis"), ("forma_producto", "product_form"),
    ("media_trt", "mean_trt"), ("media_ctrl", "mean_ctrl"), ("disp_trt", "disp_trt"), ("disp_ctrl", "disp_ctrl"),
    ("tipo_disp", "dispersion_type"), ("n_trt", "n_trt"), ("n_ctrl", "n_ctrl"), ("ev_trt", "ev_trt"),
    ("ev_ctrl", "ev_ctrl"), ("forraje_pct_ms", "forage_pct_dm"), ("fnd_pct_ms", "ndf_pct_dm"),
    ("almidon_pct_ms", "starch_pct_dm"), ("grasa_pct_ms", "fat_pct_dm"), ("pb_pct_ms", "cp_pct_dm"),
    ("dcad_meq_kg_ms", "dcad_meq_kg_dm"), ("financiacion", "funding"), ("estado_verificacion", "check_status"),
]
COMPARE_IGNORED = {"notas", "estado_verificacion", "verificado_por"}
CONCILIATED = ("doble_concordante", "resuelta")


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)), abs(float(b)))
    return a == b


def cmd_extraccion(args, cfg) -> int:
    if args.accion == "comparar":
        a, b = _load_json(args.a), _load_json(args.b)
        a, b = (a if isinstance(a, list) else [a]), (b if isinstance(b, list) else [b])
        diffs = []
        if len(a) != len(b):
            diffs.append({"registro": None, "campo": "(número de registros)", "a": len(a), "b": len(b)})
        for i, (x, y) in enumerate(zip(a, b), 1):
            for key in sorted((set(x) | set(y)) - COMPARE_IGNORED):
                if not _same(x.get(key), y.get(key)):
                    diffs.append({"registro": i, "campo": key, "a": x.get(key), "b": y.get(key)})
        text = ("Extracciones concordantes: marca ambas como 'doble_concordante'." if not diffs else
                "\n".join(f"registro {d['registro']} | {d['campo']}: A={d['a']!r}  B={d['b']!r}" for d in diffs)
                + f"\n{len(diffs)} discrepancia(s): resuélvelas releyendo la fuente y marca 'resuelta'.")
        log_event(cfg, "extraccion_comparar", inputs={"a": args.a, "b": args.b}, outputs={"discrepancias": len(diffs)},
                  actor=args.agente)
        _emit(args, {"discrepancias": diffs}, text)
        return 1 if diffs else 0
    conn = _conn(cfg)
    if args.accion == "agregar":
        data = _load_json(args.json_file)
        items = data if isinstance(data, list) else [data]
        for item in items:
            _validate("extraccion", item)
        with registry.transaction(conn):     # lote atómico: o todas o ninguna
            ids = [registry.add_extraction(conn, item, args.agente) for item in items]
        _emit(args, {"ids": ids}, f"Extracciones registradas: {ids}")
        return 0
    rows = registry.list_extractions(conn, source_id=args.fuente, outcome=args.variable)
    if args.accion == "exportar":
        if not rows:
            raise SystemExit("No hay extracciones con esos filtros")
        excluded: dict[str, int] = {}
        if args.formato == "analisis" and not args.incluir_sin_conciliar:
            # Doble extracción (regla del proyecto): al análisis solo pasan los registros conciliados.
            for r in rows:
                if r["check_status"] not in CONCILIATED:
                    excluded[r["check_status"]] = excluded.get(r["check_status"], 0) + 1
            rows = [r for r in rows if r["check_status"] in CONCILIATED]
            if not rows:
                raise SystemExit("Ninguna extracción conciliada ('doble_concordante' o 'resuelta') con esos filtros "
                                 f"(excluidas: {excluded}). Concilia la doble extracción con `extraccion comparar` o, "
                                 "solo para una exploración, usa --incluir-sin-conciliar.")
        if args.formato == "registro":
            fieldnames = list(rows[0].keys())
            out_rows = rows
        else:
            fieldnames = ["estudio", "comparacion"] + [name for name, _col in ANALYSIS_COLUMNS[1:]]
            out_rows = [{"estudio": r["source_id"], "comparacion": f"E{r['id']}",
                         **{name: r.get(col) for name, col in ANALYSIS_COLUMNS[1:]}} for r in rows]
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(out_rows)
        note = ""
        if excluded:
            note = ("\nExcluidas por no estar conciliadas: "
                    + ", ".join(f"{k}={v}" for k, v in sorted(excluded.items()))
                    + " (usa --incluir-sin-conciliar solo para una exploración)")
        elif args.formato == "analisis" and args.incluir_sin_conciliar:
            pending = sum(1 for r in rows if r["check_status"] not in CONCILIATED)
            if pending:
                note = f"\nAVISO: {pending} fila(s) sin conciliar incluidas: el análisis es solo exploratorio"
        _emit(args, {"filas": len(rows), "csv": args.csv, "formato": args.formato, "excluidas": excluded},
              f"Exportadas {len(rows)} filas a {args.csv} (formato {args.formato}){note}")
        return 0
    _emit(args, rows, "\n".join(f"#{r['id']} {r['source_id']} | {r['species']} | {r['intervention']} | {r['outcome']} "
                                f"({r['outcome_unit']}) | {r['check_status']}" for r in rows) or "(sin extracciones)")
    return 0


def cmd_afirmacion(args, cfg) -> int:
    conn = _conn(cfg)
    if args.accion == "agregar":
        data = _load_json(args.json_file)
        items = data if isinstance(data, list) else [data]
        for item in items:
            _validate("afirmacion", item)
        with registry.transaction(conn):
            ids = [registry.add_claim(conn, item, args.agente) for item in items]
        _emit(args, {"ids": ids}, f"Afirmaciones registradas: {ids}")
        return 0
    rows = registry.list_claims(conn, entity_id=args.entidad)
    _emit(args, rows, "\n".join(f"{r['id']} [{r['label']}] {r['text']} {r['source_ids'] or ''}" for r in rows)
          or "(sin afirmaciones)")
    return 0


def _entity_cmd(args, cfg, kind: str) -> int:
    conn = _conn(cfg)
    add = registry.add_opportunity if kind == "oportunidad" else registry.add_hypothesis
    get = registry.get_opportunity if kind == "oportunidad" else registry.get_hypothesis
    lst = registry.list_opportunities if kind == "oportunidad" else registry.list_hypotheses
    set_status = registry.set_opportunity_status if kind == "oportunidad" else registry.set_hypothesis_status
    if args.accion == "nueva":
        data = _load_json(args.json_file)
        items = data if isinstance(data, list) else [data]
        for item in items:
            _validate(kind, item)
        with registry.transaction(conn):
            ids = [add(conn, item, args.agente) for item in items]
        _emit(args, {"ids": ids}, f"Registradas: {', '.join(ids)}")
    elif args.accion == "listar":
        rows = lst(conn, status=args.estado)
        _emit(args, rows, "\n".join(f"{r['id']} [{r['status']}] {r['title']}" for r in rows) or "(vacío)")
    elif args.accion == "ver":
        row = get(conn, args.id)
        if not row:
            raise SystemExit(f"No existe {args.id}")
        row["decisiones"] = registry.list_decisions(conn, args.id)
        if kind == "oportunidad":
            row["compuertas"] = registry.list_gates(conn, args.id)
            row["compuertas_efectivas"] = registry.effective_gates(conn, args.id, _rubric(cfg).gate_ids)
            if not getattr(args, "sin_puntuaciones", False) and not _es_evaluador(args):
                row["puntuaciones"] = registry.list_scores(conn, args.id)
            row["hipotesis"] = registry.list_hypotheses(conn, opportunity_id=args.id)
        _emit(args, row)
    else:
        gate_ids = _rubric(cfg).gate_ids if kind == "oportunidad" else None
        set_status(conn, args.id, args.nuevo_estado, args.motivo, args.agente, args.firma_humana, gate_ids)
        _emit(args, {"id": args.id, "estado": args.nuevo_estado}, f"{args.id} -> {args.nuevo_estado}")
    return 0


def cmd_oportunidad(args, cfg) -> int:
    return _entity_cmd(args, cfg, "oportunidad")


def cmd_hipotesis(args, cfg) -> int:
    return _entity_cmd(args, cfg, "hipotesis")


def cmd_puntuar(args, cfg) -> int:
    from .scoring.engine import format_table, rank
    rubric = _rubric(cfg)
    if args.accion == "rubrica":
        if args.markdown:
            print(rubric.to_markdown())
        else:
            _emit(args, {"criterios": [c.__dict__ for c in rubric.criteria], "compuertas": [g.__dict__ for g in rubric.gates],
                         "perfiles": rubric.profiles})
        return 0
    conn = _conn(cfg)
    if args.accion == "registrar":
        data = _load_json(args.json_file)
        _validate("puntuacion", data)
        n = registry.add_scores(conn, data["oportunidad"], data["evaluador"], data["puntuaciones"], rubric.criterion_ids)
        # Los valores quedan en la base de datos (scores y scores_history), no en la procedencia, que es legible por
        # otros agentes: así un evaluador no puede ver las puntuaciones de otro consultando las trazas.
        log_event(cfg, "puntuacion", inputs={"oportunidad": data["oportunidad"], "evaluador": data["evaluador"]},
                  outputs={"criterios": n, "sha256_puntuaciones": sha256_text(json.dumps(data["puntuaciones"],
                                                                                         sort_keys=True))},
                  actor=args.agente)
        _emit(args, {"oportunidad": data["oportunidad"], "evaluador": data["evaluador"], "criterios": n},
              f"{n} puntuaciones registradas para {data['oportunidad']} (evaluador {data['evaluador']})")
        return 0
    if args.accion == "compuerta":
        registry.set_gate(conn, args.id, args.compuerta, args.estado, args.motivo, rubric.gate_ids,
                          evidence=args.evidencia, actor=args.agente, human_signoff=args.firma_humana)
        note = ""
        if args.estado == "superada" and not args.firma_humana:
            note = " (propuesta: no cuenta como superada hasta que la firme una persona)"
        _emit(args, {"id": args.id, "compuerta": args.compuerta, "estado": args.estado,
                     "firmada": bool(args.firma_humana)}, f"{args.id}: compuerta {args.compuerta} -> {args.estado}{note}")
        return 0
    # ranking
    _no_para_evaluadores(args, "consultar el ranking")
    profile = args.perfil or cfg.scoring_profile
    opps = []
    for o in registry.list_opportunities(conn):
        if o["status"] == "descartada":
            continue
        scores = [{"rater": s["rater"], "criterion": s["criterion"], "value": s["value"], "low": s["low"],
                   "high": s["high"]} for s in registry.list_scores(conn, o["id"])]
        gates = registry.effective_gates(conn, o["id"], rubric.gate_ids)
        opps.append({"id": o["id"], "title": o["title"], "scores": scores, "gates": gates, "status": o["status"]})
    rows = rank(opps, rubric, profile=profile, n_sim=args.sim, seed=args.semilla, alpha=args.alfa, top_k=args.top,
                min_raters=args.min_evaluadores)
    log_event(cfg, "ranking", inputs={"perfil": profile, "sim": args.sim, "semilla": args.semilla, "alfa": args.alfa,
                                      "min_evaluadores": args.min_evaluadores},
              outputs={"orden": [(r.opportunity_id, r.status) for r in rows]}, actor=args.agente)
    _emit(args, [r.to_dict() for r in rows],
          f"Perfil: {profile} | {args.sim} simulaciones | semilla {args.semilla} | alfa {args.alfa} | "
          f"mínimo {args.min_evaluadores} evaluadores\n" + format_table(rows, args.top))
    return 0


def cmd_decision(args, cfg) -> int:
    conn = _conn(cfg)
    if args.accion == "registrar":
        registry.add_decision(conn, args.entidad, args.decision, args.motivo, args.agente, args.firma_humana)
        _emit(args, {"entidad": args.entidad, "decision": args.decision, "firmada": bool(args.firma_humana)},
              "Decisión registrada" + ("" if args.firma_humana else " (sin firma humana: es una propuesta)"))
        return 0
    rows = registry.list_decisions(conn, args.entidad)
    _emit(args, rows, "\n".join(f"{r['ts']} {r['entity_id'] or '-':<8} {r['decision']} | {r['rationale']} | "
                                f"{r['decided_by']}{' | firma: ' + r['human_signoff'] if r['human_signoff'] else ''}"
                                for r in rows) or "(sin decisiones)")
    return 0


# ============================================================================ estadística
def _read_table(path: str, sep: str, decimal: str) -> list[dict]:
    """Lee un CSV. Con --decimal ',' convierte la coma decimal en punto (no admite separadores de miles)."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh, delimiter=sep))
    if decimal != ".":
        for r in rows:
            for k, v in r.items():
                if isinstance(v, str) and v.strip():
                    candidate = v.strip().replace(decimal, ".")
                    try:
                        float(candidate)
                    except ValueError:
                        continue
                    r[k] = candidate
    return rows


def _num(row: dict, key: str, tag: str) -> float | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{tag}: valor no numérico en '{key}': {value!r}") from None


def _effects_from_rows(rows: list[dict], measure: str) -> tuple[list[float], list[float], list[str], list[str]]:
    from .stats import effect_sizes as es
    yi, vi, labels, comps = [], [], [], []
    for i, r in enumerate(rows, 1):
        label = r.get("estudio") or r.get("study") or str(i)
        tag = f"fila {i} ({label})"
        if r.get("yi") not in (None, "") and r.get("vi") not in (None, ""):
            y, v = _num(r, "yi", tag), _num(r, "vi", tag)
        elif measure in ("RR", "OR", "RD"):
            vals = {k: _num(r, k, tag) for k in ("ev_trt", "n_trt", "ev_ctrl", "n_ctrl")}
            missing = [k for k, val in vals.items() if val is None]
            if missing:
                raise ValueError(f"{tag}: faltan {', '.join(missing)} para la medida {measure}")
            y, v = es.effect_from_counts(measure, vals["ev_trt"], vals["n_trt"], vals["ev_ctrl"], vals["n_ctrl"], label=tag)
        else:
            kind = (r.get("tipo_disp") or "").strip()
            if not kind:
                raise ValueError(f"{tag}: falta 'tipo_disp' (SD, SE, SEM_combinado, SED o IC95); no se asume DE por "
                                 "defecto porque en nutrición animal lo habitual es el SEM combinado")
            vals = {k: _num(r, k, tag) for k in ("media_trt", "media_ctrl", "n_trt", "n_ctrl", "disp_trt", "disp_ctrl")}
            missing = [k for k in ("media_trt", "media_ctrl", "n_trt", "n_ctrl") if vals[k] is None]
            if missing:
                raise ValueError(f"{tag}: faltan {', '.join(missing)}")
            try:
                y, v = es.effect_from_summary(measure, vals["media_trt"], vals["media_ctrl"], vals["n_trt"],
                                              vals["n_ctrl"], kind, vals["disp_trt"], vals["disp_ctrl"], label=tag)
            except TypeError as exc:
                raise ValueError(f"{tag}: {exc}") from exc
        yi.append(y)
        vi.append(v)
        labels.append(label)
        comps.append(r.get("comparacion") or f"{label}#{i}")
    return yi, vi, labels, comps


def cmd_meta(args, cfg) -> int:
    from .stats import meta as m
    rows = _read_table(args.csv, args.sep, args.decimal)
    if not rows:
        raise ValueError("El CSV no tiene filas")
    if args.accion == "stpierre":
        from .stats.mixed import st_pierre
        res = st_pierre(rows, args.respuesta, args.dosis, args.estudio, args.covariable, args.pendiente_aleatoria,
                        descomponer=not args.sin_descomponer)
        log_event(cfg, "meta_stpierre", inputs={"csv": args.csv, "respuesta": args.respuesta, "dosis": args.dosis},
                  outputs=res, actor=args.agente)
        _emit(args, res)
        return 0
    if args.accion == "validar-predictivo":
        from .stats.predictive import loso_cv
        res = loso_cv(rows, args.respuesta, args.predictores, args.estudio)
        log_event(cfg, "meta_validar_predictivo", inputs={"csv": args.csv, "respuesta": args.respuesta,
                                                          "predictores": args.predictores}, outputs=res, actor=args.agente)
        _emit(args, res)
        return 0
    yi, vi, labels, comps = _effects_from_rows(rows, args.medida)
    if args.accion == "exportar-r":
        return _export_r(args, rows, yi, vi, labels, comps)
    out: dict = {"medida": args.medida, "k": len(yi), "estudios": labels}
    text: list[str] = []
    deps = m.detect_dependence(labels)
    if deps:
        if args.agregar_estudio:
            if args.moderador or args.subgrupo:
                raise ValueError("La agregación por estudio no se combina con moderadores ni subgrupos por comparación: "
                                 "usa el modelo multinivel (`nutria meta exportar-r`)")
            yi, vi, labels = m.aggregate_dependent(yi, vi, labels, rho=args.rho)
            text.append(f"Efectos dependientes agregados por estudio (rho = {args.rho}); repite con otros valores de "
                        "rho como análisis de sensibilidad.")
            out["agregacion"] = {"rho": args.rho, "estudios": len(yi)}
        else:
            text.append(f"AVISO: {len(deps)} estudio(s) aportan varias comparaciones ({', '.join(list(deps)[:5])}). "
                        "Se tratan como independientes y la incertidumbre queda subestimada: usa --agregar-estudio "
                        "--rho 0.5 o el modelo multinivel (`nutria meta exportar-r`).")
            out["aviso_dependencia"] = deps
    if args.metodo == "FE":
        res = m.fixed_effect(yi, vi)
    else:
        res = m.random_effects(yi, vi, method=args.metodo, ajuste=args.ajuste)
    out["modelo"] = res.to_dict()
    text.append(res.summary())
    if args.metodo != "FE" and len(yi) >= 2:
        ci = m.tau2_confint(yi, vi)
        out["ic_heterogeneidad"] = ci
        text.append(f"IC95 Q-profile: tau² [{ci['tau2'][0]:.4f}; {ci['tau2'][1]:.4f}]; "
                    f"I² [{ci['I2'][0]:.1f} %; {ci['I2'][1]:.1f} %]")
    method_re = args.metodo if args.metodo != "FE" else "REML"
    pb = m.publication_bias(yi, vi)
    out["sesgo_publicacion"] = pb
    if pb.get("evaluable") is False:
        text.append(f"Sesgo de publicación: no evaluable ({pb.get('motivo')})")
    else:
        eg = pb.get("egger", pb)
        if eg.get("estimable") is False:
            text.append(f"Egger: no estimable ({eg.get('motivo')})")
        else:
            text.append(f"Egger: intercepto {eg['intercepto']:.3f} (EE {eg['ee']:.3f}), p = {eg['p']:.3g}")
    if len(yi) >= 3:
        out["uno_fuera"] = m.leave_one_out(yi, vi, labels, method=method_re, ajuste=args.ajuste)
        lo = min(out["uno_fuera"], key=lambda r: r["estimacion"])
        hi = max(out["uno_fuera"], key=lambda r: r["estimacion"])
        text.append(f"Uno fuera: estimación entre {lo['estimacion']:.4f} (sin {lo['excluido']}) y "
                    f"{hi['estimacion']:.4f} (sin {hi['excluido']})")
    if args.subgrupo:
        groups = [r.get(args.subgrupo, "") for r in rows]
        out["subgrupos"] = m.subgroups(yi, vi, groups, method=method_re, ajuste=args.ajuste)
        sg = out["subgrupos"]
        for g, r in sg["subgrupos"].items():
            text.append(f"  subgrupo {g}: k={r['k']}, estimación {r['estimate']:.4f} [{r['ci_low']:.4f}; {r['ci_high']:.4f}]")
        if sg.get("Q_entre") is not None:
            text.append(f"  Q entre subgrupos = {sg['Q_entre']:.3f} (gl {sg['gl']}), p = {sg['p']:.3g}. {sg['nota']}")
    if args.moderador:
        from .stats.metareg import meta_regression
        try:
            mods = [[_num(r, c, f"fila {i}") for c in args.moderador] for i, r in enumerate(rows, 1)]
            if any(v is None for row in mods for v in row):
                raise ValueError("hay moderadores vacíos")
            reg = meta_regression(yi, vi, mods, args.moderador, method=method_re, ajuste=args.ajuste)
            out["meta_regresion"] = reg.to_dict()
            text.append(reg.summary())
        except ValueError as exc:
            out["meta_regresion"] = {"error": str(exc)}
            text.append(f"Meta-regresión no estimable: {exc}")
    log_event(cfg, "metaanalisis", inputs={"csv": args.csv, "medida": args.medida, "metodo": args.metodo,
                                           "ajuste": args.ajuste, "moderadores": args.moderador,
                                           "subgrupo": args.subgrupo, "agregar_estudio": args.agregar_estudio},
              outputs={"estimacion": res.estimate, "ic": [res.ci_low, res.ci_high], "tau2": res.tau2, "k": res.k},
              actor=args.agente)
    _emit(args, out, "\n".join(text))
    return 0


def _export_r(args, rows, yi, vi, labels, comps) -> int:
    data_path = Path(args.salida).with_suffix(".csv")
    extra = [k for k in rows[0].keys() if k not in ("yi", "vi", "estudio", "comparacion")]
    with open(data_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["estudio", "comparacion", "yi", "vi", *extra])
        for r, y, v, lab, comp in zip(rows, yi, vi, labels, comps):
            writer.writerow([lab, comp, repr(float(y)), repr(float(v)), *[r.get(k, "") for k in extra]])
    script = f"""# Script generado por nutria {__version__}. Revísalo antes de usarlo.
# Los tamaños de efecto (yi, vi) ya vienen calculados por nutria, incluida la conversión del SEM o SED a DE.
# Requiere: install.packages(c("metafor", "clubSandwich"))
library(metafor)
dat <- read.csv("{data_path.name}")
# 1) Efectos aleatorios con ajuste de Knapp-Hartung
res <- rma(yi, vi, data = dat, method = "REML", test = "knha")
print(res); print(predict(res))
# 2) Dependencia: varias comparaciones por estudio (control compartido o varias variables)
if (any(duplicated(dat$estudio))) {{
  res_ml <- rma.mv(yi, vi, random = ~ 1 | estudio/comparacion, data = dat, method = "REML", test = "t")
  print(res_ml)
  print(robust(res_ml, cluster = dat$estudio, clubSandwich = TRUE))
}}
# 3) Sensibilidad; sesgo de publicación solo con k >= 10
print(leave1out(res))
if (res$k >= 10) {{ print(regtest(res)); funnel(res) }}
"""
    Path(args.salida).write_text(script, "utf-8")
    _emit(args, {"datos": str(data_path), "script": args.salida},
          f"Datos con yi/vi en {data_path} y script de R (metafor) en {args.salida}")
    return 0


def cmd_potencia(args, cfg) -> int:
    from .stats import power
    if args.accion == "medias":
        if args.diseno == "paralelo":
            res = power.n_parallel(args.delta, args.de, args.alfa, args.potencia)
            if args.m and args.icc is not None:
                de = power.design_effect(args.m, args.icc)
                res["efecto_diseno"] = de
                res["nota"] = (f"Si el corral es la unidad experimental y la DE es entre animales, multiplica por "
                               f"{de:.2f} o, mejor, usa la DE entre corrales.")
        else:
            res = power.n_crossover(args.delta, args.de, args.alfa, args.potencia)
    else:
        res = power.n_proportions(args.p1, args.p2, args.alfa, args.potencia)
    _emit(args, res)
    return 0


# ============================================================================ destino digestivo
def cmd_destino(args, cfg) -> int:
    from . import destino
    if args.fraccion_escape is None:
        missing = [n for n in ("a", "b", "kd", "kp") if getattr(args, n) is None]
        if missing:
            raise ValueError(f"Faltan {', '.join('--' + m for m in missing)} (o usa --fraccion-escape)")
    res = destino.informe_destino(args.a, args.b, args.kd, args.kp, fraccion_escape=args.fraccion_escape,
                                  dosis=args.dosis, digestibilidad_intestinal=args.digestibilidad_intestinal,
                                  unidad_dosis=args.unidad_dosis)
    log_event(cfg, "destino_ruminal", inputs={k: getattr(args, k) for k in ("a", "b", "kd", "kp", "dosis",
                                                                           "digestibilidad_intestinal", "fraccion_escape")},
              outputs=res.get("resultados", {}), actor=args.agente)
    _emit(args, res, destino.texto_informe_destino(res))
    return 0


# ============================================================================ economía
def cmd_econ(args, cfg) -> int:
    if args.accion == "ganadero":
        from .econ import farm
        model = farm.load_farm_model(Path(args.archivo))
        data = {"escenarios": farm.scenarios(model, args.sim, args.semilla),
                "monte_carlo": farm.monte_carlo(model, args.sim, args.semilla)}
        text = farm.report(model, args.sim, args.semilla)
        summary = data["monte_carlo"]
    else:
        from .econ import rnpv
        project = rnpv.load_project(Path(args.archivo))
        data = rnpv.evaluate(project, args.sim, args.semilla)
        text = rnpv.report(project, args.sim, args.semilla)
        summary = data
    log_event(cfg, f"econ_{args.accion}", inputs={"archivo": args.archivo, "sha256": sha256_file(Path(args.archivo)),
                                                  "sim": args.sim, "semilla": args.semilla},
              outputs={"resumen": summary}, actor=args.agente)
    _emit(args, data, text)
    return 0


# ============================================================================ informes
def _check_report_file(args, cfg, path: Path, online: bool) -> int:
    from .reports.check import check_report, check_report_online, write_stamp
    text = path.read_text("utf-8")
    checked_hash = sha256_text(text)
    structure = not getattr(args, "sin_secciones", False)
    issues = check_report(text, require_sections=structure)
    details: list[dict] = []
    summary: dict | None = None
    if online:
        conn = registry.connect(cfg.db_path) if cfg.db_path.is_file() else None
        more, details, summary = check_report_online(_client(cfg, fresh=True), text, conn)
        issues += more
    errors = [i for i in issues if i.severity == "error"]
    if getattr(args, "sellar", False):
        if sha256_file(path) != checked_hash:
            raise SystemExit("El informe cambió durante la verificación: no se sella. Vuelve a ejecutar la comprobación.")
        stamp = write_stamp(path, text, issues, online, details, summary, structure)
        log_event(cfg, "sello_informe", inputs={"informe": str(path)},
                  outputs={"sha256": stamp["sha256"], "errores": stamp["errores"],
                           "verificacion_completa": stamp["verificacion_completa"]}, actor=args.agente)
    lines = [str(i) for i in issues] or ["Sin incidencias"]
    if online and summary is not None:
        lines.append(f"Referencias: {summary['verificadas']}/{summary['total']} verificadas, {summary['pendientes']} sin "
                     f"verificar, {summary['con_error']} con error"
                     + ("" if summary["completa"] else " — verificación INCOMPLETA: el sello no será válido"))
    elif not online:
        lines.append("Sin verificación en línea de las referencias")
    lines.append(f"Resultado: {len(errors)} error(es), {len(issues) - len(errors)} aviso(s)")
    _emit(args, {"incidencias": [i.__dict__ for i in issues], "referencias": details, "resumen": summary},
          "\n".join(lines))
    incomplete = online and summary is not None and not summary["completa"]
    return 1 if errors or incomplete else 0


def cmd_informe(args, cfg) -> int:
    if args.accion == "nuevo":
        from .reports.render import new_report
        conn = _conn(cfg)
        text = new_report(conn, args.id)
        out = Path(args.salida) if args.salida else cfg.research_dir / "informes" / f"{args.id}.md"
        if out.exists() and not args.forzar:
            raise SystemExit(f"{out} ya existe (usa --forzar para sobrescribir)")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, "utf-8")
        _emit(args, {"informe": str(out)}, f"Esqueleto de informe creado: {out}")
        return 0
    if args.accion == "sello":
        from .reports.check import stamp_status
        ok, msg = stamp_status(Path(args.archivo))
        _emit(args, {"valido": ok, "mensaje": msg}, msg)
        return 0 if ok else 1
    return _check_report_file(args, cfg, Path(args.archivo), online=args.online)


def cmd_procedencia(args, cfg) -> int:
    _no_para_evaluadores(args, "consultar la procedencia")
    events = read_events(cfg, limit=args.ultimas, action=args.accion_filtro)
    _emit(args, events, "\n".join(f"{e['ts']} {e['actor']:<22} {e['action']:<18} "
                                  f"{json.dumps(e.get('inputs'), ensure_ascii=False)[:110]}" for e in events)
          or "(sin eventos)")
    return 0


# ============================================================================ modelos de lenguaje
def cmd_llm(args, cfg) -> int:
    from .llm import router
    if args.accion == "modelos":
        rows = []
        for s in router.load_registry(cfg):
            ok, why = s.usable(cfg)
            rows.append({"id": s.id, "proveedor": s.provider, "modelo": s.model, "roles": s.roles,
                         "politica_datos": s.data_policy, "verificado": s.verified, "utilizable": ok, "motivo": why})
        _emit(args, rows, "\n".join(f"{r['id']:<14} {r['proveedor']:<18} {r['modelo'][:34]:<34} "
                                    f"{','.join(r['roles']):<28} {r['politica_datos']:<15} "
                                    f"{'sí' if r['utilizable'] else 'no: ' + r['motivo']}" for r in rows))
        return 0
    if args.accion == "evaluar":
        _emit(args, router.evaluate(cfg, Path(args.archivo), role=args.rol, model_id=args.modelo))
        return 0
    prompt = Path(args.archivo).read_text("utf-8") if args.archivo else args.prompt
    if not prompt:
        raise SystemExit("Indica --prompt o --archivo")
    system = Path(args.sistema).read_text("utf-8") if args.sistema else None
    res = router.ask(cfg, prompt, role=args.rol, model_id=args.modelo, system=system, sensitivity=args.sensibilidad)
    _emit(args, res, "\n\n".join(f"=== {r['modelo']} ===\n" + (r.get("texto") or r.get("omitido") or r.get("error", ""))
                                 for r in res) or "Ningún modelo configurado para ese rol (ver `nutria llm modelos`).")
    return 0


# ============================================================================ parser
def build_parser() -> argparse.ArgumentParser:
    # Las opciones globales se aceptan antes o después de la orden. En las subórdenes usan
    # SUPPRESS para no sobrescribir lo indicado antes de la orden (comportamiento de argparse).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="salida JSON")
    common.add_argument("--agente", default=argparse.SUPPRESS,
                        help="nombre del agente que ejecuta la orden (procedencia)")
    common.add_argument("--offline", action="store_true", default=argparse.SUPPRESS,
                        help="usar solo la caché, sin red (la verificación de referencias quedará incompleta)")
    common.add_argument("--antiguedad-maxima", dest="antiguedad_maxima", type=float, metavar="DIAS",
                        default=argparse.SUPPRESS,
                        help="con --offline, antigüedad máxima de las copias de la caché (por defecto 180 días)")

    p = argparse.ArgumentParser(prog="nutria",
                                description="Herramientas del sistema de I+D computacional en nutrición animal.",
                                epilog=_texto_codigos_salida(), formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", default=False, help="salida JSON")
    p.add_argument("--agente", default=None, help="nombre del agente que ejecuta la orden (procedencia)")
    p.add_argument("--offline", action="store_true", default=False, help="usar solo la caché, sin red")
    p.add_argument("--antiguedad-maxima", dest="antiguedad_maxima", type=float, metavar="DIAS", default=None,
                   help="con --offline, antigüedad máxima de las copias de la caché (por defecto 180 días)")
    p.add_argument("--version", action="version", version=f"nutria {__version__}")
    sub = p.add_subparsers(dest="orden", required=True)

    s = sub.add_parser("init", parents=[common], help="inicializa el proyecto en el directorio actual")
    s.add_argument("--forzar", action="store_true", help="reescribe nutria.toml")
    s.set_defaults(func=cmd_init)
    sub.add_parser("doctor", parents=[common], help="comprueba entorno, claves y acceso a las fuentes").set_defaults(func=cmd_doctor)
    sub.add_parser("estado", parents=[common], help="resumen del registro de investigación").set_defaults(func=cmd_estado)
    s = sub.add_parser("cache", parents=[common], help="caché de respuestas: comprobar, exportar e importar")
    cs = s.add_subparsers(dest="accion", required=True)
    cs.add_parser("comprobar", parents=[common], help="integridad de las copias (huella y registro en la procedencia)")
    x = cs.add_parser("exportar", parents=[common], help="copias válidas, con fecha y huella, para otra máquina")
    x.add_argument("fichero")
    x.add_argument("--forzar", action="store_true", help="sobrescribe el fichero si existe")
    x = cs.add_parser("importar", parents=[common], help="importa una exportación (queda en la procedencia)")
    x.add_argument("fichero")
    s.set_defaults(func=cmd_cache)

    s = sub.add_parser("buscar", parents=[common], help="busca literatura")
    s.add_argument("fuente", choices=["pubmed", "europepmc", "openalex", "efsa"])
    s.add_argument("consulta")
    s.add_argument("--max", type=int, default=20)
    s.add_argument("--desde", help="año inicial")
    s.add_argument("--hasta", help="año final")
    s.add_argument("--resumenes", action="store_true", help="(PubMed) descarga resúmenes y MeSH")
    s.add_argument("--registrar", action="store_true", help="registra los resultados como fuentes SIN verificar")
    s.add_argument("--sensibilidad", choices=["normal", "confidencial"], default="normal",
                   help="confidencial: la procedencia guarda solo la huella de la consulta (que viaja igualmente "
                        "al servicio externo)")
    s.set_defaults(func=cmd_buscar)

    s = sub.add_parser("fuente", parents=[common], help="registro de fuentes")
    fs = s.add_subparsers(dest="accion", required=True)
    a = fs.add_parser("agregar", parents=[common], help="verifica y registra una fuente por DOI o PMID")
    a.add_argument("--doi")
    a.add_argument("--pmid")
    a.add_argument("--tipo-estudio", dest="tipo_estudio",
                   choices=["in_vitro", "ex_vivo", "in_vivo", "ensayo_controlado", "revision_sistematica",
                            "metaanalisis", "revision_narrativa", "observacional", "mecanistico", "computacional",
                            "regulatorio", "otro"])
    a.add_argument("--especie")
    a.add_argument("--notas")
    x = fs.add_parser("listar", parents=[common])
    x.add_argument("--no-verificadas", dest="no_verificadas", action="store_true")
    x = fs.add_parser("ver", parents=[common])
    x.add_argument("id")
    s.set_defaults(func=cmd_fuente)

    s = sub.add_parser("verificar", parents=[common],
                       help="verifica DOI/PMID, las referencias de un documento, o registra una verificación manual")
    s.add_argument("objetivos", nargs="*", help="<documento.md>, o 'manual <URL>' (solo personas)")
    s.add_argument("--doi")
    s.add_argument("--pmid")
    s.add_argument("--cita", help="texto de la cita para comprobar que el identificador corresponde")
    s.add_argument("--pendientes", action="store_true",
                   help="verifica las fuentes sin verificar o con retractación desconocida")
    s.add_argument("--sellar", action="store_true")
    s.add_argument("--firma-humana", dest="firma_humana", help="(verificar manual) persona que comprobó la URL")
    s.add_argument("--nota", help="(verificar manual) qué se comprobó")
    s.set_defaults(func=cmd_verificar)

    s = sub.add_parser("molecula", parents=[common], help="identidad y propiedades de compuestos")
    s.add_argument("fuente", choices=["pubchem", "chembl"])
    s.add_argument("nombre")
    s.add_argument("--max", type=int, default=5)
    s.add_argument("--peligros", action="store_true", help="(PubChem) frases H de la clasificación GHS")
    s.add_argument("--mecanismos", action="store_true", help="(ChEMBL) mecanismos de acción")
    s.set_defaults(func=cmd_molecula)

    s = sub.add_parser("diana", parents=[common], help="dianas moleculares en ChEMBL")
    s.add_argument("consulta")
    s.add_argument("--organismo", default=None, help='p. ej. "Bos taurus" (filtro en el servidor)')
    s.add_argument("--max", type=int, default=20)
    s.add_argument("--actividades", action="store_true")
    s.set_defaults(func=cmd_diana)

    s = sub.add_parser("proteina", parents=[common], help="proteínas en UniProt")
    s.add_argument("consulta", help='p. ej. "gene:PPARA" o "carnitine palmitoyltransferase"')
    s.add_argument("--especie", default="vacuno")
    s.add_argument("--max", type=int, default=10)
    s.add_argument("--revisadas", action="store_true")
    s.set_defaults(func=cmd_proteina)

    s = sub.add_parser("red", parents=[common], help="red de interacciones STRING")
    s.add_argument("genes", nargs="+")
    s.add_argument("--especie", default="vacuno")
    s.add_argument("--puntuacion-min", dest="puntuacion_min", type=int, default=700)
    s.add_argument("--enriquecimiento", action="store_true")
    s.set_defaults(func=cmd_red)

    s = sub.add_parser("rutas", parents=[common], help="rutas Reactome")
    s.add_argument("accion", choices=["buscar", "analizar"])
    s.add_argument("terminos", nargs="+", help="términos (buscar) o genes (analizar)")
    s.add_argument("--especie", default="Bos taurus")
    s.set_defaults(func=cmd_rutas)

    s = sub.add_parser("patentes", parents=[common], help="búsqueda de patentes (enlaces y Lens opcional)")
    s.add_argument("consulta")
    s.add_argument("--lens", action="store_true", help="usa la API de Lens (requiere LENS_API_TOKEN)")
    s.add_argument("--max", type=int, default=20)
    s.add_argument("--registrar-consulta", dest="registrar_consulta", action="store_true",
                   help="guarda la consulta en claro en la procedencia (por defecto, solo su huella SHA-256)")
    s.set_defaults(func=cmd_patentes)

    s = sub.add_parser("normativa", parents=[common], help="metadatos de normativa (EUR-Lex por número CELEX)")
    ns = s.add_subparsers(dest="fuente", required=True)
    x = ns.add_parser("eurlex", parents=[common], help="título, fecha, ELI y última versión consolidada de un acto")
    x.add_argument("celex", help="número CELEX, p. ej. 32009R0767 (Reglamento (CE) 767/2009)")
    x.add_argument("--idioma", default="ES", help="idioma de la página de EUR-Lex (por defecto ES)")
    s.set_defaults(func=cmd_normativa)

    s = sub.add_parser("destino", parents=[common], help="destino digestivo (degradabilidad ruminal efectiva)")
    ds = s.add_subparsers(dest="accion", required=True)
    x = ds.add_parser("ruminal", parents=[common],
                      help="DE = a + b·kd/(kd+kp) (Ørskov y McDonald 1979); escape y dosis posruminal")
    x.add_argument("--a", type=float, help="fracción soluble o inmediatamente degradable (0-1)")
    x.add_argument("--b", type=float, help="fracción degradable con el tiempo (0-1)")
    x.add_argument("--kd", type=float, help="tasa de degradación de b (por hora)")
    x.add_argument("--kp", type=float, help="tasa de paso ruminal (por hora)")
    x.add_argument("--fraccion-escape", dest="fraccion_escape", type=float,
                   help="fracción que escapa al rumen, si ya se conoce (con fuente)")
    x.add_argument("--dosis", type=float, help="dosis ingerida (cualquier unidad; se conserva)")
    x.add_argument("--unidad-dosis", dest="unidad_dosis", help="unidad de la dosis (p. ej. g/animal/d)")
    x.add_argument("--digestibilidad-intestinal", dest="digestibilidad_intestinal", type=float,
                   help="fracción digerida o absorbida en el intestino (0-1)")
    s.set_defaults(func=cmd_destino)

    s = sub.add_parser("extraccion", parents=[common], help="datos extraídos de estudios")
    es = s.add_subparsers(dest="accion", required=True)
    a = es.add_parser("agregar", parents=[common])
    a.add_argument("--datos", dest="json_file", required=True, help="fichero JSON (o - para stdin)")
    for name in ("listar", "exportar"):
        x = es.add_parser(name, parents=[common])
        x.add_argument("--fuente")
        x.add_argument("--variable")
        if name == "exportar":
            x.add_argument("--csv", required=True)
            x.add_argument("--formato", choices=["analisis", "registro"], default="analisis",
                           help="analisis: columnas que espera `nutria meta analizar`; registro: columnas internas")
            x.add_argument("--incluir-sin-conciliar", action="store_true",
                           help="incluye en el formato analisis las extracciones 'simple' o con 'discrepancia' "
                                "(solo para exploración; por defecto se excluyen)")
    x = es.add_parser("comparar", parents=[common], help="doble extracción: diferencias campo a campo")
    x.add_argument("a")
    x.add_argument("b")
    s.set_defaults(func=cmd_extraccion)

    s = sub.add_parser("afirmacion", parents=[common], help="afirmaciones etiquetadas")
    afs = s.add_subparsers(dest="accion", required=True)
    a = afs.add_parser("agregar", parents=[common])
    a.add_argument("--datos", dest="json_file", required=True, help="fichero JSON (o - para stdin)")
    x = afs.add_parser("listar", parents=[common])
    x.add_argument("--entidad")
    s.set_defaults(func=cmd_afirmacion)

    for kind, statuses, func in (("oportunidad", registry.OPPORTUNITY_STATUSES, cmd_oportunidad),
                                 ("hipotesis", registry.HYPOTHESIS_STATUSES, cmd_hipotesis)):
        s = sub.add_parser(kind, parents=[common],
                           help="registro de oportunidades" if kind == "oportunidad" else "registro de hipótesis")
        ks = s.add_subparsers(dest="accion", required=True)
        a = ks.add_parser("nueva", parents=[common])
        a.add_argument("--datos", dest="json_file", required=True, help="fichero JSON (o - para stdin)")
        x = ks.add_parser("listar", parents=[common])
        x.add_argument("--estado", choices=statuses)
        x = ks.add_parser("ver", parents=[common])
        x.add_argument("id")
        if kind == "oportunidad":
            x.add_argument("--sin-puntuaciones", dest="sin_puntuaciones", action="store_true",
                           help="oculta las puntuaciones (para evaluadores ciegos)")
        x = ks.add_parser("estado", parents=[common], help="cambia el estado (exige motivo)")
        x.add_argument("id")
        x.add_argument("nuevo_estado", choices=statuses)
        x.add_argument("--motivo", required=True)
        x.add_argument("--firma-humana", dest="firma_humana", help="persona que valida la decisión (solo personas)")
        s.set_defaults(func=func)

    s = sub.add_parser("puntuar", parents=[common], help="priorización con rúbrica y compuertas")
    ps = s.add_subparsers(dest="accion", required=True)
    a = ps.add_parser("registrar", parents=[common])
    a.add_argument("--datos", dest="json_file", required=True, help="fichero JSON (o - para stdin)")
    c = ps.add_parser("compuerta", parents=[common],
                      help="propone (sin firma) o decide (con firma humana) el estado de una compuerta")
    c.add_argument("id")
    c.add_argument("compuerta")
    c.add_argument("estado", choices=registry.GATE_STATUSES)
    c.add_argument("--motivo", required=True)
    c.add_argument("--evidencia", nargs="*")
    c.add_argument("--firma-humana", dest="firma_humana", help="persona que firma la decisión (solo personas)")
    r = ps.add_parser("ranking", parents=[common])
    r.add_argument("--perfil")
    r.add_argument("--sim", type=int, default=5000)
    r.add_argument("--semilla", type=int, default=42)
    r.add_argument("--alfa", type=float, default=50.0, help="concentración Dirichlet de los pesos (0 = fijos)")
    r.add_argument("--top", type=int, default=3)
    r.add_argument("--min-evaluadores", dest="min_evaluadores", type=int, default=3)
    rb = ps.add_parser("rubrica", parents=[common])
    rb.add_argument("--markdown", action="store_true")
    s.set_defaults(func=cmd_puntuar)

    s = sub.add_parser("decision", parents=[common], help="registro de decisiones")
    ds = s.add_subparsers(dest="accion", required=True)
    a = ds.add_parser("registrar", parents=[common])
    a.add_argument("--entidad")
    a.add_argument("--decision", required=True)
    a.add_argument("--motivo", required=True)
    a.add_argument("--firma-humana", dest="firma_humana", help="persona que firma la decisión (solo personas)")
    x = ds.add_parser("listar", parents=[common])
    x.add_argument("--entidad")
    s.set_defaults(func=cmd_decision)

    csv_help = ("CSV con una fila por comparación: estudio, comparacion y o bien yi, vi; o media_trt, media_ctrl, "
                "disp_trt, disp_ctrl, tipo_disp (SD|SE|SEM_combinado|SED|IC95), n_trt, n_ctrl; o, para RR/OR/RD, "
                "ev_trt, n_trt, ev_ctrl, n_ctrl. `nutria extraccion exportar` produce este formato.")
    s = sub.add_parser("meta", parents=[common], help="metaanálisis")
    ms = s.add_subparsers(dest="accion", required=True)
    for name in ("analizar", "stpierre", "exportar-r", "validar-predictivo"):
        x = ms.add_parser(name, parents=[common], description=csv_help if name in ("analizar", "exportar-r") else None)
        x.add_argument("csv")
        x.add_argument("--sep", default=",")
        x.add_argument("--decimal", default=".", help="separador decimal del CSV (',' admitido, sin separador de miles)")
        if name in ("analizar", "exportar-r"):
            x.add_argument("--medida", choices=["MD", "SMD", "ROM", "RR", "OR", "RD"], default="MD")
        if name == "analizar":
            x.add_argument("--metodo", choices=["REML", "DL", "PM", "FE"], default="REML")
            x.add_argument("--ajuste", choices=["hksj", "hksj-mod", "ninguno"], default="hksj",
                           help="Hartung-Knapp-Sidik-Jonkman (por defecto), su variante max(1,q) o Wald")
            x.add_argument("--agregar-estudio", dest="agregar_estudio", action="store_true",
                           help="agrega las comparaciones dependientes de cada estudio")
            x.add_argument("--rho", type=float, default=0.5, help="correlación supuesta entre comparaciones")
            x.add_argument("--moderador", nargs="*", help="columnas numéricas para meta-regresión")
            x.add_argument("--subgrupo", help="columna para análisis por subgrupos")
        if name == "stpierre":
            x.add_argument("--respuesta", required=True)
            x.add_argument("--dosis", required=True)
            x.add_argument("--estudio", required=True)
            x.add_argument("--covariable", nargs="*", default=[])
            x.add_argument("--pendiente-aleatoria", dest="pendiente_aleatoria", action="store_true")
            x.add_argument("--sin-descomponer", dest="sin_descomponer", action="store_true",
                           help="no separa la pendiente intra y entre estudios")
        if name == "exportar-r":
            x.add_argument("--salida", default="metaanalisis.R")
        if name == "validar-predictivo":
            x.add_argument("--respuesta", required=True)
            x.add_argument("--predictores", nargs="+", required=True)
            x.add_argument("--estudio", required=True)
    s.set_defaults(func=cmd_meta)

    s = sub.add_parser("potencia", parents=[common], help="tamaño muestral para ensayos")
    pw = s.add_subparsers(dest="accion", required=True)
    x = pw.add_parser("medias", parents=[common])
    x.add_argument("--delta", type=float, required=True, help="diferencia mínima relevante")
    x.add_argument("--de", type=float, required=True, help="DE (entre unidades experimentales o intraanimal)")
    x.add_argument("--diseno", choices=["paralelo", "crossover"], default="paralelo")
    x.add_argument("--alfa", type=float, default=0.05)
    x.add_argument("--potencia", type=float, default=0.80)
    x.add_argument("--m", type=int, help="animales por corral")
    x.add_argument("--icc", type=float, help="correlación intraclase dentro del corral")
    x = pw.add_parser("proporciones", parents=[common])
    x.add_argument("--p1", type=float, required=True)
    x.add_argument("--p2", type=float, required=True)
    x.add_argument("--alfa", type=float, default=0.05)
    x.add_argument("--potencia", type=float, default=0.80)
    s.set_defaults(func=cmd_potencia)

    s = sub.add_parser("econ", parents=[common], help="evaluación económica")
    ec = s.add_subparsers(dest="accion", required=True)
    for name in ("ganadero", "desarrollador"):
        x = ec.add_parser(name, parents=[common])
        x.add_argument("archivo", help="modelo en TOML (ver ejemplos/)")
        x.add_argument("--sim", type=int, default=10000)
        x.add_argument("--semilla", type=int, default=42)
    s.set_defaults(func=cmd_econ)

    s = sub.add_parser("informe", parents=[common], help="informes de investigación")
    ins = s.add_subparsers(dest="accion", required=True)
    x = ins.add_parser("nuevo", parents=[common])
    x.add_argument("id")
    x.add_argument("--salida")
    x.add_argument("--forzar", action="store_true")
    x = ins.add_parser("comprobar", parents=[common])
    x.add_argument("archivo")
    x.add_argument("--online", action="store_true", help="verifica DOIs/PMIDs sin caché (Crossref, OpenAlex, PubMed)")
    x.add_argument("--sellar", action="store_true", help="guarda el sello de verificación firmado")
    x.add_argument("--sin-secciones", dest="sin_secciones", action="store_true",
                   help="no exige las 20 secciones (planes, notas); el sello no valdrá para informes")
    x = ins.add_parser("sello", parents=[common])
    x.add_argument("archivo")
    s.set_defaults(func=cmd_informe)

    s = sub.add_parser("procedencia", parents=[common], help="consulta el registro de procedencia")
    s.add_argument("--ultimas", type=int, default=30)
    s.add_argument("--accion", dest="accion_filtro")
    s.set_defaults(func=cmd_procedencia)

    s = sub.add_parser("llm", parents=[common], help="segunda opinión multimodelo y evaluación de modelos")
    ls = s.add_subparsers(dest="accion", required=True)
    ls.add_parser("modelos", parents=[common])
    x = ls.add_parser("preguntar", parents=[common])
    x.add_argument("--rol")
    x.add_argument("--modelo")
    x.add_argument("--prompt")
    x.add_argument("--archivo")
    x.add_argument("--sistema", help="fichero con instrucciones de sistema")
    x.add_argument("--sensibilidad", choices=["normal", "confidencial"], default="confidencial",
                   help="confidencial (por defecto): solo modelos locales o con no retención")
    x = ls.add_parser("evaluar", parents=[common])
    x.add_argument("archivo", help="banco de preguntas JSONL (ver evals/)")
    x.add_argument("--rol")
    x.add_argument("--modelo")
    s.set_defaults(func=cmd_llm)
    return p


# ============================================================================ códigos de salida y errores
# Único sitio donde se definen los códigos de salida de `nutria` (también salen en `nutria --help`).
SALIDA_OK = 0
SALIDA_RESULTADO = 1
SALIDA_ENTRADA = 2
SALIDA_REGISTRO = 3
SALIDA_RED = 4
SALIDA_CALCULO = 5
SALIDA_NO_ENCONTRADO = 6
SALIDA_INTERNO = 70
SALIDA_INTERRUMPIDA = 130
CODIGOS_SALIDA = {
    SALIDA_OK: ("ok", "la orden terminó y el resultado es el esperado"),
    SALIDA_RESULTADO: ("resultado", "la orden terminó, pero el resultado es negativo o incompleto: discrepancias en "
                                    "`extraccion comparar`, sello no válido, referencia o informe que no supera la "
                                    "verificación, búsqueda o consulta incompleta, caché con copias no válidas, "
                                    "comprobaciones esenciales de `doctor` fallidas"),
    SALIDA_ENTRADA: ("entrada", "entrada no válida: argumentos, fichero inexistente o inaccesible (directorio, "
                                "permisos), JSON o CSV fuera de formato, identificador inexistente en el registro, "
                                "configuración no válida"),
    SALIDA_REGISTRO: ("registro", "regla del registro o error de la base de datos (transición o compuerta no "
                                  "permitida, fuente sin verificar...)"),
    SALIDA_RED: ("red", "fallo de red, host bloqueado, error o respuesta no válida del servicio, credencial rechazada, "
                        "o modo offline sin copia válida en la caché"),
    SALIDA_CALCULO: ("calculo", "falta una dependencia opcional o un cálculo no converge"),
    SALIDA_NO_ENCONTRADO: ("no_encontrado", "el recurso pedido no existe en la fuente externa (HTTP 404)"),
    SALIDA_INTERNO: ("interno", "error interno inesperado (fallo del programa; NUTRIA_DEPURAR=1 muestra la traza)"),
    SALIDA_INTERRUMPIDA: ("interrumpida", "orden interrumpida (Ctrl+C)"),
}


class ErrorOrden(Exception):
    """Error de una orden con su código de salida (tabla `CODIGOS_SALIDA`); main() lo presenta sin traza."""

    def __init__(self, mensaje: str, codigo: int = SALIDA_ENTRADA):
        super().__init__(mensaje)
        self.codigo = codigo


def _texto_codigos_salida() -> str:
    import textwrap
    lines = ["códigos de salida (con --json, los errores salen por stdout como "
             "{\"error\": ..., \"codigo\": ..., \"tipo\": ...}):"]
    for codigo, (_tipo, descripcion) in CODIGOS_SALIDA.items():
        lines.append(textwrap.fill(descripcion, width=100, initial_indent=f"  {codigo:>3}  ",
                                   subsequent_indent=" " * 7))
    return "\n".join(lines)


def _salida_verificacion(res) -> int:
    """Código de salida de la verificación de un DOI o PMID."""
    if res.status in ("error_red", "offline"):
        return SALIDA_RED
    if res.status == "no_encontrada":
        return SALIDA_NO_ENCONTRADO
    return SALIDA_OK if res.ok else SALIDA_RESULTADO


def _error(args, mensaje, codigo: int, prefijo: str, cfg=None, motivo: str | None = None) -> int:
    """Presenta un error (en JSON por stdout con --json; en texto por stderr si no) y devuelve su código."""
    from .http import redact
    texto = redact(mensaje, cfg) or prefijo
    if getattr(args, "json", False):
        payload = {"error": texto, "codigo": codigo, "tipo": CODIGOS_SALIDA.get(codigo, ("interno",))[0]}
        if motivo:
            payload["motivo"] = motivo
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"{prefijo}: {texto}", file=sys.stderr)
    return codigo


def _describe_oserror(exc: OSError) -> str:
    name = getattr(exc, "filename", None)
    reason = exc.strerror or str(exc)
    kind = {IsADirectoryError: "es un directorio", NotADirectoryError: "no es un directorio",
            FileNotFoundError: "no existe", PermissionError: "sin permiso", FileExistsError: "ya existe"}.get(type(exc))
    if name:
        return f"No se puede usar '{name}'" + (f" ({kind})" if kind else "") + f": {reason}"
    return str(exc)


def _parse_args(parser, argv: list[str]):
    """Analiza los argumentos; con --json, un error de uso sale también como JSON por stdout."""
    if "--json" not in argv:
        return parser.parse_args(argv)
    import contextlib
    import io
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stderr(buffer):
            return parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code not in (0, None):
            lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
            message = lines[-1].split(": error: ", 1)[-1] if lines else "argumentos no válidos"
            print(json.dumps({"error": message, "codigo": SALIDA_ENTRADA, "tipo": "entrada"}, ensure_ascii=False))
        raise
    finally:
        sys.stderr.write(buffer.getvalue())


def main(argv: list[str] | None = None) -> int:
    global _RECURSOS
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = _parse_args(parser, argv)
    import requests

    from .config import positive_days
    from .http import NotFoundError, OfflineError
    previous, _RECURSOS = _RECURSOS, []
    cfg = None
    try:
        cfg = load_config()
        if args.offline:
            cfg.offline = True
        if getattr(args, "antiguedad_maxima", None) is not None:
            cfg.cache_max_offline_days = positive_days(args.antiguedad_maxima, "--antiguedad-maxima")
        cfg.actor = args.agente
        return int(args.func(args, cfg) or 0)
    except SystemExit as exc:
        if exc.code is None or isinstance(exc.code, int):
            raise
        # Las órdenes que se niegan con un mensaje (SystemExit(str)): entrada no válida o condición previa.
        _error(args, str(exc.code), SALIDA_ENTRADA, "Error", cfg)
        raise SystemExit(SALIDA_ENTRADA) from None
    except ErrorOrden as exc:
        return _error(args, str(exc), exc.codigo, "Error", cfg)
    except registry.RegistryError as exc:
        return _error(args, str(exc), SALIDA_REGISTRO, "Error de registro", cfg)
    except NotFoundError as exc:
        return _error(args, str(exc), SALIDA_NO_ENCONTRADO, "No encontrado", cfg, exc.motivo)
    except OfflineError as exc:
        return _error(args, str(exc), SALIDA_RED, "Sin red (modo offline)", cfg, exc.motivo)
    except NetworkError as exc:
        return _error(args, str(exc), SALIDA_RED, "Error de red", cfg, exc.motivo)
    except (requests.RequestException, ConnectionError, TimeoutError) as exc:
        return _error(args, f"{type(exc).__name__}: {exc}", SALIDA_RED, "Error de red", cfg)
    except OSError as exc:  # IsADirectoryError, PermissionError, NotADirectoryError, FileNotFoundError...
        return _error(args, _describe_oserror(exc), SALIDA_ENTRADA, "Error", cfg)
    except (ValueError, KeyError, TypeError, ZeroDivisionError, csv.Error) as exc:
        return _error(args, str(exc), SALIDA_ENTRADA, "Error", cfg)
    except sqlite3.Error as exc:
        return _error(args, str(exc), SALIDA_REGISTRO, "Error de la base de datos", cfg)
    except RuntimeError as exc:
        return _error(args, str(exc), SALIDA_CALCULO, "Error", cfg)
    except KeyboardInterrupt:
        return _error(args, "orden interrumpida", SALIDA_INTERRUMPIDA, "Interrumpida", cfg)
    except Exception as exc:  # noqa: BLE001 - fallo del programa: mensaje claro y código propio, sin traza
        import os
        if os.environ.get("NUTRIA_DEPURAR", "").strip() == "1":
            import traceback
            traceback.print_exc()
        return _error(args, f"{type(exc).__name__}: {exc} (fallo del programa; con NUTRIA_DEPURAR=1 se muestra la "
                            "traza)", SALIDA_INTERNO, "Error interno", cfg)
    finally:
        resources, _RECURSOS = _RECURSOS, previous
        for resource in reversed(resources):
            try:
                resource.close()
            except Exception:  # noqa: BLE001 - cerrar nunca debe ocultar el resultado de la orden
                pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
