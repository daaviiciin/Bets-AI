"""Tests del mapeo nombre de competición Flashscore → ``League.sport_key``.

Los nombres son reales, sacados del feed de fixtures del 2026-10-14.
"""

from __future__ import annotations

import pytest

from backend.services.flashscore_fixtures_ingester import resolve_fixtures_sport_key


class TestLigasPropias:
    @pytest.mark.parametrize(
        ("nombre", "esperado"),
        [
            ("SPAIN: LaLiga", "soccer_spain_la_liga"),
            ("ENGLAND: Premier League", "soccer_epl"),
            ("GERMANY: Bundesliga", "soccer_germany_bundesliga"),
            ("ITALY: Serie A", "soccer_italy_serie_a"),
            ("FRANCE: Ligue 1", "soccer_france_ligue_one"),
        ],
    )
    def test_top5_con_nombre_exacto(self, nombre, esperado):
        assert resolve_fixtures_sport_key(nombre, only_top5=True) == esperado

    def test_champions_con_fase_se_reconoce(self):
        assert resolve_fixtures_sport_key(
            "EUROPE: Champions League - League phase", only_top5=False,
        ) == "soccer_uefa_champs_league"

    def test_competiciones_europeas_se_ignoran_si_solo_top5(self):
        assert resolve_fixtures_sport_key(
            "EUROPE: Champions League - League phase", only_top5=True,
        ) is None


class TestCompeticionesQueAntesSeColaban:
    @pytest.mark.parametrize(
        "nombre",
        [
            "ENGLAND: Premier League 2",
            "ENGLAND: Premier League U18",
            "SPAIN: LaLiga2",
            "GERMANY: Bundesliga Women",
            "ITALY: Serie A Women",
            "MAURITANIA: Ligue 1",
            "EUROPE: Premier League International Cup",
        ],
    )
    def test_no_son_top5(self, nombre):
        assert resolve_fixtures_sport_key(nombre, only_top5=False) is None

    @pytest.mark.parametrize(
        "nombre",
        [
            "ASIA: AFC Champions League - League phase",
            "ASIA: AFC Champions League 2",
            "ASIA: Gulf Club Champions League",
            "AFRICA: CAF Champions League - Qualification",
            "EUROPE: UEFA Youth League - League phase",
        ],
    )
    def test_no_son_champions_de_la_uefa(self, nombre):
        assert resolve_fixtures_sport_key(nombre, only_top5=False) is None

    def test_nombre_vacio(self):
        assert resolve_fixtures_sport_key(None, only_top5=False) is None
        assert resolve_fixtures_sport_key("", only_top5=False) is None
