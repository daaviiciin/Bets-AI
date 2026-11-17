"""Modelo ``Bookmaker``: una casa de apuestas (Winamax, bet365, ...).

``role`` dice qué papel juega al comparar cuotas (``soft`` = casa blanda, ``sharp`` = referencia
afilada tipo Pinnacle, ``exchange``); ``flashscore_id`` es su id en el comparador de cuotas de
Flashscore (``backend/scrapers/flashscore_cuotas.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

BOOKMAKER_ROLE_SOFT = "soft"
BOOKMAKER_ROLE_SHARP = "sharp"
BOOKMAKER_ROLE_EXCHANGE = "exchange"

if TYPE_CHECKING:
    from backend.models.odds import Odds


class Bookmaker(Base, TimestampMixin):
    __tablename__ = "bookmakers"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str | None] = mapped_column(String(8), nullable=True)
    role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    flashscore_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)

    odds: Mapped[list["Odds"]] = relationship(back_populates="bookmaker")

    def __repr__(self) -> str:
        return f"<Bookmaker id={self.id} key={self.key!r}>"
