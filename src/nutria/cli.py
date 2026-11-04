"""Interfaz de línea de órdenes `nutria`: la API que usan los agentes y las personas.

Ejecuta `nutria --help` o `nutria <orden> --help`. Añade `--json` para salida legible por máquina
y `--agente <nombre>` (o la variable NUTRIA_AGENT) para atribuir la acción en la procedencia.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from importlib import resources
from pathlib import Path

from . import __version__
from . import db as registry
from .config import CONFIG_FILENAME, DEFAULT_CONFIG_TEXT, load_config
from .http import HttpClient, NetworkError
from .provenance import environment_snapshot, log_event, read_events

SCHEMAS = ("oportunidad", "hipotesis", "extraccion", "afirmacion", "puntuacion")


# ============================================================================ utilidades
def _emit(args, data, text: str | None = None) -> None:
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print(text if text is not None else json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _load_json(path: str) -> dict:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text("utf-8")
    return json.loads(raw)


def _validate(kind: str, data: dict) -> None:
    import jsonschema
    schema = json.loads(resources.files("nutria.data").joinpath(f"schemas/{kind}.schema.json").read_text("utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(f"{'/'.join(map(str, e.path)) or '(raíz)'}: {e.message}" for e in errors[:8])
        raise SystemExit(f"JSON no válido para '{kind}': {detail}")


def _conn(cfg):
    if not cfg.db_path.is_file():
        raise SystemExit(f"No existe la base de datos ({cfg.db_path}). Ejecuta `nutria init` en la raíz del proyecto.")
    return registry.connect(cfg.db_path)


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
    print(f"Proyecto inicializado en {root}\n  configuración: {conf}\n  base de datos: {cfg.db_path}")
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
              "base_datos": cfg.db_path.is_file(), "entorno": environment_snapshot(cfg.root), "red": {}}
    client = HttpClient(cfg)
    for name, url in HOSTS.items():
        try:
            resp = client.session.get(url, timeout=15)
            report["red"][name] = f"OK ({resp.status_code})" if resp.status_code < 400 else f"HTTP {resp.status_code}"
        except requests.exceptions.ProxyError:
            report["red"][name] = "BLOQUEADO por proxy/política de red"
        except requests.RequestException as exc:
            report["red"][name] = f"sin conexión ({type(exc).__name__})"
    lines = [f"nutria {__version__} | raíz: {cfg.root}",
             f"  nutria.toml: {'sí' if report['config'] else 'NO (ejecuta nutria init)'} | "
             f"base de datos: {'sí' if report['base_datos'] else 'NO'}",
             "  entorno: " + ", ".join(f"{k}={v}" for k, v in report["entorno"].items() if v),
             "  conectividad:"] + [f"    {k:<28} {v}" for k, v in report["red"].items()]
    if any(not v.startswith("OK") for v in report["red"].values()):
        lines.append("  Algunas fuentes no son accesibles: permite esos dominios en la red o trabaja con --offline "
                     "(solo usará la caché).")
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
             "Oportunidades: " + (", ".join(f"{r['status']}={r['n']}" for r in data["oportunidades"]) or "0")]
    for o in opps:
        lines.append(f"  {o['id']} [{o['status']}] {o['title']}")
    _emit(args, {**data, "lista_oportunidades": opps}, "\n".join(lines))
    return 0


# ============================================================================ literatura
def cmd_buscar(args, cfg) -> int:
    from .sources import europepmc, openalex, pubmed
    client = HttpClient(cfg)
    if args.fuente == "pubmed":
        res = pubmed.search_records(client, args.consulta, retmax=args.max, mindate=args.desde, maxdate=args.hasta,
                                    with_abstracts=args.resumenes)
    elif args.fuente == "europepmc":
        q = args.consulta
        if args.desde or args.hasta:
            q += f" AND PUB_YEAR:[{args.desde or 1800} TO {args.hasta or 3000}]"
        res = europepmc.search(client, q, page_size=args.max)
    elif args.fuente == "efsa":
        res = europepmc.efsa_journal(client, args.consulta, page_size=args.max)
    else:
        res = openalex.search(client, args.consulta, per_page=args.max,
                              from_year=int(args.desde) if args.desde else None,
                              to_year=int(args.hasta) if args.hasta else None)
    records = res["records"]
    registered = []
    if args.registrar and records:
        conn = _conn(cfg)
        for rec in records:
            if rec.key:
                registered.append(registry.upsert_source(conn, rec.to_dict(), args.agente))
    log_event(cfg, "busqueda", inputs={"fuente": args.fuente, "consulta": args.consulta, "max": args.max,
                                       "desde": args.desde, "hasta": args.hasta},
              outputs={"total": res.get("count"), "devueltos": len(records), "registrados": len(registered)},
              actor=args.agente)
    header = f"{args.fuente}: {res.get('count', 0)} resultados totales; mostrando {len(records)}"
    if res.get("query_translation"):
        header += f"\nTraducción de la consulta: {res['query_translation']}"
    if registered:
        header += f"\nRegistradas {len(registered)} fuentes (pendientes de verificación: `nutria verificar --pendientes`)"
    _emit(args, {"total": res.get("count"), "registros": [r.to_dict() for r in records], "registradas": registered},
          header + "\n" + _records_text(records))
    return 0


def cmd_fuente(args, cfg) -> int:
    from .verify.citations import verify_doi, verify_pmid
    conn = _conn(cfg)
    if args.accion == "listar":
        rows = registry.list_sources(conn, unverified_only=args.no_verificadas)
        text = "\n".join(f"{r['id']:<45} {'✓' if r['verified'] else '·'} {'RETRACTADO ' if r.get('retracted') else ''}"
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
    from .sources import crossref, pubmed
    client = HttpClient(cfg)
    if args.doi:
        ver = verify_doi(client, args.doi)
        if ver.status != "verificada":
            raise SystemExit(f"No se registra: {ver.status} ({ver.message})")
        rec = crossref.work(client, ver.identifier).to_dict()
        rec["retracted"] = ver.retracted
        rec["verified_via"] = "crossref"
    elif args.pmid:
        ver = verify_pmid(client, args.pmid)
        if ver.status != "verificada":
            raise SystemExit(f"No se registra: {ver.status} ({ver.message})")
        rec = pubmed.summaries(client, [args.pmid])[0].to_dict()
        rec["verified_via"] = "pubmed"
    else:
        raise SystemExit("Indica --doi o --pmid")
    rec.update(verified=True, study_type=args.tipo_estudio, species=args.especie, notes=args.notas)
    source_id = registry.upsert_source(conn, rec, args.agente)
    log_event(cfg, "fuente_agregada", inputs={"doi": args.doi, "pmid": args.pmid}, outputs={"id": source_id},
              actor=args.agente)
    warn = "  ATENCIÓN: trabajo RETRACTADO" if rec.get("retracted") else ""
    _emit(args, {"id": source_id, "registro": rec}, f"Registrada y verificada: {source_id}{warn}\n  {rec.get('title')}")
    return 0


def cmd_verificar(args, cfg) -> int:
    from .verify.citations import verify_doi, verify_pmid
    client = HttpClient(cfg)
    if args.doi or args.pmid:
        res = verify_doi(client, args.doi, args.cita) if args.doi else verify_pmid(client, args.pmid, args.cita)
        data = res.to_dict()
        text = (f"{res.kind.upper()} {res.identifier}: {res.status}" + (" | RETRACTADO" if res.retracted else "")
                + (f" | DISCREPANCIA con la cita" if res.mismatch else "")
                + (f"\n  {res.title} ({res.year}) {res.journal or ''}" if res.title else "")
                + (f"\n  {res.message}" if res.message else ""))
        _emit(args, data, text)
        return 0 if res.ok else 2
    if args.pendientes:
        conn = _conn(cfg)
        rows = registry.list_sources(conn, unverified_only=True)
        out = []
        for r in rows:
            res = verify_doi(client, r["doi"]) if r.get("doi") else (verify_pmid(client, r["pmid"]) if r.get("pmid") else None)
            if res is None:
                continue
            if res.status == "verificada":
                registry.upsert_source(conn, {"doi": r.get("doi"), "pmid": r.get("pmid"), "verified": True,
                                              "verified_via": res.kind, "retracted": res.retracted}, args.agente)
            out.append({"id": r["id"], "estado": res.status, "retractado": res.retracted})
        _emit(args, out, "\n".join(f"{o['id']}: {o['estado']}{' RETRACTADO' if o['retractado'] else ''}" for o in out)
              or "No hay fuentes pendientes de verificar")
        return 0
    if not args.archivo:
        raise SystemExit("Indica --doi, --pmid, --pendientes o un archivo .md")
    args.sin_secciones = True  # verificar comprueba referencias; la estructura de informe la exige `informe comprobar`
    return _check_report_file(args, cfg, Path(args.archivo), online=True)


# ============================================================================ moléculas, proteínas, redes
def cmd_molecula(args, cfg) -> int:
    client = HttpClient(cfg)
    if args.fuente == "pubchem":
        from .sources import pubchem
        props = pubchem.compound_by_name(client, args.nombre)
        if args.peligros:
            for p in props[:3]:
                p["GHS_frases_H"] = pubchem.ghs_hazards(client, p["CID"])
        _emit(args, props)
    else:
        from .sources import chembl
        mols = chembl.molecule_search(client, args.nombre, limit=args.max)
        if args.mecanismos:
            for m in mols[:3]:
                m["mecanismos"] = chembl.mechanisms(client, m["chembl_id"])
        _emit(args, mols)
    return 0


def cmd_diana(args, cfg) -> int:
    from .sources import chembl
    client = HttpClient(cfg)
    targets = chembl.target_search(client, args.consulta, organism=args.organismo, limit=args.max)
    if args.actividades and targets:
        for t in targets[:2]:
            t["actividades"] = chembl.activities(client, target_chembl_id=t["target_chembl_id"], limit=25)
    _emit(args, targets)
    return 0


def cmd_proteina(args, cfg) -> int:
    from .sources import uniprot
    from .sources.base import taxon_id
    client = HttpClient(cfg)
    rows = uniprot.search(client, args.consulta, taxon=taxon_id(args.especie) if args.especie else None,
                          size=args.max, reviewed_only=args.revisadas)
    text = "\n".join(f"{r['accesion']:<12} {','.join(r['genes'])[:20]:<20} {r['organismo'] or '':<18} "
                     f"{'rev' if r['revisada'] else 'no rev'}  {r['proteina'] or ''}" for r in rows) or "(sin resultados)"
    _emit(args, rows, text)
    return 0


def cmd_red(args, cfg) -> int:
    from .sources import stringdb
    from .sources.base import taxon_id
    client = HttpClient(cfg)
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
    client = HttpClient(cfg)
    if args.accion == "buscar":
        _emit(args, reactome.search(client, " ".join(args.terminos), species=args.especie))
    else:
        _emit(args, reactome.analyze_genes(client, args.terminos))
    return 0


def cmd_patentes(args, cfg) -> int:
    from .sources import patents
    data = {"enlaces": patents.search_links(args.consulta)}
    if args.lens:
        data["lens"] = patents.lens_search(HttpClient(cfg), args.consulta, size=args.max)
    log_event(cfg, "busqueda_patentes", inputs={"consulta": args.consulta}, actor=args.agente)
    _emit(args, data)
    return 0


# ============================================================================ registro de evidencia
def cmd_extraccion(args, cfg) -> int:
    conn = _conn(cfg)
    if args.accion == "agregar":
        data = _load_json(args.json_file)
        items = data if isinstance(data, list) else [data]
        ids = []
        for item in items:
            _validate("extraccion", item)
            ids.append(registry.add_extraction(conn, item, args.agente))
        _emit(args, {"ids": ids}, f"Extracciones registradas: {ids}")
        return 0
    rows = registry.list_extractions(conn, source_id=args.fuente, outcome=args.variable)
    if args.accion == "exportar":
        if not rows:
            raise SystemExit("No hay extracciones con esos filtros")
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"Exportadas {len(rows)} filas a {args.csv}")
        return 0
    _emit(args, rows, "\n".join(f"#{r['id']} {r['source_id']} | {r['species']} | {r['intervention']} | {r['outcome']} "
                                f"({r['outcome_unit']}) | {r['check_status']}" for r in rows) or "(sin extracciones)")
    return 0


def cmd_afirmacion(args, cfg) -> int:
    conn = _conn(cfg)
    if args.accion == "agregar":
        data = _load_json(args.json_file)
        items = data if isinstance(data, list) else [data]
        ids = []
        for item in items:
            _validate("afirmacion", item)
            ids.append(registry.add_claim(conn, item, args.agente))
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
        ids = []
        for item in items:
            _validate(kind, item)
            ids.append(add(conn, item, args.agente))
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
            if not getattr(args, "sin_puntuaciones", False):
                row["puntuaciones"] = registry.list_scores(conn, args.id)
            row["hipotesis"] = registry.list_hypotheses(conn, opportunity_id=args.id)
        _emit(args, row)
    else:
        set_status(conn, args.id, args.nuevo_estado, args.motivo, args.agente, args.firma_humana)
        print(f"{args.id} -> {args.nuevo_estado}")
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
        log_event(cfg, "puntuacion", inputs={"oportunidad": data["oportunidad"], "evaluador": data["evaluador"]},
                  outputs={"criterios": n}, actor=args.agente)
        print(f"{n} puntuaciones registradas para {data['oportunidad']} (evaluador {data['evaluador']})")
        return 0
    if args.accion == "compuerta":
        registry.set_gate(conn, args.id, args.compuerta, args.estado, args.motivo, rubric.gate_ids,
                          evidence=args.evidencia, actor=args.agente)
        print(f"{args.id}: compuerta {args.compuerta} -> {args.estado}")
        return 0
    # ranking
    profile = args.perfil or cfg.scoring_profile
    opps = []
    for o in registry.list_opportunities(conn):
        if o["status"] == "descartada":
            continue
        scores = [{"rater": s["rater"], "criterion": s["criterion"], "value": s["value"], "low": s["low"],
                   "high": s["high"]} for s in registry.list_scores(conn, o["id"])]
        gates = {g["gate"]: g["status"] for g in registry.list_gates(conn, o["id"])}
        opps.append({"id": o["id"], "title": o["title"], "scores": scores, "gates": gates})
    rows = rank(opps, rubric, profile=profile, n_sim=args.sim, seed=args.semilla, alpha=args.alfa, top_k=args.top)
    log_event(cfg, "ranking", inputs={"perfil": profile, "sim": args.sim, "semilla": args.semilla, "alfa": args.alfa},
              outputs={"orden": [r.opportunity_id for r in rows]}, actor=args.agente)
    _emit(args, [r.to_dict() for r in rows],
          f"Perfil: {profile} | {args.sim} simulaciones | semilla {args.semilla} | alfa {args.alfa}\n"
          + format_table(rows, args.top))
    return 0


def cmd_decision(args, cfg) -> int:
    conn = _conn(cfg)
    if args.accion == "registrar":
        registry.add_decision(conn, args.entidad, args.decision, args.motivo, args.agente, args.firma_humana)
        print("Decisión registrada")
        return 0
    rows = registry.list_decisions(conn, args.entidad)
    _emit(args, rows, "\n".join(f"{r['ts']} {r['entity_id'] or '-':<8} {r['decision']} | {r['rationale']} | "
                                f"{r['decided_by']}{' | firma: ' + r['human_signoff'] if r['human_signoff'] else ''}"
                                for r in rows) or "(sin decisiones)")
    return 0


# ============================================================================ estadística
def _read_table(path: str, sep: str, decimal: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh, delimiter=sep))
    if decimal != ".":
        for r in rows:
            for k, v in r.items():
                if isinstance(v, str):
                    r[k] = v.replace(".", "").replace(decimal, ".") if _looks_numeric(v, decimal) else v
    return rows


def _looks_numeric(v: str, decimal: str) -> bool:
    try:
        float(v.replace(".", "").replace(decimal, "."))
        return True
    except ValueError:
        return False


def _effects_from_rows(rows: list[dict], measure: str) -> tuple[list[float], list[float], list[str]]:
    from .stats.effect_sizes import effect_from_summary
    yi, vi, labels = [], [], []
    for i, r in enumerate(rows, 1):
        label = r.get("estudio") or r.get("study") or str(i)
        if r.get("yi") not in (None, "") and r.get("vi") not in (None, ""):
            y, v = float(r["yi"]), float(r["vi"])
        else:
            def num(key):
                val = r.get(key)
                return float(val) if val not in (None, "") else None
            y, v = effect_from_summary(
                measure, num("media_trt"), num("media_ctrl"), num("n_trt"), num("n_ctrl"),
                r.get("tipo_disp") or "SD", num("disp_trt"), num("disp_ctrl"))
        yi.append(y)
        vi.append(v)
        labels.append(label)
    return yi, vi, labels


def cmd_meta(args, cfg) -> int:
    from .stats import meta as m
    rows = _read_table(args.csv, args.sep, args.decimal)
    if args.accion == "stpierre":
        from .stats.mixed import st_pierre
        res = st_pierre(rows, args.respuesta, args.dosis, args.estudio, args.covariable, args.pendiente_aleatoria)
        log_event(cfg, "meta_stpierre", inputs={"csv": args.csv, "respuesta": args.respuesta, "dosis": args.dosis},
                  outputs=res, actor=args.agente)
        _emit(args, res)
        return 0
    if args.accion == "exportar-r":
        script = _r_script(args)
        Path(args.salida).write_text(script, "utf-8")
        print(f"Script de R (metafor) escrito en {args.salida}")
        return 0
    yi, vi, labels = _effects_from_rows(rows, args.medida)
    out: dict = {"medida": args.medida, "k": len(yi), "estudios": labels}
    text = []
    if args.metodo == "FE":
        res = m.fixed_effect(yi, vi)
    else:
        res = m.random_effects(yi, vi, method=args.metodo, hksj=args.hksj)
    out["modelo"] = res.to_dict()
    text.append(res.summary())
    if len(yi) >= 3:
        out["egger"] = m.egger_test(yi, vi)
        eg = out["egger"]
        text.append(f"Egger: intercepto {eg['intercepto']:.3f} (EE {eg['ee']:.3f}), p = {eg['p']:.3g}"
                    + (f" — {eg['nota']}" if eg.get("nota") else ""))
        out["uno_fuera"] = m.leave_one_out(yi, vi, labels, method=args.metodo if args.metodo != "FE" else "REML",
                                           hksj=args.hksj)
        lo = min(out["uno_fuera"], key=lambda r: r["estimacion"])
        hi = max(out["uno_fuera"], key=lambda r: r["estimacion"])
        text.append(f"Uno fuera: estimación entre {lo['estimacion']:.4f} (sin {lo['excluido']}) y "
                    f"{hi['estimacion']:.4f} (sin {hi['excluido']})")
    if args.subgrupo:
        groups = [r.get(args.subgrupo, "") for r in rows]
        out["subgrupos"] = m.subgroups(yi, vi, groups, method=args.metodo if args.metodo != "FE" else "REML",
                                       hksj=args.hksj)
        sg = out["subgrupos"]
        for g, r in sg["subgrupos"].items():
            text.append(f"  subgrupo {g}: k={r['k']}, estimación {r['estimate']:.4f} [{r['ci_low']:.4f}; {r['ci_high']:.4f}]")
        if sg.get("Q_entre") is not None:
            text.append(f"  Q entre subgrupos = {sg['Q_entre']:.3f} (gl {sg['gl']}), p = {sg['p']:.3g}. {sg['nota']}")
    if args.moderador:
        from .stats.metareg import meta_regression
        mods = [[float(r[c]) for c in args.moderador] for r in rows]
        reg = meta_regression(yi, vi, mods, args.moderador, method=args.metodo if args.metodo != "FE" else "REML",
                              knha=args.hksj)
        out["meta_regresion"] = reg.to_dict()
        text.append(reg.summary())
    log_event(cfg, "metaanalisis", inputs={"csv": args.csv, "medida": args.medida, "metodo": args.metodo,
                                           "hksj": args.hksj, "moderadores": args.moderador, "subgrupo": args.subgrupo},
              outputs={"estimacion": res.estimate, "ic": [res.ci_low, res.ci_high], "tau2": res.tau2, "k": res.k},
              actor=args.agente)
    _emit(args, out, "\n".join(text))
    return 0


def _r_script(args) -> str:
    measure = {"MD": "MD", "SMD": "SMD", "ROM": "ROM"}[args.medida]
    return f"""# Script generado por nutria {__version__}. Revísalo antes de usarlo.
# Requiere: install.packages(c("metafor", "clubSandwich"))
library(metafor)
dat <- read.csv("{args.csv}", sep = "{args.sep}", dec = "{args.decimal}")
# Si el CSV trae medias y dispersiones, calcula yi/vi (convierte antes SEM/SED a DE: ver nutria.stats.effect_sizes)
if (!"yi" %in% names(dat)) {{
  dat <- escalc(measure = "{measure}", m1i = media_trt, sd1i = disp_trt, n1i = n_trt,
                m2i = media_ctrl, sd2i = disp_ctrl, n2i = n_ctrl, data = dat)
}}
# 1) Efectos aleatorios con ajuste de Knapp-Hartung
res <- rma(yi, vi, data = dat, method = "REML", test = "knha")
print(res); print(predict(res))
# 2) Dependencia: varios tratamientos frente al mismo control o varias variables por estudio.
#    Necesita columnas 'estudio' y 'comparacion' (identificador único de cada fila).
if (all(c("estudio", "comparacion") %in% names(dat))) {{
  res_ml <- rma.mv(yi, vi, random = ~ 1 | estudio/comparacion, data = dat, method = "REML")
  print(res_ml)
  print(robust(res_ml, cluster = dat$estudio, clubSandwich = TRUE))
}}
# 3) Sensibilidad y sesgo de publicación (con k < 10 interpreta con mucha cautela)
print(leave1out(res)); print(regtest(res)); funnel(res)
"""


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


# ============================================================================ economía
def cmd_econ(args, cfg) -> int:
    if args.accion == "ganadero":
        from .econ import farm
        model = farm.load_farm_model(Path(args.archivo))
        data = {"escenarios": farm.scenarios(model), "monte_carlo": farm.monte_carlo(model, args.sim, args.semilla)}
        text = farm.report(model, args.sim, args.semilla)
    else:
        from .econ import rnpv
        project = rnpv.load_project(Path(args.archivo))
        data = rnpv.evaluate(project, args.sim, args.semilla)
        text = rnpv.report(project, args.sim, args.semilla)
    log_event(cfg, f"econ_{args.accion}", inputs={"archivo": args.archivo, "sim": args.sim, "semilla": args.semilla},
              outputs={"resumen": data if args.accion != "ganadero" else data["monte_carlo"]}, actor=args.agente)
    _emit(args, data, text)
    return 0


# ============================================================================ informes
def _check_report_file(args, cfg, path: Path, online: bool) -> int:
    from .reports.check import check_report, check_report_online, write_stamp
    text = path.read_text("utf-8")
    issues = check_report(text, require_sections=not getattr(args, "sin_secciones", False))
    details: list[dict] = []
    if online:
        more, details = check_report_online(HttpClient(cfg), text)
        issues += more
    errors = [i for i in issues if i.severity == "error"]
    if getattr(args, "sellar", False):
        stamp = write_stamp(path, issues, online, details)
        log_event(cfg, "sello_informe", inputs={"informe": str(path)},
                  outputs={"sha256": stamp["sha256"], "errores": stamp["errores"]}, actor=args.agente)
    lines = [str(i) for i in issues] or ["Sin incidencias"]
    lines.append(f"Resultado: {len(errors)} error(es), {len(issues) - len(errors)} aviso(s)"
                 + (" | referencias verificadas en línea" if online else " | sin verificación en línea"))
    _emit(args, {"incidencias": [i.__dict__ for i in issues], "referencias": details}, "\n".join(lines))
    return 1 if errors else 0


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
        print(f"Esqueleto de informe creado: {out}")
        return 0
    if args.accion == "sello":
        from .reports.check import stamp_status
        ok, msg = stamp_status(Path(args.archivo))
        print(msg)
        return 0 if ok else 1
    return _check_report_file(args, cfg, Path(args.archivo), online=args.online)


def cmd_procedencia(args, cfg) -> int:
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
                        help="usar solo la caché, sin red")

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
    sub.add_parser("doctor", parents=[common], help="comprueba entorno y acceso a las fuentes").set_defaults(func=cmd_doctor)
    sub.add_parser("estado", parents=[common], help="resumen del registro de investigación").set_defaults(func=cmd_estado)

    s = sub.add_parser("buscar", parents=[common], help="busca literatura")
    s.add_argument("fuente", choices=["pubmed", "europepmc", "openalex", "efsa"])
    s.add_argument("consulta")
    s.add_argument("--max", type=int, default=20)
    s.add_argument("--desde", help="año inicial")
    s.add_argument("--hasta", help="año final")
    s.add_argument("--resumenes", action="store_true", help="(PubMed) descarga resúmenes y MeSH")
    s.add_argument("--registrar", action="store_true", help="registra los resultados como fuentes (sin verificar)")
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
    l = fs.add_parser("listar", parents=[common])
    l.add_argument("--no-verificadas", dest="no_verificadas", action="store_true")
    v = fs.add_parser("ver", parents=[common])
    v.add_argument("id")
    s.set_defaults(func=cmd_fuente)

    s = sub.add_parser("verificar", parents=[common], help="verifica DOI/PMID o las referencias de un informe")
    s.add_argument("archivo", nargs="?")
    s.add_argument("--doi")
    s.add_argument("--pmid")
    s.add_argument("--cita", help="texto de la cita para comprobar que el identificador corresponde")
    s.add_argument("--pendientes", action="store_true", help="verifica las fuentes registradas sin verificar")
    s.add_argument("--sellar", action="store_true")
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
    s.add_argument("--organismo", default=None, help='p. ej. "Bos taurus"')
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
        s = sub.add_parser(kind, parents=[common], help=f"registro de {kind}es" if kind == "oportunidad" else "registro de hipótesis")
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
        x.add_argument("--firma-humana", dest="firma_humana", help="nombre de la persona que valida la decisión")
        s.set_defaults(func=func)

    s = sub.add_parser("puntuar", parents=[common], help="priorización con rúbrica y compuertas")
    ps = s.add_subparsers(dest="accion", required=True)
    a = ps.add_parser("registrar", parents=[common])
    a.add_argument("--datos", dest="json_file", required=True, help="fichero JSON (o - para stdin)")
    c = ps.add_parser("compuerta", parents=[common])
    c.add_argument("id")
    c.add_argument("compuerta")
    c.add_argument("estado", choices=registry.GATE_STATUSES)
    c.add_argument("--motivo", required=True)
    c.add_argument("--evidencia", nargs="*")
    r = ps.add_parser("ranking", parents=[common])
    r.add_argument("--perfil")
    r.add_argument("--sim", type=int, default=5000)
    r.add_argument("--semilla", type=int, default=42)
    r.add_argument("--alfa", type=float, default=200.0, help="concentración Dirichlet de los pesos (0 = fijos)")
    r.add_argument("--top", type=int, default=3)
    rb = ps.add_parser("rubrica", parents=[common])
    rb.add_argument("--markdown", action="store_true")
    s.set_defaults(func=cmd_puntuar)

    s = sub.add_parser("decision", parents=[common], help="registro de decisiones")
    ds = s.add_subparsers(dest="accion", required=True)
    a = ds.add_parser("registrar", parents=[common])
    a.add_argument("--entidad")
    a.add_argument("--decision", required=True)
    a.add_argument("--motivo", required=True)
    a.add_argument("--firma-humana", dest="firma_humana")
    x = ds.add_parser("listar", parents=[common])
    x.add_argument("--entidad")
    s.set_defaults(func=cmd_decision)

    s = sub.add_parser("meta", parents=[common], help="metaanálisis")
    ms = s.add_subparsers(dest="accion", required=True)
    for name in ("analizar", "stpierre", "exportar-r"):
        x = ms.add_parser(name, parents=[common])
        x.add_argument("csv")
        x.add_argument("--sep", default=",")
        x.add_argument("--decimal", default=".")
        if name in ("analizar", "exportar-r"):
            x.add_argument("--medida", choices=["MD", "SMD", "ROM"], default="MD")
        if name == "analizar":
            x.add_argument("--metodo", choices=["REML", "DL", "PM", "FE"], default="REML")
            x.add_argument("--hksj", action="store_true", help="ajuste de Hartung-Knapp-Sidik-Jonkman")
            x.add_argument("--moderador", nargs="*", help="columnas numéricas para meta-regresión")
            x.add_argument("--subgrupo", help="columna para análisis por subgrupos")
        if name == "stpierre":
            x.add_argument("--respuesta", required=True)
            x.add_argument("--dosis", required=True)
            x.add_argument("--estudio", required=True)
            x.add_argument("--covariable", nargs="*", default=[])
            x.add_argument("--pendiente-aleatoria", dest="pendiente_aleatoria", action="store_true")
        if name == "exportar-r":
            x.add_argument("--salida", default="metaanalisis.R")
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
    x.add_argument("--online", action="store_true", help="verifica DOIs/PMIDs contra Crossref, OpenAlex y PubMed")
    x.add_argument("--sellar", action="store_true", help="guarda el sello de verificación")
    x.add_argument("--sin-secciones", dest="sin_secciones", action="store_true",
                   help="no exige las 20 secciones (planes, notas)")
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
    x.add_argument("--sensibilidad", choices=["normal", "confidencial"], default="normal")
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
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
