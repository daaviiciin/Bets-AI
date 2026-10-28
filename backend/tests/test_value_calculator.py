"""Tests del cálculo de valor (EV, Kelly, vig, parlays)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.services.value_calculator import (
    MAX_RECOMMENDABLE_ODDS,
    bookmaker_margin,
    expected_value,
    fair_probabilities,
    implied_probability,
    is_recommendable_odds,
    kelly_fraction,
)


class TestCuotaRecomendable:
    def test_el_tope_es_4(self):
        assert MAX_RECOMMENDABLE_ODDS == Decimal("4.00")

    @pytest.mark.parametrize("cuota", ["1.01", "1.85", "3.99", "4", "4.00"])
    def test_hasta_4_incluido_es_recomendable(self, cuota: str):
        assert is_recommendable_odds(Decimal(cuota))

    @pytest.mark.parametrize("cuota", ["4.01", "5.50", "10.00", "15"])
    def test_por_encima_de_4_es_solo_informativa(self, cuota: str):
        assert not is_recommendable_odds(Decimal(cuota))

    @pytest.mark.parametrize("cuota", [None, "1", "0.5", "NaN", "Infinity"])
    def test_cuota_invalida_no_es_recomendable(self, cuota: str | None):
        assert not is_recommendable_odds(Decimal(cuota) if cuota is not None else None)

    def test_acepta_float_y_str(self):
        assert is_recommendable_odds(2.5)
        assert not is_recommendable_odds("4.5")


class TestImpliedProbability:
    def test_cuota_2_es_50_pct(self):
        assert implied_probability(Decimal("2.0")) == Decimal("0.5")

    def test_cuota_4_es_25_pct(self):
        assert implied_probability(Decimal("4.0")) == Decimal("0.25")

    def test_cuota_negativa_o_uno_falla(self):
        with pytest.raises(ValueError):
            implied_probability(Decimal("1.0"))
        with pytest.raises(ValueError):
            implied_probability(Decimal("0.5"))


class TestBookmakerMargin:
    def test_mercado_perfecto_sin_margen(self):
        margin = bookmaker_margin([Decimal("2.0"), Decimal("2.0")])
        assert margin == Decimal("0")

    def test_winamax_clasico_alto_margen(self):
        # Cuotas de Winamax FR para Barça-Madrid: 1.76 / 4.30 / 3.50
        margin = bookmaker_margin([
            Decimal("1.76"), Decimal("4.30"), Decimal("3.50"),
        ])
        assert margin > Decimal("0.08")
        assert margin < Decimal("0.10")

    def test_pinnacle_margen_bajo(self):
        # Cuotas de Pinnacle para Barça-Madrid: 1.82 / 4.17 / 3.97
        margin = bookmaker_margin([
            Decimal("1.82"), Decimal("4.17"), Decimal("3.97"),
        ])
        assert margin < Decimal("0.05")


class TestFairProbabilities:
    def test_quita_vig_y_suma_uno(self):
        odds = {
            "Barcelona": Decimal("1.89"),
            "Empate": Decimal("4.22"),
            "Real Madrid": Decimal("4.00"),
        }
        fair = fair_probabilities(odds)
        total = sum(fair.values())
        assert abs(total - Decimal("1")) < Decimal("0.0001")

    def test_orden_de_probabilidades_se_preserva(self):
        # Real Madrid favorito (cuota más baja) → mayor probabilidad
        odds = {
            "Real Madrid": Decimal("1.50"),
            "Empate": Decimal("4.00"),
            "Cádiz": Decimal("8.00"),
        }
        fair = fair_probabilities(odds)
        assert fair["Real Madrid"] > fair["Empate"] > fair["Cádiz"]

    def test_diccionario_vacio_falla(self):
        with pytest.raises(ValueError):
            fair_probabilities({})


class TestExpectedValue:
    def test_apuesta_justa_ev_cero(self):
        # Cuota 2.0, prob real 50% → EV exactamente 0
        ev = expected_value(Decimal("2.0"), Decimal("0.5"))
        assert ev == Decimal("0")

    def test_value_bet_positivo(self):
        # Cuota 2.0, prob real 60% → EV +20%
        ev = expected_value(Decimal("2.0"), Decimal("0.6"))
        assert ev == Decimal("0.2")

    def test_apuesta_de_perdida_ev_negativo(self):
        # Cuota 1.5, prob real 50% → EV negativo
        ev = expected_value(Decimal("1.5"), Decimal("0.5"))
        assert ev < Decimal("0")

    def test_prob_fuera_de_rango_falla(self):
        with pytest.raises(ValueError):
            expected_value(Decimal("2.0"), Decimal("1.5"))
        with pytest.raises(ValueError):
            expected_value(Decimal("2.0"), Decimal("-0.1"))


class TestKellyFraction:
    def test_apuesta_sin_valor_kelly_cero(self):
        # Cuota 1.5, prob real 50% → no apostar
        assert kelly_fraction(Decimal("1.5"), Decimal("0.5")) == Decimal("0")

    def test_apuesta_justa_kelly_cero(self):
        # Cuota 2.0, prob real 50% → no apostar (EV = 0)
        assert kelly_fraction(Decimal("2.0"), Decimal("0.5")) == Decimal("0")

    def test_value_bet_kelly_positivo(self):
        # Cuota 2.0, prob real 60% → Kelly = 0.20 (apostar 20% del bankroll)
        f = kelly_fraction(Decimal("2.0"), Decimal("0.6"))
        assert f == Decimal("0.2")

    def test_value_grande_kelly_alto(self):
        # Cuota 3.0, prob real 50% → Kelly = 0.25
        f = kelly_fraction(Decimal("3.0"), Decimal("0.5"))
        assert f == Decimal("0.25")


