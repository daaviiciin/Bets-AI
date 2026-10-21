import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { finalize, switchMap, tap } from 'rxjs/operators';

import { ApiService } from '../../core/api.service';
import { BetslipService } from '../../core/betslip.service';
import {
  fmtCuota,
  fmtDateShort,
  fmtEuros,
  outcomeLabel,
  parseImporte,
  toNumber,
} from '../../core/format';
import type {
  ResultadoManual,
  SavedParlay,
  SavedParlayLegPersist,
  UserParlayMetrics,
} from '../../core/models';
import { Icon } from '../../shared/icon';
import { TvCifra, TvEntra } from '../../shared/tv-motion';

type Filtro = 'todas' | 'pending' | 'hit' | 'miss' | 'needs_review';

const FILTROS: { id: Filtro; label: string }[] = [
  { id: 'todas', label: 'Todas' },
  { id: 'pending', label: 'Pendientes' },
  { id: 'hit', label: 'Ganadas' },
  { id: 'miss', label: 'Perdidas' },
  { id: 'needs_review', label: 'A revisar' },
];

const ESTADO: Record<string, string> = {
  hit: 'Ganada',
  miss: 'Perdida',
  void: 'Nula',
  needs_review: 'A revisar',
  pending: 'Pendiente',
};

/**
 * Mis apuestas: el registro de lo que guardas desde el boletín (simples y combinadas), con su
 * importe. Se liquidan solas con los marcadores de Flashscore; si una queda "a revisar" o no te
 * cuadra, la corriges a mano (ganada, perdida o nula) y puedes devolverla al automático.
 */
@Component({
  selector: 'app-mis-apuestas',
  imports: [RouterLink, Icon, TvCifra, TvEntra],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './mis-apuestas.html',
  styles: `
    /* Estado de la apuesta: se distingue por forma (relleno, contorno, discontinuo), no solo por color. */
    .estado {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      padding: 0.2rem 0.55rem;
      font-family: var(--font-display);
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.02em;
      text-transform: uppercase;
      color: var(--color-tv-muted);
      border: 1px solid var(--color-tv-line);
    }

    .estado[data-estado='hit'] {
      color: var(--color-tv-night);
      background: var(--color-tv-ink);
      border-color: var(--color-tv-ink);
    }

    .estado[data-estado='miss'] {
      color: var(--color-tv-ink);
      border-color: var(--color-tv-muted);
    }

    .estado[data-estado='needs_review'] {
      color: var(--color-tv-doubt);
      border-style: dashed;
      border-color: var(--color-tv-doubt);
    }

    .estado[data-estado='void'] {
      text-decoration: line-through;
    }

    .pierna {
      display: inline-flex;
      color: var(--color-tv-muted);
    }

    .pierna[data-estado='hit'] {
      color: var(--color-tv-ink);
    }

    .pierna[data-estado='needs_review'] {
      color: var(--color-tv-doubt);
    }
  `,
})
export class MisApuestas {
  private readonly api = inject(ApiService);
  protected readonly slip = inject(BetslipService);

  protected readonly loading = signal(true);
  protected readonly checking = signal(false);
  /** Apuesta con una petición en curso (corregir, importe, comprobar o borrar). */
  protected readonly busyId = signal<number | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly bets = signal<SavedParlay[]>([]);
  protected readonly metrics = signal<UserParlayMetrics | null>(null);

  protected readonly filtro = signal<Filtro>('todas');
  /** Apuesta con el panel de corrección abierto. */
  protected readonly editingId = signal<number | null>(null);
  protected readonly stakeDraft = signal('');
  /** Apuesta pendiente de confirmar el borrado. */
  protected readonly confirmDeleteId = signal<number | null>(null);

  protected readonly filtros = FILTROS;
  protected readonly counts = computed(() => {
    const c: Record<Filtro, number> = { todas: 0, pending: 0, hit: 0, miss: 0, needs_review: 0 };
    for (const b of this.bets()) {
      c.todas++;
      if (b.overall_result in c) c[b.overall_result as Filtro]++;
    }
    return c;
  });
  protected readonly visibles = computed(() => {
    const f = this.filtro();
    return f === 'todas' ? this.bets() : this.bets().filter((b) => b.overall_result === f);
  });

  protected readonly fmtCuota = fmtCuota;
  protected readonly fmtDateShort = fmtDateShort;
  protected readonly fmtEuros = fmtEuros;
  protected readonly fmtPct = (v: number | null) => (v == null ? '—' : `${v.toFixed(1)} %`);
  protected readonly fmtEurosSigno = (v: number | null) => (v == null ? '—' : fmtEuros(v, true));
  protected readonly fmtEurosCifra = (v: number | null) => (v == null ? '—' : fmtEuros(v));

  constructor() {
    this.reload();
  }

  protected reload(): void {
    this.loading.set(true);
    this.error.set(null);
    this.load$()
      .pipe(finalize(() => this.loading.set(false)))
      .subscribe({ error: (err) => this.fail(err, 'No se pudieron cargar tus apuestas.') });
  }

  /** Recalcula todas con los marcadores ya cargados en la BD. */
  protected checkAll(): void {
    if (this.checking()) return;
    this.checking.set(true);
    this.error.set(null);
    this.api
      .refreshMyParlaysGrading()
      .pipe(
        switchMap(() => this.load$()),
        finalize(() => this.checking.set(false)),
      )
      .subscribe({ error: (err) => this.fail(err, 'No se pudieron comprobar los resultados.') });
  }

  protected toggleEdit(b: SavedParlay): void {
    this.confirmDeleteId.set(null);
    if (this.editingId() === b.id) {
      this.editingId.set(null);
      return;
    }
    this.editingId.set(b.id);
    this.stakeDraft.set(b.stake ? b.stake.replace('.', ',') : '');
  }

  protected setResult(b: SavedParlay, result: ResultadoManual | null): void {
    this.update(b.id, { manual_result: result });
  }

  protected stakeInvalid(): boolean {
    return this.stakeDraft().trim() !== '' && !parseImporte(this.stakeDraft());
  }

  protected saveStake(b: SavedParlay): void {
    if (this.stakeInvalid()) return;
    this.update(b.id, { stake: parseImporte(this.stakeDraft()) });
  }

  protected askDelete(id: number): void {
    this.confirmDeleteId.set(this.confirmDeleteId() === id ? null : id);
  }

  protected remove(id: number): void {
    if (this.busyId() !== null) return;
    this.busyId.set(id);
    this.api
      .deleteMyParlay(id)
      .pipe(
        switchMap(() => this.load$()),
        finalize(() => this.busyId.set(null)),
      )
      .subscribe({
        next: () => this.confirmDeleteId.set(null),
        error: (err) => this.fail(err, 'No se pudo borrar la apuesta.'),
      });
  }

  protected legs(b: SavedParlay): SavedParlayLegPersist[] {
    return b.grading_snapshot?.legs?.length ? b.grading_snapshot.legs : b.legs_snapshot;
  }

  protected legLabel(leg: SavedParlayLegPersist): string {
    const sel = outcomeLabel(leg.market_key, leg.outcome_name, leg.outcome_point);
    return leg.player_name ? `${leg.player_name} · ${sel}` : sel;
  }

  protected kind(b: SavedParlay): string {
    const n = b.legs_snapshot.length;
    return n === 1 ? 'Simple' : `Combinada · ${n} selecciones`;
  }

  protected estado(r: string): string {
    return ESTADO[r] ?? 'Pendiente';
  }

  /** Lo que cobra si acierta (pendientes) o lo que ganó o perdió (liquidadas). */
  protected payout(b: SavedParlay): number | null {
    return b.stake ? toNumber(b.stake) * toNumber(b.combined_odds) : null;
  }

  /** Qué dijo la liquidación automática, para enseñarlo cuando el usuario la ha corregido. */
  protected autoResult(b: SavedParlay): string | null {
    return b.manual_result ? (b.grading_snapshot?.auto_result ?? null) : null;
  }

  private update(
    id: number,
    changes: { stake?: string | null; manual_result?: ResultadoManual | null },
  ): void {
    if (this.busyId() !== null) return;
    this.busyId.set(id);
    this.error.set(null);
    this.api
      .updateMyParlay(id, changes)
      .pipe(
        switchMap(() => this.load$()),
        finalize(() => this.busyId.set(null)),
      )
      .subscribe({ error: (err) => this.fail(err, 'No se pudo guardar el cambio.') });
  }

  private load$() {
    return forkJoin({
      bets: this.api.listMyParlays(),
      metrics: this.api.getMyParlayMetricsSummary(),
    }).pipe(
      tap(({ bets, metrics }) => {
        this.bets.set(bets);
        this.metrics.set(metrics);
      }),
    );
  }

  private fail(err: unknown, fallback: string): void {
    const detail = (err as { error?: { detail?: unknown } })?.error?.detail;
    this.error.set(typeof detail === 'string' ? detail : fallback);
  }
}
