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


def test_title_coverage():
    real = "Meta-analyses of experimental data in animal nutrition"
    good = "[R1] Sauvant D et al. 2008. Meta-analyses of experimental data in animal nutrition. Animal 2:1203."
    bad = "[R1] Smith J. 2008. Rumen protected choline improves fertility. J Dairy Sci."
    assert c.title_coverage(real, good) == pytest.approx(1.0)
    assert c.title_coverage(real, bad) < 0.5


class FakeClient:
    cfg = None


def test_verify_doi_detects_mismatch_and_retraction(monkeypatch):
    rec = Record(source="crossref", title="Meta-analyses of experimental data in animal nutrition", year=2008,
                 doi="10.1017/S1751731108002280")
    monkeypatch.setattr(c.crossref, "work", lambda client, doi: rec)
    monkeypatch.setattr(c.openalex, "work_by_doi",
                        lambda client, doi: Record(source="openalex", title=rec.title, retracted=True))
    ok_cite = "Sauvant 2008. Meta-analyses of experimental data in animal nutrition. Animal."
    res = c.verify_doi(FakeClient(), "10.1017/S1751731108002280", ok_cite)
    assert res.status == "verificada" and res.retracted is True and not res.ok
    monkeypatch.setattr(c.openalex, "work_by_doi",
                        lambda client, doi: Record(source="openalex", title=rec.title, retracted=False))
    wrong = c.verify_doi(FakeClient(), "10.1017/S1751731108002280", "Smith 2015. Choline and fertility in cows.")
    assert wrong.mismatch and not wrong.ok


def test_verify_doi_not_found(monkeypatch):
    from nutria.http import NotFoundError

    def missing(client, doi):
        raise NotFoundError("404")
    monkeypatch.setattr(c.crossref, "work", missing)
    monkeypatch.setattr(c.openalex, "work_by_doi", missing)
    res = c.verify_doi(FakeClient(), "10.9999/inventado.2024.001")
    assert res.status == "no_encontrada" and not res.ok
