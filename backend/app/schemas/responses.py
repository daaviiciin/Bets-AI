"""Schemas Pydantic para serializar las respuestas de la API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LeagueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sport_key: str
    name: str
    country: str | None = None


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    logo_url: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None


class Odds1x2Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    home: Decimal | None = None
    draw: Decimal | None = None
    away: Decimal | None = None
    home_name: str | None = Field(None, description="Selección del local tal como la escribe Winamax.")
    away_name: str | None = Field(None, description="Selección del visitante tal como la escribe Winamax.")


class MatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str
    league: LeagueOut
    home_team: TeamOut
    away_team: TeamOut
    commence_time: datetime
    status: str = "scheduled"
    home_score: int | None = None
    away_score: int | None = None
    played_at: datetime | None = None
    # Solo con ``GET /matches?with_odds=true``: último 1X2 de Winamax (``None`` si no hay).
    odds_1x2: Odds1x2Out | None = None


class GradedOddsLineOut(BaseModel):
    """Cuota puntual calificada (todas las líneas del libro para el dashboard)."""

    match_id: int
    league: str
    league_sport_key: str = ""
    home_team: str
    away_team: str
    commence_time: datetime
    market_key: str
    outcome_name: str
    outcome_point: Decimal | None = None
    player_name: str | None = None
    bookmaker_key: str
    bookmaker_title: str = ""
    offered_odds: Decimal
    confidence: str = "unknown"
    model_probability: Decimal | None = None
    model_ev: Decimal | None = None
    # Cuota <= MAX_RECOMMENDABLE_ODDS (4,00). Si es False la línea es solo informativa:
    # nunca se marca como valor aunque tenga EV positivo.
    recommendable: bool = False
    lambda_home: Decimal | None = None
    lambda_away: Decimal | None = None
    implied_probability: Decimal | None = None
    model_summary_lines: list[str] = Field(default_factory=list)


class AuthUserBriefOut(BaseModel):
    id: int
    username: str


class TokenResponseOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: int


class ParlayLegPersistIn(BaseModel):
    """Pata snapshot al construir combinada guardada desde el cliente."""

    match_id: int
    home_team: str = Field(max_length=160)
    away_team: str = Field(max_length=160)
    league: str = Field(default="", max_length=160)
    commence_time: datetime | None = None
    market_key: str = Field(max_length=64)
    outcome_name: str = Field(max_length=160)
    outcome_point: str | None = Field(default=None, max_length=32)
    player_name: str | None = Field(default=None, max_length=160)
    # Límites anti-abuso: el producto se guarda en Numeric(14, 4).
    offered_odds: Decimal = Field(gt=Decimal("1"), le=Decimal("1000"))
    model_probability: Decimal | None = None


#: Máximo de patas por combinada guardada (el JSON va entero a ``legs_snapshot``).
MAX_SAVED_PARLAY_LEGS = 30


class SavedParlayCreateIn(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    legs: list[ParlayLegPersistIn] = Field(min_length=1, max_length=MAX_SAVED_PARLAY_LEGS)
    #: Importe apostado en euros (opcional: se puede guardar solo para seguirla).
    stake: Decimal | None = Field(default=None, gt=0, le=100000, decimal_places=2)


class SavedParlayUpdateIn(BaseModel):
    """Solo cambia los campos enviados; ``manual_result: null`` vuelve a la liquidación automática."""

    stake: Decimal | None = Field(default=None, gt=0, le=100000, decimal_places=2)
    manual_result: Literal["hit", "miss", "void"] | None = None


class SavedParlayOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    title: str | None
    combined_odds: Decimal
    model_joint_probability: Decimal | None = None
    legs_snapshot: list[Any]
    grading_snapshot: dict[str, Any] | None = None
    overall_result: str
    manual_result: str | None = None
    stake: Decimal | None = None
    #: Beneficio neto si ya está liquidada y tiene importe (``beneficio_apuesta``); si no, null.
    profit: Decimal | None = None
    last_graded_at: datetime | None = None
    created_at: datetime


class FeaturedDailyPickOut(BaseModel):
    """Picks del día con patas corregidas si ya hay marcador."""

    pick_date: date
    #: Simples (cada una con ``sin_valor``, ``aviso``, P y EV del modelo en las filas v2).
    legs: list[dict[str, Any]]
    #: Combinada del día con sus patas y ``result``; null si no hubo (motivo en ``selection_meta``).
    combo: dict[str, Any] | None = None
    rules_version: str = "v1"
    model_version: str | None = None
    generated_at: datetime | None = None
    selection_meta: dict[str, Any] | None = None
    last_graded_at: datetime | None = None
    created_at: datetime


class FeaturedTodayOut(BaseModel):
    """Picks de hoy: ``listo`` (con ``pick``), ``pendiente`` (aún sin generar) o ``sin_partidos``."""

    estado: Literal["listo", "pendiente", "sin_partidos"]
    motivo: str | None = None
    #: ``True`` si al generarlos hubo que descargar antes las cuotas de Winamax.
    cuotas_actualizadas: bool = False
    pick: FeaturedDailyPickOut | None = None


class FeaturedPicksMetricsOut(BaseModel):
    """Métricas agregadas de los picks propuestos por la app (histórico)."""

    total_pick_days: int
    total_legs: int
    hits: int
    misses: int
    pending: int
    needs_review: int
    voids: int = 0
    rate_pct: float | None = None
    #: Simples con valor (EV ≥ 0) frente a las que completaron el mínimo de 3 sin valor.
    value_hits: int = 0
    value_misses: int = 0
    value_rate_pct: float | None = None
    novalue_hits: int = 0
    novalue_misses: int = 0
    novalue_rate_pct: float | None = None
    combos_total: int = 0
    combo_hits: int = 0
    combo_misses: int = 0
    combo_pending: int = 0
    combo_rate_pct: float | None = None
    #: Por versión del modelo: ``{"simples": {hits, misses, …, rate_pct}, "combinadas": {…}}``.
    por_modelo: dict[str, dict[str, Any]] = Field(default_factory=dict)


class UserParlayMetricsOut(BaseModel):
    """Resumen de combinadas guardadas del usuario (misma escala que mis apuestas)."""

    total: int
    pending: int
    needs_review: int
    hits: int
    misses: int
    voids: int = 0
    rate_pct: float | None = None
    #: Suma de importes de las ganadas y perdidas (las nulas devuelven el dinero).
    staked: Decimal = Decimal("0")
    profit: Decimal = Decimal("0")
    roi_pct: Decimal | None = None


# ---------------------------------------------------------------------------
# Previa de partido (``GET /matches/{id}/preview``): página de partido
# ---------------------------------------------------------------------------


class TeamAveragesOut(BaseModel):
    """Medias por partido de la temporada (``None`` si no hay datos de ese campo)."""

    model_config = ConfigDict(from_attributes=True)

    matches: int = 0
    goals_for: float | None = None
    goals_against: float | None = None
    xg: float | None = None
    shots: float | None = None
    shots_on_target: float | None = None
    corners: float | None = None
    yellow_cards: float | None = None
    goals_1h: float | None = None
    goals_2h: float | None = None
    corners_1h: float | None = None
    corners_2h: float | None = None
    cards_1h: float | None = None
    cards_2h: float | None = None


class FormMatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    match_id: int
    commence_time: datetime
    opponent: str
    is_home: bool
    goals_for: int
    goals_against: int
    result: str = Field(..., description="W / D / L")


class AbsenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    status: str = Field(..., description="missing (baja) / doubtful (duda)")
    reason: str | None = None


class LineupPlayerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    position: str | None = Field(None, description="G / D / M / F")
    status: str | None = Field(None, description="missing / doubtful (solo en el probable)")
    photo_url: str | None = Field(None, description="Foto del jugador (CDN de Flashscore)")


class TeamLineupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str = Field(..., description="official / played / probable / none")
    starters: list[LineupPlayerOut] = Field(default_factory=list)
    based_on: str | None = None


class KeyPlayerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    player_id: int
    name: str
    position: str | None = None
    apps: int
    starts: int
    minutes: int
    goals: int
    shots: int
    shots_on_target: int
    yellow_cards: int
    shots_on_target_per90: float | None = None
    shots_per90: float | None = None
    photo_url: str | None = Field(None, description="Foto del jugador (CDN de Flashscore)")


class TeamPreviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    team_id: int
    name: str
    logo_url: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None
    overall: TeamAveragesOut
    venue: TeamAveragesOut
    form: list[FormMatchOut] = Field(default_factory=list)
    absences: list[AbsenceOut] = Field(default_factory=list)
    lineup: TeamLineupOut
    players: list[KeyPlayerOut] = Field(default_factory=list)


class MatchPreviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    match_id: int
    league_name: str
    league_sport_key: str
    commence_time: datetime
    status: str
    home_score: int | None = None
    away_score: int | None = None
    odds_updated_at: datetime | None = Field(
        None, description="Última vez que se leyeron cuotas de este partido.",
    )
    home: TeamPreviewOut
    away: TeamPreviewOut
