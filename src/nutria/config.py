"""Configuración del proyecto: raíz, rutas, opciones de red y secretos (vía entorno)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILENAME = "nutria.toml"
DEFAULT_MAX_OFFLINE_DAYS = 180.0
DEFAULT_STRING_URL = "https://version-12-0.string-db.org"

DEFAULT_CONFIG_TEXT = """\
# Configuración del proyecto NUTRIA.
# Las claves de API NO se escriben aquí: se leen de variables de entorno
# (NCBI_API_KEY, OPENALEX_API_KEY, LENS_API_TOKEN, ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY...).

[contacto]
# Correo de contacto que piden NCBI, Crossref y OpenAlex para su "polite pool".
# Déjalo vacío si prefieres no enviarlo; las consultas funcionan igualmente (con límites más estrictos).
email = ""
herramienta = "nutria"

[rutas]
base_datos = "research/nutria.sqlite"
cache = ".cache/nutria"

[red]
cache_dias = 30
timeout_s = 30
offline = false
# Con --offline, antigüedad máxima (días) de las copias de la caché que se aceptan; amplíala para una orden
# con --antiguedad-maxima DIAS. La fecha de cada copia es la de su registro en la procedencia.
antiguedad_maxima_offline_dias = 180

[fuentes]
# Dirección versionada de STRING (resultados estables entre consultas). Cámbiala para usar otra versión;
# la orden `nutria red` registra la versión que responde.
string_url = "https://version-12-0.string-db.org"

[puntuacion]
# Perfil de pesos por defecto: "recomendado" (v2) u "original" (pesos del prompt inicial).
perfil = "recomendado"
"""


def find_root(start: Path | None = None) -> Path:
    """Busca hacia arriba el directorio que contiene `nutria.toml`.

    `NUTRIA_HOME` tiene prioridad. Si no se encuentra, devuelve el directorio de partida.
    """
    env = os.environ.get("NUTRIA_HOME")
    if env:
        return Path(env).expanduser().resolve()
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / CONFIG_FILENAME).is_file():
            return candidate
    return here


@dataclass
class Config:
    root: Path
    email: str = ""
    tool: str = "nutria"
    db_path: Path = Path("research/nutria.sqlite")
    cache_dir: Path = Path(".cache/nutria")
    cache_days: float = 30.0
    timeout: float = 30.0
    offline: bool = False
    cache_max_offline_days: float = DEFAULT_MAX_OFFLINE_DAYS
    string_url: str = DEFAULT_STRING_URL
    scoring_profile: str = "recomendado"
    actor: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def research_dir(self) -> Path:
        return self.root / "research"

    @property
    def provenance_path(self) -> Path:
        return self.research_dir / "procedencia.jsonl"

    @staticmethod
    def secret(name: str) -> str | None:
        """Lee un secreto del entorno; nunca se guarda en disco ni en la procedencia."""
        value = os.environ.get(name, "").strip()
        return value or None


_TRUE = {"1", "true", "si", "sí", "yes", "on"}


def positive_days(value, origin: str) -> float:
    """Número de días positivo y finito; si no lo es, ValueError con el origen del valor."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{origin}: se esperaba un número de días y se encontró {value!r}") from None
    if not (number > 0 and number != float("inf")):
        raise ValueError(f"{origin}: el número de días debe ser positivo y finito (valor {value!r})")
    return number


def _url(value, origin: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text.lower().startswith(("https://", "http://")):
        raise ValueError(f"{origin}: se esperaba una URL http(s) y se encontró {value!r}")
    return text


def load_config(root: Path | None = None) -> Config:
    root = find_root(root)
    raw: dict = {}
    path = root / CONFIG_FILENAME
    if path.is_file():
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    contacto = raw.get("contacto", {})
    rutas = raw.get("rutas", {})
    red = raw.get("red", {})
    puntuacion = raw.get("puntuacion", {})
    fuentes = raw.get("fuentes", {})
    offline_env = os.environ.get("NUTRIA_OFFLINE", "").strip().lower() in _TRUE
    return Config(
        root=root,
        email=str(contacto.get("email", "")).strip(),
        tool=str(contacto.get("herramienta", "nutria")).strip() or "nutria",
        db_path=root / rutas.get("base_datos", "research/nutria.sqlite"),
        cache_dir=root / rutas.get("cache", ".cache/nutria"),
        cache_days=float(red.get("cache_dias", 30)),
        timeout=float(red.get("timeout_s", 30)),
        offline=bool(red.get("offline", False)) or offline_env,
        cache_max_offline_days=positive_days(red.get("antiguedad_maxima_offline_dias", DEFAULT_MAX_OFFLINE_DAYS),
                                             f"{CONFIG_FILENAME} [red] antiguedad_maxima_offline_dias"),
        string_url=_url(fuentes.get("string_url", DEFAULT_STRING_URL), f"{CONFIG_FILENAME} [fuentes] string_url"),
        scoring_profile=str(puntuacion.get("perfil", "recomendado")),
        raw=raw,
    )
