"""Endpoints de partidos y cuotas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.app.database import get_db
from backend.app.schemas.responses import (
    MarketPredictionOut,
    MatchAnalysisOut,
    MatchOut,
    MatchStatsOut,
    OddsOut,
    TeamSnapshotOut,
    TopPlayerStatOut,
)
from backend.models import Bookmaker, League, Market, Match, MatchStats, Odds, Player
from backend.scrapers.winamax import PLAYER_PROP_MARKETS
from backend.services.match_analysis_service import (
    MatchAnalysis,
    MatchAnalysisService,
)

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
    db: Session = Depends(get_db),
) -> list[Match]:
    """Lista partidos en una ventana temporal, opcionalmente filtrados por liga.

    * ``days_ahead``: horizonte futuro (próximos partidos).
    * ``days_back``: incluye también partidos pasados de los últimos N días
      (útiles para mostrar resultados recientes con ``status='finished'``).
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

    return list(db.scalars(stmt).all())


@router.get("/{match_id}", response_model=MatchOut)
def get_match(match_id: int, db: Session = Depends(get_db)) -> Match:
    """Detalles de un partido concreto."""
    match = db.scalar(
        select(Match)
        .where(Match.id == match_id)
        .options(
            joinedload(Match.league),
            joinedload(Match.home_team),
            joinedload(Match.away_team),
        )
    )
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Partido {match_id} no encontrado")
    return match


@router.get("/{match_id}/odds", response_model=list[OddsOut])
def get_match_odds(
    match_id: int,
    market: str | None = Query(None, description="Filtra por market key (h2h, totals, ...)"),
    bookmaker: str | None = Query(None, description="Filtra por bookmaker key"),
    db: Session = Depends(get_db),
) -> list[OddsOut]:
    """Devuelve todas las cuotas capturadas para un partido."""
    match = db.scalar(select(Match).where(Match.id == match_id))
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Partido {match_id} no encontrado")

    stmt = (
        select(Odds)
        .where(Odds.match_id == match_id)
        .where(Odds.price > 1)
        .where(Odds.price < 4)
        .options(joinedload(Odds.bookmaker), joinedload(Odds.market))
    )
    rows = db.scalars(stmt).all()

    out: list[OddsOut] = []
    for o in rows:
        if market and o.market.key != market:
            continue
        if bookmaker and o.bookmaker.key != bookmaker:
            continue
        out.append(OddsOut(
            id=o.id,
            bookmaker=o.bookmaker,
            market_key=o.market.key,
            outcome_name=o.outcome_name,
            outcome_point=o.outcome_point,
            price=o.price,
            bookmaker_last_update=o.bookmaker_last_update,
        ))
    return out


@router.get("/{match_id}/stats", response_model=MatchStatsOut)
def get_match_stats(match_id: int, db: Session = Depends(get_db)) -> MatchStatsOut:
    """Devuelve las stats post-partido (Flashscore) de un encuentro finalizado.

    Ingresar previamente con ``python -m backend.tools.import_flashscore_stats``
    o llamando a ``POST /refresh/results`` (que actualiza resultados pero **no**
    stats; las stats se importan aparte).
    """
    rows = db.scalars(
        select(MatchStats)
        .where(MatchStats.match_id == match_id)
        .options(joinedload(MatchStats.team))
    ).all()
    if not rows:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No hay stats post-partido para el match {match_id}.",
        )
    home = next((r for r in rows if r.is_home), None)
    away = next((r for r in rows if not r.is_home), None)
    if home is None or away is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Stats incompletas para match {match_id} (home={home is not None}, away={away is not None}).",
        )
    return MatchStatsOut(
        match_id=match_id,
        source=home.source,
        captured_at=home.captured_at,
        home=home,  # Pydantic from_attributes
        away=away,
    )


@router.get("/{match_id}/player-props")
def get_match_player_props(
    match_id: int,
    bookmaker: str | None = Query(
        None,
        description="Filtra por bookmaker key (default: todos los disponibles).",
    ),
    db: Session = Depends(get_db),
) -> dict:
    """Cuotas de mercados de jugador (player props) para un partido.

    Agrupa las cuotas por jugador y por mercado (tiros, tiros a puerta,
    tarjetas, goleador, etc.). Pensado para mostrar una pestaña dedicada
    en el detalle de partido con todas las opciones de apuesta sobre
    jugadores individuales.

    Estructura de respuesta::

        {
          "match_id": 10,
          "home_team": "Arsenal",
          "away_team": "Atlético Madrid",
          "players": [
            {
              "player_id": 311,
              "player_name": "Bukayo Saka",
              "markets": {
                "player_sot_alt": [
                  {"line": 0.5, "side": "over", "odds": 1.50, "bookmaker": "winamax_es"},
                  ...
                ],
                "player_shots_alt": [...],
                ...
              }
            },
            ...
          ]
        }
    """
    match = db.scalar(select(Match).where(Match.id == match_id))
    if match is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Partido {match_id} no encontrado")

    stmt = (
        select(Odds)
        .where(Odds.match_id == match_id)
        .where(Odds.player_id.is_not(None))
        .options(
            joinedload(Odds.market),
            joinedload(Odds.bookmaker),
            joinedload(Odds.player),
        )
    )
    rows = list(db.scalars(stmt).all())
    if bookmaker:
        rows = [o for o in rows if o.bookmaker.key == bookmaker]

    # Filtrar a mercados que sí son player props.
    rows = [o for o in rows if o.market.key in PLAYER_PROP_MARKETS]

    # Agrupar por jugador y mercado.
    by_player: dict[int, dict] = {}
    for o in rows:
        if o.player is None:
            continue
        pid = o.player.id
        block = by_player.get(pid)
        if block is None:
            block = {
                "player_id": pid,
                "player_name": o.player.name,
                "markets": {},
            }
            by_player[pid] = block
        market_key = o.market.key
        if market_key not in block["markets"]:
            block["markets"][market_key] = []
        side = "yes"
        name_lower = (o.outcome_name or "").lower()
        if " over " in name_lower or name_lower.endswith(" over"):
            side = "over"
        elif " under " in name_lower or name_lower.endswith(" under"):
            side = "under"
        block["markets"][market_key].append({
            "line": float(o.outcome_point) if o.outcome_point is not None else None,
            "side": side,
            "odds": float(o.price),
            "bookmaker": o.bookmaker.key,
            "outcome_name": o.outcome_name,
        })

    # Orden interno de mercados por línea ascendente.
    for block in by_player.values():
        for market_key, items in block["markets"].items():
            items.sort(key=lambda x: (
                x["line"] if x["line"] is not None else 0.0,
                x["side"],
            ))

    players = sorted(by_player.values(), key=lambda b: b["player_name"])
    return {
        "match_id": match.id,
        "home_team": match.home_team.name if match.home_team else None,
        "away_team": match.away_team.name if match.away_team else None,
        "players": players,
    }


@router.get("/{match_id}/analysis", response_model=MatchAnalysisOut)
def get_match_analysis(match_id: int, db: Session = Depends(get_db)) -> MatchAnalysisOut:
    """Análisis predictivo de un partido.

    Devuelve:
    * Stats de temporada de ambos equipos.
    * Top 5 jugadores ofensivos por SoT/90 de cada equipo.
    * Predicciones de mercado (1X2, totales 1.5/2.5/3.5, BTTS) calculadas
      con un modelo Poisson independiente sobre las medias de la liga.
    * Mejor cuota disponible en BD para cada selección + EV vs el modelo.

    Si todavía no hay stats para esa liga (p.ej. competiciones europeas
    que no scrapeamos en Sofascore), devolvemos un análisis vacío con notas.
    """
    service = MatchAnalysisService(db)
    analysis: MatchAnalysis | None = service.analyze(match_id)
    if analysis is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Partido {match_id} no encontrado")
    return _serialize_analysis(analysis)


def _serialize_analysis(a: MatchAnalysis) -> MatchAnalysisOut:
    return MatchAnalysisOut(
        match_id=a.match_id,
        league_name=a.league_name,
        commence_time=a.commence_time,
        home=TeamSnapshotOut(**a.home.__dict__),
        away=TeamSnapshotOut(**a.away.__dict__),
        expected_goals_home=a.expected_goals_home,
        expected_goals_away=a.expected_goals_away,
        league_avg_goals=a.league_avg_goals,
        home_top_players=[TopPlayerStatOut(**p.__dict__) for p in a.home_top_players],
        away_top_players=[TopPlayerStatOut(**p.__dict__) for p in a.away_top_players],
        predictions=[MarketPredictionOut(**p.__dict__) for p in a.predictions],
        notes=a.notes,
    )
