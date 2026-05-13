"""Schemas para evaluación manual de cuotas y combinadas."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class BetLegIn(BaseModel):
    """Una pata de una apuesta (manual o leída de Winamax)."""

    label: str = Field(..., min_length=1, max_length=256)
    decimal_odds: Decimal = Field(..., gt=Decimal("1"))
    # Si se rellenan, el backend intenta enlazar con el modelo (fútbol o baloncesto).
    market: str | None = Field(
        None,
        description=(
            "Ej.: h2h, totals, btts, double_chance, team_totals_home, "
            "team_totals_away. Opcional."
        ),
    )
    selection: str | None = Field(
        None,
        description=(
            "Para h2h: home, draw, away. Para totals: over, under. "
            "Para btts: yes, no. double_chance: home_or_draw, home_or_away, draw_or_away."
        ),
    )
    outcome_point: Decimal | None = Field(
        None,
        description="Línea numérica (ej. 2.5 goles o 215.5 puntos del partido/equipo).",
    )


class CustomBetAssessmentRequest(BaseModel):
    match_id: int | None = Field(
        None,
        description="Si se indica y hay stats en BD, se enriquece con probabilidad modelo.",
    )
    legs: list[BetLegIn] = Field(..., min_length=1, max_length=16)
    assume_independent: bool = Field(
        True,
        description="Si True, la prob. combinada modelo es el producto de las patas.",
    )


class BetLegAssessmentOut(BaseModel):
    label: str
    decimal_odds: str
    implied_probability: float
    model_probability: float | None = None
    model_ev: float | None = None
    model_note: str | None = None


class CustomBetAssessmentResponse(BaseModel):
    match_id: int | None = None
    match_label: str | None = None
    league_sport_key: str | None = None
    combined_decimal_odds: str
    combined_implied_probability: float
    combined_model_probability: float | None = None
    model_combined_ev: float | None = None
    assume_independence: bool
    independence_warning: str | None = None
    data_sources_note: str
    legs: list[BetLegAssessmentOut]
    notes: list[str] = Field(default_factory=list)
