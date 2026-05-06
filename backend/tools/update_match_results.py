"""CLI: actualiza el resultado de los partidos en BD a partir de Sofascore.

Uso típico (semanalmente, tras la jornada)::

    python -m backend.tools.update_match_results
    python -m backend.tools.update_match_results --liga soccer_spain_la_liga
    python -m backend.tools.update_match_results --max-pages 2

El script es idempotente: si un partido ya tiene el resultado correcto en
BD, no lo modifica.
"""

from __future__ import annotations

import argparse
import logging
import sys

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import get_settings
from backend.app.database import get_session_factory
from backend.scrapers.sofascore import TOURNAMENTS
from backend.services.match_results_updater import (
    LeagueUpdateResult,
    MatchResultsUpdater,
)


def _configurar_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _print_league(r: LeagueUpdateResult) -> None:
    log = logging.getLogger(__name__)
    log.info(
        "  [%s] %-22s eventos=%3d  actualizados=%3d  sin_cambios=%3d  no_encontrados=%3d  sin_mapeo=%2d",
        "OK" if r.events_fetched > 0 else "··",
        r.league_name[:22],
        r.events_fetched,
        r.matches_updated,
        r.matches_already_ok,
        r.matches_not_found,
        r.skipped_unmapped,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Actualiza el resultado de los partidos finalizados desde Sofascore.",
    )
    parser.add_argument(
        "--liga", choices=list(TOURNAMENTS.keys()), default=None,
        help="Sport key específico. Por defecto: todas las top-5.",
    )
    parser.add_argument(
        "--max-pages", type=int, default=3,
        help="Páginas (~30 partidos cada una) a recorrer en Sofascore (default: 3 = ~90 partidos).",
    )
    args = parser.parse_args(argv)

    force_utf8_stdio()
    settings = get_settings()
    _configurar_logging(settings.log_level)
    log = logging.getLogger(__name__)

    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        updater = MatchResultsUpdater(session, log=log)
        sport_keys = [args.liga] if args.liga else None
        summary = updater.update_all(sport_keys=sport_keys, max_pages=args.max_pages)
        session.commit()

    log.info("=" * 90)
    log.info("ACTUALIZACIÓN DE RESULTADOS")
    log.info("=" * 90)
    for r in summary.per_league:
        _print_league(r)
    log.info("-" * 90)
    log.info(
        "Total: %d actualizados, %d sin cambios, %d no encontrados",
        summary.total_updated, summary.total_already_ok, summary.total_not_found,
    )
    log.info(
        "Tiempo total: %.1fs",
        (summary.finished_at - summary.started_at).total_seconds(),
    )
    log.info("=" * 90)
    return 0


if __name__ == "__main__":
    sys.exit(main())
