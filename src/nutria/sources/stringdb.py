"""STRING: redes de interacción proteína-proteína y enriquecimiento funcional por especie.

Se consulta la dirección versionada de STRING (`DEFAULT_URL`, configurable en nutria.toml, sección [fuentes],
`string_url`), que STRING recomienda para obtener resultados estables, y `version` devuelve la versión que
responde para registrarla junto a los resultados. Dirección y formato según la documentación de STRING; no
comprobados contra el servicio real.
"""

from __future__ import annotations

import re

from ..config import DEFAULT_STRING_URL

DEFAULT_URL = DEFAULT_STRING_URL
BASE = f"{DEFAULT_URL}/api"   # dirección por defecto; las funciones usan la configurada (`api_url`)
_VERSIONED_RE = re.compile(r"(?i)^https?://version-[0-9][0-9-]*\.string-db\.org$")


def base_url(cfg) -> str:
    return str(getattr(cfg, "string_url", "") or DEFAULT_URL).rstrip("/")


def api_url(cfg) -> str:
    return base_url(cfg) + "/api"


def is_versioned(cfg) -> bool:
    """True si la dirección configurada es una de las versionadas de STRING (version-X-Y.string-db.org)."""
    return bool(_VERSIONED_RE.match(base_url(cfg)))


def _identifiers(genes: list[str]) -> str:
    return "\r".join(g.strip() for g in genes if g.strip())


def version(client) -> dict:
    """Versión de STRING que responde (`/api/json/version`): {"version", "direccion_estable"}."""
    data = client.get_json(f"{api_url(client.cfg)}/json/version", ttl_days=1)
    item = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
    out = {"version": item.get("string_version"), "direccion_estable": item.get("stable_address")}
    return {k: v for k, v in out.items() if v}


def network(client, genes: list[str], species: int = 9913, required_score: int = 700) -> list[dict]:
    """Interacciones con puntuación combinada >= required_score (0-1000)."""
    params = {"identifiers": _identifiers(genes), "species": species, "required_score": required_score,
              "caller_identity": client.cfg.tool}
    data = client.get_json(f"{api_url(client.cfg)}/json/network", params=params)
    return [{"a": d.get("preferredName_A"), "b": d.get("preferredName_B"), "score": d.get("score"),
             "experimental": d.get("escore"), "bases_datos": d.get("dscore"), "text_mining": d.get("tscore")}
            for d in data]


def enrichment(client, genes: list[str], species: int = 9913, fdr_max: float = 0.05) -> list[dict]:
    params = {"identifiers": _identifiers(genes), "species": species, "caller_identity": client.cfg.tool}
    data = client.get_json(f"{api_url(client.cfg)}/json/enrichment", params=params)
    rows = [{"categoria": d.get("category"), "termino": d.get("term"), "descripcion": d.get("description"),
             "fdr": d.get("fdr"), "p": d.get("p_value"), "genes": d.get("preferredNames"),
             "n_genes": d.get("number_of_genes"), "n_fondo": d.get("number_of_genes_in_background")}
            for d in data]
    return [r for r in rows if r["fdr"] is not None and r["fdr"] <= fdr_max]
