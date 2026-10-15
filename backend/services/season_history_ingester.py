"""Histórico de partidos jugados de la temporada desde Flashscore.

La página de clasificación de cada liga (que ya descarga
:class:`FlashscoreStandingsIngester`) publica todos los partidos jugados de la
temporada con id, equipos (``PX``/``PY``) y marcador. Con eso se crean las
filas ``Match`` en estado ``finished`` **sin peticiones extra**.

Las stats post-partido y por jugador de esos partidos las descargan después
los pasos 4 y 5 del refresh (con límite por ejecución) o el CLI
``backend.tools.backfill_flashscore_season`` de una vez.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import League, Match
from backend.models.match import MATCH_STATUS_FINISHED
from backend.scrapers import flashscore as fs
from backend.services.entity_resolver import TeamResolver
from backend.services.flashscore_fixtures_ingester import flashscore_external_id


@dataclass
class SeasonHistoryResult:
    matches_created: int = 0
    matches_finished: int = 0  # ya existían como programados y se cierran con resultado
    matches_already_ok: int = 0
    teams_created: int = 0
    errors: list[str] = field(default_factory=list)


def storable_finished_matches(fixtures: list[fs.FlashscoreMatch]) -> list[fs.FlashscoreMatch]:
    """Partidos con todo lo necesario: terminado, fecha, marcador e ids de equipo."""
    return [
        f for f in fixtures
        if f.is_finished
        and f.scheduled_at is not None
        and f.score_home is not None
        and f.score_away is not None
        and f.home_team_fs_id
        and f.away_team_fs_id
        and f.home_team_name
        and f.away_team_name
    ]


class SeasonHistoryIngester:
    def __init__(self, session: Session, *, log: logging.Logger | None = None) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._teams = TeamResolver(session, log=self.log)

    def ingest(self, finished_by_league: dict[str, list[fs.FlashscoreMatch]]) -> SeasonHistoryResult:
        result = SeasonHistoryResult()
        for sport_key, fixtures in finished_by_league.items():
            league = self.session.scalar(select(League).where(League.sport_key == sport_key))
            if league is None:
                result.errors.append(f"liga {sport_key} no existe en BD")
                continue
            for fm in storable_finished_matches(fixtures):
                self._upsert(fm, league, sport_key, result)
            self.session.commit()
        result.teams_created = self._teams.created
        return result

    def _upsert(
        self, fm: fs.FlashscoreMatch, league: League, sport_key: str, result: SeasonHistoryResult,
    ) -> None:
        ext = flashscore_external_id(fm.match_id)
        match = self.session.scalar(select(Match).where(Match.external_id == ext))
        if match is not None:
            if match.status == MATCH_STATUS_FINISHED and match.home_score is not None:
                result.matches_already_ok += 1
                return
            match.status = MATCH_STATUS_FINISHED
            match.home_score = fm.score_home
            match.away_score = fm.score_away
            match.played_at = fm.scheduled_at
            result.matches_finished += 1
            return
        home = self._teams.resolve(fm.home_team_name, fs_id=fm.home_team_fs_id, sport_key=sport_key)
        away = self._teams.resolve(fm.away_team_name, fs_id=fm.away_team_fs_id, sport_key=sport_key)
        if home is None or away is None:
            return
        self.session.add(Match(
            external_id=ext,
            league_id=league.id,
            home_team_id=home.id,
            away_team_id=away.id,
            commence_time=fm.scheduled_at,
            status=MATCH_STATUS_FINISHED,
            home_score=fm.score_home,
            away_score=fm.score_away,
            played_at=fm.scheduled_at,
        ))
        result.matches_created += 1
