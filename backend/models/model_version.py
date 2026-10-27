"""Versiones del modelo de predicción v2 (fase 3, bloque B8).

Una versión fija **cómo** se predice (hiperparámetros, calibración por familia, peso del mercado
y familias de mercado habilitadas para los picks) y guarda las métricas del backtest con el que
se aprobó. Los ratings en sí se reajustan con los datos del momento (el ajuste tarda centésimas),
así que no hace falta guardar uno por día; los picks guardan la etiqueta de la versión.

Estados: ``candidate`` (backtest hecho, sin promover), ``active`` (la que usa la app; solo una)
y ``retired``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin

MODEL_STATUS_CANDIDATE = "candidate"
MODEL_STATUS_ACTIVE = "active"
MODEL_STATUS_RETIRED = "retired"


class ModelVersion(Base, TimestampMixin):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tag: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=MODEL_STATUS_CANDIDATE)
    #: ``RatingsHyper`` como dict.
    hyperparams: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    #: ``Calibracion.to_dict()``: Platt (a, b, n) por familia.
    calibration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    #: Peso de la probabilidad sin margen de Winamax en la mezcla (0 = solo modelo).
    market_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: Familias que pasaron la validación y pueden dar picks.
    enabled_families: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    backtest_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
