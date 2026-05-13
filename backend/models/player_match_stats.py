"""Modelo ``PlayerMatchStats``: estadísticas de un jugador en un partido concreto.

Granularidad por jugador y partido. Una fila por
``(match_id, player_id)``. Es la base de datos para predecir player props
("Vinicius hará 1+ tiros a puerta", "Lewandowski hará 2+ tiros") porque
da el histórico individual partido a partido, en lugar de la media
agregada de :class:`PlayerSeasonStats`.

Fuente principal: Sofascore ``/api/v1/event/{event_id}/lineups``, que
para cada jugador titular trae goles, tiros, tiros a puerta, tarjetas,
minutos jugados y rating.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.match import Match
    from backend.models.player import Player
    from backend.models.team import Team


class PlayerMatchStats(Base, TimestampMixin):
    """Stats individuales de un jugador en un partido concreto."""

    __tablename__ = "player_match_stats"
    __table_args__ = (
        UniqueConstraint("match_id", "player_id", name="uq_pms_match_player"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # Source tracking
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="sofascore")
    source_match_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    # Estado del jugador en el partido
    is_starter: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_substitute: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    minutes_played: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_in_match: Mapped[str | None] = mapped_column(String(8), nullable=True)
    shirt_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Output ofensivo
    goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bb_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assists: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_off_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_blocked: Mapped[int | None] = mapped_column(Integer, nullable=True)
    big_chances_created: Mapped[int | None] = mapped_column(Integer, nullable=True)
    big_chances_missed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_goals: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    expected_assists: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    # Pases / juego
    touches: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passes_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passes_accurate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    key_passes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crosses_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crosses_accurate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    long_balls_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    long_balls_accurate: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Defensa
    tackles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interceptions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clearances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blocks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duels_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duels_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aerial_duels_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dribbles_succeeded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dribbles_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Disciplina
    fouls_committed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fouls_drawn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yellow_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    red_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offsides: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Portero
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goals_prevented: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    goals_conceded: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Rating Sofascore (0-10, dos decimales)
    rating: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)

    # Relaciones
    match: Mapped["Match"] = relationship()
    player: Mapped["Player"] = relationship()
    team: Mapped["Team"] = relationship()

    def __repr__(self) -> str:
        side = "home" if self.is_home else "away"
        return (
            f"<PlayerMatchStats match={self.match_id} player={self.player_id} "
            f"({side}) min={self.minutes_played} g={self.goals} sh={self.shots} "
            f"sot={self.shots_on_target} rating={self.rating}>"
        )
