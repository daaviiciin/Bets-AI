/** Helpers para formatear los strings/Decimals que llegan del backend. */

import type { GradedOddsLine, ValueBet } from './models';

export function toNumber(v: string | number | null | undefined): number {
  if (v == null) return 0;
  return typeof v === 'number' ? v : Number(v);
}

/** Rango de cuota “pagadera” para favoritos (sin ir a cuotones). */
export const LAYMAN_ODDS_MIN = 1.3;
export const LAYMAN_ODDS_MAX = 1.8;

/**
 * Por encima de esto tratamos cuota como “cuotón” largo (p. ej. goleadores @15+).
 * Misma regla para mercado de partido y props de jugador.
 */
export const DISPLAY_ODDS_CAP = 10;

export function isOddsInLaymanRange(price: string | number | null | undefined): boolean {
  const o = toNumber(price);
  return o >= LAYMAN_ODDS_MIN && o <= LAYMAN_ODDS_MAX;
}

export function isOddsBelowLongshotCeiling(price: string | number | null | undefined): boolean {
  const o = toNumber(price);
  return o > 1 && o <= DISPLAY_ODDS_CAP;
}

/**
 * Breve explicación en castellano para usuarios no técnicos (sin EV).
 * Aplica tanto a value bets clásicas como a filas ``GradedOddsLine``.
 */
export function laymanInsight(b: ValueBet | GradedOddsLine): string {
  const mk = b.market_key;
  const h = b.home_team.trim();
  const a = b.away_team.trim();
  const name = b.outcome_name.trim();
  const ln = name.toLowerCase();
  const mp = b.model_probability != null ? toNumber(b.model_probability) : null;
  const modelo =
    mp != null && mp > 0
      ? ` Con estadísticas recientes + temporada, situamos ~${Math.round(mp * 100)} % de acierto para este resultado.`
      : '';

  const zona = isOddsInLaymanRange(b.offered_odds)
    ? ' Cuota en la franja cómoda 1,30–1,80: suele estar en el “rango jugable” sin ir a golpes de efecto muy largos ni a muy favoritos muertos.'
    : '';

  if (mk === 'h2h') {
    if (name === h || ln === h.toLowerCase()) {
      return `${h} en campo propio: suele encajar cuando el equipo sufre pocos goles en casa y la cuota no está regalada.${modelo}${zona}`;
    }
    if (name === a || ln === a.toLowerCase()) {
      return `Victoria de ${a} fuera; sólo la enseñamos si el modelo ve hueco real, no porque la cuota “brille” sola.${modelo}${zona}`;
    }
    if (ln === 'draw' || ln === 'tie' || ln.includes('empate')) {
      return `Empate: encaja cuando el partido suele igualarse tras las ocasiones.${modelo}${zona}`;
    }
  }
  if (mk === 'totals') {
    if (ln === 'over') {
      return `Más goles que la línea: encaja cuando ambos tienen llegada y el ritmo suele ser alto.${modelo}${zona}`;
    }
    return `Menos goles: encaja con partidos trabados o defensas que aguantan.${modelo}${zona}`;
  }
  if (mk === 'double_chance') {
    return `Cobras con dos signos del partido (p. ej. el local no pierde): amortigua el resultado sorpresa.${modelo}${zona}`;
  }
  if (mk === 'btts') {
    if (ln === 'yes' || ln.includes('yes')) {
      return `Que marquen los dos suele aparecer cuando rara vez ves a uno a cero.${modelo}${zona}`;
    }
    return `Que falle uno a cero cuando el modelo ve partido cerrado.${modelo}${zona}`;
  }
  if (mk === 'team_home_totals_goals' || mk === 'team_away_totals_goals') {
    const lado = mk === 'team_home_totals_goals' ? 'equipo local' : 'equipo visitante';
    return (
      `Total de goles del ${lado} según nuestras esperanzas derivadas del ataque/defensa.${modelo}${zona}`
    );
  }
  if (mk === 'player_sot' || mk === 'player_sot_alt') {
    return `Tiros a puerta individuales: cruzamos la línea con la media habitual del jugador en liga.${modelo}${zona}`;
  }
  if (mk === 'player_shots' || mk === 'player_shots_alt') {
    return `Tiros totales del jugador frente a su ritmo medio en temporada (Poisson sobre la línea).${modelo}${zona}`;
  }
  if (
    mk === 'player_anytime_scorer' ||
    mk === 'player_to_score_2plus' ||
    mk === 'player_first_scorer' ||
    mk === 'player_last_scorer' ||
    mk === 'player_scorer_and_team_wins'
  ) {
    return `Goleador: comparamos la cuota con goles esperados desde sus números de liga.${modelo}${zona}`;
  }
  if (mk === 'player_card_yes_no') {
    return `Disciplina: aproximación Poisson desde tarjetas acumuladas en temporada.${modelo}${zona}`;
  }
  if (mk === 'match_corners_totals' || mk === 'match_cards_totals' || mk === 'match_shots_totals' || mk === 'match_sot_totals') {
    return `Mercado sobre totales del partido; la etiqueta solo refleja el modelo estadístico si tenemos soporte.${modelo}${zona}`;
  }
  return `Mercado revisado contra el modelo del proyecto donde hay datos suficientes.${modelo}${zona}`;
}

export function fmtPct(v: string | number | null, digits = 1): string {
  const n = toNumber(v) * 100;
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`;
}

export function fmtAbsPct(v: string | number | null, digits = 1): string {
  const n = toNumber(v) * 100;
  return `${n.toFixed(digits)}%`;
}

export function fmtOdds(v: string | number | null, digits = 2): string {
  return toNumber(v).toFixed(digits);
}

export function fmtEur(v: string | number | null, digits = 2): string {
  return `${toNumber(v).toFixed(digits)} €`;
}

const EUROS = new Intl.NumberFormat('es-ES', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  useGrouping: true,
});

/** Euros a la española ("1.250,50 €"); con `signo`, "+12,00 €" / "−8,50 €" (beneficios). */
export function fmtEuros(v: string | number | null | undefined, signo = false): string {
  if (v === null || v === undefined || v === '') return '—';
  const n = toNumber(v);
  const txt = `${EUROS.format(Math.abs(n))} €`;
  if (n < 0) return `−${txt}`;
  return signo && n > 0 ? `+${txt}` : txt;
}

/** Lee un importe escrito a mano ("10", "12,5", "12.50"): texto con 2 decimales o null si no vale. */
export function parseImporte(raw: string): string | null {
  const limpio = raw.trim().replace(/\s|€/g, '').replace(',', '.');
  if (!/^\d+(\.\d{1,2})?$/.test(limpio)) return null;
  const n = Number(limpio);
  return n > 0 && n <= 100000 ? n.toFixed(2) : null;
}

export function fmtDate(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('es-ES', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function fmtDateShort(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('es-ES', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** Solo la hora: "14:32". */
export function fmtTime(iso: string): string {
  if (!iso) return '';
  return new Date(iso).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
}

const ES_NUMBER = new Map<number, Intl.NumberFormat>();

/** Número a la española con decimales fijos: ``2.5`` → ``"2,50"`` (``digits=2``). */
export function fmtNum(v: string | number | null | undefined, digits = 2): string {
  if (v == null || v === '') return '–';
  let f = ES_NUMBER.get(digits);
  if (!f) {
    f = new Intl.NumberFormat('es-ES', { minimumFractionDigits: digits, maximumFractionDigits: digits });
    ES_NUMBER.set(digits, f);
  }
  return f.format(toNumber(v));
}

/** Cuota a la española: ``"2.050"`` → ``"2,05"``. */
export function fmtCuota(v: string | number | null | undefined): string {
  return fmtNum(v, 2);
}

/** Línea de un mercado sin ceros sobrantes: ``"2.50"`` → ``"2,5"``; ``"1.00"`` → ``"1"``. */
export function fmtLinea(v: string | number | null | undefined): string {
  if (v == null || v === '') return '';
  return toNumber(v).toLocaleString('es-ES', { maximumFractionDigits: 2 });
}

/** Probabilidad 0–1 como porcentaje entero: ``0.534`` → ``"53 %"``. */
export function fmtProb(v: string | number | null | undefined): string {
  if (v == null || v === '') return '–';
  return `${Math.round(toNumber(v) * 100)} %`;
}

/** EV con signo y sin decimales: ``0.064`` → ``"+6 %"``. */
export function fmtEv(v: string | number | null | undefined): string {
  if (v == null || v === '') return '–';
  const n = Math.round(toNumber(v) * 100);
  return `${n > 0 ? '+' : ''}${n} %`;
}

/** Clave de día local (``2026-10-16``) para agrupar partidos. */
export function dayKey(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** "Hoy", "Mañana" o "Sábado 18". */
export function fmtDayName(iso: string, now = new Date()): string {
  const key = dayKey(iso);
  if (key === dayKey(now.toISOString())) return 'Hoy';
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  if (key === dayKey(tomorrow.toISOString())) return 'Mañana';
  const d = new Date(iso);
  const weekday = d.toLocaleDateString('es-ES', { weekday: 'long' });
  return `${weekday.charAt(0).toUpperCase()}${weekday.slice(1)} ${d.getDate()}`;
}

/** "Viernes 16 oct · 21:00". */
export function fmtKickoff(iso: string): string {
  const d = new Date(iso);
  const day = d.toLocaleDateString('es-ES', { weekday: 'long', day: 'numeric', month: 'short' });
  return `${day.charAt(0).toUpperCase()}${day.slice(1).replace('.', '')} · ${fmtTime(iso)}`;
}

/** "hace 12 min", "hace 3 h", "hace 2 días". */
export function fmtAgo(iso: string | null, now = Date.now()): string {
  if (!iso) return '';
  const min = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60000));
  if (min < 1) return 'ahora mismo';
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  if (h < 48) return `hace ${h} h`;
  return `hace ${Math.round(h / 24)} días`;
}

export function evColorClass(ev: string | number | null): string {
  const n = toNumber(ev);
  if (n >= 0.1) return 'text-brand-cyan';
  if (n >= 0.05) return 'text-brand-cyan/90';
  if (n >= 0.02) return 'text-amber-300';
  if (n > 0) return 'text-brand-muted';
  return 'text-rose-400';
}

const MATCH_SIDE_TOTAL_KEYS: Record<string, string> = {
  match_corners_totals: 'Córneres (partido)',
  match_cards_totals: 'Tarjetas (partido)',
  match_shots_totals: 'Tiros (partido)',
  match_sot_totals: 'Tiros a puerta (partido)',
  match_first_half_totals: 'Goles (1ª mitad, partido)',
  match_second_half_totals: 'Goles (2ª mitad, partido)',
  match_first_half_corners_totals: 'Córneres (1ª mitad)',
  match_first_half_cards_totals: 'Tarjetas (1ª mitad)',
  match_second_half_corners_totals: 'Córneres (2ª mitad)',
  match_second_half_cards_totals: 'Tarjetas (2ª mitad)',
  match_highest_scoring_half: 'Mitad con más goles',
  match_most_goals_half: 'Mitad con más goles',
};

export function outcomeLabel(market: string, name: string, point: string | null): string {
  if (market === 'totals') return `${name} ${point ?? ''}`.trim();
  if (market === 'team_home_totals_goals' || market === 'team_away_totals_goals') {
    const side = market === 'team_home_totals_goals' ? 'Local' : 'Visitante';
    return `Goles ${side}: ${String(name)} ${point ?? ''}`.trim();
  }
  if (
    market === 'team_home_first_half_totals_goals' ||
    market === 'team_away_first_half_totals_goals'
  ) {
    const side = market === 'team_home_first_half_totals_goals' ? 'Local' : 'Visitante';
    return `Goles 1ª parte ${side}: ${String(name)} ${point ?? ''}`.trim();
  }
  if (
    market === 'team_home_second_half_totals_goals' ||
    market === 'team_away_second_half_totals_goals'
  ) {
    const side = market === 'team_home_second_half_totals_goals' ? 'Local' : 'Visitante';
    return `Goles 2ª parte ${side}: ${String(name)} ${point ?? ''}`.trim();
  }
  if (MATCH_SIDE_TOTAL_KEYS[market]) {
    return `${MATCH_SIDE_TOTAL_KEYS[market]}: ${String(name)} ${point ?? ''}`.trim();
  }
  if (market === 'player_sot' || market === 'player_sot_alt') return name.trim();
  if (market === 'player_shots' || market === 'player_shots_alt') return name.trim();
  return name.trim();
}

export function confidenceClass(confidence: string | null | undefined): string {
  switch (confidence) {
    case 'high':
      return 'bg-brand-cyan/12 text-brand-cyan border-brand-cyan/35';
    case 'medium':
      return 'bg-amber-500/15 text-amber-300 border-amber-400/30';
    case 'low':
      return 'bg-rose-500/15 text-rose-300 border-rose-400/30';
    default:
      return 'bg-brand-bg-panel/60 text-brand-muted border-brand-blue/25';
  }
}

export function confidenceLabel(confidence: string | null | undefined): string {
  switch (confidence) {
    case 'high':
      return 'Alta';
    case 'medium':
      return 'Media';
    case 'low':
      return 'Baja';
    default:
      return 'Sin modelo';
  }
}
