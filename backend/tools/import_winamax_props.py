"""CLI: ingiere cuotas de Winamax (player props + match-level) en BD.

Uso típico::

    python -m backend.tools.import_winamax_props
    python -m backend.tools.import_winamax_props --max-matches 3 --dry-run

100% gratis: scraping directo de la web de Winamax (PRELOADED_STATE en HTML).
Idempotente: cada corrida crea un nuevo snapshot, no se sobreescribe.
"""

from __future__ import annotations

import argparse
import logging
import sys

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import get_settings
from backend.app.database import get_session_factory
from backend.services.winamax_odds_ingester import WinamaxOddsIngester


def _configurar_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingesta de cuotas Winamax (player props + match-level).",
    )
    parser.add_argument(
        "--days-ahead", type=int, default=7,
        help="Horizonte futuro de partidos a buscar (default 7 días).",
    )
    parser.add_argument(
        "--max-matches", type=int, default=10,
        help="Procesar como mucho N partidos (default 10).",
    )
    parser.add_argument(
        "--all-leagues", action="store_true",
        help="Incluir todas las ligas (default: sólo top-5).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Hace rollback al final: muestra qué haría pero no persiste.",
    )
    args = parser.parse_args(argv)

    force_utf8_stdio()
    settings = get_settings()
    _configurar_logging(settings.log_level)
    log = logging.getLogger(__name__)

    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        ingester = WinamaxOddsIngester(session, log=log)
        result = ingester.ingest_upcoming(
            days_ahead=args.days_ahead,
            max_matches=args.max_matches,
            only_top5=not args.all_leagues,
            commit=not args.dry_run,
        )
        if args.dry_run:
            log.warning("--dry-run activo: rollback de la sesión.")
            session.rollback()

    log.info("=" * 90)
    log.info("INGESTA DE CUOTAS WINAMAX (player props + match-level)")
    log.info("=" * 90)
    log.info("  Partidos en BD:            %d", result.matches_in_db)
    log.info("  Emparejados con Winamax:   %d", result.matches_matched)
    log.info("  Sin emparejar:             %d", result.matches_unmatched)
    log.info("  Fallidos:                  %d", result.matches_failed)
    log.info("  Snapshots creados:         %d", result.snapshots_created)
    log.info("  Markets nuevos creados:    %d", result.markets_created)
    log.info("  Cuotas insertadas (total): %d", result.odds_inserted)
    log.info("    de las cuales player:    %d", result.player_props_inserted)
    log.info("  Jugadores creados:         %d", result.players_created)
    log.info("  Tiempo total:              %.1fs", result.duration_seconds)
    if result.errors:
        log.warning("  Errores (%d):", len(result.errors))
        for err in result.errors[:10]:
            log.warning("    - %s", err)
    log.info("=" * 90)
    return 0


if __name__ == "__main__":
    sys.exit(main())
