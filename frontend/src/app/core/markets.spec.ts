import {
  MAX_RECOMMENDABLE_ODDS,
  h2hSide,
  isRecommendable,
  ladderSummary,
  mainLineIndex,
  marketGroup,
  marketLabel,
  playerLadderText,
  propPlayerName,
  sameTeam,
  selectionLabel,
} from './markets';
import { matchPaint, readableOn } from './team-colors';

const line = (market_key: string, outcome_name: string, outcome_point: string | null = null) => ({
  market_key,
  outcome_name,
  outcome_point,
  player_name: null,
});

describe('markets: selecciones en lenguaje llano', () => {
  it('totales con la línea a la española', () => {
    expect(selectionLabel(line('totals', 'Over', '2.50'), 'A', 'B')).toBe('Más de 2,5 goles');
    expect(selectionLabel(line('match_first_half_corners_totals', 'Under', '4.50'), 'A', 'B')).toBe(
      'Menos de 4,5 córners en la 1ª parte',
    );
  });

  it('goles de un equipo llevan su nombre', () => {
    expect(selectionLabel(line('team_away_totals_goals', 'Over', '0.50'), 'Sevilla', 'Real Betis')).toBe(
      'Real Betis: más de 0,5 goles',
    );
  });

  it('doble oportunidad y ambos marcan en español', () => {
    expect(selectionLabel(line('double_chance', 'Sevilla or Draw'), 'Sevilla', 'X')).toBe('Sevilla o empate');
    expect(selectionLabel(line('btts', 'Yes'), 'A', 'B')).toBe('Sí marcan los dos');
  });

  it('el 1X2 usa el nombre de la BD aunque Winamax escriba otro', () => {
    expect(selectionLabel(line('h2h', 'Atl. Madrid'), 'Espanyol', 'Atlético Madrid')).toBe(
      'Gana Atlético Madrid',
    );
    expect(selectionLabel(line('h2h', 'Draw'), 'A', 'B')).toBe('Empate');
  });

  it('un mercado desconocido no enseña su clave cruda', () => {
    expect(marketLabel('match_nuevo_raro')).toBe('Otros mercados');
    expect(marketGroup('player_algo_nuevo')).toBe('jugadores');
  });
});

describe('markets: mismo club con nombres de Winamax', () => {
  it.each([
    ['Atlético Madrid', 'Atl. Madrid'],
    ['SSC Napoli', 'Nápoles'],
    ['Hamburger SV', 'Hamburgo'],
  ])('%s = %s', (bd, winamax) => {
    expect(sameTeam(bd, winamax)).toBe(true);
  });

  it('palabras comunes no cuentan', () => {
    expect(sameTeam('Real Madrid', 'Real Sociedad')).toBe(false);
    expect(sameTeam('Manchester United', 'Leeds United')).toBe(false);
  });

  it('lado de cada selección del 1X2', () => {
    expect(h2hSide('Hamburgo', 'Hamburger SV', 'VfB Stuttgart')).toBe('home');
    expect(h2hSide('Draw', 'A', 'B')).toBeNull();
  });
});

describe('markets: cuotas altas solo informativas', () => {
  it('el límite es 4,00', () => {
    expect(MAX_RECOMMENDABLE_ODDS).toBe(4);
  });

  it('manda el campo recommendable del backend si viene', () => {
    expect(isRecommendable({ offered_odds: '6.50', recommendable: true })).toBe(true);
    expect(isRecommendable({ offered_odds: '1.80', recommendable: false })).toBe(false);
  });

  it('sin el campo, por encima de 4,00 no es recomendable', () => {
    expect(isRecommendable({ offered_odds: '4.00' })).toBe(true);
    expect(isRecommendable({ offered_odds: '4.01' })).toBe(false);
    expect(isRecommendable({ offered_odds: '9.50', recommendable: null })).toBe(false);
  });
});

describe('markets: totales plegados en la línea principal', () => {
  const row = (over: string | null, under: string | null) => ({
    over: over ? { offered_odds: over } : null,
    under: under ? { offered_odds: under } : null,
  });
  // 1,5 · 2,5 · 3,5 · 4,5 · 5,5 goles
  const goles = [
    row('1.20', '4.00'),
    row('1.70', '2.10'),
    row('2.60', '1.45'),
    row('4.50', '1.15'),
    row(null, '1.04'),
  ];

  it('la principal es la de cuotas más parecidas', () => {
    expect(mainLineIndex(goles)).toBe(1);
  });

  it('sin filas con los dos lados, la cuota más cercana a 2,00', () => {
    expect(mainLineIndex([row('1.10', null), row('1.95', null), row('3.10', null)])).toBe(1);
  });

  it('enseña la principal y sus vecinas', () => {
    expect([...ladderSummary(goles, () => false)]).toEqual([0, 1, 2]);
  });

  it('desplaza la ventana si la principal está en un extremo', () => {
    const extremo = [row('1.90', '1.90'), row('2.50', '1.50'), row('3.50', '1.30'), row('5.00', '1.10')];
    expect([...ladderSummary(extremo, () => false)]).toEqual([0, 1, 2]);
  });

  it('nunca esconde una fila que pide keep (valor o boletín)', () => {
    const shown = ladderSummary(goles, (r) => r.under?.offered_odds === '1.04');
    expect([...shown].sort()).toEqual([0, 1, 2, 4]);
  });
});

describe('team-colors', () => {
  it('texto legible sobre el color del club', () => {
    expect(readableOn('#ffffff')).toBe('#0b1222');
    expect(readableOn('#04049f')).toBe('#ffffff');
  });

  it('si los dos visten parecido, el visitante pasa a su secundario', () => {
    const paint = matchPaint(
      { primary_color: '#04049f', secondary_color: '#5152bc' },
      { primary_color: '#031295', secondary_color: '#ffffff' },
    );
    expect(paint.away.bg).toBe('#ffffff');
  });

  it('un club negro usa su secundario en barras y fichas', () => {
    const paint = matchPaint({ primary_color: '#000000', secondary_color: '#ffffff' }, {});
    expect(paint.home.accent).toBe('#ffffff');
  });
});

describe('markets: cuotas de jugador', () => {
  const prop = (market_key: string, outcome_name: string, outcome_point: string, player_name: string | null = null) => ({
    market_key,
    outcome_name,
    outcome_point,
    player_name,
  });

  it('1+ tiros a puerta es la línea de más de 0,5', () => {
    expect(playerLadderText(prop('player_sot_alt', 'Dominic Solanke over 0.5', '0.50'))).toBe('1+ tiros a puerta');
    expect(playerLadderText(prop('player_sot_alt', 'Dominic Solanke over 1.5', '1.5'))).toBe('2+ tiros a puerta');
    expect(playerLadderText(prop('player_shots_alt', 'X over 2.5', '2.5'))).toBe('3+ tiros');
  });

  it('las paradas se leen como línea', () => {
    expect(playerLadderText(prop('player_saves_alt', 'Carl Rushworth over 1.5', '1.5'))).toBe('Más de 1,5 paradas');
    expect(playerLadderText(prop('player_saves_alt', 'Carl Rushworth under 2.5', '2.5'))).toBe('Menos de 2,5 paradas');
  });

  it('la selección lleva el nombre del jugador sin el sentido', () => {
    expect(selectionLabel(prop('player_sot_alt', 'Dominic Solanke over 0.5', '0.5'), 'A', 'B')).toBe(
      'Dominic Solanke: 1+ tiros a puerta',
    );
    expect(selectionLabel(prop('player_saves_alt', 'Rushworth over 2.5', '2.5', 'Carl Rushworth'), 'A', 'B')).toBe(
      'Carl Rushworth: más de 2,5 paradas',
    );
    expect(propPlayerName({ outcome_name: 'Dominic Solanke over 0.5', player_name: null })).toBe('Dominic Solanke');
  });

  it('las paradas del portero van con los mercados de jugador', () => {
    expect(marketLabel('player_saves_alt')).toBe('Paradas del portero');
    expect(marketGroup('player_saves_alt')).toBe('jugadores');
  });
});
