"""Tests para normalización Winamax → claves tipo Odds API."""

from __future__ import annotations

from backend.services.winamax_normalization import (
    canonical_market_key,
    completar_por_eliminacion,
    highest_scoring_half_side,
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


class TestHighestScoringHalfSide:
    def test_etiquetas_de_winamax(self) -> None:
        assert [highest_scoring_half_side(e) for e in ("1ª mitad", "Igual", "2ª mitad")] == [
            "1h", "tie", "2h",
        ]

    def test_variantes_en_ingles_y_sin_ordinal(self) -> None:
        assert highest_scoring_half_side("2nd half") == "2h"
        assert highest_scoring_half_side("Primera mitad") == "1h"
        assert highest_scoring_half_side("Tie") == "tie"

    def test_etiqueta_desconocida_es_none(self) -> None:
        assert highest_scoring_half_side("Ninguna") is None


class TestNombresEnEspanol:
    """Winamax escribe muchos equipos en español y abreviados (casos reales del 18/11/2026)."""

    def test_1x2_abreviado(self) -> None:
        assert normalize_h2h_outcome("R. Madrid", home_team="Real Madrid", away_team="Celta Vigo") == "Real Madrid"
        assert normalize_h2h_outcome(
            "B. Múnich", home_team="FC Bayern München", away_team="1. FC Köln",
        ) == "FC Bayern München"

    def test_1x2_traducido(self) -> None:
        assert normalize_h2h_outcome("Nápoles", home_team="SSC Napoli", away_team="Torino") == "SSC Napoli"
        assert normalize_h2h_outcome(
            "Hamburgo", home_team="SV Werder Bremen", away_team="Hamburger SV",
        ) == "Hamburger SV"

    def test_doble_oportunidad_con_o_en_espanol(self) -> None:
        assert normalize_double_chance_outcome(
            "Leeds o empate", home_team="Chelsea", away_team="Leeds United",
        ) == "Draw or Leeds United"
        assert normalize_double_chance_outcome(
            "Werder o  Hamburgo", home_team="SV Werder Bremen", away_team="Hamburger SV",
        ) == "SV Werder Bremen or Hamburger SV"

    def test_doble_con_un_equipo_ambiguo_es_el_rival_del_otro(self) -> None:
        # Dos «Deportivo»: Alavés se reconoce, así que el otro es el visitante.
        assert normalize_double_chance_outcome(
            "Alavés o  Deportivo", home_team="Deportivo Alavés", away_team="Deportivo de A Coruña",
        ) == "Deportivo Alavés or Deportivo de A Coruña"

    def test_nombre_contenido_en_los_dos_equipos_no_va_al_local(self) -> None:
        # Antes «Deportivo» se guardaba como «Deportivo Alavés» y el 1X2 tenía dos locales.
        kw = {"home_team": "Deportivo Alavés", "away_team": "Deportivo de A Coruña"}
        assert normalize_h2h_outcome("Alavés", **kw) == "Deportivo Alavés"
        assert normalize_h2h_outcome("Deportivo", **kw) == "Deportivo"
        nombres = [normalize_h2h_outcome(x, **kw) for x in ("Alavés", "Empate", "Deportivo")]
        assert completar_por_eliminacion("h2h", nombres, **kw) == [
            "Deportivo Alavés", "Draw", "Deportivo de A Coruña",
        ]

    def test_nombre_exacto_gana_aunque_este_dentro_del_otro(self) -> None:
        assert normalize_h2h_outcome("Inter", home_team="Inter", away_team="Inter Miami") == "Inter"

    def test_nombre_ambiguo_no_se_adivina(self) -> None:
        assert normalize_double_chance_outcome(
            "Deportivo o empate", home_team="Deportivo Alavés", away_team="Deportivo de A Coruña",
        ) == "Deportivo o empate"


class TestCompletarPorEliminacion:
    def test_1x2_la_que_falta_es_la_no_reconocida(self) -> None:
        nombres = ["Niza", "Draw", "Paris Saint-Germain"]
        assert completar_por_eliminacion(
            "h2h", nombres, home_team="Nice", away_team="Paris Saint-Germain",
        ) == ["Nice", "Draw", "Paris Saint-Germain"]

    def test_doble_oportunidad(self) -> None:
        nombres = ["Niza o empate", "Nice or Paris Saint-Germain", "Draw or Paris Saint-Germain"]
        assert completar_por_eliminacion(
            "double_chance", nombres, home_team="Nice", away_team="Paris Saint-Germain",
        )[0] == "Nice or Draw"

    def test_con_dos_sin_reconocer_no_toca_nada(self) -> None:
        nombres = ["R. Santander", "Draw", "Txuri"]
        assert completar_por_eliminacion(
            "h2h", nombres, home_team="Real Racing Club", away_team="Real Sociedad",
        ) == nombres

    def test_otros_mercados_no_cambian(self) -> None:
        assert completar_por_eliminacion("btts", ["Yes", "No"], home_team="A", away_team="B") == ["Yes", "No"]
