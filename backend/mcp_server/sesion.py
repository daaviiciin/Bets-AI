"""Sesión de BD de corta duración y de solo lectura para las herramientas del MCP.

Cada llamada abre una sesión sobre una conexión con ``postgresql_readonly`` (PostgreSQL rechaza
cualquier ``INSERT``/``UPDATE``/``DELETE``), y al terminar hace ``rollback`` y la cierra. Es la
segunda barrera: la primera es no llamar nunca a funciones que guardan (``asegurar_picks_de_hoy``,
``refresh_*``) ni a las que salen a la red.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

from backend.app.database import get_engine


@lru_cache(maxsize=1)
def _fabrica_lectura() -> sessionmaker[Session]:
    motor = get_engine().execution_options(postgresql_readonly=True)
    return sessionmaker(bind=motor, autocommit=False, autoflush=False, expire_on_commit=False)


@contextmanager
def sesion_lectura() -> Iterator[Session]:
    """``with sesion_lectura() as s:`` → sesión que nunca escribe (rollback al salir)."""
    session = _fabrica_lectura()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


__all__ = ["sesion_lectura"]
