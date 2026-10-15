"""Modelo ``MatchAbsence``: jugadores de baja o en duda para un partido.

Fuente: Flashscore (sección "Bajas" de la pestaña Alineaciones), disponible
días antes del partido y también para partidos jugados.

Cada refresh **sustituye** las bajas del partido por las actuales: un jugador
que se recupera desaparece de la lista. Para partidos ya jugados, la última
consulta posterior al inicio deja la foto definitiva.

El jugador se guarda por su ID de Flashscore + nombre, sin FK a ``players``:
los jugadores de ``players`` vienen de Sofascore/Winamax y cruzarlos por
nombre es trabajo de la fase 3.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MatchAbsence(Base):
    __tablename__ = "match_absences"
    __table_args__ = (
        UniqueConstraint("match_id", "player_external_id", name="uq_match_absence_player"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    player_external_id: Mapped[str] = mapped_column(String(32), nullable=False)
    player_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # "missing" (baja confirmada) | "doubtful" (duda)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Texto libre de Flashscore, p. ej. "Lesión - rodilla", "Sanción".
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<MatchAbsence match={self.match_id} team={self.team_id} "
            f"player={self.player_name!r} status={self.status} reason={self.reason!r}>"
        )
