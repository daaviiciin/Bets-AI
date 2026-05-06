"""Actualiza el resultado de los partidos en BD a partir de Sofascore.

Para cada liga:

1. Descarga los partidos finalizados de la temporada actual de Sofascore
   (``fetch_finished_events``).
2. Mapea cada evento al :class:`Match` correspondiente en la BD usando:

   * **Vía rápida**: ``Team.external_id`` con ``external_source='sofascore'``.
   * **Fallback**: matching difuso por nombre normalizado del equipo +
     ventana de tiempo (±36 h) alrededor de ``commence_time``.

3. Si el ``Match`` ya tenía resultado y coincide, no hace nada (idempotente).
   Si difería o no tenía, lo actualiza con ``home_score``, ``away_score``,
   ``status='finished'`` y ``played_at`` (timestamp del kickoff real).

El servicio NO crea matches nuevos: solo actualiza los que ya existen en BD
(provenientes del Odds API). Si quieres backfill puro de partidos pasados,
ese sería un job aparte.
"""

from __future__ import annotations

import logging
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import (
    League,
    Match,
    Team,
)
from backend.models.match import (
    MATCH_STATUS_CANCELED,
    MATCH_STATUS_FINISHED,
    MATCH_STATUS_POSTPONED,
)
from backend.scrapers.sofascore import TOURNAMENTS, fetch_finished_events, fetch_seasons


# Toleramos hasta 36h de desfase entre el kickoff de Sofascore y el de Odds
# API para emparejar (cubre cambios de horario y partidos aplazados un día).
NAME_MATCH_WINDOW_HOURS = 36


@dataclass
class LeagueUpdateResult:
    """Resumen del update de una sola liga."""

    league_name: str
    sport_key: str
    events_fetched: int = 0
    matches_updated: int = 0
    matches_already_ok: int = 0
    matches_not_found: int = 0
    skipped_unmapped: int = 0  # equipos Sofascore sin alias local

    def merge(self, other: "LeagueUpdateResult") -> None:
        self.events_fetched += other.events_fetched
        self.matches_updated += other.matches_updated
        self.matches_already_ok += other.matches_already_ok
        self.matches_not_found += other.matches_not_found
        self.skipped_unmapped += other.skipped_unmapped


@dataclass
class UpdateSummary:
    """Resumen agregado tras procesar varias ligas."""

    started_at: datetime
    finished_at: datetime
    per_league: list[LeagueUpdateResult] = field(default_factory=list)

    @property
    def total_updated(self) -> int:
        return sum(r.matches_updated for r in self.per_league)

    @property
    def total_already_ok(self) -> int:
        return sum(r.matches_already_ok for r in self.per_league)

    @property
    def total_not_found(self) -> int:
        return sum(r.matches_not_found for r in self.per_league)


# ---------------------------------------------------------------------------
# Servicio
# ---------------------------------------------------------------------------


class MatchResultsUpdater:
    """Actualiza ``Match.home_score`` / ``away_score`` / ``status`` desde Sofascore."""

    def __init__(self, session: Session, *, log: logging.Logger | None = None) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)

    # -- API pública --------------------------------------------------------

    def update_all(
        self,
        *,
        sport_keys: list[str] | None = None,
        max_pages: int = 5,
    ) -> UpdateSummary:
        """Actualiza resultados para varias ligas (default: todas las soportadas)."""
        targets = sport_keys or list(TOURNAMENTS.keys())
        started = datetime.now(timezone.utc)
        per_league: list[LeagueUpdateResult] = []
        for sk in targets:
            cfg = TOURNAMENTS.get(sk)
            if cfg is None:
                self.log.warning("sport_key=%s no soportado en TOURNAMENTS", sk)
                continue
            league_name, tournament_id = cfg
            result = self.update_for_league(
                sport_key=sk,
                tournament_id=tournament_id,
                league_name=league_name,
                max_pages=max_pages,
            )
            per_league.append(result)
        return UpdateSummary(
            started_at=started,
            finished_at=datetime.now(timezone.utc),
            per_league=per_league,
        )

    def update_for_league(
        self,
        *,
        sport_key: str,
        tournament_id: int,
        league_name: str,
        max_pages: int = 5,
    ) -> LeagueUpdateResult:
        """Actualiza resultados para una liga concreta."""
        result = LeagueUpdateResult(league_name=league_name, sport_key=sport_key)

        league = self.session.scalar(select(League).where(League.sport_key == sport_key))
        if league is None:
            self.log.warning("[%s] liga no existe en BD", sport_key)
            return result

        # Descargar eventos de la temporada actual.
        sf_seasons = fetch_seasons(tournament_id)
        if not sf_seasons:
            self.log.warning("[%s] sin temporadas en Sofascore", league_name)
            return result
        sofa_season_id = sf_seasons[0]["id"]
        events = fetch_finished_events(tournament_id, sofa_season_id, max_pages=max_pages)
        result.events_fetched = len(events)
        self.log.info("[%s] %d eventos descargados", league_name, len(events))

        # Caches locales.
        team_by_sofa_id = self._load_team_index_by_sofa_id()
        league_matches = self._load_matches_for_league(league.id)
        matches_by_pair = _index_matches_by_team_pair(league_matches)
        names_normalized = {t.id: _normalize(t.name) for t in self._load_all_teams()}

        for event in events:
            event_status = self._event_status(event)
            if event_status is None:
                # No final aún (live, postponed, canceled): no tocamos.
                continue

            scores = _scores(event)
            if scores is None:
                continue
            home_score, away_score = scores

            commence = _start_datetime(event)
            if commence is None:
                continue

            home_sofa = _team_ext_id(event, "home")
            away_sofa = _team_ext_id(event, "away")

            home_team = team_by_sofa_id.get(home_sofa) if home_sofa else None
            away_team = team_by_sofa_id.get(away_sofa) if away_sofa else None

            match: Match | None = None

            if home_team is not None and away_team is not None:
                match = _find_match_by_pair(
                    matches_by_pair, home_team.id, away_team.id, commence,
                )

            # Fallback: matching por nombre normalizado.
            if match is None:
                match = self._find_match_by_name(
                    league_matches=league_matches,
                    names_normalized=names_normalized,
                    home_event_name=_event_team_name(event, "home"),
                    away_event_name=_event_team_name(event, "away"),
                    commence=commence,
                )

            if match is None:
                if home_team is None or away_team is None:
                    result.skipped_unmapped += 1
                else:
                    result.matches_not_found += 1
                continue

            # Trazar el sofascore_event_id en el Match aunque ya tenga
            # resultado: nos hace falta para descargar lineups, stats por
            # jugador, etc. independientemente de si el resultado ya estaba.
            sofa_event_id = event.get("id")
            sofa_event_id_str = str(sofa_event_id) if sofa_event_id is not None else None
            if sofa_event_id_str and match.sofascore_event_id != sofa_event_id_str:
                match.sofascore_event_id = sofa_event_id_str

            if (
                match.status == event_status
                and match.home_score == home_score
                and match.away_score == away_score
            ):
                result.matches_already_ok += 1
                continue

            match.status = event_status
            match.home_score = home_score
            match.away_score = away_score
            match.played_at = commence
            result.matches_updated += 1

        self.session.flush()
        return result

    # -- Helpers internos ---------------------------------------------------

    def _load_team_index_by_sofa_id(self) -> dict[str, Team]:
        teams = self.session.scalars(
            select(Team).where(
                Team.external_source == "sofascore", Team.external_id.is_not(None),
            )
        ).all()
        return {t.external_id: t for t in teams if t.external_id}

    def _load_all_teams(self) -> list[Team]:
        return list(self.session.scalars(select(Team)).all())

    def _load_matches_for_league(self, league_id: int) -> list[Match]:
        return list(self.session.scalars(
            select(Match).where(Match.league_id == league_id)
        ).all())

    def _event_status(self, event: dict) -> str | None:
        """Devuelve el status local equivalente, o ``None`` si aún no terminó."""
        status = (event.get("status") or {})
        stype = status.get("type")
        if stype == "finished":
            return MATCH_STATUS_FINISHED
        if stype == "canceled":
            return MATCH_STATUS_CANCELED
        if stype == "postponed":
            return MATCH_STATUS_POSTPONED
        return None  # inprogress / notstarted / interrupted ...

    def _find_match_by_name(
        self,
        *,
        league_matches: list[Match],
        names_normalized: dict[int, str],
        home_event_name: str | None,
        away_event_name: str | None,
        commence: datetime,
    ) -> Match | None:
        if not home_event_name or not away_event_name:
            return None
        target_home = _normalize(home_event_name)
        target_away = _normalize(away_event_name)
        window = timedelta(hours=NAME_MATCH_WINDOW_HOURS)

        for m in league_matches:
            n_home = names_normalized.get(m.home_team_id, "")
            n_away = names_normalized.get(m.away_team_id, "")
            if not _names_match(n_home, target_home):
                continue
            if not _names_match(n_away, target_away):
                continue
            if abs(m.commence_time - commence) > window:
                continue
            return m
        return None


# ---------------------------------------------------------------------------
# Helpers puros (testables sin BD)
# ---------------------------------------------------------------------------


def _index_matches_by_team_pair(matches: Iterable[Match]) -> dict[tuple[int, int], list[Match]]:
    out: dict[tuple[int, int], list[Match]] = defaultdict(list)
    for m in matches:
        out[(m.home_team_id, m.away_team_id)].append(m)
    return out


def _find_match_by_pair(
    index: dict[tuple[int, int], list[Match]],
    home_id: int,
    away_id: int,
    commence: datetime,
) -> Match | None:
    candidates = index.get((home_id, away_id), [])
    if not candidates:
        return None
    window = timedelta(hours=NAME_MATCH_WINDOW_HOURS)
    best: Match | None = None
    best_delta: timedelta | None = None
    for m in candidates:
        delta = abs(m.commence_time - commence)
        if delta > window:
            continue
        if best is None or (best_delta is not None and delta < best_delta):
            best = m
            best_delta = delta
    return best


def _scores(event: dict) -> tuple[int, int] | None:
    home = (event.get("homeScore") or {}).get("current")
    away = (event.get("awayScore") or {}).get("current")
    if home is None or away is None:
        return None
    try:
        return int(home), int(away)
    except (TypeError, ValueError):
        return None


def _start_datetime(event: dict) -> datetime | None:
    ts = event.get("startTimestamp")
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _team_ext_id(event: dict, side: str) -> str | None:
    team = event.get(f"{side}Team") or {}
    tid = team.get("id")
    return str(tid) if tid is not None else None


def _event_team_name(event: dict, side: str) -> str | None:
    team = event.get(f"{side}Team") or {}
    return team.get("name") or team.get("shortName")


_NOISE_TOKENS = {
    "fc", "cf", "ac", "sc", "afc", "rcd", "ud", "cd", "cp",
    "club", "de", "calcio", "bv", "ssd", "asd",
}


def _normalize(name: str) -> str:
    """Normaliza un nombre de equipo para matching aproximado.

    * NFKD + remoción de diacríticos (Atlético → atletico).
    * Lowercase.
    * Tokeniza y descarta afijos comunes (``"FC"``, ``"AC"``, ``"CF"``...).
    * Compacta espacios.
    """
    if not name:
        return ""
    txt = unicodedata.normalize("NFKD", name)
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    txt = txt.lower().strip()
    tokens = [t for t in txt.split() if t and t not in _NOISE_TOKENS]
    return " ".join(tokens)


def _names_match(local: str, target: str) -> bool:
    """Igual o sub-cadena en ambas direcciones (cubre Real Madrid CF / Real Madrid)."""
    if not local or not target:
        return False
    if local == target:
        return True
    return local in target or target in local
