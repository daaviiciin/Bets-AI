"""Marcadores y stats de los partidos que se van a corregir, al pulsar "Comprobar".

Los resultados entran con el refresh programado (paso 2 de ``daily_refresh``). Si la última
tanda fue antes de que acabaran los partidos, o no llegó a ejecutarse (portátil en suspensión),
el botón "Comprobar" corregiría con datos viejos y las patas seguirían "pendientes".

Antes de corregir, aquí se pide a Flashscore **solo lo que falta para esas patas**:

1. Si alguna pata es de un partido que ya debería haber acabado y sigue ``scheduled``, el feed
   diario de resultados (``FlashscoreResultsUpdater``, 1 petición por día hacia atrás).
2. Si algún partido ya terminado no tiene stats de jugador o de equipo (props, tiros, córners),
   esas stats (2 peticiones por partido, como mucho :data:`MAX_PARTIDOS_STATS`).

Con un enfriamiento de :data:`ENFRIAMIENTO` entre descargas: pulsar el botón varias veces
seguidas no repite peticiones. Un fallo de Flashscore no impide corregir con lo que haya.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Match
from backend.models.match import MATCH_STATUS_FINISHED, MATCH_STATUS_SCHEDULED
from backend.services.flashscore_player_stats_ingester import FlashscorePlayerStatsIngester
from backend.services.flashscore_results_updater import (
    DURACION_MINIMA_PARTIDO,
    MAX_DAYS_BACK,
    FlashscoreResultsUpdater,
)
from backend.services.flashscore_stats_ingester import FlashscoreStatsIngester
from backend.services.match_absences_ingester import flashscore_id_from_external_id

#: Los partidos sin stats se descartan para esta descarga pasados estos días (las trae el refresh).
DIAS_STATS = 3
MAX_PARTIDOS_STATS = 10
ENFRIAMIENTO = timedelta(minutes=10)

_log = logging.getLogger(__name__)
_lock = threading.Lock()
_ultima_descarga: datetime | None = None


@dataclass(frozen=True)
class Pendientes:
    """Lo que falta descargar para corregir unas patas."""

    #: Partidos que ya deberían haber acabado y siguen sin marcador.
    sin_resultado: tuple[Match, ...]
    #: Partidos terminados (en los últimos :data:`DIAS_STATS` días) sin stats de jugador.
    sin_stats: tuple[Match, ...]

    @property
    def vacio(self) -> bool:
        return not self.sin_resultado and not self.sin_stats


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def que_falta(matches: Iterable[Match], ahora: datetime) -> Pendientes:
    """Clasifica los partidos de las patas según lo que les falta (función pura)."""
    sin_resultado: list[Match] = []
    sin_stats: list[Match] = []
    for m in matches:
        if not flashscore_id_from_external_id(m.external_id):
            continue
        inicio = _aware(m.commence_time)
        if m.status == MATCH_STATUS_SCHEDULED and inicio + DURACION_MINIMA_PARTIDO <= ahora:
            sin_resultado.append(m)
        elif (
            m.status == MATCH_STATUS_FINISHED
            and m.player_stats_fetched_at is None
            and inicio >= ahora - timedelta(days=DIAS_STATS)
        ):
            sin_stats.append(m)
    return Pendientes(tuple(sin_resultado), tuple(sin_stats))


def dias_hacia_atras(matches: Iterable[Match], ahora: datetime) -> int:
    """Días de feed que hay que pedir para cubrir el partido más antiguo (+1 por el huso)."""
    dias = max(((ahora - _aware(m.commence_time)).days for m in matches), default=0)
    return max(0, min(dias + 1, MAX_DAYS_BACK))


def traer_datos_para_corregir(
    session: Session,
    match_ids: Iterable[int],
    *,
    ahora: datetime | None = None,
    log: logging.Logger | None = None,
) -> bool:
    """Descarga marcadores y stats que falten para ``match_ids``. ``True`` si descargó algo."""
    global _ultima_descarga
    log = log or _log
    ahora = ahora or datetime.now(timezone.utc)
    ids = sorted(set(match_ids))
    if not ids:
        return False
    matches = session.scalars(select(Match).where(Match.id.in_(ids))).all()
    falta = que_falta(matches, ahora)
    if falta.vacio:
        return False

    # Sin bloquear: si otra petición ya está descargando, esta corrige con lo que haya.
    if not _lock.acquire(blocking=False):
        return False
    try:
        if _ultima_descarga is not None and ahora - _ultima_descarga < ENFRIAMIENTO:
            return False
        _ultima_descarga = ahora

        if falta.sin_resultado:
            try:
                res = FlashscoreResultsUpdater(session, log=log).update(
                    days_back=dias_hacia_atras(falta.sin_resultado, ahora),
                )
                session.commit()
                log.info("Comprobar: %d partidos cerrados con resultado", res.matches_updated)
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                log.warning("Comprobar: no se pudieron traer resultados: %s", exc)

        # Tras cerrar partidos puede haber nuevos sin stats.
        if que_falta(matches, ahora).sin_stats:
            try:
                FlashscorePlayerStatsIngester(session, log=log).ingest(
                    max_matches=MAX_PARTIDOS_STATS, days_back=DIAS_STATS,
                )
                FlashscoreStatsIngester(session, log=log).ingest_recent(
                    days_back=DIAS_STATS, only_top5=False, limit_matches=MAX_PARTIDOS_STATS,
                )
                session.commit()
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                log.warning("Comprobar: no se pudieron traer stats: %s", exc)
        return True
    finally:
        _lock.release()
