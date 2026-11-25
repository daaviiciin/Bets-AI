/**
 * Combinada del partido (B14): estado de sus patas y acierto de los props, para la tarjeta de la
 * portada y la sección de Transparencia. También el acierto de las simples con y sin props
 * (reglas v5). Funciones puras (sin formateo de cifras: eso va en `format.ts` o en la plantilla).
 */

import type { FamiliaProp, FeaturedPicksMetrics, MatchComboMetrics, PickCounts } from './models';

/** Resultado de una pata o combinada (`grade_single_leg` / `overall_from_leg_results`). */
export const ESTADO_RESULTADO: Record<string, string> = {
  hit: 'Acierto',
  miss: 'Fallo',
  needs_review: 'A revisar',
  pending: 'Pendiente',
  // Partido terminado, esperando stats: se ve como pendiente.
  pending_stats: 'Pendiente',
  void: 'Nula',
};

export function estadoLabel(result: string | null | undefined): string {
  return ESTADO_RESULTADO[result ?? 'pending'] ?? 'Pendiente';
}

/** ¿Tiene ya un resultado que enseñar (acierto, fallo, nula o a revisar)? */
export function tieneResultado(result: string | null | undefined): boolean {
  return result != null && result !== 'pending' && result !== 'pending_stats';
}

const plural = (n: number, uno: string, varios: string) => `${n} ${n === 1 ? uno : varios}`;

/**
 * Cómo van las patas: "2 de 3 acertadas · 1 pendiente". `null` si aún no hay ninguna con
 * resultado (no se enseña nada mientras se juega).
 */
export function resumenPatas(legs: readonly { result?: string | null }[]): string | null {
  const res = legs.map((l) => l.result ?? 'pending');
  if (!res.some(tieneResultado)) return null;
  const hits = res.filter((r) => r === 'hit').length;
  const decididas = res.filter((r) => r === 'hit' || r === 'miss').length;
  const nulas = res.filter((r) => r === 'void').length;
  const revisar = res.filter((r) => r === 'needs_review').length;
  const pendientes = res.filter((r) => !tieneResultado(r)).length;
  const partes: string[] = [];
  if (decididas) partes.push(`${hits} de ${plural(decididas, 'acertada', 'acertadas')}`);
  if (nulas) partes.push(plural(nulas, 'nula', 'nulas'));
  if (revisar) partes.push(`${revisar} a revisar`);
  if (pendientes) partes.push(plural(pendientes, 'pendiente', 'pendientes'));
  return partes.join(' · ');
}

/** Familias de props en el orden de preferencia del usuario (tiros a puerta > goleador > paradas). */
const FAMILIAS: readonly { familia: FamiliaProp; label: string }[] = [
  { familia: 'player_sot', label: 'Tiros a puerta' },
  { familia: 'goleador', label: 'Goleador' },
  { familia: 'paradas', label: 'Paradas' },
  { familia: 'otros', label: 'Otros props' },
];

export interface FilaProp extends PickCounts {
  familia: FamiliaProp;
  label: string;
}

/**
 * Métricas de la combinada del partido, o `null` si el backend no las trae (`{}` o un backend
 * anterior al B14).
 */
export function metricasCombinadaPartido(
  m: FeaturedPicksMetrics | null | undefined,
): MatchComboMetrics | null {
  const cp = m?.combinada_partido;
  return cp && typeof cp.total === 'number' ? (cp as MatchComboMetrics) : null;
}

const esCuenta = (c: PickCounts | Record<string, never> | undefined): c is PickCounts =>
  !!c && typeof c.hits === 'number';

const total = (c: PickCounts) => c.hits + c.misses + c.pending + c.needs_review + c.voids;

/**
 * Acierto de las simples de props (experimentales, desde las reglas v5) y del resto, o `null`
 * mientras no haya ninguna simple de props (o el backend no traiga el desglose).
 */
export function simplesPorProps(
  m: FeaturedPicksMetrics | null | undefined,
): { con: PickCounts; sin: PickCounts } | null {
  const con = m?.simples_con_props;
  const sin = m?.simples_sin_props;
  if (!esCuenta(con) || !esCuenta(sin) || total(con) === 0) return null;
  return { con, sin };
}

/** Acierto de las patas de props por familia, en orden de preferencia; solo las que hay. */
export function filasProps(m: MatchComboMetrics | null): FilaProp[] {
  if (!m) return [];
  return FAMILIAS.flatMap(({ familia, label }) => {
    const c = m.patas_props?.[familia];
    return c ? [{ familia, label, ...c }] : [];
  });
}
