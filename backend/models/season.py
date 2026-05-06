"""Modelo ``Season``: una temporada de una competición (LaLiga 2025-26, etc.)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.league import League
    from backend.models.player_stats import PlayerSeasonStats
    from backend.models.team_stats import TeamSeasonStats


class Season(Base, TimestampMixin):
    __tablename__ = "seasons"
    __table_args__ = (
        UniqueConstraint("league_id", "year_start", name="uq_season_league_year"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    year_start: Mapped[int] = mapped_column(Integer, nullable=False)
    year_end: Mapped[int] = mapped_column(Integer, nullable=False)

    league: Mapped["League"] = relationship()
    team_stats: Mapped[list["TeamSeasonStats"]] = relationship(
        back_populates="season", cascade="all, delete-orphan",
    )
    player_stats: Mapped[list["PlayerSeasonStats"]] = relationship(
        back_populates="season", cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Season id={self.id} name={self.name!r} league_id={self.league_id}>"
