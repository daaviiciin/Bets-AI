import { HttpErrorResponse } from '@angular/common/http';
import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { of, switchMap } from 'rxjs';

import { ApiService } from '../../core/api.service';
import { BetslipService, type SlipLeg, legKey } from '../../core/betslip.service';
import { fmtCuota, fmtEv, fmtProb, fmtTime } from '../../core/format';
import { marketLabel, selectionLabel } from '../../core/markets';
import type { FeaturedDailyPickRow, FeaturedToday, PickLeg } from '../../core/models';
import { TvEntra } from '../../shared/tv-motion';

type Estado = 'cargando' | 'preparando' | 'listo' | 'sin_partidos' | 'error';

/**
 * Picks del día del modelo: simples de cuota 1,30–1,70 con la mayor probabilidad y una
 * combinada de cuota total 3–6 (de varios partidos o, si no hay bastantes, con selecciones de un
 * mismo partido, que en Winamax van por MyMatch). La primera vez que se abre la app cada día se generan (si el
 * refresh programado no lo hizo antes) y quedan congelados; su acierto se mide en Transparencia.
 * Cada pick se lleva al boletín con un clic; la combinada, entera.
 */
@Component({
  selector: 'app-picks-del-dia',
  imports: [RouterLink, TvEntra],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block' },
  styles: `
    .sin-valor {
      font-weight: 700;
      color: var(--color-tv-doubt);
    }

    .linea-on .sin-valor,
    .linea-on .ev-valor {
      color: inherit;
    }
  `,
  template: `
    <section aria-labelledby="picks-titulo">
      <div class="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h2 id="picks-titulo" class="rotulo tv-dia pb-2 text-3xl">Picks del día</h2>
        <a
          routerLink="/transparencia"
          class="text-sm text-tv-muted underline-offset-4 hover:text-tv-ink hover:underline"
        >
          Acierto del modelo
        </a>
      </div>

      @switch (estado()) {
        @case ('cargando') {
          <div class="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3" aria-busy="true" aria-label="Cargando los picks">
            @for (i of [0, 1, 2]; track i) {
              <div class="tv-carga h-[76px]" [style.--i]="i"></div>
            }
          </div>
        }
        @case ('preparando') {
          <p class="mt-3 text-sm text-tv-muted" aria-live="polite">
            Preparando los picks de hoy: si las cuotas tienen más de 2 horas, se actualizan antes
            (hasta un minuto)…
          </p>
          <div class="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3" aria-hidden="true">
            @for (i of [0, 1, 2]; track i) {
              <div class="tv-carga h-[76px]" [style.--i]="i"></div>
            }
          </div>
        }
        @case ('sin_partidos') {
          <p class="mt-3 text-tv-muted">{{ motivo() }}. Mañana habrá picks nuevos.</p>
        }
        @case ('error') {
          <p class="mt-3 text-sm" role="alert">
            {{ motivo() }}
            <button type="button" (click)="cargar()" class="ml-2 text-tv-go underline underline-offset-4">
              Reintentar
            </button>
          </p>
        }
        @case ('listo') {
          @let p = pick()!;
          <p class="mt-2 text-sm text-tv-muted">
            Cuota de 1,30 a 1,70 con la mayor probabilidad del modelo
            @if (p.generated_at) {
              · congelados a las {{ fmtTime(p.generated_at) }}
            }
          </p>
          @if (p.model_version === 'legacy-v1') {
            <p class="mt-1 text-xs text-tv-muted">
              Modelo provisional (Poisson actual): sus probabilidades aún no están calibradas, así
              que el EV sale inflado. El acierto real es el de Transparencia.
            </p>
          } @else {
            <p class="mt-1 text-xs text-tv-muted">
              Modelo v2: Dixon–Coles con encogimiento (goles y xG), calibrado y mezclado con la
              cuota de Winamax sin margen. El EV es prudente: "sin valor" es lo normal en un mercado
              eficiente.
            </p>
          }

          <div class="mt-4 grid gap-6 xl:grid-cols-[1fr_24rem]">
            <div>
              @if (p.legs.length) {
                <ul class="grid gap-2 sm:grid-cols-2">
                  @for (l of p.legs; track key(l)) {
                    <li tvEntra="pick" [tvPaso]="40">
                      <button
                        #b
                        type="button"
                        (click)="toggle(l, b)"
                        [attr.aria-pressed]="slip.has(key(l))"
                        class="linea tv-barrido h-full"
                        [class.con-valor]="!l.sin_valor"
                        [class.linea-on]="slip.has(key(l))"
                      >
                        <span class="min-w-0">
                          <span class="block truncate text-sm text-tv-muted">
                            {{ l.home_team }} – {{ l.away_team }} · {{ hora(l) }}
                          </span>
                          <span class="block truncate font-semibold">{{ seleccion(l) }}</span>
                          <span class="block text-xs tabular-nums text-tv-muted">
                            P {{ fmtProb(l.model_probability) }} ·
                            @if (l.sin_valor) {
                              <span class="sin-valor">Sin valor</span>
                            } @else {
                              <span class="ev-valor">EV {{ fmtEv(l.model_ev) }}</span>
                            }
                          </span>
                          @if (l.aviso) {
                            <span class="block text-xs text-tv-doubt">{{ l.aviso }}</span>
                          }
                        </span>
                        <span class="font-display text-xl font-bold tabular-nums">
                          {{ fmtCuota(l.offered_odds) }}
                        </span>
                      </button>
                    </li>
                  }
                </ul>
              } @else {
                <p class="text-tv-muted">Hoy ningún partido tiene una cuota entre 1,30 y 1,70.</p>
              }
              @if (p.selection_meta?.motivo_simples; as motivo) {
                @if (p.legs.length) {
                  <p class="mt-2 text-xs text-tv-muted">{{ motivo }}.</p>
                }
              }
            </div>

            <aside class="h-fit border border-tv-line bg-tv-panel p-4" aria-labelledby="combo-titulo">
              @if (p.combo; as c) {
                <div class="flex items-baseline justify-between gap-4">
                  <h3 id="combo-titulo" class="font-display text-base font-bold uppercase tracking-wide">
                    Combinada del día
                  </h3>
                  <span class="font-display text-2xl font-bold tabular-nums">
                    {{ fmtCuota(c.combined_odds) }}
                  </span>
                </div>
                <ul class="mt-3 divide-y divide-tv-line border-y border-tv-line">
                  @for (l of c.legs; track key(l)) {
                    <li class="flex items-center justify-between gap-3 py-2">
                      <span class="min-w-0">
                        <span class="block truncate text-sm font-semibold">{{ seleccion(l) }}</span>
                        <span class="block truncate text-xs text-tv-muted">
                          {{ l.home_team }} – {{ l.away_team }} · {{ hora(l) }}
                        </span>
                      </span>
                      <span class="tabular-nums">{{ fmtCuota(l.offered_odds) }}</span>
                    </li>
                  }
                </ul>
                <p class="mt-2 text-xs tabular-nums text-tv-muted">
                  P conjunta {{ fmtProb(c.joint_probability) }} ·
                  @if (c.sin_valor) {
                    <span class="sin-valor">Sin valor</span>
                  } @else {
                    <span class="ev-valor">EV {{ fmtEv(c.ev) }}</span>
                  }
                </p>
                @if (c.mismo_partido) {
                  <p class="mt-2 border-l-2 border-tv-doubt pl-2 text-xs text-tv-muted">
                    Lleva varias selecciones de un mismo partido: en Winamax se hace con
                    <span class="text-tv-ink">MyMatch</span> y la cuota la pone Winamax (la de
                    arriba es una estimación). Tiene valor si te pagan
                    <span class="text-tv-ink tabular-nums">{{ fmtCuota(c.cuota_minima_valor) }}</span>
                    o más.
                  </p>
                }
                <button
                  #bc
                  type="button"
                  (click)="toggleCombo(bc)"
                  [attr.aria-pressed]="comboEnBoletin()"
                  class="cuota mt-3 !h-11 !w-full !flex-row gap-2 font-display text-[15px] font-bold uppercase tracking-wide"
                  [class.cuota-on]="comboEnBoletin()"
                >
                  {{ comboEnBoletin() ? 'Quitar del boletín' : 'Llevar las ' + c.legs.length + ' al boletín' }}
                </button>
              } @else {
                <h3 id="combo-titulo" class="font-display text-base font-bold uppercase tracking-wide">
                  Combinada del día
                </h3>
                <p class="mt-2 text-sm text-tv-muted">
                  {{ p.selection_meta?.motivo_combinada ?? 'Hoy no hay combinada' }}.
                </p>
              }
            </aside>
          </div>
        }
      }
    </section>
  `,
})
export class PicksDelDia {
  private readonly api = inject(ApiService);
  protected readonly slip = inject(BetslipService);

  protected readonly estado = signal<Estado>('cargando');
  protected readonly pick = signal<FeaturedDailyPickRow | null>(null);
  protected readonly motivo = signal<string | null>(null);

  protected readonly comboEnBoletin = computed(() => {
    const c = this.pick()?.combo;
    return !!c && c.legs.every((l) => this.slip.has(this.key(l)));
  });

  protected readonly fmtCuota = fmtCuota;
  protected readonly fmtProb = fmtProb;
  protected readonly fmtEv = fmtEv;
  protected readonly fmtTime = fmtTime;

  constructor() {
    this.cargar();
  }

  /** Si los picks de hoy aún no existen, se piden al backend (que los genera y congela). */
  protected cargar(): void {
    this.estado.set('cargando');
    this.api
      .getFeaturedPicksToday()
      .pipe(
        switchMap((hoy) => {
          if (hoy.estado !== 'pendiente') return of(hoy);
          this.estado.set('preparando');
          return this.api.ensureFeaturedPicksToday();
        }),
      )
      .subscribe({
        next: (hoy) => this.aplicar(hoy),
        error: (err: unknown) => {
          this.estado.set('error');
          this.motivo.set(
            err instanceof HttpErrorResponse && err.status === 429
              ? 'Los picks de hoy se están preparando en otra pestaña.'
              : 'No se pudieron cargar los picks de hoy.',
          );
        },
      });
  }

  protected key(l: PickLeg): string {
    return legKey(l);
  }

  protected seleccion(l: PickLeg): string {
    return selectionLabel(l, l.home_team, l.away_team);
  }

  protected hora(l: PickLeg): string {
    return l.commence_time ? fmtTime(l.commence_time) : '';
  }

  protected toggle(l: PickLeg, from: HTMLElement): void {
    this.slip.toggle(this.slipLeg(l), from);
  }

  /** La combinada entera: si ya está toda en el boletín se quita; si no, se añade lo que falte. */
  protected toggleCombo(from: HTMLElement): void {
    const c = this.pick()?.combo;
    if (!c) return;
    if (this.comboEnBoletin()) {
      for (const l of c.legs) this.slip.remove(this.key(l));
      return;
    }
    c.legs
      .filter((l) => !this.slip.has(this.key(l)))
      .forEach((l, i) => this.slip.toggle(this.slipLeg(l), i === 0 ? from : undefined));
  }

  private aplicar(hoy: FeaturedToday): void {
    if (hoy.estado === 'listo' && hoy.pick) {
      this.pick.set(hoy.pick);
      this.estado.set('listo');
    } else if (hoy.estado === 'sin_partidos') {
      this.motivo.set(hoy.motivo ?? 'Hoy no quedan partidos por empezar');
      this.estado.set('sin_partidos');
    } else {
      this.motivo.set('Los picks de hoy aún no están listos.');
      this.estado.set('error');
    }
  }

  private slipLeg(l: PickLeg): SlipLeg {
    return {
      key: this.key(l),
      matchId: l.match_id,
      home: l.home_team,
      away: l.away_team,
      league: l.league,
      kickoff: l.commence_time ?? '',
      marketKey: l.market_key,
      marketLabel: marketLabel(l.market_key),
      selection: this.seleccion(l),
      outcomeName: l.outcome_name,
      outcomePoint: l.outcome_point,
      playerName: l.player_name,
      odds: l.offered_odds,
      modelProbability: l.model_probability,
      modelEv: l.model_ev ?? null,
    };
  }
}
