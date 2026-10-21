import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  afterNextRender,
  inject,
  input,
  signal,
} from '@angular/core';

import { Icon } from '../../shared/icon';
import { movimientoReducido } from '../../shared/tv-motion';

/** Rectángulos del césped: 10 franjas de siega de 10,5 m a lo largo del campo (105 × 68 m). */
const FRANJAS = Array.from({ length: 10 }, (_, i) => i * 105);

/** La entrada solo se emite la primera vez que se abre la portada en la sesión. */
let yaEmitida = false;

/*
 * Geometría del campo (debe coincidir con los estilos de .campo): ancho clamp(880px, 118vw,
 * 1900px), viewBox 1170 × 860 y el punto central a 400 unidades de la banda cercana del viewBox,
 * tumbado con perspective(1200px) rotateX(52deg) desde el borde inferior.
 */
const PERSPECTIVA = 1200;
const INCLINACION = (52 * Math.PI) / 180;
const PUNTO_CENTRAL_DESDE_ABAJO = 400 / 860;
/** Diámetro del balón sin perspectiva: exagerado a propósito (a escala sería un punto). */
const BALON_BASE = 112;

export interface PosicionBalon {
  /** Distancia del suelo del balón al borde inferior de la portada (px). */
  abajo: number;
  /** Diámetro en pantalla, ya encogido por la perspectiva (px). */
  tam: number;
  /** Desde dónde entra rodando (px, negativo = izquierda) y cuánto gira para no patinar. */
  desde: number;
  giro: number;
}

/**
 * Proyecta el punto central del campo a la pantalla: a distancia d de la banda cercana, sube
 * d·cos(52°) y la perspectiva lo encoge por 1200 / (1200 + d·sin(52°)).
 */
export function posicionBalon(anchoVentana: number, altoPortada: number): PosicionBalon {
  const ancho = Math.min(Math.max(880, anchoVentana * 1.18), 1900);
  const d = ((ancho * 860) / 1170) * PUNTO_CENTRAL_DESDE_ABAJO;
  const escala = PERSPECTIVA / (PERSPECTIVA + d * Math.sin(INCLINACION));
  const tam = Math.round(BALON_BASE * escala);
  // El campo cuelga un 2 % por debajo de la portada (bottom: -2%).
  const abajo = Math.round(d * Math.cos(INCLINACION) * escala - altoPortada * 0.02 - tam * 0.12);
  const desde = -Math.round(anchoVentana / 2 + tam);
  const giro = Math.round((desde / (Math.PI * tam)) * 360);
  return { abajo, tam, desde, giro };
}

/** Estrella de 5 puntas centrada en (cx, cy) con radio exterior r, como puntos de polygon. */
function estrella(cx: number, cy: number, r: number, rot = -90): string {
  return Array.from({ length: 10 }, (_, i) => {
    const radio = i % 2 === 0 ? r : r * 0.42;
    const a = ((rot + i * 36) * Math.PI) / 180;
    return `${(cx + radio * Math.cos(a)).toFixed(2)},${(cy + radio * Math.sin(a)).toFixed(2)}`;
  }).join(' ');
}

/** Balón de estrellas propio (no es el logotipo de la UEFA): una central y cinco cortadas al borde. */
const ESTRELLAS = [
  estrella(0, 0, 17),
  ...Array.from({ length: 5 }, (_, i) => {
    const a = ((-90 + 36 + i * 72) * Math.PI) / 180;
    return estrella(41 * Math.cos(a), 41 * Math.sin(a), 15, -90 + 36 + i * 72 + 180);
  }),
];

/**
 * Portada decorativa: lo primero que se ve al abrir la app. Un campo en plano de cámara
 * principal (líneas de cal sobre franjas de siega tonales) con el título encima, como la
 * cortinilla de apertura de una retransmisión. No lleva datos: los partidos empiezan debajo.
 *
 * Al aparecer, un cabezal de escaneo cruza la pantalla mientras se trazan las líneas del campo y
 * el título barre con su filete guía. Solo la primera vez por sesión; al volver a la portada ya
 * está en antena. Con "reducir movimiento", todo quieto desde el principio.
 */
@Component({
  selector: 'app-portada',
  imports: [Icon],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block', '[class.sin-entrada]': '!anima' },
  styles: `
    .portada {
      position: relative;
      isolation: isolate;
      overflow: hidden;
      /* Pantalla entera bajo la barra superior (101 px en móvil con su fila de enlaces). */
      height: calc(100svh - 101px);
      min-height: 30rem;
      background: var(--color-tv-night);
    }

    @media (min-width: 768px) {
      .portada {
        height: calc(100svh - 4rem);
        min-height: 34rem;
      }
    }

    /* Campo plano en SVG, tumbado en perspectiva desde la banda cercana (cámara principal). */
    .campo {
      position: absolute;
      left: 50%;
      bottom: -2%;
      z-index: -1;
      width: clamp(880px, 118vw, 1900px);
      aspect-ratio: 1170 / 860;
      transform: translateX(-50%) perspective(1200px) rotateX(52deg);
      transform-origin: 50% 100%;
      /* El fondo del campo se funde con la noche, como el estadio fuera de foco. */
      mask-image: linear-gradient(to bottom, transparent 0%, #000 42%);
    }

    .campo svg {
      display: block;
      width: 100%;
      height: 100%;
    }

    .cal {
      fill: none;
      stroke: color-mix(in oklab, var(--color-tv-muted) 38%, var(--color-tv-line));
      stroke-width: 2.6;
      stroke-linecap: square;
    }

    /* Grada y franjas de siega: capas tonales noche → noche profunda → panel, sin luz. */
    .franja {
      fill: var(--color-tv-night);
    }

    .fondo {
      fill: var(--color-tv-deep);
    }

    .franja-clara {
      fill: color-mix(in oklab, var(--color-tv-panel) 55%, var(--color-tv-deep));
    }

    .grada {
      fill: color-mix(in oklab, var(--color-tv-panel) 70%, transparent);
    }

    .punto {
      fill: color-mix(in oklab, var(--color-tv-muted) 38%, var(--color-tv-line));
    }

    /* Funde la banda cercana con la página que sigue debajo. */
    .suelo {
      position: absolute;
      inset: auto 0 0 0;
      height: 28%;
      z-index: -1;
      background: linear-gradient(to bottom, transparent, var(--color-tv-night));
      pointer-events: none;
    }

    /* Cabezal de escaneo: una línea de tinta a −8° que cruza una vez y desaparece. */
    .cabezal {
      position: absolute;
      top: 0;
      bottom: 0;
      left: 0;
      width: 3px;
      background: var(--color-tv-ink);
      opacity: 0;
      pointer-events: none;
      animation: portada-escaneo 600ms var(--ease-out-expo) 40ms;
    }

    @keyframes portada-escaneo {
      from {
        opacity: 1;
        transform: translateX(-2vw) skewX(var(--skew));
      }
      85% {
        opacity: 1;
      }
      to {
        opacity: 0;
        transform: translateX(102vw) skewX(var(--skew));
      }
    }

    /* Marcas de encuadre en las cuatro esquinas, como el área segura de un monitor. */
    .encuadre {
      position: absolute;
      inset: 1rem;
      pointer-events: none;
    }

    .encuadre > span {
      position: absolute;
      width: 22px;
      height: 22px;
      border-color: var(--color-tv-line);
      border-style: solid;
      border-width: 0;
    }

    .encuadre > span:nth-child(1) {
      top: 0;
      left: 0;
      border-top-width: 1px;
      border-left-width: 1px;
    }

    .encuadre > span:nth-child(2) {
      top: 0;
      right: 0;
      border-top-width: 1px;
      border-right-width: 1px;
    }

    .encuadre > span:nth-child(3) {
      bottom: 0;
      left: 0;
      border-bottom-width: 1px;
      border-left-width: 1px;
    }

    .encuadre > span:nth-child(4) {
      bottom: 0;
      right: 0;
      border-bottom-width: 1px;
      border-right-width: 1px;
    }

    @media (min-width: 640px) {
      .encuadre {
        inset: 1.5rem 2rem;
      }
    }

    .titulo {
      font-size: clamp(4rem, 15vw, 9rem);
      line-height: 1;
    }

    /*
     * Balón: entra rodando desde la banda izquierda, frena y se queda en el punto central para
     * el saque. El rodar gira lo justo para la distancia recorrida (--giro), sin patinar; la
     * sombra viaja con él sin girar. Sin brillo: tinta, estrellas noche y una media luna tonal.
     */
    .balon {
      position: absolute;
      left: 50%;
      bottom: var(--abajo);
      width: var(--tam);
      height: var(--tam);
      margin-left: calc(var(--tam) / -2);
      pointer-events: none;
      animation: balon-avanza 1050ms cubic-bezier(0.22, 1, 0.36, 1) 180ms backwards;
    }

    .balon svg {
      position: absolute;
      inset: 0;
      display: block;
      width: 100%;
      height: 100%;
    }

    .balon .gira {
      animation: balon-rueda 1050ms cubic-bezier(0.22, 1, 0.36, 1) 180ms backwards;
    }

    .balon .sombra {
      position: absolute;
      left: 8%;
      right: 8%;
      bottom: -9%;
      height: 18%;
      border-radius: 9999px;
      background: var(--color-tv-deep);
      opacity: 0.85;
      z-index: -1;
    }

    .balon .piel {
      fill: var(--color-tv-ink);
    }

    .balon .estrella {
      fill: var(--color-tv-night);
    }

    .balon .volumen {
      fill: var(--color-tv-muted);
      opacity: 0.28;
    }

    @keyframes balon-avanza {
      from {
        transform: translateX(var(--desde));
      }
    }

    @keyframes balon-rueda {
      from {
        transform: rotate(var(--giro));
      }
    }

    /* Al volver a la portada en la misma sesión ya está en antena: nada vuelve a entrar. */
    :host(.sin-entrada) *,
    :host(.sin-entrada) *::before,
    :host(.sin-entrada) *::after {
      animation: none !important;
    }
  `,
  template: `
    <div class="portada">
      <div class="campo" aria-hidden="true">
        <svg viewBox="-60 -120 1170 860" preserveAspectRatio="xMidYMax meet">
          <rect x="-60" y="-120" width="1170" height="860" class="fondo" />
          <rect x="-60" y="-120" width="1170" height="80" class="grada" />
          @for (x of franjas; track x; let par = $even) {
            <rect
              class="franja"
              [class.franja-clara]="!par"
              [attr.x]="x"
              y="0"
              width="105"
              height="680"
            />
          }

          <!-- Líneas de cal (medidas reglamentarias × 10): se trazan al aparecer. -->
          <g class="cal tv-dibuja" style="--tv-t: 40ms">
            <path pathLength="1" d="M0 0H1050V680H0Z" />
            <path pathLength="1" d="M525 0V680" />
            <path pathLength="1" d="M525 248.5a91.5 91.5 0 1 1 0 183a91.5 91.5 0 1 1 0-183" />
            <path pathLength="1" d="M0 138.5H165V541.5H0" />
            <path pathLength="1" d="M1050 138.5H885V541.5H1050" />
            <path pathLength="1" d="M0 248.5H55V431.5H0" />
            <path pathLength="1" d="M1050 248.5H995V431.5H1050" />
            <path pathLength="1" d="M165 266.9A91.5 91.5 0 0 1 165 413.1" />
            <path pathLength="1" d="M885 266.9A91.5 91.5 0 0 0 885 413.1" />
            <path pathLength="1" d="M0 303.4H-18V376.6H0" />
            <path pathLength="1" d="M1050 303.4H1068V376.6H1050" />
            <path pathLength="1" d="M10 0A10 10 0 0 1 0 10" />
            <path pathLength="1" d="M1040 0A10 10 0 0 0 1050 10" />
            <path pathLength="1" d="M0 670A10 10 0 0 1 10 680" />
            <path pathLength="1" d="M1050 670A10 10 0 0 0 1040 680" />
          </g>
          <g class="punto">
            <circle cx="525" cy="340" r="4" />
            <circle cx="110" cy="340" r="3.5" />
            <circle cx="940" cy="340" r="3.5" />
          </g>
        </svg>
      </div>
      <div class="suelo" aria-hidden="true"></div>
      @if (balon(); as b) {
        <div
          class="balon"
          aria-hidden="true"
          [style.--abajo.px]="b.abajo"
          [style.--tam.px]="b.tam"
          [style.--desde.px]="b.desde"
          [style.--giro.deg]="b.giro"
        >
          <span class="sombra"></span>
          <svg class="gira" viewBox="-50 -50 100 100">
            <defs>
              <clipPath id="balon-borde"><circle r="48" /></clipPath>
            </defs>
            <circle class="piel" r="48" />
            <g clip-path="url(#balon-borde)">
              @for (e of estrellas; track $index) {
                <polygon class="estrella" [attr.points]="e" />
              }
            </g>
          </svg>
          <!-- Volumen: media luna tonal abajo a la derecha. No gira: la luz no se mueve con el balón. -->
          <svg class="luz" viewBox="-50 -50 100 100">
            <defs>
              <mask id="balon-media-luna">
                <circle r="48" fill="#fff" />
                <circle cx="-13" cy="-13" r="50" fill="#000" />
              </mask>
            </defs>
            <circle class="volumen" r="48" mask="url(#balon-media-luna)" />
          </svg>
        </div>
      }
      <div class="cabezal" aria-hidden="true"></div>
      <div class="encuadre" aria-hidden="true">
        <span></span><span></span><span></span><span></span>
      </div>

      <div
        class="relative mx-auto flex h-full max-w-[1440px] flex-col px-4 pb-8 pt-[14svh] sm:px-8 sm:pt-[16svh]"
      >
        <p class="titulo rotulo flex items-center gap-[0.12em]">
          <span class="tv-guia inline-block" style="--tv-t: 120ms">Bets</span>
          <span
            class="tv-placa skew inline-block bg-tv-ink px-[0.14em] text-tv-night"
            style="--tv-t: 280ms"
          >
            <span class="unskew inline-block">AI</span>
          </span>
        </p>
        <span class="mt-5 flex items-center gap-3" aria-hidden="true">
          <span class="tv-barras" style="--tv-t: 120ms"></span>
          <span class="tv-trazo h-px w-24 bg-tv-line sm:w-40" style="--tv-t: 360ms"></span>
        </span>
        <p
          class="tv-sube mt-5 max-w-[34ch] text-lg text-tv-muted text-pretty sm:text-xl"
          style="--tv-t: 320ms"
        >
          Cuotas de Winamax, estadísticas de Flashscore y el modelo Poisson, en una sola vista.
        </p>

        <button
          type="button"
          (click)="bajar()"
          aria-label="Bajar a los partidos"
          style="--tv-t: 420ms"
          class="tv-sube group mx-auto mt-auto flex flex-col items-center gap-1 px-4 py-2 text-tv-muted transition-[color,scale] duration-150 hover:text-tv-ink active:scale-[0.97]"
        >
          <span class="font-display text-sm font-bold uppercase tracking-wider">Partidos</span>
          <app-icon
            name="chevron-right"
            [size]="22"
            class="rotate-90 transition-transform duration-150 group-hover:translate-y-0.5"
          />
        </button>
      </div>
    </div>
  `,
})
export class Portada {
  /** A dónde baja el indicador (la cartela de Partidos). */
  readonly destino = input<HTMLElement>();

  protected readonly franjas = FRANJAS;
  protected readonly estrellas = ESTRELLAS;
  protected readonly anima = !yaEmitida && !movimientoReducido();
  /** Se calcula al pintar (necesita medidas reales); hasta entonces no hay balón. */
  protected readonly balon = signal<PosicionBalon | null>(null);

  constructor() {
    yaEmitida = true;
    const host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
    const destroyRef = inject(DestroyRef);
    afterNextRender(() => {
      const medir = () => this.balon.set(posicionBalon(window.innerWidth, host.offsetHeight));
      medir();
      const ro = new ResizeObserver(medir);
      ro.observe(host);
      destroyRef.onDestroy(() => ro.disconnect());
    });
  }

  protected bajar(): void {
    this.destino()?.scrollIntoView({
      behavior: movimientoReducido() ? 'auto' : 'smooth',
      block: 'start',
    });
  }
}
