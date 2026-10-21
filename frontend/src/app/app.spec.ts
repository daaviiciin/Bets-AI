import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { App } from './app';
import { BetslipService, SlipLeg } from './core/betslip.service';

function leg(key: string, odds: string): SlipLeg {
  return {
    key,
    matchId: Number(key),
    home: 'Sevilla',
    away: 'Real Betis',
    league: 'LaLiga',
    kickoff: '2026-10-18T19:00:00Z',
    marketKey: 'h2h',
    marketLabel: 'Resultado',
    selection: 'Gana Sevilla',
    outcomeName: 'Sevilla',
    outcomePoint: null,
    playerName: null,
    odds,
    modelProbability: null,
    modelEv: null,
  };
}

describe('App (estructura común)', () => {
  beforeEach(async () => {
    localStorage.clear();
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
  });

  it('se crea', () => {
    expect(TestBed.createComponent(App).componentInstance).toBeTruthy();
  });

  it('enseña la navegación principal', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const nav = (fixture.nativeElement as HTMLElement).querySelector('nav[aria-label="Principal"]');
    const links = [...(nav?.querySelectorAll('a') ?? [])].map((a) => a.textContent?.trim());
    expect(links).toEqual(['Partidos', 'Mis apuestas', 'Transparencia']);
  });

  it('el botón del boletín cuenta las selecciones y multiplica las cuotas', async () => {
    const fixture = TestBed.createComponent(App);
    const slip = TestBed.inject(BetslipService);
    slip.toggle(leg('1', '2.00'));
    slip.toggle(leg('2', '1.50'));
    fixture.detectChanges();
    await fixture.whenStable();
    const boton = (fixture.nativeElement as HTMLElement).querySelector('#boletin-boton');
    expect(boton?.textContent?.replace(/\s+/g, ' ')).toContain('2 · 3,00');
  });

  it('pulsar dos veces la misma cuota la quita del boletín', () => {
    const slip = TestBed.inject(BetslipService);
    slip.toggle(leg('1', '2.00'));
    slip.toggle(leg('1', '2.00'));
    expect(slip.count()).toBe(0);
  });
});
