/**
 * Modelos TypeScript que reflejan los schemas Pydantic del backend.
 *
 * Usamos `string` para los Decimal porque Pydantic los serializa así por
 * defecto (preservación de precisión). Convertimos a `number` solo en el
 * punto de uso (para mostrar / hacer cálculos).
 */

export interface League {
  id: number;
  sport_key: string;
  name: string;
  country: string | null;
}

export interface Team {
  id: number;
  name: string;
}

export interface Bookmaker {
  id: number;
  key: string;
  title: string;
  region: string | null;
}

export type MatchStatus = 'scheduled' | 'live' | 'finished' | 'postponed' | 'canceled';

export interface Match {
  id: number;
  external_id: string;
  league: League;
  home_team: Team;
  away_team: Team;
  commence_time: string;
  status: MatchStatus;
  home_score: number | null;
  away_score: number | null;
  played_at: string | null;
}

export interface Odds {
  id: number;
  bookmaker: Bookmaker;
  market_key: string;
  outcome_name: string;
  outcome_point: string | null;
  price: string;
  bookmaker_last_update: string | null;
}

export type ValueBetConfidence = 'high' | 'medium' | 'low' | 'unknown';

export interface ValueBet {
  match_id: number;
  league: string;
  home_team: string;
  away_team: string;
  commence_time: string;
  market_key: string;
  outcome_name: string;
  outcome_point: string | null;
  bookmaker_key: string;
  bookmaker_title: string;
  offered_odds: string;
  fair_probability: string;
  fair_odds: string;
  ev: string;
  sharp_bookie_key: string;
  sharp_bookie_margin: string;
  // Doble criterio (modelo Poisson)
  model_probability: string | null;
  model_fair_odds: string | null;
  model_ev: string | null;
  model_agrees: boolean | null;
  confidence: ValueBetConfidence;
}

export interface UserProfile {
  id: number;
  name: string;
  description: string | null;
  risk_profile: string;
  bankroll: string;
  odds_min: string;
  odds_max: string;
  min_real_probability: string;
  min_ev: string;
  kelly_modifier: string;
  max_stake_pct: string | null;
  flat_stake: string | null;
  parlay_enabled: boolean;
  parlay_legs: number;
  parlay_combined_odds_min: string | null;
  parlay_combined_odds_max: string | null;
  is_default: boolean;
}

export type ConfidenceLevel = 'alta' | 'media' | 'baja';
export type RecommendationLabel = 'Apuesta segura' | 'Equilibrada' | 'Arriesgada';

export interface Recommendation {
  value_bet: ValueBet;
  stake: string;
  potential_profit: string;
  confidence_level: ConfidenceLevel;
  recommendation_label: RecommendationLabel;
  explanation_text: string;
}

export interface ParlayLeg {
  match_id: number;
  home_team: string;
  away_team: string;
  commence_time: string;
  market_key: string;
  outcome_name: string;
  bookmaker_key: string;
  offered_odds: string;
}

export interface ParlayRecommendation {
  legs: ParlayLeg[];
  stake: string;
  combined_odds: string;
  combined_probability: string;
  combined_ev: string;
  potential_profit: string;
}

export interface RecommendationSet {
  profile_name: string;
  singles: Recommendation[];
  parlays: ParlayRecommendation[];
}

export interface TeamSnapshot {
  id: number;
  name: string;
  matches_played: number | null;
  wins: number | null;
  draws: number | null;
  losses: number | null;
  goals_for: number | null;
  goals_against: number | null;
  points: number | null;
  goals_for_per_match: number | null;
  goals_against_per_match: number | null;
}

export interface TopPlayerStat {
  name: string;
  position: string | null;
  matches_played: number | null;
  goals: number | null;
  shots: number | null;
  shots_on_target: number | null;
  shots_on_target_per_90: number | null;
  xg: number | null;
}

export interface MarketPrediction {
  market: string;
  selection: string;
  selection_label: string;
  fair_probability: number;
  best_odds: number | null;
  bookmaker_key: string | null;
  implied_probability: number | null;
  expected_value: number | null;
  fair_odds: number;
}

export interface MatchAnalysis {
  match_id: number;
  league_name: string;
  commence_time: string;
  home: TeamSnapshot;
  away: TeamSnapshot;
  expected_goals_home: number;
  expected_goals_away: number;
  league_avg_goals: number;
  home_top_players: TopPlayerStat[];
  away_top_players: TopPlayerStat[];
  predictions: MarketPrediction[];
  notes: string[];
}

export interface TeamStatsSide {
  team: Team;
  is_home: boolean;

  xg: string | null;
  xg_on_target: string | null;
  xa: string | null;

  possession_pct: number | null;

  shots_total: number | null;
  shots_on_target: number | null;
  shots_off_target: number | null;
  shots_blocked: number | null;
  shots_inside_box: number | null;
  shots_outside_box: number | null;
  shots_woodwork: number | null;
  headed_goals: number | null;

  big_chances: number | null;
  corners: number | null;
  offsides: number | null;
  free_kicks: number | null;
  throw_ins: number | null;
  touches_in_box: number | null;

  fouls: number | null;
  yellow_cards: number | null;
  red_cards: number | null;
  duels_won: number | null;
  clearances: number | null;
  interceptions: number | null;
  saves: number | null;
  goal_kicks: number | null;

  passes_pct: number | null;
  passes_completed: number | null;
  passes_attempted: number | null;

  long_passes_pct: number | null;
  long_passes_completed: number | null;
  long_passes_attempted: number | null;

  crosses_pct: number | null;
  crosses_completed: number | null;
  crosses_attempted: number | null;

  tackles_pct: number | null;
  tackles_completed: number | null;
  tackles_attempted: number | null;
}

export interface MatchStats {
  match_id: number;
  source: string;
  captured_at: string;
  home: TeamStatsSide;
  away: TeamStatsSide;
}

export interface ReliabilityBin {
  lower: number;
  upper: number;
  count: number;
  mean_predicted: number;
  observed_frequency: number;
}

export interface GridSearchPoint {
  home_advantage: number;
  dixon_coles_rho?: number;
  accuracy_1x2: number;
  brier_1x2: number;
  brier_over_25: number;
  brier_btts: number;
  loss: number;
}

export interface LeagueMetrics {
  league_name: string;
  n_samples: number;
  accuracy_1x2: number;
  brier_1x2: number;
  log_loss_1x2: number;
  brier_over_25: number;
  brier_btts: number;
  mae_total_goals: number;
  observed_home_rate: number;
  observed_draw_rate: number;
  observed_away_rate: number;
  observed_over_25_rate: number;
  observed_btts_rate: number;
}

export interface CalibrationReport {
  id: number;
  created_at: string;
  home_advantage: number;
  dixon_coles_rho?: number;
  n_samples: number;
  note: string | null;

  accuracy_1x2: number;
  brier_1x2: number;
  log_loss_1x2: number;
  brier_over_25: number;
  log_loss_over_25: number;
  brier_btts: number;
  log_loss_btts: number;
  mae_total_goals: number;
  mae_home_goals: number;
  mae_away_goals: number;

  observed_home_rate: number;
  observed_draw_rate: number;
  observed_away_rate: number;
  observed_over_25_rate: number;
  observed_btts_rate: number;

  per_league: LeagueMetrics[];
  reliability_home_win: ReliabilityBin[];
  reliability_over_25: ReliabilityBin[];
  grid_search: GridSearchPoint[];
}

export interface LeagueResultsUpdate {
  league_name: string;
  sport_key: string;
  events_fetched: number;
  matches_updated: number;
  matches_already_ok: number;
  matches_not_found: number;
  skipped_unmapped: number;
}

export interface ResultsRefreshSummary {
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  total_updated: number;
  total_already_ok: number;
  total_not_found: number;
  per_league: LeagueResultsUpdate[];
}

export interface DailyResultsBlock {
  total_updated: number;
  total_already_ok: number;
  total_not_found: number;
}

export interface DailyStatsBlock {
  matches_matched: number;
  matches_unmatched: number;
  stats_inserted: number;
  stats_updated: number;
  fetch_errors: number;
  parse_errors: number;
}

export interface DailyRefreshSummary {
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  ok: boolean;
  results: DailyResultsBlock | null;
  stats: DailyStatsBlock | null;
  errors: string[];
}

// ---------------------------------------------------------------------------
// Player Props (Winamax)
// ---------------------------------------------------------------------------

export interface PlayerPropOdds {
  line: number | null;
  side: 'over' | 'under' | 'yes';
  odds: number;
  bookmaker: string;
  outcome_name: string;
}

export interface PlayerPropsBlock {
  player_id: number;
  player_name: string;
  markets: { [marketKey: string]: PlayerPropOdds[] };
}

export interface MatchPlayerProps {
  match_id: number;
  home_team: string | null;
  away_team: string | null;
  players: PlayerPropsBlock[];
}

export interface RefreshSummary {
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  ok: boolean;
  leagues_total: number;
  leagues_ok: number;
  leagues_error: number;
  snapshots_deleted: number;
  past_matches_deleted: number;
  matches_inserted: number;
  odds_inserted: number;
  requests_used_total: number | null;
  requests_remaining: number | null;
  errors: string[];
}
