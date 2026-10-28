"""Tests de la lógica pura de resultados desde Flashscore (sin BD ni red)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.scrapers import flashscore as fs
from backend.scrapers.flashscore import FlashscoreMatch
from backend.services.flashscore_results_updater import (
    FinishedResult,
    descargado_en_directo,
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


def _feed(ab: str, *, ai: str | None = None) -> str:
    extra = f"AI÷{ai}¬" if ai else ""
    payload = f"ZA÷ITALY: Serie A¬~AA÷abc¬AD÷{int(KICKOFF.timestamp())}¬AB÷{ab}¬AG÷1¬AH÷0¬{extra}~"
    return payload


class TestEstadoDelFeed:
    @pytest.fixture
    def feed(self, monkeypatch):
        def _fetch(ab: str, ai: str | None = None) -> FlashscoreMatch:
            monkeypatch.setattr(fs, "_http_get", lambda _path: _feed(ab, ai=ai))
            return fs.fetch_fixtures_feed()[0]
        return _fetch

    def test_ab_3_es_terminado(self, feed):
        assert feed("3").is_finished

    def test_en_juego_con_ai_y_no_es_terminado(self, feed):
        # Lecce–Juventus (25/10) se guardó 0-0 en el minuto 30 por este campo.
        assert not feed("2", ai="y").is_finished

    def test_programado_no_es_terminado(self, feed):
        assert not feed("1").is_finished


class TestDescargadoEnDirecto:
    def test_stats_a_los_30_minutos_es_directo(self):
        assert descargado_en_directo(KICKOFF, KICKOFF + timedelta(minutes=30))

    def test_stats_a_las_dos_horas_no_es_directo(self):
        assert not descargado_en_directo(KICKOFF, KICKOFF + timedelta(hours=2))

    def test_basta_una_descarga_en_directo(self):
        assert descargado_en_directo(KICKOFF, KICKOFF + timedelta(days=1), KICKOFF + timedelta(minutes=50))

    def test_sin_descargas_no_es_directo(self):
        assert not descargado_en_directo(KICKOFF, None, None)
