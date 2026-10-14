import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { forkJoin, of, type Observable } from 'rxjs';
import { catchError, finalize, switchMap } from 'rxjs/operators';

import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import {
  fmtAbsPct,
  fmtDateShort,
  fmtOdds,
  outcomeLabel,
} from '../../core/format';
import type {
  FeaturedDailyPickRow,
  FeaturedPicksMetrics,
  SavedParlayLegPersist,
  UserParlayMetrics,
} from '../../core/models';

@Component({
  selector: 'app-transparencia',
  imports: [RouterLink],
  templateUrl: './transparencia.html',
})
export class Transparencia {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);

  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly featuredMetrics = signal<FeaturedPicksMetrics | null>(null);
  protected readonly today = signal<FeaturedDailyPickRow | null>(null);
  protected readonly history = signal<FeaturedDailyPickRow[]>([]);
  protected readonly myParlays = signal<UserParlayMetrics | null>(null);

  protected readonly fmtDateShort = fmtDateShort;
  protected readonly fmtOdds = fmtOdds;
  protected readonly fmtAbsPct = fmtAbsPct;

  constructor() {
    this.reload();
  }

  private loadTransparency$(): Observable<{
    featured: FeaturedPicksMetrics;
    today: FeaturedDailyPickRow;
    history: FeaturedDailyPickRow[];
    mine: UserParlayMetrics | null;
  }> {
    const mine$ = this.auth.isLoggedIn()
      ? this.api.getMyParlayMetricsSummary().pipe(
          catchError(() => of(null)),
        )
      : of(null);

    return forkJoin({
      featured: this.api.getFeaturedPicksMetrics(),
      today: this.api.getFeaturedPicksToday(),
      history: this.api.getFeaturedPicksHistory(45),
      mine: mine$,
    });
  }

  protected reload(): void {
    this.busy.set(true);
    this.error.set(null);
    this.loadTransparency$()
      .pipe(finalize(() => this.busy.set(false)))
      .subscribe({
        next: ({ featured, today, history, mine }) => {
          this.featuredMetrics.set(featured);
          this.today.set(today);
          this.history.set(history);
          this.myParlays.set(mine);
        },
        error: (err) =>
          this.error.set(
            err?.error?.detail ?? err.message ?? 'No se pudo cargar la transparencia',
          ),
      });
  }

  protected refreshFeaturedGrades(): void {
    this.busy.set(true);
    this.error.set(null);
    this.api
      .refreshFeaturedPicksGrading()
      .pipe(
        catchError((err) => {
          this.error.set(
            typeof err?.error?.detail === 'string'
              ? err.error.detail
              : err.message ?? 'Error al recalificar picks en servidor',
          );
          return of(null);
        }),
        switchMap(() => this.loadTransparency$()),
        finalize(() => this.busy.set(false)),
      )
      .subscribe({
        next: ({ featured, today, history, mine }) => {
          this.error.set(null);
          this.featuredMetrics.set(featured);
          this.today.set(today);
          this.history.set(history);
          this.myParlays.set(mine);
        },
        error: (err) =>
          this.error.set(
            err?.error?.detail ?? err.message ?? 'No se pudo cargar la transparencia',
          ),
      });
  }

  protected legLabel(leg: SavedParlayLegPersist): string {
    return outcomeLabel(leg.market_key, leg.outcome_name, leg.outcome_point);
  }

  protected legResultClass(r: string | undefined): string {
    switch (r) {
      case 'hit':
        return 'text-emerald-300';
      case 'miss':
        return 'text-rose-300';
      case 'needs_review':
        return 'text-amber-200';
      default:
        return 'text-brand-muted';
    }
  }

  protected legStatusText(leg: SavedParlayLegPersist): string {
    const r = leg.result;
    if (r === 'hit') return 'Acierto';
    if (r === 'miss') return 'Fallo';
    if (r === 'needs_review') return 'Revisar';
    return 'Pendiente';
  }

  protected isLoggedIn(): boolean {
    return this.auth.isLoggedIn();
  }
}
