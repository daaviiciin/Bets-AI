import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';

import { Icon } from '../../shared/icon';
import { movimientoReducido } from '../../shared/tv-motion';

/** Rectángulos del césped: 10 franjas de siega de 10,5 m a lo largo del campo (105 × 68 m). */
const FRANJAS = Array.from({ length: 10 }, (_, i) => i * 105);

/** La entrada solo se emite la primera vez que se abre la portada en la sesión. */
let yaEmitida = false;

/*
 * Guion (ms desde que se pinta la portada). La apertura entra una vez por sesión; la jugada se
 * repite en bucle mientras la portada está en pantalla.
 *
 *    0  la cámara baja como una grúa desde el cenital al plano de cámara principal (1300 ms)
 *  240  se trazan las líneas de cal
 *  420  aparecen los jugadores, uno tras otro
 *  560  barre el título
 *
 * Cada ciclo de la jugada (ms desde que empieza el ciclo):
 *  700  saque: tres pases y un tiro, cada tramo se dibuja con el balón en la punta y una pausa
 *       corta en cada pie (el jugador controla antes de pasar)
 * 4500  gol: la red se ilumina, sale un aro y la cámara da un golpe
 * 5700  la jugada se apaga (700 ms) y, tras un respiro a oscuras, vuelve a empezar
 */
const SAQUE = 700;
/** Cada tramo: [inicio relativo al saque, duración]. Los huecos entre tramos son el control. */
const TRAMOS: [number, number][] = [
  [0, 900],
  [1050, 900],
  [2100, 900],
  [3150, 650],
];
const JUGADA_MS = 3800;
const GOL = SAQUE + JUGADA_MS;
const APAGA = GOL + 1200;
const CICLO = 6900;
/** easeOutCubic: sale fuerte del pie y frena hasta el siguiente, sin la prisa de la expo. */
const PASE_EASE = 'cubic-bezier(0.33, 1, 0.68, 1)';
const PASE_SPLINE = '0.33 1 0.68 1';
/** Golpe de cámara en el gol (Web Animations): empuja un 2 % y vuelve. */
const CAMPO_T = 'translateX(-50%) perspective(1200px) rotateX(52deg)';

/** Atacantes (rellenos, de tinta) en orden de la jugada, en metros × 10 sobre el campo. */
const LOCALES: [number, number][] = [
  [330, 430],
  [520, 260],
  [720, 400],
  [880, 230],
];
/** Defensas y portero (contorno): dan contexto a los pases sin competir con ellos. */
const RIVALES: [number, number][] = [
  [440, 350],
  [615, 335],
  [800, 315],
  [940, 380],
  [965, 265],
  [1032, 342],
];

/** Pases rectos y un tiro con rosca que acaba dentro de la portería (x = 1050, entre los palos). */
const TRAMOS_D = [
  'M330 430L520 260',
  'M520 260L720 400',
  'M720 400L880 230',
  'M880 230Q990 225 1050 330',
];
const JUGADA_D = 'M330 430L520 260L720 400L880 230Q990 225 1050 330';
/** Fracción de distancia recorrida al final de cada tramo (largos ≈ 255, 244, 233 y 209). */
const DISTANCIAS = [0.271, 0.53, 0.778, 1];

/**
 * Para animateMotion: puntos de distancia y de tiempo con las pausas metidas como tramos quietos
 * (mismo punto, spline lineal), para que el balón llegue a cada pie justo cuando acaba su trazo.
 */
function guionBalon(): { points: string; times: string; splines: string } {
  const points = ['0'];
  const times = ['0'];
  const splines: string[] = [];
  let t = 0;
  TRAMOS.forEach(([ini, dur], i) => {
    if (ini > t) {
      points.push(points[points.length - 1]);
      times.push((ini / JUGADA_MS).toFixed(4));
      splines.push('0 0 1 1');
    }
    t = ini + dur;
    points.push(String(DISTANCIAS[i]));
    times.push((t / JUGADA_MS).toFixed(4));
    splines.push(PASE_SPLINE);
  });
  return { points: points.join(';'), times: times.join(';'), splines: splines.join('; ') };
}
const BALON = guionBalon();

/**
 * Portada decorativa: lo primero que se ve al abrir la app. Un campo en plano de cámara
 * principal (líneas de cal sobre franjas de siega tonales) con el título encima, como la
 * cortinilla de apertura de una retransmisión. No lleva datos: los partidos empiezan debajo.
 *
 * Encima corre en bucle una jugada de telestrator: tres pases y un tiro con el balón en la punta
 * que acaban en gol con un golpe de cámara. La apertura (grúa, jugadores, título) solo entra la
 * primera vez por sesión; la jugada se repite mientras la portada se ve y se para fuera de
 * pantalla o con la pestaña oculta, para no gastar nada que no se esté viendo. Con "reducir
 * movimiento", todo quieto desde el principio.
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

    /*
     * Campo plano en SVG, tumbado en perspectiva desde la banda cercana (cámara principal).
     * Entra con una grúa: desde casi cenital y girado, baja y endereza hasta el plano de siempre.
     * En el gol, un golpe de cámara corto (empuja un 2 % y vuelve).
     */
    .campo {
      --campo: translateX(-50%) perspective(1200px) rotateX(52deg);
      position: absolute;
      left: 50%;
      bottom: -2%;
      z-index: -1;
      width: clamp(880px, 118vw, 1900px);
      aspect-ratio: 1170 / 860;
      transform: var(--campo);
      transform-origin: 50% 100%;
      /* El fondo del campo se funde con la noche, como el estadio fuera de foco. */
      mask-image: linear-gradient(to bottom, transparent 0%, #000 42%);
      animation: campo-grua 1300ms var(--ease-in-out-cam) backwards;
    }

    @keyframes campo-grua {
      from {
        opacity: 0;
        transform: translateX(-50%) perspective(1200px) rotateX(14deg) rotateZ(-16deg) scale(0.9);
      }
      30% {
        opacity: 1;
      }
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

    /*
     * Jugada de telestrator, dibujada sobre el césped (por eso se ve en perspectiva). Trazos de
     * tinta sólidos, sin brillo; al terminar se apaga y queda de fondo.
     */
    .jugada {
      animation: jugada-apaga 700ms var(--ease-out-expo) var(--apaga) forwards;
    }

    @keyframes jugada-apaga {
      to {
        opacity: 0;
      }
    }

    .ficha {
      transform-box: fill-box;
      transform-origin: center;
      animation: jugada-ficha 360ms var(--ease-out-expo) var(--t) backwards;
    }

    .local {
      fill: var(--color-tv-ink);
    }

    .rival {
      fill: var(--color-tv-night);
      stroke: var(--color-tv-muted);
      stroke-width: 3;
    }

    @keyframes jugada-ficha {
      from {
        opacity: 0;
        transform: scale(0.6);
      }
    }

    .pase {
      fill: none;
      stroke: var(--color-tv-ink);
      stroke-width: 5;
      stroke-linecap: round;
      stroke-dasharray: 1;
      animation: jugada-traza var(--d) var(--pase-ease) var(--t) backwards;
    }

    @keyframes jugada-traza {
      from {
        stroke-dashoffset: 1;
      }
    }

    /* Aro que se abre bajo el pie que recibe (y uno mayor en la portería). */
    .aro {
      fill: none;
      stroke: var(--color-tv-ink);
      stroke-width: 3;
      opacity: 0;
      transform-box: fill-box;
      transform-origin: center;
      animation: jugada-aro 520ms var(--ease-out-expo) var(--t);
    }

    .aro-gol {
      stroke-width: 4;
      animation-name: jugada-aro-gol;
      animation-duration: 760ms;
    }

    @keyframes jugada-aro {
      from {
        opacity: 1;
        transform: scale(0.5);
      }
      to {
        opacity: 0;
        transform: scale(1.8);
      }
    }

    @keyframes jugada-aro-gol {
      from {
        opacity: 1;
        transform: scale(0.6);
      }
      to {
        opacity: 0;
        transform: scale(3);
      }
    }

    /* La red se enciende un instante cuando entra el balón. */
    .red {
      fill: var(--color-tv-ink);
      opacity: 0;
      animation: jugada-red 600ms var(--ease-out-expo) var(--gol);
    }

    @keyframes jugada-red {
      from {
        opacity: 0.9;
      }
    }

    /* El balón: oculto hasta el saque; animateMotion lo lleva por la jugada. */
    .balon {
      fill: var(--color-tv-ink);
      stroke: var(--color-tv-night);
      stroke-width: 3;
      animation: jugada-balon 120ms linear var(--saque) backwards;
    }

    @keyframes jugada-balon {
      from {
        opacity: 0;
      }
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
     * Al volver a la portada en la misma sesión ya está en antena: la apertura no vuelve a entrar.
     * La jugada en bucle sí sigue (va aparte, en .jugada).
     */
    :host(.sin-entrada) *:not(.jugada, .jugada *),
    :host(.sin-entrada) *:not(.jugada, .jugada *)::before,
    :host(.sin-entrada) *:not(.jugada, .jugada *)::after {
      animation: none !important;
    }
  `,
  template: `
    <div
      class="portada"
      [style.--saque.ms]="saque"
      [style.--gol.ms]="gol"
      [style.--apaga.ms]="apaga"
      [style.--pase-ease]="paseEase"
    >
      <div #campo class="campo" aria-hidden="true">
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
          <g class="cal tv-dibuja" style="--tv-t: 120ms">
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

          @if (enBucle) {
            <!-- Jugadores: fijos; solo entran con la apertura. -->
            <g class="jugadores">
              @for (r of rivales; track $index) {
                <circle
                  class="ficha rival"
                  [attr.cx]="r[0]"
                  [attr.cy]="r[1]"
                  r="10"
                  [style.--t.ms]="440 + $index * 30"
                />
              }
              @for (l of locales; track $index) {
                <circle
                  class="ficha local"
                  [attr.cx]="l[0]"
                  [attr.cy]="l[1]"
                  r="12"
                  [style.--t.ms]="420 + $index * 40"
                />
              }
            </g>
            <!-- Jugada en bucle: se vuelve a crear en cada ciclo para que todo arranque de cero. -->
            @for (c of [ciclo()]; track c) {
              <g class="jugada">
                <rect class="red" x="1050" y="303.4" width="18" height="73.2" />
                @for (t of tramos; track $index) {
                  <path
                    class="pase"
                    pathLength="1"
                    [attr.d]="t.d"
                    [style.--t.ms]="t.inicio"
                    [style.--d.ms]="t.dur"
                  />
                }
                @for (a of aros; track $index) {
                  <circle
                    class="aro"
                    [class.aro-gol]="$last"
                    [attr.cx]="a.x"
                    [attr.cy]="a.y"
                    [attr.r]="$last ? 34 : 24"
                    [style.--t.ms]="a.t"
                  />
                }
                <circle class="balon" r="9">
                  <animateMotion
                    begin="indefinite"
                    fill="freeze"
                    calcMode="spline"
                    [attr.dur]="jugadaMs + 'ms'"
                    [attr.path]="jugadaD"
                    [attr.keyPoints]="balon.points"
                    [attr.keyTimes]="balon.times"
                    [attr.keySplines]="balon.splines"
                  />
                </circle>
              </g>
            }
          }
        </svg>
      </div>
      <div class="suelo" aria-hidden="true"></div>
      <div class="cabezal" aria-hidden="true"></div>
      <div class="encuadre" aria-hidden="true">
        <span></span><span></span><span></span><span></span>
      </div>

      <div
        class="relative mx-auto flex h-full max-w-[1440px] flex-col px-4 pb-8 pt-[14svh] sm:px-8 sm:pt-[16svh]"
      >
        <p class="titulo rotulo flex items-center gap-[0.12em]">
          <span class="tv-guia inline-block" style="--tv-t: 560ms">Bets</span>
          <span
            class="tv-placa skew inline-block bg-tv-ink px-[0.14em] text-tv-night"
            style="--tv-t: 720ms"
          >
            <span class="unskew inline-block">AI</span>
          </span>
        </p>
        <span class="mt-5 flex items-center gap-3" aria-hidden="true">
          <span class="tv-barras" style="--tv-t: 560ms"></span>
          <span class="tv-trazo h-px w-24 bg-tv-line sm:w-40" style="--tv-t: 800ms"></span>
        </span>
        <p
          class="tv-sube mt-5 max-w-[34ch] text-lg text-tv-muted text-pretty sm:text-xl"
          style="--tv-t: 760ms"
        >
          Cuotas de Winamax, estadísticas de Flashscore y el modelo Poisson, en una sola vista.
        </p>

        <button
          type="button"
          (click)="bajar()"
          aria-label="Bajar a los partidos"
          style="--tv-t: 900ms"
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
  protected readonly anima = !yaEmitida && !movimientoReducido();

  /** La jugada corre siempre salvo con "reducir movimiento". */
  protected readonly enBucle = !movimientoReducido();
  /** Cada incremento vuelve a crear la jugada (nuevo ciclo). */
  protected readonly ciclo = signal(0);

  protected readonly saque = SAQUE;
  protected readonly gol = GOL;
  protected readonly apaga = APAGA;
  protected readonly paseEase = PASE_EASE;
  protected readonly jugadaMs = JUGADA_MS;
  protected readonly jugadaD = JUGADA_D;
  protected readonly balon = BALON;
  protected readonly locales = LOCALES;
  protected readonly rivales = RIVALES;
  protected readonly tramos = TRAMOS.map(([ini, dur], i) => ({
    d: TRAMOS_D[i],
    inicio: SAQUE + ini,
    dur,
  }));
  /** Un aro bajo cada pie que recibe, cuando llega el balón, y el del gol en la portería. */
  protected readonly aros = [
    ...TRAMOS.slice(0, 3).map(([ini, dur], i) => ({
      x: LOCALES[i + 1][0],
      y: LOCALES[i + 1][1],
      t: SAQUE + ini + dur,
    })),
    { x: 1059, y: 340, t: GOL },
  ];

  private readonly campo = viewChild<ElementRef<HTMLElement>>('campo');

  private readonly destroyRef = inject(DestroyRef);
  private readonly injector = inject(Injector);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private visible = true;
  private siguiente: ReturnType<typeof setTimeout> | null = null;
  private golpe: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    yaEmitida = true;
    if (!this.enBucle) return;

    const alVolver = () => this.reanudar();

    afterNextRender(() => {
      this.arrancar();
      const vigia = new IntersectionObserver(([e]) => {
        this.visible = e.isIntersecting;
        if (this.visible) this.reanudar();
      });
      vigia.observe(this.host);
      document.addEventListener('visibilitychange', alVolver);
      this.programar();
      this.destroyRef.onDestroy(() => {
        vigia.disconnect();
        document.removeEventListener('visibilitychange', alVolver);
      });
    });

    this.destroyRef.onDestroy(() => {
      if (this.siguiente) clearTimeout(this.siguiente);
      if (this.golpe) clearTimeout(this.golpe);
    });
  }

  /** Programa el siguiente ciclo; si la portada no se ve, el bucle se queda parado. */
  private programar(): void {
    this.siguiente = setTimeout(() => {
      this.siguiente = null;
      if (!this.activa()) return;
      this.nuevoCiclo();
      this.programar();
    }, CICLO);
  }

  /** Vuelve a crear la jugada y la arranca en cuanto está pintada. */
  private nuevoCiclo(): void {
    this.ciclo.update((n) => n + 1);
    afterNextRender(() => this.arrancar(), { injector: this.injector });
  }

  /**
   * El reloj de SMIL cuenta desde la carga del documento, no desde que se pinta la jugada: el
   * balón se arranca a mano, a la vez que las animaciones CSS (que empiezan al pintarse). Se busca
   * en el DOM el animateMotion recién creado (una consulta de vista podría dar aún el anterior).
   */
  private arrancar(): void {
    const recorrido = this.host.querySelector<SVGAnimationElement>('.jugada animateMotion');
    recorrido?.beginElementAt(SAQUE / 1000);
    if (this.golpe) clearTimeout(this.golpe);
    this.golpe = setTimeout(() => this.golpeDeCamara(), GOL);
  }

  /** Al volver a verse (scroll o pestaña), arranca un ciclo nuevo si el bucle estaba parado. */
  private reanudar(): void {
    if (this.siguiente || !this.activa()) return;
    this.nuevoCiclo();
    this.programar();
  }

  private activa(): boolean {
    return this.visible && !document.hidden;
  }

  private golpeDeCamara(): void {
    this.campo()?.nativeElement.animate(
      [
        { transform: CAMPO_T },
        { transform: `${CAMPO_T} scale(1.022)`, offset: 0.3 },
        { transform: CAMPO_T },
      ],
      { duration: 420, easing: 'cubic-bezier(0.16, 1, 0.3, 1)' },
    );
  }

  protected bajar(): void {
    this.destino()?.scrollIntoView({
      behavior: movimientoReducido() ? 'auto' : 'smooth',
      block: 'start',
    });
  }
}
