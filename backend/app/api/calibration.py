"""Endpoint de calibración del modelo Poisson.

Devuelve el último reporte persistido en :class:`CalibrationReport` para que
el frontend pueda mostrar las métricas (accuracy, Brier, reliability bins,
grid search) sin tener que re-ejecutar el cálculo.

Para regenerar un reporte::

    python -m backend.tools.calibrate_poisson --save --note "MVP 25-26"
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.schemas.responses import CalibrationReportOut
from backend.models import CalibrationReport

router = APIRouter(prefix="/calibration", tags=["calibration"])


@router.get("/latest", response_model=CalibrationReportOut)
def get_latest_calibration(db: Session = Depends(get_db)) -> CalibrationReportOut:
    """Último reporte de calibración persistido."""
    row = db.execute(
        select(CalibrationReport).order_by(CalibrationReport.created_at.desc())
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Aún no hay ningún reporte de calibración. "
                "Lánzalo con `python -m backend.tools.calibrate_poisson --save`."
            ),
        )
    return CalibrationReportOut(
        id=row.id,
        created_at=row.created_at,
        home_advantage=float(row.home_advantage),
        dixon_coles_rho=float(row.dixon_coles_rho) if row.dixon_coles_rho is not None else 0.0,
        n_samples=row.n_samples,
        note=row.note,
        accuracy_1x2=float(row.accuracy_1x2),
        brier_1x2=float(row.brier_1x2),
        log_loss_1x2=float(row.log_loss_1x2),
        brier_over_25=float(row.brier_over_25),
        log_loss_over_25=float(row.log_loss_over_25),
        brier_btts=float(row.brier_btts),
        log_loss_btts=float(row.log_loss_btts),
        mae_total_goals=float(row.mae_total_goals),
        mae_home_goals=float(row.mae_home_goals),
        mae_away_goals=float(row.mae_away_goals),
        observed_home_rate=float(row.observed_home_rate),
        observed_draw_rate=float(row.observed_draw_rate),
        observed_away_rate=float(row.observed_away_rate),
        observed_over_25_rate=float(row.observed_over_25_rate),
        observed_btts_rate=float(row.observed_btts_rate),
        per_league=row.per_league,
        reliability_home_win=row.reliability_home_win,
        reliability_over_25=row.reliability_over_25,
        grid_search=row.grid_search,
    )
