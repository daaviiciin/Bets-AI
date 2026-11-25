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
  /** Solo si Winamax paga más que el resto de casas (comparador de Flashscore). */
  mercado?: VentajaMercado | null;
}

/**
 * Cuota de Winamax frente al consenso sin margen de las demás casas del comparador.
 * `value` = ventaja de 3 a 15 %; `error_probable` = más del 15 % (Winamax puede anularla).
 */
export interface VentajaMercado {
  clase: 'value' | 'surebet' | 'desfasada' | 'error_probable';
  /** `cuota × p_mercado − 1`. */
  ventaja: string;
  /** P sin margen del consenso de las demás casas. */
  p_mercado: string;
  cuota_justa: string;
  casas: number;
  leido_at: string;
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
  /** `cuota_efectiva`: cuota sin las patas nulas (solo si hay alguna). */
  grading_snapshot: {
    legs: SavedParlayLegPersist[];
    auto_result?: string;
    cuota_efectiva?: string;
  } | null;
  /**
   * pending | pending_stats | hit | miss | needs_review | void (si hay `manual_result`, es ese).
   * `pending_stats`: el partido acabó pero faltan stats para liquidar; se muestra como pendiente.
   */
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

/** Pata de un pick del modelo (simple o de la combinada), congelada al generarse. */
export interface PickLeg extends SavedParlayLegPersist {
  /** Modelo que dio la P (`legacy-v1` hasta que se promueva el v2). */
  model_version?: string;
  odds_captured_at?: string | null;
  /** EV < 0: completa el mínimo de 3 simples aunque el modelo no le vea valor. */
  sin_valor?: boolean;
  aviso?: string | null;
  /**
   * Prop de jugador (tiros a puerta, goleador): aún sin historial de picks reales. En la
   * combinada del partido desde el 13/11; en simples y combinada del día desde las reglas v5
   * (17/11). Las filas anteriores no lo traen.
   */
  experimental?: boolean;
}

export interface PickCombo {
  legs: PickLeg[];
  /** Con selecciones del mismo partido es una estimación: la cuota real la pone MyMatch. */
  combined_odds: string;
  joint_probability: string;
  ev: string;
  sin_valor: boolean;
  /** Varias selecciones de un mismo partido (en Winamax se hace con MyMatch). */
  mismo_partido?: boolean;
  /** Producto simple de las cuotas de Winamax. */
  cuota_winamax?: string;
  /** Cuota desde la que tiene valor para el modelo (1/P conjunta). */
  cuota_minima_valor?: string;
  /** Resultado de la combinada (como `overall_result`); `pending` mientras se juega. */
  result?: string;
  /** Lleva alguna pata de props de jugador (reglas v5; en la del partido, desde el 13/11). */
  experimental?: boolean;
}

/**
 * Pata de la combinada del partido (`pata_a_dict` + B14e): en los props trae el jugador en
 * `player_name` y `experimental: true` (tiros a puerta, goleador o paradas del jugador).
 */
export interface MatchComboLeg extends PickLeg {
  experimental: boolean;
}

/**
 * Combinada del partido (`combinada_del_partido_a_dict`, desde el 13/11): 2–3 selecciones de
 * un mismo partido con cuota estimada de 2,50 a 5,00. En Winamax va por MyMatch: la cuota real
 * la pone Winamax y `combined_odds` es la estimación del modelo (producto corregido por la
 * correlación entre patas).
 */
export interface MatchCombo extends PickCombo {
  legs: MatchComboLeg[];
  mismo_partido: true;
  cuota_winamax: string;
  cuota_minima_valor: string;
  match_id: number;
  home_team: string;
  away_team: string;
  league: string;
  commence_time: string;
  /** Lleva alguna pata de props de jugador. */
  experimental: boolean;
}

/** Picks del día generados por el backend (métricas de transparencia de la app). */
export interface FeaturedDailyPickRow {
  pick_date: string;
  legs: PickLeg[];
  combo?: PickCombo | null;
  /** Combinada del partido; `null` si no hubo (motivo en `selection_meta`) o en filas antiguas. */
  match_combo?: MatchCombo | null;
  /**
   * `v1`: filas antiguas (sin combinada); `v2`: reglas 1,30–1,70 + combinada 3–6; `v4`: con la
   * combinada del partido; `v5` (17/11): simples 1,30–2,00, variedad de mercados y props.
   */
  rules_version?: string;
  model_version?: string | null;
  generated_at?: string | null;
  selection_meta?: {
    motivo_simples?: string;
    motivo_combinada?: string;
    motivo_combinada_partido?: string;
    candidatas?: number;
    candidatas_props?: number;
    [clave: string]: unknown;
  } | null;
  last_graded_at: string | null;
  created_at: string;
}

/** `GET/POST /refresh/auto`: pipeline completo (Flashscore + Winamax + picks) al abrir la app. */
export interface RefreshAuto {
  en_curso: boolean;
  /** Solo en el POST que acaba de lanzarlo. */
  lanzado: boolean;
  ultimo_inicio: string | null;
  ultimo_fin: string | null;
  ultimo_ok: boolean | null;
  ultimo_errores: string[];
}

/** `GET/POST /featured-picks/today`. */
export interface FeaturedToday {
  estado: 'listo' | 'pendiente' | 'sin_partidos';
  motivo: string | null;
  /** Con `sin_partidos`: inicio del próximo partido programado (`null` = ninguno). */
  proximo_partido?: string | null;
  /** Al generarlos hubo que descargar antes las cuotas de Winamax. */
  cuotas_actualizadas: boolean;
  pick: FeaturedDailyPickRow | null;
}

export interface FeaturedPicksMetrics {
  total_pick_days: number;
  total_legs: number;
  hits: number;
  misses: number;
  pending: number;
  needs_review: number;
  /** Patas nulas (partido aplazado o cancelado). */
  voids?: number;
  rate_pct: number | null;
  /** Simples con valor (EV ≥ 0) frente a las que completaron el mínimo sin valor. */
  value_hits?: number;
  value_misses?: number;
  value_rate_pct?: number | null;
  novalue_hits?: number;
  novalue_misses?: number;
  novalue_rate_pct?: number | null;
  /** Simples de props de jugador (experimentales, reglas v5) y el resto; `{}` en backends viejos. */
  simples_con_props?: PickCounts | Record<string, never>;
  simples_sin_props?: PickCounts | Record<string, never>;
  combos_total?: number;
  combo_hits?: number;
  combo_misses?: number;
  combo_pending?: number;
  combo_rate_pct?: number | null;
  /** Acierto de cada versión del modelo por separado (`legacy-v1`, `dc-ridge-…`). */
  por_modelo?: Record<string, { simples: PickCounts; combinadas: PickCounts }>;
  /** Combinada del partido, medida aparte (`metricas_combinada_del_partido`); `{}` si no hay. */
  combinada_partido?: MatchComboMetrics | Record<string, never>;
}

/** Familia de los props (`FAMILIA_PROP` del backend); `otros` si el mercado no tiene familia. */
export type FamiliaProp = 'player_sot' | 'goleador' | 'paradas' | 'otros';

/** Acierto de las combinadas del partido: todas, con y sin props, y patas de props por familia. */
export interface MatchComboMetrics extends PickCounts {
  total: number;
  con_props: PickCounts;
  sin_props: PickCounts;
  patas_props: Partial<Record<FamiliaProp, PickCounts>>;
}

export interface PickCounts {
  hits: number;
  misses: number;
  pending: number;
  needs_review: number;
  voids: number;
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

/** Bloque Winamax del resumen de ``POST /refresh/odds``. */
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
