"""Tests del emparejado de nombres de equipo Flashscore ↔ Sofascore.

Parejas reales sacadas de la BD el 2026-10-14 (antes del reset).
"""

from __future__ import annotations

import pytest

from backend.services.team_name_matching import (
    basic_names_match,
    basic_normalize_team_name,
    find_matching_team_id,
    normalize_team_name,
    player_names_match,
    team_names_match,
)


class TestJugadoresMismaPersona:
    # Parejas reales Sofascore/Winamax ↔ Flashscore sacadas de la BD el 2026-10-15.
    @pytest.mark.parametrize(
        ("sofascore", "flashscore"),
        [
            ("Nico Paz", "Paz Nicolás"),
            ("Oli McBurnie", "McBurnie Oliver"),
            ("Gleison Bremer", "Bremer"),
            ("Deniz Undav", "Undav Deniz"),
            ("Ísak Bergmann Jóhannesson", "Johannesson Isak"),
            ("Kerim Alajbegović", "Alajbegovic Kerim-Sam"),
            ("Willian Pacho", "Pacho Tenorio Willian Joel"),
            ("Dani Lorenzo", "Lorenzo Daniel"),
        ],
    )
    def test_encajan(self, sofascore: str, flashscore: str) -> None:
        assert player_names_match(sofascore, flashscore)
        assert player_names_match(flashscore, sofascore)


class TestJugadoresDistintos:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("Gabriel Jesus", "Gabriel Magalhães"),
            ("Nico Williams", "Nico Paz"),
            ("Jo Paz", "Josep Paz"),  # "jo" es demasiado corto para contar como diminutivo
            ("", "Paz Nicolás"),
        ],
    )
    def test_no_encajan(self, a: str, b: str) -> None:
        assert not player_names_match(a, b)


class TestParejasReales:
    @pytest.mark.parametrize(
        ("flashscore", "sofascore"),
        [
            ("Manchester Utd", "Manchester United"),
            ("Brighton", "Brighton & Hove Albion"),
            ("Liverpool", "Liverpool FC"),
            ("PSG", "Paris Saint-Germain"),
            ("Lyon", "Olympique Lyonnais"),
            ("Marseille", "Olympique de Marseille"),
            ("Brest", "Stade Brestois"),
            ("Bayern Munich", "FC Bayern München"),
            ("B. Monchengladbach", "Borussia M'gladbach"),
            ("Bayer Leverkusen", "Bayer 04 Leverkusen"),
            ("FC Koln", "1. FC Köln"),
            ("Mainz", "1. FSV Mainz 05"),
            ("Union Berlin", "1. FC Union Berlin"),
            ("AC Milan", "Milan"),
            ("Napoli", "SSC Napoli"),
            ("Atl. Madrid", "Atlético Madrid"),
            ("Ath Bilbao", "Athletic Club"),
            ("Dep. A Coruna", "Deportivo de A Coruña"),
            ("Racing Santander", "Real Racing Club"),
            ("Alaves", "Deportivo Alavés"),
        ],
    )
    def test_mismo_club(self, flashscore, sofascore):
        assert team_names_match(flashscore, sofascore)


class TestNoSonElMismoEquipo:
    def test_filial_no_es_el_primer_equipo(self):
        assert not team_names_match("Real Sociedad B", "Real Sociedad")
        assert not team_names_match("Celta Vigo B", "Celta Vigo")

    def test_femenino_y_sub_n_no_son_el_primer_equipo(self):
        assert not team_names_match("Bayern Munich W", "FC Bayern München")
        assert not team_names_match("Arsenal U18", "Arsenal")

    def test_clubes_distintos(self):
        assert not team_names_match("Real Madrid", "Real Betis")
        assert not team_names_match("Manchester Utd", "Manchester City")


class TestFindMatchingTeamId:
    CANDIDATOS = {1: "Paris Saint-Germain", 2: "Paris FC", 3: "Olympique Lyonnais"}

    def test_coincidencia_exacta_gana_a_la_inclusion(self):
        # "PSG" → "paris saint germain"; "Paris FC" → "paris" también está incluido.
        assert find_matching_team_id("PSG", self.CANDIDATOS) == 1

    def test_ambiguo_devuelve_none(self):
        candidatos = {1: "Manchester United", 2: "Manchester City"}
        assert find_matching_team_id("Manchester", candidatos) is None

    def test_sin_candidato_devuelve_none(self):
        assert find_matching_team_id("Albacete", self.CANDIDATOS) is None


class TestNormalize:
    def test_quita_tildes_afijos_y_puntuacion(self):
        assert normalize_team_name("1. FC Köln") == "koln"
        assert normalize_team_name("Borussia M'gladbach") == "borussia mgladbach"

    def test_vacio(self):
        assert normalize_team_name(None) == ""


class TestBasicNormalizeTeamName:
    def test_lowercase(self) -> None:
        assert basic_normalize_team_name("Real Madrid") == "real madrid"

    def test_strips_diacritics(self) -> None:
        assert basic_normalize_team_name("Atlético") == "atletico"

    def test_drops_common_suffixes(self) -> None:
        assert basic_normalize_team_name("Barcelona FC") == "barcelona"
        assert basic_normalize_team_name("Hoffenheim 1899") == "hoffenheim 1899"
        assert basic_normalize_team_name("SV 07 Elversberg") == "elversberg"
        assert basic_normalize_team_name("Bayer 04 Leverkusen") == "bayer leverkusen"
        assert basic_normalize_team_name("AC Milan") == "milan"

    def test_compresses_whitespace(self) -> None:
        assert basic_normalize_team_name("  PSG   FC  ") == "psg"

    def test_empty_string(self) -> None:
        assert basic_normalize_team_name("") == ""


class TestBasicNamesMatch:
    def test_exact(self) -> None:
        assert basic_names_match("real madrid", "real madrid") is True

    def test_substring_either_way(self) -> None:
        # "Real Madrid CF" en BD vs "Real Madrid" en Winamax
        assert basic_names_match("real madrid", "real madrid cf") is True
        assert basic_names_match("real madrid cf", "real madrid") is True

    def test_unrelated(self) -> None:
        assert basic_names_match("real madrid", "barcelona") is False

    def test_empty_inputs(self) -> None:
        assert basic_names_match("", "barcelona") is False
        assert basic_names_match("barcelona", "") is False
