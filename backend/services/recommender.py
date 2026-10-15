"""Recommender: filtra y prioriza value bets según el perfil del usuario.

Toma las value bets detectadas por ``ValueDetector`` y las personaliza:
* Aplica filtros por cuota mínima/máxima, EV mínimo y prob real mínima.
* Calcula el stake recomendado según la política del perfil (Kelly o flat).
* Si el perfil permite combinadas, sugiere parlays con +EV combinado.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.cli_utils import force_utf8_stdio
from backend.app.config import get_settings
from backend.app.database import get_session_factory
from backend.models import UserProfile
from backend.services import value_calculator as vc
from backend.services.value_detector import ValueBet, ValueDetector

CENTIMO = Decimal("0.01")


def _redondear(v: Decimal) -> Decimal:
    return v.quantize(CENTIMO, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Recommendation:
    """Una apuesta concreta recomendada para un perfil, con stake calculado.

    Además de la apuesta y el stake, lleva un *sello de calidad* en
    lenguaje claro para el usuario final:

    * ``confidence_level``: ``"alta"``/``"media"``/``"baja"``. Combina la
      coherencia entre mercado y modelo Poisson con la cuantía del valor
      detectado y la altura de la probabilidad real.
    * ``recommendation_label``: etiqueta corta ("Apuesta segura",
      "Equilibrada", "Arriesgada") pensada para mostrar como badge.
    * ``explanation_text``: 2-3 frases generadas automáticamente que
      explican POR QUÉ se recomienda, sin jerga técnica.
    """

    value_bet: ValueBet
    stake: Decimal
    confidence_level: str
    recommendation_label: str
    explanation_text: str

    @property
    def potential_profit(self) -> Decimal:
        return (self.value_bet.offered_odds - Decimal("1")) * self.stake


@dataclass(frozen=True)
class ParlayRecommendation:
    """Combinada (parlay) recomendada para un perfil."""

    legs: tuple[ValueBet, ...]
    stake: Decimal
    combined_odds: Decimal
    combined_probability: Decimal
    combined_ev: Decimal

    @property
    def potential_profit(self) -> Decimal:
        return (self.combined_odds - Decimal("1")) * self.stake


@dataclass
class RecommendationSet:
    """Conjunto de recomendaciones generadas para un perfil concreto."""

    profile_name: str
    singles: list[Recommendation] = field(default_factory=list)
    parlays: list[ParlayRecommendation] = field(default_factory=list)


class Recommender:
    """Genera recomendaciones personalizadas para un perfil."""

    def __init__(self, session: Session, *, log: logging.Logger | None = None) -> None:
        self.session = session
        self.log = log or logging.getLogger(__name__)

    def recommend_for_profile(
        self,
        profile: UserProfile,
        *,
        horizon_hours: int = 14 * 24,
        max_singles: int = 20,
        max_parlays: int = 10,
        strict: bool = True,
    ) -> RecommendationSet:
        """Genera recomendaciones para un perfil dado.

        :param strict: si ``True`` (por defecto), sólo se devuelven
            apuestas en las que **mercado y modelo Poisson coinciden**.
            Esto descarta las "trampas de cuotas" donde el bookie ha
            puesto una cuota generosa pero las estadísticas indican que
            es muy improbable que ocurra. Para ver TODAS las value bets
            sin este filtro, pasa ``strict=False``.
        """
        # Detector con threshold 0: la decisión final la toma el filtro
        # de perfil + el modo estricto (más fácil de razonar).
        detector = ValueDetector(self.session, min_ev=Decimal("0"), log=self.log)
        all_bets = detector.detect_upcoming(horizon_hours=horizon_hours)
        self.log.info("Detector: %d value bets en bruto", len(all_bets))

        filtered = [
            b for b in all_bets
            if self._passes_profile_filters(b, profile, strict=strict)
        ]
        self.log.info(
            "Filtradas para %s (strict=%s): %d value bets",
            profile.name, strict, len(filtered),
        )

        singles = self._build_singles(filtered, profile, max_count=max_singles)
        parlays: list[ParlayRecommendation] = []
        if profile.parlay_enabled and len(filtered) >= profile.parlay_legs:
            parlays = self._build_parlays(filtered, profile, max_count=max_parlays)

        return RecommendationSet(
            profile_name=profile.name, singles=singles, parlays=parlays,
        )

    def _passes_profile_filters(
        self, bet: ValueBet, profile: UserProfile, *, strict: bool,
    ) -> bool:
        if not (profile.odds_min <= bet.offered_odds <= profile.odds_max):
            return False
        if bet.fair_probability < profile.min_real_probability:
            return False
        if bet.ev < profile.min_ev:
            return False
        if strict:
            # Modelo y mercado deben coincidir: si el modelo Poisson
            # no aprueba (model_ev <= 0 o no disponible), la value bet
            # de mercado puede ser una trampa. La descartamos.
            if bet.model_ev is None or bet.model_ev <= Decimal("0"):
                return False
        return True

    def _build_singles(
        self, bets: list[ValueBet], profile: UserProfile, *, max_count: int,
    ) -> list[Recommendation]:
        recs: list[Recommendation] = []
        for bet in bets:
            stake = vc.recommended_stake(
                bankroll=profile.bankroll,
                offered_odds=bet.offered_odds,
                fair_prob=bet.fair_probability,
                kelly_modifier=profile.kelly_modifier,
                max_stake_pct=profile.max_stake_pct,
                flat_stake=profile.flat_stake,
            )
            if stake <= Decimal("0"):
                continue
            confidence, label = _classify(bet)
            explanation = _build_explanation(bet, label)
            recs.append(Recommendation(
                value_bet=bet,
                stake=_redondear(stake),
                confidence_level=confidence,
                recommendation_label=label,
                explanation_text=explanation,
            ))
        # Orden: primero las apuestas seguras, después por EV descendente.
        order_label = {"Apuesta segura": 0, "Equilibrada": 1, "Arriesgada": 2}
        recs.sort(key=lambda r: (
            order_label.get(r.recommendation_label, 9),
            -float(r.value_bet.ev),
        ))
        return recs[:max_count]

    def _build_parlays(
        self, bets: list[ValueBet], profile: UserProfile, *, max_count: int,
    ) -> list[ParlayRecommendation]:
        """Construye combinadas usando una pata por partido (no correlacionadas)."""
        n_legs = profile.parlay_legs
        if not bets:
            return []

        bet_pool = self._best_bet_per_match(bets)
        if len(bet_pool) < n_legs:
            return []

        candidates: list[ParlayRecommendation] = []
        for combo in combinations(bet_pool, n_legs):
            combined_odds, combined_prob = vc.parlay_odds_and_probability(
                [(b.offered_odds, b.fair_probability) for b in combo],
            )
            if profile.parlay_combined_odds_min is not None and combined_odds < profile.parlay_combined_odds_min:
                continue
            if profile.parlay_combined_odds_max is not None and combined_odds > profile.parlay_combined_odds_max:
                continue
            ev = vc.expected_value(combined_odds, combined_prob)
            if ev < profile.min_ev:
                continue
            stake = profile.flat_stake or vc.recommended_stake(
                bankroll=profile.bankroll,
                offered_odds=combined_odds,
                fair_prob=combined_prob,
                kelly_modifier=profile.kelly_modifier,
                max_stake_pct=profile.max_stake_pct,
            )
            candidates.append(ParlayRecommendation(
                legs=combo, stake=_redondear(stake),
                combined_odds=combined_odds,
                combined_probability=combined_prob,
                combined_ev=ev,
            ))

        candidates.sort(key=lambda p: p.combined_ev, reverse=True)
        return candidates[:max_count]

    def _best_bet_per_match(self, bets: list[ValueBet]) -> list[ValueBet]:
        """Para combinadas, tomamos la mejor apuesta por partido (mayor EV)."""
        by_match: dict[int, ValueBet] = {}
        for b in bets:
            current = by_match.get(b.match_id)
            if current is None or b.ev > current.ev:
                by_match[b.match_id] = b
        return list(by_match.values())


# ---------------------------------------------------------------------------
# Clasificación de calidad y explicación textual
# ---------------------------------------------------------------------------


def _classify(bet: ValueBet) -> tuple[str, str]:
    """Devuelve ``(confidence_level, recommendation_label)`` para una apuesta.

    Reglas (de más a menos restrictiva):

    * **alta / "Apuesta segura"**: modelo y mercado de acuerdo, probabilidad
      real ≥ 55 %, cuota ≤ 2.0 y EV de mercado ≥ 2 %.
    * **media / "Equilibrada"**: modelo de acuerdo y EV de mercado ≥ 1 %.
      O probabilidad real ≥ 50 % y cuota ≤ 2.5.
    * **baja / "Arriesgada"**: el resto (típicamente solo EV de mercado
      sin respaldo del modelo, o cuotas ≥ 3.0).
    """
    model_ok = bet.model_ev is not None and bet.model_ev > Decimal("0")
    high_prob = bet.fair_probability >= Decimal("0.55")
    medium_prob = bet.fair_probability >= Decimal("0.50")
    low_odds = bet.offered_odds <= Decimal("2.0")
    medium_odds = bet.offered_odds <= Decimal("2.5")
    decent_ev = bet.ev >= Decimal("0.01")

    # Apuesta segura: doble validación (mercado + modelo) + cuota razonable
    # + probabilidad alta. El EV no necesita ser grande si los demás
    # criterios se cumplen.
    if model_ok and high_prob and low_odds and decent_ev:
        return "alta", "Apuesta segura"
    if (model_ok and decent_ev) or (medium_prob and medium_odds):
        return "media", "Equilibrada"
    return "baja", "Arriesgada"


_MARKET_LABELS_ES = {
    "h2h": "ganador",
    "totals": "total de goles",
    "match_winner": "ganador",
    "match_double_chance": "doble oportunidad",
    "match_btts": "ambos marcan",
    "spreads": "hándicap",
}


def _market_label_es(market_key: str) -> str:
    return _MARKET_LABELS_ES.get(market_key, market_key)


def _build_explanation(bet: ValueBet, label: str) -> str:
    """Genera una explicación textual sin jerga técnica.

    Combina:

    1. Nombre amistoso del mercado.
    2. Probabilidad real estimada del suceso.
    3. Comparación cuota ofrecida vs cuota "justa".
    4. Si el modelo confirma la apuesta o no.
    """
    market_es = _market_label_es(bet.market_key)
    selection = bet.outcome_name
    if bet.outcome_point is not None and bet.market_key in ("totals", "match_totals"):
        selection = f"{bet.outcome_name} {bet.outcome_point}"

    prob_pct = float(bet.fair_probability) * 100
    fair_odds = float(bet.fair_odds)
    offered = float(bet.offered_odds)
    edge_pct = (offered / fair_odds - 1) * 100 if fair_odds > 0 else 0

    parts: list[str] = []

    # Frase 1: probabilidad estimada.
    parts.append(
        f"Estimamos {prob_pct:.0f}% de probabilidad de que ocurra "
        f"«{selection}» ({market_es})."
    )

    # Frase 2: comparación de cuotas.
    if edge_pct >= 5:
        parts.append(
            f"La cuota de {bet.bookmaker_title} ({offered:.2f}) está "
            f"{edge_pct:.0f}% por encima de la cuota justa ({fair_odds:.2f})."
        )
    elif edge_pct >= 2:
        parts.append(
            f"La cuota de {bet.bookmaker_title} ({offered:.2f}) es "
            f"ligeramente mejor que la justa ({fair_odds:.2f})."
        )
    else:
        parts.append(
            f"La cuota ofrecida ({offered:.2f}) tiene un valor pequeño "
            f"sobre la cuota justa ({fair_odds:.2f})."
        )

    # Frase 3: confirmación del modelo.
    if bet.model_ev is not None and bet.model_ev > Decimal("0"):
        model_pct = float(bet.model_probability or 0) * 100
        parts.append(
            f"Nuestro modelo basado en estadísticas también la aprueba "
            f"({model_pct:.0f}% según xG y forma reciente)."
        )
    elif bet.model_ev is not None:
        parts.append(
            "Atención: nuestro modelo basado en estadísticas no la respalda; "
            "es valor solo desde el punto de vista del mercado."
        )
    else:
        parts.append(
            "No hay suficientes estadísticas para que el modelo opine sobre "
            "esta apuesta."
        )

    # Contexto extra para underdogs visitantes (caso típico: "¿por qué
    # recomendar Mallorca fuera a cuota 4.20?").
    is_away_pick = bet.outcome_name.strip().lower() == bet.away_team.strip().lower()
    if is_away_pick and bet.offered_odds >= Decimal("3.0"):
        parts.append(
            "Es una selección visitante y de cuota alta: no es una apuesta "
            "frecuente para perfil promedio, pero entra cuando la cuota está "
            "claramente por encima de lo que marcan las probabilidades."
        )

    # Frase 4: aviso final según label.
    if label == "Apuesta segura":
        parts.append("Apuesta sólida con doble validación.")
    elif label == "Equilibrada":
        parts.append("Riesgo moderado, valor razonable.")
    else:
        parts.append("Cuota arriesgada: solo si entiendes el contexto.")

    return " ".join(parts)


def _imprimir_recomendaciones(rs: RecommendationSet) -> None:
    log = logging.getLogger(__name__)
    log.info("=" * 100)
    log.info("RECOMENDACIONES PARA EL PERFIL: %s", rs.profile_name)
    log.info("=" * 100)

    if rs.singles:
        log.info("APUESTAS INDIVIDUALES (top %d):", len(rs.singles))
        for r in rs.singles:
            b = r.value_bet
            outcome = b.outcome_name if b.market_key == "h2h" else f"{b.outcome_name} {b.outcome_point}"
            log.info("-" * 100)
            log.info(
                "[%s] %s vs %s | %s @ %s en %s",
                r.recommendation_label,
                b.home_team, b.away_team,
                outcome, b.offered_odds, b.bookmaker_title,
            )
            log.info(
                "    Probabilidad: %.0f%% | Cuota justa: %.2f | "
                "Apuesta sugerida: %.2f€ | Si gana: +%.2f€",
                float(b.fair_probability) * 100,
                float(b.fair_odds),
                float(r.stake),
                float(r.potential_profit),
            )
            log.info("    %s", r.explanation_text)
    else:
        log.info("Ninguna apuesta individual cumple los criterios del perfil.")

    if rs.parlays:
        log.info("")
        log.info("COMBINADAS RECOMENDADAS (top %d):", len(rs.parlays))
        for i, p in enumerate(rs.parlays, 1):
            log.info("-" * 100)
            log.info("Combinada #%d  cuota %.2f  P.real %.1f%%  EV %+.2f%%  stake %s€  posible %.2f€",
                     i, float(p.combined_odds), float(p.combined_probability) * 100,
                     float(p.combined_ev) * 100, f"{p.stake:.2f}", float(p.potential_profit))
            for leg in p.legs:
                outcome = leg.outcome_name if leg.market_key == "h2h" else f"{leg.outcome_name} {leg.outcome_point}"
                log.info("    + %s vs %s | %s %s @ %s en %s",
                         leg.home_team, leg.away_team,
                         leg.market_key, outcome, leg.offered_odds, leg.bookmaker_key)
    log.info("=" * 100)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera recomendaciones para un perfil.")
    parser.add_argument("--profile", default="Conservador",
                        help="Nombre del perfil (default: Conservador).")
    parser.add_argument("--horizon-hours", type=int, default=14 * 24)
    parser.add_argument("--max-singles", type=int, default=20)
    parser.add_argument("--max-parlays", type=int, default=10)
    args = parser.parse_args(argv)

    force_utf8_stdio()
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger(__name__)

    SessionLocal = get_session_factory()
    with SessionLocal() as session:
        profile = session.scalar(select(UserProfile).where(UserProfile.name == args.profile))
        if profile is None:
            log.error("No existe el perfil %r. Lánzate `python -m backend.services.seed_profiles`.",
                      args.profile)
            return 1

        recommender = Recommender(session, log=log)
        rs = recommender.recommend_for_profile(
            profile,
            horizon_hours=args.horizon_hours,
            max_singles=args.max_singles,
            max_parlays=args.max_parlays,
        )

    _imprimir_recomendaciones(rs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
