import { DestroyRef, Directive, ElementRef, afterNextRender, inject } from '@angular/core';
import { gsap } from 'gsap';

import { movimientoReducido } from '../tv-motion';

/** Por qué se va a romper el logo: al abrir la app o porque lo han pulsado. */
export type MotivoRotura = 'arranque' | 'clic';

const CLAVE_SESION = 'bets-ai:logo-roto';

/**
 * Decide si el logo hace su número. Romperlo en cada recarga cansa pronto; no romperlo nunca
 * desaprovecha el efecto. `vecesEnSesion` cuenta las roturas ya vistas en esta pestaña.
 */
export function debeRomperse(motivo: MotivoRotura, vecesEnSesion: number): boolean {
  // TODO(tú): decide la política. Ahora mismo se rompe siempre.
  void motivo;
  void vecesEnSesion;
  return true;
}

/** Margen alrededor del logo (en em del rótulo): el "AI" inclinado y la cursiva asoman. */
const MARGEN_EM = 0.3;
/** Lado aproximado de cada trozo (px): el rótulo grande se rompe en más pedazos que uno pequeño. */
const LADO_TROZO = 46;

interface Trozo {
  /** Polígono en px dentro de la capa (caja del logo + MARGEN por lado). */
  puntos: [number, number][];
  cx: number;
  cy: number;
}

/**
 * Malla de triángulos sobre la caja: rejilla con los puntos interiores desplazados al azar
 * (los del borde solo se mueven a lo largo del borde, así la unión de trozos cubre la caja
 * entera) y cada celda partida en dos por una diagonal que alterna.
 */
function trocear(w: number, h: number): Trozo[] {
  const COLS = gsap.utils.clamp(5, 14, Math.round(w / LADO_TROZO));
  const FILAS = gsap.utils.clamp(3, 6, Math.round(h / LADO_TROZO));
  const pts: [number, number][][] = [];
  for (let j = 0; j <= FILAS; j++) {
    const fila: [number, number][] = [];
    for (let i = 0; i <= COLS; i++) {
      const jx = i > 0 && i < COLS ? (Math.random() - 0.5) * (w / COLS) * 0.7 : 0;
      const jy = j > 0 && j < FILAS ? (Math.random() - 0.5) * (h / FILAS) * 0.7 : 0;
      fila.push([(i / COLS) * w + jx, (j / FILAS) * h + jy]);
    }
    pts.push(fila);
  }
  const trozos: Trozo[] = [];
  const tri = (a: [number, number], b: [number, number], c: [number, number]) =>
    trozos.push({ puntos: [a, b, c], cx: (a[0] + b[0] + c[0]) / 3, cy: (a[1] + b[1] + c[1]) / 3 });
  for (let j = 0; j < FILAS; j++) {
    for (let i = 0; i < COLS; i++) {
      const [a, b, c, d] = [pts[j][i], pts[j][i + 1], pts[j + 1][i + 1], pts[j + 1][i]];
      if ((i + j) % 2) {
        tri(a, b, c);
        tri(a, c, d);
      } else {
        tri(a, b, d);
        tri(b, c, d);
      }
    }
  }
  return trozos;
}

/**
 * El rótulo grande "Bets AI" (login y portada) cae desde la barra, se hace pedazos contra su
 * sitio y se recompone.
 * Los pedazos son copias del logo recortadas con clip-path (texto real, nítido a cualquier
 * zoom); el original se oculta mientras dura y vuelve intacto al final.
 */
@Directive({
  selector: '[tvLogoRoto]',
  host: { '(click)': 'alPulsar()' },
})
export class TvLogoRoto {
  private readonly el = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private tl: gsap.core.Timeline | null = null;

  constructor() {
    afterNextRender(() => this.romper('arranque'));
    inject(DestroyRef).onDestroy(() => this.tl?.progress(1).kill());
  }

  protected alPulsar(): void {
    this.romper('clic');
  }

  private romper(motivo: MotivoRotura): void {
    if (this.tl?.isActive() || movimientoReducido()) return;
    let veces = 0;
    try {
      veces = Number(sessionStorage.getItem(CLAVE_SESION) ?? 0);
    } catch {
      /* sin almacenamiento: cuenta como primera vez */
    }
    if (!debeRomperse(motivo, veces)) return;
    try {
      sessionStorage.setItem(CLAVE_SESION, String(veces + 1));
    } catch {
      /* da igual */
    }

    const caja = this.el.getBoundingClientRect();
    const estilo = getComputedStyle(this.el);
    const MARGEN = Math.round(parseFloat(estilo.fontSize) * MARGEN_EM);
    // Fuerza del estallido proporcional al tamaño: el rótulo de 9rem vuela más lejos.
    const k = caja.height / 30;
    const w = caja.width + MARGEN * 2;
    const h = caja.height + MARGEN * 2;
    const originales = [...this.el.children] as HTMLElement[];

    const capa = document.createElement('span');
    capa.setAttribute('aria-hidden', 'true');
    Object.assign(capa.style, {
      position: 'absolute',
      left: `${-MARGEN}px`,
      top: `${-MARGEN}px`,
      width: `${w}px`,
      height: `${h}px`,
      pointerEvents: 'none',
      perspective: '400px',
    });

    const trozos = trocear(w, h).map((t) => {
      const pieza = document.createElement('span');
      Object.assign(pieza.style, {
        position: 'absolute',
        inset: '0',
        display: 'flex',
        alignItems: 'center',
        gap: estilo.columnGap,
        padding: `${MARGEN}px`,
        clipPath: `polygon(${t.puntos.map(([x, y]) => `${x}px ${y}px`).join(',')})`,
        transformOrigin: `${t.cx}px ${t.cy}px`,
        willChange: 'transform',
      });
      for (const o of originales) {
        const copia = o.cloneNode(true) as HTMLElement;
        // Las copias no repiten la entrada CSS del rótulo (tv-sube / tv-placa).
        for (const n of [copia, ...copia.querySelectorAll<HTMLElement>('*')])
          n.style.animation = 'none';
        pieza.append(copia);
      }
      capa.append(pieza);
      return { pieza, ...t };
    });

    // Filete del impacto: la línea de "suelo" que se traza bajo el logo al chocar.
    const golpe = document.createElement('span');
    Object.assign(golpe.style, {
      position: 'absolute',
      left: '0',
      right: '0',
      bottom: `${MARGEN * 0.6}px`,
      height: `${Math.max(2, Math.round(k * 1.5))}px`,
      background: 'var(--color-tv-ink)',
      transform: 'scaleX(0)',
    });
    capa.append(golpe);

    this.el.style.position = 'relative';
    this.el.append(capa);
    gsap.set(originales, { visibility: 'hidden' });

    // Punto de impacto: centro de la base. Cada trozo sale despedido alejándose de él.
    const ix = w / 2;
    const iy = h;
    const caida = caja.top + caja.height + 24;

    const fin = () => {
      capa.remove();
      gsap.set(originales, { clearProps: 'visibility' });
      this.el.style.removeProperty('position');
    };

    this.tl = gsap
      .timeline({ onComplete: fin, onInterrupt: fin })
      .fromTo(
        capa,
        { y: -caida, rotation: -6 },
        { y: 0, rotation: 0, duration: 0.55, ease: 'power3.in' },
      )
      .addLabel('golpe')
      .to(golpe, { scaleX: 1, duration: 0.25, ease: 'expo.out' }, 'golpe')
      .to(golpe, { opacity: 0, duration: 0.3 }, 'golpe+=0.15')
      .to(
        trozos.map((t) => t.pieza),
        {
          x: (i) => {
            const t = trozos[i];
            return (t.cx - ix) * (1.1 + Math.random() * 1.4) + (Math.random() - 0.5) * 30 * k;
          },
          y: (i) => {
            const t = trozos[i];
            return (t.cy - iy) * (1.4 + Math.random() * 1.6) - Math.random() * 26 * k;
          },
          rotation: () => (Math.random() - 0.5) * 300,
          rotationX: () => (Math.random() - 0.5) * 140,
          scale: () => 0.7 + Math.random() * 0.5,
          duration: 0.5,
          ease: 'power3.out',
        },
        'golpe',
      )
      // Gravedad: tras el estallido, los trozos caen un poco antes de volver.
      .to(
        trozos.map((t) => t.pieza),
        { y: `+=${22 * k}`, duration: 0.28, ease: 'power1.in' },
        'golpe+=0.42',
      )
      .to(
        trozos.map((t) => t.pieza),
        {
          x: 0,
          y: 0,
          rotation: 0,
          rotationX: 0,
          scale: 1,
          duration: 0.7,
          ease: 'expo.inOut',
          stagger: { amount: 0.4, from: 'random' },
        },
        'golpe+=0.82',
      )
      // Encaje: un latido seco cuando el último trozo vuelve a su sitio.
      .to(capa, { scale: 1.08, duration: 0.09, ease: 'power2.out' })
      .to(capa, { scale: 1, duration: 0.35, ease: 'elastic.out(1, 0.4)' });
  }
}
