"""Tests del cálculo de valor (EV, Kelly, vig, parlays)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.services.value_calculator import (
    bookmaker_margin,
    expected_value,
    fair_probabilities,
    implied_probability,
    kelly_fraction,
    parlay_odds_and_probability,
    recommended_stake,
)


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


class TestRecommendedStake:
    def test_sin_valor_devuelve_cero(self):
        stake = recommended_stake(
            Decimal("100"), Decimal("1.5"), Decimal("0.5"),
        )
        assert stake == Decimal("0")

    def test_kelly_completo(self):
        # 100 € bankroll, cuota 2.0, prob 0.6 → Kelly 0.2 → stake 20 €
        stake = recommended_stake(
            Decimal("100"), Decimal("2.0"), Decimal("0.6"),
        )
        assert stake == Decimal("20")

    def test_kelly_fraccional(self):
        # Mismo escenario con Kelly/4 → stake 5 €
        stake = recommended_stake(
            Decimal("100"), Decimal("2.0"), Decimal("0.6"),
            kelly_modifier=Decimal("0.25"),
        )
        assert stake == Decimal("5")

    def test_max_stake_pct_se_respeta(self):
        # Kelly diría 20 €, pero límite del 5 % → 5 €
        stake = recommended_stake(
            Decimal("100"), Decimal("2.0"), Decimal("0.6"),
            max_stake_pct=Decimal("0.05"),
        )
        assert stake == Decimal("5")

    def test_flat_stake_ignora_kelly(self):
        stake = recommended_stake(
            Decimal("100"), Decimal("2.0"), Decimal("0.6"),
            flat_stake=Decimal("3"),
        )
        assert stake == Decimal("3")

    def test_flat_stake_no_se_paga_si_no_hay_valor(self):
        stake = recommended_stake(
            Decimal("100"), Decimal("1.5"), Decimal("0.5"),
            flat_stake=Decimal("3"),
        )
        assert stake == Decimal("0")


class TestParlay:
    def test_combinada_doble(self):
        # Madrid 1.55 (P=0.71) + Bayern 1.60 (P=0.65)
        odds, prob = parlay_odds_and_probability([
            (Decimal("1.55"), Decimal("0.71")),
            (Decimal("1.60"), Decimal("0.65")),
        ])
        assert odds == Decimal("2.48")
        # 0.71 * 0.65 = 0.4615
        assert prob == Decimal("0.4615")

    def test_combinada_triple_multiplica_correctamente(self):
        odds, prob = parlay_odds_and_probability([
            (Decimal("2.0"), Decimal("0.5")),
            (Decimal("2.0"), Decimal("0.5")),
            (Decimal("2.0"), Decimal("0.5")),
        ])
        assert odds == Decimal("8")
        assert prob == Decimal("0.125")

    def test_combinada_vacia_falla(self):
        with pytest.raises(ValueError):
            parlay_odds_and_probability([])
