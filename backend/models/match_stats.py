"""Modelo ``MatchStats``: estadísticas post-partido de un equipo en un encuentro.

Una fila por ``(match_id, is_home)``. Se obtiene del feed
``df_st_1_<match_id>`` de Flashscore una vez el partido está finalizado.
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
    from backend.models.team import Team


class MatchStats(Base, TimestampMixin):
    """Stats por equipo en un partido concreto (tiros, córners, tarjetas, ...).

    Granularidad por equipo (no por jugador). Para stats individuales por
    jugador habrá un modelo dedicado más adelante.
    """

    __tablename__ = "match_stats"
    __table_args__ = (
        UniqueConstraint("match_id", "is_home", name="uq_match_stats_match_side"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # Source tracking (Flashscore)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="flashscore")
    source_match_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    # Expected goals
    xg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    xg_on_target: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    xa: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    # Posesión
    possession_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Tiros
    shots_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_off_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_blocked: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_inside_box: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_outside_box: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_woodwork: Mapped[int | None] = mapped_column(Integer, nullable=True)
    headed_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Ataque
    big_chances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    corners: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offsides: Mapped[int | None] = mapped_column(Integer, nullable=True)
    free_kicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    throw_ins: Mapped[int | None] = mapped_column(Integer, nullable=True)
    touches_in_box: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Defensa / disciplina
    fouls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yellow_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    red_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duels_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clearances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interceptions: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Portero
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goal_kicks: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Pases (con desglose %, completed, attempted)
    passes_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passes_completed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passes_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)

    long_passes_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    long_passes_completed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    long_passes_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)

    passes_final_third_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passes_final_third_completed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passes_final_third_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)

    crosses_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crosses_completed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crosses_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)

    tackles_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tackles_completed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tackles_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)

    match: Mapped["Match"] = relationship()
    team: Mapped["Team"] = relationship()

    def __repr__(self) -> str:
        side = "home" if self.is_home else "away"
        return (
            f"<MatchStats match={self.match_id} team={self.team_id} ({side}) "
            f"shots={self.shots_total} SoT={self.shots_on_target} xG={self.xg}>"
        )
