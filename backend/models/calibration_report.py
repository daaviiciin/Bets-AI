"""Modelo ``CalibrationReport``: snapshot de las métricas del modelo Poisson.

Cada vez que ejecutamos :mod:`backend.tools.calibrate_poisson` guardamos un
reporte completo en BD para poder mostrarlo en el dashboard sin tener que
re-correr el cálculo (que tarda ~40 s + créditos de Sofascore).

Las métricas detalladas (por liga, reliability bins) se guardan en JSON para
mantener el modelo simple y flexible — la estructura puede crecer en el futuro
(añadir BTTS reliability, distribución de scores, etc.) sin cambiar el schema.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TimestampMixin


class CalibrationReport(Base, TimestampMixin):
    __tablename__ = "calibration_reports"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Configuración del run
    home_advantage: Mapped[Decimal] = mapped_column(Numeric(5, 3), nullable=False)
    # Corrección Dixon-Coles aplicada en este run (0.0 = Poisson independiente).
    dixon_coles_rho: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, default=Decimal("0"),
    )
    n_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Métricas globales
    brier_1x2: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    log_loss_1x2: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    accuracy_1x2: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    brier_btts: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    log_loss_btts: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    brier_over_25: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    log_loss_over_25: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    mae_total_goals: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    mae_home_goals: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    mae_away_goals: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    observed_home_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    observed_draw_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    observed_away_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    observed_over_25_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    observed_btts_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)

    # Detalle (JSON)
    per_league: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    reliability_home_win: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    reliability_over_25: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    grid_search: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    def __repr__(self) -> str:
        return f"<CalibrationReport id={self.id} n={self.n_samples} acc={self.accuracy_1x2}>"
