"""Modelo ``Match``: un partido entre dos equipos en una competición."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.league import League
    from backend.models.odds import Odds
    from backend.models.team import Team


class Match(Base, TimestampMixin):
    __tablename__ = "matches"
    __table_args__ = (
        Index("ix_match_league_commence", "league_id", "commence_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True,
    )

    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    home_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False,
    )
    away_team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False,
    )

    commence_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
    )

    league: Mapped["League"] = relationship(back_populates="matches")
    home_team: Mapped["Team"] = relationship(
        back_populates="home_matches", foreign_keys=[home_team_id],
    )
    away_team: Mapped["Team"] = relationship(
        back_populates="away_matches", foreign_keys=[away_team_id],
    )
    odds: Mapped[list["Odds"]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Match id={self.id} external_id={self.external_id!r} "
            f"home={self.home_team_id} away={self.away_team_id}>"
        )
