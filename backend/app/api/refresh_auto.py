"""Refresh automático al abrir la app: pipeline completo en segundo plano si los datos están viejos.

Sin el cupo de ``limite_refresh`` (1 cada 3 min): la app lo pide en cada apertura y no debe
dejar sin cupo al botón "Actualizar cuotas". Lo que evita lanzar scrapers en bucle es
``necesita_refresh`` y que solo corre uno a la vez.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.schemas.responses import RefreshAutoOut
from backend.services.refresco_auto import EstadoRefresh, refresco_auto

router = APIRouter(prefix="/refresh", tags=["refresh"])


def _out(e: EstadoRefresh) -> RefreshAutoOut:
    h = e.historial
    return RefreshAutoOut(
        en_curso=e.en_curso,
        lanzado=e.lanzado,
        ultimo_inicio=h.ultimo_inicio,
        ultimo_fin=h.ultimo_fin,
        ultimo_ok=h.ultimo_ok,
        ultimo_errores=h.ultimo_errores,
    )


@router.get("/auto", response_model=RefreshAutoOut)
def get_auto(db: Session = Depends(get_db)) -> RefreshAutoOut:
    """Estado del refresh automático (la portada lo consulta mientras corre)."""
    return _out(refresco_auto.estado(db))


@router.post("/auto", response_model=RefreshAutoOut)
def post_auto(db: Session = Depends(get_db)) -> RefreshAutoOut:
    """Al abrir la app: si toca, lanza el pipeline completo y responde enseguida."""
    return _out(refresco_auto.asegurar(db))
