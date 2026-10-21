"""Tests de etiquetado de líneas del dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from backend.services.graded_odds_service import (
    GradedOddsLine,
    _finite_decimal_or_none,
    _summary_lines_for_bet,
    confidence_from_model_ev,
    grade_line,
    keep_best_lines,
)


def _line(match_id: int, ev: str | None, odds: str = "2") -> GradedOddsLine:
    model_ev = Decimal(ev) if ev is not None else None
    confidence, recommendable = grade_line(Decimal(odds), model_ev)
    return GradedOddsLine(
        match_id=match_id, league="L", league_sport_key="soccer_epl", home_team="A",
        away_team="B", commence_time=datetime(2026, 10, 15, tzinfo=timezone.utc),
        market_key="match_winner", outcome_name="A", outcome_point=None, player_name=None,
        bookmaker_key="winamax_es", bookmaker_title="Winamax", offered_odds=Decimal(odds),
        confidence=confidence, model_probability=None, model_ev=model_ev,
        recommendable=recommendable,
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

    def test_una_cuota_de_mas_de_4_no_quita_sitio_a_una_recomendable(self) -> None:
        lines = [_line(1, "0.40", odds="7.50"), _line(2, "0.02", odds="1.80")]
        assert [x.match_id for x in keep_best_lines(lines, 1)] == [2]


class TestGradeLine:
    """Las líneas > 4,00 se ven (con su EV) pero nunca se califican como valor."""

    def test_cuota_de_mas_de_4_no_es_recomendable_ni_tiene_confianza(self) -> None:
        assert grade_line(Decimal("6.50"), Decimal("0.30")) == ("unknown", False)

    def test_4_01_ya_es_informativa(self) -> None:
        assert grade_line(Decimal("4.01"), Decimal("0.07")) == ("unknown", False)

    def test_la_linea_conserva_su_ev_aunque_no_sea_recomendable(self) -> None:
        line = _line(1, "0.30", odds="6.50")
        assert line.model_ev == Decimal("0.30")
        assert line.recommendable is False
        assert line.confidence == "unknown"

    @pytest.mark.parametrize(
        ("cuota", "ev", "esperada"),
        [
            ("4.00", "0.07", "high"),
            ("2.10", "0.03", "medium"),
            ("1.50", "-0.01", "low"),
            ("3.20", None, "unknown"),
        ],
    )
    def test_hasta_4_la_confianza_no_cambia(self, cuota: str, ev: str | None, esperada: str) -> None:
        model_ev = Decimal(ev) if ev is not None else None
        assert grade_line(Decimal(cuota), model_ev) == (esperada, True)
        assert confidence_from_model_ev(model_ev) == esperada

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
