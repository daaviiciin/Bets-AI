"""CLI: ingiere stats por jugador desde Sofascore para partidos finalizados.

Pre-requisito: los partidos deben tener ``sofascore_event_id`` rellenado, lo
cual sucede cuando se ejecuta ``update_match_results`` (o el daily refresh).

Uso típico::

    python -m backend.tools.import_player_match_stats
    python -m backend.tools.import_player_match_stats --max-matches 5
    python -m backend.tools.import_player_match_stats --liga soccer_spain_la_liga
    python -m backend.tools.import_player_match_stats --with-cards

Coste: 1 request a Sofascore por partido (2 si ``--with-cards``). 100% gratis.
Idempotente: si ya hay stats para un partido, las actualiza con los valores
nuevos en lugar de duplicarlas.
"""

from __future__ import annotations

import argparse
import logging
import sys

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import get_settings
from backend.app.database import get_session_factory
from backend.scrapers.sofascore import TOURNAMENTS
from backend.services.player_match_stats_ingester import PlayerMatchStatsIngester


def _configurar_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingiere stats por jugador (Sofascore /event/{id}/lineups).",
    )
    parser.add_argument(
        "--max-matches", type=int, default=30,
        help="Procesar como mucho N partidos por corrida (default 30).",
    )
    parser.add_argument(
        "--liga", choices=list(TOURNAMENTS.keys()), default=None,
        help="Filtra a una sola liga (sport_key Odds API). Default: todas.",
    )
    parser.add_argument(
        "--with-cards", action="store_true",
        help="También llama a /event/{id}/incidents para sacar tarjetas. "
             "Duplica el número de requests pero permite predicciones de "
             "mercados de tarjetas.",
    )
    args = parser.parse_args(argv)

    force_utf8_stdio()
    settings = get_settings()
    _configurar_logging(settings.log_level)
    log = logging.getLogger(__name__)

    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        ingester = PlayerMatchStatsIngester(session, log=log)
        result = ingester.ingest(
            max_matches=args.max_matches,
            league_sport_key=args.liga,
            with_cards=args.with_cards,
        )

    log.info("=" * 90)
    log.info("INGESTA DE PLAYER STATS POR PARTIDO (SOFASCORE)")
    log.info("=" * 90)
    log.info("  Partidos elegibles:        %d", result.matches_eligible)
    log.info("  Procesados:                %d", result.matches_processed)
    log.info("  Saltados (ya con stats):   %d", result.matches_skipped_already)
    log.info("  Saltados (sin sofa_id):    %d", result.matches_skipped_no_event_id)
    log.info("  Fallidos:                  %d", result.matches_failed)
    log.info("  Jugadores creados:         %d", result.players_created)
    log.info("  Jugadores actualizados:    %d", result.players_updated)
    log.info("  Filas insertadas:          %d", result.rows_inserted)
    log.info("  Filas actualizadas:        %d", result.rows_updated)
    log.info("  Tiempo total:              %.1fs", result.duration_seconds)
    if result.errors:
        log.warning("  Errores (%d):", len(result.errors))
        for err in result.errors[:10]:
            log.warning("    - %s", err)
    log.info("=" * 90)
    return 0


if __name__ == "__main__":
    sys.exit(main())
