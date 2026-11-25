import {
  estadoLabel,
  filasProps,
  metricasCombinadaPartido,
  resumenPatas,
  simplesPorProps,
  tieneResultado,
} from './combinada-partido';
import { selectionLabel } from './markets';
import type { FeaturedPicksMetrics, MatchComboMetrics, PickCounts } from './models';

const cuenta = (hits: number, misses: number, extra: Partial<PickCounts> = {}): PickCounts => ({
  hits,
  misses,
  pending: 0,
  needs_review: 0,
  voids: 0,
  rate_pct: hits + misses ? Math.round((1000 * hits) / (hits + misses)) / 10 : null,
  ...extra,
});

const metricas = (cp: FeaturedPicksMetrics['combinada_partido']): FeaturedPicksMetrics => ({
  total_pick_days: 1,
  total_legs: 3,
  hits: 0,
  misses: 0,
  pending: 0,
  needs_review: 0,
  rate_pct: null,
  combinada_partido: cp,
});

describe('combinada del partido: estado de las patas', () => {
  it('sin ninguna pata con resultado no hay resumen', () => {
    expect(resumenPatas([{}, { result: 'pending' }, { result: 'pending_stats' }])).toBeNull();
  });

  it('cuenta acertadas sobre las decididas y deja aparte nulas y pendientes', () => {
    expect(resumenPatas([{ result: 'hit' }, { result: 'void' }, { result: 'pending' }])).toBe(
      '1 de 1 acertada · 1 nula · 1 pendiente',
    );
    expect(resumenPatas([{ result: 'hit' }, { result: 'miss' }, { result: 'hit' }])).toBe(
      '2 de 3 acertadas',
    );
  });

  it('las que esperan stats cuentan como pendientes y las dudosas, a revisar', () => {
    expect(
      resumenPatas([{ result: 'miss' }, { result: 'pending_stats' }, { result: 'needs_review' }]),
    ).toBe('0 de 1 acertada · 1 a revisar · 1 pendiente');
  });

  it('todas nulas', () => {
    expect(resumenPatas([{ result: 'void' }, { result: 'void' }])).toBe('2 nulas');
  });

  it('etiquetas en femenino (pata) y pendiente por defecto', () => {
    expect(estadoLabel('void')).toBe('Nula');
    expect(estadoLabel(undefined)).toBe('Pendiente');
    expect(estadoLabel('raro')).toBe('Pendiente');
    expect(tieneResultado('pending_stats')).toBe(false);
    expect(tieneResultado('needs_review')).toBe(true);
  });
});

describe('combinada del partido: métricas de Transparencia', () => {
  const cp: MatchComboMetrics = {
    total: 4,
    ...cuenta(2, 1, { pending: 1 }),
    con_props: cuenta(1, 1),
    sin_props: cuenta(1, 0),
    patas_props: {
      paradas: cuenta(0, 1),
      otros: cuenta(1, 0),
      player_sot: cuenta(2, 1, { pending: 1 }),
    },
  };

  it('sin métricas del backend (objeto vacío) devuelve null', () => {
    expect(metricasCombinadaPartido(metricas({}))).toBeNull();
    expect(metricasCombinadaPartido(metricas(undefined))).toBeNull();
    expect(metricasCombinadaPartido(null)).toBeNull();
  });

  it('con métricas las devuelve tal cual', () => {
    expect(metricasCombinadaPartido(metricas(cp))).toBe(cp);
  });

  it('props por familia en el orden de preferencia y solo las que hay', () => {
    const filas = filasProps(cp);
    expect(filas.map((f) => f.label)).toEqual(['Tiros a puerta', 'Paradas', 'Otros props']);
    expect(filas[0]).toMatchObject({ familia: 'player_sot', hits: 2, misses: 1, pending: 1 });
  });

  it('sin métricas no hay filas de props', () => {
    expect(filasProps(null)).toEqual([]);
  });
});

describe('simples con y sin props (reglas v5)', () => {
  it('sin desglose del backend o sin ninguna simple de props no hay nada que enseñar', () => {
    expect(simplesPorProps(null)).toBeNull();
    expect(simplesPorProps(metricas({}))).toBeNull();
    expect(
      simplesPorProps({
        ...metricas({}),
        simples_con_props: cuenta(0, 0),
        simples_sin_props: cuenta(5, 3),
      }),
    ).toBeNull();
  });

  it('con alguna simple de props (aunque esté en juego) separa las dos', () => {
    const r = simplesPorProps({
      ...metricas({}),
      simples_con_props: cuenta(0, 0, { pending: 1 }),
      simples_sin_props: cuenta(5, 3),
    });
    expect(r?.con.pending).toBe(1);
    expect(r?.sin).toMatchObject({ hits: 5, misses: 3, rate_pct: 62.5 });
  });
});

describe('selecciones de los mercados nuevos de los picks (reglas v5)', () => {
  const linea = (market_key: string, outcome_name: string) => ({
    market_key,
    outcome_name,
    outcome_point: null,
    player_name: null,
  });

  it('mitad con más goles en lenguaje llano', () => {
    const mk = 'match_highest_scoring_half';
    expect(selectionLabel(linea(mk, '1ª mitad'), 'A', 'B')).toBe('Más goles en la 1ª mitad');
    expect(selectionLabel(linea(mk, 'Igual'), 'A', 'B')).toBe('Mismos goles en las dos mitades');
  });
});

describe('combinada del partido: selecciones de props', () => {
  const prop = (
    market_key: string,
    outcome_name: string,
    point: string | null,
    player: string,
  ) => ({
    market_key,
    outcome_name,
    outcome_point: point,
    player_name: player,
  });

  it('paradas sin "_alt" se leen como línea', () => {
    expect(selectionLabel(prop('player_saves', 'Over', '2.5', 'Unai Simón'), 'A', 'B')).toBe(
      'Unai Simón: más de 2,5 paradas',
    );
  });

  it('tiros a puerta y goleador con el nombre del jugador', () => {
    expect(
      selectionLabel(
        prop('player_sot_alt', 'Robert Lewandowski over 0.5', '0.5', 'Robert Lewandowski'),
        'A',
        'B',
      ),
    ).toBe('Robert Lewandowski: 1+ tiros a puerta');
    expect(
      selectionLabel(
        prop('player_anytime_scorer', 'Vinícius Júnior', null, 'Vinícius Júnior'),
        'A',
        'B',
      ),
    ).toBe('Vinícius Júnior marca');
  });
});
