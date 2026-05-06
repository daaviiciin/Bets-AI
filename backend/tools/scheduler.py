"""Scheduler local con APScheduler.

Lanza el pipeline diario en cron y se queda corriendo hasta que se mata.

Uso::

    python -m backend.tools.scheduler
    python -m backend.tools.scheduler --daily-hour 8 --daily-minute 30
    python -m backend.tools.scheduler --run-now    # ejecuta una vez al arranque

Schedule por defecto:

* **08:00 hora local** todos los días → ``daily_refresh`` (resultados +
  stats Flashscore). Gratis. Justo después de que normalmente todos los
  partidos del día anterior estén finalizados.

NO programamos el refresh de cuotas (``/refresh``) porque cuesta créditos
y queremos que el usuario lo dispare manualmente desde el dashboard.

Para detener el scheduler: Ctrl+C (manda SIGINT, APScheduler hace shutdown
limpio).
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import get_settings
from backend.app.database import get_session_factory
from backend.services.daily_refresh import DailyRefreshOrchestrator


def _configurar_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_daily_pipeline() -> None:
    """Ejecuta el pipeline diario contra una sesión nueva."""
    log = logging.getLogger("scheduler.daily")
    log.info("Trigger del pipeline diario @ %s", datetime.now(timezone.utc).isoformat())
    SessionLocal = get_session_factory()
    try:
        with SessionLocal() as session:
            orchestrator = DailyRefreshOrchestrator(session, log=log)
            summary = orchestrator.run()
        log.info(
            "Pipeline diario terminado: ok=%s  duración=%.1fs  errores=%d",
            summary.ok, summary.duration_seconds, len(summary.errors),
        )
    except Exception:  # noqa: BLE001
        # Loguemos pero NO dejemos morir el scheduler
        log.exception("Pipeline diario falló inesperadamente")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scheduler local: ejecuta el pipeline diario en cron.",
    )
    parser.add_argument(
        "--daily-hour", type=int, default=8,
        help="Hora local del pipeline diario (default 8 = 08:00).",
    )
    parser.add_argument(
        "--daily-minute", type=int, default=0,
        help="Minuto local del pipeline diario (default 0).",
    )
    parser.add_argument(
        "--run-now", action="store_true",
        help="Ejecutar el pipeline una vez al arrancar (además del cron).",
    )
    args = parser.parse_args(argv)

    force_utf8_stdio()
    settings = get_settings()
    _configurar_logging(settings.log_level)
    log = logging.getLogger("scheduler")

    scheduler = BlockingScheduler(timezone="Europe/Madrid")
    scheduler.add_job(
        run_daily_pipeline,
        trigger=CronTrigger(hour=args.daily_hour, minute=args.daily_minute),
        id="daily_refresh",
        name="Pipeline diario (resultados + stats Flashscore)",
        replace_existing=True,
        misfire_grace_time=60 * 60,  # tolerar 1h de retraso si el equipo estaba dormido
    )

    log.info("=" * 80)
    log.info("Scheduler local arrancado.")
    log.info("Cron diario: %02d:%02d (Europe/Madrid)", args.daily_hour, args.daily_minute)
    next_run = scheduler.get_job("daily_refresh").trigger.get_next_fire_time(
        None, datetime.now(scheduler.timezone),
    )
    log.info("Próxima ejecución: %s", next_run)
    log.info("Pulsa Ctrl+C para parar.")
    log.info("=" * 80)

    if args.run_now:
        log.info("Modo --run-now: ejecutando pipeline una vez al arrancar...")
        run_daily_pipeline()

    # Shutdown limpio en SIGINT/SIGTERM
    def _shutdown(signum: int, _frame) -> None:  # noqa: ANN001
        log.info("Señal %d recibida, parando scheduler...", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    try:
        scheduler.start()  # bloquea
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler detenido por el usuario.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
