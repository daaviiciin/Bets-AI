import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  inject,
  input,
  signal,
} from '@angular/core';

import { dayKey } from '../../core/format';
import type { Match } from '../../core/models';

/** Franja horaria que cubre cada día de la regla: casi todos los partidos caen entre 11 y 23 h. */
const HORA_INICIO = 11;
const HORA_FIN = 23;
/** Marcas de hora (12, 15, 18, 21 h) en % del tramo. */
const HORAS = [12, 15, 18, 21].map((h) => ((h - HORA_INICIO) / (HORA_FIN - HORA_INICIO)) * 100);
/** Alto máximo de una marca (px): con 4 saques a la misma hora o más ya no crece. */
const ALTO_MARCA = 24;

interface Marca {
  key: string;
  dia: string;
  /** Posición en % del ancho total. */
  x: number;
  /** Escala vertical: 1 saque → 8 px, cada saque más a la misma hora → +5 px. */
  k: number;
}

/**
 * Regla de la jornada: los próximos días en una línea de tiempo, con una marca en la hora de
 * cada saque (más alta si coinciden varios). Es un grafismo de apoyo, no un control: los
 * mismos datos están en los filtros y en la lista, así que se oculta a lectores de pantalla.
 *
 * Al aparecer, la línea base se traza y un cabezal de escaneo la recorre de izquierda a
 * derecha levantando las marcas a su paso. Después solo cambian colores (día elegido) y
 * alturas (filtro de competición), sin repetir la entrada.
 */
@Component({
  selector: 'app-regla-jornada',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block', 'aria-hidden': 'true' },
  styles: `
    .regla {
      --tv-sesgo: 6.5px;
      position: relative;
      height: 46px;
    }

    /* Línea base. */
    .regla::before {
      content: '';
      position: absolute;
      left: 0;
      right: 0;
      bottom: 0;
      height: 1px;
      background: var(--color-tv-line);
      transform-origin: left center;
      animation: tv-trazo 700ms var(--ease-out-expo) 40ms backwards;
    }

    /* Cabezal de escaneo: un filete de tinta que cruza la regla una vez, a velocidad fija. */
    .regla::after {
      content: '';
      position: absolute;
      inset: 0;
      border-right: 1px solid var(--color-tv-ink);
      opacity: 0;
      pointer-events: none;
      animation:
        escanea 540ms linear 120ms both,
        escanea-op 540ms linear 120ms both;
    }

    /* Cada día barre detrás del cabezal, con su separador, su nombre y sus marcas de hora. */
    .tramo {
      position: absolute;
      top: 0;
      bottom: 0;
      transition: background-color 200ms ease;
      animation: tv-barrido 520ms var(--ease-out-expo) var(--tv-t) backwards;
    }

    .tramo-on {
      background: color-mix(in oklab, var(--color-tv-panel) 60%, transparent);
    }

    .corte {
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      width: 1px;
      background: var(--color-tv-line);
      transform-origin: bottom;
      animation: tv-crece-y 420ms var(--ease-out-expo) var(--tv-t) backwards;
    }

    .nombre {
      position: absolute;
      left: 8px;
      right: 4px;
      top: 2px;
      overflow: hidden;
      font-family: var(--font-display);
      font-size: 11px;
      font-weight: 700;
      line-height: 14px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      white-space: nowrap;
      text-overflow: ellipsis;
      color: var(--color-tv-muted);
      transition: color 200ms ease;
      animation-delay: calc(var(--tv-t) + 80ms);
    }

    .tramo-on .nombre {
      color: var(--color-tv-ink);
    }

    .hora {
      position: absolute;
      bottom: 0;
      width: 1px;
      height: 5px;
      background: var(--color-tv-line);
    }

    .marca {
      position: absolute;
      bottom: 1px;
      width: 2px;
      height: ${ALTO_MARCA}px;
      margin-left: -1px;
      background: color-mix(in oklab, var(--color-tv-muted) 45%, transparent);
      transform-origin: bottom;
      transition:
        background-color 200ms ease,
        transform 240ms var(--ease-out-expo);
      animation: tv-crece-y 260ms var(--ease-out-expo) backwards;
    }

    .marca-on {
      background: var(--color-tv-ink);
    }

    @keyframes escanea {
      from {
        transform: translateX(-100%);
      }
      to {
        transform: translateX(0);
      }
    }

    @keyframes escanea-op {
      from,
      88% {
        opacity: 1;
      }
      to {
        opacity: 0;
      }
    }
  `,
  template: `
    <div class="regla">
      @for (d of dias(); track d.key; let i = $index) {
        <div
          class="tramo"
          [class.tramo-on]="d.key === dia()"
          [style.--tv-t]="primera() ? 60 + i * 90 + 'ms' : '0ms'"
          [style.left.%]="(i / dias().length) * 100"
          [style.width.%]="100 / dias().length">
          <span class="corte"></span>
          <span class="nombre tv-sube">{{ d.label }}</span>
          @for (h of horas; track h) {
            <span class="hora" [style.left.%]="h"></span>
          }
        </div>
      }
      @for (m of marcas(); track m.key) {
        <span
          class="marca"
          [class.marca-on]="dia() === 'todos' || dia() === m.dia"
          [style.left.%]="m.x"
          [style.transform]="'scaleY(' + m.k + ')'"
          [style.animation-delay.ms]="primera() ? 120 + m.x * 5.4 : 0"></span>
      }
    </div>
  `,
})
export class ReglaJornada {
  /** Partidos ya filtrados por competición. */
  readonly partidos = input.required<Match[]>();
  /** Días con partidos, en orden (los mismos que los filtros de día). */
  readonly dias = input.required<{ key: string; label: string }[]>();
  /** Día elegido o "todos". */
  readonly dia = input.required<string>();

  protected readonly horas = HORAS;
  /** Solo la primera pintada escalona las marcas con el cabezal; después aparecen sin esperar. */
  protected readonly primera = signal(true);

  protected readonly marcas = computed<Marca[]>(() => {
    const dias = this.dias();
    const indice = new Map(dias.map((d, i) => [d.key, i]));
    const porHora = new Map<string, { dia: string; x: number; n: number }>();
    for (const m of this.partidos()) {
      const dia = dayKey(m.commence_time);
      const i = indice.get(dia);
      if (i == null) continue;
      const fecha = new Date(m.commence_time);
      const hora = fecha.getHours() + fecha.getMinutes() / 60;
      const f = Math.min(1, Math.max(0, (hora - HORA_INICIO) / (HORA_FIN - HORA_INICIO)));
      const key = `${dia}|${fecha.getHours()}:${fecha.getMinutes()}`;
      const actual = porHora.get(key) ?? { dia, x: ((i + f) / dias.length) * 100, n: 0 };
      actual.n += 1;
      porHora.set(key, actual);
    }
    return [...porHora].map(([key, v]) => ({
      key,
      dia: v.dia,
      x: v.x,
      k: Math.min(ALTO_MARCA, 8 + 5 * (v.n - 1)) / ALTO_MARCA,
    }));
  });

  constructor() {
    const t = setTimeout(() => this.primera.set(false), 1200);
    inject(DestroyRef).onDestroy(() => clearTimeout(t));
  }
}
