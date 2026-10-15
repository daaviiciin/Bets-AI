"""Pipeline diario unificado para mantener la BD al día sin gastar créditos.

Desde octubre de 2026 **todo sale de Flashscore** (Sofascore responde
``403 challenge``). Pasos, en orden:

1. ``FlashscoreFixturesIngester`` → próximos partidos (top 5 + opcional UEFA)
   con ``external_id = "flashscore:<id>"``.
2. ``FlashscoreResultsUpdater`` → marca como ``finished`` los partidos jugados,
   con marcador, emparejando por id de Flashscore.
3. ``FlashscoreStandingsIngester`` → clasificación de cada liga en
   ``team_season_stats`` (la usa el modelo del dashboard).
4. ``FlashscoreStatsIngester`` → stats post-partido por equipo (xG, tiros,
   córners, tarjetas…, también por mitad).
5. ``FlashscorePlayerStatsIngester`` → stats por jugador y partido; después
   ``rollup_player_season_stats`` recalcula los totales de temporada por jugador.
6. ``MatchAbsencesIngester`` → bajas y dudas por partido.

Los servicios de Sofascore (``match_results_updater``,
``player_match_stats_ingester``, ``sofascore_ingester``) siguen en el código por
si Sofascore vuelve a responder, pero ya no forman parte del pipeline.

El orchestrator es **idempotente**: si no hay nada nuevo, los servicios no
escriben nada y el resumen lo refleja. Si un paso falla, los siguientes siguen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.services.basketball_season_rollup import (
    rollup_basketball_player_season_totals,
    rollup_basketball_team_season_stats,
)
from backend.services.flashscore_fixtures_ingester import (
    FlashscoreFixturesIngester,
    FlashscoreFixturesIngestResult,
)
from backend.services.flashscore_player_stats_ingester import FlashscorePlayerStatsIngester
from backend.services.flashscore_results_updater import (
    FlashscoreResultsResult,
    FlashscoreResultsUpdater,
)
from backend.services.flashscore_standings_ingester import (
    FlashscoreStandingsIngester,
    StandingsIngestResult,
)
from backend.services.flashscore_stats_ingester import (
    FlashscoreStatsIngester,
    IngestResult,
)
from backend.services.match_absences_ingester import (
    AbsencesIngestResult,
    MatchAbsencesIngester,
)
from backend.services.match_results_updater import UpdateSummary
from backend.services.player_match_stats_ingester import PlayerStatsIngestResult
from backend.services.player_season_rollup import rollup_player_season_stats
from backend.services.season_history_ingester import SeasonHistoryIngester


@dataclass
class DailyRefreshSummary:
    """Resumen agregado de un run del pipeline."""
    started_at: datetime
    finished_at: datetime
    # Resultados vía Sofascore: ya no se ejecuta; se mantiene por compatibilidad de la API.
    results: UpdateSummary | None = None
    fs_results: FlashscoreResultsResult | None = None
    standings: StandingsIngestResult | None = None
    stats: IngestResult | None = None
    player_stats: PlayerStatsIngestResult | None = None
    fixtures: FlashscoreFixturesIngestResult | None = None
    absences: AbsencesIngestResult | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def ok(self) -> bool:
        return not self.errors


class DailyRefreshOrchestrator:
    """Coordina los pasos del pipeline diario en orden y captura errores."""

    def __init__(
        self,
        session: Session,
        *,
        log: logging.Logger | None = None,
        fixtures_ingester: FlashscoreFixturesIngester | None = None,
        fs_results_updater: FlashscoreResultsUpdater | None = None,
        standings_ingester: FlashscoreStandingsIngester | None = None,
        stats_ingester: FlashscoreStatsIngester | None = None,
        player_stats_ingester: FlashscorePlayerStatsIngester | None = None,
        absences_ingester: MatchAbsencesIngester | None = None,
        history_ingester: SeasonHistoryIngester | None = None,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        # Permitimos inyectar dependencias (clave para tests con mocks).
        self._fixtures_ingester = fixtures_ingester or FlashscoreFixturesIngester(session, log=self.log)
        self._fs_results_updater = (
            fs_results_updater or FlashscoreResultsUpdater(session, log=self.log)
        )
        self._standings_ingester = (
            standings_ingester or FlashscoreStandingsIngester(session, log=self.log)
        )
        self._stats_ingester = stats_ingester or FlashscoreStatsIngester(session, log=self.log)
        self._player_stats_ingester = (
            player_stats_ingester or FlashscorePlayerStatsIngester(session, log=self.log)
        )
        self._absences_ingester = (
            absences_ingester or MatchAbsencesIngester(session, log=self.log)
        )
        self._history_ingester = history_ingester or SeasonHistoryIngester(session, log=self.log)

    def run(
        self,
        *,
        stats_days_back: int = 120,
        stats_only_top5: bool = True,
        stats_limit: int | None = 40,
        fixtures_days_ahead: int = 7,
        results_days_back: int = 3,
        player_stats_max_matches: int = 30,
        player_stats_days_back: int = 60,
        absences_days_ahead: int = 3,
        absences_max_matches: int = 40,
    ) -> DailyRefreshSummary:
        """Ejecuta el pipeline completo y devuelve un resumen.

        :param stats_days_back: ventana hacia atrás para buscar partidos sin
            stats post-partido.
        :param stats_only_top5: si ``True``, calendario y stats solo de las 5
            grandes ligas; si ``False``, también competiciones UEFA.
        :param stats_limit: máximo de partidos cuyas stats se ingieren por ejecución
            (2 requests por partido). Limita el coste mientras se completa el histórico.
        :param fixtures_days_ahead: días de calendario a recorrer (0..N-1).
        :param results_days_back: días hacia atrás del feed diario para buscar
            resultados (máx. 7; 1 request por día).
        :param player_stats_max_matches: máximo de partidos con stats por jugador
            por ejecución (2 requests por partido).
        :param player_stats_days_back: solo partidos jugados en los últimos N días
            (60: cubre el histórico de la temporada para los props de tiros a puerta).
        :param absences_days_ahead: bajas de los partidos de los próximos N días
            (además del histórico pendiente).
        :param absences_max_matches: máximo de consultas de bajas (1 request por partido).
        """
        started = datetime.now(timezone.utc)
        summary = DailyRefreshSummary(started_at=started, finished_at=started)

        self._step("paso 1/6: calendario Flashscore → BD")
        try:
            fixtures = self._fixtures_ingester.ingest(
                days_ahead=fixtures_days_ahead,
                only_top5=stats_only_top5,
            )
            self.session.commit()
            summary.fixtures = fixtures
            self.log.info(
                "Fixtures: fetched=%d inserted=%d updated=%d omitted_dup=%d (%.1fs)",
                fixtures.fixtures_fetched,
                fixtures.fixtures_inserted,
                fixtures.fixtures_updated,
                fixtures.skipped_duplicate_db,
                fixtures.duration_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            self._fail(summary, "flashscore_fixtures_ingester", exc)

        self._step("paso 2/6: resultados Flashscore")
        try:
            fs_results = self._fs_results_updater.update(days_back=results_days_back)
            self.session.commit()
            summary.fs_results = fs_results
            self.log.info(
                "Resultados Flashscore: %d terminados de %d pendientes, %d siguen pendientes "
                "(%d feeds, %.1fs)",
                fs_results.matches_updated,
                fs_results.matches_pending,
                fs_results.still_pending,
                fs_results.feeds_fetched,
                fs_results.duration_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            self._fail(summary, "flashscore_results_updater", exc)

        try:
            tr = rollup_basketball_team_season_stats(self.session)
            rollup_basketball_player_season_totals(self.session)
            self.session.commit()
            self.log.info(
                "Rollup baloncesto: ligas=%d, filas equipo=%d",
                tr.leagues_touched,
                tr.team_rows_upserted,
            )
        except Exception as exc:  # noqa: BLE001
            self._fail(summary, "rollup baloncesto", exc)

        self._step("paso 3/6: clasificación + histórico de la temporada")
        try:
            standings = self._standings_ingester.ingest()
            summary.standings = standings
            summary.errors.extend(f"clasificación: {e}" for e in standings.errors)
            self.log.info(
                "Clasificación: %d ligas, %d equipos, %d equipos nuevos (%.1fs)",
                len(standings.leagues_ok),
                standings.teams_upserted,
                standings.teams_created,
                standings.duration_seconds,
            )
            # La misma página trae los partidos jugados: sin peticiones extra.
            history = self._history_ingester.ingest(standings.finished_matches)
            self.log.info(
                "Histórico: %d partidos nuevos, %d cerrados con resultado, %d ya estaban",
                history.matches_created, history.matches_finished, history.matches_already_ok,
            )
        except Exception as exc:  # noqa: BLE001
            self._fail(summary, "flashscore_standings_ingester", exc)

        self._step("paso 4/6: stats post-partido por equipo")
        try:
            stats = self._stats_ingester.ingest_recent(
                days_back=stats_days_back,
                only_top5=stats_only_top5,
                limit_matches=stats_limit,
            )
            self.session.commit()
            summary.stats = stats
            self.log.info(
                "Stats: %d emparejados, %d nuevos, %d actualizados (%.1fs)",
                stats.matches_matched, stats.stats_inserted,
                stats.stats_updated, stats.duration_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            self._fail(summary, "flashscore_stats_ingester", exc)

        self._step("paso 5/6: stats por jugador + totales de temporada")
        try:
            player_stats = self._player_stats_ingester.ingest(
                max_matches=player_stats_max_matches,
                days_back=player_stats_days_back,
            )
            summary.player_stats = player_stats
            self.log.info(
                "PlayerStats: %d partidos procesados, %d filas nuevas, "
                "%d filas actualizadas, %d jugadores creados (%.1fs)",
                player_stats.matches_processed,
                player_stats.rows_inserted,
                player_stats.rows_updated,
                player_stats.players_created,
                player_stats.duration_seconds,
            )
            if player_stats.matches_processed:
                rollup_player_season_stats(self.session, log=self.log)
                self.session.commit()
        except Exception as exc:  # noqa: BLE001
            self._fail(summary, "flashscore_player_stats_ingester", exc)

        self._step("paso 6/6: bajas y dudas por partido")
        try:
            absences = self._absences_ingester.ingest(
                days_ahead=absences_days_ahead,
                max_matches=absences_max_matches,
            )
            summary.absences = absences
            self.log.info(
                "Bajas: %d/%d partidos consultados, %d bajas (%d dudas), %d errores (%.1fs)",
                absences.matches_fetched,
                absences.matches_eligible,
                absences.absences_saved,
                absences.doubtful_saved,
                absences.fetch_errors,
                absences.duration_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            self._fail(summary, "match_absences_ingester", exc)

        self.log.info("PIPELINE DIARIO  (opcional: recalificar picks destacados)")
        try:
            from backend.services.featured_daily_picks import refresh_all_grading

            refresh_all_grading(self.session)
            self.log.info("Featured daily picks: grading refrescado")
        except Exception as exc:  # noqa: BLE001
            self._fail(summary, "featured_daily_picks.refresh_all_grading", exc)

        summary.finished_at = datetime.now(timezone.utc)
        self.log.info(
            "Pipeline diario completado en %.1fs (errores=%d)",
            summary.duration_seconds, len(summary.errors),
        )
        return summary

    def _step(self, title: str) -> None:
        self.log.info("=" * 80)
        self.log.info("PIPELINE DIARIO  (%s)", title)
        self.log.info("=" * 80)

    def _fail(self, summary: DailyRefreshSummary, step: str, exc: Exception) -> None:
        msg = f"Error en {step}: {exc}"
        self.log.exception(msg)
        summary.errors.append(msg)
        self.session.rollback()


__all__ = ["DailyRefreshOrchestrator", "DailyRefreshSummary"]
