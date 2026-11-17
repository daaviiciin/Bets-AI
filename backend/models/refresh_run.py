"""Ejecuciones del pipeline completo de datos (Flashscore + cuotas Winamax + picks del día).

Una fila por ejecución, la lance la app al abrirse (``origen="app"``) o el CLI ``daily_refresh``
(``origen="cli"``, el de la tarea programada). Sirve para saber cuándo fue el último refresh y
decidir si al abrir la app hay que lanzar otro. ``finished_at`` vacío = en curso o cortado (la
API se reinició a mitad): quién corre de verdad lo sabe el proceso, no la BD.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base

REFRESH_ORIGEN_APP = "app"
REFRESH_ORIGEN_CLI = "cli"


class RefreshRun(Base):
    __tablename__ = "refresh_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    origen: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: ``None`` mientras corre; al acabar, ``True`` si ningún paso falló.
    ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Errores de los pasos (el pipeline sigue aunque falle uno).
    errores: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
