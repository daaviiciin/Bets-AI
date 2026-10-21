"""Picks diarios destacados (transparencia y corrección post-partido)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.deps.rate_limit import limite_featured_picks
from backend.app.schemas.responses import FeaturedDailyPickOut, FeaturedPicksMetricsOut
from backend.models import FeaturedDailyPick
from backend.services.featured_daily_picks import (
    aggregate_leg_metrics,
    ensure_row_for_date,
    refresh_all_grading,
)

router = APIRouter(prefix="/featured-picks", tags=["featured-picks"])


def _serialize(row: FeaturedDailyPick) -> FeaturedDailyPickOut:
    if row.grading_snapshot and row.grading_snapshot.get("legs"):
        legs = list(row.grading_snapshot["legs"])
    else:
        legs = list(row.picks_json)
    return FeaturedDailyPickOut(
        pick_date=row.pick_date,
        legs=legs,
        last_graded_at=row.last_graded_at,
        created_at=row.created_at,
    )


@router.get("/today", response_model=FeaturedDailyPickOut)
def get_today(
    db: Session = Depends(get_db),
) -> FeaturedDailyPickOut:
    """Genera (si falta) y devuelve la fila del día UTC con hasta 3 picks (conf. alta, cuota ≤ 2)."""

    today = datetime.now(timezone.utc).date()
    row = ensure_row_for_date(db, today)
    db.commit()
    db.refresh(row)
    return _serialize(row)


@router.get("/history", response_model=list[FeaturedDailyPickOut])
def list_history(
    days: int = Query(60, ge=1, le=365),
    db: Session = Depends(get_db),
) -> list[FeaturedDailyPickOut]:
    """Últimas filas guardadas ordenadas por fecha (hasta ``days`` filas)."""

    rows = db.scalars(
        select(FeaturedDailyPick)
        .order_by(FeaturedDailyPick.pick_date.desc())
        .limit(days),
    ).all()
    return [_serialize(r) for r in rows]


@router.get("/metrics", response_model=FeaturedPicksMetricsOut)
def get_metrics(db: Session = Depends(get_db)) -> FeaturedPicksMetricsOut:
    """Métricas agregadas de todas las patas propuestas históricamente."""

    data = aggregate_leg_metrics(db)
    return FeaturedPicksMetricsOut(**data)


@router.post("/refresh-grades", dependencies=[Depends(limite_featured_picks)])
def post_refresh_grades(db: Session = Depends(get_db)) -> dict[str, int]:
    """Recalifica todas las patas contra marcadores (típico tras pipeline diario)."""

    n = refresh_all_grading(db)
    return {"updated_rows": n}


@router.post(
    "/ensure-date",
    response_model=FeaturedDailyPickOut,
    dependencies=[Depends(limite_featured_picks)],
)
def post_ensure_date(
    pick_date: date | None = Query(default=None, description="Día civil UTC; por defecto hoy."),
    db: Session = Depends(get_db),
) -> FeaturedDailyPickOut:
    """Idempotente: asegura fila para ``pick_date`` (por defecto hoy UTC). Cron / backfill."""

    d = pick_date or datetime.now(timezone.utc).date()
    row = ensure_row_for_date(db, d)
    db.commit()
    db.refresh(row)
    return _serialize(row)
