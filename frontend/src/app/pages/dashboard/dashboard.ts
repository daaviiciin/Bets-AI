import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

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
  toNumber,
} from '../../core/format';
import { DailyRefreshSummary, RefreshSummary, ValueBet } from '../../core/models';

interface Kpi {
  label: string;
  value: string;
  hint?: string;
}

@Component({
  selector: 'app-dashboard',
  imports: [RouterLink],
  templateUrl: './dashboard.html',
})
export class Dashboard {
  private readonly api = inject(ApiService);

  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly bets = signal<ValueBet[]>([]);

  protected readonly refreshing = signal(false);
  protected readonly lastRefresh = signal<RefreshSummary | null>(null);

  protected readonly refreshingDaily = signal(false);
  protected readonly lastDailyRefresh = signal<DailyRefreshSummary | null>(null);

  protected readonly kpis = computed<Kpi[]>(() => {
    const all = this.bets();
    if (all.length === 0) {
      return [
        { label: 'Value bets', value: '0' },
        { label: 'Confianza alta', value: '0', hint: 'mercado y modelo coinciden' },
        { label: 'EV máximo', value: '—' },
        { label: 'Partidos con valor', value: '—' },
      ];
    }
    const evs = all.map((b) => toNumber(b.ev));
    const maxEv = Math.max(...evs);
    const matches = new Set(all.map((b) => b.match_id)).size;
    const highConf = all.filter((b) => b.confidence === 'high').length;
    return [
      { label: 'Value bets detectadas', value: String(all.length), hint: 'EV ≥ 2 %' },
      { label: 'Confianza alta', value: String(highConf), hint: 'mercado y modelo coinciden' },
      { label: 'EV máximo', value: `+${(maxEv * 100).toFixed(2)} %` },
      { label: 'Partidos con valor', value: String(matches) },
    ];
  });

  /** En el dashboard priorizamos las picks que el modelo respalda. */
  protected readonly topBets = computed(() => {
    const all = this.bets();
    const rank: Record<string, number> = { high: 0, medium: 1, low: 2, unknown: 3 };
    return [...all]
      .sort((a, b) => {
        const da = rank[a.confidence] ?? 4;
        const db = rank[b.confidence] ?? 4;
        if (da !== db) return da - db;
        return toNumber(b.ev) - toNumber(a.ev);
      })
      .slice(0, 10);
  });

  constructor() {
    this.loadBets();
  }

  private loadBets(): void {
    this.loading.set(true);
    this.api.listValueBets({ minEv: 0.02, top: 100 }).subscribe({
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

  protected reload(): void {
    this.error.set(null);
    this.loadBets();
  }

  protected refreshAll(): void {
    if (this.refreshing()) return;
    const ok = window.confirm(
      'Esto consumirá ~16 créditos de The Odds API y reemplazará todas las cuotas almacenadas. ¿Continuar?',
    );
    if (!ok) return;

    this.refreshing.set(true);
    this.error.set(null);
    this.api.triggerRefresh().subscribe({
      next: (summary) => {
        this.lastRefresh.set(summary);
        this.refreshing.set(false);
        this.loadBets();
      },
      error: (err) => {
        this.error.set(this.formatError(err));
        this.refreshing.set(false);
      },
    });
  }

  /** Pipeline diario (gratis): resultados + stats post-partido vía Flashscore. */
  protected refreshDaily(): void {
    if (this.refreshingDaily()) return;
    this.refreshingDaily.set(true);
    this.error.set(null);
    this.api.refreshDaily({ statsDaysBack: 7 }).subscribe({
      next: (summary) => {
        this.lastDailyRefresh.set(summary);
        this.refreshingDaily.set(false);
      },
      error: (err) => {
        this.error.set(this.formatError(err));
        this.refreshingDaily.set(false);
      },
    });
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
    if (typeof err === 'object' && err && 'message' in err) {
      return String((err as { message: unknown }).message);
    }
    return 'Error desconocido cargando los datos.';
  }
}
