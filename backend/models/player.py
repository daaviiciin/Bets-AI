"""Modelo ``Player``: un jugador (con su equipo actual y posición)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.player_stats import PlayerSeasonStats
    from backend.models.team import Team


class Player(Base, TimestampMixin):
    """Un jugador de fútbol.

    ``external_id`` + ``external_source`` forman la clave única estable
    contra la fuente de origen (p.ej. Sofascore player id). Permite hacer
    upsert sin colisiones por jugadores con el mismo nombre.
    """

    __tablename__ = "players"
    __table_args__ = (
        Index("ix_player_name_team", "name", "team_id"),
        UniqueConstraint("external_source", "external_id", name="uq_player_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    external_source: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default="sofascore",
    )
    nationality: Mapped[str | None] = mapped_column(String(8), nullable=True)
    position: Mapped[str | None] = mapped_column(String(16), nullable=True)
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True,
    )

    team: Mapped["Team | None"] = relationship()
    season_stats: Mapped[list["PlayerSeasonStats"]] = relationship(
        back_populates="player", cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Player id={self.id} name={self.name!r} pos={self.position}>"
