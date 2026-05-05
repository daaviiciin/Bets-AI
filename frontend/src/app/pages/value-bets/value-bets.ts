import { Component, computed, effect, inject, signal } from '@angular/core';

import { ApiService } from '../../core/api.service';
import {
  confidenceClass,
  confidenceLabel,
  evColorClass,
  fmtAbsPct,
  fmtDateShort,
  fmtOdds,
  fmtPct,
  outcomeLabel,
} from '../../core/format';
import { ValueBet, ValueBetConfidence } from '../../core/models';

const LEAGUE_OPTIONS: { key: string; label: string }[] = [
  { key: '', label: 'Todas las ligas' },
  { key: 'soccer_spain_la_liga', label: 'LaLiga' },
  { key: 'soccer_epl', label: 'Premier League' },
  { key: 'soccer_germany_bundesliga', label: 'Bundesliga' },
  { key: 'soccer_italy_serie_a', label: 'Serie A' },
  { key: 'soccer_france_ligue_one', label: 'Ligue 1' },
  { key: 'soccer_uefa_champs_league', label: 'Champions League' },
  { key: 'soccer_uefa_europa_league', label: 'Europa League' },
  { key: 'soccer_uefa_europa_conference_league', label: 'Conference League' },
];

const EV_OPTIONS: { value: number; label: string }[] = [
  { value: 0.0, label: 'Cualquiera' },
  { value: 0.02, label: '+2 %' },
  { value: 0.05, label: '+5 %' },
  { value: 0.08, label: '+8 %' },
  { value: 0.1, label: '+10 %' },
];

const ODDS_BAND_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'Todas' },
  { value: 'average', label: 'Promedio (1.70 - 2.30)' },
  { value: 'low', label: 'Bajas (1.30 - 1.80)' },
  { value: 'high', label: 'Altas (> 2.30)' },
];

@Component({
  selector: 'app-value-bets',
  imports: [],
  templateUrl: './value-bets.html',
})
export class ValueBets {
  private readonly api = inject(ApiService);

  protected readonly bets = signal<ValueBet[]>([]);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);

  protected readonly leagueOptions = LEAGUE_OPTIONS;
  protected readonly evOptions = EV_OPTIONS;
  protected readonly oddsBandOptions = ODDS_BAND_OPTIONS;

  protected readonly leagueFilter = signal<string>('');
  protected readonly minEv = signal<number>(0.02);
  protected readonly marketFilter = signal<string>('');
  protected readonly requireModel = signal<boolean>(false);
  protected readonly confidenceFilter = signal<ValueBetConfidence | ''>('');
  protected readonly oddsBand = signal<string>('average');

  protected readonly markets = computed<string[]>(() => {
    const set = new Set<string>();
    for (const b of this.bets()) set.add(b.market_key);
    return Array.from(set).sort();
  });

  protected readonly filtered = computed<ValueBet[]>(() => {
    const m = this.marketFilter();
    const c = this.confidenceFilter();
    return this.bets().filter((b) => {
      if (m && b.market_key !== m) return false;
      if (c && b.confidence !== c) return false;
      const odds = Number(b.offered_odds);
      if (this.oddsBand() === 'average' && (odds < 1.7 || odds > 2.3)) return false;
      if (this.oddsBand() === 'low' && (odds < 1.3 || odds > 1.8)) return false;
      if (this.oddsBand() === 'high' && odds <= 2.3) return false;
      return true;
    });
  });

  protected readonly counts = computed(() => {
    const result = { high: 0, medium: 0, low: 0, unknown: 0 } as Record<ValueBetConfidence, number>;
    for (const b of this.bets()) result[b.confidence] = (result[b.confidence] ?? 0) + 1;
    return result;
  });

  constructor() {
    effect(() => {
      this.load();
    });
  }

  private load(): void {
    const league = this.leagueFilter();
    const minEv = this.minEv();
    const requireModelAgreement = this.requireModel();
    this.loading.set(true);
    this.error.set(null);
    this.api
      .listValueBets({
        minEv,
        requireModelAgreement,
        top: 200,
        league: league || undefined,
      })
      .subscribe({
        next: (data) => {
          this.bets.set(data);
          this.loading.set(false);
        },
        error: (err) => {
          this.error.set(this.formatError(err));
          this.loading.set(false);
        },
      });
  }

  protected onLeagueChange(value: string): void {
    this.leagueFilter.set(value);
  }

  protected onEvChange(value: string): void {
    this.minEv.set(Number(value));
  }

  protected onMarketChange(value: string): void {
    this.marketFilter.set(value);
  }

  protected onRequireModelChange(checked: boolean): void {
    this.requireModel.set(checked);
  }

  protected onConfidenceChange(value: string): void {
    this.confidenceFilter.set(value as ValueBetConfidence | '');
  }

  protected onOddsBandChange(value: string): void {
    this.oddsBand.set(value);
  }

  protected outcome(b: ValueBet): string {
    return outcomeLabel(b.market_key, b.outcome_name, b.outcome_point);
  }

  protected fmtOdds = fmtOdds;
  protected fmtPct = fmtPct;
  protected fmtAbsPct = fmtAbsPct;
  protected fmtDateShort = fmtDateShort;
  protected evColorClass = evColorClass;
  protected confidenceClass = confidenceClass;
  protected confidenceLabel = confidenceLabel;

  private formatError(err: unknown): string {
    if (err instanceof Error) return err.message;
    return 'Error cargando los datos.';
  }
}
