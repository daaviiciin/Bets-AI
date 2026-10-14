"""Selección diaria de hasta 3 picks de alta confianza (métricas de la app)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class FeaturedDailyPick(Base, TimestampMixin):
    """Una fila por día civil (UTC): snapshot de picks y corrección posterior."""

    __tablename__ = "featured_daily_picks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pick_date: Mapped[date] = mapped_column(Date(), nullable=False, unique=True, index=True)

    #: Hasta 3 dicts compatibles con ``grade_single_leg`` / combinadas.
    picks_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    grading_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

