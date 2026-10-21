"""Previa de un partido: todo lo que enseña su página, en una sola respuesta.

Por equipo: escudo y colores, medias de la temporada (en total y en su campo: el local en
casa, el visitante fuera) con goles, córners y tarjetas por mitad, forma de los últimos 5,
bajas, alineación (``match_lineups.py``) y jugadores clave con tiros a puerta por 90'.

La temporada empieza el 1 de julio. Cuentan todas las competiciones con stats.
Los cálculos (medias, forma, agregados de jugador) son funciones puras.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from backend.models import (
    Match,
    MatchAbsence,
    MatchStats,
    OddsHistory,
    Player,
    PlayerMatchStats,
    Team,
)
from backend.models.match import MATCH_STATUS_FINISHED
from backend.services.match_lineups import MatchLineupService, TeamLineup
from backend.services.player_photos import player_photo_url

FORM_MATCHES = 5
KEY_PLAYERS = 22


@dataclass(frozen=True)
class TeamSample:
    """Un partido jugado de un equipo: marcador + sus stats (``None`` si no las hay)."""

    is_home: bool
    goals_for: int
    goals_against: int
    xg: float | None = None
    shots: float | None = None
    shots_on_target: float | None = None
    corners: float | None = None
    yellow_cards: float | None = None
    goals_1h: float | None = None
    goals_2h: float | None = None
    corners_1h: float | None = None
    corners_2h: float | None = None
    cards_1h: float | None = None
    cards_2h: float | None = None


@dataclass
class TeamAverages:
    matches: int = 0
    goals_for: float | None = None
    goals_against: float | None = None
    xg: float | None = None
    shots: float | None = None
    shots_on_target: float | None = None
    corners: float | None = None
    yellow_cards: float | None = None
    goals_1h: float | None = None
    goals_2h: float | None = None
    corners_1h: float | None = None
    corners_2h: float | None = None
    cards_1h: float | None = None
    cards_2h: float | None = None


def average(samples: list[TeamSample]) -> TeamAverages:
    """Media de cada campo sobre los partidos que lo tienen (``None`` si ninguno)."""
    out = TeamAverages(matches=len(samples))
    for f in fields(TeamAverages):
        if f.name == "matches":
            continue
        values = [getattr(s, f.name) for s in samples if getattr(s, f.name) is not None]
        setattr(out, f.name, round(sum(values) / len(values), 2) if values else None)
    return out


def result_letter(goals_for: int, goals_against: int) -> str:
    """``W`` / ``D`` / ``L`` (el frontend lo enseña como G / E / P)."""
    if goals_for > goals_against:
        return "W"
    return "D" if goals_for == goals_against else "L"


@dataclass(frozen=True)
class FormMatch:
    match_id: int
    commence_time: datetime
    opponent: str
    is_home: bool
    goals_for: int
    goals_against: int
    result: str


@dataclass(frozen=True)
class PlayerRow:
    """Una aparición de un jugador (``player_match_stats``) reducida a lo que se agrega."""

    player_id: int
    name: str
    position: str | None
    is_starter: bool
    minutes: int | None
    goals: int | None
    shots: int | None
    shots_on_target: int | None
    yellow_cards: int | None
    photo_path: str | None = None


@dataclass
class KeyPlayer:
    player_id: int
    name: str
    position: str | None
    apps: int
    starts: int
    minutes: int
    goals: int
    shots: int
    shots_on_target: int
    yellow_cards: int
    shots_on_target_per90: float | None
    shots_per90: float | None
    photo_url: str | None = None


def aggregate_players(rows: list[PlayerRow], *, limit: int = KEY_PLAYERS) -> list[KeyPlayer]:
    """Totales por jugador, ordenados por minutos; por 90' solo con 90+ minutos jugados."""
    groups: dict[int, list[PlayerRow]] = defaultdict(list)
    for r in rows:
        groups[r.player_id].append(r)
    out: list[KeyPlayer] = []
    for pid, apps in groups.items():
        minutes = sum(r.minutes or 0 for r in apps)
        shots = sum(r.shots or 0 for r in apps)
        sot = sum(r.shots_on_target or 0 for r in apps)
        positions = Counter(r.position for r in apps if r.position)
        per90 = (lambda v: round(v * 90 / minutes, 2)) if minutes >= 90 else (lambda v: None)
        out.append(KeyPlayer(
            player_id=pid,
            name=apps[0].name,
            position=positions.most_common(1)[0][0] if positions else None,
            apps=len(apps),
            starts=sum(1 for r in apps if r.is_starter),
            minutes=minutes,
            goals=sum(r.goals or 0 for r in apps),
            shots=shots,
            shots_on_target=sot,
            yellow_cards=sum(r.yellow_cards or 0 for r in apps),
            shots_on_target_per90=per90(sot),
            shots_per90=per90(shots),
            photo_url=player_photo_url(next((r.photo_path for r in apps if r.photo_path), None)),
        ))
    out.sort(key=lambda p: (-p.minutes, p.name))
    return out[:limit]


def season_start(ref: datetime) -> datetime:
    """1 de julio de la temporada en curso de ``ref``."""
    year = ref.year if ref.month >= 7 else ref.year - 1
    return datetime(year, 7, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class AbsenceItem:
    name: str
    status: str  # missing | doubtful
    reason: str | None


@dataclass
class TeamPreview:
    team_id: int
    name: str
    logo_url: str | None
    primary_color: str | None
    secondary_color: str | None
    overall: TeamAverages
    venue: TeamAverages  # local: en casa; visitante: fuera
    form: list[FormMatch] = field(default_factory=list)
    absences: list[AbsenceItem] = field(default_factory=list)
    lineup: TeamLineup = field(default_factory=lambda: TeamLineup("none"))
    players: list[KeyPlayer] = field(default_factory=list)


@dataclass
class MatchPreview:
    match_id: int
    league_name: str
    league_sport_key: str
    commence_time: datetime
    status: str
    home_score: int | None
    away_score: int | None
    odds_updated_at: datetime | None
    home: TeamPreview
    away: TeamPreview


def _num(v) -> float | None:
    return float(v) if v is not None else None


class MatchPreviewService:
    def __init__(self, session: Session, *, lineups: MatchLineupService | None = None) -> None:
        self.session = session
        self._lineups = lineups or MatchLineupService(session)

    def build(self, match_id: int) -> MatchPreview | None:
        match = self.session.scalar(
            select(Match).where(Match.id == match_id).options(
                joinedload(Match.league), joinedload(Match.home_team), joinedload(Match.away_team),
            )
        )
        if match is None:
            return None
        home_lineup, away_lineup = self._lineups.for_match(match)
        home = self._team(match, match.home_team, is_home=True, lineup=home_lineup)
        away = self._team(match, match.away_team, is_home=False, lineup=away_lineup)
        odds_updated_at = self.session.scalar(
            select(func.max(OddsHistory.last_seen_at)).where(OddsHistory.match_id == match.id)
        )
        return MatchPreview(
            match_id=match.id,
            league_name=match.league.name,
            league_sport_key=match.league.sport_key,
            commence_time=match.commence_time,
            status=match.status,
            home_score=match.home_score,
            away_score=match.away_score,
            odds_updated_at=odds_updated_at,
            home=home,
            away=away,
        )

    def _team(self, match: Match, team: Team, *, is_home: bool, lineup: TeamLineup) -> TeamPreview:
        since = season_start(match.commence_time)
        rows = self.session.execute(
            select(Match, MatchStats)
            .outerjoin(
                MatchStats,
                (MatchStats.match_id == Match.id) & (MatchStats.team_id == team.id),
            )
            .where(or_(Match.home_team_id == team.id, Match.away_team_id == team.id))
            .where(Match.status == MATCH_STATUS_FINISHED)
            .where(Match.home_score.is_not(None))
            .where(Match.commence_time >= since)
            .where(Match.commence_time < match.commence_time)
            .options(joinedload(Match.home_team), joinedload(Match.away_team))
            .order_by(Match.commence_time.desc())
        ).all()

        samples: list[TeamSample] = []
        form: list[FormMatch] = []
        for m, st in rows:
            at_home = m.home_team_id == team.id
            gf = m.home_score if at_home else m.away_score
            ga = m.away_score if at_home else m.home_score
            samples.append(TeamSample(
                is_home=at_home, goals_for=gf, goals_against=ga,
                xg=_num(st.xg) if st else None,
                shots=_num(st.shots_total) if st else None,
                shots_on_target=_num(st.shots_on_target) if st else None,
                corners=_num(st.corners) if st else None,
                yellow_cards=_num(st.yellow_cards) if st else None,
                goals_1h=_num(st.goals_1h) if st else None,
                goals_2h=_num(st.goals_2h) if st else None,
                corners_1h=_num(st.corners_1h) if st else None,
                corners_2h=_num(st.corners_2h) if st else None,
                cards_1h=_num(st.yellow_cards_1h) if st else None,
                cards_2h=_num(st.yellow_cards_2h) if st else None,
            ))
            if len(form) < FORM_MATCHES:
                rival = m.away_team if at_home else m.home_team
                form.append(FormMatch(
                    match_id=m.id, commence_time=m.commence_time, opponent=rival.name,
                    is_home=at_home, goals_for=gf, goals_against=ga,
                    result=result_letter(gf, ga),
                ))

        absences = [
            AbsenceItem(a.player_name, a.status, a.reason)
            for a in self.session.scalars(
                select(MatchAbsence)
                .where(MatchAbsence.match_id == match.id)
                .where(MatchAbsence.team_id == team.id)
                .order_by(MatchAbsence.status.desc(), MatchAbsence.player_name)
            ).all()
        ]

        player_rows = [
            PlayerRow(
                player_id=pms.player_id, name=name, position=pms.position_in_match,
                is_starter=bool(pms.is_starter), minutes=pms.minutes_played, goals=pms.goals,
                shots=pms.shots, shots_on_target=pms.shots_on_target,
                yellow_cards=pms.yellow_cards, photo_path=photo,
            )
            for pms, name, photo in self.session.execute(
                select(PlayerMatchStats, Player.name, Player.photo_path)
                .join(Player, Player.id == PlayerMatchStats.player_id)
                .join(Match, Match.id == PlayerMatchStats.match_id)
                .where(PlayerMatchStats.team_id == team.id)
                .where(Match.commence_time >= since)
                .where(Match.commence_time < match.commence_time)
            ).all()
        ]

        return TeamPreview(
            team_id=team.id,
            name=team.name,
            logo_url=team.logo_url,
            primary_color=team.primary_color,
            secondary_color=team.secondary_color,
            overall=average(samples),
            venue=average([s for s in samples if s.is_home == is_home]),
            form=form,
            absences=absences,
            lineup=lineup,
            players=aggregate_players(player_rows),
        )


__all__ = [
    "AbsenceItem",
    "FormMatch",
    "KeyPlayer",
    "MatchPreview",
    "MatchPreviewService",
    "PlayerRow",
    "TeamAverages",
    "TeamPreview",
    "TeamSample",
    "aggregate_players",
    "average",
    "result_letter",
    "season_start",
]
