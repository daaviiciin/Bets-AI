import { Injectable } from '@angular/core';
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import Lenis from 'lenis';

import { movimientoReducido } from '../tv-motion';

/**
 * Motor común de los efectos "experimento" (fondo, logo, duelo, niebla de los picks): GSAP marca el
 * reloj y Lenis suaviza el desplazamiento colgado de ese mismo reloj, así ScrollTrigger lee la
 * posición del scroll en el mismo fotograma en que Lenis la mueve. Con "reducir movimiento" no
 * se arranca Lenis: el scroll es el nativo.
 */
@Injectable({ providedIn: 'root' })
export class Movimiento {
  private lenis: Lenis | null = null;

  iniciar(): void {
    if (this.lenis || movimientoReducido()) return;
    // Se registra aquí y no al importar: ScrollTrigger toca matchMedia y en los tests no hay.
    gsap.registerPlugin(ScrollTrigger);
    this.lenis = new Lenis({ autoRaf: false, lerp: 0.12, wheelMultiplier: 0.9 });
    this.lenis.on('scroll', ScrollTrigger.update);
    gsap.ticker.add((t) => this.lenis?.raf(t * 1000));
    gsap.ticker.lagSmoothing(0);
  }

  /** Desplaza hasta un elemento; con Lenis activo, por él (un scrollIntoView nativo pelearía). */
  irA(el: HTMLElement, offset = -88): void {
    if (this.lenis) this.lenis.scrollTo(el, { offset, duration: 1.1 });
    else el.scrollIntoView({ behavior: 'auto', block: 'start' });
  }
}
