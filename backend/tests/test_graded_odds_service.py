"""Tests de etiquetado de líneas del dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from backend.services.graded_odds_service import (
    _finite_decimal_or_none,
    _summary_lines_for_bet,
    confidence_from_model_ev,
)

class TestSummaryLinesArePlainStrings:
    def test_team_away_totals_no_tuples_in_lines(self) -> None:
        """Regresión: coma final en `(a if x else b,)` metía tuplas → 500 en Pydantic."""
        m = MagicMock()
        m.home_team.name = "Locals"
        m.away_team.name = "Visitors"
        m.commence_time = datetime.now(timezone.utc)
        lines = _summary_lines_for_bet(
            match=m,
            home_st=None,
            away_st=None,
            market_key="team_away_totals_goals",
            outcome_name="Over",
            outcome_point=Decimal("2.5"),
            player_name=None,
        )
        assert lines
        assert all(isinstance(x, str) for x in lines)


class TestFiniteDecimalOrNone:
    def test_nan_and_infBecome_none(self) -> None:
        assert _finite_decimal_or_none(Decimal("NaN")) is None
        assert _finite_decimal_or_none(Decimal("Infinity")) is None

    def test_ordinals_preserved(self) -> None:
        assert _finite_decimal_or_none(Decimal("2.5")) == Decimal("2.5")
        assert _finite_decimal_or_none(None) is None


class TestConfidenceFromModelEv:
    def test_unknown(self) -> None:
        assert confidence_from_model_ev(None) == "unknown"

    def test_high(self) -> None:
        assert confidence_from_model_ev(Decimal("0.07")) == "high"

    def test_medium(self) -> None:
        assert confidence_from_model_ev(Decimal("0.03")) == "medium"

    def test_low_near_fair(self) -> None:
        assert confidence_from_model_ev(Decimal("-0.01")) == "low"
