"""Agrega puntos de baloncesto por temporada desde ``Match`` terminados."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.league import League
from backend.models.match import MATCH_STATUS_FINISHED, Match
from backend.models.player_match_stats import PlayerMatchStats
from backend.models.player_stats import PlayerSeasonStats
from backend.models.season import Season
from backend.models.team_stats import TeamSeasonStats


LOG = logging.getLogger(__name__)


@dataclass
class BasketballRollupSummary:
    leagues_touched: int = 0
    team_rows_upserted: int = 0


def is_basketball_league(league: League) -> bool:
    return "basketball" in (league.sport_key or "").lower()


def rollup_basketball_team_season_stats(session: Session) -> BasketballRollupSummary:
    """Suma ``bb_points_for/against`` y ``matches_played`` desde marcadores BD.

    Requiere partidos ``finished`` con ``home_score`` / ``away_score`` (puntos).
    Todo se asocia a la ``Season`` más reciente por liga *basketball_*.
    """
    summary = BasketballRollupSummary()
    leagues = session.scalars(
        select(League).where(League.sport_key.ilike("%basketball%"))
    ).all()
    now = datetime.now(timezone.utc)
    for league in leagues:
        season = session.scalar(
            select(Season)
            .where(Season.league_id == league.id)
            .order_by(Season.year_start.desc())
            .limit(1)
        )
        if season is None:
            LOG.debug("Basket rollup: sin season para league_id=%s", league.id)
            continue

        matches = session.scalars(
            select(Match).where(
                Match.league_id == league.id,
                Match.status == MATCH_STATUS_FINISHED,
            )
        ).all()

        pts_for: dict[int, int] = {}
        pts_against: dict[int, int] = {}
        played: dict[int, int] = {}
        for m in matches:
            if m.home_score is None or m.away_score is None:
                continue
            hid, aid = m.home_team_id, m.away_team_id
            pts_for[hid] = pts_for.get(hid, 0) + int(m.home_score)
            pts_against[hid] = pts_against.get(hid, 0) + int(m.away_score)
            pts_for[aid] = pts_for.get(aid, 0) + int(m.away_score)
            pts_against[aid] = pts_against.get(aid, 0) + int(m.home_score)
            played[hid] = played.get(hid, 0) + 1
            played[aid] = played.get(aid, 0) + 1

        all_team_ids = set(played.keys())
        if not all_team_ids:
            continue

        summary.leagues_touched += 1
        for tid in all_team_ids:
            row = session.scalar(
                select(TeamSeasonStats).where(
                    TeamSeasonStats.team_id == tid,
                    TeamSeasonStats.season_id == season.id,
                )
            )
            if row is None:
                row = TeamSeasonStats(
                    team_id=tid,
                    season_id=season.id,
                    last_updated=now,
                )
                session.add(row)
            row.bb_points_for = pts_for.get(tid, 0)
            row.bb_points_against = pts_against.get(tid, 0)
            row.matches_played = played.get(tid, 0)
            row.last_updated = now
            summary.team_rows_upserted += 1

        LOG.info(
            "Basket rollup league=%s: %d equipos actualizados",
            league.sport_key, len(all_team_ids),
        )

    return summary


def rollup_basketball_player_season_totals(session: Session) -> int:
    """Suma puntos por partido jugador (:attr:`PlayerMatchStats.bb_points`).

    Escribe ``PlayerSeasonStats.bb_points_total`` / ``matches_played`` cuando
    hay datos de origen Flashscore‑u otro ingest (``bb_points`` no nulo).

    Una fila agregadora por jugador y ``Season`` (la más reciente de la misma liga que el último match).
    """
    now = datetime.now(timezone.utc)
    updated = 0

    subq = (
        select(
            PlayerMatchStats.player_id.label("pid"),
            Match.league_id.label("lid"),
            func.sum(PlayerMatchStats.bb_points).label("psum"),
            func.count(PlayerMatchStats.id).label("cnt"),
        )
        .join(Match, Match.id == PlayerMatchStats.match_id)
        .join(League, League.id == Match.league_id)
        .where(League.sport_key.ilike("%basketball%"))
        .where(PlayerMatchStats.bb_points.is_not(None))
        .group_by(PlayerMatchStats.player_id, Match.league_id)
    )

    for pid, lid, ptsum, cnt in session.execute(subq):
        season = session.scalar(
            select(Season)
            .where(Season.league_id == lid)
            .order_by(Season.year_start.desc())
            .limit(1)
        )
        if season is None:
            continue
        row = session.scalar(
            select(PlayerSeasonStats).where(
                PlayerSeasonStats.player_id == pid,
                PlayerSeasonStats.season_id == season.id,
            )
        )
        if row is None:
            row = PlayerSeasonStats(player_id=int(pid), season_id=season.id, last_updated=now)
            session.add(row)
        row.bb_points_total = int(ptsum or 0)
        row.matches_played = int(cnt or 0)
        row.last_updated = now
        updated += 1

    LOG.info("Basket jugadores: filas temporada actualizadas=%d", updated)
    return updated
