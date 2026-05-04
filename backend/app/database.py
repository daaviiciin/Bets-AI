"""Conexión a PostgreSQL: ``engine`` global, ``SessionLocal`` y dependencia FastAPI."""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Devuelve un único ``Engine`` de SQLAlchemy (patrón singleton).

    El engine mantiene un pool de conexiones; no debe crearse uno por petición.
    """
    settings = get_settings()
    return create_engine(
        settings.resolved_database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Devuelve la fábrica de sesiones para abrir transacciones."""
    return sessionmaker(
        bind=get_engine(),
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )


def get_db() -> Generator[Session, None, None]:
    """Dependencia para FastAPI: yield de una sesión que se cierra al terminar."""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
