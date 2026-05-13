"""Modelo ``TeamSeasonStats``: estadísticas agregadas de un equipo en una temporada."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.season import Season
    from backend.models.team import Team


class TeamSeasonStats(Base, TimestampMixin):
    """Una fila por (equipo, temporada). Se actualiza con cada scraping.

    Métricas clave para el cálculo de valor:

    * ``goals_for / goals_against``: fútbol — goles totales temporada.
    * ``bb_points_for / bb_points_against``: baloncesto — puntos anotados/recibidos (totales).
    * ``xg_for / xg_against``: expected goals (más estable que goles reales).
    * ``shots_*``: tendencia ofensiva, útil para mercados de córners y tarjetas.
    * ``points``: posición real en la tabla.
    """

    __tablename__ = "team_season_stats"
    __table_args__ = (
        UniqueConstraint("team_id", "season_id", name="uq_team_stats_team_season"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    matches_played: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wins: Mapped[int | None] = mapped_column(Integer, nullable=True)
    draws: Mapped[int | None] = mapped_column(Integer, nullable=True)
    losses: Mapped[int | None] = mapped_column(Integer, nullable=True)

    goals_for: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goals_against: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bb_points_for: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bb_points_against: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goal_diff: Mapped[int | None] = mapped_column(Integer, nullable=True)
    points: Mapped[int | None] = mapped_column(Integer, nullable=True)

    xg_for: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    xg_against: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    xg_diff: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    team: Mapped["Team"] = relationship()
    season: Mapped["Season"] = relationship(back_populates="team_stats")

    def __repr__(self) -> str:
        return (
            f"<TeamSeasonStats team={self.team_id} season={self.season_id} "
            f"GF={self.goals_for} xG={self.xg_for}>"
        )
