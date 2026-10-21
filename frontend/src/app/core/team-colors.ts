/**
 * Colores de los dos clubes de un partido, listos para pintar: fondo, texto legible encima
 * y un tono para barras y fichas. Si los dos visten parecido (dos rojos), el visitante pasa
 * a su color secundario, como en la tele.
 */

export interface TeamPaint {
  /** Color del club. */
  bg: string;
  /** Texto legible sobre ``bg`` (blanco o azul noche). */
  fg: string;
  /** Tono para barras, puntos y fichas sobre el fondo oscuro (nunca casi negro). */
  accent: string;
}

interface Colored {
  primary_color?: string | null;
  secondary_color?: string | null;
}

const NIGHT = '#0b1222';
const FALLBACK_HOME = '#3a5ba0';
const FALLBACK_AWAY = '#a33a5b';

function rgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}

function luminance(hex: string): number {
  const [r, g, b] = rgb(hex).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function distance(a: string, b: string): number {
  const [x, y] = [rgb(a), rgb(b)];
  return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]);
}

function valid(hex: string | null | undefined): hex is string {
  return !!hex && /^#[0-9a-f]{6}$/i.test(hex);
}

/** Texto que contrasta: azul noche sobre colores claros, blanco sobre oscuros. */
export function readableOn(bg: string): string {
  return luminance(bg) > 0.45 ? NIGHT : '#ffffff';
}

/** Color claro (blanco, amarillo): lleva texto oscuro y se funde con una placa blanca. */
export function isLight(color: string): boolean {
  return readableOn(color) === NIGHT;
}

function paint(bg: string, secondary: string | null): TeamPaint {
  // Un club negro (Juventus, Newcastle) no se ve como barra sobre azul noche: usa su
  // secundario (blanco) para barras y puntos.
  const accent = luminance(bg) < 0.03 && secondary ? secondary : bg;
  return { bg, fg: readableOn(bg), accent };
}

export function matchPaint(home: Colored, away: Colored): { home: TeamPaint; away: TeamPaint } {
  const h = valid(home.primary_color) ? home.primary_color : FALLBACK_HOME;
  let a = valid(away.primary_color) ? away.primary_color : FALLBACK_AWAY;
  const aSecondary = valid(away.secondary_color) ? away.secondary_color : null;
  if (distance(h, a) < 90 && aSecondary && distance(h, aSecondary) >= 90) {
    a = aSecondary;
  }
  const hSecondary = valid(home.secondary_color) ? home.secondary_color : null;
  return { home: paint(h, hSecondary), away: paint(a, a === aSecondary ? null : aSecondary) };
}
