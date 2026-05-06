"""Endpoints de value bets (apuestas de valor detectadas)."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.schemas.responses import ValueBetOut
from backend.services.value_detector import ValueDetector

router = APIRouter(prefix="/value-bets", tags=["value-bets"])


@router.get("", response_model=list[ValueBetOut])
def list_value_bets(
    min_ev: float = Query(0.02, ge=0.0, le=1.0, description="EV mínimo de mercado (0.02 = +2%)"),
    min_ev_model: float = Query(0.0, ge=0.0, le=1.0, description="EV mínimo del modelo Poisson"),
    require_model_agreement: bool = Query(
        False,
        description="Si True, solo devuelve apuestas que también pasan el modelo Poisson.",
    ),
    horizon_hours: int = Query(14 * 24, ge=1, le=60 * 24),
    league: str | None = Query(None, description="Sport key de la liga"),
    top: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[ValueBetOut]:
    """Devuelve las value bets detectadas en partidos próximos.

    Doble criterio
    --------------
    Cada apuesta se evalúa contra:

    * **EV de mercado**: usando la probabilidad real implícita en el bookie
      afilado (el de menor margen).
    * **EV del modelo Poisson**: si tenemos stats del partido, calculamos la
      probabilidad teórica con el modelo y comparamos con la cuota ofrecida.

    El campo ``confidence`` resume la concordancia:

    * ``high``: ambos EV muy positivos (≥ ~5%) → apuesta robusta.
    * ``medium``: ambos positivos pero moderados.
    * ``low``: solo el mercado detecta valor; el modelo no está de acuerdo.
    * ``unknown``: no tenemos stats para predecir (típico en competiciones
      europeas no scrapeadas, como Champions).
    """
    detector = ValueDetector(
        db,
        min_ev=Decimal(str(min_ev)),
        min_ev_model=Decimal(str(min_ev_model)),
        require_model_agreement=require_model_agreement,
    )
    bets = detector.detect_upcoming(horizon_hours=horizon_hours, league_sport_key=league)
    return [
        ValueBetOut(
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
            model_probability=b.model_probability,
            model_fair_odds=b.model_fair_odds,
            model_ev=b.model_ev,
            model_agrees=b.model_agrees,
            confidence=b.confidence,
        )
        for b in bets[:top]
    ]
