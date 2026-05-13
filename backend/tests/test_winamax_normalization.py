"""Tests para normalización Winamax → claves tipo Odds API."""

from __future__ import annotations

from backend.services.winamax_normalization import (
    canonical_market_key,
    normalize_btts_outcome,
    normalize_double_chance_outcome,
    normalize_h2h_outcome,
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
