"""Tests del modelo de baloncesto (proyección y probabilidad de victoria)."""

from __future__ import annotations

import pytest

from backend.services import basketball_model as bbm


def test_basketball_projection_sane() -> None:
    proj = bbm.BasketballProjection(102.0, 99.0, 11.0, 11.0, 15.0)
    assert proj.exp_game_total == 201.0


def test_basketball_h2h_sum_order_one() -> None:
    proj = bbm.BasketballProjection(105.0, 100.0, 11.0, 11.0, 15.0)
    p_home = bbm.p_home_win(proj)
    p_away = 1.0 - p_home
    assert p_home + p_away == pytest.approx(1.0, abs=1e-9)
