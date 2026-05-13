"""Usuario de aplicación para login MVP (combinadas guardadas por cuenta)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.saved_parlay import SavedParlay


class AppUser(Base, TimestampMixin):
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)

    saved_parlays: Mapped[list["SavedParlay"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

