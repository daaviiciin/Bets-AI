"""Modelo ``OddsHistory``: evolución de cada línea de cuota a lo largo del tiempo.

A diferencia de :class:`Odds` (que vive ligada a un :class:`Snapshot` y se
purga con ``WINAMAX_SNAPSHOT_RETENTION``), esta tabla **no se borra**: es el
histórico que permite medir CLV (*closing line value*) en la fase 3.

Solo se inserta una fila cuando la cuota de una línea **cambia**. Si en una
corrida la cuota es la misma que la última registrada, se actualiza
``last_seen_at`` de esa fila. Así:

- **Apertura** de una línea = su fila con ``first_seen_at`` más antiguo.
- **Cierre** = su fila con ``last_seen_at`` más reciente antes del inicio.
- Cada fila dice durante qué intervalo ``[first_seen_at, last_seen_at]``
  estuvo vigente ese precio.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class OddsHistory(Base):
    __tablename__ = "odds_history"
    __table_args__ = (
        Index(
            "ix_odds_history_line",
            "match_id", "bookmaker_id", "market_id", "outcome_name", "first_seen_at",
        ),
    )

    # BigInteger: tabla que solo crece; evita migrar el PK más adelante.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False,
    )
    bookmaker_id: Mapped[int] = mapped_column(
        ForeignKey("bookmakers.id", ondelete="RESTRICT"), nullable=False,
    )
    market_id: Mapped[int] = mapped_column(
        ForeignKey("markets.id", ondelete="RESTRICT"), nullable=False,
    )
    player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL"), nullable=True,
    )

    outcome_name: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome_point: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<OddsHistory id={self.id} match={self.match_id} mkt={self.market_id} "
            f"outcome={self.outcome_name!r} point={self.outcome_point} price={self.price}>"
        )
