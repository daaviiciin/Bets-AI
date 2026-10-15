"""Tests para normalización Winamax → claves tipo Odds API."""

from __future__ import annotations

from backend.services.winamax_normalization import (
    canonical_market_key,
    normalize_btts_outcome,
    normalize_double_chance_outcome,
    normalize_h2h_outcome,
    normalize_match_outcome,
    normalize_totals_outcome,
)


def test_canonical_keys() -> None:
    assert canonical_market_key("match_winner") == "h2h"
    assert canonical_market_key("match_totals") == "totals"


def test_h2h_draw() -> None:
    assert normalize_h2h_outcome("Empate", home_team="A", away_team="B") == "Draw"


def test_totals_over_es() -> None:
    assert normalize_totals_outcome("Más de 2,5") == "Over"


def test_btts_si() -> None:
    assert normalize_btts_outcome("Sí") == "Yes"


def test_double_chance_1x() -> None:
    o = normalize_double_chance_outcome("1X", home_team="Lakers", away_team="Celtics")
    assert "Draw" in o and "Lakers" in o


class TestNormalizeMatchOutcome:
    def test_par_e_impar_conservan_su_etiqueta_y_no_colapsan_en_under(self) -> None:
        par = normalize_match_outcome(
            "match_total_goals_odd_even", "Par", home_team="A", away_team="B",
        )
        impar = normalize_match_outcome(
            "match_total_goals_odd_even", "Impar", home_team="A", away_team="B",
        )
        assert (par, impar) == ("Par", "Impar")

    def test_mitad_con_mas_goles_conserva_su_etiqueta(self) -> None:
        etiquetas = ["1ª mitad", "2ª mitad", "Igual"]
        nombres = {
            normalize_match_outcome("match_highest_scoring_half", e, home_team="A", away_team="B")
            for e in etiquetas
        }
        assert nombres == set(etiquetas)

    def test_totales_siguen_normalizando_a_over_under(self) -> None:
        assert normalize_match_outcome(
            "match_corners_totals", "Menos de 9,5", home_team="A", away_team="B",
        ) == "Under"

    def test_h2h_delega_en_su_normalizador(self) -> None:
        assert normalize_match_outcome("h2h", "Empate", home_team="A", away_team="B") == "Draw"
