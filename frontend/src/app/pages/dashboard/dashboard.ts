import { Component, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { of } from 'rxjs';
import { catchError, finalize, switchMap } from 'rxjs/operators';

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
  isOddsBelowLongshotCeiling,
  isOddsInLaymanRange,
  LAYMAN_ODDS_MAX,
  LAYMAN_ODDS_MIN,
  outcomeLabel,
  toNumber,
} from '../../core/format';
import { GradedOddsLine, SavedParlayLegPersist, ValueBetConfidence } from '../../core/models';

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
]);

/** 1X2 · doble · BTTS. */
function isClassicMatchMarket(key: string): boolean {
  return key === 'h2h' || key === 'double_chance' || key === 'btts';
}

function isMatchStatsMarket(key: string): boolean {
  return MATCH_STATS_MARKET_KEYS.has(key);
}

@Component({
  selector: 'app-dashboard',
  imports: [RouterLink],
  templateUrl: './dashboard.html',
})
export class Dashboard {
  private readonly api = inject(ApiService);
  protected readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly loading = signal(true);
  protected readonly busy = signal(false);
  protected readonly scrapingOfficial = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly gradedRows = signal<GradedOddsLine[]>([]);
  protected readonly parlayTitle = signal('');
  protected readonly parlaySaveHint = signal<string | null>(null);

  /** all · clásicos (1X2/DC/BTTS) · totales/partido stats · jugadores */
  protected readonly marketPreset = signal<'all' | 'classic' | 'match_stats' | 'players'>('all');

  protected readonly hideLongshots = signal(true);
  protected readonly comfortOddsOnly = signal(false);

  /** Probabilidad modelo 0–100 %. */
  protected readonly probMinPct = signal(0);
  protected readonly probMaxPct = signal(100);
  /** Incluye apuestas sin P modelo (vacío en columna probabilidad). */
  protected readonly includeNoProbRows = signal(true);

  protected readonly confHigh = signal(true);
  protected readonly confMedium = signal(true);
  protected readonly confLow = signal(true);
  protected readonly confUnknown = signal(true);

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
          return isMatchStatsMarket(k);
        default:
          return true;
      }
    });
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
      if (r.model_probability == null) return this.includeNoProbRows();
      const p = toNumber(r.model_probability);
      return p >= pMin && p <= pMax;
    });

    return base;
  });

  protected readonly kpis = computed<Kpi[]>(() => {
    const band = this.rowsFiltered();
    const totalSrv = this.gradedRows().length;
    if (band.length === 0) {
      return [
        {
          label: 'Líneas en vista',
          value: '0',
          hint: totalSrv > 0 ? `${totalSrv} en servidor · ajusta filtros` : 'Actualiza datos o relaja filtros',
        },
        { label: 'Alta · media · baja', value: '0', hint: 'confianza del modelo' },
        { label: 'Con probabilidad modelo', value: '0', hint: 'P estimada cuando hay estadística' },
        { label: 'Partidos', value: '0', hint: 'únicos tras filtros' },
      ];
    }
    const matches = new Set(band.map((b) => b.match_id)).size;
    const trio = `${band.filter((b) => b.confidence === 'high').length} · ${band.filter((b) => b.confidence === 'medium').length} · ${band.filter((b) => b.confidence === 'low').length}`;
    const withProb = band.filter((b) => b.model_probability != null).length;
    return [
      {
        label: 'Líneas en vista',
        value: String(band.length),
        hint: `${totalSrv} sin filtro de vista`,
      },
      { label: 'Alta · media · baja', value: trio, hint: '+ sin modelo según filtros' },
      { label: 'Con probabilidad modelo', value: String(withProb), hint: `${band.length - withProb} sin P o fuera de rango` },
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
    this.reloadSuggestionsOnly();
  }

  /** Pipeline oficial en servidor + tabla calificada (sin mostrar logs de pipeline en UI). */
  protected runOfficialPipelineThenLoadBets(): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.scrapingOfficial.set(true);
    this.loading.set(true);
    this.error.set(null);

    this.api
      .refreshOfficialPipeline({
        statsDaysBack: 10,
        statsAllLeagues: true,
        fixturesDaysAhead: 14,
        winamaxDaysAhead: 3,
        winamaxMaxMatches: 200,
      })
      .pipe(
        catchError(() => of(null)),
        switchMap(() => this.api.listGradedOdds({ horizonHours: 52, maxLines: 5000 })),
        finalize(() => {
          this.scrapingOfficial.set(false);
          this.busy.set(false);
          this.loading.set(false);
        }),
      )
      .subscribe({
        next: (data) => this.gradedRows.set(data),
        error: (err) => this.error.set(this.formatError(err)),
      });
  }

  protected reloadSuggestionsOnly(): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.loading.set(true);
    this.error.set(null);
    this.api
      .listGradedOdds({ horizonHours: 52, maxLines: 5000 })
      .pipe(
        finalize(() => {
          this.busy.set(false);
          this.loading.set(false);
        }),
      )
      .subscribe({
        next: (data) => this.gradedRows.set(data),
        error: (err) => this.error.set(this.formatError(err)),
      });
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

  protected resetFilters(): void {
    this.marketPreset.set('all');
    this.hideLongshots.set(true);
    this.comfortOddsOnly.set(false);
    this.probMinPct.set(0);
    this.probMaxPct.set(100);
    this.includeNoProbRows.set(true);
    this.confHigh.set(true);
    this.confMedium.set(true);
    this.confLow.set(true);
    this.confUnknown.set(true);
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
