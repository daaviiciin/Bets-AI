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
  /** Escudo de Flashscore y sus colores (``#rrggbb``); ``null`` si aún no se han calculado. */
  logo_url?: string | null;
  primary_color?: string | null;
  secondary_color?: string | null;
}

/** Último 1X2 de Winamax de un partido (``GET /matches?with_odds=true``). */
export interface Odds1x2 {
  home: string | null;
  draw: string | null;
  away: string | null;
  /** Nombre de la selección en Winamax ("Atl. Madrid"): el ``outcome_name`` de la cuota. */
  home_name: string | null;
  away_name: string | null;
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
  odds_1x2?: Odds1x2 | null;
}

// ---------------------------------------------------------------------------
// Previa de partido (``GET /matches/{id}/preview``)
// ---------------------------------------------------------------------------

/** Medias por partido de la temporada (``null`` si ese dato no existe). */
export interface TeamAverages {
  matches: number;
  goals_for: number | null;
  goals_against: number | null;
  xg: number | null;
  shots: number | null;
  shots_on_target: number | null;
  corners: number | null;
  yellow_cards: number | null;
  goals_1h: number | null;
  goals_2h: number | null;
  corners_1h: number | null;
  corners_2h: number | null;
  cards_1h: number | null;
  cards_2h: number | null;
}

export interface FormMatch {
  match_id: number;
  commence_time: string;
  opponent: string;
  is_home: boolean;
  goals_for: number;
  goals_against: number;
  /** W / D / L (se enseña como G / E / P). */
  result: 'W' | 'D' | 'L';
}

export interface AbsenceItem {
  name: string;
  status: 'missing' | 'doubtful' | string;
  reason: string | null;
}

export interface LineupPlayer {
  name: string;
  /** G / D / M / F. */
  position: string | null;
  /** Solo en el once probable: ``missing`` (baja) o ``doubtful`` (duda). */
  status: string | null;
  /** Foto (CDN de Flashscore); ``null`` si aún no se conoce. */
  photo_url?: string | null;
}

export type LineupKind = 'official' | 'played' | 'probable' | 'none';

export interface TeamLineup {
  kind: LineupKind;
  starters: LineupPlayer[];
  /** Once probable: de qué partido sale ("vs Sevilla · 12/10"). */
  based_on: string | null;
}

export interface KeyPlayer {
  player_id: number;
  name: string;
  position: string | null;
  apps: number;
  starts: number;
  minutes: number;
  goals: number;
  shots: number;
  shots_on_target: number;
  yellow_cards: number;
  shots_on_target_per90: number | null;
  shots_per90: number | null;
  /** Foto (CDN de Flashscore); ``null`` si aún no se conoce. */
  photo_url?: string | null;
}

export interface TeamPreview {
  team_id: number;
  name: string;
  logo_url: string | null;
  primary_color: string | null;
  secondary_color: string | null;
  overall: TeamAverages;
  /** Local: en casa. Visitante: fuera. */
  venue: TeamAverages;
  form: FormMatch[];
  absences: AbsenceItem[];
  lineup: TeamLineup;
  players: KeyPlayer[];
}

export interface MatchPreview {
  match_id: number;
  league_name: string;
  league_sport_key: string;
  commence_time: string;
  status: MatchStatus;
  home_score: number | null;
  away_score: number | null;
  /** Última vez que se leyeron cuotas de este partido. */
  odds_updated_at: string | null;
  home: TeamPreview;
  away: TeamPreview;
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

/** Fila de `/dashboard/graded-odds`: cada cuota con confianza estimada. */
export interface GradedOddsLine {
  match_id: number;
  league: string;
  /** ``League.sport_key`` (p. ej. soccer_epl); viene del backend para filtros. */
  league_sport_key?: string;
  home_team: string;
  away_team: string;
  commence_time: string;
  market_key: string;
  outcome_name: string;
  outcome_point: string | null;
  player_name: string | null;
  bookmaker_key: string;
  bookmaker_title: string;
  offered_odds: string;
  confidence: ValueBetConfidence;
  model_probability: string | null;
  model_ev: string | null;
  /** Cuota <= 4,00: puede marcarse como valor. Si es false la línea es solo informativa. */
  recommendable: boolean;
  /** Esperanza goles local (Poisson) cuando hay modelo de partido. */
  lambda_home?: string | null;
  lambda_away?: string | null;
  /** Probabilidad implícita bruta 1/cuota. */
  implied_probability?: string | null;
  /** Bullets con el razonamiento del modelo (columna «resumen»). */
  model_summary_lines?: string[];
}

export interface SavedParlayLegPersist {
  match_id: number;
  home_team: string;
  away_team: string;
  league: string;
  commence_time: string | null;
  market_key: string;
  outcome_name: string;
  outcome_point: string | null;
  player_name: string | null;
  offered_odds: string;
  model_probability: string | null;
  /** Metadatos extra en picks destacados del backend. */
  ev_market?: string;
  model_ev?: string | null;
  confidence?: string;
  result?: string;
  score_text?: string | null;
}

export interface SavedParlay {
  id: number;
  user_id: number;
  title: string | null;
  combined_odds: string;
  model_joint_probability: string | null;
  legs_snapshot: SavedParlayLegPersist[];
  grading_snapshot: { legs: SavedParlayLegPersist[]; auto_result?: string } | null;
  /** pending | hit | miss | needs_review | void (si hay `manual_result`, es ese). */
  overall_result: string;
  /** Corrección del usuario: hit | miss | void; null = manda la liquidación automática. */
  manual_result: ResultadoManual | null;
  /** Importe apostado en euros (Decimal como texto); null en las guardadas sin importe. */
  stake: string | null;
  /** Beneficio neto en euros si ya está liquidada y tiene importe; si no, null. */
  profit: string | null;
  last_graded_at: string | null;
  created_at: string;
}

export type ResultadoManual = 'hit' | 'miss' | 'void';

/** Picks del día generados por el backend (métricas de transparencia de la app). */
export interface FeaturedDailyPickRow {
  pick_date: string;
  legs: SavedParlayLegPersist[];
  last_graded_at: string | null;
  created_at: string;
}

export interface FeaturedPicksMetrics {
  total_pick_days: number;
  total_legs: number;
  hits: number;
  misses: number;
  pending: number;
  needs_review: number;
  rate_pct: number | null;
}

export interface UserParlayMetrics {
  total: number;
  pending: number;
  needs_review: number;
  hits: number;
  misses: number;
  voids: number;
  rate_pct: number | null;
  /** Euros (Decimal como texto): apostado en ganadas + perdidas, y beneficio neto. */
  staked: string;
  profit: string;
  roi_pct: string | null;
}

export interface AuthTokenResponse {
  access_token: string;
  token_type: string;
  expires_at: number;
}

export interface AuthUserBrief {
  id: number;
  username: string;
}

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
  player_name?: string | null;
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

  goals_1h: number | null;
  goals_2h: number | null;
  corners_1h: number | null;
  corners_2h: number | null;
  yellow_cards_1h: number | null;
  yellow_cards_2h: number | null;
  red_cards_1h: number | null;
  red_cards_2h: number | null;
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

export interface DailyRefreshFixturesBlock {
  fixtures_fetched: number;
  fixtures_inserted: number;
  fixtures_updated: number;
  skipped_duplicate_db: number;
  skipped_not_future: number;
  skipped_unknown_league: number;
  duration_seconds: number;
  errors: string[];
}

export interface DailyRefreshSummary {
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  ok: boolean;
  fixtures: DailyRefreshFixturesBlock | null;
  results: DailyResultsBlock | null;
  stats: DailyStatsBlock | null;
  errors: string[];
}

/** Resumen de una corrida de ``POST /refresh/official`` (bloque Winamax). */
export interface WinamaxIngestSummary {
  matches_in_db: number;
  matches_matched: number;
  matches_unmatched: number;
  matches_failed: number;
  /** Emparejados pero no descargados: sus cuotas se leyeron hace poco (botón). */
  matches_skipped_fresh?: number;
  snapshots_created: number;
  snapshots_pruned?: number;
  odds_inserted: number;
  /** Líneas copiadas desde el snapshot anterior (partidos no re-scrapeados). */
  odds_carried_forward?: number;
  duration_seconds: number;
  errors: string[];
}

/** Resumen de ``POST /refresh/odds``: solo cuotas Winamax (botón del dashboard). */
export interface OddsRefreshSummary {
  finished_at: string;
  ok: boolean;
  winamax: WinamaxIngestSummary;
}

/** Pipeline oficial: diario (Sofascore + Flashscore) + Winamax. */
export interface OfficialPipelineSummary {
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  ok: boolean;
  daily: DailyRefreshSummary;
  winamax: WinamaxIngestSummary;
  note: string;
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

/** Request a ``POST /analysis/custom-bet``. */
export interface BetLegIn {
  label: string;
  decimal_odds: string;
  market?: string | null;
  selection?: string | null;
  outcome_point?: string | null;
}

export interface CustomBetAssessmentRequest {
  match_id?: number | null;
  legs: BetLegIn[];
  assume_independent?: boolean;
}

export interface BetLegAssessmentOut {
  label: string;
  decimal_odds: string;
  implied_probability: number;
  model_probability: number | null;
  model_ev: number | null;
  model_note: string | null;
}

export interface CustomBetAssessmentResponse {
  match_id: number | null;
  match_label: string | null;
  league_sport_key: string | null;
  combined_decimal_odds: string;
  combined_implied_probability: number;
  combined_model_probability: number | null;
  model_combined_ev: number | null;
  assume_independence: boolean;
  independence_warning: string | null;
  data_sources_note: string;
  legs: BetLegAssessmentOut[];
  notes: string[];
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
