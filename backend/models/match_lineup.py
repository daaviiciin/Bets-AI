"""Modelo ``MatchLineup``: alineación oficial de un partido antes de jugarse.

Fuente: Flashscore (``epmsse``, la misma consulta que da los jugadores de un partido
jugado), que marca ``inBaseLineup`` en cuanto se publica el once (~1 h antes). Se pide
bajo demanda al abrir un partido que empieza pronto (``services/match_lineups.py``) y se
guarda para no repetir la petición.

Como en :class:`MatchAbsence`, el jugador va por su ID de Flashscore + nombre, sin FK a
``players``. Tras el partido, el once real sale de ``player_match_stats``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class MatchLineup(Base):
    __tablename__ = "match_lineups"
    __table_args__ = (
        UniqueConstraint("match_id", "player_external_id", name="uq_match_lineup_player"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)

    player_external_id: Mapped[str] = mapped_column(String(32), nullable=False)
    player_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # "G" portero, "D" defensa, "M" centrocampista, "F" delantero; None si no se sabe.
    position: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_starter: Mapped[bool] = mapped_column(Boolean, nullable=False)

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<MatchLineup match={self.match_id} home={self.is_home} "
            f"player={self.player_name!r} starter={self.is_starter}>"
        )
