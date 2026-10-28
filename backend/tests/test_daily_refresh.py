"""Tests del ``DailyRefreshOrchestrator`` con mocks de los servicios (sin red ni BD)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.services.daily_refresh import (
    DailyRefreshOrchestrator,
    DailyRefreshSummary,
)
from backend.services.flashscore_fixtures_ingester import FlashscoreFixturesIngestResult
from backend.services.flashscore_results_updater import FlashscoreResultsResult
from backend.services.flashscore_standings_ingester import StandingsIngestResult
from backend.services.flashscore_stats_ingester import IngestResult
from backend.services.match_absences_ingester import AbsencesIngestResult
from backend.services.flashscore_player_stats_ingester import PlayerStatsIngestResult

NOW = datetime.now(timezone.utc)


def _make_fixtures_ingester() -> MagicMock:
    mock = MagicMock()
    mock.ingest.return_value = FlashscoreFixturesIngestResult(started_at=NOW, finished_at=NOW)
    return mock


def _make_fs_results_updater(updated: int = 4) -> MagicMock:
    mock = MagicMock()
    mock.update.return_value = FlashscoreResultsResult(
        started_at=NOW, finished_at=NOW, matches_pending=updated, matches_updated=updated,
    )
    return mock


def _make_standings_ingester(errors: list[str] | None = None) -> MagicMock:
    mock = MagicMock()
    mock.ingest.return_value = StandingsIngestResult(
        started_at=NOW, finished_at=NOW, leagues_ok=["soccer_epl"], teams_upserted=20,
        errors=errors or [],
    )
    return mock


def _make_stats_ingester(matched: int = 2, inserted: int = 4) -> MagicMock:
    mock = MagicMock()
    mock.ingest_recent.return_value = IngestResult(
        started_at=NOW,
        finished_at=NOW,
        matches_eligible=matched,
        matches_matched=matched,
        stats_inserted=inserted,
    )
    return mock


def _make_player_stats_ingester(processed: int = 0) -> MagicMock:
    mock = MagicMock()
    mock.ingest.return_value = PlayerStatsIngestResult(
        started_at=NOW, finished_at=NOW, matches_processed=processed,
    )
    return mock


def _make_absences_ingester(fetched: int = 2, saved: int = 7) -> MagicMock:
    mock = MagicMock()
    mock.ingest.return_value = AbsencesIngestResult(
        started_at=NOW,
        finished_at=NOW,
        matches_eligible=fetched,
        matches_fetched=fetched,
        absences_saved=saved,
    )
    return mock


def _make_session() -> MagicMock:
    s = MagicMock()
    s.commit.return_value = None
    s.rollback.return_value = None
    return s


def _orchestrator(session: MagicMock, **kwargs: Any) -> DailyRefreshOrchestrator:
    # Todos los servicios son mocks: los reales harían peticiones a Flashscore.
    defaults = {
        "fixtures_ingester": _make_fixtures_ingester,
        "fs_results_updater": _make_fs_results_updater,
        "standings_ingester": _make_standings_ingester,
        "stats_ingester": _make_stats_ingester,
        "player_stats_ingester": _make_player_stats_ingester,
        "absences_ingester": _make_absences_ingester,
    }
    for name, factory in defaults.items():
        kwargs.setdefault(name, factory())
    return DailyRefreshOrchestrator(session=session, **kwargs)


class TestHappyPath:
    def test_ejecuta_todos_los_pasos_sin_errores(self) -> None:
        session = _make_session()
        fixtures = _make_fixtures_ingester()
        standings = _make_standings_ingester()
        stats = _make_stats_ingester(matched=3, inserted=6)
        orchestrator = _orchestrator(
            session, fixtures_ingester=fixtures, standings_ingester=standings,
            stats_ingester=stats,
        )
        summary = orchestrator.run(stats_days_back=10)

        # Por defecto, calendario de top 5 + UEFA.
        fixtures.ingest.assert_called_once_with(days_ahead=7, only_top5=False)
        standings.ingest.assert_called_once_with()
        stats.ingest_recent.assert_called_once_with(
            days_back=10, only_top5=False, limit_matches=40,
        )
        session.rollback.assert_not_called()
        assert summary.ok is True
        assert summary.fixtures is not None and summary.fs_results is not None
        assert summary.standings is not None and summary.standings.teams_upserted == 20
        assert summary.stats is not None and summary.stats.stats_inserted == 6

    def test_solo_top5_filtra_el_calendario_pero_no_las_stats(self) -> None:
        # Los partidos UEFA ya guardados siguen recibiendo stats aunque no se importen nuevos.
        fixtures = _make_fixtures_ingester()
        stats = _make_stats_ingester()
        orchestrator = _orchestrator(
            _make_session(), fixtures_ingester=fixtures, stats_ingester=stats,
        )
        orchestrator.run(stats_only_top5=True, stats_limit=5)
        fixtures.ingest.assert_called_once_with(days_ahead=7, only_top5=True)
        stats.ingest_recent.assert_called_once_with(
            days_back=120, only_top5=False, limit_matches=5,
        )


class TestFlashscoreResultsStep:
    def test_pasa_dias_atras_y_guarda_el_resumen(self) -> None:
        fs_mock = _make_fs_results_updater(updated=7)
        summary = _orchestrator(_make_session(), fs_results_updater=fs_mock).run(
            results_days_back=5,
        )
        fs_mock.update.assert_called_once_with(days_back=5)
        assert summary.fs_results is not None and summary.fs_results.matches_updated == 7


class TestStandingsStep:
    def test_errores_de_una_liga_se_reflejan_en_el_resumen(self) -> None:
        standings = _make_standings_ingester(errors=["clasificación soccer_epl: HTTP 500"])
        summary = _orchestrator(_make_session(), standings_ingester=standings).run()
        assert summary.ok is False
        assert any("soccer_epl" in e for e in summary.errors)
        assert summary.stats is not None  # los pasos siguientes siguen


class TestPlayerStatsStep:
    def test_pasa_limites_al_ingester(self) -> None:
        players = _make_player_stats_ingester()
        _orchestrator(_make_session(), player_stats_ingester=players).run(
            player_stats_max_matches=12, player_stats_days_back=7,
        )
        players.ingest.assert_called_once_with(max_matches=12, days_back=7)

    def test_fallo_no_rompe_las_bajas(self) -> None:
        players = MagicMock()
        players.ingest.side_effect = RuntimeError("HTTP 403")
        summary = _orchestrator(_make_session(), player_stats_ingester=players).run()
        assert summary.player_stats is None
        assert summary.absences is not None
        assert any("flashscore_player_stats_ingester" in e for e in summary.errors)


class TestAbsencesStep:
    def test_pasa_los_parametros_de_bajas_y_guarda_el_resumen(self) -> None:
        absences_mock = _make_absences_ingester(fetched=3, saved=9)
        summary = _orchestrator(_make_session(), absences_ingester=absences_mock).run(
            absences_days_ahead=5, absences_max_matches=10,
        )
        absences_mock.ingest.assert_called_once_with(days_ahead=5, max_matches=10)
        assert summary.absences is not None and summary.absences.absences_saved == 9

    def test_fallo_en_bajas_no_rompe_el_resto(self) -> None:
        absences_mock = _make_absences_ingester()
        absences_mock.ingest.side_effect = RuntimeError("flashscore caído")
        summary = _orchestrator(_make_session(), absences_ingester=absences_mock).run()
        assert summary.stats is not None
        assert summary.absences is None
        assert any("match_absences_ingester" in e for e in summary.errors)


class TestFailureIsolation:
    def test_fallo_en_resultados_no_bloquea_stats(self) -> None:
        session = _make_session()
        fs_mock = MagicMock()
        fs_mock.update.side_effect = RuntimeError("feed caído")
        stats = _make_stats_ingester(inserted=8)
        summary = _orchestrator(session, fs_results_updater=fs_mock, stats_ingester=stats).run()
        assert summary.ok is False
        assert any("flashscore_results_updater" in e for e in summary.errors)
        assert summary.stats is not None and summary.stats.stats_inserted == 8
        session.rollback.assert_called_once()

    def test_dos_fallos_se_registran(self) -> None:
        session = _make_session()
        fs_mock = MagicMock()
        fs_mock.update.side_effect = RuntimeError("results boom")
        stats = MagicMock()
        stats.ingest_recent.side_effect = RuntimeError("stats boom")
        summary = _orchestrator(session, fs_results_updater=fs_mock, stats_ingester=stats).run()
        assert len(summary.errors) == 2
        assert summary.fs_results is None and summary.stats is None
        assert session.rollback.call_count == 2


class TestSummary:
    def test_duration_is_non_negative(self) -> None:
        s = DailyRefreshSummary(
            started_at=datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 5, 5, 12, 0, 30, tzinfo=timezone.utc),
        )
        assert s.duration_seconds == pytest.approx(30.0)

    def test_ok_is_true_when_no_errors(self) -> None:
        s = DailyRefreshSummary(started_at=NOW, finished_at=NOW)
        assert s.ok is True

    def test_ok_is_false_when_any_error(self) -> None:
        s = DailyRefreshSummary(started_at=NOW, finished_at=NOW, errors=["some failure"])
        assert s.ok is False
