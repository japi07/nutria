"""Pruebas en vivo de cada cliente contra el servicio real (omitidas por defecto).

Solo se ejecutan con NUTRIA_PRUEBAS_EN_VIVO=1 y acceso de red a los dominios de docs/FUENTES.md:

    NUTRIA_PRUEBAS_EN_VIVO=1 python -m pytest -m vivo tests/test_vivo.py

OpenAlex exige además OPENALEX_API_KEY (clave gratuita en https://openalex.org/settings/api) y Lens,
LENS_API_TOKEN; sin ellas sus pruebas se omiten. Las comprobaciones son estructurales: el cliente entiende la
respuesta real y los identificadores pedidos vuelven en ella. No afirman nada sobre el contenido científico de las
fuentes. Cada prueba usa un proyecto temporal (caché y procedencia propias) y un cliente `fresh` (sin leer caché).
Ninguna se ha ejecutado aún contra los servicios reales: el entorno de desarrollo bloqueaba sus dominios.
"""
from __future__ import annotations

import os

import pytest

from nutria.config import Config
from nutria.http import HttpClient
from nutria.provenance import read_events
from nutria.sources import (chembl, crossref, eurlex, europepmc, openalex, patents, pubchem, pubmed, reactome,
                            stringdb, uniprot)
from nutria.sources.base import normalize_doi

EN_VIVO = os.environ.get("NUTRIA_PRUEBAS_EN_VIVO", "").strip() == "1"
pytestmark = [
    pytest.mark.vivo,
    pytest.mark.skipif(not EN_VIVO, reason="pruebas en vivo desactivadas: exporta NUTRIA_PRUEBAS_EN_VIVO=1"),
]

DOI = "10.1017/S1751731108002280"


@pytest.fixture
def cfg(tmp_path):
    return Config(root=tmp_path, cache_dir=tmp_path / "cache", db_path=tmp_path / "research" / "nutria.sqlite")


@pytest.fixture
def client(cfg):
    return HttpClient(cfg, fresh=True)


def test_pubmed(client, cfg):
    found = pubmed.search(client, "rumen", retmax=3)
    paged = pubmed.search_records(client, "rumen", retmax=3)
    assert len(paged["records"]) == 3 and not paged["incompleto"]
    assert found["count"] > 0 and 0 < len(found["ids"]) <= 3
    records = pubmed.summaries(client, found["ids"])
    assert records and {r.pmid for r in records} <= set(found["ids"])
    assert all(r.title and isinstance(r.retracted, bool) for r in records)
    assert set(pubmed.abstracts(client, found["ids"][:1])) <= set(found["ids"])
    pmid = pubmed.pmid_for_doi(client, DOI)
    assert pmid is None or pmid.isdigit()
    if pmid:
        (rec,) = pubmed.summaries(client, [pmid])
        assert rec.doi == normalize_doi(DOI)
    assert read_events(cfg, action="http")


def test_europepmc(client):
    paged = europepmc.search_all(client, "rumen", max_results=3)
    assert len(paged["records"]) == 3 and paged["next_cursor"]
    res = europepmc.search(client, "rumen", page_size=3)
    assert res["count"] > 0 and 0 < len(res["records"]) <= 3
    by_doi = europepmc.search(client, f'DOI:"{DOI}"', page_size=1)
    if by_doi["records"]:
        assert by_doi["records"][0].doi == normalize_doi(DOI)
    assert isinstance(europepmc.efsa_journal(client, "feed additive", page_size=2)["count"], int)


def test_crossref(client):
    rec = crossref.work(client, DOI)
    assert rec.doi == normalize_doi(DOI) and rec.title and isinstance(rec.year, int)
    assert rec.retracted in (None, True)  # Crossref solo informa de retractaciones, nunca de su ausencia
    hits = crossref.search_bibliographic(client, rec.title, rows=3)
    assert hits and all(h["record"].doi for h in hits)


def test_openalex(client):
    if not client.cfg.secret("OPENALEX_API_KEY"):
        pytest.skip("OpenAlex exige OPENALEX_API_KEY desde el 13-02-2026 (https://openalex.org/settings/api)")
    rec = openalex.work_by_doi(client, DOI)
    assert rec.doi == normalize_doi(DOI) and rec.openalex_id and isinstance(rec.retracted, bool)
    res = openalex.search(client, "rumen", per_page=3)
    assert res["count"] > 0 and 0 < len(res["records"]) <= 3


def test_chembl(client):
    molecules = chembl.molecule_search(client, "aspirin", limit=3)
    assert molecules and all(str(m["chembl_id"]).startswith("CHEMBL") for m in molecules)
    assert isinstance(chembl.mechanisms(client, molecules[0]["chembl_id"]), list)
    targets = chembl.target_search(client, "receptor", organism="Homo sapiens", limit=5)
    assert targets and all(t["organismo"].lower() == "homo sapiens" for t in targets)
    by_symbol = chembl.target_search(client, "PPARA", organism="Homo sapiens", limit=5)
    assert by_symbol and by_symbol[0]["coincidencia"] in ("nombre_preferido", "sinonimo_o_simbolo")
    assert chembl.release(client).get("version")
    assert chembl.target_search(client, "receptor", limit=5)
    assert isinstance(chembl.activities(client, target_chembl_id=targets[0]["target_chembl_id"], limit=3), list)


def test_pubchem(client):
    props = pubchem.compound_by_name(client, "aspirin")
    assert props and all(p.get("CID") and p.get("MolecularFormula") and "SMILES" in p for p in props)
    assert pubchem.synonyms(client, props[0]["CID"], limit=5)
    assert isinstance(pubchem.ghs_hazards(client, props[0]["CID"]), list)
    assert pubchem.ghs_classification(client, props[0]["CID"])["estado"] != pubchem.GHS_ERROR


def test_uniprot(client):
    rows = uniprot.search(client, "gene:PPARA", taxon=9913, size=5)
    assert rows and all(r["taxon"] == 9913 and r["accesion"] for r in rows)
    assert any("PPARA" in [g.upper() for g in r["genes"]] for r in rows)
    assert uniprot.release(client).get("version")


def test_string(client):
    assert stringdb.is_versioned(client.cfg) and stringdb.version(client).get("version")
    edges = stringdb.network(client, ["PPARA", "RXRA"], species=9913, required_score=400)
    assert isinstance(edges, list) and all(e["a"] and e["b"] and e["score"] is not None for e in edges)


def test_reactome(client):
    pathways = reactome.search(client, "metabolism", species="Bos taurus")
    assert pathways and all(p["id"] and p["nombre"] for p in pathways)
    result = reactome.analyze_genes(client, ["PPARA", "RXRA"])
    assert result["token"] and isinstance(result["rutas"], list)
    assert reactome.version(client).get("version")


def test_lens(client):
    if not client.cfg.secret("LENS_API_TOKEN"):
        pytest.skip("Lens exige LENS_API_TOKEN (https://www.lens.org/lens/user/subscriptions)")
    meta = {}
    rows = patents.lens_search(client, "rumen AND choline", size=3, meta=meta)
    assert isinstance(meta.get("total"), int) and all(r["lens_id"] for r in rows)


def test_eurlex(client):
    doc = eurlex.document(client, "32009R0767")
    assert doc["eli"] and doc["eli"].startswith("http://data.europa.eu/eli/") and doc["titulo"]
    assert doc["fecha_documento"] == "2009-07-13"
