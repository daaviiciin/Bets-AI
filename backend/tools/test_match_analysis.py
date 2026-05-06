"""Smoke test del MatchAnalysisService contra varios match_ids."""

from __future__ import annotations

import argparse
import logging

from backend.app.cli_utils import force_utf8_stdio
from backend.app.database import get_session_factory
from backend.services.match_analysis_service import MatchAnalysisService


def _print_analysis(match_id: int) -> None:
    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        service = MatchAnalysisService(session)
        a = service.analyze(match_id)
    if a is None:
        print(f"[match {match_id}] NOT FOUND")
        return

    print("=" * 90)
    print(f"MATCH #{a.match_id}  |  {a.league_name}  |  {a.commence_time:%Y-%m-%d %H:%M UTC}")
    print(f"{a.home.name}  vs  {a.away.name}")
    print("=" * 90)
    if a.notes:
        for n in a.notes:
            print(f"  NOTE: {n}")
        return

    print(f"  Liga avg goals/partido: {a.league_avg_goals:.2f}")
    print(f"  Esperanza λ_home={a.expected_goals_home:.2f}   λ_away={a.expected_goals_away:.2f}")
    print()
    print(f"  HOME ({a.home.name}): {a.home.matches_played}MP  {a.home.wins}V {a.home.draws}E {a.home.losses}D  GF={a.home.goals_for}  GA={a.home.goals_against}  Pts={a.home.points}")
    print(f"  AWAY ({a.away.name}): {a.away.matches_played}MP  {a.away.wins}V {a.away.draws}E {a.away.losses}D  GF={a.away.goals_for}  GA={a.away.goals_against}  Pts={a.away.points}")
    print()

    print(f"  TOP {len(a.home_top_players)} ATACANTES {a.home.name}:")
    for p in a.home_top_players:
        print(f"    - {p.name:25s} ({p.position or '-':>3}) MP={p.matches_played} Gls={p.goals} Sh={p.shots} SoT={p.shots_on_target} SoT/90={p.shots_on_target_per_90:.2f}" if p.shots_on_target_per_90 else f"    - {p.name}")
    print(f"  TOP {len(a.away_top_players)} ATACANTES {a.away.name}:")
    for p in a.away_top_players:
        print(f"    - {p.name:25s} ({p.position or '-':>3}) MP={p.matches_played} Gls={p.goals} Sh={p.shots} SoT={p.shots_on_target} SoT/90={p.shots_on_target_per_90:.2f}" if p.shots_on_target_per_90 else f"    - {p.name}")
    print()

    print(f"  PREDICCIONES DE MERCADO ({len(a.predictions)} selecciones)")
    print(f"  {'mercado':12s} {'sel.':6s} {'desc.':28s} {'p.fair':>7s} {'fair_o':>7s} {'best':>6s} {'imp':>7s} {'EV':>8s} bookmaker")
    print("  " + "-" * 110)
    for p in a.predictions:
        ev_str = f"{p.expected_value:+.3f}" if p.expected_value is not None else "    -"
        odds_str = f"{p.best_odds:.2f}" if p.best_odds is not None else "  -  "
        imp_str = f"{p.implied_probability:.3f}" if p.implied_probability is not None else "   -"
        ev_marker = "  <-- VALUE" if (p.expected_value or 0) > 0.02 else ""
        print(
            f"  {p.market:12s} {p.selection:6s} {p.selection_label[:27]:28s} "
            f"{p.fair_probability:7.3f} {p.fair_odds:7.2f} {odds_str:>6s} "
            f"{imp_str:>7s} {ev_str:>8s} {p.bookmaker_key or '-'}{ev_marker}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", type=int, nargs="+", default=[16, 78, 57, 11])
    args = parser.parse_args()

    force_utf8_stdio()
    logging.basicConfig(level=logging.WARNING)
    for mid in args.ids:
        _print_analysis(mid)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
