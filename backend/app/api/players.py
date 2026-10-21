"""Endpoints de jugadores y sus estadísticas.

* ``GET /players``               — búsqueda por nombre.
* ``GET /players/{id}``          — datos básicos del jugador.
* ``GET /players/{id}/stats``    — stats de temporada + histórico por partido
  (últimos N partidos finalizados) y un resumen calculado para player props.
"""

from __future__ import annotations

from typing import Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, joinedload

from backend.app.database import get_db
from backend.app.schemas.responses import (
    PlayerMatchStatRowOut,
    PlayerOut,
    PlayerSeasonStatsOut,
    PlayerStatsResponseOut,
    PlayerStatsSummaryOut,
    TeamOut,
)
from backend.models import (
    Match,
    Player,
    PlayerMatchStats,
    PlayerSeasonStats,
    Season,
)
from backend.services.busqueda_texto import ESCAPE_LIKE, patron_like_contiene

router = APIRouter(prefix="/players", tags=["players"])

DEFAULT_RECENT_LIMIT = 10
MAX_RECENT_LIMIT = 50


@router.get("", response_model=list[PlayerOut])
def list_players(
    q: str | None = Query(
        None, min_length=2, max_length=80,
        description="Búsqueda parcial por nombre (case-insensitive).",
    ),
    team_id: int | None = Query(None, description="Filtrar por equipo."),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> Sequence[Player]:
    """Listado/búsqueda de jugadores.

    Útil para autocompletados en el frontend (cuando lleguemos a player props).
    """
    stmt = (
        select(Player)
        .options(joinedload(Player.team))
        .order_by(Player.name)
        .limit(limit)
    )
    if q:
        stmt = stmt.where(
            func.lower(Player.name).like(patron_like_contiene(q), escape=ESCAPE_LIKE)
        )
    if team_id is not None:
        stmt = stmt.where(Player.team_id == team_id)
    return list(db.scalars(stmt).all())


@router.get("/{player_id}", response_model=PlayerOut)
def get_player(player_id: int, db: Session = Depends(get_db)) -> Player:
    """Datos básicos de un jugador."""
    player = db.scalar(
        select(Player)
        .options(joinedload(Player.team))
        .where(Player.id == player_id)
    )
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe el jugador con id={player_id}",
        )
    return player


@router.get("/{player_id}/stats", response_model=PlayerStatsResponseOut)
def get_player_stats(
    player_id: int,
    recent: int = Query(
        DEFAULT_RECENT_LIMIT,
        ge=1,
        le=MAX_RECENT_LIMIT,
        description="Número de partidos recientes a devolver (default 10).",
    ),
    db: Session = Depends(get_db),
) -> PlayerStatsResponseOut:
    """Histórico completo del jugador para análisis de player props.

    Incluye:
      * **Datos del jugador** (posición, equipo).
      * **Season stats agregadas** de la temporada activa de su liga.
      * **Histórico por partido** (últimos N) con goles, tiros, tiros a puerta,
        rating, etc.
      * **Resumen calculado** sobre el histórico (medias, % de partidos con
        ≥1 SoT, etc.) que es lo que de verdad alimenta los modelos de
        player props.
    """
    player = db.scalar(
        select(Player)
        .options(joinedload(Player.team))
        .where(Player.id == player_id)
    )
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe el jugador con id={player_id}",
        )

    # Stats de temporada (tomamos la más reciente del jugador, normalmente la actual).
    season_row = db.execute(
        select(PlayerSeasonStats, Season)
        .join(Season, Season.id == PlayerSeasonStats.season_id)
        .options(
            joinedload(PlayerSeasonStats.team),
            joinedload(PlayerSeasonStats.season).joinedload(Season.league),
        )
        .where(PlayerSeasonStats.player_id == player_id)
        .order_by(desc(Season.year_start))
        .limit(1)
    ).first()

    season_stats_out: PlayerSeasonStatsOut | None = None
    if season_row is not None:
        ps, season = season_row
        season_stats_out = PlayerSeasonStatsOut(
            season_id=season.id,
            season_name=season.name,
            league_name=season.league.name if season.league else None,
            team=TeamOut.model_validate(ps.team) if ps.team else None,
            matches_played=ps.matches_played,
            starts=ps.starts,
            minutes=ps.minutes,
            nineties=ps.nineties,
            goals=ps.goals,
            assists=ps.assists,
            shots=ps.shots,
            shots_on_target=ps.shots_on_target,
            shots_on_target_pct=ps.shots_on_target_pct,
            shots_per_90=ps.shots_per_90,
            shots_on_target_per_90=ps.shots_on_target_per_90,
            xg=ps.xg,
            npxg=ps.npxg,
            yellow_cards=ps.yellow_cards,
            red_cards=ps.red_cards,
            last_updated=ps.last_updated,
        )

    # Histórico por partido (últimos N).
    rows = list(db.scalars(
        select(PlayerMatchStats)
        .join(Match, Match.id == PlayerMatchStats.match_id)
        .options(
            joinedload(PlayerMatchStats.match).joinedload(Match.league),
            joinedload(PlayerMatchStats.match).joinedload(Match.home_team),
            joinedload(PlayerMatchStats.match).joinedload(Match.away_team),
        )
        .where(PlayerMatchStats.player_id == player_id)
        .order_by(desc(Match.commence_time))
        .limit(recent)
    ).all())

    recent_matches: list[PlayerMatchStatRowOut] = []
    for r in rows:
        match: Match = r.match
        recent_matches.append(PlayerMatchStatRowOut(
            match_id=match.id,
            league_name=match.league.name if match.league else None,
            home_team=match.home_team.name if match.home_team else "",
            away_team=match.away_team.name if match.away_team else "",
            is_home=r.is_home,
            commence_time=match.commence_time,
            home_score=match.home_score,
            away_score=match.away_score,
            is_starter=r.is_starter,
            is_substitute=r.is_substitute,
            minutes_played=r.minutes_played,
            position_in_match=r.position_in_match,
            goals=r.goals,
            assists=r.assists,
            shots=r.shots,
            shots_on_target=r.shots_on_target,
            shots_off_target=r.shots_off_target,
            shots_blocked=r.shots_blocked,
            big_chances_created=r.big_chances_created,
            big_chances_missed=r.big_chances_missed,
            expected_goals=r.expected_goals,
            expected_assists=r.expected_assists,
            key_passes=r.key_passes,
            passes_total=r.passes_total,
            passes_accurate=r.passes_accurate,
            touches=r.touches,
            tackles=r.tackles,
            interceptions=r.interceptions,
            clearances=r.clearances,
            duels_won=r.duels_won,
            fouls_committed=r.fouls_committed,
            fouls_drawn=r.fouls_drawn,
            yellow_cards=r.yellow_cards,
            red_cards=r.red_cards,
            offsides=r.offsides,
            saves=r.saves,
            goals_prevented=r.goals_prevented,
            rating=r.rating,
        ))

    summary = _build_summary(rows)

    return PlayerStatsResponseOut(
        player=PlayerOut.model_validate(player),
        season_stats=season_stats_out,
        recent_matches=recent_matches,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_summary(rows: list[PlayerMatchStats]) -> PlayerStatsSummaryOut | None:
    """Calcula promedios y tasas útiles a partir del histórico por partido.

    Sólo cuenta partidos en los que el jugador jugó algún minuto. Si no hay
    datos suficientes devuelve ``None``.
    """
    played = [r for r in rows if (r.minutes_played or 0) > 0]
    if not played:
        return None

    n = len(played)

    def _avg(getter) -> float | None:
        values = [getter(r) for r in played if getter(r) is not None]
        if not values:
            return None
        return sum(float(v) for v in values) / len(values)

    def _pct(predicate) -> float | None:
        if n == 0:
            return None
        matches = sum(1 for r in played if predicate(r))
        return matches / n

    return PlayerStatsSummaryOut(
        sample_size=n,
        avg_minutes_played=_avg(lambda r: r.minutes_played),
        avg_shots=_avg(lambda r: r.shots),
        avg_shots_on_target=_avg(lambda r: r.shots_on_target),
        avg_goals=_avg(lambda r: r.goals),
        avg_rating=_avg(lambda r: r.rating),
        avg_expected_goals=_avg(lambda r: r.expected_goals),
        pct_starts=_pct(lambda r: bool(r.is_starter)),
        pct_at_least_one_shot_on_target=_pct(lambda r: (r.shots_on_target or 0) >= 1),
        pct_at_least_two_shots_on_target=_pct(lambda r: (r.shots_on_target or 0) >= 2),
        pct_scored=_pct(lambda r: (r.goals or 0) >= 1),
    )
