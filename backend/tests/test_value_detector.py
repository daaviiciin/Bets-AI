"""Tests del ``ValueDetector`` (parte que es testable sin BD: helpers puros y
mapeo de outcomes al modelo Poisson).

Los tests de integración con BD viven en ``test_integration_*`` (se ejecutan
solo si Postgres está disponible).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from backend.services import poisson_model as pm
from backend.services.value_detector import (
    ValueDetector,
    _compute_confidence,
    _parse_player_prop_side,
    _to_averages,
)


class TestComputeConfidence:
    def test_unknown_when_no_model(self) -> None:
        assert _compute_confidence(
            ev_market=Decimal("0.10"), ev_model=None, min_ev=Decimal("0.02"),
        ) == "unknown"

    def test_low_when_model_disagrees(self) -> None:
        # Mercado dice valor (0.05), pero modelo dice EV negativo (-0.02)
        assert _compute_confidence(
            ev_market=Decimal("0.05"),
            ev_model=Decimal("-0.02"),
            min_ev=Decimal("0.02"),
        ) == "low"

    def test_medium_when_both_positive_modest(self) -> None:
        assert _compute_confidence(
            ev_market=Decimal("0.03"),
            ev_model=Decimal("0.03"),
            min_ev=Decimal("0.02"),
        ) == "medium"

    def test_high_when_both_strongly_positive(self) -> None:
        assert _compute_confidence(
            ev_market=Decimal("0.10"),
            ev_model=Decimal("0.08"),
            min_ev=Decimal("0.02"),
        ) == "high"

    def test_medium_when_only_one_high(self) -> None:
        # Si market es alto pero el modelo solo modesto, es medium
        assert _compute_confidence(
            ev_market=Decimal("0.10"),
            ev_model=Decimal("0.03"),
            min_ev=Decimal("0.02"),
        ) == "medium"


class TestToAverages:
    def test_zero_matches_returns_none(self) -> None:
        stats = MagicMock()
        stats.matches_played = 0
        stats.goals_for = 5
        stats.goals_against = 3
        assert _to_averages(stats) is None

    def test_missing_goals_returns_none(self) -> None:
        stats = MagicMock()
        stats.matches_played = 10
        stats.goals_for = None
        stats.goals_against = 5
        assert _to_averages(stats) is None

    def test_valid_stats_produce_averages(self) -> None:
        stats = MagicMock()
        stats.matches_played = 10
        stats.goals_for = 20
        stats.goals_against = 12
        avg = _to_averages(stats)
        assert avg is not None
        assert avg.matches_played == 10
        assert avg.goals_for_per_match == 2.0
        assert avg.goals_against_per_match == 1.2


class TestModelProbabilityMapping:
    """Verifica que ``_model_probability`` mapea los nombres de outcome al modelo."""

    @staticmethod
    def mk_soccer_match(home: str, away: str) -> MagicMock:
        m = MagicMock()
        lg = MagicMock()
        lg.sport_key = "soccer_tests"
        m.league = lg
        m.league_id = 1
        ht = MagicMock()
        ht.name = home
        at = MagicMock()
        at.name = away
        m.home_team = ht
        m.away_team = at
        return m

    @pytest.fixture
    def detector(self) -> ValueDetector:
        # Sesión mockeada — no usamos BD aquí.
        return ValueDetector(session=MagicMock())

    @pytest.fixture
    def expectation(self) -> pm.MatchExpectation:
        # Local fuerte vs visitante débil
        return pm.predict(
            home=pm.TeamLeagueAverages(matches_played=20, goals_for_per_match=2.5, goals_against_per_match=0.8),
            away=pm.TeamLeagueAverages(matches_played=20, goals_for_per_match=0.7, goals_against_per_match=2.0),
            league_teams=[
                pm.TeamLeagueAverages(matches_played=20, goals_for_per_match=1.4, goals_against_per_match=1.4),
            ],
        )

    def test_h2h_home(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("Real Madrid", "Espanyol"),
            market_key="h2h",
            outcome_name="Real Madrid",
            outcome_point=None,
            home_team_name="Real Madrid",
            away_team_name="Espanyol",
        )
        assert p is not None
        assert float(p) == pytest.approx(expectation.p_home_win)

    def test_h2h_away(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("Real Madrid", "Espanyol"),
            market_key="h2h",
            outcome_name="Espanyol",
            outcome_point=None,
            home_team_name="Real Madrid",
            away_team_name="Espanyol",
        )
        assert p is not None
        assert float(p) == pytest.approx(expectation.p_away_win)

    def test_h2h_draw(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("Real Madrid", "Espanyol"),
            market_key="h2h",
            outcome_name="Draw",
            outcome_point=None,
            home_team_name="Real Madrid",
            away_team_name="Espanyol",
        )
        assert p is not None
        assert float(p) == pytest.approx(expectation.p_draw)

    def test_h2h_unknown_team_returns_none(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("Real Madrid", "Espanyol"),
            market_key="h2h",
            outcome_name="Some Other Team",
            outcome_point=None,
            home_team_name="Real Madrid",
            away_team_name="Espanyol",
        )
        assert p is None

    def test_totals_over(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("Real Madrid", "Espanyol"),
            market_key="totals",
            outcome_name="Over",
            outcome_point=Decimal("2.5"),
            home_team_name="Real Madrid",
            away_team_name="Espanyol",
        )
        assert p is not None
        assert float(p) == pytest.approx(expectation.p_over_25, abs=1e-6)

    def test_totals_under(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("Real Madrid", "Espanyol"),
            market_key="totals",
            outcome_name="Under",
            outcome_point=Decimal("2.5"),
            home_team_name="Real Madrid",
            away_team_name="Espanyol",
        )
        assert p is not None
        assert float(p) == pytest.approx(expectation.p_under_25, abs=1e-6)

    def test_team_home_totals_matches_marginal_poisson(
        self, detector: ValueDetector, expectation: pm.MatchExpectation,
    ) -> None:
        po_side, pu_side = pm.probability_over_single_poisson(expectation.lambda_home, 2.5)
        p_over = detector._model_probability(
            expectation,
            self.mk_soccer_match("A", "B"),
            market_key="team_home_totals_goals",
            outcome_name="Over",
            outcome_point=Decimal("2.5"),
            home_team_name="A",
            away_team_name="B",
        )
        p_under = detector._model_probability(
            expectation,
            self.mk_soccer_match("A", "B"),
            market_key="team_home_totals_goals",
            outcome_name="Under",
            outcome_point=Decimal("2.5"),
            home_team_name="A",
            away_team_name="B",
        )
        assert p_over is not None and p_under is not None
        assert float(p_over) == pytest.approx(po_side, abs=1e-6)
        assert float(p_under) == pytest.approx(pu_side, abs=1e-6)

    def test_totals_alternative_line(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        # Línea 3.5 → over debe ser menor que en 2.5
        m_ab = self.mk_soccer_match("A", "B")
        p25 = detector._model_probability(
            expectation,
            m_ab,
            market_key="totals",
            outcome_name="Over",
            outcome_point=Decimal("2.5"),
            home_team_name="A", away_team_name="B",
        )
        p35 = detector._model_probability(
            expectation,
            m_ab,
            market_key="totals",
            outcome_name="Over",
            outcome_point=Decimal("3.5"),
            home_team_name="A", away_team_name="B",
        )
        assert p25 is not None and p35 is not None
        assert p25 > p35

    def test_returns_none_when_no_expectation(self, detector: ValueDetector) -> None:
        p = detector._model_probability(
            None,
            self.mk_soccer_match("A", "B"),
            market_key="h2h", outcome_name="A", outcome_point=None,
            home_team_name="A", away_team_name="B",
        )
        assert p is None

    def test_btts_yes(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("Real Madrid", "Espanyol"),
            market_key="btts", outcome_name="Yes", outcome_point=None,
            home_team_name="Real Madrid", away_team_name="Espanyol",
        )
        assert p is not None
        assert float(p) == pytest.approx(expectation.p_btts_yes, abs=1e-6)

    def test_btts_no(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("Real Madrid", "Espanyol"),
            market_key="btts", outcome_name="No", outcome_point=None,
            home_team_name="Real Madrid", away_team_name="Espanyol",
        )
        assert p is not None
        assert float(p) == pytest.approx(expectation.p_btts_no, abs=1e-6)

    def test_btts_unknown_outcome(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("A", "B"),
            market_key="btts", outcome_name="Maybe", outcome_point=None,
            home_team_name="A", away_team_name="B",
        )
        assert p is None

    def test_double_chance_home_or_draw(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("Real Madrid", "Espanyol"),
            market_key="double_chance",
            outcome_name="Real Madrid or Draw", outcome_point=None,
            home_team_name="Real Madrid", away_team_name="Espanyol",
        )
        assert p is not None
        assert float(p) == pytest.approx(expectation.p_home_or_draw, abs=1e-6)

    def test_double_chance_draw_or_away(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("Real Madrid", "Espanyol"),
            market_key="double_chance",
            outcome_name="Draw or Espanyol", outcome_point=None,
            home_team_name="Real Madrid", away_team_name="Espanyol",
        )
        assert p is not None
        assert float(p) == pytest.approx(expectation.p_draw_or_away, abs=1e-6)

    def test_double_chance_no_draw(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("Real Madrid", "Espanyol"),
            market_key="double_chance",
            outcome_name="Real Madrid or Espanyol", outcome_point=None,
            home_team_name="Real Madrid", away_team_name="Espanyol",
        )
        assert p is not None
        assert float(p) == pytest.approx(expectation.p_home_or_away, abs=1e-6)

    def test_double_chance_codes(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        # Aliases por código (1X / 12 / X2) también funcionan
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("A", "B"),
            market_key="double_chance",
            outcome_name="1X", outcome_point=None,
            home_team_name="A", away_team_name="B",
        )
        assert p is not None
        assert float(p) == pytest.approx(expectation.p_home_or_draw, abs=1e-6)

    def test_double_chance_higher_than_components(
        self, detector: ValueDetector, expectation: pm.MatchExpectation,
    ) -> None:
        # 1X siempre debe ser mayor que solo p_home_win
        rm = self.mk_soccer_match("Real Madrid", "Espanyol")
        p_dc = detector._model_probability(
            expectation,
            rm,
            market_key="double_chance",
            outcome_name="Real Madrid or Draw", outcome_point=None,
            home_team_name="Real Madrid", away_team_name="Espanyol",
        )
        p_home = detector._model_probability(
            expectation,
            rm,
            market_key="h2h", outcome_name="Real Madrid",
            outcome_point=None,
            home_team_name="Real Madrid", away_team_name="Espanyol",
        )
        assert p_dc is not None and p_home is not None
        assert p_dc > p_home

    def test_unsupported_market(self, detector: ValueDetector, expectation: pm.MatchExpectation) -> None:
        # Mercado completamente desconocido sigue devolviendo None
        p = detector._model_probability(
            expectation,
            self.mk_soccer_match("A", "B"),
            market_key="winner_first_half", outcome_name="Home", outcome_point=None,
            home_team_name="A", away_team_name="B",
        )
        assert p is None


class TestParsePlayerPropSide:
    def test_over_under_decimal(self) -> None:
        assert _parse_player_prop_side("Vinicius Jr over 1.5", Decimal("1.5")) == "over"
        assert _parse_player_prop_side("Vinicius Jr under 2.5", Decimal("2.5")) == "under"

    def test_mismatch_line_returns_none(self) -> None:
        assert _parse_player_prop_side("X over 1.5", Decimal("2.5")) is None
