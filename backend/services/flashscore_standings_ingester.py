"""Clasificación de liga desde Flashscore → ``team_season_stats``.

Sustituye a Sofascore (``standings/total``, bloqueado con ``403 challenge``).
Es la tabla que usa el modelo del dashboard para los goles a favor / en contra
por partido de cada equipo.

Por liga, 2 peticiones:

1. HTML de la clasificación → ``tournamentId``, ``tournamentStageId``, temporada
   y la lista de partidos jugados (se devuelve para el backfill).
2. Feed ``to_{torneo}_{fase}_1`` → una fila por equipo.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import League, Season, TeamSeasonStats
from backend.scrapers import flashscore as fs
from backend.services.entity_resolver import TeamResolver
from backend.services.odds_processor import LEAGUE_METADATA

TOP5_SPORT_KEYS: tuple[str, ...] = (
    "soccer_spain_la_liga",
    "soccer_epl",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
)
REQUEST_SLEEP_SECONDS = 1.0


@dataclass
class StandingsIngestResult:
    started_at: datetime
    finished_at: datetime
    leagues_ok: list[str] = field(default_factory=list)
    teams_upserted: int = 0
    teams_created: int = 0
    # sport_key → partidos jugados publicados en la página (para el backfill).
    finished_matches: dict[str, list[fs.FlashscoreMatch]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


def season_years(year_start: int | None, year_end: int | None, now: datetime) -> tuple[int, int]:
    """Temporada de la página o, si no aparece, la que está en curso (empieza en julio)."""
    if year_start and year_end:
        return year_start, year_end
    start = now.year if now.month >= 7 else now.year - 1
    return start, start + 1


class FlashscoreStandingsIngester:
    def __init__(
        self,
        session: Session,
        *,
        log: logging.Logger | None = None,
        fetch_page=fs.fetch_league_page,
        fetch_standings=fs.fetch_standings_feed,
        sleep_seconds: float = REQUEST_SLEEP_SECONDS,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._fetch_page = fetch_page
        self._fetch_standings = fetch_standings
        self._sleep_seconds = sleep_seconds
        self._teams = TeamResolver(session, log=self.log)

    def ingest(self, *, sport_keys: tuple[str, ...] = TOP5_SPORT_KEYS) -> StandingsIngestResult:
        started = datetime.now(timezone.utc)
        result = StandingsIngestResult(started_at=started, finished_at=started)
        for i, sport_key in enumerate(sport_keys):
            if i and self._sleep_seconds:
                time.sleep(self._sleep_seconds)
            try:
                page = self._fetch_page(sport_key)
                if self._sleep_seconds:
                    time.sleep(self._sleep_seconds)
                rows = fs.parse_standings(self._fetch_standings(page.tournament_id, page.stage_id))
                if not rows:
                    raise RuntimeError("clasificación vacía")
                season = self._get_or_create_season(
                    sport_key, *season_years(page.year_start, page.year_end, started),
                )
                for row in rows:
                    self._upsert_row(row, season, sport_key, result)
                self.session.commit()
            except Exception as exc:  # noqa: BLE001
                self.session.rollback()
                msg = f"clasificación {sport_key}: {exc}"
                self.log.warning(msg)
                result.errors.append(msg)
                continue
            result.leagues_ok.append(sport_key)
            result.finished_matches[sport_key] = page.finished_matches
        result.teams_created = self._teams.created
        result.finished_at = datetime.now(timezone.utc)
        return result

    def _upsert_row(
        self,
        row: fs.FlashscoreStandingRow,
        season: Season,
        sport_key: str,
        result: StandingsIngestResult,
    ) -> None:
        team = self._teams.resolve(row.team_name, fs_id=row.team_fs_id, sport_key=sport_key)
        if team is None:
            return
        stats = self.session.scalar(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == team.id,
                TeamSeasonStats.season_id == season.id,
            )
        )
        if stats is None:
            stats = TeamSeasonStats(team_id=team.id, season_id=season.id)
            self.session.add(stats)
        stats.matches_played = row.matches_played
        stats.wins = row.wins
        stats.draws = row.draws
        stats.losses = row.losses
        stats.goals_for = row.goals_for
        stats.goals_against = row.goals_against
        stats.goal_diff = row.goals_for - row.goals_against
        stats.points = row.points
        stats.last_updated = datetime.now(timezone.utc)
        result.teams_upserted += 1

    def _get_or_create_season(self, sport_key: str, year_start: int, year_end: int) -> Season:
        league = self.session.scalar(select(League).where(League.sport_key == sport_key))
        if league is None:
            meta = LEAGUE_METADATA.get(sport_key, (sport_key, None))
            league = League(sport_key=sport_key, name=meta[0], country=meta[1])
            self.session.add(league)
            self.session.flush()
        season = self.session.scalar(
            select(Season).where(Season.league_id == league.id, Season.year_start == year_start)
        )
        if season is None:
            season = Season(
                league_id=league.id,
                name=f"{year_start}/{year_end}",
                year_start=year_start,
                year_end=year_end,
            )
            self.session.add(season)
            self.session.flush()
        return season
