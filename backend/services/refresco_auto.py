"""Refresh automático al abrir la app: si los datos están viejos, se lanza el pipeline completo.

La tarea programada no es fiable en un portátil que pasa el día en suspensión (HOJA_DE_RUTA) y en
el PC nuevo ni existía: el calendario se quedó sin partidos y no había cuotas. Ahora la app pide
``POST /refresh/auto`` al abrirse y, si ``necesita_refresh`` dice que toca, aquí se lanza en un
hilo lo mismo que ``refresh_programado.ps1``, en el mismo orden:

1. ``DailyRefreshOrchestrator``: calendario, resultados, clasificación, stats y bajas (Flashscore).
2. Cuotas de Winamax de los próximos 5 días, todas las ligas.
3. Comparador de cuotas de Flashscore (bet365 y otras casas), solo ingesta: va tras Winamax para
   que los partidos con cuotas de Winamax ya estén y se lean primero. Se apaga con
   ``COMPARADOR_CUOTAS_ENABLED=false``.
4. Picks del día si faltan. Van al final porque se congelan: así salen con las stats al día.

Quién está corriendo lo sabe el proceso (un único uvicorn, como el limitador de peticiones). Si
la API se reinicia a mitad (``--reload``), el hilo muere y su fila se queda sin ``finished_at``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import PROJECT_ROOT, get_settings
from backend.app.database import get_session_factory
from backend.models import Match, RefreshRun
from backend.models.match import MATCH_STATUS_SCHEDULED
from backend.models.refresh_run import REFRESH_ORIGEN_APP
from backend.services.comparador_cuotas_ingester import ejecutar_paso_comparador
from backend.services.daily_refresh import DailyRefreshOrchestrator
from backend.services.featured_daily_picks import asegurar_picks_de_hoy
from backend.services.winamax_odds_ingester import WinamaxOddsIngester

#: Mismos valores que ``refresh_programado.ps1`` (``--days-ahead 5 --max-matches 200``).
WINAMAX_DIAS = 5
WINAMAX_MAX_PARTIDOS = 200
#: Como el botón "Actualizar cuotas": alguien espera delante de la pantalla.
WINAMAX_WORKERS = 4
WINAMAX_SALTAR_FRESCOS_MIN = 15
#: Tope de errores que se guardan por ejecución (Winamax da uno por partido fallido).
MAX_ERRORES = 30
#: Datos viejos a partir de aquí (la tarea programada iba cada 6 h).
MAX_ANTIGUEDAD = timedelta(hours=6)
#: Pausa mínima entre intentos, para no machacar a Flashscore si nos está bloqueando.
ESPERA_TRAS_INTENTO = timedelta(minutes=30)


@dataclass(frozen=True)
class Historial:
    """Lo que dice la BD de las ejecuciones anteriores (horas en UTC)."""

    #: Inicio del último intento, terminara o no (un corte por ``--reload`` no tiene fin).
    ultimo_inicio: datetime | None = None
    #: Fin del último que terminó, con o sin errores.
    ultimo_fin: datetime | None = None
    #: Si ese último terminado fue sin errores.
    ultimo_ok: bool | None = None
    ultimo_errores: list[str] = field(default_factory=list)
    #: Sin partidos programados en los próximos días: sin calendario no hay cuotas que enlazar.
    calendario_vacio: bool = False


@dataclass(frozen=True)
class EstadoRefresh:
    en_curso: bool
    historial: Historial
    #: ``True`` solo en la llamada que acaba de lanzarlo.
    lanzado: bool = False


def necesita_refresh(historial: Historial, ahora: datetime) -> bool:
    """¿Hay que lanzar el pipeline completo al abrir la app?

    Se llama en cada apertura (y tras cada login) cuando no hay otro en curso. Lanzarlo cuesta
    unos minutos y cientos de peticiones a Flashscore y Winamax, que pueden bloquear por IP.
    """
    if historial.ultimo_inicio is None:
        return True
    # Tras cualquier intento (en curso desde el CLI, fallido o cortado por --reload) se espera.
    if ahora - historial.ultimo_inicio < ESPERA_TRAS_INTENTO:
        return False
    termino = historial.ultimo_fin is not None and historial.ultimo_fin >= historial.ultimo_inicio
    if not termino or historial.calendario_vacio:
        return True
    # Con errores también cuenta: suelen ser menores (bajas de un partido) y repetirlo entero no
    # compensa.
    return ahora - historial.ultimo_fin >= MAX_ANTIGUEDAD


def historial_de_refresh(session: Session) -> Historial:
    ultimo = session.scalar(select(RefreshRun).order_by(RefreshRun.started_at.desc()).limit(1))
    terminado = session.scalar(
        select(RefreshRun)
        .where(RefreshRun.finished_at.is_not(None))
        .order_by(RefreshRun.finished_at.desc())
        .limit(1)
    )
    ahora = datetime.now(timezone.utc)
    proximos = session.scalar(
        select(func.count(Match.id))
        .where(Match.status == MATCH_STATUS_SCHEDULED)
        .where(Match.commence_time >= ahora)
        .where(Match.commence_time <= ahora + timedelta(days=WINAMAX_DIAS))
    )
    if ultimo is None:
        return Historial(calendario_vacio=not proximos)
    return Historial(
        ultimo_inicio=ultimo.started_at,
        ultimo_fin=terminado.finished_at if terminado else None,
        ultimo_ok=terminado.ok if terminado else None,
        ultimo_errores=list(terminado.errores or []) if terminado else [],
        calendario_vacio=not proximos,
    )


def ejecutar_pipeline(session: Session, *, log: logging.Logger) -> list[str]:
    """Los pasos de ``refresh_programado.ps1``, en su orden. Si uno falla, siguen los demás."""
    errores = list(DailyRefreshOrchestrator(session, log=log).run().errors)

    log.info("Cuotas Winamax (próximos %d días)...", WINAMAX_DIAS)
    try:
        wres = WinamaxOddsIngester(
            session, log=log, settings=get_settings(), fetch_workers=WINAMAX_WORKERS,
        ).ingest_upcoming(
            days_ahead=WINAMAX_DIAS,
            max_matches=WINAMAX_MAX_PARTIDOS,
            only_top5=False,
            sport="football",
            skip_fresh_minutes=WINAMAX_SALTAR_FRESCOS_MIN,
            commit=True,
        )
        errores.extend(f"winamax: {e}" for e in wres.errors)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.exception("Cuotas Winamax: fallo")
        errores.append(f"winamax: {exc}")

    # No lanza: sus fallos vuelven como errores y los picks salen igual.
    errores.extend(ejecutar_paso_comparador(session, log=log).errores)

    log.info("Picks del día...")
    try:
        res = asegurar_picks_de_hoy(session, log=log)
        log.info("Picks del día: %s", res.motivo or res.estado)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.exception("Picks del día: fallo")
        errores.append(f"picks del día: {exc}")
    return errores


class RefrescoAuto:
    """Lanza el pipeline en un hilo, uno a la vez por proceso."""

    def __init__(
        self,
        session_factory: Callable[[], Session] | None = None,
        *,
        lanzar: Callable[[int], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._lanzar = lanzar or self._lanzar_hilo
        self._cerrojo = threading.Lock()
        self._en_curso = False

    @property
    def en_curso(self) -> bool:
        return self._en_curso

    def estado(self, session: Session) -> EstadoRefresh:
        return EstadoRefresh(en_curso=self._en_curso, historial=historial_de_refresh(session))

    def asegurar(self, session: Session, ahora: datetime | None = None) -> EstadoRefresh:
        """Si toca, apunta la ejecución, lanza el pipeline y vuelve enseguida."""
        ahora = ahora or datetime.now(timezone.utc)
        with self._cerrojo:
            historial = historial_de_refresh(session)
            if self._en_curso or not necesita_refresh(historial, ahora):
                return EstadoRefresh(en_curso=self._en_curso, historial=historial)
            run = RefreshRun(origen=REFRESH_ORIGEN_APP, started_at=ahora)
            session.add(run)
            session.commit()
            self._en_curso = True
        try:
            self._lanzar(run.id)
        except Exception:
            self._en_curso = False
            raise
        return EstadoRefresh(
            en_curso=True, lanzado=True, historial=replace(historial, ultimo_inicio=ahora),
        )

    def _lanzar_hilo(self, run_id: int) -> None:
        threading.Thread(
            target=self._ejecutar, args=(run_id,), name="refresh-auto", daemon=True,
        ).start()

    def _ejecutar(self, run_id: int) -> None:
        log_run, handler = _logger_con_fichero()
        errores: list[str] = []
        try:
            log_run.info("=== Inicio del refresh automático (al abrir la app) ===")
            factory = self._session_factory or get_session_factory()
            with factory() as session:
                try:
                    errores = ejecutar_pipeline(session, log=log_run)
                except Exception as exc:  # noqa: BLE001
                    session.rollback()
                    log_run.exception("Refresh automático: fallo inesperado")
                    errores.append(f"inesperado: {exc}")
                run = session.get(RefreshRun, run_id)
                if run is not None:
                    run.finished_at = datetime.now(timezone.utc)
                    run.ok = not errores
                    run.errores = errores[:MAX_ERRORES] or None
                    session.commit()
            log_run.info("=== Fin del refresh automático (%d errores) ===", len(errores))
        except Exception:  # noqa: BLE001
            log_run.exception("Refresh automático: no se pudo guardar el resultado")
        finally:
            self._en_curso = False
            log_run.removeHandler(handler)
            handler.close()


def _logger_con_fichero() -> tuple[logging.Logger, logging.Handler]:
    """Mismo fichero que la tarea programada: ``logs/refresh_AAAA-MM-DD.log``."""
    carpeta = PROJECT_ROOT / "logs"
    carpeta.mkdir(exist_ok=True)
    handler = logging.FileHandler(
        carpeta / f"refresh_{datetime.now():%Y-%m-%d}.log", encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", datefmt="%H:%M:%S",
    ))
    log_run = logging.getLogger("refresco_auto.pipeline")
    log_run.setLevel(logging.INFO)
    log_run.addHandler(handler)
    return log_run, handler


#: El de la API (un único uvicorn).
refresco_auto = RefrescoAuto()

__all__ = [
    "EstadoRefresh",
    "Historial",
    "RefrescoAuto",
    "ejecutar_pipeline",
    "historial_de_refresh",
    "necesita_refresh",
    "refresco_auto",
]
