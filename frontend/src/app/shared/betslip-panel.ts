import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  computed,
  effect,
  inject,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { AuthService } from '../core/auth.service';
import { BetslipService } from '../core/betslip.service';
import { fmtCuota, fmtEuros, fmtEv, fmtTime, parseImporte } from '../core/format';
import { Icon } from './icon';
import { TvCifra, movimientoReducido } from './tv-motion';

/** tan(8°): el canto del barrido se inclina como los rótulos. */
const TAN_SKEW = 0.1405;

/**
 * Boletín lateral: siempre a mano, en todas las páginas. Al abrirse, sus selecciones entran
 * una tras otra desde la derecha mientras el cajón termina de llegar; las que se añaden o se
 * quitan con el cajón abierto entran y salen por su lado (styles.scss, bloque Boletín).
 */
@Component({
  selector: 'app-betslip-panel',
  imports: [FormsModule, RouterLink, Icon, TvCifra],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './betslip-panel.html',
})
export class BetslipPanel {
  protected readonly slip = inject(BetslipService);
  protected readonly auth = inject(AuthService);

  protected readonly title = signal('');
  /** Importe tal como se escribe ("12,50"); vacío = se guarda sin importe, solo para seguirla. */
  protected readonly stake = signal('');
  protected readonly stakeValue = computed(() => parseImporte(this.stake()));
  protected readonly stakeInvalid = computed(() => this.stake().trim() !== '' && !this.stakeValue());
  /** Lo que se cobraría si acierta (importe × cuota total). */
  protected readonly payout = computed(() => {
    const s = this.stakeValue();
    return s ? Number(s) * this.slip.totalOdds() : null;
  });
  protected readonly saving = signal(false);
  protected readonly saved = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly fmtCuota = fmtCuota;
  protected readonly fmtEuros = fmtEuros;
  protected readonly fmtEv = fmtEv;
  protected readonly fmtTime = fmtTime;

  private readonly lista = viewChild<ElementRef<HTMLElement>>('lista');

  constructor() {
    effect(() => {
      if (!this.slip.open()) return;
      untracked(() => this.entranSelecciones());
    });
  }

  @HostListener('document:keydown.escape')
  protected close(): void {
    this.slip.open.set(false);
  }

  protected save(): void {
    if (this.saving() || !this.slip.count() || this.stakeInvalid()) return;
    this.saving.set(true);
    this.error.set(null);
    this.slip.save(this.title(), this.stakeValue()).subscribe({
      next: () => {
        this.saving.set(false);
        this.saved.set(true);
        this.title.set('');
        this.stake.set('');
        this.slip.clear();
      },
      error: (err) => {
        this.saving.set(false);
        this.error.set(
          err?.status === 401
            ? 'Tu sesión ha caducado: entra de nuevo para guardarla.'
            : 'No se pudo guardar la apuesta. Revisa que la API esté arrancada y vuelve a intentarlo.',
        );
      },
    });
  }

  protected removeLeg(key: string): void {
    this.saved.set(false);
    this.slip.remove(key);
  }

  /**
   * Barrido de derecha a izquierda, escalonado 45 ms, que arranca cuando el cajón ya ha hecho
   * casi todo su recorrido (500 ms, ease-out exponencial). Web Animations y no una clase: así
   * no choca con las entradas de `animate.enter` ni se repite al re-pintarse la lista.
   */
  private entranSelecciones(): void {
    const ul = this.lista()?.nativeElement;
    if (!ul || typeof ul.animate !== 'function' || movimientoReducido()) return;
    [...ul.children].forEach((li, i) => {
      const s = `${((li as HTMLElement).offsetHeight * TAN_SKEW).toFixed(1)}px`;
      li.animate(
        [
          { clipPath: `polygon(calc(100% + ${s}) 0, calc(100% + ${s}) 0, calc(100% + ${s}) 100%, 100% 100%)` },
          { clipPath: `polygon(0 0, calc(100% + ${s}) 0, calc(100% + ${s}) 100%, -${s} 100%)` },
        ],
        {
          duration: 420,
          delay: 140 + Math.min(i, 8) * 45,
          easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
          fill: 'backwards',
        },
      );
    });
  }
}
