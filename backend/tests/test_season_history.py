"""Tests del filtrado de partidos jugados para el histórico (sin BD)."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.scrapers.flashscore import FlashscoreMatch
from backend.services.season_history_ingester import storable_finished_matches

KICKOFF = datetime(2026, 10, 10, 19, 0, tzinfo=timezone.utc)


def _fm(match_id: str = "Es8BsIUP", **overrides) -> FlashscoreMatch:
    base = {
        "match_id": match_id,
        "home_team_name": "Barcelona",
        "away_team_name": "Getafe",
        "scheduled_at": KICKOFF,
        "score_home": 3,
        "score_away": 0,
        "is_finished": True,
        "raw": {"PX": "SKbpVP5K", "PY": "dboeiWOt"},
    }
    base.update(overrides)
    return FlashscoreMatch(**base)


class TestStorableFinishedMatches:
    def test_partido_completo_se_guarda(self):
        assert storable_finished_matches([_fm()]) == [_fm()]

    def test_sin_ids_de_equipo_no_se_guarda(self):
        assert storable_finished_matches([_fm(raw={})]) == []

    def test_sin_marcador_no_se_guarda(self):
        assert storable_finished_matches([_fm(score_away=None)]) == []

    def test_no_terminado_no_se_guarda(self):
        assert storable_finished_matches([_fm(is_finished=False)]) == []

    def test_sin_fecha_no_se_guarda(self):
        assert storable_finished_matches([_fm(scheduled_at=None)]) == []
