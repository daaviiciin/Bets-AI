/** Helpers para formatear los strings/Decimals que llegan del backend. */

export function toNumber(v: string | number | null | undefined): number {
  if (v == null) return 0;
  return typeof v === 'number' ? v : Number(v);
}

export function fmtPct(v: string | number | null, digits = 1): string {
  const n = toNumber(v) * 100;
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`;
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
