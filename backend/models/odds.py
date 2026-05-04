"""Modelo ``Odds``: una cuota concreta de un bookie en un partido y mercado."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.bookmaker import Bookmaker
    from backend.models.market import Market
    from backend.models.match import Match
    from backend.models.snapshot import Snapshot


class Odds(Base, TimestampMixin):
    __tablename__ = "odds"
    __table_args__ = (
        Index("ix_odds_match_bookmaker_market", "match_id", "bookmaker_id", "market_id"),
        Index("ix_odds_snapshot", "snapshot_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False,
    )
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False,
    )
    bookmaker_id: Mapped[int] = mapped_column(
        ForeignKey("bookmakers.id", ondelete="RESTRICT"), nullable=False,
    )
    market_id: Mapped[int] = mapped_column(
        ForeignKey("markets.id", ondelete="RESTRICT"), nullable=False,
    )

    outcome_name: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome_point: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 2), nullable=True,
    )
    price: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    bookmaker_last_update: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    snapshot: Mapped["Snapshot"] = relationship(back_populates="odds")
    match: Mapped["Match"] = relationship(back_populates="odds")
    bookmaker: Mapped["Bookmaker"] = relationship(back_populates="odds")
    market: Mapped["Market"] = relationship(back_populates="odds")

    def __repr__(self) -> str:
        return (
            f"<Odds id={self.id} match={self.match_id} bm={self.bookmaker_id} "
            f"mkt={self.market_id} outcome={self.outcome_name!r} price={self.price}>"
        )
