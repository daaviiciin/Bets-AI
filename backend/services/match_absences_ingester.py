"""Ingesta de bajas y dudas por partido desde Flashscore.

Flujo:

1. Selecciona partidos con ``external_id`` de Flashscore que necesitan
   consulta (ver :func:`needs_absences_refresh`):
   - próximos (``scheduled``) que empiezan en los próximos ``days_ahead`` días:
     se consultan en cada refresh, porque las bajas cambian hasta el último día;
   - jugados (``finished``) sin consulta posterior al inicio: una consulta
     más deja la foto definitiva (y sirve de backfill del histórico).
2. Descarga las bajas (GraphQL ``dmpe2``) y **sustituye** las filas de
   ``match_absences`` de ese partido.
3. Marca ``Match.absences_fetched_at``.

Cada partido hace commit por separado: un fallo no pierde lo ya descargado.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.models import Match, MatchAbsence
from backend.models.match import MATCH_STATUS_FINISHED, MATCH_STATUS_SCHEDULED
from backend.scrapers import flashscore as fs

FLASHSCORE_EXTERNAL_PREFIX = "flashscore:"
REQUEST_SLEEP_SECONDS = 1.0
# Con 3 refreshes al día (09, 15, 21 h), 20 h = una consulta diaria por partido próximo,
# con margen por si un refresh se retrasa.
UPCOMING_REFETCH_AFTER = timedelta(hours=20)


@dataclass
class AbsencesIngestResult:
    started_at: datetime
    finished_at: datetime
    matches_eligible: int = 0
    matches_fetched: int = 0
    absences_saved: int = 0
    doubtful_saved: int = 0
    fetch_errors: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


def flashscore_id_from_external_id(external_id: str | None) -> str | None:
    """``"flashscore:4j50U1OM"`` → ``"4j50U1OM"``; otras fuentes → ``None``."""
    if not external_id or not external_id.startswith(FLASHSCORE_EXTERNAL_PREFIX):
        return None
    return external_id[len(FLASHSCORE_EXTERNAL_PREFIX):] or None


def needs_absences_refresh(
    *,
    status: str,
    commence_time: datetime,
    absences_fetched_at: datetime | None,
    now: datetime,
    days_ahead: int,
) -> bool:
    """Decide si un partido necesita consultar sus bajas en este refresh.

    Los próximos se consultan como mucho una vez al día (``UPCOMING_REFETCH_AFTER``): si ya
    se miraron en un refresh anterior del mismo día, su hueco pasa al backfill del histórico.
    """
    if status == MATCH_STATUS_SCHEDULED:
        if not now <= commence_time <= now + timedelta(days=days_ahead):
            return False
        return absences_fetched_at is None or now - absences_fetched_at >= UPCOMING_REFETCH_AFTER
    if status == MATCH_STATUS_FINISHED:
        return absences_fetched_at is None or absences_fetched_at < commence_time
    return False


def choose_absences_batch(
    upcoming: list[Match],
    finished: list[Match],
    max_matches: int,
) -> list[Match]:
    """Elige qué partidos se consultan en este refresh (como mucho ``max_matches``).

    :param upcoming: próximos partidos (``scheduled``), ya ordenados del más cercano al más
        lejano. Se reconsultan en cada refresh porque las bajas cambian hasta el último día.
    :param finished: jugados que aún no tienen la foto definitiva de bajas, del más antiguo
        al más reciente (backfill del histórico).

    Los próximos van primero. Como cada uno se consulta como mucho una vez al día (ver
    :func:`needs_absences_refresh`), en una semana con más próximos que cupo los restantes
    entran en el siguiente refresh y los huecos libres van al histórico.
    """
    return (upcoming + finished)[:max_matches]


def dedupe_absences(absences: list[fs.FlashscoreAbsence]) -> list[fs.FlashscoreAbsence]:
    """Un jugador por partido; si sale en bajas y en dudas, gana la baja (va primero)."""
    seen: set[str] = set()
    out: list[fs.FlashscoreAbsence] = []
    for a in absences:
        if a.player_external_id in seen:
            continue
        seen.add(a.player_external_id)
        out.append(a)
    return out


class MatchAbsencesIngester:
    """Descarga y persiste las bajas de los partidos que lo necesitan."""

    def __init__(
        self,
        session: Session,
        *,
        log: logging.Logger | None = None,
        fetch=fs.fetch_missing_players,
        sleep_seconds: float = REQUEST_SLEEP_SECONDS,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._fetch = fetch
        self._sleep_seconds = sleep_seconds

    def ingest(self, *, days_ahead: int = 3, max_matches: int = 40) -> AbsencesIngestResult:
        started = datetime.now(timezone.utc)
        result = AbsencesIngestResult(started_at=started, finished_at=started)

        eligible = self._eligible_matches(now=started, days_ahead=days_ahead)
        result.matches_eligible = len(eligible)
        upcoming = [m for m in eligible if m.status == MATCH_STATUS_SCHEDULED]
        finished = [m for m in eligible if m.status != MATCH_STATUS_SCHEDULED]
        batch = choose_absences_batch(upcoming, finished, max_matches)
        if len(eligible) > len(batch):
            self.log.info(
                "Bajas: %d elegibles (%d próximos, %d jugados), se consultan %d (max_matches).",
                len(eligible), len(upcoming), len(finished), len(batch),
            )
        for i, match in enumerate(batch):
            if i and self._sleep_seconds:
                time.sleep(self._sleep_seconds)
            fs_id = flashscore_id_from_external_id(match.external_id)
            try:
                payload = self._fetch(fs_id)
                absences = dedupe_absences(fs.parse_missing_players(payload))
                self._replace_absences(match, absences, captured_at=datetime.now(timezone.utc))
                self.session.commit()
            except Exception as exc:  # noqa: BLE001
                self.session.rollback()
                result.fetch_errors += 1
                result.errors.append(f"match={match.id} fs={fs_id}: {exc}")
                self.log.warning("Bajas: error en match=%s fs=%s: %s", match.id, fs_id, exc)
                continue
            result.matches_fetched += 1
            result.absences_saved += len(absences)
            result.doubtful_saved += sum(
                1 for a in absences if a.status == fs.ABSENCE_STATUS_DOUBTFUL
            )

        result.finished_at = datetime.now(timezone.utc)
        return result

    def _eligible_matches(self, *, now: datetime, days_ahead: int) -> list[Match]:
        candidates = self.session.scalars(
            select(Match)
            .where(Match.external_id.like(f"{FLASHSCORE_EXTERNAL_PREFIX}%"))
            .where(Match.status.in_([MATCH_STATUS_SCHEDULED, MATCH_STATUS_FINISHED]))
            .where(Match.commence_time <= now + timedelta(days=days_ahead))
        ).all()
        eligible = [
            m for m in candidates
            if needs_absences_refresh(
                status=m.status,
                commence_time=m.commence_time,
                absences_fetched_at=m.absences_fetched_at,
                now=now,
                days_ahead=days_ahead,
            )
        ]
        # Primero los próximos (más cercanos antes); después el histórico.
        eligible.sort(key=lambda m: (m.status != MATCH_STATUS_SCHEDULED, m.commence_time))
        return eligible

    def _replace_absences(
        self,
        match: Match,
        absences: list[fs.FlashscoreAbsence],
        *,
        captured_at: datetime,
    ) -> None:
        self.session.execute(delete(MatchAbsence).where(MatchAbsence.match_id == match.id))
        team_by_side = {"home": match.home_team_id, "away": match.away_team_id}
        for a in absences:
            self.session.add(MatchAbsence(
                match_id=match.id,
                team_id=team_by_side[a.side],
                player_external_id=a.player_external_id[:32],
                player_name=a.player_name[:128],
                status=a.status,
                reason=a.reason[:128] if a.reason else None,
                captured_at=captured_at,
            ))
        match.absences_fetched_at = captured_at
