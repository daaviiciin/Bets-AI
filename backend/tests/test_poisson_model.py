"""Tests del modelo Poisson."""

from __future__ import annotations

import math

import pytest

from backend.services.poisson_model import (
    GOAL_GRID_SIZE,
    HOME_ADVANTAGE,
    TeamLeagueAverages,
    _build_grid,
    _poisson_pmf,
    derive_market_probabilities,
    expected_goals,
    league_average_goals,
    predict,
    probability_over_single_poisson,
    probability_over_total,
)


class TestPoissonPmf:
    def test_at_zero_with_zero_lambda(self) -> None:
        assert _poisson_pmf(0, 0) == 1.0

    def test_zero_lambda_otherwise(self) -> None:
        assert _poisson_pmf(3, 0) == 0.0

    def test_pmf_sums_to_one(self) -> None:
        s = sum(_poisson_pmf(k, 1.5) for k in range(20))
        assert math.isclose(s, 1.0, rel_tol=1e-9)


class TestLeagueAverage:
    def test_empty_returns_default(self) -> None:
        assert league_average_goals([]) == pytest.approx(1.4)

    def test_simple(self) -> None:
        teams = [
            TeamLeagueAverages(matches_played=10, goals_for_per_match=2.0, goals_against_per_match=1.0),
            TeamLeagueAverages(matches_played=10, goals_for_per_match=1.0, goals_against_per_match=2.0),
        ]
        assert league_average_goals(teams) == pytest.approx(1.5)


class TestExpectedGoals:
    def test_balanced_teams_give_home_advantage(self) -> None:
        avg_team = TeamLeagueAverages(matches_played=20, goals_for_per_match=1.4, goals_against_per_match=1.4)
        lh, la = expected_goals(avg_team, avg_team, 1.4)
        assert lh > la
        assert lh == pytest.approx(1.4 * HOME_ADVANTAGE)
        assert la == pytest.approx(1.4 / HOME_ADVANTAGE)

    def test_strong_home_vs_weak_away(self) -> None:
        strong = TeamLeagueAverages(matches_played=20, goals_for_per_match=2.5, goals_against_per_match=0.8)
        weak = TeamLeagueAverages(matches_played=20, goals_for_per_match=0.7, goals_against_per_match=2.0)
        lh, la = expected_goals(strong, weak, 1.4)
        assert lh > 2.5
        assert la < 0.4


class TestMarketProbabilities:
    def test_balanced_game_distribution(self) -> None:
        result = derive_market_probabilities(1.4, 1.3)
        total = result.p_home_win + result.p_draw + result.p_away_win
        assert math.isclose(total, 1.0, abs_tol=1e-3)
        assert math.isclose(result.p_over_25 + result.p_under_25, 1.0, abs_tol=1e-3)
        assert math.isclose(result.p_btts_yes + result.p_btts_no, 1.0, abs_tol=1e-9)
        assert result.p_home_win > result.p_away_win

    def test_dominant_home_team(self) -> None:
        result = derive_market_probabilities(2.5, 0.6)
        assert result.p_home_win > 0.65
        assert result.p_away_win < 0.10

    def test_low_scoring_match(self) -> None:
        result = derive_market_probabilities(0.7, 0.6)
        assert result.p_under_25 > result.p_over_25
        assert result.p_btts_no > result.p_btts_yes


class TestProbabilityOverTotal:
    def test_matches_derive_25(self) -> None:
        # probability_over_total con .25 debería dar exactamente lo mismo que p_over_25
        ref = derive_market_probabilities(1.5, 1.3)
        po, pu = probability_over_total(1.5, 1.3, 2.5)
        assert math.isclose(po, ref.p_over_25, abs_tol=1e-9)
        assert math.isclose(pu, ref.p_under_25, abs_tol=1e-9)

    def test_matches_derive_15_and_35(self) -> None:
        ref = derive_market_probabilities(1.8, 1.2)
        po15, _ = probability_over_total(1.8, 1.2, 1.5)
        po35, _ = probability_over_total(1.8, 1.2, 3.5)
        assert math.isclose(po15, ref.p_over_15, abs_tol=1e-9)
        assert math.isclose(po35, ref.p_over_35, abs_tol=1e-9)

    def test_higher_point_lowers_over(self) -> None:
        po15, _ = probability_over_total(1.5, 1.3, 1.5)
        po25, _ = probability_over_total(1.5, 1.3, 2.5)
        po35, _ = probability_over_total(1.5, 1.3, 3.5)
        assert po15 > po25 > po35

    def test_quarter_lines_round_to_nearest_half(self) -> None:
        # 2.25 y 2.75 redondean a 2.5; 3.25 redondea a 3.5.
        po25, _ = probability_over_total(1.5, 1.3, 2.5)
        po225, _ = probability_over_total(1.5, 1.3, 2.25)
        po275, _ = probability_over_total(1.5, 1.3, 2.75)
        po35, _ = probability_over_total(1.5, 1.3, 3.5)
        po325, _ = probability_over_total(1.5, 1.3, 3.25)
        assert math.isclose(po225, po25)
        assert math.isclose(po275, po25)
        assert math.isclose(po325, po35)

    def test_integer_line_does_not_count_push(self) -> None:
        # En .0 (push), la suma over+under debe ser < 1 (la diferencia es el push)
        po, pu = probability_over_total(1.5, 1.3, 2.0)
        assert po + pu < 1.0
        assert po + pu > 0.7  # pero la mayoría no son push


class TestDoubleChance:
    """Las propiedades de Double Chance se derivan trivialmente del 1X2."""

    def test_sums_consistent_with_1x2(self) -> None:
        exp = derive_market_probabilities(1.4, 1.3)
        assert math.isclose(exp.p_home_or_draw, exp.p_home_win + exp.p_draw, abs_tol=1e-9)
        assert math.isclose(exp.p_home_or_away, exp.p_home_win + exp.p_away_win, abs_tol=1e-9)
        assert math.isclose(exp.p_draw_or_away, exp.p_draw + exp.p_away_win, abs_tol=1e-9)

    def test_dc_pairs_cover_complete_space(self) -> None:
        # Cualquier par de chances cubre el 100% si reúnes outcomes únicos.
        exp = derive_market_probabilities(1.4, 1.3)
        # 1X + X2 - X = 1
        full = exp.p_home_or_draw + exp.p_draw_or_away - exp.p_draw
        assert math.isclose(full, 1.0, abs_tol=1e-3)

    def test_dc_higher_than_each_part(self) -> None:
        exp = derive_market_probabilities(2.0, 0.6)
        assert exp.p_home_or_draw > exp.p_home_win
        assert exp.p_home_or_draw > exp.p_draw
        assert exp.p_home_or_away > exp.p_home_win

    def test_as_dict_includes_dc(self) -> None:
        exp = derive_market_probabilities(1.4, 1.3)
        d = exp.as_dict()
        assert "p_home_or_draw" in d
        assert "p_home_or_away" in d
        assert "p_draw_or_away" in d


class TestPredict:
    def test_end_to_end(self) -> None:
        home = TeamLeagueAverages(matches_played=30, goals_for_per_match=2.0, goals_against_per_match=1.0)
        away = TeamLeagueAverages(matches_played=30, goals_for_per_match=1.5, goals_against_per_match=1.2)
        league = [home, away]
        result = predict(home, away, league)
        assert result.lambda_home > 0
        assert result.lambda_away > 0
        assert math.isclose(result.p_home_win + result.p_draw + result.p_away_win, 1.0, abs_tol=1e-3)


class TestDixonColes:
    """Corrección Dixon-Coles: ajuste de las 4 celdas low-score (0-0, 0-1, 1-0, 1-1)."""

    def test_rho_zero_is_idempotent(self) -> None:
        ref = derive_market_probabilities(1.5, 1.3, dixon_coles_rho=0.0)
        plain = derive_market_probabilities(1.5, 1.3)
        assert math.isclose(ref.p_home_win, plain.p_home_win, abs_tol=1e-12)
        assert math.isclose(ref.p_draw, plain.p_draw, abs_tol=1e-12)
        assert math.isclose(ref.p_away_win, plain.p_away_win, abs_tol=1e-12)
        assert math.isclose(ref.p_over_25, plain.p_over_25, abs_tol=1e-12)
        assert math.isclose(ref.p_btts_yes, plain.p_btts_yes, abs_tol=1e-12)
        assert ref.dixon_coles_rho == 0.0

    def test_negative_rho_increases_low_diagonal_draws(self) -> None:
        """ρ < 0 ⇒ más masa en 0-0 / 1-1 ⇒ sube P(empate)."""
        rho = -0.10
        plain = derive_market_probabilities(1.4, 1.2)
        dc = derive_market_probabilities(1.4, 1.2, dixon_coles_rho=rho)
        assert dc.p_draw > plain.p_draw
        assert dc.dixon_coles_rho == rho

    def test_positive_rho_decreases_draws(self) -> None:
        """ρ > 0 ⇒ las celdas 0-1 / 1-0 ganan masa ⇒ baja P(empate)."""
        rho = 0.10
        plain = derive_market_probabilities(1.4, 1.2)
        dc = derive_market_probabilities(1.4, 1.2, dixon_coles_rho=rho)
        assert dc.p_draw < plain.p_draw

    def test_grid_sums_to_one_with_rho(self) -> None:
        # Sin DC (rho=0) la matriz 8x8 deja una cola truncada (~3e-4).
        # Con DC renormalizamos y debe quedar exactamente 1.
        for rho in (-0.15, -0.05, 0.05, 0.15):
            grid = _build_grid(1.5, 1.3, dixon_coles_rho=rho)
            total = sum(sum(row) for row in grid)
            assert math.isclose(total, 1.0, abs_tol=1e-9), (
                f"DC con rho={rho} debería renormalizar: total={total}"
            )
            assert all(v >= 0.0 for row in grid for v in row)
        # Sin DC: tolerancia ancha para la cola truncada del Poisson.
        grid_plain = _build_grid(1.5, 1.3, dixon_coles_rho=0.0)
        total_plain = sum(sum(row) for row in grid_plain)
        assert math.isclose(total_plain, 1.0, abs_tol=1e-3)

    def test_probabilities_remain_consistent_with_rho(self) -> None:
        """Sumas 1X2 / O-U / BTTS siguen valiendo 1 con DC."""
        rho = -0.08
        exp = derive_market_probabilities(1.6, 1.1, dixon_coles_rho=rho)
        # 1X2
        assert math.isclose(exp.p_home_win + exp.p_draw + exp.p_away_win, 1.0, abs_tol=1e-9)
        # O/U
        assert math.isclose(exp.p_over_15 + exp.p_under_15, 1.0, abs_tol=1e-9)
        assert math.isclose(exp.p_over_25 + exp.p_under_25, 1.0, abs_tol=1e-9)
        assert math.isclose(exp.p_over_35 + exp.p_under_35, 1.0, abs_tol=1e-9)
        # BTTS
        assert math.isclose(exp.p_btts_yes + exp.p_btts_no, 1.0, abs_tol=1e-9)

    def test_extreme_rho_does_not_explode(self) -> None:
        """Un ρ fuera del rango admisible no debe romper: las celdas se clampean."""
        # ρ = 1 satura τ(1,1) = 0 y, con λ=2.5, satura τ(0,0).
        exp = derive_market_probabilities(2.5, 2.0, dixon_coles_rho=0.99)
        assert 0.0 <= exp.p_home_win <= 1.0
        assert 0.0 <= exp.p_draw <= 1.0
        assert 0.0 <= exp.p_away_win <= 1.0
        assert math.isclose(
            exp.p_home_win + exp.p_draw + exp.p_away_win, 1.0, abs_tol=1e-9,
        )

    def test_predict_propagates_rho(self) -> None:
        home = TeamLeagueAverages(matches_played=20, goals_for_per_match=1.5, goals_against_per_match=1.2)
        away = TeamLeagueAverages(matches_played=20, goals_for_per_match=1.3, goals_against_per_match=1.4)
        result = predict(home, away, [home, away], dixon_coles_rho=-0.07)
        assert result.dixon_coles_rho == -0.07
        # Coherente con derive_market_probabilities llamado con las mismas λ.
        ref = derive_market_probabilities(
            result.lambda_home, result.lambda_away, dixon_coles_rho=-0.07,
        )
        assert math.isclose(result.p_home_win, ref.p_home_win, abs_tol=1e-12)
        assert math.isclose(result.p_draw, ref.p_draw, abs_tol=1e-12)

    def test_probability_over_total_propagates_rho(self) -> None:
        rho = -0.10
        ref = derive_market_probabilities(1.5, 1.3, dixon_coles_rho=rho)
        po25, pu25 = probability_over_total(1.5, 1.3, 2.5, dixon_coles_rho=rho)
        assert math.isclose(po25, ref.p_over_25, abs_tol=1e-12)
        assert math.isclose(pu25, ref.p_under_25, abs_tol=1e-12)


class TestSinglePoissonTotals:
    def test_half_line_sums_to_one(self) -> None:
        po, pu = probability_over_single_poisson(1.2, 1.5)
        assert math.isclose(po + pu, 1.0, abs_tol=1e-9)

    def test_lambda_zero_favors_under_high_line(self) -> None:
        po, pu = probability_over_single_poisson(0.0, 2.5)
        assert po == 0.0
        assert pu == 1.0
