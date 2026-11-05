"""Verificación de citas y registro de fuentes (paquete P3): correspondencia identificador-cita (D3-7), existencia
de DOI en doi.org y DataCite (D3-15), DOI SICI (D7-17), retractación y campos protegidos frente a búsquedas sin
verificar (D9-3), fusión segura de registros (D9-4 a), licencias (D7-16) y casos de fallo de la verificación de
citas (D3-16: año dentro del DOI, retractación desconocida, referencias de solo URL, identificadores repetidos).

Sin red: se sustituyen las funciones de los clientes (Crossref, OpenAlex, PubMed, doi.org, DataCite). Los DOI usan el
prefijo de pruebas 10.5555 y los trabajos son ficticios.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from nutria import db
from nutria.cli import main
from nutria.config import Config, load_config
from nutria.http import NetworkError, NotFoundError
from nutria.provenance import read_events
from nutria.reports.check import check_report_online
from nutria.sources import crossref, doiorg, openalex, pubmed
from nutria.sources.base import Record
from nutria.verify import citations as c

EXAMPLES = Path(__file__).resolve().parents[1] / "ejemplos"
SICI = "10.1002/(SICI)1097-0010(199709)75:1<1::AID-JSFA834>3.0.CO;2-#"

TITLE = "Effects of rumen-protected choline on milk production and liver health in transition dairy cows"
# Otro trabajo del mismo grupo, tema, primer autor, año y revista: solo cambia el compuesto.
SAME_TOPIC = "Effects of rumen-protected methionine on milk production and liver health in transition dairy cows"
AUTHORS = [("Pérez", "Juan"), ("García", "Ana"), ("López", "Luis")]
DOI = "10.5555/jds.2019-17123"          # el año del DOI (2019) no es el de la publicación (2020)


# ============================================================================ red simulada
def cr_msg(doi, title=TITLE, authors=AUTHORS, year=2020, journal="Journal of Dairy Science",
           short="J. Dairy Sci.", kind="journal-article", **extra):
    msg = {"DOI": doi, "title": [title], "author": [{"family": f, "given": g} for f, g in authors],
           "issued": {"date-parts": [[year]]}, "container-title": [journal] if journal else [],
           "short-container-title": [short] if short else [], "type": kind, "volume": "103", "page": "1-10"}
    msg.update(extra)
    return msg


def oa_work(doi, title=TITLE, names=("Juan Pérez", "Ana García"), year=2020, retracted=False, pmid=None,
            journal="Journal of Dairy Science", license_=None, wid="W1"):
    work = {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/{doi}", "display_name": title,
            "publication_year": year, "is_retracted": retracted, "type": "article",
            "authorships": [{"author": {"display_name": n}} for n in names], "ids": {},
            "primary_location": {"source": {"display_name": journal}, "license": license_}}
    if pmid:
        work["ids"]["pmid"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}"
    return openalex.parse(work)


def pm_rec(pmid, doi=None, title=TITLE, authors=("Pérez J", "García A"), year=2020, retracted=False):
    return Record(source="pubmed", pmid=pmid, doi=doi, title=title, authors=list(authors), year=year,
                  journal="Journal of dairy science", pub_types=["Journal Article"], retracted=retracted)


class FakeNet:
    """Respuestas programadas por identificador; registra cada consulta (fuente, identificador)."""

    def __init__(self):
        self.cr, self.oa, self.pm, self.pm_by_doi, self.dc = {}, {}, {}, {}, {}
        self.handles, self.agency = set(), {}
        self.oa_key = True
        self.doiorg_down = False
        self.calls = []

    def crossref_work(self, client, doi):
        self.calls.append(("crossref", doi))
        if doi in self.cr:
            return crossref.parse(self.cr[doi])
        raise NotFoundError(f"No encontrado (404): crossref {doi}")

    def openalex_work(self, client, doi):
        self.calls.append(("openalex", doi))
        if not self.oa_key:
            raise NetworkError(openalex.KEY_REQUIRED_MESSAGE)
        if doi in self.oa:
            return self.oa[doi]
        raise NotFoundError(f"No encontrado (404): openalex {doi}")

    def pmid_for_doi(self, client, doi):
        self.calls.append(("pubmed_doi", doi))
        return self.pm_by_doi.get(doi)

    def summaries(self, client, pmids):
        self.calls.append(("pubmed", tuple(pmids)))
        return [self.pm[p] for p in pmids if p in self.pm]

    def handle_exists(self, client, doi):
        self.calls.append(("doi.org", doi))
        if self.doiorg_down:
            raise NetworkError("Sin respuesta válida de doi.org tras 4 intentos")
        return doi in self.handles

    def registration_agency(self, client, doi):
        self.calls.append(("doiRA", doi))
        return self.agency.get(doi)

    def datacite_work(self, client, doi):
        self.calls.append(("datacite", doi))
        if doi in self.dc:
            return doiorg.parse_datacite(self.dc[doi])
        raise NotFoundError(f"No encontrado (404): datacite {doi}")

    def sources(self):
        return [name for name, _ in self.calls]


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(crossref, "work", fake.crossref_work)
    monkeypatch.setattr(openalex, "work_by_doi", fake.openalex_work)
    monkeypatch.setattr(pubmed, "pmid_for_doi", fake.pmid_for_doi)
    monkeypatch.setattr(pubmed, "summaries", fake.summaries)
    monkeypatch.setattr(doiorg, "handle_exists", fake.handle_exists)
    monkeypatch.setattr(doiorg, "registration_agency", fake.registration_agency)
    monkeypatch.setattr(doiorg, "datacite_work", fake.datacite_work)
    return fake


def published(net, doi=DOI, pmid=None, retracted=False, **msg):
    """Trabajo en Crossref, con la retractación comprobada en OpenAlex (y PubMed si tiene PMID)."""
    net.cr[doi] = cr_msg(doi, **msg)
    net.oa[doi] = oa_work(doi, title=msg.get("title", TITLE), retracted=retracted, pmid=pmid,
                          year=msg.get("year", 2020))
    if pmid:
        net.pm_by_doi[doi] = pmid
        net.pm[pmid] = pm_rec(pmid, doi, title=msg.get("title", TITLE), year=msg.get("year", 2020),
                              retracted=retracted)


class Client:
    cfg = None


def report(*entries):
    bib = "\n".join(f"- [R{i}] {e}" for i, e in enumerate(entries, 1))
    return f"# Informe\n\nTexto [PUB] [R1].\n\n## 20. Bibliografía\n\n{bib}\n"


def _conn():
    return db.connect(load_config().db_path)


def _example(name: str, fallback: dict) -> dict:
    """Ejemplo de `ejemplos/` (el primero si es una lista) o, si este repositorio aún no lo tiene, `fallback`."""
    path = EXAMPLES / name
    if not path.is_file():
        return dict(fallback)
    data = json.loads(path.read_text("utf-8"))
    return dict(data[0] if isinstance(data, list) else data)


def claim_file(project, sources, label="PUB") -> Path:
    """Afirmación del ejemplo de `ejemplos/` con otras fuentes (y la oportunidad a la que se refiere)."""
    assert main(["oportunidad", "nueva", "--datos", str(EXAMPLES / "oportunidad_ejemplo.json")]) == 0
    claim = _example("afirmacion_ejemplo.json", {
        "texto": "En vacas en transición, el suplemento redujo el BHB en sangre", "etiqueta": "PUB",
        "cita_textual": "BHB decreased in supplemented cows", "ubicacion": "Tabla 2", "entidad": "O-0001"})
    claim.update(etiqueta=label, fuentes=list(sources))
    path = project / "afirmacion.json"
    path.write_text(json.dumps(claim, ensure_ascii=False), "utf-8")
    return path


def add_claim(project, sources, label="PUB") -> str:
    assert main(["afirmacion", "agregar", "--datos", str(claim_file(project, sources, label))]) == 0
    return db.list_claims(_conn())[-1]["id"]


def add_extraction(project, source) -> None:
    data = _example("extraccion_ejemplo.json", {
        "especie": "vacuno lechero", "estado_fisiologico": "inicio de lactación", "dieta_basal": "TMR",
        "diseno": "paralelo", "unidad_experimental": "animal", "intervencion": "colina protegida", "dosis": 50,
        "dosis_unidad": "g", "dosis_base": "g/animal/d", "forma_producto": "protegido", "variable": "leche",
        "unidad": "kg/d", "n_control": 12, "n_tratamiento": 12, "media_control": 30.0, "media_tratamiento": 31.0,
        "tipo_dispersion": "SEM_combinado", "dispersion_tratamiento": 0.5, "ubicacion": "Tabla 2"})
    data["fuente"] = source
    path = project / "extraccion.json"
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    assert main(["extraccion", "agregar", "--datos", str(path)]) == 0


def source(sid):
    return db.get_source(_conn(), sid)


def run(argv) -> int:
    """Código de salida de una orden, tanto si devuelve el código como si sale con SystemExit (texto -> 1)."""
    try:
        return main(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def events(action):
    return read_events(load_config(), action=action)


# ============================================================================ D7-17: DOI SICI y puntuación
@pytest.mark.parametrize("text, expected", [
    (f"DOI: {SICI}", SICI.lower()),
    (f"Smith J. 1997. Título. J Sci Food Agric 75:1-8. DOI: {SICI}.", SICI.lower()),
    (f"(doi:{SICI})", SICI.lower()),
    ("https://doi.org/10.1002/(SICI)1097-0010(199709)75:1%3C1::AID-JSFA834%3E3.0.CO;2-%23", SICI.lower()),
    ("10.1175/1520-0469(1998)055<0177:ACOTUO>2.0.CO;2", "10.1175/1520-0469(1998)055<0177:acotuo>2.0.co;2"),
    ("Véase 10.5555/jds.2019-17123.", "10.5555/jds.2019-17123"),
    ("(véase doi:10.5555/jds.2019-17123).", "10.5555/jds.2019-17123"),
    ("DOI 10.5555/S0022-0302(03)73992-2.", "10.5555/s0022-0302(03)73992-2"),
    ("[DOI](https://doi.org/10.5555/jds.2019-17123)", "10.5555/jds.2019-17123"),
    ("<https://doi.org/10.5555/jds.2019-17123>", "10.5555/jds.2019-17123"),
    ("doi: 10.5555/abc</a>", "10.5555/abc"),
    ("10.5555/abc\\_def", "10.5555/abc_def"),
])
def test_doi_sici_y_puntuacion_final(text, expected):
    assert c.extract_identifiers(text)["doi"] == [expected]


def test_doi_sici_viaja_codificado_a_crossref_y_doi_org():
    class Recorder:
        cfg = Config(root=Path("."))

        def __init__(self, response):
            self.response, self.urls = response, []

        def get_json(self, url, **kw):
            self.urls.append(url)
            return self.response
    doi = c.clean_doi(SICI)
    for call, response in ((crossref.work, {"message": {}}), (doiorg.handle_exists, {"responseCode": 1}),
                           (doiorg.registration_agency, [{"RA": "Crossref"}])):
        client = Recorder(response)
        call(client, doi)
        url = client.urls[0]
        assert "%3C1%3A%3Aaid-jsfa834%3E3.0.co%3B2-%23" in url and "#" not in url and "<" not in url


# ============================================================================ D3-7: correspondencia con la cita
REC = dict(titles=[TITLE], authors=["Pérez Juan", "García Ana", "López Luis"], families=["Pérez", "García", "López"],
           journals=["Journal of Dairy Science", "J. Dairy Sci."])


def _bad(check) -> bool:
    return check.title_ok is False or check.author_ok is False or check.journal_ok is False


@pytest.mark.parametrize("cite", [
    f"Pérez, J., A. García, and L. López. 2020. {TITLE}. J. Dairy Sci. 103:1–10.",                     # ADSA
    f"Pérez J, García A, López L. {TITLE}. J Dairy Sci. 2020;103(1):1-10. Epub 2019 Nov 6.",          # Vancouver
    f"Pérez, J., García, A., & López, L. (2020). {TITLE}. *Journal of Dairy Science*, *103*(1), 1–10.",  # APA
    f"Pérez, J., García, A. and López, L. (2020) '{TITLE}', Journal of Dairy Science, 103(1), pp. 1-10.",
    f"Pérez, Juan, and Ana García. 2020. “{TITLE}.” Journal of Dairy Science 103 (1): 1–10.",          # Chicago
    f"PEREZ J, GARCIA A. 2020. {TITLE.upper()}. J DAIRY SCI 103:1-10.",
    f"Pérez J et al. 2020. Short communication: {TITLE}. J Dairy Sci 103:1-10.",
    f"Pérez J, García A. 2020. {TITLE}. Review. J Dairy Sci 103:1-10.",
    f"Pérez J, García A. 2020. {TITLE.replace('rumen-protected', 'rumen protected')}. JDS 103:1-10.",
    f"Pérez J, García A, on behalf of the XYZ Study Group. 2020. {TITLE}. J Dairy Sci 103:1-10.",
    f"Pérez J, Garsia A, Lopes L. 2020. {TITLE}. J Dairy Sci 103:1-10.",     # coautores escritos de otra forma
    f"Juan A. Pérez, Ana B. García, and Luis López. 2020. {TITLE}. Journal of Dairy Science 103:1–10.",
])
def test_cita_legitima_sin_discrepancia(cite):
    check = c.compare_citation(cite, **REC)
    assert not _bad(check), check
    assert check.year_in_citation == 2020 and check.author_ok


@pytest.mark.parametrize("cite, motivo", [
    # T8: mismo primer autor, año y revista; título de otro trabajo del mismo tema (una palabra cambiada)
    (f"Pérez J, García A. 2020. {SAME_TOPIC}. J Dairy Sci 103:1-10.", "title"),
    (f"Pérez J. 2020. Rumen-protected methionine improves milk production and liver health in transition "
     "dairy goats. J Dairy Sci 103:1-10.", "title"),
    # título corto contenido en el largo, y al revés
    ("Pérez J, García A. 2020. Rumen-protected choline in dairy cows. J Dairy Sci 103:1-10.", "title"),
    (f"Pérez J, García A. 2020. {TITLE} fed high-starch diets with monensin. J Dairy Sci 103:1-10.", "title"),
    # título inventado delante del real (estilos con año antes y Vancouver; también en mayúsculas)
    (f"Pérez J, García A. 2020. Rumen-protected methionine in goats. {TITLE}. J Dairy Sci 103:1-10.", "title"),
    (f"Pérez J, García A. Monensin and laminitis in heifers. {TITLE}. J Dairy Sci. 2020;103:1-10.", "title"),
    (f"Pérez J, García A. 2020. RUMEN-PROTECTED METHIONINE IN DAIRY GOATS. {TITLE}. J Dairy Sci 103:1-10.",
     "title"),
    # T9: el primer autor real citado en segundo lugar; autores inventados; sin coma entre autores
    (f"Smith J, Pérez J. 2020. {TITLE}. J Dairy Sci 103:1-10.", "author"),
    (f"García A, Pérez J. 2020. {TITLE}. J Dairy Sci 103:1-10.", "author"),
    (f"Smith J Pérez J, García A. 2020. {TITLE}. J Dairy Sci 103:1-10.", "author"),
    (f"Smith J. Pérez J. 2020. {TITLE}. J Dairy Sci 103:1-10.", "author"),
    (f"Inventado Z, Ficticio Q. 2020. {TITLE}. J Dairy Sci 103:1-10.", "author"),
    # T11: otra revista; y la revista no se salta con notas, volumen o páginas delante
    (f"Pérez J, García A. 2020. {TITLE}. Animal 14:1-10.", "journal"),
    (f"Pérez J, García A. 2020. {TITLE}. J Dairy Res 87:1-10.", "journal"),
    (f"Pérez J, García A. 2020. {TITLE}. 103:1-10. Animal.", "journal"),
    (f"Pérez J, García A. 2020. {TITLE}. Vol 103, pp 1-10, Animal.", "journal"),
    (f"Pérez J, García A. 2020. {TITLE}. Comment. Animal 14:1-10.", "journal"),
    # sin título: T8 por omisión (mismo primer autor, año y revista sirven para cualquier trabajo suyo)
    ("Pérez J, García A. 2020. J Dairy Sci 103:1-10.", "title"),
    ("Pérez J, García A. J Anim Sci. 2020;103:1-10.", "title"),
    ("Pérez J.", "title"),
    ("Pérez J, García A, López L. 2020.", "title"),
])
def test_cita_que_no_corresponde_da_discrepancia(cite, motivo):
    check = c.compare_citation(cite, **REC)
    assert getattr(check, f"{motivo}_ok") is False, check


def test_cita_sin_titulo_es_discrepancia_y_no_se_sella(net):
    # Regresión de la ronda 1: quitar el título de una entrada que da «NO corresponde (título)» la convertía en
    # verificada. La base la rechazaba (cobertura del título < 0,6) y ahora vuelve a ser discrepancia.
    published(net)
    for entry in (f"Pérez J, García A. 2020. J Dairy Sci 103:1-10. DOI: {DOI}", f"Pérez J. DOI: {DOI}",
                  f"Pérez J, García A, López L. 2020. Journal of Dairy Science. https://doi.org/{DOI}"):
        res = c.verify_entry(Client(), entry)[0]
        assert res.status == "verificada" and res.mismatch and not res.ok and res.title_ok is False, entry
        assert "no trae el título" in res.message and "revista" not in " ".join(res.mismatch_reasons), entry
        issues, details, summary = check_report_online(Client(), report(entry))
        assert summary["con_error"] == 1 and summary["verificadas"] == 0 and not summary["completa"], entry
    # con el título del trabajo pasa; con el de otro trabajo del mismo tema, no
    assert c.verify_doi(Client(), DOI, f"Pérez J, García A. 2020. {TITLE}. J Dairy Sci 103:1-10.").ok
    assert not c.verify_doi(Client(), DOI, f"Pérez J, García A. 2020. {SAME_TOPIC}. J Dairy Sci 103:1-10.").ok


def test_registro_sin_titulo_compara_autor_ano_y_revista():
    # Sin título en el registro no hay nada que exigir: se comparan el primer autor, el año y la revista (tras los
    # autores), y la nota lo dice.
    rec = dict(titles=[], authors=REC["authors"], families=REC["families"], journals=REC["journals"])
    good = c.compare_citation("Pérez J, García A. 2020. J Dairy Sci 103:1-10.", **rec)
    assert good.title_ok is None and good.journal_ok and good.author_ok and not good.title_absent
    assert any("no trae título" in n for n in good.notes)
    assert c.compare_citation("Pérez J, García A. J Anim Sci. 2020;103:1-10.", **rec).journal_ok is False


def test_umbral_del_titulo_mas_exigente_que_el_anterior():
    # El umbral anterior (0,6, asimétrico) dejaba pasar un título del mismo tema con el 73 % de palabras comunes.
    assert c.TITLE_MIN > 0.6
    cmp = c.compare_titles(TITLE, "Rumen-protected methionine improves milk production and liver health in "
                                  "transition dairy goats")
    assert 0.6 < cmp.score < c.TITLE_MIN and not cmp.ok
    assert c.compare_titles(TITLE, TITLE.replace("transition ", "")).ok          # una palabra de doce omitida
    assert not c.compare_titles("Rumen microbiome of yaks", "Rumen microbiome of yaks and goats").ok
    assert not c.compare_titles(TITLE, SAME_TOPIC).ok                            # sustitución: nunca


@pytest.mark.parametrize("real, cited", [
    ("Effects of feeding 50 g/d of rumen-protected choline on milk yield and liver health of transition dairy cows",
     "Effects of feeding 100 g/d of rumen-protected choline on milk yield and liver health of transition dairy cows"),
    ("Production responses of dairy cows to rumen-protected choline in the transition period. Experiment 1",
     "Production responses of dairy cows to rumen-protected choline in the transition period. Experiment 2"),
    ("Rumen-protected choline in transition dairy cows: effects on milk production and liver triglycerides. Part I",
     "Rumen-protected choline in transition dairy cows: effects on milk production and liver triglycerides. Part II"),
    ("Effects of vitamin E supplementation on mastitis and somatic cell count in grazing dairy cows",
     "Effects of vitamin D supplementation on mastitis and somatic cell count in grazing dairy cows"),
    # una cifra o una negación que falta también cuenta, aunque el resto del título (largo) coincida
    ("Effects of feeding 50 g/d of rumen-protected choline on milk yield and liver health of transition dairy cows",
     "Effects of feeding rumen-protected choline on milk yield and liver health of transition dairy cows"),
    ("Supplementation with rumen-protected choline does not improve milk yield or liver health in transition cows",
     "Supplementation with rumen-protected choline improves milk yield or liver health in transition cows"),
])
def test_titulos_que_solo_difieren_en_cifras_letras_o_negaciones_son_distintos(real, cited):
    assert not c.compare_titles(real, cited).ok
    check = c.compare_citation(f"Pérez J, García A. 2020. {cited}. J Dairy Sci 103:1-10.", titles=[real],
                               **{k: v for k, v in REC.items() if k != "titles"})
    assert check.title_ok is False and check.year_in_citation == 2020, check
    assert c.compare_titles(real, real).ok


def test_ano_dentro_del_titulo_real_no_es_el_de_la_cita(net):
    # T10: un año que forma parte del título real no se lee como el año de la cita, en ningún estilo.
    quota = "Milk production and herd structure of dairy farms after the 2015 milk quota abolition in Ireland"
    published(net, title=quota)
    for cite in (f"Pérez J, García A. {quota}. J Dairy Sci. 2020;103:1-10.",
                 f"Pérez, J., and A. García. 2020. {quota}. J. Dairy Sci. 103:1–10.",
                 f"Pérez J, García A (2020) {quota}. J Dairy Sci 103:1-10"):
        res = c.verify_doi(Client(), DOI, cite)
        assert res.year_in_citation == 2020 and res.ok and res.title_ok, (cite, res.message)
    wrong = c.verify_doi(Client(), DOI, f"Pérez J, García A. {quota}. J Dairy Sci. 2012;103:1-10.")
    assert wrong.year_in_citation == 2012 and wrong.mismatch


def test_citas_legitimas_que_daban_falso_error():
    # Nombres de pila completos (Chicago) frente a un registro de PubMed que solo da iniciales
    pubmed_rec = dict(titles=[TITLE], authors=["Pérez J", "García A", "López L"], journals=["Journal of dairy science"])
    chicago = f"Pérez, Juan, Ana García, and Luis López. 2020. “{TITLE}.” Journal of Dairy Science 103 (1): 1–10."
    assert not _bad(c.compare_citation(chicago, **pubmed_rec))
    assert not _bad(c.compare_citation(f"Juan Carlos Pérez, Ana García. 2020. {TITLE}. J Dairy Sci 103:1-10.",
                                       **dict(pubmed_rec, authors=["Pérez JC", "García A"])))
    # ...pero un título inventado delante sigue detectándose, también con nombres de pila
    for cite in (f"Pérez J, García A. Monensin and laminitis in heifers. {TITLE}. J Dairy Sci. 2020;103:1-10.",
                 f"Pérez, Juan, Ana García. 2020. Avocado laminitis in heifers. {TITLE}. J Dairy Sci 103:1-10.",
                 f"Pérez, Juan, Laminitis Avocado García. 2020. {TITLE}. J Dairy Sci 103:1-10."):
        assert c.compare_citation(cite, **pubmed_rec).title_ok is False, cite
    # Cabecera de sección de J Anim Sci ("Special topics—") que la cita no copia
    hristov = ("Special topics—Mitigation of methane and nitrous oxide emissions from animal operations: I. A review "
               "of enteric methane mitigation options")
    check = c.compare_citation("Hristov AN, Oh J, Firkins JL. 2013. Mitigation of methane and nitrous oxide emissions "
                               "from animal operations: I. A review of enteric methane mitigation options. J Anim Sci "
                               "91:5045-69.", titles=[hristov], authors=["Hristov A N", "Oh J"],
                               families=["Hristov", "Oh"], journals=["Journal of Animal Science"])
    assert not _bad(check), check
    # PNAS con y sin el calificativo de país; sigla delante de una coma
    for cited, full in (("Proceedings of the National Academy of Sciences of the USA",
                         "Proceedings of the National Academy of Sciences"),
                        ("Proc Natl Acad Sci U S A", "Proceedings of the National Academy of Sciences"),
                        ("Proc. Natl. Acad. Sci. USA",
                         "Proceedings of the National Academy of Sciences of the United States of America"),
                        ("ITEA", "ITEA, informacion tecnica economica agraria"),
                        ("J. S. Afr. Vet. Assoc.", "Journal of the South African Veterinary Association")):
        assert c.journal_matches(cited, full), (cited, full)
    for cited, full in (("J Dairy Res", "Journal of Dairy Science"), ("Animal", "Animals"),
                        ("ITEA", "Animal"), ("Nature", "Nature Communications")):
        assert not c.journal_matches(cited, full), (cited, full)


def test_variantes_sistematicas_del_titulo_y_metadatos_con_etiquetas():
    rec = dict(authors=["Smith Alan"], families=["Smith"], journals=["Animal"])
    for real, cited in (("Effects of dietary fiber on rumen fermentation in sheep",
                         "Effects of dietary fibre on rumen fermentation in sheep"),
                        ("Antibody titers in calves", "Antibody titres in calves"),
                        ("Estrus detection in dairy cows", "Oestrus detection in dairy cows"),
                        ("CH<sub>4</sub> emissions from dairy cows fed 3-NOP", "CH4 emissions from dairy cows fed 3-NOP"),
                        ("Rumen fermentation &amp; methane in sheep", "Rumen fermentation & methane in sheep")):
        check = c.compare_citation(f"Smith A. 2019. {cited}. Animal 13:1-9.", titles=[real], **rec)
        assert check.title_ok and check.journal_ok, (real, check)
    # nitrate/nitrite no son una variante ortográfica
    check = c.compare_citation("Smith A. 2019. Nitrite in sheep. Animal 13:1-9.", titles=["Nitrate in sheep"], **rec)
    assert check.title_ok is False
    # el título de la revista no es "otro título" aunque comparta palabras con el del trabajo
    check = c.compare_citation("Li X, Wang Y. 2022. Rumen microbiome of yaks. Microbiome 10:45.",
                               titles=["Rumen microbiome of yaks"], authors=["Li Xiang", "Wang Yu"],
                               families=["Li", "Wang"], journals=["Microbiome"])
    assert not _bad(check)


def test_verificacion_doi_real_atribuido_a_otro_trabajo_del_mismo_tema(net):
    # D3-7: el DOI existe y es del mismo grupo, tema, primer autor, año y revista, pero de otro trabajo
    published(net)
    cite = f"Pérez J, García A, López L. 2020. {SAME_TOPIC}. J Dairy Sci 103:1-10. DOI: {DOI}"
    res = c.verify_doi(Client(), DOI, cite)
    assert res.status == "verificada" and res.retracted is False
    assert res.mismatch and not res.ok and res.title_ok is False
    assert "NO corresponde a la cita" in res.message and "methionine" in res.message
    good = c.verify_doi(Client(), DOI, f"Pérez J, García A, López L. 2020. {TITLE}. J Dairy Sci 103:1-10.")
    assert good.ok and not good.mismatch
    issues, details, summary = check_report_online(Client(), report(cite))
    assert summary["con_error"] == 1 and not summary["completa"]
    assert any("NO corresponde" in i.message for i in issues)


def test_revista_bilingue_vale_cualquiera_de_los_titulos_del_registro(net):
    spanish = "Efecto de la colina protegida sobre la producción de leche en vacas en transición"
    net.cr[DOI] = cr_msg(DOI, title=TITLE, journal="Archivos de Zootecnia", short="Arch. Zootec.")
    net.cr[DOI]["title"] = [spanish, TITLE]
    net.oa[DOI] = oa_work(DOI, title=spanish, journal="Archivos de Zootecnia")
    for title in (spanish, TITLE):
        res = c.verify_doi(Client(), DOI, f"Pérez J, García A. 2020. {title}. Arch Zootec 69:1-10.")
        assert res.ok, res.message
    assert not c.verify_doi(Client(), DOI, f"Pérez J. 2020. {SAME_TOPIC}. Arch Zootec 69:1-10.").ok


def test_registro_de_openalex_nombre_de_pila_no_es_el_apellido(net):
    # Sin Crossref, los nombres de OpenAlex vienen como "Nombre Apellido": "Juan Smith" no es "Juan Pérez".
    net.oa[DOI] = oa_work(DOI)
    net.handles.add(DOI)
    bad = c.verify_doi(Client(), DOI, f"Juan Smith, Ana García. 2020. {TITLE}. J Dairy Sci 103:1-10.")
    assert bad.author_ok is False and bad.mismatch
    good = c.verify_doi(Client(), DOI, f"Pérez J, García A. 2020. {TITLE}. J Dairy Sci 103:1-10.")
    assert good.author_ok and good.ok


# ============================================================================ D3-15: doi.org, DataCite y PubMed
def test_doi_de_datacite_se_verifica_con_sus_metadatos(net):
    doi = "10.5555/dryad.abc123"
    net.oa_key = False
    net.handles.add(doi)
    net.agency[doi] = "DataCite"
    net.dc[doi] = {"data": {"id": doi, "attributes": {
        "doi": doi, "titles": [{"title": "Rumen fermentation data in dairy cows"}],
        "creators": [{"name": "Pérez, Juan", "familyName": "Pérez", "givenName": "Juan", "nameType": "Personal"}],
        "publicationYear": 2021, "publisher": "Dryad", "types": {"resourceTypeGeneral": "Dataset"},
        "rightsList": [{"rights": "CC0 1.0", "rightsUri": "https://creativecommons.org/publicdomain/zero/1.0/"}]}}}
    res = c.verify_doi(Client(), doi, "Pérez J. 2021. Rumen fermentation data in dairy cows. Dryad. "
                                      f"https://doi.org/{doi}")
    assert res.status == "verificada" and res.source == "datacite" and res.registration_agency == "DataCite"
    assert res.license == "https://creativecommons.org/publicdomain/zero/1.0/" and not res.mismatch
    assert res.retracted is None and not res.ok           # DataCite no publica retractaciones: pendiente
    assert net.sources()[:4] == ["crossref", "openalex", "doi.org", "doiRA"]


def test_doi_registrado_sin_metadatos_queda_pendiente_no_inexistente(net):
    doi = "10.5555/medra.2020.1"
    net.handles.add(doi)
    net.agency[doi] = "mEDRA"
    res = c.verify_doi(Client(), doi, "Rossi M. 2020. Titolo. Riv Zootec 1:1-5.")
    assert res.status == "existe_sin_metadatos" and not res.ok and res.registration_agency == "mEDRA"
    assert "PENDIENTE" in res.message and "mEDRA" in res.message
    issues, details, summary = check_report_online(Client(), report(f"Rossi M. 2020. Titolo. DOI: {doi}"))
    assert summary["pendientes"] == 1 and summary["con_error"] == 0 and not summary["completa"]


def test_doi_de_otra_agencia_con_metadatos_en_pubmed(net):
    doi = "10.5555/medra.2020.2"
    net.handles.add(doi)
    net.agency[doi] = "mEDRA"
    net.pm_by_doi[doi] = "30000001"
    net.pm["30000001"] = pm_rec("30000001", doi)
    res = c.verify_doi(Client(), doi, f"Pérez J, García A. 2020. {TITLE}. J Dairy Sci 103:1-10.")
    assert res.status == "verificada" and res.source == "pubmed" and res.retracted is False and res.ok
    assert res.record.pmid == "30000001" and res.registration_agency == "mEDRA"
    # un PMID que PubMed asocia a otro DOI no vale como metadatos de este
    net.pm["30000001"] = pm_rec("30000001", "10.5555/otro")
    assert c.verify_doi(Client(), doi).status == "existe_sin_metadatos"


def test_doi_inexistente_solo_si_doi_org_no_lo_tiene(net):
    res = c.verify_doi(Client(), "10.5555/inventado.2024.001")
    assert res.status == "no_encontrada" and "doi.org" in res.message
    net.doiorg_down = True
    res = c.verify_doi(Client(), "10.5555/inventado.2024.001")
    assert res.status == "error_red" and not res.ok          # sin doi.org no se sabe: pendiente, no error


def test_registro_de_doi_respuestas_de_la_api_de_handles():
    class One:
        def __init__(self, response=None, error=None):
            self.response, self.error = response, error

        def get_json(self, url, **kw):
            if self.error:
                raise self.error
            return self.response
    assert doiorg.handle_exists(One({"responseCode": 1}), "10.5555/x") is True
    assert doiorg.handle_exists(One({"responseCode": 200}), "10.5555/x") is True     # registrado sin valores
    assert doiorg.handle_exists(One(error=NotFoundError("404")), "10.5555/x") is False
    assert doiorg.handle_exists(One({"responseCode": 100}), "10.5555/x") is False
    with pytest.raises(NetworkError):
        doiorg.handle_exists(One({"responseCode": 2}), "10.5555/x")
    assert doiorg.registration_agency(One([{"DOI": "10.5555/x", "RA": "DataCite"}]), "10.5555/x") == "DataCite"
    assert doiorg.registration_agency(One([{"DOI": "10.5555/x", "status": "DOI does not exist"}]), "10.5555/x") is None


def test_fuente_agregar_doi_de_datacite_y_rechazo_sin_metadatos(project, net):
    doi = "10.5555/zenodo.42"
    net.handles.update({doi, "10.5555/medra.9"})
    net.agency.update({doi: "DataCite", "10.5555/medra.9": "mEDRA"})
    net.dc[doi] = {"data": {"attributes": {"doi": doi, "titles": [{"title": "Datos de fermentación"}],
                                           "creators": [{"name": "Pérez, Juan"}], "publicationYear": "2022",
                                           "types": {"resourceTypeGeneral": "Dataset"}}}}
    assert main(["fuente", "agregar", "--doi", doi]) == 0
    row = source(f"doi:{doi}")
    assert row["verified"] == 1 and row["verified_via"] == "datacite" and row["retracted"] is None
    assert run(["fuente", "agregar", "--doi", "10.5555/medra.9"]) != 0
    assert source("doi:10.5555/medra.9") is None


# ============================================================================ D3-16: casos de fallo de las citas
def test_ano_dentro_del_doi_no_es_el_ano_de_la_cita(net):
    published(net)
    cite = f"Pérez J, García A. 2019. {TITLE}. J Dairy Sci 103:2007-2016. DOI: {DOI}"
    assert c.verify_doi(Client(), DOI, cite).ok            # 2019 (DOI) y 2007-2016 (páginas) no cuentan; ±1 año
    wrong = c.verify_doi(Client(), DOI, cite.replace(" 2019. ", " 2012. "))
    assert wrong.year_in_citation == 2012 and wrong.year == 2020 and wrong.mismatch
    assert "año (cita 2012, real 2020)" in wrong.message
    assert c.citation_year(f"Sin año. DOI: {DOI}") is None


def test_ano_pegado_al_titulo_tambien_se_comprueba(net):
    published(net)
    for cite in (f"Pérez J. {TITLE} 2012. J Dairy Sci 103:1-10.", f"Pérez J, García A. {TITLE}, 2012, J Dairy Sci."):
        res = c.verify_doi(Client(), DOI, cite)
        assert res.year_in_citation == 2012 and res.mismatch, cite


def test_entrada_con_solo_el_identificador_no_se_da_por_buena(net):
    published(net)
    for entry in (f"DOI: {DOI}", f"https://doi.org/{DOI}", f"2020. doi:{DOI}"):
        res = c.verify_entry(Client(), entry)[0]
        assert res.status == "verificada" and res.mismatch and not res.ok, entry
        assert "solo trae el identificador" in res.message
    issues, details, summary = check_report_online(Client(), report(f"DOI: {DOI}"))
    assert summary["con_error"] == 1 and not summary["completa"]
    assert c.verify_doi(Client(), DOI).ok          # sin cita (verificar --doi) solo se comprueba el identificador


def test_retractacion_desconocida_sin_clave_de_openalex_ni_pubmed(net):
    net.cr[DOI] = cr_msg(DOI)          # Crossref no trae aviso de retractación: desconocida, no "no retractado"
    net.oa_key = False
    res = c.verify_doi(Client(), DOI, f"Pérez J. 2020. {TITLE}. J Dairy Sci 103:1-10.")
    assert res.status == "verificada" and res.retracted is None and res.retraction_checked_via == []
    assert not res.ok and not res.mismatch and "retractado" in res.message
    issues, details, summary = check_report_online(Client(), report(f"Pérez J. 2020. {TITLE}. DOI: {DOI}"))
    assert summary["pendientes"] == 1 and summary["verificadas"] == 0 and not summary["completa"]


def test_referencias_de_solo_url_no_se_verifican_solas(net):
    entry = "FEDNA. 2018. Normas FEDNA para la formulación de piensos. https://www.fundacionfedna.org/normas"
    assert c.extract_identifiers(entry) == {"doi": [], "pmid": [], "pmcid": []}
    assert c.verify_entry(Client(), entry) == []
    issues, details, summary = check_report_online(Client(), report(entry))
    assert summary["pendientes"] == 1 and summary["verificadas"] == 0 and not summary["completa"]
    # una URL de doi.org es un DOI: se verifica en línea
    published(net)
    linked = f"Pérez J. 2020. {TITLE}. J Dairy Sci 103:1-10. https://doi.org/{DOI}"
    assert [r.identifier for r in c.verify_entry(Client(), linked)] == [DOI]


def test_identificadores_repetidos_o_inventados_en_una_entrada(net):
    published(net)
    entry = (f"Pérez J. 2020. {TITLE}. J Dairy Sci 103:1-10. DOI: {DOI}. https://doi.org/{DOI.upper()} "
             "DOI: 10.5555/inventado.002")
    results = c.verify_entry(Client(), entry)
    assert [r.identifier for r in results] == [DOI, "10.5555/inventado.002"]      # el repetido, una vez
    assert results[0].ok and results[1].status == "no_encontrada"
    issues, details, summary = check_report_online(Client(), report(entry))
    assert summary["con_error"] == 1 and not summary["completa"]


# ============================================================================ D9-3: búsquedas sin verificar
def _search(monkeypatch, *records, agente="investigador-bibliografico"):
    def fake(client, query, **kw):
        return {"count": len(records), "records": list(records), "query_translation": None}
    monkeypatch.setattr(pubmed, "search_records", fake)
    return run(["--agente", agente, "buscar", "pubmed", "colina", "--registrar"])


def test_busqueda_no_cambia_retractacion_ni_campos_protegidos(project, net, monkeypatch):
    published(net, title=f"RETRACTED: {TITLE}", retracted=True)
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    sid = f"doi:{DOI}"
    before = source(sid)
    assert before["retracted"] == 1 and before["verified"] == 1
    hit = Record(source="pubmed", doi=DOI, pmid="999", title="Otro título distinto de una búsqueda", year=1999,
                 authors=["Otro A"], journal="Otra revista", retracted=False)
    assert _search(monkeypatch, hit) == 0
    after = source(sid)
    for key in ("retracted", "title", "year", "authors", "journal", "pmid", "verified", "verified_via"):
        assert after[key] == before[key], key
    omitted = events("fuente_omitido")
    assert omitted and "no coinciden" in omitted[-1]["outputs"]["motivo"]


def test_retractacion_desconocida_no_pasa_a_cero_por_una_busqueda(project, net, monkeypatch):
    # Caso del escéptico: DOI de Crossref, sin clave de OpenAlex y sin PubMed -> retractación desconocida.
    net.cr[DOI] = cr_msg(DOI)
    net.oa_key = False
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    sid = f"doi:{DOI}"
    assert source(sid)["retracted"] is None
    hit = Record(source="europepmc", doi=DOI, title=TITLE, year=2020, authors=["Pérez J"], retracted=False)
    assert _search(monkeypatch, hit) == 0
    assert source(sid)["retracted"] is None
    assert main(["afirmacion", "agregar", "--datos", str(claim_file(project, [sid]))]) == 3   # [PUB] rechazada
    assert db.list_claims(_conn()) == [] and db.list_decisions(_conn(), sid) == []


def test_busqueda_solo_rellena_campos_vacios_no_criticos_y_lo_registra(project, net, monkeypatch):
    net.cr[DOI] = cr_msg(DOI, volume=None, page=None)
    net.oa[DOI] = oa_work(DOI)
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    sid = f"doi:{DOI}"
    assert source(sid)["pages"] is None and source(sid)["license"] is None
    hit = Record(source="pubmed", doi=DOI, pmid="12345678", title=TITLE, year=2020, authors=["Pérez J"],
                 volume="103", pages="1-10", retracted=False, extra={"license": "cc-by"})
    assert _search(monkeypatch, hit) == 0
    row = source(sid)
    assert row["pages"] == "1-10" and row["volume"] == "103" and row["license"] == "cc-by"
    assert row["pmid"] is None                     # un identificador nunca lo pone una búsqueda
    filled = events("fuente_rellenado_sin_verificar")
    assert filled and set(filled[-1]["outputs"]["campos"]) == {"license", "pages", "volume"}


def test_busqueda_que_dice_retractado_avisa_sin_cambiar_el_registro(project, net, monkeypatch):
    published(net)
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    sid = f"doi:{DOI}"
    hit = Record(source="pubmed", doi=DOI, title=TITLE, year=2020, authors=["Pérez J"], retracted=True)
    new = Record(source="pubmed", pmid="40000001", title="Otro trabajo retractado", authors=["Ruiz R"],
                 retracted=True)
    assert _search(monkeypatch, hit, new) == 0
    assert source(sid)["retracted"] == 0 and source("pmid:40000001")["retracted"] is None
    warned = {e["outputs"]["id"]: e for e in events("fuente_aviso_retractacion")}
    assert set(warned) == {sid, "pmid:40000001"}
    # el aviso da la orden que vuelve a verificar esa fuente (`verificar --pendientes` no revisa las verificadas)
    assert f"`nutria fuente agregar --doi {DOI}`" in warned[sid]["outputs"]["motivo"]
    assert "`nutria fuente agregar --pmid 40000001`" in warned["pmid:40000001"]["outputs"]["motivo"]
    # la procedencia guarda la huella de la consulta, no la consulta (puede ser confidencial)
    assert "consulta" not in warned[sid]["inputs"] and len(warned[sid]["inputs"]["consulta_sha256"]) == 64
    published(net, retracted=True)
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    assert source(sid)["retracted"] == 1


def test_verificar_pendientes_sustituye_los_metadatos_de_la_busqueda(project, net, monkeypatch):
    hit = Record(source="europepmc", doi=DOI, title="Título que dio la búsqueda", year=1999,
                 authors=["Nadie N"], journal="Revista de la búsqueda", retracted=False)
    assert _search(monkeypatch, hit) == 0
    sid = f"doi:{DOI}"
    assert source(sid)["verified"] == 0 and source(sid)["retracted"] is None
    published(net)
    assert main(["verificar", "--pendientes"]) == 0
    row = source(sid)
    assert row["verified"] == 1 and row["retracted"] == 0
    assert row["title"] == TITLE and row["year"] == 2020 and row["journal"] == "Journal of Dairy Science"
    corrected = events("fuente_corregido_por_verificacion")
    assert corrected and {"title", "year", "journal"} <= set(corrected[-1]["outputs"]["campos"])


# ============================================================================ D9-4 a: fusión segura
def test_busqueda_con_doi_de_un_trabajo_y_pmid_de_otro_no_fusiona(project, net, monkeypatch):
    published(net, doi="10.5555/trabajo.a", title="Rumen-protected choline and milk yield in Holstein cows")
    published(net, doi="10.5555/trabajo.b", pmid="555", title="Methionine supplementation of grazing ewes")
    assert main(["fuente", "agregar", "--doi", "10.5555/trabajo.a"]) == 0
    assert main(["fuente", "agregar", "--doi", "10.5555/trabajo.b"]) == 0
    claim = add_claim(project, ["doi:10.5555/trabajo.b"])
    hit = Record(source="europepmc", doi="10.5555/trabajo.a", pmid="555", title="Rumen-protected choline")
    assert _search(monkeypatch, hit) != 0
    rows = {r["id"]: r for r in db.list_sources(_conn())}
    assert set(rows) == {"doi:10.5555/trabajo.a", "doi:10.5555/trabajo.b"}
    assert rows["doi:10.5555/trabajo.a"]["pmid"] is None and rows["doi:10.5555/trabajo.b"]["pmid"] == "555"
    assert {cl["id"]: cl["source_ids"] for cl in db.list_claims(_conn())}[claim] == ["doi:10.5555/trabajo.b"]


def _two_rows_same_work(net, title_pmid_row=TITLE):
    """doi:DOI verificada sin PMID (PubMed aún no tenía el DOI) y pmid:777 verificada por PubMed sin DOI."""
    published(net)
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    net.pm["777"] = pm_rec("777", None, title=title_pmid_row)
    assert main(["fuente", "agregar", "--pmid", "777"]) == 0
    assert source("pmid:777")["verified"] == 1 and source(f"doi:{DOI}")["pmid"] is None


def test_verificacion_fusiona_el_mismo_trabajo_y_redirige_afirmaciones_y_extracciones(project, net):
    _two_rows_same_work(net)
    claim = add_claim(project, ["pmid:777"])
    add_extraction(project, "pmid:777")
    net.pm_by_doi[DOI] = "777"                   # ahora PubMed asocia el DOI a ese PMID
    net.pm["777"] = pm_rec("777", DOI)
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    assert source("pmid:777") is None and source(f"doi:{DOI}")["pmid"] == "777"
    assert {cl["id"]: cl["source_ids"] for cl in db.list_claims(_conn())}[claim] == [f"doi:{DOI}"]
    assert {e["source_id"] for e in db.list_extractions(_conn())} == {f"doi:{DOI}"}
    decisions = [d["decision"] for d in db.list_decisions(_conn(), f"doi:{DOI}")]
    assert any("fusión de fuentes duplicadas (pmid:777 -> doi:" in d for d in decisions)
    assert events("fuente_fusion")


def test_fusion_acepta_el_titulo_traducido_de_pubmed(project, net):
    translated = "[Effects of protected choline on milk yield of dairy cows]"
    _two_rows_same_work(net, title_pmid_row=translated)
    net.pm_by_doi[DOI] = "777"
    net.pm["777"] = pm_rec("777", DOI, title=translated)
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    assert source("pmid:777") is None and source(f"doi:{DOI}")["pmid"] == "777"


def test_pubmed_con_otro_titulo_solo_vale_si_coinciden_doi_autor_y_ano(net):
    published(net)
    net.pm_by_doi[DOI] = "888"
    net.pm["888"] = pm_rec("888", DOI, title="[Efecto de la colina protegida en vacas]")     # traducido
    assert c.verify_doi(Client(), DOI).record.pmid == "888"
    net.pm["888"] = pm_rec("888", DOI, title="[Efecto de la colina protegida en vacas]", authors=("Smith K",))
    res = c.verify_doi(Client(), DOI)
    assert res.record.pmid is None and "pubmed" not in res.retraction_checked_via and "no se usa" in res.message
    net.pm["888"] = pm_rec("888", None, title="Otro trabajo sin DOI en PubMed")
    assert c.verify_doi(Client(), DOI).record.pmid is None


def test_verificacion_no_fusiona_trabajos_distintos_ni_borra_nada(project, net):
    _two_rows_same_work(net, title_pmid_row="Methionine supplementation of grazing ewes")
    claim = add_claim(project, ["pmid:777"])
    net.pm_by_doi[DOI] = "777"                   # PubMed y OpenAlex relacionan por error el DOI con ese PMID
    net.oa[DOI] = oa_work(DOI, pmid="777")
    assert main(["fuente", "agregar", "--doi", DOI]) == 0        # el DOI se verifica, sin asociarle ese PMID
    assert source("pmid:777")["title"] == "Methionine supplementation of grazing ewes"
    assert source(f"doi:{DOI}")["pmid"] is None
    assert {cl["id"]: cl["source_ids"] for cl in db.list_claims(_conn())}[claim] == ["pmid:777"]
    assert not any("fusión" in d["decision"] for d in db.list_decisions(_conn()))
    # Si un registro verificado los relacionara de todos modos, el registro tampoco fusiona: error sin tocar nada.
    conn = _conn()
    record = {"doi": DOI, "pmid": "777", "title": TITLE, "year": 2020, "verified": True, "verified_via": "crossref",
              "retracted": False, "extra": {"verified_by": "doi", "title_variants": [TITLE]}}
    with pytest.raises(db.RegistryError, match="no es el del trabajo verificado"):
        db.upsert_source(conn, record)
    assert source("pmid:777")["title"] == "Methionine supplementation of grazing ewes"
    assert source(f"doi:{DOI}")["pmid"] is None and len(db.list_sources(conn)) == 2


def test_no_se_fusiona_si_una_fila_tiene_otro_identificador(project, net):
    conn = _conn()
    db.upsert_source(conn, {"doi": DOI, "pmid": "111", "title": TITLE})          # búsqueda: DOI con otro PMID
    db.upsert_source(conn, {"pmid": "777", "title": TITLE})
    record = {"doi": DOI, "pmid": "777", "title": TITLE, "year": 2020, "verified": True, "verified_via": "crossref",
              "retracted": False}
    with pytest.raises(db.RegistryError, match="tiene el PMID 111"):
        db.upsert_source(conn, record)
    assert {r["id"] for r in db.list_sources(conn)} == {f"doi:{DOI}", "pmid:777"}


def test_busqueda_no_fusiona_dos_filas_aunque_tengan_el_mismo_titulo(project, net, monkeypatch):
    # Mutante M8: unos datos sin verificar no fusionan nunca, tampoco dos filas del mismo trabajo (mismo título):
    # solo una verificación puede hacerlo.
    first = Record(source="openalex", doi=DOI, title=TITLE, year=2020)
    second = Record(source="pubmed", pmid="555", title=TITLE, year=2020)
    assert _search(monkeypatch, first, second) == 0
    both = Record(source="europepmc", doi=DOI, pmid="555", title=TITLE, year=2020)
    assert _search(monkeypatch, both) == 3
    rows = {r["id"]: r for r in db.list_sources(_conn())}
    assert set(rows) == {f"doi:{DOI}", "pmid:555"} and rows[f"doi:{DOI}"]["pmid"] is None
    assert not db.list_decisions(_conn())


# ---------------------------------------------------------------- D9-4 a: fila única por el identificador asociado
EWES = "Methionine supplementation of grazing ewes in late pregnancy"


def test_pmid_de_openalex_solo_si_pubmed_lo_confirma(net):
    # Mutante M23: el PMID que OpenAlex asocia a un DOI solo se acepta si PubMed confirma que es ese trabajo.
    published(net)
    net.oa[DOI] = oa_work(DOI, pmid="777")
    net.pm["777"] = pm_rec("777", DOI)                         # PubMed declara el DOI: se asocia
    res = c.verify_doi(Client(), DOI)
    assert res.record.pmid == "777" and "pubmed" in res.retraction_checked_via
    net.pm["777"] = pm_rec("777", None)                        # sin DOI pero el mismo título: se asocia
    assert c.verify_doi(Client(), DOI).record.pmid == "777"
    for other in (pm_rec("777", None, title=EWES), pm_rec("777", "10.5555/otro"), None):
        if other is None:
            net.pm.pop("777")                                  # PubMed no tiene ese PMID
        else:
            net.pm["777"] = other
        res = c.verify_doi(Client(), DOI)
        assert res.record.pmid is None and "no se asocia" in res.message, other
    net.pm["777"] = pm_rec("777", DOI)
    net.pm_by_doi[DOI] = "888"                                 # PubMed da otro PMID para el DOI: ninguno
    net.pm["888"] = pm_rec("888", DOI)
    res = c.verify_doi(Client(), DOI)
    assert res.record.pmid is None and "PMID distintos" in res.message


def test_openalex_con_otro_titulo_no_aporta_identificadores_ni_no_retractado(net):
    # OpenAlex puede mezclar obras: si para el DOI da otro título, no se toman su PMID, su identificador ni su "no
    # retractado" (la retractación queda desconocida); un "retractado" sí cuenta, por prudencia.
    net.cr[DOI] = cr_msg(DOI)
    net.oa[DOI] = oa_work(DOI, title=EWES, pmid="777", wid="W9")
    net.pm["777"] = pm_rec("777", DOI, title=EWES)
    res = c.verify_doi(Client(), DOI)
    assert res.retracted is None and res.record.pmid is None and res.record.openalex_id is None
    assert "OpenAlex da para este DOI otro título" in res.message
    net.oa[DOI] = oa_work(DOI, title=EWES, retracted=True)
    assert c.verify_doi(Client(), DOI).retracted is True


def test_pmid_de_openalex_sin_respuesta_de_pubmed_no_se_asocia(net, monkeypatch):
    published(net)
    net.oa[DOI] = oa_work(DOI, pmid="777")

    def down(client, pmids):
        raise NetworkError("Sin respuesta válida de PubMed")
    monkeypatch.setattr(pubmed, "summaries", down)
    res = c.verify_doi(Client(), DOI)
    assert res.status == "verificada" and res.record.pmid is None and "no se pudo comprobar en PubMed" in res.message


def test_pubmed_con_otro_titulo_sin_corchetes_no_es_una_traduccion(net):
    # Escenario P de la revisión: PubMed declara por error el DOI en otro trabajo del mismo primer autor y año.
    published(net)
    net.pm_by_doi[DOI] = "777"
    net.pm["777"] = pm_rec("777", DOI, title=EWES)
    res = c.verify_doi(Client(), DOI)
    assert res.record.pmid is None and EWES not in res.record.extra["title_variants"]
    net.pm["777"] = pm_rec("777", DOI, title=f"[{EWES}]")     # entre corchetes sí es una traducción de PubMed
    assert c.verify_doi(Client(), DOI).record.pmid == "777"
    assert c.translated_title("[Efecto de la colina en vacas].") and not c.translated_title(EWES)


def test_fila_verificada_de_otro_trabajo_no_se_reescribe(project, net):
    # Regresión R de la ronda 1: pmid:777 verificada (otro trabajo); OpenAlex asocia ese PMID al DOI.
    net.pm["777"] = pm_rec("777", None, title=EWES, authors=("Ruiz R",), year=2015)
    assert main(["fuente", "agregar", "--pmid", "777"]) == 0
    claim = add_claim(project, ["pmid:777"])
    add_extraction(project, "pmid:777")
    published(net)
    net.oa[DOI] = oa_work(DOI, pmid="777")
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    row = source("pmid:777")
    assert row["title"] == EWES and row["doi"] is None and row["year"] == 2015
    assert source(f"doi:{DOI}")["pmid"] is None and source(f"doi:{DOI}")["title"] == TITLE
    assert {cl["id"]: cl["source_ids"] for cl in db.list_claims(_conn())}[claim] == ["pmid:777"]
    assert {e["source_id"] for e in db.list_extractions(_conn())} == {"pmid:777"}


def test_registro_no_reescribe_la_fila_de_otro_trabajo_encontrada_por_el_identificador_asociado(project):
    # La protección del registro, sin pasar por la verificación (por si una fuente confirma una asociación errónea).
    conn = _conn()
    db.upsert_source(conn, {"pmid": "777", "title": EWES, "year": 2015, "authors": ["Ruiz R"], "verified": True,
                            "verified_via": "pubmed", "retracted": False, "extra": {"verified_by": "pmid"}})
    record = {"doi": DOI, "pmid": "777", "title": TITLE, "year": 2020, "authors": ["Pérez Juan"], "verified": True,
              "verified_via": "crossref", "retracted": False, "extra": {"verified_by": "doi", "title_variants": [TITLE]}}
    changes = []
    sid = db.upsert_source(conn, record, changes=changes)
    assert sid == f"doi:{DOI}" and db.get_source(conn, sid)["pmid"] is None
    other = db.get_source(conn, "pmid:777")
    assert other["title"] == EWES and other["doi"] is None
    assert [ch["fuente"] for ch in changes if ch["accion"] == "identificador_no_asociado"] == ["pmid:777"]
    assert any("PMID 777 no asociado (es de pmid:777)" in d["decision"] for d in db.list_decisions(conn, sid))
    # Escenario S: PubMed declara para otro PMID el DOI de una fuente verificada que es otro trabajo.
    record_s = {"pmid": "888", "doi": DOI, "title": EWES, "year": 2015, "authors": ["Ruiz R"], "verified": True,
                "verified_via": "pubmed", "retracted": False, "extra": {"verified_by": "pmid", "title_variants": [EWES]}}
    assert db.upsert_source(conn, record_s) == "pmid:888"
    assert db.get_source(conn, "pmid:888")["doi"] is None and db.get_source(conn, f"doi:{DOI}")["title"] == TITLE
    # Escenario P: una fila SIN verificar que se llama por ese PMID tampoco se reescribe con otro trabajo.
    db.upsert_source(conn, {"pmid": "999", "title": EWES, "year": 2015})
    record_p = dict(record, doi="10.5555/otro.trabajo", pmid="999")
    assert db.upsert_source(conn, record_p) == "doi:10.5555/otro.trabajo"
    assert db.get_source(conn, "pmid:999")["title"] == EWES and db.get_source(conn, "pmid:999")["verified"] == 0


def test_fila_del_mismo_trabajo_recibe_el_identificador_asociado(project, net, monkeypatch, capsys):
    # El caso legítimo sigue funcionando: la búsqueda registró pmid:555 y la verificación del DOI lo confirma.
    assert _search(monkeypatch, Record(source="pubmed", pmid="555", title=TITLE, year=2020, authors=["Pérez J"])) == 0
    published(net, pmid="555")
    capsys.readouterr()
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    assert "ya estaba registrada como pmid:555" in capsys.readouterr().out
    row = source("pmid:555")
    assert row["doi"] == DOI and row["verified"] == 1 and len(db.list_sources(_conn())) == 1
    # y una fuente con el título traducido por PubMed también es el mismo trabajo
    spanish = "Efecto de la colina protegida sobre la producción de leche en vacas en transición"
    net.cr["10.5555/arch.1"] = cr_msg("10.5555/arch.1", title=spanish, year=2018, journal="Archivos de Zootecnia")
    assert main(["fuente", "agregar", "--doi", "10.5555/arch.1"]) == 0
    net.pm["666"] = pm_rec("666", "10.5555/arch.1", title="[Effect of protected choline on milk yield of cows]",
                           year=2018)
    assert main(["fuente", "agregar", "--pmid", "666"]) == 0
    assert source("doi:10.5555/arch.1")["pmid"] == "666" and source("pmid:666") is None


def test_verificacion_retira_el_pmid_que_una_busqueda_asocio_a_otro_doi(project, net, monkeypatch):
    # Una búsqueda asoció el PMID 555 a otro DOI y otro trabajo (fila sin verificar); la verificación del DOI real lo
    # confirma (PubMed declara ese DOI en el PMID 555): gana la verificación, y la fila de la búsqueda no se toca más.
    assert _search(monkeypatch, Record(source="europepmc", doi="10.5555/otro", pmid="555", title=EWES)) == 0
    published(net, pmid="555")
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    other = source("doi:10.5555/otro")
    assert other["pmid"] is None and other["title"] == EWES and source(f"doi:{DOI}")["pmid"] == "555"
    assert any("identificador retirado" in d["decision"] for d in db.list_decisions(_conn(), "doi:10.5555/otro"))


def test_una_verificacion_no_borra_una_retractacion_conocida(project, net):
    # Mutante M16: una retractación registrada por una verificación no la borra otra que dice "no retractado".
    published(net, retracted=True)
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    assert source(f"doi:{DOI}")["retracted"] == 1
    published(net, retracted=False)
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    assert source(f"doi:{DOI}")["retracted"] == 1
    assert main(["verificar", "--pendientes"]) == 0 and source(f"doi:{DOI}")["retracted"] == 1


# ============================================================================ D7-16: licencias
def test_licencias_de_crossref_openalex_y_datacite():
    msg = cr_msg(DOI, license=[{"URL": "https://www.elsevier.com/tdm/userlicense/1.0/", "content-version": "tdm"},
                               {"URL": "https://creativecommons.org/licenses/by/4.0/", "content-version": "vor",
                                "delay-in-days": 0}])
    rec = crossref.parse(msg)
    assert rec.extra["license"] == "https://creativecommons.org/licenses/by/4.0/" and len(rec.extra["licenses"]) == 2
    tdm_only = crossref.parse(cr_msg(DOI, license=[{"URL": "https://www.elsevier.com/tdm/userlicense/1.0/",
                                                    "content-version": "tdm"}]))
    assert tdm_only.extra["license"] is None      # minería de textos no es la licencia del artículo
    best = openalex.parse({"id": "W1", "best_oa_location": {"license": "cc-by-nc"},
                           "primary_location": {"license": "cc-by"}})
    assert best.extra["license"] == "cc-by-nc"
    assert openalex.parse({"id": "W1", "primary_location": {"license": "cc-by"}}).extra["license"] == "cc-by"
    dc = doiorg.parse_datacite({"data": {"attributes": {"rightsList": [{"rightsUri": "https://x.org/cc0"}]}}})
    assert dc.extra["license"] == "https://x.org/cc0"


def test_licencia_se_registra_y_se_muestra(project, net, capsys):
    published(net, license=[{"URL": "https://creativecommons.org/licenses/by/4.0/", "content-version": "vor"}])
    assert main(["fuente", "agregar", "--doi", DOI]) == 0
    assert source(f"doi:{DOI}")["license"] == "https://creativecommons.org/licenses/by/4.0/"
    capsys.readouterr()
    assert main(["fuente", "listar"]) == 0
    assert "[licencia CC BY 4.0]" in capsys.readouterr().out
    assert main(["--json", "fuente", "ver", f"doi:{DOI}"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["license"] == "https://creativecommons.org/licenses/by/4.0/" and shown["licencia"] == "CC BY 4.0"


def test_migracion_anade_la_columna_de_licencia_a_una_base_existente(tmp_path):
    path = tmp_path / "antigua.sqlite"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE sources (id TEXT PRIMARY KEY, doi TEXT, pmid TEXT, pmcid TEXT, openalex_id TEXT, "
                "title TEXT, authors TEXT, year INTEGER, journal TEXT, volume TEXT, issue TEXT, pages TEXT, "
                "pub_types TEXT, study_type TEXT, species TEXT, retracted INTEGER, verified INTEGER NOT NULL "
                "DEFAULT 0, verified_at TEXT, verified_via TEXT, url TEXT, abstract TEXT, notes TEXT, added_at TEXT, "
                "added_by TEXT)")
    old.execute("INSERT INTO sources (id, doi, title, verified) VALUES ('doi:10.5555/viejo', '10.5555/viejo', "
                "'Fuente antigua', 0)")
    old.commit()
    old.close()
    conn = db.connect(path)
    assert "license" in {r[1] for r in conn.execute("PRAGMA table_info(sources)")}
    assert db.get_source(conn, "doi:10.5555/viejo")["title"] == "Fuente antigua"
    sid = db.upsert_source(conn, {"doi": "10.5555/nuevo", "title": "Nueva", "verified": True, "retracted": False,
                                  "verified_via": "crossref", "extra": {"license": "cc-by"}})
    assert db.get_source(conn, sid)["license"] == "cc-by"
