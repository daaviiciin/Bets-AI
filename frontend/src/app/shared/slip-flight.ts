import { ChangeDetectionStrategy, Component, ElementRef, effect, inject, viewChild } from '@angular/core';

import { BetslipService } from '../core/betslip.service';
import { fmtCuota } from '../core/format';

/**
 * La cuota pulsada viaja hasta el botón del boletín (momento firma del diseño).
 * Una ficha verde con la cuota sale del botón y aterriza en ``#boletin-boton``; con
 * "reducir movimiento" no se mueve: solo late el contador.
 */
@Component({
  selector: 'app-slip-flight',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'pointer-events-none fixed inset-0 z-50', 'aria-hidden': 'true' },
  template: `<span
    #chip
    class="rotulo absolute left-0 top-0 hidden -translate-x-1/2 -translate-y-1/2 bg-tv-go px-2.5 py-1 text-sm text-tv-night"></span>`,
})
export class SlipFlight {
  private readonly slip = inject(BetslipService);
  private readonly chip = viewChild.required<ElementRef<HTMLSpanElement>>('chip');

  constructor() {
    effect(() => {
      const flight = this.slip.flight();
      if (!flight) return;
      const target = document.getElementById('boletin-boton');
      const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
      // Late con `scale` (propiedad propia), no con `transform`: así no pisa la inclinación
      // del botón (`transform: skewX`) mientras dura el latido.
      target?.animate([{ scale: '1' }, { scale: '1.12' }, { scale: '1' }], {
        duration: 320,
        delay: reduce ? 0 : 520,
        easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
      });
      if (reduce || !target) return;
      const end = target.getBoundingClientRect();
      const el = this.chip().nativeElement;
      el.textContent = fmtCuota(flight.label);
      el.classList.remove('hidden');
      const dx = end.left + end.width / 2 - flight.x;
      const dy = end.top + end.height / 2 - flight.y;
      el.animate(
        [
          { transform: `translate(${flight.x}px, ${flight.y}px) translate(-50%, -50%) skewX(-8deg) scale(1)`, opacity: 1 },
          {
            transform: `translate(${flight.x + dx * 0.55}px, ${flight.y + dy * 0.55 - 40}px) translate(-50%, -50%) skewX(-8deg) scale(0.9)`,
            opacity: 1,
            offset: 0.6,
          },
          { transform: `translate(${flight.x + dx}px, ${flight.y + dy}px) translate(-50%, -50%) skewX(-8deg) scale(0.4)`, opacity: 0 },
        ],
        { duration: 560, easing: 'cubic-bezier(0.16, 1, 0.3, 1)', fill: 'forwards' },
      ).onfinish = () => el.classList.add('hidden');
    });
  }
}
