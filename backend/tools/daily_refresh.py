"""CLI: pipeline diario unificado (gratis).

Encadena (todo desde Flashscore, ver ``backend/services/daily_refresh.py``):

1. Próximos partidos (calendario).
2. Resultados.
3. Clasificación → ``team_season_stats``.
4. Stats post-partido por equipo.
5. Stats por jugador + totales de temporada.
6. Bajas y dudas.

Uso típico (local manual o vía cron / Task Scheduler de Windows)::

    python -m backend.tools.daily_refresh
    python -m backend.tools.daily_refresh --stats-days-back 120
    python -m backend.tools.daily_refresh --stats-limit 10 --solo-top5
"""

from __future__ import annotations

import argparse
import logging
import sys

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import get_settings
from backend.app.database import get_session_factory
from backend.services.daily_refresh import DailyRefreshOrchestrator


def _configurar_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pipeline diario Flashscore: calendario, resultados, clasificación y stats.",
    )
    parser.add_argument(
        "--results-days-back", type=int, default=3,
        help="Días hacia atrás del feed diario Flashscore para buscar resultados (máx. 7).",
    )
    parser.add_argument(
        "--stats-days-back", type=int, default=120,
        help="Antigüedad máxima de partidos para el histórico Flashscore (default 120).",
    )
    parser.add_argument(
        "--solo-top5", action="store_true",
        help="Calendario solo de las 5 grandes ligas (por defecto también UEFA).",
    )
    parser.add_argument(
        "--fixtures-days-ahead", type=int, default=7,
        help="Días de ventana de feeds Flashscore para calendario próximo (default 7).",
    )
    parser.add_argument(
        "--stats-limit", type=int, default=40,
        help="Procesar como mucho N partidos en el paso de stats (default 40, igual que la API; "
             "evita lanzar cientos de peticiones a Flashscore de golpe).",
    )
    args = parser.parse_args(argv)

    force_utf8_stdio()
    settings = get_settings()
    _configurar_logging(settings.log_level)
    log = logging.getLogger("daily_refresh")

    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        orchestrator = DailyRefreshOrchestrator(session, log=log)
        summary = orchestrator.run(
            results_days_back=args.results_days_back,
            stats_days_back=args.stats_days_back,
            stats_only_top5=args.solo_top5,
            stats_limit=args.stats_limit,
            fixtures_days_ahead=args.fixtures_days_ahead,
        )

    log.info("=" * 90)
    log.info("PIPELINE DIARIO — RESUMEN FINAL")
    log.info("=" * 90)
    if summary.fixtures is not None:
        fx = summary.fixtures
        log.info(
            "  Fixtures Flashscore: fetched=%d inserted=%d updated=%d dup_skip=%d",
            fx.fixtures_fetched, fx.fixtures_inserted, fx.fixtures_updated,
            fx.skipped_duplicate_db,
        )
    else:
        log.info("  Fixtures Flashscore: NO EJECUTADO (ver errores)")
    if summary.fs_results is not None:
        r = summary.fs_results
        log.info("  Resultados: terminados=%d  pendientes=%d  siguen_pendientes=%d",
                 r.matches_updated, r.matches_pending, r.still_pending)
    else:
        log.info("  Resultados: NO EJECUTADO (ver errores)")
    if summary.standings is not None:
        st = summary.standings
        log.info("  Clasificación: ligas=%d  equipos=%d", len(st.leagues_ok), st.teams_upserted)
    else:
        log.info("  Clasificación: NO EJECUTADO (ver errores)")
    if summary.stats is not None:
        s = summary.stats
        log.info("  Stats post-partido: emparejados=%d  nuevos=%d  actualizados=%d",
                 s.matches_matched, s.stats_inserted, s.stats_updated)
    else:
        log.info("  Stats post-partido: NO EJECUTADO (ver errores)")
    if summary.errors:
        log.warning("  Errores:")
        for e in summary.errors:
            log.warning("    · %s", e)
    log.info("  Tiempo total: %.1fs", summary.duration_seconds)
    log.info("=" * 90)
    return 0 if summary.ok else 1


if __name__ == "__main__":
    sys.exit(main())
