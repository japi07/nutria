"""OpenAlex: índice abierto de literatura (citas, acceso abierto, estado de retractación)."""

from __future__ import annotations

import re
from urllib.parse import quote

from .base import Record

BASE = "https://api.openalex.org"


def _params(cfg, extra: dict | None = None) -> dict:
    params = dict(extra or {})
    if cfg.email:
        params["mailto"] = cfg.email
    key = cfg.secret("OPENALEX_API_KEY")
    if key:
        params["api_key"] = key
    return params


def reconstruct_abstract(inverted: dict | None) -> str | None:
    if not inverted:
        return None
    positions = [(pos, word) for word, poss in inverted.items() for pos in poss]
    return " ".join(word for _, word in sorted(positions)) or None


def _short_id(value: str | None) -> str | None:
    if not value:
        return None
    return value.rstrip("/").rsplit("/", 1)[-1]


def parse(work: dict) -> Record:
    ids = work.get("ids") or {}
    pmid = None
    if ids.get("pmid"):
        match = re.search(r"(\d+)\s*$", str(ids["pmid"]))
        pmid = match.group(1) if match else None
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    biblio = work.get("biblio") or {}
    pages = None
    if biblio.get("first_page"):
        pages = biblio["first_page"] + (f"-{biblio['last_page']}" if biblio.get("last_page") else "")
    oa = work.get("open_access") or {}
    return Record(
        source="openalex",
        title=work.get("display_name") or work.get("title"),
        authors=[(a.get("author") or {}).get("display_name") for a in work.get("authorships", [])
                 if (a.get("author") or {}).get("display_name")],
        year=work.get("publication_year"),
        journal=source.get("display_name"),
        volume=biblio.get("volume"),
        issue=biblio.get("issue"),
        pages=pages,
        doi=work.get("doi"),
        pmid=pmid,
        pmcid=_short_id(ids.get("pmcid")),
        openalex_id=_short_id(work.get("id")),
        pub_types=[t for t in [work.get("type")] if t],
        abstract=reconstruct_abstract(work.get("abstract_inverted_index")),
        url=oa.get("oa_url") or (location.get("landing_page_url")),
        cited_by=work.get("cited_by_count"),
        is_oa=oa.get("is_oa"),
        retracted=work.get("is_retracted"),
    )


def search(client, query: str, per_page: int = 25, page: int = 1, from_year: int | None = None,
           to_year: int | None = None, filters: list[str] | None = None) -> dict:
    flt = list(filters or [])
    if from_year:
        flt.append(f"from_publication_date:{from_year}-01-01")
    if to_year:
        flt.append(f"to_publication_date:{to_year}-12-31")
    params = {"search": query, "per-page": per_page, "page": page}
    if flt:
        params["filter"] = ",".join(flt)
    data = client.get_json(f"{BASE}/works", params=_params(client.cfg, params), ttl_days=7)
    return {"count": int((data.get("meta") or {}).get("count", 0) or 0),
            "records": [parse(w) for w in data.get("results", [])]}


def work_by_doi(client, doi: str) -> Record:
    data = client.get_json(f"{BASE}/works/doi:{quote(doi, safe='/')}", params=_params(client.cfg))
    return parse(data)
