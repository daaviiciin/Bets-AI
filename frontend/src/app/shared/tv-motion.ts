import {
  DestroyRef,
  Directive,
  ElementRef,
  Injector,
  afterNextRender,
  effect,
  inject,
  input,
  untracked,
} from '@angular/core';

import { toNumber } from '../core/format';

/**
 * Directivas del movimiento "entrada en antena" (ver la cabecera del bloque en styles.scss).
 * El aspecto de cada entrada lo ponen las clases `tv-*` de styles.scss; estas directivas solo
 * deciden CUÁNDO entra cada pieza y escriben las cifras que cuentan.
 */

/** Con "reducir movimiento", todo se pinta en su estado final y nada se escalona. */
export function movimientoReducido(): boolean {
  return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** tan(8°): el canto inclinado se desplaza 0,14 × el alto de la pieza. */
const TAN_SKEW = 0.1405;

/**
 * Un solo IntersectionObserver para todas las piezas. Las que entran en pantalla en el mismo
 * fotograma forman una tanda y se escalonan por grupo (filas con filas, filtros con filtros),
 * de arriba abajo y de izquierda a derecha.
 */
class Antena {
  private io: IntersectionObserver | null = null;
  private readonly piezas = new Map<Element, TvEntra>();

  observar(el: Element, pieza: TvEntra): void {
    this.io ??= new IntersectionObserver((entradas) => this.entran(entradas), {
      // La pieza entra cuando asoman sus primeros 24 px, no al rozar el borde.
      rootMargin: '0px 0px -24px 0px',
    });
    this.piezas.set(el, pieza);
    this.io.observe(el);
  }

  dejar(el: Element): void {
    this.piezas.delete(el);
    this.io?.unobserve(el);
  }

  private entran(entradas: IntersectionObserverEntry[]): void {
    const visibles = entradas
      .filter((e) => e.isIntersecting)
      .sort(
        (a, b) =>
          a.boundingClientRect.top - b.boundingClientRect.top ||
          a.boundingClientRect.left - b.boundingClientRect.left,
      );
    const porGrupo = new Map<string, number>();
    for (const e of visibles) {
      const pieza = this.piezas.get(e.target);
      if (!pieza) continue;
      this.dejar(e.target);
      const grupo = pieza.tvEntra();
      const i = porGrupo.get(grupo) ?? 0;
      porGrupo.set(grupo, i + 1);
      pieza.entrar(i, e.boundingClientRect.height);
    }
  }
}

const antena = new Antena();

/**
 * Retiene la entrada CSS de una pieza (y de sus hijos) hasta que se ve, y la escalona con las
 * demás de su grupo que entran a la vez. Así lo que está por debajo del pliegue entra al
 * desplazarse, una sola vez, y lo que se re-pinta al cambiar un filtro se re-escalona rápido.
 *
 * ```html
 * <li tvEntra="fila" [tvRetardo]="140" [tvPaso]="30" class="tv-fila">…</li>
 * ```
 * - `tvEntra`: grupo de escalonado (texto libre; "" vale como grupo).
 * - `tvRetardo`: ms antes de la primera pieza de la tanda.
 * - `tvPaso`: ms entre piezas del mismo grupo en la tanda.
 * - `tvTope`: escalonado máximo en ms (la tanda nunca se alarga más que esto).
 *
 * Pone `--tv-t` (retardo) y `--tv-sesgo` (canto a −8° según el alto medido) en la pieza; sus
 * hijos los heredan. Sin IntersectionObserver o con "reducir movimiento" no retiene nada.
 */
@Directive({ selector: '[tvEntra]' })
export class TvEntra {
  readonly tvEntra = input('');
  readonly tvRetardo = input(0);
  readonly tvPaso = input(40);
  readonly tvTope = input(320);

  private readonly el = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;

  constructor() {
    if (typeof IntersectionObserver === 'undefined' || movimientoReducido()) return;
    // Se marca antes del primer pintado: la entrada CSS arranca ya en pausa.
    this.el.setAttribute('data-tv-espera', '');
    antena.observar(this.el, this);
    inject(DestroyRef).onDestroy(() => antena.dejar(this.el));
  }

  /** La suelta el observador: `indice` es su puesto dentro de su grupo en la tanda. */
  entrar(indice: number, alto: number): void {
    const t = this.tvRetardo() + Math.min(indice * this.tvPaso(), this.tvTope());
    this.el.style.setProperty('--tv-t', `${t}ms`);
    if (alto > 0) this.el.style.setProperty('--tv-sesgo', `${(alto * TAN_SKEW).toFixed(1)}px`);
    this.el.removeAttribute('data-tv-espera');
  }
}

/** easeOutExpo, la misma familia que `--ease-out-expo` de styles.scss. */
function expo(p: number): number {
  return p >= 1 ? 1 : 1 - Math.pow(2, -10 * p);
}

/** Vigía compartido para las cifras: avisa una vez cuando un elemento asoma en pantalla. */
const vigilados = new Map<Element, () => void>();
let vigia: IntersectionObserver | null = null;

function cuandoVisible(el: Element, aviso: () => void): () => void {
  if (typeof IntersectionObserver === 'undefined') {
    aviso();
    return () => undefined;
  }
  vigia ??= new IntersectionObserver(
    (entradas) => {
      for (const e of entradas) {
        if (!e.isIntersecting) continue;
        const cb = vigilados.get(e.target);
        vigilados.delete(e.target);
        vigia?.unobserve(e.target);
        cb?.();
      }
    },
    { rootMargin: '0px 0px -24px 0px' },
  );
  vigilados.set(el, aviso);
  vigia.observe(el);
  return () => {
    vigilados.delete(el);
    vigia?.unobserve(el);
  };
}

/**
 * Cifra que cuenta hasta su valor, como el rótulo de un marcador. Escribe el texto del
 * elemento (déjalo vacío en la plantilla) con el formateador que se le pase, que debe venir de
 * `core/format.ts`.
 *
 * ```html
 * <span [tvCifra]="slip.totalOdds()" [tvFormato]="fmtCuota"></span>
 * <span [tvCifra]="matches().length" [tvFormato]="fmtEntero" [tvDesde]="0"></span>
 * ```
 * - Primer valor: se pinta tal cual, salvo que `tvDesde` diga desde dónde contar; en ese caso
 *   se queda en `tvDesde` hasta que la cifra se ve (no cuenta debajo del pliegue).
 * - Cambios: cuenta desde lo que se ve (si llega otro valor a mitad, sigue desde ahí).
 * - `tvDuracion`: ms (600 por defecto). Con "reducir movimiento", salta al valor final.
 */
@Directive({ selector: '[tvCifra]' })
export class TvCifra {
  readonly tvCifra = input<string | number | null | undefined>(null);
  readonly tvFormato = input.required<(v: number | null) => string>();
  readonly tvDesde = input<number | null>(null);
  readonly tvDuracion = input(600);

  private readonly el = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  /** Lo que se ve ahora mismo (null = aún nada). */
  private visto: number | null = null;
  private frame = 0;
  /** Primera cuenta aplazada hasta que la cifra asoma en pantalla. */
  private esperando: (() => void) | null = null;

  constructor() {
    effect(() => {
      const crudo = this.tvCifra();
      const formato = this.tvFormato();
      untracked(() => this.contar(crudo == null || crudo === '' ? null : toNumber(crudo), formato));
    });
    inject(DestroyRef).onDestroy(() => {
      cancelAnimationFrame(this.frame);
      this.esperando?.();
    });
  }

  private contar(destino: number | null, formato: (v: number | null) => string): void {
    cancelAnimationFrame(this.frame);
    const esperaba = this.esperando != null;
    this.esperando?.();
    this.esperando = null;
    const inicial = this.tvDesde();
    if (
      (this.visto == null || esperaba) &&
      inicial != null &&
      destino != null &&
      !movimientoReducido()
    ) {
      // Primera cuenta: se enseña el punto de partida y se cuenta al verse.
      this.pintar(inicial, formato);
      this.esperando = cuandoVisible(this.el, () => {
        this.esperando = null;
        this.contar(destino, formato);
      });
      return;
    }
    const desde = this.visto ?? inicial;
    if (
      destino == null ||
      desde == null ||
      desde === destino ||
      !Number.isFinite(destino) ||
      typeof requestAnimationFrame !== 'function' ||
      movimientoReducido()
    ) {
      this.pintar(destino, formato);
      return;
    }
    const duracion = Math.max(1, this.tvDuracion());
    // El reloj es el de requestAnimationFrame desde su primer fotograma (no performance.now):
    // así la cuenta va con el resto de animaciones aunque la línea de tiempo vaya a otro ritmo.
    let inicio: number | null = null;
    this.pintar(desde, formato);
    const paso = (ahora: number) => {
      inicio ??= ahora;
      const p = Math.min(1, Math.max(0, (ahora - inicio) / duracion));
      this.pintar(p >= 1 ? destino : desde + (destino - desde) * expo(p), formato);
      if (p < 1) this.frame = requestAnimationFrame(paso);
    };
    this.frame = requestAnimationFrame(paso);
  }

  private pintar(v: number | null, formato: (v: number | null) => string): void {
    this.visto = v;
    this.el.textContent = formato(v);
  }
}

/**
 * Reordenación animada (FLIP): mide las piezas `selector` de `raiz` (cada una con un
 * `data-flip` único), aplica `mutar()` y, tras el siguiente pintado, desliza cada pieza desde
 * su sitio viejo al nuevo (220 ms). Para ordenar tablas: el ojo sigue a cada fila. Las piezas
 * nuevas aparecen sin más; con "reducir movimiento", cambio seco.
 */
export function flip(
  raiz: HTMLElement | null | undefined,
  selector: string,
  mutar: () => void,
  injector: Injector,
): void {
  if (!raiz || movimientoReducido()) {
    mutar();
    return;
  }
  const antes = new Map<string, number>();
  raiz.querySelectorAll<HTMLElement>(selector).forEach((el) => {
    const k = el.dataset['flip'];
    if (k) antes.set(k, el.getBoundingClientRect().top);
  });
  mutar();
  afterNextRender(
    {
      read: () => {
        raiz.querySelectorAll<HTMLElement>(selector).forEach((el) => {
          const viejo = antes.get(el.dataset['flip'] ?? '');
          if (viejo == null || typeof el.animate !== 'function') return;
          const dy = viejo - el.getBoundingClientRect().top;
          if (Math.abs(dy) < 1) return;
          el.animate([{ transform: `translateY(${dy}px)` }, { transform: 'translateY(0)' }], {
            duration: 220,
            easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
          });
        });
      },
    },
    { injector },
  );
}
