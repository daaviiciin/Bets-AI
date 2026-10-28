"""Endpoints de partidos y cuotas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.app.database import get_db
from backend.app.schemas.responses import (
    MatchOut,
    MatchPreviewOut,
    Odds1x2Out,
)
from backend.models import League, Match
from backend.services.match_listing import latest_1x2_by_match
from backend.services.match_preview import MatchPreviewService

router = APIRouter(prefix="/matches", tags=["matches"])

TARGET_JORNADA_LEAGUES = frozenset({
    "soccer_epl",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_spain_la_liga",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
})


@router.get("", response_model=list[MatchOut])
def list_matches(
    league: str | None = Query(None, description="Sport key de la liga (ej. soccer_spain_la_liga)"),
    days_ahead: int = Query(14, ge=1, le=60, description="Horizonte en días"),
    days_back: int = Query(
        0, ge=0, le=30,
        description="Días hacia atrás para incluir partidos finalizados recientes (0 = solo próximos).",
    ),
    scope: str | None = Query(
        None,
        description="target = cinco grandes ligas + Champions, Europa y Conference League.",
    ),
    limit: int = Query(100, ge=1, le=500),
    with_odds: bool = Query(False, description="Añade el último 1X2 de Winamax de cada partido."),
    db: Session = Depends(get_db),
) -> list[MatchOut]:
    """Lista partidos en una ventana temporal, opcionalmente filtrados por liga.

    * ``days_ahead``: horizonte futuro (próximos partidos).
    * ``days_back``: incluye también partidos pasados de los últimos N días
      (útiles para mostrar resultados recientes con ``status='finished'``).
    * ``with_odds``: portada del dashboard, con el 1X2 en cada fila.
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days_back)
    until = now + timedelta(days=days_ahead)

    stmt = (
        select(Match)
        .where(Match.commence_time >= since)
        .where(Match.commence_time <= until)
        .options(
            joinedload(Match.league),
            joinedload(Match.home_team),
            joinedload(Match.away_team),
        )
        .order_by(Match.commence_time)
        .limit(limit)
    )
    if league:
        stmt = stmt.join(Match.league).where(League.sport_key == league)
    elif scope == "target":
        stmt = stmt.join(Match.league).where(League.sport_key.in_(TARGET_JORNADA_LEAGUES))

    matches = list(db.scalars(stmt).all())
    out = [MatchOut.model_validate(m) for m in matches]
    if with_odds:
        odds = latest_1x2_by_match(db, matches)
        for item in out:
            picked = odds.get(item.id)
            if picked is not None:
                item.odds_1x2 = Odds1x2Out.model_validate(picked)
    return out


@router.get("/{match_id}/preview", response_model=MatchPreviewOut)
def get_match_preview(match_id: int, db: Session = Depends(get_db)) -> MatchPreviewOut:
    """Todo lo de la página de partido: escudos, medias, mitades, forma, bajas, once y jugadores.

    Si el partido empieza en menos de ~75 min, pide la alineación oficial a Flashscore
    (una vez; se guarda). Si no, el once es el probable (titulares del último partido).
    """
    preview = MatchPreviewService(db).build(match_id)
    if preview is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Partido {match_id} no encontrado")
    return MatchPreviewOut.model_validate(preview)
