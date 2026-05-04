"""Modelo ``Bookmaker``: una casa de apuestas (Winamax, Pinnacle, ...)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.odds import Odds


class Bookmaker(Base, TimestampMixin):
    __tablename__ = "bookmakers"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str | None] = mapped_column(String(8), nullable=True)

    odds: Mapped[list["Odds"]] = relationship(back_populates="bookmaker")

    def __repr__(self) -> str:
        return f"<Bookmaker id={self.id} key={self.key!r}>"
