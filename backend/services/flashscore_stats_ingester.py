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
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from backend.models import Match, MatchStats, Team
from backend.models.match import MATCH_STATUS_FINISHED
from backend.scrapers import flashscore as fs
from backend.services.match_results_updater import _names_match, _normalize


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
    days_visited: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


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
        days_back: int = 7,
        only_top5: bool = True,
        limit_matches: int | None = None,
    ) -> IngestResult:
        """Ingiere stats de los partidos finalizados sin stats en los últimos
        ``days_back`` días."""
        started = datetime.now(timezone.utc)
        result = IngestResult(started_at=started, finished_at=started)

        eligible = self._load_eligible_matches(days_back=days_back, limit=limit_matches)
        result.matches_eligible = len(eligible)
        if not eligible:
            self.log.info("No hay partidos finalizados sin stats en los últimos %d días.",
                          days_back)
            result.finished_at = datetime.now(timezone.utc)
            return result

        # Agrupar por fecha local del partido para minimizar llamadas al feed.
        by_date: dict[date, list[Match]] = {}
        for m in eligible:
            played = m.played_at or m.commence_time
            day_local = played.astimezone(timezone.utc).date()
            by_date.setdefault(day_local, []).append(m)

        today = datetime.now(timezone.utc).date()
        for d, matches in sorted(by_date.items()):
            offset = (d - today).days
            self.log.info("Procesando %d partidos del %s (offset %+d)",
                          len(matches), d.isoformat(), offset)
            result.days_visited.append(d.isoformat())
            try:
                fixtures = fs.fetch_fixtures_feed(day_offset=offset)
            except Exception as exc:  # noqa: BLE001
                msg = f"Error descargando feed día {d}: {exc}"
                self.log.warning(msg)
                result.errors.append(msg)
                result.fetch_errors += 1
                continue

            if only_top5:
                fixtures = fs._filter_top5(fixtures)

            self._ingest_day(matches, fixtures, result)

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
        # IDs ya con stats (cualquier fila en match_stats)
        existing = set(self.session.scalars(
            select(MatchStats.match_id).distinct()
        ).all())

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
        if limit is not None:
            stmt = stmt.limit(limit * 4)  # margen por si hay muchos ya con stats

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
            fs_match = self._match_db_to_flashscore(m, fixtures)
            if fs_match is None:
                result.matches_unmatched += 1
                self.log.info(
                    "  [SKIP] %s vs %s: no encontrado en Flashscore",
                    m.home_team.name, m.away_team.name,
                )
                continue
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

            if not parsed["home"] and not parsed["away"]:
                self.log.info(
                    "  [EMPTY] %s vs %s: feed sin stats (¿partido sin datos?)",
                    m.home_team.name, m.away_team.name,
                )
                continue

            self._upsert_match_stats(m, fs_match.match_id, parsed, result)

    def _match_db_to_flashscore(
        self, m: Match, fixtures: list[fs.FlashscoreMatch],
    ) -> fs.FlashscoreMatch | None:
        """Empareja un Match de BD con un FlashscoreMatch del feed."""
        target_home = _normalize(m.home_team.name)
        target_away = _normalize(m.away_team.name)
        target_kickoff = m.commence_time

        candidates: list[tuple[float, fs.FlashscoreMatch]] = []
        for f in fixtures:
            if f.scheduled_at is None:
                continue
            delta_min = abs((f.scheduled_at - target_kickoff).total_seconds()) / 60
            if delta_min > self.MATCH_WINDOW_MIN:
                continue
            f_home = _normalize(f.home_team_name or "")
            f_away = _normalize(f.away_team_name or "")
            # Tolerante a flips home/away (algunas integraciones los invierten)
            same_pair = (
                (_names_match(target_home, f_home) and _names_match(target_away, f_away))
                or (_names_match(target_home, f_away) and _names_match(target_away, f_home))
            )
            if not same_pair:
                continue
            candidates.append((delta_min, f))

        if not candidates:
            return None
        candidates.sort(key=lambda t: t[0])
        return candidates[0][1]

    def _upsert_match_stats(
        self,
        match: Match,
        flashscore_match_id: str,
        parsed: dict[str, dict[str, float | int]],
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
            stats_dict = parsed.get(side, {})
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
