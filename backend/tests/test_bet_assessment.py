"""Tests del evaluador manual de cuotas / combinadas."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.services import basketball_model as bbm
from backend.services import bet_assessment_service as bas


def test_implied_probability_decimal() -> None:
    assert pytest.approx(bas.implied_probability_decimal(Decimal("2.0"))) == 0.5
    assert pytest.approx(bas.implied_probability_decimal(Decimal("4.0"))) == 0.25


def test_user_market_totals_key_matches_match_analysis_naming() -> None:
    pk = bas._user_market_to_prediction_key("totals", "over", Decimal("2.5"))
    assert pk == ("totals_2.5", "over")


def test_basketball_projection_sane() -> None:
    proj = bbm.BasketballProjection(102.0, 99.0, 11.0, 11.0, 15.0)
    assert proj.exp_game_total == 201.0


def test_basketball_h2h_sum_order_one() -> None:
    proj = bbm.BasketballProjection(105.0, 100.0, 11.0, 11.0, 15.0)
    p_home = bbm.p_home_win(proj)
    p_away = 1.0 - p_home
    assert p_home + p_away == pytest.approx(1.0, abs=1e-9)
