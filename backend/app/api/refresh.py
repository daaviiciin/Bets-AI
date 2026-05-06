"""Endpoint para refrescar todas las cuotas (scraping + ingest + cleanup)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.schemas.refresh import RefreshSummaryOut
from backend.services.daily_refresh import DailyRefreshOrchestrator
from backend.services.match_results_updater import MatchResultsUpdater
from backend.services.refresh_service import (
    DEFAULT_STALE_MATCH_BUFFER_HOURS,
    RefreshService,
)

router = APIRouter(prefix="/refresh", tags=["refresh"])
log = logging.getLogger(__name__)


class LeagueUpdateOut(BaseModel):
    league_name: str
    sport_key: str
    events_fetched: int
    matches_updated: int
    matches_already_ok: int
    matches_not_found: int
    skipped_unmapped: int


class ResultsRefreshSummaryOut(BaseModel):
    started_at: str
    finished_at: str
    duration_seconds: float
    total_updated: int
    total_already_ok: int
    total_not_found: int
    per_league: list[LeagueUpdateOut] = Field(default_factory=list)


class DailyRefreshResultsBlockOut(BaseModel):
    total_updated: int
    total_already_ok: int
    total_not_found: int


class DailyRefreshStatsBlockOut(BaseModel):
    matches_matched: int
    matches_unmatched: int
    stats_inserted: int
    stats_updated: int
    fetch_errors: int
    parse_errors: int


class DailyRefreshSummaryOut(BaseModel):
    started_at: str
    finished_at: str
    duration_seconds: float
    ok: bool
    results: DailyRefreshResultsBlockOut | None = None
    stats: DailyRefreshStatsBlockOut | None = None
    errors: list[str] = Field(default_factory=list)


@router.post("", response_model=RefreshSummaryOut)
def trigger_refresh(
    stale_match_buffer_hours: int = Query(
        DEFAULT_STALE_MATCH_BUFFER_HOURS,
        ge=0,
        le=72,
        description=(
            "Horas de margen tras el comienzo del partido antes de borrarlo. "
            "Default 6h: damos 6h por seguridad por si hay prórrogas largas."
        ),
    ),
    db: Session = Depends(get_db),
) -> RefreshSummaryOut:
    """Refresca cuotas de las 8 competiciones.

    **Importante**: cada llamada consume aproximadamente **16 créditos** de
    The Odds API (8 ligas × 2 mercados). Úsalo con moderación.

    Pasos:

    1. Captura cuotas frescas con el scraper.
    2. Las inserta en la BD (con un nuevo snapshot).
    3. Borra los snapshots anteriores (cascade borra sus odds).
    4. Borra los partidos cuyo commence_time ya pasó.

    Si el scraping falla totalmente, la BD se queda con los datos antiguos.
    """
    service = RefreshService(db, log=log)
    summary = service.refresh_all(stale_match_buffer_hours=stale_match_buffer_hours)
    return RefreshSummaryOut(
        started_at=summary.started_at,
        finished_at=summary.finished_at,
        duration_seconds=summary.duration_seconds,
        ok=summary.ok,
        leagues_total=summary.leagues_total,
        leagues_ok=summary.leagues_ok,
        leagues_error=summary.leagues_error,
        snapshots_deleted=summary.snapshots_deleted,
        past_matches_deleted=summary.past_matches_deleted,
        matches_inserted=summary.matches_inserted,
        odds_inserted=summary.odds_inserted,
        requests_used_total=summary.requests_used_total,
        requests_remaining=summary.requests_remaining,
        errors=summary.errors,
    )


@router.post("/results", response_model=ResultsRefreshSummaryOut)
def refresh_results(
    max_pages: int = Query(
        3, ge=1, le=10,
        description="Páginas de Sofascore a recorrer por liga (~30 partidos cada una).",
    ),
    db: Session = Depends(get_db),
) -> ResultsRefreshSummaryOut:
    """Actualiza el resultado de los partidos finalizados desde Sofascore.

    **Gratis** (no consume créditos del Odds API). Idempotente: si un
    partido ya tiene el resultado correcto en BD, no lo modifica.

    Recorre las 5 ligas top y mapea los eventos finalizados de Sofascore
    contra los ``Match`` del Odds API en BD. Los partidos no encontrados
    son típicamente partidos pasados que nunca llegamos a capturar
    (capturamos solo el horizonte futuro próximo).
    """
    updater = MatchResultsUpdater(db, log=log)
    summary = updater.update_all(max_pages=max_pages)
    db.commit()
    return ResultsRefreshSummaryOut(
        started_at=summary.started_at.isoformat(),
        finished_at=summary.finished_at.isoformat(),
        duration_seconds=(summary.finished_at - summary.started_at).total_seconds(),
        total_updated=summary.total_updated,
        total_already_ok=summary.total_already_ok,
        total_not_found=summary.total_not_found,
        per_league=[
            LeagueUpdateOut(
                league_name=r.league_name,
                sport_key=r.sport_key,
                events_fetched=r.events_fetched,
                matches_updated=r.matches_updated,
                matches_already_ok=r.matches_already_ok,
                matches_not_found=r.matches_not_found,
                skipped_unmapped=r.skipped_unmapped,
            )
            for r in summary.per_league
        ],
    )


@router.post("/daily", response_model=DailyRefreshSummaryOut)
def refresh_daily(
    stats_days_back: int = Query(
        7, ge=1, le=30,
        description="Ventana en días para buscar partidos sin stats Flashscore.",
    ),
    stats_all_leagues: bool = Query(
        False, description="Incluir competiciones europeas además de top-5.",
    ),
    db: Session = Depends(get_db),
) -> DailyRefreshSummaryOut:
    """Pipeline diario unificado (gratis): resultados + stats post-partido.

    Encadena en orden:

    1. ``MatchResultsUpdater`` (Sofascore unofficial API) — marca partidos
       como ``finished`` con ``home_score`` / ``away_score`` / ``played_at``.
    2. ``FlashscoreStatsIngester`` (Flashscore ``df_st_1``) — ingesta xG,
       posesión, tiros, córners, tarjetas, etc. para los partidos
       finalizados sin stats todavía.

    No consume créditos del Odds API. Idempotente. Si un paso falla, el
    siguiente sigue ejecutándose y los errores se reflejan en la respuesta.
    """
    orchestrator = DailyRefreshOrchestrator(db, log=log)
    summary = orchestrator.run(
        stats_days_back=stats_days_back,
        stats_only_top5=not stats_all_leagues,
    )

    return DailyRefreshSummaryOut(
        started_at=summary.started_at.isoformat(),
        finished_at=summary.finished_at.isoformat(),
        duration_seconds=summary.duration_seconds,
        ok=summary.ok,
        results=DailyRefreshResultsBlockOut(
            total_updated=summary.results.total_updated,
            total_already_ok=summary.results.total_already_ok,
            total_not_found=summary.results.total_not_found,
        ) if summary.results is not None else None,
        stats=DailyRefreshStatsBlockOut(
            matches_matched=summary.stats.matches_matched,
            matches_unmatched=summary.stats.matches_unmatched,
            stats_inserted=summary.stats.stats_inserted,
            stats_updated=summary.stats.stats_updated,
            fetch_errors=summary.stats.fetch_errors,
            parse_errors=summary.stats.parse_errors,
        ) if summary.stats is not None else None,
        errors=summary.errors,
    )
