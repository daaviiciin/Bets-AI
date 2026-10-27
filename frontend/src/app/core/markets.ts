/**
 * Mercados de Winamax en lenguaje llano: nombre de cada mercado, grupo para filtrar y la
 * selección como frase ("Más de 2,5 goles", "Vinícius marca"). Ninguna clave cruda llega
 * a la pantalla: un mercado desconocido se enseña con su texto de Winamax.
 */

import { fmtLinea, toNumber } from './format';

/**
 * Cuota máxima que el modelo puede marcar como valor. Por encima, la cuota se enseña y se
 * puede llevar al boletín, pero es solo informativa: el EV del modelo con cuotas tan altas no
 * es fiable. La fuente de verdad es el backend (campo ``recommendable`` de cada línea); este
 * límite solo se usa si la línea llega sin ese campo.
 */
export const MAX_RECOMMENDABLE_ODDS = 4;

/** ¿Puede el modelo marcar esta línea como valor? Manda ``recommendable`` si viene. */
export function isRecommendable(line: {
  offered_odds: string | number;
  recommendable?: boolean | null;
}): boolean {
  if (line.recommendable != null) return line.recommendable;
  return toNumber(line.offered_odds) <= MAX_RECOMMENDABLE_ODDS;
}

/** Fila de un total (más/menos de N): cuota de cada lado, si existe. */
export interface LadderRow {
  over: { offered_odds: string } | null;
  under: { offered_odds: string } | null;
}

/**
 * Línea principal de un total: la más equilibrada (cuotas de "más" y "menos" más
 * parecidas). Si ninguna fila tiene los dos lados, la de cuota más cercana a 2,00.
 */
export function mainLineIndex(rows: readonly LadderRow[]): number {
  let best = 0;
  let bestGap = Infinity;
  rows.forEach((r, i) => {
    if (!r.over || !r.under) return;
    const gap = Math.abs(toNumber(r.over.offered_odds) - toNumber(r.under.offered_odds));
    if (gap < bestGap) [best, bestGap] = [i, gap];
  });
  if (bestGap < Infinity) return best;
  rows.forEach((r, i) => {
    const side = r.over ?? r.under;
    const gap = side ? Math.abs(toNumber(side.offered_odds) - 2) : Infinity;
    if (gap < bestGap) [best, bestGap] = [i, gap];
  });
  return best;
}

/**
 * Filas que se ven con el total plegado: la línea principal y sus vecinas (``around`` por
 * cada lado, desplazando la ventana si la principal está en un extremo) más las que pida
 * ``keep`` (con valor o ya en el boletín). Devuelve los índices.
 */
export function ladderSummary<T extends LadderRow>(
  rows: readonly T[],
  keep: (row: T) => boolean,
  around = 1,
): Set<number> {
  const size = Math.min(rows.length, around * 2 + 1);
  const start = Math.max(0, Math.min(mainLineIndex(rows) - around, rows.length - size));
  const out = new Set<number>();
  rows.forEach((r, i) => {
    if ((i >= start && i < start + size) || keep(r)) out.add(i);
  });
  return out;
}

export type MarketGroup =
  | 'principales'
  | 'goles'
  | 'mitades'
  | 'corners'
  | 'tarjetas'
  | 'tiros'
  | 'jugadores';

export const MARKET_GROUPS: { id: MarketGroup; label: string }[] = [
  { id: 'principales', label: 'Principales' },
  { id: 'goles', label: 'Goles' },
  { id: 'mitades', label: 'Por mitades' },
  { id: 'corners', label: 'Córners' },
  { id: 'tarjetas', label: 'Tarjetas' },
  { id: 'tiros', label: 'Tiros' },
  { id: 'jugadores', label: 'Jugadores' },
];

interface MarketMeta {
  label: string;
  group: MarketGroup;
  /** Orden dentro de la pestaña Cuotas (menor = antes). */
  order: number;
}

const MARKETS: Record<string, MarketMeta> = {
  h2h: { label: 'Resultado', group: 'principales', order: 1 },
  double_chance: { label: 'Doble oportunidad', group: 'principales', order: 2 },
  match_winner_dnb: { label: 'Gana sin empate', group: 'principales', order: 3 },
  btts: { label: 'Marcan los dos', group: 'principales', order: 4 },
  totals: { label: 'Goles del partido', group: 'goles', order: 10 },
  match_winner_and_totals: { label: 'Resultado y goles', group: 'goles', order: 11 },
  team_home_totals_goals: { label: 'Goles del local', group: 'goles', order: 12 },
  team_away_totals_goals: { label: 'Goles del visitante', group: 'goles', order: 13 },
  match_total_goals_odd_even: { label: 'Goles pares o impares', group: 'goles', order: 14 },
  match_first_half_totals: { label: 'Goles en la 1ª parte', group: 'mitades', order: 20 },
  match_second_half_totals: { label: 'Goles en la 2ª parte', group: 'mitades', order: 21 },
  team_home_first_half_totals_goals: { label: 'Goles del local en la 1ª parte', group: 'mitades', order: 22 },
  team_away_first_half_totals_goals: { label: 'Goles del visitante en la 1ª parte', group: 'mitades', order: 23 },
  team_home_second_half_totals_goals: { label: 'Goles del local en la 2ª parte', group: 'mitades', order: 24 },
  team_away_second_half_totals_goals: { label: 'Goles del visitante en la 2ª parte', group: 'mitades', order: 25 },
  match_highest_scoring_half: { label: 'Mitad con más goles', group: 'mitades', order: 26 },
  match_most_goals_half: { label: 'Mitad con más goles', group: 'mitades', order: 26 },
  match_corners_totals: { label: 'Córners del partido', group: 'corners', order: 30 },
  match_first_half_corners_totals: { label: 'Córners en la 1ª parte', group: 'corners', order: 31 },
  match_second_half_corners_totals: { label: 'Córners en la 2ª parte', group: 'corners', order: 32 },
  match_cards_totals: { label: 'Tarjetas del partido', group: 'tarjetas', order: 40 },
  match_first_half_cards_totals: { label: 'Tarjetas en la 1ª parte', group: 'tarjetas', order: 41 },
  match_second_half_cards_totals: { label: 'Tarjetas en la 2ª parte', group: 'tarjetas', order: 42 },
  match_shots_totals: { label: 'Tiros del partido', group: 'tiros', order: 50 },
  match_sot_totals: { label: 'Tiros a puerta del partido', group: 'tiros', order: 51 },
  team_home_sot_totals: { label: 'Tiros a puerta del local', group: 'tiros', order: 52 },
  team_away_sot_totals: { label: 'Tiros a puerta del visitante', group: 'tiros', order: 53 },
  player_anytime_scorer: { label: 'Marca en cualquier momento', group: 'jugadores', order: 60 },
  player_scorer_and_team_wins: { label: 'Marca y gana su equipo', group: 'jugadores', order: 61 },
  player_to_score_2plus: { label: 'Marca 2 o más', group: 'jugadores', order: 62 },
  player_first_scorer: { label: 'Primer goleador', group: 'jugadores', order: 63 },
  player_last_scorer: { label: 'Último goleador', group: 'jugadores', order: 64 },
  player_sot: { label: 'Tiros a puerta del jugador', group: 'jugadores', order: 65 },
  player_sot_alt: { label: 'Tiros a puerta del jugador', group: 'jugadores', order: 65 },
  player_shots: { label: 'Tiros del jugador', group: 'jugadores', order: 66 },
  player_shots_alt: { label: 'Tiros del jugador', group: 'jugadores', order: 66 },
  player_saves_alt: { label: 'Paradas del portero', group: 'jugadores', order: 66 },
  player_card_yes_no: { label: 'Tarjeta al jugador', group: 'jugadores', order: 67 },
  player_assists_alt: { label: 'Asistencias del jugador', group: 'jugadores', order: 68 },
  player_passes_alt: { label: 'Pases del jugador', group: 'jugadores', order: 69 },
  player_tackles_alt: { label: 'Entradas del jugador', group: 'jugadores', order: 70 },
};

export function marketLabel(key: string): string {
  return MARKETS[key]?.label ?? 'Otros mercados';
}

export function marketGroup(key: string): MarketGroup {
  return MARKETS[key]?.group ?? (key.startsWith('player_') ? 'jugadores' : 'principales');
}

export function marketOrder(key: string): number {
  return MARKETS[key]?.order ?? 99;
}

/** Lo que hace falta de una línea para describirla. */
export interface LineLike {
  market_key: string;
  outcome_name: string;
  outcome_point: string | null;
  player_name: string | null;
}

/** Sufijo de cada total: "goles", "córners en la 1ª parte"… */
const TOTAL_NOUN: Record<string, string> = {
  totals: 'goles',
  match_first_half_totals: 'goles en la 1ª parte',
  match_second_half_totals: 'goles en la 2ª parte',
  match_corners_totals: 'córners',
  match_first_half_corners_totals: 'córners en la 1ª parte',
  match_second_half_corners_totals: 'córners en la 2ª parte',
  match_cards_totals: 'tarjetas',
  match_first_half_cards_totals: 'tarjetas en la 1ª parte',
  match_second_half_cards_totals: 'tarjetas en la 2ª parte',
  match_shots_totals: 'tiros',
  match_sot_totals: 'tiros a puerta',
};

const TEAM_TOTALS: Record<string, { side: 'home' | 'away'; tail: string }> = {
  team_home_totals_goals: { side: 'home', tail: 'goles' },
  team_away_totals_goals: { side: 'away', tail: 'goles' },
  team_home_first_half_totals_goals: { side: 'home', tail: 'goles en la 1ª parte' },
  team_away_first_half_totals_goals: { side: 'away', tail: 'goles en la 1ª parte' },
  team_home_second_half_totals_goals: { side: 'home', tail: 'goles en la 2ª parte' },
  team_away_second_half_totals_goals: { side: 'away', tail: 'goles en la 2ª parte' },
  team_home_sot_totals: { side: 'home', tail: 'tiros a puerta' },
  team_away_sot_totals: { side: 'away', tail: 'tiros a puerta' },
};

function overUnder(name: string, point: string | null, noun: string): string {
  const n = name.toLowerCase();
  const verb = n === 'over' ? 'Más de' : n === 'under' ? 'Menos de' : name;
  return `${verb} ${fmtLinea(point)} ${noun}`.replace(/\s+/g, ' ').trim();
}

function draw(name: string): string {
  return name.replace(/\bDraw\b/g, 'empate').replace(/\bor\b/g, 'o');
}

/** Palabras que no distinguen a un club ("Real", "FC", "United"…). */
const COMMON = new Set([
  'fc', 'cf', 'sc', 'ssc', 'sv', 'vfb', 'ac', 'as', 'ud', 'cd', 'rc', 'afc', 'club', 'de', 'real',
  'united', 'city', 'town', 'the', 'and', 'y', 'calcio', 'olympique', 'stade', 'borussia',
]);

function tokens(name: string): string[] {
  return name
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, ' ')
    .split(/\s+/)
    .filter((t) => t.length > 1 && !COMMON.has(t));
}

/**
 * ¿Es el mismo club? Winamax escribe en español ("Atl. Madrid", "Nápoles", "Hamburgo"):
 * basta una palabra distintiva igual o con la misma raíz de 4 letras.
 */
export function sameTeam(a: string, b: string): boolean {
  if (a === b) return true;
  const tb = tokens(b);
  return tokens(a).some((x) =>
    tb.some((y) => x === y || (x.length >= 4 && y.length >= 4 && x.slice(0, 4) === y.slice(0, 4))),
  );
}

/** ``home`` / ``away`` de una selección del 1X2 (``null`` si no se sabe o es el empate). */
export function h2hSide(name: string, home: string, away: string): 'home' | 'away' | null {
  if (name === 'Draw') return null;
  const h = sameTeam(home, name);
  const a = sameTeam(away, name);
  return h === a ? null : h ? 'home' : 'away';
}

/**
 * Mercados de jugador por escalones (cuota a "más de N"). Tiros y tiros a puerta se leen como
 * en Winamax ("1+ tiros a puerta" = más de 0,5); las paradas, como línea ("Más de 1,5 paradas").
 */
const PLAYER_LADDERS: Record<string, { noun: string; plus: boolean }> = {
  player_sot: { noun: 'tiros a puerta', plus: true },
  player_sot_alt: { noun: 'tiros a puerta', plus: true },
  player_shots: { noun: 'tiros', plus: true },
  player_shots_alt: { noun: 'tiros', plus: true },
  player_saves_alt: { noun: 'paradas', plus: false },
};

/** ``"Dominic Solanke over 0.5"`` → ``"Dominic Solanke"`` (el jugador sin el sentido ni la línea). */
export function propPlayerName(line: Pick<LineLike, 'outcome_name' | 'player_name'>): string {
  return (line.player_name || line.outcome_name.replace(/\s+(over|under)\s+[\d.]+$/i, '')).trim();
}

/** ¿Es la línea "menos de N" de un mercado de jugador? */
export function isUnderProp(line: Pick<LineLike, 'outcome_name'>): boolean {
  return /\bunder\b/i.test(line.outcome_name);
}

/**
 * Escalón de jugador en lenguaje llano y sin el nombre: "1+ tiros a puerta",
 * "Más de 1,5 paradas". ``null`` si el mercado no va por escalones o no trae línea.
 */
export function playerLadderText(line: LineLike): string | null {
  const ladder = PLAYER_LADDERS[line.market_key];
  if (!ladder || line.outcome_point == null) return null;
  const p = toNumber(line.outcome_point);
  if (isUnderProp(line)) return `Menos de ${fmtLinea(p)} ${ladder.noun}`;
  if (ladder.plus && Number.isInteger(p + 0.5)) return `${p + 0.5}+ ${ladder.noun}`;
  return `Más de ${fmtLinea(p)} ${ladder.noun}`;
}

/** La selección como frase en español: lo que se lee en el botón y en el boletín. */
export function selectionLabel(line: LineLike, home: string, away: string): string {
  const { market_key: mk, outcome_name: name, outcome_point: point } = line;
  const player = propPlayerName(line);
  if (mk === 'h2h') {
    if (name === 'Draw') return 'Empate';
    const side = h2hSide(name, home, away);
    return `Gana ${side === 'home' ? home : side === 'away' ? away : name}`;
  }
  if (mk === 'double_chance') {
    const txt = draw(name);
    return txt.charAt(0).toUpperCase() + txt.slice(1);
  }
  if (mk === 'match_winner_dnb') return `${name === 'Draw' ? 'Empate' : name} (sin empate)`;
  if (mk === 'btts') return name.toLowerCase() === 'yes' ? 'Sí marcan los dos' : 'No marcan los dos';
  if (mk === 'match_total_goals_odd_even') return `Total de goles ${name.toLowerCase()}`;
  if (mk === 'match_highest_scoring_half' || mk === 'match_most_goals_half') {
    return name.toLowerCase() === 'igual'
      ? 'Mismos goles en las dos mitades'
      : `Más goles en la ${name.toLowerCase()}`;
  }
  if (TOTAL_NOUN[mk]) return overUnder(name, point, TOTAL_NOUN[mk]);
  const team = TEAM_TOTALS[mk];
  if (team) {
    const who = team.side === 'home' ? home : away;
    return `${who}: ${overUnder(name, point, team.tail).toLowerCase()}`;
  }
  if (mk === 'match_winner_and_totals') return `${draw(name)} goles`;
  if (mk === 'player_anytime_scorer') return `${player} marca`;
  if (mk === 'player_scorer_and_team_wins') return `${player} marca y gana su equipo`;
  if (mk === 'player_to_score_2plus') return `${player} marca 2 o más`;
  if (mk === 'player_first_scorer') return `${player} marca el primer gol`;
  if (mk === 'player_last_scorer') return `${player} marca el último gol`;
  if (mk === 'player_card_yes_no') return `${player} ve tarjeta`;
  const ladder = playerLadderText(line);
  if (ladder) return `${player}: ${ladder.charAt(0).toLowerCase()}${ladder.slice(1)}`;
  return name;
}
