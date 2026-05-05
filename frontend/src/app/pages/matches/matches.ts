import { Component, computed, effect, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';
import { fmtDateShort } from '../../core/format';
import { Match } from '../../core/models';

const LEAGUE_OPTIONS: { key: string; label: string }[] = [
  { key: '', label: 'Todas las ligas' },
  { key: 'soccer_spain_la_liga', label: 'LaLiga' },
  { key: 'soccer_epl', label: 'Premier League' },
  { key: 'soccer_germany_bundesliga', label: 'Bundesliga' },
  { key: 'soccer_italy_serie_a', label: 'Serie A' },
  { key: 'soccer_france_ligue_one', label: 'Ligue 1' },
  { key: 'soccer_uefa_champs_league', label: 'Champions League' },
  { key: 'soccer_uefa_europa_league', label: 'Europa League' },
  { key: 'soccer_uefa_europa_conference_league', label: 'Conference League' },
];

@Component({
  selector: 'app-matches',
  imports: [RouterLink],
  templateUrl: './matches.html',
})
export class Matches {
  private readonly api = inject(ApiService);

  protected readonly matches = signal<Match[]>([]);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly leagueOptions = LEAGUE_OPTIONS;
  protected readonly leagueFilter = signal<string>('');
  protected readonly tab = signal<'upcoming' | 'finished'>('upcoming');

  protected readonly upcoming = computed(() =>
    this.matches().filter((m) => m.status !== 'finished'),
  );
  protected readonly finished = computed(() =>
    this.matches()
      .filter((m) => m.status === 'finished')
      .sort((a, b) => +new Date(b.commence_time) - +new Date(a.commence_time)),
  );

  protected readonly groupedByDay = computed(() => {
    const list = this.tab() === 'finished' ? this.finished() : this.upcoming();
    const map = new Map<string, Match[]>();
    for (const m of list) {
      const key = new Date(m.commence_time).toLocaleDateString('es-ES', {
        weekday: 'long',
        day: '2-digit',
        month: 'long',
      });
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(m);
    }
    return Array.from(map.entries());
  });

  constructor() {
    effect(() => {
      this.load();
    });
  }

  private load(): void {
    const league = this.leagueFilter();
    this.loading.set(true);
    this.error.set(null);
    this.api
      .listMatches({ league: league || undefined, daysAhead: 14, daysBack: 7, limit: 300 })
      .subscribe({
        next: (data) => {
          this.matches.set(data);
          this.loading.set(false);
        },
        error: (err) => {
          this.error.set(this.formatError(err));
          this.loading.set(false);
        },
      });
  }

  protected onLeagueChange(value: string): void {
    this.leagueFilter.set(value);
  }

  protected switchTab(tab: 'upcoming' | 'finished'): void {
    this.tab.set(tab);
  }

  protected fmtDateShort = fmtDateShort;

  private formatError(err: unknown): string {
    if (err instanceof Error) return err.message;
    return 'Error cargando los partidos.';
  }
}
