"""Ingesta de stats post-partido desde Flashscore (``df_st_1`` feed).

Flujo:

1. Localiza partidos en BD con ``status='finished'`` y sin ``MatchStats`` aún.
2. Agrupa por fecha (``played_at``) y descarga el feed de fixtures del día
   correspondiente desde Flashscore.
3. Para cada match en BD, encuentra el ``flashscore_match_id`` equivalente
   por matching aproximado de nombre + ventana temporal (~3 h).
4. Descarga ``df_st_1_{flashscore_match_id}``, parsea, y persiste dos filas
   ``MatchStats`` (home + away) por encuentro.

Idempotente: si ya existen stats para ese partido, los actualiza con los
valores nuevos en lugar de duplicar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import time
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.models import Match, MatchStats, Team
from backend.models.match import MATCH_STATUS_FINISHED
from backend.scrapers import flashscore as fs
from backend.services.match_results_updater import _names_match, _normalize

TOP5_SPORT_KEYS = frozenset({
    "soccer_epl",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_spain_la_liga",
    "soccer_france_ligue_one",
})
HISTORY_REQUEST_SLEEP_SECONDS = 1.5


# Stats clave -> tipo de columna en MatchStats. Usamos esto para asignar
# dinámicamente sin enumerar 40 setattrs duplicados.
_DECIMAL_FIELDS: frozenset[str] = frozenset({"xg", "xg_on_target", "xa"})

# Todas las claves canónicas que parse_match_statistics() puede emitir.
# Las que terminan en _pct/_completed/_attempted se generan dinámicamente.
_INT_FIELDS: frozenset[str] = frozenset({
    "possession_pct",
    "shots_total", "shots_on_target", "shots_off_target", "shots_blocked",
    "shots_inside_box", "shots_outside_box", "shots_woodwork", "headed_goals",
    "big_chances", "corners", "offsides", "free_kicks", "throw_ins",
    "touches_in_box",
    "fouls", "yellow_cards", "red_cards", "duels_won", "clearances",
    "interceptions", "saves", "goal_kicks",
    "goals_1h", "goals_2h",
    "corners_1h", "corners_2h",
    "yellow_cards_1h", "yellow_cards_2h",
    "red_cards_1h", "red_cards_2h",
    "passes_pct", "passes_completed", "passes_attempted",
    "long_passes_pct", "long_passes_completed", "long_passes_attempted",
    "passes_final_third_pct", "passes_final_third_completed",
    "passes_final_third_attempted",
    "crosses_pct", "crosses_completed", "crosses_attempted",
    "tackles_pct", "tackles_completed", "tackles_attempted",
})


@dataclass
class IngestResult:
    """Resumen de una ejecución."""
    started_at: datetime
    finished_at: datetime
    matches_eligible: int = 0          # finished + sin stats aún
    matches_matched: int = 0           # encontrado flashscore_id
    matches_unmatched: int = 0         # no se pudo emparejar
    stats_inserted: int = 0            # filas MatchStats nuevas
    stats_updated: int = 0             # filas MatchStats actualizadas
    fetch_errors: int = 0
    parse_errors: int = 0
    competitions_visited: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


def _matches_with_complete_stats(rows: Iterable[tuple[int, bool]]) -> set[int]:
    sides_by_match: dict[int, set[bool]] = {}
    for match_id, is_home in rows:
        sides_by_match.setdefault(match_id, set()).add(is_home)
    return {
        match_id
        for match_id, sides in sides_by_match.items()
        if sides == {True, False}
    }


def _orient_stats_to_database(
    parsed: dict[str, dict[str, float | int]],
    summary_home: dict[str, int],
    summary_away: dict[str, int],
    *,
    reversed_sides: bool,
) -> tuple[dict[str, dict[str, float | int]], dict[str, int], dict[str, int]]:
    if not reversed_sides:
        return parsed, summary_home, summary_away
    return (
        {"home": parsed.get("away", {}), "away": parsed.get("home", {})},
        summary_away,
        summary_home,
    )


def _score_match_orientation(
    home_score: int | None,
    away_score: int | None,
    fixture_home_score: int | None,
    fixture_away_score: int | None,
    *,
    delta_minutes: float,
) -> bool | None:
    """Returns side reversal only for an exact score match near kickoff."""
    if delta_minutes > 15 or None in (
        home_score, away_score, fixture_home_score, fixture_away_score,
    ):
        return None
    if (home_score, away_score) == (fixture_home_score, fixture_away_score):
        return False
    if (home_score, away_score) == (fixture_away_score, fixture_home_score):
        return True
    return None


class FlashscoreStatsIngester:
    """Servicio principal."""

    # Ventana temporal para considerar dos partidos como el mismo encuentro
    # (en minutos alrededor del kickoff).
    MATCH_WINDOW_MIN = 180

    def __init__(self, session: Session, *, log: logging.Logger | None = None) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def ingest_recent(
        self,
        *,
        days_back: int = 120,
        only_top5: bool = True,
        limit_matches: int | None = None,
    ) -> IngestResult:
        """Ingiere stats de los partidos finalizados sin stats en los últimos
        ``days_back`` días."""
        days_back = max(1, days_back)
        started = datetime.now(timezone.utc)
        result = IngestResult(started_at=started, finished_at=started)

        eligible = self._load_eligible_matches(days_back=days_back, limit=limit_matches)
        if only_top5:
            eligible = [match for match in eligible if match.league.sport_key in TOP5_SPORT_KEYS]
        result.matches_eligible = len(eligible)
        if not eligible:
            self.log.info("No hay partidos finalizados sin stats en los últimos %d días.",
                          days_back)
            result.finished_at = datetime.now(timezone.utc)
            return result

        # Standings publica resultados e IDs de los últimos eventos de la temporada,
        # incluso cuando el feed diario f_-{offset} ya dejó de servirlos.
        by_league: dict[str, list[Match]] = {}
        for match in eligible:
            by_league.setdefault(match.league.sport_key, []).append(match)

        for sport_key, matches in sorted(by_league.items()):
            try:
                fixtures = fs.fetch_league_finished_matches(sport_key)
            except Exception as exc:  # noqa: BLE001
                msg = f"Error descargando histórico Flashscore {sport_key}: {exc}"
                self.log.warning(msg)
                result.errors.append(msg)
                result.fetch_errors += 1
                continue

            result.competitions_visited.append(sport_key)
            self.log.info(
                "Procesando %d partidos pendientes contra %d eventos de %s",
                len(matches), len(fixtures), sport_key,
            )
            self._ingest_day(matches, fixtures, result)
            time.sleep(HISTORY_REQUEST_SLEEP_SECONDS)

        result.finished_at = datetime.now(timezone.utc)
        self.log.info(
            "Ingesta finalizada en %.1fs: matched=%d/%d, stats nuevas=%d, actualizadas=%d",
            result.duration_seconds,
            result.matches_matched, result.matches_eligible,
            result.stats_inserted, result.stats_updated,
        )
        return result

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _load_eligible_matches(
        self, *, days_back: int, limit: int | None,
    ) -> list[Match]:
        """Partidos en BD finalizados, sin MatchStats, en la ventana temporal."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        # Solo damos un encuentro por cubierto cuando tiene fila local y visitante.
        existing_rows = self.session.execute(
            select(MatchStats.match_id, MatchStats.is_home)
        ).all()
        existing = _matches_with_complete_stats(existing_rows)

        stmt = (
            select(Match)
            .where(Match.status == MATCH_STATUS_FINISHED)
            .where(Match.commence_time >= cutoff)
            .options(
                joinedload(Match.home_team),
                joinedload(Match.away_team),
                joinedload(Match.league),
            )
            .order_by(Match.played_at.desc().nulls_last(), Match.commence_time.desc())
        )
        rows = self.session.scalars(stmt).all()
        out = [m for m in rows if m.id not in existing]
        if limit is not None:
            out = out[:limit]
        return out

    def _ingest_day(
        self,
        db_matches: list[Match],
        fixtures: list[fs.FlashscoreMatch],
        result: IngestResult,
    ) -> None:
        for m in db_matches:
            matched = self._match_db_to_flashscore(m, fixtures)
            if matched is None:
                result.matches_unmatched += 1
                self.log.info(
                    "  [SKIP] %s vs %s: no encontrado en Flashscore",
                    m.home_team.name, m.away_team.name,
                )
                continue
            fs_match, reversed_sides = matched
            result.matches_matched += 1
            try:
                records = fs.fetch_match_statistics(fs_match.match_id)
            except Exception as exc:  # noqa: BLE001
                msg = f"  [ERR] {m.home_team.name} vs {m.away_team.name}: {exc}"
                self.log.warning(msg)
                result.errors.append(msg)
                result.fetch_errors += 1
                continue

            try:
                parsed = fs.parse_match_statistics(records)
            except Exception as exc:  # noqa: BLE001
                msg = f"  [PARSE] {m.home_team.name} vs {m.away_team.name}: {exc}"
                self.log.warning(msg)
                result.errors.append(msg)
                result.parse_errors += 1
                continue

            su_home: dict[str, int] = {}
            su_away: dict[str, int] = {}
            try:
                su_recs = fs.fetch_match_summary_events(fs_match.match_id)
                su_home, su_away = fs.parse_team_half_metrics_from_summary_events(su_recs)
            except Exception as exc:  # noqa: BLE001
                self.log.debug(
                    "df_su sin datos o error para %s: %s", fs_match.match_id, exc,
                )

            parsed, su_home, su_away = _orient_stats_to_database(
                parsed,
                su_home,
                su_away,
                reversed_sides=reversed_sides,
            )

            if not parsed["home"] and not parsed["away"] and not su_home and not su_away:
                self.log.info(
                    "  [EMPTY] %s vs %s: feed sin stats (¿partido sin datos?)",
                    m.home_team.name, m.away_team.name,
                )
                continue

            self._upsert_match_stats(m, fs_match.match_id, parsed, su_home, su_away, result)

    def _match_db_to_flashscore(
        self, m: Match, fixtures: list[fs.FlashscoreMatch],
    ) -> tuple[fs.FlashscoreMatch, bool] | None:
        """Empareja un Match de BD con un FlashscoreMatch del feed."""
        target_home = _normalize(m.home_team.name)
        target_away = _normalize(m.away_team.name)
        target_kickoff = m.commence_time

        candidates: list[tuple[float, fs.FlashscoreMatch, bool]] = []
        score_candidates: list[tuple[float, fs.FlashscoreMatch, bool]] = []
        for f in fixtures:
            if f.scheduled_at is None:
                continue
            delta_min = abs((f.scheduled_at - target_kickoff).total_seconds()) / 60
            if delta_min > self.MATCH_WINDOW_MIN:
                continue
            f_home = _normalize(f.home_team_name or "")
            f_away = _normalize(f.away_team_name or "")
            # Tolerante a flips home/away (algunas integraciones los invierten)
            direct = _names_match(target_home, f_home) and _names_match(target_away, f_away)
            reversed_sides = _names_match(target_home, f_away) and _names_match(target_away, f_home)
            if direct or reversed_sides:
                candidates.append((delta_min, f, not direct and reversed_sides))
                continue
            score_reversed = _score_match_orientation(
                m.home_score,
                m.away_score,
                f.score_home,
                f.score_away,
                delta_minutes=delta_min,
            )
            if score_reversed is not None:
                score_candidates.append((delta_min, f, score_reversed))

        if candidates:
            candidates.sort(key=lambda t: t[0])
            _, fixture, reversed_sides = candidates[0]
            return fixture, reversed_sides
        if len(score_candidates) != 1:
            return None
        _, fixture, reversed_sides = score_candidates[0]
        return fixture, reversed_sides

    def _upsert_match_stats(
        self,
        match: Match,
        flashscore_match_id: str,
        parsed: dict[str, dict[str, float | int]],
        su_home: dict[str, int],
        su_away: dict[str, int],
        result: IngestResult,
    ) -> None:
        """Inserta o actualiza las dos filas (home/away) para este partido."""
        existing = {
            row.is_home: row
            for row in self.session.scalars(
                select(MatchStats).where(MatchStats.match_id == match.id)
            ).all()
        }
        now = datetime.now(timezone.utc)

        for is_home, side in ((True, "home"), (False, "away")):
            stats_dict: dict[str, float | int] = dict(parsed.get(side, {}))
            su_extra = su_home if is_home else su_away
            for k, v in su_extra.items():
                stats_dict[k] = v
            if not stats_dict:
                continue

            row = existing.get(is_home)
            if row is None:
                row = MatchStats(
                    match_id=match.id,
                    team_id=match.home_team_id if is_home else match.away_team_id,
                    is_home=is_home,
                    source="flashscore",
                    source_match_id=flashscore_match_id,
                    captured_at=now,
                )
                self.session.add(row)
                created = True
            else:
                created = False
                row.source_match_id = flashscore_match_id
                row.captured_at = now

            for field_name, value in stats_dict.items():
                if field_name in _DECIMAL_FIELDS:
                    setattr(row, field_name, Decimal(str(value)))
                elif field_name in _INT_FIELDS:
                    setattr(row, field_name, int(value))
                # claves desconocidas se ignoran silenciosamente

            if created:
                result.stats_inserted += 1
            else:
                result.stats_updated += 1

        self.log.info(
                "  [OK] %s %d-%d %s  stats persistidas",
            match.home_team.name, match.home_score or 0,
            match.away_score or 0, match.away_team.name,
        )


__all__ = ["FlashscoreStatsIngester", "IngestResult"]
