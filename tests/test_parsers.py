"""Pruebas de los analizadores con respuestas mínimas que siguen el formato documentado de cada API,
y de los clientes de fuentes con un cliente falso (sin red). No sustituyen a las pruebas en vivo
(`tests/test_vivo.py`, con NUTRIA_PRUEBAS_EN_VIVO=1)."""
import pytest

from nutria.config import Config
from nutria.http import NetworkError, NotFoundError
from nutria.sources import chembl, crossref, europepmc, openalex, pubchem, pubmed, uniprot
from nutria.sources.base import normalize_doi, taxon_id


def test_normalize_doi_and_taxa():
    assert normalize_doi("https://doi.org/10.3168/JDS.2009-2140") == "10.3168/jds.2009-2140"
    assert taxon_id("vacuno") == 9913 and taxon_id("9823") == 9823


def test_openalex_parse_and_abstract():
    work = {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/ABC", "display_name": "T",
            "publication_year": 2020, "authorships": [{"author": {"display_name": "Ana Pérez"}}],
            "primary_location": {"source": {"display_name": "J"}}, "biblio": {"volume": "1", "first_page": "5",
            "last_page": "9"}, "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/123"}, "is_retracted": False,
            "abstract_inverted_index": {"hola": [0], "mundo": [1]}, "type": "article"}
    rec = openalex.parse(work)
    assert rec.doi == "10.1/abc" and rec.pmid == "123" and rec.pages == "5-9" and rec.abstract == "hola mundo"


def test_crossref_parse():
    msg = {"DOI": "10.1/X", "title": ["Título"], "author": [{"family": "Pérez", "given": "Ana"}],
           "container-title": ["Revista"], "issued": {"date-parts": [[2019, 5]]}, "type": "journal-article"}
    rec = crossref.parse(msg)
    assert rec.year == 2019 and rec.authors == ["Pérez Ana"] and rec.journal == "Revista"


def test_europepmc_parse_retracted():
    hit = {"id": "1", "source": "MED", "pmid": "1", "title": "x", "pubYear": "2010",
           "pubTypeList": {"pubType": ["Journal Article", "Retracted Publication"]},
           "journalInfo": {"journal": {"title": "J"}, "volume": "3"}}
    rec = europepmc.parse(hit)
    assert rec.retracted is True and rec.journal == "J" and rec.year == 2010


def test_pubmed_efetch_parse():
    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>42</PMID><Article><Abstract>
    <AbstractText Label="OBJECTIVE">Probar.</AbstractText></Abstract><PublicationTypeList>
    <PublicationType>Meta-Analysis</PublicationType></PublicationTypeList></Article>
    <MeshHeadingList><MeshHeading><DescriptorName>Cattle</DescriptorName></MeshHeading></MeshHeadingList>
    </MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/y</ArticleId></ArticleIdList>
    </PubmedData></PubmedArticle></PubmedArticleSet>"""
    out = pubmed.parse_efetch(xml)
    assert out["42"]["abstract"] == "OBJECTIVE: Probar." and out["42"]["mesh"] == ["Cattle"]
    assert out["42"]["doi"] == "10.1/y"


def test_uniprot_parse():
    entry = {"primaryAccession": "P1", "uniProtkbId": "X_BOVIN", "entryType": "UniProtKB reviewed (Swiss-Prot)",
             "proteinDescription": {"recommendedName": {"fullName": {"value": "Proteína X"}}},
             "genes": [{"geneName": {"value": "GX"}}], "organism": {"scientificName": "Bos taurus", "taxonId": 9913},
             "sequence": {"length": 100}, "comments": [{"commentType": "FUNCTION", "texts": [{"value": "Hace X."}]}]}
    row = uniprot.parse(entry)
    assert row["revisada"] and row["genes"] == ["GX"] and row["funcion"] == "Hace X."


# ============================================================================ retractación en Crossref
def _crossref_msg(**extra):
    return {"DOI": "10.1/X", "title": ["Título"], "type": "journal-article", **extra}


def test_crossref_retraction_from_updated_by_or_update_to():
    # D7-1 / D3-8: el propio registro trae la retractación o la retirada (sin distinguir mayúsculas)
    rec = crossref.parse(_crossref_msg(**{"updated-by": [{"type": "retraction", "DOI": "10.1/aviso",
                                                         "source": "retraction-watch"}]}))
    assert rec.retracted is True
    assert rec.extra["retraction_updates"] == [{"field": "updated-by", "type": "retraction", "doi": "10.1/aviso",
                                                "source": "retraction-watch"}]
    assert crossref.parse(_crossref_msg(**{"update-to": [{"type": "Withdrawal", "DOI": "10.1/x"}]})).retracted is True
    assert crossref.parse(_crossref_msg(**{"updated-by": [{"type": " RETRACTION "}]})).retracted is True


def test_crossref_retraction_unknown_without_information():
    assert crossref.parse(_crossref_msg()).retracted is None
    corrected = crossref.parse(_crossref_msg(**{"updated-by": [{"type": "correction"}],
                                                "update-to": [{"type": "erratum"}, "basura", None]}))
    assert corrected.retracted is None and corrected.extra["retraction_updates"] == []
    assert crossref.parse(_crossref_msg(title=["RETRACTED: Título"])).retracted is True


# ============================================================================ clientes con cliente falso
class FakeClient:
    """Cliente sin red: registra (url, params) y devuelve la respuesta programada."""

    def __init__(self, cfg, response=None, error=None):
        self.cfg = cfg
        self.response = response if response is not None else {}
        self.error = error
        self.calls = []

    def get_json(self, url, params=None, **kw):
        self.calls.append((url, dict(params or {})))
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for name in ("NCBI_API_KEY", "OPENALEX_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return Config(root=tmp_path, cache_dir=tmp_path / "cache")


def test_pubmed_pmid_for_doi(cfg):
    # Identificadores ficticios: solo se comprueba la consulta y la lectura de la respuesta.
    client = FakeClient(cfg, {"esearchresult": {"idlist": ["11111111"]}})
    assert pubmed.pmid_for_doi(client, "https://doi.org/10.1234/EJEMPLO.2020.001") == "11111111"
    url, params = client.calls[0]
    assert url.endswith("/esearch.fcgi") and params["db"] == "pubmed"
    assert params["term"] == "10.1234/ejemplo.2020.001[doi]" and params["tool"] == "nutria"
    assert pubmed.pmid_for_doi(FakeClient(cfg, {"esearchresult": {"idlist": []}}), "10.1/x") is None
    assert pubmed.pmid_for_doi(FakeClient(cfg, {"esearchresult": {"idlist": ["1", "2"]}}), "10.1/x") is None


def test_pubmed_pmid_for_doi_quotes_special_characters(cfg):
    client = FakeClient(cfg, {"esearchresult": {"idlist": ["7"]}})
    assert pubmed.pmid_for_doi(client, "10.1234/ejemplo(01)002-4") == "7"
    assert client.calls[0][1]["term"] == '"10.1234/ejemplo(01)002-4"[doi]'
    empty = FakeClient(cfg)
    assert pubmed.pmid_for_doi(empty, "") is None and pubmed.pmid_for_doi(empty, '10.1/"x"') is None
    assert not empty.calls


def test_openalex_requires_key_before_any_request(cfg, monkeypatch):
    # D7-5: OpenAlex exige clave desde el 13-02-2026
    client = FakeClient(cfg, {"results": [], "meta": {"count": 0}})
    for call in (lambda: openalex.search(client, "rumen"), lambda: openalex.work_by_doi(client, "10.1/x")):
        with pytest.raises(NetworkError) as info:
            call()
        assert "OPENALEX_API_KEY" in str(info.value) and "https://openalex.org/settings/api" in str(info.value)
    assert not client.calls
    monkeypatch.setenv("OPENALEX_API_KEY", "clave-de-prueba")
    assert openalex.search(client, "rumen")["count"] == 0
    assert client.calls[-1][1]["api_key"] == "clave-de-prueba"


def test_openalex_offline_without_key_lets_the_cache_answer(cfg):
    cfg.offline = True
    client = FakeClient(cfg, {"results": [], "meta": {"count": 0}})
    assert openalex.search(client, "rumen")["count"] == 0
    assert "api_key" not in client.calls[0][1]


def test_chembl_target_search_filters_organism_on_server(cfg):
    # D7-11: con organismo, el filtro va en la petición (no se descartan resultados en el cliente)
    targets = {"targets": [{"target_chembl_id": "CHEMBL1", "pref_name": "Receptor X", "organism": "Bos taurus",
                            "target_type": "SINGLE PROTEIN"},
                           {"target_chembl_id": "CHEMBL2", "pref_name": "Receptor X", "organism": "Homo sapiens",
                            "target_type": "SINGLE PROTEIN"}]}
    client = FakeClient(cfg, targets)
    rows = chembl.target_search(client, "receptor", organism=" Bos taurus ", limit=5)
    url, params = client.calls[0]
    assert url.endswith("/chembl/api/data/target.json")
    assert params == {"pref_name__icontains": "receptor", "organism__iexact": "Bos taurus", "limit": 5}
    assert [r["target_chembl_id"] for r in rows] == ["CHEMBL1"]
    client = FakeClient(cfg, targets)
    assert len(chembl.target_search(client, "receptor")) == 2
    url, params = client.calls[0]
    assert url.endswith("/target/search.json") and params == {"q": "receptor", "limit": 20}


def test_pubchem_ghs_without_classification(cfg):
    assert pubchem.ghs_hazards(FakeClient(cfg, error=NotFoundError("No encontrado (404)")), 1) == []
    with pytest.raises(NetworkError):
        pubchem.ghs_hazards(FakeClient(cfg, error=NetworkError("HTTP 503 en pubchem")), 1)
