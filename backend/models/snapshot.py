"""Modelo ``Snapshot``: cada ejecución del scraper queda registrada aquí."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.league import League
    from backend.models.odds import Odds


class Snapshot(Base, TimestampMixin):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("leagues.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="the_odds_api")
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    regions: Mapped[str | None] = mapped_column(String(64), nullable=True)
    markets: Mapped[str | None] = mapped_column(String(256), nullable=True)
    requests_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requests_used: Mapped[int | None] = mapped_column(Integer, nullable=True)

    league: Mapped["League"] = relationship(back_populates="snapshots")
    odds: Mapped[list["Odds"]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Snapshot id={self.id} league_id={self.league_id} captured_at={self.captured_at!r}>"
