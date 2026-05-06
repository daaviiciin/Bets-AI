"""Endpoints de perfiles y recomendaciones personalizadas."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.schemas.responses import (
    ParlayCandidateOut,
    ParlayLegOut,
    ParlayRecommendationOut,
    ParlaySetOut,
    RecommendationOut,
    RecommendationSetOut,
    UserProfileOut,
    ValueBetOut,
)
from backend.models import UserProfile
from backend.services.parlay_builder import (
    ParlayBuilder,
    ParlayCandidate,
    ParlayStrategy,
)
from backend.services.recommender import (
    ParlayRecommendation,
    Recommendation,
    Recommender,
)
from backend.services.value_detector import ValueDetector
from backend.services import value_calculator as vc

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("", response_model=list[UserProfileOut])
def list_profiles(db: Session = Depends(get_db)) -> list[UserProfile]:
    """Lista todos los perfiles disponibles."""
    return list(db.scalars(select(UserProfile).order_by(UserProfile.id)).all())


@router.get("/{name}", response_model=UserProfileOut)
def get_profile(name: str, db: Session = Depends(get_db)) -> UserProfile:
    """Detalle de un perfil por nombre."""
    profile = db.scalar(select(UserProfile).where(UserProfile.name == name))
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Perfil {name!r} no encontrado")
    return profile


@router.get("/{name}/recommendations", response_model=RecommendationSetOut)
def get_recommendations(
    name: str,
    horizon_hours: int = Query(14 * 24, ge=1, le=60 * 24),
    max_singles: int = Query(20, ge=1, le=100),
    max_parlays: int = Query(10, ge=0, le=50),
    strict: bool = Query(
        True,
        description=(
            "Si True (default), solo recomienda apuestas en las que mercado "
            "y modelo Poisson coinciden. Pasa false para ver todas las "
            "value bets de mercado (incluidas las que el modelo desaprueba)."
        ),
    ),
    db: Session = Depends(get_db),
) -> RecommendationSetOut:
    """Genera recomendaciones personalizadas para el perfil indicado."""
    profile = db.scalar(select(UserProfile).where(UserProfile.name == name))
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Perfil {name!r} no encontrado")

    recommender = Recommender(db)
    rs = recommender.recommend_for_profile(
        profile,
        horizon_hours=horizon_hours,
        max_singles=max_singles,
        max_parlays=max_parlays,
        strict=strict,
    )

    return RecommendationSetOut(
        profile_name=rs.profile_name,
        singles=[_serialize_single(r) for r in rs.singles],
        parlays=[_serialize_parlay(p) for p in rs.parlays],
    )


def _serialize_single(r: Recommendation) -> RecommendationOut:
    b = r.value_bet
    return RecommendationOut(
        value_bet=ValueBetOut(
            match_id=b.match_id,
            league=b.league,
            home_team=b.home_team,
            away_team=b.away_team,
            commence_time=b.commence_time,
            market_key=b.market_key,
            outcome_name=b.outcome_name,
            outcome_point=b.outcome_point,
            bookmaker_key=b.bookmaker_key,
            bookmaker_title=b.bookmaker_title,
            offered_odds=b.offered_odds,
            fair_probability=b.fair_probability,
            fair_odds=b.fair_odds,
            ev=b.ev,
            sharp_bookie_key=b.sharp_bookie_key,
            sharp_bookie_margin=b.sharp_bookie_margin,
        ),
        stake=r.stake,
        potential_profit=r.potential_profit,
        confidence_level=r.confidence_level,
        recommendation_label=r.recommendation_label,
        explanation_text=r.explanation_text,
    )


@router.get("/{name}/parlays", response_model=ParlaySetOut)
def get_smart_parlays(
    name: str,
    strategy: str = Query(
        "balanced",
        description="Estrategia: 'conservative', 'balanced' o 'aggressive'.",
    ),
    horizon_hours: int = Query(14 * 24, ge=1, le=60 * 24),
    max_results: int = Query(10, ge=1, le=30),
    db: Session = Depends(get_db),
) -> ParlaySetOut:
    """Combinadas inteligentes con correlación y diversificación.

    A diferencia de ``/recommendations``, este endpoint usa
    :class:`ParlayBuilder` con shrinkage por correlación, restricciones
    de diversificación y estrategias preconfiguradas.
    """
    profile = db.scalar(select(UserProfile).where(UserProfile.name == name))
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Perfil {name!r} no encontrado")

    strategy_obj = _strategy_from_str(strategy, profile)
    # Detector con threshold 0: dejamos que la estrategia haga el filtro
    # final. Si usásemos profile.min_ev (5-10%), filtraríamos demasiado
    # antes de que la estrategia pueda decidir.
    from decimal import Decimal as _D
    detector = ValueDetector(db, min_ev=_D("0"))
    value_bets = detector.detect_upcoming(horizon_hours=horizon_hours)
    builder = ParlayBuilder()
    candidates = builder.build(value_bets, strategy_obj, max_results=max_results)

    return ParlaySetOut(
        profile_name=profile.name,
        strategy=strategy_obj.name,
        parlays=[_serialize_candidate(c, profile) for c in candidates],
    )


def _strategy_from_str(name: str, profile: UserProfile) -> ParlayStrategy:
    name = (name or "").lower()
    if name == "conservative":
        return ParlayStrategy.conservative()
    if name == "aggressive":
        return ParlayStrategy.aggressive()
    if name == "balanced":
        return ParlayStrategy.balanced()
    if name in ("profile", "user_profile"):
        return ParlayStrategy.from_profile(profile)
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        f"Estrategia desconocida: {name!r}. Usa "
        "conservative/balanced/aggressive/profile.",
    )


def _serialize_candidate(
    c: ParlayCandidate, profile: UserProfile,
) -> ParlayCandidateOut:
    # Stake recomendado: Kelly fraccional sobre la probabilidad ajustada
    # (conservadora). Si el perfil tiene flat_stake, lo usamos.
    stake = profile.flat_stake or vc.recommended_stake(
        bankroll=profile.bankroll,
        offered_odds=c.combined_odds,
        fair_prob=c.adjusted_probability,
        kelly_modifier=profile.kelly_modifier,
        max_stake_pct=profile.max_stake_pct,
    )
    potential = (c.combined_odds - 1) * stake
    return ParlayCandidateOut(
        legs=[
            ParlayLegOut(
                match_id=leg.match_id,
                home_team=leg.home_team,
                away_team=leg.away_team,
                commence_time=leg.commence_time,
                market_key=leg.market_key,
                outcome_name=leg.outcome_name,
                bookmaker_key=leg.bookmaker_key,
                offered_odds=leg.offered_odds,
            )
            for leg in c.legs
        ],
        combined_odds=c.combined_odds,
        raw_combined_probability=c.raw_combined_probability,
        adjusted_probability=c.adjusted_probability,
        expected_value=c.expected_value,
        correlation_factor=c.correlation_factor,
        diversification_score=c.diversification_score,
        strategy_name=c.strategy_name,
        suggested_stake=stake,
        potential_profit=potential,
    )


def _serialize_parlay(p: ParlayRecommendation) -> ParlayRecommendationOut:
    return ParlayRecommendationOut(
        legs=[
            ParlayLegOut(
                match_id=leg.match_id,
                home_team=leg.home_team,
                away_team=leg.away_team,
                commence_time=leg.commence_time,
                market_key=leg.market_key,
                outcome_name=leg.outcome_name,
                bookmaker_key=leg.bookmaker_key,
                offered_odds=leg.offered_odds,
            )
            for leg in p.legs
        ],
        stake=p.stake,
        combined_odds=p.combined_odds,
        combined_probability=p.combined_probability,
        combined_ev=p.combined_ev,
        potential_profit=p.potential_profit,
    )
