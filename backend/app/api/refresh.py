"""Endpoint para refrescar todas las cuotas (scraping + ingest + cleanup)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.config import get_settings
from backend.app.schemas.refresh import RefreshSummaryOut
from backend.services.daily_refresh import DailyRefreshOrchestrator, DailyRefreshSummary
from backend.services.match_results_updater import MatchResultsUpdater
from backend.services.refresh_service import (
    DEFAULT_STALE_MATCH_BUFFER_HOURS,
    RefreshService,
)
from backend.services.winamax_odds_ingester import WinamaxIngestResult, WinamaxOddsIngester

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


class DailyRefreshStandingsBlockOut(BaseModel):
    leagues_ok: list[str]
    teams_upserted: int
    teams_created: int


class DailyRefreshFsResultsBlockOut(BaseModel):
    matches_pending: int
    matches_updated: int
    still_pending: int
    feeds_fetched: int


class DailyRefreshAbsencesBlockOut(BaseModel):
    matches_eligible: int
    matches_fetched: int
    absences_saved: int
    doubtful_saved: int
    fetch_errors: int


class DailyRefreshFixturesBlockOut(BaseModel):
    fixtures_fetched: int
    fixtures_inserted: int
    fixtures_updated: int
    skipped_duplicate_db: int
    skipped_not_future: int
    skipped_unknown_league: int
    duration_seconds: float
    errors: list[str] = Field(default_factory=list)


class DailyRefreshSummaryOut(BaseModel):
    started_at: str
    finished_at: str
    duration_seconds: float
    ok: bool
    fixtures: DailyRefreshFixturesBlockOut | None = None
    results: DailyRefreshResultsBlockOut | None = None
    stats: DailyRefreshStatsBlockOut | None = None
    fs_results: DailyRefreshFsResultsBlockOut | None = None
    standings: DailyRefreshStandingsBlockOut | None = None
    absences: DailyRefreshAbsencesBlockOut | None = None
    errors: list[str] = Field(default_factory=list)


class WinamaxIngestSummaryOut(BaseModel):
    matches_in_db: int
    matches_matched: int
    matches_unmatched: int
    matches_failed: int
    snapshots_created: int
    snapshots_pruned: int = 0
    odds_inserted: int
    odds_carried_forward: int = 0
    duration_seconds: float
    errors: list[str] = Field(default_factory=list)


class OfficialPipelineSummaryOut(BaseModel):
    """Fixtures próximos (Flashscore) + pipeline Sofascore/stats + Winamax."""

    started_at: str
    finished_at: str
    duration_seconds: float
    ok: bool
    daily: DailyRefreshSummaryOut
    winamax: WinamaxIngestSummaryOut
    note: str = Field(
        default=(
            "Confianza (alta/media/baja) la calcula el backend sobre modelo + mercado. "
            "Los próximos partidos se sincronizan desde Flashscore (`external_id` "
            "`flashscore:…`). Las cuotas pueden venir de Winamax sobre esos partidos."
        ),
    )


def _daily_refresh_summary_to_out(summary: DailyRefreshSummary) -> DailyRefreshSummaryOut:
    return DailyRefreshSummaryOut(
        started_at=summary.started_at.isoformat(),
        finished_at=summary.finished_at.isoformat(),
        duration_seconds=summary.duration_seconds,
        ok=summary.ok,
        fixtures=DailyRefreshFixturesBlockOut(
            fixtures_fetched=summary.fixtures.fixtures_fetched,
            fixtures_inserted=summary.fixtures.fixtures_inserted,
            fixtures_updated=summary.fixtures.fixtures_updated,
            skipped_duplicate_db=summary.fixtures.skipped_duplicate_db,
            skipped_not_future=summary.fixtures.skipped_not_future,
            skipped_unknown_league=summary.fixtures.skipped_unknown_league,
            duration_seconds=summary.fixtures.duration_seconds,
            errors=summary.fixtures.errors,
        ) if summary.fixtures is not None else None,
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
        fs_results=DailyRefreshFsResultsBlockOut(
            matches_pending=summary.fs_results.matches_pending,
            matches_updated=summary.fs_results.matches_updated,
            still_pending=summary.fs_results.still_pending,
            feeds_fetched=summary.fs_results.feeds_fetched,
        ) if summary.fs_results is not None else None,
        standings=DailyRefreshStandingsBlockOut(
            leagues_ok=summary.standings.leagues_ok,
            teams_upserted=summary.standings.teams_upserted,
            teams_created=summary.standings.teams_created,
        ) if summary.standings is not None else None,
        absences=DailyRefreshAbsencesBlockOut(
            matches_eligible=summary.absences.matches_eligible,
            matches_fetched=summary.absences.matches_fetched,
            absences_saved=summary.absences.absences_saved,
            doubtful_saved=summary.absences.doubtful_saved,
            fetch_errors=summary.absences.fetch_errors,
        ) if summary.absences is not None else None,
        errors=summary.errors,
    )


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
    """Refresca cuotas de las 8 competiciones vía The Odds API (de pago).

    **Importante**: cada llamada puede consumir ~**16 créditos** (8 ligas × 2 mercados)
    si ``ODDS_API_REFRESH_ENABLED=true`` y ``ODDS_API_KEY`` está configurada.
    Por defecto este endpoint está **bloqueado** (sin clave o con refresh desactivado)
    y responde **403** sin llamar a la API: **cero créditos**.

    Para el dashboard / pipeline sin Odds API usa ``POST /refresh/official``.

    Pasos (solo si Odds API está habilitada):

    1. Captura cuotas frescas con el scraper.
    2. Las inserta en la BD (con un nuevo snapshot).
    3. Borra los snapshots anteriores (cascade borra sus odds).
    4. Borra los partidos cuyo commence_time ya pasó.

    Si el scraping falla totalmente, la BD se queda con los datos antiguos.
    """
    settings = get_settings()
    if not settings.odds_api_refresh_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Refresco vía The Odds API desactivado (ODDS_API_REFRESH_ENABLED=false). "
                "No se ha consumido ningún crédito. Usa POST /refresh/official para datos "
                "sin The Odds API."
            ),
        )
    if not (settings.odds_api_key or "").strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Falta ODDS_API_KEY; no se llama a The Odds API ni se gastan créditos. "
                "Configura la clave y ODDS_API_REFRESH_ENABLED=true para usar este endpoint, "
                "o usa POST /refresh/official."
            ),
        )
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
        120, ge=1, le=365,
        description="Antigüedad máxima en días para buscar partidos sin stats Flashscore.",
    ),
    stats_all_leagues: bool = Query(
        False, description="Incluir competiciones europeas además de top-5.",
    ),
    fixtures_days_ahead: int = Query(
        7, ge=1, le=14,
        description="Días de calendario a cubrir con feeds de fixtures Flashscore (0..N-1).",
    ),
    db: Session = Depends(get_db),
) -> DailyRefreshSummaryOut:
    """Pipeline diario unificado (gratis): fixtures Flashscore, Sofascore, stats, jugadores.

    No consume créditos del Odds API. Idempotente. Si un paso falla, el
    siguiente sigue ejecutándose y los errores se reflejan en la respuesta.
    """
    orchestrator = DailyRefreshOrchestrator(db, log=log)
    summary = orchestrator.run(
        stats_days_back=stats_days_back,
        stats_only_top5=not stats_all_leagues,
        fixtures_days_ahead=fixtures_days_ahead,
    )

    return _daily_refresh_summary_to_out(summary)


@router.post("/official", response_model=OfficialPipelineSummaryOut)
def refresh_official_pipeline(
    stats_days_back: int = Query(
        120, ge=1, le=365,
        description="Antigüedad máxima en días para buscar partidos sin stats Flashscore.",
    ),
    stats_all_leagues: bool = Query(
        False, description="Incluir competiciones europeas además de top-5.",
    ),
    winamax_days_ahead: int = Query(
        7, ge=1, le=14,
        description="Horizonte de partidos programados en BD a intentar cubrir.",
    ),
    winamax_max_matches: int = Query(
        100, ge=1, le=220,
        description=(
            "Tope de partidos a scrapear en Winamax por corrida "
            "(repartidos entre Premier / Serie A / Bundesliga / La Liga / Ligue 1; "
            "luego se rellena por orden cronológico hasta este tope)."
        ),
    ),
    fixtures_days_ahead: int = Query(
        7, ge=1, le=14,
        description="Días de feeds Flashscore para poblar próximos partidos en BD.",
    ),
    db: Session = Depends(get_db),
) -> OfficialPipelineSummaryOut:
    """Pipeline principal sin The Odds API: fixtures Flashscore + pipeline diario + Winamax.

    1. Como ``/refresh/daily``: calendario próximo desde Flashscore, resultados Sofascore,
       stats post-partido, stats por jugador.
    2. Ingiere mercados Winamax sobre próximos ``Match`` del horizonte indicado.
    """
    t0 = datetime.now(timezone.utc)

    orchestrator = DailyRefreshOrchestrator(db, log=log)
    stats_top5_only = not stats_all_leagues
    dsum = orchestrator.run(
        stats_days_back=stats_days_back,
        stats_only_top5=stats_top5_only,
        fixtures_days_ahead=fixtures_days_ahead,
    )

    ingester = WinamaxOddsIngester(db, log=log, settings=get_settings())
    wres = ingester.ingest_upcoming(
        days_ahead=winamax_days_ahead,
        max_matches=winamax_max_matches,
        only_top5=stats_top5_only,
        sport="football",
        commit=True,
    )

    t1 = datetime.now(timezone.utc)
    pipeline_ok = dsum.ok and not wres.errors

    return OfficialPipelineSummaryOut(
        started_at=t0.isoformat(),
        finished_at=t1.isoformat(),
        duration_seconds=(t1 - t0).total_seconds(),
        ok=pipeline_ok,
        daily=_daily_refresh_summary_to_out(dsum),
        winamax=_winamax_summary_to_out(wres),
    )


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
        2, ge=1, le=7, description="Partidos programados de los próximos N días (default hoy y mañana).",
    ),
    max_matches: int = Query(200, ge=1, le=220, description="Tope de partidos por corrida."),
    db: Session = Depends(get_db),
) -> OddsRefreshOut:
    """Solo cuotas Winamax (top 5 + competiciones europeas), sin Flashscore.

    Es lo que usa el botón del dashboard (~15 s con una jornada europea completa). Stats,
    resultados, clasificación y bajas los trae la tarea programada (09/15/21 h).
    """
    ingester = WinamaxOddsIngester(
        db, log=log, settings=get_settings(), fetch_workers=INTERACTIVE_FETCH_WORKERS,
    )
    wres = ingester.ingest_upcoming(
        days_ahead=days_ahead,
        max_matches=max_matches,
        only_top5=False,
        sport="football",
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
        snapshots_created=wres.snapshots_created,
        snapshots_pruned=wres.snapshots_pruned,
        odds_inserted=wres.odds_inserted,
        odds_carried_forward=wres.odds_carried_forward,
        duration_seconds=wres.duration_seconds,
        errors=wres.errors,
    )
