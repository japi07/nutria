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


def _conn(cfg):
    if not cfg.db_path.is_file():
        raise SystemExit(f"No existe la base de datos ({cfg.db_path}). Ejecuta `nutria init` en la raíz del proyecto.")
    return registry.connect(cfg.db_path)


def _client(cfg, fresh: bool = False) -> HttpClient:
    """Cliente HTTP; con fresh=True no lee la caché (verificación de referencias)."""
    return HttpClient(cfg, fresh=fresh)


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
    root = Path.cwd()
    conf = root / CONFIG_FILENAME
    if not conf.exists() or args.forzar:
        conf.write_text(DEFAULT_CONFIG_TEXT, "utf-8")
    cfg = load_config(root)
    for sub in ("oportunidades", "hipotesis", "informes", "planes", "revisiones", "datos", "evals"):
        (cfg.research_dir / sub).mkdir(parents=True, exist_ok=True)
    registry.init_db(cfg.db_path).close()
    log_event(cfg, "init", outputs={"entorno": environment_snapshot(cfg.root)})
    _emit(args, {"raiz": str(root), "configuracion": str(conf), "base_de_datos": str(cfg.db_path)},
          f"Proyecto inicializado en {root}\n  configuración: {conf}\n  base de datos: {cfg.db_path}")
    return 0


HOSTS = {
    "PubMed (NCBI E-utilities)": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?retmode=json",
    "Europe PMC": "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=rumen&format=json&pageSize=1",
    "OpenAlex": "https://api.openalex.org/works?search=rumen&per-page=1",
    "Crossref": "https://api.crossref.org/works?rows=1&query=rumen",
    "ChEMBL": "https://www.ebi.ac.uk/chembl/api/data/status.json",
    "PubChem": "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/305/property/MolecularFormula/JSON",
    "UniProt": "https://rest.uniprot.org/uniprotkb/search?query=gene:PPARA&size=1&format=json",
    "STRING": "https://string-db.org/api/json/version",
    "Reactome": "https://reactome.org/ContentService/data/database/version",
}


def cmd_doctor(args, cfg) -> int:
    import requests
    report = {"version": __version__, "raiz": str(cfg.root), "config": (cfg.root / CONFIG_FILENAME).is_file(),
              "base_datos": cfg.db_path.is_file(), "entorno": environment_snapshot(cfg.root), "red": {},
              "claves": {k: bool(cfg.secret(k)) for k in ("NCBI_API_KEY", "OPENALEX_API_KEY", "LENS_API_TOKEN")}}
    client = _client(cfg)
    for name, url in HOSTS.items():
        params = {"api_key": cfg.secret("OPENALEX_API_KEY")} if name == "OpenAlex" and cfg.secret("OPENALEX_API_KEY") else None
        try:
            resp = client.session.get(url, params=params, timeout=15)
            report["red"][name] = f"OK ({resp.status_code})" if resp.status_code < 400 else f"HTTP {resp.status_code}"
        except requests.exceptions.ProxyError:
            report["red"][name] = "BLOQUEADO por proxy/política de red"
        except requests.RequestException as exc:
            report["red"][name] = f"sin conexión ({type(exc).__name__})"
    lines = [f"nutria {__version__} | raíz: {cfg.root}",
             f"  nutria.toml: {'sí' if report['config'] else 'NO (ejecuta nutria init)'} | "
             f"base de datos: {'sí' if report['base_datos'] else 'NO'}",
             "  entorno: " + ", ".join(f"{k}={v}" for k, v in report["entorno"].items() if v),
             "  claves: " + ", ".join(f"{k}={'sí' if v else 'no'}" for k, v in report["claves"].items()),
             "  conectividad:"] + [f"    {k:<28} {v}" for k, v in report["red"].items()]
    if not report["claves"]["OPENALEX_API_KEY"]:
        lines.append("  OpenAlex exige una clave gratuita desde el 13-02-2026: https://openalex.org/settings/api")
    if any(not v.startswith("OK") for v in report["red"].values()):
        lines.append("  Algunas fuentes no son accesibles: permite esos dominios en la red. Sin red, la verificación "
                     "de referencias queda incompleta y los sellos no son válidos.")
    _emit(args, report, "\n".join(lines))
    return 0


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
def cmd_buscar(args, cfg) -> int:
    from .sources import europepmc, openalex, pubmed
    client = _client(cfg)
    year_filter = (f" AND PUB_YEAR:[{args.desde or 1800} TO {args.hasta or 3000}]"
                   if (args.desde or args.hasta) else "")
    if args.fuente == "pubmed":
        res = pubmed.search_records(client, args.consulta, retmax=args.max, mindate=args.desde, maxdate=args.hasta,
                                    with_abstracts=args.resumenes)
    elif args.fuente == "europepmc":
        res = europepmc.search(client, args.consulta + year_filter, page_size=args.max)
    elif args.fuente == "efsa":
        res = europepmc.efsa_journal(client, f"({args.consulta}){year_filter}", page_size=args.max)
    else:
        res = openalex.search(client, args.consulta, per_page=args.max,
                              from_year=int(args.desde) if args.desde else None,
                              to_year=int(args.hasta) if args.hasta else None)
    records = res["records"]
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
    log_event(cfg, "busqueda", inputs={"fuente": args.fuente, "consulta": args.consulta, "max": args.max,
                                       "desde": args.desde, "hasta": args.hasta},
              outputs={"total": res.get("count"), "devueltos": len(records), "registrados": len(registered),
                       "conflictos": len(conflicts)},
              actor=args.agente)
    # Los cambios del registro de fuentes no llevan la consulta en claro (puede ser confidencial): solo su huella.
    _log_source_changes(cfg, args, changes, {"fuente": args.fuente, "consulta_sha256": sha256_text(args.consulta)})
    header = f"{args.fuente}: {res.get('count', 0)} resultados totales; mostrando {len(records)}"
    if res.get("query_translation"):
        header += f"\nTraducción de la consulta: {res['query_translation']}"
    if registered:
        new = sum(1 for c in changes if c.get("accion") == "alta")
        header += (f"\nRegistradas {new} fuentes nuevas SIN verificar (no sirven para [PUB] hasta "
                   "`nutria verificar --pendientes`)"
                   + (f"; {len(registered) - new} ya estaban en el registro" if len(registered) > new else ""))
    if changes and _change_lines(changes):
        header += "\nCambios y avisos del registro de fuentes:\n" + "\n".join(_change_lines(changes))
    _emit(args, {"total": res.get("count"), "registros": [r.to_dict() for r in records], "registradas": registered,
                 "cambios": [c for c in changes if c.get("accion") != "alta"], "conflictos": conflicts},
          header + "\n" + _records_text(records))
    if conflicts:
        print(f"Error de registro: {len(conflicts)} resultado(s) NO registrado(s) por conflicto con el registro:\n"
              + "\n".join(f"  {c['registro']}: {c['error']}" for c in conflicts), file=sys.stderr)
        return 3
    return 0


def _license_label(value) -> str:
    """Etiqueta corta de la licencia de una fuente (URL de Creative Commons, código de OpenAlex u otra URL)."""
    import re
    if not value:
        return "?"
    text = str(value).strip()
    match = re.search(r"creativecommons\.org/licenses/([a-z-]+)/(\d(?:\.\d)?)", text, re.IGNORECASE)
    if match:
        return f"CC {match.group(1).upper()} {match.group(2)}"
    if re.search(r"creativecommons\.org/publicdomain/zero|^cc0", text, re.IGNORECASE):
        return "CC0"
    if re.fullmatch(r"cc-by(?:-[a-z]{2})*", text, re.IGNORECASE):
        return "CC " + text[3:].upper()
    return text if len(text) <= 40 else text[:37] + "..."


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
        raise SystemExit(f"No se registra: {ver.status} ({ver.message})")
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
        lines.append(f"  AVISO: la fuente ya estaba registrada como {source_id} (mismo trabajo): usa {source_id}, no "
                     f"{requested}, en afirmaciones y extracciones.")
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
        return 0 if res.ok else 2
    if args.pendientes:
        conn = _conn(cfg)
        rows = registry.list_sources(conn, unverified_only=False)
        rows = [r for r in rows if not r.get("verified") or r.get("retracted") is None]
        out = []
        for r in rows:
            if registry.get_source(conn, r["id"]) is None:   # fusionada con otra en esta misma pasada
                continue
            # Se verifica por el identificador que da nombre a la fuente (pmid:... por su PMID).
            by_pmid = r.get("pmid") and (r["id"].startswith("pmid:") or not r.get("doi"))
            res = verify_pmid(client, r["pmid"]) if by_pmid else (verify_doi(client, r["doi"]) if r.get("doi") else None)
            if res is None:
                out.append({"id": r["id"], "estado": "sin_identificador", "retractado": None,
                            "detalle": "sin DOI ni PMID: no se puede verificar"})
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
        lines = []
        for o in out:
            lines.append(f"{o['id']}: {o['estado']} | retractación "
                         f"{ {True: 'SÍ', False: 'no', None: 'desconocida'}[o['retractado']] }")
            if o.get("detalle"):
                lines.append(f"  {o['detalle']}")
            lines += _change_lines(o.get("cambios") or [])
        _emit(args, out, "\n".join(lines) or "No hay fuentes pendientes de verificar")
        return 3 if any(o["estado"] == "conflicto" for o in out) else 0
    if not targets:
        raise SystemExit("Indica --doi, --pmid, --pendientes, un fichero .md o 'manual <URL>'")
    args.sin_secciones = True  # verificar comprueba referencias; la estructura de informe la exige `informe comprobar`
    return _check_report_file(args, cfg, Path(targets[0]), online=True)


# ============================================================================ moléculas, proteínas, redes
def cmd_molecula(args, cfg) -> int:
    client = _client(cfg)
    if args.fuente == "pubchem":
        from .sources import pubchem
        data = pubchem.compound_by_name(client, args.nombre)
        if args.peligros:
            for p in data[:3]:
                p["GHS_frases_H"] = pubchem.ghs_hazards(client, p["CID"])
    else:
        from .sources import chembl
        data = chembl.molecule_search(client, args.nombre, limit=args.max)
        if args.mecanismos:
            for m in data[:3]:
                m["mecanismos"] = chembl.mechanisms(client, m["chembl_id"])
    log_event(cfg, "molecula", inputs={"fuente": args.fuente, "nombre": args.nombre}, outputs={"n": len(data)},
              actor=args.agente)
    _emit(args, data)
    return 0


def cmd_diana(args, cfg) -> int:
    from .sources import chembl
    client = _client(cfg)
    targets = chembl.target_search(client, args.consulta, organism=args.organismo, limit=args.max)
    if args.actividades and targets:
        for t in targets[:2]:
            t["actividades"] = chembl.activities(client, target_chembl_id=t["target_chembl_id"], limit=25)
    log_event(cfg, "diana", inputs={"consulta": args.consulta, "organismo": args.organismo},
              outputs={"n": len(targets)}, actor=args.agente)
    _emit(args, targets)
    return 0


def cmd_proteina(args, cfg) -> int:
    from .sources import uniprot
    from .sources.base import taxon_id
    client = _client(cfg)
    rows = uniprot.search(client, args.consulta, taxon=taxon_id(args.especie) if args.especie else None,
                          size=args.max, reviewed_only=args.revisadas)
    log_event(cfg, "proteina", inputs={"consulta": args.consulta, "especie": args.especie},
              outputs={"n": len(rows)}, actor=args.agente)
    text = "\n".join(f"{r['accesion']:<12} {','.join(r['genes'])[:20]:<20} {r['organismo'] or '':<18} "
                     f"{'rev' if r['revisada'] else 'no rev'}  {r['proteina'] or ''}" for r in rows) or "(sin resultados)"
    _emit(args, rows, text)
    return 0


def cmd_red(args, cfg) -> int:
    from .sources import stringdb
    from .sources.base import taxon_id
    client = _client(cfg)
    species = taxon_id(args.especie)
    data = {"interacciones": stringdb.network(client, args.genes, species, args.puntuacion_min)}
    if args.enriquecimiento:
        data["enriquecimiento"] = stringdb.enrichment(client, args.genes, species)
    log_event(cfg, "red_string", inputs={"genes": args.genes, "especie": species}, actor=args.agente,
              outputs={"interacciones": len(data["interacciones"])})
    _emit(args, data)
    return 0


def cmd_rutas(args, cfg) -> int:
    from .sources import reactome
    client = _client(cfg)
    if args.accion == "buscar":
        data = reactome.search(client, " ".join(args.terminos), species=args.especie)
    else:
        data = reactome.analyze_genes(client, args.terminos)
    log_event(cfg, "rutas", inputs={"accion": args.accion, "terminos": args.terminos}, actor=args.agente)
    _emit(args, data)
    return 0


def cmd_patentes(args, cfg) -> int:
    from .sources import patents
    data = {"enlaces": patents.search_links(args.consulta)}
    if args.lens:
        data["lens"] = patents.lens_search(_client(cfg), args.consulta, size=args.max)
    log_event(cfg, "busqueda_patentes", inputs={"consulta": args.consulta}, actor=args.agente)
    _emit(args, data)
    return 0


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

    p = argparse.ArgumentParser(prog="nutria",
                                description="Herramientas del sistema de I+D computacional en nutrición animal.")
    p.add_argument("--json", action="store_true", default=False, help="salida JSON")
    p.add_argument("--agente", default=None, help="nombre del agente que ejecuta la orden (procedencia)")
    p.add_argument("--offline", action="store_true", default=False, help="usar solo la caché, sin red")
    p.add_argument("--version", action="version", version=f"nutria {__version__}")
    sub = p.add_subparsers(dest="orden", required=True)

    s = sub.add_parser("init", parents=[common], help="inicializa el proyecto en el directorio actual")
    s.add_argument("--forzar", action="store_true", help="reescribe nutria.toml")
    s.set_defaults(func=cmd_init)
    sub.add_parser("doctor", parents=[common], help="comprueba entorno, claves y acceso a las fuentes").set_defaults(func=cmd_doctor)
    sub.add_parser("estado", parents=[common], help="resumen del registro de investigación").set_defaults(func=cmd_estado)

    s = sub.add_parser("buscar", parents=[common], help="busca literatura")
    s.add_argument("fuente", choices=["pubmed", "europepmc", "openalex", "efsa"])
    s.add_argument("consulta")
    s.add_argument("--max", type=int, default=20)
    s.add_argument("--desde", help="año inicial")
    s.add_argument("--hasta", help="año final")
    s.add_argument("--resumenes", action="store_true", help="(PubMed) descarga resúmenes y MeSH")
    s.add_argument("--registrar", action="store_true", help="registra los resultados como fuentes SIN verificar")
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
    s.set_defaults(func=cmd_patentes)

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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config()
    if args.offline:
        cfg.offline = True
    cfg.actor = args.agente
    try:
        return int(args.func(args, cfg) or 0)
    except registry.RegistryError as exc:
        print(f"Error de registro: {exc}", file=sys.stderr)
        return 3
    except NetworkError as exc:
        print(f"Error de red: {exc}", file=sys.stderr)
        return 4
    except (ValueError, KeyError, FileNotFoundError, TypeError, ZeroDivisionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"Error de la base de datos: {exc}", file=sys.stderr)
        return 3
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
