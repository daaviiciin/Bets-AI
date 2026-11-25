import type { GradedOddsLine, VentajaMercado } from './models';
import { marcaMercado, rotuloMercado, textoMercado } from './ventaja-mercado';

const LEIDO = '2026-11-18T08:25:00Z';
const AHORA = Date.parse('2026-11-18T08:45:00Z');

const mercado = (extra: Partial<VentajaMercado> = {}): VentajaMercado => ({
  clase: 'value',
  ventaja: '0.0625',
  p_mercado: '0.5313',
  cuota_justa: '1.882',
  casas: 12,
  leido_at: LEIDO,
  ...extra,
});

const linea = (m: VentajaMercado | null): GradedOddsLine =>
  ({ market_key: 'h2h', offered_odds: '2.00', mercado: m }) as GradedOddsLine;

describe('marcaMercado', () => {
  it('sin dato del comparador no hay marca', () => {
    expect(marcaMercado(linea(null))).toBeNull();
    expect(marcaMercado(null)).toBeNull();
  });

  it('value y surebet son ventaja; más del 15 % es posible error', () => {
    expect(marcaMercado(linea(mercado()))).toBe('ventaja');
    expect(marcaMercado(linea(mercado({ clase: 'surebet' })))).toBe('ventaja');
    expect(marcaMercado(linea(mercado({ clase: 'error_probable', ventaja: '0.22' })))).toBe('error');
  });
});

describe('rotuloMercado', () => {
  it('largo y compacto', () => {
    expect(rotuloMercado(mercado())).toBe('Mercado +6,3 %');
    expect(rotuloMercado(mercado(), true)).toBe('▲ +6,3 %');
    expect(rotuloMercado(mercado({ clase: 'error_probable', ventaja: '0.22' }))).toBe(
      '¿Error? +22,0 %',
    );
  });
});

describe('textoMercado', () => {
  it('explica la cuota justa, la P y de dónde sale', () => {
    expect(textoMercado(mercado(), AHORA)).toBe(
      '+6,3 % sobre el mercado: cuota justa 1,88 (P 53 %, consenso de 12 casas, leído hace 20 min)',
    );
  });

  it('avisa de que un error de cuota se puede anular', () => {
    expect(textoMercado(mercado({ clase: 'error_probable', ventaja: '0.22' }), AHORA)).toContain(
      'Winamax puede anularla',
    );
  });
});
