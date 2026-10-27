import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';
import { forkJoin, of } from 'rxjs';
import { catchError, finalize, map, switchMap, tap } from 'rxjs/operators';

import { ApiService } from '../../core/api.service';
import { fmtCuota, fmtDayName, fmtEv, fmtProb, fmtTime } from '../../core/format';
import { selectionLabel } from '../../core/markets';
import type { FeaturedDailyPickRow, FeaturedPicksMetrics, PickLeg } from '../../core/models';
import { Icon } from '../../shared/icon';
import { TvCifra, TvEntra } from '../../shared/tv-motion';

/** Cuántos picks liquidados enseña la racha. */
const RACHA = 30;

const ESTADO: Record<string, string> = {
  hit: 'Acierto',
  miss: 'Fallo',
  needs_review: 'A revisar',
  pending: 'Pendiente',
  // Partido terminado, esperando stats: se ve como pendiente.
  pending_stats: 'Pendiente',
  void: 'Nulo',
};

/**
 * Transparencia: los picks que el modelo elige cada día (simples y combinada), congelados antes
 * de los partidos y liquidados después con los marcadores. No se borra ningún fallo: es el
 * historial con el que se mide el modelo.
 */
@Component({
  selector: 'app-transparencia',
  imports: [NgTemplateOutlet, RouterLink, Icon, TvCifra, TvEntra],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './transparencia.html',
  styles: `
    /* Racha: un cuadro por pick liquidado (relleno = acierto, contorno = fallo), como la forma V/E/D. */
    .racha {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(14px, 1fr));
      gap: 4px;
      max-width: 34rem;
    }

    .racha > span {
      aspect-ratio: 1;
      border: 1.5px solid var(--color-tv-muted);
    }

    .racha > span[data-estado='hit'] {
      background: var(--color-tv-ink);
      border-color: var(--color-tv-ink);
    }

    .marca {
      display: inline-flex;
      color: var(--color-tv-muted);
    }

    .marca[data-estado='hit'] {
      color: var(--color-tv-ink);
    }

    .marca[data-estado='needs_review'] {
      color: var(--color-tv-doubt);
    }

    .sin-valor {
      font-weight: 700;
      color: var(--color-tv-doubt);
    }
  `,
})
export class Transparencia {
  private readonly api = inject(ApiService);

  protected readonly loading = signal(true);
  protected readonly checking = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly metrics = signal<FeaturedPicksMetrics | null>(null);
  protected readonly today = signal<FeaturedDailyPickRow | null>(null);
  private readonly rows = signal<FeaturedDailyPickRow[]>([]);

  /** Días anteriores a hoy, más reciente primero (la fila de hoy va en su propia sección). */
  protected readonly history = computed(() => {
    const hoy = this.today()?.pick_date;
    return this.rows().filter((r) => r.pick_date !== hoy);
  });

  /** Últimos picks liquidados, del más antiguo al más reciente (se lee de izquierda a derecha). */
  protected readonly racha = computed(() =>
    this.rows()
      .flatMap((r) => r.legs)
      .filter((l) => l.result === 'hit' || l.result === 'miss')
      .slice(0, RACHA)
      .reverse(),
  );

  protected readonly fmtCuota = fmtCuota;
  protected readonly fmtProb = fmtProb;
  protected readonly fmtEv = fmtEv;
  protected readonly fmtTime = fmtTime;
  protected readonly fmtPct = (v: number | null) => (v == null ? '—' : `${v.toFixed(1)} %`);

  /** Acierto de cada versión del modelo, la más reciente primero. */
  protected readonly porModelo = computed(() =>
    Object.entries(this.metrics()?.por_modelo ?? {})
      .map(([version, m]) => ({ version, ...m }))
      .sort((a, b) => b.version.localeCompare(a.version)),
  );

  /** Nombre legible de una versión del modelo. */
  protected modeloLabel(version: string | null | undefined): string {
    if (!version || version === 'legacy-v1') return 'Poisson antiguo (legacy-v1)';
    if (version === 'v1') return 'Reglas antiguas (v1)';
    return `Modelo v2 (${version})`;
  }
  protected readonly fmtEntero = (v: number | null) => (v == null ? '—' : String(Math.round(v)));

  constructor() {
    this.reload();
  }

  protected reload(): void {
    this.loading.set(true);
    this.error.set(null);
    this.load$()
      .pipe(finalize(() => this.loading.set(false)))
      .subscribe({ error: () => this.error.set('No se pudieron cargar los picks del modelo.') });
  }

  /** Vuelve a liquidar todos los picks con los marcadores ya cargados. */
  protected checkAll(): void {
    if (this.checking()) return;
    this.checking.set(true);
    this.error.set(null);
    this.api
      .refreshFeaturedPicksGrading()
      .pipe(
        catchError((err) => {
          this.error.set(
            err?.status === 429
              ? 'Acabas de comprobarlos. Espera un minuto y vuelve a probar.'
              : 'No se pudieron comprobar los resultados.',
          );
          return of(null);
        }),
        switchMap(() => this.load$()),
        finalize(() => this.checking.set(false)),
      )
      .subscribe({ error: () => this.error.set('No se pudieron cargar los picks del modelo.') });
  }

  protected dayName(row: FeaturedDailyPickRow): string {
    // Mediodía local: "2026-10-20" a secas se leería como medianoche UTC.
    return fmtDayName(`${row.pick_date}T12:00:00`);
  }

  protected dayScore(row: FeaturedDailyPickRow): string {
    const hits = row.legs.filter((l) => l.result === 'hit').length;
    const settled = row.legs.filter((l) => l.result === 'hit' || l.result === 'miss').length;
    return settled ? `${hits} de ${settled}` : 'Sin liquidar';
  }

  protected legLabel(leg: PickLeg): string {
    return selectionLabel(leg, leg.home_team, leg.away_team);
  }

  protected estado(r: string | undefined): string {
    return ESTADO[r ?? 'pending'] ?? 'Pendiente';
  }

  private load$() {
    return forkJoin({
      metrics: this.api.getFeaturedPicksMetrics(),
      // Aquí no se generan: si aún no hay picks de hoy, la portada los genera al abrirla.
      today: this.api.getFeaturedPicksToday().pipe(map((t) => t.pick)),
      history: this.api.getFeaturedPicksHistory(60),
    }).pipe(
      tap(({ metrics, today, history }) => {
        this.metrics.set(metrics);
        this.today.set(today);
        this.rows.set(history);
      }),
    );
  }
}
