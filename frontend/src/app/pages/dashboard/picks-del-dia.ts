import { HttpErrorResponse } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
  untracked,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { of, switchMap } from 'rxjs';

import { ApiService } from '../../core/api.service';
import { BetslipService, type SlipLeg, legKey } from '../../core/betslip.service';
import { estadoLabel, resumenPatas, tieneResultado } from '../../core/combinada-partido';
import { fmtCuota, fmtEv, fmtProb, fmtProximosPicks, fmtTime } from '../../core/format';
import { marketLabel, selectionLabel } from '../../core/markets';
import type { FeaturedDailyPickRow, FeaturedToday, PickLeg } from '../../core/models';
import { RefrescoService } from '../../core/refresco.service';
import { TvNiebla } from '../../shared/fx/niebla';
import { Icon } from '../../shared/icon';

type Estado = 'cargando' | 'preparando' | 'listo' | 'sin_partidos' | 'error';

/**
 * Picks del día del modelo (reglas v5): simples de cuota 1,30–2,00 con la mayor probabilidad
 * (como mucho dos del mismo tipo de mercado) y una combinada de cuota total 4–6, de 3 a 5 patas
 * y como mucho dos del mismo tipo (de varios partidos o, si no hay bastantes, con selecciones de
 * un mismo partido, que en Winamax van por MyMatch). Las simples y las patas pueden ser props de
 * jugador (tiros a puerta, goleador), marcados como experimentales. La primera vez que se abre
 * la app cada día se generan (si el refresh programado no lo hizo antes) y quedan congelados; su
 * acierto se mide en Transparencia. Aparte va la combinada del partido (B14): 2–3 selecciones de
 * un mismo partido con cuota estimada de 2,50 a 5,00, que también puede llevar props.
 * Cada pick se lleva al boletín con un clic; las combinadas, enteras.
 */
@Component({
  selector: 'app-picks-del-dia',
  imports: [RouterLink, TvNiebla, Icon],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block' },
  styles: `
    .sin-valor {
      font-weight: 700;
      color: var(--color-tv-doubt);
    }

    .linea-on .sin-valor,
    .linea-on .ev-valor,
    .linea-on .experimental {
      color: inherit;
    }

    .linea-on .experimental {
      border-color: currentColor;
    }

    /* Props de jugador (simples y patas): aún sin historial de picks reales. */
    .experimental {
      padding: 0 4px;
      font-size: 10px;
      font-weight: 700;
      line-height: 16px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--color-tv-doubt);
      border: 1px solid color-mix(in oklab, var(--color-tv-doubt) 45%, transparent);
    }

    .marca {
      display: inline-flex;
      color: var(--color-tv-muted);
    }

    .marca[data-estado='hit'],
    .estado[data-estado='hit'] {
      color: var(--color-tv-ink);
    }

    .marca[data-estado='needs_review'],
    .estado[data-estado='needs_review'] {
      color: var(--color-tv-doubt);
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

      @switch (vista()) {
        @case ('cargando') {
          <div class="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3" aria-busy="true" aria-label="Cargando los picks">
            @for (i of [0, 1, 2]; track i) {
              <div class="tv-carga h-[76px]" [style.--i]="i"></div>
            }
          </div>
        }
        @case ('preparando') {
          <p class="mt-3 text-sm text-tv-muted" aria-live="polite">
            @if (refresco.enCurso()) {
              Actualizando los datos: los picks de hoy salen al terminar (unos minutos)…
            } @else {
              Preparando los picks de hoy: si las cuotas tienen más de 2 horas, se actualizan antes
              (hasta un minuto)…
            }
          </p>
          <div class="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3" aria-hidden="true">
            @for (i of [0, 1, 2]; track i) {
              <div class="tv-carga h-[76px]" [style.--i]="i"></div>
            }
          </div>
        }
        @case ('sin_partidos') {
          <p class="mt-3 text-tv-muted">{{ motivo() }}. {{ proximos() }}.</p>
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
            Cuota de 1,30 a 2,00 con la mayor probabilidad del modelo
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

          <!-- Los picks emergen de la niebla (TvNiebla): cada [data-niebla] se condensa del humo. -->
          <div tvNiebla class="mt-4 grid gap-6 xl:grid-cols-[1fr_24rem]">
            <div>
              @if (p.legs.length) {
                <ul class="grid gap-2 sm:grid-cols-2">
                  @for (l of p.legs; track key(l)) {
                    <li data-niebla>
                      <button
                        #b
                        type="button"
                        (click)="toggle(l, b)"
                        [attr.aria-pressed]="slip.has(key(l))"
                        class="linea h-full"
                        [class.con-valor]="!l.sin_valor"
                        [class.linea-on]="slip.has(key(l))"
                      >
                        <span class="min-w-0">
                          <span class="block truncate text-sm text-tv-muted">
                            {{ l.home_team }} – {{ l.away_team }} · {{ hora(l) }}
                          </span>
                          <span class="block truncate font-semibold">{{ seleccion(l) }}</span>
                          <span class="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs tabular-nums text-tv-muted">
                            <span>
                              P {{ fmtProb(l.model_probability) }} ·
                              @if (l.sin_valor) {
                                <span class="sin-valor">Sin valor</span>
                              } @else {
                                <span class="ev-valor">EV {{ fmtEv(l.model_ev) }}</span>
                              }
                            </span>
                            @if (l.experimental) {
                              <span class="experimental">Experimental</span>
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
                <p class="text-tv-muted">Hoy ningún partido tiene una cuota entre 1,30 y 2,00.</p>
              }
              @if (p.selection_meta?.motivo_simples; as motivo) {
                @if (p.legs.length) {
                  <p class="mt-2 text-xs text-tv-muted">{{ motivo }}.</p>
                }
              }
            </div>

            <div class="grid h-fit gap-4">
              <aside data-niebla class="h-fit border border-tv-line bg-tv-panel p-4" aria-labelledby="combo-titulo">
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
                          <span class="flex min-w-0 items-center gap-1.5">
                            <span class="truncate text-sm font-semibold">{{ seleccion(l) }}</span>
                            @if (l.experimental) {
                              <span class="experimental shrink-0">Experimental</span>
                            }
                          </span>
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
                  @if (c.experimental) {
                    <p class="mt-2 border-l-2 border-tv-doubt pl-2 text-xs text-pretty text-tv-muted">
                      Las patas de jugador son experimentales: el modelo de props aún se está
                      probando con picks reales.
                    </p>
                  }
                  <button
                    #bc
                    type="button"
                    (click)="toggleCombo(c.legs, bc)"
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

              <!-- Combinada del partido (B14): pick aparte, 2–3 selecciones de un mismo partido. -->
              <aside data-niebla class="h-fit border border-tv-line bg-tv-panel p-4" aria-labelledby="partido-titulo">
                @if (p.match_combo; as mc) {
                  <div class="flex items-baseline justify-between gap-4">
                    <h3 id="partido-titulo" class="font-display text-base font-bold uppercase tracking-wide">
                      Combinada del partido
                    </h3>
                    <span class="font-display text-2xl font-bold tabular-nums">
                      {{ fmtCuota(mc.combined_odds) }}
                    </span>
                  </div>
                  <p class="mt-0.5 text-sm text-tv-muted">
                    <a
                      [routerLink]="['/partido', mc.match_id]"
                      class="text-tv-ink underline-offset-4 [overflow-wrap:anywhere] hover:underline"
                    >
                      {{ mc.home_team }} – {{ mc.away_team }}
                    </a>
                    · {{ fmtTime(mc.commence_time) }}
                  </p>
                  <ul class="mt-3 divide-y divide-tv-line border-y border-tv-line">
                    @for (l of mc.legs; track key(l)) {
                      <li class="flex items-start justify-between gap-3 py-2">
                        <span class="flex min-w-0 items-start gap-2">
                          <!-- En cuanto alguna pata tiene resultado, todas llevan su marca. -->
                          @if (resumenPartido()) {
                            <span class="marca mt-[3px]" [attr.data-estado]="l.result ?? 'pending'">
                              @switch (l.result) {
                                @case ('hit') { <app-icon name="check" [size]="14" /> }
                                @case ('miss') { <app-icon name="x" [size]="14" /> }
                                @case ('needs_review') { <app-icon name="alert" [size]="14" /> }
                                @default { <app-icon name="clock" [size]="14" /> }
                              }
                            </span>
                          }
                          <span class="min-w-0">
                            <span class="block text-sm font-semibold [overflow-wrap:anywhere]">
                              {{ seleccion(l) }}
                            </span>
                            <span class="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs tabular-nums text-tv-muted">
                              <span>P {{ fmtProb(l.model_probability) }}</span>
                              @if (l.experimental) {
                                <span class="experimental">Experimental</span>
                              }
                              @if (resumenPartido()) {
                                <span [attr.data-estado]="l.result ?? 'pending'" class="estado">
                                  {{ estadoLabel(l.result) }}
                                </span>
                              }
                            </span>
                          </span>
                        </span>
                        <span class="tabular-nums">{{ fmtCuota(l.offered_odds) }}</span>
                      </li>
                    }
                  </ul>
                  @if (resumenPartido(); as r) {
                    <p class="mt-2 text-xs tabular-nums text-tv-muted">
                      <span class="font-semibold text-tv-ink">
                        {{ tieneResultado(mc.result) ? estadoLabel(mc.result) : 'En juego' }}
                      </span>
                      · {{ r }}
                    </p>
                  }
                  <p class="mt-2 text-xs tabular-nums text-tv-muted">
                    P conjunta {{ fmtProb(mc.joint_probability) }} ·
                    @if (mc.sin_valor) {
                      <span class="sin-valor">Sin valor</span>
                    } @else {
                      <span class="ev-valor">EV {{ fmtEv(mc.ev) }}</span>
                    }
                  </p>
                  <p class="mt-2 border-l-2 border-tv-doubt pl-2 text-xs text-pretty text-tv-muted">
                    En Winamax se hace con <span class="text-tv-ink">MyMatch</span>: la cuota la pone
                    Winamax y la de arriba es la que estima el modelo. Tiene valor si te pagan
                    <span class="text-tv-ink tabular-nums">{{ fmtCuota(mc.cuota_minima_valor) }}</span>
                    o más.
                    @if (mc.experimental) {
                      Las patas de jugador son experimentales: el modelo de props aún se está
                      probando con picks reales y su acierto se mide aparte en Transparencia.
                    }
                  </p>
                  <button
                    #bp
                    type="button"
                    (click)="toggleCombo(mc.legs, bp)"
                    [attr.aria-pressed]="partidoEnBoletin()"
                    class="cuota mt-3 !h-11 !w-full !flex-row gap-2 font-display text-[15px] font-bold uppercase tracking-wide"
                    [class.cuota-on]="partidoEnBoletin()"
                  >
                    {{ partidoEnBoletin() ? 'Quitar del boletín' : 'Llevar las ' + mc.legs.length + ' al boletín' }}
                  </button>
                } @else {
                  <h3 id="partido-titulo" class="font-display text-base font-bold uppercase tracking-wide">
                    Combinada del partido
                  </h3>
                  <p class="mt-2 text-sm text-tv-muted">
                    {{ p.selection_meta?.motivo_combinada_partido ?? 'Hoy no hay combinada del partido' }}.
                  </p>
                }
              </aside>
            </div>
          </div>
        }
      }
    </section>
  `,
})
export class PicksDelDia {
  private readonly api = inject(ApiService);
  protected readonly slip = inject(BetslipService);
  protected readonly refresco = inject(RefrescoService);

  protected readonly estado = signal<Estado>('cargando');
  /** Sin partidos con el refresh en curso es que aún no ha llegado el calendario nuevo. */
  protected readonly vista = computed<Estado>(() =>
    this.estado() === 'sin_partidos' && this.refresco.enCurso() ? 'preparando' : this.estado(),
  );
  protected readonly pick = signal<FeaturedDailyPickRow | null>(null);
  protected readonly motivo = signal<string | null>(null);
  /** Inicio del próximo partido cuando hoy no queda ninguno (para «Próximos picks»). */
  private readonly proximoPartido = signal<string | null>(null);
  protected readonly proximos = computed(() => fmtProximosPicks(this.proximoPartido()));

  protected readonly comboEnBoletin = computed(() => this.enBoletin(this.pick()?.combo?.legs));
  protected readonly partidoEnBoletin = computed(() =>
    this.enBoletin(this.pick()?.match_combo?.legs),
  );
  /** Cómo van las patas de la combinada del partido ("1 de 2 acertadas · 1 pendiente"). */
  protected readonly resumenPartido = computed(() => {
    const mc = this.pick()?.match_combo;
    return mc ? resumenPatas(mc.legs) : null;
  });

  protected readonly fmtCuota = fmtCuota;
  protected readonly fmtProb = fmtProb;
  protected readonly fmtEv = fmtEv;
  protected readonly fmtTime = fmtTime;
  protected readonly estadoLabel = estadoLabel;
  protected readonly tieneResultado = tieneResultado;

  constructor() {
    this.cargar();
    // El refresh completo genera los picks al terminar: entonces se vuelven a pedir.
    const vistos = this.refresco.terminados();
    effect(() => {
      if (this.refresco.terminados() !== vistos) untracked(() => this.cargar());
    });
  }

  /**
   * Si los picks de hoy aún no existen, se piden al backend (que los genera y congela). Con el
   * refresh completo en curso no: los genera él al terminar, con las stats ya al día.
   */
  protected cargar(): void {
    this.estado.set('cargando');
    this.api
      .getFeaturedPicksToday()
      .pipe(
        switchMap((hoy) => {
          if (hoy.estado !== 'pendiente' || this.refresco.enCurso()) return of(hoy);
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

  /** Una combinada entera: si ya está toda en el boletín se quita; si no, se añade lo que falte. */
  protected toggleCombo(legs: readonly PickLeg[], from: HTMLElement): void {
    if (this.enBoletin(legs)) {
      for (const l of legs) this.slip.remove(this.key(l));
      return;
    }
    legs
      .filter((l) => !this.slip.has(this.key(l)))
      .forEach((l, i) => this.slip.toggle(this.slipLeg(l), i === 0 ? from : undefined));
  }

  private enBoletin(legs: readonly PickLeg[] | undefined): boolean {
    return !!legs?.length && legs.every((l) => this.slip.has(this.key(l)));
  }

  private aplicar(hoy: FeaturedToday): void {
    if (hoy.estado === 'listo' && hoy.pick) {
      this.pick.set(hoy.pick);
      this.estado.set('listo');
    } else if (hoy.estado === 'sin_partidos') {
      this.motivo.set(hoy.motivo ?? 'Hoy no quedan partidos por empezar');
      this.proximoPartido.set(hoy.proximo_partido ?? null);
      this.estado.set('sin_partidos');
    } else if (hoy.estado === 'pendiente') {
      // Solo pasa con el refresh completo en curso (el POST no los genera): al acabar, `cargar`.
      this.estado.set('preparando');
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
