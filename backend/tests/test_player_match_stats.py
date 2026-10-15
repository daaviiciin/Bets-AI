"""Tests del parser Sofascore y de los helpers del ingester de player stats."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.scrapers.sofascore import (
    parse_event_card_counts,
    parse_event_lineups,
)
from backend.services.player_match_stats_ingester import _apply_stats


# ---------------------------------------------------------------------------
# Payload sintético que imita la estructura real de Sofascore /event/{id}/lineups
# ---------------------------------------------------------------------------


SAMPLE_LINEUPS = {
    "home": {
        "formation": "4-3-3",
        "players": [
            {
                "player": {
                    "id": 1001,
                    "name": "Test Forward",
                    "shortName": "T. Forward",
                    "position": "F",
                },
                "shirtNumber": 9,
                "position": "F",
                "substitute": False,
                "statistics": {
                    "minutesPlayed": 90,
                    "goals": 1,
                    "totalShots": 4,
                    "onTargetScoringAttempt": 2,
                    "shotOffTarget": 1,
                    "blockedScoringAttempt": 1,
                    "expectedGoals": 0.7191,
                    "expectedAssists": 0.0211,
                    "rating": 8.0,
                    "totalPass": 46,
                    "accuratePass": 42,
                    "keyPass": 1,
                    "totalCross": 1,
                    "duelWon": 7,
                    "duelLost": 3,
                    "aerialWon": 2,
                    "wonContest": 1,
                    "totalContest": 2,
                    "fouls": 1,
                    "wasFouled": 2,
                    "totalOffside": 1,
                    "touches": 67,
                    "interceptionWon": 0,
                    "totalClearance": 2,
                    "totalTackle": 3,
                    "outfielderBlock": 0,
                },
            },
            {
                "player": {
                    "id": 1002,
                    "name": "Test Goalkeeper",
                    "shortName": "T. GK",
                    "position": "G",
                },
                "shirtNumber": 1,
                "position": "G",
                "substitute": False,
                "statistics": {
                    "minutesPlayed": 90,
                    "saves": 5,
                    "goalsPrevented": 1.23,
                    "rating": 7.5,
                    "totalPass": 30,
                    "accuratePass": 25,
                },
            },
            {
                # Suplente que no salió del banquillo: minutesPlayed ausente.
                "player": {"id": 1003, "name": "Bench Warmer", "position": "F"},
                "shirtNumber": 22,
                "position": "F",
                "substitute": True,
                "statistics": {},
            },
            {
                # Suplente que entró 20 min.
                "player": {"id": 1004, "name": "Sub In", "position": "M"},
                "shirtNumber": 18,
                "position": "M",
                "substitute": True,
                "statistics": {
                    "minutesPlayed": 20,
                    "totalShots": 1,
                    "rating": 6.5,
                },
            },
        ],
    },
    "away": {
        "formation": "4-4-2",
        "players": [
            {
                "player": {"id": 2001, "name": "Away Striker", "position": "F"},
                "shirtNumber": 10,
                "position": "F",
                "substitute": False,
                "statistics": {
                    "minutesPlayed": 75,
                    "goals": 0,
                    "totalShots": 2,
                    "onTargetScoringAttempt": 1,
                    "rating": 6.8,
                },
            },
        ],
    },
}


SAMPLE_INCIDENTS = {
    "incidents": [
        {
            "incidentType": "card",
            "incidentClass": "yellow",
            "player": {"id": 1001},
        },
        {
            "incidentType": "card",
            "incidentClass": "yellow",
            "player": {"id": 2001},
        },
        {
            "incidentType": "card",
            "incidentClass": "yellowRed",
            "player": {"id": 2001},
        },
        {
            "incidentType": "card",
            "incidentClass": "red",
            "player": {"id": 1004},
        },
        # Otros eventos (goles, sustituciones) que NO deben contar.
        {"incidentType": "goal", "player": {"id": 1001}},
        {"incidentType": "substitution", "player": {"id": 1003}},
    ]
}


# ---------------------------------------------------------------------------
# parse_event_lineups
# ---------------------------------------------------------------------------


class TestParseEventLineups:
    def test_returns_home_and_away_keys(self) -> None:
        out = parse_event_lineups(SAMPLE_LINEUPS)
        assert set(out.keys()) == {"home", "away"}

    def test_skips_bench_players_without_minutes(self) -> None:
        out = parse_event_lineups(SAMPLE_LINEUPS)
        # Home arranca con 4 entradas, pero "Bench Warmer" (id=1003) no jugó.
        ext_ids = [p["external_id"] for p in out["home"]]
        assert "1001" in ext_ids
        assert "1004" in ext_ids
        assert "1003" not in ext_ids
        # Total: 3 jugadores con minutos en el local.
        assert len(out["home"]) == 3

    def test_starter_flag(self) -> None:
        out = parse_event_lineups(SAMPLE_LINEUPS)
        forward = next(p for p in out["home"] if p["external_id"] == "1001")
        sub = next(p for p in out["home"] if p["external_id"] == "1004")
        assert forward["is_starter"] is True
        assert forward["is_substitute"] is False
        assert sub["is_starter"] is False
        assert sub["is_substitute"] is True

    def test_maps_offensive_stats(self) -> None:
        out = parse_event_lineups(SAMPLE_LINEUPS)
        forward = next(p for p in out["home"] if p["external_id"] == "1001")
        s = forward["stats"]
        assert s["goals"] == 1
        assert s["shots"] == 4
        assert s["shots_on_target"] == 2
        assert s["shots_off_target"] == 1
        assert s["shots_blocked"] == 1
        assert s["minutes_played"] == 90
        assert s["passes_total"] == 46
        assert s["passes_accurate"] == 42
        assert s["key_passes"] == 1
        assert s["fouls_committed"] == 1
        assert s["fouls_drawn"] == 2
        assert s["offsides"] == 1

    def test_maps_decimal_stats_as_floats(self) -> None:
        out = parse_event_lineups(SAMPLE_LINEUPS)
        forward = next(p for p in out["home"] if p["external_id"] == "1001")
        assert forward["stats"]["expected_goals"] == pytest.approx(0.7191)
        assert forward["stats"]["expected_assists"] == pytest.approx(0.0211)
        assert forward["stats"]["rating"] == pytest.approx(8.0)

    def test_maps_goalkeeper_specific_stats(self) -> None:
        out = parse_event_lineups(SAMPLE_LINEUPS)
        gk = next(p for p in out["home"] if p["external_id"] == "1002")
        assert gk["stats"]["saves"] == 5
        assert gk["stats"]["goals_prevented"] == pytest.approx(1.23)
        assert gk["stats"]["rating"] == pytest.approx(7.5)
        # Sin tiros: la clave no existe.
        assert "shots" not in gk["stats"]

    def test_unknown_keys_are_dropped(self) -> None:
        # Inyectamos una stat desconocida — debería ser ignorada.
        payload = {
            "home": {
                "players": [
                    {
                        "player": {"id": 9001, "name": "X"},
                        "position": "M",
                        "substitute": False,
                        "statistics": {
                            "minutesPlayed": 60,
                            "totalShots": 2,
                            "completelyBogusKey": 42,
                        },
                    }
                ]
            },
            "away": {"players": []},
        }
        out = parse_event_lineups(payload)
        assert "completelyBogusKey" not in out["home"][0]["stats"]
        assert out["home"][0]["stats"]["shots"] == 2

    def test_external_id_is_string(self) -> None:
        out = parse_event_lineups(SAMPLE_LINEUPS)
        for side in ("home", "away"):
            for p in out[side]:
                assert isinstance(p["external_id"], str)

    def test_empty_payload(self) -> None:
        assert parse_event_lineups({}) == {"home": [], "away": []}

    def test_player_without_id_is_skipped(self) -> None:
        # Un entry sin player.id debe ignorarse silenciosamente.
        payload = {
            "home": {
                "players": [
                    {"player": {"name": "Anonymous"}, "statistics": {"minutesPlayed": 90}},
                    {"player": {"id": 5001, "name": "Real"}, "position": "F",
                     "substitute": False, "statistics": {"minutesPlayed": 90}},
                ]
            },
            "away": {"players": []},
        }
        out = parse_event_lineups(payload)
        assert len(out["home"]) == 1
        assert out["home"][0]["external_id"] == "5001"


# ---------------------------------------------------------------------------
# parse_event_card_counts
# ---------------------------------------------------------------------------


class TestParseEventCardCounts:
    def test_empty_payload(self) -> None:
        assert parse_event_card_counts({}) == {}
        assert parse_event_card_counts({"incidents": []}) == {}

    def test_counts_yellows(self) -> None:
        out = parse_event_card_counts(SAMPLE_INCIDENTS)
        assert out["1001"]["yellow_cards"] == 1
        assert out["1001"]["red_cards"] == 0

    def test_counts_yellow_red_as_red(self) -> None:
        out = parse_event_card_counts(SAMPLE_INCIDENTS)
        # Player 2001 tuvo amarilla + amarilla-roja
        assert out["2001"]["yellow_cards"] == 1
        assert out["2001"]["red_cards"] == 1

    def test_counts_direct_red(self) -> None:
        out = parse_event_card_counts(SAMPLE_INCIDENTS)
        assert out["1004"]["red_cards"] == 1
        assert out["1004"]["yellow_cards"] == 0

    def test_ignores_non_card_events(self) -> None:
        out = parse_event_card_counts(SAMPLE_INCIDENTS)
        assert "1003" not in out  # solo había una sustitución


# ---------------------------------------------------------------------------
# _apply_stats helper
# ---------------------------------------------------------------------------


class _Row:
    """Stub que imita PlayerMatchStats para no necesitar BD."""

    def __init__(self) -> None:
        self.goals: int | None = None
        self.shots: int | None = None
        self.shots_on_target: int | None = None
        self.minutes_played: int | None = None
        self.expected_goals: Decimal | None = None
        self.expected_assists: Decimal | None = None
        self.rating: Decimal | None = None
        self.yellow_cards: int | None = None
        self.red_cards: int | None = None


class TestApplyStats:
    def test_assigns_int_fields(self) -> None:
        row = _Row()
        _apply_stats(row, {"goals": 2, "shots": 5, "minutes_played": 90})
        assert row.goals == 2
        assert row.shots == 5
        assert row.minutes_played == 90

    def test_assigns_decimal_fields(self) -> None:
        row = _Row()
        _apply_stats(row, {"expected_goals": 0.7191, "rating": 7.5})
        assert row.expected_goals == Decimal("0.7191")
        assert row.rating == Decimal("7.5")

    def test_unknown_keys_silently_dropped(self) -> None:
        row = _Row()
        _apply_stats(row, {"goals": 1, "fake_metric": 999})
        assert row.goals == 1
        assert not hasattr(row, "fake_metric") or getattr(row, "fake_metric", None) != 999

    def test_none_values_skipped(self) -> None:
        row = _Row()
        row.goals = 1  # valor previo
        _apply_stats(row, {"goals": None, "shots": 3})
        assert row.goals == 1  # no se sobreescribe con None
        assert row.shots == 3

    def test_invalid_int_silently_skipped(self) -> None:
        row = _Row()
        _apply_stats(row, {"goals": "not-a-number", "shots": 4})
        assert row.goals is None
        assert row.shots == 4

    def test_yellow_red_cards_applied(self) -> None:
        row = _Row()
        _apply_stats(row, {"yellow_cards": 1, "red_cards": 0})
        assert row.yellow_cards == 1
        assert row.red_cards == 0
