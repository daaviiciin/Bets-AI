import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

/**
 * Foto circular de un jugador (enlazada al CDN de Flashscore). Sin foto, o si la imagen falla,
 * se ve ``fallback`` (la posición en el campo) o sus iniciales. El tamaño y los colores los pone
 * quien la usa con clases sobre el elemento (``size-6 bg-tv-raised``…).
 */
@Component({
  selector: 'app-player-photo',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    class: 'inline-grid shrink-0 place-items-center overflow-hidden rounded-full',
  },
  template: `
    @if (showImage()) {
      <img
        [src]="src()!"
        [alt]="name()"
        loading="lazy"
        decoding="async"
        referrerpolicy="no-referrer"
        class="size-full object-cover object-top"
        (error)="failedSrc.set(src() ?? null)" />
    } @else {
      <span class="font-display font-bold leading-none" aria-hidden="true">{{ fallback() ?? initials() }}</span>
    }
  `,
})
export class PlayerPhoto {
  readonly name = input.required<string>();
  readonly src = input<string | null | undefined>(null);
  /** Texto si no hay foto; por defecto, las iniciales del jugador. */
  readonly fallback = input<string | null>(null);

  /** La URL que falló: si llega otra (otro jugador en la misma ficha), se vuelve a intentar. */
  protected readonly failedSrc = signal<string | null>(null);
  protected readonly showImage = computed(() => !!this.src() && this.failedSrc() !== this.src());
  protected readonly initials = computed(() =>
    this.name()
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0])
      .join('')
      .toUpperCase(),
  );
}
