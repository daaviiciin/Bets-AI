"""CLI: escudos y colores de los equipos sin esperar al refresh programado.

Uso::

    python -m backend.tools.backfill_team_crests            # calendario 7 días + colores
    python -m backend.tools.backfill_team_crests --solo-colores

El calendario de Flashscore (7 peticiones, lo mismo que el paso 1 del refresh) guarda el
escudo de cada equipo; después se descarga una vez cada escudo sin color para calcularlo.
"""

from __future__ import annotations

import argparse
import logging
import sys

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import get_settings
from backend.app.database import get_session_factory
from backend.services.flashscore_fixtures_ingester import FlashscoreFixturesIngester
from backend.services.team_crests import TeamCrestColorizer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Escudos y colores de los equipos.")
    parser.add_argument("--solo-colores", action="store_true",
                        help="No lee el calendario: solo colorea los escudos ya guardados.")
    parser.add_argument("--limit", type=int, default=300, help="Máximo de escudos a colorear.")
    args = parser.parse_args(argv)

    force_utf8_stdio()
    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger(__name__)
    with get_session_factory()() as session:
        if not args.solo_colores:
            FlashscoreFixturesIngester(session, log=log).ingest(days_ahead=7, only_top5=True)
            session.commit()
        result = TeamCrestColorizer(session, log=log).run(limit=args.limit)
    for err in result.errors[:10]:
        log.warning("  - %s", err)
    return 0 if not result.failed else 1


if __name__ == "__main__":
    sys.exit(main())
