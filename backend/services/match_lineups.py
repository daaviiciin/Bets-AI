"""Alineaciones de un partido para su página: oficial, jugada o probable.

Prioridad, de más a menos fiable:

1. **Jugada**: el partido ya tiene ``player_match_stats`` (se jugó o está en juego).
2. **Oficial**: Flashscore la publica ~1 h antes. Si el partido empieza en menos de
   :data:`OFFICIAL_WINDOW`, se pide bajo demanda (``epmsse``) y se guarda en
   ``match_lineups``; no se repite antes de :data:`RETRY_AFTER`.
3. **Probable**: los titulares del último partido del equipo, marcando quién está de baja
   o en duda para este. No se inventa el sustituto: se dice cuántos faltan.

Las decisiones (cuándo pedir, cómo marcar bajas, orden por líneas) son funciones puras.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from backend.models import Match, MatchAbsence, MatchLineup, Player, PlayerMatchStats
from backend.scrapers import flashscore as fs
from backend.services.flashscore_player_stats_ingester import position_code
from backend.services.match_absences_ingester import flashscore_id_from_external_id
from backend.services.player_photos import player_photo_url

OFFICIAL_WINDOW = timedelta(minutes=75)
# Tras el inicio sigue mereciendo la pena (en juego) hasta que lleguen las stats.
OFFICIAL_AFTER_KICKOFF = timedelta(hours=3)
RETRY_AFTER = timedelta(minutes=10)
_POSITION_ORDER = {"G": 0, "D": 1, "M": 2, "F": 3}

LINEUP_OFFICIAL = "official"
LINEUP_PLAYED = "played"
LINEUP_PROBABLE = "probable"
LINEUP_NONE = "none"

# Último intento de pedir la oficial por partido (en memoria: basta para no repetir la
# petición en cada recarga de la página).
_last_attempt: dict[int, datetime] = {}


@dataclass(frozen=True)
class LineupPlayer:
    name: str
    position: str | None  # G/D/M/F
    # None = disponible; "missing" = baja; "doubtful" = duda (solo en el once probable).
    status: str | None = None
    # Foto (CDN de Flashscore); ``None`` si aún no se conoce: el frontend pinta iniciales.
    photo_url: str | None = None


@dataclass
class TeamLineup:
    kind: str  # official | played | probable | none
    starters: list[LineupPlayer] = field(default_factory=list)
    # Probable: de qué partido sale ("vs Sevilla · 12/10").
    based_on: str | None = None


def should_fetch_official(
    kickoff: datetime, now: datetime, last_attempt: datetime | None,
) -> bool:
    """¿Toca pedir la alineación oficial a Flashscore?"""
    if not (-OFFICIAL_AFTER_KICKOFF <= kickoff - now <= OFFICIAL_WINDOW):
        return False
    return last_attempt is None or now - last_attempt >= RETRY_AFTER


# Forma de referencia para colocar a quien no trae posición (un 4-3-3).
_TARGET_LINES = {"D": 4, "M": 3, "F": 3}


def infer_positions(players: list[LineupPlayer]) -> list[LineupPlayer]:
    """Da línea a los titulares sin posición, para que la formación no se deforme.

    Sin portero, el primero sin posición lo es. El resto va, uno a uno, a la línea a la que
    más le falta para un 4-3-3 (en empate, la más adelantada). Así un once con 3 defensas,
    4 medios, 1 delantero y 2 desconocidos queda 3-4-3 y no 3-6-1.
    """
    known = [p for p in players if p.position in _POSITION_ORDER]
    unknown = [p for p in players if p.position not in _POSITION_ORDER]
    if not unknown:
        return players
    counts = {line: sum(1 for p in known if p.position == line) for line in ("G", "D", "M", "F")}
    out = list(known)
    for p in unknown:
        if counts["G"] == 0:
            line = "G"
        else:
            line = max(("F", "M", "D"), key=lambda ln: _TARGET_LINES[ln] - counts[ln])
        counts[line] += 1
        out.append(replace(p, position=line))
    return out


def by_line(players: list[LineupPlayer]) -> list[LineupPlayer]:
    """Portero, defensas, medios, delanteros (posiciones desconocidas deducidas)."""
    return sorted(infer_positions(players), key=lambda p: _POSITION_ORDER.get(p.position or "", 4))


def _norm(name: str) -> str:
    txt = unicodedata.normalize("NFKD", name or "")
    return " ".join("".join(c for c in txt if not unicodedata.combining(c)).lower().split())


def same_player(absence_name: str, player_name: str) -> bool:
    """¿Es el mismo jugador? Las bajas vienen como "Soriano M." (apellido + inicial).

    Iguales en cualquier orden de palabras ("Lewandowski Robert" = "Robert Lewandowski"),
    o apellido presente en el nombre + inicial que coincide con otra de sus palabras.
    """
    a, p = _norm(absence_name).replace(".", " ").split(), _norm(player_name).split()
    if not a or not p:
        return False
    if set(a) == set(p):
        return True
    full = [w for w in a if len(w) > 1]
    initials = [w for w in a if len(w) == 1]
    if not full or not all(w in p for w in full):
        return False
    rest = [w for w in p if w not in full]
    return all(any(w.startswith(i) for w in rest) for i in initials)


def mark_absences(
    starters: list[tuple[str, str | None, str | None]],
    absences: list[tuple[str, str, str]],
) -> list[LineupPlayer]:
    """Once probable: ``(nombre, posición, flashscore_id)`` + bajas ``(id, nombre, estado)``.

    Cruza por id de Flashscore y, si no lo hay, por nombre (:func:`same_player`).
    """
    by_id = {fid: status for fid, _, status in absences if fid}
    out: list[LineupPlayer] = []
    for name, position, fid in starters:
        status = by_id.get(fid) if fid else None
        if status is None:
            status = next((st for _, an, st in absences if same_player(an, name)), None)
        out.append(LineupPlayer(name=name, position=position, status=status))
    return by_line(out)


class MatchLineupService:
    """Monta la alineación de cada equipo para la página del partido."""

    def __init__(
        self,
        session: Session,
        *,
        log: logging.Logger | None = None,
        fetch_players=None,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        self._fetch_players = fetch_players or _fetch_lineup_payload
        self._now = now

    def for_match(self, match: Match) -> tuple[TeamLineup, TeamLineup]:
        played = self._played(match)
        if played is not None:
            return played
        official = self._official(match)
        if official is not None:
            return official
        return (
            self._probable(match, match.home_team_id),
            self._probable(match, match.away_team_id),
        )

    # ------------------------------------------------------------- fuentes

    def _played(self, match: Match) -> tuple[TeamLineup, TeamLineup] | None:
        rows = list(self.session.scalars(
            select(PlayerMatchStats)
            .where(PlayerMatchStats.match_id == match.id)
            .where(PlayerMatchStats.is_starter.is_(True))
            .options(joinedload(PlayerMatchStats.player))
        ).all())
        if not rows:
            return None
        sides = []
        for is_home in (True, False):
            players = [
                LineupPlayer(
                    r.player.name, r.position_in_match,
                    photo_url=player_photo_url(r.player.photo_path),
                )
                for r in rows if r.is_home == is_home
            ]
            sides.append(TeamLineup(LINEUP_PLAYED, by_line(players)))
        return sides[0], sides[1]

    def _official(self, match: Match) -> tuple[TeamLineup, TeamLineup] | None:
        stored = self._stored_official(match.id)
        if stored is not None:
            return stored
        now = self._now or datetime.now(timezone.utc)
        if not should_fetch_official(match.commence_time, now, _last_attempt.get(match.id)):
            return None
        fs_id = flashscore_id_from_external_id(match.external_id)
        if not fs_id:
            return None
        _last_attempt[match.id] = now
        try:
            players = fs.parse_player_match_stats(self._fetch_players(fs_id), {})
        except Exception as exc:  # noqa: BLE001
            self.log.warning("Alineación oficial de %s no disponible: %s", match.id, exc)
            return None
        starters = [p for p in players if p.is_starter]
        if len(starters) < 20:  # todavía no publicada (o incompleta)
            return None
        self._store_photos(players)
        self.session.execute(delete(MatchLineup).where(MatchLineup.match_id == match.id))
        for p in starters:
            self.session.add(MatchLineup(
                match_id=match.id,
                is_home=p.side == "home",
                player_external_id=p.player_fs_id,
                player_name=p.player_name,
                position=position_code(p.position, is_goalkeeper=p.is_goalkeeper),
                is_starter=True,
                captured_at=now,
            ))
        self.session.commit()
        return self._stored_official(match.id)

    def _store_photos(self, players: list[fs.FlashscorePlayerMatchStats]) -> None:
        """Guarda la foto de los jugadores de la plantilla que ya están en ``players``."""
        photos = {p.player_fs_id: p.photo_path for p in players if p.photo_path}
        if not photos:
            return
        for player in self.session.scalars(
            select(Player).where(Player.flashscore_id.in_(list(photos)))
        ).all():
            player.photo_path = photos[player.flashscore_id]

    def _stored_official(self, match_id: int) -> tuple[TeamLineup, TeamLineup] | None:
        rows = self.session.execute(
            select(MatchLineup, Player.photo_path)
            .outerjoin(Player, Player.flashscore_id == MatchLineup.player_external_id)
            .where(MatchLineup.match_id == match_id)
        ).all()
        if not rows:
            return None
        return tuple(  # type: ignore[return-value]
            TeamLineup(LINEUP_OFFICIAL, by_line([
                LineupPlayer(r.player_name, r.position, photo_url=player_photo_url(photo))
                for r, photo in rows if r.is_home == is_home
            ]))
            for is_home in (True, False)
        )

    def _probable(self, match: Match, team_id: int) -> TeamLineup:
        last = self.session.scalar(
            select(Match)
            .join(PlayerMatchStats, PlayerMatchStats.match_id == Match.id)
            .where(PlayerMatchStats.team_id == team_id)
            .where(PlayerMatchStats.is_starter.is_(True))
            .where(Match.commence_time < match.commence_time)
            .order_by(Match.commence_time.desc())
            .options(joinedload(Match.home_team), joinedload(Match.away_team))
            .limit(1)
        )
        if last is None:
            return TeamLineup(LINEUP_NONE)
        rows = list(self.session.scalars(
            select(PlayerMatchStats)
            .where(PlayerMatchStats.match_id == last.id)
            .where(PlayerMatchStats.team_id == team_id)
            .where(PlayerMatchStats.is_starter.is_(True))
            .options(joinedload(PlayerMatchStats.player))
        ).all())
        absences = list(self.session.scalars(
            select(MatchAbsence)
            .where(MatchAbsence.match_id == match.id)
            .where(MatchAbsence.team_id == team_id)
        ).all())
        starters = mark_absences(
            [(r.player.name, r.position_in_match, r.player.flashscore_id) for r in rows],
            [(a.player_external_id, a.player_name, a.status) for a in absences],
        )
        photos = {r.player.name: player_photo_url(r.player.photo_path) for r in rows}
        starters = [replace(p, photo_url=photos.get(p.name)) for p in starters]
        rival = last.away_team if last.home_team_id == team_id else last.home_team
        return TeamLineup(
            LINEUP_PROBABLE, starters,
            based_on=f"vs {rival.name} · {last.commence_time:%d/%m}",
        )


def _fetch_lineup_payload(match_id: str) -> dict:
    """Solo la plantilla del partido (``epmsse``): titulares en cuanto se publican."""
    from curl_cffi import requests as cf

    r = cf.get(
        fs.MISSING_PLAYERS_URL,
        params={"_hash": fs.PLAYER_STATS_PLAYERS_HASH, "eventId": match_id,
                "projectId": fs.FLASHSCORE_PROJECT_ID},
        impersonate="chrome", timeout=20,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} en epmsse {match_id}")
    return r.json()


__all__ = [
    "LINEUP_NONE",
    "LINEUP_OFFICIAL",
    "LINEUP_PLAYED",
    "LINEUP_PROBABLE",
    "LineupPlayer",
    "MatchLineupService",
    "TeamLineup",
    "by_line",
    "mark_absences",
    "same_player",
    "should_fetch_official",
]
