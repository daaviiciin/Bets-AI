"""Endpoints de agregación para el dashboard de cuotas."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.schemas.responses import GradedOddsLineOut
from backend.services.graded_odds_service import list_graded_winamax_odds, summarize_winamax_odds_coverage

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class WinamaxCoverageOut(BaseModel):
    horizon_hours: int
    snapshots_winamax: int
    odds_rows_winamax_total: int
    football_matches_in_horizon: int
    matches_with_any_winamax_odds: int


@router.get("/winamax-coverage", response_model=WinamaxCoverageOut)
def get_winamax_odds_coverage(
    horizon_hours: int = Query(52, ge=1, le=14 * 24),
    db: Session = Depends(get_db),
) -> WinamaxCoverageOut:
    """Diagnóstico rápido: si hay snapshots/cuotas Winamax y cuántos partidos del horizonte tienen líneas."""

    data = summarize_winamax_odds_coverage(db, horizon_hours=horizon_hours)
    return WinamaxCoverageOut.model_validate(data)


@router.get("/graded-odds", response_model=list[GradedOddsLineOut])
def list_graded_dashboard_odds(
    horizon_hours: int = Query(48, ge=1, le=14 * 24),
    max_lines: int = Query(
        4000,
        ge=50,
        le=15_000,
        description="Límite de filas tras desduplicar por último snapshot Winamax.",
    ),
    bookmaker_key: str | None = Query(
        None,
        description="Ej. winamax_es. Si omites el filtro, se usan todas las "
        "filas ligadas al snapshot Winamax recuperado.",
    ),
    sport_key: str | None = Query(
        None,
        description="Clave ``League.sport_key`` (p. ej. soccer_epl). Vacío = todas las ligas de fútbol en horizonte.",
    ),
    match_id: int | None = Query(None, description="Solo las líneas de este partido."),
    db: Session = Depends(get_db),
) -> list[GradedOddsLineOut]:
    rows = list_graded_winamax_odds(
        db,
        horizon_hours=horizon_hours,
        max_lines=max_lines,
        bookmaker_key=bookmaker_key,
        sport_key=sport_key,
        match_id=match_id,
    )
    return [
        GradedOddsLineOut(
            match_id=r.match_id,
            league=r.league,
            league_sport_key=r.league_sport_key,
            home_team=r.home_team,
            away_team=r.away_team,
            commence_time=r.commence_time,
            market_key=r.market_key,
            outcome_name=r.outcome_name,
            outcome_point=r.outcome_point,
            player_name=r.player_name,
            bookmaker_key=r.bookmaker_key,
            bookmaker_title=r.bookmaker_title,
            offered_odds=r.offered_odds,
            confidence=r.confidence,
            model_probability=r.model_probability,
            model_ev=r.model_ev,
            recommendable=r.recommendable,
            lambda_home=r.lambda_home,
            lambda_away=r.lambda_away,
            implied_probability=r.implied_probability,
            model_summary_lines=list(r.model_summary_lines),
        )
        for r in rows
    ]
