"""Resultados de partidos desde el feed diario de Flashscore.

Sustituye a Sofascore como fuente de resultados (Sofascore devuelve
``403 challenge`` desde octubre de 2026). Como los partidos se crean desde el
calendario de Flashscore (``external_id = "flashscore:<id>"``), el
emparejamiento es **exacto por id**: no hay que comparar nombres de equipo.

Flujo:

1. Partidos ``scheduled`` con id de Flashscore que empezaron hace al menos
   :data:`DURACION_MINIMA_PARTIDO`.
2. Descarga los feeds ``f_1_{offset}`` de los últimos ``days_back`` días
   (offset 0 = hoy, -1 = ayer…).
3. Si el feed marca el partido como terminado y trae marcador, se guarda
   ``status='finished'``, marcador y ``played_at``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Match
from backend.models.match import MATCH_STATUS_FINISHED, MATCH_STATUS_SCHEDULED
from backend.scrapers import flashscore as fs
from backend.services.match_absences_ingester import (
    FLASHSCORE_EXTERNAL_PREFIX,
    flashscore_id_from_external_id,
)

MAX_DAYS_BACK = 7  # el feed diario no sirve más atrás
REQUEST_SLEEP_SECONDS = 1.0
#: Desde el saque hasta dar el partido por terminado (90' + descanso + añadido). Antes de eso no
#: se cierra aunque el feed lo diga: un marcador en directo guardado como final no se revisa más.
DURACION_MINIMA_PARTIDO = timedelta(minutes=115)


@dataclass(frozen=True)
class FinishedResult:
    home_score: int
    away_score: int
    played_at: datetime | None


@dataclass
class FlashscoreResultsResult:
    started_at: datetime
    finished_at: datetime
    matches_pending: int = 0
    feeds_fetched: int = 0
    matches_updated: int = 0
    still_pending: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


def finished_results_by_id(
    fixtures: list[fs.FlashscoreMatch], wanted_ids: set[str],
) -> dict[str, FinishedResult]:
    """Resultados finales del feed para los ids que nos interesan.

    Solo cuenta un partido si el feed lo da por terminado **y** trae los dos goles.
    """
    out: dict[str, FinishedResult] = {}
    for fm in fixtures:
        if fm.match_id not in wanted_ids or not fm.is_finished:
            continue
        if fm.score_home is None or fm.score_away is None:
            continue
        out[fm.match_id] = FinishedResult(fm.score_home, fm.score_away, fm.scheduled_at)
    return out


def descargado_en_directo(commence_time: datetime, *descargas: datetime | None) -> bool:
    """``True`` si alguna descarga de datos del partido fue antes de que pudiera haber acabado.

    Sirve para encontrar partidos que se cerraron con el marcador (y las stats) del directo.
    """
    fin = commence_time + DURACION_MINIMA_PARTIDO
    return any(d is not None and d < fin for d in descargas)


class FlashscoreResultsUpdater:
    """Marca como terminados los partidos ya jugados, con su marcador."""

    def __init__(
        self,
        session: Session,
        *,
        log: logging.Logger | None = None,
        fetch_fixtures=fs.fetch_fixtures_feed,
        sleep_seconds: float = REQUEST_SLEEP_SECONDS,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._fetch_fixtures = fetch_fixtures
        self._sleep_seconds = sleep_seconds

    def update(self, *, days_back: int = 3) -> FlashscoreResultsResult:
        started = datetime.now(timezone.utc)
        result = FlashscoreResultsResult(started_at=started, finished_at=started)

        pending = self._pending_matches(now=started)
        result.matches_pending = len(pending)
        if not pending:
            result.finished_at = datetime.now(timezone.utc)
            return result

        by_fs_id = {flashscore_id_from_external_id(m.external_id): m for m in pending}
        wanted = {fid for fid in by_fs_id if fid}
        days_back = max(0, min(int(days_back), MAX_DAYS_BACK))

        for i, offset in enumerate(range(0, -days_back - 1, -1)):
            if not wanted:
                break
            if i and self._sleep_seconds:
                time.sleep(self._sleep_seconds)
            try:
                fixtures = self._fetch_fixtures(
                    day_offset=offset, sport=fs.FLASHSCORE_SPORT_SOCCER,
                )
            except Exception as exc:  # noqa: BLE001
                msg = f"flashscore resultados day_offset={offset}: {exc}"
                self.log.warning(msg)
                result.errors.append(msg)
                continue
            result.feeds_fetched += 1
            for fs_id, res in finished_results_by_id(fixtures, wanted).items():
                match = by_fs_id[fs_id]
                match.status = MATCH_STATUS_FINISHED
                match.home_score = res.home_score
                match.away_score = res.away_score
                match.played_at = res.played_at or match.commence_time
                wanted.discard(fs_id)
                result.matches_updated += 1

        self.session.flush()
        result.still_pending = len(wanted)
        result.finished_at = datetime.now(timezone.utc)
        return result

    def _pending_matches(self, *, now: datetime) -> list[Match]:
        return list(self.session.scalars(
            select(Match)
            .where(Match.external_id.like(f"{FLASHSCORE_EXTERNAL_PREFIX}%"))
            .where(Match.status == MATCH_STATUS_SCHEDULED)
            .where(Match.commence_time <= now - DURACION_MINIMA_PARTIDO)
        ).all())
