"""Modelo ``League``: una competición (LaLiga, Premier, Champions, ...)."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.match import Match
    from backend.models.snapshot import Snapshot


class League(Base, TimestampMixin):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(primary_key=True)
    sport_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)

    bb_sd_margin: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    bb_sd_team_points: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    bb_sd_game_total: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)

    matches: Mapped[list["Match"]] = relationship(back_populates="league")
    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="league")

    def __repr__(self) -> str:
        return f"<League id={self.id} sport_key={self.sport_key!r}>"
