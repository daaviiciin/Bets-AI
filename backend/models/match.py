"""Modelo ``Match``: un partido entre dos equipos en una competición."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.league import League
    from backend.models.odds import Odds
    from backend.models.team import Team


# Estados posibles del partido. Mantenemos los nombres alineados con la
# nomenclatura de Sofascore (``status.type``) para no traducir continuamente:
# ``notstarted`` / ``inprogress`` / ``finished`` / ``postponed`` / ``canceled``.
MATCH_STATUS_SCHEDULED = "scheduled"
MATCH_STATUS_LIVE = "live"
MATCH_STATUS_FINISHED = "finished"
MATCH_STATUS_POSTPONED = "postponed"
MATCH_STATUS_CANCELED = "canceled"


class Match(Base, TimestampMixin):
    __tablename__ = "matches"
    __table_args__ = (
        Index("ix_match_league_commence", "league_id", "commence_time"),
        Index("ix_match_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True,
    )
    # ID del evento equivalente en Sofascore (para descargar lineups, stats
    # de jugador, etc.). Se rellena perezosamente cuando ``MatchResultsUpdater``
    # localiza el partido en el feed de eventos finalizados.
    sofascore_event_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True,
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

    # Resultado y estado (rellenados tras el partido)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MATCH_STATUS_SCHEDULED, server_default=MATCH_STATUS_SCHEDULED,
    )
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    played_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
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

    @property
    def is_finished(self) -> bool:
        return self.status == MATCH_STATUS_FINISHED

    @property
    def total_goals(self) -> int | None:
        if self.home_score is None or self.away_score is None:
            return None
        return self.home_score + self.away_score

    @property
    def winner_1x2(self) -> str | None:
        """``"H"``, ``"D"`` o ``"A"`` si el partido ya tiene resultado."""
        if not self.is_finished or self.home_score is None or self.away_score is None:
            return None
        if self.home_score > self.away_score:
            return "H"
        if self.home_score < self.away_score:
            return "A"
        return "D"

    def __repr__(self) -> str:
        score = ""
        if self.is_finished and self.home_score is not None:
            score = f" {self.home_score}-{self.away_score}"
        return (
            f"<Match id={self.id} external_id={self.external_id!r} "
            f"home={self.home_team_id} away={self.away_team_id} status={self.status}{score}>"
        )
