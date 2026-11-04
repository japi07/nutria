"""STRING: redes de interacción proteína-proteína y enriquecimiento funcional por especie."""

from __future__ import annotations

BASE = "https://string-db.org/api"


def _identifiers(genes: list[str]) -> str:
    return "\r".join(g.strip() for g in genes if g.strip())


def network(client, genes: list[str], species: int = 9913, required_score: int = 700) -> list[dict]:
    """Interacciones con puntuación combinada >= required_score (0-1000)."""
    params = {"identifiers": _identifiers(genes), "species": species, "required_score": required_score,
              "caller_identity": client.cfg.tool}
    data = client.get_json(f"{BASE}/json/network", params=params)
    return [{"a": d.get("preferredName_A"), "b": d.get("preferredName_B"), "score": d.get("score"),
             "experimental": d.get("escore"), "bases_datos": d.get("dscore"), "text_mining": d.get("tscore")}
            for d in data]


def enrichment(client, genes: list[str], species: int = 9913, fdr_max: float = 0.05) -> list[dict]:
    params = {"identifiers": _identifiers(genes), "species": species, "caller_identity": client.cfg.tool}
    data = client.get_json(f"{BASE}/json/enrichment", params=params)
    rows = [{"categoria": d.get("category"), "termino": d.get("term"), "descripcion": d.get("description"),
             "fdr": d.get("fdr"), "p": d.get("p_value"), "genes": d.get("preferredNames"),
             "n_genes": d.get("number_of_genes"), "n_fondo": d.get("number_of_genes_in_background")}
            for d in data]
    return [r for r in rows if r["fdr"] is not None and r["fdr"] <= fdr_max]
