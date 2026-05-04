"""Modelo ``Market``: tipo de apuesta (h2h, totals, double_chance, ...)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.odds import Odds


class Market(Base, TimestampMixin):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    odds: Mapped[list["Odds"]] = relationship(back_populates="market")

    def __repr__(self) -> str:
        return f"<Market id={self.id} key={self.key!r}>"
