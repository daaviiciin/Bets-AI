"""Endpoint del botón "Actualizar cuotas" del dashboard (solo Winamax).

El resto del refresco (calendario, resultados, stats, bajas) lo hace la tarea programada con
``python -m backend.tools.daily_refresh``, no la API.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.services.winamax_odds_ingester import WinamaxIngestResult, WinamaxOddsIngester

router = APIRouter(prefix="/refresh", tags=["refresh"])
log = logging.getLogger(__name__)


class WinamaxIngestSummaryOut(BaseModel):
    matches_in_db: int
    matches_matched: int
    matches_unmatched: int
    matches_failed: int
    matches_skipped_fresh: int = 0
    snapshots_created: int
    snapshots_pruned: int = 0
    odds_inserted: int
    odds_carried_forward: int = 0
    duration_seconds: float
    errors: list[str] = Field(default_factory=list)


class OddsRefreshOut(BaseModel):
    """Resumen de ``POST /refresh/odds`` (solo cuotas Winamax)."""
    finished_at: str
    ok: bool
    winamax: WinamaxIngestSummaryOut


# Alguien espera delante de la pantalla: descargas en paralelo, como un navegador (que abre
# hasta 6 conexiones por web). La tarea programada va de una en una con 1 s de pausa.
INTERACTIVE_FETCH_WORKERS = 4


@router.post("/odds", response_model=OddsRefreshOut)
def refresh_odds(
    days_ahead: int = Query(
        5, ge=1, le=7, description="Partidos programados de los próximos N días.",
    ),
    max_matches: int = Query(200, ge=1, le=220, description="Tope de partidos por corrida."),
    skip_fresh_minutes: int = Query(
        15, ge=0, le=180,
        description="No repite partidos cuyas cuotas se leyeron hace menos de N min (0 = todos).",
    ),
    db: Session = Depends(get_db),
) -> OddsRefreshOut:
    """Solo cuotas Winamax (top 5 + competiciones europeas), sin Flashscore.

    Es lo que usa el botón del dashboard. Pulsaciones seguidas no repiten los partidos que se
    acaban de leer (``skip_fresh_minutes``). Stats, resultados, clasificación y bajas los trae
    la tarea programada (09/15/21 h).
    """
    ingester = WinamaxOddsIngester(
        db, log=log, settings=get_settings(), fetch_workers=INTERACTIVE_FETCH_WORKERS,
    )
    wres = ingester.ingest_upcoming(
        days_ahead=days_ahead,
        max_matches=max_matches,
        only_top5=False,
        sport="football",
        skip_fresh_minutes=skip_fresh_minutes or None,
        commit=True,
    )
    return OddsRefreshOut(
        finished_at=datetime.now(timezone.utc).isoformat(),
        ok=not wres.errors,
        winamax=_winamax_summary_to_out(wres),
    )


def _winamax_summary_to_out(wres: WinamaxIngestResult) -> WinamaxIngestSummaryOut:
    return WinamaxIngestSummaryOut(
        matches_in_db=wres.matches_in_db,
        matches_matched=wres.matches_matched,
        matches_unmatched=wres.matches_unmatched,
        matches_failed=wres.matches_failed,
        matches_skipped_fresh=wres.matches_skipped_fresh,
        snapshots_created=wres.snapshots_created,
        snapshots_pruned=wres.snapshots_pruned,
        odds_inserted=wres.odds_inserted,
        odds_carried_forward=wres.odds_carried_forward,
        duration_seconds=wres.duration_seconds,
        errors=wres.errors,
    )
