"""Ingesta de partidos próximos desde el feed de fixtures de Flashscore.

Crea filas ``Match`` con ``external_id`` prefijado ``flashscore:{id}`` para
que Winamax y el modelo puedan trabajar sin depender del Odds API.

* Sólo partidos **programados** (código de estado ``AB=1`` en el feed).
* Por defecto filtra a las **5 grandes ligas** (mismo criterio que el resto
  del proyecto).
* Evita duplicar encuentros ya presentes en BD con el mismo trío
  liga + local + visitante + ventana horaria (~4 h).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import League, Match
from backend.models.match import (
    MATCH_STATUS_CANCELED,
    MATCH_STATUS_POSTPONED,
    MATCH_STATUS_SCHEDULED,
)
from backend.scrapers import flashscore as fs
from backend.services.odds_processor import LEAGUE_METADATA
from backend.services.entity_resolver import TeamResolver

FLASHSCORE_MATCH_PREFIX = "flashscore:"

# Nombre **exacto** de la competición en Flashscore (campo ``ZA``) → ``sport_key``.
# Se compara sin la fase (" - League phase", " - Qualification"…). Antes se usaba
# ``startswith`` / ``in`` y se colaban "Premier League 2", "Premier League U18",
# "LaLiga2", "Bundesliga Women", "Serie A Women", "AFC/CAF Champions League"…
_TOP5_LEAGUE_NAMES: dict[str, str] = {
    "SPAIN: LaLiga": "soccer_spain_la_liga",
    "ENGLAND: Premier League": "soccer_epl",
    "GERMANY: Bundesliga": "soccer_germany_bundesliga",
    "ITALY: Serie A": "soccer_italy_serie_a",
    "FRANCE: Ligue 1": "soccer_france_ligue_one",
}

_EXTRA_LEAGUE_NAMES: dict[str, str] = {
    "EUROPE: Champions League": "soccer_uefa_champs_league",
    "EUROPE: Europa League": "soccer_uefa_europa_league",
    "EUROPE: Conference League": "soccer_uefa_europa_conference_league",
}

_DEDUP_WINDOW = timedelta(hours=4)
_MAX_DAY_OFFSETS = 14


def flashscore_external_id(match_id: str) -> str:
    return f"{FLASHSCORE_MATCH_PREFIX}{match_id}"


def _competition_base_name(league_full_name: str) -> str:
    """``"EUROPE: Champions League - League phase"`` → ``"EUROPE: Champions League"``."""
    return league_full_name.split(" - ", 1)[0].strip()


def resolve_top5_sport_key(league_full_name: str | None) -> str | None:
    if not league_full_name:
        return None
    return _TOP5_LEAGUE_NAMES.get(_competition_base_name(league_full_name))


def resolve_fixtures_sport_key(league_full_name: str | None, *, only_top5: bool) -> str | None:
    """Mapea el nombre Flashscore (``ZA``) a ``League.sport_key``."""
    top = resolve_top5_sport_key(league_full_name)
    if top:
        return top
    if only_top5 or not league_full_name:
        return None
    sk = _EXTRA_LEAGUE_NAMES.get(_competition_base_name(league_full_name))
    return sk if sk in LEAGUE_METADATA else None


@dataclass
class FlashscoreFixturesIngestResult:
    started_at: datetime
    finished_at: datetime
    fixtures_fetched: int = 0
    fixtures_inserted: int = 0
    fixtures_updated: int = 0
    skipped_duplicate_db: int = 0
    skipped_not_future: int = 0
    skipped_not_scheduled: int = 0
    skipped_unknown_league: int = 0
    skipped_bad_data: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


class FlashscoreFixturesIngester:
    def __init__(
        self,
        session: Session,
        *,
        log: logging.Logger | None = None,
        fetch_fixtures=fs.fetch_fixtures_feed,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._fetch_fixtures = fetch_fixtures
        self._teams = TeamResolver(session, log=self.log)

    def ingest(
        self,
        *,
        days_ahead: int = 7,
        only_top5: bool = True,
    ) -> FlashscoreFixturesIngestResult:
        """Descarga feeds ``f_1_{offset}_...`` y persiste partidos futuros."""
        started = datetime.now(timezone.utc)
        result = FlashscoreFixturesIngestResult(started_at=started, finished_at=started)
        now = started
        n_days = max(1, min(int(days_ahead), _MAX_DAY_OFFSETS))
        seen_ids: set[str] = set()

        try:
            for day_offset in range(n_days):
                try:
                    batch = self._fetch_fixtures(
                        day_offset=day_offset,
                        sport=fs.FLASHSCORE_SPORT_SOCCER,
                    )
                except Exception as exc:  # noqa: BLE001
                    msg = f"flashscore fixtures day_offset={day_offset}: {exc}"
                    self.log.warning(msg)
                    result.errors.append(msg)
                    continue

                for fm in batch:
                    if fm.match_id in seen_ids:
                        continue
                    seen_ids.add(fm.match_id)
                    result.fixtures_fetched += 1

                    sport_key = resolve_fixtures_sport_key(
                        fm.league_full_name,
                        only_top5=only_top5,
                    )
                    if sport_key is None:
                        result.skipped_unknown_league += 1
                        continue

                    ab = fm.raw.get("AB", "")
                    if ab == "8":
                        self._upsert_postponed_or_cancel(fm, result, cancel=False)
                        continue
                    if ab not in {"", "1"}:
                        result.skipped_not_scheduled += 1
                        continue

                    if fm.is_finished or ab == "3":
                        result.skipped_not_future += 1
                        continue

                    if not fm.scheduled_at:
                        result.skipped_bad_data += 1
                        continue

                    if fm.scheduled_at < now - timedelta(minutes=30):
                        result.skipped_not_future += 1
                        continue

                    self._upsert_scheduled_match(fm, sport_key, result)

        except Exception as exc:  # noqa: BLE001
            msg = f"Error en flashscore_fixtures_ingester: {exc}"
            self.log.exception(msg)
            result.errors.append(msg)

        result.finished_at = datetime.now(timezone.utc)
        self.log.info(
            "Flashscore fixtures: fetched=%d inserted=%d updated=%d dup=%d (%.1fs)",
            result.fixtures_fetched,
            result.fixtures_inserted,
            result.fixtures_updated,
            result.skipped_duplicate_db,
            result.duration_seconds,
        )
        return result

    def _get_or_create_league(self, sport_key: str) -> League:
        league = self.session.scalar(select(League).where(League.sport_key == sport_key))
        if league is not None:
            return league
        meta = LEAGUE_METADATA.get(sport_key, (sport_key, None))
        league = League(sport_key=sport_key, name=meta[0], country=meta[1])
        self.session.add(league)
        self.session.flush()
        self.log.info("Liga creada (fixtures Flashscore): %s", sport_key)
        return league

    def _find_semantic_duplicate(
        self,
        league_id: int,
        home_id: int,
        away_id: int,
        commence_time: datetime,
    ) -> bool:
        low, high = commence_time - _DEDUP_WINDOW, commence_time + _DEDUP_WINDOW
        existing = self.session.scalar(
            select(Match.id).where(
                Match.league_id == league_id,
                Match.home_team_id == home_id,
                Match.away_team_id == away_id,
                Match.commence_time >= low,
                Match.commence_time <= high,
            ).limit(1)
        )
        return existing is not None

    def _upsert_scheduled_match(
        self,
        fm: fs.FlashscoreMatch,
        sport_key: str,
        result: FlashscoreFixturesIngestResult,
    ) -> None:
        home = self._teams.resolve(
            fm.home_team_name, fs_id=fm.home_team_fs_id, sport_key=sport_key,
        )
        away = self._teams.resolve(
            fm.away_team_name, fs_id=fm.away_team_fs_id, sport_key=sport_key,
        )
        if home is None or away is None:
            result.skipped_bad_data += 1
            return

        league = self._get_or_create_league(sport_key)
        assert fm.scheduled_at is not None
        commence = fm.scheduled_at

        ext = flashscore_external_id(fm.match_id)
        row = self.session.scalar(select(Match).where(Match.external_id == ext))
        if row is not None:
            changed = False
            if row.commence_time != commence:
                row.commence_time = commence
                changed = True
            if row.status != MATCH_STATUS_SCHEDULED:
                row.status = MATCH_STATUS_SCHEDULED
                changed = True
            if changed:
                result.fixtures_updated += 1
            return

        if self._find_semantic_duplicate(league.id, home.id, away.id, commence):
            result.skipped_duplicate_db += 1
            return

        row = Match(
            external_id=ext,
            league_id=league.id,
            home_team_id=home.id,
            away_team_id=away.id,
            commence_time=commence,
            status=MATCH_STATUS_SCHEDULED,
        )
        self.session.add(row)
        self.session.flush()
        result.fixtures_inserted += 1

    def _upsert_postponed_or_cancel(
        self,
        fm: fs.FlashscoreMatch,
        result: FlashscoreFixturesIngestResult,
        *,
        cancel: bool,
    ) -> None:
        ext = flashscore_external_id(fm.match_id)
        row = self.session.scalar(select(Match).where(Match.external_id == ext))
        if row is None:
            return
        new_status = MATCH_STATUS_CANCELED if cancel else MATCH_STATUS_POSTPONED
        if row.status != new_status:
            row.status = new_status
            result.fixtures_updated += 1


__all__ = [
    "FLASHSCORE_MATCH_PREFIX",
    "FlashscoreFixturesIngester",
    "FlashscoreFixturesIngestResult",
    "flashscore_external_id",
    "resolve_fixtures_sport_key",
    "resolve_top5_sport_key",
]
