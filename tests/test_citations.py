import pytest

from nutria.sources.base import Record
from nutria.verify import citations as c


def test_extract_identifiers_handles_parentheses_and_punctuation():
    text = ("St-Pierre 2001. J Dairy Sci 84:741. DOI: 10.3168/jds.S0022-0302(01)74530-4. "
            "Otro (doi:10.1017/S1751731108002280), PMID: 24667063.")
    ids = c.extract_identifiers(text)
    assert "10.3168/jds.s0022-0302(01)74530-4" in ids["doi"]
    assert "10.1017/s1751731108002280" in ids["doi"]
    assert ids["pmid"] == ["24667063"]


def test_citation_year_ignores_doi_years_and_page_ranges():
    # D3-7a / D9-13: el año dentro del DOI o unas páginas 2007-2016 no se toman por el año de la cita
    assert c.citation_year("Pérez A. 2012. Título. J Dairy Sci 103:2007-2016. DOI: 10.3168/jds.2019-17123") == 2012
    assert c.citation_year("Smith J (2008). T. Animal 2(8):1203-1214. https://doi.org/10.1017/x.2019.1") == 2008
    assert c.citation_year("Sin año. DOI: 10.1234/abc.2015") is None


def test_title_and_first_author():
    # D3-7: título comparado de forma simétrica y primer autor de la cita = primer autor del registro
    real = "Meta-analyses of experimental data in animal nutrition"
    good = "[R1] Sauvant D et al. 2008. Meta-analyses of experimental data in animal nutrition. Animal 2:1203."
    bad = "[R1] Smith J. 2008. Rumen protected choline improves fertility. J Dairy Sci."
    check = c.compare_citation(good, titles=[real], authors=["Sauvant D", "Schmidely P"], journals=["Animal"])
    assert check.title_ok is True and check.title_score == pytest.approx(1.0) and check.author_ok is True
    assert check.journal_ok is True and check.year_in_citation == 2008
    assert c.compare_citation(bad, titles=[real], authors=["Sauvant D"]).title_ok is False
    second = "[R1] Smith J, Sauvant D. 2008. Meta-analyses of experimental data in animal nutrition. Animal."
    assert c.compare_citation(second, titles=[real], authors=["Sauvant D"]).author_ok is False
    assert c.title_similarity(real, real.upper()) == pytest.approx(1.0)


class FakeClient:
    cfg = None


REC = Record(source="crossref", title="Meta-analyses of experimental data in animal nutrition", year=2008,
             doi="10.1017/S1751731108002280", authors=["Sauvant D", "Schmidely P"])
OK_CITE = "Sauvant D et al. 2008. Meta-analyses of experimental data in animal nutrition. Animal."


def test_unknown_retraction_is_not_ok(monkeypatch):
    # D9-2 / D3-8 / D7-1: si ninguna fuente permite saber si está retractado, la verificación no vale
    monkeypatch.setattr(c.crossref, "work", lambda client, doi: REC)
    from nutria.http import NetworkError

    def down(client, doi):
        raise NetworkError("OpenAlex bloqueado")
    monkeypatch.setattr(c.openalex, "work_by_doi", down)
    monkeypatch.setattr(c.pubmed, "pmid_for_doi", lambda client, doi: None, raising=False)
    res = c.verify_doi(FakeClient(), REC.doi, OK_CITE)
    assert res.status == "verificada" and res.retracted is None and not res.ok


def test_retraction_and_mismatch(monkeypatch):
    monkeypatch.setattr(c.crossref, "work", lambda client, doi: REC)
    monkeypatch.setattr(c.pubmed, "pmid_for_doi", lambda client, doi: None)
    monkeypatch.setattr(c.openalex, "work_by_doi",
                        lambda client, doi: Record(source="openalex", title=REC.title, retracted=True))
    res = c.verify_doi(FakeClient(), REC.doi, OK_CITE)
    assert res.retracted is True and not res.ok
    monkeypatch.setattr(c.openalex, "work_by_doi",
                        lambda client, doi: Record(source="openalex", title=REC.title, retracted=False))
    good = c.verify_doi(FakeClient(), REC.doi, OK_CITE)
    assert good.ok and good.retraction_checked_via == ["openalex"]
    wrong_author = c.verify_doi(FakeClient(), REC.doi, "Smith J, Brown K. 2008. Meta-analyses of experimental "
                                                       "data in animal nutrition. Animal.")
    assert wrong_author.mismatch and not wrong_author.ok
    wrong_year = c.verify_doi(FakeClient(), REC.doi, "Sauvant D. 2012. Meta-analyses of experimental data in "
                                                     "animal nutrition. DOI 10.1017/x.2008")
    assert wrong_year.mismatch


def test_verify_doi_not_found(monkeypatch):
    from nutria.http import NotFoundError

    def missing(client, doi):
        raise NotFoundError("404")
    monkeypatch.setattr(c.crossref, "work", missing)
    monkeypatch.setattr(c.openalex, "work_by_doi", missing)
    # D3-15: solo es "no existe" si el registro de DOI (doi.org) tampoco lo tiene
    monkeypatch.setattr(c.doiorg, "handle_exists", lambda client, doi: False)
    res = c.verify_doi(FakeClient(), "10.9999/inventado.2024.001")
    assert res.status == "no_encontrada" and not res.ok and "doi.org" in res.message
