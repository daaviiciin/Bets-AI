"""Resolución de equipos y jugadores de Flashscore contra la BD.

Orden de búsqueda (de más a menos fiable):

1. ``flashscore_id`` ya guardado.
2. Nombre exacto.
3. Emparejado aproximado (equipos: :mod:`team_name_matching`; jugadores: mismas
   palabras en cualquier orden) contra candidatos **únicos**.
4. Crear uno nuevo.

Cuando se empareja por nombre se guarda el ``flashscore_id`` para que la próxima
vez el paso 1 baste. Nunca se sobrescribe un ``flashscore_id`` distinto: si el
candidato ya tiene otro, se considera otro equipo/jugador.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import League, Match, Player, Season, Team, TeamSeasonStats
from backend.services.team_name_matching import (
    find_matching_team_id,
    player_name_key,
    player_names_match,
)

FLASHSCORE_SOURCE = "flashscore"
UEFA_SPORT_KEY_PREFIX = "soccer_uefa"


class TeamResolver:
    def __init__(self, session: Session, *, log: logging.Logger | None = None) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._candidates_by_league: dict[str, dict[int, str]] = {}
        self.created = 0

    def resolve(self, name: str | None, *, fs_id: str | None, sport_key: str) -> Team | None:
        if not name or not name.strip():
            return None
        clean = name.strip()
        if fs_id:
            team = self.session.scalar(select(Team).where(Team.flashscore_id == fs_id))
            if team is not None:
                return team
        team = self.session.scalar(select(Team).where(Team.name == clean))
        if team is None:
            team_id = find_matching_team_id(clean, self._candidates(sport_key))
            team = self.session.get(Team, team_id) if team_id is not None else None
        if team is not None:
            if fs_id and team.flashscore_id is None:
                team.flashscore_id = fs_id
                return team
            if not fs_id or team.flashscore_id == fs_id:
                return team
            # El candidato ya está ligado a otro id de Flashscore: es otro club.
        team = Team(name=self._free_name(clean), flashscore_id=fs_id)
        self.session.add(team)
        self.session.flush()
        self._candidates_by_league.clear()
        self.created += 1
        self.log.info("Equipo nuevo: %s [%s]", team.name, sport_key)
        return team

    def _candidates(self, sport_key: str) -> dict[int, str]:
        """Equipos ya ligados a la liga: con stats de temporada o con partidos en ella.

        En competiciones UEFA juegan clubes de todas las ligas, así que se usan los
        equipos con stats de cualquier liga.
        """
        cached = self._candidates_by_league.get(sport_key)
        if cached is not None:
            return cached
        with_stats = (
            select(Team.id, Team.name)
            .join(TeamSeasonStats, TeamSeasonStats.team_id == Team.id)
            .join(Season, Season.id == TeamSeasonStats.season_id)
            .join(League, League.id == Season.league_id)
        )
        if not sport_key.startswith(UEFA_SPORT_KEY_PREFIX):
            with_stats = with_stats.where(League.sport_key == sport_key)
        in_matches = (
            select(Team.id, Team.name)
            .join(Match, (Match.home_team_id == Team.id) | (Match.away_team_id == Team.id))
            .join(League, League.id == Match.league_id)
            .where(League.sport_key == sport_key)
        )
        candidates = {
            tid: tname
            for stmt in (with_stats, in_matches)
            for tid, tname in self.session.execute(stmt.distinct()).all()
        }
        self._candidates_by_league[sport_key] = candidates
        return candidates

    def _free_name(self, name: str) -> str:
        """``teams.name`` es único: si ya existe (otro club homónimo), añade sufijo."""
        if self.session.scalar(select(Team.id).where(Team.name == name)) is None:
            return name
        n = 2
        while self.session.scalar(select(Team.id).where(Team.name == f"{name} ({n})")):
            n += 1
        return f"{name} ({n})"


def find_unique_team_player(
    session: Session, name: str | None, team_ids: list[int],
) -> Player | None:
    """Jugador de ``team_ids`` cuyo nombre encaja (:func:`player_names_match`), si es único.

    Se compara contra **todos** los jugadores de esos equipos: si "Gabriel" encaja con dos,
    no se elige ninguno.
    """
    ids = [t for t in team_ids if t]
    if not name or not ids:
        return None
    players = session.scalars(select(Player).where(Player.team_id.in_(ids))).all()
    exact = [p for p in players if p.name == name]
    if len(exact) == 1:
        return exact[0]
    matches = [p for p in players if player_names_match(name, p.name)]
    return matches[0] if len(matches) == 1 else None


class PlayerResolver:
    def __init__(self, session: Session, *, log: logging.Logger | None = None) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._by_name_key: dict[frozenset[str], list[int]] | None = None

    def resolve(self, name: str, *, fs_id: str, team_id: int | None) -> tuple[Player, bool]:
        """Devuelve ``(jugador, creado)``."""
        player = self.session.scalar(select(Player).where(Player.flashscore_id == fs_id))
        if player is not None:
            if team_id and player.team_id != team_id:
                player.team_id = team_id
            return player, False
        matched = self._unique_name_match(name, team_id)
        if matched is not None:
            matched.flashscore_id = fs_id
            if team_id and matched.team_id is None:
                matched.team_id = team_id
            return matched, False
        player = Player(
            name=name[:128],
            external_source=FLASHSCORE_SOURCE,
            external_id=fs_id,
            flashscore_id=fs_id,
            team_id=team_id,
        )
        self.session.add(player)
        self.session.flush()
        return player, True

    def _unique_name_match(self, name: str, team_id: int | None) -> Player | None:
        if team_id is not None:
            # Dentro del equipo, regla flexible ("Paz Nicolás" ~ "Nico Paz") con candidato único.
            found = find_unique_team_player(self.session, name, [team_id])
            if found is not None:
                return found if found.flashscore_id is None else None
        key = player_name_key(name)
        if not key:
            return None
        if self._by_name_key is None:
            index: dict[frozenset[str], list[int]] = {}
            rows = self.session.execute(
                select(Player.id, Player.name).where(Player.flashscore_id.is_(None))
            ).all()
            for pid, pname in rows:
                index.setdefault(player_name_key(pname), []).append(pid)
            self._by_name_key = index
        ids = self._by_name_key.get(key, [])
        candidates = [self.session.get(Player, pid) for pid in ids]
        candidates = [p for p in candidates if p is not None and p.flashscore_id is None]
        if team_id is not None:
            same_team = [p for p in candidates if p.team_id == team_id]
            if len(same_team) == 1:
                return same_team[0]
            candidates = [p for p in candidates if p.team_id is None]
        return candidates[0] if len(candidates) == 1 else None
