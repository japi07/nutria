"""Clientes de fuentes externas (literatura, moléculas, proteínas, rutas, patentes).

Cada cliente devuelve estructuras normalizadas (`Record` para literatura) y nunca
inventa campos: si la fuente no aporta un dato, queda como `None`.
"""

from .base import Record, normalize_doi

__all__ = ["Record", "normalize_doi"]
