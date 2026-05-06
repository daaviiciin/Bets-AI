"""Ingester de los JSONs de Sofascore hacia PostgreSQL.

Soporta dos endpoints:

* ``top_players``: cada categoría (29) trae top ~50 jugadores con UN stat
  específico. Combinamos las 29 categorías por ``player_id`` para obtener
  estadísticas completas (goals, shots, sot, xG, ...) por jugador.

* ``standings``: clasificación con totales por equipo (matches, W/D/L, GF, GA,
  points). xG a nivel equipo no viene en este endpoint; lo dejaremos null
  hasta que añadamos otra fuente.

Idempotencia
------------
Tanto Player como Team usan ``(external_source='sofascore', external_id)``
como clave estable. Para Team primero intentamos casar por nombre exacto
(útil para reutilizar las filas que ya creó el scraper de cuotas) y después
caemos a crear nuevas.

Las stats de temporada (``PlayerSeasonStats``, ``TeamSeasonStats``) usan
``(player|team_id, season_id)`` como clave única; reaplicar el ingester
sobreescribe los valores con los más recientes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import Settings, get_settings
from backend.app.database import get_session_factory
from backend.models import (
    League,
    Player,
    PlayerSeasonStats,
    Season,
    Team,
    TeamSeasonStats,
)

EXTERNAL_SOURCE = "sofascore"


@dataclass
class IngestSummary:
    """Resumen del ingest de un fichero."""

    file_path: Path
    endpoint: str
    league_name: str
    season_name: str | None = None
    teams_created: int = 0
    teams_updated: int = 0
    players_created: int = 0
    players_updated: int = 0
    player_stats_upserted: int = 0
    team_stats_upserted: int = 0
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


class SofascoreIngester:
    """Procesa JSONs de Sofascore hacia la BD."""

    def __init__(self, session: Session, *, log: logging.Logger | None = None) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)

    def ingest_file(self, path: Path) -> IngestSummary:
        payload = json.loads(path.read_text(encoding="utf-8"))
        metadata = payload.get("metadata", {})
        endpoint = metadata.get("endpoint", "?")
        league_name = metadata.get("league_name", "?")
        sport_key = metadata.get("sport_key")
        season_id_external = metadata.get("season_id")
        season_name = metadata.get("season_name")

        summary = IngestSummary(
            file_path=path,
            endpoint=endpoint,
            league_name=league_name,
            season_name=season_name,
        )

        if not sport_key:
            summary.error = "missing sport_key in metadata"
            return summary

        league = self._get_league(sport_key)
        if league is None:
            summary.error = f"liga {sport_key!r} no existe en la BD (haz un refresh primero)"
            return summary

        season = self._get_or_create_season(league, season_name, season_id_external)

        try:
            if endpoint == "top_players":
                self._ingest_top_players(payload["data"], league, season, summary)
            elif endpoint == "standings":
                self._ingest_standings(payload["data"], league, season, summary)
            else:
                summary.error = f"endpoint desconocido: {endpoint}"
                return summary
            self.session.commit()
        except Exception as exc:
            self.session.rollback()
            summary.error = f"unexpected: {exc}"
            self.log.exception("Error procesando %s", path.name)

        return summary

    def _get_league(self, sport_key: str) -> League | None:
        return self.session.scalar(select(League).where(League.sport_key == sport_key))

    def _get_or_create_season(
        self, league: League, season_name: str | None, external_season_id: Any,
    ) -> Season:
        year_start, year_end = _parse_season_years(season_name)
        season = self.session.scalar(
            select(Season).where(
                Season.league_id == league.id,
                Season.year_start == year_start,
            )
        )
        if season is not None:
            if season_name and season.name != season_name:
                season.name = season_name
            return season

        season = Season(
            league_id=league.id,
            name=season_name or f"{year_start}-{year_end}",
            year_start=year_start,
            year_end=year_end,
        )
        self.session.add(season)
        self.session.flush()
        self.log.info("Season creada: %s (id=%d)", season.name, season.id)
        return season

    def _get_or_create_team(
        self, name: str, external_id: int | str, summary: IngestSummary,
    ) -> Team:
        external_id = str(external_id)

        team = self.session.scalar(
            select(Team).where(
                Team.external_source == EXTERNAL_SOURCE,
                Team.external_id == external_id,
            )
        )
        if team is not None:
            if team.name != name:
                self.log.debug("Team rename %s -> %s", team.name, name)
                team.name = name
                summary.teams_updated += 1
            return team

        team = self.session.scalar(select(Team).where(Team.name == name))
        if team is not None:
            team.external_source = EXTERNAL_SOURCE
            team.external_id = external_id
            summary.teams_updated += 1
            return team

        team = Team(name=name, external_source=EXTERNAL_SOURCE, external_id=external_id)
        self.session.add(team)
        self.session.flush()
        summary.teams_created += 1
        return team

    def _get_or_create_player(
        self,
        name: str,
        external_id: int | str,
        position: str | None,
        team: Team,
        summary: IngestSummary,
    ) -> Player:
        external_id = str(external_id)

        player = self.session.scalar(
            select(Player).where(
                Player.external_source == EXTERNAL_SOURCE,
                Player.external_id == external_id,
            )
        )
        if player is not None:
            changed = False
            if player.name != name:
                player.name = name
                changed = True
            if position and player.position != position:
                player.position = position
                changed = True
            if player.team_id != team.id:
                player.team_id = team.id
                changed = True
            if changed:
                summary.players_updated += 1
            return player

        player = Player(
            name=name,
            external_source=EXTERNAL_SOURCE,
            external_id=external_id,
            position=position,
            team_id=team.id,
        )
        self.session.add(player)
        self.session.flush()
        summary.players_created += 1
        return player

    def _ingest_top_players(
        self,
        data: dict,
        league: League,
        season: Season,
        summary: IngestSummary,
    ) -> None:
        """Combina las 29 categorías en stats por jugador y los inserta."""
        top = data.get("topPlayers", {})
        if not top:
            summary.warnings.append("topPlayers vacío")
            return

        merged: dict[int, dict[str, Any]] = {}
        for category, entries in top.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                player_blob = entry.get("player", {})
                team_blob = entry.get("team", {})
                stats_blob = entry.get("statistics", {})
                player_id = player_blob.get("id")
                if player_id is None:
                    continue

                bucket = merged.setdefault(player_id, {
                    "player": player_blob,
                    "team": team_blob,
                    "stats": {},
                })
                for key, value in stats_blob.items():
                    if key in {"id", "type", "statisticsType"}:
                        continue
                    bucket["stats"].setdefault(key, value)

        self.log.info(
            "Top-players merge: %d jugadores únicos en %d categorías",
            len(merged), len(top),
        )

        now = datetime.now(timezone.utc)
        for player_id, bucket in merged.items():
            team = self._get_or_create_team(
                bucket["team"]["name"], bucket["team"]["id"], summary,
            )
            player = self._get_or_create_player(
                bucket["player"]["name"],
                player_id,
                bucket["player"].get("position"),
                team,
                summary,
            )
            self._upsert_player_stats(player, season, team, bucket["stats"], now)
            summary.player_stats_upserted += 1

    def _upsert_player_stats(
        self,
        player: Player,
        season: Season,
        team: Team,
        sofa_stats: dict,
        now: datetime,
    ) -> None:
        stats = self.session.scalar(
            select(PlayerSeasonStats).where(
                PlayerSeasonStats.player_id == player.id,
                PlayerSeasonStats.season_id == season.id,
            )
        )
        if stats is None:
            stats = PlayerSeasonStats(player_id=player.id, season_id=season.id)
            self.session.add(stats)

        appearances = _to_int(sofa_stats.get("appearances"))
        total_shots = _to_int(sofa_stats.get("totalShots"))
        sot = _to_int(sofa_stats.get("shotsOnTarget"))

        stats.team_id = team.id
        stats.matches_played = appearances
        stats.minutes = appearances * 90 if appearances else None
        stats.nineties = _to_dec(appearances) if appearances is not None else None

        stats.goals = _to_int(sofa_stats.get("goals"))
        stats.assists = _to_int(sofa_stats.get("assists"))
        stats.penalties_made = _to_int(sofa_stats.get("penaltyGoals"))
        stats.penalties_attempted = _to_int(sofa_stats.get("penaltiesTaken"))

        stats.shots = total_shots
        stats.shots_on_target = sot
        stats.shots_on_target_pct = _safe_pct(sot, total_shots)

        stats.shots_per_90 = _safe_per_90(total_shots, appearances)
        stats.shots_on_target_per_90 = _safe_per_90(sot, appearances)

        goals = _to_int(sofa_stats.get("goals"))
        stats.goals_per_shot = _safe_div(goals, total_shots, 3)
        stats.goals_per_shot_on_target = _safe_div(goals, sot, 3)

        stats.xg = _to_dec(sofa_stats.get("expectedGoals"))
        stats.npxg = _to_dec(sofa_stats.get("expectedGoals"))
        stats.xg_per_shot = _safe_div(stats.xg, total_shots, 3) if stats.xg is not None else None

        stats.yellow_cards = _to_int(sofa_stats.get("yellowCards"))
        stats.red_cards = _to_int(sofa_stats.get("redCards"))

        stats.last_updated = now

    def _ingest_standings(
        self,
        data: dict,
        league: League,
        season: Season,
        summary: IngestSummary,
    ) -> None:
        groups = data.get("standings", [])
        if not groups:
            summary.warnings.append("standings vacío")
            return

        rows = []
        for g in groups:
            rows.extend(g.get("rows", []))
        self.log.info("Standings: %d equipos", len(rows))

        now = datetime.now(timezone.utc)
        for row in rows:
            team_blob = row.get("team", {})
            team = self._get_or_create_team(team_blob["name"], team_blob["id"], summary)
            self._upsert_team_stats(team, season, row, now)
            summary.team_stats_upserted += 1

    def _upsert_team_stats(
        self,
        team: Team,
        season: Season,
        row: dict,
        now: datetime,
    ) -> None:
        stats = self.session.scalar(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == team.id,
                TeamSeasonStats.season_id == season.id,
            )
        )
        if stats is None:
            stats = TeamSeasonStats(team_id=team.id, season_id=season.id)
            self.session.add(stats)

        gf = _to_int(row.get("scoresFor"))
        ga = _to_int(row.get("scoresAgainst"))
        stats.matches_played = _to_int(row.get("matches"))
        stats.wins = _to_int(row.get("wins"))
        stats.draws = _to_int(row.get("draws"))
        stats.losses = _to_int(row.get("losses"))
        stats.goals_for = gf
        stats.goals_against = ga
        stats.goal_diff = (gf - ga) if (gf is not None and ga is not None) else None
        stats.points = _to_int(row.get("points"))
        stats.last_updated = now


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _to_dec(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


def _safe_div(num: Any, den: Any, digits: int = 2) -> Decimal | None:
    n = _to_dec(num)
    d = _to_dec(den)
    if n is None or d is None or d == 0:
        return None
    return (n / d).quantize(Decimal(10) ** -digits)


def _safe_pct(num: Any, den: Any) -> Decimal | None:
    n = _to_dec(num)
    d = _to_dec(den)
    if n is None or d is None or d == 0:
        return None
    return (n * 100 / d).quantize(Decimal("0.01"))


def _safe_per_90(stat: Any, appearances: Any) -> Decimal | None:
    return _safe_div(stat, appearances, 2)


def _parse_season_years(season_name: str | None) -> tuple[int, int]:
    """``LaLiga 25/26`` o ``2025/26`` → (2025, 2026)."""
    if not season_name:
        now = datetime.now(timezone.utc)
        return now.year - 1, now.year

    fragments = season_name.replace("-", "/").split()
    for frag in reversed(fragments):
        if "/" in frag:
            a, b = frag.split("/", 1)
            try:
                ai, bi = int(a), int(b)
                if ai < 100:
                    ai += 2000
                if bi < 100:
                    bi += 2000
                return ai, bi
            except ValueError:
                continue
    now = datetime.now(timezone.utc)
    return now.year - 1, now.year


def _list_files(raw_dir: Path) -> list[Path]:
    sf_dir = raw_dir / "sofascore"
    if not sf_dir.exists():
        return []
    return sorted(sf_dir.glob("*.json"))


def _print_summary(summaries: list[IngestSummary]) -> None:
    log = logging.getLogger(__name__)
    log.info("=" * 80)
    log.info("RESUMEN INGEST SOFASCORE")
    log.info("=" * 80)
    for s in summaries:
        estado = "ERR" if s.error else "OK "
        log.info(
            "  [%s] %-40s endpoint=%s league=%s season=%s",
            estado, s.file_path.name, s.endpoint, s.league_name, s.season_name,
        )
        if s.error:
            log.info("       ERROR: %s", s.error)
        else:
            log.info(
                "       teams: +%d nuevos / %d actualizados | "
                "players: +%d nuevos / %d actualizados",
                s.teams_created, s.teams_updated,
                s.players_created, s.players_updated,
            )
            if s.player_stats_upserted:
                log.info("       player_stats upserted: %d", s.player_stats_upserted)
            if s.team_stats_upserted:
                log.info("       team_stats upserted:   %d", s.team_stats_upserted)
            for w in s.warnings:
                log.info("       WARN: %s", w)
    log.info("=" * 80)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingester de JSONs Sofascore hacia PostgreSQL.")
    parser.add_argument("--file", type=Path, default=None,
                        help="Procesa un fichero concreto (default: todos los pendientes).")
    args = parser.parse_args(argv)

    force_utf8_stdio()
    settings: Settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.file:
        files = [args.file]
    else:
        files = _list_files(settings.raw_data_dir)

    if not files:
        logging.warning("No hay ficheros Sofascore que procesar")
        return 0

    SessionLocal = get_session_factory()
    summaries: list[IngestSummary] = []
    for fp in files:
        with SessionLocal() as session:
            ingester = SofascoreIngester(session)
            summaries.append(ingester.ingest_file(fp))

    _print_summary(summaries)
    return 0 if all(s.error is None for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
