"""CLI: ingiere stats post-partido desde Flashscore para los matches finalizados.

Uso típico (después de actualizar resultados con ``update_match_results``)::

    python -m backend.tools.import_flashscore_stats
    python -m backend.tools.import_flashscore_stats --days-back 120
    python -m backend.tools.import_flashscore_stats --limit 5

Es 100% gratis (Flashscore vía scraping, sin créditos consumidos).
Idempotente: si un partido ya tiene stats, las actualiza con los valores
nuevos en lugar de duplicarlas.
"""

from __future__ import annotations

import argparse
import logging
import sys

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import get_settings
from backend.app.database import get_session_factory
from backend.services.flashscore_stats_ingester import FlashscoreStatsIngester


def _configurar_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingiere stats post-partido de Flashscore en BD.",
    )
    parser.add_argument(
        "--days-back", type=int, default=120,
        help="Antigüedad máxima de partidos a comprobar (default 120 días).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Procesar como mucho N partidos (útil para pruebas).",
    )
    parser.add_argument(
        "--all-leagues", action="store_true",
        help="No filtrar por top-5 (incluye competiciones europeas y otras).",
    )
    args = parser.parse_args(argv)

    force_utf8_stdio()
    settings = get_settings()
    _configurar_logging(settings.log_level)
    log = logging.getLogger(__name__)

    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        ingester = FlashscoreStatsIngester(session, log=log)
        result = ingester.ingest_recent(
            days_back=args.days_back,
            only_top5=not args.all_leagues,
            limit_matches=args.limit,
        )
        session.commit()

    log.info("=" * 90)
    log.info("INGESTA DE STATS POST-PARTIDO (FLASHSCORE)")
    log.info("=" * 90)
    log.info("  Partidos elegibles:    %d", result.matches_eligible)
    log.info("  Emparejados:           %d", result.matches_matched)
    log.info("  Sin emparejar:         %d", result.matches_unmatched)
    log.info("  Stats nuevas:          %d", result.stats_inserted)
    log.info("  Stats actualizadas:    %d", result.stats_updated)
    log.info("  Errores fetch/parse:   %d / %d", result.fetch_errors, result.parse_errors)
    log.info("  Competiciones:         %s", ", ".join(result.competitions_visited) or "—")
    log.info("  Tiempo total:          %.1fs", result.duration_seconds)
    log.info("=" * 90)
    return 0 if not result.errors else 0  # los errores no son fatales


if __name__ == "__main__":
    sys.exit(main())
