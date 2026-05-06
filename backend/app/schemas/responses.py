"""Schemas Pydantic para serializar las respuestas de la API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

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


class BookmakerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    title: str
    region: str | None = None


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


class TeamStatsSideOut(BaseModel):
    """Stats post-partido de un equipo (home o away) en un encuentro."""
    model_config = ConfigDict(from_attributes=True)

    team: TeamOut
    is_home: bool

    xg: Decimal | None = None
    xg_on_target: Decimal | None = None
    xa: Decimal | None = None

    possession_pct: int | None = None

    shots_total: int | None = None
    shots_on_target: int | None = None
    shots_off_target: int | None = None
    shots_blocked: int | None = None
    shots_inside_box: int | None = None
    shots_outside_box: int | None = None
    shots_woodwork: int | None = None
    headed_goals: int | None = None

    big_chances: int | None = None
    corners: int | None = None
    offsides: int | None = None
    free_kicks: int | None = None
    throw_ins: int | None = None
    touches_in_box: int | None = None

    fouls: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None
    duels_won: int | None = None
    clearances: int | None = None
    interceptions: int | None = None
    saves: int | None = None
    goal_kicks: int | None = None

    passes_pct: int | None = None
    passes_completed: int | None = None
    passes_attempted: int | None = None

    long_passes_pct: int | None = None
    long_passes_completed: int | None = None
    long_passes_attempted: int | None = None

    crosses_pct: int | None = None
    crosses_completed: int | None = None
    crosses_attempted: int | None = None

    tackles_pct: int | None = None
    tackles_completed: int | None = None
    tackles_attempted: int | None = None


class MatchStatsOut(BaseModel):
    """Stats post-partido completas de un encuentro."""

    match_id: int
    source: str
    captured_at: datetime
    home: TeamStatsSideOut
    away: TeamStatsSideOut


class OddsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bookmaker: BookmakerOut
    market_key: str = Field(..., description="Tipo de mercado (h2h, totals, ...)")
    outcome_name: str
    outcome_point: Decimal | None = None
    price: Decimal
    bookmaker_last_update: datetime | None = None


class ValueBetOut(BaseModel):
    """Apuesta con valor positivo detectada."""

    match_id: int
    league: str
    home_team: str
    away_team: str
    commence_time: datetime
    market_key: str
    outcome_name: str
    outcome_point: Decimal | None = None
    bookmaker_key: str
    bookmaker_title: str
    offered_odds: Decimal
    fair_probability: Decimal
    fair_odds: Decimal
    ev: Decimal
    sharp_bookie_key: str
    sharp_bookie_margin: Decimal
    # Doble criterio: modelo Poisson
    model_probability: Decimal | None = None
    model_fair_odds: Decimal | None = None
    model_ev: Decimal | None = None
    model_agrees: bool | None = None
    confidence: str = "unknown"


class UserProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    risk_profile: str
    bankroll: Decimal
    odds_min: Decimal
    odds_max: Decimal
    min_real_probability: Decimal
    min_ev: Decimal
    kelly_modifier: Decimal
    max_stake_pct: Decimal | None = None
    flat_stake: Decimal | None = None
    parlay_enabled: bool
    parlay_legs: int
    parlay_combined_odds_min: Decimal | None = None
    parlay_combined_odds_max: Decimal | None = None
    is_default: bool


class RecommendationOut(BaseModel):
    """Recomendación individual con stake calculado y sello de calidad."""

    value_bet: ValueBetOut
    stake: Decimal
    potential_profit: Decimal
    confidence_level: str   # "alta" | "media" | "baja"
    recommendation_label: str   # "Apuesta segura" | "Equilibrada" | "Arriesgada"
    explanation_text: str


class ParlayLegOut(BaseModel):
    """Una pata de una combinada."""

    match_id: int
    home_team: str
    away_team: str
    commence_time: datetime
    market_key: str
    outcome_name: str
    bookmaker_key: str
    offered_odds: Decimal


class ParlayRecommendationOut(BaseModel):
    """Combinada recomendada."""

    legs: list[ParlayLegOut]
    stake: Decimal
    combined_odds: Decimal
    combined_probability: Decimal
    combined_ev: Decimal
    potential_profit: Decimal


class RecommendationSetOut(BaseModel):
    profile_name: str
    singles: list[RecommendationOut]
    parlays: list[ParlayRecommendationOut]


class ParlayCandidateOut(BaseModel):
    """Combinada inteligente con métricas avanzadas (correlación, diversificación)."""

    legs: list[ParlayLegOut]
    combined_odds: Decimal
    raw_combined_probability: Decimal
    adjusted_probability: Decimal
    expected_value: Decimal
    correlation_factor: Decimal
    diversification_score: Decimal
    strategy_name: str
    suggested_stake: Decimal
    potential_profit: Decimal


class ParlaySetOut(BaseModel):
    """Set de combinadas inteligentes para un perfil + estrategia."""

    profile_name: str
    strategy: str
    parlays: list[ParlayCandidateOut]


class TeamSnapshotOut(BaseModel):
    id: int
    name: str
    matches_played: int | None = None
    wins: int | None = None
    draws: int | None = None
    losses: int | None = None
    goals_for: int | None = None
    goals_against: int | None = None
    points: int | None = None
    goals_for_per_match: float | None = None
    goals_against_per_match: float | None = None


class TopPlayerStatOut(BaseModel):
    name: str
    position: str | None = None
    matches_played: int | None = None
    goals: int | None = None
    shots: int | None = None
    shots_on_target: int | None = None
    shots_on_target_per_90: float | None = None
    xg: float | None = None


class PlayerOut(BaseModel):
    """Datos básicos de un jugador."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    position: str | None = None
    nationality: str | None = None
    external_id: str | None = None
    external_source: str | None = None
    team: TeamOut | None = None


class PlayerSeasonStatsOut(BaseModel):
    """Stats agregadas de jugador en una temporada."""

    model_config = ConfigDict(from_attributes=True)

    season_id: int
    season_name: str | None = None
    league_name: str | None = None
    team: TeamOut | None = None

    matches_played: int | None = None
    starts: int | None = None
    minutes: int | None = None
    nineties: Decimal | None = None

    goals: int | None = None
    assists: int | None = None
    shots: int | None = None
    shots_on_target: int | None = None
    shots_on_target_pct: Decimal | None = None
    shots_per_90: Decimal | None = None
    shots_on_target_per_90: Decimal | None = None
    xg: Decimal | None = None
    npxg: Decimal | None = None

    yellow_cards: int | None = None
    red_cards: int | None = None

    last_updated: datetime


class PlayerMatchStatRowOut(BaseModel):
    """Stats de un partido concreto para un jugador."""

    model_config = ConfigDict(from_attributes=True)

    match_id: int
    league_name: str | None = None
    home_team: str
    away_team: str
    is_home: bool
    commence_time: datetime
    home_score: int | None = None
    away_score: int | None = None

    is_starter: bool | None = None
    is_substitute: bool | None = None
    minutes_played: int | None = None
    position_in_match: str | None = None

    goals: int | None = None
    assists: int | None = None
    shots: int | None = None
    shots_on_target: int | None = None
    shots_off_target: int | None = None
    shots_blocked: int | None = None
    big_chances_created: int | None = None
    big_chances_missed: int | None = None
    expected_goals: Decimal | None = None
    expected_assists: Decimal | None = None

    key_passes: int | None = None
    passes_total: int | None = None
    passes_accurate: int | None = None
    touches: int | None = None

    tackles: int | None = None
    interceptions: int | None = None
    clearances: int | None = None
    duels_won: int | None = None

    fouls_committed: int | None = None
    fouls_drawn: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None
    offsides: int | None = None

    saves: int | None = None
    goals_prevented: Decimal | None = None

    rating: Decimal | None = None


class PlayerStatsSummaryOut(BaseModel):
    """Promedios derivados del histórico por partido (últimos N games)."""

    sample_size: int
    avg_minutes_played: float | None = None
    avg_shots: float | None = None
    avg_shots_on_target: float | None = None
    avg_goals: float | None = None
    avg_rating: float | None = None
    avg_expected_goals: float | None = None
    pct_starts: float | None = None
    # Tasas útiles para player props
    pct_at_least_one_shot_on_target: float | None = None
    pct_at_least_two_shots_on_target: float | None = None
    pct_scored: float | None = None


class PlayerStatsResponseOut(BaseModel):
    """Respuesta completa de ``GET /players/{id}/stats``."""

    player: PlayerOut
    season_stats: PlayerSeasonStatsOut | None = None
    recent_matches: list[PlayerMatchStatRowOut] = []
    summary: PlayerStatsSummaryOut | None = None


class MarketPredictionOut(BaseModel):
    market: str
    selection: str
    selection_label: str
    fair_probability: float
    best_odds: float | None = None
    bookmaker_key: str | None = None
    implied_probability: float | None = None
    expected_value: float | None = None
    fair_odds: float


class MatchAnalysisOut(BaseModel):
    match_id: int
    league_name: str
    commence_time: datetime
    home: TeamSnapshotOut
    away: TeamSnapshotOut
    expected_goals_home: float
    expected_goals_away: float
    league_avg_goals: float
    home_top_players: list[TopPlayerStatOut] = Field(default_factory=list)
    away_top_players: list[TopPlayerStatOut] = Field(default_factory=list)
    predictions: list[MarketPredictionOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Calibración del modelo Poisson
# ---------------------------------------------------------------------------


class ReliabilityBinOut(BaseModel):
    lower: float
    upper: float
    count: int
    mean_predicted: float
    observed_frequency: float


class GridSearchPointOut(BaseModel):
    home_advantage: float
    dixon_coles_rho: float = 0.0
    accuracy_1x2: float
    brier_1x2: float
    brier_over_25: float
    brier_btts: float
    loss: float


class LeagueMetricsOut(BaseModel):
    league_name: str
    n_samples: int
    accuracy_1x2: float
    brier_1x2: float
    log_loss_1x2: float
    brier_over_25: float
    brier_btts: float
    mae_total_goals: float
    observed_home_rate: float
    observed_draw_rate: float
    observed_away_rate: float
    observed_over_25_rate: float
    observed_btts_rate: float


class CalibrationReportOut(BaseModel):
    """Snapshot de las métricas del modelo Poisson para mostrar en el dashboard."""

    id: int
    created_at: datetime
    home_advantage: float
    dixon_coles_rho: float = 0.0
    n_samples: int
    note: str | None = None

    accuracy_1x2: float
    brier_1x2: float
    log_loss_1x2: float
    brier_over_25: float
    log_loss_over_25: float
    brier_btts: float
    log_loss_btts: float
    mae_total_goals: float
    mae_home_goals: float
    mae_away_goals: float

    observed_home_rate: float
    observed_draw_rate: float
    observed_away_rate: float
    observed_over_25_rate: float
    observed_btts_rate: float

    per_league: list[LeagueMetricsOut] = Field(default_factory=list)
    reliability_home_win: list[ReliabilityBinOut] = Field(default_factory=list)
    reliability_over_25: list[ReliabilityBinOut] = Field(default_factory=list)
    grid_search: list[GridSearchPointOut] = Field(default_factory=list)
