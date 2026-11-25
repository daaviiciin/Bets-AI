import { fmtProximosPicks, fmtVentaja } from './format';

describe('fmtVentaja', () => {
  it('da un decimal con coma y signo', () => {
    expect(fmtVentaja('0.0625')).toBe('+6,3 %');
    expect(fmtVentaja('0.03')).toBe('+3,0 %');
    expect(fmtVentaja(-0.058)).toBe('-5,8 %');
  });

  it('sin dato, un guion', () => {
    expect(fmtVentaja(null)).toBe('–');
  });
});

describe('fmtProximosPicks', () => {
  // Miércoles 18/11/2026 a media mañana (hora local del navegador).
  const ahora = new Date(2026, 10, 18, 10, 0);

  it('dice el día y la hora del primer partido', () => {
    const viernes = new Date(2026, 10, 20, 20, 30).toISOString();
    expect(fmtProximosPicks(viernes, ahora)).toBe(
      'Próximos picks: el viernes 20 (primer partido a las 20:30)',
    );
  });

  it('si es mañana, dice «mañana»', () => {
    const jueves = new Date(2026, 10, 19, 18, 45).toISOString();
    expect(fmtProximosPicks(jueves, ahora)).toBe(
      'Próximos picks: mañana (primer partido a las 18:45)',
    );
  });

  it('sin calendario no promete picks', () => {
    expect(fmtProximosPicks(null, ahora)).toBe('Aún no hay partidos en el calendario');
  });
});
