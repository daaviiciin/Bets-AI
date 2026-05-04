"""Declarative base de SQLAlchemy y mixin reutilizables."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base declarativa común a todos los modelos."""

    pass


class TimestampMixin:
    """Mixin que añade ``created_at`` con la fecha de creación en UTC."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
