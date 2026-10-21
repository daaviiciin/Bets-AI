import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { fmtNum } from '../../core/format';
import { TvCifra } from '../../shared/tv-motion';

export interface CompareRow {
  label: string;
  home: number | null;
  away: number | null;
  digits?: number;
  /** Desglose de la fila anterior (1ª / 2ª parte): más pequeña y con barra fina. */
  sub?: boolean;
}

/**
 * Comparativa local / visitante como en un grafismo de TV: las dos barras crecen desde el
 * centro, cada una con el color de su club. La cifra mayor de cada fila va en tinta y la
 * menor en gris, para leer quién lleva ventaja sin mirar las barras. Sin dato, la barra no
 * se dibuja y se ve "–". La leyenda (club y muestra) va arriba, cada club en su lado.
 *
 * Movimiento: al entrar, las barras crecen desde el centro (40 ms por fila, con tope) y las
 * cifras cuentan desde 0. Al cambiar de muestra (en su campo / toda la temporada) las barras
 * se transforman a su nuevo tamaño y las cifras cuentan del valor viejo al nuevo.
 */
@Component({
  selector: 'app-stat-compare',
  imports: [TvCifra],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .fila + .fila {
      margin-top: 0.875rem;
    }
    .fila + .fila-sub {
      margin-top: 0.375rem;
    }
    /* Tras un desglose por mitades, más aire: cada medida se lee como un grupo. */
    .fila-sub + .fila:not(.fila-sub) {
      margin-top: 1.25rem;
    }
    /*
     * Visible por defecto: sin animaciones la barra ya está a su tamaño. La barra ocupa su
     * media pista y su parte se dibuja con scaleX: así crece al entrar (keyframe desde 0) y
     * se transforma, sin tocar el layout, cuando cambian los datos.
     */
    .barra {
      width: 100%;
      transition: transform 420ms var(--ease-out-expo);
      animation: crece 650ms var(--ease-out-expo) backwards;
    }
    .barra-local {
      transform-origin: right center;
    }
    .barra-visitante {
      transform-origin: left center;
    }
    @keyframes crece {
      from {
        transform: scaleX(0);
      }
    }
  `,
  template: `
    @if (homeLabel() || awayLabel()) {
      <div class="stat-leyenda mb-4 flex items-center justify-between gap-4 text-sm text-tv-muted">
        <span class="flex min-w-0 items-center gap-2">
          <span class="size-2.5 shrink-0" [style.background]="homeColor()"></span>
          <span class="truncate">{{ homeLabel() }}</span>
        </span>
        <span class="flex min-w-0 items-center gap-2">
          <span class="truncate text-right">{{ awayLabel() }}</span>
          <span class="size-2.5 shrink-0" [style.background]="awayColor()"></span>
        </span>
      </div>
    }
    <dl>
      @for (row of rows(); track $index; let i = $index) {
        @let lead = leader(row);
        <div
          class="fila grid grid-cols-[3.5rem_minmax(0,1fr)_3.5rem] items-baseline gap-x-3"
          [class.fila-sub]="row.sub">
          <dt
            class="order-2 text-center"
            [class]="row.sub ? 'text-xs text-tv-muted' : 'text-sm font-medium text-tv-muted'">
            {{ row.label }}
          </dt>
          <dd
            class="font-display order-1 font-bold"
            [class]="row.sub ? 'text-base' : 'text-xl'"
            [class.text-tv-muted]="lead === 'away'">
            <span class="sr-only">{{ homeLabel() }} </span
            ><span [tvCifra]="row.home" [tvFormato]="formato(row.digits)" [tvDesde]="0"></span>
          </dd>
          <dd
            class="font-display order-3 text-right font-bold"
            [class]="row.sub ? 'text-base' : 'text-xl'"
            [class.text-tv-muted]="lead === 'home'">
            <span class="sr-only">{{ awayLabel() }} </span
            ><span [tvCifra]="row.away" [tvFormato]="formato(row.digits)" [tvDesde]="0"></span>
          </dd>
          <dd
            class="order-4 col-span-3 grid grid-cols-2 gap-0.5"
            [class]="row.sub ? 'mt-1 opacity-60' : 'mt-1.5'"
            aria-hidden="true">
            <span class="flex justify-end" [class]="row.sub ? 'h-1' : 'h-1.5'">
              <span
                class="barra barra-local block h-full"
                [style.transform]="'scaleX(' + share(row.home, row.away) / 100 + ')'"
                [style.background]="homeColor()"
                [style.animation-delay]="'calc(var(--tv-t) + ' + delay(i) + 'ms)'"></span>
            </span>
            <span class="flex" [class]="row.sub ? 'h-1' : 'h-1.5'">
              <span
                class="barra barra-visitante block h-full"
                [style.transform]="'scaleX(' + share(row.away, row.home) / 100 + ')'"
                [style.background]="awayColor()"
                [style.animation-delay]="'calc(var(--tv-t) + ' + delay(i) + 'ms)'"></span>
            </span>
          </dd>
        </div>
      }
    </dl>
  `,
})
export class StatCompare {
  readonly rows = input.required<CompareRow[]>();
  readonly homeColor = input.required<string>();
  readonly awayColor = input.required<string>();
  /** Leyenda de cada lado ("Real Betis · 5 en casa"); sin ella no se pinta la cabecera. */
  readonly homeLabel = input<string | null>(null);
  readonly awayLabel = input<string | null>(null);

  /** Un formateador por número de decimales (siempre el mismo objeto: TvCifra no re-cuenta). */
  private readonly formatos = new Map<number, (v: number | null) => string>();

  protected formato(digits = 1): (v: number | null) => string {
    let f = this.formatos.get(digits);
    if (!f) {
      f = (v) => (v == null ? '–' : fmtNum(v, digits));
      this.formatos.set(digits, f);
    }
    return f;
  }

  /** Escalonado de las barras: 40 ms por fila, como mucho 400 ms. */
  protected delay(i: number): number {
    return 80 + Math.min(i, 10) * 40;
  }

  /**
   * Qué lado tiene la cifra mayor tal como se enseña (redondeada): un 2,24 contra un 2,21
   * se leen "2,2" los dos y no destaca ninguno. ``null`` si empatan o falta un dato.
   */
  protected leader(row: CompareRow): 'home' | 'away' | null {
    if (row.home == null || row.away == null) return null;
    const k = 10 ** (row.digits ?? 1);
    const h = Math.round(row.home * k);
    const a = Math.round(row.away * k);
    return h === a ? null : h > a ? 'home' : 'away';
  }

  /** Mitad de la pista que ocupa cada lado: proporcional a su parte del total. */
  protected share(mine: number | null, other: number | null): number {
    if (mine == null || mine <= 0) return 0;
    const total = mine + (other ?? 0);
    return total > 0 ? (mine / total) * 100 : 0;
  }
}
