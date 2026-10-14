"""Tests del ``MatchResultsUpdater``: helpers puros y lógica de matching."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from backend.services.match_results_updater import (
    _find_match_by_pair,
    _index_matches_by_team_pair,
    _names_match,
    _normalize,
    _scores,
    _start_datetime,
    _team_ext_id,
)


class TestNormalize:
    def test_lowercase(self) -> None:
        assert _normalize("Real Madrid") == "real madrid"

    def test_strips_diacritics(self) -> None:
        assert _normalize("Atlético") == "atletico"

    def test_drops_common_suffixes(self) -> None:
        assert _normalize("Barcelona FC") == "barcelona"
        assert _normalize("Hoffenheim 1899") == "hoffenheim 1899"
        assert _normalize("SV 07 Elversberg") == "elversberg"
        assert _normalize("Bayer 04 Leverkusen") == "bayer leverkusen"
        assert _normalize("AC Milan") == "milan"

    def test_compresses_whitespace(self) -> None:
        assert _normalize("  PSG   FC  ") == "psg"

    def test_empty_string(self) -> None:
        assert _normalize("") == ""


class TestNamesMatch:
    def test_exact(self) -> None:
        assert _names_match("real madrid", "real madrid") is True

    def test_substring_either_way(self) -> None:
        # "Real Madrid CF" en BD vs "Real Madrid" en Sofascore
        assert _names_match("real madrid", "real madrid cf") is True
        assert _names_match("real madrid cf", "real madrid") is True

    def test_unrelated(self) -> None:
        assert _names_match("real madrid", "barcelona") is False

    def test_empty_inputs(self) -> None:
        assert _names_match("", "barcelona") is False
        assert _names_match("barcelona", "") is False


class TestScoresHelper:
    def test_extracts_current_scores(self) -> None:
        event = {"homeScore": {"current": 2}, "awayScore": {"current": 1}}
        assert _scores(event) == (2, 1)

    def test_returns_none_when_missing(self) -> None:
        assert _scores({"homeScore": {"current": 2}}) is None
        assert _scores({}) is None

    def test_returns_none_when_invalid(self) -> None:
        assert _scores({"homeScore": {"current": "x"}, "awayScore": {"current": 1}}) is None


class TestStartDatetimeHelper:
    def test_unix_to_utc(self) -> None:
        target = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        ts = int(target.timestamp())
        d = _start_datetime({"startTimestamp": ts})
        assert d == target

    def test_missing_returns_none(self) -> None:
        assert _start_datetime({}) is None

    def test_invalid_returns_none(self) -> None:
        assert _start_datetime({"startTimestamp": "not-a-number"}) is None


class TestTeamExtIdHelper:
    def test_extracts_id_as_string(self) -> None:
        assert _team_ext_id({"homeTeam": {"id": 42}}, "home") == "42"

    def test_returns_none_when_no_id(self) -> None:
        assert _team_ext_id({"homeTeam": {}}, "home") is None
        assert _team_ext_id({}, "home") is None


class TestIndexMatchesByPair:
    def test_groups_by_pair(self) -> None:
        m1 = MagicMock(home_team_id=1, away_team_id=2)
        m2 = MagicMock(home_team_id=1, away_team_id=2)
        m3 = MagicMock(home_team_id=3, away_team_id=4)
        index = _index_matches_by_team_pair([m1, m2, m3])
        assert len(index[(1, 2)]) == 2
        assert len(index[(3, 4)]) == 1
        assert (5, 6) not in index


class TestFindMatchByPair:
    @pytest.fixture
    def base_match(self) -> MagicMock:
        m = MagicMock()
        m.home_team_id = 1
        m.away_team_id = 2
        m.commence_time = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        return m

    def test_finds_within_window(self, base_match: MagicMock) -> None:
        index = _index_matches_by_team_pair([base_match])
        result = _find_match_by_pair(
            index, 1, 2,
            datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc),
        )
        assert result is base_match

    def test_returns_none_outside_window(self, base_match: MagicMock) -> None:
        index = _index_matches_by_team_pair([base_match])
        result = _find_match_by_pair(
            index, 1, 2,
            datetime(2026, 1, 20, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert result is None

    def test_returns_none_no_pair(self, base_match: MagicMock) -> None:
        index = _index_matches_by_team_pair([base_match])
        result = _find_match_by_pair(
            index, 99, 100,
            datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert result is None

    def test_picks_closest_when_multiple(self) -> None:
        far = MagicMock(
            home_team_id=1, away_team_id=2,
            commence_time=datetime(2026, 1, 15, 6, 0, 0, tzinfo=timezone.utc),
        )
        close = MagicMock(
            home_team_id=1, away_team_id=2,
            commence_time=datetime(2026, 1, 15, 13, 0, 0, tzinfo=timezone.utc),
        )
        index = _index_matches_by_team_pair([far, close])
        result = _find_match_by_pair(
            index, 1, 2,
            datetime(2026, 1, 15, 13, 30, 0, tzinfo=timezone.utc),
        )
        assert result is close
