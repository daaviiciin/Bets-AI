/**
 * Ventaja de una cuota de Winamax frente al resto del mercado (comparador de Flashscore).
 *
 * El backend solo manda `mercado` cuando Winamax paga más que el consenso sin margen de las
 * demás casas y el detector no lo da por ruido. Baremo del usuario (18/11/2026): de 3 a 15 %
 * es ventaja; por encima, posible error de cuota (Winamax puede anular la apuesta).
 */
import { fmtAgo, fmtCuota, fmtProb, fmtVentaja } from './format';
import type { GradedOddsLine, VentajaMercado } from './models';

export type MarcaMercado = 'ventaja' | 'error';

/** Qué marca lleva la línea: `ventaja` (3–15 %), `error` (> 15 %) o ninguna. */
export function marcaMercado(l: GradedOddsLine | null | undefined): MarcaMercado | null {
  const m = l?.mercado;
  if (!m) return null;
  return m.clase === 'error_probable' ? 'error' : 'ventaja';
}

/** Rótulo corto junto a la P del modelo: "Mercado +6,3 %" / "¿Error? +22,0 %". */
export function rotuloMercado(m: VentajaMercado, compacto = false): string {
  const v = fmtVentaja(m.ventaja);
  if (compacto) return `${m.clase === 'error_probable' ? '¿Error?' : '▲'} ${v}`;
  return `${m.clase === 'error_probable' ? '¿Error?' : 'Mercado'} ${v}`;
}

/** Frase para el `title` y el nombre accesible de la línea. */
export function textoMercado(m: VentajaMercado, now = Date.now()): string {
  const base =
    `${fmtVentaja(m.ventaja)} sobre el mercado: cuota justa ${fmtCuota(m.cuota_justa)} ` +
    `(P ${fmtProb(m.p_mercado)}, consenso de ${m.casas} casas, leído ${fmtAgo(m.leido_at, now)})`;
  if (m.clase === 'error_probable') {
    return `${base}; tan por encima que puede ser un error de cuota y Winamax puede anularla`;
  }
  if (m.clase === 'surebet') return `${base}; con las demás casas cubre todos los resultados`;
  return base;
}
