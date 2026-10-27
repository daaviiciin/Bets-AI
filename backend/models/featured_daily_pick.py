"""Picks del día del modelo: simples y combinada, congelados al generarse (transparencia)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class FeaturedDailyPick(Base, TimestampMixin):
    """Una fila por día civil: snapshot de picks y corrección posterior.

    ``rules_version`` ``v1``: filas antiguas (día UTC, hasta 3 simples de confianza alta).
    ``v2``: día de Madrid, simples con cuota 1,30–1,70 y una combinada 3–6. En las ``v2`` un
    trigger de PostgreSQL impide cambiar los picks una vez guardados: solo cambia su corrección
    (``grading_snapshot`` y ``last_graded_at``).
    """

    __tablename__ = "featured_daily_picks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pick_date: Mapped[date] = mapped_column(Date(), nullable=False, unique=True, index=True)

    #: Simples: dicts compatibles con ``grade_single_leg`` / combinadas.
    picks_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    #: Combinada del día (``{"legs": [...], "combined_odds", "joint_probability", ...}``) o null.
    combo_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    grading_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    last_graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    rules_version: Mapped[str] = mapped_column(String(8), nullable=False, server_default="v1")
    #: Modelo que dio las probabilidades (``legacy-v1`` hasta que se promueva el v2).
    model_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Motivos y recuentos de la selección (cuántos candidatos, por qué hay menos de 3…).
    selection_meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: SHA-256 del JSON canónico de simples + combinada, para comprobar que no se tocaron.
    picks_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
