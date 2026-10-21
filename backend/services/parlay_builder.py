"""Constructor de parlays inteligentes (combinadas) con value betting.

Diferencia frente al cálculo "ingenuo" que asume independencia perfecta:

1. **Diversificación**: evita que dos legs de la misma combinada sean del
   mismo "tipo" (p.ej. dos overs, dos victorias visitantes). Eso reduciría
   diversificación efectiva sin ofrecer correlación positiva clara.
2. **Penalty de correlación**: aplica un *shrinkage* a la probabilidad
   conjunta cuando las legs tienen mayor riesgo de correlación (mismo
   día, misma liga, mismo mercado). Es un haircut conservador para no
   sobreestimar el EV de combinadas correlacionadas implícitamente.
3. **Estrategias** preconfiguradas que se ajustan al perfil del usuario
   (Conservador, Equilibrado, Agresivo). Son distintas envolventes sobre
   las restricciones (rango cuotas individuales, total y nº legs).

El builder se queda al margen del modelo de probabilidad: confía en lo que
ya viene en cada :class:`ValueBet` (``fair_probability`` y opcionalmente
``model_probability``). Su trabajo es **seleccionar** y **componer**
combinadas con criterio de cartera.

Ejemplo
-------

>>> from backend.services.parlay_builder import (
...     ParlayBuilder, ParlayStrategy,
... )
>>> builder = ParlayBuilder()
>>> parlays = builder.build(value_bets, ParlayStrategy.balanced())
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from typing import Iterable

from backend.services import value_calculator as vc
from backend.services.value_detector import ValueBet

ZERO = Decimal("0")
ONE = Decimal("1")

# ---------------------------------------------------------------------------
# Estrategias de combinada (envolventes sobre restricciones)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParlayStrategy:
    """Conjunto de restricciones que define una "estrategia" de combinada.

    Las restricciones son sobre legs individuales (rango cuotas, EV
    mínimo) y sobre la combinada resultante (rango cuota total, nº legs).
    """

    name: str
    min_legs: int = 2
    max_legs: int = 3
    leg_odds_min: Decimal = Decimal("1.30")
    leg_odds_max: Decimal = Decimal("2.20")
    leg_min_ev: Decimal = Decimal("0.02")
    leg_min_fair_prob: Decimal = Decimal("0.40")
    combined_odds_min: Decimal = Decimal("2.50")
    combined_odds_max: Decimal = Decimal("8.00")
    require_model_agreement: bool = False
    correlation_penalty_same_day: Decimal = Decimal("0.97")    # 3 % shrinkage
    correlation_penalty_same_league: Decimal = Decimal("0.99")  # 1 % shrinkage
    correlation_penalty_same_market: Decimal = Decimal("0.97")  # 3 % shrinkage
    max_legs_same_market: int = 1   # diversificación: 1 over por combinada
    max_legs_same_league: int = 2   # max 2 legs en la misma liga

    @classmethod
    def conservative(cls) -> "ParlayStrategy":
        """Doble seguro: 2 legs, cuotas 1.40-1.70, total 1.96-2.89.

        Pensada para el perfil del usuario: 5 € → ~10-15 € de retorno
        con tasa de éxito alta y EV moderado.
        """
        return cls(
            name="conservative",
            min_legs=2, max_legs=2,
            leg_odds_min=Decimal("1.40"), leg_odds_max=Decimal("1.75"),
            leg_min_ev=Decimal("0.01"),
            leg_min_fair_prob=Decimal("0.55"),
            combined_odds_min=Decimal("1.90"), combined_odds_max=Decimal("3.10"),
            require_model_agreement=True,
            max_legs_same_market=1,
            max_legs_same_league=1,
        )

    @classmethod
    def balanced(cls) -> "ParlayStrategy":
        """Triple equilibrado: 3 legs con cuotas 1.45-1.65, total 3-4.

        Es el sweet spot que el usuario describió: cuotas moderadas
        combinadas para sacar 3-4 totales con riesgo razonable.

        Nota: ``require_model_agreement=False`` por defecto porque el
        modelo Poisson actual es algo ruidoso y descartaría el 80 % de
        las parlays del rango del usuario. Se mantiene la diversificación
        y la penalización de correlación como red de seguridad.
        """
        return cls(
            name="balanced",
            min_legs=3, max_legs=3,
            leg_odds_min=Decimal("1.40"), leg_odds_max=Decimal("1.70"),
            leg_min_ev=Decimal("0.01"),
            leg_min_fair_prob=Decimal("0.55"),
            combined_odds_min=Decimal("3.00"), combined_odds_max=Decimal("4.50"),
            require_model_agreement=False,
            max_legs_same_market=1,
            max_legs_same_league=2,
        )

    @classmethod
    def from_profile(cls, profile) -> "ParlayStrategy":  # type: ignore[no-untyped-def]
        """Estrategia construida a partir de los campos de un ``UserProfile``.

        Permite que la combinada respete *exactamente* las preferencias del
        usuario (rango cuotas individuales, total y nº de legs) en lugar
        de uno de los presets fijos.
        """
        n_legs = int(profile.parlay_legs or 2)
        return cls(
            name=f"profile_{profile.name.lower()}",
            min_legs=n_legs, max_legs=n_legs,
            leg_odds_min=Decimal(profile.odds_min),
            leg_odds_max=Decimal(profile.odds_max),
            leg_min_ev=Decimal(profile.min_ev or 0),
            leg_min_fair_prob=Decimal(profile.min_real_probability or 0),
            combined_odds_min=Decimal(profile.parlay_combined_odds_min or 0),
            combined_odds_max=Decimal(profile.parlay_combined_odds_max or 999),
            require_model_agreement=False,
            max_legs_same_market=1,
            max_legs_same_league=max(2, n_legs - 1),
        )

    @classmethod
    def aggressive(cls) -> "ParlayStrategy":
        """Cazador de premios: 3-4 legs, cuotas 1.30-2.20, total 5-15.

        Más varianza pero mayor potencial de retorno. Para perfiles que
        toleran perder más a menudo a cambio de cuotas grandes.
        """
        return cls(
            name="aggressive",
            min_legs=3, max_legs=4,
            leg_odds_min=Decimal("1.30"), leg_odds_max=Decimal("2.20"),
            leg_min_ev=Decimal("0.01"),
            leg_min_fair_prob=Decimal("0.40"),
            combined_odds_min=Decimal("4.00"), combined_odds_max=Decimal("15.00"),
            require_model_agreement=False,
            max_legs_same_market=3,
            max_legs_same_league=3,
        )


@dataclass(frozen=True)
class ParlayCandidate:
    """Una combinada candidata, lista para presentar al usuario."""
    legs: tuple[ValueBet, ...]
    combined_odds: Decimal
    raw_combined_probability: Decimal     # asumiendo independencia
    adjusted_probability: Decimal         # tras correlation penalty
    expected_value: Decimal
    correlation_factor: Decimal           # < 1 si hay penalty aplicada
    diversification_score: Decimal        # 0-1, donde 1 = 100 % diversificada
    strategy_name: str

    @property
    def potential_payout(self) -> Decimal:
        """Pago si todas las legs aciertan (sobre stake = 1)."""
        return self.combined_odds


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


@dataclass
class ParlayBuilder:
    """Genera combinadas inteligentes a partir de value bets detectadas."""

    max_combinations_to_explore: int = 5_000

    def build(
        self,
        value_bets: Iterable[ValueBet],
        strategy: ParlayStrategy,
        *,
        max_results: int = 10,
    ) -> list[ParlayCandidate]:
        """Construye combinadas según la estrategia. Devuelve top ``max_results``.

        Pasos:

        1. **Pre-filtrado** de legs por cuota individual + EV mínimo.
        2. **Best-per-match** (una sola pata por partido para evitar
           combinaciones del mismo evento — eso es siempre correlación
           perfecta y ningún bookie las acepta así).
        3. **Generación** de todas las combinaciones de tamaño en
           ``[min_legs, max_legs]`` que cumplan diversificación.
        4. **Cálculo** de cuota combinada, prob ajustada por correlación,
           EV y score de diversificación.
        5. **Filtrado** por rango cuota total y EV positivo.
        6. **Ordenación** por EV descendente y truncado a ``max_results``.
        """
        legs = self._prefilter_legs(value_bets, strategy)
        if len(legs) < strategy.min_legs:
            return []

        # Best-per-match: 1 pata por partido (la de mayor EV de mercado).
        legs = self._best_leg_per_match(legs)

        results: list[ParlayCandidate] = []
        explored = 0
        for size in range(strategy.min_legs, strategy.max_legs + 1):
            if len(legs) < size:
                continue
            for combo in combinations(legs, size):
                explored += 1
                if explored > self.max_combinations_to_explore:
                    break
                if not self._diversification_ok(combo, strategy):
                    continue
                candidate = self._evaluate(combo, strategy)
                if candidate is None:
                    continue
                results.append(candidate)
            if explored > self.max_combinations_to_explore:
                break

        results.sort(key=lambda c: c.expected_value, reverse=True)
        return results[:max_results]

    # -- Internos ---------------------------------------------------------

    def _prefilter_legs(
        self, bets: Iterable[ValueBet], strategy: ParlayStrategy,
    ) -> list[ValueBet]:
        out: list[ValueBet] = []
        for b in bets:
            # Ninguna pata por encima de MAX_RECOMMENDABLE_ODDS, aunque la estrategia lo permita
            # (``from_profile`` usa el ``odds_max`` del perfil, que puede llegar a 20).
            if not vc.is_recommendable_odds(b.offered_odds):
                continue
            if not (strategy.leg_odds_min <= b.offered_odds <= strategy.leg_odds_max):
                continue
            if b.fair_probability < strategy.leg_min_fair_prob:
                continue
            if b.ev < strategy.leg_min_ev:
                continue
            if strategy.require_model_agreement:
                # Sólo aceptamos si el modelo también aprueba (model_ev > 0).
                if b.model_ev is None or b.model_ev <= ZERO:
                    continue
            out.append(b)
        return out

    @staticmethod
    def _best_leg_per_match(bets: Iterable[ValueBet]) -> list[ValueBet]:
        by_match: dict[int, ValueBet] = {}
        for b in bets:
            current = by_match.get(b.match_id)
            if current is None or b.ev > current.ev:
                by_match[b.match_id] = b
        return list(by_match.values())

    @staticmethod
    def _diversification_ok(
        combo: tuple[ValueBet, ...], strategy: ParlayStrategy,
    ) -> bool:
        # Misma pata exacta (mismo match) ya está bloqueado por _best_leg_per_match.
        # Limitamos máximo de legs en mismo mercado (p. ej. 2 overs en parlay).
        markets: dict[str, int] = {}
        leagues: dict[str, int] = {}
        for b in combo:
            mkt = _market_family(b)
            markets[mkt] = markets.get(mkt, 0) + 1
            if markets[mkt] > strategy.max_legs_same_market:
                return False
            leagues[b.league] = leagues.get(b.league, 0) + 1
            if leagues[b.league] > strategy.max_legs_same_league:
                return False
        return True

    @staticmethod
    def _evaluate(
        combo: tuple[ValueBet, ...], strategy: ParlayStrategy,
    ) -> ParlayCandidate | None:
        # Independence baseline
        raw_odds, raw_prob = vc.parlay_odds_and_probability(
            [(b.offered_odds, b.fair_probability) for b in combo]
        )
        if not (strategy.combined_odds_min <= raw_odds <= strategy.combined_odds_max):
            return None

        # Correlation penalty: aplicamos haircuts si hay legs del mismo
        # día / liga / mercado. Sólo penalty si el numero de legs > 1 que
        # comparten el factor.
        penalty = ONE
        days_seen = {b.commence_time.date() for b in combo}
        leagues_seen = {b.league for b in combo}
        markets_seen = {_market_family(b) for b in combo}
        if len(days_seen) < len(combo):
            penalty *= strategy.correlation_penalty_same_day
        if len(leagues_seen) < len(combo):
            penalty *= strategy.correlation_penalty_same_league
        if len(markets_seen) < len(combo):
            penalty *= strategy.correlation_penalty_same_market

        adjusted_prob = raw_prob * penalty
        ev = raw_odds * adjusted_prob - ONE
        if ev <= ZERO:
            return None

        # Diversification score: combinación de ratios.
        n = Decimal(len(combo))
        diversity = (
            (Decimal(len(days_seen)) / n)
            * (Decimal(len(leagues_seen)) / n)
            * (Decimal(len(markets_seen)) / n)
        )

        return ParlayCandidate(
            legs=combo,
            combined_odds=raw_odds,
            raw_combined_probability=raw_prob,
            adjusted_probability=adjusted_prob,
            expected_value=ev,
            correlation_factor=penalty,
            diversification_score=diversity,
            strategy_name=strategy.name,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Familias de mercados: para diversificación, "totals 2.5 over" y "totals 1.5
# over" cuentan como mismo mercado y por defecto sólo se permite 1 leg.
_MARKET_FAMILY_OVERRIDES: dict[str, str] = {
    "h2h": "match_winner",
    "h2h_lay": "match_winner",
    "match_winner": "match_winner",
    "match_winner_dnb": "match_winner",
    "match_double_chance": "match_winner",
    "totals": "totals_goals",
    "match_totals": "totals_goals",
    "team_home_totals_goals": "totals_goals",
    "team_away_totals_goals": "totals_goals",
    "match_btts": "btts",
    "match_corners_totals": "corners",
    "match_cards_totals": "cards",
    "match_first_half_totals": "totals_goals",
    "team_home_first_half_totals_goals": "totals_goals",
    "team_away_first_half_totals_goals": "totals_goals",
    "match_first_half_corners_totals": "corners",
    "match_first_half_cards_totals": "cards",
    "player_card_yes_no": "cards",
    "match_shots_totals": "shots",
    "match_sot_totals": "shots",
    "player_shots": "player_offense",
    "player_shots_alt": "player_offense",
    "player_sot": "player_offense",
    "player_sot_alt": "player_offense",
    "player_anytime_scorer": "player_offense",
    "player_to_score_2plus": "player_offense",
}


def _market_family(bet: ValueBet) -> str:
    """Agrupa mercados similares para diversificación."""
    return _MARKET_FAMILY_OVERRIDES.get(bet.market_key, bet.market_key)


__all__ = [
    "ParlayBuilder",
    "ParlayCandidate",
    "ParlayStrategy",
]
