"""Imprime las stats de un equipo concreto (top players + season stats).

Uso::

    python -m backend.tools.team_stats --team "Real Madrid"
"""
from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import desc, select
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


def _print_team(session: Session, team_name: str) -> None:
    team = session.scalar(select(Team).where(Team.name == team_name))
    if team is None:
        like = f"%{team_name}%"
        candidates = session.execute(select(Team.name).where(Team.name.ilike(like))).scalars().all()
        print(f"No encontrado '{team_name}'. Sugerencias: {candidates[:10]}")
        return

    print(f"\n=== EQUIPO: {team.name} (id={team.id}) ===")
    print(f"  external_source={team.external_source}  external_id={team.external_id}")

    stats = session.execute(
        select(TeamSeasonStats, Season, League)
        .join(Season, Season.id == TeamSeasonStats.season_id)
        .join(League, League.id == Season.league_id)
        .where(TeamSeasonStats.team_id == team.id)
        .order_by(desc(Season.year_start))
    ).all()

    if not stats:
        print("  (sin TeamSeasonStats)")
    for ts, season, league in stats:
        print(f"\n  {league.name} — {season.name}")
        print(f"    Pos. ?  PJ={ts.matches_played}  V={ts.wins}  E={ts.draws}  D={ts.losses}")
        print(f"    GF={ts.goals_for}  GC={ts.goals_against}  Diff={ts.goal_diff}  Pts={ts.points}")

    print(f"\n  --- TOP 10 jugadores por SoT ---")
    print(f"  {'#':>2} {'Jugador':22s} {'Pos':>3} {'Pts':>4} {'Gls':>4} {'Sh':>4} {'SoT':>4} {'SoT/90':>7} {'xG':>6}")
    rows = session.execute(
        select(
            Player.name, Player.position,
            PlayerSeasonStats.matches_played,
            PlayerSeasonStats.goals,
            PlayerSeasonStats.shots,
            PlayerSeasonStats.shots_on_target,
            PlayerSeasonStats.shots_on_target_per_90,
            PlayerSeasonStats.xg,
        )
        .join(Player, Player.id == PlayerSeasonStats.player_id)
        .where(PlayerSeasonStats.team_id == team.id)
        .where(PlayerSeasonStats.shots_on_target.is_not(None))
        .order_by(desc(PlayerSeasonStats.shots_on_target))
        .limit(10)
    ).all()
    for i, r in enumerate(rows, 1):
        print(
            f"  {i:>2} {(r.name or '')[:21]:22s} "
            f"{(r.position or '-'):>3} "
            f"{r.matches_played or 0:>4} "
            f"{r.goals or 0:>4} "
            f"{r.shots or 0:>4} "
            f"{r.shots_on_target or 0:>4} "
            f"{(float(r.shots_on_target_per_90) if r.shots_on_target_per_90 else 0):>7.2f} "
            f"{(float(r.xg) if r.xg else 0):>6.2f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Stats de un equipo concreto.")
    parser.add_argument("--team", required=True, help="Nombre del equipo.")
    args = parser.parse_args()

    force_utf8_stdio()
    logging.basicConfig(level=logging.WARNING)
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        _print_team(session, args.team)
    return 0


if __name__ == "__main__":
    sys.exit(main())
