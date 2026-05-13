"""Pipeline diario unificado para mantener la BD al día sin gastar créditos.

Encadena los pasos **gratuitos** que deben correr cada día:

1. ``FlashscoreFixturesIngester`` → inserta próximos partidos (top 5 + opcional UEFA)
   en ``matches`` con ``external_id`` prefijado ``flashscore:`` (scraping gratis).
2. ``MatchResultsUpdater``  → marca como ``finished`` los partidos jugados
   y rellena ``Match.sofascore_event_id`` (Sofascore unofficial API, gratis).
3. ``FlashscoreStatsIngester`` → ingesta xG, tiros, posesión, córners,
   tarjetas, etc. para los partidos finalizados sin stats todavía
   (Flashscore feed ``df_st_1``, gratis).
4. ``PlayerMatchStatsIngester`` → ingesta stats individuales por jugador
   (goles, tiros, SoT, rating, …) usando Sofascore ``/event/{id}/lineups``.
   Se apoya en el ``sofascore_event_id`` que rellena el paso 2.

Los pasos que sí cuestan (cuotas vía Odds API) o son más pesados (re-scraping
de stats de temporada por liga) se quedan fuera de este pipeline diario;
para esos hay endpoints / CLIs dedicados que se invocan a demanda o con
una frecuencia mucho menor.

El orchestrator es **idempotente**: si un día no hay nada nuevo que hacer
(no hay partidos finalizados desde la última pasada) los servicios subyacentes
no escriben nada y el resumen lo refleja.
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
from backend.services.flashscore_stats_ingester import (
    FlashscoreStatsIngester,
    IngestResult,
)
from backend.services.match_results_updater import (
    MatchResultsUpdater,
    UpdateSummary,
)
from backend.services.player_match_stats_ingester import (
    PlayerMatchStatsIngester,
    PlayerStatsIngestResult,
)


@dataclass
class DailyRefreshSummary:
    """Resumen agregado de un run del pipeline."""
    started_at: datetime
    finished_at: datetime
    results: UpdateSummary | None = None
    stats: IngestResult | None = None
    player_stats: PlayerStatsIngestResult | None = None
    fixtures: FlashscoreFixturesIngestResult | None = None
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
        results_updater: MatchResultsUpdater | None = None,
        fixtures_ingester: FlashscoreFixturesIngester | None = None,
        stats_ingester: FlashscoreStatsIngester | None = None,
        player_stats_ingester: PlayerMatchStatsIngester | None = None,
    ) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)
        # Permitimos inyectar dependencias (clave para tests con mocks).
        self._results_updater = results_updater or MatchResultsUpdater(session, log=self.log)
        self._fixtures_ingester = fixtures_ingester or FlashscoreFixturesIngester(session, log=self.log)
        self._stats_ingester = stats_ingester or FlashscoreStatsIngester(session, log=self.log)
        self._player_stats_ingester = (
            player_stats_ingester or PlayerMatchStatsIngester(session, log=self.log)
        )

    def run(
        self,
        *,
        results_max_pages: int = 3,
        stats_days_back: int = 7,
        stats_only_top5: bool = True,
        stats_limit: int | None = None,
        fixtures_days_ahead: int = 7,
        player_stats_max_matches: int = 30,
        player_stats_with_cards: bool = False,
    ) -> DailyRefreshSummary:
        """Ejecuta el pipeline completo y devuelve un resumen.

        :param results_max_pages: páginas de Sofascore por liga.
        :param stats_days_back: ventana hacia atrás para buscar partidos
            sin stats Flashscore.
        :param stats_only_top5: si ``True``, restringe la ingesta de stats
            a las 5 grandes ligas (más fiable).
        :param stats_limit: número máximo de partidos cuyas stats se
            ingestan (útil para no saturar Flashscore).
        :param fixtures_days_ahead: días de feeds Flashscore a recorrer (0..N-1)
            para insertar partidos próximos.
        :param player_stats_max_matches: número máximo de partidos para
            los que se ingesta el detalle por jugador.
        :param player_stats_with_cards: si ``True``, además llama a
            ``/event/{id}/incidents`` para sacar tarjetas (1 request extra
            por partido).
        """
        started = datetime.now(timezone.utc)
        summary = DailyRefreshSummary(started_at=started, finished_at=started)

        self.log.info("=" * 80)
        self.log.info("PIPELINE DIARIO  (paso 1/4: fixtures próximos Flashscore → BD)")
        self.log.info("=" * 80)
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
            msg = f"Error en flashscore_fixtures_ingester: {exc}"
            self.log.exception(msg)
            summary.errors.append(msg)
            self.session.rollback()

        self.log.info("=" * 80)
        self.log.info("PIPELINE DIARIO  (paso 2/4: actualizar resultados de partidos)")
        self.log.info("=" * 80)
        try:
            results = self._results_updater.update_all(max_pages=results_max_pages)
            self.session.commit()
            summary.results = results
            self.log.info(
                "Resultados: %d actualizados, %d ya OK, %d sin partido en BD (%.1fs)",
                results.total_updated, results.total_already_ok,
                results.total_not_found,
                (results.finished_at - results.started_at).total_seconds(),
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"Error en match_results_updater: {exc}"
            self.log.exception(msg)
            summary.errors.append(msg)
            self.session.rollback()

        self.log.info(
            "PIPELINE DIARIO  (entre 2 y 3: rollup bb_points_* temporada desde resultados)",
        )
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
            msg = f"Error en rollup baloncesto: {exc}"
            self.log.exception(msg)
            summary.errors.append(msg)
            self.session.rollback()

        self.log.info("=" * 80)
        self.log.info("PIPELINE DIARIO  (paso 3/4: stats post-partido por equipo)")
        self.log.info("=" * 80)
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
            msg = f"Error en flashscore_stats_ingester: {exc}"
            self.log.exception(msg)
            summary.errors.append(msg)
            self.session.rollback()

        self.log.info("=" * 80)
        self.log.info("PIPELINE DIARIO  (paso 4/4: stats por jugador)")
        self.log.info("=" * 80)
        try:
            player_stats = self._player_stats_ingester.ingest(
                max_matches=player_stats_max_matches,
                with_cards=player_stats_with_cards,
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
        except Exception as exc:  # noqa: BLE001
            msg = f"Error en player_match_stats_ingester: {exc}"
            self.log.exception(msg)
            summary.errors.append(msg)
            self.session.rollback()

        summary.finished_at = datetime.now(timezone.utc)
        self.log.info(
            "Pipeline diario completado en %.1fs (errores=%d)",
            summary.duration_seconds, len(summary.errors),
        )
        return summary


__all__ = ["DailyRefreshOrchestrator", "DailyRefreshSummary"]
