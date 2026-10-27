"""Picks diarios del modelo (transparencia y corrección post-partido)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.deps.rate_limit import limite_featured_picks, limite_picks_hoy
from backend.app.schemas.responses import (
    FeaturedDailyPickOut,
    FeaturedPicksMetricsOut,
    FeaturedTodayOut,
)
from backend.models import FeaturedDailyPick
from backend.services.featured_daily_picks import (
    PicksDeHoy,
    aggregate_leg_metrics,
    asegurar_picks_de_hoy,
    combinada_corregida,
    dia_de_picks,
    fila_del_dia,
    refresh_all_grading,
    simples_corregidas,
)

router = APIRouter(prefix="/featured-picks", tags=["featured-picks"])
log = logging.getLogger(__name__)


def _serialize(row: FeaturedDailyPick) -> FeaturedDailyPickOut:
    return FeaturedDailyPickOut(
        pick_date=row.pick_date,
        legs=simples_corregidas(row),
        combo=combinada_corregida(row),
        rules_version=row.rules_version,
        model_version=row.model_version,
        generated_at=row.generated_at,
        selection_meta=row.selection_meta,
        last_graded_at=row.last_graded_at,
        created_at=row.created_at,
    )


def _today_out(res: PicksDeHoy) -> FeaturedTodayOut:
    return FeaturedTodayOut(
        estado=res.estado,
        motivo=res.motivo,
        cuotas_actualizadas=res.cuotas_actualizadas,
        pick=_serialize(res.fila) if res.fila is not None else None,
    )


@router.get("/today", response_model=FeaturedTodayOut)
def get_today(db: Session = Depends(get_db)) -> FeaturedTodayOut:
    """Picks de hoy (día de Madrid) si ya se generaron; si no, ``estado="pendiente"``.

    No genera nada: para eso está ``POST /featured-picks/today`` (lo llama la portada).
    """
    row = fila_del_dia(db, dia_de_picks(datetime.now(timezone.utc)))
    if row is None:
        return FeaturedTodayOut(estado="pendiente")
    return FeaturedTodayOut(estado="listo", pick=_serialize(row))


@router.post("/today", response_model=FeaturedTodayOut, dependencies=[Depends(limite_picks_hoy)])
def post_today(db: Session = Depends(get_db)) -> FeaturedTodayOut:
    """Genera y congela los picks de hoy si aún no existen (primera apertura del día).

    Si las cuotas de hoy están viejas, antes las descarga de Winamax (~30 s).
    """
    return _today_out(asegurar_picks_de_hoy(db, actualizar_cuotas=True, log=log))


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
    response_model=FeaturedTodayOut,
    dependencies=[Depends(limite_featured_picks)],
)
def post_ensure_date(
    pick_date: date | None = Query(default=None, description="Solo hoy (día de Madrid)."),
    db: Session = Depends(get_db),
) -> FeaturedTodayOut:
    """Asegura los picks de hoy sin descargar cuotas. Otra fecha → 422: generar picks de un día
    pasado con los datos de hoy falsearía el historial."""

    hoy = dia_de_picks(datetime.now(timezone.utc))
    if pick_date is not None and pick_date != hoy:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Solo se pueden generar los picks de hoy ({hoy.isoformat()}).",
        )
    return _today_out(asegurar_picks_de_hoy(db, log=log))
