"""Europe PMC REST (incluye MEDLINE, PMC, preprints, patentes y literatura de agencias como EFSA)."""

from __future__ import annotations

from .base import Record, year_from

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def parse(hit: dict) -> Record:
    journal_info = hit.get("journalInfo") or {}
    journal = (journal_info.get("journal") or {}).get("title") or hit.get("journalTitle")
    pub_types = _as_list((hit.get("pubTypeList") or {}).get("pubType")) or _as_list(hit.get("pubType"))
    authors = [a.get("fullName") for a in _as_list((hit.get("authorList") or {}).get("author"))
               if isinstance(a, dict) and a.get("fullName")]
    if not authors and hit.get("authorString"):
        authors = [a.strip() for a in hit["authorString"].rstrip(".").split(",") if a.strip()]
    return Record(
        source="europepmc",
        title=hit.get("title"),
        authors=authors,
        year=year_from(hit.get("pubYear") or journal_info.get("yearOfPublication")),
        journal=journal,
        volume=journal_info.get("volume") or hit.get("journalVolume"),
        issue=journal_info.get("issue") or hit.get("issue"),
        pages=hit.get("pageInfo"),
        doi=hit.get("doi"),
        pmid=hit.get("pmid"),
        pmcid=hit.get("pmcid"),
        pub_types=[str(t) for t in pub_types],
        abstract=hit.get("abstractText"),
        is_oa=(hit.get("isOpenAccess") == "Y") if hit.get("isOpenAccess") else None,
        cited_by=hit.get("citedByCount"),
        retracted=any(str(t).strip().lower() == "retracted publication" for t in pub_types),
        url=f"https://europepmc.org/article/{hit.get('source')}/{hit.get('id')}" if hit.get("id") else None,
        extra={"epmc_id": hit.get("id"), "epmc_source": hit.get("source")},
    )


def search(client, query: str, page_size: int = 25, cursor: str = "*", result_type: str = "core",
           sort: str | None = None) -> dict:
    params = {"query": query, "format": "json", "pageSize": page_size, "cursorMark": cursor,
              "resultType": result_type}
    if sort:
        params["sort"] = sort
    data = client.get_json(f"{BASE}/search", params=params, ttl_days=7)
    hits = (data.get("resultList") or {}).get("result", [])
    return {"count": int(data.get("hitCount", 0) or 0), "next_cursor": data.get("nextCursorMark"),
            "records": [parse(h) for h in hits]}


def efsa_journal(client, query: str, page_size: int = 25) -> dict:
    """Opiniones y guías de EFSA (FEEDAP incluido) publicadas en EFSA Journal."""
    return search(client, f'({query}) AND JOURNAL:"EFSA Journal"', page_size=page_size)
