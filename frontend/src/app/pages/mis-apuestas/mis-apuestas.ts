import { Component, computed, inject, signal } from '@angular/core';
import { finalize, switchMap } from 'rxjs/operators';

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

  /** Solo GET /me/parlays (recarga inicial o manual). */
  protected readonly listBusy = signal(false);
  /** POST refresh-grades masivo. */
  protected readonly bulkGrading = signal(false);
  /** Recalculando una sola combinada (POST /me/parlays/:id/refresh-grade). */
  protected readonly refreshingId = signal<number | null>(null);
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
    this.listBusy.set(true);
    this.error.set(null);
    this.api
      .listMyParlays()
      .pipe(finalize(() => this.listBusy.set(false)))
      .subscribe({
        next: (rows) => this.parlays.set(rows),
        error: (err) =>
          this.error.set(err?.error?.detail ?? err.message ?? 'No se pudieron cargar las combinadas'),
      });
  }

  protected refreshGrades(): void {
    this.error.set(null);
    this.bulkGrading.set(true);
    this.api
      .refreshMyParlaysGrading()
      .pipe(
        switchMap(() => this.api.listMyParlays()),
        finalize(() => this.bulkGrading.set(false)),
      )
      .subscribe({
        next: (rows) => this.parlays.set(rows),
        error: (err) =>
          this.error.set(err?.error?.detail ?? err.message ?? 'Error al recalcular marcadores'),
      });
  }

  protected refreshOneParlay(id: number): void {
    if (this.bulkGrading() || this.refreshingId() !== null) return;
    this.error.set(null);
    this.refreshingId.set(id);
    this.api
      .refreshMyParlayGrade(id)
      .pipe(
        switchMap(() => this.api.listMyParlays()),
        finalize(() => this.refreshingId.set(null)),
      )
      .subscribe({
        next: (rows) => this.parlays.set(rows),
        error: (err) =>
          this.error.set(
            typeof err?.error?.detail === 'string'
              ? err.error.detail
              : Array.isArray(err?.error?.detail)
                ? err.error.detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join(' ')
                : err.message ?? 'No se pudo comprobar el resultado de la combinada',
          ),
      });
  }

  protected remove(id: number): void {
    if (this.bulkGrading() || this.refreshingId() !== null) return;
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

  protected overallShortLabel(o: string): string {
    switch (o) {
      case 'hit':
        return 'Ganada';
      case 'miss':
        return 'Perdida';
      case 'needs_review':
        return 'A revisar';
      default:
        return 'Pendiente';
    }
  }

  protected overallBadgeClass(o: string): string {
    switch (o) {
      case 'hit':
        return 'border-emerald-400/40 bg-emerald-500/15 text-emerald-200';
      case 'miss':
        return 'border-rose-400/40 bg-rose-500/15 text-rose-200';
      case 'needs_review':
        return 'border-amber-400/40 bg-amber-500/15 text-amber-200';
      default:
        return 'border-brand-blue/30 bg-brand-bg-deep/50 text-brand-muted';
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
