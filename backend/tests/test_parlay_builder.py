"""Tests del :class:`ParlayBuilder` con value bets sintéticas."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backend.services.parlay_builder import (
    ParlayBuilder,
    ParlayStrategy,
    _market_family,
)
from backend.services.value_detector import ValueBet


def _bet(
    *,
    match_id: int,
    league: str = "soccer_spain_la_liga",
    home_team: str = "Home",
    away_team: str = "Away",
    market_key: str = "h2h",
    outcome_name: str = "Home",
    offered_odds: str = "1.55",
    fair_probability: str = "0.65",
    model_probability: str | None = "0.65",
    commence_time: datetime | None = None,
) -> ValueBet:
    """Helper para crear ValueBets sintéticas con valores razonables."""
    odds = Decimal(offered_odds)
    fair = Decimal(fair_probability)
    ev = odds * fair - Decimal("1")
    fair_odds = Decimal("1") / fair if fair > Decimal("0") else Decimal("0")
    model_prob = Decimal(model_probability) if model_probability is not None else None
    model_ev = (odds * model_prob - Decimal("1")) if model_prob is not None else None
    model_fair_odds = (Decimal("1") / model_prob) if model_prob is not None and model_prob > Decimal("0") else None
    return ValueBet(
        match_id=match_id,
        league=league,
        home_team=home_team,
        away_team=away_team,
        commence_time=commence_time or datetime(2026, 5, 10, 17, 0, tzinfo=timezone.utc),
        market_key=market_key,
        outcome_name=outcome_name,
        outcome_point=None,
        bookmaker_key="winamax_es",
        bookmaker_title="Winamax (ES)",
        offered_odds=odds,
        fair_probability=fair,
        fair_odds=fair_odds,
        ev=ev,
        sharp_bookie_key="pinnacle",
        sharp_bookie_margin=Decimal("0.02"),
        model_probability=model_prob,
        model_fair_odds=model_fair_odds,
        model_ev=model_ev,
        model_agrees=(model_ev is not None and model_ev > Decimal("0")),
        confidence="high",
    )


# ---------------------------------------------------------------------------
# ParlayStrategy presets
# ---------------------------------------------------------------------------


class TestStrategyPresets:
    def test_balanced_aligns_with_user_profile(self) -> None:
        s = ParlayStrategy.balanced()
        # Usuario: cuotas 1.40-1.70 individuales, totales 3-4.
        assert s.min_legs == 3 and s.max_legs == 3
        assert s.leg_odds_min == Decimal("1.40")
        assert s.leg_odds_max == Decimal("1.70")
        assert s.combined_odds_min == Decimal("3.00")
        assert s.combined_odds_max == Decimal("4.50")

    def test_conservative_two_legs(self) -> None:
        s = ParlayStrategy.conservative()
        assert s.min_legs == 2 and s.max_legs == 2

    def test_aggressive_more_variance(self) -> None:
        s = ParlayStrategy.aggressive()
        assert s.max_legs >= 3
        assert s.combined_odds_max >= Decimal("10")
        assert not s.require_model_agreement


# ---------------------------------------------------------------------------
# Pre-filtrado de legs
# ---------------------------------------------------------------------------


class TestPreFilter:
    def test_drops_legs_outside_odds_range(self) -> None:
        bets = [
            _bet(match_id=1, offered_odds="1.20"),  # demasiado baja
            _bet(match_id=2, offered_odds="2.50"),  # demasiado alta para conservative
            _bet(match_id=3, offered_odds="1.55"),  # OK
        ]
        out = ParlayBuilder().build(bets, ParlayStrategy.conservative())
        # Sólo queda 1 leg viable → no hay parlays posibles (necesita 2 legs)
        assert out == []

    def test_drops_legs_with_low_ev(self) -> None:
        bets = [
            _bet(match_id=1, offered_odds="1.45", fair_probability="0.50"),  # EV negativo
            _bet(match_id=2, offered_odds="1.45", fair_probability="0.70"),  # EV positivo
        ]
        out = ParlayBuilder().build(bets, ParlayStrategy.conservative())
        assert out == []  # no se forma parlay con 1 sola leg viable

    def test_drops_legs_when_model_disagrees_in_strict_mode(self) -> None:
        # En conservadora se exige model_agreement.
        bet_a = _bet(match_id=1, offered_odds="1.55", fair_probability="0.70",
                     model_probability="0.40")  # modelo da EV negativo
        bet_b = _bet(match_id=2, offered_odds="1.55", fair_probability="0.70",
                     model_probability="0.70")
        out = ParlayBuilder().build(
            [bet_a, bet_b], ParlayStrategy.conservative(),
        )
        assert out == []  # bet_a descartada → no hay 2 legs

    def test_nunca_mete_una_pata_de_mas_de_4_aunque_la_estrategia_lo_permita(self) -> None:
        # Como ``from_profile`` con un perfil de odds_max=20: el rango de la estrategia no basta.
        amplia = ParlayStrategy(
            name="amplia", min_legs=2, max_legs=2,
            leg_odds_max=Decimal("20"), leg_min_fair_prob=Decimal("0"),
            combined_odds_min=Decimal("1"), combined_odds_max=Decimal("999"),
        )
        bets = [
            _bet(match_id=1, league="soccer_epl", market_key="h2h",
                 offered_odds="1.80", fair_probability="0.60"),
            _bet(match_id=2, league="soccer_spain_la_liga", market_key="totals",
                 outcome_name="Over", offered_odds="6.00", fair_probability="0.20"),
            _bet(match_id=3, league="soccer_germany_bundesliga", market_key="match_btts",
                 outcome_name="Yes", offered_odds="4.00", fair_probability="0.30"),
        ]
        out = ParlayBuilder().build(bets, amplia)
        assert [sorted(leg.match_id for leg in c.legs) for c in out] == [[1, 3]]


# ---------------------------------------------------------------------------
# Combinaciones, diversificación, correlación
# ---------------------------------------------------------------------------


class TestParlayConstruction:
    def _three_independent_bets(self) -> list[ValueBet]:
        # 3 partidos en 3 ligas distintas, mismo día (penalización same_day).
        d1 = datetime(2026, 5, 10, 17, 0, tzinfo=timezone.utc)
        return [
            _bet(match_id=1, league="soccer_spain_la_liga",
                 market_key="h2h", offered_odds="1.55",
                 fair_probability="0.70", model_probability="0.70",
                 commence_time=d1),
            _bet(match_id=2, league="soccer_epl",
                 market_key="totals", outcome_name="Over",
                 offered_odds="1.55", fair_probability="0.70",
                 model_probability="0.70",
                 commence_time=d1),
            _bet(match_id=3, league="soccer_germany_bundesliga",
                 market_key="match_btts", outcome_name="Yes",
                 offered_odds="1.50", fair_probability="0.70",
                 model_probability="0.70",
                 commence_time=d1),
        ]

    def test_balanced_builds_3leg_combination(self) -> None:
        bets = self._three_independent_bets()
        out = ParlayBuilder().build(bets, ParlayStrategy.balanced())
        assert len(out) == 1
        c = out[0]
        assert len(c.legs) == 3
        # Cuota combinada: 1.55 * 1.55 * 1.50 = 3.60375
        assert c.combined_odds == pytest.approx(Decimal("3.60375"))
        assert c.expected_value > Decimal("0")
        assert c.strategy_name == "balanced"

    def test_correlation_penalty_applied_when_same_day(self) -> None:
        bets = self._three_independent_bets()
        out = ParlayBuilder().build(bets, ParlayStrategy.balanced())
        c = out[0]
        # Hay correlación same_day (3 patas mismo día) → factor 0.97.
        # No same_league (3 ligas distintas) ni same_market (h2h, totals, btts).
        assert c.correlation_factor == pytest.approx(Decimal("0.97"))
        assert c.adjusted_probability < c.raw_combined_probability

    def test_diversification_blocks_two_overs_in_balanced(self) -> None:
        d = datetime(2026, 5, 10, 17, 0, tzinfo=timezone.utc)
        bets = [
            _bet(match_id=1, league="soccer_epl", market_key="totals",
                 outcome_name="Over", offered_odds="1.55",
                 fair_probability="0.70", model_probability="0.70",
                 commence_time=d),
            _bet(match_id=2, league="soccer_spain_la_liga", market_key="totals",
                 outcome_name="Over", offered_odds="1.55",
                 fair_probability="0.70", model_probability="0.70",
                 commence_time=d),
            _bet(match_id=3, league="soccer_germany_bundesliga", market_key="totals",
                 outcome_name="Over", offered_odds="1.50",
                 fair_probability="0.70", model_probability="0.70",
                 commence_time=d),
        ]
        # En balanced, max_legs_same_market=1: NO debe formarse la parlay 3-overs.
        out = ParlayBuilder().build(bets, ParlayStrategy.balanced())
        assert out == []

    def test_diversification_allows_in_aggressive(self) -> None:
        # En aggressive max_legs_same_market=2 (permite 2 overs).
        # Cuotas suficientemente altas para superar combined_odds_min=4.0.
        d = datetime(2026, 5, 10, 17, 0, tzinfo=timezone.utc)
        bets = [
            _bet(match_id=1, league="soccer_epl", market_key="totals",
                 outcome_name="Over", offered_odds="1.75",
                 fair_probability="0.62", commence_time=d),
            _bet(match_id=2, league="soccer_spain_la_liga", market_key="totals",
                 outcome_name="Over", offered_odds="1.75",
                 fair_probability="0.62", commence_time=d),
            _bet(match_id=3, league="soccer_germany_bundesliga", market_key="h2h",
                 outcome_name="Home", offered_odds="1.65",
                 fair_probability="0.65", commence_time=d),
        ]
        out = ParlayBuilder().build(bets, ParlayStrategy.aggressive())
        assert len(out) >= 1
        # Verifica que la combinación incluye los dos "totals" (2 mismo mercado)
        c = out[0]
        market_keys = [leg.market_key for leg in c.legs]
        assert market_keys.count("totals") == 2

    def test_only_one_leg_per_match(self) -> None:
        # Si pasamos dos value bets del mismo partido, solo se conserva una.
        d = datetime(2026, 5, 10, 17, 0, tzinfo=timezone.utc)
        same_match = [
            _bet(match_id=1, league="soccer_epl", market_key="h2h",
                 outcome_name="Home", offered_odds="1.50",
                 fair_probability="0.70", commence_time=d),
            _bet(match_id=1, league="soccer_epl", market_key="totals",
                 outcome_name="Over", offered_odds="1.60",
                 fair_probability="0.70", commence_time=d),  # mejor EV
            _bet(match_id=2, league="soccer_spain_la_liga", market_key="h2h",
                 outcome_name="Home", offered_odds="1.55",
                 fair_probability="0.70", commence_time=d),
            _bet(match_id=3, league="soccer_germany_bundesliga", market_key="match_btts",
                 outcome_name="Yes", offered_odds="1.55",
                 fair_probability="0.70", commence_time=d),
        ]
        out = ParlayBuilder().build(same_match, ParlayStrategy.balanced())
        assert len(out) == 1
        # La leg conservada del partido 1 tiene odds=1.60 (mayor EV).
        c = out[0]
        for leg in c.legs:
            if leg.match_id == 1:
                assert leg.offered_odds == Decimal("1.60")

    def test_filters_out_when_total_odds_outside_range(self) -> None:
        # Tres legs con cuotas 1.40 → total = 2.74, FUERA de [3.0, 4.5] balanced.
        d = datetime(2026, 5, 10, 17, 0, tzinfo=timezone.utc)
        bets = [
            _bet(match_id=1, league="soccer_epl", market_key="h2h",
                 offered_odds="1.40", fair_probability="0.75",
                 model_probability="0.75", commence_time=d),
            _bet(match_id=2, league="soccer_spain_la_liga", market_key="totals",
                 outcome_name="Over", offered_odds="1.40",
                 fair_probability="0.75", model_probability="0.75", commence_time=d),
            _bet(match_id=3, league="soccer_germany_bundesliga", market_key="match_btts",
                 outcome_name="Yes", offered_odds="1.40", fair_probability="0.75",
                 model_probability="0.75", commence_time=d),
        ]
        out = ParlayBuilder().build(bets, ParlayStrategy.balanced())
        assert out == []  # 1.40^3 = 2.744 < 3.00

    def test_results_sorted_by_ev_desc(self) -> None:
        d = datetime(2026, 5, 10, 17, 0, tzinfo=timezone.utc)
        # 4 partidos → C(4,3) = 4 combinaciones, todas válidas.
        bets = [
            _bet(match_id=1, league="soccer_epl", market_key="h2h",
                 offered_odds="1.50", fair_probability="0.70",
                 model_probability="0.70", commence_time=d),
            _bet(match_id=2, league="soccer_spain_la_liga", market_key="totals",
                 outcome_name="Over", offered_odds="1.55",
                 fair_probability="0.70", model_probability="0.70", commence_time=d),
            _bet(match_id=3, league="soccer_germany_bundesliga", market_key="match_btts",
                 outcome_name="Yes", offered_odds="1.60",
                 fair_probability="0.65", model_probability="0.65", commence_time=d),
            _bet(match_id=4, league="soccer_italy_serie_a", market_key="match_double_chance",
                 outcome_name="1X", offered_odds="1.45", fair_probability="0.75",
                 model_probability="0.75", commence_time=d),
        ]
        out = ParlayBuilder().build(bets, ParlayStrategy.balanced(), max_results=10)
        evs = [c.expected_value for c in out]
        assert evs == sorted(evs, reverse=True)


# ---------------------------------------------------------------------------
# Helper de mercado
# ---------------------------------------------------------------------------


class TestMarketFamily:
    def test_h2h_and_match_winner_dnb_are_same_family(self) -> None:
        b1 = _bet(match_id=1, market_key="h2h")
        b2 = _bet(match_id=2, market_key="match_winner_dnb")
        assert _market_family(b1) == _market_family(b2) == "match_winner"

    def test_player_shots_and_sot_are_player_offense(self) -> None:
        b1 = _bet(match_id=1, market_key="player_shots")
        b2 = _bet(match_id=2, market_key="player_sot_alt")
        b3 = _bet(match_id=3, market_key="player_anytime_scorer")
        for b in (b1, b2, b3):
            assert _market_family(b) == "player_offense"

    def test_unknown_market_falls_back_to_self(self) -> None:
        b = _bet(match_id=1, market_key="totally_new_market")
        assert _market_family(b) == "totally_new_market"
