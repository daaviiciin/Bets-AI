"""Tests de etiquetado de líneas del dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from backend.services.graded_odds_service import (
    GradedOddsLine,
    _finite_decimal_or_none,
    _summary_lines_for_bet,
    confidence_from_model_ev,
    keep_best_lines,
)


def _line(match_id: int, ev: str | None) -> GradedOddsLine:
    return GradedOddsLine(
        match_id=match_id, league="L", league_sport_key="soccer_epl", home_team="A",
        away_team="B", commence_time=datetime(2026, 10, 15, tzinfo=timezone.utc),
        market_key="match_winner", outcome_name="A", outcome_point=None, player_name=None,
        bookmaker_key="winamax_es", bookmaker_title="Winamax", offered_odds=Decimal("2"),
        confidence="baja", model_probability=None,
        model_ev=Decimal(ev) if ev is not None else None,
    )


class TestKeepBestLines:
    def test_bajo_el_tope_no_se_toca(self) -> None:
        lines = [_line(1, "-0.1"), _line(2, None)]
        assert keep_best_lines(lines, 5) == lines

    def test_sobre_el_tope_se_quedan_las_de_mas_ev_en_su_orden(self) -> None:
        lines = [_line(1, "-0.20"), _line(2, None), _line(3, "0.08"), _line(4, "0.01")]
        assert [x.match_id for x in keep_best_lines(lines, 2)] == [3, 4]

    def test_las_lineas_sin_ev_van_detras_de_las_negativas(self) -> None:
        lines = [_line(1, None), _line(2, "-0.30")]
        assert [x.match_id for x in keep_best_lines(lines, 1)] == [2]

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
