"""Tests de la lógica pura de resultados desde Flashscore (sin BD ni red)."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.scrapers.flashscore import FlashscoreMatch
from backend.services.flashscore_results_updater import (
    FinishedResult,
    finished_results_by_id,
)

KICKOFF = datetime(2026, 10, 14, 19, 0, tzinfo=timezone.utc)


def _fm(match_id: str, home: int | None, away: int | None, *, finished: bool) -> FlashscoreMatch:
    return FlashscoreMatch(
        match_id=match_id,
        home_team_name="Roma",
        away_team_name="Real Madrid",
        scheduled_at=KICKOFF,
        score_home=home,
        score_away=away,
        is_finished=finished,
    )


class TestFinishedResultsById:
    def test_partido_terminado_con_marcador(self):
        res = finished_results_by_id([_fm("4j50U1OM", 1, 2, finished=True)], {"4j50U1OM"})
        assert res == {"4j50U1OM": FinishedResult(1, 2, KICKOFF)}

    def test_empate_a_cero_cuenta(self):
        res = finished_results_by_id([_fm("a", 0, 0, finished=True)], {"a"})
        assert res["a"].home_score == 0 and res["a"].away_score == 0

    def test_partido_en_juego_no_se_toca(self):
        assert finished_results_by_id([_fm("a", 1, 0, finished=False)], {"a"}) == {}

    def test_terminado_sin_marcador_no_se_toca(self):
        assert finished_results_by_id([_fm("a", None, None, finished=True)], {"a"}) == {}

    def test_ignora_partidos_que_no_estan_en_bd(self):
        assert finished_results_by_id([_fm("otro", 3, 1, finished=True)], {"a"}) == {}
