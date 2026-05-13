import { Component, inject, signal } from '@angular/core';

import { ApiService } from '../../core/api.service';
import { fmtPct, toNumber } from '../../core/format';
import { BetLegIn, CustomBetAssessmentResponse } from '../../core/models';

interface LegDraft {
  label: string;
  decimal_odds: string;
  market: string;
  selection: string;
  line: string;
}

function blankLeg(): LegDraft {
  return { label: '', decimal_odds: '2.00', market: 'h2h', selection: 'home', line: '' };
}

@Component({
  selector: 'app-analisis',
  imports: [],
  templateUrl: './analisis.html',
})
export class Analisis {
  private readonly api = inject(ApiService);

  protected readonly matchIdStr = signal('');
  protected readonly legs = signal<LegDraft[]>([blankLeg()]);
  protected readonly assumeIndependent = signal(true);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly result = signal<CustomBetAssessmentResponse | null>(null);

  protected readonly fmtPct = fmtPct;

  protected addLeg(): void {
    this.legs.update((list) => [...list, blankLeg()]);
  }

  protected removeLeg(index: number): void {
    this.legs.update((list) => (list.length <= 1 ? list : list.filter((_, i) => i !== index)));
  }

  protected patchLeg(index: number, patch: Partial<LegDraft>): void {
    this.legs.update((list) => {
      const next = [...list];
      next[index] = { ...next[index], ...patch };
      return next;
    });
  }

  protected run(): void {
    this.error.set(null);
    this.result.set(null);

    const midRaw = this.matchIdStr().trim();
    let match_id: number | null = null;
    if (midRaw !== '') {
      const n = Number(midRaw);
      if (!Number.isInteger(n) || n < 1) {
        this.error.set('ID de partido inválido (debe ser entero positivo o vacío).');
        return;
      }
      match_id = n;
    }

    const legsIn: BetLegIn[] = this.legs().map((lg) => {
      const lbl = lg.label.trim() || 'Selección manual';
      const pt = lg.line.trim();
      const line = pt === '' ? null : pt.replace(',', '.');
      return {
        label: lbl,
        decimal_odds: lg.decimal_odds.trim().replace(',', '.'),
        market: lg.market.trim() === '' ? null : lg.market.trim(),
        selection: lg.selection.trim() === '' ? null : lg.selection.trim(),
        outcome_point: line,
      };
    });

    for (const leg of legsIn) {
      const o = toNumber(leg.decimal_odds);
      if (!(o > 1)) {
        this.error.set('Todas las cuotas deben ser mayores que 1.');
        return;
      }
    }

    this.loading.set(true);
    this.api
      .postCustomBetAssessment({
        match_id,
        legs: legsIn,
        assume_independent: this.assumeIndependent(),
      })
      .subscribe({
        next: (res) => {
          this.result.set(res);
          this.loading.set(false);
        },
        error: (err) => {
          this.error.set(this.formatError(err));
          this.loading.set(false);
        },
      });
  }

  private formatError(err: unknown): string {
    if (err && typeof err === 'object' && 'error' in err) {
      const e = (err as { error?: unknown }).error;
      if (e && typeof e === 'object' && 'detail' in e) {
        const d = (e as { detail?: unknown }).detail;
        return typeof d === 'string' ? d : JSON.stringify(d);
      }
    }
    return 'No se pudo contactar con la API. ¿Está arrancado el backend?';
  }
}
