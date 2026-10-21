import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { LineupPlayer, TeamLineup } from '../../core/models';
import type { TeamPaint } from '../../core/team-colors';
import { PlayerPhoto } from './player-photo';

interface Dot {
  player: LineupPlayer;
  x: number;
  y: number;
  home: boolean;
}

/** Profundidad de cada línea (en % del ancho, desde la portería propia). */
const LINE_X: Record<string, number> = { G: 7, D: 19, M: 31, F: 43 };

function place(lineup: TeamLineup, home: boolean): Dot[] {
  const lines = new Map<string, LineupPlayer[]>();
  for (const p of lineup.starters) {
    const key = p.position && LINE_X[p.position] != null ? p.position : 'M';
    lines.set(key, [...(lines.get(key) ?? []), p]);
  }
  const dots: Dot[] = [];
  for (const [key, players] of lines) {
    players.forEach((player, i) => {
      const x = LINE_X[key];
      dots.push({
        player,
        x: home ? x : 100 - x,
        y: ((i + 1) / (players.length + 1)) * 100,
        home,
      });
    });
  }
  return dots;
}

/**
 * Campo visto desde arriba con los dos onces: local a la izquierda, visitante a la derecha.
 * ``compact`` lo inclina en perspectiva y oculta nombres y fotos (pestaña Previa): a 16 px una
 * cara no se reconoce y el resumen se lee mejor con el color de cada club.
 * Ficha completa: foto del jugador (o su posición si no hay). Baja: aro rojo y foto en gris.
 * Duda: aro discontinuo ámbar.
 *
 * Entrada: el césped barre con el canto a −8°, las líneas del campo se trazan y las fichas
 * aparecen de cada portería hacia el centro (de arriba abajo en cada línea); los nombres
 * suben detrás de su ficha. Todo cabe en < 900 ms y respeta "reducir movimiento".
 */
@Component({
  selector: 'app-pitch',
  imports: [PlayerPhoto],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .cesped {
      background:
        repeating-linear-gradient(90deg, #14532d 0 9.0909%, #166534 9.0909% 18.1818%);
    }
    .cesped {
      animation: tv-barrido 460ms var(--ease-out-expo) var(--tv-t) backwards;
    }
    .lineas > * {
      stroke-dasharray: 1;
      animation: tv-dibuja 700ms var(--ease-out-expo) calc(var(--tv-t) + 160ms) backwards;
    }
    .perspectiva {
      transform: perspective(900px) rotateX(32deg);
      transform-origin: center bottom;
      /* La rotación encoge el dibujo pero no su caja: se recupera el hueco de arriba. La caja
         invisible sube sobre lo anterior, así que no recibe clics (los nombres están en la
         pestaña Alineaciones). */
      margin-top: -12%;
      pointer-events: none;
    }
  `,
  template: `
    <div [class.perspectiva]="compact()" [class.pt-2]="compact()">
      <div
        class="cesped relative w-full overflow-hidden"
        [style.aspect-ratio]="compact() ? '2 / 1' : '105 / 68'"
        [style.--tv-sesgo]="compact() ? '7%' : '9.1%'"
        role="img"
        [attr.aria-label]="label()">
        <svg
          class="lineas absolute inset-0 h-full w-full"
          viewBox="0 0 105 68"
          preserveAspectRatio="none"
          fill="none"
          stroke="rgb(255 255 255 / 0.55)"
          stroke-width="0.35"
          aria-hidden="true">
          <rect pathLength="1" x="1" y="1" width="103" height="66" />
          <line pathLength="1" x1="52.5" y1="1" x2="52.5" y2="67" />
          <circle pathLength="1" cx="52.5" cy="34" r="9.15" />
          <rect pathLength="1" x="1" y="13.85" width="16.5" height="40.3" />
          <rect pathLength="1" x="87.5" y="13.85" width="16.5" height="40.3" />
          <rect pathLength="1" x="1" y="24.85" width="5.5" height="18.3" />
          <rect pathLength="1" x="98.5" y="24.85" width="5.5" height="18.3" />
        </svg>
        @for (d of dots(); track $index) {
          <div
            class="absolute flex -translate-x-1/2 -translate-y-1/2 flex-col items-center"
            [style.left.%]="d.x"
            [style.top.%]="d.y"
            [attr.title]="d.player.name">
            <span
              class="tv-ficha grid place-items-center overflow-hidden rounded-full border-2 text-[10px] font-bold"
              [style.animation-delay]="'calc(var(--tv-t) + ' + entrada(d) + 'ms)'"
              [class.size-4]="compact()"
              [class.size-8]="!compact()"
              [style.background]="d.home ? homePaint().accent : awayPaint().accent"
              [style.color]="d.home ? homePaint().fg : awayPaint().fg"
              [style.border-color]="ring(d.player)"
              [style.border-style]="d.player.status === 'doubtful' ? 'dashed' : 'solid'">
              @if (!compact()) {
                <!-- Foto del jugador sobre el color del club; sin foto, su posición. -->
                <app-player-photo
                  class="size-full"
                  [class.grayscale]="d.player.status === 'missing'"
                  [name]="d.player.name"
                  [src]="d.player.photo_url"
                  [fallback]="d.player.position ?? ''" />
              }
            </span>
            @if (!compact()) {
              <span
                class="tv-sube mt-1 max-w-[min(112px,13vw)] truncate text-center text-[11px] font-semibold leading-tight text-white"
                [style.animation-delay]="'calc(var(--tv-t) + ' + (entrada(d) + 120) + 'ms)'"
                [class.line-through]="d.player.status === 'missing'"
                [class.opacity-60]="d.player.status === 'missing'"
                style="text-shadow: 0 1px 2px rgb(0 0 0 / 0.7)">
                {{ d.player.name }}
              </span>
            }
          </div>
        }
      </div>
    </div>
  `,
})
export class Pitch {
  readonly home = input.required<TeamLineup>();
  readonly away = input.required<TeamLineup>();
  readonly homePaint = input.required<TeamPaint>();
  readonly awayPaint = input.required<TeamPaint>();
  readonly compact = input(false);

  protected readonly dots = computed(() => [
    ...place(this.home(), true),
    ...place(this.away(), false),
  ]);

  protected readonly label = computed(() => {
    const status = (p: LineupPlayer) =>
      p.status === 'missing' ? ' (baja)' : p.status === 'doubtful' ? ' (duda)' : '';
    const names = (l: TeamLineup) => l.starters.map((p) => p.name + status(p)).join(', ');
    return `Local: ${names(this.home()) || 'sin alineación'}. Visitante: ${names(this.away()) || 'sin alineación'}.`;
  });

  /**
   * Retardo de cada ficha (ms): de su portería hacia el centro (6 ms por % de profundidad) y,
   * dentro de una línea, de arriba abajo. Como mucho ~520 ms.
   */
  protected entrada(d: Dot): number {
    const fondo = (d.home ? d.x : 100 - d.x) - LINE_X['G'];
    return Math.round(260 + fondo * 6 + d.y * 0.4);
  }

  protected ring(p: LineupPlayer): string {
    if (p.status === 'missing') return 'var(--color-tv-alert)';
    if (p.status === 'doubtful') return 'var(--color-tv-doubt)';
    return 'rgb(255 255 255 / 0.85)';
  }
}
