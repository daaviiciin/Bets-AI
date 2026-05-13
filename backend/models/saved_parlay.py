"""Combinada guardada por un usuario (`legs_snapshot` como JSON estable)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Numeric, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.models.app_user import AppUser


class SavedParlay(Base, TimestampMixin):
    __tablename__ = "saved_parlays"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), index=True)

    title: Mapped[str | None] = mapped_column(String(160), nullable=True)

    #: Producto decimal de todas las cuotas al guardar (cuota combinada declarada por el usuario/libro).
    combined_odds: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    #: Producto independiente de P modelo donde todas las patas la tenían; si no: null.
    model_joint_probability: Mapped[Decimal | None] = mapped_column(Numeric(16, 8), nullable=True)

    #: Ser list[dict]: match_id, home_team, away_team, league, market_key, outcome_name ...
    legs_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    grading_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: pending | hit | miss | needs_review (resumen combinada cuando todas las patas tienen resultado).
    overall_result: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")

    last_graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["AppUser"] = relationship(back_populates="saved_parlays")

