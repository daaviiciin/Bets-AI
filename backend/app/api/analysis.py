"""Endpoint para evaluar cuotas/combinadas manuales frente al modelo estadístico."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.schemas.analysis import (
    CustomBetAssessmentRequest,
    CustomBetAssessmentResponse,
)
from backend.services import bet_assessment_service as bas

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post(
    "/custom-bet",
    response_model=CustomBetAssessmentResponse,
    status_code=status.HTTP_200_OK,
)
def post_custom_bet_assessment(
    body: CustomBetAssessmentRequest,
    db: Session = Depends(get_db),
) -> CustomBetAssessmentResponse:
    """Calcula probabilidad implícita, encaja con modelo (fútbol/basket MVP) si hay ``match_id``."""
    payload = bas.assess_custom_bet(
        db,
        body.legs,
        match_id=body.match_id,
        assume_independent=body.assume_independent,
    )
    return CustomBetAssessmentResponse.model_validate(payload)
