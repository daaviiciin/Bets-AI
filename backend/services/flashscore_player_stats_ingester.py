"""Estadísticas por jugador y partido desde Flashscore → ``player_match_stats``.

Sustituye a Sofascore (``/event/{id}/lineups``, bloqueado con ``403 challenge``)
y no necesita ``sofascore_event_id``: usa el id de Flashscore del partido.

Por partido jugado, 2 peticiones (pestaña "Estadísticas individuales"):
``epmsse`` (jugadores) y ``epmsd`` (~100 estadísticas + nota).

Se consulta una sola vez por partido (``Match.player_stats_fetched_at``), aunque
Flashscore no tenga datos, para no repetir peticiones en cada refresh.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Match, PlayerMatchStats
from backend.models.match import MATCH_STATUS_FINISHED
from backend.scrapers import flashscore as fs
from backend.services.entity_resolver import FLASHSCORE_SOURCE, PlayerResolver
from backend.services.match_absences_ingester import (
    FLASHSCORE_EXTERNAL_PREFIX,
    flashscore_id_from_external_id,
)
from backend.services.player_match_stats_ingester import PlayerStatsIngestResult, _apply_stats

REQUEST_SLEEP_SECONDS = 1.0

# Palabra de la posición de Flashscore (es) → código corto G/D/M/F de la columna.
_POSITION_CODES: tuple[tuple[str, str], ...] = (
    ("guardameta", "G"), ("portero", "G"),
    ("defensa", "D"), ("lateral", "D"), ("central", "D"),
    ("centrocampista", "M"), ("mediocentro", "M"), ("pivote", "M"), ("mediapunta", "M"),
    ("extremo", "F"), ("delantero", "F"),
)


def position_code(position: str | None, *, is_goalkeeper: bool) -> str | None:
    """``"Centrocampista"`` → ``"M"``; desconocida → ``None``."""
    if is_goalkeeper:
        return "G"
    low = (position or "").lower()
    for word, code in _POSITION_CODES:
        if word in low:
            return code
    return None


class FlashscorePlayerStatsIngester:
    def __init__(
        self,
        session: Session,
        *,
        log: logging.Logger | None = None,
        fetch=fs.fetch_player_match_stats,
        sleep_seconds: float = REQUEST_SLEEP_SECONDS,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._fetch = fetch
        self._sleep_seconds = sleep_seconds
        self._players = PlayerResolver(session, log=self.log)

    def ingest(self, *, max_matches: int = 30, days_back: int = 60) -> PlayerStatsIngestResult:
        started = datetime.now(timezone.utc)
        result = PlayerStatsIngestResult(started_at=started, finished_at=started)
        eligible = self._eligible(now=started, days_back=days_back)
        result.matches_eligible = len(eligible)
        for i, match in enumerate(eligible[:max_matches]):
            if i and self._sleep_seconds:
                time.sleep(self._sleep_seconds)
            fs_id = flashscore_id_from_external_id(match.external_id)
            try:
                players_payload, stats_payload = self._fetch(fs_id)
                rows = fs.parse_player_match_stats(players_payload, stats_payload)
                self._save(match, fs_id, rows, result)
                match.player_stats_fetched_at = datetime.now(timezone.utc)
                self.session.commit()
            except Exception as exc:  # noqa: BLE001
                self.session.rollback()
                result.matches_failed += 1
                result.errors.append(f"match={match.id} fs={fs_id}: {exc}")
                self.log.warning("Stats jugadores: error match=%s fs=%s: %s", match.id, fs_id, exc)
                continue
            result.matches_processed += 1
        result.finished_at = datetime.now(timezone.utc)
        return result

    def _eligible(self, *, now: datetime, days_back: int) -> list[Match]:
        return list(self.session.scalars(
            select(Match)
            .where(Match.external_id.like(f"{FLASHSCORE_EXTERNAL_PREFIX}%"))
            .where(Match.status == MATCH_STATUS_FINISHED)
            .where(Match.player_stats_fetched_at.is_(None))
            .where(Match.commence_time >= now - timedelta(days=days_back))
            .order_by(Match.commence_time.desc())
        ).all())

    def _save(
        self,
        match: Match,
        fs_id: str,
        rows: list[fs.FlashscorePlayerMatchStats],
        result: PlayerStatsIngestResult,
    ) -> None:
        captured_at = datetime.now(timezone.utc)
        for row in rows:
            is_home = row.side == "home"
            team_id = match.home_team_id if is_home else match.away_team_id
            player, created = self._players.resolve(
                row.player_name, fs_id=row.player_fs_id, team_id=team_id,
            )
            if created:
                result.players_created += 1
            if row.photo_path and player.photo_path != row.photo_path:
                player.photo_path = row.photo_path
            pms = self.session.scalar(
                select(PlayerMatchStats).where(
                    PlayerMatchStats.match_id == match.id,
                    PlayerMatchStats.player_id == player.id,
                )
            )
            if pms is None:
                pms = PlayerMatchStats(match_id=match.id, player_id=player.id)
                self.session.add(pms)
                result.rows_inserted += 1
            else:
                result.rows_updated += 1
            pms.team_id = team_id
            pms.is_home = is_home
            pms.source = FLASHSCORE_SOURCE
            pms.source_match_id = fs_id
            pms.captured_at = captured_at
            pms.is_starter = row.is_starter
            pms.is_substitute = not row.is_starter
            pms.position_in_match = position_code(row.position, is_goalkeeper=row.is_goalkeeper)
            stats = dict(row.stats)
            if row.rating is not None:
                stats["rating"] = row.rating
            _apply_stats(pms, stats)
