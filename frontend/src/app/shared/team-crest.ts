import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import { readableOn } from '../core/team-colors';

/** Escudo del club; si no hay imagen (o falla), sus iniciales sobre su color. */
@Component({
  selector: 'app-team-crest',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'inline-flex shrink-0 items-center justify-center' },
  template: `
    @if (logo() && !failed()) {
      <img
        [src]="logo()!"
        [alt]="'Escudo de ' + name()"
        [width]="size()"
        [height]="size()"
        loading="lazy"
        decoding="async"
        class="object-contain"
        [style.width.px]="size()"
        [style.height.px]="size()"
        (error)="failed.set(true)" />
    } @else {
      <span
        class="grid place-items-center rounded-full font-display font-bold"
        [style.width.px]="size()"
        [style.height.px]="size()"
        [style.background]="color()"
        [style.color]="ink()"
        [style.font-size.px]="size() * 0.38"
        [attr.aria-label]="'Escudo de ' + name()"
        role="img">
        {{ initials() }}
      </span>
    }
  `,
})
export class TeamCrest {
  readonly name = input.required<string>();
  readonly logo = input<string | null | undefined>(null);
  readonly color = input('#26365c');
  readonly size = input(28);

  protected readonly failed = signal(false);
  protected readonly ink = computed(() => readableOn(this.color()));
  protected readonly initials = computed(() =>
    this.name()
      .replace(/\b(FC|CF|AC|AS|SC|SV|UD|CD|RC|SSC|TSG|VfB|OGC)\b/g, '')
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0])
      .join('')
      .toUpperCase(),
  );
}
