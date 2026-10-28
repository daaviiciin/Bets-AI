"""Reabre los partidos que se cerraron con el marcador del directo.

Uso::

    python -m backend.tools.reparar_partidos_en_directo            # solo lista
    python -m backend.tools.reparar_partidos_en_directo --aplicar  # reabre

Hasta 2026-10-28 el feed diario de Flashscore daba por terminado un partido en juego
(``AI÷y``) y el refresh guardaba el marcador del momento como final. Después las stats de
jugador y de equipo se bajaban también a mitad de partido. Ninguno de los dos se revisaba más,
así que las patas se corregían con un marcador parcial.

Detecta los partidos terminados cuyas stats se descargaron antes de
:data:`DURACION_MINIMA_PARTIDO` desde el saque. Con ``--aplicar`` los devuelve a
``scheduled`` sin marcador y borra sus stats parciales. El siguiente refresh
(``python -m backend.tools.daily_refresh``) trae el marcador real (feed diario o histórico de la
clasificación, que cubre la temporada) y las stats, y recorrige los picks del día. Las
combinadas de "Mis apuestas" se recorrigen con su botón "Comprobar".
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.cli_utils import force_utf8_stdio
from backend.app.database import get_session_factory
from backend.models import Match, MatchStats, PlayerMatchStats
from backend.models.match import MATCH_STATUS_FINISHED, MATCH_STATUS_SCHEDULED
from backend.services.flashscore_results_updater import descargado_en_directo


def partidos_cerrados_en_directo(session: Session) -> list[Match]:
    captura_equipo = dict(session.execute(
        select(MatchStats.match_id, func.min(MatchStats.captured_at)).group_by(MatchStats.match_id),
    ).all())
    terminados = session.scalars(
        select(Match).where(Match.status == MATCH_STATUS_FINISHED).order_by(Match.commence_time),
    ).all()
    return [
        m for m in terminados
        if descargado_en_directo(m.commence_time, m.player_stats_fetched_at, captura_equipo.get(m.id))
    ]


def reabrir(session: Session, matches: list[Match]) -> None:
    ids = [m.id for m in matches]
    session.execute(delete(PlayerMatchStats).where(PlayerMatchStats.match_id.in_(ids)))
    session.execute(delete(MatchStats).where(MatchStats.match_id.in_(ids)))
    for m in matches:
        m.status = MATCH_STATUS_SCHEDULED
        m.home_score = None
        m.away_score = None
        m.played_at = None
        m.player_stats_fetched_at = None


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--aplicar", action="store_true", help="Reabre los partidos (si no, solo lista).")
    args = parser.parse_args(argv)

    with get_session_factory()() as session:
        matches = partidos_cerrados_en_directo(session)
        for m in matches:
            print(
                f"  {m.id:>5}  {m.commence_time:%Y-%m-%d %H:%M} UTC  "
                f"{m.home_team.name} {m.home_score}-{m.away_score} {m.away_team.name}",
            )
        print(f"{len(matches)} partidos cerrados con datos del directo.")
        if not matches or not args.aplicar:
            if matches:
                print("Nada cambiado. Usa --aplicar para reabrirlos.")
            return 0
        reabrir(session, matches)
        session.commit()
        print("Reabiertos. Lanza ahora: python -m backend.tools.daily_refresh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
