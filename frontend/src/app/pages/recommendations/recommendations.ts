import { Component, computed, inject, signal } from '@angular/core';

import { ApiService } from '../../core/api.service';
import {
  evColorClass,
  fmtAbsPct,
  fmtDateShort,
  fmtEur,
  fmtOdds,
  fmtPct,
  outcomeLabel,
  toNumber,
} from '../../core/format';
import { ConfidenceLevel, RecommendationLabel, RecommendationSet, UserProfile, ValueBet } from '../../core/models';

@Component({
  selector: 'app-recommendations',
  imports: [],
  templateUrl: './recommendations.html',
})
export class Recommendations {
  private readonly api = inject(ApiService);

  protected readonly profiles = signal<UserProfile[]>([]);
  protected readonly selected = signal<string | null>(null);
  protected readonly recs = signal<RecommendationSet | null>(null);
  protected readonly loadingProfiles = signal(true);
  protected readonly loadingRecs = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly strict = signal(true);
  protected readonly averageMode = signal(true);

  protected readonly currentProfile = computed<UserProfile | null>(() => {
    const name = this.selected();
    if (!name) return null;
    return this.profiles().find((p) => p.name === name) ?? null;
  });

  protected readonly totalStake = computed<number>(() => {
    return this.visibleSingles().reduce((sum, s) => sum + toNumber(s.stake), 0);
  });

  protected readonly totalPotential = computed<number>(() => {
    return this.visibleSingles().reduce((sum, s) => sum + toNumber(s.potential_profit), 0);
  });

  protected readonly visibleSingles = computed(() => {
    const r = this.recs();
    if (!r) return [];
    if (!this.averageMode()) return r.singles;
    // Modo usuario promedio: prioriza cuotas alrededor de 2.00
    // y elimina picks extremos difíciles de entender para la mayoría.
    return r.singles.filter((s) => {
      const o = toNumber(s.value_bet.offered_odds);
      return o >= 1.7 && o <= 2.3;
    });
  });

  constructor() {
    this.loadProfiles();
  }

  private loadProfiles(): void {
    this.api.listProfiles().subscribe({
      next: (data) => {
        this.profiles.set(data);
        this.loadingProfiles.set(false);
        // Priorizamos el perfil "Inversor" (cuota ~2) para que el flujo
        // inicial sea más natural para usuario promedio.
        const def =
          data.find((p) => p.name.toLowerCase() === 'inversor') ??
          data.find((p) => p.is_default) ??
          data[0];
        if (def) this.selectProfile(def.name);
      },
      error: (err) => {
        this.error.set(this.formatError(err));
        this.loadingProfiles.set(false);
      },
    });
  }

  protected selectProfile(name: string): void {
    this.selected.set(name);
    this.fetchRecommendations(name);
  }

  protected toggleStrict(): void {
    this.strict.update((v) => !v);
    const name = this.selected();
    if (name) this.fetchRecommendations(name);
  }

  protected toggleAverageMode(): void {
    this.averageMode.update((v) => !v);
  }

  private fetchRecommendations(name: string): void {
    this.recs.set(null);
    this.error.set(null);
    this.loadingRecs.set(true);
    this.api
      .getRecommendations(name, { maxSingles: 30, maxParlays: 5, strict: this.strict() })
      .subscribe({
        next: (data) => {
          this.recs.set(data);
          this.loadingRecs.set(false);
        },
        error: (err) => {
          this.error.set(this.formatError(err));
          this.loadingRecs.set(false);
        },
      });
  }

  protected outcome(b: ValueBet): string {
    return outcomeLabel(b.market_key, b.outcome_name, b.outcome_point);
  }

  /** Color del borde + acento de cada tarjeta según el sello de calidad. */
  protected confidenceCardClass(level: ConfidenceLevel): string {
    switch (level) {
      case 'alta':
        return 'border-emerald-400/40 bg-emerald-500/5';
      case 'media':
        return 'border-amber-400/40 bg-amber-500/5';
      default:
        return 'border-rose-400/40 bg-rose-500/5';
    }
  }

  protected confidenceBadgeClass(level: ConfidenceLevel): string {
    switch (level) {
      case 'alta':
        return 'bg-emerald-500/20 text-emerald-300 border-emerald-400/40';
      case 'media':
        return 'bg-amber-500/20 text-amber-200 border-amber-400/40';
      default:
        return 'bg-rose-500/20 text-rose-200 border-rose-400/40';
    }
  }

  protected labelIcon(label: RecommendationLabel): string {
    switch (label) {
      case 'Apuesta segura':
        return '✓';
      case 'Equilibrada':
        return '~';
      default:
        return '!';
    }
  }

  protected riskColor(profile: UserProfile): string {
    switch (profile.risk_profile) {
      case 'conservative':
        return 'from-emerald-500/20 to-emerald-500/5 border-emerald-400/30 text-emerald-300';
      case 'balanced':
        return 'from-cyan-500/20 to-cyan-500/5 border-cyan-400/30 text-cyan-300';
      case 'aggressive':
        return 'from-amber-500/20 to-amber-500/5 border-amber-400/30 text-amber-300';
      case 'lottery':
        return 'from-fuchsia-500/20 to-fuchsia-500/5 border-fuchsia-400/30 text-fuchsia-300';
      default:
        return 'from-slate-500/20 to-slate-500/5 border-slate-400/30 text-slate-300';
    }
  }

  protected fmtOdds = fmtOdds;
  protected fmtPct = fmtPct;
  protected fmtAbsPct = fmtAbsPct;
  protected fmtEur = fmtEur;
  protected fmtDateShort = fmtDateShort;
  protected evColorClass = evColorClass;
  protected toNumber = toNumber;

  private formatError(err: unknown): string {
    if (err instanceof Error) return err.message;
    if (typeof err === 'object' && err && 'message' in err) {
      return String((err as { message: unknown }).message);
    }
    return 'Error desconocido cargando los datos.';
  }
}
