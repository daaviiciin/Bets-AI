"""Forma reciente de goles por rol (local / visitante) desde partidos jugados."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.match import MATCH_STATUS_FINISHED, Match


def _rates_from_rows(scores: list[tuple[int, int]]) -> tuple[float, float]:
    """(GF medio, GA medio) en esos encuentros desde perspectiva del equipo focal."""
    if not scores:
        return 0.0, 0.0
    gf = sum(s[0] for s in scores) / len(scores)
    ga = sum(s[1] for s in scores) / len(scores)
    return gf, ga


def recent_rates_as_home(
    session: Session,
    *,
    team_id: int,
    league_id: int,
    limit: int = 8,
    exclude_match_id: int | None = None,
    min_matches: int = 2,
) -> tuple[float, float, int] | None:
    """Promedios de goles marcados/recibidos en los últimos partidos jugando como local."""
    stmt = (
        select(Match)
        .where(
            Match.league_id == league_id,
            Match.home_team_id == team_id,
            Match.status == MATCH_STATUS_FINISHED,
            Match.home_score.isnot(None),
            Match.away_score.isnot(None),
        )
        .order_by(Match.played_at.desc().nulls_last(), Match.commence_time.desc())
        .limit(limit)
    )
    rows = list(session.scalars(stmt).all())
    if exclude_match_id is not None:
        rows = [m for m in rows if m.id != exclude_match_id]
    scores = [(m.home_score or 0, m.away_score or 0) for m in rows]
    if len(scores) < min_matches:
        return None
    gf, ga = _rates_from_rows(scores)
    return gf, ga, len(scores)


def recent_rates_as_visitor(
    session: Session,
    *,
    team_id: int,
    league_id: int,
    limit: int = 8,
    exclude_match_id: int | None = None,
    min_matches: int = 2,
) -> tuple[float, float, int] | None:
    """Promedios marcados/recibidos como visitante (últimos partidos fuera)."""
    stmt = (
        select(Match)
        .where(
            Match.league_id == league_id,
            Match.away_team_id == team_id,
            Match.status == MATCH_STATUS_FINISHED,
            Match.home_score.isnot(None),
            Match.away_score.isnot(None),
        )
        .order_by(Match.played_at.desc().nulls_last(), Match.commence_time.desc())
        .limit(limit)
    )
    rows = list(session.scalars(stmt).all())
    if exclude_match_id is not None:
        rows = [m for m in rows if m.id != exclude_match_id]
    scores = [(m.away_score or 0, m.home_score or 0) for m in rows]
    if len(scores) < min_matches:
        return None
    gf, ga = _rates_from_rows(scores)
    return gf, ga, len(scores)
