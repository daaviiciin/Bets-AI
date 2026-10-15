"""Totales de temporada por jugador a partir de ``player_match_stats`` (Flashscore).

Sustituye a Sofascore ``top-players`` (bloqueado). Los usa ``value_detector``
para los player props (minutos, titularidades, tiros, tiros a puerta, goles…).

Solo cuenta partidos de liga (la temporada se busca por liga + año de inicio)
y solo sobrescribe una fila existente si la suma cubre al menos tantos partidos
como ella: así una fila más completa (p. ej. la de Sofascore del 17/09) no se
pisa con datos parciales antes de tener el histórico completo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Match, PlayerMatchStats, PlayerSeasonStats, Season
from backend.services.entity_resolver import FLASHSCORE_SOURCE


@dataclass(frozen=True)
class PlayerMatchLine:
    """Lo necesario de una fila de ``player_match_stats`` para agregar."""

    team_id: int | None
    commence_time: datetime
    is_starter: bool
    minutes: int
    goals: int
    assists: int
    shots: int
    shots_on_target: int
    xg: Decimal
    yellow_cards: int
    red_cards: int


@dataclass
class SeasonTotals:
    team_id: int | None
    matches_played: int
    starts: int
    minutes: int
    goals: int
    assists: int
    shots: int
    shots_on_target: int
    xg: Decimal
    yellow_cards: int
    red_cards: int

    @property
    def nineties(self) -> Decimal:
        return (Decimal(self.minutes) / Decimal(90)).quantize(Decimal("0.1"))

    def per_90(self, value: int) -> Decimal | None:
        if self.minutes <= 0:
            return None
        return (Decimal(value) * Decimal(90) / Decimal(self.minutes)).quantize(Decimal("0.01"))

    @staticmethod
    def ratio(num: int | Decimal, den: int, places: str = "0.001") -> Decimal | None:
        if den <= 0:
            return None
        return (Decimal(num) / Decimal(den)).quantize(Decimal(places))


def aggregate_season(lines: list[PlayerMatchLine]) -> SeasonTotals | None:
    """Suma los partidos con minutos; el equipo es el del partido más reciente."""
    played = [ln for ln in lines if ln.minutes > 0]
    if not played:
        return None
    latest = max(played, key=lambda ln: ln.commence_time)
    return SeasonTotals(
        team_id=latest.team_id,
        matches_played=len(played),
        starts=sum(1 for ln in played if ln.is_starter),
        minutes=sum(ln.minutes for ln in played),
        goals=sum(ln.goals for ln in played),
        assists=sum(ln.assists for ln in played),
        shots=sum(ln.shots for ln in played),
        shots_on_target=sum(ln.shots_on_target for ln in played),
        xg=sum((ln.xg for ln in played), Decimal("0")),
        yellow_cards=sum(ln.yellow_cards for ln in played),
        red_cards=sum(ln.red_cards for ln in played),
    )


def season_start_year(when: datetime) -> int:
    """Temporadas de julio a junio: oct-2026 → 2026, mar-2027 → 2026."""
    return when.year if when.month >= 7 else when.year - 1


@dataclass
class RollupResult:
    rows_written: int = 0
    rows_kept: int = 0  # filas existentes con más partidos que la suma
    errors: list[str] = field(default_factory=list)


def rollup_player_season_stats(session: Session, *, log: logging.Logger | None = None) -> RollupResult:
    log = log or logging.getLogger(__name__)
    result = RollupResult()
    seasons = {
        (s.league_id, s.year_start): s.id
        for s in session.scalars(select(Season)).all()
    }
    rows = session.execute(
        select(PlayerMatchStats, Match.league_id, Match.commence_time)
        .join(Match, Match.id == PlayerMatchStats.match_id)
        .where(PlayerMatchStats.source == FLASHSCORE_SOURCE)
    ).all()
    by_key: dict[tuple[int, int], list[PlayerMatchLine]] = {}
    for pms, league_id, commence in rows:
        season_id = seasons.get((league_id, season_start_year(commence)))
        if season_id is None:
            continue  # competición sin temporada propia (UEFA): no cuenta para la liga
        by_key.setdefault((pms.player_id, season_id), []).append(PlayerMatchLine(
            team_id=pms.team_id,
            commence_time=commence,
            is_starter=bool(pms.is_starter),
            minutes=pms.minutes_played or 0,
            goals=pms.goals or 0,
            assists=pms.assists or 0,
            shots=pms.shots or 0,
            shots_on_target=pms.shots_on_target or 0,
            xg=pms.expected_goals or Decimal("0"),
            yellow_cards=pms.yellow_cards or 0,
            red_cards=pms.red_cards or 0,
        ))

    now = datetime.now(timezone.utc)
    for (player_id, season_id), lines in by_key.items():
        totals = aggregate_season(lines)
        if totals is None:
            continue
        row = session.scalar(
            select(PlayerSeasonStats).where(
                PlayerSeasonStats.player_id == player_id,
                PlayerSeasonStats.season_id == season_id,
            )
        )
        if row is not None and (row.matches_played or 0) > totals.matches_played:
            result.rows_kept += 1
            continue
        if row is None:
            row = PlayerSeasonStats(player_id=player_id, season_id=season_id)
            session.add(row)
        row.team_id = totals.team_id
        row.matches_played = totals.matches_played
        row.starts = totals.starts
        row.minutes = totals.minutes
        row.nineties = totals.nineties
        row.goals = totals.goals
        row.assists = totals.assists
        row.shots = totals.shots
        row.shots_on_target = totals.shots_on_target
        row.shots_on_target_pct = SeasonTotals.ratio(
            totals.shots_on_target * 100, totals.shots, "0.01",
        )
        row.shots_per_90 = totals.per_90(totals.shots)
        row.shots_on_target_per_90 = totals.per_90(totals.shots_on_target)
        row.goals_per_shot = SeasonTotals.ratio(totals.goals, totals.shots)
        row.goals_per_shot_on_target = SeasonTotals.ratio(totals.goals, totals.shots_on_target)
        row.xg = totals.xg.quantize(Decimal("0.01"))
        row.xg_per_shot = SeasonTotals.ratio(totals.xg, totals.shots)
        row.yellow_cards = totals.yellow_cards
        row.red_cards = totals.red_cards
        row.last_updated = now
        result.rows_written += 1
    session.flush()
    log.info(
        "Rollup jugadores: %d filas escritas, %d conservadas (tenían más partidos)",
        result.rows_written, result.rows_kept,
    )
    return result
