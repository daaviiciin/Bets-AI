"""Tests del parser del comparador de cuotas de Flashscore (respuestas reales recortadas, sin red)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from backend.scrapers import flashscore_cuotas as fc

_MUESTRAS = Path(__file__).parent / "muestras"
BET365, WINAMAX, SPORTIUM, INTERWETTEN = 16, 883, 406, 1


def _cargar(nombre: str) -> dict:
    return json.loads((_MUESTRAS / nombre).read_text(encoding="utf-8"))


def _jugado() -> fc.ComparadorPartido:
    return fc.parse_odds_comparison(_cargar("flashscore_cuotas_oce_jugado.json"))


def _futuro() -> fc.ComparadorPartido:
    return fc.parse_odds_comparison(_cargar("flashscore_cuotas_oce.json"))


def _cuota(comp, bm, market, outcome, line=None) -> fc.CuotaComparador:
    linea = Decimal(str(line)) if line is not None else None
    encontradas = [
        c for c in comp.cuotas
        if c.bookmaker_fs_id == bm and c.market_key == market and c.outcome_key == outcome
        and c.line == linea
    ]
    assert len(encontradas) == 1, (bm, market, outcome, line, encontradas)
    return encontradas[0]


class TestClaveMercado:
    def test_partido_completo_usa_las_claves_de_winamax(self):
        assert fc.clave_mercado("HOME_DRAW_AWAY", "FULL_TIME") == "h2h"
        assert fc.clave_mercado("OVER_UNDER", "FULL_TIME") == "totals"
        assert fc.clave_mercado("BOTH_TEAMS_TO_SCORE", "FULL_TIME") == "btts"
        assert fc.clave_mercado("ODD_OR_EVEN", "FULL_TIME") == "match_total_goals_odd_even"

    def test_primera_mitad_lleva_prefijo(self):
        assert fc.clave_mercado("OVER_UNDER", "FIRST_HALF") == "match_first_half_totals"
        assert fc.clave_mercado("HOME_DRAW_AWAY", "SECOND_HALF") == "match_second_half_h2h"

    def test_tipo_desconocido_no_se_pierde(self):
        assert fc.clave_mercado("TO_QUALIFY", "FULL_TIME") == "fs_to_qualify"


class TestCasas:
    def test_salen_bet365_y_winamax(self):
        comp = _futuro()
        assert comp.bookmakers[BET365] == "bet365"
        assert comp.bookmakers[WINAMAX] == "Winamax.es"
        assert comp.event_id == "G6MoOrne"


class TestParticipantes:
    def test_local_es_el_primero_del_1x2(self):
        payload = _cargar("flashscore_cuotas_oce_jugado.json")
        odds = payload["data"]["findOddsByEventId"]["odds"]
        assert fc.participantes_local_visitante(odds) == ("fBvd4ctg", "SOx42yB5")

    def test_sin_1x2_ni_dnb_no_adivina(self):
        assert fc.participantes_local_visitante([]) == (None, None)


class TestTraduccion:
    def test_1x2_de_bet365(self):
        comp = _jugado()
        assert _cuota(comp, BET365, "h2h", "home").price == Decimal("3.6")
        assert _cuota(comp, BET365, "h2h", "away").price == Decimal("1.8")
        assert _cuota(comp, BET365, "h2h", "draw").price == Decimal("4.5")

    def test_apertura_se_guarda(self):
        assert _cuota(_jugado(), BET365, "h2h", "home").opening_price == Decimal("3.8")

    def test_mas_menos_con_linea(self):
        c = _cuota(_jugado(), BET365, "totals", "over", 2.5)
        assert c.price == Decimal("1.36")
        assert c.line == Decimal("2.5")

    def test_doble_oportunidad(self):
        comp = _jugado()
        assert _cuota(comp, BET365, "double_chance", "12").price == Decimal("1.18")
        assert _cuota(comp, BET365, "double_chance", "1X").price == Decimal("1.95")
        assert _cuota(comp, BET365, "double_chance", "X2").price == Decimal("1.29")

    def test_ambos_marcan(self):
        comp = _jugado()
        assert _cuota(comp, BET365, "btts", "yes").price == Decimal("1.36")
        assert _cuota(comp, BET365, "btts", "no").price == Decimal("3.0")

    def test_handicap_asiatico_visitante_se_guarda_desde_el_local(self):
        # «Visitante −0,5» a 1,88 = línea +0,5 del local, lado visitante.
        comp = _jugado()
        assert _cuota(comp, BET365, "asian_handicap", "away", 0.5).price == Decimal("1.88")
        assert _cuota(comp, BET365, "asian_handicap", "home", -0.5).price == Decimal("3.3")

    def test_handicap_europeo_forma_mercado_de_tres(self):
        comp = _jugado()
        local = _cuota(comp, INTERWETTEN, "european_handicap", "home", 1)
        empate = _cuota(comp, INTERWETTEN, "european_handicap", "draw", 1)
        visitante = _cuota(comp, INTERWETTEN, "european_handicap", "away", 1)
        suma = sum(1 / c.price for c in (local, empate, visitante))
        assert Decimal("1") < suma < Decimal("1.2")

    def test_marcador_exacto_y_descanso_final(self):
        comp = _jugado()
        assert any(c.market_key == "correct_score" and c.outcome_key == "1:0" for c in comp.cuotas)
        assert _cuota(comp, BET365, "half_time_full_time", "X/1").price == Decimal("8.5")

    def test_cuota_1_se_descarta(self):
        comp = _jugado()
        assert all(c.price > 1 for c in comp.cuotas)
        assert comp.descartadas >= 1

    def test_inactiva_queda_marcada(self):
        comp = _jugado()
        assert any(not c.active for c in comp.cuotas if c.bookmaker_fs_id == SPORTIUM)

    def test_respuesta_vacia_no_rompe(self):
        comp = fc.parse_odds_comparison({})
        assert comp.cuotas == [] and comp.bookmakers == {}
