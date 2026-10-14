"""Tests del ``DailyRefreshOrchestrator`` con mocks de los servicios."""

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
from backend.services.flashscore_stats_ingester import IngestResult
from backend.services.match_results_updater import LeagueUpdateResult, UpdateSummary
from backend.services.player_match_stats_ingester import PlayerStatsIngestResult


def _make_results_updater(updated: int = 3, not_found: int = 10) -> MagicMock:
    mock = MagicMock()
    now = datetime.now(timezone.utc)
    mock.update_all.return_value = UpdateSummary(
        started_at=now,
        finished_at=now,
        per_league=[
            LeagueUpdateResult(
                league_name="Test League",
                sport_key="test",
                events_fetched=updated + not_found,
                matches_updated=updated,
                matches_already_ok=2,
                matches_not_found=not_found,
                skipped_unmapped=0,
            ),
        ],
    )
    return mock


def _make_stats_ingester(matched: int = 2, inserted: int = 4) -> MagicMock:
    mock = MagicMock()
    now = datetime.now(timezone.utc)
    mock.ingest_recent.return_value = IngestResult(
        started_at=now,
        finished_at=now,
        matches_eligible=matched,
        matches_matched=matched,
        matches_unmatched=0,
        stats_inserted=inserted,
        stats_updated=0,
    )
    return mock


def _make_fixtures_ingester() -> MagicMock:
    mock = MagicMock()
    now = datetime.now(timezone.utc)
    mock.ingest.return_value = FlashscoreFixturesIngestResult(started_at=now, finished_at=now)
    return mock


def _make_player_stats_ingester() -> MagicMock:
    mock = MagicMock()
    now = datetime.now(timezone.utc)
    mock.ingest.return_value = PlayerStatsIngestResult(started_at=now, finished_at=now)
    return mock


def _make_session() -> MagicMock:
    s = MagicMock()
    s.commit.return_value = None
    s.rollback.return_value = None
    return s


def _orchestrator(session: MagicMock, **kwargs: Any) -> DailyRefreshOrchestrator:
    fixtures = kwargs.pop("fixtures_ingester", _make_fixtures_ingester())
    player = kwargs.pop("player_stats_ingester", _make_player_stats_ingester())
    return DailyRefreshOrchestrator(
        session=session,
        fixtures_ingester=fixtures,
        player_stats_ingester=player,
        **kwargs,
    )


class TestHappyPath:
    def test_runs_both_steps_and_commits(self) -> None:
        session = _make_session()
        results_mock = _make_results_updater(updated=5)
        stats_mock = _make_stats_ingester(matched=3, inserted=6)
        fixtures_mock = _make_fixtures_ingester()

        orchestrator = _orchestrator(
            session,
            results_updater=results_mock,
            stats_ingester=stats_mock,
            fixtures_ingester=fixtures_mock,
        )
        summary = orchestrator.run(results_max_pages=2, stats_days_back=10)

        fixtures_mock.ingest.assert_called_once_with(days_ahead=7, only_top5=True)
        results_mock.update_all.assert_called_once_with(max_pages=2)
        stats_mock.ingest_recent.assert_called_once_with(
            days_back=10, only_top5=True, limit_matches=None,
        )
        assert session.commit.call_count == 5
        session.rollback.assert_not_called()

        assert summary.ok is True
        assert summary.fixtures is not None
        assert summary.results is not None and summary.results.total_updated == 5
        assert summary.stats is not None and summary.stats.stats_inserted == 6
        assert summary.duration_seconds >= 0

    def test_passes_stats_filters_through(self) -> None:
        session = _make_session()
        results_mock = _make_results_updater()
        stats_mock = _make_stats_ingester()
        fixtures_mock = _make_fixtures_ingester()
        orchestrator = _orchestrator(
            session,
            results_updater=results_mock,
            stats_ingester=stats_mock,
            fixtures_ingester=fixtures_mock,
        )
        orchestrator.run(stats_only_top5=False, stats_limit=5)
        fixtures_mock.ingest.assert_called_once_with(days_ahead=7, only_top5=False)
        stats_mock.ingest_recent.assert_called_once_with(
            days_back=120, only_top5=False, limit_matches=5,
        )


class TestFailureIsolation:
    def test_results_failure_does_not_block_stats(self) -> None:
        session = _make_session()
        results_mock = MagicMock()
        results_mock.update_all.side_effect = RuntimeError("network down")
        stats_mock = _make_stats_ingester(matched=4, inserted=8)
        fixtures_mock = _make_fixtures_ingester()

        orchestrator = _orchestrator(
            session,
            results_updater=results_mock,
            stats_ingester=stats_mock,
            fixtures_ingester=fixtures_mock,
        )
        summary = orchestrator.run()

        assert summary.ok is False
        assert any("match_results_updater" in e for e in summary.errors)
        stats_mock.ingest_recent.assert_called_once()
        assert summary.stats is not None and summary.stats.stats_inserted == 8
        session.rollback.assert_called_once()
        assert session.commit.call_count == 4

    def test_stats_failure_does_not_break_results(self) -> None:
        session = _make_session()
        results_mock = _make_results_updater(updated=2)
        stats_mock = MagicMock()
        stats_mock.ingest_recent.side_effect = ValueError("bad payload")
        fixtures_mock = _make_fixtures_ingester()

        orchestrator = _orchestrator(
            session,
            results_updater=results_mock,
            stats_ingester=stats_mock,
            fixtures_ingester=fixtures_mock,
        )
        summary = orchestrator.run()

        assert summary.ok is False
        assert any("flashscore_stats_ingester" in e for e in summary.errors)
        assert summary.results is not None and summary.results.total_updated == 2
        assert session.commit.call_count == 4
        session.rollback.assert_called_once()

    def test_both_failures_recorded(self) -> None:
        session = _make_session()
        results_mock = MagicMock()
        results_mock.update_all.side_effect = RuntimeError("results boom")
        stats_mock = MagicMock()
        stats_mock.ingest_recent.side_effect = RuntimeError("stats boom")
        fixtures_mock = _make_fixtures_ingester()

        orchestrator = _orchestrator(
            session,
            results_updater=results_mock,
            stats_ingester=stats_mock,
            fixtures_ingester=fixtures_mock,
        )
        summary = orchestrator.run()

        assert summary.ok is False
        assert len(summary.errors) == 2
        assert summary.results is None
        assert summary.stats is None
        assert session.rollback.call_count == 2
        assert session.commit.call_count == 3


class TestSummary:
    def test_duration_is_non_negative(self) -> None:
        s = DailyRefreshSummary(
            started_at=datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 5, 5, 12, 0, 30, tzinfo=timezone.utc),
        )
        assert s.duration_seconds == pytest.approx(30.0)

    def test_ok_is_true_when_no_errors(self) -> None:
        s = DailyRefreshSummary(
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
        assert s.ok is True

    def test_ok_is_false_when_any_error(self) -> None:
        s = DailyRefreshSummary(
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            errors=["some failure"],
        )
        assert s.ok is False
