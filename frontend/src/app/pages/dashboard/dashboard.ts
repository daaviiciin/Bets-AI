import { Component, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { finalize } from 'rxjs/operators';

import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import {
  confidenceClass,
  confidenceLabel,
  DISPLAY_ODDS_CAP,
  fmtAbsPct,
  fmtDateShort,
  fmtOdds,
  fmtPct,
  fmtTime,
  isOddsBelowLongshotCeiling,
  isOddsInLaymanRange,
  LAYMAN_ODDS_MAX,
  LAYMAN_ODDS_MIN,
  outcomeLabel,
  toNumber,
} from '../../core/format';
import {
  GradedOddsLine,
  Match,
  Odds,
  OddsRefreshSummary,
  SavedParlayLegPersist,
  ValueBetConfidence,
} from '../../core/models';

interface Kpi {
  label: string;
  value: string;
  hint?: string;
}

/** Mercados OU / totales equipo / estadísticas agregadas del partido (no props jugador). */
const MATCH_STATS_MARKET_KEYS = new Set([
  'totals',
  'team_home_totals_goals',
  'team_away_totals_goals',
  'match_corners_totals',
  'match_cards_totals',
  'match_shots_totals',
  'match_sot_totals',
  'match_first_half_totals',
  'team_home_first_half_totals_goals',
  'team_away_first_half_totals_goals',
  'match_first_half_corners_totals',
  'match_first_half_cards_totals',
  // 2ª mitad / extras Winamax (cuando existan en BD)
  'match_second_half_totals',
  'team_home_second_half_totals_goals',
  'team_away_second_half_totals_goals',
  'match_second_half_corners_totals',
  'match_second_half_cards_totals',
  'match_total_goals_odd_even',
  'match_winner_and_totals',
  'match_highest_scoring_half',
  'match_most_goals_half',
]);

export type MatchStatsSubFilter = 'all' | 'corners' | 'cards' | 'goals' | 'shots';

/** 1X2 · doble · BTTS. */
function isClassicMatchMarket(key: string): boolean {
  return key === 'h2h' || key === 'double_chance' || key === 'btts';
}

/** Nuevos ``match_*`` del scraper: totales / córners / tarjetas / mitades sin listarlos todos a mano. */
function isMatchStatsMarketFallback(key: string): boolean {
  if (!(key.startsWith('match_') || key.startsWith('team_home_') || key.startsWith('team_away_'))) {
    return false;
  }
  const k = key.toLowerCase();
  if (k.startsWith('match_') && (k.includes('player') || k.includes('scorer'))) return false;
  if (
    k === 'match_winner' ||
    k === 'match_winner_dnb' ||
    k === 'match_btts' ||
    k === 'match_double_chance'
  ) {
    return false;
  }
  return (
    k.includes('totals') ||
    k.includes('corners') ||
    k.includes('cards') ||
    k.includes('shots') ||
    k.includes('_sot') ||
    k.includes('half') ||
    k.includes('goal')
  );
}

function isMatchStatsMarket(key: string): boolean {
  if (MATCH_STATS_MARKET_KEYS.has(key)) return true;
  if (key.startsWith('player_')) return false;
  if (isClassicMatchMarket(key)) return false;
  return isMatchStatsMarketFallback(key);
}

function matchesMatchStatsSub(key: string, sub: MatchStatsSubFilter): boolean {
  if (sub === 'all') return true;
  if (sub === 'corners') return key.toLowerCase().includes('corners');
  if (sub === 'cards') return key.toLowerCase().includes('cards');
  const kl = key.toLowerCase();
  if (sub === 'shots') return kl.includes('shots') || kl.includes('_sot');
  // goals: goles totales / mitades / “mitad con más goles” — excluye córners, tarjetas, tiros
  if (kl.includes('corners') || kl.includes('cards')) return false;
  if (kl.includes('shots') || kl.includes('_sot')) return false;
  return (
    key === 'totals' ||
    kl.includes('totals_goals') ||
    kl.includes('goal') ||
    kl.includes('half') ||
    kl.includes('scoring') ||
    kl.includes('most_goals')
  );
}

/** Competiciones top‑5 para el selector del dashboard (``League.sport_key``). */
const DASHBOARD_LEAGUE_OPTIONS: readonly { sport_key: string; label: string }[] = [
  { sport_key: 'soccer_epl', label: 'Premier League' },
  { sport_key: 'soccer_italy_serie_a', label: 'Serie A' },
  { sport_key: 'soccer_germany_bundesliga', label: 'Bundesliga' },
  { sport_key: 'soccer_spain_la_liga', label: 'La Liga' },
  { sport_key: 'soccer_france_ligue_one', label: 'Ligue 1' },
];

@Component({
  selector: 'app-dashboard',
  templateUrl: './dashboard.html',
})
export class Dashboard {
  private readonly api = inject(ApiService);
  protected readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly loading = signal(true);
  protected readonly busy = signal(false);
  protected readonly refreshingOdds = signal(false);
  /** Resultado de la última actualización de cuotas (se muestra junto al botón). */
  protected readonly oddsStatus = signal<{ ok: boolean; text: string } | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly matches = signal<Match[]>([]);
  protected readonly selectedMatch = signal<Match | null>(null);
  protected readonly selectedMatchOdds = signal<Odds[]>([]);
  protected readonly loadingMatchOdds = signal(false);
  protected readonly gradedRows = signal<GradedOddsLine[]>([]);
  protected readonly parlayTitle = signal('');
  protected readonly parlaySaveHint = signal<string | null>(null);
  protected readonly oddsMarketFilter = signal('all');
  protected readonly oddsOutcomeFilter = signal('all');
  protected readonly oddsMin = signal(1);
  protected readonly oddsMax = signal(4);

  /** Vista inicial del producto: únicamente cuotas, sin valoración del modelo. */
  protected readonly oddsRows = computed(() => {
    const market = this.oddsMarketFilter();
    const outcome = this.oddsOutcomeFilter();
    const min = this.oddsMin();
    const max = this.oddsMax();
    return [...this.gradedRows()]
      .filter((row) => {
        const key = row.market_key.toLowerCase();
        if (market !== 'all' && key !== market) return false;
        const price = toNumber(row.offered_odds);
        if (price < min || price >= max) return false;
        return this.matchesOddsOutcome(row, outcome);
      })
      .sort((a, b) => {
        const dateA = Date.parse(a.commence_time);
        const dateB = Date.parse(b.commence_time);
        if (dateA !== dateB) return dateA - dateB;
        return toNumber(a.offered_odds) - toNumber(b.offered_odds);
      })
      .slice(0, 900);
  });

  protected readonly visibleMatchOdds = computed(() => {
    const market = this.oddsMarketFilter();
    const outcome = this.oddsOutcomeFilter();
    const min = this.oddsMin();
    const max = this.oddsMax();
    return this.selectedMatchOdds().filter((odd) => {
      const price = toNumber(odd.price);
      if (price < min || price >= max) return false;
      if (market !== 'all' && odd.market_key !== market) return false;
      const value = odd.outcome_name.toLowerCase();
      if (outcome === 'all') return true;
      if (outcome === 'draw') return value === 'draw' || value === 'empate';
      if (outcome === 'over') return value.includes('over') || value.includes('más') || value.includes('mas');
      if (outcome === 'under') return value.includes('under') || value.includes('menos');
      if (outcome === 'yes') return value === 'yes' || value === 'sí' || value === 'si';
      if (outcome === 'no') return value === 'no';
      const selected = this.selectedMatch();
      if (outcome === 'home') return !!selected && value === selected.home_team.name.toLowerCase();
      if (outcome === 'away') return !!selected && value === selected.away_team.name.toLowerCase();
      return true;
    });
  });

  private matchesOddsOutcome(row: GradedOddsLine, filter: string): boolean {
    if (filter === 'all') return true;
    const outcome = row.outcome_name.trim().toLowerCase();
    const home = row.home_team.trim().toLowerCase();
    const away = row.away_team.trim().toLowerCase();
    if (filter === 'home') return outcome === home || outcome.includes('local');
    if (filter === 'away') return outcome === away || outcome.includes('visitante');
    if (filter === 'draw') return outcome === 'draw' || outcome === 'empate';
    if (filter === 'over') return outcome === 'over' || outcome.includes('más') || outcome.includes('mas');
    if (filter === 'under') return outcome === 'under' || outcome.includes('menos');
    if (filter === 'yes') return outcome === 'yes' || outcome === 'sí' || outcome === 'si';
    if (filter === 'no') return outcome === 'no';
    return true;
  }

  protected setOddsMin(ev: Event): void {
    const value = Number((ev.target as HTMLInputElement).value);
    const next = Number.isFinite(value) ? Math.max(1, Math.min(3.99, value)) : 1;
    this.oddsMin.set(Math.min(next, this.oddsMax() - 0.01));
  }

  protected setOddsMax(ev: Event): void {
    const value = Number((ev.target as HTMLInputElement).value);
    const next = Number.isFinite(value) ? Math.max(1.01, Math.min(4, value)) : 4;
    this.oddsMax.set(Math.max(next, this.oddsMin() + 0.01));
  }

  /** all · clásicos (1X2/DC/BTTS) · totales/partido stats · jugadores */
  protected readonly marketPreset = signal<'all' | 'classic' | 'match_stats' | 'players'>('all');

  /** Dentro de «Totales partido»: refinar por tipo (córners, tarjetas, goles/mitades, tiros). */
  protected readonly matchStatsSub = signal<MatchStatsSubFilter>('all');

  protected readonly hideLongshots = signal(true);
  protected readonly comfortOddsOnly = signal(false);

  /** Probabilidad modelo 0–100 % (solo se listan líneas que ya tienen P modelo). */
  protected readonly probMinPct = signal(0);
  protected readonly probMaxPct = signal(100);

  protected readonly confHigh = signal(true);
  protected readonly confMedium = signal(true);
  protected readonly confLow = signal(true);
  protected readonly confUnknown = signal(true);

  /** Competición para pedir `/graded-odds?sport_key=` (vacío = todas). */
  protected readonly leagueSportKeyFilter = signal('');
  /** Incluye líneas sin P modelo (depuración: stats/Poisson ausentes por liga). */
  protected readonly debugShowLinesWithoutModel = signal(false);

  protected readonly leagueOptions = DASHBOARD_LEAGUE_OPTIONS;

  protected readonly rowsAfterMarket = computed(() => {
    const all = this.gradedRows();
    const preset = this.marketPreset();
    return all.filter((r) => {
      const k = r.market_key;
      switch (preset) {
        case 'players':
          return k.startsWith('player_');
        case 'classic':
          return isClassicMatchMarket(k);
        case 'match_stats':
          if (!isMatchStatsMarket(k)) return false;
          return matchesMatchStatsSub(k, this.matchStatsSub());
        default:
          return true;
      }
    });
  });

  /** Recuento por ``league_sport_key`` en la última respuesta del servidor (antes de filtros UI). */
  protected readonly leagueInventory = computed(() => {
    const by = new Map<string, { label: string; total: number; withModel: number }>();
    for (const r of this.gradedRows()) {
      const sk = (r.league_sport_key ?? '').trim() || '__unknown__';
      const prev = by.get(sk);
      if (!prev) {
        by.set(sk, {
          label: r.league || sk,
          total: 1,
          withModel: r.model_probability != null ? 1 : 0,
        });
      } else {
        prev.total++;
        if (r.model_probability != null) prev.withModel++;
      }
    }
    return by;
  });

  protected readonly leagueInventoryHint = computed(() => {
    const inv = this.leagueInventory();
    if (inv.size === 0) return '';
    return [...inv.entries()]
      .sort((a, b) => a[1].label.localeCompare(b[1].label))
      .map(([, v]) => `${v.label}: ${v.withModel}/${v.total} con P modelo`)
      .join(' · ');
  });

  protected readonly rowsFiltered = computed(() => {
    let base = this.rowsAfterMarket();
    if (this.hideLongshots()) {
      base = base.filter((r) => isOddsBelowLongshotCeiling(r.offered_odds));
    }
    if (this.comfortOddsOnly()) {
      base = base.filter((r) => isOddsInLaymanRange(r.offered_odds));
    }
    base = base.filter((r) => this.matchesConfidenceFilter(r.confidence));

    const pMin = this.probMinPct() / 100;
    const pMax = this.probMaxPct() / 100;
    base = base.filter((r) => {
      if (r.model_probability == null) {
        return this.debugShowLinesWithoutModel();
      }
      const p = toNumber(r.model_probability);
      return p >= pMin && p <= pMax;
    });

    return base;
  });

  protected readonly kpis = computed<Kpi[]>(() => {
    const band = this.rowsFiltered();
    const inSrv = this.gradedRows().length;
    const noProbSrv = this.gradedRows().filter((b) => b.model_probability == null).length;
    const dbg = this.debugShowLinesWithoutModel();
    if (band.length === 0) {
      return [
        {
          label: 'Líneas en vista',
          value: '0',
          hint:
            inSrv > 0
              ? `${inSrv} en servidor (${noProbSrv} sin P modelo${dbg ? '; depuración activa' : ' ocultas'})`
              : 'Actualiza datos o relaja filtros',
        },
        { label: 'Alta · media · baja', value: '0', hint: 'confianza del modelo' },
        { label: 'Con estimación Poisson', value: '0', hint: 'solo se listan líneas con P' },
        { label: 'Partidos', value: '0', hint: 'únicos tras filtros' },
      ];
    }
    const matches = new Set(band.map((b) => b.match_id)).size;
    const trio = `${band.filter((b) => b.confidence === 'high').length} · ${band.filter((b) => b.confidence === 'medium').length} · ${band.filter((b) => b.confidence === 'low').length}`;
    const withProb = band.length;
    return [
      {
        label: 'Líneas en vista',
        value: String(band.length),
        hint: `${inSrv} en servidor · ${noProbSrv} sin P no mostradas`,
      },
      { label: 'Alta · media · baja', value: trio, hint: '+ sin modelo según filtros' },
      { label: 'Con estimación Poisson', value: String(withProb), hint: 'todas las filas visibles' },
      { label: 'Partidos', value: String(matches), hint: 'encuentros distintos' },
    ];
  });

  /** Orden: confianza (alta→…→unknown) · EV modelo · cuota */
  protected readonly displayRows = computed(() => {
    const all = this.rowsFiltered();
    const rank: Record<string, number> = { high: 0, medium: 1, low: 2, unknown: 3 };
    return [...all]
      .sort((a, b) => {
        const da = rank[a.confidence] ?? 4;
        const db = rank[b.confidence] ?? 4;
        if (da !== db) return da - db;
        const me = toNumber(b.model_ev) - toNumber(a.model_ev);
        if (me !== 0) return me;
        return toNumber(b.offered_odds) - toNumber(a.offered_odds);
      })
      .slice(0, 900);
  });

  /**
   * Patas de una combinada de prueba (independencia: P conjunta ≈ producto de P modelo).
   * No sustituye al motor de valores del backend; es orientativa para usuarios tipo casa de apuestas.
   */
  protected readonly parlayLegs = signal<GradedOddsLine[]>([]);

  protected readonly parlayStats = computed(() => {
    const legs = this.parlayLegs();
    if (legs.length === 0) {
      return {
        n: 0,
        combinedOdds: null as number | null,
        modelJointProb: null as number | null,
        impliedJoint: null as number | null,
        ev: null as number | null,
        duplicateMatches: false,
      };
    }
    const combinedOdds = legs.reduce((acc, r) => acc * toNumber(r.offered_odds), 1);
    const impliedJoint =
      legs.every((r) => r.implied_probability != null && toNumber(r.implied_probability) > 0)
        ? legs.reduce((acc, r) => acc * toNumber(r.implied_probability), 1)
        : null;
    let modelJointProb: number | null = null;
    if (legs.every((r) => r.model_probability != null && toNumber(r.model_probability) > 0)) {
      modelJointProb = legs.reduce((acc, r) => acc * toNumber(r.model_probability), 1);
    }
    const ev =
      modelJointProb != null && combinedOdds > 0 ? modelJointProb * combinedOdds - 1 : null;
    const matchIds = legs.map((l) => l.match_id);
    const duplicateMatches = new Set(matchIds).size !== matchIds.length;
    return { n: legs.length, combinedOdds, modelJointProb, impliedJoint, ev, duplicateMatches };
  });

  constructor() {
    // No ejecutar el pipeline oficial (Flashscore + Winamax) en cada entrada al dashboard,
    // recarga F5 ni al navegar desde otras rutas: sólo lista cuotas ya en servidor.
    // El scrape pesado es explícito con «Actualizar desde fuentes».
    this.reloadJornada();
  }

  private loadJornada(): void {
    this.api
      .listMatches({ scope: 'target', daysAhead: 7, limit: 500 })
      .subscribe({
        next: (matches) => {
          this.matches.set(matches);
          this.loading.set(false);
        },
        error: (err) => {
          this.error.set(this.formatError(err));
          this.loading.set(false);
        },
      });
  }

  protected reloadJornada(): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.loading.set(true);
    this.error.set(null);
    this.selectedMatch.set(null);
    this.selectedMatchOdds.set([]);
    this.api
      .listMatches({ scope: 'target', daysAhead: 7, limit: 500 })
      .pipe(finalize(() => {
        this.busy.set(false);
        this.loading.set(false);
      }))
      .subscribe({
        next: (matches) => this.matches.set(matches),
        error: (err) => this.error.set(this.formatError(err)),
      });
  }

  protected selectMatch(match: Match): void {
    if (this.loadingMatchOdds()) return;
    this.selectedMatch.set(match);
    this.selectedMatchOdds.set([]);
    this.loadingMatchOdds.set(true);
    this.api
      .getMatchOdds(match.id, { bookmaker: 'winamax_es' })
      .pipe(finalize(() => this.loadingMatchOdds.set(false)))
      .subscribe({
        next: (odds) => this.selectedMatchOdds.set(odds),
        error: (err) => this.error.set(this.formatError(err)),
      });
  }

  protected oddsOutcome(odd: Odds): string {
    return odd.outcome_point ? `${odd.outcome_name} ${odd.outcome_point}` : odd.outcome_name;
  }

  protected marketLabel(key: string): string {
    const labels: Record<string, string> = {
      h2h: 'Resultado',
      double_chance: 'Doble oportunidad',
      totals: 'Goles totales',
      btts: 'Ambos marcan',
    };
    return labels[key] ?? key;
  }

  private gradedOddsRequestOpts(): {
    horizonHours: number;
    maxLines: number;
    sportKey?: string;
  } {
    const sk = this.leagueSportKeyFilter().trim();
    return {
      horizonHours: 52,
      maxLines: 5000,
      ...(sk ? { sportKey: sk } : {}),
    };
  }

  /**
   * Solo cuotas Winamax de los próximos 5 días (~1 min la primera vez; las siguientes
   * pulsaciones saltan los partidos leídos hace poco). Antes lanzaba el pipeline completo
   * (Flashscore + backfill, ~4 min); eso ya lo hace la tarea programada (09/15/21 h).
   * La lista de partidos sigue usable mientras tanto.
   */
  protected refreshOdds(): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.refreshingOdds.set(true);
    this.oddsStatus.set(null);
    const done = () => {
      this.refreshingOdds.set(false);
      this.busy.set(false);
    };
    this.api.refreshOdds().subscribe({
      next: (res) => {
        done();
        this.oddsStatus.set({ ok: res.ok, text: this.oddsStatusText(res) });
        this.reloadJornada();
      },
      error: (err) => {
        done();
        this.oddsStatus.set({
          ok: false,
          text: `No se pudieron actualizar las cuotas: ${this.formatError(err)}`,
        });
      },
    });
  }

  private oddsStatusText(res: OddsRefreshSummary): string {
    const w = res.winamax;
    const fresh = w.matches_skipped_fresh ?? 0;
    if (w.matches_matched === 0) {
      return fresh
        ? `${fmtTime(res.finished_at)} · Las cuotas ya estaban al día (${fresh} partidos)`
        : `${fmtTime(res.finished_at)} · Winamax no tiene cuotas para los partidos de los próximos 5 días`;
    }
    const failed = w.matches_failed ? ` · ${w.matches_failed} con error` : '';
    const skipped = fresh ? ` · ${fresh} ya al día` : '';
    return `Cuotas actualizadas a las ${fmtTime(res.finished_at)} · ${w.matches_matched} partidos${skipped}${failed}`;
  }

  protected reloadSuggestionsOnly(): void {
    this.reloadJornada();
  }

  protected setProbMinFromInput(ev: Event): void {
    const raw = Number((ev.target as HTMLInputElement).value);
    let v = Number.isFinite(raw) ? Math.min(100, Math.max(0, Math.round(raw))) : 0;
    const mx = this.probMaxPct();
    if (v > mx) v = mx;
    this.probMinPct.set(v);
  }

  protected setProbMaxFromInput(ev: Event): void {
    const raw = Number((ev.target as HTMLInputElement).value);
    let v = Number.isFinite(raw) ? Math.min(100, Math.max(0, Math.round(raw))) : 100;
    const mn = this.probMinPct();
    if (v < mn) v = mn;
    this.probMaxPct.set(v);
  }

  protected toggleConfidence(key: ValueBetConfidence): void {
    if (key === 'high') this.confHigh.update((x) => !x);
    else if (key === 'medium') this.confMedium.update((x) => !x);
    else if (key === 'low') this.confLow.update((x) => !x);
    else this.confUnknown.update((x) => !x);
    if (
      !this.confHigh() &&
      !this.confMedium() &&
      !this.confLow() &&
      !this.confUnknown()
    ) {
      if (key === 'high') this.confHigh.set(true);
      else if (key === 'medium') this.confMedium.set(true);
      else if (key === 'low') this.confLow.set(true);
      else this.confUnknown.set(true);
    }
  }

  protected isConfActive(key: ValueBetConfidence): boolean {
    if (key === 'high') return this.confHigh();
    if (key === 'medium') return this.confMedium();
    if (key === 'low') return this.confLow();
    return this.confUnknown();
  }

  protected onLeagueSportKeyChange(ev: Event): void {
    const v = (ev.target as HTMLSelectElement).value ?? '';
    this.leagueSportKeyFilter.set(v);
    this.reloadSuggestionsOnly();
  }

  protected toggleDebugWithoutModel(): void {
    this.debugShowLinesWithoutModel.update((x) => !x);
  }

  protected resetFilters(): void {
    this.marketPreset.set('all');
    this.matchStatsSub.set('all');
    this.hideLongshots.set(true);
    this.comfortOddsOnly.set(false);
    this.probMinPct.set(0);
    this.probMaxPct.set(100);
    this.confHigh.set(true);
    this.confMedium.set(true);
    this.confLow.set(true);
    this.confUnknown.set(true);
    this.leagueSportKeyFilter.set('');
    this.debugShowLinesWithoutModel.set(false);
    this.reloadSuggestionsOnly();
  }

  protected setMarketPreset(p: 'all' | 'classic' | 'match_stats' | 'players'): void {
    this.marketPreset.set(p);
    if (p !== 'match_stats') {
      this.matchStatsSub.set('all');
    }
  }

  protected matchesConfidenceFilter(c: ValueBetConfidence): boolean {
    switch (c) {
      case 'high':
        return this.confHigh();
      case 'medium':
        return this.confMedium();
      case 'low':
        return this.confLow();
      default:
        return this.confUnknown();
    }
  }

  protected outcome(row: GradedOddsLine): string {
    return outcomeLabel(row.market_key, row.outcome_name, row.outcome_point);
  }

  protected isPlayerMarket(row: GradedOddsLine): boolean {
    return row.market_key.startsWith('player_');
  }

  protected isTotalsRow(row: GradedOddsLine): boolean {
    return isMatchStatsMarket(row.market_key);
  }

  /** Fichas de temporada de equipos (backend); sin probabilidades ni jargon del modelo aquí. */
  protected summaryBullets(row: GradedOddsLine): string[] {
    const lines = row.model_summary_lines;
    if (Array.isArray(lines) && lines.length > 0) return lines;
    return ['Las estadísticas de equipo no llegaron desde el servidor.'];
  }

  protected rowParlayKey(row: GradedOddsLine): string {
    return `${row.match_id}|${row.market_key}|${row.outcome_name}|${row.outcome_point ?? ''}|${row.player_name ?? ''}`;
  }

  protected isInParlay(row: GradedOddsLine): boolean {
    const k = this.rowParlayKey(row);
    return this.parlayLegs().some((l) => this.rowParlayKey(l) === k);
  }

  protected addToParlay(row: GradedOddsLine): void {
    const k = this.rowParlayKey(row);
    this.parlayLegs.update((legs) => {
      if (legs.some((l) => this.rowParlayKey(l) === k)) return legs;
      return [...legs, row];
    });
  }

  protected removeParlayLeg(row: GradedOddsLine): void {
    const k = this.rowParlayKey(row);
    this.parlayLegs.update((legs) => legs.filter((l) => this.rowParlayKey(l) !== k));
  }

  protected clearParlay(): void {
    this.parlayLegs.set([]);
  }

  protected scrollToCombinadaPanel(): void {
    document.getElementById('combinada-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  protected saveCombinadaServidor(): void {
    if (!this.auth.isLoggedIn()) {
      void this.router.navigate(['/']);
      return;
    }
    const legsRows = this.parlayLegs();
    if (legsRows.length === 0) return;
    const legs: SavedParlayLegPersist[] = legsRows.map((r) => ({
      match_id: r.match_id,
      home_team: r.home_team,
      away_team: r.away_team,
      league: r.league,
      commence_time: r.commence_time,
      market_key: r.market_key,
      outcome_name: r.outcome_name,
      outcome_point: r.outcome_point,
      player_name: r.player_name,
      offered_odds: r.offered_odds,
      model_probability: r.model_probability,
    }));
    const titleRaw = this.parlayTitle().trim();
    const title = titleRaw.length > 0 ? titleRaw.slice(0, 160) : null;
    this.busy.set(true);
    this.parlaySaveHint.set(null);
    this.api
      .saveMyParlay({ title, legs })
      .pipe(finalize(() => this.busy.set(false)))
      .subscribe({
        next: () => {
          this.parlaySaveHint.set('Combinada guardada en la base de datos (pestaña Mis apuestas).');
          window.setTimeout(() => this.parlaySaveHint.set(null), 6200);
          this.clearParlay();
          this.parlayTitle.set('');
        },
        error: (err: { status?: number }) => {
          if (err?.status === 401) void this.router.navigate(['/']);
          else
            this.parlaySaveHint.set(
              'No se pudo guardar. ¿Migración «alembic upgrade head» y backend en marcha?',
            );
        },
      });
  }

  protected fmtOdds = fmtOdds;
  protected fmtPct = fmtPct;
  protected fmtAbsPct = fmtAbsPct;
  protected fmtDateShort = fmtDateShort;
  protected confidenceClass = confidenceClass;
  protected confidenceLabel = confidenceLabel;
  protected readonly oddsRangeLabel = `${LAYMAN_ODDS_MIN} – ${LAYMAN_ODDS_MAX}`;
  protected readonly displayOddsCap = DISPLAY_ODDS_CAP;

  readonly chipInactive =
    'px-3 py-1.5 rounded-lg border border-slate-700/90 bg-slate-950/50 text-slate-400 hover:border-slate-600 hover:text-slate-200 transition-colors disabled:opacity-45';
  readonly chipTealOn =
    'px-3 py-1.5 rounded-lg border border-teal-500/40 bg-teal-500/15 text-teal-100 shadow-sm shadow-teal-900/10 disabled:opacity-45';
  readonly chipConfHighOn =
    'px-3 py-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/12 text-emerald-200 disabled:opacity-45';
  readonly chipConfMediumOn =
    'px-3 py-1.5 rounded-lg border border-amber-500/40 bg-amber-500/12 text-amber-200 disabled:opacity-45';
  readonly chipConfLowOn =
    'px-3 py-1.5 rounded-lg border border-rose-500/35 bg-rose-500/10 text-rose-200 disabled:opacity-45';
  readonly chipConfUnknownOn =
    'px-3 py-1.5 rounded-lg border border-slate-500/50 bg-slate-800 text-slate-200 disabled:opacity-45';

  private formatError(err: unknown): string {
    if (err instanceof Error) return err.message;
    if (typeof err === 'object' && err && 'message' in err) {
      return String((err as { message: unknown }).message);
    }
    return 'No se pudieron cargar los datos.';
  }
}
