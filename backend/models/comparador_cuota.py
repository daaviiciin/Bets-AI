"""Modelo ``ComparadorCuota``: cuotas de varias casas leídas del comparador de Flashscore.

Va en una tabla propia, separada de ``odds`` / ``odds_history`` (que son las de Winamax leídas
con nuestro scraper), para que bet365 y el resto de casas no se cuelen en el EV del dashboard,
los picks del día ni el backtest del modelo, que leen esas tablas sin filtrar siempre por casa.

Funciona como ``odds_history``: una fila por precio de cada línea y casa; si en una lectura la
cuota no cambia, solo se alarga ``last_seen_at``. La fila vigente de una línea es la de
``last_seen_at`` más reciente. ``outcome_name`` va en **claves neutrales** (``home``, ``draw``,
``away``, ``over``, ``under``, ``yes``, ``no``, ``1X``, ``12``, ``X2``, ``odd``, ``even``,
``"2:1"``, ``"1/X"``…) y ``outcome_point`` es la línea **desde el punto de vista del local** en
los hándicaps (ver ``backend/scrapers/flashscore_cuotas.py``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class ComparadorCuota(Base):
    __tablename__ = "comparador_cuotas"
    __table_args__ = (
        Index(
            "ix_comparador_cuotas_linea",
            "match_id", "bookmaker_id", "market_id", "outcome_name", "first_seen_at",
        ),
        Index("ix_comparador_cuotas_match_visto", "match_id", "last_seen_at"),
    )

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
    outcome_name: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome_point: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    #: Cuota de apertura según Flashscore (la primera que publicó la casa).
    opening_price: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<ComparadorCuota id={self.id} match={self.match_id} bm={self.bookmaker_id} "
            f"mkt={self.market_id} {self.outcome_name!r}@{self.outcome_point} = {self.price}>"
        )
