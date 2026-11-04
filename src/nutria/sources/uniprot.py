"""UniProtKB REST: proteínas por gen y especie (p. ej. ortólogos bovinos)."""

from __future__ import annotations

BASE = "https://rest.uniprot.org/uniprotkb"
FIELDS = "accession,id,protein_name,gene_names,organism_name,organism_id,length,reviewed,cc_function"


def _protein_name(entry: dict) -> str | None:
    desc = entry.get("proteinDescription") or {}
    rec = (desc.get("recommendedName") or {}).get("fullName") or {}
    if rec.get("value"):
        return rec["value"]
    for sub in desc.get("submissionNames", []) or []:
        value = (sub.get("fullName") or {}).get("value")
        if value:
            return value
    return None


def _function(entry: dict) -> str | None:
    for comment in entry.get("comments", []) or []:
        if comment.get("commentType") == "FUNCTION":
            texts = [t.get("value") for t in comment.get("texts", []) if t.get("value")]
            if texts:
                return " ".join(texts)
    return None


def parse(entry: dict) -> dict:
    genes = [(g.get("geneName") or {}).get("value") for g in entry.get("genes", []) or []]
    return {
        "accesion": entry.get("primaryAccession"),
        "entrada": entry.get("uniProtkbId"),
        "proteina": _protein_name(entry),
        "genes": [g for g in genes if g],
        "organismo": (entry.get("organism") or {}).get("scientificName"),
        "taxon": (entry.get("organism") or {}).get("taxonId"),
        "longitud": (entry.get("sequence") or {}).get("length"),
        "revisada": str(entry.get("entryType", "")).lower().startswith("uniprotkb reviewed"),
        "funcion": _function(entry),
        "url": f"https://www.uniprot.org/uniprotkb/{entry.get('primaryAccession')}",
    }


def search(client, query: str, taxon: int | None = None, size: int = 10,
           reviewed_only: bool = False) -> list[dict]:
    q = f"({query})"
    if taxon:
        q += f" AND (organism_id:{taxon})"
    if reviewed_only:
        q += " AND (reviewed:true)"
    data = client.get_json(f"{BASE}/search", params={"query": q, "fields": FIELDS, "format": "json",
                                                     "size": size})
    return [parse(e) for e in data.get("results", [])]
