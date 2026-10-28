import { DestroyRef, Directive, ElementRef, afterNextRender, inject } from '@angular/core';
import { gsap } from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

import { movimientoReducido } from '../tv-motion';

/** Bancos de niebla sobre la sección; más grandes que las tarjetas para que no se vean bordes. */
const N_BANCOS = 14;
/** Cuánto sobresale la niebla de la sección. */
const DESBORDE = 90;
/** Separación entre una tarjeta y la siguiente al condensarse (s). */
const PASO = 0.24;

/**
 * Los picks salen de la niebla del fondo: la bruma se espesa sobre la sección, cada tarjeta
 * (`[data-niebla]`) se condensa del humo (desenfocada → nítida) y un foco la ilumina cuando
 * aparece; después la niebla se aclara. Una vez, cuando la sección asoma en pantalla. Con
 * "reducir movimiento", las tarjetas están en su sitio desde el principio.
 */
@Directive({ selector: '[tvNiebla]' })
export class TvNiebla {
  private readonly el = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;

  constructor() {
    const destroyRef = inject(DestroyRef);
    afterNextRender(() => {
      if (movimientoReducido()) return;
      gsap.registerPlugin(ScrollTrigger);
      const tarjetas = [...this.el.querySelectorAll<HTMLElement>('[data-niebla]')];
      gsap.set(tarjetas, { autoAlpha: 0 });
      let tl: gsap.core.Timeline | null = null;
      const st = ScrollTrigger.create({
        trigger: this.el,
        start: 'top 80%',
        // onToggle y no onEnter: si se vuelve a la portada ya desplazado por debajo de los
        // picks, emergen al subir hasta ellos (onEnter no saltaría y se quedarían ocultos).
        onToggle: (self) => {
          if (!self.isActive) return;
          self.kill();
          tl = this.emerger(tarjetas);
        },
      });
      destroyRef.onDestroy(() => {
        st.kill();
        tl?.progress(1).kill();
      });
    });
  }

  private emerger(tarjetas: HTMLElement[]): gsap.core.Timeline {
    const caja = this.el.getBoundingClientRect();
    const capa = document.createElement('div');
    capa.setAttribute('aria-hidden', 'true');
    Object.assign(capa.style, {
      position: 'absolute',
      inset: `${-DESBORDE}px`,
      pointerEvents: 'none',
      zIndex: '10',
      overflow: 'hidden',
    });

    const W = caja.width + DESBORDE * 2;
    const H = caja.height + DESBORDE * 2;
    const bancos = Array.from({ length: N_BANCOS }, () => {
      const b = document.createElement('div');
      const lado = gsap.utils.random(240, 480);
      Object.assign(b.style, {
        position: 'absolute',
        left: `${gsap.utils.random(-lado / 3, W - (lado * 2) / 3)}px`,
        top: `${gsap.utils.random(-lado / 3, H - (lado * 2) / 3)}px`,
        width: `${lado * 1.6}px`,
        height: `${lado}px`,
        borderRadius: '50%',
        background:
          'radial-gradient(closest-side, rgb(150 170 215 / 0.34), rgb(120 140 190 / 0.12) 60%, transparent)',
        filter: 'blur(18px)',
      });
      capa.append(b);
      return b;
    });

    // Foco: una elipse de luz que salta a cada tarjeta en el momento en que se condensa.
    const foco = document.createElement('div');
    Object.assign(foco.style, {
      position: 'absolute',
      left: '0',
      top: '0',
      width: '420px',
      height: '240px',
      marginLeft: '-210px',
      marginTop: '-120px',
      borderRadius: '50%',
      background:
        'radial-gradient(closest-side, rgb(220 230 255 / 0.3), rgb(127 156 245 / 0.1) 55%, transparent)',
      mixBlendMode: 'screen',
    });
    capa.append(foco);

    this.el.style.position = 'relative';
    this.el.append(capa);

    // Orden de aparición al azar: la niebla no revela de izquierda a derecha, revela donde se abre.
    const orden = gsap.utils.shuffle([...tarjetas]);
    const centro = (t: HTMLElement) => {
      const r = t.getBoundingClientRect();
      return {
        x: r.left - caja.left + r.width / 2 + DESBORDE,
        y: r.top - caja.top + r.height / 2 + DESBORDE,
      };
    };

    const fin = () => {
      capa.remove();
      this.el.style.removeProperty('position');
      gsap.set(tarjetas, { clearProps: 'all' });
    };

    const tl = gsap.timeline({ onComplete: fin });
    // La bruma se espesa y deriva despacio; luego se aclara mientras termina la última tarjeta.
    tl.fromTo(
      bancos,
      { autoAlpha: 0, scale: 0.7 },
      {
        autoAlpha: 1,
        scale: 1,
        duration: 0.9,
        ease: 'power2.out',
        stagger: { each: 0.03, from: 'random' },
      },
      0,
    );
    tl.to(
      bancos,
      {
        x: () => gsap.utils.random(-90, 90),
        y: () => gsap.utils.random(-25, 25),
        duration: 4,
        ease: 'sine.inOut',
      },
      0,
    );
    const ultima = 0.55 + PASO * (orden.length - 1);
    tl.to(
      bancos,
      {
        autoAlpha: 0,
        duration: 1.6,
        ease: 'power1.inOut',
        stagger: { each: 0.04, from: 'random' },
      },
      ultima + 0.3,
    );

    orden.forEach((t, i) => {
      const en = 0.55 + PASO * i;
      const c = centro(t);
      tl.set(foco, { x: c.x, y: c.y }, en)
        .fromTo(
          foco,
          { autoAlpha: 0, scale: 0.6 },
          { autoAlpha: 1, scale: 1, duration: 0.25, ease: 'power2.out' },
          en,
        )
        .fromTo(
          t,
          { autoAlpha: 0, filter: 'blur(14px)', scale: 1.06, y: 8 },
          { autoAlpha: 1, filter: 'blur(0px)', scale: 1, y: 0, duration: 1.1, ease: 'power2.out' },
          en,
        );
    });
    tl.to(foco, { autoAlpha: 0, duration: 0.6, ease: 'power1.in' }, ultima + 0.35);
    return tl;
  }
}
