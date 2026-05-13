import { Component, computed, inject, signal } from '@angular/core';
import { finalize } from 'rxjs/operators';

import { ApiService } from '../../core/api.service';
import {
  fmtAbsPct,
  fmtDateShort,
  fmtOdds,
  outcomeLabel,
} from '../../core/format';
import type { SavedParlay, SavedParlayLegPersist } from '../../core/models';

@Component({
  selector: 'app-mis-apuestas',
  imports: [],
  templateUrl: './mis-apuestas.html',
})
export class MisApuestas {
  private readonly api = inject(ApiService);

  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly parlays = signal<SavedParlay[]>([]);

  protected readonly summary = computed(() => {
    const rows = this.parlays();
    const settled = rows.filter((r) => r.overall_result === 'hit' || r.overall_result === 'miss');
    const hits = settled.filter((r) => r.overall_result === 'hit').length;
    const rate =
      settled.length > 0 ? Math.round((hits / settled.length) * 1000) / 10 : null;
    return {
      total: rows.length,
      pending: rows.filter((r) => r.overall_result === 'pending').length,
      hits,
      misses: settled.length - hits,
      needsReview: rows.filter((r) => r.overall_result === 'needs_review').length,
      ratePct: rate,
    };
  });

  constructor() {
    this.reload();
  }

  protected reload(): void {
    this.busy.set(true);
    this.error.set(null);
    this.api
      .listMyParlays()
      .pipe(finalize(() => this.busy.set(false)))
      .subscribe({
        next: (rows) => this.parlays.set(rows),
        error: (err) =>
          this.error.set(err?.error?.detail ?? err.message ?? 'No se pudieron cargar las combinadas'),
      });
  }

  protected refreshGrades(): void {
    this.error.set(null);
    this.api.refreshMyParlaysGrading().subscribe({
      next: () => this.reload(),
      error: (err) =>
        this.error.set(err?.error?.detail ?? err.message ?? 'Error al recalcular marcadores'),
    });
  }

  protected remove(id: number): void {
    if (this.busy()) return;
    this.api.deleteMyParlay(id).subscribe(() => this.reload());
  }

  protected legLabel(leg: SavedParlayLegPersist): string {
    return outcomeLabel(leg.market_key, leg.outcome_name, leg.outcome_point);
  }

  protected gradedLegs(p: SavedParlay): SavedParlayLegPersist[] {
    return p.grading_snapshot?.legs?.length ? p.grading_snapshot.legs : p.legs_snapshot;
  }

  protected legResultClass(r: string | undefined): string {
    switch (r) {
      case 'hit':
        return 'text-brand-cyan';
      case 'miss':
        return 'text-rose-300';
      case 'needs_review':
        return 'text-amber-200';
      default:
        return 'text-brand-muted';
    }
  }

  protected overallLabel(o: string): string {
    switch (o) {
      case 'hit':
        return 'Combinada ganada (todas las patas)';
      case 'miss':
        return 'Combinada perdida';
      case 'needs_review':
        return 'Revisa alguna pata con el marcador';
      default:
        return 'Combinada pendiente';
    }
  }

  protected mpAtSave(p: SavedParlay): string {
    return p.model_joint_probability != null
      ? fmtAbsPct(p.model_joint_probability, 2)
      : '—';
  }

  protected fmtDateShort = fmtDateShort;
  protected fmtOdds = fmtOdds;
  protected fmtAbsPct = fmtAbsPct;
}
