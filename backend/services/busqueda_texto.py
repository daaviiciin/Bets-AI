"""Utilidades para búsquedas de texto libre en consultas SQL (``LIKE``)."""

from __future__ import annotations

#: Carácter de escape que se pasa a ``.like(..., escape=ESCAPE_LIKE)``.
ESCAPE_LIKE = "\\"


def patron_like_contiene(texto: str) -> str:
    """Patrón ``%texto%`` en minúsculas con ``%``, ``_`` y ``\\`` escapados.

    Sin escapar, una búsqueda como ``"%"`` o ``"_"`` actúa como comodín y devuelve
    toda la tabla en vez de buscar el carácter literal.
    """
    t = texto.lower()
    t = t.replace(ESCAPE_LIKE, ESCAPE_LIKE * 2)
    t = t.replace("%", ESCAPE_LIKE + "%").replace("_", ESCAPE_LIKE + "_")
    return f"%{t}%"
