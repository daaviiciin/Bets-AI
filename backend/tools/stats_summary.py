"""Resumen de estadísticas almacenadas. Útil para verificar el ingester.

Uso::

    python -m backend.tools.stats_summary

Imprime totales por liga y rankings de top goleadores y top tiros a puerta.
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from backend.app.cli_utils import force_utf8_stdio
from backend.app.database import get_session_factory
from backend.models import (
    League,
    Player,
    PlayerSeasonStats,
    Season,
    Team,
    TeamSeasonStats,
)

LIMIT = 10


def _print_totals(session: Session) -> None:
    print("\n=== TOTALES EN BD ===")
    counts = {
        "leagues": session.scalar(select(func.count()).select_from(League)),
        "seasons": session.scalar(select(func.count()).select_from(Season)),
        "teams": session.scalar(select(func.count()).select_from(Team)),
        "team_season_stats": session.scalar(select(func.count()).select_from(TeamSeasonStats)),
        "players": session.scalar(select(func.count()).select_from(Player)),
        "player_season_stats": session.scalar(select(func.count()).select_from(PlayerSeasonStats)),
    }
    for k, v in counts.items():
        print(f"  {k:24s} {v}")


def _print_top_scorers(session: Session) -> None:
    print(f"\n=== TOP {LIMIT} GOLEADORES (todas las ligas) ===")
    print(f"{'#':>3} {'Jugador':25s} {'Equipo':20s} {'Liga':18s} {'Pts':>4} {'Gls':>4} {'Sh':>4} {'SoT':>4} {'xG':>6}")
    print("-" * 95)

    stmt = (
        select(
            Player.name,
            Team.name.label("team_name"),
            League.name.label("league_name"),
            PlayerSeasonStats.matches_played,
            PlayerSeasonStats.goals,
            PlayerSeasonStats.shots,
            PlayerSeasonStats.shots_on_target,
            PlayerSeasonStats.xg,
        )
        .join(Player, Player.id == PlayerSeasonStats.player_id)
        .join(Team, Team.id == PlayerSeasonStats.team_id, isouter=True)
        .join(Season, Season.id == PlayerSeasonStats.season_id)
        .join(League, League.id == Season.league_id)
        .where(PlayerSeasonStats.goals.is_not(None))
        .order_by(desc(PlayerSeasonStats.goals), desc(PlayerSeasonStats.shots_on_target))
        .limit(LIMIT)
    )
    for i, row in enumerate(session.execute(stmt).all(), 1):
        print(
            f"{i:>3} {(row.name or '')[:24]:25s} "
            f"{(row.team_name or '-')[:19]:20s} "
            f"{(row.league_name or '-')[:17]:18s} "
            f"{row.matches_played or 0:>4} "
            f"{row.goals or 0:>4} "
            f"{row.shots or 0:>4} "
            f"{row.shots_on_target or 0:>4} "
            f"{(float(row.xg) if row.xg else 0):>6.2f}"
        )


def _print_top_sot(session: Session) -> None:
    print(f"\n=== TOP {LIMIT} TIROS A PUERTA (todas las ligas) ===")
    print(f"{'#':>3} {'Jugador':25s} {'Equipo':20s} {'Liga':18s} {'Pts':>4} {'SoT':>4} {'SoT/90':>7} {'Sh':>4} {'SoT%':>5}")
    print("-" * 100)

    stmt = (
        select(
            Player.name,
            Team.name.label("team_name"),
            League.name.label("league_name"),
            PlayerSeasonStats.matches_played,
            PlayerSeasonStats.shots_on_target,
            PlayerSeasonStats.shots_on_target_per_90,
            PlayerSeasonStats.shots,
            PlayerSeasonStats.shots_on_target_pct,
        )
        .join(Player, Player.id == PlayerSeasonStats.player_id)
        .join(Team, Team.id == PlayerSeasonStats.team_id, isouter=True)
        .join(Season, Season.id == PlayerSeasonStats.season_id)
        .join(League, League.id == Season.league_id)
        .where(PlayerSeasonStats.shots_on_target.is_not(None))
        .order_by(desc(PlayerSeasonStats.shots_on_target))
        .limit(LIMIT)
    )
    for i, row in enumerate(session.execute(stmt).all(), 1):
        print(
            f"{i:>3} {(row.name or '')[:24]:25s} "
            f"{(row.team_name or '-')[:19]:20s} "
            f"{(row.league_name or '-')[:17]:18s} "
            f"{row.matches_played or 0:>4} "
            f"{row.shots_on_target or 0:>4} "
            f"{(float(row.shots_on_target_per_90) if row.shots_on_target_per_90 else 0):>7.2f} "
            f"{row.shots or 0:>4} "
            f"{(float(row.shots_on_target_pct) if row.shots_on_target_pct else 0):>5.1f}"
        )


def _print_top_xg(session: Session) -> None:
    print(f"\n=== TOP {LIMIT} xG (expected goals; todas las ligas) ===")
    print(f"{'#':>3} {'Jugador':25s} {'Equipo':20s} {'Liga':18s} {'Gls':>4} {'xG':>6} {'Diff':>6}")
    print("-" * 90)

    stmt = (
        select(
            Player.name,
            Team.name.label("team_name"),
            League.name.label("league_name"),
            PlayerSeasonStats.goals,
            PlayerSeasonStats.xg,
        )
        .join(Player, Player.id == PlayerSeasonStats.player_id)
        .join(Team, Team.id == PlayerSeasonStats.team_id, isouter=True)
        .join(Season, Season.id == PlayerSeasonStats.season_id)
        .join(League, League.id == Season.league_id)
        .where(PlayerSeasonStats.xg.is_not(None))
        .order_by(desc(PlayerSeasonStats.xg))
        .limit(LIMIT)
    )
    for i, row in enumerate(session.execute(stmt).all(), 1):
        gls = row.goals or 0
        xg = float(row.xg) if row.xg else 0
        diff = gls - xg
        print(
            f"{i:>3} {(row.name or '')[:24]:25s} "
            f"{(row.team_name or '-')[:19]:20s} "
            f"{(row.league_name or '-')[:17]:18s} "
            f"{gls:>4} {xg:>6.2f} {diff:>+6.2f}"
        )


def _print_standings_per_league(session: Session) -> None:
    print("\n=== CLASIFICACIONES (TOP 5 por liga) ===")
    leagues = session.execute(select(League).order_by(League.name)).scalars().all()
    for league in leagues:
        seasons = session.execute(
            select(Season).where(Season.league_id == league.id).order_by(desc(Season.year_start))
        ).scalars().all()
        if not seasons:
            continue
        season = seasons[0]
        rows = session.execute(
            select(
                Team.name,
                TeamSeasonStats.matches_played,
                TeamSeasonStats.wins,
                TeamSeasonStats.draws,
                TeamSeasonStats.losses,
                TeamSeasonStats.goals_for,
                TeamSeasonStats.goals_against,
                TeamSeasonStats.points,
            )
            .join(Team, Team.id == TeamSeasonStats.team_id)
            .where(TeamSeasonStats.season_id == season.id)
            .order_by(desc(TeamSeasonStats.points), desc(TeamSeasonStats.goals_for))
            .limit(5)
        ).all()
        if not rows:
            continue
        print(f"\n  {league.name} — {season.name}")
        print(f"  {'#':>2} {'Equipo':22s} {'PJ':>3} {'V':>3} {'E':>3} {'D':>3} {'GF':>3} {'GC':>3} {'Pts':>4}")
        for i, r in enumerate(rows, 1):
            print(
                f"  {i:>2} {(r.name or '')[:21]:22s} "
                f"{r.matches_played or 0:>3} {r.wins or 0:>3} {r.draws or 0:>3} {r.losses or 0:>3} "
                f"{r.goals_for or 0:>3} {r.goals_against or 0:>3} {r.points or 0:>4}"
            )


def main() -> int:
    force_utf8_stdio()
    logging.basicConfig(level=logging.WARNING)
    SessionLocal = get_session_factory()

    with SessionLocal() as session:
        _print_totals(session)
        _print_standings_per_league(session)
        _print_top_scorers(session)
        _print_top_sot(session)
        _print_top_xg(session)

    return 0


if __name__ == "__main__":
    sys.exit(main())
