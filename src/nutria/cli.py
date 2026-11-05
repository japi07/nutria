"""Interfaz de línea de órdenes `nutria`: la API que usan los agentes y las personas.

Ejecuta `nutria --help` o `nutria <orden> --help`. Añade `--json` para salida legible por máquina
y `--agente <nombre>` (o la variable NUTRIA_AGENT) para atribuir la acción en la procedencia.

Las firmas humanas (`--firma-humana`) solo puede escribirlas una persona en su propia terminal. La CLI rechaza
cualquier firma con --agente, con NUTRIA_AGENT, dentro de Claude Code o sin terminal de control
(`_firma_solo_de_personas`), ningún analizador admite abreviaturas de las opciones (`_Parser`) y el hook de Claude
Code del proyecto bloquea además esas órdenes. La firma es una atestación sin autenticar: no identifica a la persona.
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
    """Los evaluadores independientes se identifican con --agente evaluador-<n> o NUTRIA_AGENT (regla 9 de CLAUDE.md)."""
    from .provenance import declared_agent
    return declared_agent(getattr(args, "agente", None)).lower().startswith("evaluador")


# Campos que pueden anticipar la conclusión esperada (el motivo del último cambio de estado: «seleccionada: mejor del
# ranking») y que un evaluador independiente no ve (regla 9; revisión final de P8, residuo de D1-3).
_OCULTOS_A_EVALUADORES = ("status_reason",)


def _sin_conclusion(args, rows):
    """`rows` (una fila o una lista) sin los campos que un evaluador no debe ver, si quien consulta es un evaluador."""
    if not _es_evaluador(args):
        return rows
    def limpia(row):
        if not isinstance(row, dict):
            return row
        out = {k: v for k, v in row.items() if k not in _OCULTOS_A_EVALUADORES}
        if isinstance(out.get("hipotesis"), list):
            out["hipotesis"] = [limpia(h) for h in out["hipotesis"]]
        return out
    return [limpia(r) for r in rows] if isinstance(rows, list) else limpia(rows)


def _no_para_evaluadores(args, what: str) -> None:
    if _es_evaluador(args):
        raise SystemExit(f"Los evaluadores independientes no pueden {what}: puntúan a ciegas "
                         "(regla 9 de CLAUDE.md). Usa `oportunidad ver <ID> --sin-puntuaciones`.")


class _FirmaRechazada(SystemExit):
    """Salida con el código 3 cuyo texto (str) es el motivo del rechazo de la firma."""

    def __init__(self, message: str):
        super().__init__(3)
        self.message = message

    def __str__(self) -> str:
        return self.message


def _firma_solo_de_personas(args) -> None:
    """Regla 12: la firma humana solo se acepta desde la terminal de una persona (comprobación central).

    Se aplica tras analizar los argumentos y antes de tocar nada, a cualquier opción de firma de cualquier orden
    (destinos que empiezan por 'firma', como firma_humana), también a las que se añadan después. Rechaza la orden
    (código 3) si hay señales de agente: --agente, NUTRIA_AGENT, alguna variable de Claude Code en el entorno o en el
    entorno inicial de un proceso antecesor, Claude Code entre los antecesores o, en Linux, ningún proceso de la
    cadena con terminal de control (`provenance.human_terminal_problems`).

    Residuo: la firma es una atestación sin autenticar. Excluye a los agentes que usan la CLI desde Claude Code, pero
    no identifica a la persona. La eluden:
    - en Linux, una sola orden de Bash lanzada desde Claude Code que deje el proceso desvinculado de Claude Code
      (huérfano), con el entorno vacío y con una terminal propia (un multiplexor como tmux o screen, su -P, script,
      una pseudoterminal de Python...). El hook de Bash bloquea por su texto la opción de firma y las formas que
      conoce de vaciar el entorno, obtener una terminal y desvincular un proceso, pero sus listas no son exhaustivas,
      y no ve un nombre construido con variables, concatenación o base64 (`T=tm; ${T}ux ...`, la opción leída de un
      fichero) ni un programa escrito antes en un fichero;
    - en Windows, vaciar el entorno: sin /proc ni ps solo se mira el del propio proceso (en macOS los antecesores
      salen de ps, aunque sin su entorno inicial);
    - una copia modificada de nutria, un programa que escriba en la base de datos y una persona malintencionada.
    Por diseño, una persona sin terminal de control (ssh sin -t, cron, CI, un servicio) no puede firmar.
    """
    from .provenance import signature_problems
    firmas = [dest for dest, value in vars(args).items() if dest.startswith("firma") and value not in (None, False)]
    if not firmas:
        return
    motivos = signature_problems(getattr(args, "agente", None))
    if motivos:
        message = ("Firma humana rechazada (regla 12): la firma humana no la registra un agente; solo se acepta desde la "
                   "terminal de una persona (interactiva, fuera de Claude Code, sin --agente ni NUTRIA_AGENT). "
                   "Detectado: " + ", ".join(dict.fromkeys(motivos)) + ". Registra la acción sin firma (queda como "
                   "propuesta) y pide a la persona responsable que la firme en su propia terminal.")
        # El contrato de errores de main() (`_error`): el texto siempre por stderr y, con --json, además el objeto
        # JSON por stdout ({error, codigo, tipo}). Un flujo cerrado (`| head`, `2>&-`) no cambia el resultado
        # (código 3) ni mezcla el texto con el JSON de stdout.
        _error(args, message, SALIDA_REGISTRO, "Error de registro")
        raise _FirmaRechazada(message)


def _load_json(path: str):
    raw = sys.stdin.read() if path == "-" else Path(path).read_text("utf-8")
    return json.loads(raw)


def _schema(kind: str) -> dict:
    return json.loads(resources.files("nutria.data").joinpath(f"schemas/{kind}.schema.json").read_text("utf-8"))


def _validate(kind: str, data: dict) -> None:
    errors = registry.schema_errors(kind, data)  # esquema JSON + textos informados y específicos ("x-texto")
    if errors:
        raise SystemExit(f"JSON no válido para '{kind}': {'; '.join(errors[:8])}")
    if kind == "extraccion":
        problems = registry.extraction_problems(data)
        if problems:
            raise SystemExit("Extracción: " + "; ".join(problems))


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

    from .config import find_root
    # Con NUTRIA_HOME, el proyecto se crea en la raíz que usan las demás órdenes (la misma resolución que
    # `load_config`: config.find_root), no en el directorio actual.
    home = bool(os.environ.get("NUTRIA_HOME"))
    root = find_root() if home else Path.cwd()
    if root.exists() and not root.is_dir():
        raise NotADirectoryError(20, "NUTRIA_HOME no es un directorio", str(root))
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
# Dominios de lectura con WebFetch (los usan los agentes de Claude Code, no `nutria`). `doctor` los lee de
# docs/FUENTES.md (frase «Para lectura con WebFetch: ...»); esta lista solo se usa si el proyecto no tiene ese
# documento o no se reconoce la frase.
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
    """Versión de la base de datos que respondió a la comprobación de doctor, si la expone, comprobada con el mismo
    criterio que las órdenes ({"version": ...} o {"version_no_reconocida": ...}; {} si no la expone)."""
    from .sources import chembl, reactome, stringdb, uniprot
    nombre, texto = res.get("nombre"), res.get("texto") or ""
    if res.get("estado") != "ok":
        return {}
    try:
        if nombre == "STRING":
            return stringdb.parse_version(json.loads(texto))
        if nombre == "ChEMBL":
            return chembl.parse_release(json.loads(texto))
        if nombre == "Reactome":
            return reactome.parse_version(texto)
        if nombre == "UniProt":
            return uniprot.parse_release(res.get("cabeceras"))
    except ValueError:
        return {"version_no_reconocida": texto.strip()[:60]} if texto.strip() else {}
    return {}


def _webfetch_dominios(root: Path) -> tuple[list[str], str]:
    """Dominios de lectura con WebFetch que declara docs/FUENTES.md y de dónde salen ("docs/FUENTES.md" o, si el
    proyecto no tiene el documento o no se reconoce la frase «Para lectura con WebFetch:», "lista interna")."""
    import re
    try:
        text = re.sub(r"\s+", " ", (root / "docs" / "FUENTES.md").read_text("utf-8"))
    except (OSError, UnicodeDecodeError):
        return list(WEBFETCH_DOMINIOS), "lista interna"
    start = text.find("lectura con WebFetch:")
    if start < 0:
        return list(WEBFETCH_DOMINIOS), "lista interna"
    segment = text[start:]
    end = segment.find("Comprueba con")
    segment = segment[:end] if end > 0 else segment[:800]
    found = list(dict.fromkeys(re.findall(r"`([a-z0-9][a-z0-9.-]*\.[a-z]{2,})`", segment.lower())))
    return (found, "docs/FUENTES.md") if found else (list(WEBFETCH_DOMINIOS), "lista interna")


_HOOK_PROBE = """import json, sys
sys.path.insert(0, sys.argv[1])
try:
    import jsonschema, nutria, nutria.db, nutria.reports.check
except Exception as exc:
    print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(3)
print(json.dumps({"doctor_hook": "ok", "python": sys.executable, "nutria": nutria.__file__}))
"""
_HOOK_CANDIDATES = (Path(".venv") / "bin" / "python", Path(".venv") / "Scripts" / "python.exe", "python3", "python")


def _hook_candidates(root: Path) -> list[str]:
    """Intérpretes que scripts/hooks/run_hook.sh encontraría, en su orden (.venv, python3, python)."""
    import os
    import shutil
    found: list[str] = []
    for candidate in _HOOK_CANDIDATES:
        if isinstance(candidate, Path):
            path = root / candidate
            if path.is_file() and os.access(path, os.X_OK):
                found.append(str(path))
        else:
            path = shutil.which(candidate)
            if path and path not in found:
                found.append(path)
    return found


def _probe_interpreter(interpreter: str, root: Path) -> tuple[bool, str]:
    """¿Importa este intérprete lo que usa check_research_file.py (con <raíz>/src antepuesto a sys.path)?"""
    import subprocess
    try:
        proc = subprocess.run([interpreter, "-c", _HOOK_PROBE, str(root / "src")], capture_output=True, text=True,
                              timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)[:300]
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, (lines[-1] if lines else f"código {proc.returncode}")[:300]
    return True, ""


def _run_hook_black_box(root: Path) -> dict | None:
    """Ejecuta una copia de scripts/hooks/run_hook.sh (con la .venv del proyecto enlazada) con una sonda en lugar del
    hook: así se comprueba el intérprete que elegiría la versión instalada de run_hook.sh, con su misma lógica (tanto
    si usa el primero que encuentra como si prueba el siguiente cuando uno falla). None si no se pudo ejecutar."""
    import os
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="nutria_doctor_") as tmp:
        hooks = Path(tmp) / "scripts" / "hooks"
        hooks.mkdir(parents=True)
        try:
            (hooks / "run_hook.sh").write_text((root / "scripts" / "hooks" / "run_hook.sh").read_text("utf-8"), "utf-8")
            (hooks / "doctor_probe.py").write_text(
                "import sys\nsys.argv.append(" + repr(str(root / "src")) + ")\n" + _HOOK_PROBE, "utf-8")
            if (root / ".venv").exists():
                os.symlink(root / ".venv", Path(tmp) / ".venv", target_is_directory=True)
            proc = subprocess.run(["sh", str(hooks / "run_hook.sh"), "doctor_probe.py"], input="{}",
                                  capture_output=True, text=True, timeout=180)
        except (OSError, UnicodeDecodeError, subprocess.SubprocessError):
            return None
    for line in reversed((proc.stdout or "").splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("doctor_hook") == "ok":
            return {"ok": True, "interprete": data.get("python"), "nutria": data.get("nutria")}
    return {"ok": False, "codigo": proc.returncode}


def _doctor_hook(root: Path) -> dict:
    """¿Puede scripts/hooks/run_hook.sh ejecutar los hooks de Claude Code? Se ejecuta una copia del propio
    run_hook.sh con una sonda que importa, como check_research_file.py (con <raíz>/src antepuesto a sys.path),
    jsonschema y nutria; además se prueba cada intérprete candidato (.venv, python3, python) para explicar los
    fallos."""
    import shutil
    if not (root / "scripts" / "hooks" / "run_hook.sh").is_file():
        return {"estado": "no_aplica", "esencial": False,
                "detalle": "el proyecto no tiene los hooks de Claude Code (scripts/hooks/run_hook.sh)"}
    if not shutil.which("sh"):
        return {"estado": "no_encontrado", "esencial": True, "detalle": "no hay `sh` para ejecutar run_hook.sh"}
    candidates = _hook_candidates(root)
    if not candidates:
        return {"estado": "no_encontrado", "esencial": True,
                "detalle": "run_hook.sh no encuentra Python: bloqueará las escrituras en research/ (falla cerrado)"}
    checks = []
    for interpreter in candidates:
        ok, why = _probe_interpreter(interpreter, root)
        checks.append({"interprete": interpreter, "ok": ok, **({"detalle": why} if why else {})})
    runner = _run_hook_black_box(root)
    failed = "; ".join(f"{c['interprete']}: {c['detalle']}" for c in checks if not c["ok"])
    if runner is None:   # no se pudo ejecutar la copia: se juzga como la versión que usa el primero que encuentra
        first = checks[0]
        if first["ok"]:
            return {"estado": "ok", "esencial": True, "interprete": first["interprete"], "candidatos": checks}
        return {"estado": "sin_dependencias", "esencial": True, "interprete": first["interprete"],
                "candidatos": checks,
                "detalle": f"{failed} — el hook bloqueará las escrituras en research/ que debía validar"}
    if runner["ok"]:
        out = {"estado": "ok", "esencial": True, "interprete": runner.get("interprete"), "nutria": runner.get("nutria"),
               "candidatos": checks}
        if failed:
            out["detalle"] = f"run_hook.sh usa otro intérprete porque estos no sirven: {failed}"
        return out
    return {"estado": "sin_dependencias", "esencial": True, "interprete": checks[0]["interprete"], "candidatos": checks,
            "detalle": (failed or f"run_hook.sh terminó con código {runner.get('codigo')} sin ejecutar la sonda")
            + " — el hook bloqueará las escrituras en research/ que debía validar"}


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
    dominios, origen_dominios = _webfetch_dominios(cfg.root)
    report["webfetch_origen"] = origen_dominios
    # Fuera de un proyecto (sin nutria.toml), doctor no deja procedencia: no crea research/ donde no lo hay.
    record = bool(report["config"])
    for nombre, url, esencial in FUENTES_DOCTOR:
        url = url or f"{stringdb.api_url(cfg)}/json/version"
        params = None
        if nombre == "OpenAlex":
            if not cfg.secret("OPENALEX_API_KEY"):
                report["red"][nombre] = {"estado": "sin_clave", "url": url, "esencial": esencial}
                continue
            params = {"api_key": cfg.secret("OPENALEX_API_KEY")}
        res = client.probe(url, params=params, headers={"Accept": "*/*"}, record=record)
        res.update(nombre=nombre, url=url, esencial=esencial)
        found = _doctor_versiones(res)
        if found.get("version"):
            report["versiones"][nombre] = found["version"]
        elif found.get("version_no_reconocida"):
            report.setdefault("versiones_no_reconocidas", {})[nombre] = found["version_no_reconocida"]
        res.pop("texto", None)
        res.pop("nombre", None)
        report["red"][nombre] = res
    if cfg.secret("LENS_API_TOKEN"):
        res = client.probe(patents.LENS_URL, method="POST",
                           json_body={"query": {"query_string": {"query": "rumen"}}, "size": 1},
                           headers={"Authorization": f"Bearer {cfg.secret('LENS_API_TOKEN')}"}, record=record)
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
    for domain in dominios:
        res = client.probe(f"https://{domain}/", headers={"Accept": "*/*"}, record=record)
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
    if record:
        log_event(cfg, "doctor", inputs={"offline": cfg.offline},
                  outputs={"red": {n: r["estado"] for n, r in report["red"].items()}, "versiones": report["versiones"],
                           "versiones_no_reconocidas": report.get("versiones_no_reconocidas") or {},
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
        lines += [f"    WebFetch {d:<19} https://{d}/" for d in dominios]
        lines.append(f"    (dominios de WebFetch tomados de {origen_dominios})")
    else:
        lines.append("  fuentes de nutria (esenciales marcadas con *):")
        lines += [f"  {'*' if r.get('esencial') else ' '} {n:<28} {_doctor_estado(r)}" for n, r in report["red"].items()]
        if report["versiones"]:
            lines.append("  versiones: " + " | ".join(f"{k} {v}" for k, v in report["versiones"].items()))
        for name, raw in (report.get("versiones_no_reconocidas") or {}).items():
            lines.append(f"  AVISO: {name} respondió sin una versión reconocible («{raw}»): no se registra como versión")
        lines.append(f"  dominios de WebFetch (agentes de Claude Code; lista de {origen_dominios}), accesibilidad desde "
                     "esta máquina:")
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
    if fallos:
        lines.append("  Resultado: FALLAN comprobaciones esenciales: " + ", ".join(fallos))
    elif cfg.offline:
        lines.append("  Resultado: OK en lo comprobado; la red NO se ha comprobado (modo offline)")
    else:
        lines.append("  Resultado: OK")
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
    _emit(args, {**data, "lista_oportunidades": _sin_conclusion(args, opps)}, "\n".join(lines))
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


# Las versiones se piden aparte y se guardan en la caché un día (ttl_days=1): si un resultado sale de una copia de la
# caché con una fecha que se aleja más que eso de la de la versión, la versión puede no ser la de ese resultado.
VERSION_TOLERANCIA_DIAS = 1.0


def _version_valida(info: dict, nombre: str, avisos: list[str]) -> dict:
    """Deja en `info` solo una versión reconocible; avisa si no la hay o si la respuesta traía otra cosa."""
    info = dict(info or {})
    raw = info.pop("version_no_reconocida", None)
    if not info.get("version"):
        info = {}
        if raw:
            avisos.append(f"{nombre} respondió sin una versión reconocible («{raw}»): el resultado no queda ligado a "
                          "una versión concreta")
        else:
            avisos.append(f"{nombre} no informó de su versión: el resultado no queda ligado a una versión concreta")
    return info


def _version_fuente(func, client, nombre: str, avisos: list[str]) -> dict:
    """Versión de la base de datos consultada (`func(client)`, que se llama DESPUÉS de pedir los resultados), o {}
    con un aviso si no se pudo saber. Añade `version_consultada` (fecha de la respuesta que dio la versión: la de su
    copia en la caché o la actual) y avisa si algún resultado sale de una copia de la caché de otra fecha, porque
    entonces la versión puede no ser la suya."""
    import time

    from .http import _iso, _parse_ts
    previous = len(getattr(client, "cache_hits", None) or [])
    try:
        info = func(client) or {}
    except NetworkError as exc:
        if exc.motivo == "respuesta":
            avisos.append(f"{nombre} respondió sin una versión reconocible ({exc}): el resultado no queda ligado a una "
                          "versión concreta")
        else:
            avisos.append(f"No se pudo consultar la versión de {nombre}: {exc}")
        return {}
    info = _version_valida(info, nombre, avisos)
    if not info:
        return info
    hits = list(getattr(client, "cache_hits", None) or [])
    own = hits[previous:]
    moment = (_parse_ts(own[-1].get("fecha")) if own else None) or time.time()
    info["version_consultada"] = _iso(moment)
    stale = [h for h in hits[:previous]
             if abs((_parse_ts(h.get("fecha")) or moment) - moment) > VERSION_TOLERANCIA_DIAS * 86400]
    if stale:
        oldest = min(stale, key=lambda h: _parse_ts(h.get("fecha")) or moment)
        avisos.append(f"La versión de {nombre} ({info['version']}) es la de una respuesta del {info['version_consultada']}, "
                      f"pero {len(stale)} respuesta(s) de los resultados salen de copias de la caché de otra fecha (la "
                      f"más alejada, del {oldest.get('fecha')}): esos resultados pueden ser de otra versión de "
                      f"{nombre}")
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
    registered, conflicts, changes = [], [], []
    if args.registrar and records:
        conn = _conn(cfg)
        with registry.transaction(conn):
            for rec in records:
                if rec.key:
                    try:  # un conflicto (p. ej. DOI y PMID en dos fuentes distintas) no se registra ni deshace el resto
                        registered.append(registry.upsert_source(conn, {**rec.to_dict(), "verified": False},
                                                                 args.agente, changes))
                    except registry.RegistryError as exc:
                        conflicts.append({"registro": rec.key, "error": str(exc)})
    identifiers = record_identifiers(records)
    inputs = {"fuente": args.fuente, "max": args.max, "desde": args.desde, "hasta": args.hasta,
              "sensibilidad": "confidencial" if confidencial else "normal"}
    outputs = {"total": total, "devueltos": len(records), "registrados": len(registered),
               "conflictos": len(conflicts), "paginas": res.get("paginas"), "incompleto": incompleto,
               "cache": _red_procedencia(client)}
    if confidencial:
        # Ni la consulta ni lo que devolvió quedan en claro en el registro versionado.
        inputs["consulta_sha256"] = sha256_text(args.consulta)
        outputs["identificadores_sha256"] = sha256_text(json.dumps(identifiers, sort_keys=True))
        outputs["avisos_api"] = len(api_avisos)
    else:
        inputs["consulta"] = args.consulta
        outputs["identificadores"] = identifiers
        outputs["avisos"] = api_avisos
    # Huellas con la API común de la procedencia (petición de P1, D9-7 y D1-11): la de la consulta y la de los
    # identificadores devueltos, en las dos sensibilidades, en huellas.entrada y huellas.salida.
    log_event(cfg, "busqueda", inputs=inputs, outputs=outputs, actor=args.agente,
              params={"fuente": args.fuente, "max": args.max, "desde": args.desde, "hasta": args.hasta},
              input_hashes={"consulta": sha256_text(args.consulta)},
              output_hashes={"identificadores": sha256_text(json.dumps(identifiers, sort_keys=True))})
    # Los cambios del registro de fuentes no llevan la consulta en claro (puede ser confidencial): solo su huella.
    _log_source_changes(cfg, args, changes, {"fuente": args.fuente, "consulta_sha256": sha256_text(args.consulta)})
    notas = []
    if confidencial:
        notas.append("consulta confidencial: la procedencia guarda solo su huella SHA-256 (y la de los identificadores "
                     f"devueltos), pero la consulta ha viajado igualmente al servicio externo ({args.fuente})")
        if registered:
            notas.append("las fuentes registradas con --registrar quedan en claro en el registro (research/nutria.sqlite, "
                         "que se versiona con git) y pueden dejar ver el tema de la consulta")
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
        new = sum(1 for c in changes if c.get("accion") == "alta")
        header += (f"\nRegistradas {new} fuentes nuevas SIN verificar (no sirven para [PUB] hasta "
                   "`nutria verificar --pendientes`)"
                   + (f"; {len(registered) - new} ya estaban en el registro" if len(registered) > new else ""))
    if changes and _change_lines(changes):
        header += "\nCambios y avisos del registro de fuentes:\n" + "\n".join(_change_lines(changes))
    extra = _red_lineas(cfg, client, api_avisos, notas)
    _emit(args, {"total": total, "registros": [r.to_dict() for r in records], "registradas": registered,
                 "cambios": [c for c in changes if c.get("accion") != "alta"], "conflictos": conflicts,
                 "devueltos": len(records), "paginas": res.get("paginas"), "incompleto": incompleto,
                 "siguiente_cursor": res.get("next_cursor"), **_red_salida(client, api_avisos), "notas": notas},
          header + "\n" + _records_text(records) + ("\n" + "\n".join(extra) if extra else ""))
    if conflicts:
        _imprimir(sys.stderr, f"Error de registro: {len(conflicts)} resultado(s) NO registrado(s) por conflicto con "
                              "el registro:\n" + "\n".join(f"  {c['registro']}: {c['error']}" for c in conflicts))
        return SALIDA_REGISTRO
    return SALIDA_RESULTADO if incompleto else SALIDA_OK


def _license_label(value) -> str:
    """Etiqueta corta de la licencia de una fuente (URL de Creative Commons, código de OpenAlex u otra URL)."""
    import re
    if not value:
        return "?"
    text = str(value).strip()
    qualifier = re.search(r"\s(\([^()]*\))\s*$", text)       # "(manuscrito aceptado; embargo: 365 días)"
    note = f" {qualifier.group(1)}" if qualifier else ""
    if qualifier:
        text = text[:qualifier.start()].strip()
    match = re.search(r"creativecommons\.org/licenses/([a-z-]+)/(\d(?:\.\d)?)", text, re.IGNORECASE)
    if match:
        return f"CC {match.group(1).upper()} {match.group(2)}{note}"
    if re.search(r"creativecommons\.org/publicdomain/zero|^cc0", text, re.IGNORECASE):
        return "CC0" + note
    if re.fullmatch(r"cc-by(?:-[a-z]{2})*", text, re.IGNORECASE):
        return "CC " + text[3:].upper() + note
    return (text if len(text) <= 40 else text[:37] + "...") + note


def _change_lines(changes: list[dict]) -> list[str]:
    """Texto de los cambios del registro de fuentes que no son un alta (rellenos, correcciones, fusiones, avisos)."""
    lines = []
    for change in changes:
        action = change.get("accion")
        if action == "alta":
            continue
        detail = change.get("motivo") or change.get("eliminada") or change.get("campos") or ""
        if isinstance(detail, dict):
            detail = ", ".join(detail)
        elif isinstance(detail, list):
            detail = ", ".join(map(str, detail))
        lines.append(f"  {change.get('id')}: {action.replace('_', ' ')}" + (f" ({detail})" if detail else ""))
    return lines


def _log_source_changes(cfg, args, changes: list[dict], inputs: dict) -> None:
    """Deja en la procedencia cada cambio del registro de fuentes que no es un alta."""
    for change in changes:
        if change.get("accion") != "alta":
            log_event(cfg, f"fuente_{change.get('accion')}", inputs=inputs, outputs=change, actor=args.agente)


def cmd_fuente(args, cfg) -> int:
    from .verify.citations import verify_doi, verify_pmid
    conn = _conn(cfg)
    if args.accion == "listar":
        rows = registry.list_sources(conn, unverified_only=args.no_verificadas)
        text = "\n".join(f"{r['id']:<45} {'✓' if r['verified'] else '·'} "
                         f"{'RETRACTADO ' if r.get('retracted') == 1 else ('retractación? ' if r.get('retracted') is None else '')}"
                         f"[licencia {_license_label(r.get('license'))}] "
                         f"{(r.get('title') or '')[:90]}" for r in rows) or "(sin fuentes)"
        _emit(args, rows, text)
        return 0
    if args.accion == "ver":
        row = registry.get_source(conn, args.id)
        if not row:
            raise SystemExit(f"No existe la fuente {args.id}")
        row["licencia"] = _license_label(row.get("license")) if row.get("license") else None
        _emit(args, row)
        return 0
    # agregar: verifica contra la fuente autoritativa y registra lo que dio esa verificación (sin otra consulta)
    client = _client(cfg, fresh=True)
    if args.doi:
        ver = verify_doi(client, args.doi)
    elif args.pmid:
        ver = verify_pmid(client, args.pmid)
    else:
        raise SystemExit("Indica --doi o --pmid")
    if ver.status != "verificada" or ver.record is None:
        raise ErrorOrden(f"No se registra: {ver.status} ({ver.message})",
                         _salida_verificacion(ver) or SALIDA_RESULTADO)
    rec = ver.record.to_dict()
    via = ver.record.source
    rec.update(verified=True, verified_via=via, retracted=ver.retracted, study_type=args.tipo_estudio,
               species=args.especie, notes=args.notas)
    changes: list[dict] = []
    source_id = registry.upsert_source(conn, rec, args.agente, changes)
    log_event(cfg, "fuente_agregada", inputs={"doi": args.doi, "pmid": args.pmid},
              outputs={"id": source_id, "via": via, "retractado": ver.retracted, "licencia": ver.license},
              actor=args.agente)
    _log_source_changes(cfg, args, changes, {"doi": args.doi, "pmid": args.pmid})
    warn = ""
    if ver.retracted:
        warn = "  ATENCIÓN: trabajo RETRACTADO"
    elif ver.retracted is None:
        warn = "  ATENCIÓN: no se pudo comprobar la retractación; no servirá para [PUB] hasta comprobarla"
    lines = [f"Registrada y verificada ({via}): {source_id}{warn}", f"  {rec.get('title')}",
             f"  licencia: {ver.license or 'la fuente no la indica'}"]
    if ver.message:                    # p. ej. un PMID que da OpenAlex y PubMed no confirma, y no se asocia
        lines.append(f"  verificación: {ver.message}")
    if args.doi and args.pmid and rec.get("pmid") != str(args.pmid).strip():
        lines.append(f"  AVISO: no se asocia el PMID {args.pmid}: la verificación del DOI no lo confirma "
                     f"(PMID confirmado: {rec.get('pmid') or 'ninguno'}).")
    requested = f"{ver.kind}:{ver.identifier}"
    if source_id != requested:
        if not any(c.get("accion") == "alta" and c.get("id") == source_id for c in changes):
            where = f"la fuente ya estaba registrada como {source_id} (mismo trabajo)"
        elif source_id.startswith("doi:"):     # alta nueva: se nombra por el DOI que da la verificación
            where = f"la fuente se registra como {source_id} (por el DOI que da la verificación)"
        else:
            where = f"la fuente se registra como {source_id} (el identificador que da la verificación)"
        lines.append(f"  AVISO: {where}: usa {source_id}, no {requested}, en afirmaciones y extracciones.")
    lines += _change_lines(changes)
    _emit(args, {"id": source_id, "registro": rec, "cambios": changes}, "\n".join(lines))
    return 0


def _verification_text(res) -> str:
    retraction = {True: "RETRACTADO", False: "no retractado", None: "retractación DESCONOCIDA"}[res.retracted]
    text = f"{res.kind.upper()} {res.identifier}: {res.status} | {retraction}"
    if res.mismatch:
        text += " | DISCREPANCIA con la cita"
    if res.title:
        text += f"\n  {res.title} ({res.year}) {res.journal or ''}"
    if res.source or res.registration_agency:
        text += f"\n  fuente: {res.source or '-'} | agencia de registro: {res.registration_agency or '-'}"
        text += f" | licencia: {res.license or 'no indicada'}"
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
        out, codes = [], []
        for r in rows:
            if registry.get_source(conn, r["id"]) is None:   # fusionada con otra en esta misma pasada
                continue
            # Se verifica por el identificador que da nombre a la fuente (pmid:... por su PMID).
            by_pmid = r.get("pmid") and (r["id"].startswith("pmid:") or not r.get("doi"))
            res = verify_pmid(client, r["pmid"]) if by_pmid else (verify_doi(client, r["doi"]) if r.get("doi") else None)
            if res is None:
                out.append({"id": r["id"], "estado": "sin_identificador", "retractado": None,
                            "detalle": "sin DOI ni PMID: no se puede verificar"})
                codes.append(SALIDA_RESULTADO)
                continue
            entry = {"id": r["id"], "estado": res.status, "retractado": res.retracted}
            changes: list[dict] = []
            if res.status == "verificada" and res.record is not None:
                # Se registra el registro verificado completo: sus metadatos sustituyen a los de la búsqueda.
                rec = res.record.to_dict()
                rec.update(verified=True, verified_via=res.record.source, retracted=res.retracted)
                try:
                    entry["registro"] = registry.upsert_source(conn, rec, args.agente, changes)
                except registry.RegistryError as exc:
                    entry.update(estado="conflicto", detalle=str(exc))
            elif res.message:
                entry["detalle"] = res.message
            entry["cambios"] = changes
            log_event(cfg, "verificacion_fuente", inputs={"id": r["id"]},
                      outputs={"estado": entry["estado"], "retractado": res.retracted,
                               "detalle": entry.get("detalle")}, actor=args.agente)
            _log_source_changes(cfg, args, changes, {"id": r["id"]})
            out.append(entry)
            codes.append(SALIDA_REGISTRO if entry["estado"] == "conflicto" else _salida_verificacion(res))
        lines = []
        for o in out:
            lines.append(f"{o['id']}: {o['estado']} | retractación "
                         f"{ {True: 'SÍ', False: 'no', None: 'desconocida'}[o['retractado']] }")
            if o.get("detalle"):
                lines.append(f"  {o['detalle']}")
            lines += _change_lines(o.get("cambios") or [])
        _emit(args, out, "\n".join(lines) or "No hay fuentes pendientes de verificar")
        # Lote: conflicto con el registro (3), después red caída o sin red (4), después negativo o incompleto (1).
        for code in (SALIDA_REGISTRO, SALIDA_RED):
            if code in codes:
                return code
        return SALIDA_RESULTADO if any(codes) else SALIDA_OK
    if not targets:
        raise SystemExit("Indica --doi, --pmid, --pendientes, un fichero .md o 'manual <URL>'")
    from .reports.check import is_report_path
    # En un informe (research/informes, también en subcarpetas) se exige la misma estructura que en `informe
    # comprobar`, así que las dos órdenes producen el mismo sello; en otros documentos no se exigen las 20 secciones.
    args.sin_secciones = not is_report_path(Path(targets[0]))
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


SENSIBILIDAD_AYUDA = ("confidencial: la procedencia (versionada) guarda solo la huella SHA-256 de la consulta y de lo "
                      "que devolvió; la consulta viaja igualmente al servicio externo")


def _es_confidencial(args) -> bool:
    return getattr(args, "sensibilidad", "normal") == "confidencial"


def _registro_consulta(args, inputs: dict, consulta: dict, outputs: dict, devuelto: dict) -> list[str]:
    """Completa la procedencia de una consulta a una fuente externa: en claro, o con --sensibilidad confidencial
    solo con las huellas SHA-256 de la consulta (`consulta`) y de lo que devolvió (`devuelto`), para no dejar en el
    registro versionado algo que revele una combinación inventiva. Devuelve las notas para la salida."""
    if not _es_confidencial(args):
        inputs.update(consulta, sensibilidad="normal")
        outputs.update(devuelto)
        return []

    def digest(value) -> str:
        return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))

    inputs.update(consulta_sha256=digest(consulta), sensibilidad="confidencial", confidencial=True)
    outputs.update({f"{name}_sha256": digest(value) for name, value in devuelto.items()})
    return ["consulta confidencial: la procedencia guarda solo su huella SHA-256 (y la de lo que devolvió), pero la "
            "consulta ha viajado igualmente al servicio externo"]


def cmd_molecula(args, cfg) -> int:
    client = _client(cfg, confidential=_es_confidencial(args))
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
    inputs = {"fuente": args.fuente, "peligros": args.peligros, "mecanismos": args.mecanismos}
    outputs = {"n": len(data), "fuente": fuente, "incompleto": incompleto, "cache": _red_procedencia(client)}
    devuelto: dict = {"identificadores": identifiers}
    if args.fuente == "pubchem" and args.peligros:
        devuelto["ghs"] = {str(c.get("CID")): c["GHS"]["estado"] for c in data}
    notas = _registro_consulta(args, inputs, {"nombre": args.nombre}, outputs, devuelto)
    log_event(cfg, "molecula", inputs=inputs, outputs=outputs, actor=args.agente)
    lines = [_json_text(data), _fuente_texto(fuente)]
    if args.fuente == "pubchem" and args.peligros:
        lines += [f"GHS CID {c.get('CID')}: {c['GHS']['estado']}"
                  + (f" ({', '.join(c['GHS']['frases_H'])})" if c["GHS"]["frases_H"] else "")
                  + f" — {c['GHS']['detalle']}" for c in data]
    lines += _red_lineas(cfg, client, avisos, notas)
    _emit(args, {"resultados": data, "fuente": fuente, "incompleto": incompleto, **_red_salida(client, avisos),
                 "notas": notas}, "\n".join(lines))
    return SALIDA_RESULTADO if incompleto else SALIDA_OK


def cmd_diana(args, cfg) -> int:
    from .sources import chembl
    client = _client(cfg, confidential=_es_confidencial(args))
    avisos: list[str] = []
    tried: list[dict] = []
    if args.max < 1:
        raise ValueError("--max debe ser un entero positivo")
    targets = chembl.target_search(client, args.consulta, organism=args.organismo, limit=args.max, tried=tried)
    nombres = {"nombre_preferido": "nombre preferido", "accesion_uniprot": "número de acceso de UniProt",
               "sinonimo_o_simbolo": "sinónimo o símbolo de gen", "texto_libre": "texto libre"}
    failed = [t for t in tried if t.get("error")]
    for t in failed:
        avisos.append(f"La búsqueda por {nombres.get(t['estrategia'], t['estrategia'])} falló ({t['error']}): los "
                      "resultados están INCOMPLETOS")
    if args.organismo and not targets:
        avisos.append(f"ChEMBL no devolvió dianas de «{args.organismo.strip()}» para «{args.consulta}» (buscado por "
                      + ", ".join(nombres.get(t["estrategia"], t["estrategia"]) for t in tried
                                  if not t.get("error")) + "). No demuestra "
                      "que la diana no exista en esa especie (ChEMBL tiene pocas dianas de especies ganaderas): busca "
                      "el ortólogo con `nutria proteina` (UniProt) y etiqueta la diana de otra especie como evidencia "
                      "de otra especie")
    for t in tried:
        if t.get("truncada"):
            avisos.append(f"La búsqueda por {nombres.get(t['estrategia'], t['estrategia'])} llegó al máximo "
                          f"({args.max}): puede haber más dianas que no se han visto (sube --max)")
    if args.actividades and targets:
        for t in targets[:2]:
            t["actividades"] = chembl.activities(client, target_chembl_id=t["target_chembl_id"], limit=25)
    fuente = {"nombre": "ChEMBL", **_version_fuente(chembl.release, client, "ChEMBL", avisos)}
    inputs: dict = {}
    outputs = {"n": len(targets), "incompleto": bool(failed), "fuente": fuente, "cache": _red_procedencia(client)}
    if _es_confidencial(args):   # el texto de un error puede repetir la consulta: solo si hubo error
        outputs["estrategias"] = [{**t, "error": bool(t.get("error"))} for t in tried]
    else:
        outputs["estrategias"] = tried
    notas = _registro_consulta(args, inputs, {"consulta": args.consulta, "organismo": args.organismo}, outputs,
                               {"identificadores": [t["target_chembl_id"] for t in targets]})
    log_event(cfg, "diana", inputs=inputs, outputs=outputs, actor=args.agente)
    lines = [_json_text(targets),
             "Estrategias: " + ", ".join(f"{t['estrategia']} ({t['n']})" for t in tried), _fuente_texto(fuente)]
    lines += _red_lineas(cfg, client, avisos, notas)
    _emit(args, {"resultados": targets, "estrategias": tried, "incompleto": bool(failed), "fuente": fuente,
                 **_red_salida(client, avisos), "notas": notas}, "\n".join(lines))
    return SALIDA_RESULTADO if failed else SALIDA_OK


def cmd_proteina(args, cfg) -> int:
    from .sources import uniprot
    from .sources.base import taxon_id
    client = _client(cfg, confidential=_es_confidencial(args))
    avisos: list[str] = []
    rows = uniprot.search(client, args.consulta, taxon=taxon_id(args.especie) if args.especie else None,
                          size=args.max, reviewed_only=args.revisadas)
    # La versión sale de las cabeceras de la última respuesta (de la red o de su copia en la caché): es la suya.
    fuente = {"nombre": "UniProtKB", **_version_valida(uniprot.release(client), "UniProt (cabecera X-UniProt-Release)",
                                                       avisos)}
    inputs = {"especie": args.especie}
    outputs = {"n": len(rows), "fuente": fuente, "cache": _red_procedencia(client)}
    notas = _registro_consulta(args, inputs, {"consulta": args.consulta}, outputs,
                               {"identificadores": [r["accesion"] for r in rows]})
    log_event(cfg, "proteina", inputs=inputs, outputs=outputs, actor=args.agente)
    text = "\n".join(f"{r['accesion']:<12} {','.join(r['genes'])[:20]:<20} {r['organismo'] or '':<18} "
                     f"{'rev' if r['revisada'] else 'no rev'}  {r['proteina'] or ''}" for r in rows) or "(sin resultados)"
    lines = [text, _fuente_texto(fuente)] + _red_lineas(cfg, client, avisos, notas)
    _emit(args, {"resultados": rows, "fuente": fuente, **_red_salida(client, avisos), "notas": notas},
          "\n".join(lines))
    return SALIDA_OK


def cmd_red(args, cfg) -> int:
    from .sources import stringdb
    from .sources.base import taxon_id
    client = _client(cfg, confidential=_es_confidencial(args))
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
    inputs = {"especie": species, "puntuacion_min": args.puntuacion_min}
    outputs = {"interacciones": len(data["interacciones"]),
               "enriquecimiento": len(data["enriquecimiento"]) if "enriquecimiento" in data else None,
               "fuente": fuente, "cache": _red_procedencia(client)}
    notas = _registro_consulta(args, inputs, {"genes": args.genes}, outputs, {})
    log_event(cfg, "red_string", inputs=inputs, actor=args.agente, outputs=outputs)
    lines = [_json_text(data), _fuente_texto(fuente)] + _red_lineas(cfg, client, avisos, notas)
    _emit(args, {**data, "fuente": fuente, **_red_salida(client, avisos), "notas": notas}, "\n".join(lines))
    return SALIDA_OK


def cmd_rutas(args, cfg) -> int:
    from .sources import reactome
    client = _client(cfg, confidential=_es_confidencial(args))
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
    inputs = {"accion": args.accion}
    outputs = {"n": len(identifiers), "fuente": fuente, "cache": _red_procedencia(client)}
    notas = _registro_consulta(args, inputs, {"terminos": args.terminos}, outputs, {"identificadores": identifiers})
    log_event(cfg, "rutas", inputs=inputs, actor=args.agente, outputs=outputs)
    lines = [_json_text(data), _fuente_texto(fuente)] + _red_lineas(cfg, client, avisos, notas)
    _emit(args, {"resultados": data, "fuente": fuente, **_red_salida(client, avisos), "notas": notas},
          "\n".join(lines))
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
    log_event(cfg, "busqueda_patentes", inputs=inputs, outputs=outputs, actor=args.agente,
              params={"lens": bool(args.lens), "max": args.max},
              input_hashes={"consulta": sha256_text(args.consulta)},
              output_hashes={"lens_ids": sha256_text(json.dumps(lens_ids))} if args.lens else None)
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
        res = export_cache(client, destination, include_confidential=args.incluir_confidenciales)
        log_event(cfg, "cache_exportar", inputs={"fichero": str(destination),
                                                 "incluir_confidenciales": args.incluir_confidenciales},
                  outputs={"entradas": res["entradas"], "sha256": res["sha256"], "no_exportadas": res["no_exportadas"],
                           "confidenciales_omitidas": res["confidenciales_omitidas"],
                           "confidenciales_incluidas": res["confidenciales_incluidas"]},
                  actor=args.agente)
        aviso = ("la exportación contiene las consultas y las respuestas en claro: trátala como un dato del proyecto "
                 "y no la publiques ni la versiones sin revisarla")
        if res["confidenciales_incluidas"]:
            aviso += (f"; incluye {res['confidenciales_incluidas']} consulta(s) CONFIDENCIAL(ES) (búsquedas con "
                      "--sensibilidad confidencial o de patentes) que pueden revelar una idea patentable")
        lines = [f"Exportadas {res['entradas']} copias a {destination} (sha256 {res['sha256']})"]
        if res["confidenciales_omitidas"]:
            lines.append(f"No exportadas {res['confidenciales_omitidas']} copias de consultas confidenciales (usa "
                         "--incluir-confidenciales solo si la otra máquina puede recibirlas)")
        if res["no_exportadas"]:
            lines.append("No exportadas (no válidas): " + "; ".join(f"{k}: {v}" for k, v in res["no_exportadas"].items()))
        lines.append(f"AVISO: {aviso}")
        _emit(args, {**res, "aviso": aviso}, "\n".join(lines))
        return SALIDA_OK
    if args.accion == "importar":
        res = import_cache(client, Path(args.fichero), expected_sha256=args.sha256)
        lines = [f"Importadas {res['importadas']} copias de {res['fichero']} (conservan su fecha original; "
                 f"{res['conservadas_locales']} copias locales iguales o más recientes se mantienen)"]
        if args.sha256 is None:
            lines.append("Nota: sin --sha256 no se comprueba que el fichero sea el que se exportó (la huella que "
                         "mostró `nutria cache exportar`)")
        lines += [f"RECHAZADA entrada {r['indice']}: {r['motivo']}" for r in res["rechazadas"]]
        if res["fechas_declaradas"]:
            lines.append(f"Fechas que declara la exportación (del {res['exportado']}): de "
                         f"{res['fechas_declaradas']['desde']} a {res['fechas_declaradas']['hasta']}")
        lines.append("Nota: una copia importada vale lo que valga su origen: nutria comprueba la huella, la clave y que "
                     "las fechas cuadren, pero no puede comprobar cuándo se obtuvo cada respuesta (una exportación "
                     "fabricada puede declarar fechas recientes). Importa solo exportaciones de origen conocido, con "
                     "--sha256. La importación queda en la procedencia con la huella del fichero, las copias se "
                     "marcan «importada» al servirse y la verificación de referencias nunca usa la caché")
        _emit(args, res, "\n".join(lines))
        return SALIDA_RESULTADO if res["rechazadas"] else SALIDA_OK
    res = inspect_cache(client)
    lines = [f"Copias válidas: {res['validas']}"
             + (f" (de {res['mas_antigua']} a {res['mas_reciente']}; {res['confidenciales']} de consultas "
                "confidenciales)" if res["validas"] else "")]
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
    ("ev_ctrl", "ev_ctrl"), ("hito_referencia", "time_reference"), ("ventana_inicio_dias", "window_start_days"),
    ("ventana_fin_dias", "window_end_days"), ("definicion_caso", "case_definition"),
    ("suplementacion_inicio_dias", "supp_start_days"), ("suplementacion_fin_dias", "supp_end_days"),
    ("forraje_pct_ms", "forage_pct_dm"), ("fnd_pct_ms", "ndf_pct_dm"),
    ("almidon_pct_ms", "starch_pct_dm"), ("grasa_pct_ms", "fat_pct_dm"), ("pb_pct_ms", "cp_pct_dm"),
    ("pdr_pct_pb", "rdp_pct_cp"), ("pdr_pct_ms", "rdp_pct_dm"),
    ("dcad_meq_kg_ms", "dcad_meq_kg_dm"), ("financiacion", "funding"), ("estado_verificacion", "check_status"),
    ("id_doble_extraccion", "comparison_id"),
]
# Moderadores numéricos de la dieta basal: columna del CSV de análisis -> columna del registro.
NUMERIC_MODERATORS = {"forraje_pct_ms": "forage_pct_dm", "fnd_pct_ms": "ndf_pct_dm", "almidon_pct_ms": "starch_pct_dm",
                      "grasa_pct_ms": "fat_pct_dm", "pb_pct_ms": "cp_pct_dm", "pdr_pct_pb": "rdp_pct_cp",
                      "pdr_pct_ms": "rdp_pct_dm", "dcad_meq_kg_ms": "dcad_meq_kg_dm"}
COMPARE_IGNORED = registry.COMPARE_IGNORED
CONCILIATED = registry.DOUBLE_CHECKED


def _coincide(valor, consulta: str) -> bool:
    """Todas las palabras de la consulta aparecen como palabras completas, sin distinguir mayúsculas ni tildes
    ('ovino' no coincide con 'bovino')."""
    buscadas, presentes = registry.text_words(consulta), set(registry.text_words(valor))
    return bool(buscadas) and all(p in presentes for p in buscadas)


def _distintos(rows: list[dict], col: str) -> dict[str, dict]:
    vistos: dict[str, dict] = {}
    for r in rows:
        clave = " ".join(registry.text_words(r.get(col))) or "(sin dato)"
        vistos.setdefault(clave, {"valor": r.get(col) or "(sin dato)", "n": 0})["n"] += 1
    return vistos


def _moderadores_ausentes(row: dict) -> list[str]:
    return [name for name, col in NUMERIC_MODERATORS.items() if row.get(col) is None]


def _estado_efectivo(row: dict, verificadas: set[int], comparaciones: set[str]) -> str:
    """Estado de doble extracción que respalda el registro: un 'doble_concordante' o 'resuelta' que no salió de una
    comparación registrada (o cuyos valores ya no son los de su comparación) no se presenta como tal."""
    status = row["check_status"]
    if status not in CONCILIATED or row["id"] in verificadas:
        return status
    if row.get("comparison_id") in comparaciones:
        return f"{status}_no_coincide_con_su_comparacion"
    return f"{status}_sin_comparacion_registrada"


def _estado_doble(row: dict, verificadas: set[int], comparaciones: set[str] = frozenset()) -> str:
    status = row["check_status"]
    if status in CONCILIATED and row["id"] not in verificadas:
        if row.get("comparison_id") in comparaciones:
            return f"{status} declarado, pero la fila NO coincide con su comparación {row['comparison_id']} (no cuenta)"
        return f"{status} declarado SIN comparación registrada (no cuenta)"
    return f"{status} ({row['comparison_id']})" if row.get("comparison_id") else status


def _avisos_incidencia(rows: list[dict]) -> tuple[list[str], dict]:
    """Avisa si una variable mezcla ventanas de observación (respecto al hito) o, en las incidencias (con eventos o
    expresadas como proporción), si mezcla definiciones de caso o le faltan la definición o la ventana."""
    avisos: list[str] = []
    detalle: dict[str, dict] = {}
    por_variable: dict[str, list[dict]] = {}
    for r in rows:
        por_variable.setdefault(r["outcome"], []).append(r)
    for variable, todas in por_variable.items():
        filas = [r for r in todas if registry.is_incidence(lambda k, r=r: r.get(registry.EXTRACTION_FIELDS[k]))]
        ventanas = sorted({(" ".join(registry.text_words(r.get("time_reference"))), float(r["window_start_days"]),
                            float(r["window_end_days"])) for r in todas
                           if r.get("window_start_days") is not None and r.get("window_end_days") is not None})
        if len(ventanas) > 1:
            avisos.append(f"AVISO: '{variable}' mezcla {len(ventanas)} ventanas de observación: "
                          + "; ".join(f"{h or 'hito sin indicar'} {a:g} a {b:g} d" for h, a, b in ventanas[:6]))
        if not filas:
            continue
        sin = [f"E{r['id']}" for r in filas if not r.get("case_definition") or r.get("window_start_days") is None
               or r.get("window_end_days") is None]
        definiciones = sorted({" ".join(str(r["case_definition"]).casefold().split()) for r in filas
                               if r.get("case_definition")})
        sin_umbral = sorted({d for d in definiciones if not any(ch.isdigit() for ch in d)})
        detalle[variable] = {"definiciones": definiciones, "ventanas": [list(v) for v in ventanas],
                             "sin_definicion_o_ventana": sin, "definiciones_sin_umbral_numerico": sin_umbral}
        if sin:
            avisos.append(f"AVISO: '{variable}': {len(sin)} registro(s) de incidencia sin definición de caso o sin "
                          f"ventana respecto al hito ({', '.join(sin[:10])})")
        if sin_umbral:
            avisos.append(f"AVISO: '{variable}': definición de caso sin umbral numérico ({' | '.join(sin_umbral[:5])}); "
                          "compruébese que es un criterio clínico y no un umbral omitido")
        if len(definiciones) > 1:
            avisos.append(f"AVISO: '{variable}' mezcla {len(definiciones)} definiciones de caso: "
                          + " | ".join(definiciones[:5]))
    return avisos, detalle


def _texto_discrepancias(diffs: list[dict]) -> str:
    return "\n".join(f"registro {d['registro']} | {d['campo']}: A={d['a']!r}  B={d['b']!r}" for d in diffs)


COMPARAR_SOLO_REGISTRADA = (
    "Las copias de una doble extracción solo se comparan con --registrar, en el proyecto: cada extractor deposita la "
    "suya (`nutria --agente <id> extraccion depositar --datos <fichero>`) y quien concilia ejecuta `nutria --agente "
    "<id> extraccion comparar A.json B.json --registrar`, que registra la comparación (sha256 de las dos copias, "
    "extractores y discrepancias) y muestra las discrepancias. nutria no las muestra sin registrarlas, ni dentro ni "
    "fuera de un proyecto: verlas antes permitiría igualar las copias (o sustituirlas por otras de extractores nuevos) "
    "y obtener 'doble_concordante' sin conciliación ni rastro.")


def _comparar_extracciones(args, cfg) -> int:
    if not args.registrar:
        # Ningún par de ficheros (dos borradores, una copia depositada reformateada o por la entrada estándar frente a
        # un borrador, dos copias depositadas), ni desde otro directorio fuera del proyecto: toda comparación de copias
        # deja rastro en el registro. En el proyecto, el intento queda en la procedencia; fuera no se escribe nada.
        if cfg.db_path.is_file():
            log_event(cfg, "extraccion_comparar_rechazada", inputs={"a": args.a, "b": args.b},
                      outputs={"motivo": "sin --registrar"}, actor=args.agente)
        raise registry.RegistryError(COMPARAR_SOLO_REGISTRADA)
    if "-" in (args.a, args.b):
        raise SystemExit("--registrar necesita las rutas de los dos ficheros: su sha256 identifica cada copia depositada")
    path_a, path_b = Path(args.a), Path(args.b)
    sha_a, sha_b = sha256_file(path_a), sha256_file(path_b)
    if path_a.resolve() == path_b.resolve() or path_a.samefile(path_b):
        raise SystemExit("A y B son el mismo fichero: la doble extracción compara dos copias independientes")
    conn = _conn(cfg)
    res = registry.register_extraction_comparison(conn, sha_a, sha_b, args.agente, file_a=args.a, file_b=args.b)
    diffs = res["discrepancias"]
    log_event(cfg, "extraccion_comparar", inputs={"a": args.a, "b": args.b, "sha256_a": sha_a, "sha256_b": sha_b,
                                                  "registrar": True},
              outputs={"comparacion": res["id"], "resultado": res["resultado"], "discrepancias": len(diffs),
                       "ids": res["ids"]}, actor=args.agente)
    quien = f"A: {res['extractor_a']}, B: {res['extractor_b']}"
    if diffs:
        text = (_texto_discrepancias(diffs) + f"\nComparación {res['id']} registrada con {len(diffs)} discrepancia(s) "
                f"({quien}); no se registra ninguna extracción. Resuelve cada discrepancia releyendo la fuente y "
                f"registra la conciliación: nutria extraccion conciliar {res['id']} --datos <conciliacion.json> "
                "(formato en ejemplos/conciliacion_ejemplo.json).")
    else:
        text = (f"Comparación {res['id']} registrada: copias concordantes ({quien}). Extracciones registradas como "
                f"'doble_concordante': {res['ids']}")
    text += "".join(f"\nAVISO: {w}" for w in res.get("avisos") or [])
    _emit(args, res, text)
    return 1 if diffs else 0


def _leer_fichero_json(path: str) -> tuple[object, str]:
    """Lee un JSON de un fichero (no de la entrada estándar) y devuelve también el sha256 de sus bytes."""
    if path == "-":
        raise SystemExit("Esta orden necesita la ruta de un fichero: su sha256 queda en el registro")
    raw = Path(path).read_bytes()
    return json.loads(raw.decode("utf-8")), sha256_text(raw)


def _depositar_copia(args, cfg, conn) -> int:
    data, sha = _leer_fichero_json(args.json_file)
    records = data if isinstance(data, list) else [data]
    for i, item in enumerate(records, 1):
        try:
            _validate("extraccion", item)
        except SystemExit as exc:
            raise SystemExit(f"Registro {i} de {args.json_file}: {exc}") from None
    res = registry.deposit_extraction_copy(conn, records, sha, args.json_file, args.agente)
    log_event(cfg, "extraccion_depositar", inputs={"archivo": args.json_file, "sha256": sha}, outputs=res,
              actor=args.agente)
    _emit(args, res, f"Copia {'depositada' if res['nueva'] else 'ya depositada'}: #{res['id']} ({res['registros']} "
                     f"registro(s), sha256 {sha[:12]}…, extractor {res['extractor']}). No entra en el análisis hasta "
                     "compararla con la otra copia: nutria extraccion comparar A.json B.json --registrar")
    return 0


def _conciliar_extracciones(args, cfg, conn) -> int:
    data, sha = _leer_fichero_json(args.json_file)
    _validate("conciliacion", data)
    res = registry.conciliate_extraction_comparison(conn, args.id, data, sha, args.agente)
    log_event(cfg, "extraccion_conciliar", inputs={"comparacion": args.id, "archivo": args.json_file, "sha256": sha},
              outputs={"ids": res["ids"], "no_resueltos": len(res["no_resueltos"])}, actor=args.agente)
    estados = ", ".join(f"#{d['id']} {d['estado']}" for d in res["registros"]) or "ninguna"
    _emit(args, res, f"Conciliación de {args.id} registrada. Extracciones: {estados}. No resueltos (sin registrar): "
                     f"{len(res['no_resueltos'])}" + "".join(f"\nAVISO: {w}" for w in res.get("avisos") or []))
    return 0


def _exportar_extracciones(args, rows: list[dict], verificadas: set[int], fuera_de_filtro: list[dict],
                           comparaciones: set[str] = frozenset()) -> int:
    if not rows:
        raise SystemExit("No hay extracciones con esos filtros"
                         + (f" (--especie/--etapa dejan fuera {len(fuera_de_filtro)}: especies "
                            + ", ".join(_distintos(fuera_de_filtro, "species")) + ")" if fuera_de_filtro else ""))
    excluded: dict[str, int] = {}
    if args.formato == "analisis" and not args.incluir_sin_conciliar:
        # Doble extracción (regla del proyecto): al análisis solo pasan los registros cuyo estado respalda una
        # comparación (y, si hubo discrepancias, una conciliación) registradas por el sistema.
        kept = []
        for r in rows:
            if r["id"] in verificadas:
                kept.append(r)
                continue
            motivo = _estado_efectivo(r, verificadas, comparaciones)
            excluded[motivo] = excluded.get(motivo, 0) + 1
        rows = kept
        if not rows:
            raise SystemExit("Ninguna extracción con doble extracción verificada (comparación registrada con "
                             f"`extraccion comparar A B --registrar` o conciliación registrada) con esos filtros "
                             f"(excluidas: {excluded}). Solo para una exploración, usa --incluir-sin-conciliar.")
    sin_casos: dict[str, dict] = {}
    if args.casos_completos:
        desconocidos = [m for m in args.casos_completos if m not in NUMERIC_MODERATORS]
        if desconocidos:
            raise SystemExit(f"--casos-completos: moderadores desconocidos ({', '.join(desconocidos)}); válidos: "
                             + ", ".join(NUMERIC_MODERATORS))
        kept = []
        for r in rows:
            faltan = [m for m in args.casos_completos if r.get(NUMERIC_MODERATORS[m]) is None]
            if faltan:
                sin_casos[f"E{r['id']}"] = {"estudio": r["source_id"], "faltan": faltan}
            else:
                kept.append(r)
        rows = kept
        if not rows:
            raise SystemExit("Ningún registro informa de todos los moderadores pedidos en --casos-completos")
    avisos: list[str] = []
    especies = _distintos(rows, "species")
    if len(especies) > 1:
        lista = ", ".join(f"{v['valor']} ({v['n']})" for v in especies.values())
        if args.formato == "registro":
            # el volcado del registro no es un análisis: lista todas las especies y solo avisa
            avisos.append(f"AVISO: el volcado incluye especies distintas ({lista}); para el análisis, exporta con "
                          "--especie")
        elif not args.mezclar_especies:
            raise SystemExit(f"El resultado mezcla especies: {lista}. Filtra con --especie (y --etapa) o, si combinarlas "
                             "está justificado (transferibilidad) o son la misma especie escrita de otra forma, repite "
                             "con --mezclar-especies (el aviso las listará).")
        else:
            avisos.append(f"AVISO: se combinan especies distintas ({lista}): justifica la transferibilidad y analiza "
                          "la especie como subgrupo (--subgrupo especie)")
    estados = _distintos(rows, "physiological_stage")
    if len(estados) > 1:
        avisos.append("AVISO: se combinan estados fisiológicos distintos ("
                      + ", ".join(f"{v['valor']} ({v['n']})" for v in estados.values()) + ")")
    if fuera_de_filtro:
        avisos.append(f"AVISO: --especie/--etapa dejan fuera {len(fuera_de_filtro)} registro(s) de esas variables "
                      "(especies: " + ", ".join(f"{v['valor']} ({v['n']})" for v in
                                                _distintos(fuera_de_filtro, "species").values())
                      + "; estados: " + ", ".join(f"{v['valor']} ({v['n']})" for v in
                                                  _distintos(fuera_de_filtro, "physiological_stage").values())
                      + "): compruébese que no son la misma especie o etapa escrita de otra forma")
    por_estudio: dict[tuple, list[str]] = {}
    for r in rows:
        por_estudio.setdefault((r["source_id"], r["outcome"]), []).append(f"E{r['id']}")
    repetidos = {k: v for k, v in por_estudio.items() if len(v) > 1}
    if repetidos:
        avisos.append(f"AVISO: {len(repetidos)} estudio(s) aportan varias comparaciones de la misma variable ("
                      + "; ".join(f"{s} {o}: {', '.join(ids)}" for (s, o), ids in list(repetidos.items())[:6])
                      + "): son dependientes (en meta analizar, --agregar-estudio) y no deben ser el mismo dato "
                      "extraído dos veces")
    ausentes: dict[str, set[str]] = {}
    for r in rows:
        faltan = _moderadores_ausentes(r)
        if faltan:
            ausentes.setdefault(r["source_id"], set()).update(faltan)
    if ausentes:
        avisos.append(f"AVISO: {len(ausentes)} estudio(s) no informan de algún moderador numérico (columna "
                      "moderadores_no_informados; para una meta-regresión de casos completos usa --casos-completos): "
                      + "; ".join(f"{k}: {', '.join(sorted(v))}" for k, v in list(ausentes.items())[:8])
                      + (" ..." if len(ausentes) > 8 else ""))
    if sin_casos:
        avisos.append(f"Excluidos por --casos-completos ({len(sin_casos)}): "
                      + "; ".join(f"{k} {v['estudio']} sin {', '.join(v['faltan'])}" for k, v in sin_casos.items()))
    avisos_inc, incidencia = _avisos_incidencia(rows)
    avisos += avisos_inc
    if excluded:
        avisos.append("Excluidas por no tener doble extracción verificada: "
                      + ", ".join(f"{k}={v}" for k, v in sorted(excluded.items()))
                      + " (usa --incluir-sin-conciliar solo para una exploración)")
    elif args.formato == "analisis" and args.incluir_sin_conciliar:
        pending = sum(1 for r in rows if r["id"] not in verificadas)
        if pending:
            avisos.append(f"AVISO: {pending} fila(s) sin doble extracción verificada incluidas: el análisis es solo "
                          "exploratorio")
    if args.formato == "registro":
        fieldnames = list(rows[0].keys()) + ["doble_extraccion_verificada"]
        out_rows = [{**r, "doble_extraccion_verificada": "si" if r["id"] in verificadas else "no"} for r in rows]
    else:
        fieldnames = (["estudio", "comparacion"] + [name for name, _col in ANALYSIS_COLUMNS[1:]]
                      + ["doble_extraccion_verificada", "moderadores_no_informados"])
        out_rows = [{"estudio": r["source_id"], "comparacion": f"E{r['id']}",
                     **{name: r.get(col) for name, col in ANALYSIS_COLUMNS[1:]},
                     # el estado efectivo: un 'doble_concordante' sin comparación registrada no se presenta como tal
                     "estado_verificacion": _estado_efectivo(r, verificadas, comparaciones),
                     "doble_extraccion_verificada": "si" if r["id"] in verificadas else "no",
                     "moderadores_no_informados": ";".join(_moderadores_ausentes(r))} for r in rows]
    with open(args.csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(out_rows)
    _emit(args, {"filas": len(rows), "csv": args.csv, "formato": args.formato, "excluidas": excluded,
                 "especies": {v["valor"]: v["n"] for v in especies.values()},
                 "moderadores_no_informados": {k: sorted(v) for k, v in ausentes.items()},
                 "excluidas_por_casos_completos": sin_casos, "fuera_de_filtro": [f"E{r['id']}" for r in fuera_de_filtro],
                 "incidencia": incidencia, "avisos": avisos},
          f"Exportadas {len(rows)} filas a {args.csv} (formato {args.formato})" + "".join(f"\n{a}" for a in avisos))
    return 0


def cmd_extraccion(args, cfg) -> int:
    if args.accion == "comparar":
        return _comparar_extracciones(args, cfg)
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
    if args.accion == "depositar":
        return _depositar_copia(args, cfg, conn)
    if args.accion == "conciliar":
        return _conciliar_extracciones(args, cfg, conn)
    if args.accion == "comparaciones":
        rows = registry.list_extraction_comparisons(conn, pending_only=args.pendientes)
        _emit(args, rows, "\n".join(
            f"{r['id']} {r['result']}{' (conciliada)' if r.get('conciliation') else ''} | A: {r['file_a']} "
            f"({r['actor_a']}) | B: {r['file_b']} ({r['actor_b']}) | {len(r['discrepancies'] or [])} discrepancia(s)"
            for r in rows) or "(sin comparaciones)")
        return 0
    rows = todas = registry.list_extractions(conn, source_id=args.fuente, outcome=args.variable)
    if args.especie:
        rows = [r for r in rows if _coincide(r.get("species"), args.especie)]
    if args.etapa:
        rows = [r for r in rows if _coincide(r.get("physiological_stage"), args.etapa)]
    verificadas = registry.verified_extraction_ids(conn)
    comparaciones = {c["id"] for c in registry.list_extraction_comparisons(conn)}
    if args.accion == "exportar":
        dentro = {r["id"] for r in rows}
        return _exportar_extracciones(args, rows, verificadas, [r for r in todas if r["id"] not in dentro],
                                      comparaciones)
    _emit(args, rows, "\n".join(f"#{r['id']} {r['source_id']} | {r['species']} | {r['intervention']} | {r['outcome']} "
                                f"({r['outcome_unit']}) | {_estado_doble(r, verificadas, comparaciones)}" for r in rows)
          or "(sin extracciones)")
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
    _emit(args, rows, "\n".join(f"{r['id']} [{r['label']}] {r['text']} {r['source_ids'] or ''}"
                                + (f" | {r['context'].get('especie')}" if isinstance(r.get("context"), dict) else "")
                                for r in rows)
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
        _emit(args, _sin_conclusion(args, rows), "\n".join(f"{r['id']} [{r['status']}] {r['title']}" for r in rows)
              or "(vacío)")
    elif args.accion == "ver":
        row = get(conn, args.id)
        if not row:
            raise SystemExit(f"No existe {args.id}")
        if not _es_evaluador(args):  # regla 9: un evaluador no ve las decisiones propuestas (la conclusión esperada)
            row["decisiones"] = registry.list_decisions(conn, args.id)
        if kind == "oportunidad":
            row["compuertas"] = registry.list_gates(conn, args.id)
            row["compuertas_efectivas"] = registry.effective_gates(conn, args.id, _rubric(cfg).gate_ids)
            if not getattr(args, "sin_puntuaciones", False) and not _es_evaluador(args):
                row["puntuaciones"] = registry.list_scores(conn, args.id)
            row["hipotesis"] = registry.list_hypotheses(conn, opportunity_id=args.id)
        _emit(args, _sin_conclusion(args, row))
    else:
        gate_ids = _rubric(cfg).gate_ids if kind == "oportunidad" else None
        set_status(conn, args.id, args.nuevo_estado, args.motivo, args.agente, args.firma_humana, gate_ids)
        log_event(cfg, "cambio_estado", inputs={"entidad": args.id, "estado": args.nuevo_estado, "motivo": args.motivo},
                  outputs={"firmada": bool(args.firma_humana), "firma": args.firma_humana}, actor=args.agente)
        _emit(args, {"id": args.id, "estado": args.nuevo_estado}, f"{args.id} -> {args.nuevo_estado}")
    return 0


def cmd_oportunidad(args, cfg) -> int:
    if args.accion == "clasificar":
        return _clasificar_oportunidad(args, cfg)
    return _entity_cmd(args, cfg, "oportunidad")


def cmd_hipotesis(args, cfg) -> int:
    return _entity_cmd(args, cfg, "hipotesis")


def _opciones_esquema(kind: str, campo: str) -> list[str]:
    return list(registry.load_schema(kind)["properties"][campo]["enum"])


def _clasificar_oportunidad(args, cfg) -> int:
    """`oportunidad clasificar`: registra la clasificación regulatoria que resulta del triage (ruta, grupo funcional y
    situación), con decisión y actor. Una ruta fuera_de_alcance_* bloquea la compuerta regulatoria."""
    _no_para_evaluadores(args, "clasificar oportunidades")
    conn = _conn(cfg)
    res = registry.classify_opportunity(conn, args.id, args.ruta, args.motivo, functional_group=args.grupo_funcional,
                                        situation=args.situacion, actor=args.agente, human_signoff=args.firma_humana)
    log_event(cfg, "oportunidad_clasificar", inputs={"id": args.id, "ruta": args.ruta, "grupo_funcional":
                                                     args.grupo_funcional, "situacion": args.situacion},
              outputs=res, actor=args.agente)
    previa, nueva = res["anterior"], res["nueva"]
    text = (f"{args.id}: clasificación {previa['ruta'] or 'sin ruta'} -> {nueva['ruta']}"
            + (f" | grupo funcional {nueva['grupo_funcional']}" if nueva["grupo_funcional"] else "")
            + (f" | situación {nueva['situacion']}" if nueva["situacion"] else "")
            + f" | compuerta regulatoria: {res['compuerta_regulatoria']}"
            + (f" (cambio automático: {res['cambio_compuerta_regulatoria']})" if res["cambio_compuerta_regulatoria"]
               else ""))
    _emit(args, res, text)
    return 0


def cmd_puntuar(args, cfg) -> int:
    from .scoring.engine import format_table, rank, validate_anchors
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
        data["puntuaciones"] = validate_anchors(rubric, data["puntuaciones"],
                                                warn=lambda m: _imprimir(sys.stderr, m))
        quien = _evaluador_que_registra(args)
        n = registry.add_scores(conn, data["oportunidad"], data["evaluador"], data["puntuaciones"],
                                rubric.criterion_ids, actor=quien)
        # Los valores quedan en la base de datos (scores y scores_history), no en la procedencia, que es legible por
        # otros agentes: así un evaluador no puede ver las puntuaciones de otro consultando las trazas.
        log_event(cfg, "puntuacion", inputs={"oportunidad": data["oportunidad"], "evaluador": data["evaluador"]},
                  outputs={"criterios": n, "sha256_puntuaciones": sha256_text(json.dumps(data["puntuaciones"],
                                                                                         sort_keys=True))},
                  actor=quien)
        _emit(args, {"oportunidad": data["oportunidad"], "evaluador": data["evaluador"], "criterios": n},
              f"{n} puntuaciones registradas para {data['oportunidad']} (evaluador {data['evaluador']})")
        return 0
    if args.accion == "compuerta":
        _comprobar_evidencia(cfg, args.estado, args.evidencia, propia=args.id)
        registry.set_gate(conn, args.id, args.compuerta, args.estado, args.motivo, rubric.gate_ids,
                          evidence=args.evidencia, actor=args.agente, human_signoff=args.firma_humana)
        efectiva = registry.effective_gates(conn, args.id, rubric.gate_ids).get(args.compuerta)
        log_event(cfg, "compuerta", inputs={"oportunidad": args.id, "compuerta": args.compuerta, "estado": args.estado,
                                            "motivo": args.motivo, "evidencia": args.evidencia},
                  outputs={"firmada": bool(args.firma_humana), "firma": args.firma_humana, "efectiva": efectiva},
                  actor=args.agente)
        note = ""
        if args.estado == "superada" and not args.firma_humana:
            note = " (propuesta: no cuenta como superada hasta que la firme una persona)"
        _emit(args, {"id": args.id, "compuerta": args.compuerta, "estado": args.estado,
                     "firmada": bool(args.firma_humana)}, f"{args.id}: compuerta {args.compuerta} -> {args.estado}{note}")
        return 0
    # ranking
    from .scoring.engine import ranking_warnings
    _no_para_evaluadores(args, "consultar el ranking")
    if args.min_evaluadores < 3:
        raise ValueError(f"--min-evaluadores {args.min_evaluadores}: el ranking exige al menos 3 evaluadores "
                         "independientes por criterio (regla 9); con menos, la oportunidad queda INSUFICIENTE")
    if args.top < 1 or args.sim < 1 or not (args.alfa >= 0 and args.alfa != float("inf")):
        raise ValueError("--top y --sim deben ser >= 1 y --alfa un número finito >= 0 (0 = pesos fijos)")
    profile = args.perfil or cfg.scoring_profile
    opps = []
    for o in registry.list_opportunities(conn):
        if o["status"] == "descartada":
            continue
        # Cada evaluador cuenta por su identidad normalizada (`normalize_rater`, y comparada sin signos con `actor_key`,
        # como las identidades de los extractores): las puntuaciones registradas antes de normalizarla con variantes
        # del mismo nombre (mayúsculas, espacios, guiones o guiones bajos, caracteres invisibles) son de un solo
        # evaluador, y de cada criterio vale la última que registró.
        por_evaluador: dict[tuple[str, str], dict] = {}
        for s in sorted(registry.list_scores(conn, o["id"]), key=lambda r: (r.get("created_at") or "", r["id"])):
            quien = registry.normalize_rater(s["rater"])
            clave = registry.actor_key(quien) or quien
            por_evaluador[(clave, s["criterion"])] = {"rater": quien, "criterion": s["criterion"], "value": s["value"],
                                                      "low": s["low"], "high": s["high"]}
        scores = sorted(por_evaluador.values(), key=lambda r: (r["criterion"], r["rater"]))
        gates = registry.effective_gates(conn, o["id"], rubric.gate_ids)
        # Estado registrado (propuesta o decisión) de cada compuerta: distingue "pendiente de firma" de "sin resolver".
        proposals = {g["gate"]: g["status"] for g in registry.list_gates(conn, o["id"])}
        opps.append({"id": o["id"], "title": o["title"], "scores": scores, "gates": gates,
                     "gate_proposals": proposals, "status": o["status"]})
    rows = rank(opps, rubric, profile=profile, n_sim=args.sim, seed=args.semilla, alpha=args.alfa, top_k=args.top,
                min_raters=args.min_evaluadores)
    warnings = ranking_warnings(rows, args.top, profile)
    # Procedencia reproducible (D9-7): parámetros, huellas de la rúbrica, de las puntuaciones y de toda la entrada, y
    # el orden resultante. Las puntuaciones no se copian (los evaluadores no deben verlas en las trazas).
    def canonical(x) -> str:
        return json.dumps(x, sort_keys=True, ensure_ascii=False, default=str)

    log_event(cfg, "ranking", inputs={
        "perfil": profile, "sim": args.sim, "semilla": args.semilla, "alfa": args.alfa, "top": args.top,
        "min_evaluadores": args.min_evaluadores, "rubrica_version": rubric.version,
        "sha256_rubrica": rubric.fingerprint(), "n_oportunidades": len(opps),
        "n_puntuaciones": sum(len(o["scores"]) for o in opps),
        "sha256_puntuaciones": sha256_text(canonical([[o["id"], o["scores"]] for o in opps])),
        "sha256_entrada": sha256_text(canonical(opps))},
        outputs={"orden": [[r.opportunity_id, r.status, r.gate_tier] for r in rows], "avisos": warnings,
                 "sha256_resultado": sha256_text(canonical([r.to_dict() for r in rows]))}, actor=args.agente)
    if args.json and warnings:
        _imprimir(sys.stderr, "\n".join(warnings))
    _emit(args, [r.to_dict() for r in rows],
          f"Perfil: {profile} | {args.sim} simulaciones | semilla {args.semilla} | alfa {args.alfa} | "
          f"mínimo {args.min_evaluadores} evaluadores\n" + format_table(rows, args.top, profile))
    return 0


def cmd_decision(args, cfg) -> int:
    conn = _conn(cfg)
    if args.accion == "registrar":
        registry.add_decision(conn, args.entidad, args.decision, args.motivo, args.agente, args.firma_humana)
        log_event(cfg, "decision", inputs={"entidad": args.entidad, "decision": args.decision, "motivo": args.motivo},
                  outputs={"firmada": bool(args.firma_humana), "firma": args.firma_humana}, actor=args.agente)
        _emit(args, {"entidad": args.entidad, "decision": args.decision, "firmada": bool(args.firma_humana)},
              "Decisión registrada" + ("" if args.firma_humana else " (sin firma humana: es una propuesta)"))
        return 0
    _no_para_evaluadores(args, "consultar las decisiones")  # regla 9: las propuestas son la conclusión esperada
    rows = registry.list_decisions(conn, args.entidad)
    _emit(args, rows, "\n".join(f"{r['ts']} {r['entity_id'] or '-':<8} {r['decision']} | {r['rationale']} | "
                                f"{r['decided_by']}{' [' + r['context'] + ']' if r.get('context') else ''}"
                                f"{' | firma: ' + r['human_signoff'] if r['human_signoff'] else ''}"
                                for r in rows) or "(sin decisiones)")
    return 0


def _evaluador_que_registra(args) -> str:
    """Regla 9: solo puntúa un evaluador independiente (--agente evaluador-<n> o NUTRIA_AGENT), y solo con su propio
    identificador, el del campo 'evaluador' del JSON (lo comprueba `registry.add_scores`)."""
    from .provenance import declared_agent
    quien = declared_agent(getattr(args, "agente", None))
    if not registry.normalize_rater(quien).startswith("evaluador"):
        raise registry.RegistryError("Solo los evaluadores independientes registran puntuaciones (regla 9): "
                                     "identifícate con --agente evaluador-<n>, el mismo identificador del campo "
                                     "'evaluador' del JSON")
    return quien


def _comprobar_evidencia(cfg, estado: str, evidencia: list | None, propia: str | None = None) -> None:
    """La evidencia de una compuerta debe poder comprobarse: IDs del registro (su existencia la comprueba el
    registro; la propia oportunidad, `propia`, no vale), URL o ficheros regulares dentro del proyecto (no
    directorios ni rutas de fuera); el texto libre no vale. Tampoco valen el propio registro (la base de datos y la
    procedencia) ni los JSON de la propia oportunidad en research/oportunidades/: no prueban nada sobre ella. En
    "bloqueada" no se exige nada: un error en la evidencia no debe impedir un bloqueo (criterio de prudencia)."""
    import re
    if estado == "bloqueada":
        return
    raiz = Path(cfg.root).resolve()
    base_datos = Path(cfg.db_path) if Path(cfg.db_path).is_absolute() else raiz / cfg.db_path
    registro = [base_datos, *(Path(f"{base_datos}{sufijo}") for sufijo in ("-wal", "-shm", "-journal")),
                Path(cfg.provenance_path)]
    oportunidades = raiz / "research" / "oportunidades"
    faltan = []
    for item in (str(e).strip() for e in evidencia or []):
        if propia and item.casefold() == str(propia).strip().casefold():
            raise registry.RegistryError(f"{propia} no es evidencia de su propia compuerta: cita las afirmaciones "
                                         "(A-xxxx), fuentes, URL o ficheros con el análisis")
        if not item or registry.is_registry_id(item) or re.fullmatch(r"https?://\S+", item):
            continue
        path = Path(item).expanduser()
        candidatos = [c for c in ([path] if path.is_absolute() else [raiz / path, Path.cwd() / path])
                      if _fichero_del_proyecto(c, raiz)]
        if not candidatos:
            faltan.append(item)
            continue
        real = candidatos[0].resolve()
        propio = bool(propia) and real.suffix.casefold() == ".json" and _mismo_fichero(real.parent, oportunidades) \
            and real.name.casefold().startswith(str(propia).strip().casefold())
        if propio or any(_mismo_fichero(real, r) for r in registro):
            raise registry.RegistryError(f"{item} no es evidencia de una compuerta: el registro, la procedencia y el JSON "
                                         "de la propia oportunidad no prueban nada sobre ella. Cita el análisis "
                                         "(research/revisiones/...), afirmaciones (A-xxxx), fuentes o URL")
    if faltan:
        raise registry.RegistryError(f"Evidencia que no se puede comprobar: {', '.join(faltan)}. Indica IDs del registro "
                                     "(A-xxxx, doi:..., pmid:...), URL o ficheros regulares que existan dentro del "
                                     "proyecto (rutas relativas a su raíz)")


def _fichero_del_proyecto(path: Path, raiz: Path) -> bool:
    """¿Es `path` un fichero regular dentro de `raiz` (una vez resueltos los enlaces simbólicos)?"""
    try:
        real = path.resolve()
    except (OSError, RuntimeError):
        return False
    return real.is_file() and (real == raiz or raiz in real.parents)


def _mismo_fichero(a: Path, b: Path) -> bool:
    """¿Son el mismo fichero o directorio (también con otras mayúsculas en sistemas que no las distinguen)?"""
    try:
        return a.samefile(b)
    except OSError:
        return False


def cmd_autoria(args, cfg) -> int:
    """Autoría inventiva humana (regla 11): quién aportó qué decisión inventiva, firmado por una persona."""
    conn = _conn(cfg)
    if args.accion == "registrar":
        if not (args.firma_humana or "").strip():
            raise registry.RegistryError("Registrar la autoría inventiva exige --firma-humana: solo una persona puede "
                                         "registrarla, en su propia terminal (regla 11)")
        cid = registry.add_inventive_contribution(conn, args.entidad, args.persona, args.aportacion, args.firma_humana,
                                                  args.agente)
        # La aportación puede describir una idea patentable: en la procedencia (versionada) solo queda su huella.
        log_event(cfg, "autoria", inputs={"entidad": args.entidad, "persona": args.persona},
                  outputs={"id": cid, "firma": args.firma_humana},
                  input_hashes={"aportacion": sha256_text(args.aportacion.strip())}, actor=args.agente)
        _emit(args, {"id": cid, "entidad": args.entidad, "persona": args.persona, "firma": args.firma_humana},
              f"Autoría inventiva registrada (#{cid}): {args.persona} en {args.entidad} (firma: {args.firma_humana})")
        return 0
    rows = registry.list_inventive_contributions(conn, args.entidad)
    _emit(args, rows, "\n".join(f"#{r['id']} {r['signed_at']} {r['entity_id']} | {r['person']}: {r['contribution']} | "
                                f"firma: {r['signed_by']}" for r in rows) or "(sin aportaciones registradas)")
    return 0


# ============================================================================ estadística
def _read_table(path: str, sep: str, decimal: str, huella: dict | None = None) -> list[dict]:
    """Lee un CSV. Con --decimal ',' convierte la coma decimal en punto (no admite separadores de miles).

    El fichero se lee una sola vez; con `huella` (dict) se guarda en huella["sha256"] la huella SHA-256 de
    los bytes leídos (procedencia: identifica exactamente los datos analizados). Una fila con más campos
    que la cabecera (p. ej. comas decimales sin comillas en un CSV separado por comas) es un error: los
    valores quedarían desplazados de columna."""
    import io
    raw = Path(path).read_bytes()
    if huella is not None:
        huella["sha256"] = sha256_text(raw)
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), delimiter=sep))
    # Campos sobrantes vacíos (p. ej. una coma final) se ignoran; con contenido, desplazarían las columnas.
    largas = [i for i, r in enumerate(rows, 2) if any(str(x).strip() for x in (r.get(None) or []))]
    for r in rows:
        r.pop(None, None)
    if largas:
        raise ValueError(f"{path}: la(s) línea(s) {', '.join(map(str, largas[:10]))} tienen más campos que la cabecera: "
                         "revisa --sep y --decimal (¿coma decimal sin comillas en un CSV separado por comas?)")
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


def _effects_from_rows(rows: list[dict], measure: str, rho_intrasujeto: float | None = None,
                       mensajes: list | None = None) -> tuple[list[float], list[float], list[str], list[str]]:
    """(yi, vi, etiquetas, comparaciones) de cada fila.

    La columna opcional `diseno` (paralelo, crossover, cuadrado_latino, factorial, bloques, otro) decide la
    fórmula: en los diseños cruzados se usan las de grupos emparejados con la correlación intrasujeto de la
    columna `rho_intrasujeto` o, si está vacía, `rho_intrasujeto` (--rho-intrasujeto). Un diseño desconocido
    es un error. Con `mensajes` (lista) se añaden tuplas (nivel, texto) que explican el tratamiento de las
    filas de diseño cruzado, agrupadas."""
    from .stats import effect_sizes as es
    yi, vi, labels, comps = [], [], [], []
    grupos: dict[tuple[str, str], list[str]] = {}
    for i, r in enumerate(rows, 1):
        label = r.get("estudio") or r.get("study") or str(i)
        tag = f"fila {i} ({label})"
        try:
            diseno = es.normalize_design(r.get("diseno"))
        except ValueError as exc:
            raise ValueError(f"{tag}: {exc}") from None
        paired = diseno in es.PAIRED_DESIGNS
        nota = None
        if r.get("yi") not in (None, "") and r.get("vi") not in (None, ""):
            y, v = _num(r, "yi", tag), _num(r, "vi", tag)
            nota = es.design_note(measure, None, diseno)
        elif measure in ("RR", "OR", "RD"):
            vals = {k: _num(r, k, tag) for k in ("ev_trt", "n_trt", "ev_ctrl", "n_ctrl")}
            missing = [k for k, val in vals.items() if val is None]
            if missing:
                raise ValueError(f"{tag}: faltan {', '.join(missing)} para la medida {measure}")
            y, v = es.effect_from_counts(measure, vals["ev_trt"], vals["n_trt"], vals["ev_ctrl"], vals["n_ctrl"], label=tag)
            if paired:
                nota = ("aviso", "Diseño cruzado con variable binaria: los recuentos se tratan como grupos "
                                 "independientes (no hay fórmula emparejada implementada para RR, OR o RD).")
        else:
            kind = (r.get("tipo_disp") or "").strip()
            if not kind:
                raise ValueError(f"{tag}: falta 'tipo_disp' (SD, SE, SEM_combinado, SED o IC95); no se asume DE por "
                                 "defecto porque en nutrición animal lo habitual es el SEM combinado")
            vals = {k: _num(r, k, tag) for k in ("media_trt", "media_ctrl", "n_trt", "n_ctrl", "disp_trt", "disp_ctrl")}
            missing = [k for k in ("media_trt", "media_ctrl", "n_trt", "n_ctrl") if vals[k] is None]
            if missing:
                raise ValueError(f"{tag}: faltan {', '.join(missing)}")
            rho = None
            if paired:
                rho = _num(r, "rho_intrasujeto", tag)
                if rho is not None and rho != rho:  # "nan" en la celda cuenta como dato ausente
                    rho = None
                if rho is None and rho_intrasujeto is not None:
                    rho = rho_intrasujeto
                    grupos.setdefault(("aviso", f"Correlación intrasujeto supuesta r = {rho_intrasujeto:g} "
                                                "(--rho-intrasujeto, sin dato en la columna rho_intrasujeto): es un "
                                                "supuesto de trabajo [SUP]; repite el análisis con otros valores "
                                                "(p. ej. 0,3 y 0,8) como sensibilidad."), []).append(label)
            try:
                y, v = es.effect_from_summary(measure, vals["media_trt"], vals["media_ctrl"], vals["n_trt"],
                                              vals["n_ctrl"], kind, vals["disp_trt"], vals["disp_ctrl"], label=tag,
                                              diseno=diseno, rho_intrasujeto=rho)
            except TypeError as exc:
                raise ValueError(f"{tag}: {exc}") from exc
            if paired:
                nota = es.design_note(measure, kind, diseno, rho)
                if vals["n_trt"] != vals["n_ctrl"]:
                    grupos.setdefault(("aviso", "Diseño cruzado con n_trt distinto de n_ctrl: se usa como n el menor "
                                                "(unidades experimentales con ambos tratamientos)."), []).append(label)
        if nota:
            grupos.setdefault(nota, []).append(label)
        yi.append(y)
        vi.append(v)
        labels.append(label)
        comps.append(r.get("comparacion") or f"{label}#{i}")
    if mensajes is not None:
        for (nivel, texto), labs in grupos.items():
            mensajes.append((nivel, f"{texto} Filas: {', '.join(labs[:8])}{' y otras' if len(labs) > 8 else ''}."))
    return yi, vi, labels, comps


def _resumen_disenos(rows: list[dict], measure: str, labels: list[str]) -> tuple[dict, list[tuple[str, str]]]:
    """Recuento de diseños y unidades experimentales y avisos si se mezclan (columnas opcionales `diseno` y
    `unidad_experimental`, las que exporta `nutria extraccion exportar`)."""
    from collections import Counter

    from .stats import effect_sizes as es
    info: dict = {}
    mensajes: list[tuple[str, str]] = []
    columnas = set().union(*(r.keys() for r in rows))
    desde_medias = measure in ("MD", "SMD", "ROM") and any(r.get("yi") in (None, "") for r in rows)
    if "diseno" in columnas:
        disenos = [es.normalize_design(r.get("diseno")) for r in rows]
        cuenta = Counter(d or "sin_declarar" for d in disenos)
        info["disenos"] = dict(cuenta)
        declarados = {d for d in disenos if d}
        sin = [lab for lab, d in zip(labels, disenos) if not d]
        if len(declarados) > 1:
            cruzados = declarados & set(es.PAIRED_DESIGNS)
            mensajes.append(("aviso", "Se combinan diseños distintos (" + "; ".join(f"{k}: {v}" for k, v in cuenta.items())
                             + "). " + ("Los crossovers y cuadrados latinos estiman el efecto dentro de animal "
                                        "(otra varianza y, en SMD, otra escala si no se corrige). " if cruzados else "")
                             + "Comprueba que los efectos son comparables y modela el diseño como moderador "
                               "(--moderador diseno) o analiza por separado (--subgrupo diseno)."))
        if sin and declarados:
            mensajes.append(("aviso", f"{len(sin)} fila(s) sin diseño declarado se tratan como diseños paralelos: "
                             + ", ".join(sin[:8]) + (" y otras" if len(sin) > 8 else "") + "."))
        elif not declarados:
            mensajes.append(("nota", "La columna diseno está vacía: todas las filas se tratan como diseños paralelos."))
        otros = [lab for lab, d in zip(labels, disenos) if d == "otro"]
        if otros:
            mensajes.append(("aviso", f"{len(otros)} fila(s) con diseño 'otro' se tratan como grupos independientes "
                             "(diseño paralelo): comprueba que lo son o calcula yi y vi con la fórmula de su diseño. "
                             "Filas: " + ", ".join(otros[:8]) + (" y otras" if len(otros) > 8 else "") + "."))
    elif desde_medias:
        mensajes.append(("nota", "El CSV no tiene columna diseno: todas las filas se tratan como diseños paralelos. Si "
                                 "hay crossovers o cuadrados latinos, decláralos (columna diseno) para calcular su "
                                 "efecto con la fórmula de grupos emparejados."))
    if "unidad_experimental" in columnas:
        unidades = [es.normalize_unit(r.get("unidad_experimental")) for r in rows]
        cuenta = Counter(u or "sin_declarar" for u in unidades)
        info["unidades_experimentales"] = dict(cuenta)
        declaradas = {u for u in unidades if u}
        if len(declaradas) > 1:
            mensajes.append(("aviso", "Se combinan unidades experimentales distintas ("
                             + "; ".join(f"{k}: {v}" for k, v in cuenta.items())
                             + "): la variabilidad entre corrales (o granjas) y entre animales no es la misma y los n "
                               "cuentan cosas distintas. Modela la unidad como moderador (--moderador "
                               "unidad_experimental) o analiza por separado (--subgrupo unidad_experimental)."))
        desconocidas = sorted(u for u in declaradas if u not in es.UNITS)
        if desconocidas:
            mensajes.append(("aviso", f"Unidad experimental no reconocida: {', '.join(desconocidas)} (admitidas: "
                                      f"{', '.join(es.UNITS)})."))
        no_animal = sorted(u for u in declaradas if u != "animal")
        if no_animal:
            mensajes.append(("nota", f"Con unidad experimental {', '.join(no_animal)}, n_trt y n_ctrl deben contar "
                                     "unidades experimentales (p. ej. corrales), no animales: si cuentan animales, la "
                                     "varianza queda subestimada."))
    return info, mensajes


def _avisos_mezcla_stpierre(rows: list[dict]) -> list[str]:
    """Avisos de `meta stpierre` si el CSV declara diseños o unidades experimentales distintos."""
    from .stats import effect_sizes as es

    def seguro(norm, value):
        try:
            return norm(value)
        except ValueError:
            return str(value).strip().lower()

    avisos = []
    columnas = set().union(*(r.keys() for r in rows))
    for col, norm, nombre in (("diseno", es.normalize_design, "diseños"),
                              ("unidad_experimental", es.normalize_unit, "unidades experimentales")):
        if col in columnas:
            valores = sorted({seguro(norm, r.get(col)) for r in rows} - {None})
            if len(valores) > 1:
                avisos.append(f"El CSV combina {nombre} distintos ({', '.join(valores)}): las medias de tratamiento de "
                              "crossovers o cuadrados latinos y de paralelos, o de corrales y de animales, no tienen "
                              "la misma varianza residual. Analízalos por separado o añade el diseño como covariable "
                              "(indicadora 0/1) y compruébalo.")
    return avisos


def _referencias(items) -> dict[str, str]:
    """--referencia COLUMNA=CATEGORIA (repetible) -> {columna: categoría}."""
    refs: dict[str, str] = {}
    for item in items or []:
        col, sep, val = str(item).partition("=")
        col, val = col.strip(), val.strip()
        if not sep or not col or not val:
            raise ValueError(f"--referencia espera COLUMNA=CATEGORIA: {item!r}")
        if col in refs:
            raise ValueError(f"--referencia repetida para la columna {col}")
        refs[col] = val
    return refs


def _texto_mensajes(mensajes: list[tuple[str, str]]) -> list[str]:
    return ([f"AVISO: {t}" for nivel, t in mensajes if nivel == "aviso"]
            + [f"Nota: {t}" for nivel, t in mensajes if nivel == "nota"])


def cmd_meta(args, cfg) -> int:
    from .stats import meta as m
    huella: dict = {}
    rows = _read_table(args.csv, args.sep, args.decimal, huella)
    if not rows:
        raise ValueError("El CSV no tiene filas")
    entrada = {"csv": args.csv, "sha256": huella["sha256"], "sep": args.sep, "decimal": args.decimal}
    if args.accion == "stpierre":
        from .stats.mixed import ALFA_LRT, st_pierre
        modo = "si" if args.pendiente_aleatoria else ("no" if args.sin_pendiente_aleatoria else "auto")
        res = st_pierre(rows, args.respuesta, args.dosis, args.estudio, args.covariable,
                        {"si": True, "no": False, "auto": "auto"}[modo], descomponer=not args.sin_descomponer,
                        sem=args.ponderar_sem)
        res["avisos"] = [*_avisos_mezcla_stpierre(rows), *res["avisos"]]
        log_event(cfg, "meta_stpierre", inputs={**entrada, "respuesta": args.respuesta, "dosis": args.dosis,
                                                "estudio": args.estudio, "covariables": args.covariable,
                                                "pendiente_aleatoria": modo, "descomponer": not args.sin_descomponer,
                                                "alfa_lrt": ALFA_LRT, "ponderar_sem": args.ponderar_sem},
                  outputs=res, actor=args.agente)
        _emit(args, res)
        return 0
    if args.accion == "validar-predictivo":
        from .stats.predictive import loso_cv
        res = loso_cv(rows, args.respuesta, args.predictores, args.estudio)
        log_event(cfg, "meta_validar_predictivo", inputs={**entrada, "respuesta": args.respuesta,
                                                          "predictores": args.predictores, "estudio": args.estudio},
                  outputs=res, actor=args.agente)
        _emit(args, res)
        return 0
    from .stats import effect_sizes as es
    rho_intra = es._check_rho(args.rho_intrasujeto, "--rho-intrasujeto: ")
    mensajes: list[tuple[str, str]] = []
    yi, vi, labels, comps = _effects_from_rows(rows, args.medida, rho_intra, mensajes)
    info_disenos, mensajes_disenos = _resumen_disenos(rows, args.medida, labels)
    mensajes = mensajes_disenos + mensajes
    hay_cruzados = any(es.normalize_design(r.get("diseno")) in es.PAIRED_DESIGNS for r in rows)
    if rho_intra is not None and not hay_cruzados:
        mensajes.append(("nota", "--rho-intrasujeto no se usa: no hay filas de diseño cruzado (crossover o cuadrado "
                                 "latino)."))
    if args.accion == "exportar-r":
        return _export_r(args, rows, yi, vi, labels, comps, cfg=cfg, entrada=entrada, mensajes=mensajes)
    referencias = _referencias(args.referencia)
    sobrantes = [c for c in referencias if c not in (args.moderador or [])]
    if sobrantes:
        raise ValueError(f"--referencia solo se aplica a moderadores (--moderador): {', '.join(sobrantes)}")
    if args.moderador is not None and not args.moderador:
        raise ValueError("--moderador necesita al menos una columna del CSV")
    # Los moderadores se codifican antes del análisis: un error de los datos (columna inexistente, celdas
    # vacías, números mezclados con texto, referencia inexistente) detiene la orden con código 2; solo un
    # moderador sin contraste estimable (una sola categoría) se informa como meta-regresión no estimable.
    codificados = no_estimable = None
    if args.moderador:
        from .stats.metareg import ModeradorNoEstimable, codificar_moderadores
        try:
            codificados = codificar_moderadores(rows, args.moderador, referencias)
        except ModeradorNoEstimable as exc:
            no_estimable = str(exc)
    out: dict = {"medida": args.medida, "k": len(yi), "estudios": labels, **info_disenos,
                 "avisos_diseno": [t for nivel, t in mensajes if nivel == "aviso"],
                 "notas_diseno": [t for nivel, t in mensajes if nivel == "nota"]}
    text: list[str] = _texto_mensajes(mensajes)
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
        if args.ajuste != "ninguno":
            res.notes.append(f"Con --metodo FE no se aplica --ajuste {args.ajuste}: el efecto fijo usa la prueba z.")
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
        estrechos = [r["excluido"] for r in out["uno_fuera"] if r.get("avisos")]
        if estrechos:
            text.append(f"AVISO: en {len(estrechos)} de {len(out['uno_fuera'])} análisis uno-fuera (sin "
                        f"{', '.join(map(str, estrechos[:5]))}{' y otros' if len(estrechos) > 5 else ''}) el IC del "
                        "HKSJ estándar es más estrecho que el de Wald (q < 1); su p e IC modificados están en "
                        "uno_fuera (p_mod, ci_mod_low, ci_mod_high).")
    if args.subgrupo:
        from .stats.metareg import categorias_de_columna
        if args.subgrupo not in set().union(*(r.keys() for r in rows)):
            raise ValueError(f"La columna de subgrupo '{args.subgrupo}' no existe en el CSV")
        vacias = [i for i, r in enumerate(rows, 1) if str(r.get(args.subgrupo) or "").strip() == ""]
        if vacias:
            raise ValueError(f"La columna de subgrupo '{args.subgrupo}' está vacía en la(s) fila(s) "
                             + ", ".join(map(str, vacias[:10])))
        groups = categorias_de_columna(args.subgrupo, [r.get(args.subgrupo) for r in rows])
        out["subgrupos"] = m.subgroups(yi, vi, groups, method=method_re, ajuste=args.ajuste)
        sg = out["subgrupos"]
        for g, r in sg["subgrupos"].items():
            text.append(f"  subgrupo {g}: k={r['k']}, estimación {r['estimate']:.4f} [{r['ci_low']:.4f}; {r['ci_high']:.4f}]")
            text += [f"  AVISO (subgrupo {g}): {a}" for a in r.get("avisos") or []]
        if sg.get("Q_entre") is not None:
            text.append(f"  Q entre subgrupos = {sg['Q_entre']:.3f} (gl {sg['gl']}), p = {sg['p']:.3g}. {sg['nota']}")
    reg = None
    if no_estimable is not None:
        out["meta_regresion"] = {"error": no_estimable}
        text.append(f"Meta-regresión no estimable: {no_estimable}")
    elif codificados is not None:
        from .stats.metareg import meta_regression
        mods, names, desc = codificados
        try:
            reg = meta_regression(yi, vi, mods, names, method=method_re, ajuste=args.ajuste)
            out["meta_regresion"] = {**reg.to_dict(), "moderadores": desc}
            for col, d in desc.items():
                if d["tipo"] == "categorico":
                    text.append(f"Moderador categórico {col}: categoría de referencia '{d['referencia']}' "
                                f"(codificación ficticia: cada coeficiente {col}[x] es la diferencia frente a la "
                                f"referencia); categorías: {', '.join(d['categorias'])}")
            text.append(reg.summary())
        except ValueError as exc:
            out["meta_regresion"] = {"error": str(exc)}
            text.append(f"Meta-regresión no estimable: {exc}")
    # Todo lo que el texto muestra como AVISO (diseño, dependencia, HKSJ con q < 1 en el modelo, uno fuera,
    # subgrupos y meta-regresión) también va en el JSON y en la procedencia.
    out["avisos"] = [linea.strip() for linea in "\n".join(text).splitlines() if linea.strip().startswith("AVISO")]
    salidas = {"k": res.k, "modelo": res.model, "prueba": res.test, "estimacion": res.estimate, "ee": res.se,
               "ic": [res.ci_low, res.ci_high], "p": res.p_value, "tau2": res.tau2, "I2": res.I2, "Q": res.Q,
               "ip": [res.pi_low, res.pi_high], "ip_gl": res.pi_df, "hksj_q": res.hksj_q,
               "ic_wald": [res.ci_wald_low, res.ci_wald_high], "avisos": out["avisos"], **info_disenos}
    if reg is not None:
        salidas["meta_regresion"] = {"coeficientes": dict(zip(reg.names, reg.coef)), "p": dict(zip(reg.names, reg.p_values)),
                                     "tau2": reg.tau2, "prueba": reg.test, "moderadores": codificados[2]}
    elif args.moderador:
        salidas["meta_regresion"] = {"error": out.get("meta_regresion", {}).get("error")}
    if args.subgrupo:
        salidas["subgrupos"] = {g: r["estimate"] for g, r in out["subgrupos"]["subgrupos"].items()}
    salidas["ajuste_aplicado"] = res.ajuste
    log_event(cfg, "metaanalisis", inputs={**entrada, "medida": args.medida, "metodo": args.metodo,
                                           "ajuste": args.ajuste, "nivel": res.level,
                                           "agregar_estudio": args.agregar_estudio, "rho": args.rho,
                                           "rho_intrasujeto": rho_intra, "moderadores": args.moderador,
                                           "referencias": referencias, "subgrupo": args.subgrupo},
              outputs=salidas, actor=args.agente)
    _emit(args, out, "\n".join(text))
    return 0


def _script_r(data_name: str, medida: str, metodo: str, ajuste: str) -> str:
    """Script de metafor que reproduce exactamente la inferencia de `nutria meta analizar` con los mismos
    parámetros (estimación, EE, IC, p e intervalo de predicción de Higgins et al. 2009).

    El modelo de metafor (rma) solo aporta tau² (mismo estimador que nutria); la estimación, el EE, el IC, la
    p y el intervalo de predicción se calculan después con las mismas fórmulas que nutria.stats.meta, para
    que no dependan de las convenciones de cada versión de metafor (p. ej. predict() o el HKSJ modificado)."""
    if metodo == "FE":
        modelo = 'res <- rma(yi, vi, data = dat, method = "FE")'
        inferencia = ("se <- sqrt(1 / sum(w)); crit <- qnorm(1 - (1 - nivel) / 2)\n"
                      "p <- 2 * pnorm(abs(mu / se), lower.tail = FALSE); q <- NA")
        ajuste_txt = "efecto fijo (prueba z)"
    else:
        test = "z" if ajuste == "ninguno" else "knha"
        modelo = f'res <- rma(yi, vi, data = dat, method = "{metodo}", test = "{test}")'
        if ajuste == "ninguno":
            inferencia = ("se <- sqrt(1 / sum(w)); crit <- qnorm(1 - (1 - nivel) / 2)\n"
                          "p <- 2 * pnorm(abs(mu / se), lower.tail = FALSE); q <- NA")
            ajuste_txt = "ninguno (prueba z de Wald)"
        else:
            factor = "max(1, q)" if ajuste == "hksj-mod" else f"if (q < {_Q_ZERO_R}) 1 else q"
            ajuste_txt = ("HKSJ modificado, max(1, q)" if ajuste == "hksj-mod"
                          else "HKSJ estándar (con q = 0 se usa max(1, q))")
            inferencia = (f"if (k > 1) {{\n"
                          f"  q <- sum(w * (dat$yi - mu)^2) / (k - 1)\n"
                          f"  se <- sqrt(({factor}) / sum(w)); crit <- qt(1 - (1 - nivel) / 2, df = k - 1)\n"
                          f"  p <- 2 * pt(abs(mu / se), df = k - 1, lower.tail = FALSE)\n"
                          f"}} else {{\n"
                          f"  q <- NA; se <- sqrt(1 / sum(w)); crit <- qnorm(1 - (1 - nivel) / 2)\n"
                          f"  p <- 2 * pnorm(abs(mu / se), lower.tail = FALSE)\n"
                          f"}}")
    ip = ("" if metodo == "FE" else
          "# 3) Intervalo de predicción de nutria: t con k - 2 gl y EE de Wald, sqrt(1 / sum(w)), con el mismo tau²\n"
          "#    (Higgins, Thompson y Spiegelhalter 2009), sea cual sea el ajuste del IC. No uses predict(res) para\n"
          "#    compararlo: según la prueba del modelo, metafor usa otros gl (k - 1) u otro EE (el del ajuste) o z.\n"
          "if (k >= 3) {\n"
          "  ip <- mu + c(-1, 1) * qt(1 - (1 - nivel) / 2, df = k - 2) * sqrt(tau2 + 1 / sum(w))\n"
          "  cat(sprintf(\"nutria: intervalo de prediccion [%.4f; %.4f] (gl = %d)\\n\", ip[1], ip[2], k - 2))\n"
          "}\n")
    return f"""# Script generado por nutria {__version__}. Revísalo antes de usarlo y ejecútalo desde su carpeta.
# Reproduce `nutria meta analizar <csv> --medida {medida} --metodo {metodo} --ajuste {ajuste}` (sin --agregar-estudio):
# medida {medida}; método {metodo}; ajuste {ajuste_txt}; nivel 95 %.
# Los tamaños de efecto (yi, vi) ya vienen calculados por nutria, incluida la conversión del SEM o SED a DE y las
# fórmulas de grupos emparejados de los diseños cruzados.
# Requiere: install.packages(c("metafor", "clubSandwich"))
library(metafor)
dat <- read.csv("{data_name}")
nivel <- 0.95
# 1) Modelo de metafor: aporta tau² (mismo estimador que nutria). print(res) muestra además la inferencia propia
#    de metafor, que puede diferir de la de nutria (p. ej. Knapp-Hartung sin max(1, q)): compara las líneas "nutria:".
{modelo}
print(res)
# 2) Inferencia de nutria, calculada explícitamente con el tau² del modelo: debe coincidir con `nutria meta analizar`
k <- res$k; tau2 <- if (is.null(res$tau2)) 0 else res$tau2
w <- 1 / (dat$vi + tau2); mu <- sum(w * dat$yi) / sum(w)
{inferencia}
cat(sprintf("nutria: estimacion %.4f; EE %.4f; IC [%.4f; %.4f]; p = %.4g; q = %.4g\\n",
            mu, se, mu - crit * se, mu + crit * se, p, q))
{ip}# 4) Dependencia: varias comparaciones por estudio (control compartido o varias variables)
if (any(duplicated(dat$estudio))) {{
  res_ml <- rma.mv(yi, vi, random = ~ 1 | estudio/comparacion, data = dat, method = "REML", test = "t")
  print(res_ml)
  print(robust(res_ml, cluster = dat$estudio, clubSandwich = TRUE))
}}
# 5) Diseño como moderador (crossover y cuadrado latino frente a paralelo), si hay varios diseños, con la misma
#    categoría de referencia que nutria (paralelo) y la prueba de Knapp-Hartung de metafor (sin max(1, s²)).
#    Como en nutria, no se ajusta si alguna fila no declara el diseño.
if ("diseno" %in% names(dat)) {{
  d <- as.character(dat$diseno)
  if (all(!is.na(d) & d != "") && length(unique(d)) > 1) {{
    dat$diseno_f <- factor(d)
    if ("paralelo" %in% levels(dat$diseno_f)) dat$diseno_f <- relevel(dat$diseno_f, ref = "paralelo")
    print(rma(yi, vi, mods = ~ diseno_f, data = dat, method = "REML", test = "knha"))
  }}
}}
# 6) Sensibilidad (uno fuera con la prueba de metafor); sesgo de publicación solo con k >= 10
print(leave1out(res))
if (res$k >= 10) {{ print(regtest(res)); funnel(res) }}
"""


# Umbral de q nulo del HKSJ en el script de R (el mismo que nutria.stats.meta._Q_ZERO_TOL).
_Q_ZERO_R = "1e-10"


def _export_r(args, rows, yi, vi, labels, comps, cfg=None, entrada: dict | None = None,
              mensajes: list | None = None) -> int:
    script_path = Path(args.salida)
    data_path = script_path.with_suffix(".csv")
    if data_path.resolve() == script_path.resolve():
        raise ValueError("--salida debe ser el script de R (p. ej. metaanalisis.R), no un .csv: los datos se "
                         "guardan junto a él con extensión .csv")
    from .stats import effect_sizes as es
    extra = [k for k in rows[0].keys() if k not in ("yi", "vi", "estudio", "comparacion")]
    # diseno y unidad_experimental se exportan normalizados (las mismas categorías que usó nutria).
    normalizar = {"diseno": es.normalize_design, "unidad_experimental": es.normalize_unit}
    with open(data_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["estudio", "comparacion", "yi", "vi", *extra])
        for r, y, v, lab, comp in zip(rows, yi, vi, labels, comps):
            valores = [(normalizar[k](r.get(k)) or "") if k in normalizar else r.get(k, "") for k in extra]
            writer.writerow([lab, comp, repr(float(y)), repr(float(v)), *valores])
    script_path.write_text(_script_r(data_path.name, args.medida, args.metodo, args.ajuste), "utf-8")
    if cfg is not None:
        log_event(cfg, "meta_exportar_r", inputs={**(entrada or {}), "medida": args.medida, "metodo": args.metodo,
                                                  "ajuste": args.ajuste, "rho_intrasujeto": args.rho_intrasujeto,
                                                  "salida": args.salida},
                  outputs={"k": len(yi), "datos": str(data_path), "sha256_datos": sha256_file(data_path),
                           "script": str(script_path), "sha256_script": sha256_file(script_path)},
                  actor=args.agente)
    mensajes = mensajes or []
    _emit(args, {"datos": str(data_path), "script": args.salida, "medida": args.medida, "metodo": args.metodo,
                 "ajuste": args.ajuste, "avisos_diseno": [t for nivel, t in mensajes if nivel == "aviso"],
                 "notas_diseno": [t for nivel, t in mensajes if nivel == "nota"]},
          "\n".join(_texto_mensajes(mensajes)
                    + [f"Datos con yi/vi en {data_path} y script de R (metafor) en {args.salida} (medida {args.medida}, "
                       f"método {args.metodo}, ajuste {args.ajuste}; el script imprime la misma estimación, IC, p e "
                       "intervalo de predicción que `nutria meta analizar`)"]))
    return 0


def cmd_potencia(args, cfg) -> int:
    from .stats import power
    if args.accion == "medias":
        res = power.n_means(args.diseno, args.delta, sd=args.de, sd_within=args.de_intra, rho=args.rho_intrasujeto,
                            animals_per_pen=args.m, icc=args.icc, treatments=args.tratamientos, alpha=args.alfa,
                            power=args.potencia)
        text = power.describe(res, args.delta, args.alfa, args.potencia)
    else:
        res = power.n_proportions(args.p1, args.p2, args.alfa, args.potencia, animals_per_pen=args.m, icc=args.icc)
        if res["unidad_experimental"] == "corral":
            text = (f"Unidad experimental: corral ({res['animales_por_unidad']} animales, ICC {res['icc']:g}, efecto de "
                    f"diseño {res['efecto_diseno']:.2f}). Corrales: {res['n_unidades_por_grupo']} por grupo "
                    f"({res['n_unidades_total']} en total). Animales: {res['n_animales_por_grupo']} por grupo "
                    f"({res['n_animales_total']} en total).\nNota: {res['nota']}")
        else:
            text = (f"Unidad experimental: animal. Animales: {res['n_por_grupo']} por grupo ({res['n_total']} en total)."
                    f"\nNota: {res['nota']}")
    _emit(args, res, text)
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
def _econ_source_problems(cfg, sources: list[tuple[str, str]]) -> list[str]:
    """Fuentes de un modelo económico que no se pueden rastrear: [PUB] sin el identificador de una fuente registrada y
    verificada (doi:, pmid:, openalex:) o de una afirmación [PUB] registrada (A-xxxx), y [DER] sin la orden `nutria` o
    el script que da el valor. Las fuentes [PUB] se comprueban en el registro del proyecto."""
    import re

    from .econ.dist import source_ids
    problems, pub = [], []
    # [DER] rastreable: la orden (`nutria <orden> ...`) o el fichero del script (con su extensión), no solo la palabra.
    der_trace = re.compile(r"\bnutria\s+[a-z]|[\w./-]+\.(?:py|R|r|ipynb|jl|sh|m)\b")
    for where, text in sources:
        if "[DER]" in text and not der_trace.search(text):
            problems.append(f"{where}: [DER] sin la orden nutria o el script que da el valor")
        if "[PUB]" in text:
            ids = source_ids(text)
            if ids:
                pub.append((where, ids))
            else:
                problems.append(f"{where}: [PUB] sin el identificador de una fuente registrada (doi:..., pmid:..., "
                                "openalex:W...) o de una afirmación [PUB] registrada (A-xxxx)")
    if not pub:
        return problems
    if not cfg.db_path.is_file():
        return problems + ["hay fuentes [PUB] y no existe el registro del proyecto para comprobarlas (`nutria init`)"]
    conn = registry.connect(cfg.db_path)
    claims = {c["id"]: c for c in registry.list_claims(conn)}
    # Una fuente registrada por su PMID puede citarse por su DOI (y al revés): se resuelve por cualquiera de los dos.
    aliases: dict[str, str] = {}
    if any(not sid.startswith("A-") for _where, ids in pub for sid in ids):
        for src in registry.list_sources(conn):
            for column, prefix, norm in (("doi", "doi:", str.lower), ("pmid", "pmid:", str),
                                         ("openalex_id", "openalex:", str.upper)):
                if src.get(column):
                    aliases.setdefault(prefix + norm(str(src[column])), src["id"])
    for where, ids in pub:
        for sid in ids:
            try:
                if sid.startswith("A-"):
                    claim = claims.get(sid)
                    if claim is None:
                        raise registry.RegistryError(f"la afirmación {sid} no está registrada")
                    if claim["label"] != "PUB":
                        raise registry.RegistryError(f"la afirmación {sid} es [{claim['label']}], no [PUB]")
                    for src in claim.get("source_ids") or []:
                        registry._require_usable_source(conn, src, f"respaldar {where}")
                else:
                    registry._require_usable_source(conn, aliases.get(sid, sid), f"respaldar {where}")
            except registry.RegistryError as exc:
                problems.append(f"{where}: {exc}")
    return problems


def cmd_econ(args, cfg) -> int:
    from .econ import farm, rnpv
    central = args.valor_central
    if args.accion == "coherencia":
        res = rnpv.coherence(farm.load_farm_model(Path(args.ganadero), central),
                             rnpv.load_project(Path(args.desarrollador), central), rtol=args.tolerancia)
        log_event(cfg, "econ_coherencia", inputs={"ganadero": args.ganadero, "sha256_ganadero": sha256_file(
            Path(args.ganadero)), "desarrollador": args.desarrollador, "sha256_desarrollador": sha256_file(
            Path(args.desarrollador)), "tolerancia": args.tolerancia, "valor_central": central}, outputs=res,
            actor=args.agente)
        _emit(args, res, rnpv.coherence_text(res))
        return 0 if res["coherente"] else 1
    status = 0
    if args.accion == "ganadero":
        model = farm.load_farm_model(Path(args.archivo), central)
        missing = farm.missing_sources(model)
        untraceable = _econ_source_problems(cfg, farm.declared_sources(model))
    else:
        project = rnpv.load_project(Path(args.archivo), central)
        missing = rnpv.missing_sources(project)
        untraceable = _econ_source_problems(cfg, rnpv.declared_sources(project))
    if args.exigir_fuentes and (missing or untraceable):
        raise ValueError("--exigir-fuentes: " + "; ".join(
            (["entradas sin fuente etiquetada ([PUB], [DER], [SUP], [DESC]) o valores por defecto sin declarar: "
              + ", ".join(missing)] if missing else [])
            + (["fuentes sin rastrear en el registro: " + "; ".join(untraceable)] if untraceable else [])))
    sources_warning = ("\n\nAVISO: fuentes sin rastrear en el registro ([PUB] con fuente o afirmación registrada y "
                       "verificada; [DER] con la orden o el script): " + "; ".join(untraceable)
                       + ". `--exigir-fuentes` las rechaza.") if untraceable else ""
    if args.accion == "ganadero":
        data = {"escenarios": farm.scenarios(model, args.sim, args.semilla),
                "monte_carlo": farm.monte_carlo(model, args.sim, args.semilla),
                "dosis": farm.dose_summary(model), "sensibilidad": farm.sensitivity(model),
                "entradas": farm.input_table(model), "entradas_sin_fuente": missing,
                "fuentes_sin_rastrear": untraceable, "resumen_fuentes": farm.sources_overview(model),
                "estadistico_central": central}
        text = farm.report(model, args.sim, args.semilla) + sources_warning
        summary = {k: data[k] for k in ("monte_carlo", "dosis", "entradas_sin_fuente", "fuentes_sin_rastrear",
                                        "resumen_fuentes", "estadistico_central")}
    else:
        data = rnpv.evaluate(project, args.sim, args.semilla, loss_threshold=args.umbral_perdida)
        data.update({"sensibilidad": rnpv.sensitivity(project), "entradas": rnpv.input_table(project),
                     "entradas_sin_fuente": missing, "fuentes_sin_rastrear": untraceable,
                     "resumen_fuentes": rnpv.sources_overview(project)})
        text = rnpv.report(project, args.sim, args.semilla, loss_threshold=args.umbral_perdida) + sources_warning
        if args.ganadero:
            data["coherencia"] = rnpv.coherence(farm.load_farm_model(Path(args.ganadero), central), project)
            text += "\n\n" + rnpv.coherence_text(data["coherencia"])
            status = 0 if data["coherencia"]["coherente"] else 1
        else:
            data["coherencia"] = None
            text += ("\n\nAVISO: no se ha comprobado la coherencia con el modelo del ganadero (precio, dosis y días): "
                     "usa --ganadero <econ_ganadero.toml> o `nutria econ coherencia`.")
        summary = {k: v for k, v in data.items() if k not in ("sensibilidad", "entradas")}
    inputs = {"archivo": args.archivo, "sha256": sha256_file(Path(args.archivo)), "sim": args.sim,
              "semilla": args.semilla, "exigir_fuentes": args.exigir_fuentes, "valor_central": central}
    if args.accion == "desarrollador":
        inputs["umbral_perdida"] = args.umbral_perdida
        if args.ganadero:
            inputs.update({"ganadero": args.ganadero, "sha256_ganadero": sha256_file(Path(args.ganadero))})
    log_event(cfg, f"econ_{args.accion}", inputs=inputs, outputs={"resumen": summary}, actor=args.agente)
    _emit(args, data, text)
    return status


def cmd_dosis(args, cfg) -> int:
    from . import dosis
    res = dosis.convertir(args.valor, args.desde, ingestion_ms=args.ingestion_ms, portador=args.portador,
                          base_pienso=args.base_pienso, ingestion_pienso=args.ingestion_pienso,
                          ms_pienso=args.ms_pienso, precio_eur_kg=args.precio_eur_kg)
    log_event(cfg, "dosis_convertir", inputs=res["entrada"],
              outputs={"dosis": res["dosis"], "faltan": res["faltan"], "coste": res.get("coste")}, actor=args.agente)
    _emit(args, res, dosis.texto_conversion(res))
    return 0


# ============================================================================ informes
_NO_COMPROBADO = ("que cada fuente respalde la afirmación que se le atribuye (lo registra verificador-citas en el "
                  "Anexo A y lo revisa una persona) ni el texto completo de las fuentes")


def _report_registry(cfg, path: Path):
    """Registro del proyecto del documento (o, si el documento no está en un proyecto, el del proyecto actual)."""
    from .reports.check import open_registry_for
    conn = open_registry_for(path)
    if conn is None and cfg.db_path.is_file():
        conn = registry.connect(cfg.db_path)
    return conn


def _check_report_file(args, cfg, path: Path, online: bool) -> int:
    from .reports.check import StampError, is_report_path, stamp_status, verify_report, write_stamp
    structure = not getattr(args, "sin_secciones", False)
    conn = _report_registry(cfg, path)
    seal = None
    try:
        # Las compuertas exigidas en la sección 14 son las de la rúbrica del proyecto del documento (las mismas que
        # usan `informe sello` y el hook).
        result = verify_report(path, client=_client(cfg, fresh=True) if online else None, conn=conn,
                               require_sections=structure)
        if getattr(args, "sellar", False):
            try:
                stamp = write_stamp(result)
            except StampError as exc:
                raise SystemExit(str(exc))
            seal = stamp_status(path, conn=conn)
            from .reports.check import issue_kind
            avisos = [i for i in result.issues if i.severity != "error"]
            log_event(cfg, "sello_informe", inputs={"informe": str(path)},
                      outputs={"sha256": stamp["sha256"], "errores": stamp["errores"],
                               "verificacion_completa": stamp["verificacion_completa"], "sello_valido": seal[0],
                               # avisos con que se selló (número y tipos; resto de D3-12)
                               "avisos": len(avisos), "tipos_avisos": sorted({issue_kind(i) for i in avisos})},
                      actor=args.agente)
    finally:
        if conn is not None:
            conn.close()
    issues, details, summary = result.issues, result.details, result.summary
    errors = result.errors
    lines = [str(i) for i in issues] or ["Sin incidencias"]
    if online and summary is not None:
        if not summary["total"]:
            lines.append("Referencias: no había referencias que verificar")
        else:
            lines.append(f"Referencias: {summary['verificadas']}/{summary['total']} verificadas en línea (existencia, "
                         f"correspondencia con la cita y retractación), {summary['pendientes']} sin verificar, "
                         f"{summary['con_error']} con error"
                         + ("" if summary["completa"] else " — verificación INCOMPLETA: el sello no será válido"))
    elif not online:
        lines.append("Sin verificación en línea de las referencias (usa --online): no se puede sellar como válido")
    lines.append(f"Resultado: {len(errors)} error(es), {len(issues) - len(errors)} aviso(s)")
    lines.append(f"No se comprueba: {_NO_COMPROBADO}.")
    if seal is not None:
        lines.append(("Sello guardado: " if seal[0] else "Sello guardado, pero NO es válido: ") + seal[1])
        if not structure and not is_report_path(path):
            lines.append("Este sello no comprobó la estructura de informe: no vale para un informe de research/informes.")
    data = {"incidencias": [i.__dict__ for i in issues], "referencias": details, "resumen": summary,
            "citas_anexo_a": result.quotes, "no_comprobado": _NO_COMPROBADO}
    if seal is not None:
        data["sello"] = {"valido": seal[0], "mensaje": seal[1]}
    _emit(args, data, "\n".join(lines))
    incomplete = online and summary is not None and not summary["completa"]
    return 1 if errors or incomplete or (seal is not None and not seal[0]) else 0


def _review_report(args, cfg) -> int:
    """`nutria informe revisar`: una persona registra que revisó el contenido exacto del informe."""
    import os
    from .reports.check import (check_report, front_matter_value, is_report_path, report_key, review_digest,
                                reviewer_name_problem, set_front_matter_fields, status_info, REVIEW_CODE)
    signer = " ".join((getattr(args, "firma_humana", None) or "").split())
    if not signer:
        raise SystemExit("La revisión humana exige --firma-humana NOMBRE: solo la registra la persona que revisó el "
                         "informe, en su propia terminal")
    if getattr(args, "agente", None) or any(os.environ.get(name) for name in ("NUTRIA_AGENT", "CLAUDECODE",
                                                                                  "CLAUDE_CODE_ENTRYPOINT")):
        raise SystemExit("La revisión humana no la registra un agente: la persona que revisó el informe ejecuta la orden "
                         "en su propia terminal (fuera de Claude Code), sin --agente ni NUTRIA_AGENT")
    problem = reviewer_name_problem(signer)
    if problem:
        raise SystemExit(f"No se registra la revisión: {problem}")
    path = Path(args.archivo)
    conn = _report_registry(cfg, path)
    if conn is None:
        raise SystemExit("No hay registro del proyecto (research/nutria.sqlite): la revisión humana se registra en él. "
                         "Ejecuta `nutria init` en la raíz del proyecto.")
    try:
        raw = path.read_bytes()            # en bytes: se conservan los saltos de línea del fichero
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise SystemExit(f"{path} no está en UTF-8: no se puede revisar")
        updates = {"revisado_por_humano": signer}
        if args.estado:
            updates["estado"] = args.estado
        try:
            new_text = set_front_matter_fields(text, updates)
        except ValueError as exc:
            raise SystemExit(str(exc))
        problems = [i for i in check_report(new_text, require_sections=is_report_path(path), conn=conn, path=path)
                    if i.severity == "error" and i.code != REVIEW_CODE]
        if problems:
            raise SystemExit(f"No se registra la revisión: el informe tiene {len(problems)} error(es); corrígelos "
                             "antes:\n" + "\n".join(str(i) for i in problems[:25]))
        if sha256_file(path) != sha256_text(raw):
            raise SystemExit("El informe cambió mientras se revisaba: vuelve a ejecutar la orden")
        digest = review_digest(new_text)
        status = status_info(new_text).value
        review_id = registry.add_report_review(conn, report_key(path), digest, signer, status, args.nota,
                                               entity_id=front_matter_value(new_text, "oportunidad"))
        tmp = path.with_name(path.name + ".tmp-revision")
        tmp.write_bytes(new_text.encode("utf-8"))
        os.replace(tmp, path)
    finally:
        conn.close()
    log_event(cfg, "revision_humana_informe", inputs={"informe": str(path)},
              outputs={"sha256_revision": digest, "firma": signer, "estado": status, "id": review_id})
    _emit(args, {"informe": str(path), "revisado_por_humano": signer, "estado": status, "sha256_revision": digest},
          f"Revisión humana registrada: {path} (estado '{status}', revisado por {signer}; huella {digest[:12]}). "
          "Si el informe cambia, la revisión deja de valer. El sello anterior ya no vale: vuelve a sellar con "
          f"`nutria informe comprobar {path} --online --sellar`.")
    return 0


def cmd_informe(args, cfg) -> int:
    if args.accion == "nuevo":
        from .reports.render import new_report
        conn = _conn(cfg)
        try:
            text = new_report(conn, args.id, gate_ids=[g.id for g in _rubric(cfg).gates])
        finally:
            conn.close()
        out = Path(args.salida) if args.salida else cfg.research_dir / "informes" / f"{args.id}.md"
        if out.exists() and not args.forzar:
            raise SystemExit(f"{out} ya existe (usa --forzar para sobrescribir)")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, "utf-8")
        _emit(args, {"informe": str(out)}, f"Esqueleto de informe creado: {out} (borrador con apartados "
                                           "[POR COMPLETAR]; ningún sello lo convierte en un informe completo)")
        return 0
    if args.accion == "sello":
        from .reports.check import stamp_status
        path = Path(args.archivo)
        conn = _report_registry(cfg, path)
        try:
            ok, msg = stamp_status(path, conn=conn)
        finally:
            if conn is not None:
                conn.close()
        _emit(args, {"valido": ok, "mensaje": msg}, msg)
        return 0 if ok else 1
    if args.accion == "revisar":
        return _review_report(args, cfg)
    return _check_report_file(args, cfg, Path(args.archivo), online=args.online)


def cmd_procedencia(args, cfg) -> int:
    import warnings

    from .provenance import verify_chain
    _no_para_evaluadores(args, "consultar la procedencia")
    if getattr(args, "operacion", "listar") == "verificar":
        res = verify_chain(cfg)
        _emit(args, res, _texto_verificacion_procedencia(res))
        return 0 if res["integra"] else 1
    with warnings.catch_warnings():   # las líneas corruptas se informan aquí, una vez y en español
        warnings.simplefilter("ignore")
        events = read_events(cfg, limit=args.ultimas, action=args.accion_filtro)
    corruptas = getattr(events, "corruptas", [])
    integridad = verify_chain(cfg, use_git=False)   # el listado avisa si lo que muestra puede estar alterado
    if not integridad["integra"]:
        _imprimir(sys.stderr, f"AVISO: la procedencia no es íntegra: {'; '.join(integridad['problemas'])}. Lo que se "
                              "muestra puede incluir eventos alterados o añadidos por fuera de nutria; detalle con "
                              "`nutria procedencia verificar`.")
    elif corruptas:
        _imprimir(sys.stderr, f"AVISO: {len(corruptas)} línea(s) corrupta(s) en {cfg.provenance_path} (líneas "
                              f"{', '.join(map(str, corruptas[:10]))}{' ...' if len(corruptas) > 10 else ''}): no se "
                              "muestran. Comprueba la integridad con `nutria procedencia verificar`.")
    _emit(args, list(events), "\n".join(f"{e.get('ts')} {str(e.get('actor')):<22} {str(e.get('action')):<18} "
                                        f"{json.dumps(e.get('inputs'), ensure_ascii=False)[:110]}" for e in events)
          or "(sin eventos)")
    return 0


def _texto_verificacion_procedencia(res: dict) -> str:
    if not res["existe"] and res["integra"]:
        return f"Sin registro de procedencia ({res['fichero']})\nResultado: íntegra (vacía)"
    if res["existe"]:
        lines = [f"Procedencia: {res['fichero']}",
                 f"  eventos: {res['eventos']} (encadenados {res['encadenados']}, anteriores a la cadena "
                 f"{res['anteriores_a_la_cadena']})"]
    else:
        lines = [f"Procedencia: {res['fichero']} (no existe)"]
    git = res.get("git") or {}
    if git.get("estado") == "amplia_lo_confirmado":
        nuevas = f"; {git['eventos_nuevos']} línea(s) nuevas sin confirmar" if "eventos_nuevos" in git else ""
        lines.append(f"  git: amplía lo confirmado (versiones comprobadas: {git.get('versiones_comprobadas')}; "
                     f"HEAD {str(git.get('commit'))[:12]}{nuevas})")
    elif git.get("estado") == "sin_referencia":
        lines.append("  git: sin versión confirmada con la que comparar")
    lines += [f"  AVISO: {a}" for a in res["avisos"]]
    lines += [f"  PROBLEMA: {p}" for p in res["problemas"]]
    lines.append("Resultado: " + ("íntegra" if res["integra"] else "NO íntegra"))
    return "\n".join(lines)


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
        res = router.evaluate(cfg, Path(args.archivo), role=args.rol, model_id=args.modelo)
        _emit(args, res)
        # Como `preguntar`: sin ningún resultado (todos omitidos, p. ej. con --offline, o sin modelos) o con algún
        # fallo, el resultado es negativo o incompleto (código 1).
        return SALIDA_OK if res.get("resultados") and not res.get("errores") else SALIDA_RESULTADO
    prompt = Path(args.archivo).read_text("utf-8") if args.archivo else args.prompt
    if not prompt:
        raise SystemExit("Indica --prompt o --archivo")
    system = Path(args.sistema).read_text("utf-8") if args.sistema else None
    res = router.ask(cfg, prompt, role=args.rol, model_id=args.modelo, system=system, sensitivity=args.sensibilidad)
    _emit(args, res, "\n\n".join(f"=== {r['modelo']} ===\n" + (r.get("texto") or r.get("omitido") or r.get("error", ""))
                                 for r in res) or "Ningún modelo configurado para ese rol (ver `nutria llm modelos`).")
    # Código de la tabla: sin ninguna respuesta (todos omitidos, p. ej. con --offline, o sin modelos) o con algún
    # modelo que falló, el resultado es negativo o incompleto.
    answered = any(r.get("texto") for r in res)
    return SALIDA_OK if answered and not any(r.get("error") for r in res) else SALIDA_RESULTADO


# ============================================================================ parser
class _Parser(argparse.ArgumentParser):
    """Analizador que nunca acepta abreviaturas de las opciones largas (allow_abbrev=False).

    Con abreviaturas, `--firma` o `--fir` equivaldrían a `--firma-humana` y eludirían el hook que bloquea esa opción a
    los agentes. `add_subparsers` crea los subanalizadores con la clase del padre (type(self)) o con `parser_class`,
    así que la regla cubre todas las órdenes y subórdenes, también las que se añadan después.
    """

    def __init__(self, *args, **kwargs):
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)


def _sin_abreviaturas(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.allow_abbrev = False
    return parser


def build_parser() -> argparse.ArgumentParser:
    # Las opciones globales se aceptan antes o después de la orden. En las subórdenes usan
    # SUPPRESS para no sobrescribir lo indicado antes de la orden (comportamiento de argparse).
    common = _Parser(add_help=False)
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
    sub = _sin_abreviaturas(p).add_subparsers(dest="orden", required=True, parser_class=_Parser)

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
    x.add_argument("--incluir-confidenciales", dest="incluir_confidenciales", action="store_true",
                   help="incluye las copias de consultas confidenciales (por defecto se omiten)")
    x = cs.add_parser("importar", parents=[common], help="importa una exportación (queda en la procedencia)")
    x.add_argument("fichero")
    x.add_argument("--sha256", default=None,
                   help="huella que mostró `cache exportar`: si el fichero no la tiene, no se importa nada")
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

    # Sin abreviaturas: «--firma» no puede valer por la opción de firma de `verificar manual` (el hook de Bash solo
    # reconoce la opción completa).
    s = sub.add_parser("verificar", parents=[common], allow_abbrev=False,
                       help="verifica DOI/PMID, las referencias de un documento, o registra una verificación manual")
    s.add_argument("objetivos", nargs="*", help="<documento.md>, o 'manual <URL>' (solo personas)")
    s.add_argument("--doi")
    s.add_argument("--pmid")
    s.add_argument("--cita", help="texto de la cita para comprobar que el identificador corresponde")
    s.add_argument("--pendientes", action="store_true",
                   help="verifica las fuentes sin verificar o con retractación desconocida")
    s.add_argument("--sellar", action="store_true",
                   help="(documento .md) guarda el sello firmado; en un informe de research/informes exige la misma "
                        "estructura que `informe comprobar --online --sellar`")
    s.add_argument("--firma-humana", dest="firma_humana", help="(verificar manual) persona que comprobó la URL")
    s.add_argument("--nota", help="(verificar manual) qué se comprobó")
    s.set_defaults(func=cmd_verificar)

    s = sub.add_parser("molecula", parents=[common], help="identidad y propiedades de compuestos")
    s.add_argument("fuente", choices=["pubchem", "chembl"])
    s.add_argument("nombre")
    s.add_argument("--max", type=int, default=5)
    s.add_argument("--peligros", action="store_true", help="(PubChem) frases H de la clasificación GHS")
    s.add_argument("--mecanismos", action="store_true", help="(ChEMBL) mecanismos de acción")
    s.add_argument("--sensibilidad", choices=["normal", "confidencial"], default="normal", help=SENSIBILIDAD_AYUDA)
    s.set_defaults(func=cmd_molecula)

    s = sub.add_parser("diana", parents=[common], help="dianas moleculares en ChEMBL")
    s.add_argument("consulta")
    s.add_argument("--organismo", default=None,
                   help='p. ej. "Bos taurus" (filtro en el servidor; busca por nombre, símbolo de gen o sinónimo y '
                        'número de acceso de UniProt)')
    s.add_argument("--max", type=int, default=20)
    s.add_argument("--actividades", action="store_true")
    s.add_argument("--sensibilidad", choices=["normal", "confidencial"], default="normal", help=SENSIBILIDAD_AYUDA)
    s.set_defaults(func=cmd_diana)

    s = sub.add_parser("proteina", parents=[common], help="proteínas en UniProt")
    s.add_argument("consulta", help='p. ej. "gene:PPARA" o "carnitine palmitoyltransferase"')
    s.add_argument("--especie", default="vacuno")
    s.add_argument("--max", type=int, default=10)
    s.add_argument("--revisadas", action="store_true")
    s.add_argument("--sensibilidad", choices=["normal", "confidencial"], default="normal", help=SENSIBILIDAD_AYUDA)
    s.set_defaults(func=cmd_proteina)

    s = sub.add_parser("red", parents=[common], help="red de interacciones STRING")
    s.add_argument("genes", nargs="+")
    s.add_argument("--especie", default="vacuno")
    s.add_argument("--puntuacion-min", dest="puntuacion_min", type=int, default=700)
    s.add_argument("--enriquecimiento", action="store_true")
    s.add_argument("--sensibilidad", choices=["normal", "confidencial"], default="normal", help=SENSIBILIDAD_AYUDA)
    s.set_defaults(func=cmd_red)

    s = sub.add_parser("rutas", parents=[common], help="rutas Reactome")
    s.add_argument("accion", choices=["buscar", "analizar"])
    s.add_argument("terminos", nargs="+", help="términos (buscar) o genes (analizar)")
    s.add_argument("--especie", default="Bos taurus")
    s.add_argument("--sensibilidad", choices=["normal", "confidencial"], default="normal", help=SENSIBILIDAD_AYUDA)
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
        x.add_argument("--especie", help="solo los registros cuya especie contiene estas palabras completas "
                                         "(p. ej. 'vacuno lechero'; 'ovino' no incluye 'bovino')")
        x.add_argument("--etapa", "--estado-fisiologico", dest="etapa",
                       help="solo los registros cuyo estado fisiológico contiene estas palabras completas")
        if name == "exportar":
            x.add_argument("--csv", required=True)
            x.add_argument("--formato", choices=["analisis", "registro"], default="analisis",
                           help="analisis: columnas que espera `nutria meta analizar`; registro: columnas internas")
            x.add_argument("--incluir-sin-conciliar", action="store_true",
                           help="incluye en el formato analisis las extracciones sin doble extracción verificada "
                                "(solo para exploración; por defecto se excluyen)")
            x.add_argument("--mezclar-especies", dest="mezclar_especies", action="store_true",
                           help="permite exportar varias especies juntas (si no, se niega); avisa en todo caso")
            x.add_argument("--casos-completos", dest="casos_completos", nargs="+", metavar="MODERADOR",
                           help="exporta solo los registros que informan de estos moderadores numéricos "
                                "(meta-regresión de casos completos) y lista los excluidos")
    x = es.add_parser("comparar", parents=[common],
                      help="doble extracción: compara y registra dos copias depositadas (--registrar)")
    x.add_argument("a")
    x.add_argument("b")
    x.add_argument("--registrar", action="store_true",
                   help="obligatoria: registra la comparación de dos copias depositadas por extractores distintos; "
                        "sin discrepancias, registra los registros como 'doble_concordante'. Las discrepancias no se "
                        "muestran sin registrarlas (ni dentro ni fuera de un proyecto)")
    x = es.add_parser("depositar", parents=[common],
                      help="deposita una copia de doble extracción (sha256 y extractor) sin registrarla")
    x.add_argument("--datos", dest="json_file", required=True, help="fichero JSON de la copia (A o B)")
    x = es.add_parser("conciliar", parents=[common],
                      help="registra la conciliación de una comparación con discrepancias (registros 'resuelta')")
    x.add_argument("id", help="comparación registrada (CE-xxxx)")
    x.add_argument("--datos", dest="json_file", required=True,
                   help="fichero JSON de la conciliación (ver ejemplos/conciliacion_ejemplo.json)")
    x = es.add_parser("comparaciones", parents=[common], help="comparaciones de doble extracción registradas")
    x.add_argument("--pendientes", action="store_true", help="solo las que tienen discrepancias sin conciliar")
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
        if kind == "oportunidad":
            x = ks.add_parser("clasificar", parents=[common],
                              help="sustituye la clasificación regulatoria (ruta, grupo funcional y situación) por el "
                                   "resultado del triage (sin firma, solo --agente regulacion-seguridad; una ruta "
                                   "fuera_de_alcance_* la propone cualquiera y bloquea la compuerta regulatoria)")
            x.add_argument("id")
            x.add_argument("--ruta", required=True, choices=_opciones_esquema("oportunidad", "ruta_regulatoria_preliminar"))
            x.add_argument("--grupo-funcional", dest="grupo_funcional", metavar="CATEGORIA.GRUPO",
                           choices=_opciones_esquema("oportunidad", "grupo_funcional"),
                           help="categoría y grupo funcional del anexo I del Reglamento (CE) 1831/2003; "
                                "obligatorio con una ruta aditivo_* (p. ej. "
                                "zootecnico.estabilizadores_condiciones_fisiologicas; valores en "
                                "src/nutria/data/schemas/oportunidad.schema.json)")
            x.add_argument("--situacion", choices=_opciones_esquema("oportunidad", "situacion"))
            x.add_argument("--motivo", required=True, help="resultado del triage que justifica la clasificación")
            x.add_argument("--firma-humana", dest="firma_humana",
                           help="persona que clasifica o que valida salir de una ruta fuera_de_alcance_* (solo personas)")
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
    r.add_argument("--min-evaluadores", dest="min_evaluadores", type=int, default=3,
                   help="evaluadores distintos exigidos por criterio (al menos 3, regla 9)")
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

    s = sub.add_parser("autoria", parents=[common], help="autoría inventiva humana (la registra una persona, con firma)")
    au = s.add_subparsers(dest="accion", required=True)
    a = au.add_parser("registrar", parents=[common], help="registra la aportación inventiva de una persona")
    a.add_argument("--entidad", required=True, help="oportunidad o hipótesis (O-xxxx, H-xxxx)")
    a.add_argument("--persona", required=True, help="persona autora de la aportación")
    a.add_argument("--aportacion", required=True, help="decisión inventiva concreta que aportó")
    a.add_argument("--firma-humana", dest="firma_humana",
                   help="persona que firma el registro (obligatoria; solo personas, en su terminal)")
    x = au.add_parser("listar", parents=[common])
    x.add_argument("--entidad")
    s.set_defaults(func=cmd_autoria)

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
            x.add_argument("--metodo", choices=["REML", "DL", "PM", "FE"], default="REML")
            x.add_argument("--ajuste", choices=["hksj", "hksj-mod", "ninguno"], default="hksj-mod",
                           help="hksj-mod (por defecto): Hartung-Knapp-Sidik-Jonkman modificado, max(1, q), que nunca "
                                "da un IC más estrecho que Wald y coincide con el estándar si q >= 1; hksj: el "
                                "estándar (con q < 1 avisa y da también la p y el IC modificados); ninguno: Wald")
            x.add_argument("--rho-intrasujeto", dest="rho_intrasujeto", type=float,
                           help="correlación intrasujeto r en [0, 1) para las filas de diseño cruzado (crossover o "
                                "cuadrado latino) sin columna rho_intrasujeto; la SMD de esos diseños la exige")
        if name == "analizar":
            x.add_argument("--agregar-estudio", dest="agregar_estudio", action="store_true",
                           help="agrega las comparaciones dependientes de cada estudio")
            x.add_argument("--rho", type=float, default=0.5, help="correlación supuesta entre comparaciones")
            x.add_argument("--moderador", nargs="*",
                           help="columnas para meta-regresión: numéricas o categóricas (p. ej. diseno, "
                                "unidad_experimental), estas con codificación ficticia frente a una referencia")
            x.add_argument("--referencia", nargs="*", default=[], metavar="COLUMNA=CATEGORIA",
                           help="categoría de referencia de un moderador categórico (por defecto, paralelo en diseno, "
                                "animal en unidad_experimental y, en las demás columnas, la primera en orden "
                                "alfabético); también fuerza a tratar como categórica una columna numérica")
            x.add_argument("--subgrupo", help="columna para análisis por subgrupos (p. ej. diseno)")
        if name == "stpierre":
            x.add_argument("--respuesta", required=True)
            x.add_argument("--dosis", required=True)
            x.add_argument("--estudio", required=True)
            x.add_argument("--covariable", nargs="*", default=[])
            pendiente = x.add_mutually_exclusive_group()
            pendiente.add_argument("--pendiente-aleatoria", dest="pendiente_aleatoria", action="store_true",
                                   help="fuerza la pendiente aleatoria (por defecto se elige con la razón de "
                                        "verosimilitudes si es estimable)")
            pendiente.add_argument("--sin-pendiente-aleatoria", dest="sin_pendiente_aleatoria", action="store_true",
                                   help="fuerza el intercepto aleatorio (avisa si los datos indican pendientes "
                                        "heterogéneas)")
            x.add_argument("--sin-descomponer", dest="sin_descomponer", action="store_true",
                           help="no separa la pendiente intra y entre estudios")
            x.add_argument("--ponderar-sem", dest="ponderar_sem", metavar="COLUMNA",
                           help="pondera cada media por 1/SEM² (St-Pierre 2001) con el SEM de esa columna; "
                                "por defecto no pondera")
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
    x.add_argument("--de", type=float,
                   help="DE entre animales (paralelo, también con corrales; en diseños cruzados, con --rho-intrasujeto)")
    x.add_argument("--diseno", choices=["paralelo", "crossover", "cuadrado_latino"], default="paralelo")
    x.add_argument("--alfa", type=float, default=0.05)
    x.add_argument("--potencia", type=float, default=0.80)
    x.add_argument("--m", type=int, help="animales por corral (el corral es la unidad experimental; con --icc)")
    x.add_argument("--icc", type=float, help="correlación intraclase dentro del corral")
    x.add_argument("--de-intra", dest="de_intra", type=float, help="DE intraanimal (residual) de un diseño cruzado")
    x.add_argument("--rho-intrasujeto", dest="rho_intrasujeto", type=float,
                   help="correlación intrasujeto, para obtener la DE intraanimal de --de en un diseño cruzado")
    x.add_argument("--tratamientos", type=int, help="tratamientos (y periodos) del cuadrado latino")
    x = pw.add_parser("proporciones", parents=[common])
    x.add_argument("--p1", type=float, required=True)
    x.add_argument("--p2", type=float, required=True)
    x.add_argument("--alfa", type=float, default=0.05)
    x.add_argument("--potencia", type=float, default=0.80)
    x.add_argument("--m", type=int, help="animales por corral o lote (el corral es la unidad experimental; con --icc)")
    x.add_argument("--icc", type=float, help="correlación intraclase dentro del corral")
    s.set_defaults(func=cmd_potencia)

    s = sub.add_parser("econ", parents=[common], help="evaluación económica")
    ec = s.add_subparsers(dest="accion", required=True)
    central_help = ("estadístico del valor central de TODAS las entradas (valores centrales, respuesta umbral, "
                    "tornado y coherencia): moda (por defecto) o mediana")
    for name in ("ganadero", "desarrollador"):
        x = ec.add_parser(name, parents=[common])
        x.add_argument("archivo", help="modelo en TOML (ver ejemplos/)")
        x.add_argument("--sim", type=int, default=10000)
        x.add_argument("--semilla", type=int, default=42)
        x.add_argument("--exigir-fuentes", dest="exigir_fuentes", action="store_true",
                       help="falla si alguna entrada declarada no tiene 'fuente' con etiqueta ([PUB], [DER], [SUP], "
                            "[DESC]), si se usa un valor por defecto no neutro sin declararlo, si una fuente [PUB] no "
                            "cita una fuente o afirmación registrada y verificada o si una [DER] no indica la orden")
        x.add_argument("--valor-central", dest="valor_central", choices=("moda", "mediana"), default="moda",
                       help=central_help)
        if name == "desarrollador":
            x.add_argument("--umbral-perdida", dest="umbral_perdida", type=float,
                           help="umbral de pérdida en € para P(pérdida > umbral) (por defecto, [proyecto]."
                                "umbral_perdida_eur o el 50 %% del coste central de desarrollo)")
            x.add_argument("--ganadero", help="modelo del ganadero: comprueba que precio, dosis y días coinciden "
                                              "(código de salida 1 si no)")
    x = ec.add_parser("coherencia", parents=[common],
                      help="compara precio, dosis y días del modelo del ganadero y del de la empresa (salida 1 si "
                           "difieren o no puede comprobarse)")
    x.add_argument("ganadero", help="modelo del ganadero (TOML)")
    x.add_argument("desarrollador", help="modelo de la empresa (TOML) con las ventas construidas")
    x.add_argument("--tolerancia", type=float, default=0.001, help="diferencia relativa admitida (0,001 = 0,1 %%)")
    x.add_argument("--valor-central", dest="valor_central", choices=("moda", "mediana"), default="moda",
                   help=central_help)
    s.set_defaults(func=cmd_econ)

    s = sub.add_parser("dosis", parents=[common], help="conversión de dosis entre g/animal/día, mg/kg de MS y g/t")
    dsub = s.add_subparsers(dest="accion", required=True)
    x = dsub.add_parser("convertir", parents=[common],
                        help="convierte una dosis a las tres bases (salida [DER]); las ingestiones se declaran")
    x.add_argument("valor", type=float, help="dosis en la base indicada con --desde")
    x.add_argument("--desde", required=True, choices=("g_animal_dia", "mg_kg_ms", "g_t"),
                   help="base de la dosis: g/animal/día, mg/kg de MS de la ración o g/t del pienso portador")
    x.add_argument("--ingestion-ms", dest="ingestion_ms", type=float,
                   help="ingestión de MS de la ración (kg MS/animal/día), con fuente o [SUP]")
    x.add_argument("--portador", choices=("racion_completa", "concentrado"), help="pienso que lleva el producto")
    x.add_argument("--base", dest="base_pienso", choices=("tal_cual", "ms"), help="base del g/t del pienso portador")
    x.add_argument("--ingestion-pienso", dest="ingestion_pienso", type=float,
                   help="ingestión del pienso portador en su base (kg/animal/día)")
    x.add_argument("--ms-pienso", dest="ms_pienso", type=float, help="fracción de MS del pienso portador (0-1)")
    x.add_argument("--precio-eur-kg", dest="precio_eur_kg", type=float,
                   help="precio del producto (€/kg): da el coste por animal y día y por tonelada de pienso")
    s.set_defaults(func=cmd_dosis)

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
    x = ins.add_parser("revisar", parents=[common], allow_abbrev=False,
                       help="registra la revisión humana del contenido exacto del informe (solo personas)")
    x.add_argument("archivo")
    x.add_argument("--firma-humana", dest="firma_humana", help="persona que revisó el informe (solo personas)")
    x.add_argument("--estado", choices=["revisado", "final"], help="cambia también el estado del informe")
    x.add_argument("--nota", help="qué se revisó")
    s.set_defaults(func=cmd_informe)

    s = sub.add_parser("procedencia", parents=[common], help="consulta el registro de procedencia")
    s.add_argument("--ultimas", type=int, default=30)
    s.add_argument("--accion", dest="accion_filtro")
    s.add_argument("operacion", nargs="?", choices=["listar", "verificar"], default="listar",
                   help="listar (por defecto) o verificar la integridad: cadena de hashes, líneas corruptas y versión "
                        "confirmada en git (código 1 si no es íntegra)")
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
SALIDA_INTERRUMPIDA = 130   # 128 + SIGINT, como las demás herramientas de Unix
SALIDA_TUBERIA = 141        # 128 + SIGPIPE
CODIGOS_SALIDA = {
    SALIDA_OK: ("ok", "la orden terminó y el resultado es el esperado"),
    SALIDA_RESULTADO: ("resultado", "la orden terminó, pero el resultado es negativo o incompleto: discrepancias en "
                                    "`extraccion comparar`, sello no válido, referencia, lote de referencias o informe "
                                    "que no supera la verificación, búsqueda o consulta incompleta (alguna página o "
                                    "parte falló), caché con copias no válidas o importación con entradas rechazadas, "
                                    "comprobaciones esenciales de `doctor` fallidas, `llm preguntar` o `llm evaluar` "
                                    "sin ninguna respuesta o con algún modelo que falló"),
    SALIDA_ENTRADA: ("entrada", "entrada no válida o condición previa no cumplida: argumentos, fichero inexistente o "
                                "inaccesible (directorio, permisos), JSON o CSV fuera de formato, configuración no "
                                "válida, proyecto sin inicializar, orden no permitida para el rol del agente"),
    SALIDA_REGISTRO: ("registro", "regla del registro o error de la base de datos (identificador que no existe en el "
                                  "registro, transición o compuerta no permitida, fuente sin verificar...)"),
    SALIDA_RED: ("red", "fallo de red, host bloqueado, error o respuesta no válida del servicio, credencial ausente o "
                        "rechazada, límite de uso del servicio, o modo offline sin copia válida en la caché"),
    SALIDA_CALCULO: ("calculo", "falta una dependencia opcional o un cálculo no converge"),
    SALIDA_NO_ENCONTRADO: ("no_encontrado", "el recurso pedido no existe en la fuente externa (HTTP 404)"),
    SALIDA_INTERNO: ("interno", "error interno inesperado (fallo del programa; NUTRIA_DEPURAR=1 muestra la traza)"),
    SALIDA_INTERRUMPIDA: ("interrumpida", "orden interrumpida (Ctrl+C)"),
    SALIDA_TUBERIA: ("tuberia", "quien leía la salida la cerró antes de que terminara (p. ej. `| head`): la salida "
                                "no llegó entera; no se imprime ningún error (si la orden ya había fallado, el código "
                                "es el de su error, con su texto por stderr)"),
}


# «No existe X»: un identificador que no está en el registro es un error del registro (código 3), tanto si lo detecta
# la base de datos (RegistryError) como si lo detecta la propia orden (SystemExit con mensaje).
_NO_EXISTE = "no existe "


def _es_no_existe(mensaje) -> bool:
    """Mensaje de un identificador que no existe en el registro (no el de un proyecto sin base de datos, que es una
    condición previa: código 2)."""
    text = str(mensaje or "").strip().lower()
    return text.startswith(_NO_EXISTE) and not text.startswith(_NO_EXISTE + "la base de datos")


# Prefijo del mensaje de un NetworkError según su motivo (el código es siempre SALIDA_RED): una credencial, una
# petición rechazada, un error del servicio o una respuesta inesperada no son «errores de red». Los motivos red,
# bloqueado y conexion sí lo son («Error de red»).
_PREFIJOS_RED = {"credenciales": "Error de credenciales", "respuesta": "Respuesta no válida del servicio",
                 "limite": "Límite de uso del servicio", "peticion": "Petición rechazada por el servicio",
                 "servicio": "Error del servicio", "http": "Error del servicio"}


class SalidaOrden(SystemExit):
    """SystemExit con un código numérico de la tabla que conserva el mensaje de la orden: `exc.code` es el código (el
    intérprete sale con él sin volver a imprimir nada) y `str(exc)`, el mensaje (lo que comprueba
    `pytest.raises(SystemExit, match=...)` en las pruebas que llaman a main())."""

    def __init__(self, codigo: int, mensaje: str):
        super().__init__(codigo)
        self.mensaje = mensaje

    def __str__(self) -> str:
        return self.mensaje


class ErrorOrden(Exception):
    """Error de una orden con su código de salida (tabla `CODIGOS_SALIDA`); main() lo presenta sin traza."""

    def __init__(self, mensaje: str, codigo: int = SALIDA_ENTRADA):
        super().__init__(mensaje)
        self.codigo = codigo


def _texto_codigos_salida() -> str:
    import textwrap
    lines = ["códigos de salida (los errores salen en texto por stderr; con --json, además por stdout como "
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


def _silenciar(stream) -> None:
    """Redirige `stream` (stdout o stderr) a /dev/null cuando quien lo leía ya lo cerró (tubería rota), para que el
    intérprete no vuelva a fallar al vaciarlo al terminar («Exception ignored ... BrokenPipeError», código 120)."""
    import os
    try:
        fd = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(fd, stream.fileno())
        finally:
            os.close(fd)
    except (OSError, ValueError, AttributeError):   # sin descriptor propio (p. ej. la salida capturada de las pruebas)
        pass


def _vaciar_stdout() -> bool:
    """Vacía stdout; False (y stdout silenciada) si quien la leía ya la había cerrado (p. ej. `| head` o `| true`).
    Una stdout cerrada desde el arranque (`>&-`, sys.stdout None) no es una tubería rota: Python descarta lo impreso."""
    if sys.stdout is None:
        return True
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        _silenciar(sys.stdout)
        return False
    except ValueError:   # flujo cerrado: nada que vaciar
        return True
    except OSError:
        # Otro error de E/S (disco lleno, EIO): lo impreso no llegó a su destino. Se silencia stdout para que el
        # intérprete no vuelva a fallar al cerrar («Exception ignored ...», código 120) y main() lo presenta como un
        # error con su código de la tabla.
        _silenciar(sys.stdout)
        raise
    return True


def _imprimir(stream, texto: str) -> bool:
    """Imprime una línea en `stream` y la vacía; False, sin traza, si quien la leía ya había cerrado la tubería."""
    if stream is None:   # flujo cerrado al arrancar (p. ej. `2>&-`): print(file=None) escribiría en stdout
        return False
    try:
        print(texto, file=stream, flush=True)
    except BrokenPipeError:
        _silenciar(stream)
        return False
    except ValueError:   # flujo cerrado: no hay dónde informar
        return False
    except OSError:      # disco lleno, EIO...: no hay dónde informar; sin dejar el búfer lleno para el cierre
        _silenciar(stream)
        return False
    return True


def _error(args, mensaje, codigo: int, prefijo: str, cfg=None, motivo: str | None = None) -> int:
    """Presenta un error y devuelve su código.

    El texto («prefijo: mensaje») sale siempre por stderr, con --json o sin él: es lo que lee una persona (y lo que
    leían las órdenes y pruebas anteriores a la salida en JSON). Con --json, el error sale además por stdout como un
    objeto JSON ({"error", "codigo", "tipo"[, "motivo"]}), que es lo que lee quien procesa la salida. Si quien leía
    stdout ya la había cerrado, el código sigue siendo el del error (su texto está en stderr), sin traza."""
    from .http import redact
    texto = redact(mensaje, cfg) or prefijo
    if getattr(args, "json", False):
        payload = {"error": texto, "codigo": codigo, "tipo": CODIGOS_SALIDA.get(codigo, ("interno",))[0]}
        if motivo:
            payload["motivo"] = motivo
        _imprimir(sys.stdout, json.dumps(payload, ensure_ascii=False))
    else:
        _vaciar_stdout()   # lo que la orden ya imprimió sale antes que el error (y una tubería cerrada no da traza)
    _imprimir(sys.stderr, f"{prefijo}: {texto}")
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
    """Analiza los argumentos. Un error de uso sale por stderr (argparse) y, con --json, también como JSON por
    stdout. Si quien leía la ayuda (`--help`) cerró la tubería antes de recibirla, el código es el de la tabla (141),
    sin «Exception ignored» al cerrar el intérprete."""
    try:
        return _parse_args_json(parser, argv) if "--json" in argv else parser.parse_args(argv)
    except SystemExit as exc:
        if not _vaciar_stdout() and exc.code in (0, None):
            raise SystemExit(SALIDA_TUBERIA) from None
        raise
    except BrokenPipeError:   # la ayuda, sin búfer (PYTHONUNBUFFERED=1), a una tubería ya cerrada
        _silenciar(sys.stdout)
        raise SystemExit(SALIDA_TUBERIA) from None


def _parse_args_json(parser, argv: list[str]):
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
            _imprimir(sys.stdout, json.dumps({"error": message, "codigo": SALIDA_ENTRADA, "tipo": "entrada"},
                                             ensure_ascii=False))
        raise
    finally:
        if buffer.getvalue() and sys.stderr is not None:
            try:
                sys.stderr.write(buffer.getvalue())
                sys.stderr.flush()
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    global _RECURSOS
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = _parse_args(parser, argv)
    _firma_solo_de_personas(args)
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
        code = int(args.func(args, cfg) or 0)
        # Se vacía aquí, dentro del try: si quien leía la salida ya la cerró (`| head`, `| true`), es el código 141 de
        # la tabla y no un «Exception ignored ... BrokenPipeError» con código 120 al cerrar el intérprete.
        return code if _vaciar_stdout() else SALIDA_TUBERIA
    except SystemExit as exc:
        if exc.code is None or isinstance(exc.code, int):
            try:
                vaciada = _vaciar_stdout()
            except OSError as err:   # la salida no se pudo escribir: error (código de la orden o, si era 0, entrada)
                codigo = exc.code or SALIDA_ENTRADA
                _error(args, f"no se pudo escribir la salida: {_describe_oserror(err)}", codigo, "Error", cfg)
                raise SystemExit(codigo) from None
            if not vaciada and not exc.code:
                raise SystemExit(SALIDA_TUBERIA) from None
            raise
        # Las órdenes que se niegan con un mensaje (SystemExit(str)): entrada no válida o condición previa, salvo un
        # identificador que no existe en el registro, que es el mismo error que da la base de datos (código 3).
        # Se relanza SystemExit (con el código numérico y el mismo mensaje) para quien llama a main() desde Python.
        codigo = SALIDA_REGISTRO if _es_no_existe(exc.code) else SALIDA_ENTRADA
        _error(args, str(exc.code), codigo, "Error de registro" if codigo == SALIDA_REGISTRO else "Error", cfg)
        raise SalidaOrden(codigo, str(exc.code)) from None
    except ErrorOrden as exc:
        return _error(args, str(exc), exc.codigo, "Error", cfg)
    except registry.RegistryError as exc:
        return _error(args, str(exc), SALIDA_REGISTRO, "Error de registro", cfg)
    except NotFoundError as exc:
        return _error(args, str(exc), SALIDA_NO_ENCONTRADO, "No encontrado", cfg, exc.motivo)
    except OfflineError as exc:
        return _error(args, str(exc), SALIDA_RED, "Sin red (modo offline)", cfg, exc.motivo)
    except NetworkError as exc:
        prefijo = _PREFIJOS_RED.get(exc.motivo, "Error de red")
        return _error(args, str(exc), SALIDA_RED, prefijo, cfg, exc.motivo)
    except BrokenPipeError:   # quien lee la salida la cerró antes (p. ej. `| head`): no es un error de la orden
        _silenciar(sys.stdout)
        return SALIDA_TUBERIA
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
