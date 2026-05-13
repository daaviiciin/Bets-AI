"""Modelo ``PlayerSeasonStats``: stats individuales de un jugador en una temporada.

Foco en disparos y tiros a puerta, métricas críticas para predecir el mercado
"shots on target" de jugador (atacantes principalmente).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.player import Player
    from backend.models.season import Season
    from backend.models.team import Team


class PlayerSeasonStats(Base, TimestampMixin):
    __tablename__ = "player_season_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "season_id", name="uq_player_stats_player_season"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True,
    )

    matches_played: Mapped[int | None] = mapped_column(Integer, nullable=True)
    starts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nineties: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)

    goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assists: Mapped[int | None] = mapped_column(Integer, nullable=True)
    penalties_made: Mapped[int | None] = mapped_column(Integer, nullable=True)
    penalties_attempted: Mapped[int | None] = mapped_column(Integer, nullable=True)

    shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_on_target_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    shots_per_90: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    shots_on_target_per_90: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    goals_per_shot: Mapped[Decimal | None] = mapped_column(Numeric(5, 3), nullable=True)
    goals_per_shot_on_target: Mapped[Decimal | None] = mapped_column(Numeric(5, 3), nullable=True)

    xg: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    npxg: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    xg_per_shot: Mapped[Decimal | None] = mapped_column(Numeric(5, 3), nullable=True)

    bb_points_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bb_rebounds_total: Mapped[int | None] = mapped_column(Integer, nullable=True)

    yellow_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    red_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)

    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    player: Mapped["Player"] = relationship(back_populates="season_stats")
    season: Mapped["Season"] = relationship(back_populates="player_stats")
    team: Mapped["Team | None"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<PlayerSeasonStats player={self.player_id} season={self.season_id} "
            f"min={self.minutes} sh={self.shots} sot={self.shots_on_target}>"
        )
