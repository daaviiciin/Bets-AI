"""CLI: comparador de cuotas de Flashscore (bet365, Winamax y otras casas españolas) a BD.

Uso::

    python -m backend.tools.import_comparador_cuotas
    python -m backend.tools.import_comparador_cuotas --days-ahead 2 --max-matches 10
    python -m backend.tools.import_comparador_cuotas --dry-run

Lo lanza ``refresh_programado.ps1`` después de las cuotas de Winamax y antes de los picks; el
refresh al abrir la app hace lo mismo con ``ejecutar_paso_comparador``. Sin argumentos usa
``COMPARADOR_CUOTAS_DAYS_AHEAD`` y ``COMPARADOR_CUOTAS_MAX_PARTIDOS``; con
``COMPARADOR_CUOTAS_ENABLED=false`` no hace nada. Cada partido es 1 petición más 1,5 s de pausa.

``--dry-run`` solo consulta la BD y lista los partidos que se leerían: **no hace peticiones**.
Código de salida 1 si algún partido falló (la tarea programada no lo propaga: no corta nada).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import get_settings
from backend.app.database import get_session_factory
from backend.services.comparador_cuotas_ingester import (
    ComparadorCuotasIngester,
    ejecutar_paso_comparador,
)


def _configurar_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()  # antes de argparse: la ayuda lleva tildes
    parser = argparse.ArgumentParser(
        description="Ingesta del comparador de cuotas de Flashscore (bet365 y casas españolas).",
    )
    parser.add_argument(
        "--days-ahead", type=int, default=None,
        help="Días vista (por defecto COMPARADOR_CUOTAS_DAYS_AHEAD, 3).",
    )
    parser.add_argument(
        "--max-matches", type=int, default=None,
        help="Tope de partidos = peticiones (por defecto COMPARADOR_CUOTAS_MAX_PARTIDOS, 40).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Solo lista los partidos que se leerían (consulta la BD, sin peticiones).",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    _configurar_logging(settings.log_level)
    log = logging.getLogger("import_comparador_cuotas")

    with get_session_factory()() as session:
        if args.dry_run:
            if not settings.comparador_cuotas_enabled:
                log.info("--dry-run: COMPARADOR_CUOTAS_ENABLED=false, el refresh se saltaría el paso.")
            dias = settings.comparador_cuotas_days_ahead if args.days_ahead is None else args.days_ahead
            tope = (
                settings.comparador_cuotas_max_partidos if args.max_matches is None
                else args.max_matches
            )
            elegibles = ComparadorCuotasIngester(session, log=log).partidos_elegibles(
                now=datetime.now(timezone.utc), days_ahead=dias,
            )
            log.info(
                "--dry-run: %d partidos elegibles en %d días; se leerían %d (sin peticiones).",
                len(elegibles), dias, min(len(elegibles), tope),
            )
            for m in elegibles[:tope]:
                log.info("  match=%s %s %s", m.id, f"{m.commence_time:%Y-%m-%d %H:%M}", m.external_id)
            session.rollback()
            return 0
        paso = ejecutar_paso_comparador(
            session, log=log, settings=settings,
            days_ahead=args.days_ahead, max_matches=args.max_matches,
        )

    res = paso.result
    if res is not None:
        log.info("=" * 90)
        log.info("COMPARADOR DE CUOTAS (Flashscore)")
        log.info("=" * 90)
        log.info("  Partidos elegibles:        %d", res.matches_eligible)
        log.info("  Leídos:                    %d", res.matches_fetched)
        log.info("  Fallidos:                  %d%s", res.fetch_errors,
                 " (tanda cortada)" if res.cortado else "")
        log.info("  Cuotas vistas:             %d", res.cuotas_vistas)
        log.info("  Filas nuevas / alargadas:  %d / %d", res.filas_nuevas, res.filas_extendidas)
        for casa, n in sorted(res.cuotas_por_casa.items(), key=lambda kv: -kv[1]):
            log.info("    %-22s %d", casa, n)
        log.info("  Tiempo total:              %.1fs", res.duration_seconds)
        for err in res.errors[:10]:
            log.warning("    - %s", err)
        log.info("=" * 90)
    for err in paso.errores:
        log.warning("%s", err)
    return 1 if paso.errores else 0


if __name__ == "__main__":
    sys.exit(main())
