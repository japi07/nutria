"""Pruebas de los analizadores con respuestas mínimas que siguen el formato documentado de cada API.
No sustituyen a una prueba en vivo (`nutria doctor` y una búsqueda real en tu entorno)."""
from nutria.sources import crossref, europepmc, openalex, pubmed, uniprot
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
