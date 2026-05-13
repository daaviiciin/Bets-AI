import { Component, computed, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { forkJoin, of } from 'rxjs';
import { catchError } from 'rxjs/operators';

import { ApiService } from '../../core/api.service';
import { evColorClass, fmtAbsPct, fmtDate, fmtOdds, toNumber } from '../../core/format';
import {
  Match,
  MatchAnalysis,
  MatchPlayerProps,
  MatchStats,
  MarketPrediction,
  Odds,
  PlayerPropOdds,
} from '../../core/models';

interface OutcomeRow {
  outcome: string;
  point: string | null;
  prices: Record<string, { price: number; bookmaker: string }>;
  best?: { bookie: string; price: number };
}

interface MarketGroup {
  key: string;
  label: string;
  bookies: string[];
  rows: OutcomeRow[];
}

const MARKET_LABELS: Record<string, string> = {
  h2h: 'Resultado del partido (1X2)',
  totals: 'Total de goles (Over / Under)',
  btts: 'Ambos equipos marcan (BTTS)',
  double_chance: 'Doble oportunidad (1X · 12 · X2)',
};

@Component({
  selector: 'app-match-detail',
  imports: [RouterLink],
  templateUrl: './match-detail.html',
})
export class MatchDetail {
  readonly id = input.required<string>();

  private readonly api = inject(ApiService);

  protected readonly match = signal<Match | null>(null);
  protected readonly odds = signal<Odds[]>([]);
  protected readonly analysis = signal<MatchAnalysis | null>(null);
  protected readonly stats = signal<MatchStats | null>(null);
  protected readonly playerProps = signal<MatchPlayerProps | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);

  protected readonly markets = computed<MarketGroup[]>(() => this.buildMarketGroups());
  protected readonly h2hPredictions = computed(() =>
    (this.analysis()?.predictions ?? []).filter((p) => p.market === 'h2h'),
  );
  protected readonly totalsPredictions = computed(() =>
    (this.analysis()?.predictions ?? []).filter((p) => p.market.startsWith('totals')),
  );
  protected readonly bttsPredictions = computed(() =>
    (this.analysis()?.predictions ?? []).filter((p) => p.market === 'btts'),
  );
  protected readonly doubleChancePredictions = computed(() =>
    (this.analysis()?.predictions ?? []).filter((p) => p.market === 'double_chance'),
  );

  constructor() {
    queueMicrotask(() => this.load());
  }

  private load(): void {
    const matchId = Number(this.id());
    this.loading.set(true);
    this.error.set(null);
    forkJoin({
      match: this.api.getMatch(matchId),
      odds: this.api.getMatchOdds(matchId),
      analysis: this.api.getMatchAnalysis(matchId).pipe(catchError(() => of(null))),
      stats: this.api.getMatchStats(matchId).pipe(catchError(() => of(null))),
      playerProps: this.api
        .getMatchPlayerProps(matchId, { bookmaker: 'winamax_es' })
        .pipe(catchError(() => of(null))),
    }).subscribe({
      next: ({ match, odds, analysis, stats, playerProps }) => {
        this.match.set(match);
        this.odds.set(odds);
        this.analysis.set(analysis);
        this.stats.set(stats);
        this.playerProps.set(playerProps);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(this.formatError(err));
        this.loading.set(false);
      },
    });
  }

  private buildMarketGroups(): MarketGroup[] {
    const byMarket = new Map<string, Odds[]>();
    for (const o of this.odds()) {
      if (!byMarket.has(o.market_key)) byMarket.set(o.market_key, []);
      byMarket.get(o.market_key)!.push(o);
    }

    const groups: MarketGroup[] = [];
    for (const [marketKey, items] of byMarket) {
      const bookieSet = new Set<string>();
      const rowMap = new Map<string, OutcomeRow>();

      for (const o of items) {
        bookieSet.add(o.bookmaker.title);
        const rowKey = `${o.outcome_name}|${o.outcome_point ?? ''}`;
        if (!rowMap.has(rowKey)) {
          rowMap.set(rowKey, {
            outcome: o.outcome_name,
            point: o.outcome_point,
            prices: {},
          });
        }
        const row = rowMap.get(rowKey)!;
        row.prices[o.bookmaker.title] = {
          price: toNumber(o.price),
          bookmaker: o.bookmaker.title,
        };
      }

      const rows = Array.from(rowMap.values());
      for (const row of rows) {
        let best: { bookie: string; price: number } | undefined;
        for (const [bookie, p] of Object.entries(row.prices)) {
          if (!best || p.price > best.price) best = { bookie, price: p.price };
        }
        row.best = best;
      }

      groups.push({
        key: marketKey,
        label: MARKET_LABELS[marketKey] ?? marketKey,
        bookies: Array.from(bookieSet).sort(),
        rows: rows.sort((a, b) =>
          (a.point ?? '').localeCompare(b.point ?? '') || a.outcome.localeCompare(b.outcome),
        ),
      });
    }
    return groups.sort((a, b) => a.key.localeCompare(b.key));
  }

  protected fmtOdds = fmtOdds;
  protected fmtDate = fmtDate;
  protected fmtAbsPct = fmtAbsPct;
  protected evColorClass = evColorClass;
  protected marketRows(block: { [marketKey: string]: PlayerPropOdds[] }): [string, PlayerPropOdds[]][] {
    return Object.entries(block).sort((a, b) => a[0].localeCompare(b[0]));
  }

  protected priceFor(row: OutcomeRow, bookie: string): number | null {
    return row.prices[bookie]?.price ?? null;
  }

  protected isBest(row: OutcomeRow, bookie: string): boolean {
    return row.best?.bookie === bookie;
  }

  protected isValuePrediction(p: MarketPrediction): boolean {
    return (p.expected_value ?? 0) > 0.02;
  }

  protected fmtLambda(v: number): string {
    return v.toFixed(2);
  }

  protected fmtPerNine(v: number | null): string {
    if (v == null) return '—';
    return v.toFixed(2);
  }

  protected goalsForPerMatch(v: number | null): string {
    if (v == null) return '—';
    return v.toFixed(2);
  }

  protected formNumber(n: number | null): string {
    return n == null ? '—' : String(n);
  }

  /** Devuelve el porcentaje del valor home sobre el total (home + away) en 0-100. */
  protected statShareHome(home: number | string | null, away: number | string | null): number {
    const h = home == null ? 0 : Number(home);
    const a = away == null ? 0 : Number(away);
    const sum = h + a;
    if (sum <= 0) return 50;
    return Math.round((h / sum) * 100);
  }

  protected statValue(v: number | string | null): string {
    if (v == null || v === '') return '—';
    return String(v);
  }

  protected fmtPasses(completed: number | null, attempted: number | null, pct: number | null): string {
    if (completed != null && attempted != null) return `${pct ?? '—'}% (${completed}/${attempted})`;
    if (pct != null) return `${pct}%`;
    return '—';
  }

  private static readonly HALF_METRIC_FIELDS: readonly (keyof MatchStats['home'])[] = [
    'goals_1h',
    'goals_2h',
    'corners_1h',
    'corners_2h',
    'yellow_cards_1h',
    'yellow_cards_2h',
    'red_cards_1h',
    'red_cards_2h',
  ];

  protected hasHalfSlice(st: MatchStats): boolean {
    const seen = (side: MatchStats['home']) =>
      MatchDetail.HALF_METRIC_FIELDS.some((f) => side[f] != null);
    return seen(st.home) || seen(st.away);
  }

  private formatError(err: unknown): string {
    if (err instanceof Error) return err.message;
    return 'Error cargando el partido.';
  }
}
