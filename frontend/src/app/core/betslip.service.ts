/**
 * Boletín: las selecciones que el usuario va añadiendo desde cualquier partido.
 *
 * Vive en el shell (siempre a mano), se guarda en ``localStorage`` para no perderlo al
 * recargar y se convierte en una combinada guardada (``POST /me/parlays``).
 */

import { Injectable, computed, effect, inject, signal } from '@angular/core';
import { Observable } from 'rxjs';

import { ApiService } from './api.service';
import type { SavedParlay } from './models';
import { toNumber } from './format';

export interface SlipLeg {
  /** Identidad de la línea: partido + mercado + selección + línea + jugador. */
  key: string;
  matchId: number;
  home: string;
  away: string;
  league: string;
  kickoff: string;
  marketKey: string;
  marketLabel: string;
  selection: string;
  outcomeName: string;
  outcomePoint: string | null;
  playerName: string | null;
  odds: string;
  modelProbability: string | null;
  modelEv: string | null;
}

/** Desde dónde sale volando una selección hacia el botón del boletín. */
export interface SlipFlight {
  id: number;
  x: number;
  y: number;
  label: string;
}

const STORAGE_KEY = 'betsai.boletin.v1';

export function legKey(l: {
  match_id: number;
  market_key: string;
  outcome_name: string;
  outcome_point: string | null;
  player_name: string | null;
}): string {
  const point = l.outcome_point == null ? '' : String(toNumber(l.outcome_point));
  return [l.match_id, l.market_key, l.outcome_name, point, l.player_name ?? ''].join('|');
}

function load(): SlipLeg[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as SlipLeg[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

@Injectable({ providedIn: 'root' })
export class BetslipService {
  private readonly api = inject(ApiService);
  private flightSeq = 0;

  readonly legs = signal<SlipLeg[]>(load());
  readonly open = signal(false);
  readonly flight = signal<SlipFlight | null>(null);

  readonly count = computed(() => this.legs().length);
  readonly totalOdds = computed(() =>
    this.legs().reduce((acc, l) => acc * toNumber(l.odds), this.legs().length ? 1 : 0),
  );
  /** Partidos con más de una selección: Winamax no siempre deja combinarlas. */
  readonly repeatedMatches = computed(() => {
    const seen = new Map<number, number>();
    for (const l of this.legs()) seen.set(l.matchId, (seen.get(l.matchId) ?? 0) + 1);
    return new Set([...seen].filter(([, n]) => n > 1).map(([id]) => id));
  });
  private readonly keys = computed(() => new Set(this.legs().map((l) => l.key)));

  constructor() {
    effect(() => {
      const legs = this.legs();
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(legs));
      } catch {
        /* sin almacenamiento (modo privado): el boletín dura lo que la pestaña */
      }
    });
  }

  has(key: string): boolean {
    return this.keys().has(key);
  }

  /** Añade o quita. Al añadir, ``from`` es el botón pulsado (para la animación). */
  toggle(leg: SlipLeg, from?: HTMLElement): void {
    if (this.has(leg.key)) {
      this.remove(leg.key);
      return;
    }
    this.legs.update((ls) => [...ls, leg]);
    if (from) {
      const r = from.getBoundingClientRect();
      this.flight.set({
        id: ++this.flightSeq,
        x: r.left + r.width / 2,
        y: r.top + r.height / 2,
        label: leg.odds,
      });
    }
  }

  remove(key: string): void {
    this.legs.update((ls) => ls.filter((l) => l.key !== key));
  }

  clear(): void {
    this.legs.set([]);
  }

  save(title: string | null, stake: string | null): Observable<SavedParlay> {
    return this.api.saveMyParlay({
      title: title?.trim() || null,
      stake,
      legs: this.legs().map((l) => ({
        match_id: l.matchId,
        home_team: l.home,
        away_team: l.away,
        league: l.league,
        commence_time: l.kickoff,
        market_key: l.marketKey,
        outcome_name: l.outcomeName,
        outcome_point: l.outcomePoint,
        player_name: l.playerName,
        offered_odds: l.odds,
        model_probability: l.modelProbability,
        model_ev: l.modelEv,
      })),
    });
  }
}
