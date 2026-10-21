import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/** Iconos propios: un solo trazo (1,75) y punta redonda, dibujados a 24×24. */
const PATHS = {
  'chevron-left': 'M15 18l-6-6 6-6',
  'chevron-right': 'M9 18l6-6-6-6',
  x: 'M18 6 6 18M6 6l12 12',
  check: 'M5 12.5l4.5 4.5L19 7',
  plus: 'M12 5v14M5 12h14',
  refresh: 'M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5',
  ticket:
    'M4 8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v1.5a2.5 2.5 0 0 0 0 5V16a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-1.5a2.5 2.5 0 0 0 0-5Z M14 6v2M14 11v2M14 16v2',
  alert: 'M12 4 21 20H3Z M12 10v4 M12 17h.01',
  trash: 'M4 7h16 M10 11v6 M14 11v6 M6 7l1 13h10l1-13 M9 7V4h6v3',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z M12 7v5l3 2',
  lock: 'M6 11h12v9H6Z M8.5 11V8a3.5 3.5 0 0 1 7 0v3',
  shirt: 'M8 4 4 7l2 4 2-1v10h8V10l2 1 2-4-4-3c-.5 1.5-2 2.5-4 2.5S8.5 5.5 8 4Z',
} as const;

export type IconName = keyof typeof PATHS;

@Component({
  selector: 'app-icon',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'inline-flex shrink-0', 'aria-hidden': 'true' },
  template: `
    <svg
      [attr.width]="size()"
      [attr.height]="size()"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.75"
      stroke-linecap="round"
      stroke-linejoin="round">
      <!-- pathLength="1": permite trazar el icono al entrar (clase .tv-dibuja de styles.scss). -->
      <path pathLength="1" [attr.d]="d()" />
    </svg>
  `,
})
export class Icon {
  readonly name = input.required<IconName>();
  readonly size = input(18);
  protected readonly d = computed(() => PATHS[this.name()]);
}
